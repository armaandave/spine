"""Apple Music search, people search and artist pages, from recorded responses."""

import copy
from datetime import date
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

from app.models import AppleAlbum, Sources
from app.providers import apple_music, services
from app.providers.search_rank import RANKING_KEYS
from app.tests.providers.test_apple_music import (
    AppleMusicTestCase,
    apple_item,
    clear_pause,
    fixture,
    query,
)

SZA_ID = "605800394"
SOS_IMAGE = (
    "https://is1-ssl.mzstatic.com/image/thumb/Music122/v4/bd/3b/a9/"
    "bd3ba9fb-9609-144f-bcfe-ead67b5f6ab3/196589564931.jpg/600x600bb.jpg"
)


def search_answers(*names):
    """Return the recorded answers for search pages: each search, then its families."""
    answers = []
    for name in names:
        answers += [
            (200, fixture(f"search_albums_{name}.json")),
            (200, fixture(f"search_families_{name}.json")),
        ]
    return answers


def person_answers():
    return [
        (200, fixture("person_sza.json")),
        (200, fixture("person_sza_singles_2.json")),
        (200, fixture("person_sza_appears-on-albums_2.json")),
        (200, fixture("person_sza_families_1.json")),
    ]


def ids(results):
    return [row["media_id"] for row in results]


class AlbumSearchTests(AppleMusicTestCase):
    def test_apples_order_with_each_listing_once(self):
        fake = self.apple(*search_answers("sza_sos_1"))

        data = apple_music.search_albums("sza sos", 1)

        self.assertEqual(urlsplit(fake.requests[0].url).path, "/v1/catalog/us/search")
        self.assertEqual(query(fake.requests[0]), {"term": "sza sos", "types": "albums", "limit": "25"})
        searched = [album["id"] for album in fixture("search_albums_sza_sos_1.json")["results"]["albums"]["data"]]
        self.assertEqual(
            query(fake.requests[1]),
            {"ids": ",".join(searched), "views": "other-versions"},
        )
        # Clean twins and LANA's 38-track upload fold into their listings; Avicii's
        # incomplete single and BLAZE 2.0's are hidden.
        self.assertEqual(
            ids(data["results"])[:8],
            [
                "1657869377",
                "1794644675",
                "1628387909",
                "1239976329",
                "1546390050",
                "1598221271",
                "1689825179",
                "1764132972",
            ],
        )
        for left_out in ("1658650093", "1796270264", "1786643044", "1457882147", "1649413469", "1569461513"):
            self.assertNotIn(left_out, ids(data["results"]))
        self.assertEqual(len(ids(data["results"])), len(set(ids(data["results"]))))
        self.assertEqual((data["page"], data["total_pages"], data["total_results"]), (1, 2, 19))

    def test_each_row_shows_its_listing(self):
        self.apple(*search_answers("sza_sos_1"))

        sos, lana = apple_music.search_albums("sza sos", 1)["results"][:2]

        self.assertEqual(
            sos,
            {
                "media_id": "1657869377",
                "source": "apple_music",
                "media_type": "music",
                "title": "SOS",
                "artist_credits": [{"artist_mbid": None, "name": "SZA", "join_phrase": ""}],
                "release_date": "2022-12-09",
                "primary_type": "Album",
                "secondary_types": [],
                "disambiguation": None,
                "content_rating": "explicit",
                "image": SOS_IMAGE,
                "poster_width": 600,
                "poster_height": 600,
                "poster_aspect_ratio": 1.0,
                "subtitle": "SZA · 2022 · Album",
            },
        )
        # The 42-track explicit album is LANA's identity and its best member.
        self.assertEqual((lana["media_id"], lana["title"]), ("1794644675", "SOS Deluxe: LANA"))
        self.assertEqual(
            dict(AppleAlbum.objects.filter(apple_album_id__in=["1786643044", "1796270264"]).values_list(
                "apple_album_id",
                "canonical_id",
            )),
            {"1786643044": "1794644675", "1796270264": "1794644675"},
        )

    def test_a_later_page_never_repeats_a_listing(self):
        fake = self.apple(*search_answers("sza_sos_1", "sza_sos_2"))

        first = apple_music.search_albums("sza sos", 1)["results"]
        second = apple_music.search_albums("sza sos", 2)

        self.assertEqual(urlsplit(fake.requests[2].url).path, "/v1/catalog/us/search")
        self.assertEqual(query(fake.requests[2])["offset"], "25")
        self.assertEqual(len(fake.requests), 4)
        # Page 2 has the clean Good Days, Ctrl and Black Panther, whose explicit
        # listings page 1 showed.
        for clean_twin, listing in (
            ("1546390012", "1546390050"),
            ("1239977075", "1239976329"),
            ("1440903005", "1440906927"),
        ):
            self.assertIn(listing, ids(first))
            self.assertNotIn(clean_twin, ids(second["results"]))
            self.assertNotIn(listing, ids(second["results"]))
        self.assertFalse(set(ids(first)) & set(ids(second["results"])))
        # The clean Finally Rich opens as its explicit album, which wasn't searched.
        self.assertIn("1440853396", ids(second["results"]))
        self.assertNotIn("1444005836", ids(second["results"]))
        self.assertEqual((second["total_pages"], second["total_results"]), (3, 40))

    def test_a_later_page_alone_reads_the_pages_before_it(self):
        fake = self.apple(*search_answers("sza_sos_1", "sza_sos_2"))

        second = apple_music.search_albums("sza sos", 2)
        first = apple_music.search_albums("sza sos", 1)

        self.assertEqual(len(fake.requests), 4)
        self.assertEqual(ids(first["results"])[0], "1657869377")
        self.assertNotIn("1546390050", ids(second["results"]))
        self.assertNotIn("1546390012", ids(second["results"]))

    def test_past_apples_last_page_is_empty_without_requests(self):
        last = fixture("search_albums_donda.json")
        del last["results"]["albums"]["next"]
        fake = self.apple((200, last), (200, fixture("search_families_donda.json")))

        first = apple_music.search_albums("kanye west donda", 1)
        later = apple_music.search_albums("kanye west donda", 5)

        self.assertEqual(first["total_pages"], 1)
        self.assertEqual((later["results"], later["total_pages"]), ([], 5))
        self.assertEqual(later["total_results"], first["total_results"])
        self.assertEqual(len(fake.requests), 2)

    def test_ep_and_single_suffixes_become_the_type(self):
        self.apple(*search_answers("sza_sos_1"))

        rows = {row["media_id"]: row for row in apple_music.search_albums("sza sos", 1)["results"]}

        good_days = rows["1546390050"]
        self.assertEqual((good_days["title"], good_days["primary_type"]), ("Good Days", "Single"))
        self.assertEqual(good_days["subtitle"], "SZA · 2020 · Single")
        ep = rows["1764994407"]
        self.assertEqual((ep["title"], ep["primary_type"]), ("SOS (Sex on Sight)", "EP"))
        self.assertEqual(ep["subtitle"], "Victoria Monét & USHER · 2024 · EP")

    def test_only_an_album_with_no_explicit_copy_is_labelled_clean(self):
        self.apple(*search_answers("donda"))

        rows = {row["media_id"]: row for row in apple_music.search_albums("kanye west donda", 1)["results"]}

        deluxe = rows["1595496182"]
        self.assertEqual(deluxe["content_rating"], "clean")
        self.assertEqual(deluxe["subtitle"], "Kanye West · 2021 · Album · Clean")
        # Donda itself has no rating; The Life of Pablo's clean copy folds into the
        # explicit one, which Apple's search left out.
        self.assertEqual((rows["1587795158"]["content_rating"], rows["1587795158"]["subtitle"]), (None, "Kanye West · 2021 · Album"))
        self.assertNotIn("1442966257", rows)
        self.assertEqual(rows["1443063578"]["content_rating"], "explicit")

    def test_my_turn_is_two_albums(self):
        self.apple(*search_answers("my_turn"))

        results = apple_music.search_albums("lil baby my turn", 1)["results"]

        self.assertEqual(ids(results)[:2], ["1534816297", "1498988850"])
        self.assertEqual([row["title"] for row in results[:2]], ["My Turn (Deluxe)", "My Turn"])
        for folded in ("1509850659", "1499567361", "1534811491", "1510840178"):
            self.assertNotIn(folded, ids(results))

    def test_an_item_keeps_its_identity_in_search(self):
        apple_item("1786643044", title="SOS Deluxe: LANA")
        self.apple(*search_answers("sza_sos_1"))

        results = apple_music.search_albums("sza sos", 1)["results"]

        self.assertIn("1786643044", ids(results))
        self.assertNotIn("1794644675", ids(results))

    @patch("app.providers.apple_music.timezone.localdate", return_value=date(2026, 10, 26))
    def test_pre_orders_and_video_albums_stay(self, _today):
        albums = [
            fixture("page_weathered_preorder.json")["data"][0],
            fixture("page_multitude_video.json")["data"][0],
        ]
        search = {"results": {"albums": {"data": [{"id": album["id"], "type": "albums"} for album in albums]}}}
        self.apple((200, search), (200, {"data": albums}))

        results = apple_music.search_albums("weathered multitude", 1)["results"]

        self.assertEqual(ids(results), ["6803287133", "1621698801"])
        self.assertEqual(results[1]["title"], "Multitude (Video Album)")

    def test_ranking_fields_only_for_all_media_search(self):
        self.apple(*search_answers("sza_sos_1"))

        plain = apple_music.search_albums("sza sos", 1)["results"]
        ranked = apple_music.search_albums("sza sos", 1, preserve_ranking_fields=True)["results"]

        self.assertFalse(any(key in row for row in plain for key in RANKING_KEYS))
        self.assertEqual([row["provider_rank_boost"] for row in ranked[:12]], [30, 27, 24, 21, 18, 15, 12, 9, 6, 3, 0, 0])
        self.assertEqual(ranked[0]["artist_name"], "SZA")
        self.assertEqual(ranked[0]["first_release_date"], "2022-12-09")
        self.assertEqual(ids(plain), ids(ranked))

    def test_a_search_is_cached_for_both_kinds_of_search(self):
        fake = self.apple(*search_answers("sza_sos_1"))

        apple_music.search_albums("sza sos", 1)
        apple_music.search_albums("  SZA   sos ", 1, preserve_ranking_fields=True)

        self.assertEqual(len(fake.requests), 2)

    def test_an_empty_query_makes_no_request(self):
        fake = self.apple()

        self.assertEqual(apple_music.search_albums("   ", 1)["results"], [])
        self.assertEqual(fake.requests, [])

    def test_search_never_sleeps_and_waits_out_the_pause(self):
        background = self.apple(background=True)
        fake = self.apple((429, {"errors": []}))

        with self.assertRaises(services.ProviderAPIError) as raised:
            apple_music.search_albums("sza sos", 1)
        self.assertEqual(raised.exception.status_code, 429)
        with self.assertRaises(apple_music.SkippedRequestError):
            apple_music.search_albums("sza sos", 1)

        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.options[0]["timeout"], apple_music.USER_TIMEOUT)
        self.assertEqual(background.requests, [])
        self.sleep.assert_not_called()

    def test_a_failed_family_batch_caches_nothing(self):
        fake = self.apple(
            (200, fixture("search_albums_sza_sos_1.json")),
            (503, b""),
            *search_answers("sza_sos_1"),
        )

        with self.assertRaises(services.ProviderAPIError):
            apple_music.search_albums("sza sos", 1)
        clear_pause()
        results = apple_music.search_albums("sza sos", 1)["results"]

        self.assertEqual(ids(results)[0], "1657869377")
        self.assertEqual(len(fake.requests), 4)


class PeopleSearchTests(AppleMusicTestCase):
    def test_artists_with_their_pictures(self):
        fake = self.apple((200, fixture("search_artists_sza.json")))

        people = apple_music.search_people("sza", limit=10)

        self.assertEqual(query(fake.requests[0]), {"term": "sza", "types": "artists", "limit": "10"})
        self.assertEqual(
            people[0],
            {
                "source": "apple_music",
                "person_id": SZA_ID,
                "name": "SZA",
                "profile_url": (
                    "https://is1-ssl.mzstatic.com/image/thumb/AMCArtistImages221/v4/6e/00/30/"
                    "6e003069-1d1f-480c-aab8-806892e73877/ami-identity-294a220289779df8e389876bc46ac060-"
                    "2024-12-20T20-08-51.286Z_cropped.png/600x600bb.jpg"
                ),
                "known_for_department": "Artist",
            },
        )
        self.assertEqual(len(people), 10)
        self.assertIsNone(people[2]["profile_url"])

    def test_at_most_25_and_the_limit_asked_for(self):
        fake = self.apple((200, fixture("search_artists_sza.json")), (200, fixture("search_artists_sza.json")))

        self.assertEqual(len(apple_music.search_people("sza", limit=3)), 3)
        apple_music.search_people("sza", limit=40)

        self.assertEqual([query(request)["limit"] for request in fake.requests], ["3", "25"])


class ArtistPageTests(AppleMusicTestCase):
    def groups(self, page):
        groups = {}
        for credit in page["credits"]:
            for role in credit["credit_roles"]:
                groups.setdefault(role, []).append(credit["media_id"])
        return groups

    def test_apples_sections_fill_the_music_credit_groups(self):
        fake = self.apple(*person_answers())

        page = apple_music.person_page(SZA_ID)

        self.assertEqual(
            [urlsplit(request.url).path for request in fake.requests],
            [
                f"/v1/catalog/us/artists/{SZA_ID}",
                f"/v1/catalog/us/artists/{SZA_ID}/view/singles",
                f"/v1/catalog/us/artists/{SZA_ID}/view/appears-on-albums",
                "/v1/catalog/us/albums",
            ],
        )
        self.assertEqual(query(fake.requests[0]), {"views": ",".join(apple_music.ARTIST_VIEWS)})
        self.assertEqual(query(fake.requests[1]), {"offset": "10", "limit": "100"})
        self.assertEqual(query(fake.requests[3])["views"], "other-versions")
        groups = self.groups(page)
        self.assertEqual(list(groups), ["Albums", "Singles", "EPs", "Live releases", "Appears on"])
        # Apple's Albums section, as Apple orders it.
        self.assertEqual(groups["Albums"], ["1794644675", "1657869377", "1628387909"])
        self.assertEqual(groups["EPs"], ["852343474"])
        self.assertEqual(groups["Live releases"], ["1728151465"])
        self.assertEqual(len(groups["Singles"]), 22)
        self.assertEqual(groups["Singles"][:2], ["6819802963", "1894091581"])
        # Kill Bill's incomplete single and Rihanna's incomplete EP are hidden, and
        # Black Panther's clean copy folds into the explicit one.
        self.assertNotIn("1664548283", groups["Singles"])
        self.assertNotIn("1445029775", groups["Appears on"])
        self.assertIn("1440906927", groups["Appears on"])
        self.assertNotIn("1440903005", groups["Appears on"])
        self.assertEqual(len(groups["Appears on"]), 12)

    def test_the_person_and_each_credit(self):
        self.apple(*person_answers())

        page = apple_music.person_page(SZA_ID)

        self.assertEqual(
            {key: value for key, value in page.items() if key != "credits"},
            {
                "source": "apple_music",
                "person_id": SZA_ID,
                "name": "SZA",
                "image": (
                    "https://is1-ssl.mzstatic.com/image/thumb/AMCArtistImages221/v4/6e/00/30/"
                    "6e003069-1d1f-480c-aab8-806892e73877/ami-identity-294a220289779df8e389876bc46ac060-"
                    "2024-12-20T20-08-51.286Z_cropped.png/600x600bb.jpg"
                ),
                "biography": None,
                "known_for_department": "Artist",
                "birth_date": None,
                "death_date": None,
                "place_of_birth": None,
                "popularity": None,
            },
        )
        sos = next(credit for credit in page["credits"] if credit["media_id"] == "1657869377")
        self.assertEqual(
            sos,
            {
                "media_type": "music",
                "source": "apple_music",
                "media_id": "1657869377",
                "title": "SOS",
                "image": SOS_IMAGE,
                "release_date": "2022-12-09",
                "year": "2022",
                "genres": ["r&b"],
                "content_rating": "explicit",
                "roles": ["Artist"],
                "credit_roles": ["Albums"],
                "vote_average": None,
                "vote_count": None,
            },
        )
        z = next(credit for credit in page["credits"] if credit["media_id"] == "852343474")
        self.assertEqual(z["title"], "Z")

    def test_an_album_in_two_sections_is_one_credit_in_both_groups(self):
        artist = fixture("person_sza.json")
        views = artist["data"][0]["views"]
        views["live-albums"]["data"].append(copy.deepcopy(views["full-albums"]["data"][1]))
        self.apple(
            (200, artist),
            (200, fixture("person_sza_singles_2.json")),
            (200, fixture("person_sza_appears-on-albums_2.json")),
            (200, fixture("person_sza_families_1.json")),
        )

        page = apple_music.person_page(SZA_ID)

        sos = [credit for credit in page["credits"] if credit["media_id"] == "1657869377"]
        self.assertEqual(len(sos), 1)
        self.assertEqual(sos[0]["credit_roles"], ["Albums", "Live releases"])

    def test_an_item_keeps_its_identity_on_the_artist_page(self):
        apple_item("1786643044", title="SOS Deluxe: LANA")
        self.apple(*person_answers())

        groups = self.groups(apple_music.person_page(SZA_ID))

        self.assertEqual(groups["Albums"][0], "1786643044")

    def test_the_artist_page_is_cached(self):
        fake = self.apple(*person_answers())

        self.assertEqual(apple_music.person_page(SZA_ID), apple_music.person_page(SZA_ID))
        self.assertEqual(len(fake.requests), 4)

    def test_a_404_artist_raises(self):
        self.apple((404, fixture("error_404.json")["body"]))

        with self.assertRaises(services.ProviderAPIError) as raised:
            apple_music.person_page("1")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertIsNone(cache.get("apple_music_v1_artist_page_1"))


class SourceTests(AppleMusicTestCase):
    def test_music_search_goes_to_apple_only_for_apple_music(self):
        self.apple(*search_answers("sza_sos_1"))

        data = services.search("music", "sza sos", 1, "apple_music", preserve_ranking_fields=True, timeout=8)

        self.assertEqual(data["results"][0]["source"], "apple_music")
        self.assertEqual(data["results"][0]["provider_rank_boost"], 30)
        with patch("app.providers.services.musicbrainz.search", return_value={"results": []}) as search:
            services.search("music", "sza sos", 1, "musicbrainz", preserve_ranking_fields=True, timeout=8)
            services.search("music", "sza sos", 2)
        self.assertEqual(
            [(call.args, call.kwargs) for call in search.call_args_list],
            [
                (("sza sos", 1), {"preserve_ranking_fields": True, "timeout": 8}),
                (("sza sos", 2), {}),
            ],
        )

    def test_artist_pages_and_people_search_by_source(self):
        self.apple(*person_answers(), (200, fixture("search_artists_sza.json")))

        self.assertEqual(services.get_person_page("apple_music", SZA_ID)["name"], "SZA")
        people = services.search_people("apple_music", "sza", limit=5)

        self.assertEqual([person["source"] for person in people], ["apple_music"] * 5)
        self.assertIn(Sources.APPLE_MUSIC.value, services.SUPPORTED_PERSON_SOURCES)
        with patch("app.providers.services.musicbrainz.person_page", return_value={"name": "SZA"}) as page:
            services.get_person_page("musicbrainz", "a8d2ab4b-2a6d-4d1f-9b14-2d4b2e4b6c11")
        page.assert_called_once_with("a8d2ab4b-2a6d-4d1f-9b14-2d4b2e4b6c11")

    def test_people_search_asks_only_the_catalogs_music_artists(self):
        before = ("tmdb", "hardcover", "openlibrary", "musicbrainz", "mal", "mangaupdates", "anilist")

        self.assertEqual(services.person_search_sources(), before)
        with override_settings(MUSIC_CATALOG_SOURCE="apple_music"):
            self.assertEqual(
                services.person_search_sources(),
                ("tmdb", "hardcover", "openlibrary", "apple_music", "mal", "mangaupdates", "anilist"),
            )
        with override_settings(MUSIC_CATALOG_SOURCE="apple-music"):
            self.assertEqual(services.person_search_sources(), before)
        self.assertEqual(settings.MUSIC_CATALOG_SOURCE, "musicbrainz")
