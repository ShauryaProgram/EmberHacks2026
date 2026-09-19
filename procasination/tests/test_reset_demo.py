from __future__ import annotations

from zoneinfo import ZoneInfo

from app.db import Database
from app.utils import iso, parse_datetime, utc_now
from procasination.reset_demo import DEMO_SOURCE_KEY, reset_demo
from procasination.store import FocusStore


def test_reset_returns_to_onboarding_and_keeps_only_demo_session(tmp_path):
    db = Database(tmp_path / "demo.db")
    db.initialize()
    FocusStore(db).initialize()
    now = iso(utc_now())
    db.execute(
        """INSERT INTO profile(
               id,canvas_user_id,name,token_encrypted,timezone,reminder_offsets,onboarded_at,updated_at
           ) VALUES(1,'1','Student','encrypted','America/Toronto','[]',?,?)""",
        (now, now),
    )
    db.execute(
        """INSERT INTO calendar_events(
               id,source_key,source_type,title,start_at,status,kind,created_at,updated_at
           ) VALUES('old','study:old','study_plan','Old session',?,'active','study',?,?)""",
        (now, now, now),
    )
    db.execute("UPDATE study_settings SET planning_profile=? WHERE id=1", ('{"version":5}',))

    reset_demo(db)

    assert db.fetch_one("SELECT id FROM profile WHERE id=1") is None
    events = db.fetch_all("SELECT * FROM calendar_events")
    assert len(events) == 1
    assert events[0]["source_key"] == DEMO_SOURCE_KEY
    timezone = ZoneInfo("America/Toronto")
    assert parse_datetime(events[0]["start_at"]).astimezone(timezone).hour == 15
    assert parse_datetime(events[0]["end_at"]).astimezone(timezone).hour == 16
    settings = db.fetch_one("SELECT planning_profile FROM study_settings WHERE id=1")
    assert settings["planning_profile"] == "{}"
