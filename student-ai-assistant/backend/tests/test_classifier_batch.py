"""
Batched classification.

The free tier caps both requests (1000/day) and tokens (200k/day), and one
request per item blew through both: 350 items cost ~241k tokens because every
call re-sent the same category rules. Batching fixes that, but introduces a
failure mode single calls never had -- a response that is misaligned with the
items it describes, which would file one student's summary under another's row.
These tests are mostly about that.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.intelligence.classifier import FALLBACK, classify_item, classify_items

ITEMS = [
    {"title": "Assignment 3", "raw_content": "Due Friday 6pm on Classroom"},
    {"title": "Mess menu", "raw_content": "This week's menu is attached"},
    {"title": "Placement talk", "raw_content": "Company visiting Tuesday"},
]


def _entry(i: int, category: str = "academic", priority: str = "HIGH") -> dict:
    return {
        "id": i,
        "category": category,
        "priority": priority,
        "relevance": 0.8,
        "one_line_summary": f"summary {i}",
    }


def _mock_llm(payload) -> AsyncMock:
    client = AsyncMock()
    client.chat = AsyncMock(
        return_value=payload if isinstance(payload, str) else json.dumps(payload)
    )
    return client


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_one_result_per_item_in_order(self):
        payload = {"results": [_entry(1), _entry(2, "mess", "LOW"), _entry(3, "placement", "MEDIUM")]}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            out = await classify_items(ITEMS)

        assert [r["category"] for r in out] == ["academic", "mess", "placement"]
        assert [r["priority"] for r in out] == ["HIGH", "LOW", "MEDIUM"]
        assert [r["summary"] for r in out] == ["summary 1", "summary 2", "summary 3"]

    @pytest.mark.asyncio
    async def test_one_request_regardless_of_batch_size(self):
        payload = {"results": [_entry(i) for i in range(1, 4)]}
        client = _mock_llm(payload)
        with patch("app.intelligence.classifier.llm", return_value=client):
            await classify_items(ITEMS)

        assert client.chat.await_count == 1

    @pytest.mark.asyncio
    async def test_results_are_matched_by_id_not_by_position(self):
        """A model that reorders its output must not shift classifications."""
        payload = {"results": [_entry(3, "placement"), _entry(1, "academic"), _entry(2, "mess")]}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            out = await classify_items(ITEMS)

        assert [r["category"] for r in out] == ["academic", "mess", "placement"]

    @pytest.mark.asyncio
    async def test_empty_input_costs_no_request(self):
        client = _mock_llm({"results": []})
        with patch("app.intelligence.classifier.llm", return_value=client):
            assert await classify_items([]) == []
        client.chat.assert_not_awaited()


class TestUntrustworthyResponses:
    """Each of these must return None so the caller retries per-item."""

    @pytest.mark.asyncio
    async def test_missing_an_id_is_rejected_wholesale(self):
        payload = {"results": [_entry(1), _entry(3)]}      # 2 dropped
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            assert await classify_items(ITEMS) is None

    @pytest.mark.asyncio
    async def test_too_few_results_is_rejected(self):
        payload = {"results": [_entry(1)]}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            assert await classify_items(ITEMS) is None

    @pytest.mark.asyncio
    async def test_no_results_array_is_rejected(self):
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm({"category": "academic"})):
            assert await classify_items(ITEMS) is None

    @pytest.mark.asyncio
    async def test_unparseable_output_is_rejected(self):
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm("not json at all")):
            assert await classify_items(ITEMS) is None

    @pytest.mark.asyncio
    async def test_non_numeric_ids_are_rejected(self):
        payload = {"results": [dict(_entry(1), id="one"), _entry(2), _entry(3)]}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            assert await classify_items(ITEMS) is None

    @pytest.mark.asyncio
    async def test_api_failure_is_rejected_not_raised(self):
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=RuntimeError("groq down"))
        with patch("app.intelligence.classifier.llm", return_value=client):
            assert await classify_items(ITEMS) is None


class TestNormalisation:
    @pytest.mark.asyncio
    async def test_out_of_vocabulary_values_are_coerced_not_persisted(self):
        """An unknown category would violate the items CHECK constraint."""
        payload = {"results": [
            dict(_entry(1), category="nonsense", priority="URGENT", relevance=9.9),
            _entry(2), _entry(3),
        ]}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            out = await classify_items(ITEMS)

        assert out[0]["category"] == "general"
        assert out[0]["priority"] == "LOW"
        assert out[0]["relevance_score"] == 1.0
        assert out[0].keys() == FALLBACK.keys()


class TestDegradedMarker:
    """
    A failed call and a genuine "general" verdict used to produce identical
    rows. A day of 429s therefore marked 93% of a real corpus classified, and
    since nothing rescans processed rows, those placeholders were permanent.
    """

    @pytest.mark.asyncio
    async def test_api_failure_is_marked_degraded(self):
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=RuntimeError("429 tokens per day"))
        with patch("app.intelligence.classifier.llm", return_value=client):
            result = await classify_item("Quiz 1 on Friday", title="Statistics")

        assert result["degraded"] is True
        assert result["category"] == "general"

    @pytest.mark.asyncio
    async def test_unparseable_output_is_marked_degraded(self):
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm("¯\\_(ツ)_/¯")):
            result = await classify_item("Quiz 1 on Friday", title="Statistics")

        assert result["degraded"] is True

    @pytest.mark.asyncio
    async def test_a_real_general_verdict_is_not_degraded(self):
        """The distinction the old code could not make."""
        payload = {"category": "general", "priority": "LOW",
                   "relevance": 0.4, "one_line_summary": "Newsletter"}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            result = await classify_item("Weekly newsletter", title="News")

        assert result["degraded"] is False
        assert result["category"] == "general"
        assert result["priority"] == "LOW"

    @pytest.mark.asyncio
    async def test_empty_input_is_not_degraded(self):
        """Nothing to classify is a real answer; retrying it forever burns quota."""
        client = AsyncMock()
        with patch("app.intelligence.classifier.llm", return_value=client):
            result = await classify_item("   ", title=None)

        assert result["degraded"] is False
        client.chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batched_results_are_not_degraded(self):
        payload = {"results": [_entry(1), _entry(2), _entry(3)]}
        with patch("app.intelligence.classifier.llm", return_value=_mock_llm(payload)):
            out = await classify_items(ITEMS)

        assert all(r["degraded"] is False for r in out)
