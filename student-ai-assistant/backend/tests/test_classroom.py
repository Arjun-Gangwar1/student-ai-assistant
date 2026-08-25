"""
Classroom course staleness filtering.

IITDh does not appear to archive courses when a semester ends, so Google's
own courseStates=ACTIVE filter alone still returns courses like "MA 101 2024"
two years later. _course_is_current is the app-level filter that catches what
ACTIVE misses, judged by actual coursework/announcement activity rather than
course state.
"""

from datetime import timedelta

from app.connectors.classroom import _course_is_current
from app.utils.date_utils import now_utc


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TestCourseIsCurrent:
    def test_course_with_no_activity_at_all_is_kept(self):
        """A brand-new course with nothing posted yet is not 'finished'."""
        assert _course_is_current([], []) is True

    def test_recent_coursework_due_date_keeps_course(self):
        recent = now_utc() + timedelta(days=5)
        coursework = [{"dueDate": {"year": recent.year, "month": recent.month, "day": recent.day}}]
        assert _course_is_current(coursework, []) is True

    def test_old_coursework_due_date_is_stale(self):
        old = now_utc() - timedelta(days=400)
        coursework = [{"dueDate": {"year": old.year, "month": old.month, "day": old.day}}]
        assert _course_is_current(coursework, []) is False

    def test_recent_announcement_keeps_course_even_with_old_coursework(self):
        old = now_utc() - timedelta(days=400)
        coursework = [{"dueDate": {"year": old.year, "month": old.month, "day": old.day}}]
        announcements = [{"updateTime": _iso(now_utc() - timedelta(days=1))}]
        assert _course_is_current(coursework, announcements) is True

    def test_undated_recent_coursework_is_current(self):
        """Coursework with no due date (e.g. ungraded material) still signals activity."""
        coursework = [{"creationTime": _iso(now_utc() - timedelta(days=3))}]
        assert _course_is_current(coursework, []) is True

    def test_old_undated_coursework_and_old_announcements_are_stale(self):
        old = now_utc() - timedelta(days=300)
        coursework = [{"creationTime": _iso(old)}]
        announcements = [{"updateTime": _iso(old)}]
        assert _course_is_current(coursework, announcements) is False

    def test_malformed_due_date_does_not_crash(self):
        """An incomplete dueDate (missing day) must be skipped, not raise."""
        coursework = [{"dueDate": {"year": 2024, "month": 5}}]
        assert _course_is_current(coursework, []) is False
