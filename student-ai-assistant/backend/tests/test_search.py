"""
Retrieval.

The keyword half of "hybrid" search never worked: it passed the raw question to
to_tsquery(), which raises on spaces and punctuation, inside a try/except that
logged a warning and returned []. Every test here would have failed before.
"""

import pytest

from app.db import queries

pytestmark = pytest.mark.db


def fake_vector(seed: int, dims: int = 768) -> list[float]:
    import random

    rng = random.Random(seed)
    values = [rng.gauss(0, 1) for _ in range(dims)]
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values]


CORPUS = [
    ("classroom", "c1", "MA201 Linear Algebra: Assignment 3",
     "Submit Assignment 3 on eigenvalues and diagonalization via Google Classroom"),
    ("classroom", "c2", "PH103 Physics Quiz 2",
     "Quiz 2 covers thermodynamics and the second law of thermodynamics"),
    ("gmail", "g1", "Qualcomm campus drive registration",
     "Registration for the Qualcomm placement drive closes Friday. CS and EE eligible."),
    ("website", "w1", "Mess menu this week",
     "Monday poha. Tuesday idli sambar. Wednesday aloo paratha."),
]


@pytest.fixture
async def corpus(student):
    student_id = str(student["id"])
    for index, (source, source_id, title, body) in enumerate(CORPUS):
        item = await queries.upsert_item(
            student_id=student_id, source=source, source_id=source_id,
            raw_content=body, title=title,
        )
        await queries.save_item_analysis(
            item_id=str(item["id"]), category="academic", priority="MEDIUM",
            relevance_score=0.8, summary=title, embedding=fake_vector(index),
        )
    return student_id


class TestHybridSearchRobustness:
    @pytest.mark.parametrize(
        "question",
        [
            "what assignments are due this week?",
            "what's due?",
            "mess menu -- monday",
            "a & b | c",
            "exam (quiz) !!",
            "when is the quiz???",
            "kal kya hai",
            "100% marks needed",
            "email from prof@iitdh.ac.in",
            "'quoted phrase' test",
        ],
    )
    async def test_punctuation_never_raises(self, corpus, question):
        """
        to_tsquery() raises on every one of these. websearch_to_tsquery does not.
        """
        results = await queries.hybrid_search(
            query_text=question, query_embedding=fake_vector(0),
            student_ids=[corpus], limit=5,
        )
        assert isinstance(results, list)

    async def test_empty_query_is_safe(self, corpus):
        results = await queries.hybrid_search(
            query_text="", query_embedding=fake_vector(0), student_ids=[corpus], limit=5
        )
        assert isinstance(results, list)


class TestKeywordHalfActuallyRuns:
    async def test_exact_terms_match_via_keyword(self, corpus):
        """An unrelated embedding — any hit must come from the text half."""
        results = await queries.hybrid_search(
            query_text="Qualcomm placement drive",
            query_embedding=fake_vector(999),
            student_ids=[corpus], limit=5,
        )
        matched = [r for r in results if r["keyword_rank"] is not None]
        assert matched, "keyword half contributed nothing"
        assert any("Qualcomm" in r["title"] for r in matched)

    async def test_conversational_question_falls_back_to_or(self, corpus):
        """
        websearch_to_tsquery ANDs terms, so 'assignments due this week' requires
        all of assign+due+week and matches nothing. The OR fallback is what
        keeps the keyword half useful for natural questions.
        """
        results = await queries.hybrid_search(
            query_text="what assignments are due this week?",
            query_embedding=fake_vector(999),
            student_ids=[corpus], limit=5,
        )
        matched = [r for r in results if r["keyword_rank"] is not None]
        assert matched, "OR fallback did not engage"

    async def test_and_precision_preserved_when_it_matches(self, corpus):
        """Multi-term queries that do match should stay precise, not go OR."""
        results = await queries.hybrid_search(
            query_text="Qualcomm registration",
            query_embedding=fake_vector(999),
            student_ids=[corpus], limit=5,
        )
        matched = [r for r in results if r["keyword_rank"] is not None]
        assert len(matched) == 1
        assert "Qualcomm" in matched[0]["title"]


class TestSearchIsolation:
    async def test_never_crosses_students(self, corpus, student):
        other = await queries.upsert_student(
            google_id="other_search", email="other@iitdh.ac.in", name="Other", scopes=[]
        )
        other_id = str(other["id"])
        item = await queries.upsert_item(
            student_id=other_id, source="gmail", source_id="secret",
            raw_content="Confidential salary offer 42 LPA", title="SECRET offer",
        )
        await queries.save_item_analysis(
            item_id=str(item["id"]), category="placement", priority="HIGH",
            relevance_score=1.0, summary="secret", embedding=fake_vector(0),
        )

        results = await queries.hybrid_search(
            query_text="confidential salary offer secret",
            query_embedding=fake_vector(0), student_ids=[corpus], limit=10,
        )
        assert not any("SECRET" in r["title"] for r in results)

    async def test_multi_account_search_is_explicit(self, corpus, student):
        """A Telegram chat may legitimately span two linked Google accounts."""
        other = await queries.upsert_student(
            google_id="linked", email="linked@iitdh.ac.in", name="Linked", scopes=[]
        )
        other_id = str(other["id"])
        item = await queries.upsert_item(
            student_id=other_id, source="gmail", source_id="x",
            raw_content="Hostel allocation notice", title="Hostel allocation",
        )
        await queries.save_item_analysis(
            item_id=str(item["id"]), category="hostel", priority="LOW",
            relevance_score=0.5, summary="Hostel allocation", embedding=fake_vector(7),
        )

        results = await queries.hybrid_search(
            query_text="hostel allocation", query_embedding=fake_vector(7),
            student_ids=[corpus, other_id], limit=10,
        )
        assert any("Hostel" in r["title"] for r in results)


class TestKeywordFallback:
    async def test_keyword_only_path_works(self, corpus):
        """Used when embedding is unavailable — better than answering nothing."""
        results = await queries.search_items_keyword("Qualcomm", [corpus], limit=5)
        assert results and "Qualcomm" in results[0]["title"]


class TestContextFormatting:
    """
    Regression guard: the LLM repeats whatever the context says, so a formatting
    bug here becomes a wrong deadline shown to a student.
    """

    def test_deadline_is_rendered_in_ist_not_utc(self):
        from datetime import datetime

        from app.rag.retriever import format_context_for_llm
        from app.utils.date_utils import UTC

        # 18:29 UTC is 23:59 IST — the "end of day" default the extractor uses.
        item = {
            "id": "x",
            "source": "gmail",
            "title": "Assignment 3",
            "summary": "Assignment 3",
            "deadline": datetime(2026, 8, 24, 18, 29, tzinfo=UTC),
            "created_at": datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
        }
        context = format_context_for_llm([item])

        assert "11:59 PM" in context, f"expected IST 11:59 PM, got:\n{context}"
        assert "06:29 PM" not in context, "UTC time leaked into an IST-labelled field"

    def test_no_deadline_line_when_absent(self):
        from app.rag.retriever import format_context_for_llm

        context = format_context_for_llm([{"id": "x", "source": "website", "title": "Notice"}])
        assert "deadline:" not in context
