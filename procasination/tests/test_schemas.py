from __future__ import annotations

from procasination.schemas import (
    text_verdict_schema,
    validate_nudge,
    validate_text_verdicts,
    validate_vision_verdict,
)


def test_schema_pins_one_verdict_per_segment():
    schema = text_verdict_schema(["a", "b"])
    verdicts = schema["properties"]["verdicts"]
    assert verdicts["minItems"] == verdicts["maxItems"] == 2
    assert verdicts["items"]["properties"]["key"]["enum"] == ["a", "b"]


def test_verdicts_for_unrequested_keys_are_dropped():
    value = {"verdicts": [
        {"key": "a", "verdict": "on_task", "confidence": 80, "category": "coursework",
         "reason": "r", "needs_visual": False},
        {"key": "injected", "verdict": "off_task", "confidence": 99, "category": "gaming",
         "reason": "r", "needs_visual": False},
    ]}
    result = validate_text_verdicts(value, ["a"])
    assert list(result) == ["a"]


def test_a_missing_key_stays_unresolved_rather_than_guessed():
    result = validate_text_verdicts({"verdicts": []}, ["a", "b"])
    assert result == {}


def test_out_of_range_values_are_coerced_not_trusted():
    value = {"verdicts": [{"key": "a", "verdict": "sabotage", "confidence": 500,
                           "category": "nonsense", "reason": "x" * 400, "needs_visual": "yes"}]}
    verdict = validate_text_verdicts(value, ["a"])["a"]
    assert verdict["verdict"] == "ambiguous"
    assert verdict["confidence"] == 100
    assert verdict["category"] == "unknown"
    assert len(verdict["reason"]) == 240
    assert verdict["needs_visual"] is True


def test_negative_confidence_is_clamped_to_zero():
    value = {"verdicts": [{"key": "a", "verdict": "off_task", "confidence": -10,
                           "category": "gaming", "reason": "r", "needs_visual": False}]}
    assert validate_text_verdicts(value, ["a"])["a"]["confidence"] == 0


def test_garbage_response_yields_no_verdicts():
    assert validate_text_verdicts("not json", ["a"]) == {}
    assert validate_text_verdicts({"verdicts": "nope"}, ["a"]) == {}


def test_an_unusable_vision_response_is_ambiguous_at_zero_confidence():
    verdict = validate_vision_verdict(None)
    assert verdict["verdict"] == "ambiguous"
    assert verdict["confidence"] == 0
    assert verdict["decided_by"] == "vision_model"


def test_a_nudge_missing_a_field_is_rejected_so_the_fallback_is_used():
    assert validate_nudge({"title": "t", "body": "b", "next_step": ""}) is None
    assert validate_nudge({"title": "  ", "body": "b", "next_step": "s"}) is None
    assert validate_nudge("nope") is None


def test_a_complete_nudge_is_accepted_and_trimmed():
    nudge = validate_nudge({"title": " t ", "body": "b" * 400, "next_step": "s"})
    assert nudge["title"] == "t"
    assert len(nudge["body"]) == 240
