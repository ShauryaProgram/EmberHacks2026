from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from .canvas import CanvasClient, CanvasError
from .config import settings
from .db import Database
from .notifications import PushService
from .openrouter import OpenRouterClient, OpenRouterError
from .schemas import (
    EventCreate,
    EventUpdate,
    OnboardingRequest,
    OnboardingResponse,
    PushSubscriptionRequest,
    ReminderCreate,
    ReminderUpdate,
    NaturalLanguageInput,
    StudyRecomputeRequest,
    StudySessionUpdate,
    StudySettingsUpdate,
    SyncResponse,
)
from .security import SecretBox
from .sync import SyncService
from .study import StudyPlanner
from .utils import iso, parse_datetime, utc_now


db = Database(settings.database_path)
secrets = SecretBox(settings.secret_key_path)
sync_service = SyncService(db, settings, secrets)
study_planner = StudyPlanner(db, settings)
push_service: Optional[PushService] = None
scheduler: Optional[AsyncIOScheduler] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global push_service, scheduler
    db.initialize()
    push_service = PushService(db, settings)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(sync_service.run, "interval", seconds=settings.sync_interval_seconds,
                      args=["scheduler"], id="quercus-sync", max_instances=1, coalesce=True)
    scheduler.add_job(push_service.dispatch_due, "interval", seconds=settings.notification_interval_seconds,
                      id="push-reminders", max_instances=1, coalesce=True)
    scheduler.start()
    if db.fetch_one("SELECT id FROM profile WHERE id=1"):
        asyncio.create_task(sync_service.run("startup"))
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Quercus Calendar Backend",
    version="1.0.0",
    description="Per-user Quercus ingestion, calendar, reminders, and Web Push API.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_profile() -> Dict[str, Any]:
    profile = db.fetch_one("SELECT id,canvas_user_id,name,timezone,reminder_offsets,onboarded_at,updated_at FROM profile WHERE id=1")
    if not profile:
        raise HTTPException(status_code=409, detail="Onboarding is required")
    profile["reminder_offsets_days"] = json.loads(profile.pop("reminder_offsets"))
    return profile


def decode_event(row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(row)
    row["all_day"] = bool(row["all_day"])
    row["metadata"] = json.loads(row.get("metadata") or "{}")
    return row


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "onboarded": bool(db.fetch_one("SELECT id FROM profile WHERE id=1")),
            "sync_running": sync_service.running, "openrouter_configured": bool(settings.openrouter_api_key)}


@app.post("/api/onboarding", response_model=OnboardingResponse)
async def onboard(payload: OnboardingRequest, background_tasks: BackgroundTasks) -> OnboardingResponse:
    try:
        async with CanvasClient(payload.quercus_api_token, settings) as canvas:
            user = await canvas.current_user()
    except CanvasError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = iso(utc_now())
    encrypted = secrets.encrypt(payload.quercus_api_token)
    with db.transaction() as connection:
        connection.execute(
            """INSERT INTO profile(id,canvas_user_id,name,token_encrypted,timezone,reminder_offsets,onboarded_at,updated_at)
               VALUES(1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET canvas_user_id=excluded.canvas_user_id,
               name=excluded.name,token_encrypted=excluded.token_encrypted,timezone=excluded.timezone,
               reminder_offsets=excluded.reminder_offsets,updated_at=excluded.updated_at""",
            (str(user.get("id", "")), user.get("name") or "Quercus user", encrypted, payload.timezone,
             db.json(payload.reminder_offsets_days), now, now),
        )
    background_tasks.add_task(sync_service.run, "onboarding")
    return OnboardingResponse(onboarded=True, user={"id": str(user.get("id", "")), "name": user.get("name", "")},
                              initial_sync_queued=True)


@app.get("/api/onboarding/status")
async def onboarding_status() -> Dict[str, Any]:
    profile = db.fetch_one("SELECT id,canvas_user_id,name,timezone,reminder_offsets,onboarded_at,updated_at FROM profile WHERE id=1")
    if not profile:
        return {"onboarded": False}
    profile["reminder_offsets_days"] = json.loads(profile.pop("reminder_offsets"))
    return {"onboarded": True, "profile": profile}


@app.delete("/api/onboarding", status_code=204)
async def reset_onboarding() -> Response:
    with db.transaction() as connection:
        for table in ("study_plan_runs", "push_subscriptions", "reminders", "calendar_events", "analysis_results",
                      "source_documents", "announcements", "assignments", "courses", "sync_runs", "profile"):
            connection.execute("DELETE FROM " + table)
        connection.execute(
            """UPDATE study_settings SET weekday_start='09:00',weekday_end='21:30',
               weekend_start='10:00',weekend_end='20:00',max_daily_minutes=240,
               weekend_max_daily_minutes=300,max_session_minutes=90,min_session_minutes=30,
               break_minutes=15,planning_horizon_days=90,planning_profile='{}',updated_at=?
               WHERE id=1""",
            (iso(utc_now()),),
        )
    return Response(status_code=204)


@app.post("/api/sync", response_model=SyncResponse, status_code=202)
async def trigger_sync(background_tasks: BackgroundTasks) -> SyncResponse:
    require_profile()
    if sync_service.running:
        return SyncResponse(accepted=False, message="A sync is already running")
    background_tasks.add_task(sync_service.run, "manual")
    return SyncResponse(accepted=True, message="Quercus sync started")


@app.get("/api/sync/status")
async def sync_status() -> Dict[str, Any]:
    latest = db.fetch_one("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1")
    if latest and latest.get("summary"):
        latest["summary"] = json.loads(latest["summary"])
    pending = db.fetch_one("SELECT COUNT(*) AS count FROM source_documents WHERE analysis_status='pending'")
    errors = db.fetch_one("SELECT COUNT(*) AS count FROM source_documents WHERE analysis_status='error'")
    return {"running": sync_service.running, "latest": latest,
            "pending_analysis": int((pending or {}).get("count", 0)),
            "analysis_errors": int((errors or {}).get("count", 0))}


@app.get("/api/courses")
async def list_courses() -> List[Dict[str, Any]]:
    require_profile()
    rows = db.fetch_all(
        "SELECT id,code,name,term_name,start_at,end_at,html_url,enrollment_state,updated_at,raw_json FROM courses ORDER BY code"
    )
    selected = []
    for row in rows:
        raw = json.loads(row.pop("raw_json"))
        if not sync_service._include_course(raw):
            continue
        row["sections"] = [
            {key: section.get(key) for key in ("id", "name", "start_at", "end_at")}
            for section in raw.get("sections") or []
        ]
        selected.append(row)
    return selected


@app.get("/api/assignments")
async def list_assignments(
    course_id: Optional[str] = None,
    include_completed: bool = False,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
) -> List[Dict[str, Any]]:
    require_profile()
    clauses, params = ["active=1"], []
    if course_id:
        clauses.append("course_id=?"); params.append(course_id)
    if not include_completed:
        clauses.append("completed=0")
    if from_date:
        clauses.append("(due_at IS NULL OR due_at>=?)"); params.append(from_date)
    if to_date:
        clauses.append("(due_at IS NULL OR due_at<=?)"); params.append(to_date)
    rows = db.fetch_all("SELECT * FROM assignments WHERE " + " AND ".join(clauses) + " ORDER BY due_at IS NULL,due_at", params)
    return db.decode_rows(rows, ["submission_types", "raw_json"])


@app.get("/api/announcements")
async def list_announcements(course_id: Optional[str] = None, unread_only: bool = False) -> List[Dict[str, Any]]:
    require_profile()
    clauses, params = ["1=1"], []
    if course_id:
        clauses.append("course_id=?"); params.append(course_id)
    if unread_only:
        clauses.append("read_state='new'")
    return db.fetch_all("SELECT * FROM announcements WHERE " + " AND ".join(clauses) + " ORDER BY posted_at DESC", params)


@app.post("/api/announcements/{source_key:path}/read")
async def mark_announcement_read(source_key: str) -> Dict[str, bool]:
    require_profile()
    changed = db.execute("UPDATE announcements SET read_state='read',updated_at=? WHERE source_key=?", (iso(utc_now()), source_key))
    if not changed:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return {"ok": True}


@app.get("/api/calendar/events")
async def list_events(
    start: Optional[str] = None,
    end: Optional[str] = None,
    course_id: Optional[str] = None,
    kind: Optional[str] = None,
    source_type: Optional[str] = None,
    include_cancelled: bool = False,
) -> List[Dict[str, Any]]:
    require_profile()
    clauses, params = ["1=1"], []
    if start:
        clauses.append("COALESCE(end_at,start_at)>=?"); params.append(start)
    if end:
        clauses.append("start_at<=?"); params.append(end)
    if course_id:
        clauses.append("course_id=?"); params.append(course_id)
    if kind:
        clauses.append("kind=?"); params.append(kind)
    if source_type:
        clauses.append("source_type=?"); params.append(source_type)
    if not include_cancelled:
        clauses.append("status!='cancelled'")
    rows = db.fetch_all("SELECT * FROM calendar_events WHERE " + " AND ".join(clauses) + " ORDER BY start_at", params)
    return [decode_event(row) for row in rows]


@app.get("/api/calendar/events/{event_id}")
async def get_event(event_id: str) -> Dict[str, Any]:
    require_profile()
    row = db.fetch_one("SELECT * FROM calendar_events WHERE id=?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return decode_event(row)


@app.post("/api/calendar/events", status_code=201)
async def create_event(payload: EventCreate) -> Dict[str, Any]:
    profile = require_profile()
    try:
        start = parse_datetime(payload.start_at)
        end = parse_datetime(payload.end_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid ISO-8601 event date") from exc
    if not start or (end and end <= start):
        raise HTTPException(status_code=422, detail="Event end must be after its start")
    event_id = str(uuid.uuid4())
    source_key = "manual:" + event_id
    now = iso(utc_now())
    db.execute(
        """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,all_day,
           status,kind,url,location,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?)""",
        (event_id, source_key, payload.course_id, "manual", payload.title, payload.description, payload.start_at,
         payload.end_at, int(payload.all_day), payload.kind, payload.url, payload.location, "{}", now, now),
    )
    local_date = start.astimezone(ZoneInfo(profile["timezone"])).date()
    study_planner.recompute(profile, from_date=local_date)
    sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    return await get_event(event_id)


@app.patch("/api/calendar/events/{event_id}")
async def update_event(event_id: str, payload: EventUpdate) -> Dict[str, Any]:
    profile = require_profile()
    row = db.fetch_one("SELECT * FROM calendar_events WHERE id=?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if row["source_type"] != "manual":
        raise HTTPException(status_code=409, detail="Synced events are read-only; edit them in Quercus")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return decode_event(row)
    if "start_at" in values or "end_at" in values:
        try:
            start = parse_datetime(values.get("start_at", row["start_at"]))
            end = parse_datetime(values.get("end_at", row["end_at"]))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid ISO-8601 event date") from exc
        if not start or (end and end <= start):
            raise HTTPException(status_code=422, detail="Event end must be after its start")
    columns = {"title", "description", "start_at", "end_at", "all_day", "kind", "location", "url", "status"}
    assignments, params = [], []
    for key, value in values.items():
        if key in columns:
            assignments.append(key + "=?"); params.append(int(value) if key == "all_day" else value)
    assignments.append("updated_at=?"); params.extend([iso(utc_now()), event_id])
    db.execute("UPDATE calendar_events SET " + ",".join(assignments) + " WHERE id=?", params)
    affected = parse_datetime(values.get("start_at", row["start_at"]))
    if affected:
        study_planner.recompute(profile, from_date=affected.astimezone(ZoneInfo(profile["timezone"])).date())
    sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    return await get_event(event_id)


@app.delete("/api/calendar/events/{event_id}", status_code=204)
async def delete_event(event_id: str) -> Response:
    profile = require_profile()
    row = db.fetch_one("SELECT source_type,start_at FROM calendar_events WHERE id=?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if row["source_type"] != "manual":
        raise HTTPException(status_code=409, detail="Synced events are read-only")
    db.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
    affected = parse_datetime(row["start_at"])
    if affected:
        study_planner.recompute(profile, from_date=affected.astimezone(ZoneInfo(profile["timezone"])).date())
        sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    return Response(status_code=204)


@app.get("/api/study/settings")
async def get_study_settings() -> Dict[str, Any]:
    require_profile()
    return study_planner.settings_row()


@app.patch("/api/study/settings")
async def update_study_settings(payload: StudySettingsUpdate) -> Dict[str, Any]:
    profile = require_profile()
    values = payload.model_dump(exclude_unset=True)
    current = study_planner.settings_row()
    planning_profile = values.pop("planning_profile", None)
    if planning_profile is not None:
        preferences = planning_profile["preferences"]
        windows = planning_profile["windows"]
        values.update({
            "weekday_start": windows["weekday"]["start"],
            "weekday_end": windows["weekday"]["end"],
            "weekend_start": windows["weekend"]["start"],
            "weekend_end": windows["weekend"]["end"],
            "max_daily_minutes": preferences["dailyLimit"],
            "weekend_max_daily_minutes": preferences["dailyLimit"],
            "max_session_minutes": preferences["focusBlock"],
            "break_minutes": preferences["buffer"],
            "planning_profile": planning_profile,
        })
    elif current.get("planning_profile") and values:
        # Keep the v5 profile in step when the compact Settings form edits one
        # of the legacy scalar fields directly.
        planning_profile = current["planning_profile"]
        scalar_paths = {
            "weekday_start": ("windows", "weekday", "start"),
            "weekday_end": ("windows", "weekday", "end"),
            "weekend_start": ("windows", "weekend", "start"),
            "weekend_end": ("windows", "weekend", "end"),
            "max_daily_minutes": ("preferences", "dailyLimit"),
            "max_session_minutes": ("preferences", "focusBlock"),
            "break_minutes": ("preferences", "buffer"),
        }
        for key, path in scalar_paths.items():
            if key not in values:
                continue
            cursor = planning_profile
            for part in path[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[path[-1]] = values[key]
        values["planning_profile"] = planning_profile
    merged = {**current, **values}
    if dt.time.fromisoformat(merged["weekday_start"]) >= dt.time.fromisoformat(merged["weekday_end"]):
        raise HTTPException(status_code=422, detail="weekday_start must be before weekday_end")
    if dt.time.fromisoformat(merged["weekend_start"]) >= dt.time.fromisoformat(merged["weekend_end"]):
        raise HTTPException(status_code=422, detail="weekend_start must be before weekend_end")
    if int(merged["min_session_minutes"]) > int(merged["max_session_minutes"]):
        raise HTTPException(status_code=422, detail="min_session_minutes cannot exceed max_session_minutes")
    if values:
        assignments, params = [], []
        for key, value in values.items():
            assignments.append(key + "=?")
            params.append(db.json(value) if key == "planning_profile" else value)
        assignments.append("updated_at=?")
        params.append(iso(utc_now()))
        db.execute("UPDATE study_settings SET " + ",".join(assignments) + " WHERE id=1", params)
        study_planner.recompute(profile)
        sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    return study_planner.settings_row()


@app.post("/api/study/recompute")
async def recompute_study_plan(payload: StudyRecomputeRequest) -> Dict[str, Any]:
    profile = require_profile()
    try:
        from_date = dt.date.fromisoformat(payload.from_date) if payload.from_date else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="from_date must use YYYY-MM-DD") from exc
    result = study_planner.recompute(profile, from_date=from_date, horizon_days=payload.horizon_days)
    sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    return result


@app.get("/api/study/plan")
async def get_study_plan(start: Optional[str] = None, end: Optional[str] = None) -> List[Dict[str, Any]]:
    require_profile()
    clauses, params = ["source_type='study_plan'", "status!='cancelled'"], []
    if start:
        clauses.append("COALESCE(end_at,start_at)>=?")
        params.append(start)
    if end:
        clauses.append("start_at<=?")
        params.append(end)
    rows = db.fetch_all("SELECT * FROM calendar_events WHERE " + " AND ".join(clauses) + " ORDER BY start_at", params)
    return [decode_event(row) for row in rows]


@app.patch("/api/study/sessions/{event_id}")
async def update_study_session(event_id: str, payload: StudySessionUpdate) -> Dict[str, Any]:
    profile = require_profile()
    row = db.fetch_one("SELECT * FROM calendar_events WHERE id=?", (event_id,))
    if not row or row["source_type"] != "study_plan":
        raise HTTPException(status_code=404, detail="Study session not found")
    db.execute("UPDATE calendar_events SET status=?,updated_at=? WHERE id=?",
               (payload.status, iso(utc_now()), event_id))
    if payload.status == "skipped":
        start = parse_datetime(row["start_at"])
        if start:
            study_planner.recompute(profile, from_date=start.astimezone(ZoneInfo(profile["timezone"])).date())
    sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    updated = db.fetch_one("SELECT * FROM calendar_events WHERE id=?", (event_id,))
    return decode_event(updated) if updated else {"status": "rescheduled"}


_DAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _expand_calendar_action(action: Dict[str, Any], timezone: ZoneInfo) -> List[Dict[str, Any]]:
    """Expand a bounded recurrence while preserving the user's local wall time."""
    start = parse_datetime(action["start_at"])
    end = parse_datetime(action["end_at"])
    if not start or not end:
        return []
    if action["recurrence"] == "once":
        return [{**action, "start_at": iso(start), "end_at": iso(end)}]

    local_start = start.astimezone(timezone)
    duration = end - start
    requested_until = dt.date.fromisoformat(action["repeat_until"])
    repeat_until = min(requested_until, local_start.date() + dt.timedelta(days=366))
    selected_days = set(action.get("weekdays") or [_DAY_CODES[local_start.weekday()]])
    expanded = []
    day = local_start.date()
    while day <= repeat_until:
        weekday = day.weekday()
        include = (
            action["recurrence"] == "daily"
            or (action["recurrence"] == "weekdays" and weekday < 5)
            or (action["recurrence"] == "weekends" and weekday >= 5)
            or (action["recurrence"] == "weekly" and _DAY_CODES[weekday] in selected_days)
        )
        if include:
            occurrence_start = dt.datetime.combine(day, local_start.timetz().replace(tzinfo=None), tzinfo=timezone)
            if occurrence_start >= local_start:
                expanded.append({
                    **action,
                    "start_at": iso(occurrence_start),
                    "end_at": iso(occurrence_start + duration),
                })
        day += dt.timedelta(days=1)
    return expanded


def _deletion_targets(action: Dict[str, Any]) -> List[Dict[str, Any]]:
    event_ids = list(dict.fromkeys(action.get("target_event_ids") or []))
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    selected = db.fetch_all(
        "SELECT * FROM calendar_events WHERE source_type='manual' AND id IN (%s)" % placeholders,
        event_ids,
    )
    if action.get("delete_scope") != "series":
        return selected
    series_ids = {
        json.loads(row.get("metadata") or "{}").get("series_id") for row in selected
    }
    series_ids.discard(None)
    if not series_ids:
        return selected
    targets = {row["id"]: row for row in selected}
    for row in db.fetch_all("SELECT * FROM calendar_events WHERE source_type='manual'"):
        if json.loads(row.get("metadata") or "{}").get("series_id") in series_ids:
            targets[row["id"]] = row
    return list(targets.values())


@app.post("/api/input")
async def natural_language_input(payload: NaturalLanguageInput) -> Dict[str, Any]:
    profile = require_profile()
    now = utc_now()
    timezone = ZoneInfo(profile["timezone"])
    existing = db.fetch_all(
        """SELECT id,title,start_at,end_at,source_type,metadata FROM calendar_events WHERE status='active'
           AND start_at>=? AND start_at<=? ORDER BY start_at LIMIT 300""",
        (iso(now - dt.timedelta(days=30)), iso(now + dt.timedelta(days=365))),
    )
    for event in existing:
        metadata = json.loads(event.pop("metadata") or "{}")
        event["series_id"] = metadata.get("series_id")
    try:
        async with OpenRouterClient(settings) as llm:
            parsed = await llm.interpret_calendar_input(
                payload.text, profile["timezone"], now.astimezone(timezone).isoformat(), existing
            )
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if parsed["needs_clarification"]:
        return {**parsed, "created_events": [], "deleted_events": [], "study_plan": None}
    created_ids = []
    deleted = []
    earliest: Optional[dt.date] = None
    timestamp = iso(now)
    with db.transaction() as connection:
        for action in parsed["actions"]:
            if action["operation"] == "delete":
                targets = _deletion_targets(action)
                for row in targets:
                    start = parse_datetime(row["start_at"])
                    if start:
                        local_date = start.astimezone(timezone).date()
                        earliest = min(earliest, local_date) if earliest else local_date
                    deleted.append(decode_event(row))
                    connection.execute("DELETE FROM calendar_events WHERE id=?", (row["id"],))
                continue
            series_id = str(uuid.uuid4()) if action["recurrence"] != "once" else None
            for event in _expand_calendar_action(action, timezone):
                event_id = str(uuid.uuid4())
                start = parse_datetime(event["start_at"])
                if not start:
                    continue
                local_date = start.astimezone(timezone).date()
                earliest = min(earliest, local_date) if earliest else local_date
                metadata = {"created_from_text": payload.text}
                if series_id:
                    metadata.update({
                        "series_id": series_id,
                        "recurrence": action["recurrence"],
                        "repeat_until": action["repeat_until"],
                    })
                connection.execute(
                    """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,
                       all_day,status,kind,url,location,metadata,created_at,updated_at)
                       VALUES(?, ?,NULL,'manual',?,?,?,?,?,'active','personal',NULL,?,?,?,?)""",
                    (event_id, "manual:" + event_id, event["title"], event["notes"], event["start_at"], event["end_at"],
                     int(event["all_day"]), event["location"] or None, db.json(metadata), timestamp, timestamp),
                )
                created_ids.append(event_id)
    plan = study_planner.recompute(profile, from_date=earliest) if earliest else None
    if earliest:
        sync_service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1") or {})
    created = [decode_event(row) for row in db.fetch_all(
        "SELECT * FROM calendar_events WHERE id IN (%s) ORDER BY start_at" % ",".join("?" for _ in created_ids),
        created_ids,
    )] if created_ids else []
    if parsed["actions"] and not created and not deleted:
        return {
            **parsed,
            "needs_clarification": True,
            "question": "I couldn't find an occurrence to change. Which date range should I use?",
            "reply": "",
            "created_events": [],
            "deleted_events": [],
            "study_plan": None,
        }
    return {**parsed, "created_events": created, "deleted_events": deleted, "study_plan": plan}


@app.get("/api/reminders")
async def list_reminders(status_filter: Optional[str] = Query(default=None, alias="status")) -> List[Dict[str, Any]]:
    require_profile()
    if status_filter:
        return db.fetch_all("SELECT * FROM reminders WHERE status=? ORDER BY notify_at", (status_filter,))
    return db.fetch_all("SELECT * FROM reminders ORDER BY notify_at")


@app.post("/api/reminders", status_code=201)
async def create_reminder(payload: ReminderCreate) -> Dict[str, Any]:
    require_profile()
    try:
        if not parse_datetime(payload.notify_at):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid reminder date") from exc
    reminder_id = str(uuid.uuid4())
    now = iso(utc_now())
    db.execute(
        """INSERT INTO reminders(id,event_id,source_key,title,body,notify_at,due_at,status,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
        (reminder_id, payload.event_id, "manual:" + reminder_id, payload.title, payload.body,
         payload.notify_at, payload.due_at, now, now),
    )
    return db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,)) or {}


@app.patch("/api/reminders/{reminder_id}")
async def update_reminder(reminder_id: str, payload: ReminderUpdate) -> Dict[str, Any]:
    require_profile()
    row = db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Reminder not found")
    values = payload.model_dump(exclude_unset=True)
    allowed = {"title", "body", "notify_at", "due_at", "status", "snoozed_until"}
    assignments, params = [], []
    for key, value in values.items():
        if key in allowed:
            assignments.append(key + "=?"); params.append(value)
    if assignments:
        assignments.append("updated_at=?"); params.extend([iso(utc_now()), reminder_id])
        db.execute("UPDATE reminders SET " + ",".join(assignments) + " WHERE id=?", params)
    return db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,)) or {}


@app.delete("/api/reminders/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: str) -> Response:
    require_profile()
    db.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
    return Response(status_code=204)


@app.get("/api/notifications/vapid-public-key")
async def vapid_public_key() -> Dict[str, str]:
    if not push_service:
        raise HTTPException(status_code=503, detail="Push service is starting")
    return {"publicKey": push_service.public_key}


@app.post("/api/notifications/subscriptions", status_code=201)
async def subscribe(payload: PushSubscriptionRequest, user_agent: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_profile()
    if not push_service:
        raise HTTPException(status_code=503, detail="Push service is starting")
    return push_service.subscribe(str(payload.endpoint), payload.keys.p256dh, payload.keys.auth,
                                  payload.user_agent or user_agent or "")


@app.delete("/api/notifications/subscriptions", status_code=204)
async def unsubscribe(endpoint: str = Query(...)) -> Response:
    require_profile()
    db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    return Response(status_code=204)


@app.post("/api/notifications/dispatch")
async def dispatch_notifications() -> Dict[str, int]:
    require_profile()
    if not push_service:
        raise HTTPException(status_code=503, detail="Push service is starting")
    return await push_service.dispatch_due()
