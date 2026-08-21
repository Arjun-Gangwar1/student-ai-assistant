"""
Deadline extraction and classification, with the LLM mocked.

The model's output is untrusted input. These tests cover what happens when it
returns something wrong — which is the common case worth engineering for, not
the exceptional one.
"""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.intelligence.classifier import classify_item
from app.intelligence.extractor import CONFIDENCE_THRESHOLD, extract_deadlines
from app.intelligence.llm_client import parse_json_response
from app.utils.date_utils import now_ist, now_utc


def mock_llm(payload):
    """
    Patch the LLM to return a fixed payload.

    Patched where it is *used*, not where it is defined: extractor.py and
    classifier.py both do `from app.intelligence.llm_client import llm`, so each
    holds its own reference. Patching only llm_client.llm leaves those bound to
    the real client — every test then hits the live API and the suite hangs.
    """
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    client = AsyncMock()
    client.chat = AsyncMock(return_value=raw)
    return _patch_all(lambda: client)


class _patch_all:
    """Patch `llm` in every module that imported it."""

    TARGETS = ("app.intelligence.extractor.llm", "app.intelligence.classifier.llm")

    def __init__(self, factory):
        self._factory = factory
        self._patches = []

    def __enter__(self):
        client = self._factory()
        for target in self.TARGETS:
            patcher = patch(target, return_value=client)
            patcher.start()
            self._patches.append(patcher)
        return client

    def __exit__(self, *exc):
        for patcher in self._patches:
            patcher.stop()
        return False


def iso_in(days: float) -> str:
    return (now_ist() + timedelta(days=days)).isoformat()


class TestJsonParsing:
    def test_plain_json(self):
        assert parse_json_response('{"a": 1}') == {"a": 1}

    def test_code_fenced(self):
        assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_preamble_before_json(self):
        assert parse_json_response('Sure! Here you go:\n{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("garbage", ["", "not json", "[1,2,3]", "{broken"])
    def test_garbage_yields_empty_dict(self, garbage):
        """Never raise — an unparseable response must degrade, not crash ingest."""
        assert parse_json_response(garbage) == {}


class TestDeadlineValidation:
    """Non-Negotiable Rule #1: a wrong deadline is a sev-1 bug."""

    async def test_high_confidence_is_auto_confirmed(self):
        with mock_llm({"deadlines": [
            {"title": "Assignment 3", "due_at": iso_in(3), "confidence": 0.95}
        ]}):
            result = await extract_deadlines("Assignment 3 due in three days")
        assert len(result) == 1
        assert result[0]["confirmed"] is True

    @pytest.mark.parametrize("confidence", [0.79, 0.5, 0.1])
    async def test_low_confidence_is_never_auto_confirmed(self, confidence):
        """Below the bar it is a suggestion needing a human, not a fact."""
        with mock_llm({"deadlines": [
            {"title": "Maybe due", "due_at": iso_in(3), "confidence": confidence}
        ]}):
            result = await extract_deadlines("something vague about next week")
        assert result[0]["confirmed"] is False

    async def test_threshold_boundary_is_inclusive(self):
        with mock_llm({"deadlines": [
            {"title": "Edge", "due_at": iso_in(3), "confidence": CONFIDENCE_THRESHOLD}
        ]}):
            result = await extract_deadlines("text long enough to be processed")
        assert result[0]["confirmed"] is True

    async def test_far_future_dates_are_discarded(self):
        """A 'deadline' years out is a misparsed year, not a due date."""
        with mock_llm({"deadlines": [
            {"title": "Bogus", "due_at": iso_in(800), "confidence": 1.0}
        ]}):
            assert await extract_deadlines("some text about the year 2035") == []

    async def test_long_past_dates_are_discarded(self):
        with mock_llm({"deadlines": [
            {"title": "Old", "due_at": iso_in(-30), "confidence": 1.0}
        ]}):
            assert await extract_deadlines("a notice mentioning last month") == []

    async def test_recent_past_is_kept(self):
        """A notice processed hours after its deadline is still worth showing."""
        with mock_llm({"deadlines": [
            {"title": "Just missed", "due_at": iso_in(-0.5), "confidence": 0.9}
        ]}):
            assert len(await extract_deadlines("deadline was earlier today")) == 1

    @pytest.mark.parametrize("bad_date", ["not a date", "", None, "2026-13-45", "null"])
    async def test_unparseable_dates_are_discarded(self, bad_date):
        with mock_llm({"deadlines": [
            {"title": "Broken", "due_at": bad_date, "confidence": 1.0}
        ]}):
            assert await extract_deadlines("some text long enough to process") == []

    async def test_missing_title_is_discarded(self):
        with mock_llm({"deadlines": [{"due_at": iso_in(2), "confidence": 1.0}]}):
            assert await extract_deadlines("some text long enough to process") == []

    async def test_malformed_response_shapes_are_survivable(self):
        for payload in ({"deadlines": "not a list"}, {}, {"deadlines": [None, "x", 42]}):
            with mock_llm(payload):
                assert await extract_deadlines("some text long enough here") == []

    async def test_llm_failure_returns_empty_not_raise(self):
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=RuntimeError("provider down"))
        with _patch_all(lambda: client):
            assert await extract_deadlines("Assignment due Friday, submit soon") == []

    async def test_short_text_skips_the_llm_entirely(self):
        client = AsyncMock()
        with _patch_all(lambda: client):
            assert await extract_deadlines("hi") == []
        client.chat.assert_not_called()

    async def test_confidence_is_clamped(self):
        with mock_llm({"deadlines": [
            {"title": "Weird", "due_at": iso_in(2), "confidence": 47}
        ]}):
            result = await extract_deadlines("some text long enough to process")
        assert 0.0 <= result[0]["confidence"] <= 1.0


class TestClassification:
    async def test_valid_response_passes_through(self):
        with mock_llm({"category": "academic", "priority": "HIGH",
                       "relevance": 0.9, "one_line_summary": "Assignment 3 due Friday"}):
            result = await classify_item("Assignment 3 due Friday")
        assert result["category"] == "academic"
        assert result["priority"] == "HIGH"
        assert result["relevance_score"] == 0.9

    async def test_unknown_category_falls_back_to_general(self):
        """
        An out-of-vocabulary category would violate the CHECK constraint and
        fail the write for the entire item.
        """
        with mock_llm({"category": "sports_and_recreation", "priority": "HIGH",
                       "relevance": 0.5, "one_line_summary": "x"}):
            assert (await classify_item("text"))["category"] == "general"

    async def test_unknown_priority_falls_back_to_low(self):
        with mock_llm({"category": "academic", "priority": "URGENT!!",
                       "relevance": 0.5, "one_line_summary": "x"}):
            assert (await classify_item("text"))["priority"] == "LOW"

    @pytest.mark.parametrize("value,expected", [(1.7, 1.0), (-3, 0.0), ("high", 0.5), (None, 0.5)])
    async def test_relevance_is_clamped_and_coerced(self, value, expected):
        with mock_llm({"category": "academic", "priority": "LOW",
                       "relevance": value, "one_line_summary": "x"}):
            assert (await classify_item("text"))["relevance_score"] == expected

    async def test_llm_failure_yields_safe_defaults(self):
        """
        A failed call must not produce a HIGH-priority item — an unclassified
        item jumping the digest queue is worse than it being missed.
        """
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=RuntimeError("down"))
        with _patch_all(lambda: client):
            result = await classify_item("some text")
        assert result["category"] == "general"
        assert result["priority"] == "LOW"

    async def test_empty_input_skips_the_llm(self):
        client = AsyncMock()
        with _patch_all(lambda: client):
            await classify_item("   ")
        client.chat.assert_not_called()
