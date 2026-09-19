from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any, Optional

from bs4 import BeautifulSoup


UTC = dt.timezone.utc


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def html_to_text(value: Optional[str], limit: int = 50_000) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def digest(value: Any) -> str:
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def classify_kind(title: str, description: str = "") -> str:
    text = (title + " " + description[:500]).casefold()
    if re.search(r"\b(test|exam|midterm|final exam|term test|crt)\b", text):
        return "test"
    if re.search(r"\b(essay|writing|paper|reflection|portfolio|draft|peer review)\b", text):
        return "writing"
    if re.search(r"\b(project|presentation|capstone)\b", text):
        return "project"
    if re.search(r"\b(quiz|pcq\d*|reading check)\b", text):
        return "quiz"
    return "assignment"


def reminder_offsets(kind: str) -> list[int]:
    return {
        "test": [14, 7, 3, 1],
        "project": [14, 7, 3, 1],
        "writing": [14, 7, 3, 1],
        "quiz": [3, 1],
        "class_change": [1, 0],
    }.get(kind, [7, 3, 1])
