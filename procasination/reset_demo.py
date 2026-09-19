"""Reset Ordo to first-run onboarding while keeping the 3-4 PM demo session.

Stop the combined server before running this script, then restart it afterward:

    make reset-demo
    make run
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import Database
from app.utils import iso, utc_now


DEMO_SOURCE_KEY = "study:demo:mandatory-3pm"
DEMO_NAMESPACE = uuid.UUID("551f70c2-2a50-4c67-916f-55a3f07f9623")


def _table_exists(connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def reset_demo(db: Database) -> dict[str, str]:
    db.initialize()
    now = utc_now()
    profile = db.fetch_one("SELECT timezone FROM profile WHERE id=1")
    timezone_name = str((profile or {}).get("timezone") or "America/Toronto")
    timezone = ZoneInfo(timezone_name)
    local_day = now.astimezone(timezone).date()
    start = dt.datetime.combine(local_day, dt.time(15, 0), timezone).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(local_day, dt.time(16, 0), timezone).astimezone(dt.timezone.utc)
    due = dt.datetime.combine(local_day + dt.timedelta(days=2), dt.time(23, 59), timezone)
    event_id = str(uuid.uuid5(DEMO_NAMESPACE, local_day.isoformat()))
    timestamp = iso(now)

    with db.transaction() as connection:
        for table in ("focus_observations", "focus_nudges", "focus_sessions", "focus_watcher"):
            if _table_exists(connection, table):
                connection.execute("DELETE FROM " + table)

        for table in (
            "study_plan_runs", "push_subscriptions", "reminders", "calendar_events",
            "analysis_results", "source_documents", "announcements", "assignments",
            "courses", "sync_runs", "profile",
        ):
            connection.execute("DELETE FROM " + table)

        connection.execute(
            """UPDATE study_settings SET weekday_start='09:00',weekday_end='21:30',
               weekend_start='10:00',weekend_end='20:00',max_daily_minutes=240,
               weekend_max_daily_minutes=300,max_session_minutes=90,min_session_minutes=30,
               break_minutes=15,planning_horizon_days=90,planning_profile='{}',updated_at=?
               WHERE id=1""",
            (timestamp,),
        )
        connection.execute(
            """INSERT INTO calendar_events(
                   id,source_key,course_id,source_type,title,description,start_at,end_at,
                   all_day,status,kind,url,location,metadata,created_at,updated_at
               ) VALUES(?,?,NULL,'study_plan',?,?,?,?,0,'active','study',NULL,NULL,?,?,?)""",
            (
                event_id,
                DEMO_SOURCE_KEY,
                "Study: MAT137H5 - PCQ3",
                "Compulsory demo study session for MAT137 PCQ3.",
                iso(start),
                iso(end),
                json.dumps({
                    "target_title": "PCQ3",
                    "target_due_at": iso(due),
                    "generated_by": "demo_reset",
                    "compulsory": True,
                }),
                timestamp,
                timestamp,
            ),
        )

    return {
        "database": str(db.path),
        "timezone": timezone_name,
        "study_start": start.astimezone(timezone).isoformat(),
        "study_end": end.astimezone(timezone).isoformat(),
    }


def main() -> int:
    result = reset_demo(Database(settings.database_path))
    print("Ordo reset to first-run onboarding.")
    print("Cleared synced, planning, notification, and focus-history data.")
    print("Kept compulsory study session: %s to %s." % (result["study_start"], result["study_end"]))
    print("Database: %s" % result["database"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
