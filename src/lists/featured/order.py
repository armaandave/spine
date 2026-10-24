"""The curated order of featured lists: shelves, the "Now" strip, and first syncs.

This is the one file to edit in the weekly review (docs/featured-lists/README.md,
"How to change the order").

- ``SHELF_ORDER``: per media type, the keys that lead that shelf, in order. Live lists
  still come first; lists not named here follow by ``position``, then name. A list
  with two media types follows each shelf's own order.
- ``NOW_PRIORITY``: the charts allowed in "Now", in order. A chart shows there only
  while its content changed in the last 7 days, at most ``NOW_PER_TYPE`` per media
  type. Charts not named here never appear in "Now".
- The dispatcher syncs never-synced lists in this order too, so flagship lists fill
  first after a deploy.

Unknown keys are ignored at runtime, but ``problems()`` (run by the registry tests)
fails on any key that isn't in the registry, unless it's in ``ALLOWED_MISSING``.
Place a live ceremony list right after its award's all-time list: its live state
floats it to the top during its window.
"""

from app.models import PRIMARY_MEDIA_TYPES

NOW_PER_TYPE = 2

# Keys named below that may be absent from the registry (planned lists).
ALLOWED_MISSING = frozenset()

SHELF_ORDER = {
    "movie": [
        "letterboxd-top-500",
        "imdb-top-250-movies",
        "netflix-top-10-films-english",
        "trakt-weekend-box-office",
        "oscars-best-picture",
        "oscars-2027",
        "sight-and-sound-2022-critics",
        "tmdb-trending-movies-week",
        "criterion-collection",
        "afi-100-years-100-movies-2007",
        "1001-movies-you-must-see-before-you-die-2021",
        "cannes-palme-dor",
        "nyt-100-best-movies-21st-century",
        "golden-globes-best-motion-picture-drama",
        "golden-globes-2027-film",
        "imdb-moviemeter-movies",
        "justwatch-streaming-charts-movies",
        "tspdt-1000-greatest-films-2026",
        "edgar-wright-1000-favourite-films",
        "letterboxd-top-250-horror",
        "letterboxd-top-250-science-fiction",
        "letterboxd-top-250-documentary",
        "letterboxd-top-250-animated",
        "letterboxd-top-250-romantic-comedy",
        "letterboxd-top-100-anime-films",
        "netflix-top-10-films-non-english",
        "national-film-registry",
        "bafta-best-film",
        "venice-golden-lion",
        "berlinale-golden-bear",
        "netflix-most-popular-films",
        "sight-and-sound-2022-directors",
        "bbc-culture-100-greatest-films-21st-century",
        "quentin-tarantino-sight-and-sound-2012-ballot",
        "martin-scorsese-39-essential-foreign-films",
        "stanley-kubrick-top-10-1963",
        "bong-joon-ho-sight-and-sound-2022-ballot",
        "nielsen-streaming-top-10-movies",
        "trakt-trending-movies",
        "golden-globes-best-motion-picture-musical-or-comedy",
    ],
    "tv": [
        "imdb-top-250-tv",
        "netflix-top-10-tv-english",
        "emmys-outstanding-drama-series",
        "tmdb-trending-tv-week",
        "letterboxd-top-250-miniseries",
        "bbc-culture-100-greatest-tv-series-21st-century",
        "rolling-stone-100-greatest-tv-shows",
        "golden-globes-best-television-series-drama",
        "golden-globes-2027-tv",
        "imdb-moviemeter-tv",
        "justwatch-streaming-charts-tv",
        "emmys-outstanding-comedy-series",
        "emmys-outstanding-limited-series",
        "nielsen-streaming-top-10",
        "wga-101-best-written-tv-series",
        "netflix-top-10-tv-non-english",
        "netflix-most-popular-tv",
        "trakt-trending-shows",
        "golden-globes-best-television-series-musical-or-comedy",
    ],
    "book": [
        "nyt-combined-print-and-e-book-fiction",
        "nyt-combined-print-and-e-book-nonfiction",
        "pulitzer-fiction",
        "booker-prize",
        "booker-prize-2026",
        "goodreads-choice-fiction",
        "nyt-100-best-books-21st-century",
        "oprahs-book-club",
        "reeses-book-club",
        "apple-books-top-paid",
        "national-book-award-fiction",
        "national-book-awards-2026",
        "hardcover-trending",
        "read-with-jenna",
        "womens-prize-fiction",
        "hugo-best-novel",
        "international-booker-prize",
        "modern-library-100-best-novels",
        "time-all-time-100-novels",
        "guardian-100-best-books-21st-century",
        "bbc-big-read-top-100",
        "le-monde-100-books-of-the-century",
        "obama-favorite-books-2025",
        "nobel-prize-literature",
        # The other NYT lists
        "nyt-advice-how-to-and-miscellaneous",
        "nyt-business-books",
        "nyt-graphic-books-and-manga",
        "nyt-hardcover-fiction",
        "nyt-hardcover-nonfiction",
        "nyt-trade-fiction-paperback",
        "nyt-paperback-nonfiction-monthly",
        "nyt-young-adult-hardcover",
        "nyt-young-adult-paperback-monthly",
        "nyt-childrens-middle-grade-hardcover",
        "nyt-middle-grade-paperback-monthly",
        "nyt-picture-books",
        "nyt-series-books",
        "nyt-audio-fiction",
        "nyt-audio-nonfiction",
        "nyt-audio-childrens",
        "nyt-audio-advice-how-to-and-miscellaneous",
        # The other Pulitzer categories
        "pulitzer-general-nonfiction",
        "pulitzer-history",
        "pulitzer-biography",
        "pulitzer-memoir",
        "pulitzer-poetry",
        # The other National Book Award categories
        "national-book-award-nonfiction",
        "national-book-award-poetry",
        "national-book-award-translated-literature",
        "national-book-award-young-peoples-literature",
        # The other Goodreads Choice categories
        "goodreads-choice-mystery-thriller",
        "goodreads-choice-historical-fiction",
        "goodreads-choice-fantasy",
        "goodreads-choice-romance",
        "goodreads-choice-science-fiction",
        "goodreads-choice-horror",
        "goodreads-choice-nonfiction",
        "goodreads-choice-young-adult-fiction",
        "newbery-medal",
        "caldecott-medal",
        "apple-books-top-free",
        "apple-books-top-audiobooks",
        "openlibrary-trending-daily",
        "openlibrary-trending-weekly",
    ],
    "game": [
        "game-awards-game-of-the-year",
        "the-game-awards-2026",
        "steam-weekly-top-sellers",
        "igdb-most-wishlisted-upcoming",
        "twitch-top-games",
        "golden-joystick-game-of-the-year",
        "golden-joystick-awards-2026",
        "time-all-time-100-video-games",
        "ign-top-100-video-games-2021",
        "steam-awards-game-of-the-year",
        "steam-most-played",
        "edge-100-greatest-games-2023",
        "dice-game-of-the-year",
        "bafta-games-best-game",
        "gdc-game-of-the-year",
        "igdb-playing",
        "igdb-want-to-play",
        "world-video-game-hall-of-fame",
        "moma-video-game-collection",
        "steam-awards-2025",
        "steam-awards-2024",
        "steam-awards-2023",
    ],
    "anime": [
        "mal-this-season-anime",
        "mal-top-anime",
        "mal-top-airing-anime",
        "crunchyroll-anime-of-the-year",
        "mal-most-popular-anime",
    ],
    "manga": [
        "mal-top-manga",
        "kono-manga-ga-sugoi-mens-top-10",
        "kono-manga-ga-sugoi-womens-top-10",
        "manga-taisho",
        "mal-most-popular-manga",
        "mangaupdates-top-rated",
        "kono-manga-ga-sugoi-number-ones",
    ],
    "comic": [
        "eisner-best-graphic-album-new",
        "time-all-time-10-graphic-novels",
        "eisner-best-continuing-series",
    ],
    "music": [
        "rolling-stone-500-greatest-albums",
        "apple-music-top-albums-us",
        "grammy-album-of-the-year",
        "grammy-awards-2027",
        "pitchfork-best-new-album",
        "billboard-200-number-one-albums",
        "1001-albums-you-must-hear",
        "apple-music-100-best-albums",
        "pitchfork-best-albums-of-the-year",
        "the-needle-drop-top-albums-of-the-year",
        "official-charts-uk-albums",
        "mercury-prize-winners",
        "listenbrainz-top-albums-week",
        "1001-albums-generator-highest-rated",
        "guardian-100-best-albums-ever",
        "pitchfork-100-best-albums-2020s-so-far",
        "pitchfork-200-best-albums-2010s",
        "pitchfork-150-best-albums-1990s",
        "pitchfork-200-best-albums-1980s",
        "pitchfork-100-best-albums-1970s",
        "pitchfork-200-best-albums-1960s",
        "apple-music-top-albums-uk",
        "listenbrainz-top-albums-all-time",
    ],
}

NOW_PRIORITY = [
    "netflix-top-10-films-english",
    "netflix-top-10-tv-english",
    "nyt-combined-print-and-e-book-fiction",
    "trakt-weekend-box-office",
    "steam-weekly-top-sellers",
    "apple-music-top-albums-us",
    "mal-this-season-anime",
    "nyt-combined-print-and-e-book-nonfiction",
    "tmdb-trending-movies-week",
    "tmdb-trending-tv-week",
    "billboard-200-number-one-albums",
    "twitch-top-games",
    "apple-books-top-paid",
    "official-charts-uk-albums",
    "igdb-most-wishlisted-upcoming",
]


def shelf_index(media_type):
    """Return ``{key: index}`` for one shelf's curated keys (empty when it has none)."""
    return {key: index for index, key in enumerate(SHELF_ORDER.get(media_type, ()))}


def now_index():
    """Return ``{key: index}`` for the charts allowed in "Now"."""
    return {key: index for index, key in enumerate(NOW_PRIORITY)}


def sync_ranks():
    """Return ``{key: rank}``: a key's best index across every shelf it leads.

    The dispatcher syncs equally due lists in this order, so the first list of every
    shelf comes before the second of any.
    """
    ranks = {}
    for keys in SHELF_ORDER.values():
        for index, key in enumerate(keys):
            ranks[key] = min(index, ranks.get(key, index))
    return ranks


def problems(definitions):
    """Return every problem with the curated order against ``definitions``."""
    by_key = {definition.key: definition for definition in definitions}
    found = []

    def check(key, where):
        definition = by_key.get(key)
        if definition is None:
            if key not in ALLOWED_MISSING:
                found.append(f"{where}: unknown key {key!r}")
            return None
        return definition

    for media_type, keys in SHELF_ORDER.items():
        where = f"SHELF_ORDER[{media_type!r}]"
        if media_type not in PRIMARY_MEDIA_TYPES:
            found.append(f"{where}: unknown media type")
        if len(set(keys)) != len(keys):
            found.append(f"{where}: duplicate keys")
        for key in keys:
            definition = check(key, where)
            if definition is not None and media_type not in definition.media_types:
                found.append(f"{where}: {key!r} is not a {media_type} list")
    if len(set(NOW_PRIORITY)) != len(NOW_PRIORITY):
        found.append("NOW_PRIORITY: duplicate keys")
    for key in NOW_PRIORITY:
        definition = check(key, "NOW_PRIORITY")
        if definition is not None and definition.kind != "chart":
            found.append(f"NOW_PRIORITY: {key!r} is not a chart")
    return found
