"""
Database layer, against real Postgres.

Concentrates on the behaviours that were actually broken: deadline
deduplication, hybrid search, alert eligibility, and student isolation.
"""

from datetime import timedelta

import pytest

from app.db import queries
from app.utils.date_utils import now_utc

pytestmark = pytest.mark.db


def fake_vector(seed: int, dims: int = 768) -> list[float]:
    """Deterministic unit vector — no model needed for structural tests."""
    import random

    rng = random.Random(seed)
    values = [rng.gauss(0, 1) for _ in range(dims)]
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values]


class TestDeadlineDedup:
    async def test_repeated_sync_does_not_duplicate(self, student):
        """
        The original upsert named no conflict target, so every 2-hourly poll
        inserted another row — duplicating the radar and re-firing every alert.
        """
        student_id = str(student["id"])
        due = now_utc() + timedelta(days=3)

        for _ in range(5):
            await queries.upsert_deadline(
                student_id=student_id, dedup_key="classroom:abc123",
                title="Assignment 3", due_at=due, source="classroom", confirmed=True,
            )

        assert len(await queries.get_upcoming_deadlines(student_id, days=30)) == 1

    async def test_unchanged_due_date_preserves_alert_flags(self, student):
        """Re-syncing must not re-arm reminders already sent."""
        student_id = str(student["id"])
        due = now_utc() + timedelta(hours=20)

        created = await queries.upsert_deadline(
            student_id=student_id, dedup_key="classroom:abc",
            title="Quiz", due_at=due, source="classroom", confirmed=True,
        )
        await queries.mark_alert_sent(str(created["id"]), "alert_sent_24h")

        again = await queries.upsert_deadline(
            student_id=student_id, dedup_key="classroom:abc",
            title="Quiz", due_at=due, source="classroom", confirmed=True,
        )
        assert again["alert_sent_24h"] is True

    async def test_moved_due_date_rearms_alerts(self, student):
        """A rescheduled deadline should alert again — that is new information."""
        student_id = str(student["id"])

        created = await queries.upsert_deadline(
            student_id=student_id, dedup_key="classroom:abc", title="Quiz",
            due_at=now_utc() + timedelta(hours=20), source="classroom", confirmed=True,
        )
        await queries.mark_alert_sent(str(created["id"]), "alert_sent_24h")

        moved = await queries.upsert_deadline(
            student_id=student_id, dedup_key="classroom:abc", title="Quiz",
            due_at=now_utc() + timedelta(days=5), source="classroom", confirmed=True,
        )
        assert moved["alert_sent_24h"] is False

    async def test_student_confirmation_survives_resync(self, student):
        """A human's judgement outranks a machine re-import."""
        student_id = str(student["id"])
        due = now_utc() + timedelta(days=2)

        await queries.upsert_deadline(
            student_id=student_id, dedup_key="web:x", title="Fee payment",
            due_at=due, source="website", confirmed=False, confidence=0.6,
        )
        created = (await queries.get_upcoming_deadlines(student_id, days=30))[0]
        await queries.confirm_deadline(str(created["id"]), student_id, confirmed=True)

        again = await queries.upsert_deadline(
            student_id=student_id, dedup_key="web:x", title="Fee payment",
            due_at=due, source="website", confirmed=False, confidence=0.6,
        )
        assert again["confirmed"] is True


class TestAlertEligibility:
    async def test_unconfirmed_deadlines_never_alert(self, student):
        """
        A low-confidence extraction must not wake anyone. Alerting on a date the
        model guessed is the fastest way to lose a user permanently.
        """
        student_id = str(student["id"])
        async with (await _pool()).acquire() as conn:
            await conn.execute(
                "UPDATE students SET telegram_chat_id = 999 WHERE id = $1", student_id
            )

        await queries.upsert_deadline(
            student_id=student_id, dedup_key="web:guess", title="Maybe due?",
            due_at=now_utc() + timedelta(hours=20), source="website",
            confirmed=False, confidence=0.55,
        )
        assert await queries.get_deadlines_needing_alert("alert_sent_24h") == []

    async def test_confirmed_deadline_in_window_alerts(self, student):
        student_id = str(student["id"])
        async with (await _pool()).acquire() as conn:
            await conn.execute(
                "UPDATE students SET telegram_chat_id = 999 WHERE id = $1", student_id
            )

        await queries.upsert_deadline(
            student_id=student_id, dedup_key="classroom:real", title="Assignment 3",
            due_at=now_utc() + timedelta(hours=20), source="classroom", confirmed=True,
        )
        pending = await queries.get_deadlines_needing_alert("alert_sent_24h")
        assert len(pending) == 1
        assert pending[0]["telegram_chat_id"] == 999

    async def test_past_deadlines_do_not_alert(self, student):
        student_id = str(student["id"])
        await queries.upsert_deadline(
            student_id=student_id, dedup_key="classroom:old", title="Old",
            due_at=now_utc() - timedelta(hours=2), source="classroom", confirmed=True,
        )
        assert await queries.get_deadlines_needing_alert("alert_sent_24h") == []


class TestItemUpsert:
    async def test_unchanged_content_keeps_processed_state(self, student):
        """
        Re-ingesting identical content must not re-queue it for the LLM —
        that would re-classify every item on every poll and burn the quota.
        """
        student_id = str(student["id"])

        item = await queries.upsert_item(
            student_id=student_id, source="gmail", source_id="m1",
            raw_content="Same body", title="Subject",
        )
        await queries.save_item_analysis(
            item_id=str(item["id"]), category="academic", priority="HIGH",
            relevance_score=0.9, summary="s", embedding=fake_vector(1),
        )

        again = await queries.upsert_item(
            student_id=student_id, source="gmail", source_id="m1",
            raw_content="Same body", title="Subject",
        )
        assert again["processed_at"] is not None
        assert await queries.count_unprocessed_items(student_id) == 0

    async def test_changed_content_requeues_for_processing(self, student):
        student_id = str(student["id"])

        item = await queries.upsert_item(
            student_id=student_id, source="gmail", source_id="m1",
            raw_content="Original", title="Subject",
        )
        await queries.save_item_analysis(
            item_id=str(item["id"]), category="academic", priority="LOW",
            relevance_score=0.5, summary="s", embedding=fake_vector(1),
        )

        await queries.upsert_item(
            student_id=student_id, source="gmail", source_id="m1",
            raw_content="Edited — deadline moved to Friday", title="Subject",
        )
        assert await queries.count_unprocessed_items(student_id) == 1


class TestStudentIsolation:
    async def test_get_item_is_scoped_to_owner(self, student, clean_db):
        other = await queries.upsert_student(
            google_id="other", email="other@iitdh.ac.in", name="Other", scopes=[],
        )
        mine = await queries.upsert_item(
            student_id=str(student["id"]), source="gmail", source_id="x",
            raw_content="private", title="My private mail",
        )
        assert await queries.get_item(str(mine["id"]), str(other["id"])) is None
        assert await queries.get_item(str(mine["id"]), str(student["id"])) is not None

    async def test_mark_read_is_scoped_to_owner(self, student):
        other = await queries.upsert_student(
            google_id="other2", email="other2@iitdh.ac.in", name="Other", scopes=[],
        )
        mine = await queries.upsert_item(
            student_id=str(student["id"]), source="gmail", source_id="y",
            raw_content="private", title="Mine",
        )
        assert await queries.mark_item_read(str(mine["id"]), str(other["id"])) is False
        assert await queries.mark_item_read(str(mine["id"]), str(student["id"])) is True


class TestRegenerate:
    """delete_last_assistant_message backs the chat 'regenerate' action."""

    async def test_deletes_only_the_most_recent_assistant_reply(self, student):
        student_id = str(student["id"])
        convo = await queries.create_conversation(student_id, first_message="Q1")
        convo_id = str(convo["id"])

        await queries.add_message(convo_id, "user", "Q1")
        first_answer = await queries.add_message(convo_id, "assistant", "A1")
        await queries.add_message(convo_id, "user", "Q2")
        second_answer = await queries.add_message(convo_id, "assistant", "A2")

        assert await queries.delete_last_assistant_message(convo_id, student_id) is True

        remaining = [m["id"] for m in await queries.get_messages(convo_id, student_id)]
        assert first_answer["id"] in remaining
        assert second_answer["id"] not in remaining

    async def test_is_scoped_to_owner(self, student):
        other = await queries.upsert_student(
            google_id="other3", email="other3@iitdh.ac.in", name="Other", scopes=[],
        )
        convo = await queries.create_conversation(str(student["id"]), first_message="Q")
        convo_id = str(convo["id"])
        await queries.add_message(convo_id, "user", "Q")
        answer = await queries.add_message(convo_id, "assistant", "A")

        assert await queries.delete_last_assistant_message(convo_id, str(other["id"])) is False

        remaining = [m["id"] for m in await queries.get_messages(convo_id, str(student["id"]))]
        assert answer["id"] in remaining

    async def test_empty_conversation_is_a_no_op(self, student):
        student_id = str(student["id"])
        convo = await queries.create_conversation(student_id)
        assert await queries.delete_last_assistant_message(str(convo["id"]), student_id) is False


class TestDigestGuard:
    async def test_second_digest_same_day_is_rejected(self, student):
        """Enforced by a unique index, not by scheduler discipline."""
        student_id = str(student["id"])
        assert await queries.log_alert(student_id, "digest") is True
        assert await queries.log_alert(student_id, "digest") is False
        assert await queries.digest_already_sent_today(student_id) is True

    async def test_deadline_alerts_are_not_deduped_by_day(self, student):
        """The daily guard applies only to digests."""
        student_id = str(student["id"])
        assert await queries.log_alert(student_id, "alert_sent_48h") is True
        assert await queries.log_alert(student_id, "alert_sent_24h") is True


class TestDataRights:
    async def test_soft_delete_destroys_tokens_immediately(self, student):
        student_id = str(student["id"])
        await queries.set_student_tokens(student_id, {"refresh_token": "secret"})
        await queries.soft_delete_student(student_id)

        async with (await _pool()).acquire() as conn:
            blob = await conn.fetchval(
                "SELECT google_tokens_enc FROM students WHERE id = $1", student_id
            )
        assert blob is None
        assert await queries.get_student(student_id) is None

    async def test_export_includes_every_category(self, student):
        student_id = str(student["id"])
        await queries.upsert_item(
            student_id=student_id, source="gmail", source_id="e1",
            raw_content="body", title="Subject",
        )
        export = await queries.export_student_data(student_id)
        assert {"student", "items", "deadlines", "emails", "alerts"} <= export.keys()
        assert len(export["items"]) == 1


async def _pool():
    from app.db.pool import get_pool

    return get_pool()
