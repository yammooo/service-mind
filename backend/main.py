import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.store import DomainError, Store
from backend.vision import create_vision_provider

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SERVICE_MIND_DB", ROOT / "data" / "service-mind.sqlite3"))
VISION_PROVIDER_NAME = os.environ.get("VISION_PROVIDER", "mock").lower()

app = FastAPI(title="ServiceMind", description="A local restaurant service coordination prototype")
app.state.store = Store(DB_PATH)
app.state.vision = create_vision_provider(VISION_PROVIDER_NAME)
app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")


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


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def home(worker: str | None = Query(default=None)):
    if worker is not None and worker not in {"A", "B"}:
        raise HTTPException(status_code=404, detail="Unknown worker")
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/state")
def get_state():
    return app.state.store.snapshot()


@app.get("/api/events")
def get_events():
    return app.state.store.events()


@app.post("/api/observations")
def observe(body: Observation):
    normalized = app.state.vision.observe(body.plate_id, body.food_state)
    return app.state.store.observe(body.table_id, normalized["plate_id"], normalized["food_state"])


@app.post("/api/workers/{worker_id}/availability")
def set_availability(worker_id: str, body: Availability):
    return app.state.store.set_availability(worker_id, body.availability)


@app.post("/api/tasks/{task_id}/accept")
def accept_task(task_id: str, body: WorkerAction):
    return app.state.store.accept(task_id, body.worker_id)


@app.post("/api/tasks/{task_id}/decline")
def decline_task(task_id: str, body: WorkerAction):
    return app.state.store.decline(task_id, body.worker_id)


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: str, body: WorkerAction):
    return app.state.store.complete(task_id, body.worker_id)


@app.post("/api/tables/{table_id}/requests")
def create_request(table_id: str, body: TableRequest):
    return app.state.store.request(table_id, body.worker_id, body.request_type)


@app.post("/api/tables/{table_id}/correction")
def correct_table(table_id: str, body: Correction):
    if body.correction != "not_ready":
        raise HTTPException(status_code=400, detail="Unsupported correction")
    return app.state.store.correct_not_ready(table_id)


@app.post("/api/demo/reset")
def reset_demo():
    return app.state.store.reset()
