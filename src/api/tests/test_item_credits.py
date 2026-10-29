"""Credits (ItemCredit) stored from provider metadata by store_item_metadata (STAT-219).

The metadata is built with the providers' own helpers from raw provider answers, so
these tests follow the shapes the fill and the detail pages really store.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from api.services.item_metadata import (
    CURRENT_METADATA_VERSION,
    is_metadata_current,
    metadata_credits,
    store_item_credits,
    store_item_metadata,
)
from app import metadata_fill
from app.models import Item, ItemCredit, MediaTypes, Movie, Sources
from app.providers import apple_music, hardcover, musicbrainz, tmdb

PROFILE = "https://image.tmdb.org/t/p/w500"


def tmdb_credits(cast_count=18):
    """Return a raw TMDB ``credits`` answer: two directors, a writer and billed cast."""
    cast = [
        {"id": 500 + index, "name": f"Actor {index}", "character": f"Role {index}", "order": index, "profile_path": f"/a{index}.jpg"}
        for index in range(cast_count)
    ]
    # One actor in two roles, and one without a picture.
    cast.insert(3, {"id": 501, "name": "Actor 1", "character": "Second role", "order": 3, "profile_path": None})
    if len(cast) > 5:
        cast[5]["profile_path"] = None
    return {
        "cast": cast,
        "crew": [
            {"id": 1, "name": "Lana Wachowski", "job": "Director", "profile_path": "/lana.jpg"},
            {"id": 2, "name": "Lilly Wachowski", "job": "Director", "profile_path": None},
            {"id": 3, "name": "A Writer", "job": "Screenplay", "profile_path": "/w.jpg"},
        ],
    }


def movie_metadata(raw_credits=None):
    """Return the credit keys of tmdb.movie()'s metadata, from the same helpers."""
    raw_credits = tmdb_credits() if raw_credits is None else raw_credits
    return {
        "source": Sources.TMDB.value,
        "release_date": "1999-03-31",
        "runtime": 136,
        "details": {"directors": tmdb.get_directors(raw_credits)},
        "cast": tmdb.get_cast(raw_credits),
        "crew": tmdb.get_crew(raw_credits),
    }


def tv_response(**extra):
    """Return a raw TMDB TV answer, as tmdb.tv() and the season batches get it."""
    return {
        "id": 1396,
        "name": "Breaking Bad",
        "number_of_episodes": 62,
        "number_of_seasons": 5,
        "poster_path": "/poster.jpg",
        "backdrop_path": None,
        "overview": "A teacher.",
        "genres": [{"id": 18, "name": "Drama"}],
        "vote_average": 8.9,
        "vote_count": 1000,
        "popularity": 100,
        "original_language": "en",
        "spoken_languages": [{"iso_639_1": "en", "english_name": "English", "name": "English"}],
        "first_air_date": "2008-01-20",
        "last_air_date": "2013-09-29",
        "status": "Ended",
        "episode_run_time": [47],
        "content_ratings": {"results": []},
        "production_companies": [],
        "production_countries": [],
        "created_by": [
            {"id": 66633, "name": "Vince Gilligan", "profile_path": "/vince.jpg"},
            {"id": 66634, "name": "Second Creator", "profile_path": None},
        ],
        "credits": {"cast": tmdb_credits(4)["cast"], "crew": []},
        "seasons": [],
        "recommendations": {"results": []},
        "external_ids": {},
        **extra,
    }


class CreditTestCase(TestCase):
    """Item and credit helpers."""

    counter = 0

    def item(self, media_type=MediaTypes.MOVIE.value, source=Sources.TMDB.value, **fields):
        CreditTestCase.counter += 1
        return Item.objects.create(
            media_id=str(9000 + CreditTestCase.counter),
            source=source,
            media_type=media_type,
            title=f"Title {CreditTestCase.counter}",
            image=settings.IMG_NONE,
            **fields,
        )

    def credits(self, item, role=None):
        rows = ItemCredit.objects.filter(item=item).order_by("role", "order")
        if role is not None:
            rows = rows.filter(role=role)
        return [(row.person_source, row.person_id, row.name, row.image_url) for row in rows]


class StoredCreditsTests(CreditTestCase):
    """Each type's credits, from realistic metadata."""

    def test_a_movie_keeps_its_directors_and_top_15_billed_cast(self):
        film = self.item()

        store_item_metadata(film, movie_metadata())

        self.assertEqual(
            self.credits(film, "director"),
            [
                ("tmdb", "1", "Lana Wachowski", f"{PROFILE}/lana.jpg"),
                ("tmdb", "2", "Lilly Wachowski", ""),
            ],
        )
        cast = self.credits(film, "cast")
        # get_cast keeps 15 billed entries; Actor 1's second role counts once.
        self.assertEqual(len(cast), 14)
        self.assertEqual(cast[0], ("tmdb", "500", "Actor 0", f"{PROFILE}/a0.jpg"))
        self.assertEqual([row[1] for row in cast].count("501"), 1)
        # No picture is a blank image, never TMDB's placeholder.
        self.assertEqual(cast[4][3], "")
        self.assertEqual(
            list(ItemCredit.objects.filter(item=film, role="cast").values_list("order", flat=True)),
            list(range(14)),
        )

    def test_a_show_keeps_its_creators_and_cast_from_tmdbs_answer(self):
        show = self.item(MediaTypes.TV.value)

        store_item_metadata(show, tmdb.process_tv(tv_response()))

        self.assertEqual(
            self.credits(show, "creator"),
            [
                ("tmdb", "66633", "Vince Gilligan", f"{PROFILE}/vince.jpg"),
                ("tmdb", "66634", "Second Creator", ""),
            ],
        )
        self.assertEqual([row[2] for row in self.credits(show, "cast")], ["Actor 0", "Actor 1", "Actor 2", "Actor 3"])

    def test_a_show_without_credits_keeps_its_stored_cast(self):
        show = self.item(MediaTypes.TV.value)
        store_item_metadata(show, tmdb.process_tv(tv_response()))

        # An answer without "credits" (cast None) only replaces the creators.
        store_item_metadata(show, tmdb.process_tv(tv_response(credits=None, created_by=[])))

        self.assertEqual(self.credits(show, "creator"), [])
        self.assertEqual(len(self.credits(show, "cast")), 4)

    def test_seasons_episodes_and_other_types_store_none(self):
        season = self.item(MediaTypes.SEASON.value, season_number=1)
        episode = self.item(MediaTypes.EPISODE.value, season_number=1, episode_number=1)
        anime = self.item(MediaTypes.ANIME.value, Sources.MAL.value)
        for item in (season, episode, anime):
            store_item_metadata(item, movie_metadata())

        self.assertFalse(ItemCredit.objects.exists())

    def test_an_apple_album_keeps_its_artists_with_their_pictures(self):
        album = self.item(MediaTypes.MUSIC.value, Sources.APPLE_MUSIC.value)
        artwork = {"url": "https://is1-ssl.mzstatic.com/image/thumb/sza/{w}x{h}{c}.{f}", "width": 2400, "height": 2400}
        resource = {
            "attributes": {"artistName": "SZA & Travis Scott"},
            "relationships": {
                "artists": {
                    "data": [
                        {"id": "605800394", "type": "artists", "attributes": {"name": "SZA", "artwork": artwork}},
                        {"id": "549236696", "type": "artists", "attributes": {"name": "Travis Scott"}},
                    ],
                },
            },
        }

        store_item_metadata(album, {"runtime": 68, "details": {"artist_credits": apple_music._artist_credits(resource)}})

        self.assertEqual(
            self.credits(album),
            [
                ("apple_music", "605800394", "SZA", "https://is1-ssl.mzstatic.com/image/thumb/sza/600x600bb.jpg"),
                ("apple_music", "549236696", "Travis Scott", ""),
            ],
        )

    def test_a_musicbrainz_album_keeps_its_artists_but_not_various_artists(self):
        album = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value)
        compilation = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value)
        raw = [
            {"name": "Daft Punk", "joinphrase": " & ", "artist": {"id": "056e4f3e-d505-4dad-8ec1-d04f521cbb56", "name": "Daft Punk"}},
            {"name": "Pharrell", "joinphrase": "", "artist": {"id": "149e6720-4e4a-41a4-afca-6d29083fc091", "name": "Pharrell Williams"}},
        ]
        various = [{"name": "Various Artists", "artist": {"id": "89ad4ac3-39f7-470e-963a-56509c546377"}}]

        store_item_metadata(album, {"runtime": 74, "details": {"artist_credits": musicbrainz._artist_credits(raw)}})
        store_item_metadata(compilation, {"runtime": 50, "music": {"artist_credit": musicbrainz._artist_credits(various)}})

        self.assertEqual(
            [row[:3] for row in self.credits(album)],
            [
                ("musicbrainz", "056e4f3e-d505-4dad-8ec1-d04f521cbb56", "Daft Punk"),
                ("musicbrainz", "149e6720-4e4a-41a4-afca-6d29083fc091", "Pharrell"),
            ],
        )
        self.assertEqual(self.credits(compilation), [])

    def test_artists_without_an_id_never_replace_stored_ones(self):
        album = self.item(MediaTypes.MUSIC.value, Sources.APPLE_MUSIC.value)
        store_item_metadata(
            album,
            {"runtime": 40, "details": {"artist_credits": [{"name": "SZA", "artist_source": "apple_music", "artist_id": "605800394"}]}},
        )

        # Apple's answer without its artists relationship credits the name alone.
        store_item_metadata(album, {"runtime": 40, "details": {"artist_credits": apple_music._artist_credits({"attributes": {"artistName": "SZA"}})}})

        self.assertEqual([row[1] for row in self.credits(album)], ["605800394"])

    def test_hardcover_books_keep_their_authors_from_the_page_and_the_batch(self):
        page_book = self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value)
        batch_book = self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value)
        raw = {
            "id": 1,
            "title": "Good Omens",
            "pages": 400,
            "cached_contributors": "Terry Pratchett",
            "contributions": [
                {"contribution": None, "author": {"id": 101, "name": "Terry Pratchett", "cached_image": "https://hc/terry.jpg"}},
                {"contribution": "Author", "author": {"id": 102, "name": "Neil Gaiman", "cached_image": None}},
                {"contribution": "Illustrator", "author": {"id": 103, "name": "An Illustrator"}},
            ],
        }

        store_item_metadata(page_book, {"details": {"authors": hardcover.get_authors(raw)}})
        store_item_metadata(batch_book, hardcover._book_metadata(raw, None, {}))

        expected = [
            ("hardcover", "101", "Terry Pratchett", "https://hc/terry.jpg"),
            ("hardcover", "102", "Neil Gaiman", ""),
        ]
        self.assertEqual(self.credits(page_book), expected)
        self.assertEqual(self.credits(batch_book), expected)

    def test_open_library_books_keep_their_authors(self):
        book = self.item(MediaTypes.BOOK.value, Sources.OPENLIBRARY.value)
        authors = [{"name": "Ursula K. Le Guin", "person_id": "OL31353A", "source": "openlibrary"}]

        store_item_metadata(book, {"details": {"authors": authors}})

        self.assertEqual(self.credits(book), [("openlibrary", "OL31353A", "Ursula K. Le Guin", "")])

    def test_a_name_only_author_isnt_carried(self):
        book = self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value)

        self.assertEqual(metadata_credits(book, {"details": {"authors": [{"name": "Anonymous"}]}}), {})


class ReplaceCreditsTests(CreditTestCase):
    """Storing again is idempotent and replaces what changed."""

    def test_storing_the_same_credits_again_reads_once_and_writes_nothing(self):
        film = self.item()
        metadata = movie_metadata()
        self.assertTrue(store_item_credits(film, metadata))
        ids = sorted(ItemCredit.objects.filter(item=film).values_list("id", flat=True))

        with self.assertNumQueries(1):
            self.assertFalse(store_item_credits(film, metadata))

        self.assertEqual(sorted(ItemCredit.objects.filter(item=film).values_list("id", flat=True)), ids)

    def test_new_credits_replace_the_old_ones(self):
        film = self.item()
        store_item_credits(film, movie_metadata())
        changed = tmdb_credits(2)
        changed["crew"] = [{"id": 9, "name": "New Director", "job": "Director", "profile_path": None}]

        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(store_item_credits(film, movie_metadata(changed)))

        writes = [query["sql"].split()[0] for query in queries if query["sql"].startswith(("INSERT", "DELETE", "UPDATE"))]
        self.assertEqual(writes, ["DELETE", "INSERT"])
        self.assertEqual(self.credits(film, "director"), [("tmdb", "9", "New Director", "")])
        self.assertEqual([row[2] for row in self.credits(film, "cast")], ["Actor 0", "Actor 1"])

    def test_other_items_keep_their_credits(self):
        film = self.item()
        other = self.item()
        store_item_credits(film, movie_metadata())
        store_item_credits(other, movie_metadata())

        store_item_credits(film, movie_metadata(tmdb_credits(1)))

        self.assertEqual(len(self.credits(other, "cast")), 14)

    def test_metadata_without_credits_keeps_them(self):
        film = self.item()
        store_item_credits(film, movie_metadata())

        store_item_metadata(film, {"release_date": "1999-03-31", "runtime": 136})

        self.assertEqual(len(self.credits(film)), 16)


class CreditVersionTests(CreditTestCase):
    """The credit rules bump each type's version, so the fill revisits stored titles."""

    def test_titles_filled_before_credits_are_pending_again(self):
        user = get_user_model().objects.create_user(username="viewer", password="x")
        film = self.item(metadata_version=2)
        season = self.item(MediaTypes.SEASON.value, season_number=1, metadata_version=2)
        Movie.objects.bulk_create([Movie(item=film, user=user)])

        self.assertEqual(
            {
                media_type: CURRENT_METADATA_VERSION[media_type]
                for media_type in ("movie", "tv", "music", "book", "season", "episode")
            },
            {"movie": 3, "tv": 3, "music": 3, "book": 4, "season": 2, "episode": 2},
        )
        self.assertFalse(is_metadata_current(film))
        self.assertTrue(is_metadata_current(season))
        self.assertIn(film, metadata_fill.pending_items())

        store_item_metadata(film, movie_metadata())

        film.refresh_from_db()
        self.assertEqual(film.metadata_version, 3)
        self.assertNotIn(film, metadata_fill.pending_items())
