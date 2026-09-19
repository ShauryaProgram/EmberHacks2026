from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


VERDICTS = ("on_task", "off_task", "ambiguous")
CATEGORIES = (
    "coursework",
    # Real work, wrong subject. Off task during a block scheduled for one
    # assignment, but nothing like watching videos, and worded differently.
    "other_coursework",
    "research",
    "writing",
    "communication",
    "entertainment",
    "social",
    "gaming",
    "shopping",
    "system",
    "unknown",
)


def text_verdict_schema(keys: List[str]) -> Dict[str, Any]:
    """Strict schema for the batched text pass: exactly one verdict per segment."""
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "minItems": len(keys),
                "maxItems": len(keys),
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": keys},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "confidence": {"type": "integer"},
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "reason": {"type": "string", "maxLength": 240},
                        "needs_visual": {"type": "boolean"},
                    },
                    "required": ["key", "verdict", "confidence", "category", "reason", "needs_visual"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    }


VISION_VERDICT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "integer"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "visible_activity": {"type": "string", "maxLength": 240},
        "reason": {"type": "string", "maxLength": 240},
    },
    "required": ["verdict", "confidence", "category", "visible_activity", "reason"],
    "additionalProperties": False,
}


NUDGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "body": {"type": "string", "maxLength": 240},
        "next_step": {"type": "string", "maxLength": 240},
    },
    "required": ["title", "body", "next_step"],
    "additionalProperties": False,
}


def _clamp_confidence(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 50
    return max(0, min(100, number))


def _enum_or(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def validate_text_verdicts(value: Any, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """Coerce the model response into one trusted verdict per requested key.

    A key the model omitted is left out rather than guessed; the caller treats a
    missing key as unresolved and does not act on it.
    """
    if not isinstance(value, dict) or not isinstance(value.get("verdicts"), list):
        return {}
    allowed = set(keys)
    result: Dict[str, Dict[str, Any]] = {}
    for row in value["verdicts"]:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        if key not in allowed or key in result:
            continue
        result[key] = {
            "verdict": _enum_or(row.get("verdict"), VERDICTS, "ambiguous"),
            "confidence": _clamp_confidence(row.get("confidence")),
            "category": _enum_or(row.get("category"), CATEGORIES, "unknown"),
            "reason": str(row.get("reason") or "")[:240],
            "needs_visual": bool(row.get("needs_visual")),
            "decided_by": "text_model",
        }
    return result


def validate_vision_verdict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "verdict": "ambiguous",
            "confidence": 0,
            "category": "unknown",
            "visible_activity": "",
            "reason": "The vision pass returned no usable verdict.",
            "decided_by": "vision_model",
        }
    return {
        "verdict": _enum_or(value.get("verdict"), VERDICTS, "ambiguous"),
        "confidence": _clamp_confidence(value.get("confidence")),
        "category": _enum_or(value.get("category"), CATEGORIES, "unknown"),
        "visible_activity": str(value.get("visible_activity") or "")[:240],
        "reason": str(value.get("reason") or "")[:240],
        "decided_by": "vision_model",
    }


def validate_nudge(value: Any) -> Optional[Dict[str, str]]:
    """Return a nudge only if the model produced real text for every field."""
    if not isinstance(value, dict):
        return None
    title = str(value.get("title") or "").strip()[:80]
    body = str(value.get("body") or "").strip()[:240]
    next_step = str(value.get("next_step") or "").strip()[:240]
    if not title or not body or not next_step:
        return None
    return {"title": title, "body": body, "next_step": next_step}


class ProcrastinationSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    nudge_after_seconds: Optional[int] = Field(default=None, ge=15, le=1800)
    nudge_cooldown_seconds: Optional[int] = Field(default=None, ge=60, le=7200)
    idle_threshold_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    enable_screenshots: Optional[bool] = None
    enable_web_push: Optional[bool] = None
    enable_native_notifications: Optional[bool] = None
    nudge_style: Optional[str] = Field(default=None, pattern=r"^(banner|alert|speak)(\+(banner|alert|speak))*$")
