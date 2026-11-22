"""Curated lists stored as CSV files in ``lists/featured/static/``.

Params: ``{"file": "<key>.csv"}``. Columns: ``rank,title,year,creator,ref,image,note``.
Every row needs a ``ref``; leave a row out rather than guess. ``definitions/static.py``
builds the definitions from ``static/manifest-*.csv``.

Only two note shapes reach the poster in builds with the poster marks: "Joint 21" (a
tie: the poster shows 21) and "Inducted 2015" or "Acquired 2012" (the poster shows the
year). Any other note is shown only by older builds.
"""

import csv
import hashlib
import re
from pathlib import Path

from lists.featured.resolve import parse_ref
from lists.featured.types import PERSON_MEDIA_TYPE, Adapter, AdapterError, Entry, FetchResult

DIRECTORY = Path(__file__).resolve().parent.parent / "static"
COLUMNS = ["rank", "title", "year", "creator", "ref", "image", "note"]
_JOINT = re.compile(r"^Joint (\d+)$")
_JOINED_IN = re.compile(r"^(?:Inducted|Acquired) (\d{4})$")


def note_poster(note):
    """Return ``(fact, shown_rank)`` from a static note ("Joint 21", "Inducted 2015")."""
    if joint := _JOINT.match(note):
        return "", int(joint.group(1))
    if joined := _JOINED_IN.match(note):
        return joined.group(1), None
    return "", None


def path_for(definition):
    """Return the CSV path for a static definition."""
    name = str(definition.params.get("file") or "")
    if not name or "/" in name or "\\" in name or not name.endswith(".csv"):
        msg = "file must be a bare <name>.csv"
        raise ValueError(msg)
    return DIRECTORY / name


def _int(value, column, line):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        msg = f"line {line}: {column} must be a whole number"
        raise ValueError(msg) from None


def read_entries(path, *, allowed_media_types=None):
    """Parse one static CSV into entries, raising ``ValueError`` on any bad row."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            msg = f"{Path(path).name}: header must be {','.join(COLUMNS)}"
            raise ValueError(msg)
        entries, refs = [], set()
        for line, row in enumerate(reader, start=2):
            ref = (row["ref"] or "").strip()
            if not ref:
                msg = f"line {line}: ref is required"
                raise ValueError(msg)
            source, media_type, _ = parse_ref(ref)
            if allowed_media_types is not None and media_type not in allowed_media_types:
                msg = f"line {line}: {media_type} is not one of this list's media types"
                raise ValueError(msg)
            if ref in refs:
                msg = f"line {line}: {ref} appears twice"
                raise ValueError(msg)
            refs.add(ref)
            note = (row["note"] or "").strip()
            fact, shown_rank = note_poster(note)
            entries.append(
                Entry(
                    rank=_int(row["rank"], "rank", line),
                    title=(row["title"] or "").strip() or ref,
                    year=_int(row["year"], "year", line),
                    creator=row["creator"] or "",
                    media_type=media_type,
                    ids={"ref": ref},
                    image=row["image"] or "",
                    note=note,
                    fact=fact,
                    shown_rank=shown_rank,
                ),
            )
    return entries


def _allowed(definition):
    if definition.list_type == "people":
        return {PERSON_MEDIA_TYPE}
    return set(definition.media_types)


def validate(definition):
    """Parse the file; any problem raises ``ValueError``."""
    path = path_for(definition)
    if not path.exists():
        msg = f"static/{path.name} does not exist"
        raise ValueError(msg)
    entries = read_entries(path, allowed_media_types=_allowed(definition))
    if not entries:
        msg = f"static/{path.name} has no rows"
        raise ValueError(msg)


def version(definition):
    """Return a hash of the file, so editing it re-syncs the list."""
    try:
        return hashlib.sha256(path_for(definition).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return "missing"


def fetch(definition):
    """Read the file."""
    try:
        entries = read_entries(path_for(definition), allowed_media_types=_allowed(definition))
    except (OSError, ValueError) as error:
        raise AdapterError(str(error)) from None
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="static", fetch=fetch, validate=validate, version=version)
