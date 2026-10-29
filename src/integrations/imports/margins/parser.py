import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from django.utils import timezone

from integrations.imports.helpers import MediaImportError

REQUIRED_COLUMNS = {
    "Title",
    "Author(s)",
    "Status",
    "Start Date",
    "End Date",
    "Rating",
    "Review",
}

READ = "read"
STOPPED = "stopped"
READING = "reading"
TO_READ = "to_read"

# Margins gives books a Goodreads import brought over without a shelf the status
# Unread; they were that Goodreads Want to Read shelf.
STATES = {
    "finished": READ,
    "stopped": STOPPED,
    "reading": READING,
    "want to read": TO_READ,
    "unread": TO_READ,
}


@dataclass(frozen=True)
class MarginsRow:
    """One Margins row: a read of a book, or a shelved book."""

    index: int
    title: str
    authors: tuple[str, ...]
    status: str
    state: str | None
    start_date: date | None
    end_date: date | None
    rating: Decimal | None
    review: str

    @property
    def author(self):
        return self.authors[0] if self.authors else ""


def parse_export(file_or_bytes):
    """Parse a Margins reading CSV export."""
    payload = file_or_bytes.read() if hasattr(file_or_bytes, "read") else file_or_bytes
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a Margins CSV export."
        raise MediaImportError(msg) from error

    reader = csv.DictReader(io.StringIO(text))
    missing = REQUIRED_COLUMNS - {name.strip() for name in reader.fieldnames or [] if name}
    if missing:
        msg = f"Unsupported Margins CSV format. Missing columns: {', '.join(sorted(missing))}."
        raise MediaImportError(msg)

    today = timezone.localdate()
    return [_normalize_row(index, row, today) for index, row in enumerate(reader, start=1)]


def _normalize_row(index, row, today):
    row = {key.strip(): (value or "").strip() for key, value in row.items() if key}
    status = row.get("Status", "")
    return MarginsRow(
        index=index,
        title=row.get("Title", ""),
        authors=tuple(name.strip() for name in row.get("Author(s)", "").split(",") if name.strip()),
        status=status,
        state=STATES.get(status.casefold()),
        start_date=_date(row.get("Start Date"), today),
        end_date=_date(row.get("End Date"), today),
        rating=_rating(row.get("Rating")),
        review=row.get("Review", ""),
    )


def _date(value, today):
    """Return a calendar date; "Unknown", blanks and future dates count as unknown."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed <= today else None


def _rating(value):
    """Return the doubled storage rating; quarter stars round up to the next half star."""
    try:
        stars = Decimal(value)
    except (InvalidOperation, TypeError):
        return None
    rating = (stars * 2).to_integral_value(rounding=ROUND_CEILING)
    return rating if 1 <= rating <= 10 else None
