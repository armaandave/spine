import base64
import datetime
import hashlib
import json
import logging
from collections import defaultdict
from decimal import ROUND_HALF_UP

from cryptography.fernet import Fernet
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app
from api.services.activity_rows import log_day
from api.services.item_metadata import is_metadata_current, store_item_metadata
from app import anime_tracking, book_tracking, series_tracking, single_weight, tv_tracking, user_calendar
from app.models import DiaryEntry, MediaTypes

logger = logging.getLogger(__name__)


class MediaImportError(Exception):
    """Custom exception for import errors."""


class MediaImportUnexpectedError(Exception):
    """Custom exception for unexpected import errors."""


def store_free_metadata(item, metadata):
    """Store the metadata an import got without an extra provider call.

    It is partial, so the Item isn't stamped and no task is queued: the hourly
    metadata fill completes it. An Item already holding its provider's full record
    is left alone.
    """
    if item is not None and not is_metadata_current(item):
        store_item_metadata(item, metadata, complete=False, persist_series=False)


def tmdb_episode_metadata(season_metadata, episode_number):
    """Return one episode's air date and length from TMDB season metadata, for storing."""
    for episode in season_metadata.get("episodes") or []:
        if episode.get("episode_number") == episode_number:
            return {"release_date": episode.get("air_date"), "runtime": episode.get("runtime")}
    return {}


def get_existing_media(user):
    """Get all existing media for the user to check against during import."""
    excluded_types = [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]
    valid_types = [value for value in MediaTypes.values if value not in excluded_types]
    existing = defaultdict(lambda: defaultdict(dict))

    for media_type in valid_types:
        media_model = apps.get_model(app_label="app", model_name=media_type)

        for media in media_model.objects.filter(user=user).select_related("item"):
            existing[media_type][media.item.source].setdefault(
                media.item.media_id,
                media,
            )

    counts = [
        f"{media_type}: {sum(len(source_dict) for source_dict in media_dict.values())}"
        for media_type, media_dict in existing.items()
    ]
    logger.debug("Existing media for user %s: %s", user.username, ", ".join(counts))
    return existing


def should_process_media(existing_media, to_delete, media_type, source, media_id, mode):
    """Determine if a media item should be processed based on mode."""
    exists = media_id in existing_media[media_type][source]

    if mode == "new" and exists:
        # In "new" mode, skip if media already exists
        logger.debug(
            "Skipping existing %s: %s (mode: new)",
            media_type,
            media_id,
        )
        return False

    if mode == "overwrite" and exists:
        # In "overwrite" mode, add to the deletion list
        logger.debug(
            "Adding existing %s to deletion list: %s (mode: overwrite)",
            media_type,
            media_id,
        )
        to_delete[media_type][source].add(media_id)

    return True


def cleanup_existing_media(to_delete, user):
    """Delete existing media if in overwrite mode."""
    for media_type, sources in to_delete.items():
        if not sources:
            continue

        model = apps.get_model(app_label="app", model_name=media_type)
        total_deleted = 0

        # Single-weight imports reconcile through their domain service. Deleting
        # tracking here could violate the universal diary-history guard and is
        # unnecessary because canonical tracking is unique per user/title.
        if single_weight.supports(media_type) or media_type == MediaTypes.GAME.value:
            continue

        if series_tracking.supports(media_type):
            for source, media_ids in sources.items():
                series_tracking.clear_for_import(user, media_type, source, media_ids)
            continue

        if anime_tracking.supports(media_type):
            for source, media_ids in sources.items():
                anime_tracking.clear_for_import(user, source, media_ids)
            continue

        for source, media_ids in sources.items():
            if not media_ids:
                continue

            rows = model.objects.filter(
                item__media_id__in=media_ids,
                item__source=source,
                user=user,
            )
            if media_type == MediaTypes.TV.value:
                # Overwrite replaces these shows' viewings, marks and choices; their
                # logs survive and get their viewings back after the import (TV-800).
                _remember_overwritten_shows(user, rows.values_list("item_id", flat=True))
            deleted_count, _ = rows.delete()
            total_deleted += deleted_count

        if total_deleted > 0:
            logger.info(
                "Deleted %s %s objects for user %s in overwrite mode",
                total_deleted,
                media_type,
                user,
            )


def update_season_references(seasons, user):
    """Update season references with actual TV instances.

    When bulk_create skips existing TV shows, seasons would still reference
    the unsaved TV instances. This updates those references to point to
    the existing TV shows in the database, preventing the ValueError about
    unsaved related objects during bulk creation of seasons.
    """
    # Get existing TV shows from database
    existing_tv = {
        tv.item.media_id: tv
        for tv in app.models.TV.objects.filter(
            user=user,
            item__media_id__in=[season.item.media_id for season in seasons],
        )
    }

    # Update references
    for season in seasons:
        media_id = season.item.media_id
        if media_id in existing_tv:
            season.related_tv = existing_tv[media_id]
            logger.debug(
                "Updated new season %s with existing TV %s",
                season,
                existing_tv[media_id],
            )


def update_episode_references(episodes, user):
    """Update episode references with actual Season instances.

    When bulk_create skips existing seasons, episodes would still reference
    the unsaved season instances. This updates those references to point to
    the existing seasons in the database, preventing the ValueError about
    unsaved related objects during bulk creation of episodes.
    """
    # Create mapping of season instances
    existing_seasons = {
        (season.item.media_id, season.item.season_number): season
        for season in app.models.Season.objects.filter(
            user=user,
            item__media_id__in={episode.item.media_id for episode in episodes},
        )
    }

    # Update references
    for episode in episodes:
        season_key = (
            episode.item.media_id,
            episode.item.season_number,
        )
        if season_key in existing_seasons:
            episode.related_season = existing_seasons[season_key]
            logger.debug(
                "Updated new episode %s with existing season %s",
                episode,
                existing_seasons[season_key],
            )


def _remember_overwritten_shows(user, show_item_ids):
    """Record shows overwrite mode deleted, for reconcile_imported (TV-800).

    The importers call cleanup_existing_media and then bulk_create_media with the same
    user object, so the record travels on it.
    """
    overwritten = getattr(user, "_tv_overwritten_show_item_ids", set())
    overwritten.update(show_item_ids)
    user._tv_overwritten_show_item_ids = overwritten  # noqa: SLF001


def _imported_show_items(bulk_media_list):
    """Return the tv Items of every show the import wrote rows for."""
    keys = set()
    for media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
        for row in bulk_media_list.get(media_type) or ():
            keys.add((row.item.media_id, row.item.source))
    if not keys:
        return []
    shows = app.models.Item.objects.filter(
        media_type=MediaTypes.TV.value,
        media_id__in={media_id for media_id, _ in keys},
        source__in={source for _, source in keys},
    )
    return [show for show in shows if (show.media_id, show.source) in keys]


def reconcile_imported_tv(bulk_media_list, user):
    """Work out each imported show once, so it ends where the app would (TV-800)."""
    overwritten = getattr(user, "_tv_overwritten_show_item_ids", set())
    if hasattr(user, "_tv_overwritten_show_item_ids"):
        del user._tv_overwritten_show_item_ids  # noqa: SLF001
    show_items = _imported_show_items(bulk_media_list)
    if not show_items:
        return
    tv_tracking.reconcile_imported(
        user,
        show_items,
        overwritten_show_item_ids=sorted(overwritten),
    )


def bulk_create_media(bulk_media_list, user, *, source="", warnings=None):
    """Bulk create all media objects; `source` names the import on any logs it writes.

    What a row couldn't keep as it stood goes on `warnings`, when the import has a list.
    """
    _bulk_create_media(bulk_media_list, user, source, [] if warnings is None else warnings)
    reconcile_imported_tv(bulk_media_list, user)


def imported_counts(bulk_media_list):
    """Count what an import brought in: a title once, however many reads it has (MC-400, AN-800)."""
    return {
        media_type: (
            len({row.item_id for row in rows})
            if series_tracking.supports(media_type) or anime_tracking.supports(media_type)
            else len(rows)
        )
        for media_type, rows in bulk_media_list.items()
    }


def _bulk_create_media(bulk_media_list, user, source, warnings):
    """Bulk create each media type's rows as the importer built them."""
    for media_type, bulk_media in bulk_media_list.items():
        if not bulk_media:
            continue

        model = apps.get_model(app_label="app", model_name=media_type)

        logger.info("Bulk importing %s", media_type)

        if single_weight.supports(media_type):
            _import_single_weight_rows(bulk_media, user, model)
            continue

        if media_type == MediaTypes.GAME.value:
            _import_game_rows(bulk_media, user)
            continue

        if media_type == MediaTypes.BOOK.value:
            bulk_media = _import_book_rows(bulk_media, user, source, warnings)

        if series_tracking.supports(media_type):
            _import_series_rows(bulk_media, user, source)
            continue

        if anime_tracking.supports(media_type):
            _import_anime_rows(bulk_media, user)
            continue

        # Update references for seasons and episodes
        if media_type == MediaTypes.SEASON.value:
            logger.info("Updating references for season to existing TV shows")
            update_season_references(bulk_media, user)
        elif media_type == MediaTypes.EPISODE.value:
            logger.info(
                "Updating references for episodes to existing TV seasons",
            )
            update_episode_references(bulk_media, user)

        bulk_create_with_history(
            bulk_media,
            model,
            batch_size=500,
            default_user=user,
        )


def _import_single_weight_rows(rows, user, model):
    """Collapse legacy title imports into canonical undated tracking."""
    rows_by_item = defaultdict(list)
    for row in rows:
        rows_by_item[row.item_id].append(row)
    for item_rows in rows_by_item.values():
        latest = item_rows[-1]
        completed = any(row.status == app.models.Status.COMPLETED.value for row in item_rows)
        rating = next((row.score for row in reversed(item_rows) if row.score is not None), single_weight.UNSET)
        if completed or rating is not single_weight.UNSET:
            tracking = single_weight.import_title_state(
                user,
                latest.item,
                consumed=completed,
                rating=rating,
                history_date=getattr(latest, "_history_date", None),
            )
        else:
            tracking, _ = model.objects.get_or_create(
                user=user,
                item=latest.item,
                defaults={"status": latest.status},
            )
        notes = next((row.notes for row in reversed(item_rows) if row.notes), "")
        if notes and tracking.notes != notes:
            tracking.notes = notes
            tracking.save(update_fields=["notes"])


def book_log_on(user, item, day):
    """Return this title's log on a calendar day, or None (SW-504).

    It reads a log's day as the calculation does: a UTC-midnight carrier as stored, and an
    old log's real moment on the owner's calendar. Read any other way, the evening's log
    is on another day, and a file comes back as a second read.
    """
    zone = user_calendar.calendar_tz(user)
    for entry in DiaryEntry.objects.filter(user=user, item=item):
        if log_day(entry.consumed_at, zone) == day:
            return entry
    return None


def _log_rating(row, warnings):
    """Return a file's score as a log's rating, which holds only half stars.

    One off them rounds to the nearest, as a bad row is reported rather than stopping the
    import (docs/media-imports.md gives 8.5 as an example).
    """
    if row.score in (None, 0) or row.score in single_weight.STORAGE_RATINGS:
        return row.score
    rounded = max(row.score.to_integral_value(rounding=ROUND_HALF_UP), 1)
    warnings.append(f"{row.item.title}: a score of {row.score} isn't a half star, so its log holds {rounded}")
    return rounded


def _import_book_rows(rows, user, source, warnings):
    """Log the dated reads of a file's book rows, and return the rows left to create.

    A dated Read lands on its date as a log, unless the title has a log that day: that's
    your own file coming back (SW-504). A Read with no date, or a row already marked, is
    the undated mark, whatever date it holds: eye taps from before the book contract can
    still hold one. A Read with no date on a title with a log isn't: the eye over a log
    writes that row, and the log already says it was read.
    """
    plain = []
    for row in rows:
        day = _read_day(row, user)
        if row.status != app.models.Status.COMPLETED.value or row.completed_manually or day is None:
            if row.status == app.models.Status.COMPLETED.value and (
                row.completed_manually or not DiaryEntry.objects.filter(user=user, item=row.item).exists()
            ):
                row.completed_manually = True
                row.undated_read_previous_tracked = True
            plain.append(row)
            continue
        if book_log_on(user, row.item, day):
            plain.append(row)
            continue
        _, entry = book_tracking.complete(
            user,
            row.item,
            completion_date=day,
            rating=_log_rating(row, warnings),
            import_source=source,
            import_source_id=f"{row.item.source}:{row.item.media_id}:{day.isoformat()}",
            emit_activity=False,
            force_direct=True,
        )
        start = row.start_date and user_calendar.day_of(row.start_date, user)
        if start and start <= day:
            book_tracking.update_journey(user, row.item, entry.book_journey.id, start_date=start)
        if row.notes:
            app.models.Book.objects.filter(user=user, item=row.item).update(notes=row.notes)
    return plain


def _read_day(row, user):
    """Return the day a file's row says it was read; a day still to come isn't one."""
    if row.end_date is None:
        return None
    moment = row.end_date
    day = moment.date() if single_weight.is_calendar_datetime(moment) else user_calendar.day_of(moment, user)
    return day if day <= user_calendar.today(user) else None


def _import_game_rows(rows, user):
    """Reconcile explicit imported game states without deleting playthroughs."""
    from app import game_tracking

    # Multiple rows for one title describe current title state, not new attempts.
    rows_by_item = {row.item_id: row for row in rows}
    for row in rows_by_item.values():
        game_tracking.import_title_state(
            user,
            row.item,
            status=row.status,
            rating=row.score if row.score is not None else game_tracking.UNSET,
            progress=row.progress,
            start_date=row.start_date,
            end_date=row.end_date,
            notes=row.notes,
            history_date=getattr(row, "_history_date", None),
            imported_lifetime_minutes=row.imported_lifetime_minutes,
            imported_lifetime_source=row.imported_lifetime_source,
        )


def _import_series_rows(rows, user, source):
    """Keep one tracking record per series: a title's earlier rows are its reads (MC-400)."""
    rows_by_item = defaultdict(list)
    for row in rows:
        rows_by_item[row.item_id].append(row)
    for item_rows in rows_by_item.values():
        # Old Yamtrack exports list a title's newest row first. Rows with no date stay
        # in file order, ahead of those with one.
        item_rows.sort(
            key=lambda row: getattr(row, "_history_date", None) or datetime.datetime.min.replace(tzinfo=datetime.UTC),
        )
        *earlier, current = item_rows
        series_tracking.import_title(
            user,
            current.item,
            status=current.status,
            place=current.progress,
            score=current.score,
            start_date=current.start_date,
            end_date=current.end_date,
            notes=current.notes,
            earlier=[
                {
                    "status": row.status,
                    "place": row.progress,
                    "score": row.score,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                }
                for row in earlier
            ],
            history_date=getattr(current, "_history_date", None),
            import_source=source,
            import_source_id=getattr(current, "_import_source_id", ""),
            tags=getattr(current, "_import_tags", None),
        )


def _import_anime_rows(rows, user):
    """Keep one tracking record per title: a title's earlier rows are earlier passes (AN-800)."""
    rows_by_item = defaultdict(list)
    for row in rows:
        rows_by_item[row.item_id].append(row)
    for item_rows in rows_by_item.values():
        # Old Yamtrack exports list a title's newest row first. Rows with no date stay
        # in file order, ahead of those with one.
        item_rows.sort(
            key=lambda row: getattr(row, "_history_date", None) or datetime.datetime.min.replace(tzinfo=datetime.UTC),
        )
        *earlier, current = item_rows
        anime_tracking.import_title(
            user,
            current.item,
            status=current.status,
            count=current.progress,
            score=next((row.score for row in reversed(item_rows) if row.score), None),
            notes=next((row.notes for row in reversed(item_rows) if row.notes), ""),
            earlier=[{"count": row.progress} for row in earlier if row.status == app.models.Status.COMPLETED.value],
            total=next((getattr(row, "_anime_total", None) for row in item_rows if getattr(row, "_anime_total", None)), None),
            start_date=current.start_date,
            end_date=current.end_date,
            history_date=getattr(current, "_history_date", None),
        )


def bulk_update_media(bulk_media_list, fields_by_media_type, user):
    """Bulk update media objects with history tracking."""
    for media_type, bulk_media in bulk_media_list.items():
        if not bulk_media:
            continue

        fields = fields_by_media_type.get(media_type)
        if not fields:
            logger.warning(
                "Skipping bulk update for %s without configured fields",
                media_type,
            )
            continue

        model = apps.get_model(app_label="app", model_name=media_type)

        logger.info("Bulk updating %s", media_type)
        bulk_update_with_history(
            bulk_media,
            model,
            fields=fields,
            batch_size=500,
            default_user=user,
        )


def create_import_schedule(
    username,
    request,
    mode,
    frequency,
    import_time,
    source,
    token=None,
    task_kwargs=None,
):
    """Create an import schedule."""
    try:
        import_time = (
            datetime.datetime.strptime(import_time, "%H:%M")
            .astimezone(
                timezone.get_default_timezone(),
            )
            .time()
        )
    except ValueError:
        messages.error(request, "Invalid import time.")
        return

    task_name = f"Import from {source} for {username} at {import_time} {frequency}"
    if PeriodicTask.objects.filter(name=task_name).exists():
        messages.error(
            request,
            "The same import task is already scheduled.",
        )
        return

    crontab, _ = CrontabSchedule.objects.get_or_create(
        hour=import_time.hour,
        minute=import_time.minute,
        day_of_week="*" if frequency == "daily" else "*/2",
        timezone=timezone.get_default_timezone(),
    )

    kwargs = {
        "username": username,
        "user_id": request.user.id,
        "mode": mode,
    }

    if token:
        kwargs["token"] = token
    if task_kwargs:
        kwargs.update(task_kwargs)

    # Create new periodic task
    PeriodicTask.objects.create(
        name=task_name,
        task=f"Import from {source}",
        crontab=crontab,
        kwargs=json.dumps(kwargs),
        start_time=timezone.now(),
    )
    messages.success(request, f"{source} import task scheduled.")


def join_with_commas_and(items):
    """Join a list of items with commas and 'and'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def fernet():
    """Derive a stable 32-byte key from Django's SECRET_KEY.

    Uses SHA-256 then urlsafe_b64encode to satisfy Fernet.
    """
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    """Return url-safe encrypted string."""
    return fernet().encrypt(value.encode()).decode()


def decrypt(token):
    """Decrypt value that was encrypted with `encrypt`."""
    return fernet().decrypt(token.encode()).decode()
