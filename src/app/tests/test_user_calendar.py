"""The user's calendar (docs/tv-tracking-contract.md, Terms; A-60).

The server runs on America/New_York in production; these cases use an account calendar
of America/Los_Angeles at 9 p.m. Pacific, when the two calendars name different days.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.utils import timezone

from app import user_calendar
from app.tests.tv_fixtures import make_user, patch_now

PACIFIC = ZoneInfo("America/Los_Angeles")
NEW_YORK = ZoneInfo("America/New_York")
# 9 p.m. on Oct 25 in Los Angeles: already Oct 26 in New York.
PACIFIC_9PM = datetime(2026, 10, 25, 21, 0, tzinfo=PACIFIC)


@override_settings(TIME_ZONE="America/New_York")
class UserCalendarTests(TestCase):
    """Which day it is for a user, and which zone decides it."""

    def setUp(self):
        self.pacific = make_user(calendar="America/Los_Angeles")
        self.no_calendar = make_user(calendar="")

    def test_a60_today_is_the_users_calendar_day(self):
        with patch_now(PACIFIC_9PM):
            self.assertEqual(user_calendar.today(self.pacific), date(2026, 10, 25))
            self.assertEqual(user_calendar.today(self.no_calendar), date(2026, 10, 26))

    def test_without_a_stored_calendar_the_server_zone_is_used(self):
        self.assertEqual(user_calendar.calendar_tz(self.no_calendar), NEW_YORK)
        self.assertEqual(user_calendar.calendar_tz(None), NEW_YORK)
        self.no_calendar.calendar_timezone = "Not/AZone"
        self.assertEqual(user_calendar.calendar_tz(self.no_calendar), NEW_YORK)

    def test_now_reads_the_patched_django_clock(self):
        with patch_now(PACIFIC_9PM) as clock:
            self.assertEqual(user_calendar.now(), PACIFIC_9PM)
            clock.advance(hours=1)
            self.assertEqual(timezone.now(), user_calendar.now())
            self.assertEqual(user_calendar.now(), datetime(2026, 10, 26, 5, 0, tzinfo=UTC))

    def test_day_of_a_moment(self):
        moment = datetime(2026, 10, 26, 4, 0, tzinfo=UTC)
        self.assertEqual(user_calendar.day_of(moment, self.pacific), date(2026, 10, 25))
        self.assertEqual(user_calendar.day_of(moment, self.no_calendar), date(2026, 10, 26))
        self.assertEqual(user_calendar.day_of(date(2026, 1, 2), self.pacific), date(2026, 1, 2))
        naive = datetime(2026, 10, 25, 23, 30)
        self.assertEqual(user_calendar.day_of(naive, self.no_calendar), date(2026, 10, 25))

    def test_remember_stores_a_new_valid_zone_with_one_update(self):
        with self.assertNumQueries(1):
            self.assertTrue(user_calendar.remember(self.no_calendar, "Europe/Paris"))
        self.no_calendar.refresh_from_db()
        self.assertEqual(self.no_calendar.calendar_timezone, "Europe/Paris")
        with self.assertNumQueries(0):
            self.assertFalse(user_calendar.remember(self.no_calendar, "Europe/Paris"))

    def test_remember_ignores_invalid_zones(self):
        for invalid in ("", "not/a/timezone", "/etc/passwd", "../UTC", "x" * 65, None):
            with self.subTest(invalid=invalid), self.assertNumQueries(0):
                self.assertFalse(user_calendar.remember(self.pacific, invalid))
        self.pacific.refresh_from_db()
        self.assertEqual(self.pacific.calendar_timezone, "America/Los_Angeles")

    def test_the_request_zone_applies_to_the_requesting_user_only(self):
        request = SimpleNamespace(user=self.no_calendar)
        zone_token = user_calendar.REQUEST_TIMEZONE.set(ZoneInfo("Asia/Tokyo"))
        request_token = user_calendar.REQUEST.set(request)
        try:
            self.assertEqual(user_calendar.calendar_tz(self.no_calendar), ZoneInfo("Asia/Tokyo"))
            self.assertEqual(user_calendar.calendar_tz(self.pacific), PACIFIC)
            request.user = AnonymousUser()
            self.assertEqual(user_calendar.calendar_tz(self.no_calendar), NEW_YORK)
        finally:
            user_calendar.REQUEST.reset(request_token)
            user_calendar.REQUEST_TIMEZONE.reset(zone_token)

    def test_a_request_zone_set_without_a_request_applies_to_everyone(self):
        token = user_calendar.REQUEST_TIMEZONE.set(ZoneInfo("Asia/Tokyo"))
        try:
            self.assertEqual(user_calendar.calendar_tz(self.pacific), ZoneInfo("Asia/Tokyo"))
        finally:
            user_calendar.REQUEST_TIMEZONE.reset(token)
        self.assertEqual(user_calendar.calendar_tz(self.pacific), PACIFIC)


@override_settings(TIME_ZONE="UTC")
class UserCalendarUTCServerTests(TestCase):
    """The same rules with the server on UTC, as in CI."""

    def test_a60_today_on_a_utc_server(self):
        pacific = make_user(calendar="America/Los_Angeles")
        no_calendar = make_user(calendar="")
        with patch_now(PACIFIC_9PM):
            self.assertEqual(user_calendar.today(pacific), date(2026, 10, 25))
            self.assertEqual(user_calendar.today(no_calendar), date(2026, 10, 26))
        self.assertEqual(user_calendar.calendar_tz(no_calendar), ZoneInfo("UTC"))
