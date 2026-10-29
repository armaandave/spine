import logging
import re
import unicodedata

from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

# Margins exports no ISBN, so a search result is only trusted when its title and
# author both match; anything else is reported instead of guessed.
ARTICLES = ("the ", "a ", "an ")


class MarginsResolver:
    """Resolve Margins rows to Spine book search results."""

    SOURCES = (Sources.HARDCOVER.value, Sources.OPENLIBRARY.value)

    def resolve_rows(self, rows):
        resolved = {}
        by_book = {}
        for row in rows:
            if not row.title or not row.authors:
                continue
            key = (_normalize(row.title), _normalize(row.author))
            if key not in by_book:
                by_book[key] = self.resolve_one(row)
            if by_book[key]:
                resolved[row.index] = by_book[key]
        return resolved

    def resolve_one(self, row):
        query = f"{row.title} {row.author}"
        title = _title_key(row.title)
        for source in self.SOURCES:
            results = [
                result
                for result in self._search(query, source)
                if _same_author(row.authors, result.get("author_name"))
            ]
            # The exact title wins over an edition with a subtitle, such as a film companion.
            match = next(
                (result for result in results if _title_key(result.get("title")) == title),
                None,
            ) or next(
                (result for result in results if _same_title(row.title, result.get("title"))),
                None,
            )
            if match:
                return match
        return None

    def _search(self, query, source):
        try:
            # Ranking fields kept: they carry the authors and first publication year.
            return services.search(
                MediaTypes.BOOK.value,
                query,
                1,
                source,
                preserve_ranking_fields=True,
            ).get("results", [])
        except services.ProviderAPIError as error:
            logger.warning("Margins %s lookup failed for %s: %s", source, query, error)
            return []


def _same_title(ours, theirs):
    """Match whole titles, or the main title when one side adds a subtitle.

    Open Library titles a translated edition "Edition title: Work title", so the
    part after its first colon counts too.
    """
    ours, theirs = ours or "", theirs or ""
    our_forms = {_title_key(ours), _title_key(_main(ours))}
    their_forms = {_title_key(theirs), _title_key(_main(theirs))}
    if ":" in theirs:
        their_forms.add(_title_key(theirs.split(":", 1)[1]))
    return bool((our_forms & their_forms) - {""})


def _same_author(ours, theirs):
    if isinstance(theirs, str):
        theirs = [theirs]
    their_keys = set().union(*(_name_keys(name) for name in theirs or []))
    return any(_name_keys(name) & their_keys for name in ours)


def _main(title):
    return re.split(r"[:(]", title, maxsplit=1)[0]


def _title_key(title):
    key = _normalize(title)
    for article in ARTICLES:
        if key.startswith(article):
            return key[len(article):]
    return key


def _name_keys(name):
    """Surname with first initial ("R.F. Kuang" is "R. F. Kuang"), and the names in
    any order ("Sohn Won-Pyung" is "Won-pyung Sohn", "Lee Mi-ye" is "Miye Lee")."""
    parts = _normalize((name or "").replace("-", "")).split()
    if not parts:
        return set()
    return {(parts[0][0], parts[-1]), frozenset(parts)}


def _normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    text = text.replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())
