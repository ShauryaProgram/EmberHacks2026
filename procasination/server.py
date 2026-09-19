"""Run the backend with the procrastination watcher attached.

    cd backend && ../.venv/bin/uvicorn procasination.server:app --port 8766

This wraps the backend's own lifespan rather than editing it, so nothing under
`backend/app` changes. Running `app.main:app` directly still gives you the
plain calendar backend with no watcher.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from app.config import settings as backend_settings
from app.main import app, db
from fastapi.staticfiles import StaticFiles

from . import api
from .config import procrastination_settings
from .monitor import ProcrastinationMonitor

monitor = ProcrastinationMonitor(db, backend_settings, procrastination_settings)

_backend_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(scoped_app):
    # The backend's lifespan runs first and to completion: it creates the
    # database and the PushService the watcher depends on.
    async with _backend_lifespan(scoped_app):
        api.bind(monitor)
        await monitor.start()
        try:
            yield
        finally:
            await monitor.stop()


app.router.lifespan_context = lifespan
app.include_router(api.router)

# The production frontend is served by the same process as the API and watcher.
# API routes are registered first so the catch-all static mount cannot shadow
# them. Vite's development server proxies these same paths during UI work.
frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

__all__ = ["app", "monitor"]
