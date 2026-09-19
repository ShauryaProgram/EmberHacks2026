from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database
from .utils import iso, parse_datetime, utc_now


Interval = Tuple[dt.datetime, dt.datetime]
STUDY_NAMESPACE = uuid.UUID("2996b84b-8319-4385-a251-cbea01393f33")


class StudyPlanner:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    def recompute(
        self,
        profile: Dict[str, Any],
        from_date: Optional[dt.date] = None,
        horizon_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        timezone = ZoneInfo(profile["timezone"])
        now = utc_now()
        today = now.astimezone(timezone).date()
        start_day = max(from_date or today, today)
        planner_settings = self.settings_row()
        horizon = horizon_days or int(planner_settings["planning_horizon_days"])
        configured_end = dt.date.fromisoformat(self.settings.academic_term_end)
        end_day = min(start_day + dt.timedelta(days=horizon), configured_end)
        if end_day < start_day:
            return self._empty_summary(start_day, end_day)

        range_start = dt.datetime.combine(start_day, dt.time(), timezone).astimezone(dt.timezone.utc)
        range_end = dt.datetime.combine(end_day + dt.timedelta(days=1), dt.time(), timezone).astimezone(dt.timezone.utc)
        replace_start = max(range_start, now)
        not_before = self._ceil_datetime(max(range_start, now + dt.timedelta(minutes=15)), 15).astimezone(timezone)
        targets = self._targets(now, range_end)
        completed_minutes = self._minutes_before(replace_start)

        with self.db.transaction() as connection:
            connection.execute(
                """DELETE FROM calendar_events WHERE source_type='study_plan'
                   AND source_key NOT LIKE 'study:demo:%'
                   AND start_at>=? AND start_at<? AND status!='completed'""",
                (iso(replace_start), iso(range_end)),
            )

        busy = self._busy_intervals(range_start, range_end, timezone, planner_settings)
        scheduled: Dict[dt.date, List[Interval]] = defaultdict(list)
        day_minutes: Dict[dt.date, int] = defaultdict(int)
        target_days: Dict[str, set[dt.date]] = defaultdict(set)
        sessions: List[Dict[str, Any]] = []
        unscheduled: List[Dict[str, Any]] = []

        for target in targets:
            estimate = self._estimate_minutes(target)
            remaining = max(0, estimate - completed_minutes.get(target["source_key"], 0))
            if remaining == 0:
                continue
            due = parse_datetime(target["due_at"])
            if not due:
                continue
            due_local = due.astimezone(timezone)
            lead_days = self._lead_days(target["kind"], remaining)
            target_start = max(start_day, due_local.date() - dt.timedelta(days=lead_days))
            target_end = min(end_day, due_local.date())
            created_for_target = 0
            for duration in self._session_lengths(
                remaining,
                int(planner_settings["max_session_minutes"]),
                int(planner_settings["min_session_minutes"]),
            ):
                placement = self._best_placement(
                    target_start,
                    target_end,
                    due_local,
                    duration,
                    target["source_key"],
                    planner_settings,
                    timezone,
                    busy,
                    scheduled,
                    day_minutes,
                    target_days,
                    not_before,
                )
                if not placement:
                    unscheduled.append({
                        "source_key": target["source_key"],
                        "title": target["title"],
                        "minutes": duration,
                        "due_at": target["due_at"],
                    })
                    continue
                start_at, end_at = placement
                local_day = start_at.astimezone(timezone).date()
                scheduled[local_day].append((
                    start_at - dt.timedelta(minutes=int(planner_settings["break_minutes"])),
                    end_at + dt.timedelta(minutes=int(planner_settings["break_minutes"])),
                ))
                day_minutes[local_day] += duration
                target_days[target["source_key"]].add(local_day)
                created_for_target += 1
                sessions.append(self._session_event(target, start_at, end_at, estimate, created_for_target))

        timestamp = iso(now)
        with self.db.transaction() as connection:
            for event in sessions:
                event_id = str(uuid.uuid5(STUDY_NAMESPACE, event["source_key"]))
                connection.execute(
                    """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,
                       all_day,status,kind,url,location,metadata,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,0,'active','study',?,?,?, ?,?)
                       ON CONFLICT(source_key) DO UPDATE SET title=excluded.title,description=excluded.description,
                       start_at=excluded.start_at,end_at=excluded.end_at,status='active',metadata=excluded.metadata,
                       updated_at=excluded.updated_at""",
                    (event_id, event["source_key"], event.get("course_id"), "study_plan", event["title"],
                     event["description"], event["start_at"], event["end_at"], event.get("url"), None,
                     self.db.json(event["metadata"]), timestamp, timestamp),
                )
            summary = {
                "from_date": start_day.isoformat(),
                "through_date": end_day.isoformat(),
                "sessions_created": len(sessions),
                "scheduled_minutes": sum(self._duration_minutes(row) for row in sessions),
                "unscheduled_minutes": sum(row["minutes"] for row in unscheduled),
                "unscheduled": unscheduled,
            }
            connection.execute(
                """INSERT INTO study_plan_runs(id,started_at,from_date,through_date,sessions_created,
                   scheduled_minutes,unscheduled_minutes,summary) VALUES(?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), timestamp, summary["from_date"], summary["through_date"],
                 summary["sessions_created"], summary["scheduled_minutes"], summary["unscheduled_minutes"],
                 self.db.json(summary)),
            )
        summary["sessions"] = [self._decode_session(row) for row in self._stored_sessions(range_start, range_end)]
        return summary

    def settings_row(self) -> Dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM study_settings WHERE id=1") or {}
        try:
            row["planning_profile"] = json.loads(row.get("planning_profile") or "{}")
        except (TypeError, ValueError):
            row["planning_profile"] = {}
        return row

    def _targets(self, now: dt.datetime, range_end: dt.datetime) -> List[Dict[str, Any]]:
        assignments = self.db.fetch_all(
            """SELECT a.source_key,a.course_id,a.title,a.description,a.due_at,a.points,a.kind,a.url,c.code AS course_code
               FROM assignments a JOIN courses c ON c.id=a.course_id
               WHERE a.active=1 AND a.completed=0 AND a.due_at>? AND a.due_at<? ORDER BY a.due_at""",
            (iso(now), iso(range_end)),
        )
        inferred = self.db.fetch_all(
            """SELECT e.source_key,e.course_id,e.title,e.description,e.start_at AS due_at,NULL AS points,
               e.kind,e.url,c.code AS course_code FROM calendar_events e
               LEFT JOIN courses c ON c.id=e.course_id
               WHERE e.source_type='llm' AND e.status='active' AND e.kind IN ('assignment','quiz','test','project','writing')
               AND e.start_at>? AND e.start_at<? ORDER BY e.start_at""",
            (iso(now), iso(range_end)),
        )
        known = {row["source_key"] for row in assignments}
        return assignments + [row for row in inferred if row["source_key"] not in known]

    def _minutes_before(self, cutoff: dt.datetime) -> Dict[str, int]:
        result: Dict[str, int] = defaultdict(int)
        rows = self.db.fetch_all(
            """SELECT start_at,end_at,metadata FROM calendar_events WHERE source_type='study_plan'
               AND (status='completed' OR (status='active' AND start_at<?))""",
            (iso(cutoff),),
        )
        for row in rows:
            metadata = json.loads(row.get("metadata") or "{}")
            key = metadata.get("target_source_key")
            start = parse_datetime(row["start_at"])
            end = parse_datetime(row["end_at"])
            if key and start and end:
                result[key] += max(0, round((end - start).total_seconds() / 60))
        return result

    def _busy_intervals(
        self, start: dt.datetime, end: dt.datetime, timezone: ZoneInfo, settings: Dict[str, Any]
    ) -> Dict[dt.date, List[Interval]]:
        busy: Dict[dt.date, List[Interval]] = defaultdict(list)
        rows = self.db.fetch_all(
            """SELECT start_at,end_at,all_day FROM calendar_events
               WHERE status IN ('active','completed')
               AND COALESCE(end_at,start_at)>=? AND start_at<?""",
            (iso(start), iso(end)),
        )
        buffer = dt.timedelta(minutes=int(settings["break_minutes"]))
        for row in rows:
            event_start = parse_datetime(row["start_at"])
            event_end = parse_datetime(row.get("end_at"))
            if not event_start:
                continue
            local_start = event_start.astimezone(timezone)
            if row.get("all_day"):
                local_end = (event_end or event_start + dt.timedelta(days=1)).astimezone(timezone)
                date = local_start.date()
                while date <= local_end.date():
                    busy[date].append((
                        dt.datetime.combine(date, dt.time.min, timezone),
                        dt.datetime.combine(date, dt.time.max, timezone),
                    ))
                    date += dt.timedelta(days=1)
            elif event_end and event_end > event_start:
                local_end = event_end.astimezone(timezone)
                busy[local_start.date()].append((local_start - buffer, local_end + buffer))
        self._add_recurring_protections(busy, start, end, timezone, settings, buffer)
        return busy

    @staticmethod
    def _add_recurring_protections(
        busy: Dict[dt.date, List[Interval]],
        start: dt.datetime,
        end: dt.datetime,
        timezone: ZoneInfo,
        settings: Dict[str, Any],
        buffer: dt.timedelta,
    ) -> None:
        """Expand the v5 planning profile into local busy intervals.

        These stay as planner constraints rather than calendar events. The
        frontend can label them as meals or commitments without making the
        synced calendar claim they came from Quercus.
        """
        profile = settings.get("planning_profile") or {}
        if not isinstance(profile, dict) or not profile.get("completed"):
            return
        meals = profile.get("meals") if isinstance(profile.get("meals"), list) else []
        commitments = (
            profile.get("commitments") if isinstance(profile.get("commitments"), list) else []
        )
        preferences = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}
        no_study_day = preferences.get("noStudyDay", "")
        first_day = start.astimezone(timezone).date()
        last_day = (end - dt.timedelta(microseconds=1)).astimezone(timezone).date()

        def interval(day: dt.date, value: Dict[str, Any]) -> Optional[Interval]:
            try:
                local_start = dt.datetime.combine(day, dt.time.fromisoformat(str(value["start"])), timezone)
                local_end = dt.datetime.combine(day, dt.time.fromisoformat(str(value["end"])), timezone)
            except (KeyError, TypeError, ValueError):
                return None
            if local_end <= local_start:
                return None
            return local_start - buffer, local_end + buffer

        day = first_day
        while day <= last_day:
            weekday = day.weekday()
            if str(no_study_day) == str(weekday):
                busy[day].append((
                    dt.datetime.combine(day, dt.time.min, timezone),
                    dt.datetime.combine(day, dt.time.max, timezone),
                ))
                day += dt.timedelta(days=1)
                continue
            for meal in meals:
                if not isinstance(meal, dict) or not meal.get("enabled"):
                    continue
                schedule = meal.get("schedule", "daily")
                if schedule == "weekdays" and weekday >= 5:
                    continue
                if schedule == "weekends" and weekday < 5:
                    continue
                protected = interval(day, meal)
                if protected:
                    busy[day].append(protected)
            for commitment in commitments:
                if not isinstance(commitment, dict):
                    continue
                try:
                    days = {int(value) for value in commitment.get("days", [])}
                except (TypeError, ValueError):
                    continue
                if weekday not in days:
                    continue
                protected = interval(day, commitment)
                if protected:
                    busy[day].append(protected)
            day += dt.timedelta(days=1)

    def _best_placement(
        self,
        first_day: dt.date,
        last_day: dt.date,
        due: dt.datetime,
        duration: int,
        target_key: str,
        settings: Dict[str, Any],
        timezone: ZoneInfo,
        busy: Dict[dt.date, List[Interval]],
        scheduled: Dict[dt.date, List[Interval]],
        day_minutes: Dict[dt.date, int],
        target_days: Dict[str, set[dt.date]],
        not_before: dt.datetime,
    ) -> Optional[Interval]:
        candidates = []
        date = first_day
        while date <= last_day:
            weekend = date.weekday() >= 5
            cap = int(settings["weekend_max_daily_minutes"] if weekend else settings["max_daily_minutes"])
            if day_minutes[date] + duration <= cap:
                start_text = settings["weekend_start"] if weekend else settings["weekday_start"]
                end_text = settings["weekend_end"] if weekend else settings["weekday_end"]
                window_start = dt.datetime.combine(date, dt.time.fromisoformat(start_text), timezone)
                window_end = dt.datetime.combine(date, dt.time.fromisoformat(end_text), timezone)
                window_start = max(window_start, not_before)
                if date == due.date():
                    window_end = min(window_end, due)
                intervals = busy.get(date, []) + scheduled.get(date, [])
                for free_start, free_end in self._subtract((window_start, window_end), intervals):
                    if (free_end - free_start).total_seconds() < duration * 60:
                        continue
                    preferred = dt.datetime.combine(date, dt.time(11 if weekend else 17), timezone)
                    latest = free_end - dt.timedelta(minutes=duration)
                    chosen = max(free_start, min(preferred, latest))
                    repeated = int(date in target_days[target_key])
                    score = (
                        repeated,
                        day_minutes[date] / max(cap, 1),
                        date,
                        abs((chosen - preferred).total_seconds()),
                    )
                    candidates.append((score, chosen, chosen + dt.timedelta(minutes=duration)))
            date += dt.timedelta(days=1)
        if not candidates:
            return None
        _, local_start, local_end = min(candidates, key=lambda row: row[0])
        return local_start.astimezone(dt.timezone.utc), local_end.astimezone(dt.timezone.utc)

    @staticmethod
    def _subtract(window: Interval, blocked: Sequence[Interval]) -> List[Interval]:
        intervals = [window]
        for block_start, block_end in sorted(blocked):
            next_intervals = []
            for free_start, free_end in intervals:
                if block_end <= free_start or block_start >= free_end:
                    next_intervals.append((free_start, free_end))
                    continue
                if block_start > free_start:
                    next_intervals.append((free_start, min(block_start, free_end)))
                if block_end < free_end:
                    next_intervals.append((max(block_end, free_start), free_end))
            intervals = next_intervals
        return intervals

    @staticmethod
    def _ceil_datetime(value: dt.datetime, minutes: int) -> dt.datetime:
        value = value.replace(second=0, microsecond=0)
        remainder = value.minute % minutes
        return value if remainder == 0 else value + dt.timedelta(minutes=minutes - remainder)

    @staticmethod
    def _estimate_minutes(target: Dict[str, Any]) -> int:
        base = {"quiz": 120, "assignment": 180, "writing": 420, "project": 600, "test": 720}.get(
            target.get("kind"), 180
        )
        points = float(target.get("points") or 0)
        if points >= 100:
            base = round(base * 1.25)
        elif 0 < points <= 10:
            base = round(base * 0.75)
        description_bonus = min(90, len(target.get("description") or "") // 600 * 15)
        return int(base + description_bonus)

    @staticmethod
    def _lead_days(kind: str, minutes: int) -> int:
        base = {"quiz": 4, "assignment": 7, "writing": 14, "project": 21, "test": 14}.get(kind, 7)
        return max(base, math.ceil(minutes / 60) * 2)

    @staticmethod
    def _session_lengths(total: int, maximum: int, minimum: int) -> List[int]:
        if total <= maximum:
            return [total]
        lengths = [maximum] * (total // maximum)
        remainder = total % maximum
        if remainder == 0:
            return lengths
        if remainder >= minimum:
            return lengths + [remainder]
        # Preserve the selected focus-block length where possible, while
        # borrowing just enough from the last full block for a useful final one.
        borrowed = minimum - remainder
        if lengths and lengths[-1] - borrowed >= minimum:
            lengths[-1] -= borrowed
            return lengths + [minimum]
        return lengths + [remainder]

    @staticmethod
    def _session_event(
        target: Dict[str, Any], start: dt.datetime, end: dt.datetime, estimate: int, index: int
    ) -> Dict[str, Any]:
        key = "study:%s:%s:%s" % (target["source_key"], start.strftime("%Y%m%dT%H%M"), index)
        course = target.get("course_code") or "Course"
        return {
            "source_key": key,
            "course_id": target.get("course_id"),
            "title": "Study: %s - %s" % (course, target["title"]),
            "description": "Focused preparation for %s, due %s." % (target["title"], target["due_at"]),
            "start_at": iso(start),
            "end_at": iso(end),
            "url": target.get("url"),
            "metadata": {
                "target_source_key": target["source_key"],
                "target_title": target["title"],
                "target_due_at": target["due_at"],
                "estimated_total_minutes": estimate,
                "generated_by": "constraint_planner",
                "reminder_offsets_minutes": [15],
            },
        }

    @staticmethod
    def _duration_minutes(event: Dict[str, Any]) -> int:
        start = parse_datetime(event["start_at"])
        end = parse_datetime(event["end_at"])
        return round((end - start).total_seconds() / 60) if start and end else 0

    def _stored_sessions(self, start: dt.datetime, end: dt.datetime) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT * FROM calendar_events WHERE source_type='study_plan' AND status!='cancelled'
               AND start_at>=? AND start_at<? ORDER BY start_at""",
            (iso(start), iso(end)),
        )

    @staticmethod
    def _decode_session(row: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(row)
        row["all_day"] = bool(row["all_day"])
        row["metadata"] = json.loads(row.get("metadata") or "{}")
        return row

    @staticmethod
    def _empty_summary(start: dt.date, end: dt.date) -> Dict[str, Any]:
        return {
            "from_date": start.isoformat(), "through_date": end.isoformat(), "sessions_created": 0,
            "scheduled_minutes": 0, "unscheduled_minutes": 0, "unscheduled": [], "sessions": [],
        }
