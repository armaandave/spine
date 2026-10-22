"""Read with Jenna picks, newest first, from TODAY's book club list page.

The page (``today.com/shop/read-jenna-book-club-list-…``) is server-rendered: each month
is an ``<h2 class="body-heading">October 2026</h2>`` followed by one or more product
titles, ``<h2 … data-testid="product-title"><a href="…">"Partita" by Barbara
Kingsolver</a></h2>``. Older picks name only the title, so the product link matters: an
Amazon print edition (``/dp/<ISBN-10>``) or a Bookshop.org edition
(``/books/<slug>/<ISBN-13>``, ``?ean=<ISBN-13>``), possibly behind an affiliate redirect's
``d=`` parameter, is passed along as an ISBN when it validates. A pick with neither an
author nor an ISBN takes its author from a Bookshop.org slug that spells
``<title>-<author>`` (``/p/books/sam-allegra-goodman/…``).
"""

import html
import re
from urllib.parse import parse_qs, urlparse

from lists.featured.helpers import get_text
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://www.today.com/shop/read-jenna-book-club-list-today-s-jenna-bush-hager-t164652"
_TOKEN = re.compile(
    r'<h2 class="body-heading">(.*?)</h2>'
    r'|data-testid="product-title"><a [^>]*?href="([^"]*)"[^>]*>(.*?)</a>',
    re.S,
)
_MONTH = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})$",
)
_AMAZON_ISBN = re.compile(r"amazon\.com/(?:[^?#]*/)?(?:dp|gp/product|exec/obidos/ASIN)/([0-9]{9}[0-9X])(?:[/?#]|$)")
_BOOKSHOP_ISBN = re.compile(r"bookshop\.org/books/[^/?#]+/(97[89][0-9]{10})(?:[/?#]|$)")
_REDIRECT_PARAMS = ("d", "u", "url")
_BOOKSHOP_SLUG = re.compile(r"bookshop\.org/p/books/([a-z0-9-]+)/")
_QUOTED = re.compile(r'^["“”]+(.+?)[,"“”]+\s*(?:by\s+(.+))?$', re.S)
_TAGS = re.compile(r"<[^>]+>")
MIN_PICKS = 10


def validate(definition):
    """Check the definition without network."""
    if set(definition.media_types) != {"book"}:
        msg = "Read with Jenna is a book list"
        raise ValueError(msg)
    limit = definition.params.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        msg = "limit must be a positive integer"
        raise ValueError(msg)


def _text(value):
    return " ".join(html.unescape(_TAGS.sub(" ", value or "")).split())


def isbn10(value):
    """Return ``value`` if it's a valid ISBN-10, else ""."""
    text = str(value or "").upper()
    if not re.fullmatch(r"[0-9]{9}[0-9X]", text):
        return ""
    total = sum((10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(text))
    return text if total % 11 == 0 else ""


def isbn13(value):
    """Return ``value`` if it's a valid ISBN-13, else ""."""
    text = str(value or "")
    if not re.fullmatch(r"97[89][0-9]{10}", text):
        return ""
    total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(text))
    return text if total % 10 == 0 else ""


def _store_url(href):
    url = html.unescape(href or "")
    query = parse_qs(urlparse(url).query)
    for name in _REDIRECT_PARAMS:  # affiliate redirects carry the store URL
        if query.get(name, [""])[0].startswith("http"):
            return query[name][0]
    return url


def _slug(value):
    return "-".join(re.findall(r"[a-z0-9]+", str(value or "").casefold().replace("'", "").replace("’", "")))


def slug_author(href, title):
    """Return the author in a Bookshop.org ``<title>-<author>`` slug, or ""."""
    found = _BOOKSHOP_SLUG.search(_store_url(href))
    prefix = f"{_slug(title)}-"
    if not found or not found.group(1).startswith(prefix):
        return ""
    return " ".join(part.capitalize() for part in found.group(1).removeprefix(prefix).split("-"))


def link_isbn(href):
    """Return the edition ISBN in a product link (Amazon or Bookshop.org), or ""."""
    url = html.unescape(href or "")
    query = parse_qs(urlparse(url).query)
    for name in _REDIRECT_PARAMS:  # affiliate redirects carry the store URL
        if query.get(name, [""])[0].startswith("http"):
            url = query[name][0]
            query = parse_qs(urlparse(url).query)
            break
    for value in query.get("ean", []):
        if isbn13(value):
            return value
    found = _BOOKSHOP_ISBN.search(url)
    if found and isbn13(found.group(1)):
        return found.group(1)
    found = _AMAZON_ISBN.search(url)
    return isbn10(found.group(1)) if found else ""


def split_title(value):
    """Return ``(title, author)`` from ``"Title" by Author`` (author may be "")."""
    text = _text(value)
    match = _QUOTED.match(text)
    if match:
        return match.group(1).strip(), (match.group(2) or "").strip()
    title, _, author = text.partition(" by ")
    return title.strip(' "“”'), author.strip()


def parse(page):
    """Return ``[(month label, title, author, isbn)]`` in the page's order."""
    picks = []
    month = None
    for heading, href, product in _TOKEN.findall(page):
        if heading:
            label = _text(heading)
            month = label if _MONTH.match(label) else None
            continue
        if month is None:  # a product outside the month sections (deals, ads)
            continue
        title, author = split_title(product)
        if not title:
            continue
        isbn = link_isbn(href)
        if not author and not isbn:
            author = slug_author(href, title)
        picks.append((month, title, author, isbn))
    return picks


def fetch(definition):
    """Return every pick, newest first."""
    picks = parse(get_text("today", URL))
    if len(picks) < MIN_PICKS:
        msg = f"Read with Jenna page layout changed: found {len(picks)} picks."
        raise AdapterError(msg)
    limit = definition.params.get("limit")
    picks = picks[:limit] if limit else picks
    entries = [
        Entry(rank=None, title=title, creator=author, media_type="book", isbns=(isbn,) if isbn else (), note=month)
        for month, title, author, isbn in picks
    ]
    return FetchResult(entries=entries, edition=picks[0][0] if picks else "")


ADAPTER = Adapter(key="read_with_jenna", fetch=fetch, validate=validate)
