from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from .monitor import ProcrastinationMonitor
from .schemas import ProcrastinationSettingsUpdate
from .store import FocusStore

router = APIRouter(prefix="/api/procrastination", tags=["procrastination"])

_monitor: Optional[ProcrastinationMonitor] = None


def bind(monitor: ProcrastinationMonitor) -> None:
    """Hand the router the live monitor. Called from the app's lifespan."""
    global _monitor
    _monitor = monitor


def require_monitor() -> ProcrastinationMonitor:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="The procrastination watcher is not running")
    return _monitor


def _store() -> FocusStore:
    return require_monitor().store


@router.get("/status")
async def status() -> Dict[str, Any]:
    """What the watcher is doing right now, for the frontend's focus panel."""
    return require_monitor().status()


@router.get("/sessions")
async def list_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    return _store().recent_sessions(limit)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> Dict[str, Any]:
    store = _store()
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Focus session not found")
    return {
        "session": session,
        "observations": store.observations(session_id),
        "nudges": store.nudges(session_id),
    }


@router.patch("/settings")
async def update_settings(payload: ProcrastinationSettingsUpdate) -> Dict[str, Any]:
    """Change the tunables that matter mid-session without a restart."""
    monitor = require_monitor()
    fields = {
        "enabled": "enabled",
        "nudge_after_seconds": "nudge_after_seconds",
        "nudge_cooldown_seconds": "nudge_cooldown_seconds",
        "idle_threshold_seconds": "idle_threshold_seconds",
        "enable_screenshots": "enable_screenshots",
    }
    for name, attribute in fields.items():
        value = getattr(payload, name)
        if value is not None:
            setattr(monitor, attribute, value)
    # These two live on the delivery path's settings rather than the monitor.
    for name in ("enable_web_push", "enable_native_notifications", "nudge_style"):
        value = getattr(payload, name)
        if value is not None:
            object.__setattr__(monitor.settings, name, value)
    return {
        "enabled": monitor.enabled,
        "nudge_after_seconds": monitor.nudge_after_seconds,
        "nudge_cooldown_seconds": monitor.nudge_cooldown_seconds,
        "idle_threshold_seconds": monitor.idle_threshold_seconds,
        "enable_screenshots": monitor.enable_screenshots,
        "enable_web_push": monitor.settings.enable_web_push,
        "enable_native_notifications": monitor.settings.enable_native_notifications,
        "nudge_style": monitor.settings.nudge_style,
    }


@router.post("/sample")
async def sample_now() -> Dict[str, Any]:
    """One raw reading. Use this to verify macOS permissions during setup.

    An empty window_title for an app that clearly has windows means
    Accessibility is not granted yet.
    """
    monitor = require_monitor()
    reading = await monitor.capture.sample()
    return {
        "app": reading.app,
        "window_title": reading.window_title,
        "url": reading.url,
        "site": reading.host,
        "idle_seconds": reading.idle_seconds,
        "sensitive": reading.sensitive,
        "captured_at": reading.captured_at,
    }


@router.post("/test-nudge")
async def test_nudge() -> Dict[str, Any]:
    """Send a sample nudge so the frontend and macOS banners can be checked."""
    monitor = require_monitor()
    state = monitor.state
    session = state.focus_session if state else {"id": "test", "event_id": None}
    nudge = {
        "title": "Focus check",
        "body": "This is a test nudge from the procrastination watcher.",
        "next_step": "Nothing to do; delivery is working.",
    }
    push_ok, native_ok = await monitor.delivery.send(nudge, session)
    return {
        "delivered_push": push_ok,
        "delivered_native": native_ok,
        "nudge_style": monitor.settings.nudge_style,
        # A banner can be discarded by macOS with a success exit code, so
        # delivered_native=True still does not prove a human saw it.
        "error": monitor.delivery.last_error,
        "nudge": nudge,
    }
