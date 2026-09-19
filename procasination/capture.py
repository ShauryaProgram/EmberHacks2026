from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import urlsplit

from app.utils import iso, utc_now

from .config import ProcrastinationSettings


# Browsers whose active-tab URL is worth asking for, and the AppleScript term
# each one uses. Every `tell application "X"` is compiled by osascript at run
# time, so a browser that is not installed must never appear in a script we
# send. That is why the URL lookup is a second, conditional call rather than
# one big if-chain.
BROWSER_TAB_TERMS = {
    "Safari": "URL of current tab of front window",
    "Safari Technology Preview": "URL of current tab of front window",
    "Google Chrome": "URL of active tab of front window",
    "Google Chrome Canary": "URL of active tab of front window",
    "Brave Browser": "URL of active tab of front window",
    "Microsoft Edge": "URL of active tab of front window",
    "Arc": "URL of active tab of front window",
    "Chromium": "URL of active tab of front window",
    "Vivaldi": "URL of active tab of front window",
    "Dia": "URL of active tab of front window",
}

# Firefox and its forks expose no scriptable tab URL on macOS, so asking costs a
# subprocess and an Automation prompt to learn nothing. They are recognised here
# purely so the watcher knows a browser is in front and skips the lookup. Their
# window title still carries the page title, which is the signal that matters:
# "Epsilon-delta proofs - YouTube" is separable from "Minecraft 100 Days - YouTube"
# with no URL at all, and a title too generic to judge escalates to a screenshot.
TITLE_ONLY_BROWSERS = {
    "Firefox",
    "Firefox Developer Edition",
    "Firefox Nightly",
    "zen",
    "Zen",
    "Zen Browser",
    "LibreWolf",
    "Waterfox",
    "Tor Browser",
    "Orion",
}

FRONTMOST_SCRIPT = """
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set winTitle to ""
    try
        set winTitle to name of front window of frontApp
    end try
end tell
return appName & "\\n" & winTitle
"""

_IDLE_PATTERN = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


@dataclass(frozen=True)
class ActivitySample:
    """One point-in-time observation. Text only; no pixels."""

    captured_at: str
    app: str
    window_title: str
    url: str
    idle_seconds: int
    sensitive: bool

    @property
    def is_browser(self) -> bool:
        return self.app in BROWSER_TAB_TERMS or self.app in TITLE_ONLY_BROWSERS

    @property
    def host(self) -> str:
        if not self.url:
            return ""
        try:
            return (urlsplit(self.url).hostname or "").lower()
        except ValueError:
            return ""


class CaptureError(RuntimeError):
    pass


async def _run_status(argv: Sequence[str], timeout: int) -> tuple[bool, str]:
    """Run a helper and report whether it actually succeeded, plus its stdout."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except (OSError, ValueError) as exc:
        return False, str(exc)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return False, "timed out after %ss" % timeout
    if process.returncode != 0:
        return False, stderr.decode("utf-8", errors="replace").strip()
    return True, stdout.decode("utf-8", errors="replace").strip()


async def _run(argv: Sequence[str], timeout: int) -> str:
    """Run a helper and return stdout, or "" on any failure.

    Capture must never take the monitor loop down: a denied permission, a
    missing binary, or a hung AppleScript all degrade to an empty reading.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except (OSError, ValueError):
        return ""
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return ""
    if process.returncode != 0:
        return ""
    return stdout.decode("utf-8", errors="replace").strip()


class ActivityCapture:
    """Samples the frontmost window on macOS using only built-in helpers.

    Requires Accessibility permission for window titles and Automation
    permission per browser for tab URLs. Both degrade to empty strings, so the
    watcher keeps working (app name and idle time alone) if either is refused.
    """

    def __init__(self, settings: ProcrastinationSettings):
        self.settings = settings
        self._sensitive = settings.sensitive_app_set()
        self.title_permission_ok: Optional[bool] = None

    async def sample(self) -> ActivitySample:
        timeout = self.settings.capture_timeout_seconds
        frontmost, idle_seconds = await asyncio.gather(
            _run(["osascript", "-e", FRONTMOST_SCRIPT], timeout),
            self._idle_seconds(timeout),
        )
        lines = frontmost.split("\n") if frontmost else []
        app = lines[0].strip() if lines else ""
        title = lines[1].strip() if len(lines) > 1 else ""
        if app:
            # A title is expected for most apps; its absence across every sample
            # is the signature of a missing Accessibility grant.
            self.title_permission_ok = bool(title) or self.title_permission_ok is True

        sensitive = app.casefold() in self._sensitive
        url = ""
        if not sensitive and app in BROWSER_TAB_TERMS:
            url = await self._browser_url(app, timeout)

        return ActivitySample(
            captured_at=iso(utc_now()),
            app=app or "unknown",
            window_title="" if sensitive else title,
            url=url,
            idle_seconds=idle_seconds,
            sensitive=sensitive,
        )

    async def _browser_url(self, app: str, timeout: int) -> str:
        term = BROWSER_TAB_TERMS[app]
        script = 'tell application "%s" to return %s' % (app, term)
        value = await _run(["osascript", "-e", script], timeout)
        return value if value.startswith(("http://", "https://")) else ""

    async def _idle_seconds(self, timeout: int) -> int:
        """Seconds since the last keyboard or mouse event. Needs no permission."""
        output = await _run(["ioreg", "-c", "IOHIDSystem", "-d", "4", "-r"], timeout)
        match = _IDLE_PATTERN.search(output)
        if not match:
            return 0
        return int(int(match.group(1)) / 1_000_000_000)

    async def screenshot_base64(self) -> Optional[str]:
        """Capture the main display, downscale, and return JPEG base64.

        Nothing is written outside a private temp file, and the file is removed
        before this returns whether or not the capture succeeded.
        """
        if not self.settings.enable_screenshots:
            return None
        handle, path = tempfile.mkstemp(prefix="procrastination-", suffix=".jpg")
        os.close(handle)
        try:
            timeout = max(self.settings.capture_timeout_seconds, 10)
            # -x silences the shutter, -m limits to the main display so a
            # multi-monitor setup cannot fan out into several files.
            await _run(["screencapture", "-x", "-m", "-t", "jpg", path], timeout)
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return None
            await _run(["sips", "-Z", str(self.settings.screenshot_max_pixels), path], timeout)
            with open(path, "rb") as handle_in:
                payload = handle_in.read()
            if not payload:
                return None
            return base64.b64encode(payload).decode("ascii")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
