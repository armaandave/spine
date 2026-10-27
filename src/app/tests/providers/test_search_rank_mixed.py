from unittest.mock import patch

from django.test import SimpleTestCase

from app.models import MediaTypes, Sources
from app.providers import apple_music
from app.providers.search_rank import rank_mixed_results, rank_results
from app.tests.providers.test_apple_music import AppleMusicTestCase
from app.tests.providers.test_apple_music_search import search_answers


class MixedSearchRankTests(SimpleTestCase):
    def test_deathly_hallows_adaptations_beat_weaker_cross_media_matches(self):
        candidates = [
            (
                0,
                {
                    "media_id": "weak",
                    "media_type": MediaTypes.GAME.value,
                    "source": Sources.IGDB.value,
                    "title": "Deathly Quest",
                    "total_rating_count": 1_000_000,
                },
            ),
            (
                0,
                {
                    "media_id": "book",
                    "media_type": MediaTypes.BOOK.value,
                    "source": Sources.HARDCOVER.value,
                    "title": "Harry Potter and the Deathly Hallows",
                    "ratings_count": 100_000,
                },
            ),
            (
                0,
                {
                    "media_id": "movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "title": "Harry Potter and the Deathly Hallows: Part 1",
                    "vote_count": 50_000,
                },
            ),
        ]

        ranked = rank_mixed_results("deathly hallows", candidates, limit=24)

        self.assertEqual([item["media_id"] for item in ranked[:2]], ["book", "movie"])
        self.assertNotIn("ratings_count", ranked[0])
        self.assertNotIn("vote_count", ranked[1])

    def test_equal_scores_use_provider_rank_then_stable_reference(self):
        candidates = [
            (
                1,
                {
                    "media_id": "2",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "title": "Dune",
                },
            ),
            (
                0,
                {
                    "media_id": "1",
                    "media_type": MediaTypes.BOOK.value,
                    "source": Sources.HARDCOVER.value,
                    "title": "Dune",
                },
            ),
        ]

        ranked = rank_mixed_results("dune", candidates, limit=24)

        self.assertEqual([item["media_id"] for item in ranked], ["1", "2"])

    def test_cross_media_golden_queries_favor_strong_title_matches(self):
        cases = (
            ("Dune", "Dune", "Dune Messiah", MediaTypes.BOOK.value, MediaTypes.MOVIE.value),
            (
                "The Last of Us",
                "The Last of Us",
                "The Making of The Last of Us",
                MediaTypes.GAME.value,
                MediaTypes.TV.value,
            ),
            ("Watchmen", "Watchmen", "Watching Men", MediaTypes.COMIC.value, MediaTypes.MOVIE.value),
            ("Death Note", "Death Note", "Death Parade", MediaTypes.MANGA.value, MediaTypes.ANIME.value),
            ("Spider-Man", "Spider Man", "Spider Woman", MediaTypes.COMIC.value, MediaTypes.MOVIE.value),
            ("Amelie", "Amélie", "Emily in Paris", MediaTypes.MOVIE.value, MediaTypes.BOOK.value),
            ("It", "It", "It Takes Two", MediaTypes.BOOK.value, MediaTypes.MOVIE.value),
        )

        for query, title, weaker_title, first_type, second_type in cases:
            with self.subTest(query=query):
                candidates = [
                    (
                        0,
                        {
                            "media_id": "strong-1",
                            "media_type": first_type,
                            "source": "provider-a",
                            "title": title,
                            "image": "https://example.com/cover.jpg",
                            "release_date": "2020-01-01",
                            "vote_count": 10_000,
                        },
                    ),
                    (
                        1,
                        {
                            "media_id": "strong-2",
                            "media_type": second_type,
                            "source": "provider-b",
                            "title": title,
                            "image": "https://example.com/poster.jpg",
                            "release_date": "2021-01-01",
                            "vote_count": 5_000,
                        },
                    ),
                    (
                        0,
                        {
                            "media_id": "weak",
                            "media_type": MediaTypes.GAME.value,
                            "source": Sources.IGDB.value,
                            "title": weaker_title,
                            "total_rating_count": 100,
                        },
                    ),
                ]

                ranked = rank_mixed_results(query, candidates, limit=24)

                self.assertEqual(
                    [item["media_id"] for item in ranked[:2]],
                    ["strong-1", "strong-2"],
                )

    def test_popularity_supports_relevance_without_overriding_exact_match(self):
        candidates = [
            (
                0,
                {
                    "media_id": "exact",
                    "media_type": MediaTypes.BOOK.value,
                    "source": Sources.HARDCOVER.value,
                    "title": "Dune",
                    "ratings_count": 1_000,
                    "first_publish_year": 1965,
                },
            ),
            (
                0,
                {
                    "media_id": "popular-prefix",
                    "media_type": MediaTypes.GAME.value,
                    "source": Sources.IGDB.value,
                    "title": "Dune Awakening",
                    "total_rating_count": 100_000_000,
                    "first_release_date": 1_800_000_000,
                },
            ),
        ]

        ranked = rank_mixed_results("Dune", candidates, limit=24)

        self.assertEqual(ranked[0]["media_id"], "exact")

    def test_music_provider_relevance_does_not_overwhelm_popular_exact_adaptations(self):
        candidates = [
            (
                0,
                {
                    "media_id": "album",
                    "media_type": MediaTypes.MUSIC.value,
                    "source": Sources.MUSICBRAINZ.value,
                    "title": "Dune",
                    "image": "https://example.com/album.jpg",
                    "first_release_date": "1995",
                },
            ),
            (
                1,
                {
                    "media_id": "greatest-hits",
                    "media_type": MediaTypes.MUSIC.value,
                    "source": Sources.MUSICBRAINZ.value,
                    "title": "Dune Greatest Hits",
                    "image": "https://example.com/hits.jpg",
                    "first_release_date": "1999",
                },
            ),
            (
                0,
                {
                    "media_id": "movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "title": "Dune",
                    "image": "https://example.com/movie.jpg",
                    "release_date": "2021-09-15",
                    "vote_count": 10_000,
                },
            ),
            (
                0,
                {
                    "media_id": "book",
                    "media_type": MediaTypes.BOOK.value,
                    "source": Sources.HARDCOVER.value,
                    "title": "Dune",
                    "image": "https://example.com/book.jpg",
                    "first_publish_year": 1965,
                    "author_name": "Frank Herbert",
                    "ratings_count": 100_000,
                },
            ),
        ]

        ranked = rank_mixed_results("dune", candidates, limit=24)

        self.assertEqual(
            [item["media_type"] for item in ranked[:2]],
            [MediaTypes.BOOK.value, MediaTypes.MOVIE.value],
        )

    def test_mixed_results_are_capped_and_strip_internal_fields(self):
        candidates = [
            (
                index,
                {
                    "media_id": str(index),
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "title": "Dune",
                    "vote_count": 100 - index,
                },
            )
            for index in range(30)
        ]

        ranked = rank_mixed_results("Dune", candidates, limit=24)

        self.assertEqual(len(ranked), 24)
        self.assertTrue(all("vote_count" not in item for item in ranked))

    @patch("app.providers.search_rank._score")
    def test_single_and_mixed_ranking_delegate_to_the_same_score(self, score):
        score.side_effect = lambda _query, result, _media_type: result["shared_score"]
        results = [
            {
                "media_id": "low",
                "media_type": MediaTypes.MOVIE.value,
                "source": Sources.TMDB.value,
                "title": "Dune",
                "shared_score": 1,
            },
            {
                "media_id": "high",
                "media_type": MediaTypes.BOOK.value,
                "source": Sources.HARDCOVER.value,
                "title": "Dune",
                "shared_score": 2,
            },
        ]

        single = rank_results("Dune", results, MediaTypes.MOVIE.value)
        mixed = rank_mixed_results("Dune", list(enumerate(results)), limit=24)

        self.assertEqual([item["media_id"] for item in single], ["high", "low"])
        self.assertEqual([item["media_id"] for item in mixed], ["high", "low"])

    def test_words_found_in_the_artist_leave_the_rest_to_match_the_title(self):
        def album(media_id, title, artist, users, listens):
            return {
                "media_id": media_id,
                "title": title,
                "artist_name": artist,
                "image": f"https://example.com/{media_id}.jpg",
                "first_release_date": "2022",
                "total_user_count": users,
                "total_listen_count": listens,
            }

        # MusicBrainz and ListenBrainz figures for "RENAISSANCE beyonce" (2026-10-26).
        results = [
            album("film", "RENAISSANCE: A Film by Beyoncé (Live)", "Beyoncé", 14, 676),
            album("self-titled", "BEYONCÉ", "Beyoncé", 18_394, 318_906),
            album("renaissance", "RENAISSANCE", "Beyoncé", 11_320, 453_186),
            album("band", "Renaissance", "Renaissance", 239, 1_985),
        ]

        ranked = rank_results("RENAISSANCE beyonce", results, MediaTypes.MUSIC.value)

        self.assertEqual(
            [item["media_id"] for item in ranked],
            ["renaissance", "film", "self-titled", "band"],
        )
        self.assertNotIn("artist_name", ranked[0])

    def test_a_query_naming_only_the_artist_counts_every_word_found(self):
        results = [
            {"media_id": "west", "title": "West", "artist_name": "Lucinda Williams"},
            {"media_id": "graduation", "title": "Graduation", "artist_name": "Kanye West"},
        ]

        ranked = rank_results("kanye west", results, MediaTypes.MUSIC.value)

        self.assertEqual([item["media_id"] for item in ranked], ["graduation", "west"])

    def test_little_words_in_the_artist_stay_with_the_title(self):
        results = [
            {"media_id": media_id, "title": title, "artist_name": artist, "release_date": "1979"}
            for media_id, title, artist in (
                ("kills", "Wall", "The Kills"),
                ("floyd", "The Wall", "Pink Floyd"),
            )
        ]

        ranked = rank_results("the wall", results, MediaTypes.MUSIC.value)

        self.assertEqual([item["media_id"] for item in ranked], ["floyd", "kills"])


def film(media_id, title, votes):
    return {
        "media_id": media_id,
        "media_type": MediaTypes.MOVIE.value,
        "source": Sources.TMDB.value,
        "title": title,
        "image": f"https://example.com/{media_id}.jpg",
        "release_date": "2006-03-15",
        "vote_count": votes,
    }


class AppleMusicSearchRankTests(AppleMusicTestCase):
    """All media search with Apple Music's albums, which come with no popularity.

    Apple's order stands in for it: the first album gets 30, and each one after it
    3 less. Tuned with Apple's first page for each query (recorded 2026-10-26).
    """

    # Apple's first album for each query: Beyoncé's, Michael Jackson's and SZA's.
    QUERIES = (
        ("renaissance", "renaissance", "1630005298"),
        ("thriller", "thriller", "269572838"),
        ("sos", "sos", "1657869377"),
    )

    def candidates(self, name, query, *others):
        self.apple(*search_answers(name))
        albums = apple_music.search_albums(query, 1, preserve_ranking_fields=True)["results"]
        return [*enumerate(albums), *((0, other) for other in others)]

    def test_the_boost_falls_by_three_a_place(self):
        self.assertEqual(
            [apple_music.rank_boost(position) for position in range(12)],
            [30, 27, 24, 21, 18, 15, 12, 9, 6, 3, 0, 0],
        )

    def test_apples_first_album_leads_a_film_with_the_same_title(self):
        for name, query, first in self.QUERIES:
            with self.subTest(query=query):
                candidates = self.candidates(name, query, film("film", query.title(), 300))

                ranked = rank_mixed_results(query, candidates, limit=24)
                unboosted = rank_mixed_results(
                    query,
                    [(rank, {**item, "provider_rank_boost": 0}) for rank, item in candidates],
                    limit=24,
                )

                self.assertEqual([item["media_id"] for item in ranked[:2]], [first, "film"])
                self.assertEqual(unboosted[0]["media_id"], "film")
                self.assertNotIn("provider_rank_boost", ranked[0])

    def test_a_popular_film_with_the_same_title_still_comes_first(self):
        for name, query, first in self.QUERIES:
            with self.subTest(query=query):
                candidates = self.candidates(name, query, film("film", query.title(), 10_000))

                ranked = rank_mixed_results(query, candidates, limit=24)

                self.assertEqual([item["media_id"] for item in ranked[:2]], ["film", first])

    def test_the_same_title_lower_in_apples_list_ranks_below_its_first(self):
        candidates = self.candidates("thriller", "thriller")

        ranked = [item["media_id"] for item in rank_mixed_results("thriller", candidates, limit=24)]

        # Michael Jackson's Thriller, then the compilation called Thriller, then
        # other artists' singles called Thriller, in Apple's order.
        self.assertEqual(ranked[:4], ["269572838", "6827221246", "1838923953", "1587850086"])

    def test_apples_first_album_never_overwhelms_popular_exact_adaptations(self):
        album = {
            "media_id": "album",
            "media_type": MediaTypes.MUSIC.value,
            "source": Sources.APPLE_MUSIC.value,
            "title": "Dune",
            "image": "https://example.com/album.jpg",
            "first_release_date": "1995",
            "provider_rank_boost": apple_music.rank_boost(0),
        }
        candidates = [
            (0, album),
            (0, {**film("movie", "Dune", 10_000), "release_date": "2021-09-15"}),
            (
                0,
                {
                    "media_id": "book",
                    "media_type": MediaTypes.BOOK.value,
                    "source": Sources.HARDCOVER.value,
                    "title": "Dune",
                    "image": "https://example.com/book.jpg",
                    "first_publish_year": 1965,
                    "author_name": "Frank Herbert",
                    "ratings_count": 100_000,
                },
            ),
        ]

        ranked = rank_mixed_results("dune", candidates, limit=24)

        self.assertEqual([item["media_id"] for item in ranked], ["book", "movie", "album"])
