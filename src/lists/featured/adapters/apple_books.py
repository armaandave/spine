"""Apple Books charts from Apple's marketing RSS feeds (no key).

Params: ``{"feed": "books/top-paid" | "books/top-free" | "audio-books/top", "limit": 50,
"country": "us"}``. The feed has no ISBNs, but most cover filenames are the edition's
ISBN-13 (``…/9781250412584.jpg/100x152bb.png``); a filename that validates as an
ISBN-13 is used as one, and every entry also carries its title and author for the
strict title search. The feed's ``releaseDate`` is the store edition's (ebook reissues
of old books carry recent dates), so it isn't used as the publication year.
"""

import re
import time
from email.utils import parsedate_to_datetime

from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

BASE_URL = "https://rss.marketingtools.apple.com/api/v2"
FEEDS = {"books/top-paid": "books", "books/top-free": "books", "audio-books/top": "audio-books"}
LIMITS = (10, 25, 50, 100)
ATTEMPTS = 3  # the feed often answers 502/504 and then succeeds a moment later
RETRY_SECONDS = 3
_ARTWORK_SIZE = re.compile(r"/\d+x\d+bb\.(?:png|jpg)$")
# Store decorations that aren't part of the book's title. The resolver matches an
# entry's whole title against a result's title or main title, so they must go.
_TRAILING_PARENS = re.compile(r"\s*\([^()]*\)\s*$")  # "(Unabridged)", "(Diary of a Wimpy Kid #21)"
_STORE_SUBTITLES = (
    re.compile(r":\s*[^:]*,\s*Book\s+\d+\s*$", re.IGNORECASE),  # ": Dungeon Crawler Carl, Book 1"
    re.compile(r":\s*Reese's Book Club\s*$", re.IGNORECASE),
    re.compile(
        r"(?::|\s[-–—])\s*(?:An?\s+)?[^:]*\b(?:Novel|Thriller|Mystery|Romance|Memoir|Story|Prequel|Novella)\b[^:]*$",
        re.IGNORECASE,
    ),  # ": A Novel", ": A Reacher Novel", " - Spicy Billionaire Romance"
    re.compile(r":\s*[^:]*\bEdition\s*$", re.IGNORECASE),  # ": Modern English Edition"
)


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("feed") not in FEEDS:
        msg = f"feed must be one of {sorted(FEEDS)}"
        raise ValueError(msg)
    if params.get("limit", 50) not in LIMITS:
        msg = f"limit must be one of {LIMITS}"
        raise ValueError(msg)
    country = params.get("country", "us")
    if len(country) != 2 or not country.islower():  # noqa: PLR2004
        msg = "country must be a two-letter lower-case code"
        raise ValueError(msg)
    if set(definition.media_types) != {"book"}:
        msg = "Apple Books charts are book lists"
        raise ValueError(msg)


def isbn13(value):
    """Return ``value`` if it's a valid ISBN-13 (978/979 prefix, check digit), else ""."""
    text = str(value or "")
    if len(text) != 13 or not text.isdigit() or text[:3] not in {"978", "979"}:  # noqa: PLR2004
        return ""
    total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(text))
    return text if total % 10 == 0 else ""


def cover_isbn(artwork_url):
    """Return the ISBN-13 in an Apple cover URL's filename, or ""."""
    parts = str(artwork_url or "").rstrip("/").split("/")
    if len(parts) < 2:  # noqa: PLR2004
        return ""
    return isbn13(parts[-2].split(".", 1)[0])


def clean_title(value):
    """Strip store decorations: trailing parentheticals and genre or series subtitles."""
    title = str(value or "").strip()
    previous = None
    while title and title != previous:
        previous = title
        title = _TRAILING_PARENS.sub("", title).strip()
        for pattern in _STORE_SUBTITLES:
            title = pattern.sub("", title).strip()
    return title or str(value or "").strip()


def large_artwork(artwork_url):
    """Ask Apple's image server for a 600 px cover instead of the 100 px thumbnail."""
    url = str(artwork_url or "")
    return _ARTWORK_SIZE.sub("/600x600bb.jpg", url) if url else ""


def _edition(updated):
    try:
        moment = parsedate_to_datetime(updated)
    except (TypeError, ValueError):
        return ""
    return f"{moment:%b} {moment.day}, {moment.year}"


def fetch(definition):
    """Return the chart."""
    params = definition.params
    feed = params["feed"]
    url = f"{BASE_URL}/{params.get('country', 'us')}/{feed}/{params.get('limit', 50)}/{FEEDS[feed]}.json"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            data = get_json("apple books", url)
            break
        except AdapterError as error:
            if attempt == ATTEMPTS or "(HTTP 5" not in str(error):
                raise
            time.sleep(RETRY_SECONDS)
    feed_data = data.get("feed") if isinstance(data, dict) else None
    rows = feed_data.get("results") if isinstance(feed_data, dict) else None
    if not rows:
        msg = "Apple Books returned an empty chart."
        raise AdapterError(msg)
    entries = []
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not row.get("name"):
            continue
        isbn = cover_isbn(row.get("artworkUrl100"))
        entries.append(
            Entry(
                rank=rank,
                title=clean_title(row["name"]),
                creator=row.get("artistName") or "",
                media_type="book",
                isbns=(isbn,) if isbn else (),
                image=large_artwork(row.get("artworkUrl100")),
            ),
        )
    return FetchResult(entries=entries, edition=_edition(feed_data.get("updated")))


ADAPTER = Adapter(key="apple_books", fetch=fetch, validate=validate)
