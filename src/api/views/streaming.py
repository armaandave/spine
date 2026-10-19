"""Streaming country and service settings.

Contract: docs/streaming-availability-contract.md
"""

import logging

from django.core.cache import cache
from django.db import transaction
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from streaming import payloads, services, tmdb
from streaming.models import UserStreamingService
from streaming.region_names import REGION_NAMES
from streaming.tasks import check_user_streaming_library

logger = logging.getLogger(__name__)

# Seconds during which repeated saves for one user and country queue one library check.
LIBRARY_CHECK_DEBOUNCE = 60


def _queue_library_check(user_id, region):
    """Queue the ST-102 library check at most once a minute per user and country.

    Fails open: when the cache is unavailable the check is queued anyway.
    """
    try:
        claimed = cache.add(
            f"streaming:library-check:v1:{user_id}:{region}",
            1,
            timeout=LIBRARY_CHECK_DEBOUNCE,
        )
    except (ConnectionInterrupted, RedisError, ConnectionError, OSError) as error:
        logger.warning("Streaming library check debounce unavailable type=%s", type(error).__name__)
        claimed = True
    if claimed:
        check_user_streaming_library.delay(user_id)


def _known_region(value):
    """Return the normalized code when TMDB lists it as a watch region."""
    code = services.normalize_region(value)
    if code is None or code not in services.region_names():
        return None
    return code


def _provider_object(provider):
    return payloads.provider_payload(
        provider["provider_id"],
        provider["name"],
        provider["logo_path"],
        provider["display_priority"],
    )


class StreamingSettingsSerializer(serializers.Serializer):
    """Validate a full replacement of the user's streaming settings."""

    region = serializers.CharField()
    provider_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        max_length=services.MAX_SERVICES,
    )

    def validate_region(self, value):
        region = _known_region(value)
        if region is None:
            raise serializers.ValidationError("Unknown region.")
        return region

    def validate_provider_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Provider IDs must be unique.")
        return value

    def validate(self, attrs):
        # Already-selected services stay valid after a country change (ST-101),
        # so only new IDs must be in the country's catalog.
        stored = set(
            UserStreamingService.objects.filter(
                user=self.context["request"].user,
            ).values_list("provider_id", flat=True),
        )
        new_ids = set(attrs["provider_ids"]) - stored
        attrs["new_providers"] = []
        if new_ids:
            catalog = {
                provider["provider_id"]: provider
                for provider in tmdb.provider_catalog(attrs["region"])
            }
            unknown = sorted(new_ids - set(catalog))
            if unknown:
                ids = ", ".join(map(str, unknown))
                raise serializers.ValidationError(
                    {"provider_ids": [f"Unknown provider IDs for {attrs['region']}: {ids}."]},
                )
            attrs["new_providers"] = [catalog[provider_id] for provider_id in sorted(new_ids)]
        return attrs


class StreamingRegionsView(APIView):
    """Countries with streaming availability, sorted by name."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        results = sorted(
            (
                {"code": code, "name": REGION_NAMES.get(code, name)}
                for code, name in services.region_names().items()
            ),
            key=lambda region: (region["name"].casefold(), region["code"]),
        )
        return Response({"results": results})


class StreamingProvidersView(APIView):
    """Streaming service catalog for one country (ST-101)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        requested = request.query_params.get("region")
        if requested:
            region = _known_region(requested)
            if region is None:
                return Response(
                    {"region": ["Unknown region."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # A user who turned availability off still browses a catalog.
            region = services.effective_region(
                request.user,
                request,
            ) or services.effective_region(None, request)
        return Response(
            {
                "region": region,
                "results": [_provider_object(provider) for provider in tmdb.provider_catalog(region)],
            },
        )


class MeStreamingView(APIView):
    """The current user's streaming country and services."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(payloads.streaming_settings(request.user, request))

    def put(self, request):
        user = request.user
        if user.is_demo:
            return Response(
                {"detail": "Streaming settings are view-only for demo accounts."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = StreamingSettingsSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        region = serializer.validated_data["region"]
        provider_ids = serializer.validated_data["provider_ids"]
        tmdb.upsert_providers(serializer.validated_data["new_providers"])
        with transaction.atomic():
            if user.watch_provider_region != region:
                user.watch_provider_region = region
                user.save(update_fields=["watch_provider_region"])
            selections = UserStreamingService.objects.filter(user=user)
            selections.exclude(provider_id__in=provider_ids).delete()
            kept = set(selections.values_list("provider_id", flat=True))
            UserStreamingService.objects.bulk_create(
                [
                    UserStreamingService(user=user, provider_id=provider_id)
                    for provider_id in provider_ids
                    if provider_id not in kept
                ],
                ignore_conflicts=True,
            )
            # Commit callbacks run in order, so the check sees the new active
            # regions. ST-102: the save never waits for the library check.
            transaction.on_commit(services.invalidate_active_regions)
            transaction.on_commit(lambda: _queue_library_check(user.pk, region))
        return Response(payloads.streaming_settings(user, request))
