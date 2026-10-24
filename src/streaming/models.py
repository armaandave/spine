"""Streaming availability for TMDB movies and TV shows.

The data comes from TMDB's watch-provider endpoints, which TMDB sources from
JustWatch. Any surface that shows it must credit JustWatch.
"""

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint

# Offer types that count as "available to stream". Rent and buy never do.
STREAMABLE_MONETIZATIONS = ("flatrate", "free", "ads")


class StreamingProvider(models.Model):
    """A streaming service, keyed by its TMDB watch-provider ID."""

    id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    logo_path = models.CharField(max_length=255, blank=True, default="")
    display_priority = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_priority", "name"]

    def __str__(self):
        """Return the provider name."""
        return self.name


class StreamingAvailability(models.Model):
    """Where-to-watch snapshot for one TMDB title in one region.

    A row with no offers means the title was checked and is not available in
    that region. A missing row means it has not been checked yet.
    """

    class TitleType(models.TextChoices):
        MOVIE = "movie", "Movie"
        TV = "tv", "TV"

    title_type = models.CharField(max_length=5, choices=TitleType.choices)
    tmdb_id = models.CharField(max_length=36)
    region = models.CharField(max_length=2)
    link = models.URLField(max_length=500, blank=True, default="")
    fetched_at = models.DateTimeField()

    class Meta:
        verbose_name_plural = "streaming availability"
        constraints = [
            UniqueConstraint(
                fields=["title_type", "tmdb_id", "region"],
                name="streaming_avail_title_region_uniq",
            ),
            CheckConstraint(
                condition=Q(title_type__in=["movie", "tv"]),
                name="streaming_avail_title_type_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["region", "fetched_at"],
                name="streaming_avail_staleness_idx",
            ),
        ]

    def __str__(self):
        """Return a readable title/region label."""
        return f"{self.title_type}:{self.tmdb_id} ({self.region})"


class StreamingOffer(models.Model):
    """One way to watch a title in a region: a provider plus an offer type."""

    class Monetization(models.TextChoices):
        FLATRATE = "flatrate", "Stream"
        FREE = "free", "Free"
        ADS = "ads", "Free with ads"
        RENT = "rent", "Rent"
        BUY = "buy", "Buy"

    availability = models.ForeignKey(
        StreamingAvailability,
        on_delete=models.CASCADE,
        related_name="offers",
    )
    provider = models.ForeignKey(
        StreamingProvider,
        on_delete=models.CASCADE,
        related_name="offers",
    )
    monetization = models.CharField(max_length=10, choices=Monetization.choices)
    display_priority = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["availability", "provider", "monetization"],
                name="streaming_offer_uniq",
            ),
            CheckConstraint(
                condition=Q(monetization__in=["flatrate", "free", "ads", "rent", "buy"]),
                name="streaming_offer_monetization_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["provider", "monetization"],
                name="streaming_offer_provider_idx",
            ),
        ]

    def __str__(self):
        """Return a readable offer label."""
        return f"{self.provider_id} {self.monetization}"


class UserStreamingService(models.Model):
    """A streaming service the user has said they subscribe to."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="streaming_services",
    )
    provider = models.ForeignKey(
        StreamingProvider,
        on_delete=models.CASCADE,
        related_name="subscribers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["user", "provider"],
                name="streaming_user_service_uniq",
            ),
        ]

    def __str__(self):
        """Return a readable user/provider label."""
        return f"{self.user_id} -> {self.provider_id}"
