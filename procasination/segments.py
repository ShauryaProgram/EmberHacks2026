from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from app.utils import normalized_title, parse_datetime

from .capture import ActivitySample
from .config import ProcrastinationSettings


# Deliberately tight lists. A rule here spends no tokens but can also be wrong
# with total confidence, so anything genuinely dual-purpose (YouTube, Notion,
# Discord, Reddit, Spotify, Preview) is left for the model, which gets to read
# the window title and knows which assignment is due.
ON_TASK_APPS = {
    "xcode", "visual studio code", "code", "cursor", "zed", "sublime text", "neovim", "vim", "emacs",
    "terminal", "iterm2", "warp", "ghostty", "alacritty", "kitty",
    "pycharm", "pycharm ce", "intellij idea", "clion", "webstorm", "rstudio", "rstudio ide",
    "matlab", "jupyter notebook", "texshop", "texstudio", "zotero", "mendeley desktop",
    "anki", "obsidian", "logisim", "wolfram mathematica", "spyder", "dbeaver", "postman",
}
OFF_TASK_APPS = {
    "steam", "epic games launcher", "battle.net", "riot client", "league of legends",
    "minecraft", "roblox", "valorant", "dota 2", "counter-strike 2", "ea app",
    "netflix", "twitch", "tiktok", "disney+", "hulu", "prime video", "plex", "vlc",
    "playstation remote play", "xbox", "nintendo switch online",
}
OFF_TASK_HOSTS = {
    "tiktok.com", "netflix.com", "twitch.tv", "instagram.com", "facebook.com", "hulu.com",
    "disneyplus.com", "primevideo.com", "9gag.com", "pinterest.com", "roblox.com",
    "crazygames.com", "poki.com", "chess.com", "lichess.org", "coolmathgames.com",
    "onlyfans.com", "espn.com", "shein.com", "temu.com",
}
ON_TASK_HOSTS = {
    "overleaf.com", "wolframalpha.com", "desmos.com", "scholar.google.com", "jstor.org",
    "arxiv.org", "libgen.is", "sciencedirect.com", "springer.com", "ieee.org", "acm.org",
    "pubmed.ncbi.nlm.nih.gov", "symbolab.com", "geogebra.org", "colab.research.google.com",
    "leetcode.com", "gradescope.com", "piazza.com", "markus.teach.cs.toronto.edu",
}


# Unread-count prefixes that browsers and chat apps put in the window title:
# "(248) YouTube", "(3) Discord", "(12) Inbox". The count ticks on its own while
# the page sits untouched, and without stripping it every tick looks like a brand
# new context, thrashing the verdict cache and paying for a fresh model call.
_UNREAD_PREFIX = re.compile(r"^\s*[(\[]\d+[)\]]\s*")


def stable_title(value: str) -> str:
    """The window title with self-changing decoration removed."""
    return _UNREAD_PREFIX.sub("", value or "").strip()


def _host(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _registrable(host: str) -> str:
    """Trim a host to its last two labels so subdomains share a rule."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host


@dataclass
class Segment:
    """A run of consecutive samples showing the same thing on screen."""

    app: str
    window_title: str
    url: str
    started_at: str
    ended_at: str
    sample_count: int = 1
    max_idle_seconds: int = 0
    sensitive: bool = False
    is_browser: bool = False
    # Seconds of this segment already added to the session totals. The live
    # segment is re-reported every cycle as it grows, so only the delta counts.
    counted_seconds: int = 0

    @property
    def host(self) -> str:
        return _host(self.url)

    @property
    def key(self) -> str:
        """Cache identity: the same app, title, and site resolve to one verdict."""
        return "%s|%s|%s" % (
            self.app.casefold(),
            normalized_title(stable_title(self.window_title))[:160],
            self.host,
        )

    def duration_seconds(self, sample_interval: int) -> int:
        """Wall-clock seconds this segment accounts for.

        Each sample stands for the interval that follows it, so the span between
        the first and last sample is one interval short of the time observed. A
        lone sample therefore counts as exactly one interval.
        """
        start = parse_datetime(self.started_at)
        end = parse_datetime(self.ended_at)
        span = int((end - start).total_seconds()) if start and end else 0
        return max(0, span) + max(0, sample_interval)

    def matches(self, sample: ActivitySample) -> bool:
        return (
            self.app.casefold() == sample.app.casefold()
            and normalized_title(stable_title(self.window_title))
            == normalized_title(stable_title(sample.window_title))
            and self.host == sample.host
        )

    def absorb(self, sample: ActivitySample) -> None:
        self.ended_at = sample.captured_at
        self.sample_count += 1
        self.max_idle_seconds = max(self.max_idle_seconds, sample.idle_seconds)
        self.sensitive = self.sensitive or sample.sensitive

    def describe(self, sample_interval: int = 1) -> Dict[str, object]:
        """The only shape ever sent to a model.

        No pixels, and no URL beyond the host: the model is told which site is
        open, never the path or query, which is where the private part of a URL
        usually lives.
        """
        return {
            "key": self.key,
            "app": self.app,
            "window_title": stable_title(self.window_title)[:300],
            "site": self.host,
            "duration_seconds": self.duration_seconds(sample_interval),
            "idle_seconds": self.max_idle_seconds,
        }

    @classmethod
    def from_sample(cls, sample: ActivitySample) -> "Segment":
        return cls(
            app=sample.app,
            window_title=sample.window_title,
            url=sample.url,
            started_at=sample.captured_at,
            ended_at=sample.captured_at,
            max_idle_seconds=sample.idle_seconds,
            sensitive=sample.sensitive,
            is_browser=sample.is_browser,
        )


class Segmenter:
    """Collapses samples into segments so repeated identical context is free."""

    def __init__(self) -> None:
        self.current: Optional[Segment] = None
        self.closed: List[Segment] = []

    def add(self, sample: ActivitySample) -> None:
        if self.current is not None and self.current.matches(sample):
            self.current.absorb(sample)
            return
        if self.current is not None:
            self.closed.append(self.current)
        self.current = Segment.from_sample(sample)

    def drain(self) -> List[Segment]:
        """Return every segment worth classifying, keeping the live one open.

        The in-progress segment is included because drift has to be caught while
        it is happening, not after the student switches away from it.
        """
        pending = list(self.closed)
        self.closed.clear()
        if self.current is not None:
            pending.append(self.current)
        return pending

    def reset(self) -> None:
        self.current = None
        self.closed.clear()


def prefilter(segment: Segment, settings: ProcrastinationSettings) -> Optional[Dict[str, object]]:
    """Resolve the obvious cases locally. Returns None when the model is needed."""
    if segment.max_idle_seconds >= settings.idle_threshold_seconds:
        minutes = segment.max_idle_seconds // 60
        return {
            "verdict": "off_task",
            "confidence": 95,
            "category": "system",
            "reason": "No keyboard or mouse input for %d minute%s." % (minutes, "" if minutes == 1 else "s"),
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    if segment.sensitive:
        # A private app is recorded but never judged and never photographed.
        return {
            "verdict": "ambiguous",
            "confidence": 0,
            "category": "unknown",
            "reason": "%s is on the private-app list, so its contents are not inspected." % segment.app,
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    app = segment.app.casefold()
    if app in ON_TASK_APPS:
        return {
            "verdict": "on_task",
            "confidence": 90,
            "category": "coursework",
            "reason": "%s is a study or development tool." % segment.app,
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    if app in OFF_TASK_APPS:
        return {
            "verdict": "off_task",
            "confidence": 95,
            "category": "gaming" if app not in {"netflix", "twitch", "vlc", "plex"} else "entertainment",
            "reason": "%s is not a study tool." % segment.app,
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    if segment.is_browser and not segment.host and not stable_title(segment.window_title):
        # A browser window with no title and no readable URL carries no text to
        # judge, so a text model call would be spent to be told exactly that.
        # Send it straight to the screenshot path instead.
        return {
            "verdict": "ambiguous",
            "confidence": 0,
            "category": "unknown",
            "reason": "A browser window with no readable title or address.",
            "needs_visual": True,
            "decided_by": "prefilter",
        }
    host = _registrable(segment.host)
    title = normalized_title(stable_title(segment.window_title))
    if settings.demo_mode and (host == "youtube.com" or "youtube" in title):
        return {
            "verdict": "off_task",
            "confidence": 100,
            "category": "entertainment",
            "reason": "Demo mode treats YouTube as off-task.",
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    if host and host in ON_TASK_HOSTS:
        return {
            "verdict": "on_task",
            "confidence": 85,
            "category": "coursework",
            "reason": "%s is a coursework site." % host,
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    if host and host in OFF_TASK_HOSTS:
        return {
            "verdict": "off_task",
            "confidence": 90,
            "category": "entertainment",
            "reason": "%s is an entertainment site." % host,
            "needs_visual": False,
            "decided_by": "prefilter",
        }
    return None


class VerdictCache:
    """Per-session verdict memo.

    Scoped to one study session on purpose: "on task" only means anything
    relative to the assignment that session was created for, so the cache is
    dropped whenever the session changes.
    """

    def __init__(self, confidence_floor: int = 70):
        self.confidence_floor = confidence_floor
        self._entries: Dict[str, Dict[str, object]] = {}

    def get(self, key: str) -> Optional[Dict[str, object]]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        cached = dict(entry)
        cached["decided_by"] = "cache"
        return cached

    def put(self, key: str, verdict: Dict[str, object]) -> None:
        decided_by = verdict.get("decided_by")
        confidence = int(verdict.get("confidence") or 0)
        # Only durable answers are memoized. An ambiguous text verdict must stay
        # unresolved so the next sighting can escalate to a screenshot.
        if decided_by == "vision_model" and verdict.get("verdict") != "ambiguous":
            self._entries[key] = dict(verdict)
            return
        if verdict.get("verdict") == "ambiguous":
            return
        if confidence >= self.confidence_floor:
            self._entries[key] = dict(verdict)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
