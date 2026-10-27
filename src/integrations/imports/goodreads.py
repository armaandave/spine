import csv
import io
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import Book, DiaryEntry, Item, MediaTypes, Sources, Status
from app.providers import hardcover, openlibrary, services
from app.providers.search_rank import normalize_search_text
from app.services import create_diary_entry
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"Title", "Exclusive Shelf"}
STATUS_MAP = {
    "read": Status.COMPLETED.value,
    "currently-reading": Status.IN_PROGRESS.value,
    "to-read": Status.PLANNING.value,
    "paused": Status.PAUSED.value,
    "on-hold": Status.PAUSED.value,
    "dnf": Status.DROPPED.value,
    "did-not-finish": Status.DROPPED.value,
    "unfinished": Status.DROPPED.value,
    "abandoned": Status.DROPPED.value,
    "dropped": Status.DROPPED.value,
}
ISBN_RE = re.compile(r"[\s=\"'-]+")


@dataclass
class GoodreadsRow:
    index: int
    raw: dict
    book_id: str
    title: str
    author: str
    additional_authors: str
    isbn10_raw: str
    isbn13_raw: str
    isbn10: str
    isbn13: str
    exclusive_shelf: str
    status: str
    status_was_fallback: bool
    rating: Decimal | None
    publisher: str
    binding: str
    number_of_pages: int | None
    year_published: int | None
    original_publication_year: int | None
    date_read: datetime | None
    date_added: datetime | None
    bookshelves: list[str] = field(default_factory=list)
    review: str = ""
    contains_spoilers: bool = False
    private_notes: str = ""
    read_count: int = 0
    owned_copies: int = 0

    @property
    def isbn(self):
        """Return the preferred normalized ISBN for compatibility."""
        return self.isbn13 or self.isbn10

    @property
    def identifiers(self):
        """Return ISBN-13 then ISBN-10, deriving an equivalent form when possible."""
        isbn13 = self.isbn13 or _isbn10_to_isbn13(self.isbn10)
        isbn10 = self.isbn10 or _isbn13_to_isbn10(self.isbn13)
        return list(dict.fromkeys(value for value in (isbn13, isbn10) if value))

    @property
    def can_create_diary(self):
        return self.status == Status.COMPLETED.value and self.date_read is not None


def importer(file, user, mode):
    """Import books from a Goodreads library CSV export."""
    return GoodReadsImporter(file, user, mode).import_data()


def parse_export(file_or_bytes):
    """Parse current and legacy Goodreads library CSV exports by header name."""
    payload = file_or_bytes.read() if hasattr(file_or_bytes, "read") else file_or_bytes
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = payload.decode("utf-8-sig")
        except (AttributeError, UnicodeDecodeError) as error:
            msg = "Invalid file format. Please upload a Goodreads CSV export."
            raise MediaImportError(msg) from error

    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = {str(name).strip() for name in (reader.fieldnames or []) if name}
    missing = REQUIRED_COLUMNS - fieldnames
    if missing:
        msg = f"Unsupported Goodreads CSV format. Missing columns: {', '.join(sorted(missing))}."
        raise MediaImportError(msg)

    return [_normalize_row(index, row) for index, row in enumerate(reader, start=1)]


def _normalize_row(index, row):
    cleaned = {str(key).strip(): str(value or "").strip() for key, value in row.items() if key}
    exclusive_shelf = cleaned.get("Exclusive Shelf", "").casefold()
    status = STATUS_MAP.get(exclusive_shelf, Status.PLANNING.value)
    return GoodreadsRow(
        index=index,
        raw=cleaned,
        book_id=cleaned.get("Book Id", ""),
        title=cleaned.get("Title", ""),
        author=cleaned.get("Author", ""),
        additional_authors=cleaned.get("Additional Authors", ""),
        isbn10_raw=cleaned.get("ISBN", ""),
        isbn13_raw=cleaned.get("ISBN13", ""),
        isbn10=_isbn(cleaned.get("ISBN", ""), length=10),
        isbn13=_isbn(cleaned.get("ISBN13", ""), length=13),
        exclusive_shelf=exclusive_shelf,
        status=status,
        status_was_fallback=exclusive_shelf not in STATUS_MAP,
        rating=_rating(cleaned.get("My Rating")),
        publisher=cleaned.get("Publisher", ""),
        binding=cleaned.get("Binding", ""),
        number_of_pages=_positive_int(cleaned.get("Number of Pages")),
        year_published=_positive_int(cleaned.get("Year Published")),
        original_publication_year=_positive_int(cleaned.get("Original Publication Year")),
        date_read=_date(cleaned.get("Date Read")),
        date_added=_date(cleaned.get("Date Added")),
        bookshelves=_bookshelves(cleaned.get("Bookshelves", ""), exclusive_shelf),
        review=cleaned.get("My Review", ""),
        contains_spoilers=_bool(cleaned.get("Spoiler")),
        private_notes=cleaned.get("Private Notes", ""),
        read_count=_positive_int(cleaned.get("Read Count")) or 0,
        owned_copies=_positive_int(cleaned.get("Owned Copies")) or 0,
    )


class GoodreadsResolver:
    """Resolve Goodreads rows to exact or high-confidence Spine book results."""

    SOURCES = (Sources.HARDCOVER.value, Sources.OPENLIBRARY.value)

    def __init__(self):
        self._exact_cache = {}
        self._metadata_cache = {}

    def resolve_rows(self, rows):
        resolved = {}
        for row in rows:
            result = self.resolve_one(row)
            if result:
                resolved[row.index] = result
        return resolved

    def resolve_one(self, row):
        for source in self.SOURCES:
            for identifier in row.identifiers:
                result = self._exact_lookup(identifier, source)
                if result:
                    result.setdefault("source", source)
                    return result

        return self._metadata_lookup(row)

    def _exact_lookup(self, identifier, source):
        key = (source, identifier)
        if key in self._exact_cache:
            return self._exact_cache[key]

        lookup = (
            hardcover.lookup_book_by_isbn
            if source == Sources.HARDCOVER.value
            else openlibrary.lookup_book_by_isbn
        )
        try:
            result = lookup(identifier)
        except services.ProviderAPIError as error:
            logger.warning("Goodreads %s ISBN lookup failed for %s: %s", source, identifier, error)
            result = None
        self._exact_cache[key] = result
        return result

    def _metadata_lookup(self, row):
        query = " ".join(part for part in (row.title, row.author) if part).strip()
        if not query:
            return None

        key = (
            normalize_search_text(row.title),
            normalize_search_text(row.author),
            row.original_publication_year or row.year_published,
        )
        if key in self._metadata_cache:
            return self._metadata_cache[key]

        for source in self.SOURCES:
            try:
                results = services.search(
                    MediaTypes.BOOK.value,
                    query,
                    1,
                    source,
                    preserve_ranking_fields=True,
                ).get("results", [])
            except services.ProviderAPIError as error:
                logger.warning("Goodreads %s metadata lookup failed for %s: %s", source, query, error)
                continue

            result = _best_metadata_match(row, results)
            if result:
                result.setdefault("source", source)
                self._metadata_cache[key] = result
                return result

        self._metadata_cache[key] = None
        return None


class GoodReadsImporter:
    """Import Goodreads library rows into Spine books and diary entries."""

    def __init__(self, file, user, mode, resolver=None):
        self.file = file
        self.user = user
        self.mode = mode
        self.resolver = resolver or GoodreadsResolver()
        self.warnings = []
        self.counts = defaultdict(int)

    def import_data(self):
        rows = parse_export(self.file)
        resolved = self.resolver.resolve_rows(rows)

        with transaction.atomic():
            if self.mode == "overwrite":
                self._cleanup()
            for row in rows:
                self._import_row(row, resolved.get(row.index))

        warnings = "\n".join(dict.fromkeys(self.warnings))
        return dict(self.counts), warnings or None

    def _cleanup(self):
        DiaryEntry.objects.filter(
            user=self.user,
            item__media_type=MediaTypes.BOOK.value,
        ).delete()
        Book.objects.filter(user=self.user).delete()

    def _import_row(self, row, result):
        label = row.title or f"Goodreads row {row.index}"
        if row.status_was_fallback:
            self.warnings.append(
                f"{label}: Unknown Goodreads shelf {row.exclusive_shelf!r}; imported as Planning",
            )
        if not result:
            goodreads_id = f" (Goodreads ID {row.book_id})" if row.book_id else ""
            self.warnings.append(
                f"{label}{goodreads_id}: Couldn't resolve Goodreads book",
            )
            return

        item = self._get_or_create_item(row, result)
        book = Book.objects.filter(user=self.user, item=item).first()
        created = False
        if book is None:
            book = self._create_book(item, row)
            created = True
            self.counts[MediaTypes.BOOK.value] += 1

        latest_entry, diary_created = self._import_diary_entry(
            item,
            row,
            update_current=created,
        )
        if row.rating is not None and (created or diary_created):
            self.counts["ratings"] += 1
        if row.review and (created or diary_created):
            self.counts["reviews"] += 1
        book.refresh_from_db()
        if (
            latest_entry
            and (created or book.completion_diary_entry_id is None)
            and book.completion_diary_entry_id != latest_entry.id
        ):
            book.completion_diary_entry = latest_entry
            book.save(update_fields=["completion_diary_entry"])

    def _get_or_create_item(self, row, result):
        source = result.get("source") or Sources.HARDCOVER.value
        title = result.get("title") or row.title or str(result["media_id"])
        image = result.get("image") or settings.IMG_NONE
        total_pages = result.get("max_progress") or result.get("total_pages") or row.number_of_pages
        item, _ = Item.objects.get_or_create(
            media_id=str(result["media_id"]),
            source=source,
            media_type=MediaTypes.BOOK.value,
            defaults={
                "title": title,
                "image": image,
                "total_pages": total_pages,
            },
        )

        update_fields = []
        if title and item.title != title:
            item.title = title
            update_fields.append("title")
        if image and item.image != image:
            item.image = image
            update_fields.append("image")
        if total_pages and item.total_pages is None:
            item.total_pages = total_pages
            update_fields.append("total_pages")
        if update_fields:
            item.save(update_fields=update_fields)
        # Only the first publication year, which the export or search result carries;
        # the metadata fill completes the Item later.
        helpers.store_free_metadata(
            item,
            {"first_publish_year": row.original_publication_year or result.get("first_publish_year")},
        )
        return item

    def _create_book(self, item, row):
        history_date = row.date_read or row.date_added
        book = Book(
            user=self.user,
            item=item,
            status=row.status,
            progress=0,
            end_date=row.date_read if row.status == Status.COMPLETED.value else None,
            score=row.rating,
            notes=self._notes(row),
            completed_manually=False,
        )
        if history_date:
            book._history_date = history_date
        book.save()

        # Completing a Book creates a BookSession whose default end date is now.
        # Preserve the export's completion date in both records after that hook runs.
        if row.status == Status.COMPLETED.value and row.date_read:
            book.reading_sessions.filter(status=Status.COMPLETED.value).update(
                end_date=row.date_read,
            )
            book.end_date = row.date_read
            book._history_date = row.date_read
            book.save(update_fields=["end_date"])
        return book

    def _import_diary_entry(self, item, row, *, update_current):
        if not row.can_create_diary:
            if row.review:
                self.warnings.append(
                    f"{row.title or 'Goodreads row'}: Review was not imported because Date Read is missing",
                )
            return None, False

        existing = DiaryEntry.objects.filter(
            user=self.user,
            item=item,
            consumed_at__date=row.date_read.date(),
        ).first()
        if existing:
            return existing, False

        entry = create_diary_entry(
            self.user,
            item,
            consumed_at=row.date_read,
            rating=row.rating,
            review=row.review,
            is_rewatch=row.read_count > 1,
            tags=row.bookshelves,
            update_current=update_current,
        )
        if row.contains_spoilers:
            entry.contains_spoilers = True
            entry.save(update_fields=["contains_spoilers", "updated_at"])
        self.counts["diary"] += 1
        return entry, True

    def _notes(self, row):
        lines = ["Imported from Goodreads"]
        mapping = [
            ("Goodreads ID", row.book_id),
            ("Author", row.author),
            ("Additional authors", row.additional_authors),
            ("ISBN", row.isbn10_raw),
            ("ISBN13", row.isbn13_raw),
            ("Binding", row.binding),
            ("Publisher", row.publisher),
            ("Number of pages", str(row.number_of_pages) if row.number_of_pages else ""),
            ("Year published", str(row.year_published) if row.year_published else ""),
            (
                "Original publication year",
                str(row.original_publication_year) if row.original_publication_year else "",
            ),
            ("Date added", _date_text(row.date_added)),
            ("Date read", _date_text(row.date_read)),
            ("Exclusive shelf", row.exclusive_shelf),
            ("Bookshelves", ", ".join(row.bookshelves)),
            ("Read count", str(row.read_count) if row.read_count else ""),
            ("Owned copies", str(row.owned_copies) if row.owned_copies else ""),
            ("Private notes", row.private_notes),
            ("Review", row.review if not row.can_create_diary else ""),
        ]
        for label, value in mapping:
            if value:
                lines.append(f"{label}: {value}")

        dated_reads = 1 if row.date_read else 0
        undated_reads = max(0, row.read_count - dated_reads)
        if undated_reads:
            lines.append(f"Undated reads: {undated_reads}")
        return "\n".join(lines)


def _best_metadata_match(row, results):
    matches = []
    seen = set()
    expected_titles = set(_title_variants(row.title))
    expected_authors = _author_variants(row.author)
    expected_years = {
        year
        for year in (row.original_publication_year, row.year_published)
        if year
    }
    for result in results:
        media_id = str(result.get("media_id") or "")
        if not media_id or media_id in seen:
            continue
        seen.add(media_id)

        if not expected_titles.intersection(_title_variants(result.get("title"))):
            continue

        candidate_authors = result.get("author_name")
        if expected_authors and not _candidate_author_variants(candidate_authors).intersection(
            expected_authors,
        ):
            continue

        candidate_year = _candidate_year(result)
        if expected_years and candidate_year not in expected_years:
            continue

        matches.append(result)

    return matches[0] if len(matches) == 1 else None


def _title_variants(value):
    raw = str(value or "")
    candidates = [raw, re.sub(r"\([^)]*\)", " ", raw), raw.split(":", 1)[0]]
    return list(dict.fromkeys(normalized for part in candidates if (normalized := normalize_search_text(part))))


def _author_variants(value):
    raw = str(value or "").strip()
    if not raw:
        return set()
    candidates = [raw]
    if "," in raw:
        family_name, given_names = raw.split(",", 1)
        candidates.append(f"{given_names} {family_name}")
    return {
        normalized
        for candidate in candidates
        if (normalized := normalize_search_text(candidate))
    }


def _candidate_author_variants(value):
    candidates = value if isinstance(value, (list, tuple)) else [value]
    return set().union(*(_author_variants(candidate) for candidate in candidates))


def _candidate_year(result):
    value = result.get("first_publish_year") or result.get("release_date")
    match = re.search(r"\b(\d{4})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _bookshelves(value, exclusive_shelf):
    shelves = [shelf.strip() for shelf in str(value or "").split(",") if shelf.strip()]
    return list(dict.fromkeys(shelf for shelf in shelves if shelf.casefold() != exclusive_shelf))


def _rating(value):
    if value in (None, ""):
        return None
    try:
        rating = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    if rating == 0:
        return None
    if not Decimal("0") < rating <= Decimal("5"):
        return None
    return rating * 2


def _positive_int(value):
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bool(value):
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%Y/%m/%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.get_current_timezone())


def _date_text(value):
    return value.strftime("%Y/%m/%d") if value else ""


def _isbn(value, *, length):
    cleaned = ISBN_RE.sub("", str(value or "")).upper()
    if length == 13 and _valid_isbn13(cleaned):
        return cleaned
    if length == 10 and _valid_isbn10(cleaned):
        return cleaned
    return ""


def _valid_isbn13(value):
    if len(value) != 13 or not value.isdigit():
        return False
    total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(value[:12]))
    return (10 - total % 10) % 10 == int(value[-1])


def _valid_isbn10(value):
    if len(value) != 10 or not value[:9].isdigit() or not (value[-1].isdigit() or value[-1] == "X"):
        return False
    total = sum((10 - index) * (10 if digit == "X" else int(digit)) for index, digit in enumerate(value))
    return total % 11 == 0


def _isbn10_to_isbn13(value):
    if not _valid_isbn10(value):
        return ""
    prefix = f"978{value[:9]}"
    total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(prefix))
    return f"{prefix}{(10 - total % 10) % 10}"


def _isbn13_to_isbn10(value):
    if not _valid_isbn13(value) or not value.startswith("978"):
        return ""
    body = value[3:12]
    total = sum((10 - index) * int(digit) for index, digit in enumerate(body))
    check = (11 - total % 11) % 11
    suffix = "X" if check == 10 else str(check)
    return f"{body}{suffix}"
