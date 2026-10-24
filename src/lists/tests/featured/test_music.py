"""Music adapters: MusicBrainz series, Apple Music, ListenBrainz, 1001 Albums Generator, Official Charts."""

from datetime import date, timedelta
from unittest.mock import patch

import requests

from app.models import Item
from app.providers import services
from lists.featured import registry
from lists.featured.adapters import ADAPTERS, _music, apple_music, official_charts
from lists.featured.definitions import music
from lists.featured.engine import run
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, few_rows, load_fixture, make_definition

TAKE_CARE_RELEASE = "1e268632-6b5a-4592-bd60-64be3276d104"
TAKE_CARE_RG = "0f2ef5ab-cb0b-3b0b-b2ea-cdb3a4e4ff85"
ZACH_RG = "8a8f1e0c-1b31-4f39-9d2c-4a1d5d4f0001"


def definition(key):
    return few_rows(registry.get(key) or next(item for item in music.DEFINITIONS if item.key == key))


def series_definition(**params):
    return make_definition(
        key="test-music",
        media_types=("music",),
        position=799,
        adapter="musicbrainz_series",
        params=params,
        cadence=None,
    )


def group(mbid, title, artist, year, primary="Album", secondary=()):
    return {
        "id": mbid,
        "title": title,
        "primary-type": primary,
        "secondary-types": list(secondary),
        "first-release-date": f"{year}-01-01" if year else "",
        "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": "a", "name": artist}}],
    }


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


class SeriesTests(FeaturedTestCase):
    def test_ranked_series_orders_by_number_with_ids_and_art(self):
        with api_routes({"ws/2/series/": load_fixture("music", "series_ranked.json")}) as mocked:
            result = ADAPTERS["musicbrainz_series"].fetch(definition("guardian-100-best-albums-ever"))

        self.assertEqual([entry.rank for entry in result.entries], [1, 2, 3])  # "n/a" is skipped
        first = result.entries[0]
        self.assertEqual((first.title, first.year, first.creator), ("What’s Going On", 1971, "Marvin Gaye"))
        self.assertEqual(first.ids, {"mbid_rg": "c1fa4d2c-ec62-37d5-b01d-6df7f8fd2c90"})
        self.assertEqual(
            first.image,
            "https://coverartarchive.org/release-group/c1fa4d2c-ec62-37d5-b01d-6df7f8fd2c90/front-500",
        )
        _, kwargs = mocked.call_args
        self.assertIs(kwargs["request_session"], services.musicbrainz_session)
        self.assertIn("Spine/1.0", kwargs["headers"]["User-Agent"])
        self.assertEqual(kwargs["params"], {"fmt": "json", "inc": "release-group-rels+artist-credits"})

    def test_winners_are_newest_first_without_nominees(self):
        with api_routes({"ws/2/series/": load_fixture("music", "series_awards.json")}):
            result = ADAPTERS["musicbrainz_series"].fetch(definition("grammy-album-of-the-year"))

        self.assertEqual([entry.title for entry in result.entries], ["DeBÍ TiRAR MáS FOToS", "COWBOY CARTER", "The Music From Peter Gunn"])
        self.assertEqual([entry.note for entry in result.entries], ["2026", "2025", "1959"])
        self.assertIsNone(result.entries[0].rank)
        self.assertEqual(result.edition, "1959–2026")

    def test_dated_series_keeps_the_window_newest_first(self):
        with (
            api_routes({"ws/2/series/": load_fixture("music", "series_bnm.json")}),
            patch("lists.featured.adapters.musicbrainz_series.timezone.localdate", return_value=date(2026, 10, 22)),
        ):
            result = ADAPTERS["musicbrainz_series"].fetch(definition("pitchfork-best-new-album"))

        self.assertEqual([entry.title for entry in result.entries], ["Pugnello", "ULTIMATE LOVE FOREVER", "Peanut"])
        self.assertEqual(result.entries[0].note, "Sep 25, 2026")
        self.assertEqual(result.edition, "Since Oct 22, 2025")

    def test_latest_takes_the_newest_complete_edition(self):
        searched = load_fixture("music", "series_search.json")
        by_id = {series["id"]: series["name"] for series in searched["series"]}
        newest = next(key for key, name in by_id.items() if name.endswith("2025"))
        ranked = load_fixture("music", "series_ranked.json")

        def lookup(url, params):
            # The 2025 edition is still being entered (one entry); 2024 is complete.
            relations = ranked["relations"][:1] if url.endswith(newest) else ranked["relations"]
            return {"name": by_id[url.rsplit("/", 1)[1]], "relations": relations}

        spec = {"query": "TheNeedleDrop: Top 50 Albums of", "pattern": r"TheNeedleDrop: Top 50 Albums of (\d{4})", "min_entries": 3}
        with api_routes({"ws/2/series/": lookup, "ws/2/series": searched}) as mocked:
            result = ADAPTERS["musicbrainz_series"].fetch(series_definition(latest=spec))

        self.assertEqual(result.edition, "2024")
        self.assertEqual(result.description, "TheNeedleDrop: Top 50 Albums of 2024.")
        self.assertEqual(len(result.entries), 3)
        self.assertEqual(mocked.call_args_list[0].kwargs["params"]["query"], 'series:"TheNeedleDrop: Top 50 Albums of"')

    def test_latest_without_a_complete_edition_fails(self):
        spec = {"query": "x", "pattern": r"Nothing (\d{4})", "min_entries": 1}
        with api_routes({"ws/2/series": load_fixture("music", "series_search.json")}), self.assertRaises(AdapterError):
            ADAPTERS["musicbrainz_series"].fetch(series_definition(latest=spec))

    def test_validate(self):
        validate = ADAPTERS["musicbrainz_series"].validate
        for params in (
            {},
            {"series": "not-a-uuid"},
            {"series": "6a4b53b9-2756-4afe-93f2-306039d41910", "order": "alphabetical"},
            {"series": "6a4b53b9-2756-4afe-93f2-306039d41910", "order": "date"},
            {"latest": {"query": "x", "pattern": "no group"}},
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate(series_definition(**params))
        validate(series_definition(series="6a4b53b9-2756-4afe-93f2-306039d41910"))

    def test_service_unavailable_is_retried(self):
        responses = [http_error(503), load_fixture("music", "series_ranked.json")]

        def flaky(url, params):
            value = responses.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        with api_routes({"ws/2/series/": flaky}), patch("lists.featured.adapters._music.time.sleep") as slept:
            result = ADAPTERS["musicbrainz_series"].fetch(definition("guardian-100-best-albums-ever"))
        self.assertEqual(len(result.entries), 3)
        slept.assert_called_once()

    def test_a_sync_creates_items_without_metadata_calls(self):
        with api_routes({"ws/2/series/": load_fixture("music", "series_ranked.json")}):
            outcome = run(definition("guardian-100-best-albums-ever"), self.owner, trigger="test")

        self.assertEqual(outcome.status, "ok", outcome.message)
        self.assertListRefs(
            outcome.custom_list,
            [
                "musicbrainz:music:c1fa4d2c-ec62-37d5-b01d-6df7f8fd2c90",
                "musicbrainz:music:72d15666-99a7-321e-b1f3-a3f8c09dff9f",
                "musicbrainz:music:e959a4b3-6306-3c45-9df6-e7241dae9ea3",
            ],
        )
        item = Item.objects.get(media_id="72d15666-99a7-321e-b1f3-a3f8c09dff9f")
        self.assertEqual((item.title, item.release_year), ("Revolver", 1966))


class StrictSearchTests(FeaturedTestCase):
    def test_strip_edition(self):
        cases = {
            "Take Care (Deluxe Version)": "Take Care",
            "SOS Deluxe: LANA": "SOS Deluxe: LANA",
            "Westlife - Greatest Hits (Deluxe Edition)": "Westlife - Greatest Hits",
            "Rumours [Remastered 2004]": "Rumours",
            "Hamilton (Original Broadway Cast Recording)": "Hamilton",
            "Abbey Road - Super Deluxe Edition": "Abbey Road",
            "50 Years - Don't Stop": "50 Years - Don't Stop",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_music.strip_edition(title), expected)

    def test_pick_prefers_the_album_and_skips_singles(self):
        groups = [
            group("single", "Take Care", "Drake", 2011, primary="Single"),
            group("live", "Take Care", "Drake", 2011, secondary=("Live",)),
            group("album", "Take Care", "Drake", 2011),
        ]
        self.assertEqual(_music.pick_release_group(groups, "Take Care (Deluxe Version)", "Drake", 2011)["id"], "album")
        self.assertIsNone(_music.pick_release_group(groups[:1], "Take Care", "Drake", 2011))
        self.assertEqual(
            _music.pick_release_group(groups[:1], "Take Care", "Drake", 2011, include_singles=True)["id"],
            "single",
        )

    def test_pick_needs_the_artist_the_year_and_one_best_candidate(self):
        groups = [group("a", "Greatest Hits", "Queen", 1981, secondary=("Compilation",))]
        self.assertIsNone(_music.pick_release_group(groups, "Greatest Hits", "ABBA"))
        self.assertIsNone(_music.pick_release_group(groups, "Greatest Hits", "Queen", 1990))
        self.assertEqual(_music.pick_release_group(groups, "Greatest Hits", "Queen", 1982)["id"], "a")
        twin = [*groups, group("b", "Greatest Hits", "Queen", 2008, secondary=("Compilation",))]
        self.assertIsNone(_music.pick_release_group(twin, "Greatest Hits", "Queen"))
        self.assertEqual(_music.pick_release_group(twin, "Greatest Hits", "Queen", 1981)["id"], "a")

    def test_pick_artist_names_and_article(self):
        cash = [group("cash", "Johnny Cash at San Quentin", "Johnny Cash", 1969, secondary=("Live",))]
        self.assertEqual(_music.pick_release_group(cash, "At San Quentin", "Johnny Cash", 1969)["id"], "cash")
        smiths = [group("smiths", "The Sound of The Smiths", "The Smiths", 2008)]
        self.assertEqual(_music.pick_release_group(smiths, "THE SOUND OF", "SMITHS")["id"], "smiths")
        beatles = [group("road", "Abbey Road", "The Beatles", 1969)]
        self.assertEqual(_music.pick_release_group(beatles, "Abbey Road", "Beatles", 1969)["id"], "road")

    def test_truncated_titles_only_on_chart_pages(self):
        groups = [group("sad", "you seem pretty sad for a girl so in love", "Olivia Rodrigo", 2026)]
        title = "YOU SEEM PRETTY SAD FOR A GIRL SO IN"
        self.assertIsNone(_music.pick_release_group(groups, title, "OLIVIA RODRIGO"))
        self.assertEqual(_music.pick_release_group(groups, title, "OLIVIA RODRIGO", abbreviated=True)["id"], "sad")
        self.assertIsNone(_music.pick_release_group(groups, "YOU SEEM", "OLIVIA RODRIGO", abbreviated=True))

    def test_search_uses_plain_words_then_the_main_title(self):
        queries = []

        def search(url, params):
            queries.append(params["query"])
            if "papercuts" in params["query"] and "singles" not in params["query"]:
                return {"release-groups": [group("p", "Papercuts (Singles Collection 2000–2023)", "Linkin Park", 2024)]}
            return {"release-groups": []}

        with api_routes({"ws/2/release-group": search}):
            info = _music.search_release_group("PAPERCUTS - SINGLES COLLECTION 2000-2023", "LINKIN PARK")
        self.assertEqual(info["id"], "p")
        self.assertEqual(
            queries,
            [
                "releasegroup:(papercuts singles collection 2000 2023) AND artist:(linkin park)",
                "releasegroup:(papercuts) AND artist:(linkin park)",
            ],
        )


class AppleMusicTests(FeaturedTestCase):
    def routes(self, feed):
        def search(url, params):
            if "young manhood" in params["query"]:
                return {"release-groups": [group(ZACH_RG, "Young Manhood and Other Donatable Organs", "Zach Bryan", 2026)]}
            return {"release-groups": []}

        return {
            "rss.marketingtools.apple.com": feed,
            "ws/2/url": load_fixture("music", "url_lookup.json"),
            "ws/2/release-group": search,
            "ws/2/release": {
                "releases": [
                    {
                        "id": TAKE_CARE_RELEASE,
                        "release-group": {"id": TAKE_CARE_RG, "title": "Take Care", "primary-type": "Album"},
                        "artist-credit": [{"name": "Drake", "joinphrase": ""}],
                    },
                ],
            },
        }

    def test_albums_resolve_through_urls_then_search(self):
        with api_routes(self.routes(load_fixture("music", "apple_us.json"))) as mocked:
            result = ADAPTERS["apple_music"].fetch(definition("apple-music-top-albums-us"))

        self.assertEqual(result.edition, "Oct 22, 2026")
        by_title = {entry.title: entry for entry in result.entries}
        self.assertEqual(by_title["Take Care"].ids, {"mbid_rg": TAKE_CARE_RG})  # URL relationship, title from MB
        self.assertEqual(by_title["Young Manhood and Other Donatable Organs"].ids, {"mbid_rg": ZACH_RG})  # search
        unresolved = by_title["Brown Noise: Loops for Relaxation, Deep Sleep, Meditation, And Babies"]
        self.assertEqual((unresolved.ids, unresolved.year, unresolved.rank), ({}, 2018, 3))
        url_call = next(call for call in mocked.call_args_list if "ws/2/url" in call.args[2])
        self.assertIn("https://music.apple.com/us/album/1440642493", url_call.kwargs["params"]["resource"])

    def test_uk_chart_also_looks_up_uk_urls(self):
        self.assertEqual(
            apple_music.album_urls("1", "gb"),
            ["https://music.apple.com/us/album/1", "https://music.apple.com/gb/album/1"],
        )

    def test_gateway_timeouts_are_retried(self):
        feed = [http_error(504), load_fixture("music", "apple_us.json")]

        def flaky(url, params):
            value = feed.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        with api_routes(self.routes(flaky)), patch("lists.featured.adapters.apple_music.time.sleep"):
            result = ADAPTERS["apple_music"].fetch(definition("apple-music-top-albums-us"))
        self.assertEqual(len(result.entries), 3)

    def test_failures_are_adapter_errors(self):
        with (
            api_routes({"rss.marketingtools.apple.com": http_error(504)}),
            patch("lists.featured.adapters.apple_music.time.sleep"),
            self.assertRaises(AdapterError),
        ):
            ADAPTERS["apple_music"].fetch(definition("apple-music-top-albums-us"))
        with api_routes({"rss.marketingtools.apple.com": {"feed": {"results": []}}}), self.assertRaises(AdapterError):
            ADAPTERS["apple_music"].fetch(definition("apple-music-top-albums-us"))


class ListenBrainzTests(FeaturedTestCase):
    def test_repeats_and_singles_are_dropped(self):
        def details(url, params):
            if params["query"].startswith("rgid:"):
                return {
                    "release-groups": [
                        group("21159e3f-172e-43f6-aa7d-8e06a81fea49", "ARIRANG", "BTS", 2026),
                        group("4c87e1ec-5d76-4f74-a683-2177e5f0ddf1", "D‐DAY", "Agust D", 2023),
                        group("066ee758-118c-47f9-b2bd-13b07e005ade", "MUSE", "Jimin", 2024, primary="Single"),
                    ],
                }
            return {"release-groups": [group("indigo", "Indigo", "RM", 2022)]}

        routes = {"api.listenbrainz.org": load_fixture("music", "listenbrainz_week.json"), "ws/2/release-group": details}
        with api_routes(routes) as mocked:
            result = ADAPTERS["listenbrainz"].fetch(definition("listenbrainz-top-albums-week"))

        self.assertEqual(result.edition, "Week of Oct 12, 2026")
        self.assertEqual([entry.title for entry in result.entries], ["ARIRANG", "D‐DAY", "Indigo"])
        self.assertEqual([entry.rank for entry in result.entries], [1, 2, 3])
        self.assertEqual(result.entries[2].ids, {"mbid_rg": "indigo"})  # found by a strict search
        self.assertEqual(result.entries[0].note, "284,179 listens")
        self.assertEqual(mocked.call_args_list[0].kwargs["params"], {"range": "week", "count": 100})

    def test_validate_and_empty(self):
        with self.assertRaises(ValueError):
            ADAPTERS["listenbrainz"].validate(series_definition(range="forever"))
        with api_routes({"api.listenbrainz.org": {"payload": {"release_groups": []}}}), self.assertRaises(AdapterError):
            ADAPTERS["listenbrainz"].fetch(definition("listenbrainz-top-albums-week"))


class AlbumsGeneratorTests(FeaturedTestCase):
    def test_highest_rated_with_spotify_urls(self):
        def url_lookup(url, params):
            resource = next(item for item in params["resource"] if item.endswith("63k57x0qOkUWEMR0dkMivh"))
            return {"urls": [{"resource": resource, "relations": [{"target-type": "release", "release": {"id": "r1"}}]}]}

        def search(url, params):
            if "dark side" in params["query"]:
                return {"release-groups": [group("dsotm", "The Dark Side of the Moon", "Pink Floyd", 1973)]}
            return {"release-groups": [group("road", "Abbey Road", "The Beatles", 1969)]}

        routes = {
            "1001albumsgenerator.com": load_fixture("music", "generator_stats.json"),
            "ws/2/url": url_lookup,
            "ws/2/release-group": search,
            "ws/2/release": {"releases": [{"id": "r1", "release-group": {"id": "rumours", "title": "Rumours"}}]},
        }
        with api_routes(routes):
            result = ADAPTERS["albums_generator"].fetch(definition("1001-albums-generator-highest-rated"))

        # Rumours and Abbey Road tie on 4.45; more votes ranks first. Five votes is too few.
        self.assertEqual([entry.title for entry in result.entries], ["Rumours", "Abbey Road", "The Dark Side of the Moon"])
        self.assertEqual([entry.ids["mbid_rg"] for entry in result.entries], ["rumours", "road", "dsotm"])
        self.assertEqual(result.entries[0].note, "★ 4.45 · 31,752 votes")


class OfficialChartsTests(FeaturedTestCase):
    def test_the_friday_chart_is_read_within_two_days(self):
        uk = registry.get("official-charts-uk-albums")
        self.assertLessEqual(uk.cadence, timedelta(days=2))
        self.assertEqual(registry.cadence_label(uk), "weekly")

    def test_parse_chart(self):
        rows, start = official_charts.parse_chart(load_fixture("music", "albums_chart.html"))
        self.assertEqual(
            rows,
            [
                (1, "GHOSTS (100 DAYS OF MORNING)", "JAMIE T"),
                (2, "GENERIC EVERYTHING", "CHASE & STATUS"),
                (3, "YOU SEEM PRETTY SAD FOR A GIRL SO IN", "OLIVIA RODRIGO"),
            ],
        )
        self.assertEqual(start, date(2026, 10, 16))

    def test_fetch_matches_truncated_titles_and_verified_albums(self):
        html = load_fixture("music", "albums_chart.html")
        queen = html.replace("GENERIC EVERYTHING", "GREATEST HITS").replace("CHASE &amp; STATUS", "QUEEN").replace(
            "CHASE & STATUS",
            "QUEEN",
        )

        def search(url, params):
            if params["query"].startswith("rgid:"):
                return {"release-groups": [group("69ce61c8-127f-3809-95d8-62fdf3ae1347", "Greatest Hits", "Queen", 1981)]}
            if "rodrigo" in params["query"]:
                return {"release-groups": [group("sad", "you seem pretty sad for a girl so in love", "Olivia Rodrigo", 2026)]}
            return {"release-groups": []}

        with (
            api_routes({"officialcharts.com": queen, "ws/2/release-group": search}),
            patch("lists.featured.adapters.official_charts.MIN_ROWS", 3),
        ):
            result = ADAPTERS["official_charts"].fetch(definition("official-charts-uk-albums"))

        self.assertEqual(result.edition, "Week of Oct 16, 2026")
        self.assertEqual(
            [(entry.rank, entry.title, entry.ids.get("mbid_rg")) for entry in result.entries],
            [
                (1, "GHOSTS (100 DAYS OF MORNING)", None),
                (2, "Greatest Hits", "69ce61c8-127f-3809-95d8-62fdf3ae1347"),
                (3, "you seem pretty sad for a girl so in love", "sad"),
            ],
        )

    def test_a_changed_layout_is_an_error(self):
        with api_routes({"officialcharts.com": "<html></html>"}), self.assertRaises(AdapterError):
            ADAPTERS["official_charts"].fetch(definition("official-charts-uk-albums"))


class MusicDefinitionTests(FeaturedTestCase):
    def test_every_music_list_skips_the_generic_title_search(self):
        for item in music.DEFINITIONS:
            with self.subTest(key=item.key):
                self.assertEqual(item.guards.max_title_searches, 0)
                self.assertTrue(700 <= item.position <= 749)
                self.assertEqual(item.media_types, ("music",))
