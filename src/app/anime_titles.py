"""An anime's shown title: its English title wherever Spine names it (AN-705).

MAL's English title comes first, then AniList's, then the original. Spine stores it on the
Item whenever it reads either provider for the title anyway, and a job fills in the rest
from AniList in bulk. Item.shown_title reads it.
"""

from app.models import Item, MediaTypes, Sources

# A provider this read didn't ask.
UNREAD = object()


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
