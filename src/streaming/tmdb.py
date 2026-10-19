"""TMDB watch-provider requests and parsing (ST-001).

TMDB sources this data from JustWatch.
"""

from typing import NamedTuple

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from app.models import Sources
from app.providers import services as provider_services
from app.providers import tmdb as tmdb_provider
from streaming.models import StreamingProvider
from streaming.services import OFFER_GROUPS, normalize_region

CATALOG_CACHE_VERSION = "v1"
CATALOG_TTL = 60 * 60 * 24
TITLE_TYPES = ("movie", "tv")
MONETIZATIONS = tuple(monetization for monetization, _label in OFFER_GROUPS)
# Sorts providers without a priority after every ranked one.
UNKNOWN_PRIORITY = 999
MAX_LINK_LENGTH = 500


class Offer(NamedTuple):
    """One way to watch a title in a region: a provider plus an offer type."""

    provider_id: int
    name: str
    logo_path: str
    display_priority: int
    monetization: str


def _get(path, **params):
    try:
        return provider_services.api_request(
            Sources.TMDB.value,
            "GET",
            f"{tmdb_provider.base_url}{path}",
            params={**tmdb_provider.base_params, **params},
        )
    except requests.exceptions.HTTPError as error:
        tmdb_provider.handle_error(error)
        raise


def fetch_title_providers(title_type, tmdb_id):
    """Return TMDB's raw watch-provider ``results`` for one title, uncached.

    The dict is keyed by region, e.g. ``{"US": {"link": ..., "flatrate": [...]}}``.
    """
    if title_type not in TITLE_TYPES:
        msg = f"Unsupported title type: {title_type}"
        raise ValueError(msg)
    response = _get(f"/{title_type}/{tmdb_id}/watch/providers")
    results = response.get("results")
    return results if isinstance(results, dict) else {}


def _priority(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return UNKNOWN_PRIORITY


def parse_provider(entry, region=None):
    """Normalize one TMDB provider entry, or return None without a usable ID.

    With ``region``, the region's ``display_priorities`` entry wins over the
    generic ``display_priority``, which is not region-specific in catalogs.
    """
    if not isinstance(entry, dict):
        return None
    try:
        provider_id = int(entry.get("provider_id"))
    except (TypeError, ValueError):
        return None
    if provider_id <= 0:
        return None
    priority = None
    if region:
        priorities = entry.get("display_priorities")
        if isinstance(priorities, dict):
            priority = priorities.get(region)
    if priority is None:
        priority = entry.get("display_priority")
    return {
        "provider_id": provider_id,
        "name": str(entry.get("provider_name") or "").strip(),
        "logo_path": str(entry.get("logo_path") or ""),
        "display_priority": _priority(priority),
    }


def parse_offers(region_data):
    """Return the offers in one region's TMDB payload, in OFFER_GROUPS order.

    Unknown keys are ignored and a repeated provider/type pair keeps its first
    entry.
    """
    if not isinstance(region_data, dict):
        return []
    offers = []
    seen = set()
    for monetization in MONETIZATIONS:
        entries = region_data.get(monetization)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            provider = parse_provider(entry)
            if provider is None or (provider["provider_id"], monetization) in seen:
                continue
            seen.add((provider["provider_id"], monetization))
            offers.append(Offer(monetization=monetization, **provider))
    return offers


def region_link(region_data):
    """Return TMDB's watch-page link for one region's payload, or ""."""
    link = region_data.get("link") if isinstance(region_data, dict) else None
    if isinstance(link, str) and len(link) <= MAX_LINK_LENGTH:
        return link
    return ""


def _provider_updates(row, data, *, update_priority):
    updates = {}
    if data["name"] and data["name"] != row.name:
        updates["name"] = data["name"]
    if data["logo_path"] and data["logo_path"] != row.logo_path:
        updates["logo_path"] = data["logo_path"]
    if update_priority and data["display_priority"] != row.display_priority:
        updates["display_priority"] = data["display_priority"]
    return updates


def upsert_providers(providers, *, update_priority=False):
    """Create missing StreamingProvider rows and refresh changed names or logos.

    ``providers`` holds dicts with provider_id, name, logo_path, and
    display_priority. A stored priority changes only with ``update_priority``,
    so per-title and other-region priorities do not keep overwriting it.
    """
    incoming = {}
    for provider in providers:
        incoming.setdefault(provider["provider_id"], provider)
    if not incoming:
        return
    existing = StreamingProvider.objects.in_bulk(list(incoming))
    now = timezone.now()
    created = []
    changed = []
    for provider_id, data in incoming.items():
        row = existing.get(provider_id)
        if row is None:
            created.append(
                StreamingProvider(
                    id=provider_id,
                    name=data["name"] or str(provider_id),
                    logo_path=data["logo_path"],
                    display_priority=data["display_priority"],
                ),
            )
            continue
        updates = _provider_updates(row, data, update_priority=update_priority)
        if updates:
            for field, value in updates.items():
                setattr(row, field, value)
            row.updated_at = now
            changed.append(row)
    if created:
        StreamingProvider.objects.bulk_create(created, ignore_conflicts=True)
    if changed:
        StreamingProvider.objects.bulk_update(
            changed,
            ["name", "logo_path", "display_priority", "updated_at"],
        )


def _catalog_cache_key(region):
    return f"streaming:catalog:{CATALOG_CACHE_VERSION}:{region}"


def provider_catalog(region):
    """Return the merged movie and TV provider catalog for a region (ST-101).

    Entries are provider dicts (see ``parse_provider``), de-duplicated and
    ordered by the region's display priority. Cached for 24 hours; a fetch
    also upserts StreamingProvider rows.
    """
    region = normalize_region(region)
    if region is None:
        raise ValueError("A two-letter region code is required.")
    cache_key = _catalog_cache_key(region)
    catalog = cache.get(cache_key)
    if catalog is not None:
        return catalog
    merged = {}
    for title_type in TITLE_TYPES:
        response = _get(f"/watch/providers/{title_type}", watch_region=region)
        for entry in response.get("results") or []:
            provider = parse_provider(entry, region=region)
            if provider is None:
                continue
            current = merged.get(provider["provider_id"])
            if current is None or provider["display_priority"] < current["display_priority"]:
                merged[provider["provider_id"]] = provider
    catalog = sorted(
        merged.values(),
        key=lambda provider: (
            provider["display_priority"],
            provider["name"].casefold(),
            provider["provider_id"],
        ),
    )
    upsert_providers(
        catalog,
        update_priority=region == normalize_region(settings.STREAMING_DEFAULT_REGION),
    )
    cache.set(cache_key, catalog, CATALOG_TTL)
    return catalog
