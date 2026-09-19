from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import uuid
from typing import Any, Dict, List, Optional

import asyncio

import pytest

from app.config import Settings
from app.db import Database
from app.utils import iso, utc_now

from procasination.capture import ActivitySample
from procasination.config import ProcrastinationSettings
from procasination.gemini import ProcrastinationModelError
from procasination.monitor import ProcrastinationMonitor
from procasination.segments import Segment


class FakeCapture:
    """Replays a scripted sequence of readings; repeats the last one forever.

    Each reading is stamped with an advancing clock so segments grow exactly as
    they would against a real machine sampling on an interval.
    """

    def __init__(self, readings: List[ActivitySample], screenshot: Optional[str] = "ZmFrZQ==",
                 interval_seconds: int = 60):
        self.readings = readings
        self.index = 0
        self.interval_seconds = interval_seconds
        self.start = utc_now()
        self.screenshot = screenshot
        self.screenshots_taken = 0
        self.title_permission_ok = True

    async def sample(self) -> ActivitySample:
        reading = self.readings[min(self.index, len(self.readings) - 1)]
        captured_at = iso(self.start + dt.timedelta(seconds=self.index * self.interval_seconds))
        self.index += 1
        return dataclasses.replace(reading, captured_at=captured_at)

    async def screenshot_base64(self) -> Optional[str]:
        self.screenshots_taken += 1
        return self.screenshot


class FakeClient:
    def __init__(self, text: Optional[Dict[str, Any]] = None, vision: Optional[Dict[str, Any]] = None,
                 nudge: Optional[Dict[str, str]] = None, raise_on: tuple = ()):
        self.text = text or {}
        self.vision = vision
        self.nudge = nudge or {"title": "Back to it", "body": "b", "next_step": "s"}
        self.raise_on = raise_on
        self.text_calls: List[List[str]] = []
        self.vision_calls = 0
        self.nudge_contexts: List[Dict[str, Any]] = []

    async def classify_segments(self, segments, task):
        if "text" in self.raise_on:
            raise ProcrastinationModelError("boom")
        self.text_calls.append([segment.key for segment in segments])
        return {
            segment.key: dict(self.text, decided_by="text_model")
            for segment in segments
            if self.text
        }

    async def inspect_screenshot(self, image, segment, task):
        if "vision" in self.raise_on:
            raise ProcrastinationModelError("boom")
        self.vision_calls += 1
        return dict(self.vision or {}, decided_by="vision_model")

    async def compose_nudge(self, context):
        self.nudge_contexts.append(context)
        if "nudge" in self.raise_on:
            raise ProcrastinationModelError("boom")
        return self.nudge


class FakeDelivery:
    def __init__(self, push_ok: bool = True, native_ok: bool = True, error: str = "",
                 block: Optional[asyncio.Event] = None):
        self.sent: List[Dict[str, str]] = []
        self.push_ok = push_ok
        self.native_ok = native_ok
        self.last_error = error
        # Stands in for a modal dialog waiting on a human.
        self.block = block
        self.started = asyncio.Event()

    async def send(self, nudge, session):
        self.sent.append(nudge)
        self.started.set()
        if self.block is not None:
            await self.block.wait()
        return self.push_ok, self.native_ok


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


def add_course(db: Database, course_id: str = "443198", code: str = "CSC110Y5_Fall_2026") -> str:
    now = iso(utc_now())
    db.execute(
        """INSERT INTO courses(id,code,name,raw_json,last_seen_at,updated_at)
           VALUES(?,?,?,'{}',?,?)""",
        (course_id, code, code + " :All Sections", now, now),
    )
    return course_id


def add_study_session(db: Database, minutes_in: int = 10, minutes_left: int = 50,
                      target: str = "CSC236 Problem Set 3", course_id: Optional[str] = None) -> str:
    now = utc_now()
    event_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO calendar_events(id,source_key,course_id,source_type,title,description,start_at,end_at,
           all_day,status,kind,metadata,created_at,updated_at)
           VALUES(?,?,?,'study_plan',?,'',?,?,0,'active','study',?,?,?)""",
        (
            event_id,
            "study:" + event_id,
            course_id,
            "Study: CSC236 - " + target,
            iso(now - dt.timedelta(minutes=minutes_in)),
            iso(now + dt.timedelta(minutes=minutes_left)),
            json.dumps({"target_title": target, "target_due_at": iso(now + dt.timedelta(days=1))}),
            iso(now),
            iso(now),
        ),
    )
    return event_id


def reading(app: str, title: str = "", url: str = "", idle: int = 0, sensitive: bool = False) -> ActivitySample:
    return ActivitySample(captured_at=iso(utc_now()), app=app, window_title=title, url=url,
                          idle_seconds=idle, sensitive=sensitive)


def build(db: Database, capture: FakeCapture, client: FakeClient, **overrides) -> ProcrastinationMonitor:
    settings = ProcrastinationSettings(
        demo_mode=overrides.pop("demo_mode", False),
        sample_interval_seconds=overrides.pop("sample_interval_seconds", 60),
        verdict_interval_seconds=0,
        nudge_after_seconds=overrides.pop("nudge_after_seconds", 90),
        **overrides,
    )
    monitor = ProcrastinationMonitor(db, Settings(), settings, client=client, capture=capture)
    monitor.store.initialize()
    monitor.delivery = FakeDelivery()
    return monitor


@pytest.mark.asyncio
async def test_nothing_is_captured_outside_a_study_session(tmp_path):
    capture = FakeCapture([reading("Steam", "Library")])
    monitor = build(make_db(tmp_path), capture, FakeClient())

    await monitor.tick()

    assert monitor.state is None
    assert capture.index == 0, "the screen must not be sampled when no session is scheduled"


@pytest.mark.asyncio
async def test_a_session_in_progress_starts_a_watch_with_the_assignment_as_context(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Preview", "ps3.pdf")]), client)

    await monitor.tick()

    assert monitor.state is not None
    assert monitor.state.task["assignment_title"] == "CSC236 Problem Set 3"
    assert monitor.state.task["due_at"] is not None
    # The assignment travels with every classification request.
    assert client.text_calls


@pytest.mark.asyncio
async def test_a_session_attached_to_a_real_course_names_it(tmp_path):
    """The courses table column is `code`, not `course_code`.

    Reading the wrong name threw inside the tick, which is swallowed and logged,
    so the watcher would have silently done nothing on every real study session
    while working fine on a synthetic one with no course attached.
    """
    db = make_db(tmp_path)
    course_id = add_course(db)
    add_study_session(db, course_id=course_id)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Preview", "ps3.pdf")]), client)

    await monitor.tick()

    assert monitor.state is not None, "a real course must not break the watch"
    assert monitor.state.task["course"] == "CSC110Y5_Fall_2026"


@pytest.mark.asyncio
async def test_the_model_is_told_which_courses_the_student_takes(tmp_path):
    """So a different course's work reads as misdirected work, not slacking."""
    db = make_db(tmp_path)
    add_course(db, "443198", "CSC110Y5_Fall_2026")
    add_course(db, "457590", "MAT137H5_F_2026")
    add_study_session(db)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Notion", "notes")]), client)

    await monitor.tick()

    assert monitor.state.task["enrolled_courses"] == ["CSC110Y5_Fall_2026", "MAT137H5_F_2026"]


@pytest.mark.asyncio
async def test_work_for_another_course_is_off_task_but_not_called_slacking(tmp_path):
    """Doing CSC110 homework in a MAT137 block is misdirected, not procrastination."""
    db = make_db(tmp_path)
    add_study_session(db, target="MAT137 Problem Set 3")
    client = FakeClient(text={"verdict": "off_task", "confidence": 90, "category": "other_coursework",
                              "reason": "This is CSC110 homework, not MAT137.", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("zen", "HW3.1 - CSC110 | PrairieLearn")]), client)

    await monitor.tick()
    await monitor.tick()
    await monitor.wait_for_nudges()

    assert len(monitor.delivery.sent) == 1
    context = client.nudge_contexts[0]
    # The wording model must know it is a subject mismatch, not idle browsing.
    assert context["category"] == "other_coursework"


@pytest.mark.asyncio
async def test_a_locally_resolvable_activity_never_reaches_the_model(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient()
    monitor = build(db, FakeCapture([reading("Visual Studio Code", "ps3.tex")]), client)

    await monitor.tick()

    assert client.text_calls == []
    assert monitor.state.last_verdict["decided_by"] == "prefilter"


@pytest.mark.asyncio
async def test_a_repeat_context_is_served_from_cache_without_a_second_call(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Notion", "PS3 notes")]), client)

    await monitor.tick()
    await monitor.tick()

    assert len(client.text_calls) == 1
    assert monitor.state.last_verdict["decided_by"] == "cache"


@pytest.mark.asyncio
async def test_off_task_time_accumulates_and_nudges_once_past_the_threshold(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "Playing a game", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Google Chrome", "Chess", "https://chess.com")]), client)

    await monitor.tick()
    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == [], "one minute of drift is not yet worth interrupting"

    await monitor.tick()

    await monitor.wait_for_nudges()
    assert len(monitor.delivery.sent) == 1
    assert monitor.state.off_task_streak_seconds == 0, "the clock restarts after a nudge"


@pytest.mark.asyncio
async def test_returning_to_work_clears_the_drift_before_it_can_nudge(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([
        reading("Steam", "Library"),
        reading("Visual Studio Code", "ps3.tex"),
        reading("Visual Studio Code", "ps3.tex"),
    ])
    monitor = build(db, capture, FakeClient())

    for _ in range(3):
        await monitor.tick()

    assert monitor.state.off_task_streak_seconds == 0
    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []


@pytest.mark.asyncio
async def test_alt_tabbing_between_work_and_a_distraction_still_gets_caught(tmp_path):
    """The most common shape of procrastination, and the easiest one to miss.

    A glance at the editor must not wipe the drift clock, or interleaved
    slacking is invisible forever.
    """
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([
        reading("Steam", "Library"),
        reading("Steam", "Library"),
        reading("Visual Studio Code", "ps3.tex"),
        reading("Steam", "Library"),
        reading("Steam", "Library"),
        reading("Steam", "Library"),
    ])
    monitor = build(db, capture, FakeClient())

    for _ in range(6):
        await monitor.tick()

    await monitor.wait_for_nudges()
    assert len(monitor.delivery.sent) == 1


@pytest.mark.asyncio
async def test_sustained_real_work_still_drains_the_drift_clock(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture(
        [reading("Steam", "Library")] + [reading("Visual Studio Code", "ps3.tex")] * 5
    )
    monitor = build(db, capture, FakeClient())

    for _ in range(6):
        await monitor.tick()

    assert monitor.state.off_task_streak_seconds == 0
    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []


@pytest.mark.asyncio
async def test_a_low_confidence_accusation_is_never_nudged_on_alone(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    # Text is unsure and the screenshot pass is unavailable, so nothing confirms it.
    client = FakeClient(
        text={"verdict": "off_task", "confidence": 55, "category": "entertainment",
              "reason": "Maybe a video", "needs_visual": False},
        raise_on=("vision",),
    )
    monitor = build(db, FakeCapture([reading("Google Chrome", "Watch", "https://vimeo.com/1")]), client)

    for _ in range(4):
        await monitor.tick()

    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []
    assert monitor._nudge_blocked(monitor.state) == "low_confidence"


@pytest.mark.asyncio
async def test_an_ambiguous_segment_escalates_to_a_screenshot(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([reading("Google Chrome", "", "https://youtube.com/watch?v=1")])
    client = FakeClient(
        text={"verdict": "ambiguous", "confidence": 20, "category": "unknown",
              "reason": "No title", "needs_visual": True},
        vision={"verdict": "off_task", "confidence": 95, "category": "entertainment",
                "visible_activity": "A music video is playing fullscreen", "reason": "Not coursework"},
    )
    monitor = build(db, capture, client)

    await monitor.tick()

    assert capture.screenshots_taken == 1
    assert monitor.state.last_verdict["decided_by"] == "vision_model"
    assert monitor.state.last_verdict["verdict"] == "off_task"


@pytest.mark.asyncio
async def test_a_screenshot_that_clears_the_student_prevents_the_nudge(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(
        text={"verdict": "off_task", "confidence": 60, "category": "entertainment",
              "reason": "Looks like a video", "needs_visual": True},
        vision={"verdict": "on_task", "confidence": 90, "category": "coursework",
                "visible_activity": "A recorded CSC236 lecture", "reason": "Course material"},
    )
    monitor = build(db, FakeCapture([reading("Google Chrome", "Lecture 7", "https://youtube.com/watch?v=1")]), client)

    for _ in range(4):
        await monitor.tick()

    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []
    assert monitor.state.off_task_streak_seconds == 0


@pytest.mark.asyncio
async def test_a_private_app_is_never_photographed(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([reading("1Password", "", sensitive=True)])
    monitor = build(db, capture, FakeClient())

    for _ in range(3):
        await monitor.tick()

    assert capture.screenshots_taken == 0
    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []


@pytest.mark.asyncio
async def test_being_away_from_the_keyboard_counts_as_drift(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    monitor = build(db, FakeCapture([reading("Finder", "Desktop", idle=900)]), FakeClient())

    await monitor.tick()
    await monitor.tick()

    await monitor.wait_for_nudges()
    assert len(monitor.delivery.sent) == 1


@pytest.mark.asyncio
async def test_a_second_nudge_waits_for_the_cooldown(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)

    for _ in range(8):
        await monitor.tick()

    await monitor.wait_for_nudges()
    assert len(monitor.delivery.sent) == 1
    assert monitor._nudge_blocked(monitor.state) == "cooldown"


@pytest.mark.asyncio
async def test_no_nudge_in_the_last_minutes_of_a_session(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db, minutes_left=1)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)

    for _ in range(3):
        await monitor.tick()

    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []
    assert monitor._nudge_blocked(monitor.state) == "session_ending"


@pytest.mark.asyncio
async def test_an_unreachable_model_produces_no_verdict_and_no_nudge(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(raise_on=("text",))
    monitor = build(db, FakeCapture([reading("Google Chrome", "Something", "https://example.com")]), client)

    for _ in range(3):
        await monitor.tick()

    await monitor.wait_for_nudges()
    assert monitor.delivery.sent == []
    assert monitor.state.last_verdict is None
    assert monitor.state.model_error == "boom"


@pytest.mark.asyncio
async def test_the_nudge_still_arrives_when_only_the_wording_model_fails(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False}, raise_on=("nudge",))
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)

    await monitor.tick()
    await monitor.tick()

    await monitor.wait_for_nudges()
    assert len(monitor.delivery.sent) == 1
    sent = monitor.delivery.sent[0]
    assert "CSC236 Problem Set 3" in sent["title"]
    assert "minute" in sent["body"]


@pytest.mark.asyncio
async def test_the_nudge_context_carries_the_facts_and_nothing_invented(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db, minutes_left=40)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "Browsing a game store", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)

    await monitor.tick()
    await monitor.tick()

    await monitor.wait_for_nudges()
    context = client.nudge_contexts[0]
    assert context["assignment"]["assignment_title"] == "CSC236 Problem Set 3"
    assert context["minutes_remaining_in_session"] in (39, 40)
    assert context["minutes_off_task"] >= 1
    assert context["drifted_to"]["app"] == "Steam"


@pytest.mark.asyncio
async def test_the_screenshot_budget_is_capped_per_session(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([reading("Google Chrome", "", "https://example.com/%d" % index) for index in range(20)])
    client = FakeClient(
        text={"verdict": "ambiguous", "confidence": 10, "category": "unknown",
              "reason": "No title", "needs_visual": True},
        vision={"verdict": "ambiguous", "confidence": 10, "category": "unknown",
                "visible_activity": "unclear", "reason": "unclear"},
    )
    monitor = build(db, capture, client, max_screenshots_per_session=3)

    for _ in range(10):
        await monitor.tick()

    assert capture.screenshots_taken == 3


@pytest.mark.asyncio
async def test_screenshots_can_be_turned_off_entirely(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([reading("Google Chrome", "", "https://example.com")])
    client = FakeClient(text={"verdict": "ambiguous", "confidence": 10, "category": "unknown",
                              "reason": "No title", "needs_visual": True})
    monitor = build(db, capture, client)
    monitor.enable_screenshots = False

    for _ in range(3):
        await monitor.tick()

    assert capture.screenshots_taken == 0


@pytest.mark.asyncio
async def test_ending_the_session_closes_the_record_and_clears_state(tmp_path):
    db = make_db(tmp_path)
    event_id = add_study_session(db)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Preview", "ps3.pdf")]), client)
    await monitor.tick()
    focus_id = monitor.state.focus_session["id"]

    db.execute("UPDATE calendar_events SET status='completed' WHERE id=?", (event_id,))
    await monitor.tick()

    assert monitor.state is None
    assert monitor.store.get_session(focus_id)["ended_at"] is not None


@pytest.mark.asyncio
async def test_a_skipped_session_is_not_watched(tmp_path):
    db = make_db(tmp_path)
    event_id = add_study_session(db)
    db.execute("UPDATE calendar_events SET status='skipped' WHERE id=?", (event_id,))
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), FakeClient())

    await monitor.tick()

    assert monitor.state is None


@pytest.mark.asyncio
async def test_the_watcher_can_be_disabled_at_runtime(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    capture = FakeCapture([reading("Steam", "Library")])
    monitor = build(db, capture, FakeClient())
    monitor.enabled = False

    await monitor.tick()

    assert monitor.state is None
    assert capture.index == 0


@pytest.mark.asyncio
async def test_time_is_split_across_buckets_without_double_counting(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Notion", "PS3 notes")]), client)

    for _ in range(3):
        await monitor.tick()

    session = monitor.store.get_session(monitor.state.focus_session["id"])
    # Three ticks of one continuous segment at a 60s interval, counted once each.
    assert session["on_task_seconds"] == 180
    assert session["off_task_seconds"] == 0
    # And the still-open segment is one row, not three.
    assert len(monitor.store.observations(session["id"])) == 1


@pytest.mark.asyncio
async def test_a_restart_mid_session_resumes_the_same_record(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "on_task", "confidence": 90, "category": "coursework",
                              "reason": "r", "needs_visual": False})
    first = build(db, FakeCapture([reading("Preview", "ps3.pdf")]), client)
    await first.tick()
    focus_id = first.state.focus_session["id"]

    second = build(db, FakeCapture([reading("Preview", "ps3.pdf")]), client)
    await second.tick()

    assert second.state.focus_session["id"] == focus_id


@pytest.mark.asyncio
async def test_an_unreadable_screen_is_skipped_rather_than_recorded(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient()
    monitor = build(db, FakeCapture([reading("unknown", "")]), client)

    await monitor.tick()

    assert client.text_calls == []
    assert monitor.store.observations(monitor.state.focus_session["id"]) == []


@pytest.mark.asyncio
async def test_a_second_watcher_stands_by_instead_of_double_counting(tmp_path):
    """Two servers on one database would double every total and nudge twice.

    Easy to cause by leaving an old server running on another port.
    """
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False})
    first = build(db, FakeCapture([reading("Steam", "Library")]), client)
    await first.tick()

    second_capture = FakeCapture([reading("Steam", "Library")])
    second = build(db, second_capture, client)
    # Hand the lock to a live foreign pid, as a second server would hold it.
    db.execute("UPDATE focus_watcher SET pid=?,heartbeat_at=? WHERE id=1",
               (os.getpid() + 1, iso(utc_now())))

    await second.tick()

    assert second.state is None
    assert second_capture.index == 0, "a standby watcher must not sample the screen"
    assert second.standby_for_pid == os.getpid() + 1
    assert second.status()["standby_for_pid"] == os.getpid() + 1


@pytest.mark.asyncio
async def test_a_dead_watchers_lock_is_taken_over(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    monitor = build(db, FakeCapture([reading("Visual Studio Code", "ps3.tex")]), FakeClient())
    # A lock whose heartbeat has gone quiet must not block the next server forever.
    monitor.store.claim_watch(os.getpid(), 60)
    db.execute("UPDATE focus_watcher SET pid=?,heartbeat_at=? WHERE id=1",
               (os.getpid() + 1, iso(utc_now() - dt.timedelta(hours=1))))

    await monitor.tick()

    assert monitor.state is not None
    assert monitor.standby_for_pid is None


@pytest.mark.asyncio
async def test_status_reports_the_live_verdict_and_hold_reason(tmp_path):
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "A game store", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)
    await monitor.tick()

    status = monitor.status()

    assert status["watching"] is True
    assert status["current"]["app"] == "Steam"
    assert status["current"]["verdict"] == "off_task"
    assert status["nudge_hold_reason"] == "below_threshold"
    assert status["assignment"]["assignment_title"] == "CSC236 Problem Set 3"


@pytest.mark.asyncio
async def test_the_watcher_keeps_sampling_while_a_nudge_is_on_screen(tmp_path):
    """A modal waits on a human for as long as it takes.

    Delivering it inline froze the loop for the duration, so the watcher went
    blind exactly when it most needed to notice the student coming back, and it
    kept re-qualifying for a nudge that was already up.
    """
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False})
    capture = FakeCapture([reading("Steam", "Library")])
    monitor = build(db, capture, client)
    gate = asyncio.Event()
    monitor.delivery = FakeDelivery(block=gate)

    await monitor.tick()
    await monitor.tick()
    await monitor.delivery.started.wait()

    # The dialog is still up and unanswered.
    assert monitor.delivery.block.is_set() is False
    assert monitor.status()["nudge_in_flight"] is True
    assert monitor._nudge_blocked(monitor.state) == "delivering"

    samples_before = capture.index
    await monitor.tick()
    await monitor.tick()
    assert capture.index > samples_before, "sampling must continue while a nudge waits"
    # And it must not queue a second nudge for drift it already acted on.
    assert len(monitor.delivery.sent) == 1

    gate.set()
    await monitor.wait_for_nudges()
    assert monitor._nudge_blocked(monitor.state) == "cooldown"


@pytest.mark.asyncio
async def test_a_nudge_is_recorded_before_it_is_acknowledged(tmp_path):
    """The timeline should not have a hole in it while a dialog waits."""
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)
    gate = asyncio.Event()
    monitor.delivery = FakeDelivery(block=gate)

    await monitor.tick()
    await monitor.tick()
    await monitor.delivery.started.wait()

    nudges = monitor.store.nudges(monitor.state.focus_session["id"])
    assert len(nudges) == 1, "the nudge is recorded before delivery completes"
    assert bool(nudges[0]["delivered_native"]) is False, "not yet acknowledged"

    gate.set()
    await monitor.wait_for_nudges()
    nudges = monitor.store.nudges(monitor.state.focus_session["id"])
    assert bool(nudges[0]["delivered_native"]) is True


@pytest.mark.asyncio
async def test_a_failed_delivery_is_reported_not_hidden(tmp_path):
    """macOS can discard a banner with a success exit code.

    Recording every nudge as delivered would make a silently broken watcher look
    like a working one, which is the worst possible failure for this feature.
    """
    db = make_db(tmp_path)
    add_study_session(db)
    client = FakeClient(text={"verdict": "off_task", "confidence": 95, "category": "gaming",
                              "reason": "r", "needs_visual": False})
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), client)
    monitor.delivery = FakeDelivery(push_ok=False, native_ok=False, error="banner: suppressed")

    await monitor.tick()
    await monitor.tick()

    await monitor.wait_for_nudges()
    nudges = monitor.store.nudges(monitor.state.focus_session["id"])
    assert len(nudges) == 1
    assert bool(nudges[0]["delivered_push"]) is False
    assert bool(nudges[0]["delivered_native"]) is False
    assert monitor.status()["last_delivery_error"] == "banner: suppressed"


@pytest.mark.asyncio
async def test_status_points_at_the_next_session_when_idle(tmp_path):
    db = make_db(tmp_path)
    db.execute(
        """INSERT INTO calendar_events(id,source_key,source_type,title,description,start_at,end_at,
           all_day,status,kind,metadata,created_at,updated_at)
           VALUES('later','study:later','study_plan','Study: later','',?,?,0,'active','study','{}',?,?)""",
        (
            iso(utc_now() + dt.timedelta(hours=2)),
            iso(utc_now() + dt.timedelta(hours=3)),
            iso(utc_now()),
            iso(utc_now()),
        ),
    )
    monitor = build(db, FakeCapture([reading("Steam", "Library")]), FakeClient())

    await monitor.tick()
    status = monitor.status()

    assert status["watching"] is False
    assert status["next_study_session"]["id"] == "later"
