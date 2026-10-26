from decimal import Decimal

from rest_framework import serializers

from app.models import Status


class GameIntegerField(serializers.IntegerField):
    """Reject booleans and fractional values before integer coercion."""

    def to_internal_value(self, data):
        if isinstance(data, (bool, float)) or "." in str(data):
            self.fail("invalid")
        return super().to_internal_value(data)


class TrackingWriteSerializer(serializers.Serializer):
    """Validate generic tracking writes."""

    status = serializers.ChoiceField(choices=Status.values, required=False)
    rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=Decimal(0),
        max_value=Decimal(10),
        required=False,
        allow_null=True,
    )
    progress = serializers.IntegerField(min_value=0, required=False)
    progressed_on = serializers.DateField(required=False)
    total_minutes = GameIntegerField(min_value=0, max_value=2147483647, required=False, allow_null=True)
    percentage = GameIntegerField(min_value=0, max_value=100, required=False, allow_null=True)
    start_date = serializers.DateTimeField(required=False, allow_null=True)
    end_date = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    season_number = serializers.IntegerField(required=False, allow_null=True)
    mutation_id = serializers.UUIDField(required=False, allow_null=True)


TV_RATING_MESSAGE = "Rating must be a whole number from 1 to 10."
TV_RATING_MIN = 1
TV_RATING_MAX = 10


def validate_tv_rating(value):
    """Return a TV rating as a whole number 1-10, or None to clear it (TV-504).

    TV keeps the doubled 0-10 wire scale, so new writes accept only the ten half-star
    values: whole numbers 1 to 10.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise serializers.ValidationError(TV_RATING_MESSAGE)
    try:
        rating = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as error:
        raise serializers.ValidationError(TV_RATING_MESSAGE) from error
    if not rating.is_finite() or rating != rating.to_integral_value():
        raise serializers.ValidationError(TV_RATING_MESSAGE)
    if not TV_RATING_MIN <= rating <= TV_RATING_MAX:
        raise serializers.ValidationError(TV_RATING_MESSAGE)
    return int(rating)


class TVRatingField(serializers.Field):
    """A TV rating on the 0-10 wire scale: a whole number 1-10, or null (TV-504)."""

    def __init__(self, **kwargs):
        """Allow null, which clears the stars."""
        kwargs.setdefault("allow_null", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        return validate_tv_rating(data)

    def to_representation(self, value):
        return value


class TVEpisodeCoordinateSerializer(serializers.Serializer):
    """One episode's season and episode numbers (plan 2.3.5, `from` and `to`)."""

    season_number = GameIntegerField(min_value=0)
    episode_number = GameIntegerField(min_value=0)


class TVActionSerializer(serializers.Serializer):
    """The body of POST tracking/{source}/tv/{id}/actions/{action}/ (plan 2.3.7).

    Every key is optional unless the action needs it; the view checks what each action
    needs. `from` is a Python keyword, so it is declared in get_fields().
    """

    season_number = GameIntegerField(min_value=0, required=False, allow_null=True)
    episode_number = GameIntegerField(min_value=0, required=False, allow_null=True)
    viewing_id = GameIntegerField(min_value=1, required=False, allow_null=True)
    mutation_id = serializers.UUIDField(required=False, allow_null=True)
    expected_version = GameIntegerField(
        min_value=0,
        max_value=9223372036854775807,
        required=False,
        allow_null=True,
    )
    confirm = serializers.BooleanField(required=False, default=False)
    to = TVEpisodeCoordinateSerializer(required=False, allow_null=True)
    offer_season_number = GameIntegerField(min_value=0, required=False, allow_null=True)
    rating = TVRatingField(required=False)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = TVEpisodeCoordinateSerializer(required=False, allow_null=True)
        return fields


class ConsumeSerializer(serializers.Serializer):
    """Validate consume action payloads."""

    consumed_at = serializers.DateTimeField(required=False, allow_null=True)
    create_diary_entry = serializers.BooleanField(required=False, default=False)


class EpisodeWatchSerializer(serializers.Serializer):
    """Validate watched episode payloads."""

    watched_at = serializers.DateTimeField(required=False, allow_null=True)


class BookProgressSerializer(serializers.Serializer):
    """Validate book progress payloads."""

    progress_type = serializers.ChoiceField(choices=["pages", "percentage"])
    value = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal(0))
    notes = serializers.CharField(required=False, allow_blank=True)
    progressed_on = serializers.DateField(required=False)


class BookActionSerializer(serializers.Serializer):
    """Validate an idempotent book journey/status action."""

    mutation_id = serializers.UUIDField(required=False, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)


class BookJourneyWriteSerializer(serializers.Serializer):
    """Validate editable book journey dates."""

    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)


class BookCompletionSerializer(serializers.Serializer):
    """Validate the atomic book completion composer payload."""

    journey_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    completion_date = serializers.DateField()
    rating = serializers.DecimalField(
        max_digits=2,
        decimal_places=1,
        min_value=Decimal("0.5"),
        max_value=Decimal("5.0"),
        required=False,
        allow_null=True,
    )
    review = serializers.CharField(required=False, allow_blank=True, default="")
    review_title = serializers.CharField(required=False, allow_blank=True, default="")
    liked = serializers.BooleanField(required=False, default=False)
    is_rewatch = serializers.BooleanField(required=False, default=False)
    contains_spoilers = serializers.BooleanField(required=False, default=False)
    tags = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        default=list,
    )
    mutation_id = serializers.UUIDField()

    def validate_rating(self, value):
        """Accept only the shared ten half-star values."""
        if value is not None and value * 2 != (value * 2).to_integral_value():
            raise serializers.ValidationError(
                "Rating must be a half-star value from 0.5 to 5.0.",
            )
        return value




class GameProgressSerializer(serializers.Serializer):
    """Validate and expose canonical game playthrough state."""

    playthrough_id = serializers.IntegerField(required=False, min_value=1)
    progressed_on = serializers.DateField(required=False)
    total_minutes = GameIntegerField(required=False, allow_null=True, min_value=0, max_value=2147483647)
    percentage = GameIntegerField(required=False, allow_null=True, min_value=0, max_value=100)


class GamePlaythroughSerializer(GameProgressSerializer):
    """Validate and expose canonical game playthrough state."""

    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)


class GameCompletionSerializer(GameProgressSerializer):
    """Validate and expose canonical game playthrough state."""

    progressed_on = None
    completion_date = serializers.DateField()
    start_date = serializers.DateField(required=False, allow_null=True)
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, min_value=Decimal("0.5"), max_value=Decimal("5.0"), required=False, allow_null=True)
    liked = serializers.BooleanField(required=False)
    is_rewatch = serializers.BooleanField(required=False)
    review = serializers.CharField(required=False, allow_blank=True)
    review_title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    contains_spoilers = serializers.BooleanField(required=False)
    tags = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    mutation_id = serializers.UUIDField()
