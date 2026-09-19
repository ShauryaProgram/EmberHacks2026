from __future__ import annotations

from app.db import Database
from app.utils import iso, utc_now
from procasination.reset_onboarding import reset_onboarding
from procasination.store import FocusStore


def test_reset_returns_to_onboarding_without_demo_events(tmp_path):
    db = Database(tmp_path / "reset.db")
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

    reset_onboarding(db)

    assert db.fetch_one("SELECT id FROM profile WHERE id=1") is None
    assert db.fetch_all("SELECT * FROM calendar_events") == []
    settings = db.fetch_one("SELECT planning_profile FROM study_settings WHERE id=1")
    assert settings["planning_profile"] == "{}"
