import tempfile
import unittest
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
        self.assertIn("request_created", [event["type"] for event in self.events()])
        state = self.store.complete(task_id, "B")
        self.assertEqual(state["table"]["status"], "checked")
        self.assertEqual(state["tasks"][0]["status"], "completed")
        self.assertEqual(state["workers"][1]["availability"], "available")
        event_count = len(self.events())
        self.observe("A", "empty")
        self.observe("B", "empty")
        self.assertEqual(len(self.events()), event_count)
        self.assertEqual(len(self.state()["tasks"]), 1)

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
