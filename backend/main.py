import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend.live_vision import app, camera_ready
from backend.monitoring import VisionMonitor
from backend.store import DomainError, Store
from backend.simulation import SimulationStore
from backend.vision import create_vision_provider

ROOT = Path(__file__).resolve().parent.parent
MODE = os.environ.get("SERVICE_MIND_MODE", "legacy").lower()
if MODE not in {"legacy", "simulation"}:
    raise RuntimeError("SERVICE_MIND_MODE must be legacy or simulation")
DB_PATH = Path(
    os.environ.get("SERVICE_MIND_SIM_DB", ROOT / "data" / "service-mind-simulation.sqlite3")
    if MODE == "simulation"
    else os.environ.get("SERVICE_MIND_DB", ROOT / "data" / "service-mind.sqlite3")
)
VISION_PROVIDER_NAME = os.environ.get("VISION_PROVIDER", "mock").lower()

app.state.mode = MODE
app.state.store = SimulationStore(DB_PATH) if MODE == "simulation" else Store(DB_PATH)
app.state.vision = None if MODE == "simulation" else create_vision_provider(VISION_PROVIDER_NAME)
app.state.vision_monitor = None if MODE == "simulation" else VisionMonitor(app.state.store)


class Observation(BaseModel):
    table_id: str = "1"
    plate_id: Literal["A", "B"]
    food_state: Literal["food", "empty", "unknown"]


class Availability(BaseModel):
    availability: Literal["available", "busy"]


class WorkerAction(BaseModel):
    worker_id: str


class TableRequest(BaseModel):
    worker_id: str
    request_type: Literal["dessert", "water"]


class Correction(BaseModel):
    correction: Literal["not_ready"]


class VisionRegions(BaseModel):
    regions: dict[Literal["A", "B"], tuple[float, float, float, float]]


class SimulationAvailability(BaseModel):
    availability: Literal["available", "busy"]


class SimulationTaskAction(BaseModel):
    worker_id: str


class SimulationAdvance(BaseModel):
    seconds: int


class SimulationOrderReady(BaseModel):
    source: Literal["kitchen", "bar"]
    order_id: str | None = None
    urgent: bool = False


class SimulationRequest(BaseModel):
    request_type: Literal["water", "dessert"]
    request_id: str | None = None
    urgent: bool = False


class SimulationEvidence(BaseModel):
    meal_state: Literal["eating", "finished"]


class SimulationHold(BaseModel):
    worker_id: str
    source: Literal["kitchen", "bar"]
    reason: str = "Service hold"
    duration_seconds: int | None = None


def require_mode(mode: str):
    if app.state.mode != mode:
        raise HTTPException(status_code=404, detail=f"This endpoint requires {mode} mode")


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
@app.get("/admin", include_in_schema=False)
def home(worker: str | None = Query(default=None)):
    if app.state.mode == "simulation":
        if worker is not None:
            raise HTTPException(status_code=404, detail="Worker pages are unavailable in simulation mode; use /simulation")
        return FileResponse(ROOT / "frontend" / "simulation.html")
    if worker is not None and worker not in {"A", "B"}:
        raise HTTPException(status_code=404, detail="Unknown worker")
    if worker:
        return RedirectResponse(f"/worker/{worker}", status_code=307)
    return FileResponse(ROOT / "frontend" / "admin.html")


@app.get("/worker/{worker_id}", include_in_schema=False)
def worker_view(worker_id: str):
    require_mode("legacy")
    if worker_id not in {"A", "B"}:
        raise HTTPException(status_code=404, detail="Unknown worker")
    return FileResponse(ROOT / "frontend" / "worker.html")


@app.get("/api/state")
def get_state():
    return app.state.store.snapshot()


@app.get("/api/events")
def get_events():
    return app.state.store.events()


@app.post("/api/observations")
def observe(body: Observation):
    require_mode("legacy")
    normalized = app.state.vision.observe(body.plate_id, body.food_state)
    return app.state.vision_monitor.observe_mock(body.table_id, normalized["plate_id"], normalized["food_state"])


@app.post("/api/vision/regions")
def set_vision_regions(body: VisionRegions):
    require_mode("legacy")
    return app.state.vision_monitor.set_regions(body.regions)


@app.post("/api/vision/start")
def start_vision():
    require_mode("legacy")
    with app.state.vision_monitor.lock:
        if not camera_ready():
            raise HTTPException(status_code=503, detail="Camera unavailable or stale; restart ServiceMind after reconnecting it")
        return app.state.vision_monitor.start()


@app.post("/api/vision/pause")
def pause_vision():
    require_mode("legacy")
    return app.state.vision_monitor.pause()


@app.post("/api/workers/{worker_id}/availability")
def set_availability(worker_id: str, body: Availability):
    require_mode("legacy")
    return app.state.store.set_availability(worker_id, body.availability)


@app.post("/api/tasks/{task_id}/accept")
def accept_task(task_id: str, body: WorkerAction):
    require_mode("legacy")
    return app.state.store.accept(task_id, body.worker_id)


@app.post("/api/tasks/{task_id}/decline")
def decline_task(task_id: str, body: WorkerAction):
    require_mode("legacy")
    return app.state.store.decline(task_id, body.worker_id)


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: str, body: WorkerAction):
    require_mode("legacy")
    return app.state.store.complete(task_id, body.worker_id)


@app.post("/api/tables/{table_id}/requests")
def create_request(table_id: str, body: TableRequest):
    require_mode("legacy")
    return app.state.store.request(table_id, body.worker_id, body.request_type)


@app.post("/api/tables/{table_id}/correction")
def correct_table(table_id: str, body: Correction):
    require_mode("legacy")
    if body.correction != "not_ready":
        raise HTTPException(status_code=400, detail="Unsupported correction")
    return app.state.vision_monitor.correct_not_ready(table_id)


@app.post("/api/demo/reset")
def reset_demo():
    require_mode("legacy")
    return app.state.vision_monitor.reset()


@app.get("/simulation", include_in_schema=False)
def simulation_view():
    require_mode("simulation")
    return FileResponse(ROOT / "frontend" / "simulation.html")


@app.get("/api/simulation/state")
def simulation_state():
    require_mode("simulation")
    return app.state.store.snapshot()


@app.get("/api/simulation/events")
def simulation_events():
    require_mode("simulation")
    return app.state.store.events()


@app.post("/api/simulation/clock/advance")
def simulation_advance(body: SimulationAdvance):
    require_mode("simulation")
    return app.state.store.advance(body.seconds)


@app.post("/api/simulation/tables/{table_id}/order-ready")
def simulation_order_ready(table_id: str, body: SimulationOrderReady):
    require_mode("simulation")
    return app.state.store.order_ready(table_id, body.source, body.order_id, body.urgent)


@app.post("/api/simulation/tables/{table_id}/requests")
def simulation_request(table_id: str, body: SimulationRequest):
    require_mode("simulation")
    return app.state.store.request(table_id, body.request_type, body.request_id, body.urgent)


@app.post("/api/simulation/tables/{table_id}/evidence")
def simulation_evidence(table_id: str, body: SimulationEvidence):
    require_mode("simulation")
    return app.state.store.observe_meal(table_id, body.meal_state)


@app.post("/api/simulation/tables/{table_id}/hold")
def simulation_hold(table_id: str, body: SimulationHold):
    require_mode("simulation")
    return app.state.store.set_hold(table_id, body.worker_id, body.source, body.reason, body.duration_seconds)


@app.post("/api/simulation/tables/{table_id}/hold/clear")
def simulation_hold_clear(table_id: str, body: SimulationTaskAction):
    require_mode("simulation")
    return app.state.store.clear_hold(table_id, body.worker_id)


@app.post("/api/simulation/workers/{worker_id}/availability")
def simulation_availability(worker_id: str, body: SimulationAvailability):
    require_mode("simulation")
    return app.state.store.set_availability(worker_id, body.availability)


@app.post("/api/simulation/tasks/{task_id}/accept")
def simulation_accept(task_id: str, body: SimulationTaskAction):
    require_mode("simulation")
    return app.state.store.accept(task_id, body.worker_id)


@app.post("/api/simulation/tasks/{task_id}/decline")
def simulation_decline(task_id: str, body: SimulationTaskAction):
    require_mode("simulation")
    return app.state.store.decline(task_id, body.worker_id)


@app.post("/api/simulation/tasks/{task_id}/complete")
def simulation_complete(task_id: str, body: SimulationTaskAction):
    require_mode("simulation")
    return app.state.store.complete(task_id, body.worker_id)


@app.post("/api/simulation/tasks/{task_id}/release")
def simulation_release(task_id: str, body: SimulationTaskAction):
    require_mode("simulation")
    return app.state.store.release(task_id, body.worker_id)


@app.post("/api/simulation/tasks/{task_id}/acknowledge")
def simulation_acknowledge(task_id: str, body: SimulationTaskAction):
    require_mode("simulation")
    return app.state.store.acknowledge_conflict(task_id, body.worker_id)


@app.post("/api/simulation/reset")
def simulation_reset():
    require_mode("simulation")
    return app.state.store.reset()
