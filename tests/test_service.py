import tempfile
import unittest
import json
import sqlite3
from pathlib import Path

from backend.store import DomainError, Store, initial_state


class ServiceMindFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "demo.sqlite3"
        self.store = Store(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def state(self):
        return self.store.snapshot()

    def events(self):
        return self.store.events()

    def observe(self, plate_id, food_state):
        return self.store.observe("1", plate_id, food_state)

    def start_check(self):
        self.observe("A", "empty")
        self.observe("B", "empty")
        return self.state()["tasks"][0]["id"]

    def test_initial_demo_state(self):
        state = self.state()
        expected = initial_state()
        self.assertEqual(state["table"], expected["table"])
        self.assertEqual(state["workers"], expected["workers"])
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["requests"], [])
        self.assertEqual([event["type"] for event in self.events()], ["demo_started"])

    def test_first_and_second_empty_observations_and_duplicates(self):
        self.observe("A", "empty")
        self.assertEqual(self.state()["table"]["status"], "eating")
        self.assertEqual(self.state()["tasks"], [])
        event_count = len(self.events())
        self.observe("A", "empty")
        self.assertEqual(len(self.events()), event_count)
        self.observe("B", "empty")
        state = self.state()
        self.assertEqual(state["table"]["status"], "needs_check")
        self.assertEqual(len(state["tasks"]), 1)
        self.assertEqual(state["tasks"][0]["status"], "offered")
        self.assertEqual(state["tasks"][0]["offered_to"], "B")
        event_count = len(self.events())
        self.observe("B", "empty")
        self.assertEqual(len(self.events()), event_count)
        self.assertEqual(len(self.state()["tasks"]), 1)

    def test_unknown_never_counts_as_empty(self):
        self.observe("B", "unknown")
        self.observe("A", "empty")
        state = self.state()
        self.assertEqual(state["table"]["status"], "eating")
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["table"]["customers"][1]["food_state"], "unknown")

    def test_accept_request_complete_and_no_recreated_task(self):
        task_id = self.start_check()
        state = self.store.accept(task_id, "B")
        self.assertEqual(state["table"]["status"], "being_served")
        self.assertEqual(state["workers"][1]["availability"], "busy")
        state = self.store.request("1", "B", "dessert")
        self.assertEqual(state["requests"][0]["type"], "dessert")
        request_task = next(task for task in state["tasks"] if task["type"] == "fulfill_request")
        self.assertEqual(request_task["status"], "pending")
        self.assertEqual(request_task["request_id"], state["requests"][0]["id"])
        self.assertIn("request_created", [event["type"] for event in self.events()])
        state = self.store.complete(task_id, "B")
        self.assertEqual(state["table"]["status"], "checked")
        self.assertEqual(state["tasks"][0]["status"], "completed")
        self.assertEqual(state["workers"][1]["availability"], "available")
        request_task = next(task for task in state["tasks"] if task["type"] == "fulfill_request")
        self.assertEqual(request_task["status"], "offered")
        state = self.store.accept(request_task["id"], "B")
        self.assertEqual(state["table"]["status"], "checked")
        state = self.store.complete(request_task["id"], "B")
        self.assertEqual(state["table"]["status"], "checked")
        self.assertEqual(state["requests"][0]["status"], "fulfilled")
        self.assertEqual(state["requests"][0]["fulfilled_by"], "B")
        event_count = len(self.events())
        self.observe("A", "empty")
        self.observe("B", "empty")
        self.assertEqual(len(self.events()), event_count)
        self.assertEqual(len(self.state()["tasks"]), 2)

    def test_request_waits_for_another_worker_and_does_not_change_check_state(self):
        check_id = self.start_check()
        self.store.accept(check_id, "B")
        state = self.store.request("1", "B", "dessert")
        request_task = next(task for task in state["tasks"] if task["type"] == "fulfill_request")
        self.assertEqual(request_task["status"], "pending")
        state = self.store.set_availability("A", "available")
        request_task = next(task for task in state["tasks"] if task["id"] == request_task["id"])
        self.assertEqual(request_task["status"], "offered")
        self.assertEqual(request_task["offered_to"], "A")
        self.assertEqual(state["tasks"][0]["status"], "assigned")
        self.assertEqual(state["table"]["status"], "being_served")
        state = self.store.accept(request_task["id"], "A")
        self.assertEqual(state["table"]["status"], "being_served")
        state = self.store.complete(request_task["id"], "A")
        self.assertEqual(state["table"]["status"], "being_served")
        self.assertEqual(state["requests"][0]["status"], "fulfilled")
        self.assertEqual(state["workers"][1]["availability"], "busy")
        self.assertEqual(self.store.complete(check_id, "B")["table"]["status"], "checked")

    def test_duplicate_open_request_is_idempotent(self):
        state = self.store.request("1", "B", "dessert")
        event_count = len(self.events())
        repeated = self.store.request("1", "A", "dessert")
        self.assertEqual(repeated["requests"], state["requests"])
        self.assertEqual(repeated["tasks"], state["tasks"])
        self.assertEqual(len(self.events()), event_count)

    def test_pending_requests_fill_each_free_worker(self):
        state = initial_state()
        state["workers"][0]["availability"] = "available"
        requests = [
            {"id": "request-1", "table_id": "1", "worker_id": "A", "type": "dessert", "status": "open"},
            {"id": "request-2", "table_id": "1", "worker_id": "B", "type": "water", "status": "open"},
        ]
        state["tasks"] = [self.store._request_task(request, f"task-{index}") for index, request in enumerate(requests, start=1)]
        state["requests"] = requests
        events = Store._offer_pending(state)
        self.assertEqual([(task["status"], task["offered_to"]) for task in state["tasks"]], [("offered", "A"), ("offered", "B")])
        self.assertEqual(len(events), 2)

    def test_not_ready_cancels_check_but_keeps_open_fulfillment(self):
        check_id = self.start_check()
        self.store.accept(check_id, "B")
        state = self.store.request("1", "B", "water")
        followup_id = next(task["id"] for task in state["tasks"] if task["type"] == "fulfill_request")
        state = self.store.correct_not_ready("1")
        check = next(task for task in state["tasks"] if task["id"] == check_id)
        followup = next(task for task in state["tasks"] if task["id"] == followup_id)
        self.assertEqual(check["status"], "cancelled")
        self.assertEqual(followup["status"], "offered")
        self.assertEqual(followup["offered_to"], "B")
        self.assertEqual(state["requests"][0]["status"], "open")

    def test_legacy_open_request_migrates_once_and_preserves_history(self):
        legacy = initial_state()
        legacy["requests"] = [{"id": "request-1", "table_id": "1", "worker_id": "B", "type": "dessert", "status": "open"}]
        legacy["_next_request_id"] = 2
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE current_state SET value = ? WHERE id = 1", (json.dumps(legacy),))
        prior_events = self.events()
        migrated = Store(self.db_path)
        state = migrated.snapshot()
        tasks = [task for task in state["tasks"] if task["type"] == "fulfill_request"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["request_id"], "request-1")
        self.assertEqual(tasks[0]["status"], "offered")
        self.assertEqual(state["requests"][0], legacy["requests"][0])
        self.assertEqual(migrated.events()[:len(prior_events)], prior_events)
        after_migration = migrated.events()
        reopened = Store(self.db_path)
        self.assertEqual(reopened.snapshot()["tasks"], state["tasks"])
        self.assertEqual(reopened.events(), after_migration)

    def test_reset_restores_demo_and_retains_history(self):
        self.start_check()
        self.store.request("1", "B", "water")
        before = len(self.events())
        state = self.store.reset()
        self.assertEqual(state["table"], initial_state()["table"])
        self.assertEqual(state["workers"], initial_state()["workers"])
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["requests"], [])
        events = self.events()
        self.assertEqual(len(events), before + 1)
        self.assertEqual(events[-1]["type"], "demo_reset")
        self.assertIn("request_created", [event["type"] for event in events])

    def test_state_and_events_persist_after_store_restart(self):
        self.start_check()
        previous_state = self.state()
        previous_events = self.events()
        restarted = Store(self.db_path)
        self.assertEqual(restarted.snapshot(), previous_state)
        self.assertEqual(restarted.events(), previous_events)

    def test_decline_reoffers_to_another_available_worker(self):
        task_id = self.start_check()
        self.store.set_availability("A", "available")
        state = self.store.decline(task_id, "B")
        task = state["tasks"][0]
        self.assertEqual(task["status"], "offered")
        self.assertEqual(task["offered_to"], "A")
        self.assertIn("task_declined", [event["type"] for event in self.events()])

    def test_not_ready_suppresses_until_new_evidence(self):
        self.start_check()
        state = self.store.correct_not_ready("1")
        self.assertEqual(state["table"]["status"], "eating")
        self.assertEqual(state["tasks"][0]["status"], "cancelled")
        count = len(self.events())
        self.observe("A", "empty")
        self.assertEqual(len(self.events()), count)
        self.observe("A", "unknown")
        self.assertEqual(self.state()["tasks"][0]["status"], "cancelled")
        self.observe("A", "food")
        self.assertEqual(len(self.state()["tasks"]), 1)
        state = self.observe("A", "empty")
        self.assertEqual(len(state["tasks"]), 2)
        self.assertEqual(state["tasks"][1]["status"], "offered")
        self.assertIn("correction_cleared", [event["type"] for event in self.events()])

    def test_not_ready_cancels_pending_task_too(self):
        self.store.set_availability("B", "busy")
        self.observe("A", "empty")
        self.observe("B", "empty")
        self.assertEqual(self.state()["tasks"][0]["status"], "pending")
        self.store.correct_not_ready("1")
        self.assertEqual(self.state()["tasks"][0]["status"], "cancelled")
        self.store.set_availability("B", "available")
        self.assertEqual(self.state()["tasks"][0]["status"], "cancelled")
        self.assertEqual(len(self.state()["tasks"]), 1)

    def test_invalid_ids_states_and_transitions_are_rejected(self):
        task_id = self.start_check()
        with self.assertRaises(DomainError):
            self.store.accept(task_id, "A")
        with self.assertRaises(DomainError):
            self.store.complete(task_id, "B")
        with self.assertRaises(DomainError):
            self.store.set_availability("X", "available")
        with self.assertRaises(DomainError):
            self.observe("C", "empty")
        with self.assertRaises(DomainError):
            self.observe("A", "gone")
        with self.assertRaises(DomainError):
            self.store.request("2", "B", "water")
        self.store.accept(task_id, "B")
        with self.assertRaises(DomainError):
            self.store.accept(task_id, "B")
        with self.assertRaises(DomainError):
            self.store.set_availability("B", "available")


if __name__ == "__main__":
    unittest.main()
