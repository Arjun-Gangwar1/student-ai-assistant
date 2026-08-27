"""
The token budget, and its handling of a provider-reported quota outage.

The local counter is an estimate that resets with the process, so after a
deploy it reports a full budget while the server-side quota is still spent.
Every sync then spends part of a 1000/day request allowance rediscovering the
same 429. Groq states when the limit frees up; that answer should win.
"""

import time

import pytest

from app.intelligence.llm_client import LLMQuotaExhausted, _parse_retry_after, _wrap
from app.utils import token_budget


@pytest.fixture(autouse=True)
def _reset_budget():
    """Module-level state; without this the tests leak into each other."""
    token_budget._used = 0
    token_budget._exhausted_until = 0.0
    token_budget._warned_background = False
    yield
    token_budget._used = 0
    token_budget._exhausted_until = 0.0
    token_budget._warned_background = False


class TestRetryAfterParsing:
    @pytest.mark.parametrize(
        "message, expected",
        [
            ("Please try again in 55.372s", 55.372),
            ("Please try again in 4m28.272s", 268.272),
            ("Please try again in 11m36.383999999s", 696.383999999),
            ("try again in 2m0s", 120.0),
            ("no hint at all", None),
            ("", None),
        ],
    )
    def test_parses_groqs_own_wording(self, message, expected):
        assert _parse_retry_after(message) == expected


class TestProviderReportedExhaustion:
    def test_a_fresh_budget_allows_work(self):
        assert token_budget.remaining() == token_budget.DAILY_TOKEN_LIMIT
        assert token_budget.allow_background() is True
        assert token_budget.allow_chat() is True

    def test_provider_429_overrides_an_optimistic_local_count(self):
        """The exact case a restart creates: local says full, server says no."""
        assert token_budget.allow_background() is True

        token_budget.note_exhausted(60)

        assert token_budget.remaining() == 0
        assert token_budget.allow_background() is False
        assert token_budget.allow_chat() is False

    def test_pause_lifts_once_the_stated_time_passes(self):
        token_budget.note_exhausted(0.05)
        assert token_budget.allow_chat() is False
        time.sleep(0.08)
        assert token_budget.allow_chat() is True
        assert token_budget.remaining() == token_budget.DAILY_TOKEN_LIMIT

    def test_missing_hint_falls_back_to_the_default_cooldown(self):
        token_budget.note_exhausted(None)
        assert token_budget.exhausted_for() == pytest.approx(
            token_budget.DEFAULT_COOLDOWN_SECONDS, abs=2
        )

    def test_a_longer_pause_is_never_shortened_by_a_later_shorter_one(self):
        token_budget.note_exhausted(600)
        token_budget.note_exhausted(5)
        assert token_budget.exhausted_for() > 500


class TestWrapReportsToTheBudget:
    def test_per_day_quota_error_pauses_the_budget(self):
        exc = Exception(
            "Rate limit reached ... on tokens per day (TPD): Limit 200000, "
            "Used 200000, Requested 621. Please try again in 4m28.272s"
        )
        wrapped = _wrap(exc)

        assert isinstance(wrapped, LLMQuotaExhausted)
        assert token_budget.allow_chat() is False
        assert token_budget.exhausted_for() == pytest.approx(268, abs=5)

    def test_a_transient_error_does_not_pause_anything(self):
        """A per-minute limit or a network blip must not stop the day's work."""
        wrapped = _wrap(Exception("Rate limit reached on tokens per minute (TPM)"))

        assert not isinstance(wrapped, LLMQuotaExhausted)
        assert token_budget.allow_chat() is True
        assert token_budget.exhausted_for() == 0
