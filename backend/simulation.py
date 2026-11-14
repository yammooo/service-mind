import json
import re
import threading
from contextlib import closing
from pathlib import Path

from backend.store import DomainError, Store, event, now

ROOMS = {"room-1": "Room 1", "room-2": "Room 2"}
TABLE_IDS = tuple(str(i) for i in range(1, 7))
WORKERS = {
    "W1": ("Worker 1", "room-1"),
    "W2": ("Worker 2", "room-1"),
    "W3": ("Worker 3", "room-2"),
}
TASK_TYPES = ("food_delivery", "service_request", "check_table")


def empty_hold() -> dict:
    return {"active": False, "worker_id": None, "source": None, "reason": None, "until_seconds": None, "overdue": False, "needs_confirmation": False}


def simulation_initial_state() -> dict:
    tables = [
        {"id": table_id, "name": f"Table {table_id}", "room": "room-1" if int(table_id) <= 3 else "room-2", "status": "eating", "meal_state": "eating", "hold": empty_hold()}
        for table_id in TABLE_IDS
    ]
    return {
        "mode": "simulation",
        "clock_seconds": 0,
        "rooms": [
            {"id": room_id, "name": name, "tables": [table["id"] for table in tables if table["room"] == room_id]}
            for room_id, name in ROOMS.items()
        ],
        "tables": tables,
        "workers": [
            {"id": worker_id, "name": name, "primary_room": room, "availability": "available", "available_since": 0}
            for worker_id, (name, room) in WORKERS.items()
        ],
        "tasks": [],
        "requests": [],
        "orders": [],
        "_next_task_id": 1,
        "_next_request_id": 1,
        "_next_order_id": 1,
        "_next_task_seq": 1,
    }


class SimulationStore(Store):
    """Simulation mode on the same SQLite transaction and event store as the demo."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS current_state (id INTEGER PRIMARY KEY CHECK (id = 1), value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS app_mode (id INTEGER PRIMARY KEY CHECK (id = 1), value TEXT NOT NULL);"
            )
            mode = db.execute("SELECT value FROM app_mode WHERE id = 1").fetchone()
            if mode and mode["value"] != "simulation":
                raise RuntimeError(f"Database belongs to {mode['value']} mode")
            existing = db.execute("SELECT value FROM current_state WHERE id = 1").fetchone()
            if existing:
                state = json.loads(existing["value"])
                if state.get("mode") != "simulation":
                    raise RuntimeError("Refusing to use a non-simulation database in simulation mode")
            db.execute("INSERT OR IGNORE INTO app_mode (id, value) VALUES (1, 'simulation')")
            if existing is None:
                db.execute("BEGIN IMMEDIATE")
                self._save_state(db, simulation_initial_state())
                self._save_events(db, [event("simulation_started", "Restaurant simulation started", table_count=6, room_count=2, worker_count=3)])
                db.commit()

    @staticmethod
    def _public_state(state: dict) -> dict:
        public = Store._public_state(state)
        for task in public["tasks"]:
            task.pop("_route_key", None)
        return public

    @staticmethod
    def _table(state: dict, table_id: str) -> dict:
        table = next((item for item in state["tables"] if item["id"] == table_id), None)
        if table is None:
            raise DomainError("Unknown table", 404)
        return table

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
    def _order(state: dict, order_id: str) -> dict | None:
        return next((item for item in state["orders"] if item["id"] == order_id), None)

    @staticmethod
    def _request(state: dict, request_id: str) -> dict | None:
        return next((item for item in state["requests"] if item["id"] == request_id), None)

    @staticmethod
    def _task_room(state: dict, task: dict) -> str:
        return SimulationStore._table(state, task["table_id"])["room"]

    @staticmethod
    def _prerequisite(state: dict, task: dict) -> bool:
        table = SimulationStore._table(state, task["table_id"])
        if task["type"] == "food_delivery":
            order = SimulationStore._order(state, task["order_id"])
            return bool(order and order["status"] == "ready")
        if task["type"] == "service_request":
            request = SimulationStore._request(state, task["request_id"])
            return bool(request and request["status"] == "open")
        return task["type"] == "check_table" and table["status"] == "needs_check"

    @staticmethod
    def _blocked(state: dict, task: dict) -> bool:
        table = SimulationStore._table(state, task["table_id"])
        return task["type"] == "food_delivery" and table["hold"]["active"]

    @staticmethod
    def _priority(task: dict) -> tuple[int, int]:
        if task["urgent"]:
            group = 0
        elif task["wait_seconds"] >= 90:
            group = 1
        else:
            group = {"food_delivery": 2, "service_request": 3, "check_table": 4}[task["type"]]
        return group, -task["wait_seconds"]

    @staticmethod
    def _decision(state: dict, task: dict, key: str, reason: str) -> list[dict]:
        if task.get("_route_key") == key:
            task["decision_reason"] = reason
            return []
        task["_route_key"] = key
        task["decision_reason"] = reason
        return [event("routing_decision", reason, task_id=task["id"], task_type=task["type"], table_id=task["table_id"], wait_seconds=task["wait_seconds"], urgent=task["urgent"])]

    @classmethod
    def _route(cls, state: dict) -> list[dict]:
        emitted = []
        for task in state["tasks"]:
            if task["status"] != "offered":
                continue
            worker = cls._worker(state, task["offered_to"])
            if worker["availability"] == "available" and cls._prerequisite(state, task) and not cls._blocked(state, task):
                continue
            reason = "Table hold blocks food delivery" if cls._blocked(state, task) else "The offer is no longer eligible"
            task.update(status="pending", offered_to=None)
            emitted.append(event("task_offer_withdrawn", f"Offer withdrawn for {task['id']}: {reason.lower()}", task_id=task["id"], worker_id=worker["id"], table_id=task["table_id"], reason=reason))

        occupied = {
            task.get("offered_to") or task.get("worker_id")
            for task in state["tasks"]
            if task["status"] in {"offered", "assigned"}
        }
        reserved_tasks = {task["id"] for task in state["tasks"] if task["status"] == "offered"}
        while True:
            candidates = []
            free_workers = [worker for worker in state["workers"] if worker["availability"] == "available" and worker["id"] not in occupied]
            if not free_workers:
                break
            for task in state["tasks"]:
                if task["status"] != "pending" or task["id"] in reserved_tasks or not cls._prerequisite(state, task) or cls._blocked(state, task):
                    continue
                for worker in free_workers:
                    if worker["id"] in task["declined_by"]:
                        continue
                    room_match = worker["primary_room"] != cls._task_room(state, task)
                    available_for = state["clock_seconds"] - worker["available_since"]
                    candidates.append((
                        *cls._priority(task), room_match, -available_for, worker["id"], task["created_seq"], task, worker,
                    ))
            if not candidates:
                break
            candidates.sort(key=lambda row: row[:-2])
            _, _, _, _, worker_id, _, task, worker = candidates[0]
            rank = cls._priority(task)[0]
            if task["urgent"]:
                reason = "Explicit urgent task routed first"
            elif task["wait_seconds"] >= 90:
                reason = f"{task['wait_seconds']} eligible seconds made this task overdue"
            else:
                reason = {"food_delivery": "Ready food delivery", "service_request": "Customer request", "check_table": "Table check from finished-meal evidence"}[task["type"]]
            if worker["primary_room"] == cls._task_room(state, task):
                reason += f"; Worker {worker['id']} matches the room"
            else:
                reason += f"; Worker {worker['id']} had the longest available time"
            task.update(status="offered", offered_to=worker["id"], decision_reason=reason, _route_key=f"offered:{worker['id']}:{rank}:{task['wait_seconds']}")
            occupied.add(worker["id"])
            reserved_tasks.add(task["id"])
            emitted.append(event("task_offered", f"{task['id']} offered to Worker {worker['id']}: {reason}", task_id=task["id"], worker_id=worker["id"], table_id=task["table_id"], priority=rank, wait_seconds=task["wait_seconds"], reason=reason))

        for task in state["tasks"]:
            if task["status"] != "pending":
                continue
            if not cls._prerequisite(state, task):
                reason = "Waiting for its table or request prerequisite"
            elif cls._blocked(state, task):
                reason = "Table service hold blocks food delivery; eligible wait is paused"
            elif not any(worker["availability"] == "available" and worker["id"] not in occupied for worker in state["workers"]):
                reason = "Waiting for an available worker; eligible wait continues"
            elif task["declined_by"] and all(worker["id"] in task["declined_by"] for worker in state["workers"] if worker["availability"] == "available" and worker["id"] not in occupied):
                reason = "All available workers declined this task"
            else:
                reason = "Waiting behind higher-priority work"
            emitted.extend(cls._decision(state, task, f"waiting:{reason}", reason))
        return emitted

    @classmethod
    def _new_task(cls, state: dict, task_type: str, table_id: str, reason: str, urgent: bool = False, **details) -> dict:
        task = {
            "id": f"task-{state['_next_task_id']}",
            "type": task_type,
            "table_id": table_id,
            "reason": reason,
            "status": "pending",
            "offered_to": None,
            "worker_id": None,
            "urgent": urgent,
            "wait_seconds": 0,
            "created_seq": state["_next_task_seq"],
            "decision_reason": "Waiting for routing",
            "declined_by": [],
            "_route_key": None,
        }
        task.update(details)
        state["_next_task_id"] += 1
        state["_next_task_seq"] += 1
        state["tasks"].append(task)
        return task

    def order_ready(self, table_id: str, source: str, order_id: str | None = None, urgent: bool = False):
        if source not in {"kitchen", "bar"}:
            raise DomainError("source must be kitchen or bar")
        if order_id is not None and (not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", order_id)):
            raise DomainError("order_id must contain 1 to 64 letters, numbers, underscores, or hyphens")
        if not isinstance(urgent, bool):
            raise DomainError("urgent must be true or false")

        def mutate(state):
            table = self._table(state, table_id)
            if order_id:
                assigned_id = order_id
            else:
                while self._order(state, f"order-{state['_next_order_id']}"):
                    state["_next_order_id"] += 1
                assigned_id = f"order-{state['_next_order_id']}"
                state["_next_order_id"] += 1
            existing = self._order(state, assigned_id)
            if existing:
                if (existing["table_id"], existing["source"], existing["urgent"]) != (table_id, source, urgent):
                    raise DomainError("order_id is already used for a different order", 409)
                return []
            order = {"id": assigned_id, "table_id": table_id, "source": source, "status": "ready", "urgent": urgent, "created_at": state["clock_seconds"]}
            state["orders"].append(order)
            task = self._new_task(state, "food_delivery", table_id, f"{source.title()} order {assigned_id} ready for {table['name']}", urgent, order_id=assigned_id, source=source)
            emitted = [event("order_ready", f"{source.title()} order {assigned_id} is ready for {table['name']}", order_id=assigned_id, table_id=table_id, source=source, urgent=urgent), event("task_created", "Food delivery task created", task_id=task["id"], table_id=table_id, order_id=assigned_id, source=source, urgent=urgent)]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def request(self, table_id: str, request_type: str, request_id: str | None = None, urgent: bool = False):
        if request_type not in {"water", "dessert"}:
            raise DomainError("request_type must be water or dessert")
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id)):
            raise DomainError("request_id must contain 1 to 64 letters, numbers, underscores, or hyphens")
        if not isinstance(urgent, bool):
            raise DomainError("urgent must be true or false")

        def mutate(state):
            table = self._table(state, table_id)
            if request_id:
                previous = self._request(state, request_id)
                if previous:
                    if (previous["table_id"], previous["type"], previous["urgent"]) != (table_id, request_type, urgent):
                        raise DomainError("request_id is already used for a different request", 409)
                    return []
            existing = next((item for item in state["requests"] if item["table_id"] == table_id and item["type"] == request_type and item["status"] == "open"), None)
            if existing:
                return []
            if request_id:
                assigned_id = request_id
            else:
                while self._request(state, f"request-{state['_next_request_id']}"):
                    state["_next_request_id"] += 1
                assigned_id = f"request-{state['_next_request_id']}"
                state["_next_request_id"] += 1
            if self._request(state, assigned_id):
                return []
            request = {"id": assigned_id, "table_id": table_id, "type": request_type, "status": "open", "urgent": urgent, "created_at": state["clock_seconds"]}
            state["requests"].append(request)
            task = self._new_task(state, "service_request", table_id, f"{request_type.title()} requested at {table['name']}", urgent, request_id=assigned_id, request_type=request_type)
            emitted = [event("request_created", f"Customer requested {request_type} at {table['name']}", request_id=assigned_id, table_id=table_id, request_type=request_type, urgent=urgent), event("task_created", "Customer service task created", task_id=task["id"], table_id=table_id, request_id=assigned_id, urgent=urgent)]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def observe_meal(self, table_id: str, meal_state: str):
        if meal_state not in {"eating", "finished"}:
            raise DomainError("meal_state must be eating or finished")

        def mutate(state):
            table = self._table(state, table_id)
            if table["meal_state"] == meal_state:
                return []
            previous = table["meal_state"]
            table["meal_state"] = meal_state
            emitted = [event("meal_evidence", f"{table['name']} meal state changed from {previous} to {meal_state}", table_id=table_id, previous_state=previous, meal_state=meal_state)]
            if meal_state == "finished":
                active_check = next((task for task in state["tasks"] if task["type"] == "check_table" and task["table_id"] == table_id and task["status"] in {"pending", "offered", "assigned"}), None)
                if not active_check and table["status"] != "being_served":
                    table["status"] = "needs_check"
                    task = self._new_task(state, "check_table", table_id, f"Finished-meal evidence; check {table['name']}")
                    emitted.append(event("task_created", "Finished-meal evidence created a table-check task", task_id=task["id"], table_id=table_id))
            else:
                for task in state["tasks"]:
                    if task["type"] != "check_table" or task["table_id"] != table_id or task["status"] not in {"pending", "offered"}:
                        continue
                    worker_id = task.get("offered_to")
                    task.update(status="cancelled", offered_to=None, worker_id=None)
                    emitted.append(event("task_cancelled", "Table-check task cancelled because the customer is still eating", task_id=task["id"], table_id=table_id, worker_id=worker_id))
                assigned_check = any(task["type"] == "check_table" and task["table_id"] == table_id and task["status"] == "assigned" for task in state["tasks"])
                table["status"] = "being_served" if assigned_check else "eating"
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def set_hold(self, table_id: str, worker_id: str, source: str, reason: str = "Service hold", duration_seconds: int | None = None):
        if source not in {"kitchen", "bar"}:
            raise DomainError("source must be kitchen or bar")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 120:
            raise DomainError("reason must contain 1 to 120 characters")
        if duration_seconds is not None and (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int) or not 1 <= duration_seconds <= 3600):
            raise DomainError("duration_seconds must be between 1 and 3600")

        def mutate(state):
            table = self._table(state, table_id)
            self._worker(state, worker_id)
            hold = table["hold"]
            until = state["clock_seconds"] + duration_seconds if duration_seconds is not None else None
            changed = not hold["active"] or any(hold[key] != value for key, value in {"worker_id": worker_id, "source": source, "reason": reason.strip(), "until_seconds": until}.items())
            if not changed:
                return []
            was_active = hold["active"]
            table["hold"] = {"active": True, "worker_id": worker_id, "source": source, "reason": reason.strip(), "until_seconds": until, "overdue": False, "needs_confirmation": False}
            action = "updated" if was_active else "started"
            emitted = [event("service_hold_started" if not was_active else "service_hold_updated", f"Service hold {action} for {table['name']}: {reason.strip()}", table_id=table_id, worker_id=worker_id, source=source, reason=reason.strip(), until_seconds=until)]
            for task in state["tasks"]:
                conflict = task.get("conflict") or {}
                if task["type"] == "food_delivery" and task["table_id"] == table_id and task["status"] == "assigned" and (not conflict.get("active") or conflict.get("acknowledged")):
                    task["conflict"] = {"active": True, "acknowledged": False, "raised_at": state["clock_seconds"]}
                    emitted.append(event("task_hold_conflict", f"Worker {task['worker_id']} has a delivery conflict at {table['name']}; acknowledgement and hold clearance are required", task_id=task["id"], table_id=table_id, worker_id=task["worker_id"], hold_worker_id=worker_id, source=source))
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def clear_hold(self, table_id: str, worker_id: str):
        def mutate(state):
            table = self._table(state, table_id)
            self._worker(state, worker_id)
            hold = table["hold"]
            if not hold["active"]:
                return []
            if hold["worker_id"] != worker_id:
                raise DomainError("Only the worker who set the hold can clear it")
            previous = dict(hold)
            table["hold"] = empty_hold()
            emitted = [event("service_hold_cleared", f"Service hold cleared for {table['name']}", table_id=table_id, worker_id=worker_id, source=previous["source"], overdue=previous["overdue"])]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def set_availability(self, worker_id: str, availability: str):
        if availability not in {"available", "busy"}:
            raise DomainError("availability must be available or busy")

        def mutate(state):
            worker = self._worker(state, worker_id)
            assigned = next((task for task in state["tasks"] if task["status"] == "assigned" and task["worker_id"] == worker_id), None)
            if availability == "available" and assigned:
                raise DomainError("Complete or release the assigned task before becoming available")
            if worker["availability"] == availability:
                return []
            worker.update(availability=availability, available_since=state["clock_seconds"] if availability == "available" else None)
            emitted = [event("worker_availability_changed", f"Worker {worker_id} is now {availability}", worker_id=worker_id, availability=availability)]
            emitted.extend(self._route(state))
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
            if self._blocked(state, task):
                raise DomainError("A table service hold blocks this food delivery")
            task.update(status="assigned", worker_id=worker_id, offered_to=None)
            worker.update(availability="busy", available_since=None)
            if task["type"] == "check_table":
                self._table(state, task["table_id"])["status"] = "being_served"
            emitted = [event("task_accepted", f"Worker {worker_id} accepted {task['type']} for Table {task['table_id']}", task_id=task_id, worker_id=worker_id, table_id=task["table_id"], task_type=task["type"])]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def decline(self, task_id: str, worker_id: str):
        def mutate(state):
            self._worker(state, worker_id)
            task = self._task(state, task_id)
            if task["status"] != "offered" or task["offered_to"] != worker_id:
                raise DomainError("Task is not offered to this worker")
            task.update(status="pending", offered_to=None)
            if worker_id not in task["declined_by"]:
                task["declined_by"].append(worker_id)
            emitted = [event("task_declined", f"Worker {worker_id} declined {task['type']} for Table {task['table_id']}", task_id=task_id, worker_id=worker_id, table_id=task["table_id"])]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def acknowledge_conflict(self, task_id: str, worker_id: str):
        def mutate(state):
            task = self._task(state, task_id)
            self._worker(state, worker_id)
            conflict = task.get("conflict")
            if task["status"] != "assigned" or task["worker_id"] != worker_id or not conflict or not conflict["active"]:
                raise DomainError("This worker has no active delivery conflict to acknowledge")
            if conflict["acknowledged"]:
                return []
            conflict["acknowledged"] = True
            return [event("task_conflict_acknowledged", f"Worker {worker_id} acknowledged the delivery conflict for Table {task['table_id']}", task_id=task_id, worker_id=worker_id, table_id=task["table_id"])]

        return self._change(mutate)

    def complete(self, task_id: str, worker_id: str):
        def mutate(state):
            worker = self._worker(state, worker_id)
            task = self._task(state, task_id)
            if task["status"] != "assigned" or task["worker_id"] != worker_id:
                raise DomainError("Task is not assigned to this worker")
            table = self._table(state, task["table_id"])
            if task["type"] == "food_delivery" and table["hold"]["active"]:
                raise DomainError("Clear the table service hold before completing this delivery")
            conflict = task.get("conflict")
            if conflict and conflict["active"] and not conflict["acknowledged"]:
                raise DomainError("Acknowledge the delivery conflict before completing this task")
            task["status"] = "completed"
            worker.update(availability="available", available_since=state["clock_seconds"])
            if task["type"] == "food_delivery":
                order = self._order(state, task["order_id"])
                order.update(status="delivered", delivered_by=worker_id)
                message = f"Worker {worker_id} delivered order {order['id']} to Table {task['table_id']}"
                emitted = [event("task_completed", message, task_id=task_id, worker_id=worker_id, table_id=task["table_id"], order_id=order["id"]), event("order_delivered", message, order_id=order["id"], table_id=task["table_id"], worker_id=worker_id)]
            elif task["type"] == "service_request":
                request = self._request(state, task["request_id"])
                request.update(status="fulfilled", fulfilled_by=worker_id)
                message = f"Worker {worker_id} fulfilled {request['type']} at Table {task['table_id']}"
                emitted = [event("task_completed", message, task_id=task_id, worker_id=worker_id, table_id=task["table_id"], request_id=request["id"]), event("request_fulfilled", message, request_id=request["id"], table_id=task["table_id"], worker_id=worker_id)]
            else:
                table["status"] = "checked"
                message = f"Worker {worker_id} completed the table check at Table {task['table_id']}"
                emitted = [event("task_completed", message, task_id=task_id, worker_id=worker_id, table_id=task["table_id"])]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def release(self, task_id: str, worker_id: str):
        def mutate(state):
            worker = self._worker(state, worker_id)
            task = self._task(state, task_id)
            if task["status"] != "assigned" or task["worker_id"] != worker_id:
                raise DomainError("Task is not assigned to this worker")
            task.update(status="pending", worker_id=None, offered_to=None)
            if task.get("conflict"):
                task["conflict"].update(active=False, released=True)
            if task["type"] == "check_table":
                table = self._table(state, task["table_id"])
                if table["meal_state"] == "eating":
                    task["status"] = "cancelled"
                    table["status"] = "eating"
                else:
                    table["status"] = "needs_check"
            worker.update(availability="available", available_since=state["clock_seconds"])
            event_type = "task_cancelled" if task["status"] == "cancelled" else "task_released"
            message = "Table-check task cancelled on release because the customer is still eating" if task["status"] == "cancelled" else f"Worker {worker_id} released {task['type']} for Table {task['table_id']}"
            emitted = [event(event_type, message, task_id=task_id, worker_id=worker_id, table_id=task["table_id"], reason="Explicit release returned the task to routing")]
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def advance(self, seconds: int):
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 3600:
            raise DomainError("seconds must be between 1 and 3600")

        def mutate(state):
            previous = state["clock_seconds"]
            state["clock_seconds"] += seconds
            emitted = [event("clock_advanced", f"Simulation clock advanced by {seconds} seconds", from_seconds=previous, to_seconds=state["clock_seconds"])]
            for task in state["tasks"]:
                if task["status"] not in {"pending", "offered"} or not self._prerequisite(state, task) or self._blocked(state, task):
                    continue
                before = task["wait_seconds"]
                task["wait_seconds"] += seconds
                if before < 90 <= task["wait_seconds"]:
                    emitted.append(event("task_overdue", f"{task['id']} reached {task['wait_seconds']} eligible seconds and is now overdue", task_id=task["id"], table_id=task["table_id"], wait_seconds=task["wait_seconds"]))
            for table in state["tables"]:
                hold = table["hold"]
                if hold["active"] and hold["until_seconds"] is not None and state["clock_seconds"] >= hold["until_seconds"] and not hold["overdue"]:
                    hold.update(overdue=True, needs_confirmation=True)
                    emitted.append(event("service_hold_overdue", f"Service hold for Table {table['id']} reached its time limit and needs confirmation", table_id=table["id"], worker_id=hold["worker_id"], source=hold["source"], needs_confirmation=True))
            emitted.extend(self._route(state))
            return emitted

        return self._change(mutate)

    def reset(self):
        def mutate(state):
            previous = {"task_count": len(state["tasks"]), "request_count": len(state["requests"]), "order_count": len(state["orders"])}
            ids = (state["_next_task_id"], state["_next_request_id"], state["_next_order_id"], state["_next_task_seq"])
            state.clear()
            state.update(simulation_initial_state())
            state["_next_task_id"], state["_next_request_id"], state["_next_order_id"], state["_next_task_seq"] = ids
            return [event("simulation_reset", "Simulation restored to its starting state; chronological event history retained", previous=previous)]

        return self._change(mutate)
