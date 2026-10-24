"""Deterministic text normalization for featured-list matching (no fuzzy logic)."""

import re
import unicodedata

_CREATOR_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bwith\b|;|/)\s*", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|2[01]\d\d)(?!\d)")


def normalize_title(value):
    """Return a casefolded, accent-free, punctuation-free form of a title.

    ``&`` becomes ``and``; everything that isn't a letter or digit becomes a space.
    """
    text = str(value or "").replace("&", " and ")
    chars = []
    for char in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(char):
            continue
        category = unicodedata.category(char)
        if category[0] in {"L", "N"}:
            chars.append(char.casefold())
        elif char in "'’`":
            continue
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def title_forms(value):
    """Return the normalized title plus its main title before a subtitle."""
    full = normalize_title(value)
    forms = {full} if full else set()
    for separator in (":", " - ", " – ", " — ", "("):
        head = str(value or "").split(separator, 1)[0]
        head_form = normalize_title(head)
        if head_form and head_form != full:
            forms.add(head_form)
    return forms


def normalize_isbn(value):
    """Return an ISBN with spaces and hyphens removed, upper-cased."""
    return re.sub(r"[\s-]", "", str(value or "")).upper()


def creator_names(value):
    """Return normalized individual names from ``"A and B"``, lists, or dicts."""
    if value is None:
        return set()
    if isinstance(value, dict):
        value = value.get("name") or value.get("artist", {}).get("name") or ""
    if isinstance(value, (list, tuple, set)):
        names = set()
        for part in value:
            names |= creator_names(part)
        return names
    return {
        normalized
        for part in _CREATOR_SPLIT.split(str(value))
        if (normalized := normalize_title(part))
    }


def creators_match(expected, actual):
    """Return whether any expected creator equals any credited creator."""
    expected_names = creator_names(expected)
    actual_names = creator_names(actual)
    if not expected_names or not actual_names:
        return True
    if expected_names & actual_names:
        return True
    # "J. R. R. Tolkien" vs "J.R.R. Tolkien": compare without spaces as well.
    squashed = {name.replace(" ", "") for name in actual_names}
    return any(name.replace(" ", "") in squashed for name in expected_names)


def parse_year(value):
    """Return a four-digit year from an int, a timestamp, or a date-like string."""
    if value in (None, ""):
        return None
    if isinstance(value, int):
        if 1500 <= value <= 2199:  # noqa: PLR2004
            return value
        if value > 10_000_000:  # a Unix timestamp (IGDB first_release_date)
            from datetime import UTC, datetime  # noqa: PLC0415

            return datetime.fromtimestamp(value, tz=UTC).year
        return None
    match = _YEAR.search(str(value))
    return int(match.group(1)) if match else None
