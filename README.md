# ServiceMind

A one-process restaurant service coordination prototype. It runs locally with FastAPI, SQLite, and plain HTML/CSS/JavaScript. The demo uses deterministic mock observations and rules; it does not call an LLM or use a camera.

## Run it

Requires Python 3.10 or newer. The same Python setup is intended for the Linux ARM64 demo laptop.

```bash
cd service-mind
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open these views in a browser:

- Admin overview: <http://localhost:8000/>
- Worker A: <http://localhost:8000/?worker=A>
- Worker B: <http://localhost:8000/?worker=B>
- API schema: <http://localhost:8000/docs>

Keep one Uvicorn process. The default SQLite database is `data/service-mind.sqlite3`; set `SERVICE_MIND_DB=/path/to/file.sqlite3` to choose another location. Reset from the admin view or call `POST /api/demo/reset`. Reset restores the initial live state and appends a reset event, so activity history remains.

## Demo flow

1. Open the overview and Worker B in separate tabs. The demo starts with Customer A and Customer B eating, Worker A busy, Worker B available, and no tasks.
2. On the overview, report Plate A as **Empty**. The table remains in `eating` and no task appears.
3. Report Plate B as **Empty**. The table changes to `needs_check`, and one `check_table` task is offered to Worker B.
4. In Worker B's view, accept the check-in. Worker B becomes busy and the table changes to `being_served`.
5. Use the worker's Dessert or Water action. The request appears in the shared overview and event history.
6. Complete the check-in. Worker B becomes available and the table changes to `checked`. Repeating the same empty observations does not create another task.
7. Try **Guests not ready** to cancel an active check and suppress follow-up. Suppression lasts until a plate's observation changes or the demo is reset.

The mock observation buttons also support **Food** and **Unknown**. Unknown is stored as unknown and never counts as empty or clear a not-ready correction. New `food` or `empty` evidence clears that correction. Duplicate observations are ignored without creating events or tasks. Declined tasks are offered to another available worker, or stay pending when no other worker is available.

## API

All APIs are relative to the current host. `GET /api/state` returns the current table, customers, workers, tasks, and requests. `GET /api/events` returns the append-only event history in chronological order. State changes and their timestamped events are written together in a SQLite transaction.

| Endpoint | Body or behavior |
| --- | --- |
| `GET /api/state` | Current shared state |
| `GET /api/events` | Chronological events |
| `POST /api/observations` | `{"table_id":"1","plate_id":"A","food_state":"empty"}`; plate state is `food`, `empty`, or `unknown` |
| `POST /api/workers/{id}/availability` | `{"availability":"available"}` or `{"availability":"busy"}` |
| `POST /api/tasks/{id}/accept` | `{"worker_id":"B"}` |
| `POST /api/tasks/{id}/decline` | `{"worker_id":"B"}` |
| `POST /api/tasks/{id}/complete` | `{"worker_id":"B"}` |
| `POST /api/tables/{id}/requests` | `{"worker_id":"B","request_type":"dessert"}` or `water` |
| `POST /api/tables/{id}/correction` | `{"correction":"not_ready"}` |
| `POST /api/demo/reset` | No body required |

Unknown table/worker/task IDs return 404; invalid inputs return 422; invalid state transitions return 400. Task statuses are `pending`, `offered`, `assigned`, `completed`, and `cancelled`. Table statuses are `eating`, `needs_check`, `being_served`, and `checked`.

## Vision boundary

`backend.vision.VisionProvider` defines the normalized observation boundary. `MockVisionProvider` is the default and currently powers the observation API. `RealVisionProvider` intentionally raises `NotImplementedError`; setting `VISION_PROVIDER=real` exits with a clear error rather than pretending to process video. No model, camera, or glasses integration is included.

`config/camera.json` records normalized image regions as `[x1, y1, x2, y2]` and `stable_observations_required: 5` for the future camera adapter. The mock API accepts each observation directly; it does not implement the five-frame stability filter. A real adapter must detect plates and apply that stability threshold before sending normalized observations to the event processor.

## Checks and current limits

Run the state and persistence regression suite with:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

The next hardware step is an Arduino or camera adapter that identifies plate A/B, applies the five-observation stability rule, and sends normalized `food`, `empty`, or `unknown` observations to `POST /api/observations`. It must not write SQLite directly. The `VisionProvider` is the boundary for this adapter; real inference is still unimplemented.

A future Meta API client can show the shared table state and offered task in a glasses companion view, then send accept, decline, complete, dessert, or water actions to these same endpoints. Keep the coordination rules and SQLite writes in this server; add authentication before exposing it beyond the local demo network. There is no Meta integration in this prototype.

The 10-test regression suite exercises state transitions and persistence at the store boundary. Live HTTP checks and a Chrome flow were also run separately. This prototype is intentionally limited to one table, two customers, two workers, and one in-process server. State is persistent locally; there is no authentication, network deployment hardening, real vision inference, multi-process coordination, task reassignment policy beyond the deterministic demo, or request-resolution workflow.
