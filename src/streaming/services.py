"""Shared streaming availability helpers.

Contract: docs/streaming-availability-contract.md
"""

import re
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache

from app.models import MediaTypes, Sources
from streaming.models import UserStreamingService

ATTRIBUTION = {"name": "JustWatch", "url": "https://www.justwatch.com"}
FRESH_FOR = timedelta(hours=24)
LOGO_BASE_URL = "https://image.tmdb.org/t/p/w154"
# ST-200 order and labels for offer groups.
OFFER_GROUPS = (
    ("flatrate", "Stream"),
    ("free", "Free"),
    ("ads", "Free with ads"),
    ("rent", "Rent"),
    ("buy", "Buy"),
)
REGION_HEADER = "X-Spine-Region"
UNSET_REGION = "UNSET"
MAX_SERVICES = 100

ACTIVE_REGIONS_CACHE_KEY = "streaming:active_regions:v1"
ACTIVE_REGIONS_TTL = 300
# Country names almost never change, so keep them long enough that request
# paths which must not call TMDB nearly always find them.
REGION_NAMES_CACHE_KEY = "streaming:region_names:v1"
REGION_NAMES_TTL = 60 * 60 * 24 * 30

_REGION_PATTERN = re.compile(r"^[A-Z]{2}$")
_TV_LIKE_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
}


def normalize_region(value):
    """Return an uppercase ISO 3166-1 alpha-2 code, or None."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if _REGION_PATTERN.match(code) else None


def _saved_region(user):
    if user is None or not user.is_authenticated:
        return UNSET_REGION
    return getattr(user, "watch_provider_region", UNSET_REGION)


def availability_enabled(user):
    """Return False only when the user saved the web "Disabled" region."""
    return _saved_region(user) != ""


def region_is_set(user):
    """Return True when the user saved a real country (ST-100 step 1)."""
    return normalize_region(_saved_region(user)) is not None


def effective_region(user, request=None):
    """Resolve the user's streaming country per ST-100, or None when disabled."""
    saved = _saved_region(user)
    if saved == "":
        return None
    code = normalize_region(saved)
    if code:
        return code
    if request is not None:
        code = normalize_region(request.headers.get(REGION_HEADER))
        if code:
            return code
    return normalize_region(settings.STREAMING_DEFAULT_REGION) or "US"


def user_provider_ids(user):
    """Return the user's selected TMDB provider IDs."""
    if user is None or not user.is_authenticated:
        return []
    return list(
        UserStreamingService.objects.filter(user=user)
        .order_by("provider__display_priority", "provider__name")
        .values_list("provider_id", flat=True),
    )


def active_regions():
    """Return countries we store snapshots for (ST-400).

    That is every country saved by a user with services, plus the default.
    """
    regions = cache.get(ACTIVE_REGIONS_CACHE_KEY)
    if regions is None:
        from django.contrib.auth import get_user_model

        saved = (
            get_user_model()
            .objects.filter(streaming_services__isnull=False)
            .values_list("watch_provider_region", flat=True)
            .distinct()
        )
        regions = {code for code in map(normalize_region, saved) if code}
        default = normalize_region(settings.STREAMING_DEFAULT_REGION)
        if default:
            regions.add(default)
        cache.set(ACTIVE_REGIONS_CACHE_KEY, regions, ACTIVE_REGIONS_TTL)
    return set(regions)


def invalidate_active_regions():
    """Forget the cached active-region set after preferences change."""
    cache.delete(ACTIVE_REGIONS_CACHE_KEY)


def title_key(source, media_type, media_id):
    """Map an item identity to its (title_type, tmdb_id), or None (ST-002)."""
    if source != Sources.TMDB.value or media_id in (None, ""):
        return None
    if media_type == MediaTypes.MOVIE.value:
        return ("movie", str(media_id))
    if media_type in _TV_LIKE_TYPES:
        return ("tv", str(media_id))
    return None


def logo_url(logo_path):
    """Return a sized TMDB logo URL for a provider logo path."""
    return f"{LOGO_BASE_URL}{logo_path}" if logo_path else None


_region_names = {"expires": 0.0, "names": {}}


def _memoized_region_names():
    if _region_names["names"] and _region_names["expires"] > time.monotonic():
        return _region_names["names"]
    return None


def _remember_region_names(names):
    _region_names.update(names=names, expires=time.monotonic() + 86400)
    return names


def region_names():
    """Return {code: English name} for TMDB watch regions.

    Memoized per process for a day on top of the Django cache, so DEBUG's
    DummyCache does not add a TMDB call to every request.
    """
    names = _memoized_region_names()
    if names is not None:
        return names
    names = cache.get(REGION_NAMES_CACHE_KEY)
    if not names:
        from app.providers import tmdb

        names = {code: name for code, name in tmdb.watch_provider_regions() if code}
        if names:
            cache.set(REGION_NAMES_CACHE_KEY, names, REGION_NAMES_TTL)
    return _remember_region_names(names)


def cached_region_names():
    """Return known region names without contacting TMDB ({} when unknown)."""
    names = _memoized_region_names()
    if names is not None:
        return names
    names = cache.get(REGION_NAMES_CACHE_KEY)
    return _remember_region_names(names) if names else {}


def region_name(code, *, fetch=True):
    """Return a country's display name, falling back to the code.

    With ``fetch=False`` it never contacts TMDB, for paths such as media
    detail that must not make extra provider requests.
    """
    if not code:
        return None
    try:
        names = region_names() if fetch else cached_region_names()
    except Exception:  # noqa: BLE001 - a name lookup must never break a page
        return code
    return names.get(code, code)
