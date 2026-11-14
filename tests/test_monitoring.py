import tempfile
import time
import unittest
from pathlib import Path

from backend.monitoring import VisionMonitor
from backend.store import DomainError, Store


FOOD = [
    {"label": "banana", "confidence": 0.9, "box": [120, 160, 170, 220]},
    {"label": "sandwich", "confidence": 0.8, "box": [440, 170, 500, 230]},
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

    def test_requires_stable_food_then_stable_absence(self):
        self.monitor.start()
        for frame_id in range(1, 6):
            self.assertTrue(self.frame(frame_id, FOOD))
        state = self.store.snapshot()
        self.assertEqual([c["food_state"] for c in state["table"]["customers"]], ["food", "food"])
        self.assertEqual(state["tasks"], [])

        for frame_id in range(6, 10):
            self.frame(frame_id, [])
        self.assertEqual(self.store.snapshot()["tasks"], [])
        self.frame(10, [])
        state = self.store.snapshot()
        self.assertEqual([c["food_state"] for c in state["table"]["customers"]], ["empty", "empty"])
        self.assertEqual(len(state["tasks"]), 1)
        self.assertEqual(state["tasks"][0]["status"], "offered")
        self.assertEqual({e["details"].get("source") for e in self.store.events() if e["type"] == "plate_observed"}, {"camera"})
        for frame_id in range(11, 16):
            self.frame(frame_id, [])
        self.assertEqual(len(self.store.snapshot()["tasks"]), 1)

    def test_absence_never_means_empty_before_food_was_stably_seen(self):
        self.monitor.start()
        for frame_id in range(1, 11):
            self.frame(frame_id, [])
        state = self.store.snapshot()
        self.assertEqual([c["food_state"] for c in state["table"]["customers"]], ["unknown", "unknown"])
        self.assertEqual(state["tasks"], [])

    def test_person_occlusion_is_unknown(self):
        self.monitor.start()
        for frame_id in range(1, 6):
            self.frame(frame_id, FOOD)
        self.frame(6, [{"label": "person", "confidence": 0.9, "box": [80, 100, 240, 400]}])
        state = self.store.snapshot()
        self.assertEqual(state["table"]["customers"][0]["food_state"], "unknown")
        self.assertEqual(state["tasks"], [])

    def test_pause_and_reset_discard_inflight_frames(self):
        self.monitor.start()
        context = self.monitor.begin_frame(time.monotonic(), 1)
        self.monitor.pause()
        events_after_pause = len(self.store.events())
        self.assertFalse(self.monitor.consume_frame(context, FOOD))
        self.assertEqual(len(self.store.events()), events_after_pause)
        context = self.monitor.begin_frame(time.monotonic(), 2)
        self.monitor.reset()
        events_after_reset = len(self.store.events())
        self.assertFalse(self.monitor.consume_frame(context, FOOD))
        state = self.store.snapshot()
        self.assertEqual(state["tasks"], [])
        self.assertFalse(state["vision"]["monitoring"])
        self.assertEqual(len(self.store.events()), events_after_reset)

    def test_stale_gap_clears_food_arming_and_stability(self):
        self.monitor.start()
        self.monitor.max_frame_age = 0.02
        for frame_id in range(1, 6):
            self.frame(frame_id, FOOD)
        for frame_id in range(6, 10):
            self.frame(frame_id, [])
        time.sleep(0.03)
        self.assertFalse(self.frame(10, []))
        self.frame(11, [])
        state = self.store.snapshot()
        self.assertEqual([c["food_state"] for c in state["table"]["customers"]], ["unknown", "unknown"])
        self.assertEqual(state["tasks"], [])

    def test_atomic_batch_does_not_create_task_from_crossed_states(self):
        self.store.observe("1", "B", "empty")
        self.store.observe_batch("1", {"A": "empty", "B": "food"}, source="camera")
        state = self.store.snapshot()
        self.assertEqual([c["food_state"] for c in state["table"]["customers"]], ["empty", "food"])
        self.assertEqual(state["tasks"], [])

    def test_open_request_task_does_not_block_camera_check_task(self):
        self.store.request("1", "B", "dessert")
        monitor = VisionMonitor(self.store)
        monitor.start()
        for frame_ids, detections in ((range(1, 6), FOOD), (range(6, 11), [])):
            for frame_id in frame_ids:
                context = monitor.begin_frame(time.monotonic(), frame_id)
                monitor.consume_frame(context, detections)
        tasks = self.store.snapshot()["tasks"]
        self.assertEqual({task["type"] for task in tasks}, {"fulfill_request", "check_table"})


if __name__ == "__main__":
    unittest.main()
