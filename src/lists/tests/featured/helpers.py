"""Test helpers for featured lists. Family agents: build your adapter tests on these.

- ``load_fixture("<adapter>", "<file>")`` reads ``fixtures/<adapter>/<file>`` (JSON is
  parsed, anything else is returned as text).
- ``api_routes({...})`` patches ``app.providers.services.api_request`` so that each
  URL containing a key returns its value; an unmatched URL fails the test, so a test
  can never reach the network.
- ``make_definition(**fields)`` returns a valid ``FeaturedList`` with test defaults.
- ``few_rows(definition)`` lets a real chart definition accept a trimmed fixture: it
  sets ``min_entries`` (the rows a complete chart has) to 1.
- ``fake_adapter(result)`` registers a temporary adapter (key ``"test"``) that returns
  ``result`` (a ``FetchResult``, a list of entries, or a callable).
- ``FeaturedTestCase`` clears the cache, provider quotas and pending ratings, creates the ``Spine`` owner, and offers
  ``run_list(definition, entries)`` and ``assertListRefs(custom_list, refs)``.
"""

import dataclasses
import json
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.providers import quota
from lists.featured import engine, overrides, ratings, registry
from lists.featured.adapters import ADAPTERS
from lists.featured.registry import FeaturedList
from lists.featured.types import Adapter, Entry, FetchResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_path(adapter, name):
    """Return the path of ``fixtures/<adapter>/<name>``."""
    return FIXTURES / adapter / name


def load_fixture(adapter, name):
    """Return a fixture: parsed JSON for ``.json`` files, text otherwise."""
    path = fixture_path(adapter, name)
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


@contextmanager
def api_routes(routes):
    """Patch ``services.api_request``: the first route key found in the URL wins.

    A value may be a payload, an exception instance (raised), or a callable taking
    ``(url, params)``. Yields the mock so tests can inspect ``call_args_list``.
    """

    def respond(provider, method, url, params=None, *args, **kwargs):  # noqa: ARG001
        for fragment, value in routes.items():
            if fragment in url:
                if isinstance(value, BaseException):
                    raise value
                return value(url, params) if callable(value) else value
        msg = f"Unexpected {method} {url} (add a route)"
        raise AssertionError(msg)

    with patch("app.providers.services.api_request", side_effect=respond) as mocked:
        yield mocked


def make_definition(**fields):
    """Return a valid definition; any field may be overridden."""
    values = {
        "key": "test-list",
        "name": "Test List",
        "media_types": ("movie",),
        "kind": "chart",
        "position": 150,
        "adapter": "test",
        "params": {},
        "cadence": timedelta(days=1),
    }
    values.update(fields)
    return FeaturedList(**values)


def few_rows(definition, minimum=1):
    """Return ``definition`` with ``params["min_entries"]`` lowered for a trimmed fixture."""
    return dataclasses.replace(definition, params={**definition.params, "min_entries": minimum})


def entry(rank, title, **fields):
    """Return an ``Entry`` (keyword fields as in ``Entry``)."""
    return Entry(rank=rank, title=title, **fields)


@contextmanager
def fake_adapter(result, key="test", **adapter_fields):
    """Register a temporary adapter returning ``result`` for every definition."""

    def fetch(definition):
        value = result(definition) if callable(result) else result
        return value if isinstance(value, FetchResult) else FetchResult(entries=list(value))

    previous = ADAPTERS.get(key)
    ADAPTERS[key] = Adapter(key=key, fetch=fetch, **adapter_fields)
    try:
        yield ADAPTERS[key]
    finally:
        if previous is None:
            ADAPTERS.pop(key, None)
        else:
            ADAPTERS[key] = previous


@contextmanager
def no_overrides():
    """Ignore the repository's override CSVs."""
    with patch.object(overrides, "table", return_value={}):
        yield


@override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
class FeaturedTestCase(TestCase):
    """Base class: empty cache, a ``Spine`` owner, and run helpers."""

    def setUp(self):
        super().setUp()
        cache.clear()
        quota.clear()
        ratings.clear()
        self.owner = get_user_model().objects.create_user(username=engine.OWNER_USERNAME)

    def run_list(self, definition, entries=None, *, fetch_result=None, **run_kwargs):
        """Run ``definition`` through the engine with a fake adapter."""
        result = fetch_result if fetch_result is not None else FetchResult(entries=list(entries or []))
        with fake_adapter(result, key=definition.adapter), registry.override_definitions([definition]):
            return engine.run(definition, self.owner, trigger="test", **run_kwargs)

    def assertListRefs(self, custom_list, refs):  # noqa: N802
        """Assert a media list holds exactly ``refs`` (``source:type:id``) in order."""
        stored = [
            f"{row.item.source}:{row.item.media_type}:{row.item.media_id}"
            for row in custom_list.customlistitem_set.select_related("item").order_by("position", "id")
        ]
        self.assertEqual(stored, list(refs))
