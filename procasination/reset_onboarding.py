"""Reset Ordo to first-run onboarding and clear local synced data.

Stop the combined server before running this script, then restart it afterward:

    make reset-onboarding
    make run
"""

from __future__ import annotations

from app.config import settings
from app.db import Database
from app.utils import iso, utc_now


def _table_exists(connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def reset_onboarding(db: Database) -> str:
    db.initialize()
    timestamp = iso(utc_now())

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

    return str(db.path)


def main() -> int:
    database = reset_onboarding(Database(settings.database_path))
    print("Ordo reset to first-run onboarding.")
    print("Cleared synced, planning, notification, focus-history, and calendar data.")
    print("Database: %s" % database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
