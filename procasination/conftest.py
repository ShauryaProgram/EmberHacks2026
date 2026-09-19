"""Put `backend/` on sys.path so `procasination` can import the `app` package.

Lets the suite run from the repository root as well as from `backend/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
