"""Import a MyAnimeList XML list export into Spine tracking and diary."""

import logging
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from datetime import time as dt_time

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from app import single_weight
from app.models import (
    Anime,
    DiaryEntry,
    DiaryEntryTag,
    Item,
    Manga,
    MediaTypes,
    Sources,
    Status,
    Tag,
)
from app.providers import mal
from app.services import create_diary_entry
from integrations.imports import helpers
from integrations.imports.mal_export.parser import parse_export

logger = logging.getLogger(__name__)

IMPORT_SOURCE = "myanimelist"
TRACKING_MODELS = {
    MediaTypes.ANIME.value: Anime,
    MediaTypes.MANGA.value: Manga,
}
COUNT_KEYS = (MediaTypes.ANIME.value, MediaTypes.MANGA.value, "diary", "ratings")
QUERY_CHUNK_SIZE = 500
METADATA_WORKERS = 4
# MAL's API is paced at 30 requests/minute app-wide, so the import only spends
# a short budget on posters; titles still on the placeholder afterwards keep
# their export title and are handed to the paced backfill_mal_metadata task.
METADATA_BUDGET_SECONDS = 60
METADATA_TIMEOUT_SECONDS = 20


def importer(file, user, mode):
    """Import anime and manga from a MyAnimeList XML export."""
    return MALExportImporter(file, user, mode).import_data()


def fetch_mal_metadata(media_type, media_id):
    """Fetch (cached) MAL metadata for one anime or manga."""
    if media_type == MediaTypes.ANIME.value:
        return mal.anime(media_id, timeout=METADATA_TIMEOUT_SECONDS)
    return mal.manga(media_id)


class MALExportImporter:
    """Create one tracking row per exported title plus dated completion logs."""

    def __init__(self, file, user, mode, metadata_fetcher=None):
        self.file = file
        self.user = user
        self.mode = mode
        self.metadata_fetcher = metadata_fetcher or fetch_mal_metadata
        self.warnings = []
        self.counts = Counter()

    def import_data(self):
        """Parse, enrich outside the transaction, then write atomically."""
        export = parse_export(self.file)
        self.warnings.extend(export.warnings)
        entries = export.entries

        items = self._existing_items(entries)
        if self.mode == "new":
            tracked = self._tracked_keys(entries)
            entries = [entry for entry in entries if _key(entry) not in tracked]

        metadata = self._fetch_metadata(
            [_key(entry) for entry in entries if _needs_metadata(items.get(_key(entry)))],
        )

        with transaction.atomic():
            self._ensure_items(entries, items, metadata)
            if self.mode == "overwrite":
                self._cleanup(entries, items)
            else:
                # Re-check after the (slow) metadata phase so a title tracked in
                # the meantime still ends up with exactly one row.
                tracked = self._tracked_keys(entries)
                entries = [entry for entry in entries if _key(entry) not in tracked]
            self._create_tracking(entries, items, metadata)
            self._create_diary_entries(entries, items)

        self._queue_metadata_backfill(entries, items)
        counts ={key: self.counts[key] for key in COUNT_KEYS if self.counts[key]}
        warnings = "\n".join(dict.fromkeys(self.warnings))
        return counts, warnings or None

    def _existing_items(self, entries):
        ids_by_type = _ids_by_type(entries)
        items = {}
        for media_type, media_ids in ids_by_type.items():
            for chunk in _chunks(media_ids):
                for item in Item.objects.filter(
                    source=Sources.MAL.value,
                    media_type=media_type,
                    media_id__in=chunk,
                    season_number__isnull=True,
                    episode_number__isnull=True,
                ):
                    items[(media_type, item.media_id)] = item
        return items

    def _tracked_keys(self, entries):
        tracked = set()
        for media_type, media_ids in _ids_by_type(entries).items():
            model = TRACKING_MODELS[media_type]
            for chunk in _chunks(media_ids):
                tracked.update(
                    (media_type, media_id)
                    for media_id in model.objects.filter(
                        user=self.user,
                        item__source=Sources.MAL.value,
                        item__media_id__in=chunk,
                    ).values_list("item__media_id", flat=True)
                )
        return tracked

    def _fetch_metadata(self, keys):
        """Fetch provider metadata concurrently; every failure falls back."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}

        deadline = time.monotonic() + METADATA_BUDGET_SECONDS

        def fetch(key):
            if time.monotonic() > deadline:
                return key, None
            try:
                return key, self.metadata_fetcher(*key)
            except Exception as error:  # noqa: BLE001 - metadata is best effort
                logger.warning(
                    "MyAnimeList export: metadata fetch failed for %s %s: %s",
                    *key,
                    error,
                )
                return key, None

        results = {}
        with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as executor:
            for key, data in executor.map(fetch, keys):
                if isinstance(data, dict):
                    results[key] = data

        skipped = len(keys) - len(results)
        if skipped:
            logger.info(
                "MyAnimeList export: %s of %s titles imported without provider metadata",
                skipped,
                len(keys),
            )
        return results

    def _queue_metadata_backfill(self, entries, items):
        """Hand titles still on the placeholder poster to the paced backfill task."""
        item_ids = list(
            dict.fromkeys(
                items[_key(entry)].id
                for entry in entries
                if _needs_metadata(items[_key(entry)])
            ),
        )
        if not item_ids:
            return
        from integrations import tasks  # noqa: PLC0415 - tasks imports this module

        transaction.on_commit(lambda: tasks.backfill_mal_metadata.delay(item_ids))

    def _cleanup(self, entries, items):
        """Remove prior state only for titles present in this file."""
        item_ids_by_type = defaultdict(list)
        for entry in entries:
            item = items.get(_key(entry))
            if item is not None:
                item_ids_by_type[entry.media_type].append(item.id)

        for media_type, item_ids in item_ids_by_type.items():
            model = TRACKING_MODELS[media_type]
            for chunk in _chunks(item_ids):
                model.objects.filter(user=self.user, item_id__in=chunk).delete()
                imported_entries = DiaryEntry.objects.filter(
                    user=self.user,
                    item_id__in=chunk,
                    import_source=IMPORT_SOURCE,
                )
                tag_counts = Counter(
                    DiaryEntryTag.objects.filter(
                        diary_entry__in=imported_entries,
                    ).values_list("tag_id", flat=True),
                )
                imported_entries.delete()
                for tag_id, count in tag_counts.items():
                    Tag.objects.filter(id=tag_id).update(
                        usage_count=Greatest(F("usage_count") - count, 0),
                    )

    def _ensure_items(self, entries, items, metadata):
        for entry in entries:
            key = _key(entry)
            data = metadata.get(key) or {}
            image = data.get("image") or settings.IMG_NONE
            item = items.get(key)
            if item is None:
                item, _ = Item.objects.get_or_create(
                    media_id=entry.media_id,
                    source=Sources.MAL.value,
                    media_type=entry.media_type,
                    season_number=None,
                    episode_number=None,
                    defaults={
                        "title": data.get("title") or entry.title,
                        "image": image,
                    },
                )
                items[key] = item
            elif image != settings.IMG_NONE and item.image in ("", settings.IMG_NONE):
                item.image = image
                item.save(update_fields=["image"])

    def _create_tracking(self, entries, items, metadata):
        bulk_media = defaultdict(list)
        for entry in entries:
            key = _key(entry)
            total = entry.total or _int((metadata.get(key) or {}).get("max_progress"))
            progress = entry.progress
            if entry.status == Status.COMPLETED.value and progress == 0 and total > 0:
                progress = total

            model = TRACKING_MODELS[entry.media_type]
            bulk_media[entry.media_type].append(
                model(
                    item=items[key],
                    user=self.user,
                    score=entry.score,
                    progress=progress,
                    status=entry.status,
                    start_date=_date_carrier(entry.start_date),
                    end_date=_date_carrier(entry.finish_date),
                    notes=_notes(entry),
                ),
            )
            self.counts[entry.media_type] += 1
            if entry.score is not None:
                self.counts["ratings"] += 1

        # Bulk creation skips Media.save(), so no provider calls run in the
        # transaction and history rows are still written.
        helpers.bulk_create_media(bulk_media, self.user)

    def _create_diary_entries(self, entries, items):
        today = timezone.localdate()
        candidates = [
            entry
            for entry in entries
            if entry.completed_once
            and entry.finish_date is not None
            and entry.finish_date <= today
        ]
        if not candidates:
            return

        item_ids = [items[_key(entry)].id for entry in candidates]
        existing_source_ids = set()
        existing_days = defaultdict(set)
        for chunk in _chunks(item_ids):
            for item_id, consumed_at, source, source_id in DiaryEntry.objects.filter(
                user=self.user,
                item_id__in=chunk,
            ).values_list("item_id", "consumed_at", "import_source", "import_source_id"):
                if source == IMPORT_SOURCE and source_id:
                    existing_source_ids.add(source_id)
                existing_days[item_id].update(_entry_days(consumed_at))

        for entry in candidates:
            item = items[_key(entry)]
            source_id = f"{entry.media_type}:{entry.media_id}:finish"
            if source_id in existing_source_ids:
                continue
            if entry.finish_date in existing_days[item.id]:
                # A log for that day already exists (e.g. created by hand).
                continue
            create_diary_entry(
                self.user,
                item,
                consumed_at=single_weight.calendar_datetime(entry.finish_date, today=today),
                rating=entry.score,
                review="",
                is_rewatch=False,
                tags=entry.tags,
                import_source=IMPORT_SOURCE,
                import_source_id=source_id,
                emit_activity=False,
                update_current=False,
            )
            existing_source_ids.add(source_id)
            existing_days[item.id].add(entry.finish_date)
            self.counts["diary"] += 1


def backfill_metadata(item_ids, fetcher=fetch_mal_metadata):
    """Fill in posters for MAL items still on the placeholder; return failed ids."""
    failed = []
    items = Item.objects.filter(
        id__in=item_ids,
        source=Sources.MAL.value,
        media_type__in=list(TRACKING_MODELS),
    )
    for item in items:
        if not _needs_metadata(item):
            continue
        try:
            data = fetcher(item.media_type, item.media_id)
        except Exception as error:  # noqa: BLE001 - metadata is best effort
            logger.warning(
                "MyAnimeList backfill: metadata fetch failed for %s %s: %s",
                item.media_type,
                item.media_id,
                error,
            )
            failed.append(item.id)
            continue
        image = data.get("image") if isinstance(data, dict) else None
        if image and image != settings.IMG_NONE:
            # Only the poster changes; Item's post_save hook only acts on creation.
            Item.objects.filter(
                id=item.id,
                image__in=("", settings.IMG_NONE),
            ).update(image=image)
    return failed


def _key(entry):
    return (entry.media_type, entry.media_id)


def _ids_by_type(entries):
    ids_by_type = defaultdict(list)
    for entry in entries:
        ids_by_type[entry.media_type].append(entry.media_id)
    return ids_by_type


def _needs_metadata(item):
    return item is None or not item.image or item.image == settings.IMG_NONE


def _chunks(values, size=QUERY_CHUNK_SIZE):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _date_carrier(value):
    """Store a calendar date as UTC midnight, matching diary date carriers."""
    if value is None:
        return None
    return datetime.combine(value, dt_time.min, tzinfo=UTC)


def _entry_days(consumed_at):
    if consumed_at is None:
        return set()
    if timezone.is_naive(consumed_at):
        return {consumed_at.date()}
    return {consumed_at.astimezone(UTC).date(), timezone.localdate(consumed_at)}


def _int(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _notes(entry):
    lines = ["Imported from MyAnimeList"]
    if entry.comments:
        lines.append(entry.comments)
    if entry.times_consumed > 0:
        verb = "watched" if entry.media_type == MediaTypes.ANIME.value else "read"
        lines.append(f"Times {verb}: {entry.times_consumed}")
    if entry.tags:
        lines.append(f"Tags: {', '.join(entry.tags)}")
    return "\n".join(lines)
