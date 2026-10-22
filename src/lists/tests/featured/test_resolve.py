from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from app.providers import services
from lists.featured import resolve
from lists.featured.registry import Guards
from lists.featured.resolve import Resolver, parse_ref, pick_title_match
from lists.featured.types import Entry, Match
from lists.tests.featured.helpers import make_definition


def search_response(*results):
    return {"page": 1, "total_results": len(results), "total_pages": 1, "results": list(results)}


def tmdb_result(media_id, title, release_date):
    return {
        "media_id": media_id,
        "source": "tmdb",
        "media_type": "movie",
        "title": title,
        "image": f"https://image.tmdb.org/t/p/w500/{media_id}.jpg",
        "release_date": release_date,
    }


class ResolverTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def resolver(self, *, overrides=None, **fields):
        return Resolver(make_definition(**fields), overrides=overrides or {})


class RefParsingTests(SimpleTestCase):
    def test_parse_ref(self):
        self.assertEqual(parse_ref("tmdb:movie:550"), ("tmdb", "movie", "550"))
        self.assertEqual(parse_ref("openlibrary:book:OL7353617M"), ("openlibrary", "book", "OL7353617M"))
        self.assertEqual(parse_ref("musicbrainz:music:6b9a9e04-abd7-4666-86ba-bb220ef4c3b2")[2], "6b9a9e04-abd7-4666-86ba-bb220ef4c3b2")
        self.assertEqual(parse_ref("openlibrary:person:OL1A"), ("openlibrary", "person", "OL1A"))
        for bad in ("tmdb:movie", "tmdb:season:1", "igdb:movie:1", "imdb:person:nm1", ""):
            with self.subTest(bad), self.assertRaises(ValueError):
                parse_ref(bad)


class ResolverOrderTests(ResolverTestCase):
    def test_override_wins_and_dash_skips(self):
        entry = Entry(1, "Dune", year=2021, ids={"tmdb": "1"})
        resolver = self.resolver(overrides={entry.key: "tmdb:movie:438631", "t:skip|": "-"})
        resolution = resolver.resolve_one(entry)
        self.assertEqual((resolution.match.ref, resolution.how), ("tmdb:movie:438631", "override"))
        self.assertTrue(resolver.resolve_one(Entry(2, "Skip")).skipped)

    def test_entry_ref_is_exact(self):
        resolution = self.resolver().resolve_one(Entry(1, "Fight Club", ids={"ref": "tmdb:movie:550"}, image="https://x/p.jpg"))
        self.assertEqual(resolution.match, Match("tmdb", "movie", "550", "Fight Club", "https://x/p.jpg"))
        self.assertEqual(resolution.how, "ref")

    def test_ref_outside_the_list_media_types_is_unresolved(self):
        resolution = self.resolver().resolve_one(Entry(1, "Dune", ids={"ref": "hardcover:book:1"}))
        self.assertIsNone(resolution.match)
        self.assertIn("not one of this list's media types", resolution.reason)

    def test_direct_ids_need_no_network(self):
        cases = [
            ({"media_types": ("movie",)}, {"tmdb": "550"}, "tmdb:movie:550"),
            ({"media_types": ("tv",), "position": 250}, {"tmdb": "1396"}, "tmdb:tv:1396"),
            ({"media_types": ("game",), "position": 550}, {"igdb": "1942"}, "igdb:game:1942"),
            ({"media_types": ("anime",), "position": 620}, {"mal": "5114"}, "mal:anime:5114"),
            ({"media_types": ("manga",), "position": 460}, {"mangaupdates": "15"}, "mangaupdates:manga:15"),
            ({"media_types": ("book",), "position": 350}, {"hardcover": "427"}, "hardcover:book:427"),
            ({"media_types": ("book",), "position": 350}, {"olid": "ol7353617m"}, "openlibrary:book:OL7353617M"),
            ({"media_types": ("music",), "position": 750}, {"mbid_rg": "abc"}, "musicbrainz:music:abc"),
            ({"media_types": ("comic",), "position": 420}, {"comicvine": "4050"}, "comicvine:comic:4050"),
        ]
        with patch("app.providers.services.api_request", side_effect=AssertionError("network")):
            for fields, ids, ref in cases:
                with self.subTest(ref):
                    resolution = self.resolver(**fields).resolve_one(Entry(1, "Title", ids=ids))
                    self.assertEqual((resolution.match.ref, resolution.how), (ref, "id"))

    def test_tmdb_id_with_ambiguous_media_type_falls_back_to_imdb(self):
        resolver = self.resolver(media_types=("movie", "tv"), position=250)
        response = {"movie_results": [], "tv_results": [{"id": 1396, "name": "Breaking Bad", "poster_path": "/bb.jpg", "first_air_date": "2008-01-20"}]}
        with patch("app.providers.tmdb.find", return_value=response) as find:
            resolution = resolver.resolve_one(Entry(1, "Breaking Bad", ids={"tmdb": "1396", "imdb": "tt0903747"}))
        find.assert_called_once_with("tt0903747", "imdb_id")
        self.assertEqual(resolution.match.ref, "tmdb:tv:1396")
        self.assertEqual(resolution.match.image, "https://image.tmdb.org/t/p/w500/bb.jpg")
        self.assertEqual(resolution.match.year, 2008)
        self.assertEqual(resolution.how, "imdb")

    def test_steam_through_igdb(self):
        with patch("app.providers.igdb.external_game", return_value=1942) as external_game:
            resolution = self.resolver(media_types=("game",), position=550).resolve_one(Entry(1, "Witcher 3", ids={"steam": "292030"}))
        external_game.assert_called_once_with("292030")
        self.assertEqual((resolution.match.ref, resolution.how), ("igdb:game:1942", "steam"))

    def test_isbns_try_hardcover_then_open_library(self):
        with (
            patch("app.providers.hardcover.lookup_book_by_isbn", side_effect=[None, None]) as hardcover,
            patch("app.providers.openlibrary.lookup_book_by_isbn", return_value={"source": "openlibrary", "media_id": "OL1M", "title": "Book", "image": "https://c/1.jpg", "total_pages": 300}) as openlibrary,
        ):
            resolution = self.resolver(media_types=("book",), position=350).resolve_one(Entry(1, "BOOK", isbns=("111", "222")))
        self.assertEqual([call.args for call in hardcover.call_args_list], [("111",), ("222",)])
        openlibrary.assert_called_once_with("111")
        self.assertEqual(resolution.match, Match("openlibrary", "book", "OL1M", "Book", "https://c/1.jpg", total_pages=300))

    def test_isbn_lookup_errors_are_not_cached(self):
        error = services.ProviderAPIError("hardcover", Exception("down"))
        resolver = self.resolver(media_types=("book",), position=350, guards=Guards(max_title_searches=0))
        entry = Entry(1, "BOOK", isbns=("111",))
        with (
            patch("app.providers.hardcover.lookup_book_by_isbn", side_effect=error),
            patch("app.providers.openlibrary.lookup_book_by_isbn", return_value=None),
        ):
            resolution = resolver.resolve_one(entry)
        self.assertIsNone(resolution.match)
        self.assertIn("isbn lookup failed", resolution.reason)
        self.assertIsNone(cache.get(resolver._cache_key(entry)))


class TitleSearchTests(ResolverTestCase):
    def search(self, entry, results, **fields):
        resolver = self.resolver(**fields)
        with patch("app.providers.services.search", return_value=search_response(*results)) as search:
            return resolver.resolve_one(entry), search, resolver

    def test_exact_title_and_year_within_one(self):
        results = [tmdb_result(1, "Dune", "1984-12-14"), tmdb_result(438631, "Dune", "2021-09-15")]
        resolution, search, _ = self.search(Entry(1, "DUNE", year=2022), results)
        self.assertEqual((resolution.match.ref, resolution.how), ("tmdb:movie:438631", "title"))
        search.assert_called_once_with("movie", "DUNE", 1, source="tmdb", preserve_ranking_fields=True)

    def test_prefers_the_exact_year(self):
        results = [tmdb_result(1, "Heat", "1996-01-01"), tmdb_result(949, "Heat", "1995-12-15")]
        resolution, _, _ = self.search(Entry(1, "Heat", year=1995), results)
        self.assertEqual(resolution.match.media_id, "949")

    def test_no_fuzzy_guessing(self):
        resolution, _, _ = self.search(Entry(1, "Dune Messiah", year=2026), [tmdb_result(1, "Dune: Part Two", "2024-02-27")])
        self.assertIsNone(resolution.match)
        resolution, _, _ = self.search(Entry(1, "Dune", year=2010), [tmdb_result(1, "Dune", "2021-09-15")])
        self.assertIsNone(resolution.match)

    def test_ambiguous_title_without_year_is_unresolved(self):
        results = [tmdb_result(1, "Dune", "1984-12-14"), tmdb_result(2, "Dune", "2021-09-15")]
        resolution, _, _ = self.search(Entry(1, "Dune"), results)
        self.assertIsNone(resolution.match)
        cache.clear()
        resolution, _, _ = self.search(Entry(1, "Dune"), results[:1])
        self.assertEqual(resolution.match.media_id, "1")

    def test_subtitle_and_creator_for_books(self):
        results = [
            {"media_id": 9, "source": "hardcover", "title": "Atomic Habits: An Easy & Proven Way", "author_name": ["Someone Else"], "first_publish_year": 2018},
            {"media_id": 10, "source": "hardcover", "title": "Atomic Habits: An Easy & Proven Way", "author_name": ["James Clear"], "first_publish_year": 2018},
        ]
        resolution, search, _ = self.search(Entry(1, "ATOMIC HABITS", creator="James Clear"), results, media_types=("book",), position=350)
        self.assertEqual(resolution.match.ref, "hardcover:book:10")
        self.assertEqual(search.call_args.kwargs["source"], "hardcover")

    def test_budget_caps_title_searches_and_is_not_cached(self):
        resolver = self.resolver(guards=Guards(max_title_searches=1))
        first, second = Entry(1, "Alpha", year=2000), Entry(2, "Beta", year=2000)
        with patch("app.providers.services.search", return_value=search_response()) as search:
            self.assertIsNone(resolver.resolve_one(first).match)
            blocked = resolver.resolve_one(second)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(blocked.reason, "title search budget used up")
        self.assertEqual(cache.get(resolver._cache_key(first)), resolve.MISS)
        self.assertIsNone(cache.get(resolver._cache_key(second)))

    def test_hits_and_misses_are_cached(self):
        entry = Entry(1, "Dune", year=2021)
        resolution, _, resolver = self.search(entry, [tmdb_result(438631, "Dune", "2021-09-15")])
        self.assertTrue(resolver._cache_key(entry).startswith("featured:resolve:movie:t:dune|2021"))
        with patch("app.providers.services.search", side_effect=AssertionError("cached")):
            again = self.resolver().resolve_one(entry)
        self.assertEqual((again.match.ref, again.how), (resolution.match.ref, "cache"))

    def test_pick_title_match_reads_provider_years(self):
        igdb_result = {"media_id": 1, "title": "Hades", "first_release_date": 1600387200}
        self.assertEqual(pick_title_match(Entry(1, "Hades", year=2020), [igdb_result]), igdb_result)


class PeopleResolutionTests(ResolverTestCase):
    def people_resolver(self, **params):
        return self.resolver(list_type="people", media_types=("book",), position=950, params=params)

    def test_direct_person_ids(self):
        resolution = self.people_resolver().resolve_one(Entry(1, "Annie Ernaux", ids={"openlibrary_person": "OL1393513A"}))
        self.assertEqual(resolution.match.ref, "openlibrary:person:OL1393513A")
        self.assertTrue(resolution.match.is_person)

    def test_person_refs_and_media_refs(self):
        resolver = self.people_resolver()
        self.assertEqual(resolver.resolve_one(Entry(1, "X", ids={"ref": "tmdb:person:1"})).match.ref, "tmdb:person:1")
        self.assertIsNone(resolver.resolve_one(Entry(1, "X", ids={"ref": "tmdb:movie:1"})).match)

    def test_name_search_needs_a_unique_exact_name(self):
        people = [
            {"person_id": "OL2632116A", "name": "Kazuo Ishiguro", "profile_url": "https://p/1.jpg"},
            {"person_id": "OL9A", "name": "Kazuo Ishiguro Jr."},
        ]
        with patch("app.providers.services.search_people", return_value=people) as search_people:
            resolution = self.people_resolver(person_source="openlibrary").resolve_one(Entry(1, "Kazuo Ishiguro"))
        search_people.assert_called_once_with("openlibrary", "Kazuo Ishiguro", limit=10)
        self.assertEqual(resolution.match.ref, "openlibrary:person:OL2632116A")
        self.assertEqual(resolution.match.image, "https://p/1.jpg")
