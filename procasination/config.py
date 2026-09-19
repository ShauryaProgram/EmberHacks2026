from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from app.config import settings as backend_settings


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Apps whose window titles and pixels never leave the machine. The app name is
# still recorded so the timeline is honest about the gap, but the title is
# blanked and a screenshot is never taken while one of these is frontmost.
DEFAULT_SENSITIVE_APPS = (
    "1Password,1Password 7,1Password 8,Keychain Access,Bitwarden,LastPass,Dashlane,"
    "Messages,WhatsApp,Signal,Telegram,Mail,Authy,Banking,GPG Keychain"
)


@dataclass(frozen=True)
class ProcrastinationSettings:
    """Tunables for the watcher. All optional; every default is demo-ready."""

    # Demo mode intentionally treats an otherwise ambiguous generic YouTube
    # page as off-task so a short product demo can exercise the nudge path.
    demo_mode: bool = os.getenv("PROCRASTINATION_DEMO_MODE", "0") == "1"

    # Models. Both must be Gemini on OpenRouter.
    text_model: str = os.getenv("PROCRASTINATION_TEXT_MODEL", "google/gemini-3.1-flash-lite")
    vision_model: str = os.getenv("PROCRASTINATION_VISION_MODEL", "google/gemini-3.8-flash")

    # Cadence.
    sample_interval_seconds: int = _int("PROCRASTINATION_SAMPLE_INTERVAL_SECONDS", 15)
    # Kept short because the local rules and the per-session cache absorb most
    # cycles for free, so a tighter loop mostly costs nothing and halves how long
    # drift takes to register.
    verdict_interval_seconds: int = _int("PROCRASTINATION_VERDICT_INTERVAL_SECONDS", 30)
    session_poll_seconds: int = _int("PROCRASTINATION_SESSION_POLL_SECONDS", 30)

    # Drift thresholds.
    # Deliberately generous: a student reading a proof, watching a lecture, or
    # working on paper produces no keyboard or mouse input for minutes at a time,
    # so a short threshold accuses people of slacking while they are studying.
    # Ten minutes of nothing means away from the desk.
    idle_threshold_seconds: int = _int("PROCRASTINATION_IDLE_THRESHOLD_SECONDS", 600)
    nudge_after_seconds: int = _int("PROCRASTINATION_NUDGE_AFTER_SECONDS", 90)
    nudge_cooldown_seconds: int = _int("PROCRASTINATION_NUDGE_COOLDOWN_SECONDS", 600)
    # Below this confidence a text off_task verdict is never nudged on alone.
    vision_confidence_threshold: int = _int("PROCRASTINATION_VISION_CONFIDENCE_THRESHOLD", 75)
    max_screenshots_per_session: int = _int("PROCRASTINATION_MAX_SCREENSHOTS_PER_SESSION", 12)

    # Capture.
    enable_screenshots: bool = os.getenv("PROCRASTINATION_ENABLE_SCREENSHOTS", "1") != "0"
    screenshot_max_pixels: int = _int("PROCRASTINATION_SCREENSHOT_MAX_PIXELS", 1024)
    capture_timeout_seconds: int = _int("PROCRASTINATION_CAPTURE_TIMEOUT_SECONDS", 5)
    sensitive_apps: List[str] = field(
        default_factory=lambda: _list("PROCRASTINATION_SENSITIVE_APPS", DEFAULT_SENSITIVE_APPS)
    )

    # Delivery.
    enable_web_push: bool = os.getenv("PROCRASTINATION_ENABLE_WEB_PUSH", "1") != "0"
    enable_native_notifications: bool = os.getenv("PROCRASTINATION_ENABLE_NATIVE", "1") != "0"
    # "banner", "alert", "speak", or a combination like "alert+speak".
    #
    # Defaults to "alert" (a modal that auto-dismisses) rather than the politer
    # banner, because a banner posted through osascript is attributed to Script
    # Editor and macOS discards it **with a success exit code** whenever that
    # app's notifications are switched off — which is the default state on a
    # fresh machine. Measured here: "banner" reported success and was never
    # visible. A nudge that can vanish silently makes a broken watcher look like
    # a working one, so the reliable channel is the right default and "banner" is
    # opt-in for anyone who has granted Script Editor notification permission.
    nudge_style: str = os.getenv("PROCRASTINATION_NUDGE_STYLE", "alert")

    @property
    def openrouter_api_key(self) -> str:
        return backend_settings.openrouter_api_key

    @property
    def openrouter_base_url(self) -> str:
        return backend_settings.openrouter_base_url

    @property
    def app_url(self) -> str:
        return backend_settings.app_url

    def sensitive_app_set(self) -> set[str]:
        return {name.casefold() for name in self.sensitive_apps}


procrastination_settings = ProcrastinationSettings()
