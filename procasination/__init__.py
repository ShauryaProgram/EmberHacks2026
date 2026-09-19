"""Procrastination watching for scheduled study sessions.

The monitor activates only while a `study_plan` calendar event is in progress.
It samples what the student is doing as text (frontmost app, window title,
browser URL, idle time), classifies each activity segment against the
assignment that session was created for, escalates to a screenshot only when
text is not conclusive, and nudges with a concrete way back on task.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This package reads the backend's database, settings, and PushService, so
# `backend/` has to be importable as `app`. Doing it here means the package
# works whether it is launched from the repository root or from `backend/`,
# without a packaging change or an edit inside `backend/`.
_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if _BACKEND_ROOT.is_dir() and str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from .config import ProcrastinationSettings, procrastination_settings  # noqa: E402

__all__ = ["ProcrastinationSettings", "procrastination_settings"]
