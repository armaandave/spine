"""Provider budgets and 429 back-off for featured syncs (``app.providers.quota``)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from app.providers import quota, services
from lists.featured import engine, registry
from lists.featured.dispatch import due
from lists.featured.registry import Guards
from lists.featured.resolve import Resolver
from lists.featured.types import Entry, FetchResult
from lists.models import CustomList, FeaturedListSync
from lists.tests.featured.helpers import FeaturedTestCase, entry, fake_adapter, make_definition

HARDCOVER = "https://api.hardcover.app/v1/graphql"
NOW = datetime(2026, 10, 22, 15, 30, tzinfo=UTC)
MIDNIGHT = datetime(2026, 10, 23, tzinfo=UTC)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code = status
        self.payload = payload if payload is not None else {"data": {}}
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:  # noqa: PLR2004
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self.payload


class FakeSession:
    """Answers each POST with the next response; fails when asked for more."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, **kwargs):
        self.calls += 1
        if not self.responses:
            msg = "unexpected request"
            raise AssertionError(msg)
        return self.responses.pop(0)

    get = post


def call(session, provider="hardcover", url=HARDCOVER):
    return services.api_request(provider, "POST", url, params={}, request_session=session)


class QuotaTestCase(SimpleTestCase):
    def setUp(self):
        quota.clear()
        clock = patch("app.providers.quota.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)


class BudgetTests(QuotaTestCase):
    def test_scoped_requests_spend_the_daily_budget_then_defer_until_midnight(self):
        session = FakeSession(FakeResponse(), FakeResponse())
        with quota.scope("featured", {"hardcover": 2}):
            call(session)
            call(session)
            with self.assertRaises(quota.Deferred) as raised:
                call(session)
        self.assertEqual(session.calls, 2)
        self.assertEqual(raised.exception.until, MIDNIGHT)
        self.assertIn("daily budget of 2 requests", str(raised.exception))
        self.assertEqual(quota.used_today("featured", "hardcover"), 3)

    def test_the_budget_starts_again_the_next_utc_day(self):
        with quota.scope("featured", {"hardcover": 1}):
            call(FakeSession(FakeResponse()))
            with patch("app.providers.quota.timezone.now", return_value=MIDNIGHT + timedelta(minutes=1)):
                call(FakeSession(FakeResponse()))

    def test_requests_outside_a_scope_are_never_counted_or_blocked(self):
        session = FakeSession(FakeResponse(), FakeResponse())
        with quota.scope("featured", {"hardcover": 0}):
            pass
        call(session)
        call(session)
        self.assertEqual(session.calls, 2)
        self.assertEqual(quota.used_today("featured", "hardcover"), 0)

    def test_unbudgeted_providers_pass_and_hosts_name_the_provider(self):
        with quota.scope("featured", {"hardcover": 1, "openlibrary": 0}):
            call(FakeSession(FakeResponse()), provider="tmdb", url="https://api.themoviedb.org/3/x")
            # An adapter's own label still counts against the host's provider.
            call(FakeSession(FakeResponse()), provider="", url=HARDCOVER)
            with self.assertRaises(quota.Deferred):
                call(FakeSession(), provider="open_library", url="https://openlibrary.org/search.json")
        self.assertEqual(quota.used_today("featured", "hardcover"), 1)


class RateLimitTests(QuotaTestCase):
    def throttled(self, **fields):
        return FakeResponse(status=429, **fields)

    def test_a_long_429_defers_at_once_and_blocks_until_the_reset(self):
        session = FakeSession(self.throttled(text='{"error": "Throttled. Try again in 69163 seconds"}'))
        with (
            patch("app.providers.services.time.sleep", side_effect=AssertionError("must not sleep")),
            quota.scope("featured", {"hardcover": 100}),
        ):
            with self.assertRaises(quota.Deferred) as raised:
                call(session)
            self.assertEqual(raised.exception.until, NOW + timedelta(seconds=69163))
            with self.assertRaises(quota.Deferred):
                call(FakeSession())  # blocked: no request at all
        self.assertEqual(session.calls, 1)
        self.assertEqual(quota.blocked_until("hardcover"), NOW + timedelta(seconds=69163))
        # The app's own requests are not blocked.
        call(FakeSession(FakeResponse()))

    def test_a_long_429_seen_by_the_app_blocks_featured_syncs(self):
        session = FakeSession(*[self.throttled(headers={"Retry-After": "3600"})] * 4)
        with patch("app.providers.services.time.sleep"), self.assertRaises(requests.exceptions.HTTPError):
            call(session)
        with quota.scope("featured", {"hardcover": 100}), self.assertRaises(quota.Deferred) as raised:
            call(FakeSession())
        self.assertEqual(raised.exception.until, NOW + timedelta(hours=1))

    def test_a_short_429_still_waits_and_retries(self):
        session = FakeSession(self.throttled(headers={"Retry-After": "2"}), FakeResponse(payload={"ok": True}))
        with patch("app.providers.services.time.sleep") as sleep, quota.scope("featured", {"hardcover": 100}):
            self.assertEqual(call(session), {"ok": True})
        sleep.assert_called_once_with(2)
        self.assertIsNone(quota.blocked_until("hardcover"))
        self.assertEqual(quota.used_today("featured", "hardcover"), 2)

    def test_retry_after_parsing(self):
        self.assertEqual(quota.retry_after_seconds(FakeResponse(headers={"Retry-After": "120"})), 120)
        self.assertEqual(quota.retry_after_seconds(FakeResponse(text="Try again in 69163 seconds")), 69163)
        self.assertIsNone(quota.retry_after_seconds(FakeResponse(text="slow down")))


def deferred(provider="hardcover", until=MIDNIGHT):
    return quota.Deferred(provider, until, "daily budget of 0 requests for featured is spent")


class ResolverDeferralTests(QuotaTestCase):
    def test_a_deferred_isbn_lookup_waits_instead_of_falling_back_to_open_library(self):
        resolver = Resolver(make_definition(media_types=("book",), position=350), overrides={})
        book = Entry(1, "Book", media_type="book", isbns=("9780000000001",))
        with (
            patch("app.providers.hardcover.lookup_book_by_isbn", side_effect=deferred()),
            patch("app.providers.openlibrary.lookup_book_by_isbn", side_effect=AssertionError("no fallback")),
        ):
            resolution = resolver.resolve_one(book)
        self.assertTrue(resolution.deferred)
        self.assertIsNone(resolution.match)
        self.assertEqual(resolution.retry_at, MIDNIGHT)
        self.assertIn("Hardcover daily budget", resolution.reason)
        with patch("app.providers.hardcover.lookup_book_by_isbn", return_value={"source": "hardcover", "media_id": "5", "title": "Book", "image": "https://c/5.jpg"}):
            self.assertEqual(resolver.resolve_one(book).match.ref, "hardcover:book:5")  # not cached as a miss

    def test_a_deferred_title_search_waits(self):
        resolver = Resolver(make_definition(media_types=("book",), position=350), overrides={})
        with patch("app.providers.services.search", side_effect=deferred()):
            self.assertTrue(resolver.resolve_one(Entry(1, "Some Book", year=2020)).deferred)


def books(*numbers):
    return [
        entry(number, f"Book {number}", media_type="book", ids={"hardcover": str(number)}, image=f"https://c/{number}.jpg")
        for number in numbers
    ]


def isbn_books(*numbers):
    return [entry(number, f"New {number}", media_type="book", isbns=(f"97800000000{number:02d}",)) for number in numbers]


@override_settings(FEATURED_HARDCOVER_DAILY_BUDGET=0, FEATURED_OPENLIBRARY_DAILY_BUDGET=0)
class EngineDeferralTests(FeaturedTestCase):
    """The real provider functions run; a zero budget stops them before the network."""

    def setUp(self):
        super().setUp()
        for patcher in (
            patch.object(services.session, "post", side_effect=AssertionError("network")),
            patch("app.providers.quota.timezone.now", return_value=NOW),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.definition = make_definition(media_types=("book",), position=350, guards=Guards(min_resolve_rate=0.9))

    def sync(self):
        return FeaturedListSync.objects.get(key="test-list")

    def test_a_new_list_is_written_with_what_resolved_and_retried_at_midnight(self):
        # 9 of 10 matched clears the 90% bar even with the waiting entry counted as missing.
        result = self.run_list(self.definition, [*books(*range(1, 10)), *isbn_books(10)], now=NOW)

        self.assertEqual(result.status, "deferred")
        self.assertListRefs(result.custom_list, [f"hardcover:book:{number}" for number in range(1, 10)])
        sync = self.sync()
        self.assertEqual((sync.last_status, sync.consecutive_failures), ("deferred", 0))
        self.assertEqual(sync.deferred_until, MIDNIGHT)
        self.assertIn("daily budget of 0 requests", sync.last_error)
        self.assertEqual((sync.entry_count, sync.resolved_count), (10, 9))
        self.assertTrue(all("deferred until" in row["reason"] for row in sync.unresolved))

    def test_waiting_entries_count_as_missing_against_the_bar(self):
        # Production on 2026-10-23: 1 of 15 books matched and 14 waited for Hardcover's
        # budget. 1 of 15 is below the default 50% bar, so the list doesn't appear yet.
        definition = make_definition(media_types=("book",), position=350)
        result = self.run_list(definition, [*books(1), *isbn_books(*range(2, 16))], now=NOW)

        self.assertEqual(result.status, "deferred")
        self.assertIn("the list was left as it is", result.message)
        self.assertFalse(CustomList.objects.exists())
        sync = self.sync()
        self.assertEqual((sync.last_status, sync.deferred_until, sync.consecutive_failures), ("deferred", MIDNIGHT, 0))
        self.assertEqual((sync.entry_count, sync.resolved_count, sync.last_success_at), (15, 1, None))
        self.assertEqual(len(sync.unresolved), 14)

    def test_a_list_that_clears_its_bar_with_waiting_entries_counted_as_missing_is_written(self):
        definition = make_definition(media_types=("book",), position=350)
        result = self.run_list(definition, [*books(*range(1, 9)), *isbn_books(*range(9, 16))], now=NOW)  # 8 of 15

        self.assertEqual(result.status, "deferred")
        self.assertEqual(result.custom_list.items.count(), 8)
        self.assertEqual((self.sync().deferred_until, self.sync().resolved_count), (MIDNIGHT, 8))

    def test_a_current_list_is_left_as_it_is_rather_than_shrunk(self):
        with override_settings(FEATURED_HARDCOVER_DAILY_BUDGET=100):
            first = self.run_list(self.definition, books(1, 2, 3), now=NOW - timedelta(days=1))
        success_at = self.sync().last_success_at

        result = self.run_list(self.definition, [*books(1), *isbn_books(4, 5)], now=NOW)

        self.assertEqual(result.status, "deferred")
        self.assertIn("the list was left as it is", result.message)
        self.assertListRefs(first.custom_list, ["hardcover:book:1", "hardcover:book:2", "hardcover:book:3"])
        sync = self.sync()
        self.assertEqual((sync.last_status, sync.last_success_at, sync.consecutive_failures), ("deferred", success_at, 0))
        self.assertIsNotNone(sync.deferred_until)

    def test_nothing_resolved_but_deferred_is_not_guarded(self):
        result = self.run_list(self.definition, isbn_books(1, 2), now=NOW)
        self.assertEqual(result.status, "deferred")
        self.assertFalse(CustomList.objects.exists())
        self.assertEqual((self.sync().last_status, self.sync().consecutive_failures), ("deferred", 0))

    def test_a_real_miss_is_still_guarded(self):
        definition = make_definition(media_types=("book",), position=350, guards=Guards(min_resolve_rate=0.9, max_title_searches=0))
        result = self.run_list(definition, [*books(1), entry(2, "Nothing", media_type="book"), *isbn_books(3)], now=NOW)
        self.assertEqual(result.status, "guarded")  # 1 of 2 non-deferred entries resolved

    def test_metadata_for_new_items_counts_and_defers(self):
        definition = replace(self.definition, guards=Guards(min_resolve_rate=0.5))  # 1 of 2 clears it
        result = self.run_list(definition, [*books(1), entry(2, "", media_type="book", ids={"hardcover": "2"})], now=NOW)
        self.assertEqual(result.status, "deferred")
        self.assertListRefs(result.custom_list, ["hardcover:book:1"])
        self.assertIn("deferred until", self.sync().unresolved[0]["reason"])

    def test_an_adapter_that_needs_the_provider_defers_the_run(self):
        def fetch(definition):
            raise deferred()

        with fake_adapter(fetch), registry.override_definitions([self.definition]):
            result = engine.run(self.definition, self.owner, trigger="test", now=NOW)
        self.assertEqual(result.status, "deferred")
        self.assertEqual((self.sync().last_status, self.sync().deferred_until), ("deferred", MIDNIGHT))

    def test_the_next_full_run_clears_the_deferral(self):
        self.run_list(self.definition, [*books(1), *isbn_books(2)], now=NOW)
        with override_settings(FEATURED_HARDCOVER_DAILY_BUDGET=100):
            result = self.run_list(self.definition, books(1, 2), now=MIDNIGHT + timedelta(minutes=5))
        self.assertEqual(result.status, "ok")
        self.assertIsNone(self.sync().deferred_until)
        self.assertEqual(self.sync().last_error, "")

    def test_dry_runs_are_budgeted_too(self):
        with fake_adapter(FetchResult(entries=[*books(1), *isbn_books(2)])):
            result = engine.dry_run(self.definition, self.owner)
        self.assertTrue(result.resolutions[1].deferred)
        self.assertIn("1 entries deferred", result.message)


class DeferredDueTests(SimpleTestCase):
    def test_a_deferred_list_waits_for_its_retry_time(self):
        definition = make_definition(cadence=timedelta(days=7))
        sync = FeaturedListSync(
            key="test-list",
            last_attempt_at=NOW,
            last_success_at=None,
            definition_hash="hash",
            deferred_until=MIDNIGHT,
        )
        self.assertEqual(due(definition, sync, NOW + timedelta(minutes=5)).reason, "deferred")
        self.assertFalse(due(definition, sync, NOW + timedelta(minutes=5)).due)
        decision = due(definition, sync, MIDNIGHT + timedelta(seconds=1))
        self.assertEqual((decision.due, decision.reason), (True, "deferral over"))
