from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings
from app.db import Database
from app.utils import iso, parse_datetime, utc_now

from .capture import ActivityCapture
from .config import ProcrastinationSettings
from .gemini import ProcrastinationClient, ProcrastinationModelError
from .nudge import NudgeDelivery
from .segments import Segment, Segmenter, VerdictCache, prefilter
from .store import FocusStore

logger = logging.getLogger("procasination")


@dataclass
class SessionState:
    """Everything the watcher knows about the study block in progress."""

    focus_session: Dict[str, Any]
    event: Dict[str, Any]
    task: Dict[str, Any]
    segmenter: Segmenter = field(default_factory=Segmenter)
    cache: VerdictCache = field(default_factory=VerdictCache)
    off_task_streak_seconds: int = 0
    nudge_level: int = 0
    screenshots_used: int = 0
    last_nudge_at: Optional[dt.datetime] = None
    last_verdict_at: Optional[dt.datetime] = None
    last_verdict: Optional[Dict[str, Any]] = None
    last_segment: Optional[Segment] = None
    nudge_titles: List[str] = field(default_factory=list)
    model_error: str = ""
    # Set while a nudge is being written and shown. A modal can sit on screen for
    # the better part of a minute, and the loop must not wait on it.
    delivery_task: Optional[asyncio.Task] = None


class ProcrastinationMonitor:
    """Watches the student only while a planned study session is in progress.

    The loop is text-first: it samples the frontmost window every few seconds,
    collapses repeats into segments, resolves what it can with local rules and a
    per-session cache, and asks a cheap Gemini model about the rest once a
    minute. A screenshot is captured only to settle a segment the text could not,
    and the decision to interrupt is made in code, never by a model.
    """

    def __init__(
        self,
        db: Database,
        backend_settings: Settings,
        settings: Optional[ProcrastinationSettings] = None,
        client: Optional[ProcrastinationClient] = None,
        capture: Optional[ActivityCapture] = None,
    ):
        self.db = db
        self.backend_settings = backend_settings
        self.settings = settings or ProcrastinationSettings()
        self.store = FocusStore(db)
        self.capture = capture or ActivityCapture(self.settings)
        self.delivery = NudgeDelivery(db, self.settings)
        self._client = client
        self._owns_client = client is None
        self.state: Optional[SessionState] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

        # Runtime-tunable copies of the settings the API is allowed to change.
        self.enabled = True
        self.nudge_after_seconds = self.settings.nudge_after_seconds
        self.nudge_cooldown_seconds = self.settings.nudge_cooldown_seconds
        self.idle_threshold_seconds = self.settings.idle_threshold_seconds
        self.enable_screenshots = self.settings.enable_screenshots

        self.ticks = 0
        self.model_calls = 0
        self.vision_calls = 0
        # Set when another live process already holds the watch. This instance
        # then serves its API read-only and samples nothing.
        self.standby_for_pid: Optional[int] = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def client(self) -> ProcrastinationClient:
        if self._client is None:
            self._client = ProcrastinationClient(self.settings)
        return self._client

    async def start(self) -> None:
        self.store.initialize()
        self._stop.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="procrastination-monitor")

    async def wait_for_nudges(self, timeout: float = 90.0) -> None:
        """Wait for an in-flight nudge to finish being written and shown.

        Delivery runs detached so the loop keeps sampling while a dialog is up;
        this is how a caller that needs the outcome (a test, or shutdown) joins it.
        """
        state = self.state
        task = state.delivery_task if state is not None else None
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001 - the task logs its own failures
            pass

    async def stop(self) -> None:
        self._stop.set()
        state = self.state
        if state is not None and state.delivery_task is not None:
            state.delivery_task.cancel()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
            self._task = None
        if self.state is not None:
            self.store.close_focus_session(self.state.focus_session["id"])
            self.state = None
        self.store.release_watch(os.getpid())
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad tick must not end the watch
                logger.exception("procrastination tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._sleep_seconds())
            except asyncio.TimeoutError:
                continue

    def _sleep_seconds(self) -> int:
        """Poll slowly when there is nothing to watch."""
        if self.state is None:
            return max(self.settings.session_poll_seconds, self.settings.sample_interval_seconds)
        return self.settings.sample_interval_seconds

    # -- the tick ----------------------------------------------------------

    async def tick(self) -> None:
        async with self._lock:
            self.ticks += 1
            if not self._hold_watch():
                await self._finish_session()
                return
            event = self.store.active_study_session() if self.enabled else None

            if event is None:
                await self._finish_session()
                return
            if self.state is not None and self.state.event["id"] != event["id"]:
                await self._finish_session()
            if self.state is None:
                self._begin_session(event)

            state = self.state
            assert state is not None

            sample = await self.capture.sample()
            if sample.app == "unknown" and not sample.window_title:
                # Nothing readable this tick, usually a permission prompt still
                # pending or a screen lock. Recording it would only add noise.
                return
            state.segmenter.add(sample)

            if self._verdicts_due(state):
                await self._classify(state)

    def _hold_watch(self) -> bool:
        """Claim or renew the single-watcher lock for this database."""
        stale_after = max(3 * self.settings.sample_interval_seconds, 60)
        outcome = self.store.claim_watch(os.getpid(), stale_after)
        if outcome["granted"]:
            if self.standby_for_pid is not None:
                logger.info("took over the watch from pid %s", self.standby_for_pid)
            self.standby_for_pid = None
            return True
        if self.standby_for_pid != outcome["holder_pid"]:
            logger.warning(
                "another watcher (pid %s) is already running against this database; "
                "standing by so time is not double-counted and nudges are not doubled",
                outcome["holder_pid"],
            )
        self.standby_for_pid = outcome["holder_pid"]
        return False

    def _verdicts_due(self, state: SessionState) -> bool:
        if state.last_verdict_at is None:
            return True
        elapsed = (utc_now() - state.last_verdict_at).total_seconds()
        return elapsed >= self.settings.verdict_interval_seconds

    def _begin_session(self, event: Dict[str, Any]) -> None:
        focus_session = self.store.open_focus_session(event)
        metadata = event.get("metadata") or {}
        task = {
            "assignment_title": str(metadata.get("target_title") or event.get("title") or "")[:300],
            "course": self.store.course_code(event.get("course_id")),
            "due_at": metadata.get("target_due_at"),
            "study_session_title": str(event.get("title") or "")[:300],
            "study_session_ends_at": event["end_at"],
            # So the model can recognise the student's own course codes and tell
            # another course's work apart from something merely academic-looking.
            "enrolled_courses": self.store.enrolled_courses(),
        }
        self.state = SessionState(focus_session=focus_session, event=event, task=task)
        logger.info("procrastination watch started for %s", task["assignment_title"] or event["id"])

    async def _finish_session(self) -> None:
        if self.state is None:
            return
        # Settle whatever is still buffered so the timeline is not truncated.
        try:
            await self._classify(self.state)
        except Exception:  # noqa: BLE001 - never block session close
            logger.exception("final classification failed")
        self.store.close_focus_session(self.state.focus_session["id"])
        logger.info("procrastination watch ended for %s", self.state.event["id"])
        # An in-flight delivery is deliberately left running: the dialog may
        # already be on screen, and cancelling would lose its record without
        # taking it off the student's display.
        self.state = None

    # -- classification ----------------------------------------------------

    async def _classify(self, state: SessionState) -> None:
        state.last_verdict_at = utc_now()
        segments = state.segmenter.drain()
        if not segments:
            return

        resolved: List[Tuple[Segment, Dict[str, Any]]] = []
        unresolved: List[Segment] = []
        for segment in segments:
            verdict = prefilter(segment, self._effective_settings()) or state.cache.get(segment.key)
            if verdict is None:
                unresolved.append(segment)
            else:
                resolved.append((segment, dict(verdict)))

        if unresolved:
            try:
                verdicts = await self.client.classify_segments(unresolved, state.task)
                self.model_calls += 1
                state.model_error = ""
            except ProcrastinationModelError as exc:
                # An unreachable model must never produce a verdict, and above all
                # must never produce a nudge. Leave the segments unjudged.
                state.model_error = str(exc)
                logger.warning("text verdict unavailable: %s", exc)
                verdicts = {}
            for segment in unresolved:
                verdict = verdicts.get(segment.key)
                if verdict is None:
                    continue
                state.cache.put(segment.key, verdict)
                resolved.append((segment, dict(verdict)))

        resolved.sort(key=lambda pair: pair[0].started_at)
        for segment, verdict in resolved:
            await self._apply(state, segment, verdict, is_live=segment is state.segmenter.current)

        await self._maybe_nudge(state)

    async def _apply(self, state: SessionState, segment: Segment, verdict: Dict[str, Any], is_live: bool) -> None:
        if is_live:
            escalated = await self._maybe_escalate(state, segment, verdict)
            if escalated is not None:
                verdict = escalated
                state.cache.put(segment.key, verdict)

        duration = segment.duration_seconds(self.settings.sample_interval_seconds)
        delta = max(0, duration - segment.counted_seconds)
        segment.counted_seconds = duration

        bucket = {"on_task": "on_task", "off_task": "off_task"}.get(verdict["verdict"], "unknown")
        self.store.add_time(state.focus_session["id"], bucket, delta)
        self.store.record_observation(state.focus_session["id"], segment, verdict, duration)

        if verdict["verdict"] == "off_task":
            state.off_task_streak_seconds += delta
        elif verdict["verdict"] == "on_task":
            # Decay, not reset. Alt-tabbing between the assignment and a
            # distraction is the most common shape procrastination actually
            # takes, and zeroing the clock on every glance at the editor means
            # that pattern is never caught: fifteen seconds of work would erase
            # a minute of drift. On-task time offsets drift second for second.
            state.off_task_streak_seconds = max(0, state.off_task_streak_seconds - delta)
        # ambiguous leaves the streak untouched: it neither accuses nor absolves.

        if is_live:
            state.last_verdict = verdict
            state.last_segment = segment

    async def _maybe_escalate(
        self, state: SessionState, segment: Segment, verdict: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Take a screenshot only when the text genuinely did not settle it."""
        if not self.enable_screenshots or segment.sensitive:
            return None
        if verdict.get("decided_by") == "vision_model":
            return None
        if state.screenshots_used >= self.settings.max_screenshots_per_session:
            return None

        confidence = int(verdict.get("confidence") or 0)
        ambiguous = verdict["verdict"] == "ambiguous" or bool(verdict.get("needs_visual"))
        # An off_task call the text model is unsure about is never acted on
        # directly; it either gets confirmed by a screenshot or it gets dropped.
        weak_accusation = (
            verdict["verdict"] == "off_task"
            and confidence < self.settings.vision_confidence_threshold
            and verdict.get("decided_by") != "prefilter"
        )
        about_to_nudge = state.off_task_streak_seconds + segment.duration_seconds(
            self.settings.sample_interval_seconds
        ) >= self.nudge_after_seconds
        if not (ambiguous or weak_accusation):
            return None
        if not ambiguous and not about_to_nudge:
            return None

        image = await self.capture.screenshot_base64()
        if not image:
            return None
        state.screenshots_used += 1
        self.store.count_screenshot(state.focus_session["id"])
        try:
            confirmed = await self.client.inspect_screenshot(image, segment, state.task)
            self.vision_calls += 1
        except ProcrastinationModelError as exc:
            state.model_error = str(exc)
            logger.warning("vision verdict unavailable: %s", exc)
            return None
        finally:
            del image
        return confirmed

    # -- nudging -----------------------------------------------------------

    def _nudge_blocked(self, state: SessionState) -> Optional[str]:
        """Why we are not interrupting right now, or None if we should."""
        task = state.delivery_task
        if task is not None and not task.done():
            return "delivering"
        verdict = state.last_verdict
        if verdict is None:
            return "no_verdict_yet"
        if verdict["verdict"] == "ambiguous":
            return "unresolved"
        if verdict["verdict"] != "off_task":
            return "on_task"
        if state.off_task_streak_seconds < self.nudge_after_seconds:
            return "below_threshold"
        # Only a confident source may interrupt: a local rule, a screenshot, or a
        # text verdict that cleared the confidence bar.
        decided_by = verdict.get("decided_by")
        confident = decided_by in {"prefilter", "vision_model"} or int(
            verdict.get("confidence") or 0
        ) >= self.settings.vision_confidence_threshold
        if not confident:
            return "low_confidence"
        if state.last_nudge_at is not None:
            since = (utc_now() - state.last_nudge_at).total_seconds()
            if since < self.nudge_cooldown_seconds:
                return "cooldown"
        if self._minutes_remaining(state) < 2:
            return "session_ending"
        return None

    async def _maybe_nudge(self, state: SessionState) -> None:
        if self._nudge_blocked(state) is not None:
            return
        segment = state.last_segment
        verdict = state.last_verdict
        assert segment is not None and verdict is not None

        state.nudge_level += 1
        drift = state.off_task_streak_seconds
        # Commit the decision before doing any of the slow work. Composing the
        # text is a network call and a modal waits on a human, so if this were
        # left until afterwards the loop would keep re-qualifying for the same
        # nudge while the first one was still on screen.
        state.last_nudge_at = utc_now()
        # Restart the clock so one continuous drift produces spaced nudges rather
        # than one per verdict cycle.
        state.off_task_streak_seconds = 0

        context = {
            "assignment": state.task,
            "minutes_remaining_in_session": self._minutes_remaining(state),
            "minutes_off_task": max(1, drift // 60),
            "drifted_to": {
                "app": segment.app,
                "window_title": segment.window_title[:300],
                "site": segment.host,
            },
            # The wording prompt branches on this: other_coursework is real work
            # aimed at the wrong subject and must not be called procrastination.
            "category": verdict.get("category") or "unknown",
            "observation": verdict.get("visible_activity") or verdict.get("reason") or "",
            "nudge_number_this_session": state.nudge_level,
            "previous_nudges": state.nudge_titles[-3:],
        }
        # Hand the rest to a background task so sampling continues while the
        # notification is on screen. Without this the watcher goes blind for as
        # long as the dialog waits, and would miss the student going back to work.
        state.delivery_task = asyncio.create_task(
            self._compose_and_deliver(state, segment, context, drift, state.nudge_level),
            name="procrastination-nudge",
        )

    async def _compose_and_deliver(
        self,
        state: SessionState,
        segment: Segment,
        context: Dict[str, Any],
        drift: int,
        level: int,
    ) -> None:
        try:
            nudge = await self.client.compose_nudge(context)
        except ProcrastinationModelError as exc:
            state.model_error = str(exc)
            logger.warning("nudge text unavailable: %s", exc)
            nudge = None
        except asyncio.CancelledError:
            raise
        if nudge is None:
            nudge = self._fallback_nudge_from(context, segment, drift)

        # Recorded before delivery: a modal can wait on a click for most of a
        # minute, and the timeline should not have a hole in it until then.
        row = self.store.record_nudge(
            state.focus_session["id"],
            level,
            nudge,
            segment.app,
            segment.window_title,
            drift,
        )
        state.nudge_titles.append(nudge["title"])
        try:
            push_ok, native_ok = await self.delivery.send(nudge, state.focus_session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed notification must not end the watch
            logger.exception("nudge delivery failed")
            return
        self.store.mark_nudge_delivered(row["id"], push_ok, native_ok)
        if not (push_ok or native_ok):
            logger.warning("nudge reached nobody: %s", getattr(self.delivery, "last_error", ""))

    def _fallback_nudge_from(
        self, context: Dict[str, Any], segment: Segment, drift: int
    ) -> Dict[str, str]:
        """Used when the model is unreachable. Facts only, no invention."""
        assignment = (context.get("assignment") or {}).get("assignment_title") or "your study session"
        minutes = max(1, drift // 60)
        remaining = int(context.get("minutes_remaining_in_session") or 0)
        return {
            "title": "Back to %s" % assignment[:60],
            "body": "You have been on %s for %d minute%s. %d minute%s left in this study block."
            % (segment.app or "something else", minutes, "" if minutes == 1 else "s", remaining, "" if remaining == 1 else "s"),
            "next_step": "Reopen %s and pick the smallest next piece." % assignment[:80],
        }

    # -- helpers -----------------------------------------------------------

    def _effective_settings(self) -> ProcrastinationSettings:
        """Settings with the runtime-tunable idle threshold applied."""
        if self.idle_threshold_seconds == self.settings.idle_threshold_seconds:
            return self.settings
        return ProcrastinationSettings(
            text_model=self.settings.text_model,
            vision_model=self.settings.vision_model,
            sample_interval_seconds=self.settings.sample_interval_seconds,
            verdict_interval_seconds=self.settings.verdict_interval_seconds,
            session_poll_seconds=self.settings.session_poll_seconds,
            idle_threshold_seconds=self.idle_threshold_seconds,
            nudge_after_seconds=self.nudge_after_seconds,
            nudge_cooldown_seconds=self.nudge_cooldown_seconds,
            vision_confidence_threshold=self.settings.vision_confidence_threshold,
            max_screenshots_per_session=self.settings.max_screenshots_per_session,
            enable_screenshots=self.enable_screenshots,
            screenshot_max_pixels=self.settings.screenshot_max_pixels,
            capture_timeout_seconds=self.settings.capture_timeout_seconds,
            sensitive_apps=list(self.settings.sensitive_apps),
            enable_web_push=self.settings.enable_web_push,
            enable_native_notifications=self.settings.enable_native_notifications,
            nudge_style=self.settings.nudge_style,
        )

    def _live_drift_seconds(self, state: SessionState) -> int:
        """Drift including time since the last verdict cycle.

        `off_task_streak_seconds` only advances when segments are classified, so
        between cycles it looks frozen even though drift is still accumulating.
        This is the honest number to show a person; the committed streak remains
        what the nudge decision uses.
        """
        drift = state.off_task_streak_seconds
        if state.last_verdict is None or state.last_verdict["verdict"] != "off_task":
            return drift
        if state.last_verdict_at is None:
            return drift
        return drift + max(0, int((utc_now() - state.last_verdict_at).total_seconds()))

    def _minutes_remaining(self, state: SessionState) -> int:
        end = parse_datetime(state.event["end_at"])
        if end is None:
            return 0
        return max(0, int((end - utc_now()).total_seconds() // 60))

    def status(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "enabled": self.enabled,
            "running": self._task is not None and not self._task.done(),
            "watching": self.state is not None,
            "text_model": self.settings.text_model,
            "vision_model": self.settings.vision_model,
            "openrouter_configured": bool(self.settings.openrouter_api_key),
            "screenshots_enabled": self.enable_screenshots,
            "pid": os.getpid(),
            "standby_for_pid": self.standby_for_pid,
            "nudge_style": self.settings.nudge_style,
            # Why the last nudge did not land. A macOS banner can be discarded
            # with a success exit code, so this is the only honest signal.
            "last_delivery_error": self.delivery.last_error,
            "window_titles_readable": self.capture.title_permission_ok,
            "ticks": self.ticks,
            "text_model_calls": self.model_calls,
            "vision_model_calls": self.vision_calls,
            "now": iso(utc_now()),
        }
        if self.state is None:
            upcoming = self.store.next_study_session()
            base["next_study_session"] = upcoming
            return base
        state = self.state
        session = self.store.get_session(state.focus_session["id"]) or state.focus_session
        base.update(
            {
                "focus_session_id": state.focus_session["id"],
                "event_id": state.event["id"],
                "assignment": state.task,
                "minutes_remaining": self._minutes_remaining(state),
                "off_task_streak_seconds": state.off_task_streak_seconds,
                "off_task_streak_live_seconds": self._live_drift_seconds(state),
                "seconds_until_nudge": max(
                    0, self.nudge_after_seconds - self._live_drift_seconds(state)
                ),
                "next_verdict_in_seconds": max(
                    0,
                    self.settings.verdict_interval_seconds
                    - int((utc_now() - state.last_verdict_at).total_seconds()),
                )
                if state.last_verdict_at is not None
                else 0,
                "nudges_sent": state.nudge_level,
                "nudge_in_flight": state.delivery_task is not None and not state.delivery_task.done(),
                "screenshots_used": state.screenshots_used,
                "cached_verdicts": len(state.cache),
                "nudge_hold_reason": self._nudge_blocked(state),
                "model_error": state.model_error,
                "current": (
                    {
                        "app": state.last_segment.app,
                        "window_title": state.last_segment.window_title,
                        "site": state.last_segment.host,
                        "verdict": state.last_verdict["verdict"],
                        "confidence": state.last_verdict.get("confidence"),
                        "category": state.last_verdict.get("category"),
                        "reason": state.last_verdict.get("reason"),
                        "decided_by": state.last_verdict.get("decided_by"),
                    }
                    if state.last_segment is not None and state.last_verdict is not None
                    else None
                ),
                "totals": {
                    "on_task_seconds": session["on_task_seconds"],
                    "off_task_seconds": session["off_task_seconds"],
                    "unknown_seconds": session["unknown_seconds"],
                },
            }
        )
        return base
