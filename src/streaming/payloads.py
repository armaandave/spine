"""API payloads for streaming availability.

Contract: docs/streaming-availability-contract.md
"""

from django.utils import timezone

from app.models import MediaTypes, Sources
from streaming import services, tmdb
from streaming.models import (
    STREAMABLE_MONETIZATIONS,
    StreamingAvailability,
    UserStreamingService,
)

WHERE_TO_WATCH_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
}


def provider_payload(provider_id, name, logo_path, display_priority):
    """Return the contract's provider object."""
    return {
        "provider_id": provider_id,
        "name": name,
        "logo_url": services.logo_url(logo_path),
        "display_priority": display_priority,
    }


def _stored_offers(title_type, tmdb_id, region):
    """Return (link, offers) from a fresh stored snapshot, or (None, None)."""
    snapshot = (
        StreamingAvailability.objects.filter(
            title_type=title_type,
            tmdb_id=tmdb_id,
            region=region,
            fetched_at__gte=timezone.now() - services.FRESH_FOR,
        )
        .only("id", "link")
        .first()
    )
    if snapshot is None:
        return None, None
    offers = [
        tmdb.Offer(
            provider_id=offer.provider_id,
            name=offer.provider.name,
            logo_path=offer.provider.logo_path,
            display_priority=offer.display_priority,
            monetization=offer.monetization,
        )
        for offer in snapshot.offers.select_related("provider")
    ]
    return snapshot.link, offers


def where_to_watch(*, source, media_type, media_id, metadata, user=None, request=None):
    """Return the detail page's ``where_to_watch`` object, or None (ST-200).

    ``providers`` lists the services that stream the title in the effective
    country, once each, keeping only the user's services and major ones: the
    user's come first, then display priority decides. Movies and shows use
    the stored snapshot for the effective country when it is fresh and
    otherwise the detail metadata, which covers every country. Seasons always
    use their own season-level metadata. Makes no provider request.
    """
    if source != Sources.TMDB.value or media_type not in WHERE_TO_WATCH_MEDIA_TYPES:
        return None
    key = services.title_key(source, media_type, media_id)
    region = services.effective_region(user, request)
    if key is None or region is None:
        return None
    mine = set(services.user_provider_ids(user))
    link, offers = None, None
    if media_type != MediaTypes.SEASON.value:
        link, offers = _stored_offers(*key, region)
    if offers is None:
        providers = (metadata or {}).get("providers")
        region_data = providers.get(region) if isinstance(providers, dict) else None
        link, offers = tmdb.region_link(region_data), tmdb.parse_offers(region_data)

    shown = {}
    for offer in offers:
        if offer.monetization not in STREAMABLE_MONETIZATIONS:
            continue
        if offer.provider_id not in mine and offer.provider_id not in services.MAJOR_PROVIDER_IDS:
            continue
        current = shown.get(offer.provider_id)
        if current is None or offer.display_priority < current.display_priority:
            shown[offer.provider_id] = offer
    ordered = sorted(
        shown.values(),
        key=lambda offer: (
            offer.provider_id not in mine,
            offer.display_priority,
            offer.name.casefold(),
            offer.provider_id,
        ),
    )
    return {
        "region": region,
        "region_name": services.region_name(region, fetch=False),
        "link": link or None,
        "providers": [
            {
                **provider_payload(
                    offer.provider_id,
                    offer.name,
                    offer.logo_path,
                    offer.display_priority,
                ),
                "is_mine": offer.provider_id in mine,
            }
            for offer in ordered
        ],
        "attribution": dict(services.ATTRIBUTION),
    }


def streaming_settings(user, request=None):
    """Return the ``/me/streaming/`` payload for a user."""
    region = services.effective_region(user, request)
    providers = [
        selection.provider
        for selection in UserStreamingService.objects.filter(user=user)
        .select_related("provider")
        .order_by("provider__display_priority", "provider__name", "provider_id")
    ]
    return {
        "region": region,
        "region_name": services.region_name(region),
        "region_is_set": services.region_is_set(user),
        "enabled": services.availability_enabled(user),
        "provider_ids": [provider.id for provider in providers],
        "providers": [
            provider_payload(
                provider.id,
                provider.name,
                provider.logo_path,
                provider.display_priority,
            )
            for provider in providers
        ],
        "attribution": dict(services.ATTRIBUTION),
    }
