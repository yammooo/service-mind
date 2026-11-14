import unittest
from unittest.mock import patch

from scripts import task_announcer


class AnnouncerTests(unittest.TestCase):
    def test_only_sink_with_configured_bluetooth_address_matches(self):
        sinks = [
            {"name": "laptop-speakers", "properties": {"device.string": "internal"}},
            {"name": "glasses-dynamic-name", "properties": {"api.bluez5.address": "98:59:49:E9:64:F1"}},
        ]
        self.assertEqual(task_announcer.glasses_sink(sinks)["name"], "glasses-dynamic-name")
        self.assertIsNone(task_announcer.glasses_sink(sinks[:1]))

    def test_announces_only_latest_live_offer_for_configured_worker(self):
        tasks = [
            {"id": "task-1", "type": "check_table", "status": "offered", "offered_to": "B"},
            {"id": "task-2", "type": "fulfill_request", "request_type": "water", "status": "offered", "offered_to": "A"},
            {"id": "task-3", "type": "check_table", "status": "completed", "offered_to": "B"},
        ]
        events = [
            {"id": 4, "type": "task_offered", "details": {"task_id": "task-1", "worker_id": "B"}},
            {"id": 7, "type": "task_offered", "details": {"task_id": "task-1", "worker_id": "B"}},
            {"id": 8, "type": "task_offered", "details": {"task_id": "task-2", "worker_id": "A"}},
            {"id": 9, "type": "task_offered", "details": {"task_id": "task-3", "worker_id": "B"}},
        ]
        self.assertEqual(task_announcer.current_offers(tasks, events, "B"), [(7, tasks[0])])

    def test_fixed_speech_for_each_supported_offer(self):
        self.assertEqual(
            task_announcer.speech_for({"type": "check_table"}),
            "Table one needs checking. Accept on your phone.",
        )
        self.assertEqual(
            task_announcer.speech_for({"type": "fulfill_request", "request_type": "dessert"}),
            "Bring dessert to Table one. Accept on your phone.",
        )
        self.assertIsNone(task_announcer.speech_for({"type": "fulfill_request", "request_type": "unknown"}))

    def test_changed_offer_is_checked_after_synthesis_before_playback(self):
        with patch.object(task_announcer, "inspect_sink", side_effect=[("glasses-sink", True, False, None), ("glasses-sink", True, False, None)]), patch.object(task_announcer.subprocess, "run") as run:
            with self.assertRaises(task_announcer.PlaybackSkipped):
                task_announcer.play_text("test", before_play=lambda: False)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "espeak-ng")

    def test_playback_uses_named_sink_without_rescue_properties(self):
        with patch.object(task_announcer, "inspect_sink", side_effect=[("glasses-sink", True, False, None), ("glasses-sink", True, False, None)]), patch.object(task_announcer.subprocess, "run") as run:
            task_announcer.play_text("test")
        command = run.call_args.args[0]
        self.assertIn("--device=glasses-sink", command)
        self.assertIn("--property=node.dont-fallback=true", command)
        self.assertIn("--property=node.dont-reconnect=true", command)
        self.assertIn("--property=node.dont-move=true", command)


if __name__ == "__main__":
    unittest.main()
