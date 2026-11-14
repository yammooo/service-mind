# ServiceMind

A local ServiceMind prototype. The active milestone is a live on-device camera view at `/vision` on the Arduino UNO Q. It shows a pretrained YOLOv8n model's general COCO object detections over the USB camera feed. It does not identify plates, food state, or service actions.

## Live camera milestone

The app uses OpenCV DNN to run a small ONNX model on CPU; it avoids PyTorch and does not save camera frames. The first run downloads the model separately because model weights are excluded from Git.

```bash
cd /home/arduino/service-mind-vision
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p models
curl -L --fail -o models/yolov8n.onnx https://huggingface.co/unileon-robotics/YOLO8-ONNX/resolve/66e5888954d3454da709847e77b36df31f756aa9/yolov8n.onnx
uvicorn backend.live_vision:app --host 0.0.0.0 --port 7000
```

Open `http://localhost:7000/vision` locally or `http://10.0.41.164:7000/vision` from the same Wi-Fi. The page reports camera/model errors, detected COCO classes and confidence, inference FPS, inference time, and frame age. The process defaults to camera index `0`; override with `CAMERA_INDEX`. Override the ONNX file with `VISION_MODEL_PATH`.

The live vision endpoints are `GET /vision` for the page, `GET /api/vision/stream` for the MJPEG feed, and `GET /api/vision/status` for camera, model, detections, FPS, inference time, and frame age.

The UNO Q currently delivers about 1.5–2.3 inference FPS at 640×480 (roughly 424–588 ms/model run in observed samples; browser samples showed 1.5–1.7 FPS and 777–892 ms frame age). The web page shows current measurements. Only one Uvicorn worker should run because it owns the USB camera. The page and feed have no authentication and are intended for the local demo Wi-Fi only.

The `yolov8n.onnx` file comes from [unileon-robotics/YOLO8-ONNX](https://huggingface.co/unileon-robotics/YOLO8-ONNX), pinned to revision `66e5888954d3454da709847e77b36df31f756aa9`. Its model card says it is an Ultralytics YOLOv8 checkpoint exported to ONNX at 640×480, trained for 80 COCO classes, and licensed AGPL-3.0; it is not a model trained for this restaurant or for plate/food recognition. Review that license before reuse beyond this prototype. Model weights are not checked into the repo.

This view is exploratory: the generic COCO model can detect labels such as person, chair, cup, bottle, and dining table, but does not include a plate class or reliably recognize table contents. It makes no meal-state claims and does not feed the service coordination API.

## Existing coordination prototype

The app also contains the separate deterministic service coordination prototype described below. That later milestone still uses mock plate observations and does not consume the camera detections.

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

`backend.vision.VisionProvider` defines the normalized observation boundary. `MockVisionProvider` is the default and currently powers the observation API. `RealVisionProvider` intentionally raises `NotImplementedError`; setting `VISION_PROVIDER=real` exits with a clear error rather than pretending to produce plate observations. The live view (`backend.live_vision`) does not send detections into this API. No glasses integration is included.

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
