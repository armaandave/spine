"""Reese's Book Club picks, newest first, from reesesbookclub.com/our-picks/.

The site's REST API needs a login, so this reads the server-rendered page (robots.txt
allows it, with a 10 s crawl delay; this is one request per run). Each pick is a
``<li class="… post-<id> book type-book … book_category-<month>-pick">`` card with the
title in its cover's ``alt`` and the pick label ("August ’26 Pick") as its term, plus a
``<div class="popup-details" id="post-<id>">`` holding ``<h4>by <strong>Author</strong>``.
The newest picks sometimes have no label yet. Titles and authors only: the resolver
does a strict title + author search.
"""

import html
import re

from lists.featured.helpers import get_text
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://reesesbookclub.com/our-picks/"
_CARD = re.compile(r'<li class="wp-block-post post-(\d+) book type-book[^"]*">(.*?)</li>', re.S)
_ALT = re.compile(r'\balt="([^"]*)"')
_TERM = re.compile(r'rel="tag">([^<]*)<')
_POPUP = re.compile(r'<div class="popup-details[^"]*" id="post-(\d+)">(.*?)(?=<div class="popup-details|\Z)', re.S)
_POPUP_TITLE = re.compile(r"<h3>(.*?)</h3>", re.S)
_POPUP_AUTHOR = re.compile(r"<h4>\s*by\s*(.*?)</h4>", re.S | re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
MIN_PICKS = 10


def validate(definition):
    """Check the definition without network."""
    if set(definition.media_types) != {"book"}:
        msg = "Reese's Book Club is a book list"
        raise ValueError(msg)
    limit = definition.params.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        msg = "limit must be a positive integer"
        raise ValueError(msg)


def _text(value):
    return " ".join(html.unescape(_TAGS.sub(" ", value or "")).split())


def parse(page):
    """Return ``[(title, author, label)]`` in the page's order (newest first)."""
    details = {}
    for post_id, block in _POPUP.findall(page):
        title = _POPUP_TITLE.search(block)
        author = _POPUP_AUTHOR.search(block)
        details[post_id] = (_text(title.group(1)) if title else "", _text(author.group(1)) if author else "")
    picks = []
    for post_id, card in _CARD.findall(page):
        popup_title, author = details.get(post_id, ("", ""))
        alt = _ALT.search(card)
        title = popup_title or (_text(alt.group(1)) if alt else "")
        term = _TERM.search(card)
        if title:
            picks.append((title, author, _text(term.group(1)) if term else ""))
    return picks


def fetch(definition):
    """Return every pick, newest first."""
    picks = parse(get_text("reesesbookclub", URL))
    if len(picks) < MIN_PICKS:
        msg = f"Reese's Book Club page layout changed: found {len(picks)} picks."
        raise AdapterError(msg)
    limit = definition.params.get("limit")
    picks = picks[:limit] if limit else picks
    entries = [
        Entry(rank=None, title=title, creator=author, media_type="book", note=label)
        for title, author, label in picks
    ]
    # "August ’26" when the newest pick is labeled; unlabeled newest picks get no edition.
    return FetchResult(entries=entries, edition=picks[0][2].removesuffix(" Pick") if picks else "")


ADAPTER = Adapter(key="reeses_book_club", fetch=fetch, validate=validate)
