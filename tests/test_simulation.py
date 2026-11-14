import tempfile
import unittest
from pathlib import Path

from backend.simulation import SimulationStore
from backend.store import DomainError, Store


class SimulationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "simulation.sqlite3"
        self.store = SimulationStore(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def state(self):
        return self.store.snapshot()

    def task(self, task_type=None, status=None, table_id=None):
        return next(task for task in self.state()["tasks"] if (task_type is None or task["type"] == task_type) and (status is None or task["status"] == status) and (table_id is None or task["table_id"] == table_id))

    def test_initial_floor_and_simulation_database_mode(self):
        state = self.state()
        self.assertEqual(state["mode"], "simulation")
        self.assertEqual([room["tables"] for room in state["rooms"]], [["1", "2", "3"], ["4", "5", "6"]])
        self.assertEqual([worker["availability"] for worker in state["workers"]], ["available"] * 3)
        self.assertEqual(state["tasks"], [])
        with self.assertRaisesRegex(RuntimeError, "belongs to simulation mode"):
            Store(self.path)

    def test_table_hold_blocks_only_its_deliveries_and_preserves_assigned_owner(self):
        state = self.store.order_ready("1", "kitchen", "meal-1")
        task = next(task for task in state["tasks"] if task["type"] == "food_delivery")
        self.assertEqual((task["status"], task["offered_to"]), ("offered", "W1"))
        self.store.accept(task["id"], "W1")
        self.store.set_hold("1", "W2", "kitchen", "ticket blocked", 30)
        task = self.task("food_delivery", "assigned", "1")
        self.assertEqual(task["worker_id"], "W1")
        self.assertEqual(task["conflict"], {"active": True, "acknowledged": False, "raised_at": 0})
        self.assertEqual(self.store.order_ready("1", "kitchen", "meal-2")["tasks"][-1]["status"], "pending")
        self.store.request("1", "water")
        self.assertEqual(self.task("service_request")["status"], "offered")
        with self.assertRaisesRegex(DomainError, "Clear the table service hold"):
            self.store.complete(task["id"], "W1")
        self.store.acknowledge_conflict(task["id"], "W1")
        with self.assertRaisesRegex(DomainError, "Clear the table service hold"):
            self.store.complete(task["id"], "W1")
        self.store.clear_hold("1", "W2")
        self.store.complete(task["id"], "W1")
        self.assertEqual(self.task("food_delivery", "completed", "1")["worker_id"], "W1")

    def test_hold_pauses_eligible_age_and_overdue_hold_requires_confirmation(self):
        self.store.set_hold("1", "W1", "bar", "wait for access", 10)
        task = next(task for task in self.store.order_ready("1", "kitchen", "held-order")["tasks"] if task["type"] == "food_delivery")
        task_id = task["id"]
        self.assertEqual(task["status"], "pending")
        state = self.store.advance(100)
        task = next(task for task in state["tasks"] if task["id"] == task_id)
        table = next(table for table in state["tables"] if table["id"] == "1")
        self.assertEqual(task["wait_seconds"], 0)
        self.assertTrue(table["hold"]["active"])
        self.assertTrue(table["hold"]["overdue"])
        self.assertTrue(table["hold"]["needs_confirmation"])
        self.store.clear_hold("1", "W1")
        self.assertEqual(self.task("food_delivery", "offered", "1")["wait_seconds"], 0)

    def test_clock_priority_overdue_beats_newer_food_and_requests(self):
        self.store.order_ready("1", "kitchen", "initial-food")
        self.store.request("4", "water", "initial-water")
        self.store.observe_meal("2", "finished")
        assigned = [task for task in self.state()["tasks"] if task["status"] == "offered"]
        self.assertEqual(len(assigned), 3)
        for task in assigned:
            self.store.accept(task["id"], task["offered_to"])
        late = self.store.order_ready("3", "bar", "late-food")["tasks"][-1]
        self.store.advance(91)
        state = self.store.request("5", "dessert", "new-dessert")
        newest_request = next(task for task in state["tasks"] if task.get("request_id") == "new-dessert")
        self.assertEqual(newest_request["status"], "pending")
        self.assertEqual(next(task for task in state["tasks"] if task["id"] == late["id"])["wait_seconds"], 91)
        request_task = next(task for task in state["tasks"] if task.get("request_id") == "initial-water")
        self.store.complete(request_task["id"], request_task["worker_id"])
        self.assertEqual(self.task("food_delivery", "offered", "3")["id"], late["id"])
        self.assertTrue(any(item["type"] == "task_overdue" and item["details"]["task_id"] == late["id"] for item in self.store.events()))

    def test_offers_are_sticky_and_urgent_only_changes_pending_routing(self):
        first = self.store.order_ready("1", "kitchen", "first")["tasks"][-1]
        offered_to = first["offered_to"]
        self.store.order_ready("2", "bar", "urgent", urgent=True)
        state = self.state()
        first = next(task for task in state["tasks"] if task["id"] == first["id"])
        urgent = next(task for task in state["tasks"] if task.get("order_id") == "urgent")
        self.assertEqual(first["offered_to"], offered_to)
        self.assertEqual(urgent["status"], "offered")
        self.assertNotEqual(urgent["offered_to"], offered_to)

    def test_room_affinity_breaks_equal_age_task_ties_before_creation_order(self):
        for worker_id in ("W1", "W2", "W3"):
            self.store.set_availability(worker_id, "busy")
        self.store.request("1", "water", "room-one")
        self.store.request("4", "water", "room-two")
        state = self.store.set_availability("W3", "available")
        offered = next(task for task in state["tasks"] if task["status"] == "offered")
        self.assertEqual(offered["table_id"], "4")
        self.assertEqual(offered["offered_to"], "W3")

    def test_finished_meal_creates_one_check_and_accepted_check_is_not_preempted(self):
        self.store.observe_meal("2", "finished")
        count = len(self.store.events())
        self.store.observe_meal("2", "finished")
        self.assertEqual(len(self.store.events()), count)
        check = self.task("check_table", "offered", "2")
        worker_id = check["offered_to"]
        self.store.accept(check["id"], worker_id)
        state = self.store.observe_meal("2", "eating")
        check = next(task for task in state["tasks"] if task["id"] == check["id"])
        worker = next(worker for worker in state["workers"] if worker["id"] == worker_id)
        self.assertEqual(check["status"], "assigned")
        self.assertEqual(worker["availability"], "busy")
        self.assertEqual(next(table for table in state["tables"] if table["id"] == "2")["status"], "being_served")
        state = self.store.release(check["id"], worker_id)
        check = next(task for task in state["tasks"] if task["id"] == check["id"])
        self.assertEqual(check["status"], "cancelled")
        self.assertEqual(next(table for table in state["tables"] if table["id"] == "2")["status"], "eating")

    def test_duplicate_ids_and_auto_ids_do_not_create_duplicates_or_stall(self):
        self.store.order_ready("1", "kitchen", "order-2")
        self.store.order_ready("2", "bar")
        self.store.order_ready("3", "kitchen")
        ids = [order["id"] for order in self.state()["orders"]]
        self.assertEqual(ids, ["order-2", "order-1", "order-3"])
        count = len(self.store.events())
        self.store.order_ready("1", "kitchen", "order-2")
        self.assertEqual(len(self.store.events()), count)
        with self.assertRaisesRegex(DomainError, "different order"):
            self.store.order_ready("4", "bar", "order-2")
        self.store.request("1", "water", "request-2")
        self.store.request("2", "water")
        self.store.request("3", "dessert")
        self.assertEqual([request["id"] for request in self.state()["requests"]], ["request-2", "request-1", "request-3"])
        with self.assertRaisesRegex(DomainError, "different request"):
            self.store.request("4", "dessert", "request-2")

    def test_conflict_must_be_acknowledged_for_each_new_hold(self):
        self.store.order_ready("1", "kitchen", "repeat-hold")
        task = self.task("food_delivery", "offered")
        worker_id = task["offered_to"]
        self.store.accept(task["id"], worker_id)
        self.store.set_hold("1", "W2", "kitchen", "first")
        self.store.acknowledge_conflict(task["id"], worker_id)
        self.store.clear_hold("1", "W2")
        self.store.set_hold("1", "W3", "bar", "second")
        task = self.task("food_delivery", "assigned")
        self.assertFalse(task["conflict"]["acknowledged"])
        self.store.clear_hold("1", "W3")
        with self.assertRaisesRegex(DomainError, "Acknowledge"):
            self.store.complete(task["id"], worker_id)

    def test_reset_keeps_history_and_mode_marker_prevents_cross_use(self):
        self.store.order_ready("1", "kitchen", "reset-order")
        before = len(self.store.events())
        state = self.store.reset()
        self.assertEqual(state["clock_seconds"], 0)
        self.assertEqual(state["tasks"], [])
        self.assertEqual(len(self.store.events()), before + 1)
        with self.assertRaisesRegex(RuntimeError, "belongs to simulation mode"):
            Store(self.path)


if __name__ == "__main__":
    unittest.main()
