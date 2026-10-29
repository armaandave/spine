"""Import a Margins CSV export into Spine books, reading journeys and diary logs."""

from collections import Counter, defaultdict
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from app import book_tracking
from app.models import (
    Book,
    DiaryEntry,
    DiaryEntryTag,
    Item,
    MediaLike,
    MediaTypes,
    Status,
    Tag,
)
from app.tasks import update_daily_statistics
from integrations.imports import helpers
from integrations.imports.margins.parser import (
    READ,
    READING,
    STOPPED,
    TO_READ,
    parse_export,
)
from integrations.imports.margins.resolver import MarginsResolver

IMPORT_SOURCE = "margins"
DATED_LABELS = {READ: "Read", STOPPED: "Stopped", READING: "Reading since"}
UNDATED_LABELS = {READ: "Read, no date", STOPPED: "Stopped", READING: "Reading", TO_READ: "To Read"}


def importer(file, user, mode):
    """Import books from a Margins CSV export."""
    return MarginsImporter(file, user, mode).import_data()


class MarginsImporter:
    """Import Margins rows, one per read or shelved book, into Spine books."""

    def __init__(self, file, user, mode, resolver=None):
        self.file = file
        self.user = user
        self.mode = mode
        self.resolver = resolver or MarginsResolver()
        self.warnings = []
        self.counts = Counter()

    def import_data(self):
        rows = parse_export(self.file)
        known = []
        for row in rows:
            if row.state:
                known.append(row)
            else:
                self.warnings.append(f"{_label(row)}: Unknown Margins status {row.status!r}.")
        resolved = self.resolver.resolve_rows(known)

        rows_by_book = defaultdict(list)
        results = {}
        for row in known:
            result = resolved.get(row.index)
            if result is None:
                reason = "No matching book found" if row.authors else "No author in the Margins file"
                self.warnings.append(f"{_label(row)}: {_summary(row)}. {reason}.")
                continue
            key = (result["source"], str(result["media_id"]))
            results.setdefault(key, result)
            rows_by_book[key].append(row)

        with transaction.atomic():
            for key, book_rows in rows_by_book.items():
                item = self._get_or_create_item(results[key])
                counts = self.counts.copy()
                try:
                    # One book breaking a reading rule is reported, not the whole import.
                    with transaction.atomic():
                        self._import_book(item, book_rows)
                except (ValidationError, book_tracking.BookTrackingConflict) as error:
                    self.counts = counts
                    message = getattr(error, "message", None) or "; ".join(error.messages)
                    self.warnings.append(f"{_label(book_rows[0])}: {_summary(book_rows[0])}. {message}")

        warnings = "\n".join(dict.fromkeys(self.warnings))
        return {key: count for key, count in self.counts.items() if count}, warnings or None

    def _get_or_create_item(self, result):
        item = Item.objects.get_or_create(
            media_id=str(result["media_id"]),
            source=result["source"],
            media_type=MediaTypes.BOOK.value,
            defaults={
                "title": result.get("title") or str(result["media_id"]),
                "image": result.get("image") or settings.IMG_NONE,
            },
        )[0]
        # Only the search result's first publication year; the metadata fill
        # completes the Item later.
        helpers.store_free_metadata(item, {"first_publish_year": result.get("first_publish_year")})
        return item

    def _import_book(self, item, rows):
        if Book.objects.filter(user=self.user, item=item).exists():
            if self.mode != "overwrite":
                return
            self._clear_book(item)
        self.counts[MediaTypes.BOOK.value] += 1

        if any(row.state == READ and row.end_date is None for row in rows):
            book_tracking.mark_read(self.user, item)
        # Undated reads count as earlier; later reads and DNFs follow by date.
        past = sorted(
            (row for row in rows if row.state == STOPPED or (row.state == READ and row.end_date)),
            key=lambda row: (row.end_date or row.start_date or date.min, row.index),
        )
        logged_days = set()
        for row in past:
            if row.state == READ:
                # A book has one log per finish day; a second row for that day is a duplicate.
                if row.end_date not in logged_days:
                    logged_days.add(row.end_date)
                    self._log_read(item, row)
            else:
                book_tracking.import_journey(
                    self.user,
                    item,
                    status=Status.DROPPED.value,
                    start_date=_start(row),
                    end_date=row.end_date,
                )
        reading = next((row for row in rows if row.state == READING), None)
        if reading:
            book_tracking.import_journey(
                self.user,
                item,
                status=Status.IN_PROGRESS.value,
                start_date=reading.start_date or timezone.localdate(),
            )
        if all(row.state == TO_READ for row in rows):
            book_tracking.assign_status(self.user, item, Status.PLANNING.value)
        self._keep_unlogged(item, rows)

    def _log_read(self, item, row):
        _, entry = book_tracking.complete(
            self.user,
            item,
            completion_date=row.end_date,
            rating=row.rating,
            review=row.review,
            import_source=IMPORT_SOURCE,
            import_source_id=f"{item.source}:{item.media_id}:{row.end_date.isoformat()}",
            emit_activity=False,
        )
        if _start(row):
            book_tracking.update_journey(self.user, item, entry.book_journey.id, start_date=row.start_date)
        self.counts["diary"] += 1
        self.counts["ratings"] += int(row.rating is not None)
        self.counts["reviews"] += int(bool(row.review))

    def _keep_unlogged(self, item, rows):
        """Keep a rating and reviews no log could take: undated reads, DNFs, shelves."""
        unlogged = [row for row in rows if not (row.state == READ and row.end_date)]
        rated = [row for row in unlogged if row.rating is not None]
        book = Book.objects.get(user=self.user, item=item)
        if rated and book.score is None:
            if book.status == Status.COMPLETED.value:
                book_tracking.set_rating(self.user, item, rated[-1].rating)
                self.counts["ratings"] += 1
            else:
                self.warnings.append(
                    f"{_label(rated[-1])}: {_summary(rated[-1])}. "
                    "Imported without its rating: Spine only rates books you've read.",
                )
        reviews = [row.review for row in unlogged if row.review]
        if reviews:
            Book.objects.filter(id=book.id).update(notes="\n\n".join(reviews))

    def _clear_book(self, item):
        """Delete what Spine has for this book so the file's version replaces it."""
        from social.models import Activity, ProgressChange  # noqa: PLC0415

        entries = DiaryEntry.objects.filter(user=self.user, item=item)
        days = {consumed_at.date() for consumed_at in entries.values_list("consumed_at", flat=True)}
        tag_counts = Counter(
            DiaryEntryTag.objects.filter(diary_entry__in=entries).values_list("tag_id", flat=True),
        )
        entries.delete()
        for tag_id, count in tag_counts.items():
            Tag.objects.filter(id=tag_id).update(usage_count=Greatest(F("usage_count") - count, 0))
        Activity.objects.filter(actor=self.user, item=item).delete()
        ProgressChange.objects.filter(actor=self.user, item=item).delete()
        MediaLike.objects.filter(user=self.user, item=item).delete()
        Book.objects.filter(user=self.user, item=item).delete()
        for day in days:
            transaction.on_commit(
                lambda day=day: update_daily_statistics.delay(
                    user_id=self.user.id,
                    date_str=day.isoformat(),
                ),
            )


def _start(row):
    """Return the start date when it fits before the end date."""
    if row.start_date and (row.end_date is None or row.start_date <= row.end_date):
        return row.start_date
    return None


def _label(row):
    title = row.title or "Untitled book"
    return f"{title} by {row.author}" if row.author else title


def _summary(row):
    """Say what the book was in Margins, so it can be added by hand."""
    when = row.start_date if row.state == READING else row.end_date
    if row.state in DATED_LABELS and when:
        parts = [f"{DATED_LABELS[row.state]} {when:%b} {when.day}, {when.year}"]
    else:
        parts = [UNDATED_LABELS[row.state]]
    if row.rating is not None:
        parts.append(f"{(row.rating / 2).normalize()}★")
    if row.review:
        parts.append("with a review")
    return ", ".join(parts)
