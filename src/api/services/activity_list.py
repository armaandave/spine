"""The Activity list's words and numbers (docs/activity-contract.md, AC-1 to AC-4).

A page of activity is the calculation's rows (api.services.activity_rows), sorted and
sliced. This module says what a row was in words, reads the list's filters, and builds
the three things a list sends beside its rows when it was opened from a Stats number:
the top line, the type's total, and the undated line. Each is worked out from the same
rows the list shows, so a list always adds up to the number that opened it.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from rest_framework.exceptions import ValidationError

from api.services import activity_rows, stats_time
from api.services.stats_time import HOURS_TYPES, UNITS
from app import exposure
from app.models import MediaTypes

COUNTS = (
    activity_rows.TITLES,
    activity_rows.DAYS,
    activity_rows.WEEKS,
    activity_rows.MULTI_TITLE_DAYS,
    activity_rows.EPISODES,
    activity_rows.HOURS,
)
TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
# What a type's titles are called, singular then plural; None says "titles".
NOUNS = {
    MediaTypes.BOOK.value: ("book", "books"),
    MediaTypes.GAME.value: ("game", "games"),
    MediaTypes.MANGA.value: ("manga", "manga"),
    MediaTypes.COMIC.value: ("comic", "comics"),
    MediaTypes.ANIME.value: ("anime", "anime"),
    MediaTypes.TV.value: ("show", "shows"),
    MediaTypes.MOVIE.value: ("movie", "movies"),
    MediaTypes.MUSIC.value: ("album", "albums"),
}
# What a log or a viewing is called where titles aren't what's counted.
TOLD_NOUNS = {
    MediaTypes.MOVIE.value: ("watch", "watches"),
    MediaTypes.MUSIC.value: ("listen", "listens"),
}
# The types whose rows show their time beside the words. Games' words already say hours.
TIME_LABEL_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MOVIE.value,
    MediaTypes.MUSIC.value,
)
NEVER = datetime(1970, 1, 1, tzinfo=UTC)
TOLD_UNITS = ("episode", "page", "chapter", "issue")


# Numbers in words.


def number(amount):
    """Return a plain number: 40, or 2.25 where there's a fraction."""
    rounded = round(float(amount), 2)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def minutes_label(amount):
    """Return minutes as "2h 15m"."""
    hours, minutes = divmod(round(amount), 60)
    return " ".join(part for part in (f"{hours}h" if hours else "", f"{minutes}m" if minutes else "") if part)


def plain_number(amount):
    """Return a Decimal as an int, or a rounded float where it has a fraction."""
    rounded = round(float(amount), 2)
    return int(rounded) if rounded == int(rounded) else rounded


def with_commas(amount):
    """Return a whole number with thousands separators."""
    return f"{int(amount):,}"


def counted(value, singular, plural=None):
    """Return "1 book" or "5 books"."""
    return f"{with_commas(value)} {singular if value == 1 else plural or singular + 's'}"


def hours_words(minutes, *, approximate=False):
    """Say minutes the way the list's top line does: "2 hours (2h 15m)", "≈ 86 hours".

    Whole hours, rounded, with the exact time in brackets only when it says more.
    """
    minutes = int(minutes)
    if minutes < 60:
        words = counted(minutes, "minute")
    else:
        hours = (minutes + 30) // 60
        words = counted(hours, "hour")
        if minutes % 60:
            words += f" ({minutes_label(minutes)})"
    return f"≈ {words}" if approximate else words


# A row in words.


def told_label(row):
    """Say what a row was told in: "40 pages", "2h", "25%", or None."""
    if not row.told:
        return None
    unit = row.told_unit
    if unit == activity_rows.MINUTE:
        return minutes_label(row.told)
    if unit == activity_rows.PERCENT:
        return f"{number(row.told)}%"
    if unit in TOLD_UNITS:
        return f"{number(row.told)} {unit if row.told == 1 else unit + 's'}"
    return None


def start_verb(media_type):
    """Return what starting a title of this type is called."""
    return {
        MediaTypes.TV.value: "watching",
        MediaTypes.SEASON.value: "watching",
        MediaTypes.ANIME.value: "watching",
        MediaTypes.GAME.value: "playing",
    }.get(media_type, "reading")


def progress_words(row):
    """Say what a day's progress was: ("read 40 pages, paused", "40 pages").

    The label is only what was told (or "Started"), and none on a day with only a status
    move, or the app would print "+Paused" (AC-1).
    """
    label = told_label(row)
    parts = []
    if label:
        verb = {
            activity_rows.EPISODE: "watched",
            activity_rows.MINUTE: "played",
        }.get(row.told_unit)
        if row.told_unit == activity_rows.PERCENT:
            verb = "progressed" if row.bucket == MediaTypes.GAME.value else "read"
        parts.append(f"{verb or 'read'} {label}")
    elif row.started:
        parts.append(f"started {start_verb(row.bucket)}")
        label = "Started"
    # Starting again says it all: the pick-up that went with it adds no words.
    parts.extend(move for move in row.moves if not (row.started and move == activity_rows.PICKED_BACK_UP))
    return ", ".join(parts), label


def time_label(row):
    """Return a row's time beside its words, on the types that add up in time."""
    if row.bucket in TIME_LABEL_TYPES and row.amount:
        return minutes_label(row.amount)
    return None


def row_fields(row):
    """Return what a row adds to its `object` beyond its words (5.6, all optional)."""
    return {
        "amount": plain_number(row.amount),
        "unit": UNITS.get(row.bucket),
        "time_label": time_label(row),
    }


PLACE_UNITS = {
    activity_rows.PAGE: "pages",
    "chapter": "chapters",
    "issue": "issues",
    activity_rows.MINUTE: "minutes",
    activity_rows.PERCENT: "percent",
    activity_rows.EPISODE: "episodes",
    activity_rows.SEASON_LEVEL: "season",
}


def progress_fields(row):
    """Return a progress row's moves, the percent it told, and the day's place."""
    return {
        **row_fields(row),
        "moves": [activity_rows.MOVE_KEYS[move] for move in row.moves],
        "percent": plain_number(row.told) if row.told_unit == activity_rows.PERCENT else None,
        "place": {PLACE_UNITS[unit]: plain_number(value) for unit, value in row.place.items()} if row.place else None,
    }


def log_fields(row):
    """Return a log row's added fields; its label is what it added, with no plus."""
    label = told_label(row)
    return {**row_fields(row), "label": label}


# What was asked.


@dataclass
class Filters:
    """What a list was asked for: one type, a range of days, a number to add up."""

    media_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    counts: str | None = None

    @property
    def asked(self):
        """Whether the request names a number: a type, dates or a count."""
        return bool(self.media_type or self.start_date or self.end_date or self.counts)

    @property
    def is_all_time(self):
        return self.start_date is None and self.end_date is None


def parse_filters(params):
    """Read a list's filters from its query, or raise a 400."""
    media_type = params.get("media_type") or None
    if media_type:
        exposure.require_media_type(media_type)
        if media_type in TV_TYPES:
            media_type = MediaTypes.TV.value
        elif media_type not in activity_rows.ADAPTERS:
            raise ValidationError({"media_type": "This type has no activity."})
    counts = params.get("counts") or None
    if counts is not None and counts not in COUNTS:
        raise ValidationError({"counts": f"Use one of {', '.join(COUNTS)}."})
    return Filters(
        media_type,
        _day(params, "start_date"),
        _day(params, "end_date"),
        counts,
    )


def _day(params, key):
    value = params.get(key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError({key: "Use a date as YYYY-MM-DD."}) from error


def exposed(rows):
    """Keep the rows of the types the server exposes."""
    allowed = set(exposure.primary_media_types())
    return [row for row in rows if row.bucket in allowed]


def needs_piles(filters):
    """Whether this list's numbers include the undated pile: only All Time's do."""
    return filters.asked and filters.is_all_time and filters.counts not in (
        activity_rows.DAYS,
        activity_rows.WEEKS,
        activity_rows.MULTI_TITLE_DAYS,
    )


def select(calculation, filters):
    """Return the list's rows, newest first, with ties always broken the same way."""
    listed = activity_rows.list_rows(
        calculation.rows,
        media_type=filters.media_type,
        start_date=filters.start_date,
        end_date=filters.end_date,
        counts=filters.counts,
    )
    return sorted(listed, key=row_order, reverse=True)


def row_order(row):
    """Return a row's place in the list: day, written, kind, id, title, then person."""
    day, written, kind, row_id = row.sort_key
    return (day, written or NEVER, kind, row_id, row.title_id, row.actor_id or 0)


# The number that opened the list.


def _pile_of(piles, filters):
    """Return the pile the tapped number includes, or an empty one."""
    if not needs_piles(filters):
        return activity_rows.Pile(None, filters.media_type)
    if filters.counts == activity_rows.HOURS:
        pile = activity_rows.Pile(None, None)
        for bucket in HOURS_TYPES:
            pile = _join(pile, activity_rows.piled(piles, bucket))
        return pile
    return activity_rows.piled(piles, filters.media_type)


def _join(pile, other):
    pile.amount += other.amount
    pile.told += other.told
    pile.titles |= other.titles
    pile.known |= other.known
    pile.no_amount |= other.no_amount
    pile.unknown_told += other.unknown_told
    pile.approximate = pile.approximate or other.approximate
    return pile


def extras(calculation, filters, listed, owner):
    """Return `top_line`, `total` and `undated` for a list opened from a number."""
    pile = _pile_of(calculation.piles, filters)
    in_range = activity_rows.in_range(
        calculation.rows,
        media_type=filters.media_type,
        start_date=filters.start_date,
        end_date=filters.end_date,
    )
    result = {
        "top_line": top_line(filters, listed, in_range, pile, owner),
        "total": total(filters, in_range, calculation.piles),
        "undated": undated(filters, listed, pile),
    }
    return result


def total(filters, in_range, piles):
    """Return a type's number as Stats reads it, when the list is of one type."""
    if not filters.media_type:
        return None
    all_time_piles = piles if filters.is_all_time else []
    time = stats_time._from_rows(exposed(in_range), all_time_piles, filters.media_type)
    return {
        "value": time.value,
        "unit": time.unit,
        "known": time.known,
        "total": time.total,
        "approximate": time.approximate,
        "time_label": minutes_label(time.value) if time.unit == "minutes" and time.value else None,
    }


def _titles_with_rows(listed):
    return {row.title_id for row in listed if row.counts_title}


def top_line(filters, listed, in_range, pile, owner):  # noqa: PLR0911
    """Say the number tapped, in the server's words, or None if none was."""
    if not filters.asked:
        return None
    media_type = filters.media_type
    approximate = pile.approximate or any(row.approximate for row in listed)
    counts = filters.counts
    if counts == activity_rows.TITLES:
        value = len(listed) + len(pile.titles - _titles_with_rows(listed))
        singular, plural = NOUNS.get(media_type, ("title", "titles"))
        return counted(value, singular, plural)
    if counts == activity_rows.DAYS:
        return counted(len({row.day for row in listed}), "active day")
    if counts == activity_rows.WEEKS:
        from api.services.stats_v2_activity import week_start

        start = getattr(owner, "week_start_day", None) or "monday"
        return counted(len({week_start(row.day, start) for row in listed}), "week")
    if counts == activity_rows.MULTI_TITLE_DAYS:
        return counted(len({row.day for row in listed}), "day") + " with 2+ titles"
    if counts == activity_rows.EPISODES:
        told = sum((row.told for row in listed), pile.told)
        return counted(int(told), "episode")
    if counts == activity_rows.HOURS:
        minutes = sum((row.amount for row in listed), pile.amount)
        return hours_words(minutes, approximate=approximate)
    if media_type is None:
        return None
    unit = UNITS[media_type]
    amount = sum((row.amount for row in in_range), pile.amount)
    if unit == "minutes":
        return hours_words(amount, approximate=approximate)
    singular = {"pages": "page", "chapters": "chapter", "issues": "issue"}[unit]
    return counted(int(amount), singular)


def undated(filters, listed, pile):
    """Describe what the number tapped includes with no date (decision 39)."""
    if not needs_piles(filters):
        return None
    media_type = filters.media_type
    counts = filters.counts
    if counts is None and media_type is None:
        return None
    titles = pile.titles - _titles_with_rows(listed)
    minutes_type = media_type is None or UNITS.get(media_type) == "minutes"
    parts = []
    singular, plural = NOUNS.get(media_type, ("title", "titles"))
    if counts == activity_rows.TITLES:
        parts.append(counted(len(titles), singular, plural) if titles else None)
    elif counts == activity_rows.HOURS and media_type is None:
        parts.append(None)
    elif media_type in TOLD_NOUNS:
        told_singular, told_plural = TOLD_NOUNS[media_type]
        parts.append(counted(int(pile.told), told_singular, told_plural) if pile.told else None)
    elif media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        parts.append(counted(len(titles), singular, plural) if titles else None)
        parts.append(counted(int(pile.told), "episode") if pile.told else None)
    else:
        parts.append(counted(len(titles), singular, plural) if titles else None)
    if counts != activity_rows.TITLES and pile.amount:
        if minutes_type:
            parts.append(counted((int(pile.amount) + 30) // 60, "hour"))
        else:
            unit = UNITS[media_type]
            parts.append(counted(int(pile.amount), unit[:-1], unit))
    parts = [part for part in parts if part]
    if not parts:
        return None
    return {
        "amount": plain_number(pile.amount),
        "unit": UNITS.get(media_type, "minutes"),
        "titles": len(titles),
        "told": plain_number(pile.told),
        "label": f"Includes {_join_parts(parts)} with no date",
    }


def _join_parts(parts):
    """Join "a", "a and b" or "a, b and c"."""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"

