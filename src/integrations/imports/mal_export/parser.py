"""Parse MyAnimeList XML list exports (anime and manga)."""

import gzip
import io
import re
import zlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeElementTree

from app.models import MediaTypes, Status
from integrations.imports.helpers import MediaImportError

MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024
GZIP_MAGIC = b"\x1f\x8b"
UTF8_BOM = b"\xef\xbb\xbf"

NOT_AN_EXPORT_MESSAGE = (
    "This doesn't look like a MyAnimeList export. Upload the .xml or .xml.gz "
    "file from myanimelist.net/panel.php?go=export."
)
EMPTY_FILE_MESSAGE = (
    "The uploaded file is empty. Upload the .xml or .xml.gz file from "
    "myanimelist.net/panel.php?go=export."
)
TOO_LARGE_MESSAGE = (
    "This MyAnimeList export is too large to import "
    f"(over {MAX_DECOMPRESSED_BYTES // (1024 * 1024)} MB uncompressed)."
)
CORRUPT_GZIP_MESSAGE = (
    "The uploaded .gz file couldn't be decompressed. Download a fresh export from "
    "myanimelist.net/panel.php?go=export and try again."
)
UNSAFE_XML_MESSAGE = (
    "This file contains XML features that aren't allowed (DTDs or entities). "
    "Upload the unmodified export from myanimelist.net/panel.php?go=export."
)

# XML 1.0 forbids most C0 control characters; MAL comments occasionally contain them.
ILLEGAL_XML_CHARS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")

STATUS_MAP = {
    "watching": Status.IN_PROGRESS.value,
    "reading": Status.IN_PROGRESS.value,
    "completed": Status.COMPLETED.value,
    "onhold": Status.PAUSED.value,
    "dropped": Status.DROPPED.value,
    "plantowatch": Status.PLANNING.value,
    "plantoread": Status.PLANNING.value,
    "1": Status.IN_PROGRESS.value,
    "2": Status.COMPLETED.value,
    "3": Status.PAUSED.value,
    "4": Status.DROPPED.value,
    "6": Status.PLANNING.value,
}

TRUE_VALUES = {"1", "yes", "y", "true"}

# Per media type: (entry tag, id, title, total progress, consumed progress,
# times consumed, repeating flags, repeat progress)
ENTRY_FIELDS = {
    MediaTypes.ANIME.value: {
        "tag": "anime",
        "id": ("series_animedb_id",),
        "title": ("series_title",),
        "total": ("series_episodes",),
        "progress": ("my_watched_episodes",),
        "times": ("my_times_watched",),
        "repeating": ("my_rewatching",),
        "repeat_progress": ("my_rewatching_ep",),
    },
    MediaTypes.MANGA.value: {
        "tag": "manga",
        "id": ("manga_mangadb_id",),
        "title": ("manga_title",),
        "total": ("manga_chapters",),
        "progress": ("my_read_chapters",),
        "times": ("my_times_read",),
        "repeating": ("my_rereading", "my_rereadingg"),
        "repeat_progress": ("my_rereading_chap",),
    },
}


@dataclass
class MALExportEntry:
    """A normalized MyAnimeList list entry."""

    media_type: str
    media_id: str
    title: str
    raw_status: str
    status: str
    progress: int
    total: int
    start_date: date | None
    finish_date: date | None
    score: Decimal | None
    comments: str = ""
    times_consumed: int = 0
    tags: list[str] = field(default_factory=list)
    repeating: bool = False
    series_type: str = ""

    @property
    def completed_once(self):
        """Return whether MAL records at least one finished run."""
        return self.raw_status_normalized in {"completed", "2"}

    @property
    def raw_status_normalized(self):
        """Return the normalized raw MAL status key."""
        return _status_key(self.raw_status)


@dataclass
class MALExport:
    """Parsed export entries plus per-entry warnings."""

    entries: list[MALExportEntry]
    warnings: list[str]


def parse_export(file_or_bytes):
    """Parse a MyAnimeList XML export (optionally gzip-compressed)."""
    payload = _read_payload(file_or_bytes)
    root = _parse_xml(payload)
    if _local_name(root.tag) != "myanimelist":
        raise MediaImportError(NOT_AN_EXPORT_MESSAGE)

    entries = []
    warnings = []
    seen = set()
    for element in root:
        media_type = _media_type_for_tag(_local_name(element.tag))
        if media_type is None:
            continue
        entry = _parse_entry(element, media_type, warnings)
        if entry is None:
            continue
        key = (entry.media_type, entry.media_id)
        if key in seen:
            warnings.append(f"{entry.title}: Duplicate MyAnimeList entry skipped")
            continue
        seen.add(key)
        entries.append(entry)
    return MALExport(entries=entries, warnings=warnings)


def _read_payload(file_or_bytes):
    """Return the decompressed export bytes, bounded by MAX_DECOMPRESSED_BYTES."""
    if hasattr(file_or_bytes, "read"):
        if hasattr(file_or_bytes, "seek"):
            try:
                file_or_bytes.seek(0)
            except (OSError, ValueError):
                pass
        payload = file_or_bytes.read(MAX_DECOMPRESSED_BYTES + 1)
    else:
        payload = file_or_bytes or b""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    payload = bytes(payload)
    if len(payload) > MAX_DECOMPRESSED_BYTES:
        raise MediaImportError(TOO_LARGE_MESSAGE)
    if payload[:2] == GZIP_MAGIC:
        return _gunzip(payload)
    return payload


def _gunzip(payload):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as gz:
            # Bounded read: stops decompressing a gzip bomb at the size cap.
            decompressed = gz.read(MAX_DECOMPRESSED_BYTES + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise MediaImportError(CORRUPT_GZIP_MESSAGE) from error
    if len(decompressed) > MAX_DECOMPRESSED_BYTES:
        raise MediaImportError(TOO_LARGE_MESSAGE)
    return decompressed


def _parse_xml(payload):
    if payload.startswith(UTF8_BOM):
        payload = payload[len(UTF8_BOM):]
    payload = payload.lstrip()
    if not payload:
        raise MediaImportError(EMPTY_FILE_MESSAGE)
    if not payload.startswith(b"<"):
        raise MediaImportError(NOT_AN_EXPORT_MESSAGE)
    try:
        return _fromstring(payload)
    except DefusedXmlException as error:
        raise MediaImportError(UNSAFE_XML_MESSAGE) from error
    except ParseError as error:
        cleaned = ILLEGAL_XML_CHARS.sub(b"", payload)
        if cleaned != payload:
            try:
                return _fromstring(cleaned)
            except DefusedXmlException as retry_error:
                raise MediaImportError(UNSAFE_XML_MESSAGE) from retry_error
            except ParseError:
                pass
        raise MediaImportError(NOT_AN_EXPORT_MESSAGE) from error


def _fromstring(payload):
    return SafeElementTree.fromstring(
        payload,
        forbid_dtd=True,
        forbid_entities=True,
        forbid_external=True,
    )


def _parse_entry(element, media_type, warnings):
    fields = ENTRY_FIELDS[media_type]
    values = {_local_name(child.tag): (child.text or "").strip() for child in element}

    def first(names):
        for name in names:
            if values.get(name):
                return values[name]
        return ""

    title = first(fields["title"])
    raw_id = first(fields["id"])
    label = title or f"MyAnimeList {media_type} {raw_id or '(no ID)'}"
    media_id = _media_id(raw_id)
    if media_id is None:
        warnings.append(f"{label}: Missing or invalid MyAnimeList ID {raw_id!r}")
        return None

    raw_status = values.get("my_status", "")
    status = STATUS_MAP.get(_status_key(raw_status))
    if status is None:
        warnings.append(f"{label}: Unknown MyAnimeList status {raw_status!r}")
        return None

    repeating = first(fields["repeating"]).casefold() in TRUE_VALUES
    progress = _int(first(fields["progress"]))
    if repeating:
        status = Status.IN_PROGRESS.value
        repeat_progress = _int(first(fields["repeat_progress"]))
        if repeat_progress:
            progress = repeat_progress

    return MALExportEntry(
        media_type=media_type,
        media_id=media_id,
        title=title or media_id,
        raw_status=raw_status,
        status=status,
        progress=progress,
        total=_int(first(fields["total"])),
        start_date=parse_mal_date(values.get("my_start_date")),
        finish_date=parse_mal_date(values.get("my_finish_date")),
        score=_score(values.get("my_score")),
        comments=values.get("my_comments", ""),
        times_consumed=_int(first(fields["times"])),
        tags=parse_tags(values.get("my_tags", "")),
        repeating=repeating,
        series_type=values.get("series_type", ""),
    )


def parse_mal_date(value):
    """Parse MAL's YYYY-MM-DD dates; 00 month/day parts default to 01."""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 3 or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    year, month, day = (int(part) for part in parts)
    if year == 0:
        return None
    try:
        return date(year, month or 1, day or 1)
    except ValueError:
        return None


def parse_tags(value):
    """Split MAL's comma-separated tags, trimming and de-duplicating."""
    tags = {}
    for raw in (value or "").split(","):
        tag = raw.strip()
        if tag and tag.casefold() not in tags:
            tags[tag.casefold()] = tag
    return list(tags.values())


def _score(value):
    if not value:
        return None
    try:
        score = Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None
    if not score.is_finite() or score <= 0 or score > 10:
        return None
    return score.quantize(Decimal("0.1"))


def _media_id(value):
    if not value or not (value.isascii() and value.isdigit()):
        return None
    media_id = str(int(value))
    return media_id if media_id != "0" else None


def _int(value):
    try:
        return max(int(value), 0) if value else 0
    except (TypeError, ValueError):
        return 0


def _status_key(value):
    return re.sub(r"[\s_\-]+", "", (value or "").casefold())


def _local_name(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _media_type_for_tag(tag):
    for media_type, fields in ENTRY_FIELDS.items():
        if fields["tag"] == tag:
            return media_type
    return None
