"""Core value types shared by featured-list adapters, the resolver and the writer."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from lists.featured.text import normalize_isbn, normalize_title

# Order in which ``Entry.key`` picks its identity. The first id present wins, so a
# source that always sends the same ids always produces the same key. New ids go last,
# so entries that already have a key keep it (and their snapshots and chart moves).
KEY_ID_ORDER = (
    "ref",
    "tmdb",
    "imdb",
    "igdb",
    "steam",
    "mal",
    "mangaupdates",
    "anilist",
    "mbid_rg",
    "hardcover",
    "olid",
    "comicvine",
    "wikidata",
    "goodreads",
    "apple_album",
)
# Ids whose value space is shared between media types, so the key carries the type.
TYPED_IDS = {"tmdb", "mal"}

PERSON_MEDIA_TYPE = "person"


@dataclass(frozen=True)
class Entry:
    """One row of a remote list, before it is matched to a Spine item.

    ``ids`` keys: ``ref`` (an exact Spine ref, see ``Match.ref``), ``tmdb``,
    ``imdb``, ``igdb``, ``steam``, ``mal``, ``mangaupdates``, ``anilist``,
    ``mbid_rg`` (MusicBrainz release group), ``hardcover``, ``olid`` (Open Library
    edition, ``OL…M``), ``comicvine``, ``wikidata``, ``goodreads``, ``apple_album``
    (Apple Music album). People lists also
    use ``tmdb_person``, ``openlibrary_person``, ``hardcover_person``,
    ``musicbrainz_person``, ``mal_person`` and ``mangaupdates_person``.
    """

    rank: int | None
    title: str
    year: int | None = None
    creator: str = ""
    media_type: str | None = None
    ids: Mapping[str, str] = field(default_factory=dict)
    isbns: tuple[str, ...] = ()
    image: str = ""
    note: str = ""

    def __post_init__(self):
        """Normalize ids to non-empty strings and ISBNs to a de-duplicated tuple."""
        ids = {
            str(name): str(value).strip()
            for name, value in dict(self.ids or {}).items()
            if value is not None and str(value).strip()
        }
        isbns = tuple(
            dict.fromkeys(
                isbn for isbn in (normalize_isbn(value) for value in self.isbns or ()) if isbn
            ),
        )
        object.__setattr__(self, "ids", ids)
        object.__setattr__(self, "isbns", isbns)
        object.__setattr__(self, "title", str(self.title or "").strip())
        object.__setattr__(self, "creator", str(self.creator or "").strip())
        object.__setattr__(self, "image", str(self.image or "").strip())
        object.__setattr__(self, "note", str(self.note or "").strip())

    @property
    def key(self):
        """Return the stable identity of this entry within its source."""
        for name in KEY_ID_ORDER:
            value = self.ids.get(name)
            if not value:
                continue
            if name in TYPED_IDS and self.media_type:
                return f"{name}:{self.media_type}:{value}"
            return f"{name}:{value}"
        for name in sorted(self.ids):
            if name.endswith("_person"):
                return f"{name}:{self.ids[name]}"
        if self.isbns:
            return f"isbn:{self.isbns[0]}"
        return f"t:{normalize_title(self.title)}|{self.year or ''}"


class FetchResult(NamedTuple):
    """What an adapter returns for one definition."""

    entries: list[Entry]
    edition: str = ""  # period label: "Week of Oct 13", "2026", "Fall 2026"
    expected_count: int | None = None
    name: str | None = None  # the remote list's name, when the source has one
    live_label: str = ""  # "7 of 23 announced"
    description: str = ""  # replaces the definition's description for this edition


@dataclass(frozen=True)
class Match:
    """A resolved Spine identity, with optional hints for creating the Item."""

    source: str
    media_type: str
    media_id: str
    title: str = ""
    image: str = ""
    year: int | None = None
    total_pages: int | None = None
    known_for_department: str = ""  # people only

    @property
    def ref(self):
        """Return ``<source>:<media_type>:<media_id>``."""
        return f"{self.source}:{self.media_type}:{self.media_id}"

    @property
    def is_person(self):
        """Return whether this identifies a person rather than an Item."""
        return self.media_type == PERSON_MEDIA_TYPE

    def as_cache(self):
        """Return a JSON-safe dict for the resolution cache."""
        return {
            "source": self.source,
            "media_type": self.media_type,
            "media_id": self.media_id,
            "title": self.title,
            "image": self.image,
            "year": self.year,
            "total_pages": self.total_pages,
            "known_for_department": self.known_for_department,
        }

    @classmethod
    def from_cache(cls, data):
        """Rebuild a match stored by ``as_cache``."""
        return cls(
            source=data["source"],
            media_type=data["media_type"],
            media_id=str(data["media_id"]),
            title=data.get("title") or "",
            image=data.get("image") or "",
            year=data.get("year"),
            total_pages=data.get("total_pages"),
            known_for_department=data.get("known_for_department") or "",
        )


@dataclass(frozen=True)
class Adapter:
    """A source family: how to fetch one definition's entries.

    ``fetch(definition) -> FetchResult`` does the network work and raises
    ``AdapterError`` with a sanitized message when the source fails.
    ``validate(definition) -> None`` checks ``definition.params`` without network
    and raises ``ValueError`` when they are wrong; the registry tests call it.
    ``version(definition) -> str`` is optional: a fingerprint of local inputs (a
    static file's bytes) folded into the definition hash, so edits trigger a sync.
    """

    key: str
    fetch: Callable[[Any], FetchResult]
    validate: Callable[[Any], None] | None = None
    version: Callable[[Any], str] | None = None


class AdapterError(Exception):
    """A source failed. The message must never contain a key or a keyed URL."""


class GuardError(Exception):
    """A writer guard refused the result; the existing list was left untouched."""

    def __init__(self, reason, message, *, status="guarded", **details):
        """Store the machine-readable reason, the run status and any counts."""
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.details = details
