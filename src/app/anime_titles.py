"""An anime's shown title: its English title wherever Spine names it (AN-705).

MAL's English title comes first, then AniList's, then the original. Spine stores it on the
Item whenever it reads either provider for the title anyway, and a job fills in the rest
from AniList in bulk. Item.shown_title reads it.
"""

import logging
import time

import requests
from django.db.models import Case, F, Q, TextField, Value, When
from django.db.models.functions import Coalesce, Lower, NullIf

from app.models import Item, MediaTypes, Sources
from app.providers import anilist

logger = logging.getLogger(__name__)

# A provider this read didn't ask.
UNREAD = object()
# The fill job: 25 titles a request, spaced under AniList's limit of about 30 a minute,
# and a short wait on each, as every job read has.
FILL_CHUNK = 25
FILL_PACE = 2.5
FILL_TIMEOUT = 10
FILL_LIMIT = 2500


def shown_title_order(item_path="item__"):
    """Return the shown title, lowercased, as an expression to sort by (AN-705).

    Only an anime's is its stored English one; every other type sorts as before.
    """
    title = F(f"{item_path}title")
    return Lower(
        Case(
            When(
                **{f"{item_path}media_type": MediaTypes.ANIME.value},
                then=Coalesce(NullIf(F(f"{item_path}display_title"), Value("")), title),
            ),
            default=title,
            output_field=TextField(),
        ),
    )


def title_search(query, item_path="item__"):
    """Return a filter matching a title by its original or, for an anime, its English one (AN-705)."""
    return Q(**{f"{item_path}title__icontains": query}) | Q(
        **{f"{item_path}media_type": MediaTypes.ANIME.value, f"{item_path}display_title__icontains": query},
    )


def store_shown_title(item, *, mal=UNREAD, anilist=UNREAD):
    """Store an anime's shown title from what this read asked (AN-705).

    mal and anilist are each provider's English title, or None when it has none; one this
    read didn't ask is left out. MAL's always wins. AniList's only fills a title with no
    English one known yet, since a stored English title may be MAL's.
    """
    if item is None or item.media_type != MediaTypes.ANIME.value or item.source != Sources.MAL.value:
        return
    if mal is not UNREAD and mal:
        shown = mal
    elif anilist is UNREAD:
        return
    elif mal is UNREAD and item.display_title not in ("", item.title):
        return
    else:
        shown = anilist or item.title
    shown = str(shown).strip()
    if shown and shown != item.display_title:
        Item.objects.filter(pk=item.pk).update(display_title=shown)
        item.display_title = shown


def fill_missing(*, limit=FILL_LIMIT, sleep=time.sleep):
    """Fill the shown title of every anime Spine has read none for, from AniList (AN-705).

    Tracked or not, 25 titles a request, paced. A failed request costs only its own titles,
    which the next run asks again. A timeout ends the run, as AniList is hanging.
    """
    items = list(
        Item.objects.filter(
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            display_title="",
        ).order_by("pk")[:limit],
    )
    filled = 0
    for start in range(0, len(items), FILL_CHUNK):
        chunk = items[start : start + FILL_CHUNK]
        if start:
            sleep(FILL_PACE)
        try:
            titles = anilist.anime_english_titles([item.media_id for item in chunk], timeout=FILL_TIMEOUT)
        except anilist.READ_FAILURES as error:
            logger.warning("AniList's English titles can't be read for %s anime", len(chunk), exc_info=True)
            if isinstance(error, requests.exceptions.Timeout):
                break
            continue
        for item in chunk:
            # One AniList doesn't have says no more than one with no English title.
            store_shown_title(item, anilist=titles.get(str(item.media_id)))
            filled += bool(item.display_title)
    return {"waiting": len(items), "filled": filled}
