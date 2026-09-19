from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    canvas_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    token_encrypted TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/Toronto',
    reminder_offsets TEXT NOT NULL DEFAULT '[7,3,1]',
    onboarded_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    term_name TEXT,
    start_at TEXT,
    end_at TEXT,
    html_url TEXT,
    enrollment_state TEXT,
    raw_json TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    source_key TEXT PRIMARY KEY,
    canvas_id TEXT NOT NULL,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    url TEXT,
    due_at TEXT,
    unlock_at TEXT,
    lock_at TEXT,
    points REAL,
    submission_types TEXT NOT NULL,
    submission_status TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignments_due ON assignments(due_at);
CREATE INDEX IF NOT EXISTS idx_assignments_course ON assignments(course_id);

CREATE TABLE IF NOT EXISTS announcements (
    source_key TEXT PRIMARY KEY,
    canvas_id TEXT NOT NULL,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    url TEXT,
    posted_at TEXT,
    canvas_updated_at TEXT,
    content_hash TEXT NOT NULL,
    read_state TEXT NOT NULL DEFAULT 'new',
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_announcements_posted ON announcements(posted_at);

CREATE TABLE IF NOT EXISTS source_documents (
    source_key TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    canvas_updated_at TEXT,
    analyzed_hash TEXT,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_error TEXT,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_analysis ON source_documents(analysis_status);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    course_id TEXT REFERENCES courses(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_at TEXT NOT NULL,
    end_at TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    kind TEXT NOT NULL DEFAULT 'general',
    url TEXT,
    location TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_range ON calendar_events(start_at, end_at);
CREATE INDEX IF NOT EXISTS idx_events_course ON calendar_events(course_id);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    event_id TEXT REFERENCES calendar_events(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    notify_at TEXT NOT NULL,
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    delivered_at TEXT,
    snoozed_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, notify_at);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    last_success_at TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS analysis_results (
    source_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_key, content_hash)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS study_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    weekday_start TEXT NOT NULL DEFAULT '09:00',
    weekday_end TEXT NOT NULL DEFAULT '21:30',
    weekend_start TEXT NOT NULL DEFAULT '10:00',
    weekend_end TEXT NOT NULL DEFAULT '20:00',
    max_daily_minutes INTEGER NOT NULL DEFAULT 240,
    weekend_max_daily_minutes INTEGER NOT NULL DEFAULT 300,
    max_session_minutes INTEGER NOT NULL DEFAULT 90,
    min_session_minutes INTEGER NOT NULL DEFAULT 30,
    break_minutes INTEGER NOT NULL DEFAULT 15,
    planning_horizon_days INTEGER NOT NULL DEFAULT 90,
    planning_profile TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_plan_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    from_date TEXT NOT NULL,
    through_date TEXT NOT NULL,
    sessions_created INTEGER NOT NULL,
    scheduled_minutes INTEGER NOT NULL,
    unscheduled_minutes INTEGER NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}'
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """INSERT OR IGNORE INTO study_settings(
                   id,weekday_start,weekday_end,weekend_start,weekend_end,max_daily_minutes,
                   weekend_max_daily_minutes,max_session_minutes,min_session_minutes,break_minutes,
                   planning_horizon_days,updated_at)
                   VALUES(1,'09:00','21:30','10:00','20:00',240,300,90,30,15,90,datetime('now'))"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(study_settings)")}
            if "planning_profile" not in columns:
                connection.execute(
                    "ALTER TABLE study_settings ADD COLUMN planning_profile TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute("PRAGMA journal_mode = WAL")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        with self.connection() as connection:
            row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, params)
            return cursor.rowcount

    @staticmethod
    def json(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def decode_rows(rows: Iterable[Dict[str, Any]], fields: Iterable[str]) -> List[Dict[str, Any]]:
        result = []
        for original in rows:
            row = dict(original)
            for field in fields:
                if row.get(field):
                    row[field] = json.loads(row[field])
            result.append(row)
        return result
