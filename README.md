# ServiceMind

ServiceMind is a local restaurant-team prototype. The integrated demo runs one FastAPI/Uvicorn process, SQLite, plain responsive HTML/CSS/JavaScript, and the Arduino UNO Q USB camera. It keeps camera frames and YOLO detections in memory; SQLite stores coordination state and timestamped events.

## Run on the Arduino UNO Q

The board's deployed directory is `/home/arduino/service-mind-vision`; its `.venv` and downloaded model are kept outside Git.

```bash
cd /home/arduino/service-mind-vision
source .venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 7000 --timeout-graceful-shutdown 5
```

Open these pages on the same Wi-Fi:

- Admin and live annotated camera: `http://10.0.41.164:7000/admin` (also `/`)
- Worker A: `http://10.0.41.164:7000/worker/A`
- Worker B: `http://10.0.41.164:7000/worker/B`
- API schema: `http://10.0.41.164:7000/docs`
- Camera-only debug view: `http://10.0.41.164:7000/vision`

Run exactly one Uvicorn worker: the process owns the USB camera and in-memory inference loop. The server has no authentication and is intended only for the local demo Wi-Fi. The default database is `data/service-mind.sqlite3`; set `SERVICE_MIND_DB=/path/to/file.sqlite3` to change it. Configure the camera with `CAMERA_INDEX` and model with `VISION_MODEL_PATH`.

For a fresh Linux ARM64 setup, install Python 3.10+, use the versions in `requirements.txt`, download the model separately (weights are intentionally excluded from Git), then start the app:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p models
curl -L --fail -o models/yolov8n.onnx https://huggingface.co/unileon-robotics/YOLO8-ONNX/resolve/66e5888954d3454da709847e77b36df31f756aa9/yolov8n.onnx
uvicorn backend.main:app --host 0.0.0.0 --port 7000 --timeout-graceful-shutdown 5
```

The ONNX model comes from [unileon-robotics/YOLO8-ONNX](https://huggingface.co/unileon-robotics/YOLO8-ONNX), pinned to revision `66e5888954d3454da709847e77b36df31f756aa9`. The model card identifies it as an Ultralytics YOLOv8 export trained on 80 COCO classes and licensed AGPL-3.0. Review that license before reuse beyond this prototype. The app runs OpenCV DNN on CPU; no PyTorch, cloud inference, or frame recording is used.

## Start automatically at boot

After installing the app, virtualenv, and model at the paths above, install the systemd unit once:

```bash
sudo install -m 0644 deploy/service-mind.service /etc/systemd/system/service-mind.service
sudo systemctl daemon-reload
sudo systemctl enable --now service-mind.service
sudo systemctl status service-mind.service
```

The service runs as `arduino`, waits for networking and `/dev/video0`, and starts the camera feed with observation monitoring **paused**. Start observing from `/admin` when ready. App output is in the system journal:

```bash
sudo journalctl -u service-mind.service -f
sudo systemctl restart service-mind.service
sudo systemctl stop service-mind.service
sudo systemctl poweroff
```

`systemctl stop` stops the app without changing the SQLite state. After a code update, restart the service; the current state, regions, requests, tasks, and history stay in the database. Before removing board power, run `sudo systemctl poweroff` and wait for shutdown to finish.

## Admin and worker demo

The admin page shows the live YOLO feed with detections, confidence, inference FPS/time, frame age and camera/model status. Draw or resize the normalized A/B rectangles over the camera image and save them. Changing regions pauses observation processing and clears stabilization evidence. Press **Start observing** to begin a fresh evidence window or **Pause observing** to stop updating the shared plate state while camera detections continue. Reset restores the deterministic world, pauses observation, retains saved regions and keeps the timestamped history with a reset event. The collapsed mock controls are for explicitly testing the coordination API.

The initial world has two customers eating, Worker A busy, Worker B available, and no tasks. The default regions are A `[0.05, 0.15, 0.45, 0.85]`, B `[0.55, 0.15, 0.95, 0.85]`; stability is five distinct fresh frames. The current camera heuristic can arm a region after five consecutive frames containing a COCO food label, then report empty after five consecutive fresh frames with no food detection. When both plates become empty, the existing deterministic rule offers one table-check task to an available worker. Once a worker accepts the check, Dessert and Water create linked fulfillment tasks for another available worker; they stay pending if everyone else is busy. A fulfillment task can be accepted and completed independently without ending the table check. Repeating the same request type while its request is still open does not create a duplicate. If a person overlaps a region, the observation is unknown. Pause, stale frames, or camera/model errors never mean empty.

This is an exploratory heuristic, not reliable plate recognition. The generic model has no plate class and can miss food, confuse objects, or fail to see food under hands/occlusion; it only treats person overlap as a conservative unknown. Fixed camera placement and suitable lighting are required. Do not use its inferred state as a safety-critical or operational guarantee.

## API

All paths are relative to the current host. `GET /api/state` returns shared table, customer, worker, task, request, and vision-monitor state. `GET /api/events` returns chronological audit history. Mutations and their events are committed together in SQLite transactions.

| Endpoint | Body / behavior |
| --- | --- |
| `GET /api/vision/status` | Camera, model, detection boxes/labels/confidence, FPS, inference time, frame age, monitor state |
| `GET /api/vision/stream` | Annotated MJPEG feed |
| `POST /api/vision/regions` | `{"regions":{"A":[0.05,0.15,0.45,0.85],"B":[0.55,0.15,0.95,0.85]}}`; validates and saves both regions, pauses monitor |
| `POST /api/vision/start` / `pause` | Begin fresh observation window (requires a healthy, fresh camera frame) / pause state observations |
| `POST /api/observations` | Explicit mock observation: `{"table_id":"1","plate_id":"A","food_state":"empty"}` |
| `POST /api/workers/{id}/availability` | `{"availability":"available"}` or `busy` |
| `POST /api/tasks/{id}/accept`, `/decline`, `/complete` | `{"worker_id":"B"}` |
| `POST /api/tables/{id}/requests` | `{"worker_id":"B","request_type":"dessert"}` or `water`; creates one linked fulfillment task offered to an available worker or left pending. A repeated same-type request is ignored while it remains open. |
| `POST /api/tables/{id}/correction` | `{"correction":"not_ready"}` |
| `POST /api/demo/reset` | Reset world; pause observations, retain regions and append reset event |

## Development and tests

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
uvicorn backend.main:app --host 0.0.0.0 --port 7000 --timeout-graceful-shutdown 5
```

`backend.vision.VisionProvider` remains the explicit normalized-observation boundary. `MockVisionProvider` supports deterministic mock API tests; `RealVisionProvider` raises `NotImplementedError`, and `VISION_PROVIDER=real` fails clearly rather than pretending to provide general real plate understanding. The current live camera integration is the separate documented YOLO/COCO heuristic in `backend.monitoring`; it does not use an LLM. On startup, any legacy open request missing a linked fulfillment task is migrated transactionally and idempotently, with its prior event history retained. The next reliable hardware step needs a restaurant-specific model and validated empty/food/unknown semantics before any operational reliance.
