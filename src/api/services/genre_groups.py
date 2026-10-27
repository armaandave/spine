"""Fold every provider's genre names into 15 shared groups for the Global stats view.

Each media type stores its provider's own genre names (TMDB, MAL, MangaUpdates, IGDB
genres and themes, Hardcover tags, Open Library subjects, ComicVine concepts). Global
stats can't compare those directly, so groups_for maps one stored value to the shared
groups it belongs to:

- Fixed vocabularies (TMDB, MAL, MangaUpdates, IGDB) use exact tables. A name in a
  table's NO_GROUP list is deliberately in no group (Animation is a format; Shounen is
  an audience).
- Open vocabularies (Hardcover and Open Library books, ComicVine comics) use keyword
  rules that match whole words, so one value can reach several groups.
- Music is never grouped: album genres are listed separately.

Matching ignores case and punctuation (normalize). Nothing derived is stored: the
groups are applied when stats are computed, so retuning a table is a code change.
"""

import re
from functools import lru_cache

from django.db.models import Exists, OuterRef, Q

from app.models import DiaryEntry, Item, ItemFilterFacet, MediaTypes

# Stable keys for the API, in display order, with the plain-word label sent to clients.
GROUPS = (
    ("drama", "Drama"),
    ("comedy", "Comedy"),
    ("action_adventure", "Action & Adventure"),
    ("scifi_fantasy", "Sci-Fi & Fantasy"),
    ("horror", "Horror"),
    ("thriller_crime", "Thriller & Crime"),
    ("mystery", "Mystery"),
    ("romance", "Romance"),
    ("documentary_nonfiction", "Documentary & Nonfiction"),
    ("family_kids", "Family & Kids"),
    ("history_war", "History & War"),
    ("slice_of_life", "Slice of Life"),
    ("sports", "Sports"),
    ("music_musicals", "Music & Musicals"),
    ("strategy_puzzle", "Strategy & Puzzle"),
)
GROUP_LABELS = dict(GROUPS)
NO_GROUP = None

# Facet types whose values are genres: IGDB themes carry a game's story genres.
GENRE_FACET_TYPES = (ItemFilterFacet.FacetType.GENRE, ItemFilterFacet.FacetType.THEME)

_NON_ALPHANUMERIC = re.compile(r"[\W_]+")


def normalize(value):
    """Casefold, collapse any run of non-alphanumerics into one space, and trim."""
    return _NON_ALPHANUMERIC.sub(" ", str(value or "").casefold()).strip()


# TMDB movie (19) and TV (16) genres.
TMDB_TABLE = {
    "action_adventure": ["Action", "Adventure", "Action & Adventure", "Western"],
    "comedy": ["Comedy"],
    "thriller_crime": ["Crime", "Thriller"],
    "documentary_nonfiction": ["Documentary", "News", "Reality", "Talk"],
    "drama": ["Drama", "Soap"],
    "family_kids": ["Family", "Kids"],
    "scifi_fantasy": ["Fantasy", "Science Fiction", "Sci-Fi & Fantasy"],
    "history_war": ["History", "War", "War & Politics"],
    "horror": ["Horror"],
    "music_musicals": ["Music"],
    "mystery": ["Mystery"],
    "romance": ["Romance"],
    NO_GROUP: ["Animation", "TV Movie"],
}

# MAL genres, explicit genres, themes and demographics (anime and manga share them),
# MAL's older names, and MangaUpdates' own genres.
MAL_TABLE = {
    "action_adventure": ["Action", "Adventure", "Martial Arts", "Survival"],
    "comedy": ["Comedy", "Gag Humor", "Parody"],
    "thriller_crime": [
        "Suspense",
        "High Stakes Game",
        "Organized Crime",
        "Psychological",
        "Thriller",
        "Police",
    ],
    "documentary_nonfiction": ["Educational", "Memoir"],
    "drama": ["Drama", "Medical", "Tragedy"],
    "family_kids": ["Kids"],
    "history_war": ["Historical", "Military", "Samurai"],
    "horror": ["Horror", "Gore", "Vampire"],
    "music_musicals": ["Idols (Female)", "Idols (Male)", "Music", "Performing Arts"],
    "mystery": ["Mystery", "Detective"],
    "romance": [
        "Boys Love",
        "Girls Love",
        "Romance",
        "Harem",
        "Reverse Harem",
        "Love Polygon",
        "Love Status Quo",
        "Shounen Ai",
        "Shoujo Ai",
        "Yaoi",
        "Yuri",
    ],
    "scifi_fantasy": [
        "Fantasy",
        "Sci-Fi",
        "Supernatural",
        "Isekai",
        "Mahou Shoujo",
        "Mecha",
        "Mythology",
        "Reincarnation",
        "Space",
        "Super Power",
        "Time Travel",
        "Urban Fantasy",
        "Magic",
        "Demons",
    ],
    "slice_of_life": [
        "Gourmet",
        "Slice of Life",
        "CGDCT",
        "Childcare",
        "Iyashikei",
        "Pets",
        "Workplace",
        "School Life",
    ],
    "sports": ["Sports", "Combat Sports", "Racing", "Team Sports", "Cars"],
    "strategy_puzzle": ["Strategy Game", "Game"],
    NO_GROUP: [
        "Avant Garde",
        "Award Winning",
        "Ecchi",
        "Erotica",
        "Hentai",
        "Adult Cast",
        "Anthropomorphic",
        "Crossdressing",
        "Delinquents",
        "Magical Sex Shift",
        "Otaku Culture",
        "School",
        "Showbiz",
        "Video Game",
        "Villainess",
        "Visual Arts",
        "Josei",
        "Seinen",
        "Shoujo",
        "Shounen",
        "Dementia",
        "Adult",
        "Doujinshi",
        "Gender Bender",
        "Lolicon",
        "Mature",
        "Shotacon",
        "Smut",
    ],
}

# IGDB genres (23) and themes.
IGDB_TABLE = {
    "action_adventure": [
        "Adventure",
        "Fighting",
        "Hack and slash/Beat 'em up",
        "Platform",
        "Point-and-click",
        "Role-playing (RPG)",
        "Shooter",
        "Action",
        "Stealth",
        "Survival",
        "Western",
    ],
    "comedy": ["Comedy"],
    "thriller_crime": ["Thriller"],
    "documentary_nonfiction": ["Non-fiction", "Educational"],
    "drama": ["Drama"],
    "family_kids": ["Kids"],
    "history_war": ["Historical", "Warfare"],
    "horror": ["Horror"],
    "music_musicals": ["Music"],
    "mystery": ["Mystery"],
    "romance": ["Romance"],
    "scifi_fantasy": ["Fantasy", "Science fiction", "Superhero"],
    "sports": ["Racing", "Sport"],
    "strategy_puzzle": [
        "Card & Board Game",
        "MOBA",
        "Puzzle",
        "Quiz/Trivia",
        "Real Time Strategy (RTS)",
        "Strategy",
        "Tactical",
        "Turn-based strategy (TBS)",
        "4X",
    ],
    NO_GROUP: [
        "Arcade",
        "Indie",
        "Pinball",
        "Simulator",
        "Visual Novel",
        "Business",
        "Erotic",
        "Open world",
        "Party",
        "Sandbox",
    ],
}

# Book subjects and tags (Hardcover, Open Library): whole-word phrases.
BOOK_KEYWORDS = {
    "scifi_fantasy": [
        "science fiction", "sci fi", "scifi", "fantasy", "dystopia", "dystopian",
        "space opera", "cyberpunk", "steampunk", "paranormal", "supernatural", "magic",
        "wizards", "witches", "dragons", "time travel", "mythology",
    ],
    "horror": ["horror", "ghost", "ghosts", "ghost stories", "vampires", "zombies", "werewolves"],
    "thriller_crime": [
        "thriller", "thrillers", "suspense", "crime", "noir", "spy", "espionage",
    ],
    "mystery": ["mystery", "mysteries", "detective", "whodunit"],
    "romance": ["romance", "love stories", "love story", "romantic"],
    "comedy": ["humor", "humour", "humorous", "comedy", "satire"],
    "documentary_nonfiction": [
        "nonfiction", "non fiction", "biography", "autobiography", "memoir", "memoirs",
        "essays", "true crime", "self help", "popular science", "journalism",
    ],
    "history_war": ["history", "historical", "war", "world war", "military"],
    "family_kids": ["juvenile", "children", "picture books", "middle grade"],
    "sports": ["sports", "baseball", "football", "basketball", "soccer"],
    "music_musicals": ["music", "musicians", "musicals"],
    "action_adventure": ["adventure", "action", "western", "westerns", "martial arts"],
    "drama": ["literary fiction", "drama", "tragedy", "family saga", "domestic fiction"],
    "strategy_puzzle": ["puzzles", "chess"],
}
# Book values that reach a group only as the whole value.
BOOK_WHOLE_VALUES = {"science": "documentary_nonfiction"}

# ComicVine concepts: whole-word phrases. Concepts are tropes, so coverage is partial.
# "soldier" is left out of History & War so "Super Soldier" stays ungrouped.
COMIC_KEYWORDS = {
    "horror": [
        "vampire", "vampires", "zombie", "zombies", "werewolf", "werewolves", "ghost",
        "ghosts", "demon", "demons", "monster", "monsters", "horror",
    ],
    "scifi_fantasy": [
        "alien", "aliens", "robot", "robots", "cyborg", "cyborgs", "android", "androids",
        "time travel", "time machine", "space", "mutant", "mutants", "multiverse",
        "alternate reality", "artificial intelligence", "apocalypse", "dystopia",
        "magic", "sorcery", "wizard", "wizards", "witch", "witches", "dragon", "dragons",
        "mythology", "superhero", "superheroes",
    ],
    "thriller_crime": [
        "crime", "gang", "gangs", "mafia", "heist", "serial killer", "assassin", "assassins",
    ],
    "mystery": ["detective", "detectives", "mystery"],
    "history_war": ["world war", "military"],
    "action_adventure": [
        "martial arts", "ninja", "ninjas", "samurai", "pirate", "pirates", "western",
        "cowboy", "cowboys",
    ],
    "comedy": ["humor", "comedy", "parody"],
    "romance": ["romance"],
    "sports": ["sports"],
}


def _exact(table):
    """Invert a readable {group: [names]} table to {normalized name: frozenset}."""
    lookup = {}
    for group, names in table.items():
        for name in names:
            key = normalize(name)
            groups = lookup.get(key, frozenset())
            lookup[key] = groups | {group} if group else groups
    return lookup


def _keywords(table):
    """Compile a {group: [phrases]} table to [(group, whole-word pattern)]."""
    return [
        (
            group,
            re.compile(
                r"(?:^| )(?:" + "|".join(re.escape(normalize(p)) for p in phrases) + r")(?: |$)",
            ),
        )
        for group, phrases in table.items()
    ]


_TMDB = _exact(TMDB_TABLE)
_MAL = _exact(MAL_TABLE)
_IGDB = _exact(IGDB_TABLE)
_BOOK_RULES = _keywords(BOOK_KEYWORDS)
_COMIC_RULES = _keywords(COMIC_KEYWORDS)

_EXACT_BY_TYPE = {
    MediaTypes.MOVIE.value: _TMDB,
    MediaTypes.TV.value: _TMDB,
    MediaTypes.SEASON.value: _TMDB,
    MediaTypes.EPISODE.value: _TMDB,
    MediaTypes.ANIME.value: _MAL,
    MediaTypes.MANGA.value: _MAL,
    MediaTypes.GAME.value: _IGDB,
}


@lru_cache(maxsize=8192)
def groups_for(media_type, value):
    """Return the group keys a stored genre value of a media type belongs to.

    An empty frozenset means no group: a deliberately ungrouped or unknown name, or
    any music genre.
    """
    if media_type == MediaTypes.MUSIC.value:
        return frozenset()
    normalized = normalize(value)
    if not normalized:
        return frozenset()
    exact = _EXACT_BY_TYPE.get(media_type)
    if exact is not None:
        return exact.get(normalized, frozenset())
    if media_type == MediaTypes.BOOK.value:
        whole = BOOK_WHOLE_VALUES.get(normalized)
        if whole:
            return frozenset({whole})
        rules = _BOOK_RULES
    elif media_type == MediaTypes.COMIC.value:
        rules = _COMIC_RULES
    else:
        return frozenset()
    return frozenset(group for group, pattern in rules if pattern.search(normalized))


def raw_values_for(owner, group_key):
    """Return [(value, media_type)] for the owner's stored genre values in a group.

    Values are the exact stored spellings of genre and theme facets
    (GENRE_FACET_TYPES) on Items the owner logged, so a drill-down can match them
    with an indexed (facet_type, value) lookup per media type. A logged season or
    episode also brings its show's values, paired with "tv", because stats count the
    show. Music is never included.
    """
    if group_key not in GROUP_LABELS:
        return []
    logged = DiaryEntry.objects.filter(user=owner)
    shows = Item.objects.filter(media_type=MediaTypes.TV.value).filter(
        Exists(
            logged.filter(
                item__media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
                item__source=OuterRef("source"),
                item__media_id=OuterRef("media_id"),
            ),
        ),
    )
    pairs = set(
        ItemFilterFacet.objects.filter(facet_type__in=GENRE_FACET_TYPES)
        .filter(Q(item__in=logged.values("item_id")) | Q(item__in=shows.values("id")))
        .exclude(item__media_type=MediaTypes.MUSIC.value)
        .values_list("value", "item__media_type")
        .distinct(),
    )
    return sorted(
        (value, media_type)
        for value, media_type in pairs
        if group_key in groups_for(media_type, value)
    )
