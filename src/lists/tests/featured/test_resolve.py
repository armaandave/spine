from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from app.models import AppleAlbum, Item, MusicAlbumLink
from app.providers import services
from lists.featured import registry, resolve, types, writer
from lists.featured.registry import Guards
from lists.featured.resolve import Resolver, parse_ref, pick_title_match
from lists.featured.types import Entry, FetchResult, Match
from lists.models import CustomList, FeaturedListSnapshot, FeaturedListSync
from lists.tests.featured.helpers import FeaturedTestCase, make_definition

APPLE = "apple_music"
MUSICBRAINZ = "musicbrainz"
SOS = "1657869377"
SOS_CLEAN = "1658650093"
CTRL = "1239976329"
SOS_MBID = "1646286a-d0ad-4288-bfab-34b0fb7b22c1"
CTRL_MBID = "8e9b6e3c-8a1f-4d4b-9bf6-0d58c43c2a7e"


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


@override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
class ResolverTestCase(TestCase):
    # A database test: music matches are made canonical from the link tables.
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
        # Cut off by the cap: deferred to the next run, not unresolved.
        self.assertTrue(blocked.deferred)
        self.assertEqual(blocked.reason, "this run's 1 title searches are used up")
        self.assertAlmostEqual(blocked.retry_at, timezone.now() + resolve.TITLE_SEARCH_RETRY, delta=timedelta(minutes=1))
        self.assertEqual(cache.get(resolver._cache_key(first)), resolve.MISS)
        self.assertIsNone(cache.get(resolver._cache_key(second)))

    def test_hits_outlive_the_longest_cadence_and_refresh_on_use(self):
        longest = max(definition.cadence for definition in registry.all_definitions() if definition.cadence)
        self.assertGreater(timedelta(seconds=resolve.HIT_TTL), longest)
        entry = Entry(1, "Dune", year=2021)
        _, _, resolver = self.search(entry, [tmdb_result(438631, "Dune", "2021-09-15")])
        with patch.object(cache, "touch") as touch:
            self.assertIsNotNone(resolver.resolve_one(entry).match)
        touch.assert_called_once_with(resolver._cache_key(entry), resolve.HIT_TTL)

    def test_hits_and_misses_are_cached(self):
        entry = Entry(1, "Dune", year=2021)
        resolution, _, resolver = self.search(entry, [tmdb_result(438631, "Dune", "2021-09-15")])
        self.assertTrue(resolver._cache_key(entry).startswith("featured:resolve:movie:t:dune|2021"))
        with patch("app.providers.services.search", side_effect=AssertionError("cached")):
            again = self.resolver().resolve_one(entry)
        self.assertEqual((again.match.ref, again.how), (resolution.match.ref, "cache"))

    def test_creator_confirmed_result_beats_an_uncredited_exact_title(self):
        stub = {"media_id": 1, "source": "hardcover", "title": "Anatomy"}
        real = {"media_id": 2, "source": "hardcover", "title": "Anatomy: A Love Story", "author_name": ["Dana Schwartz"]}
        self.assertEqual(pick_title_match(Entry(1, "Anatomy", creator="Dana Schwartz"), [stub, real]), real)
        # Without a creator to confirm, the exact title still wins.
        self.assertEqual(pick_title_match(Entry(1, "Anatomy", year=2022), [stub, real]), stub)

    def test_entry_subtitle_falls_back_to_the_main_title(self):
        senate = {"media_id": 7, "source": "hardcover", "title": "Master of the Senate", "author_name": ["Robert A. Caro"], "first_publish_year": 2002}
        entry = Entry(1, "Master of the Senate: The Years of Lyndon Johnson", year=2002, creator="Robert A. Caro")
        self.assertEqual(pick_title_match(entry, [senate]), senate)
        # Both sides subtitled differently still share the main title.
        other = {**senate, "title": "Master of the Senate: Volume III"}
        self.assertEqual(pick_title_match(entry, [other]), other)
        # The year and creator checks still apply to the fallback.
        self.assertIsNone(pick_title_match(Entry(1, entry.title, year=2010, creator="Robert A. Caro"), [senate]))
        self.assertIsNone(pick_title_match(Entry(1, entry.title, year=2002, creator="Someone Else"), [senate]))
        # Without a year or creator to agree, a main title alone is too weak.
        self.assertIsNone(pick_title_match(Entry(1, entry.title), [senate]))

    def test_entry_main_title_shared_by_two_books_is_unresolved(self):
        results = [
            {"media_id": 1, "source": "hardcover", "title": "Empire", "first_publish_year": 2003},
            {"media_id": 2, "source": "hardcover", "title": "Empire: Book One", "first_publish_year": 2004},
        ]
        self.assertIsNone(pick_title_match(Entry(1, "Empire: A Novel", year=2003), results))
        self.assertEqual(pick_title_match(Entry(1, "Empire: A Novel", year=2003), results[:1]), results[0])

    def test_exact_title_beats_the_entry_main_title_fallback(self):
        exact = {"media_id": 1, "source": "tmdb", "title": "Mission: Impossible", "release_date": "1996-05-22"}
        short = {"media_id": 2, "source": "tmdb", "title": "Mission", "release_date": "1996-01-01"}
        self.assertEqual(pick_title_match(Entry(1, "Mission: Impossible", year=1996), [short, exact]), exact)

    def test_main_title_fallback_resolves_through_the_search(self):
        senate = {"media_id": 7, "source": "hardcover", "title": "Master of the Senate", "author_name": ["Robert A. Caro"], "first_publish_year": 2002}
        entry = Entry(1, "Master of the Senate: The Years of Lyndon Johnson", year=2002, creator="Robert A. Caro")
        resolution, search, _ = self.search(entry, [senate], media_types=("book",), position=350)
        self.assertEqual((resolution.match.ref, resolution.how), ("hardcover:book:7", "title"))
        self.assertEqual(search.call_args.args[1], entry.title)

    def test_pick_title_match_reads_provider_years(self):
        igdb_result = {"media_id": 1, "title": "Hades", "first_release_date": 1600387200}
        self.assertEqual(pick_title_match(Entry(1, "Hades", year=2020), [igdb_result]), igdb_result)


class SnapshotMemoryTests(FeaturedTestCase):
    """The list's latest snapshot answers before the cache and the network."""

    def setUp(self):
        super().setUp()
        self.definition = make_definition(media_types=("book",), position=350)
        self.entry = Entry(1, "Remembered", media_type="book", isbns=("9780000000007",), image="https://source/7.jpg")
        self.book = Item.objects.create(source="hardcover", media_type="book", media_id="7", title="Remembered", image="https://c/7.jpg")
        older = Item.objects.create(source="hardcover", media_type="book", media_id="6", title="Older", image="https://c/6.jpg")
        sync = FeaturedListSync.objects.create(key=self.definition.key)
        FeaturedListSnapshot.objects.create(sync=sync, taken_on=date(2026, 10, 1), fingerprint="a", entries=[[1, self.entry.key, older.pk]])
        FeaturedListSnapshot.objects.create(
            sync=sync,
            taken_on=date(2026, 10, 8),
            fingerprint="b",
            entries=[[1, self.entry.key, self.book.pk], [2, "isbn:9780000000008", None]],
        )

    def test_an_entry_in_the_latest_snapshot_resolves_from_the_database(self):
        resolver = Resolver(self.definition, overrides={}, remember=True)
        with (
            patch("app.providers.services.api_request", side_effect=AssertionError("network")),
            patch.object(cache, "get", side_effect=AssertionError("cache")),
            self.assertNumQueries(2),  # the snapshot, then its Items: once per run
        ):
            first = resolver.resolve_one(self.entry)
            again = resolver.resolve_one(self.entry)
        self.assertEqual((first.match.ref, first.how), ("hardcover:book:7", "snapshot"))
        self.assertEqual(first.match.title, "Remembered")
        self.assertEqual(first.match.image, "https://source/7.jpg")  # the source's poster, not Item.image
        self.assertEqual(again.match, first.match)

    def test_an_override_beats_the_snapshot(self):
        resolver = Resolver(self.definition, overrides={self.entry.key: "hardcover:book:9"}, remember=True)
        resolution = resolver.resolve_one(self.entry)
        self.assertEqual((resolution.match.ref, resolution.how), ("hardcover:book:9", "override"))

    def test_a_deleted_item_falls_through_to_the_lookup(self):
        self.book.delete()
        resolver = Resolver(self.definition, overrides={}, remember=True)
        found = {"source": "hardcover", "media_id": "70", "title": "Remembered", "image": "https://c/70.jpg"}
        with patch("app.providers.hardcover.lookup_book_by_isbn", return_value=found):
            resolution = resolver.resolve_one(self.entry)
        self.assertEqual((resolution.match.ref, resolution.how), ("hardcover:book:70", "isbn"))


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


def music_definition(**fields):
    return make_definition(media_types=("music",), position=750, guards=Guards(max_title_searches=0), **fields)


def no_network():
    return patch("app.providers.services.api_request", side_effect=AssertionError("network"))


def moved_sos():
    """Return SOS's Item after the data move: re-keyed to Apple Music, with its applied row."""
    item = Item.objects.create(source=APPLE, media_type="music", media_id=SOS, title="SOS")
    MusicAlbumLink.objects.create(release_group_mbid=SOS_MBID, apple_album_id=SOS, status="applied", item=item)
    return item


class AppleAlbumIdTests(ResolverTestCase):
    def test_apple_album_ids_resolve_directly_and_come_before_mbid_rg(self):
        resolver = Resolver(music_definition(), overrides={})
        with no_network():
            alone = resolver.resolve_one(Entry(1, "SOS", ids={"apple_album": SOS}, image="https://x/sos.jpg"))
            both = resolver.resolve_one(Entry(2, "SOS", ids={"mbid_rg": SOS_MBID, "apple_album": SOS}))
        self.assertEqual((alone.match.ref, alone.how, alone.match.image), (f"{APPLE}:music:{SOS}", "id", "https://x/sos.jpg"))
        self.assertEqual(both.match.ref, f"{APPLE}:music:{SOS}")

    def test_existing_entry_keys_stay_as_they_were(self):
        self.assertEqual(types.KEY_ID_ORDER[-1], "apple_album")
        self.assertEqual(Entry(1, "SOS", ids={"mbid_rg": SOS_MBID, "apple_album": SOS}).key, f"mbid_rg:{SOS_MBID}")
        self.assertEqual(Entry(1, "SOS", ids={"wikidata": "Q115", "apple_album": SOS}).key, "wikidata:Q115")
        self.assertEqual(Entry(1, "SOS", ids={"apple_album": SOS}).key, f"apple_album:{SOS}")

    def test_music_refs_name_either_source(self):
        self.assertEqual(parse_ref(f"{APPLE}:music:{SOS}"), (APPLE, "music", SOS))
        self.assertEqual(parse_ref(f"{MUSICBRAINZ}:music:{SOS_MBID}")[0], MUSICBRAINZ)
        with self.assertRaises(ValueError):
            parse_ref(f"{APPLE}:movie:{SOS}")

    def test_an_apple_alias_resolves_to_its_listing(self):
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        resolution = Resolver(music_definition(), overrides={}).resolve_one(Entry(1, "SOS", ids={"apple_album": SOS_CLEAN}))
        self.assertEqual(resolution.match.ref, f"{APPLE}:music:{SOS}")


class MovedAlbumResolveTests(ResolverTestCase):
    """Whichever step finds a moved album, it resolves to the Item that holds it."""

    def setUp(self):
        super().setUp()
        moved_sos()

    def test_ids_refs_and_overrides(self):
        entry = Entry(1, "SOS", ids={"mbid_rg": SOS_MBID}, image="https://coverartarchive.org/sos")
        cases = [
            ("id", Resolver(music_definition(), overrides={}), entry),
            ("ref", Resolver(music_definition(), overrides={}), Entry(1, "SOS", ids={"ref": f"{MUSICBRAINZ}:music:{SOS_MBID}"})),
            ("override", Resolver(music_definition(), overrides={entry.key: f"{MUSICBRAINZ}:music:{SOS_MBID}"}), entry),
        ]
        for how, resolver, row in cases:
            with self.subTest(how), no_network():
                resolution = resolver.resolve_one(row)
                self.assertEqual((resolution.match.ref, resolution.how), (f"{APPLE}:music:{SOS}", how))
                # The MusicBrainz cover isn't the Apple album's.
                self.assertEqual(resolution.match.image, "")

    def test_a_cached_match_is_served_canonical_and_stays_raw_in_the_cache(self):
        resolver = Resolver(music_definition(), overrides={})
        entry = Entry(1, "SOS", creator="SZA", year=2022)
        cache.set(resolver._cache_key(entry), Match(MUSICBRAINZ, "music", SOS_MBID, "SOS").as_cache())

        with no_network():
            resolution = resolver.resolve_one(entry)

        self.assertEqual((resolution.match.ref, resolution.how), (f"{APPLE}:music:{SOS}", "cache"))
        self.assertEqual(cache.get(resolver._cache_key(entry))["source"], MUSICBRAINZ)

    def test_a_snapshots_stray_musicbrainz_item_resolves_to_the_moved_album(self):
        definition = music_definition()
        stray = Item.objects.create(source=MUSICBRAINZ, media_type="music", media_id=SOS_MBID, title="SOS")
        entry = Entry(1, "SOS", creator="SZA")
        sync = FeaturedListSync.objects.create(key=definition.key)
        FeaturedListSnapshot.objects.create(sync=sync, taken_on=date(2026, 10, 1), fingerprint="a", entries=[[1, entry.key, stray.pk]])

        with no_network():
            resolution = Resolver(definition, overrides={}, remember=True).resolve_one(entry)

        self.assertEqual((resolution.match.ref, resolution.how), (f"{APPLE}:music:{SOS}", "snapshot"))

    def test_a_batch_of_entries_takes_two_queries(self):
        entries = [Entry(rank, "Album", ids={"mbid_rg": f"{rank:08d}-0000-0000-0000-000000000000"}) for rank in range(1, 40)]
        resolver = Resolver(music_definition(), overrides={})
        with self.assertNumQueries(2):
            resolutions = resolver.resolve(entries)
        self.assertEqual(len(resolutions), 39)


class MusicBrainzCatalogResolveTests(ResolverTestCase):
    def test_todays_matching_is_unchanged(self):
        # A match only the genre pages and featured lists use, with the catalog on
        # MusicBrainz: the list keeps its MusicBrainz album, and nothing is searched.
        MusicAlbumLink.objects.create(release_group_mbid=SOS_MBID, apple_album_id=SOS, status="matched", tier=3, method="search")
        with no_network(), patch("app.providers.apple_music.match_album") as match_album:
            resolution = Resolver(music_definition(), overrides={}).resolve_one(Entry(1, "SOS", creator="SZA", ids={"mbid_rg": SOS_MBID}))
        self.assertEqual(resolution.match.ref, f"{MUSICBRAINZ}:music:{SOS_MBID}")
        match_album.assert_not_called()

    def test_title_searches_ask_only_the_catalogs_source(self):
        definition = make_definition(media_types=("music",), position=750, guards=Guards(max_title_searches=5))
        for catalog in (MUSICBRAINZ, APPLE):
            with (
                self.subTest(catalog=catalog),
                override_settings(MUSIC_CATALOG_SOURCE=catalog),
                patch("app.providers.services.search", return_value=search_response()) as search,
            ):
                cache.clear()
                Resolver(definition, overrides={}).resolve_one(Entry(1, "SOS", creator="SZA"))
            search.assert_called_once_with("music", "SOS", 1, source=catalog, preserve_ranking_fields=True)


@override_settings(MUSIC_CATALOG_SOURCE=APPLE)
class AppleCatalogResolveTests(ResolverTestCase):
    """With the catalog on Apple Music, MusicBrainz albums become their Apple albums."""

    def resolve(self, *entries):
        return Resolver(music_definition(), overrides={}).resolve(list(entries))

    def sos(self, rank=1, **fields):
        return Entry(rank, "SOS", creator="SZA", ids={"mbid_rg": SOS_MBID}, image="https://coverartarchive.org/sos", **fields)

    def test_a_matched_row_gives_its_apple_album_with_its_title_and_cover(self):
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        MusicAlbumLink.objects.create(
            release_group_mbid=SOS_MBID,
            apple_album_id=SOS_CLEAN,
            status="matched",
            tier=3,
            method="search",
            apple_title="SOS",
            apple_image="https://example.com/sos.jpg",
        )
        with no_network():
            (resolution,) = self.resolve(self.sos())
        self.assertEqual(resolution.match, Match(APPLE, "music", SOS, "SOS", "https://example.com/sos.jpg"))
        self.assertEqual(resolution.how, "id")

    def test_an_album_with_its_own_musicbrainz_item_keeps_it(self):
        Item.objects.create(source=MUSICBRAINZ, media_type="music", media_id=SOS_MBID, title="SOS")
        MusicAlbumLink.objects.create(release_group_mbid=SOS_MBID, apple_album_id=SOS, status="matched", tier=3)
        with no_network():
            (resolution,) = self.resolve(self.sos())
        self.assertEqual(resolution.match.ref, f"{MUSICBRAINZ}:music:{SOS_MBID}")

    def test_an_album_with_no_row_is_searched_once_and_recorded(self):
        found = {"media_id": SOS, "title": "SOS", "image": "https://example.com/sos.jpg"}
        with patch("app.providers.apple_music.match_album", return_value=found) as match_album:
            (first,) = self.resolve(self.sos())
            (again,) = self.resolve(self.sos())
        match_album.assert_called_once_with("SOS", "SZA")
        self.assertEqual(first.match.ref, f"{APPLE}:music:{SOS}")
        self.assertEqual(again.match, first.match)
        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.status, link.tier, link.method, link.apple_album_id), ("matched", 3, "search", SOS))

    def test_a_miss_keeps_the_musicbrainz_album(self):
        with patch("app.providers.apple_music.match_album", return_value=None) as match_album:
            (first,) = self.resolve(self.sos())
            (again,) = self.resolve(self.sos())
        match_album.assert_called_once()
        self.assertEqual(first.match.ref, f"{MUSICBRAINZ}:music:{SOS_MBID}")
        self.assertEqual(first.match.image, "https://coverartarchive.org/sos")
        self.assertEqual(again.match, first.match)
        self.assertEqual(MusicAlbumLink.objects.get().status, "unmatched")

    def test_an_apple_failure_keeps_musicbrainz_writes_nothing_and_stops_searching(self):
        error = services.ProviderAPIError(APPLE, Exception("down"))
        ctrl = Entry(2, "Ctrl", creator="SZA", ids={"mbid_rg": CTRL_MBID})
        with patch("app.providers.apple_music.match_album", side_effect=error) as match_album:
            resolutions = self.resolve(self.sos(), ctrl)
        match_album.assert_called_once()
        self.assertEqual([resolution.match.source for resolution in resolutions], [MUSICBRAINZ, MUSICBRAINZ])
        self.assertFalse(MusicAlbumLink.objects.exists())

    def test_searches_are_capped_per_run_and_need_an_artist(self):
        ctrl = Entry(2, "Ctrl", creator="SZA", ids={"mbid_rg": CTRL_MBID})
        no_artist = Entry(3, "Untitled", ids={"mbid_rg": "00000000-0000-0000-0000-000000000003"})
        with (
            patch.object(resolve, "MAX_APPLE_SEARCHES", 1),
            patch("app.providers.apple_music.match_album", return_value=None) as match_album,
        ):
            self.resolve(self.sos(), ctrl, no_artist)
        match_album.assert_called_once_with("SOS", "SZA")


class MusicListWriteTests(FeaturedTestCase):
    def test_two_entries_that_are_one_album_make_one_row(self):
        sos = moved_sos()
        definition = music_definition()
        entries = [
            Entry(1, "SOS", ids={"mbid_rg": SOS_MBID}, image="https://coverartarchive.org/sos"),
            Entry(2, "SOS", ids={"apple_album": SOS}, image="https://example.com/sos.jpg"),
            Entry(3, "Ctrl", ids={"apple_album": CTRL}, image="https://example.com/ctrl.jpg"),
        ]

        with no_network():
            result = self.run_list(definition, entries)

        self.assertEqual(result.status, "ok")
        self.assertListRefs(result.custom_list, [f"{APPLE}:music:{SOS}", f"{APPLE}:music:{CTRL}"])
        self.assertEqual(result.custom_list.customlistitem_set.get(position=1).item, sos)
        self.assertFalse(Item.objects.filter(source=MUSICBRAINZ).exists())

    def test_an_album_that_moves_before_the_write_lands_on_its_apple_item(self):
        definition = music_definition()
        entries = [
            Entry(1, "SOS", ids={"mbid_rg": SOS_MBID}, image="https://coverartarchive.org/sos"),
            Entry(2, "Ctrl", ids={"mbid_rg": CTRL_MBID}, image="https://coverartarchive.org/ctrl"),
        ]
        resolutions = Resolver(definition, overrides={}).resolve(entries)
        sos = moved_sos()  # the data move ran meanwhile

        with no_network():
            writer.commit(
                definition,
                self.owner,
                FetchResult(entries=entries),
                resolutions,
                sync=FeaturedListSync.objects.create(key=definition.key),
                now=timezone.now(),
                definition_hash="hash",
            )

        custom_list = CustomList.objects.get(import_source_id=definition.key)
        self.assertListRefs(custom_list, [f"{APPLE}:music:{SOS}", f"{MUSICBRAINZ}:music:{CTRL_MBID}"])
        self.assertEqual(custom_list.customlistitem_set.get(position=1).item, sos)
        self.assertFalse(Item.objects.filter(source=MUSICBRAINZ, media_id=SOS_MBID).exists())
