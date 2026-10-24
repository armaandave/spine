"""Shared fixtures for streaming tests."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from streaming import services
from streaming.models import (
    StreamingAvailability,
    StreamingOffer,
    StreamingProvider,
    UserStreamingService,
)

REGION_NAMES = {
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "GB": "United Kingdom",
    "US": "United States",
}

NETFLIX = {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/netflix.png", "display_priority": 1}
HULU = {"provider_id": 15, "provider_name": "Hulu", "logo_path": "/hulu.png", "display_priority": 5}
MAX = {"provider_id": 1899, "provider_name": "HBO Max", "logo_path": "/max.png", "display_priority": 151}
TUBI = {"provider_id": 73, "provider_name": "Tubi TV", "logo_path": "/tubi.png", "display_priority": 20}
KANOPY = {"provider_id": 191, "provider_name": "Kanopy", "logo_path": "/kanopy.png", "display_priority": 30}
APPLE = {"provider_id": 2, "provider_name": "Apple TV Store", "logo_path": "/apple.png", "display_priority": 9}
AMAZON = {"provider_id": 10, "provider_name": "Amazon Video", "logo_path": "/amazon.png", "display_priority": 8}

US_LINK = "https://www.themoviedb.org/movie/693134-dune-part-two/watch?locale=US"
GB_LINK = "https://www.themoviedb.org/movie/693134-dune-part-two/watch?locale=GB"

# TMDB watch/providers ``results`` for one title.
TITLE_RESULTS = {
    "US": {
        "link": US_LINK,
        "flatrate": [MAX, NETFLIX],
        "free": [KANOPY],
        "ads": [TUBI],
        "rent": [AMAZON, APPLE],
        "buy": [APPLE],
        "cinema": [NETFLIX],
    },
    "GB": {"link": GB_LINK, "flatrate": [NETFLIX]},
    "FR": {"link": "https://www.themoviedb.org/movie/1/watch?locale=FR", "rent": [APPLE]},
}


def _forget_streaming_state():
    cache.clear()
    services._region_names.update(expires=0.0, names={})


def reset_streaming_state(test_case, region_names=REGION_NAMES):
    """Start ``test_case`` from clean caches, seeded with known region names.

    The per-process region-name memo outlives a test, so it is cleared again
    afterwards to keep it from leaking into other test modules.
    """
    _forget_streaming_state()
    test_case.addCleanup(_forget_streaming_state)
    if region_names:
        cache.set(services.REGION_NAMES_CACHE_KEY, dict(region_names), None)


def make_provider(entry, **overrides):
    """Create a StreamingProvider row from a TMDB-style provider entry."""
    fields = {
        "name": entry["provider_name"],
        "logo_path": entry["logo_path"],
        "display_priority": entry["display_priority"],
        **overrides,
    }
    return StreamingProvider.objects.create(id=entry["provider_id"], **fields)


def activate_region(region):
    """Make ``region`` active by giving a user who saved it a service."""
    provider = StreamingProvider.objects.filter(pk=NETFLIX["provider_id"]).first() or make_provider(NETFLIX)
    user = get_user_model().objects.create_user(username=f"active-{region}", password="pw-12345678")
    user.watch_provider_region = region
    user.save(update_fields=["watch_provider_region"])
    UserStreamingService.objects.create(user=user, provider=provider)
    return user


def make_snapshot(title_type, tmdb_id, region, offers=(), *, age=timedelta(0), link=""):
    """Create a snapshot with ``offers`` as ``(provider_entry, monetization)`` pairs."""
    availability = StreamingAvailability.objects.create(
        title_type=title_type,
        tmdb_id=tmdb_id,
        region=region,
        link=link,
        fetched_at=timezone.now() - age,
    )
    for entry, monetization in offers:
        provider = StreamingProvider.objects.filter(pk=entry["provider_id"]).first() or make_provider(entry)
        StreamingOffer.objects.create(
            availability=availability,
            provider=provider,
            monetization=monetization,
            display_priority=entry["display_priority"],
        )
    return availability
