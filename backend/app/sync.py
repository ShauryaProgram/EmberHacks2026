from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

from .canvas import CanvasClient, CanvasError, CanvasSource
from .config import Settings
from .db import Database
from .openrouter import OpenRouterClient, OpenRouterError
from .security import SecretBox
from .study import StudyPlanner
from .timetable import TimetableClient, TimetableError
from .utils import (
    classify_kind,
    digest,
    html_to_text,
    iso,
    normalized_title,
    parse_datetime,
    reminder_offsets,
    utc_now,
)


EVENT_NAMESPACE = uuid.UUID("f31c7cca-ec9e-47a2-84f6-5371b014e997")


@dataclass
class CourseBundle:
    course: Dict[str, Any]
    assignments: List[Dict[str, Any]]
    announcements: List[Dict[str, Any]]
    calendar_events: List[Dict[str, Any]]
    planner_items: List[Dict[str, Any]]
    sources: List[CanvasSource]
    errors: List[str]
    assignments_complete: bool
    calendar_complete: bool = True
    timetable_events: List[Dict[str, Any]] = field(default_factory=list)
    timetable_complete: bool = True


class SyncService:
    def __init__(self, db: Database, settings: Settings, secrets: SecretBox):
        self.db = db
        self.settings = settings
        self.secrets = secrets
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def run(self, trigger: str = "manual") -> Dict[str, Any]:
        if self._lock.locked():
            return {"status": "skipped", "reason": "A sync is already running"}
        async with self._lock:
            run_id = str(uuid.uuid4())
            started = iso(utc_now())
            self.db.execute(
                "INSERT INTO sync_runs(id,started_at,status,trigger) VALUES(?,?,?,?)",
                (run_id, started, "running", trigger),
            )
            try:
                result = await self._run_once()
            except Exception as exc:
                message = str(exc) if isinstance(exc, (CanvasError, OpenRouterError, RuntimeError)) else "Unexpected sync failure"
                self.db.execute(
                    "UPDATE sync_runs SET finished_at=?,status='failed',error=? WHERE id=?",
                    (iso(utc_now()), message[:1000], run_id),
                )
                raise
            self.db.execute(
                "UPDATE sync_runs SET finished_at=?,status='succeeded',summary=? WHERE id=?",
                (iso(utc_now()), self.db.json(result), run_id),
            )
            return result

    async def _run_once(self) -> Dict[str, Any]:
        profile = self.db.fetch_one("SELECT * FROM profile WHERE id=1")
        if not profile:
            raise RuntimeError("Complete onboarding before syncing")
        token = self.secrets.decrypt(profile["token_encrypted"])
        now = utc_now()
        async with CanvasClient(token, self.settings) as canvas, TimetableClient(self.settings) as timetable:
            user, courses = await asyncio.gather(canvas.current_user(), canvas.courses())
            active_courses = [course for course in courses if self._include_course(course)]
            semaphore = asyncio.Semaphore(4)

            async def load(course: Dict[str, Any]) -> CourseBundle:
                async with semaphore:
                    return await self._load_course(canvas, timetable, course)

            bundles = await asyncio.gather(*(load(course) for course in active_courses))

        changed_sources = self._persist_canvas_data(bundles, now)
        analysis = await self._analyze_pending(profile, limit=50)
        study_plan = StudyPlanner(self.db, self.settings).recompute(profile)
        self._reconcile_all_reminders(profile)
        summary = {
            "status": "succeeded",
            "user": {"id": str(user.get("id", "")), "name": user.get("name", "")},
            "courses": len(bundles),
            "assignments": sum(len(bundle.assignments) for bundle in bundles),
            "announcements": sum(len(bundle.announcements) for bundle in bundles),
            "canvas_events": sum(len(bundle.calendar_events) for bundle in bundles),
            "timetable_events": sum(len(bundle.timetable_events) for bundle in bundles),
            "planner_items": sum(len(bundle.planner_items) for bundle in bundles),
            "changed_sources": changed_sources,
            "analyzed_sources": analysis["analyzed"],
            "pending_analysis": analysis["pending"],
            "study_sessions": study_plan["sessions_created"],
            "study_minutes": study_plan["scheduled_minutes"],
            "unscheduled_study_minutes": study_plan["unscheduled_minutes"],
            "warnings": [error for bundle in bundles for error in bundle.errors],
        }
        return summary

    def _include_course(self, course: Dict[str, Any]) -> bool:
        if course.get("access_restricted_by_date"):
            return False
        term = course.get("term") or {}
        term_name = str(term.get("name") or "").casefold()
        year = self.settings.academic_term_year
        season = self.settings.academic_term_season
        if str(year) in term_name and season in term_name:
            return True
        start_raw = course.get("start_at") or term.get("start_at")
        try:
            start = parse_datetime(start_raw)
        except (TypeError, ValueError):
            start = None
        if not start:
            return False
        if season == "fall":
            return start.year == year and 8 <= start.month <= 12
        return start.year == year

    async def _load_course(
        self, canvas: CanvasClient, timetable: TimetableClient, course: Dict[str, Any]
    ) -> CourseBundle:
        course_id = str(course["id"])
        errors: List[str] = []

        async def optional(label: str, awaitable: Any) -> Any:
            try:
                return await awaitable
            except CanvasError as exc:
                errors.append("%s %s: %s" % (course.get("course_code", course_id), label, str(exc)))
                return []

        assignments_complete = True
        try:
            assignments = await canvas.assignments(course_id)
        except CanvasError as exc:
            assignments = []
            assignments_complete = False
            errors.append("%s assignments: %s" % (course.get("course_code", course_id), str(exc)))
        async def load_calendar() -> tuple[List[Dict[str, Any]], bool]:
            try:
                return await canvas.calendar_events(course_id), True
            except CanvasError as exc:
                errors.append("%s calendar: %s" % (course.get("course_code", course_id), str(exc)))
                return [], False

        async def load_timetable() -> tuple[List[Dict[str, Any]], bool]:
            try:
                return await timetable.course_meetings(course), True
            except (TimetableError, ValueError) as exc:
                errors.append("%s timetable: %s" % (course.get("course_code", course_id), str(exc)))
                return [], False

        announcements, calendar_result, planner_items, sources, timetable_result = await asyncio.gather(
            optional("announcements", canvas.announcements(course_id)),
            load_calendar(),
            optional("planner", canvas.planner_items(course_id)),
            optional("materials", canvas.course_sources(course)),
            load_timetable(),
        )
        calendar_events, calendar_complete = calendar_result
        timetable_events, timetable_complete = timetable_result
        return CourseBundle(course, assignments, announcements, calendar_events, planner_items, sources, errors,
                            assignments_complete, calendar_complete, timetable_events, timetable_complete)

    def _persist_canvas_data(self, bundles: Sequence[CourseBundle], now: dt.datetime) -> int:
        timestamp = iso(now)
        changed_sources = 0
        with self.db.transaction() as connection:
            selected_ids = {str(bundle.course["id"]) for bundle in bundles}
            self._remove_unselected_courses(connection, selected_ids)
            for bundle in bundles:
                course = bundle.course
                course_id = str(course["id"])
                term = course.get("term") or {}
                enrollment = (course.get("enrollments") or [{}])[0]
                connection.execute(
                    """INSERT INTO courses(id,code,name,term_name,start_at,end_at,html_url,enrollment_state,raw_json,last_seen_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET code=excluded.code,name=excluded.name,term_name=excluded.term_name,
                       start_at=excluded.start_at,end_at=excluded.end_at,html_url=excluded.html_url,
                       enrollment_state=excluded.enrollment_state,raw_json=excluded.raw_json,
                       last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                    (course_id, course.get("course_code") or course.get("name") or course_id,
                     course.get("name") or course.get("course_code") or course_id, term.get("name"),
                     course.get("start_at") or term.get("start_at"), course.get("end_at") or term.get("end_at"),
                     course.get("html_url"), enrollment.get("enrollment_state"), self.db.json(course), timestamp, timestamp),
                )
                seen_assignments = set()
                for assignment in bundle.assignments:
                    if assignment.get("published") is False:
                        continue
                    key = "assignment:%s:%s" % (course_id, assignment["id"])
                    seen_assignments.add(key)
                    changed_sources += self._upsert_assignment(connection, course, assignment, key, timestamp)
                if bundle.assignments_complete:
                    self._archive_missing_assignments(connection, course_id, seen_assignments, timestamp)
                for announcement in bundle.announcements:
                    changed_sources += self._upsert_announcement(connection, course, announcement, timestamp)
                seen_calendar_events: Set[str] = set()
                for event in bundle.calendar_events:
                    for occurrence in self._expand_canvas_event(course, event):
                        key = self._upsert_canvas_event(connection, course, occurrence, timestamp)
                        if key:
                            seen_calendar_events.add(key)
                if bundle.calendar_complete:
                    self._archive_missing_calendar_events(connection, course_id, seen_calendar_events, timestamp)
                seen_timetable = set()
                for event in bundle.timetable_events:
                    self._upsert_event(connection, event, timestamp)
                    seen_timetable.add(event["source_key"])
                if bundle.timetable_complete:
                    self._archive_missing_timetable_events(connection, course_id, seen_timetable, timestamp)
                for item in bundle.planner_items:
                    self._upsert_planner_event(connection, course, item, timestamp)
                for source in bundle.sources:
                    changed_sources += self._upsert_source(connection, source, timestamp)
        return changed_sources

    @staticmethod
    def _remove_unselected_courses(connection: Any, selected_ids: Set[str]) -> None:
        old_ids = [row["id"] for row in connection.execute("SELECT id FROM courses").fetchall()
                   if row["id"] not in selected_ids]
        for course_id in old_ids:
            source_keys = connection.execute(
                "SELECT source_key FROM source_documents WHERE course_id=?", (course_id,)
            ).fetchall()
            for row in source_keys:
                connection.execute("DELETE FROM analysis_results WHERE source_key=?", (row["source_key"],))
            connection.execute(
                "DELETE FROM calendar_events WHERE course_id=? AND source_type!='manual'", (course_id,)
            )
            connection.execute("DELETE FROM courses WHERE id=?", (course_id,))

    def _upsert_assignment(self, connection: Any, course: Dict[str, Any], row: Dict[str, Any], key: str, timestamp: str) -> int:
        course_id = str(course["id"])
        description = html_to_text(row.get("description"))
        rubric = []
        for criterion in row.get("rubric") or []:
            text = html_to_text(criterion.get("description"))
            detail = html_to_text(criterion.get("long_description"))
            if text or detail:
                rubric.append("Rubric: " + ". ".join(part for part in (text, detail) if part))
        full_description = "\n".join([description] + rubric).strip()
        content_hash = digest({
            "title": row.get("name"), "description": full_description, "due_at": row.get("due_at"),
            "unlock_at": row.get("unlock_at"), "lock_at": row.get("lock_at"),
            "submission_types": row.get("submission_types"), "updated_at": row.get("updated_at"),
        })
        old = connection.execute("SELECT content_hash FROM assignments WHERE source_key=?", (key,)).fetchone()
        submission = row.get("submission") or {}
        completed = bool(submission.get("submitted_at") or submission.get("excused"))
        kind = classify_kind(str(row.get("name", "")), full_description)
        connection.execute(
            """INSERT INTO assignments(source_key,canvas_id,course_id,title,description,url,due_at,unlock_at,lock_at,
               points,submission_types,submission_status,completed,active,kind,content_hash,raw_json,last_seen_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,description=excluded.description,url=excluded.url,
               due_at=excluded.due_at,unlock_at=excluded.unlock_at,lock_at=excluded.lock_at,points=excluded.points,
               submission_types=excluded.submission_types,submission_status=excluded.submission_status,
               completed=excluded.completed,active=1,kind=excluded.kind,content_hash=excluded.content_hash,
               raw_json=excluded.raw_json,last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
            (key, str(row["id"]), course_id, row.get("name") or "Assignment", full_description,
             row.get("html_url"), row.get("due_at"), row.get("unlock_at"), row.get("lock_at"),
             row.get("points_possible"), self.db.json(row.get("submission_types") or []),
             submission.get("workflow_state"), int(completed), kind, content_hash, self.db.json(row), timestamp, timestamp),
        )
        if row.get("due_at"):
            self._upsert_event(connection, {
                "source_key": key, "course_id": course_id, "source_type": "assignment",
                "title": "%s: %s" % (course.get("course_code") or course.get("name"), row.get("name") or "Assignment"),
                "description": full_description[:20_000], "start_at": row["due_at"], "end_at": None,
                "all_day": False, "status": "completed" if completed else "active", "kind": kind,
                "url": row.get("html_url"), "location": None,
                "metadata": {"points": row.get("points_possible"), "submission_types": row.get("submission_types") or []},
            }, timestamp)
        else:
            connection.execute("UPDATE calendar_events SET status='cancelled',updated_at=? WHERE source_key=?", (timestamp, key))
        source = CanvasSource("assignment_text:%s" % key, course_id, "assignment", row.get("name") or "Assignment",
                              row.get("html_url") or "", full_description, row.get("updated_at"))
        # A dated assignment is already authoritative structured data. Only undated descriptions
        # need semantic extraction; this avoids spending one model call per ordinary assignment.
        source_changed = self._upsert_source(connection, source, timestamp) if full_description and not row.get("due_at") else 0
        return int(not old or old["content_hash"] != content_hash) + source_changed

    def _archive_missing_assignments(self, connection: Any, course_id: str, seen: Set[str], timestamp: str) -> None:
        rows = connection.execute("SELECT source_key FROM assignments WHERE course_id=? AND active=1", (course_id,)).fetchall()
        missing = [row["source_key"] for row in rows if row["source_key"] not in seen]
        for key in missing:
            connection.execute("UPDATE assignments SET active=0,updated_at=? WHERE source_key=?", (timestamp, key))
            connection.execute("UPDATE calendar_events SET status='cancelled',updated_at=? WHERE source_key=?", (timestamp, key))

    def _upsert_announcement(self, connection: Any, course: Dict[str, Any], row: Dict[str, Any], timestamp: str) -> int:
        course_id = str(course["id"])
        key = "announcement:%s:%s" % (course_id, row["id"])
        message = html_to_text(row.get("message"))
        content_hash = digest({"title": row.get("title"), "message": message, "posted_at": row.get("posted_at")})
        old = connection.execute("SELECT content_hash FROM announcements WHERE source_key=?", (key,)).fetchone()
        connection.execute(
            """INSERT INTO announcements(source_key,canvas_id,course_id,title,message,url,posted_at,canvas_updated_at,
               content_hash,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,message=excluded.message,url=excluded.url,
               posted_at=excluded.posted_at,canvas_updated_at=excluded.canvas_updated_at,content_hash=excluded.content_hash,
               read_state=CASE WHEN announcements.content_hash != excluded.content_hash THEN 'new' ELSE announcements.read_state END,
               last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
            (key, str(row["id"]), course_id, row.get("title") or "Announcement", message,
             row.get("html_url"), row.get("posted_at"), row.get("updated_at") or row.get("posted_at"),
             content_hash, timestamp, timestamp),
        )
        if message:
            self._upsert_source(connection, CanvasSource(key, course_id, "announcement", row.get("title") or "Announcement",
                                row.get("html_url") or "", message, row.get("updated_at") or row.get("posted_at")), timestamp)
        return int(not old or old["content_hash"] != content_hash)

    @staticmethod
    def _expand_canvas_event(course: Dict[str, Any], row: Dict[str, Any]) -> List[Dict[str, Any]]:
        children = [child for child in row.get("child_events") or [] if isinstance(child, dict)]
        if not row.get("hidden") or not children:
            return [row]
        enrolled_sections = {str(section.get("id")) for section in course.get("sections") or []
                             if section.get("id") is not None}
        matching = []
        for child in children:
            context = str(child.get("context_code") or "")
            section_id = context.removeprefix("course_section_") if context.startswith("course_section_") else ""
            if enrolled_sections and section_id and section_id not in enrolled_sections:
                continue
            merged = dict(row)
            merged.update({key: value for key, value in child.items() if value is not None})
            merged["hidden"] = False
            merged["parent_event_id"] = child.get("parent_event_id") or row.get("id")
            matching.append(merged)
        return matching or [row]

    @staticmethod
    def _meeting_kind(row: Dict[str, Any]) -> Optional[str]:
        text = " ".join(str(row.get(key) or "") for key in (
            "title", "context_name", "location_name", "series_natural_language"
        )).casefold()
        if re.search(r"\b(tutorial|tut)\s*\d*\b", text):
            return "tutorial"
        if re.search(r"\b(practical|pra|laboratory|lab)\s*\d*\b", text):
            return "practical"
        if re.search(r"\b(lecture|lec|seminar|class)\s*\d*\b", text):
            return "lecture"
        context = str(row.get("context_code") or "")
        if row.get("series_uuid") or row.get("rrule") or row.get("parent_event_id") or context.startswith("course_section_"):
            return "class"
        return None

    def _upsert_canvas_event(self, connection: Any, course: Dict[str, Any], row: Dict[str, Any], timestamp: str) -> Optional[str]:
        start = row.get("start_at")
        if not start or row.get("id") is None:
            return None
        key = "canvas_event:%s:%s" % (course["id"], row["id"])
        meeting_kind = self._meeting_kind(row)
        self._upsert_event(connection, {
            "source_key": key, "course_id": str(course["id"]),
            "source_type": "course_meeting" if meeting_kind else "canvas_event",
            "title": "%s: %s" % (course.get("course_code") or course.get("name"), row.get("title") or "Course event"),
            "description": html_to_text(row.get("description")), "start_at": start, "end_at": row.get("end_at"),
            "all_day": bool(row.get("all_day")), "status": "cancelled" if row.get("workflow_state") == "deleted" else "active",
            "kind": meeting_kind or "general", "url": row.get("html_url"),
            "location": row.get("location_name"), "metadata": {
                "canvas_id": row.get("id"),
                "parent_event_id": row.get("parent_event_id"),
                "series_uuid": row.get("series_uuid"),
                "rrule": row.get("rrule"),
                "series_natural_language": row.get("series_natural_language"),
                "context_code": row.get("context_code"),
                "effective_context_code": row.get("effective_context_code"),
                "location_address": row.get("location_address"),
                "reminders_disabled": bool(meeting_kind),
            },
        }, timestamp)
        return key

    @staticmethod
    def _archive_missing_calendar_events(
        connection: Any, course_id: str, seen: Set[str], timestamp: str
    ) -> None:
        rows = connection.execute(
            "SELECT source_key FROM calendar_events WHERE course_id=? AND source_key LIKE 'canvas_event:%'",
            (course_id,),
        ).fetchall()
        for row in rows:
            if row["source_key"] not in seen:
                connection.execute(
                    "UPDATE calendar_events SET status='cancelled',updated_at=? WHERE source_key=?",
                    (timestamp, row["source_key"]),
                )

    @staticmethod
    def _archive_missing_timetable_events(
        connection: Any, course_id: str, seen: Set[str], timestamp: str
    ) -> None:
        rows = connection.execute(
            "SELECT source_key FROM calendar_events WHERE course_id=? AND source_key LIKE 'timetable:%'",
            (course_id,),
        ).fetchall()
        for row in rows:
            if row["source_key"] not in seen:
                connection.execute(
                    "UPDATE calendar_events SET status='cancelled',updated_at=? WHERE source_key=?",
                    (timestamp, row["source_key"]),
                )

    def _upsert_planner_event(self, connection: Any, course: Dict[str, Any], row: Dict[str, Any], timestamp: str) -> None:
        plannable_type = str(row.get("plannable_type") or "").casefold()
        # These are already represented by richer authoritative endpoints.
        if plannable_type in {"assignment", "calendar_event", "announcement"}:
            return
        plannable = row.get("plannable") or {}
        start = (plannable.get("todo_date") or plannable.get("due_at") or
                 plannable.get("start_at") or row.get("start_at"))
        if not start:
            return
        planner_id = row.get("plannable_id") or plannable.get("id")
        if planner_id is None:
            return
        title = plannable.get("title") or plannable.get("name") or plannable_type.replace("_", " ").title()
        description = html_to_text(plannable.get("details") or plannable.get("description") or plannable.get("message"))
        override = row.get("planner_override") or {}
        submissions = row.get("submissions") or {}
        complete = bool(override.get("marked_complete") or submissions.get("submitted_at") or submissions.get("excused"))
        key = "planner:%s:%s:%s" % (course["id"], plannable_type or "item", planner_id)
        self._upsert_event(connection, {
            "source_key": key, "course_id": str(course["id"]), "source_type": "planner",
            "title": "%s: %s" % (course.get("course_code") or course.get("name"), title),
            "description": description, "start_at": start, "end_at": plannable.get("end_at"),
            "all_day": bool(plannable.get("all_day")), "status": "completed" if complete else "active",
            "kind": classify_kind(str(title), description),
            "url": row.get("html_url") or plannable.get("html_url"), "location": plannable.get("location_name"),
            "metadata": {"plannable_type": plannable_type, "plannable_id": planner_id},
        }, timestamp)

    def _upsert_source(self, connection: Any, source: CanvasSource, timestamp: str) -> int:
        content_hash = digest(source.text)
        old = connection.execute("SELECT content_hash,analyzed_hash FROM source_documents WHERE source_key=?", (source.source_key,)).fetchone()
        changed = not old or old["content_hash"] != content_hash
        status = "pending" if changed else None
        connection.execute(
            """INSERT INTO source_documents(source_key,course_id,kind,title,url,content_text,content_hash,canvas_updated_at,
               analysis_status,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,url=excluded.url,content_text=excluded.content_text,
               content_hash=excluded.content_hash,canvas_updated_at=excluded.canvas_updated_at,
               analysis_status=CASE WHEN source_documents.content_hash != excluded.content_hash THEN 'pending' ELSE source_documents.analysis_status END,
               analysis_error=CASE WHEN source_documents.content_hash != excluded.content_hash THEN NULL ELSE source_documents.analysis_error END,
               last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
            (source.source_key, source.course_id, source.kind, source.title, source.url, source.text,
             content_hash, source.updated_at, status or "pending", timestamp, timestamp),
        )
        return int(changed)

    def _upsert_event(self, connection: Any, event: Dict[str, Any], timestamp: str) -> str:
        event_id = str(uuid.uuid5(EVENT_NAMESPACE, event["source_key"]))
        connection.execute(
            """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,all_day,
               status,kind,url,location,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET course_id=excluded.course_id,source_type=excluded.source_type,
               title=excluded.title,description=excluded.description,start_at=excluded.start_at,end_at=excluded.end_at,
               all_day=excluded.all_day,status=excluded.status,kind=excluded.kind,url=excluded.url,
               location=excluded.location,metadata=excluded.metadata,updated_at=excluded.updated_at""",
            (event_id, event["source_key"], event.get("course_id"), event["source_type"], event["title"],
             event.get("description", ""), event["start_at"], event.get("end_at"), int(event.get("all_day", False)),
             event.get("status", "active"), event.get("kind", "general"), event.get("url"), event.get("location"),
             self.db.json(event.get("metadata", {})), timestamp, timestamp),
        )
        return event_id

    async def _analyze_pending(self, profile: Dict[str, Any], limit: int) -> Dict[str, int]:
        rows = self.db.fetch_all(
            """SELECT * FROM source_documents WHERE analysis_status='pending'
               ORDER BY CASE kind WHEN 'announcement' THEN 0 WHEN 'syllabus' THEN 1 WHEN 'assignment' THEN 2 ELSE 3 END, updated_at LIMIT ?""",
            (limit,),
        )
        if not rows or not self.settings.openrouter_api_key:
            return {"analyzed": 0, "pending": len(rows)}
        analyzed = 0
        async with OpenRouterClient(self.settings) as llm:
            mandatory = {row["source_key"] for row in rows if row["kind"] in {"syllabus", "file", "assignment"}}
            triage_candidates = [row for row in rows if row["source_key"] not in mandatory]
            try:
                decisions = await llm.triage(triage_candidates) if triage_candidates else {}
            except OpenRouterError:
                # Leave the complete batch pending. A later hourly run can retry without losing content.
                return {"analyzed": 0, "pending": len(rows)}
            selected = mandatory | {key for key, should_analyze in decisions.items() if should_analyze}
            ignored = [row for row in rows if row["source_key"] not in selected]
            with self.db.transaction() as connection:
                for source in ignored:
                    connection.execute(
                        """UPDATE source_documents SET analyzed_hash=content_hash,analysis_status='analyzed',
                           analysis_error=NULL,updated_at=? WHERE source_key=?""",
                        (iso(utc_now()), source["source_key"]),
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO analysis_results(source_key,content_hash,result_json,created_at) VALUES(?,?,?,?)",
                        (source["source_key"], source["content_hash"], "[]", iso(utc_now())),
                    )
                    analyzed += 1
            for source in rows:
                if source["source_key"] not in selected:
                    continue
                existing = self.db.fetch_all(
                    "SELECT * FROM calendar_events WHERE course_id=? AND status='active' ORDER BY start_at",
                    (source["course_id"],),
                )
                try:
                    actions = await llm.analyze(source, existing, profile["timezone"])
                    self._apply_analysis(source, actions)
                except OpenRouterError as exc:
                    self.db.execute(
                        "UPDATE source_documents SET analysis_status='error',analysis_error=?,updated_at=? WHERE source_key=?",
                        (str(exc)[:1000], iso(utc_now()), source["source_key"]),
                    )
                    continue
                analyzed += 1
        pending = self.db.fetch_one("SELECT COUNT(*) AS count FROM source_documents WHERE analysis_status='pending'")
        return {"analyzed": analyzed, "pending": int((pending or {}).get("count", 0))}

    def _apply_analysis(self, source: Dict[str, Any], actions: List[Dict[str, Any]]) -> None:
        timestamp = iso(utc_now())
        prefix = "llm:%s:" % digest(source["source_key"])[:16]
        retained: Set[str] = set()
        with self.db.transaction() as connection:
            for action in actions:
                target = self._match_event(connection, source["course_id"], action)
                operation = action["operation"]
                if operation == "create":
                    duplicate = self._find_duplicate(connection, source["course_id"], action)
                    if duplicate:
                        continue
                if operation in {"update", "cancel"} and target:
                    if operation == "cancel":
                        connection.execute("UPDATE calendar_events SET status='cancelled',updated_at=? WHERE id=?", (timestamp, target["id"]))
                    else:
                        connection.execute(
                            """UPDATE calendar_events SET start_at=?,end_at=?,location=COALESCE(NULLIF(?,''),location),
                               description=?,updated_at=? WHERE id=?""",
                            (action["start_at"], action.get("end_at"), action.get("location"),
                             self._analysis_description(source, action), timestamp, target["id"]),
                        )
                    continue
                identity = digest({"title": action["title"], "start": action["start_at"], "evidence": action["evidence"]})[:20]
                source_key = prefix + identity
                retained.add(source_key)
                kind = "class_change" if action["action_type"] == "class_change" else action["category"]
                self._upsert_event(connection, {
                    "source_key": source_key, "course_id": source["course_id"], "source_type": "llm",
                    "title": action["title"], "description": self._analysis_description(source, action),
                    "start_at": action["start_at"], "end_at": action.get("end_at"), "all_day": action["all_day"],
                    # An unmatched class cancellation remains a visible alert instead of disappearing.
                    "status": ("active" if action["action_type"] == "class_change" else "cancelled")
                              if operation == "cancel" else "active", "kind": kind,
                    "url": source.get("url"), "location": action.get("location"),
                    "metadata": {"origin_source": source["source_key"], "evidence": action["evidence"],
                                 "reminder_offsets_days": action["reminder_offsets_days"]},
                }, timestamp)
            old = connection.execute("SELECT source_key FROM calendar_events WHERE source_key LIKE ?", (prefix + "%",)).fetchall()
            for row in old:
                if row["source_key"] not in retained:
                    connection.execute("UPDATE calendar_events SET status='cancelled',updated_at=? WHERE source_key=?",
                                       (timestamp, row["source_key"]))
            connection.execute(
                "UPDATE source_documents SET analyzed_hash=content_hash,analysis_status='analyzed',analysis_error=NULL,updated_at=? WHERE source_key=?",
                (timestamp, source["source_key"]),
            )
            connection.execute(
                "INSERT OR REPLACE INTO analysis_results(source_key,content_hash,result_json,created_at) VALUES(?,?,?,?)",
                (source["source_key"], source["content_hash"], self.db.json(actions), timestamp),
            )

    @staticmethod
    def _analysis_description(source: Dict[str, Any], action: Dict[str, Any]) -> str:
        return "\n".join(part for part in (
            action.get("summary"), "Evidence: " + action["evidence"], "Source: " + (source.get("url") or "")
        ) if part)[:20_000]

    @staticmethod
    def _match_event(connection: Any, course_id: str, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        target = action.get("normalized_target")
        if not target:
            return None
        rows = connection.execute(
            "SELECT * FROM calendar_events WHERE course_id=? AND status='active'", (course_id,)
        ).fetchall()
        matches = []
        action_time = parse_datetime(action.get("target_start_at") or action.get("start_at"))
        for raw in rows:
            row = dict(raw)
            title = normalized_title(row["title"])
            if target != title and target not in title:
                continue
            event_time = parse_datetime(row["start_at"])
            if action_time and event_time and abs((event_time - action_time).total_seconds()) > 36 * 3600:
                continue
            matches.append(row)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _find_duplicate(connection: Any, course_id: str, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = normalized_title(action["title"])
        action_time = parse_datetime(action.get("start_at"))
        if not title or not action_time:
            return None
        rows = connection.execute(
            "SELECT * FROM calendar_events WHERE course_id=? AND status='active'", (course_id,)
        ).fetchall()
        matches = []
        for raw in rows:
            row = dict(raw)
            event_title = normalized_title(row["title"])
            event_time = parse_datetime(row["start_at"])
            if event_time and (title == event_title or title in event_title or event_title in title):
                if abs((event_time - action_time).total_seconds()) <= 5 * 60:
                    matches.append(row)
        return matches[0] if len(matches) == 1 else None

    def _reconcile_all_reminders(self, profile: Dict[str, Any]) -> None:
        events = self.db.fetch_all("SELECT * FROM calendar_events")
        default_offsets = json.loads(profile["reminder_offsets"])
        timezone = ZoneInfo(profile["timezone"])
        now = utc_now()
        timestamp = iso(now)
        with self.db.transaction() as connection:
            for event in events:
                due = parse_datetime(event["start_at"])
                existing = connection.execute("SELECT * FROM reminders WHERE event_id=?", (event["id"],)).fetchall()
                if event["status"] != "active" or due is None or due < now:
                    connection.execute(
                        "UPDATE reminders SET status='cancelled',updated_at=? WHERE event_id=? AND status='pending'",
                        (timestamp, event["id"]),
                    )
                    continue
                metadata = json.loads(event.get("metadata") or "{}")
                if metadata.get("reminders_disabled"):
                    connection.execute(
                        "UPDATE reminders SET status='cancelled',updated_at=? WHERE event_id=? AND status='pending'",
                        (timestamp, event["id"]),
                    )
                    continue
                minute_offsets = metadata.get("reminder_offsets_minutes")
                if minute_offsets is not None:
                    wanted = set()
                    for minutes in sorted({int(value) for value in minute_offsets if 0 <= int(value) <= 1440}, reverse=True):
                        notify = due - dt.timedelta(minutes=minutes)
                        if notify <= now:
                            continue
                        source_key = "reminder:%s:m%s" % (event["id"], minutes)
                        wanted.add(source_key)
                        reminder_id = str(uuid.uuid5(EVENT_NAMESPACE, source_key))
                        connection.execute(
                            """INSERT INTO reminders(id,event_id,source_key,title,body,notify_at,due_at,status,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?,'pending',?,?)
                               ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,body=excluded.body,
                               notify_at=excluded.notify_at,due_at=excluded.due_at,
                               status=CASE WHEN reminders.status='delivered' AND reminders.notify_at=excluded.notify_at
                                      THEN 'delivered' ELSE 'pending' END,
                               delivered_at=CASE WHEN reminders.notify_at=excluded.notify_at
                                            THEN reminders.delivered_at ELSE NULL END,
                               updated_at=excluded.updated_at""",
                            (reminder_id, event["id"], source_key, event["title"], event["description"][:1000],
                             iso(notify), event["start_at"], timestamp, timestamp),
                        )
                    for reminder in existing:
                        if reminder["source_key"] not in wanted and reminder["status"] == "pending":
                            connection.execute(
                                "UPDATE reminders SET status='cancelled',updated_at=? WHERE id=?",
                                (timestamp, reminder["id"]),
                            )
                    continue
                offsets = metadata.get("reminder_offsets_days") or reminder_offsets(event["kind"])
                if event["source_type"] == "assignment" and event["kind"] == "assignment":
                    offsets = default_offsets
                wanted = set()
                for days in sorted({int(value) for value in offsets if 0 <= int(value) <= 60}, reverse=True):
                    local_due = due.astimezone(timezone)
                    if days == 0:
                        notify = due - dt.timedelta(hours=2)
                    else:
                        notify_local = (local_due - dt.timedelta(days=days)).replace(hour=18, minute=0, second=0, microsecond=0)
                        notify = notify_local.astimezone(dt.timezone.utc)
                    if notify <= now:
                        continue
                    source_key = "reminder:%s:d%s" % (event["id"], days)
                    wanted.add(source_key)
                    reminder_id = str(uuid.uuid5(EVENT_NAMESPACE, source_key))
                    connection.execute(
                        """INSERT INTO reminders(id,event_id,source_key,title,body,notify_at,due_at,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,'pending',?,?)
                           ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,body=excluded.body,
                           notify_at=excluded.notify_at,due_at=excluded.due_at,
                           status=CASE WHEN reminders.status='delivered' AND reminders.notify_at=excluded.notify_at THEN 'delivered' ELSE 'pending' END,
                           delivered_at=CASE WHEN reminders.notify_at=excluded.notify_at THEN reminders.delivered_at ELSE NULL END,
                           updated_at=excluded.updated_at""",
                        (reminder_id, event["id"], source_key, event["title"], event["description"][:1000],
                         iso(notify), event["start_at"], timestamp, timestamp),
                    )
                if not wanted and not existing and due > now:
                    source_key = "reminder:%s:catchup" % event["id"]
                    reminder_id = str(uuid.uuid5(EVENT_NAMESPACE, source_key))
                    connection.execute(
                        """INSERT OR IGNORE INTO reminders(id,event_id,source_key,title,body,notify_at,due_at,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                        (reminder_id, event["id"], source_key, event["title"], event["description"][:1000],
                         iso(now + dt.timedelta(minutes=5)), event["start_at"], timestamp, timestamp),
                    )
                    wanted.add(source_key)
                for reminder in existing:
                    if reminder["source_key"] not in wanted and reminder["status"] == "pending":
                        connection.execute("UPDATE reminders SET status='cancelled',updated_at=? WHERE id=?",
                                           (timestamp, reminder["id"]))
