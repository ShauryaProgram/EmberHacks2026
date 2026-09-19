import datetime as dt
import json

from app.canvas import CanvasSource
from app.config import Settings
from app.db import Database
from app.openrouter import validate_actions
from app.security import SecretBox
from app.sync import CourseBundle, SyncService
from app.utils import iso, utc_now


def source(text="Lab 1 is now due on September 21 at 9:00 AM."):
    return {
        "source_key": "announcement:1:9",
        "course_id": "1",
        "kind": "announcement",
        "title": "Update",
        "content_text": text,
        "canvas_updated_at": "2026-09-08T12:00:00-04:00",
        "url": "https://q.utoronto.ca/a",
    }


def valid_action(evidence="Lab 1 is now due on September 21 at 9:00 AM."):
    return {
        "action_type": "obligation",
        "operation": "create",
        "title": "Lab 1",
        "target_title": "",
        "target_start_at": None,
        "start_at": "2026-09-21T09:00:00-04:00",
        "end_at": None,
        "all_day": False,
        "location": "",
        "category": "assignment",
        "summary": "Submit Lab 1.",
        "evidence": evidence,
        "reminder_offsets_days": [7, 3, 1],
    }


def make_service(tmp_path):
    settings = Settings(
        database_path=tmp_path / "test.db",
        secret_key_path=tmp_path / ".key",
        vapid_key_path=tmp_path / ".vapid.pem",
    )
    db = Database(settings.database_path)
    db.initialize()
    secrets = SecretBox(settings.secret_key_path)
    return db, SyncService(db, settings, secrets)


def insert_profile(db):
    now = iso(utc_now())
    db.execute(
        "INSERT INTO profile VALUES(1,'7','Student','encrypted','America/Toronto','[7,3,1]',?,?)",
        (now, now),
    )


def test_model_result_requires_exact_evidence_and_valid_date():
    accepted = validate_actions({"actions": [valid_action()]}, source())
    assert len(accepted) == 1
    assert accepted[0]["category"] == "assignment"

    invented = valid_action("A made-up quote")
    assert validate_actions({"actions": [invented]}, source()) == []

    malformed = valid_action()
    malformed["start_at"] = "next week"
    assert validate_actions({"actions": [malformed]}, source()) == []


def test_assignment_sync_creates_event_and_reminders(tmp_path):
    db, service = make_service(tmp_path)
    insert_profile(db)
    due = iso(utc_now() + dt.timedelta(days=10))
    course = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {}}
    assignment = {
        "id": 2,
        "name": "Lab 1",
        "description": "<p>Upload your solution.</p>",
        "due_at": due,
        "submission": {},
        "submission_types": ["online_upload"],
        "published": True,
        "html_url": "https://q.utoronto.ca/courses/1/assignments/2",
    }
    bundle = CourseBundle(course, [assignment], [], [], [], [], [], True)
    service._persist_canvas_data([bundle], utc_now())
    service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1"))

    event = db.fetch_one("SELECT * FROM calendar_events WHERE source_key='assignment:1:2'")
    assert event["title"] == "CSC110: Lab 1"
    reminders = db.fetch_all("SELECT * FROM reminders WHERE event_id=?", (event["id"],))
    assert [row["source_key"].split(":")[-1] for row in reminders] == ["d7", "d3", "d1"]


def test_reschedule_reuses_event_and_reminder_ids(tmp_path):
    db, service = make_service(tmp_path)
    insert_profile(db)
    course = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {}}
    row = {"id": 2, "name": "Lab", "description": "", "due_at": iso(utc_now() + dt.timedelta(days=10)),
           "submission": {}, "submission_types": [], "published": True}
    service._persist_canvas_data([CourseBundle(course, [row], [], [], [], [], [], True)], utc_now())
    profile = db.fetch_one("SELECT * FROM profile WHERE id=1")
    service._reconcile_all_reminders(profile)
    event_id = db.fetch_one("SELECT id FROM calendar_events")["id"]
    reminder_ids = {item["id"] for item in db.fetch_all("SELECT id FROM reminders")}

    row["due_at"] = iso(utc_now() + dt.timedelta(days=11))
    service._persist_canvas_data([CourseBundle(course, [row], [], [], [], [], [], True)], utc_now())
    service._reconcile_all_reminders(profile)
    assert db.fetch_one("SELECT id FROM calendar_events")["id"] == event_id
    assert {item["id"] for item in db.fetch_all("SELECT id FROM reminders")} == reminder_ids


def test_missing_assignment_is_cancelled_only_after_complete_course_fetch(tmp_path):
    db, service = make_service(tmp_path)
    course = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {}}
    row = {"id": 2, "name": "Lab", "description": "", "due_at": iso(utc_now() + dt.timedelta(days=10)),
           "submission": {}, "submission_types": [], "published": True}
    service._persist_canvas_data([CourseBundle(course, [row], [], [], [], [], [], True)], utc_now())
    service._persist_canvas_data([CourseBundle(course, [], [], [], [], [], [], False)], utc_now())
    assert db.fetch_one("SELECT active FROM assignments")["active"] == 1
    service._persist_canvas_data([CourseBundle(course, [], [], [], [], [], [], True)], utc_now())
    assert db.fetch_one("SELECT active FROM assignments")["active"] == 0
    assert db.fetch_one("SELECT status FROM calendar_events")["status"] == "cancelled"


def test_changed_source_is_queued_but_unchanged_source_is_not(tmp_path):
    db, service = make_service(tmp_path)
    course = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {}}
    one = CanvasSource("syllabus:1", "1", "syllabus", "Syllabus", "https://q.utoronto.ca", "Exam October 1", None)
    service._persist_canvas_data([CourseBundle(course, [], [], [], [], [one], [], True)], utc_now())
    assert db.fetch_one("SELECT analysis_status FROM source_documents")["analysis_status"] == "pending"
    db.execute("UPDATE source_documents SET analysis_status='analyzed',analyzed_hash=content_hash")
    service._persist_canvas_data([CourseBundle(course, [], [], [], [], [one], [], True)], utc_now())
    assert db.fetch_one("SELECT analysis_status FROM source_documents")["analysis_status"] == "analyzed"
    two = CanvasSource("syllabus:1", "1", "syllabus", "Syllabus", "https://q.utoronto.ca", "Exam October 2", None)
    service._persist_canvas_data([CourseBundle(course, [], [], [], [], [two], [], True)], utc_now())
    assert db.fetch_one("SELECT analysis_status FROM source_documents")["analysis_status"] == "pending"


def test_non_assignment_planner_item_becomes_calendar_event(tmp_path):
    db, service = make_service(tmp_path)
    course = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {}}
    planner = {
        "plannable_id": 44,
        "plannable_type": "discussion_topic",
        "plannable": {"title": "Weekly response", "due_at": iso(utc_now() + dt.timedelta(days=4))},
        "planner_override": None,
        "submissions": False,
        "html_url": "https://q.utoronto.ca/courses/1/discussion_topics/44",
    }
    service._persist_canvas_data([CourseBundle(course, [], [], [], [planner], [], [], True)], utc_now())
    event = db.fetch_one("SELECT * FROM calendar_events")
    assert event["source_type"] == "planner"
    assert event["title"] == "CSC110: Weekly response"


def test_announcement_can_reschedule_exact_existing_event(tmp_path):
    db, service = make_service(tmp_path)
    course = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {}}
    old_due = iso(utc_now() + dt.timedelta(days=5))
    new_due = iso(utc_now() + dt.timedelta(days=12))
    assignment = {"id": 2, "name": "Lab 1", "description": "", "due_at": old_due,
                  "submission": {}, "submission_types": [], "published": True}
    notice = CanvasSource("announcement:1:4", "1", "announcement", "Moved", "https://q.utoronto.ca/a",
                          "Lab 1 has moved to next week.", iso(utc_now()))
    service._persist_canvas_data(
        [CourseBundle(course, [assignment], [], [], [], [notice], [], True)], utc_now()
    )
    stored_source = db.fetch_one("SELECT * FROM source_documents WHERE source_key='announcement:1:4'")
    action = valid_action("Lab 1 has moved to next week.")
    action.update({"operation": "update", "target_title": "Lab 1", "normalized_target": "lab 1",
                   "target_start_at": old_due, "start_at": new_due})
    service._apply_analysis(stored_source, [action])
    event = db.fetch_one("SELECT * FROM calendar_events WHERE source_key='assignment:1:2'")
    assert event["start_at"] == new_due


def test_only_fall_2026_courses_are_included(tmp_path):
    _db, service = make_service(tmp_path)

    assert service._include_course({"term": {"name": "2026 Fall"}})
    assert service._include_course({"term": {"name": "2026-2027 Fall/Winter"}})
    assert service._include_course({"term": {"name": "Unknown", "start_at": "2026-09-08T00:00:00Z"}})
    assert not service._include_course({"term": {"name": "2026 Winter", "start_at": "2026-01-05T00:00:00Z"}})
    assert not service._include_course({"term": {"name": "2025 Fall", "start_at": "2025-09-08T00:00:00Z"}})
    assert not service._include_course({"term": {"name": "Default Term"}})


def test_sync_removes_courses_outside_selected_term(tmp_path):
    db, service = make_service(tmp_path)
    fall = {"id": 1, "course_code": "CSC110", "name": "Computer Science", "term": {"name": "2026 Fall"}}
    winter = {"id": 2, "course_code": "MAT137", "name": "Calculus", "term": {"name": "2026 Winter"}}
    service._persist_canvas_data([
        CourseBundle(fall, [], [], [], [], [], [], True),
        CourseBundle(winter, [], [], [], [], [], [], True),
    ], utc_now())

    service._persist_canvas_data([CourseBundle(fall, [], [], [], [], [], [], True)], utc_now())

    assert [row["id"] for row in db.fetch_all("SELECT id FROM courses")] == ["1"]


def test_section_child_event_becomes_course_meeting_without_reminders(tmp_path):
    db, service = make_service(tmp_path)
    insert_profile(db)
    course = {
        "id": 1,
        "course_code": "CSC110",
        "name": "Computer Science",
        "term": {"name": "2026 Fall"},
        "sections": [{"id": 88, "name": "LEC0101"}],
    }
    event = {
        "id": 900,
        "title": "Lecture",
        "description": "Weekly lecture",
        "start_at": "2026-09-21T14:00:00-04:00",
        "end_at": "2026-09-21T15:00:00-04:00",
        "hidden": True,
        "series_uuid": "series-1",
        "child_events": [
            {
                "id": 901,
                "context_code": "course_section_88",
                "context_name": "CSC110 LEC0101",
                "start_at": "2026-09-21T15:00:00-04:00",
                "end_at": "2026-09-21T16:00:00-04:00",
                "location_name": "BA 1130",
            },
            {
                "id": 902,
                "context_code": "course_section_99",
                "context_name": "CSC110 LEC0201",
                "start_at": "2026-09-21T18:00:00-04:00",
                "end_at": "2026-09-21T19:00:00-04:00",
            },
        ],
    }
    service._persist_canvas_data([CourseBundle(course, [], [], [event], [], [], [], True)], utc_now())
    service._reconcile_all_reminders(db.fetch_one("SELECT * FROM profile WHERE id=1"))

    meeting = db.fetch_one("SELECT * FROM calendar_events")
    assert meeting["source_key"] == "canvas_event:1:901"
    assert meeting["source_type"] == "course_meeting"
    assert meeting["kind"] == "lecture"
    assert meeting["location"] == "BA 1130"
    assert json.loads(meeting["metadata"])["parent_event_id"] == 900
    assert db.fetch_all("SELECT * FROM reminders") == []
