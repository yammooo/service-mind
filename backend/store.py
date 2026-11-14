import json
import math
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

CAMERA_CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "camera.json").read_text())


def vision_state() -> dict:
    return {"regions": CAMERA_CONFIG["regions"], "stable_seconds": CAMERA_CONFIG["stable_seconds"], "monitoring": False}


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def initial_state() -> dict:
    return {
        "vision": vision_state(),
        "table": {
            "id": "1",
            "name": "Table 1",
            "status": "eating",
            "customers": [
                {"id": "customer-a", "name": "Customer A", "plate_id": "A", "food_state": "food"},
                {"id": "customer-b", "name": "Customer B", "plate_id": "B", "food_state": "food"},
            ],
        },
        "workers": [
            {"id": "A", "name": "Worker A", "availability": "busy"},
            {"id": "B", "name": "Worker B", "availability": "available"},
        ],
        "tasks": [],
        "requests": [],
        "_automation": {"not_ready_suppressed": False, "check_requested": False},
        "_next_task_id": 1,
        "_next_request_id": 1,
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def event(kind: str, message: str, **details) -> dict:
    return {"type": kind, "message": message, "details": details}


class Store:
    """SQLite-backed event processor; each state change and its events commit together."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS current_state (id INTEGER PRIMARY KEY CHECK (id = 1), value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL);"
            )
            if db.execute("SELECT 1 FROM current_state WHERE id = 1").fetchone() is None:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM current_state WHERE id = 1").fetchone() is None:
                    self._save_state(db, initial_state())
                    self._save_events(db, [event("demo_started", "ServiceMind demo started")])
                db.commit()
            else:
                db.execute("BEGIN IMMEDIATE")
                state = json.loads(db.execute("SELECT value FROM current_state WHERE id = 1").fetchone()["value"])
                defaults = vision_state()
                if "vision" not in state:
                    state["vision"] = defaults
                else:
                    state["vision"].setdefault("regions", defaults["regions"])
                    state["vision"].pop("stable_observations_required", None)
                    state["vision"].setdefault("stable_seconds", defaults["stable_seconds"])
                    state["vision"].setdefault("monitoring", False)
                migration_events = self._migrate_open_requests(state)
                self._save_state(db, state)
                if migration_events:
                    self._save_events(db, migration_events)
                db.commit()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    @staticmethod
    def _save_state(db, state: dict):
        db.execute(
            "INSERT INTO current_state (id, value) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET value = excluded.value",
            (json.dumps(state, separators=(",", ":")),),
        )

    @staticmethod
    def _save_events(db, events: list[dict]):
        timestamp = now()
        db.executemany(
            "INSERT INTO events (at, type, message, details) VALUES (?, ?, ?, ?)",
            [(timestamp, item["type"], item["message"], json.dumps(item["details"], separators=(",", ":"))) for item in events],
        )

    @staticmethod
    def _request_task(request: dict, task_id: str) -> dict:
        request_type = request["type"]
        return {
            "id": task_id,
            "type": "fulfill_request",
            "request_type": request_type,
            "request_id": request["id"],
            "reason": f"Bring {request_type} to Table 1.",
            "status": "pending",
            "table_id": request["table_id"],
            "offered_to": None,
            "worker_id": None,
            "declined_by": [],
        }

    @classmethod
    def _migrate_open_requests(cls, state: dict) -> list[dict]:
        """Give legacy open requests a linked fulfillment task, once."""
        state.setdefault("tasks", [])
        state.setdefault("_next_task_id", 1)
        linked_request_ids = {
            task.get("request_id")
            for task in state["tasks"]
            if task.get("type") == "fulfill_request"
        }
        emitted = []
        for request in state.get("requests", []):
            if request.get("status") != "open" or request.get("id") in linked_request_ids:
                continue
            task = cls._request_task(request, f"task-{state['_next_task_id']}")
            state["_next_task_id"] += 1
            state["tasks"].append(task)
            linked_request_ids.add(request["id"])
            emitted.append(event(
                "request_task_created",
                f"Fulfillment task restored for the open {request['type']} request",
                task_id=task["id"], request_id=request["id"], request_type=request["type"],
            ))
            emitted.extend(cls._offer_task(state, task))
        return emitted

    @staticmethod
    def _public_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if not key.startswith("_")}

    def snapshot(self) -> dict:
        with self._lock, closing(self._connect()) as db:
            row = db.execute("SELECT value FROM current_state WHERE id = 1").fetchone()
            return self._public_state(json.loads(row["value"]))

    def events(self) -> list[dict]:
        with self._lock, closing(self._connect()) as db:
            rows = db.execute("SELECT id, at, type, message, details FROM events ORDER BY id").fetchall()
            return [
                {"id": row["id"], "at": row["at"], "type": row["type"], "message": row["message"], "details": json.loads(row["details"])}
                for row in rows
            ]

    def _change(self, mutate):
        with self._lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                state = json.loads(db.execute("SELECT value FROM current_state WHERE id = 1").fetchone()["value"])
                emitted = mutate(state)
                if emitted:
                    self._save_state(db, state)
                    self._save_events(db, emitted)
                db.commit()
                return self._public_state(state)
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _worker(state: dict, worker_id: str) -> dict:
        worker = next((item for item in state["workers"] if item["id"] == worker_id), None)
        if worker is None:
            raise DomainError("Unknown worker", 404)
        return worker

    @staticmethod
    def _task(state: dict, task_id: str) -> dict:
        task = next((item for item in state["tasks"] if item["id"] == task_id), None)
        if task is None:
            raise DomainError("Unknown task", 404)
        return task

    @staticmethod
    def _offer_task(state: dict, task: dict) -> list[dict]:
        occupied = {
            item.get("offered_to") or item.get("worker_id")
            for item in state["tasks"]
            if item["status"] in {"offered", "assigned"} and item["id"] != task["id"]
        }
        worker = next(
            (w for w in state["workers"] if w["availability"] == "available" and w["id"] not in occupied and w["id"] not in task["declined_by"]),
            None,
        )
        if worker is None:
            task["status"] = "pending"
            task["offered_to"] = None
            return []
        task["status"] = "offered"
        task["offered_to"] = worker["id"]
        if task["type"] == "fulfill_request":
            message = f"{task['request_type'].title()} task offered to Worker {worker['id']}"
            details = {"task_id": task["id"], "worker_id": worker["id"], "request_id": task["request_id"]}
        else:
            message = f"Check table task offered to Worker {worker['id']}"
            details = {"task_id": task["id"], "worker_id": worker["id"]}
        return [event("task_offered", message, **details)]

    @staticmethod
    def _offer_pending(state: dict) -> list[dict]:
        emitted = []
        for task in state["tasks"]:
            if task["status"] == "pending":
                emitted.extend(Store._offer_task(state, task))
        return emitted

    def observe(self, table_id: str, plate_id: str, food_state: str):
        return self.observe_batch(table_id, {plate_id: food_state}, source="mock")

    def observe_batch(self, table_id: str, observations: dict[str, str], source: str):
        if table_id != "1":
            raise DomainError("Unknown table", 404)
        if not observations or any(plate_id not in {"A", "B"} for plate_id in observations):
            raise DomainError("observations must include plate A or B")
        if any(food_state not in {"food", "empty", "unknown"} for food_state in observations.values()):
            raise DomainError("food_state must be food, empty, or unknown")
        if source not in {"mock", "camera"}:
            raise DomainError("source must be mock or camera")

        def mutate(state):
            changed = []
            for plate_id, food_state in observations.items():
                customer = next(c for c in state["table"]["customers"] if c["plate_id"] == plate_id)
                if customer["food_state"] != food_state:
                    changed.append((customer, plate_id, customer["food_state"], food_state))
            if not changed:
                return []
            emitted = []
            for customer, plate_id, previous, food_state in changed:
                customer["food_state"] = food_state
                emitted.append(event("plate_observed", f"Plate {plate_id} at Table 1 changed from {previous} to {food_state}", table_id="1", plate_id=plate_id, food_state=food_state, source=source))
            automation = state["_automation"]
            if automation["not_ready_suppressed"] and any(food_state != "unknown" for _, _, _, food_state in changed):
                automation["not_ready_suppressed"] = False
                automation["check_requested"] = False
                emitted.append(event("correction_cleared", "New plate evidence cleared the not-ready correction"))
            if state["table"]["status"] == "checked":
                state["table"]["status"] = "eating"
                automation["check_requested"] = False
            if all(c["food_state"] == "empty" for c in state["table"]["customers"]):
                if not automation["check_requested"] and not automation["not_ready_suppressed"] and state["table"]["status"] != "being_served":
                    automation["check_requested"] = True
                    state["table"]["status"] = "needs_check"
                    task = {
                        "id": f"task-{state['_next_task_id']}",
                        "type": "check_table",
                        "reason": "Both plates appear empty; check whether the customers need anything.",
                        "status": "pending",
                        "table_id": "1",
                        "offered_to": None,
                        "worker_id": None,
                        "declined_by": [],
                    }
                    state["_next_task_id"] += 1
                    state["tasks"].append(task)
                    emitted.append(event("task_created", "Check table task created", task_id=task["id"], table_id="1"))
                    emitted.extend(self._offer_task(state, task))
            return emitted

        return self._change(mutate)

    @staticmethod
    def _validated_regions(regions: dict) -> dict:
        if not isinstance(regions, dict) or set(regions) != {"A", "B"}:
            raise DomainError("regions must contain exactly A and B")
        normalized = {}
        for plate_id, values in regions.items():
            if not isinstance(values, (list, tuple)) or len(values) != 4:
                raise DomainError(f"region {plate_id} must have four normalized coordinates")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
                raise DomainError(f"region {plate_id} coordinates must be finite numbers")
            x1, y1, x2, y2 = map(float, values)
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise DomainError(f"region {plate_id} must be an increasing rectangle inside the image")
            normalized[plate_id] = [x1, y1, x2, y2]
        return normalized

    def set_regions(self, regions: dict):
        normalized = self._validated_regions(regions)

        def mutate(state):
            vision = state["vision"]
            if vision["regions"] == normalized and not vision["monitoring"]:
                return []
            was_monitoring = vision["monitoring"]
            vision["regions"] = normalized
            vision["monitoring"] = False
            emitted = [event("vision_regions_saved", "Plate regions saved; observation monitoring paused", regions=normalized)]
            if was_monitoring:
                emitted.append(event("vision_monitoring_paused", "Observation monitoring paused after region changes"))
            return emitted

        return self._change(mutate)

    def set_monitoring(self, monitoring: bool):
        if not isinstance(monitoring, bool):
            raise DomainError("monitoring must be true or false")

        def mutate(state):
            vision = state["vision"]
            if not monitoring and not vision["monitoring"]:
                return []
            vision["monitoring"] = monitoring
            if not monitoring:
                return [event("vision_monitoring_paused", "Observation monitoring paused")]
            emitted = [event("vision_monitoring_started", "Observation monitoring started; waiting for fresh stable evidence")]
            active_check = any(
                task["type"] == "check_table" and task["status"] in {"pending", "offered", "assigned"}
                for task in state["tasks"]
            )
            for customer in state["table"]["customers"]:
                if customer["food_state"] != "unknown":
                    previous = customer["food_state"]
                    customer["food_state"] = "unknown"
                    emitted.append(event("plate_observed", f"Plate {customer['plate_id']} observation cleared for fresh monitoring", table_id="1", plate_id=customer["plate_id"], previous_state=previous, food_state="unknown", source="camera"))
            if not active_check and state["table"]["status"] in {"checked", "needs_check"}:
                state["table"]["status"] = "eating"
            state["_automation"]["check_requested"] = active_check
            return emitted

        return self._change(mutate)

    def camera_unavailable(self):
        def mutate(state):
            if not state["vision"]["monitoring"]:
                return []
            state["vision"]["monitoring"] = False
            emitted = [event("vision_camera_unavailable", "Camera unavailable; observation monitoring paused")]
            for customer in state["table"]["customers"]:
                if customer["food_state"] != "unknown":
                    previous = customer["food_state"]
                    customer["food_state"] = "unknown"
                    emitted.append(event("plate_observed", f"Plate {customer['plate_id']} state is unknown because the camera is unavailable", table_id="1", plate_id=customer["plate_id"], previous_state=previous, food_state="unknown", source="camera"))
            return emitted

        return self._change(mutate)

    def set_availability(self, worker_id: str, availability: str):
        if availability not in {"available", "busy"}:
            raise DomainError("availability must be available or busy")

        def mutate(state):
            worker = self._worker(state, worker_id)
            if availability == "available" and any(t["status"] == "assigned" and t["worker_id"] == worker_id for t in state["tasks"]):
                raise DomainError("Complete the assigned task before becoming available")
            if worker["availability"] == availability:
                return []
            worker["availability"] = availability
            emitted = [event("worker_availability_changed", f"Worker {worker_id} is now {availability}", worker_id=worker_id, availability=availability)]
            if availability == "busy":
                for task in state["tasks"]:
                    if task["status"] == "offered" and task["offered_to"] == worker_id:
                        task["status"] = "pending"
                        task["offered_to"] = None
                        emitted.extend(self._offer_task(state, task))
            else:
                emitted.extend(self._offer_pending(state))
            return emitted

        return self._change(mutate)

    def accept(self, task_id: str, worker_id: str):
        def mutate(state):
            worker = self._worker(state, worker_id)
            task = self._task(state, task_id)
            if task["status"] != "offered" or task["offered_to"] != worker_id:
                raise DomainError("Task is not offered to this worker")
            if worker["availability"] != "available":
                raise DomainError("Worker must be available to accept a task")
            task["status"] = "assigned"
            task["worker_id"] = worker_id
            task["offered_to"] = None
            worker["availability"] = "busy"
            if task["type"] == "check_table":
                state["table"]["status"] = "being_served"
                message = f"Worker {worker_id} accepted the table check"
            else:
                message = f"Worker {worker_id} accepted the {task['request_type']} task"
            return [event(
                "task_accepted", message, task_id=task_id, worker_id=worker_id,
                table_id=task["table_id"], request_id=task.get("request_id"),
            )]

        return self._change(mutate)

    def decline(self, task_id: str, worker_id: str):
        def mutate(state):
            worker = self._worker(state, worker_id)
            task = self._task(state, task_id)
            if task["status"] != "offered" or task["offered_to"] != worker_id:
                raise DomainError("Task is not offered to this worker")
            task["status"] = "pending"
            task["offered_to"] = None
            task["declined_by"].append(worker_id)
            description = "table check" if task["type"] == "check_table" else f"{task['request_type']} task"
            emitted = [event("task_declined", f"Worker {worker_id} declined the {description}", task_id=task_id, worker_id=worker_id)]
            emitted.extend(self._offer_task(state, task))
            emitted.extend(self._offer_pending(state))
            return emitted

        return self._change(mutate)

    def complete(self, task_id: str, worker_id: str):
        def mutate(state):
            worker = self._worker(state, worker_id)
            task = self._task(state, task_id)
            if task["status"] != "assigned" or task["worker_id"] != worker_id:
                raise DomainError("Task is not assigned to this worker")
            task["status"] = "completed"
            worker["availability"] = "available"
            if task["type"] == "check_table":
                state["table"]["status"] = "checked"
                message = f"Worker {worker_id} completed the table check"
            else:
                request = next((r for r in state["requests"] if r["id"] == task["request_id"]), None)
                if request is None or request["status"] != "open":
                    raise DomainError("The linked request is no longer open")
                request["status"] = "fulfilled"
                request["fulfilled_by"] = worker_id
                message = f"Worker {worker_id} delivered {task['request_type']} to Table 1"
            emitted = [event("task_completed", message, task_id=task_id, worker_id=worker_id, table_id="1", request_id=task.get("request_id"))]
            if task["type"] == "fulfill_request":
                emitted.append(event("request_fulfilled", message, request_id=task["request_id"], worker_id=worker_id, table_id="1"))
            emitted.extend(self._offer_pending(state))
            return emitted

        return self._change(mutate)

    def request(self, table_id: str, worker_id: str, request_type: str):
        if table_id != "1":
            raise DomainError("Unknown table", 404)
        if request_type not in {"dessert", "water"}:
            raise DomainError("request_type must be dessert or water")

        def mutate(state):
            self._worker(state, worker_id)
            existing = next((
                request for request in state["requests"]
                if request["table_id"] == table_id and request["type"] == request_type and request["status"] == "open"
            ), None)
            if existing is not None:
                return []
            request = {
                "id": f"request-{state['_next_request_id']}",
                "table_id": table_id,
                "worker_id": worker_id,
                "type": request_type,
                "status": "open",
            }
            state["_next_request_id"] += 1
            state["requests"].append(request)
            task = self._request_task(request, f"task-{state['_next_task_id']}")
            state["_next_task_id"] += 1
            state["tasks"].append(task)
            emitted = [
                event("request_created", f"{request_type.title()} requested at Table 1", request_id=request["id"], table_id=table_id, worker_id=worker_id, request_type=request_type),
                event("request_task_created", f"{request_type.title()} fulfillment task created", task_id=task["id"], request_id=request["id"], request_type=request_type),
            ]
            emitted.extend(self._offer_task(state, task))
            return emitted

        return self._change(mutate)

    def correct_not_ready(self, table_id: str):
        if table_id != "1":
            raise DomainError("Unknown table", 404)

        def mutate(state):
            if state["_automation"]["not_ready_suppressed"]:
                return []
            state["_automation"]["not_ready_suppressed"] = True
            state["_automation"]["check_requested"] = True
            state["table"]["status"] = "eating"
            emitted = [event("customer_not_ready", "Customers are not ready; follow-up suppressed until new plate evidence or reset", table_id=table_id)]
            for task in state["tasks"]:
                if task["type"] != "check_table":
                    continue
                if task["status"] in {"pending", "offered"} and task["table_id"] == table_id:
                    task["status"] = "cancelled"
                    task["offered_to"] = None
                    emitted.append(event("task_cancelled", "Table check cancelled after not-ready correction", task_id=task["id"], table_id=table_id))
                elif task["status"] == "assigned" and task["table_id"] == table_id:
                    worker = self._worker(state, task["worker_id"])
                    worker["availability"] = "available"
                    task["status"] = "cancelled"
                    emitted.append(event("task_cancelled", "Assigned table check cancelled after not-ready correction", task_id=task["id"], table_id=table_id))
            emitted.extend(self._offer_pending(state))
            return emitted

        return self._change(mutate)

    def reset(self):
        def mutate(state):
            previous = {"task_count": len(state["tasks"]), "request_count": len(state["requests"])}
            next_task_id = state["_next_task_id"]
            next_request_id = state["_next_request_id"]
            regions = state["vision"]["regions"]
            state.clear()
            state.update(initial_state())
            state["vision"]["regions"] = regions
            state["_next_task_id"] = next_task_id
            state["_next_request_id"] = next_request_id
            return [event("demo_reset", "Demo state reset to its starting values", previous=previous)]

        return self._change(mutate)
