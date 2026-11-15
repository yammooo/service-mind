"""Standalone live YOLO camera view; detections do not feed service state."""

import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
MODEL = Path(os.getenv("VISION_MODEL_PATH", ROOT / "models" / "yolov8n.onnx"))
CAMERA = int(os.getenv("CAMERA_INDEX", "0"))
WIDTH, HEIGHT = 640, 480
# shortcut: COCO objects only, upgrade when a restaurant-specific plate/food model is available.
COCO = """person
bicycle
car
motorcycle
airplane
bus
train
truck
boat
traffic light
fire hydrant
stop sign
parking meter
bench
bird
cat
dog
horse
sheep
cow
elephant
bear
zebra
giraffe
backpack
umbrella
handbag
tie
suitcase
frisbee
skis
snowboard
sports ball
kite
baseball bat
baseball glove
skateboard
surfboard
tennis racket
bottle
wine glass
cup
fork
knife
spoon
bowl
banana
apple
sandwich
orange
broccoli
carrot
hot dog
pizza
donut
cake
chair
couch
potted plant
bed
dining table
toilet
tv
laptop
mouse
remote
keyboard
cell phone
microwave
oven
toaster
sink
refrigerator
book
clock
vase
scissors
teddy bear
hair drier
toothbrush""".splitlines()

app = FastAPI(title="ServiceMind", description="A local restaurant service coordination prototype")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
state_lock = threading.Lock()
stop_event = threading.Event()
worker_thread = None
latest_jpeg = None
status = {"camera": "waiting", "model": str(MODEL), "fps": 0, "inference_ms": 0, "frame_age_ms": None, "detections": [], "error": None}


def decode_predictions(output, confidence=0.2):
    """Decode the fixed YOLOv8n COCO output into xywh boxes and labels."""
    predictions = np.asarray(output).squeeze()
    if predictions.ndim != 2:
        raise ValueError(f"Expected a 2D YOLO output, got {predictions.shape}")
    if predictions.shape[0] == 84:
        predictions = predictions.T
    if predictions.shape[1] != 84:
        raise ValueError(f"Expected 84 COCO values per box, got {predictions.shape}")
    scores = predictions[:, 4:]
    classes = scores.argmax(axis=1)
    confidences = scores[np.arange(len(scores)), classes]
    indices = np.flatnonzero(confidences >= confidence)
    boxes, selected_scores, selected_classes, labels = [], [], [], []
    for index in indices:
        cx, cy, width, height = predictions[index, :4]
        boxes.append([float(cx - width / 2), float(cy - height / 2), float(width), float(height)])
        selected_scores.append(float(confidences[index]))
        selected_classes.append(int(classes[index]))
        labels.append(COCO[selected_classes[-1]])
    if not boxes:
        return []
    kept = cv2.dnn.NMSBoxesBatched(boxes, selected_scores, selected_classes, confidence, 0.45)
    return [(boxes[int(index)], selected_scores[int(index)], labels[int(index)]) for index in np.asarray(kept).reshape(-1)]


def _publish(jpeg=None, **values):
    global latest_jpeg
    with state_lock:
        if jpeg is not None:
            latest_jpeg = jpeg
        status.update(values)


def _error_jpeg(message):
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    cv2.putText(frame, "Live vision unavailable", (25, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 180, 255), 2)
    cv2.putText(frame, message[:70], (25, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
    return cv2.imencode(".jpg", frame)[1].tobytes()


def camera_worker():
    camera = None
    try:
        if not MODEL.is_file():
            raise FileNotFoundError(f"YOLO model missing: {MODEL}")
        net = cv2.dnn.readNetFromONNX(str(MODEL))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        camera = cv2.VideoCapture(CAMERA, cv2.CAP_V4L2)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        if not camera.isOpened():
            raise RuntimeError(f"Cannot open camera {CAMERA}")
        _publish(camera="connected", error=None)
        previous = None
        frame_id = 0
        while not stop_event.is_set():
            ok, frame = camera.read()
            captured_at = time.perf_counter()
            if not ok:
                raise RuntimeError("Camera stopped returning frames")
            frame_id += 1
            if frame.shape[:2] != (HEIGHT, WIDTH):
                frame = cv2.resize(frame, (WIDTH, HEIGHT))
            monitor = getattr(app.state, "vision_monitor", None)
            context = monitor.begin_frame(captured_at, frame_id) if monitor else None
            started = time.perf_counter()
            net.setInput(cv2.dnn.blobFromImage(frame, 1 / 255.0, (WIDTH, HEIGHT), swapRB=True, crop=False))
            detections = decode_predictions(net.forward())
            inference_ms = (time.perf_counter() - started) * 1000
            for (x, y, width, height), score, label in detections:
                left, top = max(0, int(x)), max(0, int(y))
                right, bottom = min(WIDTH - 1, int(x + width)), min(HEIGHT - 1, int(y + height))
                cv2.rectangle(frame, (left, top), (right, bottom), (35, 210, 145), 2)
                cv2.putText(frame, f"{label} {score:.2f}", (left, max(20, top - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (35, 210, 145), 2)
            detection_rows = [{"label": label, "confidence": round(score, 2), "box": [round(x), round(y), round(x + width), round(y + height)]} for (x, y, width, height), score, label in detections]
            if monitor and context:
                monitor.consume_frame(context, detection_rows)
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if not ok:
                raise RuntimeError("Could not encode annotated camera frame")
            now = time.perf_counter()
            fps = 1 / (captured_at - previous) if previous else 0
            previous = captured_at
            _publish(encoded.tobytes(), camera="connected", fps=round(fps, 1), inference_ms=round(inference_ms), frame_at=captured_at, detections=detection_rows, error=None)
    except Exception as error:
        _publish(_error_jpeg(str(error)), camera="error", fps=0, inference_ms=0, frame_at=None, frame_age_ms=None, detections=[], error=str(error))
        monitor = getattr(app.state, "vision_monitor", None)
        if monitor:
            monitor.camera_unavailable()
    finally:
        if camera is not None:
            camera.release()
        if stop_event.is_set():
            _publish(camera="stopped", fps=0, inference_ms=0, frame_at=None, frame_age_ms=None, detections=[])


@app.on_event("startup")
def start_camera():
    global worker_thread
    if getattr(app.state, "mode", "legacy") == "simulation":
        return
    worker_thread = threading.Thread(target=camera_worker, name="vision-camera", daemon=True)
    worker_thread.start()


@app.on_event("shutdown")
def stop_camera():
    stop_event.set()
    if worker_thread:
        worker_thread.join(timeout=3)


@app.get("/vision", include_in_schema=False)
def page():
    return FileResponse(ROOT / "frontend" / "vision.html")


@app.get("/api/vision/status")
def get_status():
    with state_lock:
        result = dict(status)
    if result.get("frame_at"):
        result["frame_age_ms"] = round((time.perf_counter() - result.pop("frame_at")) * 1000)
    monitor = getattr(app.state, "vision_monitor", None)
    if monitor:
        result["plate_observations"] = monitor.status()
    return result


def camera_ready() -> bool:
    with state_lock:
        captured_at = status.get("frame_at")
        return status["camera"] == "connected" and captured_at is not None and time.perf_counter() - captured_at <= 2.0


@app.get("/api/vision/stream")
def stream():
    def frames():
        while True:
            with state_lock:
                jpeg = latest_jpeg
            if jpeg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(0.25)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")
