from datetime import date
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from django.core.cache import cache

from app.models import AppleAlbum
from app.providers import apple_music, services
from app.tests.providers.test_apple_music import (
    AppleMusicTestCase,
    apple_item,
    clear_pause,
    fixture,
    query,
    related_answers,
)

SOS = "1657869377"
SOS_CLEAN = "1658650093"
LANA = "1794644675"
LANA_38 = "1786643044"
LANA_CLEAN = "1796270264"
THRILLER = "269572838"
WHOS_NEXT_DELUXE = "1440850317"
WHOS_NEXT_2022 = "1706357665"
FATD = "1710685602"
SCARY_HOURS = "1716979097"
# Apple's album pages, by album: the fixture, its editions fixtures and its artist's.
PAGES = {
    SOS: ("page_sos.json", "sos", "sza"),
    LANA: ("page_lana.json", "lana", "sza"),
    THRILLER: ("page_thriller.json", "thriller", "michael_jackson"),
    WHOS_NEXT_DELUXE: ("page_whos_next_deluxe.json", "whos_next_deluxe", "the_who"),
    WHOS_NEXT_2022: ("page_whos_next_2022.json", "whos_next_2022", "the_who"),
    FATD: ("page_fatd.json", "fatd", "drake"),
    SCARY_HOURS: ("page_fatd_scary_hours.json", "fatd_scary_hours", "drake"),
}


def path(request):
    return urlsplit(request.url).path.removeprefix("/v1/catalog/us/")


def badges(rows):
    return [(row["media_id"], row["title"], row["relation"]) for row in rows]


def ids(rows):
    return [row["media_id"] for row in rows]


@patch("app.providers.apple_music.timezone.localdate", return_value=date(2026, 10, 26))
class RelatedSectionTests(AppleMusicTestCase):
    def album(self, album_id, *answers):
        page, editions, artist = PAGES[album_id]
        fake = self.apple((200, fixture(page)), *(answers or related_answers(editions, artist)))
        return apple_music.music(album_id, related=True), fake

    def test_sos_and_its_deluxe_link_each_other(self, _today):
        sos, fake = self.album(SOS)

        # Apple's family holds only SOS's clean twin; the deluxe comes from shared songs,
        # and its three albums show as one.
        self.assertEqual(badges(sos["related"]["other_versions"]), [(LANA, "SOS Deluxe: LANA", "Deluxe")])
        self.assertEqual(
            [path(request) for request in fake.requests],
            [f"albums/{SOS}", "songs", "albums", "artists/605800394/view/full-albums"],
        )
        self.assertEqual(query(fake.requests[1]), {"filter[isrc]": "USRC12204583"})
        # SOS's own listing isn't asked for.
        self.assertEqual(
            query(fake.requests[2]),
            {"ids": f"{LANA},{LANA_38}", "include": "tracks", "views": "other-versions"},
        )
        self.assertEqual(query(fake.requests[3]), {"limit": "20"})

        lana, fake = self.album(LANA, *related_answers("lana", "sza")[:2])

        self.assertEqual(badges(lana["related"]["other_versions"]), [(SOS, "SOS", "Original album")])
        # LANA starts with its new songs, so the first song of each disc is looked up.
        self.assertEqual(query(fake.requests[1]), {"filter[isrc]": "USRC12403823,USRC12204583"})
        self.assertEqual(query(fake.requests[2])["ids"], SOS)
        # The artist's albums are cached for a day.
        self.assertEqual(len(fake.requests), 3)

    def test_a_family_comes_first_then_editions_by_shared_songs(self, _today):
        thriller, _ = self.album(THRILLER)

        # Not the soundtrack that shares a few songs, nor compilations.
        self.assertEqual(
            badges(thriller["related"]["other_versions"]),
            [
                ("273048762", "Thriller (25th Anniversary) [Deluxe Edition]", "Anniversary"),
                ("1641780996", "Thriller 40", "Deluxe"),
            ],
        )

    def test_whos_next(self, _today):
        deluxe, _ = self.album(WHOS_NEXT_DELUXE)
        remaster, _ = self.album(WHOS_NEXT_2022, *related_answers("whos_next_2022", "the_who")[:2])

        self.assertEqual(
            badges(deluxe["related"]["other_versions"]),
            [
                (WHOS_NEXT_2022, "Who's Next (Remastered 2022)", "Remaster"),
                ("1440815866", "Who's Next (Bonus Track Version)", "Deluxe"),
            ],
        )
        # Life House holds every song of the 2022 remaster; the older deluxe has none.
        self.assertEqual(
            badges(remaster["related"]["other_versions"]),
            [
                (WHOS_NEXT_DELUXE, "Who's Next (Deluxe Edition)", "Deluxe"),
                ("1440815866", "Who's Next (Bonus Track Version)", "Deluxe"),
                ("1698563957", "Who’s Next : Life House (Super Deluxe)", "Deluxe"),
            ],
        )

    def test_the_original_of_a_deluxe_in_the_same_family(self, _today):
        original, _ = self.album(FATD)
        deluxe, _ = self.album(SCARY_HOURS, *related_answers("fatd_scary_hours", "drake")[:2])

        self.assertEqual(
            badges(original["related"]["other_versions"]),
            [(SCARY_HOURS, "For All The Dogs Scary Hours Edition", "Deluxe")],
        )
        self.assertEqual(
            badges(deluxe["related"]["other_versions"]),
            [(FATD, "For All The Dogs", "Original album")],
        )

    def test_only_the_artists_own_editions_with_the_same_rating(self, _today):
        # Kill Bill is on SOS, LANA, the Kill Bill single and many compilations; the
        # clean albums have it too.
        sos, fake = self.album(
            SOS,
            (200, fixture("editions_songs_kill_bill.json")),
            (200, fixture("editions_albums_kill_bill.json")),
            (200, fixture("artist_albums_sza.json")),
        )

        self.assertEqual(badges(sos["related"]["other_versions"]), [(LANA, "SOS Deluxe: LANA", "Deluxe")])
        self.assertNotIn(SOS_CLEAN, query(fake.requests[2])["ids"].split(","))

    def test_each_rule_for_an_edition(self, _today):
        def lose_kill_bill(album):
            tracks = album["relationships"]["tracks"]
            tracks["data"] = [song for song in tracks["data"] if song["attributes"]["isrc"] != "USRC12204584"]

        changes = {
            "another artist": lambda album: album["relationships"].update(
                artists={"data": [{"id": "1", "type": "artists"}]},
            ),
            "a compilation": lambda album: album["attributes"].update(isCompilation=True),
            "a single": lambda album: album["attributes"].update(isSingle=True),
            "another rating": lambda album: album["attributes"].update(contentRating="clean"),
            "only some of this album's songs": lose_kill_bill,
        }
        for name, change in changes.items():
            with self.subTest(name):
                cache.clear()
                editions = fixture("editions_albums_sos.json")
                for album in editions["data"]:
                    change(album)
                sos, _ = self.album(
                    SOS,
                    (200, fixture("editions_songs_sos.json")),
                    (200, editions),
                    (200, fixture("artist_albums_sza.json")),
                )
                self.assertNotIn("other_versions", sos["related"])

    def test_an_item_keeps_its_identity_in_other_versions(self, _today):
        apple_item(LANA_38, title="SOS Deluxe: LANA")

        sos, _ = self.album(SOS)

        self.assertEqual(ids(sos["related"]["other_versions"]), [LANA_38])
        rows = AppleAlbum.objects.filter(apple_album_id__in=(LANA, LANA_38, LANA_CLEAN))
        self.assertEqual(
            dict(rows.values_list("apple_album_id", "canonical_id")),
            {LANA: LANA_38, LANA_38: LANA_38, LANA_CLEAN: LANA_38},
        )

    def test_more_by_is_the_artists_albums_without_this_one_or_hidden_ones(self, _today):
        thriller, _ = self.album(THRILLER)
        sos, _ = self.album(SOS)

        more_by = thriller["related"]["more_by_artist"]
        self.assertEqual(len(more_by), 10)
        self.assertNotIn(THRILLER, ids(more_by))
        # HIStory Continues is among Apple's first ten, but incomplete with a past date.
        apple_order = [album["id"] for album in fixture("artist_albums_michael_jackson.json")["data"]]
        self.assertLess(apple_order.index("1894092998"), 10)
        self.assertNotIn("1894092998", ids(more_by))
        self.assertEqual(
            [(row["media_id"], row["title"], row["subtitle"]) for row in sos["related"]["more_by_artist"]],
            [(LANA, "SOS Deluxe: LANA", "SZA · 2024 · Album"), ("1628387909", "Ctrl (Deluxe)", "SZA · 2017 · Album")],
        )
        self.assertNotIn("relation", sos["related"]["more_by_artist"][0])

    def test_you_might_also_like_is_apples_similar_albums(self, _today):
        page = fixture("page_sos.json")
        similar = page["data"][0]["views"]["related-albums"]["data"]
        del similar[1]["attributes"]["playParams"]

        fake = self.apple((200, page), *related_answers())
        sos = apple_music.music(SOS, related=True)

        rows = sos["related"]["related_albums"]
        self.assertEqual(ids(rows), [album["id"] for album in similar if album["id"] != similar[1]["id"]])
        self.assertEqual(rows[0]["title"], "Still Over It")
        self.assertEqual(rows[0]["subtitle"], "Summer Walker · 2021 · Album")
        self.assertTrue(rows[0]["image"].endswith("/600x600bb.jpg"))
        # Read from the album's own page: no request of its own.
        self.assertEqual(len(fake.requests), 4)

    def test_at_most_ten_albums_a_section(self, _today):
        with patch.object(apple_music, "RELATED_LIMIT", 1):
            sos, _ = self.album(SOS)

        self.assertEqual({key: len(rows) for key, rows in sos["related"].items()}, dict.fromkeys(sos["related"], 1))

    def test_related_sections_are_cached_for_a_day(self, _today):
        self.album(SOS)
        fake = self.apple()

        sos = apple_music.music(SOS, related=True)

        self.assertEqual(fake.requests, [])
        self.assertEqual(sorted(sos["related"]), ["more_by_artist", "other_versions", "related_albums"])

    def test_a_failed_lookup_leaves_out_only_its_part_and_is_tried_again(self, _today):
        deluxe, _ = self.album(
            WHOS_NEXT_DELUXE,
            (404, fixture("error_404.json")["body"]),
            (404, fixture("error_404.json")["body"]),
        )

        # The family still shows, and Apple's similar albums need no request.
        self.assertEqual(ids(deluxe["related"]["other_versions"]), [WHOS_NEXT_2022, "1440815866"])
        self.assertNotIn("more_by_artist", deluxe["related"])
        self.assertEqual(len(deluxe["related"]["related_albums"]), 10)
        # A 404 would come back the same, so the page may be cached without those parts.
        self.assertNotIn(services.PARTIAL_PAGE, deluxe)

        fake = self.apple(*related_answers("whos_next_deluxe", "the_who"))
        again = apple_music.music(WHOS_NEXT_DELUXE, related=True)

        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(len(again["related"]["more_by_artist"]), 10)

    def test_while_apple_is_paused_the_page_opens_without_lookups(self, _today):
        self.apple((200, fixture("page_sos.json")))
        apple_music.music(SOS)
        services.redis_db.set(apple_music.backoff_key(), 1, ex=60)
        fake = self.apple()

        sos = apple_music.music(SOS, related=True)

        self.assertEqual(fake.requests, [])
        self.assertEqual(list(sos["related"]), ["related_albums"])
        self.assertTrue(sos[services.PARTIAL_PAGE])

    def test_a_page_missing_a_part_apple_failed_for_a_moment_is_marked(self, _today):
        failures = {
            "a 5xx": (503, {"errors": []}),
            "a 429": (429, {"errors": []}),
            "a timeout": requests.exceptions.ReadTimeout("read timed out"),
        }
        for name, failure in failures.items():
            with self.subTest(name):
                cache.clear()
                clear_pause()
                sos, _ = self.album(SOS, failure)
                self.assertTrue(sos[services.PARTIAL_PAGE])
                self.assertNotIn("other_versions", sos["related"])
        cache.clear()
        clear_pause()
        sos, _ = self.album(SOS)
        self.assertNotIn(services.PARTIAL_PAGE, sos)
        # Pages without related sections are never marked.
        self.assertNotIn(services.PARTIAL_PAGE, apple_music.music(SOS))

    def test_only_album_pages_ask_for_related_sections(self, _today):
        fake = self.apple((200, fixture("page_sos.json")))

        album = services.get_media_metadata("music", SOS, "apple_music")

        self.assertEqual((album["related"], len(fake.requests)), ({}, 1))
        self.apple(*related_answers())
        self.assertEqual(
            sorted(services.get_media_metadata("music", SOS, "apple_music", include_music_related=True)["related"]),
            ["more_by_artist", "other_versions", "related_albums"],
        )


class RelationTests(AppleMusicTestCase):
    def test_badges(self):
        cases = [
            ("SOS", apple_music.SMALLER, "Original album"),
            ("Who's Next (Remastered 2022)", apple_music.SMALLER, "Original album"),
            ("Live at Wembley", None, "Live"),
            ("Thriller (25th Anniversary) [Deluxe Edition]", None, "Anniversary"),
            ("SOS Deluxe: LANA", apple_music.BIGGER, "Deluxe"),
            ("Who's Next (Bonus Track Version)", None, "Deluxe"),
            ("Abbey Road (Remastered)", None, "Remaster"),
            ("For All The Dogs Scary Hours Edition", apple_music.BIGGER, "Deluxe"),
            ("Thriller 40", apple_music.BIGGER, "Deluxe"),
            ("Abbey Road (2019 Mix)", None, "Other version"),
            ("Multitude (Video Album)", apple_music.SAME, "Other version"),
        ]
        for title, songs, badge in cases:
            with self.subTest(title):
                self.assertEqual(apple_music._relation(title, songs), badge)

    def test_songs_compare_by_isrc(self):
        own = frozenset({"A", "B"})
        self.assertEqual(apple_music._compare_songs(own, frozenset({"A", "B", "C"})), apple_music.BIGGER)
        self.assertEqual(apple_music._compare_songs(own, frozenset({"A"})), apple_music.SMALLER)
        self.assertEqual(apple_music._compare_songs(own, frozenset({"B", "A"})), apple_music.SAME)
        self.assertIsNone(apple_music._compare_songs(own, frozenset({"A", "C"})))
        self.assertIsNone(apple_music._compare_songs(own, frozenset()))
