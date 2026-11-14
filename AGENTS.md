# ServiceMind prototype

## Goal and demo scope

Build a complete local prototype for the Junction demo, due Nov 15, 2026 at 10:00 Finnish time. The demo uses one shared AI brain for physical restaurant teams. This is a one-process FastAPI/Uvicorn app with SQLite, plain responsive HTML/CSS/JavaScript, relative API URLs, and one-second polling. It must run on a Linux ARM64 laptop later. No cloud service, Node runtime, LLM inference, real vision inference, or glasses integration is in scope.

## Fixed demo data and behavior

- One restaurant table and two customers, with plates A and B and workers A and B.
- Initial state: both customers are eating; worker A is busy and worker B is available; no tasks.
- Deterministic rules only. When the first plate is reported empty, do not create a task. When the second plate is reported empty, set the table to `needs_check` and create exactly one `check_table` task offered to an available worker.
- Accept assigns the task, makes that worker busy, and sets the table to `being_served`. A dessert request creates a shared request and timestamped event. Complete makes the worker available and table `checked`; do not recreate a task while the same observation remains unchanged.
- Decline reoffers to a different available worker. A `not_ready` correction suppresses follow-up until relevant new evidence or reset.
- Duplicate/unchanged observations do not add events or tasks. Unknown is never treated as empty.
- State enums: table `eating`, `needs_check`, `being_served`, `checked`; task `pending`, `offered`, `assigned`, `completed`, `cancelled`.
- Reset restores deterministic demo state and retains history by recording a reset event.

## Implementation requirements

- Persist current state and timestamped events atomically in SQLite; serialize writes safely with thread locking and transactions.
- Expose `GET /api/state`, `GET /api/events`, and POST APIs for observations, worker availability, task accept/decline/complete, table requests, corrections, and demo reset. Validate malformed IDs, states, and actions.
- Define a `VisionProvider` interface and functional `MockVisionProvider` API pipeline. `RealVisionProvider` must fail explicitly with `NotImplementedError`; `VISION_PROVIDER=mock` is default and selecting `real` must fail clearly.
- Include `config/camera.json` with normalized regions A `[0.05, 0.15, 0.45, 0.85]`, B `[0.55, 0.15, 0.95, 0.85]`, and `stable_observations_required: 5`. No real inference.
- Admin view at `/`; worker views at `/?worker=A` and `/?worker=B`. Show shared tasks, requests, workers, chronological history, and clearly labeled mock food/empty/unknown controls and reset. Worker views show availability, assigned task reason, accept/decline/complete, dessert/water/not-ready controls, shared state, and recent notifications without extra task-request controls.
- Accessible mobile-friendly styling with clear product presentation and labels.
- SQLite and virtualenv/cache files must be ignored by git.

## Verification and docs

- Add essential tests covering: initial state; first and second empty observations; duplicates; accept; shared requests; complete; reset; persistence after restart; decline; correction; invalid transitions.
- Run the app with `uvicorn backend.main:app --host 0.0.0.0 --port 8000` and exercise its real HTTP API.
- README must give exact setup commands, URLs, demo flow, tests, API, vision config, future hardware integration boundary, and current limitations.

## Working constraints

- Follow the ponytail skill at `/home/yammo/.codex/plugins/cache/ponytail/ponytail/5.3.3/skills/ponytail/SKILL.md`: smallest complete implementation, reuse dependencies, cover caller/config/test impact, and test non-trivial logic.
- Use Astra for orchestration and Luna xhigh for all engineering.
