# ServiceMind prototype

## Current milestone — integrated admin, workers, and live camera heuristic

Build and maintain the local ServiceMind prototype for the Junction demo. The active implementation is one FastAPI/Uvicorn process with SQLite, plain responsive HTML/CSS/JavaScript, relative API URLs, one-second polling, and a live USB camera on the Arduino UNO Q (Linux ARM64). Use `/admin`, `/worker/A`, and `/worker/B`; `/` aliases the admin page and `/?worker=A|B` redirects to the worker view.

The live page shows all detections from a small pretrained YOLOv8n COCO ONNX model and measured inference FPS, inference time, frame age, confidence, and errors. The admin can draw and persist normalized A/B rectangles, start/pause observation processing, reset the world, and use collapsed mock controls. Worker pages expose focused availability and task actions, dessert/water, and not-ready actions. Existing Store/SQLite is the source of truth; do not add a parallel state store or server.

Only a banana detection whose center is inside a saved region means food; other classes do not affect plate state. If no banana is detected there, the candidate is empty. A candidate must persist for two seconds of fresh frames before submission. Stale frames and camera/model errors remain unknown and do not count toward the delay. Start, pause, reset, and region changes clear candidate timing. Reset pauses observation, keeps ROIs, and appends an audit event. In-flight inference started before pause/start/reset/ROI changes must not commit into the new state generation.

This is an unvalidated prototype heuristic. The generic model has no plate class, misses objects, and cannot reliably recognize hands/occlusion; do not claim validated plate understanding or operational safety. No cloud, LLM, authentication, Meta glasses, or trained restaurant model integration is in scope. Do not expose the unauthenticated local demo server outside trusted demo Wi-Fi.

## Fixed coordination behavior

- One restaurant table, two customers/plates A and B, workers A and B.
- Initial state: both customers eating; worker A busy, worker B available; no tasks.
- A first empty report does not create a task. When both plates are empty, set `needs_check` and offer exactly one `check_table` task to an available worker.
- Accepting a `check_table` task assigns it, marks that worker busy and sets the table to `being_served`. Dessert/water requests create one shared request and linked `fulfill_request` task; offer it to an available worker, or leave it pending until one becomes available. Accepting a fulfillment task marks that worker busy; completing it fulfills the linked request without changing the table-check status. Completing a check task makes its worker available and sets the table to `checked`.
- Decline reoffers that task to another available worker and can offer other pending work to the decliner. Repeated clicks on the same still-open request type do not create duplicate requests/tasks. `not_ready` cancels check-table tasks and suppresses further table-check follow-up until relevant new plate evidence or reset; it does not cancel request fulfillment.
- Duplicate/unchanged observations add no events or tasks. Unknown is never empty.
- Reset restores deterministic world state and retains chronological history with a reset event.
- Only an active `check_table` task controls table-check state and not-ready cancellation; an active fulfillment task must not suppress new table evidence. State enums: table `eating`, `needs_check`, `being_served`, `checked`; task types `check_table`, `fulfill_request`; task statuses `pending`, `offered`, `assigned`, `completed`, `cancelled`.

## Implementation and verification

- Persist state and timestamped events atomically in SQLite; serialize writes with thread locking and transactions.
- The UNO Q boots `deploy/service-mind.service` as user `arduino`; observation monitoring is forced paused at every process start without resetting table/workers/requests/ROIs/history.
- Preserve APIs: `/api/state`, `/api/events`, observation, availability, task, request, correction, demo reset, and camera status/stream/region/start/pause endpoints. Validate malformed IDs/actions/regions.
- `VisionProvider`, functional `MockVisionProvider`, explicit `RealVisionProvider` `NotImplementedError`, and `VISION_PROVIDER=mock` default remain supported.
- Keep camera ROIs normalized and stability configuration in `config/camera.json`.
- Keep the UI accessible, responsive and focused. The worker view has no admin/mock controls.
- Keep SQLite, virtualenv/cache files, runtime model weights and captures out of Git. Never save camera frames by default.
- Test non-trivial logic with `python -m unittest discover -s tests -v`; run the app with `uvicorn backend.main:app --host 0.0.0.0 --port 7000 --timeout-graceful-shutdown 5` and exercise HTTP/browser behavior when changing integration.
- README documents setup, URLs, demo flow, APIs, model provenance/license, camera limitations, and hardware boundary.
- Follow `/home/yammo/.codex/plugins/cache/ponytail/ponytail/5.3.3/skills/ponytail/SKILL.md`: smallest complete implementation, reuse dependencies, account for callers/config/tests, test meaningful logic.

## Opt-in floor coordination simulation

This branch adds a separate, opt-in simulation milestone. The existing camera demo and its one-table state remain the default and must keep their behavior. Select simulation with `SERVICE_MIND_MODE=simulation`; it uses only `SERVICE_MIND_SIM_DB` (default `data/service-mind-simulation.sqlite3`) and rejects a database tagged for the other mode. One process selects one Store. Simulation mode never starts or reads the camera.

The simulated floor has Tables 1–3 in Room 1 and Tables 4–6 in Room 2. Worker W1 and W2 prefer Room 1; W3 prefers Room 2. Kitchen and bar are order-ready input sources, not room names. Requests and finished-meal evidence create separate service-request and check-table tasks; neither reports that food is ready.

Simulation dispatch ranks urgent work first, then work with at least 90 eligible simulated wait seconds, then ready food deliveries, customer requests, and table checks. Ties use longest eligible wait, room preference, longest worker availability, then worker ID. A table service hold blocks only food delivery for that table and pauses its delivery wait. Offers remain with their worker until invalidated or declined; accepted tasks are never reassigned automatically. A hold that conflicts with an assigned delivery needs task acknowledgement and explicit hold clearance before completion. Timed holds only raise an overdue confirmation event; they never clear themselves. Reset restores the floor while retaining chronological event history.

Run the legacy suite plus simulation rules with `python -m unittest discover -s tests -v`. Keep the simulation database outside the main camera database; do not use camera observations as simulated evidence or describe this mode as a validation of restaurant operations.
