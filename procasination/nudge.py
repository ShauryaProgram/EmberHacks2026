from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from app.db import Database

from .capture import _run_status
from .config import ProcrastinationSettings


# Passing the strings as argv instead of interpolating them into the script is
# what keeps a window title full of quotes or backslashes from breaking the
# notification, or worse, being compiled as AppleScript.
BANNER_SCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv)",
    "end run",
)

# A modal dialog cannot be silently suppressed the way a banner can, and it is
# brought to the front deliberately: a nudge you never see is worthless. It gives
# up on its own so a student who has walked away does not come back to a stuck
# dialog, and so this never blocks the monitor loop.
ALERT_SCRIPT = (
    "on run argv",
    'tell application "System Events"',
    "activate",
    "display dialog (item 2 of argv) & return & return & (item 3 of argv) "
    'with title (item 1 of argv) buttons {"Dismiss", "Back to work"} '
    "default button 2 giving up after 45",
    "end tell",
    "end run",
)

VALID_STYLES = ("banner", "alert", "speak")


class NudgeDelivery:
    """Sends a nudge over Web Push and through macOS.

    Both, because the situation being detected is precisely the one where the
    calendar tab is not the thing on screen. Web Push gives the frontend its
    history; the macOS side is what the student actually sees mid-game.

    Delivery is reported honestly. A banner posted through osascript is
    attributed to Script Editor, and macOS discards it **with a success exit
    code** when that app's notifications are switched off, so a zero exit is not
    proof a human saw anything. `last_error` records why nothing landed, and the
    `alert` style exists for when a banner cannot be trusted to appear.
    """

    def __init__(self, db: Database, settings: ProcrastinationSettings):
        self.db = db
        self.settings = settings
        self.last_error: str = ""

    def styles(self) -> List[str]:
        chosen = [item.strip().casefold() for item in self.settings.nudge_style.split("+")]
        valid = [item for item in chosen if item in VALID_STYLES]
        return valid or ["banner"]

    async def send(self, nudge: Dict[str, str], session: Dict[str, Any]) -> Tuple[bool, bool]:
        push_ok, native_ok = await asyncio.gather(
            self._send_push(nudge, session),
            self._send_native(nudge),
        )
        return push_ok, native_ok

    async def _send_push(self, nudge: Dict[str, str], session: Dict[str, Any]) -> bool:
        if not self.settings.enable_web_push:
            return False
        push_service = self._push_service()
        if push_service is None:
            return False
        subscriptions = self.db.fetch_all("SELECT * FROM push_subscriptions")
        if not subscriptions:
            # No browser has subscribed yet, so there is nowhere to push. Say so
            # rather than letting it read as a delivery failure.
            self.last_error = self.last_error or "no Web Push subscription registered"
            return False
        payload = json.dumps(
            {
                "title": nudge["title"],
                "body": "%s\n%s" % (nudge["body"], nudge.get("next_step", "")),
                # A stable tag per session replaces the previous banner instead of
                # stacking a pile of nudges on the lock screen.
                "tag": "focus-nudge-%s" % session["id"],
                "data": {
                    "eventId": session.get("event_id"),
                    "focusSessionId": session["id"],
                    "url": "/calendar",
                    "kind": "procrastination_nudge",
                },
            }
        )
        results = await asyncio.gather(
            *(asyncio.to_thread(push_service._send, subscription, payload) for subscription in subscriptions),
            return_exceptions=True,
        )
        return any(result is True for result in results)

    async def _send_native(self, nudge: Dict[str, str]) -> bool:
        if not self.settings.enable_native_notifications:
            return False
        timeout = 60
        delivered = False
        errors: List[str] = []
        for style in self.styles():
            ok, detail = await self._deliver(style, nudge, timeout)
            if ok:
                delivered = True
            elif detail:
                errors.append("%s: %s" % (style, detail))
        self.last_error = "; ".join(errors)
        return delivered

    async def _deliver(self, style: str, nudge: Dict[str, str], timeout: int) -> Tuple[bool, str]:
        title = nudge["title"]
        body = nudge["body"]
        next_step = nudge.get("next_step", "")
        if style == "banner":
            argv = ["osascript"]
            for line in BANNER_SCRIPT:
                argv.extend(["-e", line])
            argv.extend([title, body, next_step[:120]])
            ok, detail = await _run_status(argv, timeout)
            # A zero exit here means osascript ran, not that a banner appeared.
            return ok, detail
        if style == "alert":
            argv = ["osascript"]
            for line in ALERT_SCRIPT:
                argv.extend(["-e", line])
            argv.extend([title, body, next_step])
            ok, detail = await _run_status(argv, timeout)
            if not ok and "User canceled" in detail:
                # Dismissing the dialog is a delivery, not a failure.
                return True, ""
            return ok, detail
        if style == "speak":
            ok, detail = await _run_status(["say", "-r", "190", "%s. %s" % (title, next_step)], timeout)
            return ok, detail
        return False, "unknown style %r" % style

    @staticmethod
    def _push_service() -> Optional[Any]:
        """Read the backend's PushService lazily.

        It is created inside the backend's lifespan, so it does not exist at
        import time and must be looked up through the module each call.
        """
        try:
            from app import main as backend_main
        except ImportError:
            return None
        return getattr(backend_main, "push_service", None)
