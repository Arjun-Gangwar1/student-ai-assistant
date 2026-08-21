"""
Date handling.

Deadline correctness is Non-Negotiable Rule #1, and every bug this file guards
against was a real one in the original code.
"""

from datetime import datetime, timedelta

import pytest

from app.utils.date_utils import (
    IST,
    UTC,
    days_until,
    ensure_aware,
    format_deadline_for_telegram,
    hours_until,
    parse_classroom_date,
    parse_iso,
    priority_from_deadline,
    to_ist,
)


class TestClassroomDates:
    def test_due_time_is_utc_not_ist(self):
        """
        Google documents Classroom dueDate/dueTime as UTC. The original code
        localised them to IST, which shifted every deadline 5h30m early — and
        for a 23:59 due time, onto the previous calendar day.
        """
        result = parse_classroom_date(
            {"year": 2026, "month": 2, "day": 18}, {"hours": 18, "minutes": 30}
        )
        assert result == datetime(2026, 2, 18, 18, 30, tzinfo=UTC)
        # 18:30 UTC is midnight IST on the 19th.
        assert to_ist(result).hour == 0
        assert to_ist(result).day == 19

    def test_date_without_time_is_end_of_day_ist(self):
        """No time means 'end of that day'; erring late beats erring early."""
        result = parse_classroom_date({"year": 2026, "month": 2, "day": 18})
        as_ist = to_ist(result)
        assert (as_ist.day, as_ist.hour, as_ist.minute) == (18, 23, 59)

    def test_incomplete_date_raises(self):
        with pytest.raises(ValueError):
            parse_classroom_date({"year": 2026, "month": 2})


class TestDaysUntil:
    def test_counts_calendar_days_not_24h_blocks(self):
        """
        Something due at 09:00 tomorrow is 'tomorrow' to a student even when it
        is 14 hours away. timedelta.days reported 0 — "due today" — which is a
        materially misleading thing to show above a deadline.
        """
        from app.utils.date_utils import now_ist

        tomorrow_morning = (now_ist() + timedelta(days=1)).replace(hour=9, minute=0)
        assert days_until(tomorrow_morning) == 1

    def test_today_is_zero(self):
        from app.utils.date_utils import now_ist

        assert days_until(now_ist() + timedelta(minutes=30)) == 0

    def test_past_is_negative(self):
        from app.utils.date_utils import now_utc

        assert days_until(now_utc() - timedelta(days=3)) == -3


class TestHoursUntil:
    def test_future_positive_past_negative(self):
        from app.utils.date_utils import now_utc

        now = now_utc()
        assert hours_until(now + timedelta(hours=5)) == 5
        # Overdue must read as overdue. The original clamped at 0 with max(),
        # so a missed deadline displayed as "0h left" — indistinguishable from
        # one due this minute.
        assert hours_until(now - timedelta(hours=5)) < 0


class TestPriority:
    @pytest.mark.parametrize(
        "hours,expected",
        [(1, "HIGH"), (23, "HIGH"), (25, "MEDIUM"), (71, "MEDIUM"), (100, "LOW")],
    )
    def test_thresholds(self, hours, expected):
        from app.utils.date_utils import now_utc

        assert priority_from_deadline(now_utc() + timedelta(hours=hours)) == expected


class TestFormatting:
    def test_overdue_says_overdue(self):
        from app.utils.date_utils import now_utc

        assert "overdue" in format_deadline_for_telegram(now_utc() - timedelta(hours=3))

    def test_tomorrow_says_tomorrow(self):
        from app.utils.date_utils import now_ist

        text = format_deadline_for_telegram((now_ist() + timedelta(days=1)).replace(hour=15))
        assert "tomorrow" in text


class TestEnsureAware:
    def test_naive_gets_assumed_zone(self):
        naive = datetime(2026, 8, 21, 12, 0)
        assert ensure_aware(naive).tzinfo is UTC
        assert ensure_aware(naive, assume=IST).tzinfo is IST

    def test_aware_is_untouched(self):
        aware = datetime(2026, 8, 21, 12, 0, tzinfo=IST)
        assert ensure_aware(aware) is aware


class TestParseIso:
    @pytest.mark.parametrize("value", [None, "", "not a date", "2026-13-45"])
    def test_bad_input_returns_none(self, value):
        assert parse_iso(value) is None

    def test_z_suffix(self):
        assert parse_iso("2026-08-21T12:00:00Z") == datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
