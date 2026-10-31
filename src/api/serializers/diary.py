from rest_framework import serializers

from api.serializers.common import MediaRefSerializer
from api.serializers.tracking import GameIntegerField


class CalendarOrDateTimeField(serializers.Field):
    """Accept a calendar date for single-weight and legacy datetimes elsewhere."""

    def to_internal_value(self, data):
        if "T" in str(data):
            return serializers.DateTimeField().run_validation(data)
        return serializers.DateField().run_validation(data)


class DiaryFavoriteSerializer(serializers.Serializer):
    """A log's favorite: a song on its album, or an episode of its show or season."""

    kind = serializers.ChoiceField(choices=["song", "episode"])
    title = serializers.CharField(max_length=255)
    # Songs: the tracklist's recording ID, and its number on the album ("3", "A3").
    id = serializers.CharField(required=False, max_length=64)
    number = serializers.CharField(required=False, allow_blank=True, max_length=16)
    # Episodes.
    season_number = serializers.IntegerField(required=False, min_value=0)
    episode_number = serializers.IntegerField(required=False, min_value=0)

    def validate(self, attrs):
        if attrs["kind"] == "song" and not attrs.get("id"):
            raise serializers.ValidationError({"id": "A favorite song needs its ID."})
        if attrs["kind"] == "episode" and (
            attrs.get("season_number") is None or attrs.get("episode_number") is None
        ):
            raise serializers.ValidationError("A favorite episode needs its season and episode numbers.")
        return attrs


class DiaryEntryWriteSerializer(serializers.Serializer):
    """Validate diary entry create/update payloads."""

    ref = MediaRefSerializer(required=False)
    mutation_id = serializers.UUIDField(required=False)
    playthrough_id = serializers.IntegerField(required=False, min_value=1)
    total_minutes = GameIntegerField(required=False, min_value=0, allow_null=True)
    percentage = GameIntegerField(required=False, min_value=0, max_value=100, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    consumed_at = CalendarOrDateTimeField(required=False)
    rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=0,
        max_value=10,
        required=False,
        allow_null=True,
    )
    review = serializers.CharField(required=False, allow_blank=True)
    review_title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    liked = serializers.BooleanField(required=False)
    is_rewatch = serializers.BooleanField(required=False)
    auto_mark_consumed = serializers.BooleanField(required=False, default=False)
    contains_spoilers = serializers.BooleanField(required=False, default=False)
    visibility = serializers.ChoiceField(
        choices=["public", "followers", "private"],
        required=False,
        default="public",
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )
    favorite = DiaryFavoriteSerializer(required=False, allow_null=True)
    # A TV log saved from a season or episode page names that page, so the response's
    # `tv` carries the page's state and composer (TV-404, TV-705).
    page_season_number = GameIntegerField(required=False, min_value=0, allow_null=True)
    page_episode_number = GameIntegerField(required=False, min_value=0, allow_null=True)
