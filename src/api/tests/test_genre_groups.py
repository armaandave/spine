"""Shared genre groups.

Every official provider name, variants, open vocabularies, music, and the owner's raw
spellings for a drill-down.
"""

from datetime import UTC, datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from api.services import genre_groups
from api.services.genre_groups import (
    GROUP_LABELS,
    groups_for,
    normalize,
    raw_values_for,
)
from app.models import DiaryEntry, Item, ItemFilterFacet, MediaTypes, Sources

MOVIE = MediaTypes.MOVIE.value
TV = MediaTypes.TV.value
SEASON = MediaTypes.SEASON.value
EPISODE = MediaTypes.EPISODE.value
ANIME = MediaTypes.ANIME.value
MANGA = MediaTypes.MANGA.value
GAME = MediaTypes.GAME.value
BOOK = MediaTypes.BOOK.value
COMIC = MediaTypes.COMIC.value
MUSIC = MediaTypes.MUSIC.value

NONE = None
DRAMA = "drama"
COMEDY = "comedy"
ACTION = "action_adventure"
SFF = "scifi_fantasy"
HORROR = "horror"
CRIME = "thriller_crime"
MYSTERY = "mystery"
ROMANCE = "romance"
DOC = "documentary_nonfiction"
FAMILY = "family_kids"
HISTORY = "history_war"
SOL = "slice_of_life"
SPORTS = "sports"
MUSICAL = "music_musicals"
STRATEGY = "strategy_puzzle"

# Official vocabularies, each name written out with its group (NONE: deliberately
# none). A provider name nobody classified fails test_every_official_name_has_a_group.
TMDB_MOVIE = {
    "Action": ACTION, "Adventure": ACTION, "Animation": NONE, "Comedy": COMEDY,
    "Crime": CRIME, "Documentary": DOC, "Drama": DRAMA, "Family": FAMILY,
    "Fantasy": SFF, "History": HISTORY, "Horror": HORROR, "Music": MUSICAL,
    "Mystery": MYSTERY, "Romance": ROMANCE, "Science Fiction": SFF, "TV Movie": NONE,
    "Thriller": CRIME, "War": HISTORY, "Western": ACTION,
}
TMDB_TV = {
    "Action & Adventure": ACTION, "Animation": NONE, "Comedy": COMEDY, "Crime": CRIME,
    "Documentary": DOC, "Drama": DRAMA, "Family": FAMILY, "Kids": FAMILY,
    "Mystery": MYSTERY, "News": DOC, "Reality": DOC, "Sci-Fi & Fantasy": SFF,
    "Soap": DRAMA, "Talk": DOC, "War & Politics": HISTORY, "Western": ACTION,
}
MAL_GENRES = {
    "Action": ACTION, "Adventure": ACTION, "Avant Garde": NONE, "Award Winning": NONE,
    "Boys Love": ROMANCE, "Comedy": COMEDY, "Drama": DRAMA, "Fantasy": SFF,
    "Girls Love": ROMANCE, "Gourmet": SOL, "Horror": HORROR, "Mystery": MYSTERY,
    "Romance": ROMANCE, "Sci-Fi": SFF, "Slice of Life": SOL, "Sports": SPORTS,
    "Supernatural": SFF, "Suspense": CRIME,
}
MAL_EXPLICIT = {"Ecchi": NONE, "Erotica": NONE, "Hentai": NONE}
MAL_THEMES = {
    "Adult Cast": NONE, "Anthropomorphic": NONE, "CGDCT": SOL, "Childcare": SOL,
    "Combat Sports": SPORTS, "Crossdressing": NONE, "Delinquents": NONE,
    "Detective": MYSTERY, "Educational": DOC, "Gag Humor": COMEDY, "Gore": HORROR,
    "Harem": ROMANCE, "High Stakes Game": CRIME, "Historical": HISTORY,
    "Idols (Female)": MUSICAL, "Idols (Male)": MUSICAL, "Isekai": SFF, "Iyashikei": SOL,
    "Love Polygon": ROMANCE, "Love Status Quo": ROMANCE, "Magical Sex Shift": NONE,
    "Mahou Shoujo": SFF, "Martial Arts": ACTION, "Mecha": SFF, "Medical": DRAMA,
    "Military": HISTORY, "Music": MUSICAL, "Mythology": SFF, "Organized Crime": CRIME,
    "Otaku Culture": NONE, "Parody": COMEDY, "Performing Arts": MUSICAL, "Pets": SOL,
    "Psychological": CRIME, "Racing": SPORTS, "Reincarnation": SFF,
    "Reverse Harem": ROMANCE, "Samurai": HISTORY, "School": NONE, "Showbiz": NONE,
    "Space": SFF, "Strategy Game": STRATEGY, "Super Power": SFF, "Survival": ACTION,
    "Team Sports": SPORTS, "Time Travel": SFF, "Urban Fantasy": SFF, "Vampire": HORROR,
    "Video Game": NONE, "Villainess": NONE, "Visual Arts": NONE, "Workplace": SOL,
    # Manga only.
    "Memoir": DOC,
}
MAL_DEMOGRAPHICS = {"Josei": NONE, "Kids": FAMILY, "Seinen": NONE, "Shoujo": NONE, "Shounen": NONE}
MAL_OLD_NAMES = {
    "Thriller": CRIME, "Police": CRIME, "Game": STRATEGY, "Cars": SPORTS, "Magic": SFF,
    "Demons": SFF, "Dementia": NONE,
}
MANGAUPDATES = {
    "Action": ACTION, "Adult": NONE, "Adventure": ACTION, "Comedy": COMEDY,
    "Doujinshi": NONE, "Drama": DRAMA, "Ecchi": NONE, "Fantasy": SFF,
    "Gender Bender": NONE, "Harem": ROMANCE, "Hentai": NONE, "Historical": HISTORY,
    "Horror": HORROR, "Josei": NONE, "Lolicon": NONE, "Martial Arts": ACTION,
    "Mature": NONE, "Mecha": SFF, "Mystery": MYSTERY, "Psychological": CRIME,
    "Romance": ROMANCE, "School Life": SOL, "Sci-fi": SFF, "Seinen": NONE,
    "Shotacon": NONE, "Shoujo": NONE, "Shoujo Ai": ROMANCE, "Shounen": NONE,
    "Shounen Ai": ROMANCE, "Slice of Life": SOL, "Smut": NONE, "Sports": SPORTS,
    "Supernatural": SFF, "Tragedy": DRAMA, "Yaoi": ROMANCE, "Yuri": ROMANCE,
}
IGDB_GENRES = {
    "Point-and-click": ACTION, "Fighting": ACTION, "Shooter": ACTION, "Music": MUSICAL,
    "Platform": ACTION, "Puzzle": STRATEGY, "Racing": SPORTS,
    "Real Time Strategy (RTS)": STRATEGY, "Role-playing (RPG)": ACTION,
    "Simulator": NONE, "Sport": SPORTS, "Strategy": STRATEGY,
    "Turn-based strategy (TBS)": STRATEGY, "Tactical": STRATEGY,
    "Hack and slash/Beat 'em up": ACTION, "Quiz/Trivia": STRATEGY, "Pinball": NONE,
    "Adventure": ACTION, "Indie": NONE, "Arcade": NONE, "Visual Novel": NONE,
    "Card & Board Game": STRATEGY, "MOBA": STRATEGY,
}
IGDB_THEMES = {
    "Action": ACTION, "Fantasy": SFF, "Science fiction": SFF, "Horror": HORROR,
    "Thriller": CRIME, "Survival": ACTION, "Historical": HISTORY, "Stealth": ACTION,
    "Comedy": COMEDY, "Business": NONE, "Drama": DRAMA, "Non-fiction": DOC,
    "Sandbox": NONE, "Educational": DOC, "Kids": FAMILY, "Open world": NONE,
    "Warfare": HISTORY, "Party": NONE, "4X": STRATEGY, "Erotic": NONE,
    "Mystery": MYSTERY, "Romance": ROMANCE, "Western": ACTION, "Superhero": SFF,
}

VOCABULARIES = (
    ("TMDB movie", (MOVIE,), TMDB_MOVIE, 19),
    ("TMDB TV", (TV, SEASON, EPISODE), TMDB_TV, 16),
    ("MAL genres", (ANIME, MANGA), MAL_GENRES, 18),
    ("MAL explicit", (ANIME, MANGA), MAL_EXPLICIT, 3),
    ("MAL themes", (ANIME, MANGA), MAL_THEMES, 53),
    ("MAL demographics", (ANIME, MANGA), MAL_DEMOGRAPHICS, 5),
    ("MAL old names", (ANIME, MANGA), MAL_OLD_NAMES, 7),
    ("MangaUpdates", (MANGA,), MANGAUPDATES, 36),
    ("IGDB genres", (GAME,), IGDB_GENRES, 23),
    ("IGDB themes", (GAME,), IGDB_THEMES, 24),
)


def expected(group):
    """Return the groups a snapshot entry expects."""
    return frozenset() if group is NONE else frozenset({group})


class GroupTableTests(SimpleTestCase):
    """The group list and the exact tables."""

    def test_fifteen_groups_with_plain_labels(self):
        self.assertEqual(len(GROUP_LABELS), 15)
        self.assertEqual(GROUP_LABELS[SFF], "Sci-Fi & Fantasy")
        self.assertNotIn("animation", GROUP_LABELS)

    def test_every_official_name_has_a_group(self):
        for label, media_types, names, count in VOCABULARIES:
            self.assertEqual(len(names), count, label)
            for media_type in media_types:
                for name, group in names.items():
                    with self.subTest(vocabulary=label, media_type=media_type, name=name):
                        self.assertEqual(groups_for(media_type, name), expected(group))

    def test_every_table_entry_is_in_a_snapshot(self):
        # A name added to a table without a snapshot here fails, so every
        # classification is reviewed in this file.
        snapshot = {
            "tmdb": {normalize(n) for n in (*TMDB_MOVIE, *TMDB_TV)},
            "mal": {
                normalize(n)
                for n in (
                    *MAL_GENRES, *MAL_EXPLICIT, *MAL_THEMES, *MAL_DEMOGRAPHICS,
                    *MAL_OLD_NAMES, *MANGAUPDATES,
                )
            },
            "igdb": {normalize(n) for n in (*IGDB_GENRES, *IGDB_THEMES)},
        }
        tables = {
            "tmdb": genre_groups.TMDB_TABLE,
            "mal": genre_groups.MAL_TABLE,
            "igdb": genre_groups.IGDB_TABLE,
        }
        for key, table in tables.items():
            names = {normalize(name) for values in table.values() for name in values}
            self.assertEqual(names, snapshot[key], key)

    def test_a_name_never_sits_in_two_groups_of_one_table(self):
        for table in (genre_groups.TMDB_TABLE, genre_groups.MAL_TABLE, genre_groups.IGDB_TABLE):
            names = [normalize(name) for values in table.values() for name in values]
            self.assertEqual(len(names), len(set(names)))

    def test_unknown_and_empty_values_have_no_group(self):
        self.assertEqual(groups_for(MOVIE, "Telenovela"), frozenset())
        self.assertEqual(groups_for(ANIME, ""), frozenset())
        self.assertEqual(groups_for(GAME, None), frozenset())
        self.assertEqual(groups_for("podcast", "Drama"), frozenset())


class VariantTests(SimpleTestCase):
    """Case and punctuation variants map the same way."""

    def test_normalize(self):
        self.assertEqual(normalize("Sci-Fi & Fantasy"), "sci fi fantasy")
        self.assertEqual(normalize("Hack and slash/Beat 'em up"), "hack and slash beat em up")
        self.assertEqual(normalize("  Idols (Female) "), "idols female")
        self.assertEqual(normalize("Bokmål"), "bokmål")

    def test_case_and_punctuation_are_ignored(self):
        for value in ("Sci-Fi", "sci-fi", " SCI-FI ", "Sci-fi", "sci fi", "Sci_Fi"):
            with self.subTest(value=value):
                self.assertEqual(groups_for(MANGA, value), {SFF})
        self.assertEqual(groups_for(GAME, "Hack and Slash/Beat 'em Up"), {ACTION})
        self.assertEqual(groups_for(TV, "action & adventure"), {ACTION})

    def test_every_provider_spelling_of_science_fiction_reaches_one_group(self):
        self.assertEqual(groups_for(MOVIE, "Science Fiction"), {SFF})
        self.assertEqual(groups_for(TV, "Sci-Fi & Fantasy"), {SFF})
        self.assertEqual(groups_for(ANIME, "Sci-Fi"), {SFF})
        self.assertEqual(groups_for(GAME, "Science fiction"), {SFF})
        self.assertEqual(groups_for(BOOK, "Science fiction"), {SFF})

    def test_lookups_are_memoised(self):
        groups_for.cache_clear()
        groups_for(MOVIE, "Drama")
        groups_for(MOVIE, "Drama")

        self.assertEqual(groups_for.cache_info().hits, 1)


class BookAndComicRuleTests(SimpleTestCase):
    """Keyword rules for open vocabularies."""

    def test_book_samples(self):
        cases = {
            "Fiction, fantasy, general": {SFF},
            "Detective and mystery stories": {MYSTERY},
            "Love stories": {ROMANCE},
            "Juvenile fiction": {FAMILY},
            "Juvenile Fiction": {FAMILY},
            "Humorous stories": {COMEDY},
            "Science fiction": {SFF},
            "Science Fiction & Fantasy": {SFF},
            "Science": {DOC},
            "Popular science": {DOC},
            "True crime": {DOC, CRIME},
            "Historical romance": {HISTORY, ROMANCE},
            "World War, 1939-1945 -- Fiction": {HISTORY},
            "Children's stories": {FAMILY},
            "Self-Help": {DOC},
            "Mystery & Thrillers": {MYSTERY, CRIME},
            "Literary Fiction": {DRAMA},
            "Ghost stories": {HORROR},
            "Biography & Autobiography": {DOC},
        }
        for value, groups in cases.items():
            with self.subTest(value=value):
                self.assertEqual(groups_for(BOOK, value), groups)

    def test_book_values_with_no_group(self):
        for value in (
            "Accessible book",
            "Protected DAISY",
            "In library",
            "London (England) -- Fiction",
            "Fiction",
            "Award winners",
            "Warriors -- Fiction",
            "Star Wars",
            "Young Adult",
            "Classics",
            "Poetry",
            "LGBTQ",
            "Graphic novels",
            # Family as a tag means family stories, not children's books.
            "Family",
        ):
            with self.subTest(value=value):
                self.assertEqual(groups_for(BOOK, value), frozenset())

    def test_comic_samples(self):
        cases = {
            "Vampire": {HORROR},
            "Time Travel": {SFF},
            "Super Soldier": frozenset(),
            "Magic": {SFF},
            "Aliens": {SFF},
            "Martial Arts": {ACTION},
            "Serial Killer": {CRIME},
            "World War II": {HISTORY},
            "Detective": {MYSTERY},
            "Starwars": frozenset(),
            "Teleportation": frozenset(),
        }
        for value, groups in cases.items():
            with self.subTest(value=value):
                self.assertEqual(groups_for(COMIC, value), groups)

    def test_comics_and_books_ignore_the_fixed_tables(self):
        # "Kids" is a TMDB and MAL name; on open vocabularies only the rules apply.
        self.assertEqual(groups_for(COMIC, "Kids"), frozenset())
        self.assertEqual(groups_for(BOOK, "Shounen"), frozenset())


class MusicTests(SimpleTestCase):
    """Album genres stay out of every group."""

    def test_music_is_never_grouped(self):
        for value in ("drama", "musical", "Rock", "soundtrack", "comedy", "horror punk"):
            with self.subTest(value=value):
                self.assertEqual(groups_for(MUSIC, value), frozenset())


class RawValuesTests(TestCase):
    """The owner's stored spellings for a group."""

    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.other = users.create_user(username="other", password="strong-password-123")
        self.counter = 0

    def item(self, media_type, source=Sources.TMDB.value, media_id=None, *, genres=(), themes=(), **fields):
        self.counter += 1
        item = Item.objects.create(
            media_id=media_id or f"raw-{self.counter}",
            source=source,
            media_type=media_type,
            title=f"Title {self.counter}",
            image=settings.IMG_NONE,
            **fields,
        )
        for facet_type, values in (
            (ItemFilterFacet.FacetType.GENRE, genres),
            (ItemFilterFacet.FacetType.THEME, themes),
        ):
            for value in values:
                ItemFilterFacet.objects.create(item=item, facet_type=facet_type, value=value)
        return item

    def log(self, item, user=None):
        DiaryEntry.objects.create(
            user=user or self.owner,
            item=item,
            consumed_at=datetime(2026, 3, 1, 12, tzinfo=UTC),
        )

    def test_exact_spellings_of_the_owners_logged_items_per_type(self):
        self.log(self.item(MOVIE, genres=["Science Fiction", "Drama"]))
        self.log(self.item(ANIME, source=Sources.MAL.value, genres=["Sci-Fi"]))
        self.log(self.item(MANGA, source=Sources.MANGAUPDATES.value, genres=["Sci-fi"]))
        self.log(self.item(GAME, source=Sources.IGDB.value, genres=["Shooter"], themes=["Science fiction"]))
        self.log(self.item(BOOK, source=Sources.HARDCOVER.value, genres=["Fiction, fantasy, general"]))
        # Not logged by the owner: an unlogged Item and another user's log.
        self.item(MOVIE, genres=["Fantasy"])
        self.log(self.item(TV, genres=["Sci-Fi & Fantasy"]), user=self.other)

        self.assertEqual(
            raw_values_for(self.owner, SFF),
            [
                ("Fiction, fantasy, general", BOOK),
                ("Sci-Fi", ANIME),
                ("Sci-fi", MANGA),
                ("Science Fiction", MOVIE),
                ("Science fiction", GAME),
            ],
        )

    def test_a_logged_season_or_episode_brings_its_show(self):
        self.item(TV, media_id="1396", genres=["Mystery", "Drama"])
        self.log(self.item(SEASON, media_id="1396", season_number=1))
        self.item(TV, media_id="2000", genres=["Mystery"])
        self.log(self.item(EPISODE, media_id="2000", season_number=1, episode_number=1))
        # A show on another source with the same id stays out.
        self.item(TV, source=Sources.MANUAL.value, media_id="1396", genres=["Mystery & Crime"])

        self.assertEqual(raw_values_for(self.owner, MYSTERY), [("Mystery", TV)])

    def test_music_is_excluded(self):
        self.log(self.item(MUSIC, source=Sources.MUSICBRAINZ.value, genres=["drama", "Drama"]))
        self.log(self.item(MOVIE, genres=["Drama"]))

        self.assertEqual(raw_values_for(self.owner, DRAMA), [("Drama", MOVIE)])

    def test_values_outside_the_group_and_unknown_keys(self):
        self.log(self.item(MOVIE, genres=["Animation", "Comedy"]))

        self.assertEqual(raw_values_for(self.owner, HORROR), [])
        self.assertEqual(raw_values_for(self.owner, "animation"), [])

    def test_a_value_logged_twice_is_listed_once(self):
        movie = self.item(MOVIE, genres=["Horror"])
        self.log(movie)
        self.log(movie)
        self.log(self.item(MOVIE, genres=["Horror"]))

        self.assertEqual(raw_values_for(self.owner, HORROR), [("Horror", MOVIE)])
