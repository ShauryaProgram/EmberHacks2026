import datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.db import Database
from app.main import _expand_calendar_action
from app.openrouter import OpenRouterError, validate_calendar_input
from app.study import StudyPlanner
from app.utils import iso, parse_datetime


def make_planner(tmp_path):
    settings = Settings(
        database_path=tmp_path / "study.db",
        secret_key_path=tmp_path / ".key",
        vapid_key_path=tmp_path / ".vapid",
        academic_term_start="2026-09-08",
        academic_term_end="2026-12-08",
    )
    db = Database(settings.database_path)
    db.initialize()
    return db, StudyPlanner(db, settings)


def insert_course_and_assignment(db, due_at, source_key="assignment:1:2", title="Lab 2"):
    now = "2026-09-19T12:00:00Z"
    db.execute(
        """INSERT INTO courses(id,code,name,term_name,start_at,end_at,html_url,enrollment_state,raw_json,last_seen_at,updated_at)
           VALUES('1','CSC110Y5','Computer Science','2026 Fall',NULL,NULL,NULL,'active','{}',?,?)""",
        (now, now),
    )
    db.execute(
        """INSERT INTO assignments(source_key,canvas_id,course_id,title,description,url,due_at,unlock_at,lock_at,
           points,submission_types,submission_status,completed,active,kind,content_hash,raw_json,last_seen_at,updated_at)
           VALUES(?, '2','1',?,'Complete the exercises',NULL,?,NULL,NULL,10,'[]',NULL,0,1,'assignment','x','{}',?,?)""",
        (source_key, title, due_at, now, now),
    )


def test_study_plan_respects_events_hours_and_daily_cap(tmp_path, monkeypatch):
    db, planner = make_planner(tmp_path)
    fixed_now = dt.datetime(2026, 9, 19, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("app.study.utc_now", lambda: fixed_now)
    insert_course_and_assignment(db, "2026-09-25T23:59:00-04:00")
    db.execute(
        """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,
           all_day,status,kind,metadata,created_at,updated_at)
           VALUES('class','timetable:1','1','course_meeting','Lecture','',
           '2026-09-21T20:30:00Z','2026-09-21T22:30:00Z',0,'active','lecture','{}',?,?)""",
        (iso(fixed_now), iso(fixed_now)),
    )
    profile = {"timezone": "America/Toronto"}

    result = planner.recompute(profile)

    assert result["scheduled_minutes"] == 135
    assert result["unscheduled_minutes"] == 0
    assert len(result["sessions"]) == 2
    timezone = ZoneInfo("America/Toronto")
    daily = defaultdict(int)
    for session in result["sessions"]:
        start = parse_datetime(session["start_at"])
        end = parse_datetime(session["end_at"])
        local_start = start.astimezone(timezone)
        local_end = end.astimezone(timezone)
        assert dt.time(9) <= local_start.time()
        assert local_end.time() <= dt.time(21, 30)
        assert end <= parse_datetime("2026-09-25T23:59:00-04:00")
        assert not (start < parse_datetime("2026-09-21T22:30:00Z") and
                    end > parse_datetime("2026-09-21T20:30:00Z"))
        daily[local_start.date()] += round((end - start).total_seconds() / 60)
    assert max(daily.values()) <= 240


def test_new_busy_event_moves_only_future_study_sessions(tmp_path, monkeypatch):
    db, planner = make_planner(tmp_path)
    fixed_now = dt.datetime(2026, 9, 19, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("app.study.utc_now", lambda: fixed_now)
    insert_course_and_assignment(db, "2026-09-25T23:59:00-04:00")
    profile = {"timezone": "America/Toronto"}
    first = planner.recompute(profile)
    blocked = first["sessions"][0]
    db.execute(
        """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,
           all_day,status,kind,metadata,created_at,updated_at)
           VALUES('personal','manual:personal',NULL,'manual','Appointment','',?,?,0,'active','personal','{}',?,?)""",
        (blocked["start_at"], blocked["end_at"], iso(fixed_now), iso(fixed_now)),
    )
    blocked_day = parse_datetime(blocked["start_at"]).astimezone(ZoneInfo("America/Toronto")).date()

    second = planner.recompute(profile, from_date=blocked_day)

    assert all(not (
        parse_datetime(row["start_at"]) < parse_datetime(blocked["end_at"]) and
        parse_datetime(row["end_at"]) > parse_datetime(blocked["start_at"])
    ) for row in second["sessions"])


def test_study_sessions_on_same_day_include_configured_break(tmp_path, monkeypatch):
    db, planner = make_planner(tmp_path)
    fixed_now = dt.datetime(2026, 9, 19, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("app.study.utc_now", lambda: fixed_now)
    insert_course_and_assignment(db, "2026-09-19T23:59:00-04:00")

    result = planner.recompute({"timezone": "America/Toronto"})

    sessions = sorted(result["sessions"], key=lambda row: row["start_at"])
    assert len(sessions) == 2
    first_end = parse_datetime(sessions[0]["end_at"])
    second_start = parse_datetime(sessions[1]["start_at"])
    assert second_start - first_end >= dt.timedelta(minutes=15)


def test_v5_meals_commitments_and_no_study_day_are_protected(tmp_path, monkeypatch):
    db, planner = make_planner(tmp_path)
    fixed_now = dt.datetime(2026, 9, 19, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("app.study.utc_now", lambda: fixed_now)
    insert_course_and_assignment(db, "2026-09-20T23:59:00-04:00")
    planning_profile = {
        "version": 5,
        "completed": True,
        "windows": {
            "weekday": {"start": "09:00", "end": "21:30"},
            "weekend": {"start": "08:00", "end": "20:00"},
        },
        "meals": [{
            "id": "lunch", "label": "Lunch", "enabled": True, "schedule": "weekends",
            "start": "12:30", "end": "13:30",
        }],
        "commitments": [{
            "id": "work", "title": "Work shift", "category": "Work", "days": [5],
            "start": "10:00", "end": "12:00",
        }],
        "preferences": {"focusBlock": 60, "buffer": 15, "dailyLimit": 180, "noStudyDay": 6},
    }
    db.execute(
        """UPDATE study_settings SET weekend_start='08:00',weekend_end='20:00',
           max_daily_minutes=180,weekend_max_daily_minutes=180,max_session_minutes=60,
           break_minutes=15,planning_profile=? WHERE id=1""",
        (db.json(planning_profile),),
    )

    result = planner.recompute({"timezone": "America/Toronto"})

    timezone = ZoneInfo("America/Toronto")
    sessions = [
        (parse_datetime(row["start_at"]).astimezone(timezone),
         parse_datetime(row["end_at"]).astimezone(timezone))
        for row in result["sessions"]
    ]
    assert sessions
    assert all(start.weekday() == 5 for start, _ in sessions)
    protected = [
        (dt.time(9, 45), dt.time(12, 15)),
        (dt.time(12, 15), dt.time(13, 45)),
    ]
    for start, end in sessions:
        assert all(end.time() <= blocked_start or start.time() >= blocked_end
                   for blocked_start, blocked_end in protected)


def test_selected_focus_block_is_used_instead_of_evenly_splitting_work():
    assert StudyPlanner._session_lengths(135, 90, 30) == [90, 45]
    assert StudyPlanner._session_lengths(135, 60, 30) == [60, 45, 30]
    assert StudyPlanner._session_lengths(180, 60, 30) == [60, 60, 60]


def test_study_settings_decode_the_persisted_v5_profile(tmp_path):
    db, planner = make_planner(tmp_path)
    profile = {"version": 5, "completed": True, "meals": [], "commitments": []}
    db.execute("UPDATE study_settings SET planning_profile=? WHERE id=1", (db.json(profile),))

    assert planner.settings_row()["planning_profile"] == profile


def test_calendar_input_validation_requires_complete_times():
    value = {
        "needs_clarification": False,
        "question": "",
        "reply": "Added Dentist.",
        "actions": [{
            "operation": "create",
            "target_event_ids": [],
            "delete_scope": "selected",
            "title": "Dentist",
            "start_at": "2026-09-22T15:00:00-04:00",
            "end_at": "2026-09-22T16:00:00-04:00",
            "all_day": False,
            "location": "Clinic",
            "notes": "Cleaning",
            "recurrence": "once",
            "weekdays": [],
            "repeat_until": None,
        }],
    }
    parsed = validate_calendar_input(value)
    assert parsed["actions"][0]["title"] == "Dentist"

    clarification = validate_calendar_input({
        "needs_clarification": True, "question": "What time?", "reply": "", "actions": []
    })
    assert clarification["question"] == "What time?"

    with pytest.raises(OpenRouterError):
        validate_calendar_input({"needs_clarification": False, "question": "", "reply": "", "actions": []})


def test_calendar_input_accepts_only_editable_delete_targets():
    value = {
        "needs_clarification": False,
        "question": "",
        "reply": "Removed it.",
        "actions": [{
            "operation": "delete",
            "target_event_ids": ["manual", "lecture"],
            "delete_scope": "selected",
            "title": "",
            "start_at": None,
            "end_at": None,
            "all_day": False,
            "location": "",
            "notes": "",
            "recurrence": "once",
            "weekdays": [],
            "repeat_until": None,
        }],
    }
    parsed = validate_calendar_input(value, [
        {"id": "manual", "source_type": "manual"},
        {"id": "lecture", "source_type": "course_meeting"},
    ])

    assert parsed["actions"][0]["target_event_ids"] == ["manual"]


def test_weekday_calendar_action_expands_in_local_time():
    action = {
        "operation": "create",
        "title": "Morning walk",
        "start_at": "2026-09-18T08:00:00-04:00",
        "end_at": "2026-09-18T08:30:00-04:00",
        "all_day": False,
        "location": "",
        "notes": "",
        "recurrence": "weekdays",
        "weekdays": [],
        "repeat_until": "2026-09-23",
    }

    expanded = _expand_calendar_action(action, ZoneInfo("America/Toronto"))

    assert [parse_datetime(row["start_at"]).astimezone(ZoneInfo("America/Toronto")).strftime("%a %H:%M")
            for row in expanded] == ["Fri 08:00", "Mon 08:00", "Tue 08:00", "Wed 08:00"]
