#!/usr/bin/env python3
"""Speak current offers for one worker to a specifically paired glasses sink."""

import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

API_ROOT = os.environ.get("SERVICE_MIND_URL", "http://10.0.41.164:7000").rstrip("/")
WORKER_ID = os.environ.get("ANNOUNCER_WORKER", "B")
GLASSES_ADDRESS = os.environ.get("GLASSES_BLUETOOTH_ADDRESS", "98:59:49:E9:64:F1")
if WORKER_ID not in {"A", "B"} or not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", GLASSES_ADDRESS):
    raise SystemExit("Set ANNOUNCER_WORKER to A or B and GLASSES_BLUETOOTH_ADDRESS to a Bluetooth MAC address")


def api_json(path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(API_ROOT + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def _address(value):
    return re.sub(r"[^0-9a-f]", "", str(value).lower())


def glasses_sink(sinks, address=GLASSES_ADDRESS):
    wanted = _address(address)
    for sink in sinks:
        props = sink.get("properties", {})
        if any(
            key.lower() in {"api.bluez5.address", "bluez5.address", "device.string"}
            and _address(value) == wanted
            for key, value in props.items()
        ):
            return sink
    return None


def inspect_sink():
    try:
        result = subprocess.run(
            ["pactl", "-f", "json", "list", "sinks"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        sink = glasses_sink(json.loads(result.stdout))
        if sink is None:
            return None, False, None, None
        muted = sink.get("mute")
        if not isinstance(muted, bool):
            return sink["name"], True, None, "Could not verify the glasses sink mute state"
        return sink["name"], True, muted, "Glasses sink is muted" if muted else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        return None, False, None, f"Could not inspect audio devices: {str(error)[:160]}"


class PlaybackSkipped(Exception):
    pass


def play_text(text, before_play=None, heartbeat=None):
    sink_name, connected, muted, error = inspect_sink()
    if not connected:
        raise RuntimeError(error or "Glasses sink is not connected")
    if muted is not False:
        raise RuntimeError(error or "Glasses sink mute state is unknown")
    with tempfile.TemporaryDirectory(prefix="servicemind-audio-") as directory:
        wav_path = os.path.join(directory, "announcement.wav")
        subprocess.run(["espeak-ng", "-w", wav_path, text], check=True, capture_output=True, text=True, timeout=5)
        # Recheck after synthesis so a reconnect never routes audio to a stale/default sink.
        sink_name, connected, muted, error = inspect_sink()
        if not connected or muted is not False:
            raise RuntimeError(error or "Glasses sink is not ready")
        if heartbeat:
            heartbeat()
        if before_play and not before_play():
            raise PlaybackSkipped()
        subprocess.run([
            "paplay",
            "--property=node.dont-fallback=true",
            "--property=node.dont-reconnect=true",
            "--property=node.dont-move=true",
            f"--device={sink_name}",
            wav_path,
        ], check=True, capture_output=True, text=True, timeout=8)
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def current_offers(tasks, events, worker_id):
    latest = {}
    for item in events:
        if item.get("type") != "task_offered":
            continue
        details = item.get("details", {})
        if details.get("worker_id") == worker_id and details.get("task_id"):
            latest[details["task_id"]] = item["id"]
    return [
        (latest[task["id"]], task)
        for task in tasks
        if task.get("status") == "offered"
        and task.get("offered_to") == worker_id
        and task.get("id") in latest
    ]


def speech_for(task):
    if task.get("type") == "check_table":
        return "Table one needs checking. Accept on your phone."
    if task.get("type") == "fulfill_request" and task.get("request_type") in {"dessert", "water"}:
        return f"Bring {task['request_type']} to Table one. Accept on your phone."
    return None


def _short_error(error):
    return (str(error).strip() or error.__class__.__name__)[:200]


def run():
    spoken_offers = set()
    offer_retry_after = {}
    test_retry_after = {}
    last_success = None
    last_error = None

    while True:
        started = time.monotonic()
        sink_name, sink_connected, sink_muted, sink_error = inspect_sink()
        if sink_error:
            last_error = sink_error
        try:
            status = api_json("/api/announcer/status")
            api_json("/api/announcer/heartbeat", {
                "worker_id": WORKER_ID,
                "sink_connected": sink_connected,
                "sink_muted": sink_muted,
                "last_successful_playback": last_success,
                "last_error": last_error,
            }, method="POST")

            def heartbeat_now():
                api_json("/api/announcer/heartbeat", {
                    "worker_id": WORKER_ID,
                    "sink_connected": sink_connected,
                    "sink_muted": sink_muted,
                    "last_successful_playback": last_success,
                    "last_error": last_error,
                }, method="POST")

            if status["test_pending"] and sink_connected and sink_muted is False:
                test_id = status["test_id"]
                if time.monotonic() >= test_retry_after.get(test_id, 0):
                    def claim_test():
                        return api_json("/api/announcer/test/claim", {
                            "worker_id": WORKER_ID,
                            "test_id": test_id,
                        }, method="POST")["claimed"]

                    try:
                        last_success = play_text("This is a glasses connection test.", claim_test, heartbeat_now)
                        last_error = None
                    except PlaybackSkipped:
                        pass
                    except Exception as error:
                        last_error = _short_error(error)
                        test_retry_after[test_id] = time.monotonic() + 15

            state = api_json("/api/state")
            events = api_json("/api/events")
            for event_id, task in current_offers(state["tasks"], events, WORKER_ID):
                text = speech_for(task)
                if not text or event_id in spoken_offers or not sink_connected or sink_muted is not False:
                    continue
                if time.monotonic() < offer_retry_after.get(event_id, 0):
                    continue

                def offer_is_current():
                    fresh_events = api_json("/api/events")
                    fresh_state = api_json("/api/state")
                    return any(
                        current_id == event_id
                        for current_id, _ in current_offers(fresh_state["tasks"], fresh_events, WORKER_ID)
                    )

                try:
                    last_success = play_text(text, offer_is_current, heartbeat_now)
                    last_error = None
                    spoken_offers.add(event_id)
                    offer_retry_after.pop(event_id, None)
                except PlaybackSkipped:
                    pass
                except Exception as error:
                    last_error = _short_error(error)
                    offer_retry_after[event_id] = time.monotonic() + 15
        except Exception as error:
            last_error = f"ServiceMind API unavailable: {_short_error(error)}"[:200]

        sink_name, sink_connected, sink_muted, sink_error = inspect_sink()
        if sink_error:
            last_error = sink_error
        try:
            api_json("/api/announcer/heartbeat", {
                "worker_id": WORKER_ID,
                "sink_connected": sink_connected,
                "sink_muted": sink_muted,
                "last_successful_playback": last_success,
                "last_error": last_error,
            }, method="POST")
        except Exception:
            pass
        time.sleep(max(0, 1 - (time.monotonic() - started)))


if __name__ == "__main__":
    run()
