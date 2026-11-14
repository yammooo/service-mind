import threading
import time

from backend.store import DomainError, Store


class VisionMonitor:
    """Apply a delayed banana-only heuristic to fresh camera frames."""

    def __init__(self, store: Store, max_frame_age: float = 2.0):
        self.store = store
        self.max_frame_age = max_frame_age
        self.lock = threading.RLock()
        self.generation = 0
        self.started_at = None
        self.last_frame_id = -1
        self.last_capture_at = None
        self._reset_evidence()
        if self.store.snapshot()["vision"]["monitoring"]:
            self.store.set_monitoring(False)

    def _reset_evidence(self):
        self.evidence = {plate_id: {"candidate": None, "since": None} for plate_id in ("A", "B")}
        self.submitted_state = {"A": "unknown", "B": "unknown"}
        self.last_frame_id = -1
        self.last_capture_at = None

    def _camera_gap(self):
        for evidence in self.evidence.values():
            evidence.update(candidate=None, since=None)
        state = self.store.snapshot()
        self.submitted_state = {customer["plate_id"]: customer["food_state"] for customer in state["table"]["customers"]}
        unknown = {plate_id: "unknown" for plate_id, food_state in self.submitted_state.items() if food_state != "unknown"}
        if unknown:
            self.store.observe_batch("1", unknown, source="camera")
        self.submitted_state = {"A": "unknown", "B": "unknown"}

    def begin_frame(self, captured_at: float, frame_id: int) -> dict:
        with self.lock:
            vision = self.store.snapshot()["vision"]
            return {
                "generation": self.generation,
                "monitoring": vision["monitoring"],
                "regions": vision["regions"],
                "stable_seconds": vision["stable_seconds"],
                "started_at": self.started_at,
                "captured_at": captured_at,
                "frame_id": frame_id,
            }

    @staticmethod
    def _inside(x: float, y: float, rect: tuple[float, float, float, float]) -> bool:
        return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]

    @staticmethod
    def _classify(detections: list[dict], region: list[float]) -> str:
        rect = (region[0] * 640, region[1] * 480, region[2] * 640, region[3] * 480)
        for detection in detections:
            box = detection["box"]
            if detection["label"] == "banana" and VisionMonitor._inside((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, rect):
                return "food"
        return "empty"

    def consume_frame(self, context: dict, detections: list[dict]) -> bool:
        captured_at = context["captured_at"]
        with self.lock:
            vision = self.store.snapshot()["vision"]
            if (
                context["generation"] != self.generation
                or not context["monitoring"]
                or not vision["monitoring"]
                or self.started_at is None
                or context["started_at"] != self.started_at
                or context["frame_id"] <= self.last_frame_id
            ):
                return False
            self.last_frame_id = context["frame_id"]
            if captured_at < self.started_at:
                return False
            if time.monotonic() - captured_at > self.max_frame_age:
                self._camera_gap()
                self.last_capture_at = None
                return False
            if self.last_capture_at is not None and captured_at - self.last_capture_at > self.max_frame_age:
                self._camera_gap()
            self.last_capture_at = captured_at
            observations = {}
            stable_seconds = context["stable_seconds"]
            for plate_id, region in context["regions"].items():
                evidence = self.evidence[plate_id]
                detected = self._classify(detections, region)
                if evidence["candidate"] != detected:
                    evidence.update(candidate=detected, since=captured_at)
                if captured_at - evidence["since"] >= stable_seconds and self.submitted_state[plate_id] != detected:
                    observations[plate_id] = detected
                    self.submitted_state[plate_id] = detected
            if observations:
                self.store.observe_batch("1", observations, source="camera")
            return True

    def _invalidate(self):
        self.generation += 1
        self._reset_evidence()

    def set_regions(self, regions: dict):
        with self.lock:
            normalized = self.store._validated_regions(regions)
            self._invalidate()
            self.started_at = None
            return self.store.set_regions(normalized)

    def start(self):
        with self.lock:
            self._invalidate()
            self.started_at = time.monotonic()
            return self.store.set_monitoring(True)

    def pause(self):
        with self.lock:
            self._invalidate()
            self.started_at = None
            return self.store.set_monitoring(False)

    def reset(self):
        with self.lock:
            self._invalidate()
            self.started_at = None
            return self.store.reset()

    def observe_mock(self, table_id: str, plate_id: str, food_state: str):
        if table_id != "1":
            raise DomainError("Unknown table", 404)
        if plate_id not in {"A", "B"}:
            raise DomainError("plate_id must be A or B")
        if food_state not in {"food", "empty", "unknown"}:
            raise DomainError("food_state must be food, empty, or unknown")
        with self.lock:
            self._invalidate()
            self.started_at = None
            state = self.store.snapshot()
            if state["vision"]["monitoring"]:
                self.store.set_monitoring(False)
            return self.store.observe(table_id, plate_id, food_state)

    def correct_not_ready(self, table_id: str):
        if table_id != "1":
            raise DomainError("Unknown table", 404)
        with self.lock:
            self._invalidate()
            state = self.store.snapshot()
            self.started_at = time.monotonic() if state["vision"]["monitoring"] else None
            self.submitted_state = {customer["plate_id"]: customer["food_state"] for customer in state["table"]["customers"]}
            return self.store.correct_not_ready(table_id)

    def camera_unavailable(self):
        with self.lock:
            self._invalidate()
            self.started_at = None
            return self.store.camera_unavailable()

    def status(self) -> list[dict]:
        with self.lock:
            customers = {customer["plate_id"]: customer["food_state"] for customer in self.store.snapshot()["table"]["customers"]}
            return [
                {
                    "plate_id": plate_id,
                    "food_state": customers[plate_id],
                    "candidate": evidence["candidate"],
                    "stable_for_ms": round(max(0, time.monotonic() - evidence["since"]) * 1000) if evidence["since"] is not None else 0,
                }
                for plate_id, evidence in self.evidence.items()
            ]
