"""
Backlog draining in process_student_items.

A first sync ingests a few hundred items at once. One pass used to stop at
`limit` and wait for the next scheduled run, which on a two-hour Classroom
interval left most of a student's data unclassified for days.
"""

import pytest

from app.intelligence import pipeline

STUDENT = {"id": "11111111-1111-1111-1111-111111111111"}


def _batches_of(*sizes: int, record: list) -> callable:
    """Fake _process_batch returning each size in turn, logging the limits asked for."""
    remaining = list(sizes)

    async def fake(student: dict, limit: int) -> int:
        record.append(limit)
        return remaining.pop(0) if remaining else 0

    return fake


class TestDraining:
    @pytest.mark.asyncio
    async def test_keeps_going_while_batches_come_back_full(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(pipeline, "_process_batch", _batches_of(50, 50, 17, record=calls))

        assert await pipeline.process_student_items(STUDENT, limit=50) == 117
        assert calls == [50, 50, 50]

    @pytest.mark.asyncio
    async def test_stops_on_the_first_short_batch(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(pipeline, "_process_batch", _batches_of(50, 3, 50, record=calls))

        # The 3 ends it: whatever did not process is still unprocessed, so
        # asking again would just refetch the same failing rows.
        assert await pipeline.process_student_items(STUDENT, limit=50) == 53
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_nothing_to_do_is_a_single_batch(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(pipeline, "_process_batch", _batches_of(0, record=calls))

        assert await pipeline.process_student_items(STUDENT) == 0
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_permanently_full_backlog_is_capped_not_infinite(self, monkeypatch):
        """Without the cap, a source refilling as fast as it drains never returns."""
        calls: list[int] = []

        async def always_full(student: dict, limit: int) -> int:
            calls.append(limit)
            return limit

        monkeypatch.setattr(pipeline, "_process_batch", always_full)

        total = await pipeline.process_student_items(STUDENT, limit=50)
        assert len(calls) == pipeline.MAX_DRAIN_BATCHES
        assert total == 50 * pipeline.MAX_DRAIN_BATCHES
