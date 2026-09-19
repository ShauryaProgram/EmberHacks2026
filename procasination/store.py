from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from app.db import Database
from app.utils import iso, parse_datetime, utc_now


# Own tables on the backend's SQLite file. event_id is intentionally not a
# foreign key: the study planner deletes and recreates study events on every
# recompute, and a finished focus session should outlive that.
SCHEMA = """
CREATE TABLE IF NOT EXISTS focus_sessions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    course_id TEXT,
    session_title TEXT NOT NULL DEFAULT '',
    target_title TEXT NOT NULL DEFAULT '',
    target_due_at TEXT,
    scheduled_start_at TEXT NOT NULL,
    scheduled_end_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    on_task_seconds INTEGER NOT NULL DEFAULT 0,
    off_task_seconds INTEGER NOT NULL DEFAULT 0,
    unknown_seconds INTEGER NOT NULL DEFAULT 0,
    screenshot_count INTEGER NOT NULL DEFAULT 0,
    nudge_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_focus_sessions_event ON focus_sessions(event_id);
CREATE INDEX IF NOT EXISTS idx_focus_sessions_started ON focus_sessions(started_at);

CREATE TABLE IF NOT EXISTS focus_observations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES focus_sessions(id) ON DELETE CASCADE,
    segment_key TEXT NOT NULL,
    app TEXT NOT NULL,
    window_title TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    sample_count INTEGER NOT NULL DEFAULT 1,
    idle_seconds INTEGER NOT NULL DEFAULT 0,
    verdict TEXT NOT NULL,
    confidence INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'unknown',
    reason TEXT NOT NULL DEFAULT '',
    decided_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_focus_observations_session ON focus_observations(session_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_focus_observations_segment
    ON focus_observations(session_id, segment_key, started_at);

CREATE TABLE IF NOT EXISTS focus_nudges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES focus_sessions(id) ON DELETE CASCADE,
    level INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    next_step TEXT NOT NULL DEFAULT '',
    trigger_app TEXT NOT NULL DEFAULT '',
    trigger_title TEXT NOT NULL DEFAULT '',
    off_task_seconds INTEGER NOT NULL DEFAULT 0,
    delivered_push INTEGER NOT NULL DEFAULT 0,
    delivered_native INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_focus_nudges_session ON focus_nudges(session_id, created_at);

-- Exactly one row, naming the process currently allowed to watch. Two monitors
-- on one database would double-count every second and nudge twice for the same
-- drift, which is easy to cause by leaving an old server running.
CREATE TABLE IF NOT EXISTS focus_watcher (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
"""

OBSERVATION_LIMIT = 500


class FocusStore:
    """Every read and write the watcher performs."""

    def __init__(self, db: Database):
        self.db = db

    def initialize(self) -> None:
        with self.db.transaction() as connection:
            connection.executescript(SCHEMA)

    # -- study sessions the watcher should be active for -------------------

    def active_study_session(self, now: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The study block in progress right now, if any.

        A skipped or completed session is not watched; the student has already
        told us where they stand.
        """
        moment = now or iso(utc_now())
        row = self.db.fetch_one(
            """SELECT id,course_id,title,start_at,end_at,metadata FROM calendar_events
               WHERE source_type='study_plan' AND status='active'
               AND start_at<=? AND end_at>? ORDER BY start_at LIMIT 1""",
            (moment, moment),
        )
        if not row:
            return None
        row["metadata"] = json.loads(row.get("metadata") or "{}")
        return row

    def next_study_session(self, now: Optional[str] = None) -> Optional[Dict[str, Any]]:
        moment = now or iso(utc_now())
        row = self.db.fetch_one(
            """SELECT id,title,start_at,end_at FROM calendar_events
               WHERE source_type='study_plan' AND status='active' AND start_at>?
               ORDER BY start_at LIMIT 1""",
            (moment,),
        )
        return row

    def course_code(self, course_id: Optional[str]) -> str:
        if not course_id:
            return ""
        row = self.db.fetch_one("SELECT code,name FROM courses WHERE id=?", (course_id,))
        if not row:
            return ""
        return str(row.get("code") or row.get("name") or "")

    def enrolled_courses(self, limit: int = 20) -> List[str]:
        """Course codes the student is taking.

        Given to the model so it can tell a different course's work apart from
        entertainment. Both are off task during a block scheduled for one
        assignment, but they deserve very different wording.
        """
        rows = self.db.fetch_all(
            "SELECT code,name FROM courses ORDER BY code LIMIT ?", (max(1, min(limit, 50)),)
        )
        codes = []
        for row in rows:
            code = str(row.get("code") or row.get("name") or "").strip()
            if code:
                codes.append(code[:80])
        return codes

    # -- single-watcher lock -----------------------------------------------

    def claim_watch(self, pid: int, stale_after_seconds: int) -> Dict[str, Any]:
        """Try to become the process that watches this database.

        Returns the outcome and the holder's pid. A lock whose heartbeat has gone
        quiet is taken over, so a crashed or killed server does not block the
        next one forever.
        """
        now = utc_now()
        with self.db.transaction() as connection:
            row = connection.execute("SELECT pid,heartbeat_at FROM focus_watcher WHERE id=1").fetchone()
            if row is not None and int(row["pid"]) != pid:
                beat = parse_datetime(row["heartbeat_at"])
                fresh = beat is not None and (now - beat).total_seconds() < stale_after_seconds
                if fresh:
                    return {"granted": False, "holder_pid": int(row["pid"])}
            connection.execute(
                """INSERT INTO focus_watcher(id,pid,started_at,heartbeat_at) VALUES(1,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,
                     started_at=CASE WHEN focus_watcher.pid=excluded.pid
                                     THEN focus_watcher.started_at ELSE excluded.started_at END,
                     heartbeat_at=excluded.heartbeat_at""",
                (pid, iso(now), iso(now)),
            )
        return {"granted": True, "holder_pid": pid}

    def release_watch(self, pid: int) -> None:
        self.db.execute("DELETE FROM focus_watcher WHERE id=1 AND pid=?", (pid,))

    # -- focus sessions ----------------------------------------------------

    def open_focus_session(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Find or create the focus session for a study block.

        Reuses an unfinished row so a backend restart mid-session resumes the
        same record instead of fragmenting the timeline.
        """
        existing = self.db.fetch_one(
            "SELECT * FROM focus_sessions WHERE event_id=? AND ended_at IS NULL", (event["id"],)
        )
        if existing:
            return existing
        metadata = event.get("metadata") or {}
        timestamp = iso(utc_now())
        session_id = str(uuid.uuid4())
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO focus_sessions(id,event_id,course_id,session_title,target_title,target_due_at,
                   scheduled_start_at,scheduled_end_at,started_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    event["id"],
                    event.get("course_id"),
                    str(event.get("title") or "")[:300],
                    str(metadata.get("target_title") or "")[:300],
                    metadata.get("target_due_at"),
                    event["start_at"],
                    event["end_at"],
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row)

    def close_focus_session(self, session_id: str) -> None:
        timestamp = iso(utc_now())
        self.db.execute(
            "UPDATE focus_sessions SET ended_at=?,updated_at=? WHERE id=? AND ended_at IS NULL",
            (timestamp, timestamp, session_id),
        )

    def add_time(self, session_id: str, bucket: str, seconds: int) -> None:
        column = {
            "on_task": "on_task_seconds",
            "off_task": "off_task_seconds",
            "unknown": "unknown_seconds",
        }.get(bucket)
        if not column or seconds <= 0:
            return
        self.db.execute(
            "UPDATE focus_sessions SET %s=%s+?,updated_at=? WHERE id=?" % (column, column),
            (seconds, iso(utc_now()), session_id),
        )

    def count_screenshot(self, session_id: str) -> None:
        self.db.execute(
            "UPDATE focus_sessions SET screenshot_count=screenshot_count+1,updated_at=? WHERE id=?",
            (iso(utc_now()), session_id),
        )

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.db.fetch_one("SELECT * FROM focus_sessions WHERE id=?", (session_id,))

    def recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM focus_sessions ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 100)),)
        )

    # -- observations ------------------------------------------------------

    def record_observation(self, session_id: str, segment: Any, verdict: Dict[str, Any], duration: int) -> None:
        """Upsert one segment's row.

        A segment that is still on screen is re-reported every verdict cycle as
        it grows, and a screenshot can overturn its earlier text verdict, so the
        row is keyed on the segment rather than appended to.
        """
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO focus_observations(id,session_id,segment_key,app,window_title,site,
                   started_at,ended_at,duration_seconds,sample_count,idle_seconds,
                   verdict,confidence,category,reason,decided_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id,segment_key,started_at) DO UPDATE SET
                     ended_at=excluded.ended_at,duration_seconds=excluded.duration_seconds,
                     sample_count=excluded.sample_count,idle_seconds=excluded.idle_seconds,
                     verdict=excluded.verdict,confidence=excluded.confidence,
                     category=excluded.category,reason=excluded.reason,decided_by=excluded.decided_by""",
                (
                    str(uuid.uuid4()),
                    session_id,
                    segment.key,
                    segment.app,
                    segment.window_title[:300],
                    segment.host,
                    segment.started_at,
                    segment.ended_at,
                    duration,
                    segment.sample_count,
                    segment.max_idle_seconds,
                    verdict["verdict"],
                    int(verdict.get("confidence") or 0),
                    str(verdict.get("category") or "unknown"),
                    str(verdict.get("reason") or "")[:240],
                    str(verdict.get("decided_by") or "unknown"),
                    iso(utc_now()),
                ),
            )

    def observations(self, session_id: str, limit: int = OBSERVATION_LIMIT) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM focus_observations WHERE session_id=? ORDER BY started_at LIMIT ?",
            (session_id, max(1, min(limit, OBSERVATION_LIMIT))),
        )

    # -- nudges ------------------------------------------------------------

    def mark_nudge_delivered(self, nudge_id: str, delivered_push: bool, delivered_native: bool) -> None:
        """Fill in the delivery outcome once the notification has been attempted.

        The row is written before delivery, because a modal can sit on screen for
        the better part of a minute and the record should not wait on a click.
        """
        self.db.execute(
            "UPDATE focus_nudges SET delivered_push=?,delivered_native=? WHERE id=?",
            (int(delivered_push), int(delivered_native), nudge_id),
        )

    def record_nudge(
        self,
        session_id: str,
        level: int,
        nudge: Dict[str, str],
        trigger_app: str,
        trigger_title: str,
        off_task_seconds: int,
        delivered_push: bool = False,
        delivered_native: bool = False,
    ) -> Dict[str, Any]:
        nudge_id = str(uuid.uuid4())
        timestamp = iso(utc_now())
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO focus_nudges(id,session_id,level,title,body,next_step,trigger_app,trigger_title,
                   off_task_seconds,delivered_push,delivered_native,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    nudge_id,
                    session_id,
                    level,
                    nudge["title"],
                    nudge["body"],
                    nudge.get("next_step", ""),
                    trigger_app[:120],
                    trigger_title[:300],
                    off_task_seconds,
                    int(delivered_push),
                    int(delivered_native),
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE focus_sessions SET nudge_count=nudge_count+1,updated_at=? WHERE id=?",
                (timestamp, session_id),
            )
            row = connection.execute("SELECT * FROM focus_nudges WHERE id=?", (nudge_id,)).fetchone()
        return dict(row)

    def nudges(self, session_id: str) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM focus_nudges WHERE session_id=? ORDER BY created_at", (session_id,)
        )
