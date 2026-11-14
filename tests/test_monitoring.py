import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.monitoring import VisionMonitor
from backend.store import DomainError, Store


BANANAS = [
    {"label": "banana", "confidence": 0.9, "box": [120, 160, 170, 220]},
    {"label": "banana", "confidence": 0.8, "box": [440, 170, 500, 230]},
]


class VisionMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "demo.sqlite3"
        self.store = Store(self.path)
        self.monitor = VisionMonitor(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def frame(self, frame_id, detections):
        context = self.monitor.begin_frame(time.monotonic(), frame_id)
        return self.monitor.consume_frame(context, detections)

    def timeline(self):
        base = time.monotonic()
        clock = [base]
        patcher = patch("backend.monitoring.time.monotonic", side_effect=lambda: clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.monitor.start()

        def frame(frame_id, elapsed, detections):
            clock[0] = base + elapsed
            context = self.monitor.begin_frame(clock[0], frame_id)
            return self.monitor.consume_frame(context, detections)

        return frame, clock, base

    def test_regions_validate_persist_and_survive_reset(self):
        regions = {"A": [0.1, 0.2, 0.4, 0.8], "B": [0.6, 0.2, 0.9, 0.8]}
        self.monitor.set_regions(regions)
        before = self.store.snapshot()
        generation = self.monitor.generation
        with self.assertRaises(DomainError):
            self.monitor.set_regions({"A": [0.8, 0.2, 0.4, 0.8], "B": regions["B"]})
        self.assertEqual(self.monitor.generation, generation)
        self.assertEqual(self.store.snapshot(), before)

        self.monitor.start()
        self.assertTrue(self.store.snapshot()["vision"]["monitoring"])
        self.monitor.reset()
        state = Store(self.path).snapshot()
        self.assertEqual(state["vision"]["regions"], regions)
        self.assertFalse(state["vision"]["monitoring"])
        self.assertEqual(state["tasks"], [])

    def test_banana_means_food_after_two_seconds_even_with_person_overlap(self):
        frame, _, _ = self.timeline()
        detections = BANANAS + [{"label": "person", "confidence": 0.9, "box": [0, 0, 640, 480]}]
        frame(1, 0, detections)
        frame(2, 1.9, detections)
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["unknown", "unknown"])
        frame(3, 2, detections)
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["food", "food"])
        self.assertEqual(state["tasks"], [])

    def test_no_banana_means_empty_after_two_seconds_including_at_start(self):
        frame, _, _ = self.timeline()
        other_objects = [
            {"label": "apple", "confidence": 0.9, "box": [120, 160, 170, 220]},
            {"label": "sandwich", "confidence": 0.8, "box": [440, 170, 500, 230]},
            {"label": "person", "confidence": 0.9, "box": [0, 0, 640, 480]},
        ]
        frame(1, 0, other_objects)
        frame(2, 1.9, other_objects)
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["unknown", "unknown"])
        self.assertEqual(state["tasks"], [])
        frame(3, 2, other_objects)
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["empty", "empty"])
        self.assertEqual(len(state["tasks"]), 1)
        self.assertEqual(state["tasks"][0]["status"], "offered")
        self.assertEqual({event["details"].get("source") for event in self.store.events() if event["type"] == "plate_observed"}, {"camera"})
        frame(4, 2.5, other_objects)
        self.assertEqual(len(self.store.snapshot()["tasks"]), 1)

    def test_candidate_change_restarts_two_second_delay(self):
        frame, _, _ = self.timeline()
        frame(1, 0, BANANAS)
        frame(2, 2, BANANAS)
        self.assertEqual([customer["food_state"] for customer in self.store.snapshot()["table"]["customers"]], ["food", "food"])
        frame(3, 2.1, [])
        frame(4, 3.9, [])
        frame(5, 4, BANANAS)
        frame(6, 4.1, [])
        frame(7, 5.9, [])
        self.assertEqual([customer["food_state"] for customer in self.store.snapshot()["table"]["customers"]], ["food", "food"])
        frame(8, 6.1, [])
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["empty", "empty"])

    def test_camera_gap_resets_candidate_and_starts_delay_on_current_fresh_frame(self):
        frame, _, _ = self.timeline()
        frame(1, 0, BANANAS)
        frame(2, 1, BANANAS)
        frame(3, 3.1, BANANAS)
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["unknown", "unknown"])
        self.assertEqual(self.monitor.status()[0]["stable_for_ms"], 0)
        frame(4, 4, BANANAS)
        frame(5, 5.1, BANANAS)
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["food", "food"])

    def test_stale_frame_sets_unknown_and_does_not_count(self):
        frame, clock, base = self.timeline()
        frame(1, 0, BANANAS)
        frame(2, 1, BANANAS)
        frame(3, 2, BANANAS)
        self.assertEqual(self.store.snapshot()["table"]["customers"][0]["food_state"], "food")
        clock[0] = base + 5
        context = self.monitor.begin_frame(base + 2.5, 4)
        self.assertFalse(self.monitor.consume_frame(context, []))
        state = self.store.snapshot()
        self.assertEqual([customer["food_state"] for customer in state["table"]["customers"]], ["unknown", "unknown"])
        self.assertEqual(self.monitor.status()[0]["stable_for_ms"], 0)

    def test_pause_and_reset_discard_inflight_frames(self):
        self.monitor.start()
        context = self.monitor.begin_frame(time.monotonic(), 1)
        self.monitor.pause()
        events_after_pause = len(self.store.events())
        self.assertFalse(self.monitor.consume_frame(context, BANANAS))
        self.assertEqual(len(self.store.events()), events_after_pause)
        context = self.monitor.begin_frame(time.monotonic(), 2)
        self.monitor.reset()
        events_after_reset = len(self.store.events())
        self.assertFalse(self.monitor.consume_frame(context, BANANAS))
        state = self.store.snapshot()
        self.assertEqual(state["tasks"], [])
        self.assertFalse(state["vision"]["monitoring"])
        self.assertEqual(len(self.store.events()), events_after_reset)

    def test_atomic_batch_does_not_create_task_from_crossed_states(self):
        self.store.observe("1", "B", "empty")
        self.store.observe_batch("1", {"A": "empty", "B": "food"}, source="camera")
        state = self.store.snapshot()
        self.assertEqual([c["food_state"] for c in state["table"]["customers"]], ["empty", "food"])
        self.assertEqual(state["tasks"], [])

    def test_open_request_task_does_not_block_camera_check_task(self):
        self.store.request("1", "B", "dessert")
        frame, _, _ = self.timeline()
        frame(1, 0, BANANAS)
        frame(2, 2, BANANAS)
        frame(3, 2.1, [])
        frame(4, 3.1, [])
        frame(5, 4.1, [])
        tasks = self.store.snapshot()["tasks"]
        self.assertEqual({task["type"] for task in tasks}, {"fulfill_request", "check_table"})

    def test_old_frame_count_config_migrates_to_two_seconds(self):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM current_state WHERE id = 1").fetchone()
            state = json.loads(row[0])
            state["vision"]["stable_observations_required"] = 5
            state["vision"].pop("stable_seconds", None)
            db.execute("UPDATE current_state SET value = ? WHERE id = 1", (json.dumps(state),))

        migrated = Store(self.path).snapshot()["vision"]
        self.assertEqual(migrated["stable_seconds"], 2.0)
        self.assertNotIn("stable_observations_required", migrated)

    def test_process_start_pauses_persisted_monitoring_without_resetting_world(self):
        self.monitor.start()
        self.store.observe("1", "A", "empty")
        self.store.observe("1", "B", "empty")
        check_id = self.store.snapshot()["tasks"][0]["id"]
        self.store.accept(check_id, "B")
        self.store.request("1", "B", "dessert")
        before = self.store.snapshot()
        event_count = len(self.store.events())

        restarted = Store(self.path)
        VisionMonitor(restarted)
        after = restarted.snapshot()

        self.assertFalse(after["vision"]["monitoring"])
        self.assertEqual(after["vision"]["stable_seconds"], 2.0)
        self.assertEqual(after["vision"]["regions"], before["vision"]["regions"])
        for key in ("table", "workers", "tasks", "requests"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(len(restarted.events()), event_count + 1)
        self.assertEqual(restarted.events()[-1]["type"], "vision_monitoring_paused")


if __name__ == "__main__":
    unittest.main()
