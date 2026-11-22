import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
from django.core.cache import cache
from django.test import TestCase

from app.providers import openlibrary


class FakeResponse:
    def __init__(self, status, body=None):
        self.status = status
        self.body = body

    async def json(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    """Answers each get() with the next outcome: a status, (status, body), or an exception."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, _url):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        status, body = outcome if isinstance(outcome, tuple) else (outcome, None)
        return FakeResponse(status, body)


@patch("app.providers.openlibrary.asyncio.sleep", new_callable=AsyncMock)
class FetchAuthorDataTests(TestCase):
    def fetch(self, session):
        return asyncio.run(openlibrary.fetch_author_data(session, "https://openlibrary.org/authors/OL1A.json"))

    def test_a_passing_failure_is_retried(self, _sleep):
        for failure in (503, 429, aiohttp.ClientConnectionError()):
            with self.subTest(failure=failure):
                session = FakeSession(failure, (200, {"name": "Ursula K. Le Guin"}))
                self.assertEqual(self.fetch(session), {"name": "Ursula K. Le Guin"})
                self.assertEqual(session.calls, 2)

    def test_a_missing_author_is_skipped_without_retrying(self, _sleep):
        session = FakeSession(404)
        self.assertEqual(self.fetch(session), {})
        self.assertEqual(session.calls, 1)

    def test_failures_that_outlast_the_retries_or_time_out_give_none(self, _sleep):
        session = FakeSession(503, 503, 503)
        self.assertIsNone(self.fetch(session))
        self.assertEqual(session.calls, openlibrary.AUTHOR_FETCH_ATTEMPTS)

        session = FakeSession(TimeoutError())
        self.assertIsNone(self.fetch(session))
        self.assertEqual(session.calls, 1)


@patch("app.providers.openlibrary.get_ratings", new_callable=AsyncMock, return_value=(None, 0))
@patch("app.providers.openlibrary.get_editions", new_callable=AsyncMock, return_value=[])
@patch("app.providers.openlibrary.services.api_request")
class BookCachingTests(TestCase):
    def setUp(self):
        cache.clear()

    def open_book(self, api_request, author):
        book = {"key": "/books/OL1M", "title": "A Wizard of Earthsea", "works": [{"key": "/works/OL1W"}]}
        work = {"key": "/works/OL1W", "authors": [{"author": {"key": "/authors/OL1A"}}]}
        api_request.side_effect = lambda *args, **_kwargs: work if "/works/" in args[2] else book
        with patch("app.providers.openlibrary.fetch_author_data", new_callable=AsyncMock, return_value=author):
            return openlibrary.book("OL1M")

    def test_a_book_whose_author_failed_for_a_moment_is_not_cached(self, api_request, *_mocks):
        self.open_book(api_request, None)
        self.assertEqual(api_request.call_count, 2)

        metadata = self.open_book(api_request, {"name": "Ursula K. Le Guin"})
        self.assertEqual(api_request.call_count, 4)
        self.assertEqual(metadata["details"]["author"], "Ursula K. Le Guin")

        self.open_book(api_request, {"name": "Ursula K. Le Guin"})
        self.assertEqual(api_request.call_count, 4)
