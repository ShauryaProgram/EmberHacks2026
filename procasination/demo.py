"""Seed a study session that is active right now, so the watcher starts at once.

    python -m procasination.demo --minutes 20

Creates one `study_plan` event covering the current time and removes it again on
Ctrl+C, so you do not have to wait for a real study block to come around. The
event carries a marker source key and is the only thing this script ever writes
or deletes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import sys
import time
import uuid

from app.db import Database
from app.config import settings as backend_settings
from app.utils import iso, utc_now

MARKER = "study:procrastination-demo"


def seed(db: Database, minutes: int, target: str) -> str:
    now = utc_now()
    event_id = str(uuid.uuid4())
    db.execute("DELETE FROM calendar_events WHERE source_key=?", (MARKER,))
    db.execute(
        """INSERT INTO calendar_events(id,source_key,source_type,title,description,start_at,end_at,
           all_day,status,kind,metadata,created_at,updated_at)
           VALUES(?,?,'study_plan',?,'',?,?,0,'active','study',?,?,?)""",
        (
            event_id,
            MARKER,
            "Study: " + target,
            # Backdated a minute so the session is unambiguously in progress.
            iso(now - dt.timedelta(minutes=1)),
            iso(now + dt.timedelta(minutes=minutes)),
            json.dumps({
                "target_title": target,
                "target_due_at": iso(now + dt.timedelta(days=1)),
                "generated_by": "procrastination_demo",
            }),
            iso(now),
            iso(now),
        ),
    )
    return event_id


def remove(db: Database) -> None:
    db.execute("DELETE FROM calendar_events WHERE source_key=?", (MARKER,))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a live study session for the watcher.")
    parser.add_argument("--minutes", type=int, default=20, help="how long the session runs (default 20)")
    parser.add_argument(
        "--target",
        default="MAT137 Problem Set 3: epsilon-delta proofs",
        help="the assignment the watcher judges you against",
    )
    parser.add_argument("--keep", action="store_true", help="leave the event behind on exit")
    args = parser.parse_args()

    db = Database(backend_settings.database_path)
    db.initialize()
    seed(db, args.minutes, args.target)

    print("Seeded a study session ending in %d minutes." % args.minutes)
    print("Assignment: %s" % args.target)
    print()
    print("The watcher picks it up within ~15s. Try:")
    print("  curl -s localhost:8766/api/procrastination/status | python3 -m json.tool")
    print()
    if args.keep:
        return 0
    print("Ctrl+C removes the event again.")

    def cleanup(*_args: object) -> None:
        remove(db)
        print("\nRemoved the demo session.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
