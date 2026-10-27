import copy
import json
from datetime import date
from pathlib import Path

from django.test import SimpleTestCase

from app import music_listing

# Recorded by scripts/music/record_apple_fixtures.py.
FIXTURES = Path(__file__).parent / "mock_data" / "apple_music"
TODAY = date(2026, 10, 26)


def albums(name):
    return json.loads((FIXTURES / name).read_text())["data"]


def ids(listing):
    return [album["id"] for album in listing]


def by_id(resources, album_id):
    return next(album for album in resources if album["id"] == album_id)


class NormalizeTitleTests(SimpleTestCase):
    def test_folds_case_quotes_brackets_and_spaces(self):
        self.assertEqual(
            music_listing.normalize_title("  Weezer [Red Album]   (Deluxe Edition) "),
            "weezer (red album) (deluxe edition)",
        )
        self.assertEqual(
            music_listing.normalize_title("“Awaken, My Love!”"),
            '"awaken, my love!"',
        )
        self.assertEqual(music_listing.normalize_title("Don’t Smile"), "don't smile")
        self.assertEqual(music_listing.normalize_title("ＳＯＳ"), "sos")

    def test_edition_words_stay(self):
        self.assertNotEqual(
            music_listing.normalize_title("SOS"),
            music_listing.normalize_title("SOS Deluxe: LANA"),
        )
        self.assertNotEqual(
            music_listing.normalize_title("Kill Bill"),
            music_listing.normalize_title("Kill Bill - Single"),
        )


class AlbumIdTests(SimpleTestCase):
    def test_reads_the_id_at_the_end_of_the_link(self):
        self.assertEqual(
            music_listing.album_id_from_url(
                "https://music.apple.com/us/album/year-zero/1440826538",
            ),
            "1440826538",
        )
        self.assertEqual(
            music_listing.album_id_from_url(
                "https://music.apple.com/us/album/sos/1657869377/?i=1657869393",
            ),
            "1657869377",
        )
        for url in ("", None, "https://music.apple.com/us/album/sos", "not a link"):
            with self.subTest(url=url):
                self.assertIsNone(music_listing.album_id_from_url(url))

    def test_year_zero_alias_folds_into_the_linked_album(self):
        alias, real = albums("albums_year_zero.json")

        self.assertEqual(alias["id"], "1147788150")
        self.assertEqual(music_listing.url_album_id(alias), "1440826538")
        for order in ([alias, real], [real, alias]):
            with self.subTest(first=order[0]["id"]):
                listings = music_listing.group_listings(order)
                self.assertEqual(len(listings), 1)
                self.assertEqual(
                    music_listing.pick_identity(listings[0], today=TODAY),
                    "1440826538",
                )
        self.assertEqual(
            music_listing.pick_identity([alias], today=TODAY),
            "1440826538",
        )


class GroupListingsTests(SimpleTestCase):
    def test_sos_and_lana_collapse_their_twins_and_reupload(self):
        listings = music_listing.group_listings(albums("albums_sos_lana.json"))

        self.assertEqual(
            [ids(listing) for listing in listings],
            [
                ["1657869377", "1658650093"],
                ["1794644675", "1786643044", "1796270264"],
            ],
        )

    def test_my_turn_keeps_standard_and_deluxe_apart(self):
        listings = music_listing.group_listings(albums("family_my_turn.json"))

        self.assertEqual(
            [ids(listing) for listing in listings],
            [
                ["1498988850", "1499567361"],
                ["1534816297", "1510840178", "1534811491", "1509850659"],
            ],
        )

    def test_midnights_editions_are_separate_listings(self):
        listings = music_listing.group_listings(albums("family_midnights.json"))

        self.assertEqual(
            [ids(listing) for listing in listings],
            [
                ["1649434004", "1645937456", "1649434996", "1645937249"],
                ["1689131527", "1689089710"],
                ["1651516410"],
                ["1650841512", "1650859878"],
            ],
        )

    def test_donda_and_its_clean_only_deluxe_are_two_listings(self):
        listings = music_listing.group_listings(albums("family_donda.json"))

        self.assertEqual([ids(listing) for listing in listings], [["1587795158"], ["1595496182"]])

    def test_weezers_self_titled_albums_stay_apart(self):
        weezer = albums("family_weezer.json")

        listings = music_listing.group_listings(weezer)

        # Only the two uploads of the Red Album share a title and a family.
        self.assertEqual(len(weezer), 11)
        self.assertEqual(
            [ids(listing) for listing in listings if len(listing) > 1],
            [["1443225062", "1443275314"]],
        )
        self.assertEqual(len(listings), 10)

    def test_same_title_needs_the_family_link(self):
        red = [
            copy.deepcopy(by_id(albums("family_weezer.json"), album_id))
            for album_id in ("1443225062", "1443275314")
        ]
        for album in red:
            album.pop("views")

        self.assertEqual(len(music_listing.group_listings(red)), 2)

    def test_family_link_needs_the_same_artist(self):
        explicit, clean = copy.deepcopy(albums("albums_sos_lana.json")[:2])
        clean["attributes"]["artistName"] = "Someone Else"

        self.assertEqual(len(music_listing.group_listings([explicit, clean])), 2)

    def test_a_repeated_album_is_listed_once(self):
        sos = albums("albums_sos_lana.json")[0]

        self.assertEqual([ids(listing) for listing in music_listing.group_listings([sos, sos])], [["1657869377"]])


class PickIdentityTests(SimpleTestCase):
    def identity(self, name, *, member_of=None, item_ids=(), today=TODAY):
        listings = music_listing.group_listings(albums(name))
        listing = next(listing for listing in listings if member_of in ids(listing)) if member_of else listings[0]
        return music_listing.pick_identity(listing, item_ids, today=today)

    def test_explicit_wins_over_clean(self):
        self.assertEqual(self.identity("albums_sos_lana.json"), "1657869377")
        self.assertEqual(self.identity("family_my_turn.json"), "1498988850")

    def test_explicit_then_most_tracks(self):
        self.assertEqual(self.identity("albums_sos_lana.json", member_of="1786643044"), "1794644675")
        self.assertEqual(self.identity("family_my_turn.json", member_of="1509850659"), "1534816297")
        self.assertEqual(self.identity("family_midnights.json"), "1649434004")

    def test_a_clean_only_listing_keeps_its_clean_album(self):
        self.assertEqual(self.identity("family_donda.json", member_of="1595496182"), "1595496182")

    def test_most_tracks_among_unrated_uploads(self):
        self.assertEqual(self.identity("family_weezer.json", member_of="1443275314"), "1443225062")

    def test_a_member_that_can_be_streamed_wins(self):
        lana = copy.deepcopy(music_listing.group_listings(albums("albums_sos_lana.json"))[1])
        by_id(lana, "1794644675")["attributes"].pop("playParams")

        self.assertEqual(music_listing.pick_identity(lana, today=TODAY), "1786643044")

    def test_a_member_with_an_item_keeps_the_identity(self):
        sos = copy.deepcopy(music_listing.group_listings(albums("albums_sos_lana.json"))[0])
        # The clean album has a Spine Item, then Apple stops streaming it.
        by_id(sos, "1658650093")["attributes"].pop("playParams")

        self.assertEqual(music_listing.pick_identity(sos, {"1658650093"}, today=TODAY), "1658650093")
        self.assertEqual(music_listing.pick_identity(sos, ["1657869377"], today=TODAY), "1657869377")
        self.assertEqual(music_listing.pick_content_member(sos, today=TODAY)["id"], "1657869377")

    def test_an_item_on_an_alias_id_keeps_its_own_id(self):
        # The Item sits on 1147788150; Apple later points that ID's link at 1440826538.
        alias, real = albums("albums_year_zero.json")

        for listing in ([real, alias], [alias]):
            with self.subTest(members=ids(listing)):
                self.assertEqual(
                    music_listing.pick_identity(listing, {"1147788150"}, today=TODAY),
                    "1147788150",
                )
        self.assertEqual(
            music_listing.pick_identity([alias], {"1440826538"}, today=TODAY),
            "1440826538",
        )
        # With Items on both IDs, the linked ID wins.
        self.assertEqual(
            music_listing.pick_identity([real, alias], {"1147788150", "1440826538"}, today=TODAY),
            "1440826538",
        )

    def test_a_pre_order_counts_as_complete_until_its_date_passes(self):
        pre_order = by_id(albums("family_creed_weathered.json"), "6803287133")
        stale = copy.deepcopy(pre_order)
        stale["id"] = "6803287134"
        stale["attributes"].update(
            url="https://music.apple.com/us/album/weathered/6803287134",
            releaseDate="2026-01-09",
            trackCount=16,
        )
        listing = [stale, pre_order]

        self.assertEqual(music_listing.pick_identity(listing, today=TODAY), "6803287133")
        # Once 2026-11-20 has passed, both are incomplete, so the bigger one wins.
        self.assertEqual(
            music_listing.pick_identity(listing, today=date(2026, 11, 21)),
            "6803287134",
        )

    def test_the_lowest_id_breaks_a_tie(self):
        first = by_id(albums("albums_sos_lana.json"), "1657869377")
        twin = copy.deepcopy(first)
        twin["id"] = "1657869376"
        twin["attributes"]["url"] = "https://music.apple.com/us/album/sos/1657869376"

        self.assertEqual(music_listing.pick_identity([first, twin], today=TODAY), "1657869376")


class ContentMemberTests(SimpleTestCase):
    def test_content_comes_from_the_best_streamable_member(self):
        lana = music_listing.group_listings(albums("albums_sos_lana.json"))[1]

        self.assertEqual(music_listing.pick_content_member(lana, today=TODAY)["id"], "1794644675")

    def test_a_member_that_cant_be_streamed_gives_way(self):
        lana = copy.deepcopy(music_listing.group_listings(albums("albums_sos_lana.json"))[1])
        by_id(lana, "1794644675")["attributes"].pop("playParams")

        self.assertEqual(music_listing.pick_content_member(lana, today=TODAY)["id"], "1786643044")


class HiddenTests(SimpleTestCase):
    def test_a_pre_order_shows(self):
        weathered = albums("family_creed_weathered.json")
        listings = music_listing.group_listings(weathered)
        pre_order = next(listing for listing in listings if ids(listing) == ["6803287133"])

        # Incomplete and due 2026-11-20; it can be streamed, so it shows after that too.
        self.assertEqual(len(listings), 2)
        self.assertFalse(music_listing.is_hidden(pre_order))
        self.assertFalse(music_listing.is_hidden([by_id(weathered, "1440738578")]))

    def test_a_listing_no_one_can_stream_is_hidden(self):
        sos = copy.deepcopy(music_listing.group_listings(albums("albums_sos_lana.json"))[0])
        by_id(sos, "1658650093")["attributes"].pop("playParams")

        self.assertFalse(music_listing.is_hidden(sos))
        by_id(sos, "1657869377")["attributes"].pop("playParams")
        self.assertTrue(music_listing.is_hidden(sos))

    def test_lemonade_shows_though_both_its_albums_are_incomplete(self):
        found = albums("search_families_lemonade.json")
        lemonade = [by_id(found, album_id) for album_id in ("1460430561", "1460432013")]
        listings = music_listing.group_listings(lemonade)

        # Apple marks the explicit album and its clean twin incomplete, released 2016.
        self.assertEqual([ids(listing) for listing in listings], [["1460430561", "1460432013"]])
        self.assertEqual(
            [(album["attributes"]["isComplete"], album["attributes"]["releaseDate"]) for album in lemonade],
            [(False, "2016-04-23"), (False, "2016-04-23")],
        )
        self.assertFalse(music_listing.is_hidden(listings[0]))
        self.assertEqual(music_listing.pick_identity(listings[0], today=TODAY), "1460430561")

    def test_completeness_only_ranks_members(self):
        sos = copy.deepcopy(music_listing.group_listings(albums("albums_sos_lana.json"))[0])
        by_id(sos, "1657869377")["attributes"]["isComplete"] = False

        # The complete clean twin ranks first; with both incomplete, explicit does again.
        self.assertEqual(music_listing.pick_identity(sos, today=TODAY), "1658650093")
        self.assertFalse(music_listing.is_hidden(sos))
        by_id(sos, "1658650093")["attributes"]["isComplete"] = False
        self.assertEqual(music_listing.pick_identity(sos, today=TODAY), "1657869377")
        self.assertFalse(music_listing.is_hidden(sos))

    def test_partial_dates_count_as_their_last_day(self):
        sos = copy.deepcopy(music_listing.group_listings(albums("albums_sos_lana.json"))[0])
        explicit = by_id(sos, "1657869377")
        explicit["attributes"]["isComplete"] = False
        for release_date, last_day in (("2010", date(2010, 12, 31)), ("1994-12", date(1994, 12, 31))):
            explicit["attributes"]["releaseDate"] = release_date
            with self.subTest(release_date=release_date):
                # Not released yet until its last day, so it ranks as complete until then.
                self.assertEqual(music_listing.pick_identity(sos, today=last_day), "1657869377")
                self.assertEqual(
                    music_listing.pick_identity(sos, today=date(last_day.year + 1, 1, 1)),
                    "1658650093",
                )

    def test_release_day(self):
        self.assertEqual(music_listing.release_day("2024-02"), date(2024, 2, 29))
        self.assertEqual(music_listing.release_day("2022-12-09"), date(2022, 12, 9))
        for value in ("", None, "2022-13", "2022-02-30", "Dec 2022"):
            with self.subTest(value=value):
                self.assertIsNone(music_listing.release_day(value))
