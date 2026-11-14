import os
import tempfile
import time
import unittest
from pathlib import Path
from pydantic import ValidationError

_temporary_database = tempfile.TemporaryDirectory(prefix="service-mind-announcer-test-")
_old_db = os.environ.get("SERVICE_MIND_DB")
os.environ["SERVICE_MIND_DB"] = str(Path(_temporary_database.name) / "test.sqlite3")
from backend import main
if _old_db is None:
    os.environ.pop("SERVICE_MIND_DB", None)
else:
    os.environ["SERVICE_MIND_DB"] = _old_db


class AnnouncerApiTests(unittest.TestCase):
    def setUp(self):
        with main.app.state.announcer_lock:
            self.previous = main.app.state.announcer.copy()
            main.app.state.announcer.update({
                "heartbeat_at": None,
                "worker_id": None,
                "sink_connected": False,
                "sink_muted": None,
                "last_successful_playback": None,
                "last_error": None,
                "test_id": 0,
                "test_pending": False,
                "test_requested_at": None,
            })

    def tearDown(self):
        with main.app.state.announcer_lock:
            main.app.state.announcer.update(self.previous)

    def test_heartbeat_is_ephemeral_and_stale_laptops_are_offline(self):
        status = main.announcer_heartbeat(main.AnnouncerHeartbeat(
            worker_id="B",
            sink_connected=True,
            sink_muted=False,
            last_successful_playback="2026-11-14T12:00:00Z",
        ))
        self.assertTrue(status["online"])
        self.assertTrue(status["sink_connected"])
        with main.app.state.announcer_lock:
            main.app.state.announcer["heartbeat_at"] = time.monotonic() - 16
        self.assertFalse(main.announcer_status()["online"])
        self.assertFalse(main.announcer_status()["sink_connected"])

    def test_test_button_queues_one_expiring_claim(self):
        queued = main.announcer_test()
        repeated = main.announcer_test()
        self.assertEqual(queued["test_id"], repeated["test_id"])
        self.assertTrue(main.announcer_status()["test_pending"])
        main.announcer_heartbeat(main.AnnouncerHeartbeat(
            worker_id="B", sink_connected=True, sink_muted=False,
        ))
        claim = main.AnnouncerTestClaim(worker_id="B", test_id=queued["test_id"])
        self.assertTrue(main.claim_announcer_test(claim)["claimed"])
        self.assertFalse(main.announcer_status()["test_pending"])
        self.assertFalse(main.claim_announcer_test(claim)["claimed"])

    def test_test_request_expires_and_heartbeat_rejects_unknown_workers(self):
        queued = main.announcer_test()
        with main.app.state.announcer_lock:
            main.app.state.announcer["test_requested_at"] = time.monotonic() - 31
        self.assertFalse(main.announcer_status()["test_pending"])
        self.assertGreater(main.announcer_test()["test_id"], queued["test_id"])
        with self.assertRaises(ValidationError):
            main.AnnouncerHeartbeat(worker_id="X", sink_connected=True, sink_muted=False)


if __name__ == "__main__":
    unittest.main()
