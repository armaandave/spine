import csv
import logging

from django.apps import apps
from django.db.models import Field, Prefetch

from app import helpers
from app.models import Episode, Item, MediaTypes, Season, Status

logger = logging.getLogger(__name__)

# Fields the TV tracking contract added to TV, Season and Episode. They aren't exported,
# so the CSV keeps today's columns (TV-803). No other model has a field by these names,
# so other media types' columns still follow their models.
TV_TRACKING_FIELD_NAMES = frozenset(
    {
        "kind",
        "watched_on",
        "choice",
        "choice_at",
        "ended_choice",
        "ended_choice_at",
        "rewatch_started_at",
        "rewatch_season_number",
        "earlier_question_at",
        "finish_offers",
        "legacy_pending",
        "reconciled_at",
        "state_version",
        "watched_count",
        "aired_count",
        "episode_count",
        "up_next_season",
        "up_next_episode",
        "up_next_air_date",
        "up_next_is_new",
        "next_air_season",
        "next_air_episode",
        "next_air_date",
        "finished",
        "caught_up",
        "last_watched_on",
        "last_activity_on",
        "last_activity_at",
        "is_started",
        "first_gap_episode",
        # The anime contract's bookkeeping: viewings aren't exported either (AN-802).
        "pass_number",
        "log_offered_pass",
        "total_is_written",
    },
)
TV_MEDIA_TYPES = frozenset(
    {MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value},
)
# TV and Season also gained like_is_independent, an existing column that movies, music,
# books and games fill. TV and Season rows keep it empty, as before, and its position
# stays where those models put it.
TV_EMPTY_COLUMNS = frozenset({"like_is_independent"})
# What the manga and comic contract added to their rows, under names books and games
# already export. Manga and comic rows keep those columns empty, as before, and the
# columns stay where books and games put them (MC-500).
SERIES_MEDIA_TYPES = frozenset({MediaTypes.MANGA.value, MediaTypes.COMIC.value})
SERIES_TRACKING_FIELD_NAMES = frozenset(
    {
        "completed_manually",
        "like_is_independent",
        "undated_read_previous_tracked",
        "undated_read_previous_status",
        "undated_read_previous_override",
        "read_override_active",
        "read_override_previous_tracked",
        "read_override_previous_status",
    },
)
# Bookkeeping only these rows have. No column at all, so the CSV keeps today's columns.
SERIES_NOT_EXPORTED = frozenset({"undated_read_previous_place", "read_override_previous_place"})
# The metadata fill's bookkeeping on Item. It isn't exported, so the CSV keeps today's
# columns.
ITEM_METADATA_FILL_FIELD_NAMES = frozenset({"metadata_version", "metadata_attempted_at"})
# A manga's or comic's total comes from its provider, so the CSV keeps today's columns.
# An anime's English title stays out too: the title column keeps the original (AN-705).
ITEM_NOT_EXPORTED = ITEM_METADATA_FILL_FIELD_NAMES | {"series_total", "display_title"}


class Echo:
    """An object that implements just the write method of the file-like interface."""

    def write(self, value):
        """Write the value by returning it, instead of storing in a buffer."""
        return value


def generate_rows(user):
    """Generate CSV rows."""
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer, quoting=csv.QUOTE_ALL)

    # Get fields
    fields = {
        "item": [
            field for field in get_model_fields(Item) if field not in ITEM_NOT_EXPORTED
        ],
        "track": get_track_fields(),
    }

    # Yield header row
    yield writer.writerow(fields["item"] + fields["track"])

    prefetch_config = {
        MediaTypes.TV.value: Prefetch(
            "seasons",
            queryset=Season.objects.select_related("item").prefetch_related(
                Prefetch(
                    "episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
            ),
        ),
        MediaTypes.SEASON.value: Prefetch(
            "episodes",
            queryset=Episode.objects.select_related("item"),
        ),
    }

    # Yield data rows
    for media_type in MediaTypes.values:
        model = apps.get_model("app", media_type)

        filter_kwargs = (
            {"related_season__user": user}
            if media_type == MediaTypes.EPISODE.value
            else {"user": user}
        )

        queryset = model.objects.filter(**filter_kwargs).select_related("item")

        if media_type in prefetch_config:
            queryset = queryset.prefetch_related(prefetch_config[media_type])

        logger.debug("Streaming %ss to CSV", media_type)

        for media in queryset.iterator(chunk_size=500):
            row = [getattr(media.item, field, "") for field in fields["item"]] + [
                track_value(media, media_type, field) for field in fields["track"]
            ]

            if media_type == MediaTypes.GAME.value:
                # calculate index of progress field
                progress_index = fields["track"].index("progress")
                row[progress_index + len(fields["item"])] = helpers.minutes_to_hhmm(
                    media.progress,
                )

            yield writer.writerow(row)

        logger.debug("Finished streaming %ss to CSV", media_type)


def track_value(media, media_type, field):
    """Return one tracking column's value for a row."""
    if media_type in SERIES_MEDIA_TYPES and field in SERIES_TRACKING_FIELD_NAMES:
        return ""
    if media_type == MediaTypes.ANIME.value:
        if field in TV_EMPTY_COLUMNS:
            return ""
        if media.rewatch_started_at is not None and field in ("status", "progress"):
            return anime_rewatch_value(media, field)
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        if field in TV_EMPTY_COLUMNS:
            return ""
        if field == "progress":
            return tv_progress(media, media_type)
    return getattr(media, field, "")


def anime_rewatch_value(media, field):
    """A rewatch in progress isn't exported, so its title re-imports as finished (AN-802)."""
    if field == "status":
        return Status.COMPLETED.value
    return media.viewings.values("episode_number").distinct().count()


def tv_progress(media, media_type):
    """Return a show's or season's progress as the contract works it out (TV-104).

    That is the stored watched count once the show has been worked out, else today's
    computed property.
    """
    if media_type == MediaTypes.TV.value:
        worked_out = not media.legacy_pending and media.reconciled_at is not None
    else:
        worked_out = media.is_started is not None
    if worked_out and media.watched_count is not None:
        return media.watched_count
    return media.progress


def get_model_fields(model):
    """Get a list of fields names from a model."""
    return [
        field.name
        for field in model._meta.get_fields()
        if isinstance(field, Field) and not field.auto_created and not field.is_relation
    ]


def get_track_fields():
    """Get a list of all track fields from all media models."""
    all_fields = []

    for media_type in MediaTypes.values:
        model = apps.get_model("app", media_type)
        for field in get_model_fields(model):
            if (
                field in TV_TRACKING_FIELD_NAMES
                or field in SERIES_NOT_EXPORTED
                or (media_type in TV_MEDIA_TYPES and field in TV_EMPTY_COLUMNS)
                or (media_type == MediaTypes.ANIME.value and field in TV_EMPTY_COLUMNS)
                or (media_type in SERIES_MEDIA_TYPES and field in SERIES_TRACKING_FIELD_NAMES)
            ):
                continue
            if field not in all_fields:
                all_fields.append(field)

    # Put start_date and end_date next to each other
    # happens because Episode has end_date but not start_date
    if "start_date" in all_fields and "end_date" in all_fields:
        end_idx = all_fields.index("end_date")

        # Remove both dates
        all_fields.remove("start_date")
        all_fields.remove("end_date")
        # Insert them in the correct order at the earlier index
        all_fields.insert(end_idx, "end_date")
        all_fields.insert(end_idx, "start_date")

    for timestamp_field in ("created_at", "progressed_at"):
        if timestamp_field in all_fields:
            all_fields.remove(timestamp_field)
            all_fields.append(timestamp_field)

    return list(all_fields)
