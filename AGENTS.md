# ServiceMind prototype

## Current milestone — integrated admin, workers, and live camera heuristic

Build and maintain the local ServiceMind prototype for the Junction demo. The active implementation is one FastAPI/Uvicorn process with SQLite, plain responsive HTML/CSS/JavaScript, relative API URLs, one-second polling, and a live USB camera on the Arduino UNO Q (Linux ARM64). Use `/admin`, `/worker/A`, and `/worker/B`; `/` aliases the admin page and `/?worker=A|B` redirects to the worker view.

The live page shows all detections from a small pretrained YOLOv8n COCO ONNX model and measured inference FPS, inference time, frame age, confidence, and errors. The admin can draw and persist normalized A/B rectangles, start/pause observation processing, reset the world, and use collapsed mock controls. Worker pages expose focused availability and task actions, dessert/water, and not-ready actions. Existing Store/SQLite is the source of truth; do not add a parallel state store or server.

Only stable COCO food detection within saved regions informs the experimental food/empty heuristic. Five consecutive fresh frames with a food label arm that region; five fresh frames without food may report empty. Person overlap, stale frames, paused observation, or camera/model error must remain unknown and must never count as empty. Region changes and reset clear stabilization evidence. Reset pauses observation, keeps ROIs, and appends an audit event. In-flight inference started before pause/start/reset/ROI changes must not commit into the new state generation.

This is an unvalidated prototype heuristic. The generic model has no plate class, misses objects, and cannot reliably recognize hands/occlusion; do not claim validated plate understanding or operational safety. No cloud, LLM, authentication, Meta glasses, or trained restaurant model integration is in scope. Do not expose the unauthenticated local demo server outside trusted demo Wi-Fi.

## Fixed coordination behavior

- One restaurant table, two customers/plates A and B, workers A and B.
- Initial state: both customers eating; worker A busy, worker B available; no tasks.
- A first empty report does not create a task. When both plates are empty, set `needs_check` and offer exactly one `check_table` task to an available worker.
- Accept assigns the task, marks the worker busy and table `being_served`. Dessert/water requests create a shared request and timestamped event. Complete makes the worker available and table `checked`; unchanged evidence must not recreate a task.
- Decline reoffers to another available worker. `not_ready` suppresses follow-up until relevant new evidence or reset.
- Duplicate/unchanged observations add no events or tasks. Unknown is never empty.
- Reset restores deterministic world state and retains chronological history with a reset event.
- State enums: table `eating`, `needs_check`, `being_served`, `checked`; task `pending`, `offered`, `assigned`, `completed`, `cancelled`.

## Implementation and verification

- Persist state and timestamped events atomically in SQLite; serialize writes with thread locking and transactions.
- Preserve APIs: `/api/state`, `/api/events`, observation, availability, task, request, correction, demo reset, and camera status/stream/region/start/pause endpoints. Validate malformed IDs/actions/regions.
- `VisionProvider`, functional `MockVisionProvider`, explicit `RealVisionProvider` `NotImplementedError`, and `VISION_PROVIDER=mock` default remain supported.
- Keep camera ROIs normalized and stability configuration in `config/camera.json`.
- Keep the UI accessible, responsive and focused. The worker view has no admin/mock controls.
- Keep SQLite, virtualenv/cache files, runtime model weights and captures out of Git. Never save camera frames by default.
- Test non-trivial logic with `python -m unittest discover -s tests -v`; run the app with `uvicorn backend.main:app --host 0.0.0.0 --port 7000 --timeout-graceful-shutdown 5` and exercise HTTP/browser behavior when changing integration.
- README documents setup, URLs, demo flow, APIs, model provenance/license, camera limitations, and hardware boundary.
- Follow `/home/yammo/.codex/plugins/cache/ponytail/ponytail/5.3.3/skills/ponytail/SKILL.md`: smallest complete implementation, reuse dependencies, account for callers/config/tests, test meaningful logic.
