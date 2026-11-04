"""The stored copy of a title's numbers and rows (AN-100, AN-101), and the job (AN-202)."""

import copy
from datetime import UTC, datetime, timedelta
from unittest import mock

import requests
from django.test import SimpleTestCase
from django.urls import reverse

from app import anime_listing, anime_tracking
from app.anime_listing import AnimeDetailsUnavailable
from app.models import AnimeListing, AnimeMark, Item, MediaLike
from app.providers import anilist, mal, services
from app.tests.anime_fixtures import OCT_25, AnimeTestCase, FakeAnimeProvider
from app.tests.tv_fixtures import ProviderDown
from events.calendar import anime as calendar_anime
from events.calendar.main import process_items
from events.calendar.selectors import filter_items_to_fetch
from events.models import Event

REAL_ANIME_EPISODES = anilist.anime_episodes
ATTACK_ON_TITAN = {
    "data": {
        "Page": {
            "pageInfo": {"hasNextPage": False},
            "media": [
                {
                    "idMal": 16498,
                    "nextAiringEpisode": None,
                    "airingSchedule": {"nodes": []},
                    "streamingEpisodes": [
                        {
                            "title": "Episode 2 - That Day - The Fall of Zhiganshina (2)",
                            "thumbnail": "https://img1.ak.crunchyroll.com/2.jpg",
                        },
                        {
                            "title": "Episode 1 - To You, 2,000 Years in the Future",
                            "thumbnail": "https://img1.ak.crunchyroll.com/1.jpg",
                        },
                        {"title": "Special Recap", "thumbnail": "https://img1.ak.crunchyroll.com/x.jpg"},
                    ],
                },
            ],
        },
    },
}
ONE_PIECE = {
    "data": {
        "Page": {
            "pageInfo": {"hasNextPage": False},
            "media": [
                {
                    "idMal": 21,
                    "nextAiringEpisode": {"episode": 1181, "airingAt": 1798985760},
                    "airingSchedule": {
                        "nodes": [
                            {"episode": 1181, "airingAt": 1798985760},
                            {"episode": 1182, "airingAt": 1799590560},
                        ],
                    },
                    "streamingEpisodes": [
                        {"title": "Episode 130 - Scent of Danger!", "thumbnail": "https://example.com/130.jpg"},
                        {"title": "Episode 129 - It All Started", "thumbnail": "https://example.com/129.jpg"},
                        {"title": "Episode 129 - It All Started", "thumbnail": "https://example.com/other-site.jpg"},
                    ],
                },
            ],
        },
    },
}


class AniListParsingTests(SimpleTestCase):
    """The shapes AniList really answers with, recorded 2026-11-03 for MAL 16498 and 21."""

    def test_an_101_the_episode_number_is_parsed_out_of_the_title(self):
        for value, expected in (
            ("Episode 130 - Scent of Danger!", (130, "Scent of Danger!")),
            ("Episode 5", (5, "")),
            ("Ep. 12: Finale", (12, "Finale")),
            ("episode 7 – Title", (7, "Title")),
            ("Recap", (None, "")),
            (None, (None, "")),
        ):
            self.assertEqual(anilist.parse_episode_title(value), expected, value)

    def test_an_101_order_isnt_guaranteed_and_a_second_site_doesnt_replace_the_first(self):
        with mock.patch.object(services, "api_request", return_value=copy.deepcopy(ONE_PIECE)):
            result = anilist.anime_episodes(["21"])["21"]
        self.assertEqual(result["next"], {"episode": 1181, "airing_at": 1798985760})
        self.assertEqual(result["schedule"], {1181: 1798985760, 1182: 1799590560})
        self.assertEqual(sorted(result["episodes"]), [129, 130])
        self.assertEqual(result["episodes"][129]["image"], "https://example.com/129.jpg")

    def test_an_101_a_finished_title_has_no_schedule(self):
        with mock.patch.object(services, "api_request", return_value=copy.deepcopy(ATTACK_ON_TITAN)):
            result = anilist.anime_episodes(["16498"])["16498"]
        self.assertIsNone(result["next"])
        self.assertEqual(result["schedule"], {})
        self.assertEqual(result["episodes"][1]["title"], "To You, 2,000 Years in the Future")

    def test_an_101_one_request_per_25_titles(self):
        empty = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        with mock.patch.object(services, "api_request", return_value=empty) as request:
            self.assertEqual(anilist.anime_episodes([str(number) for number in range(1, 51)]), {})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(request.call_args.kwargs["params"]["variables"]["malIds"]), 25)

    def test_an_101_a_full_page_stops_once_every_title_has_answered(self):
        # A page of 25 reports hasNextPage even with nothing left.
        media = [
            {"idMal": number, "nextAiringEpisode": None, "airingSchedule": {"nodes": []}, "streamingEpisodes": []}
            for number in range(1, 26)
        ]
        full = {"data": {"Page": {"pageInfo": {"hasNextPage": True}, "media": media}}}
        empty = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        with mock.patch.object(services, "api_request", side_effect=[full, empty]) as request:
            result = anilist.anime_episodes([str(number) for number in range(1, 26)])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(result), 25)

    def break_media(self, mal_id, **values):
        return {
            "id": mal_id + 1000,
            "idMal": mal_id,
            "status": "RELEASING",
            "nextAiringEpisode": None,
            "airingSchedule": {"nodes": []},
            "streamingEpisodes": [],
            **values,
        }

    def page(self, *media):
        return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": list(media)}}}

    def test_an_100_a_title_on_a_break_asks_for_its_last_aired_entry(self):
        last = {"data": {"a1021": {"airingSchedules": [{"episode": 1180, "airingAt": 1790000000}]}}}
        with mock.patch.object(services, "api_request", side_effect=[self.page(self.break_media(21)), last]) as request:
            result = anilist.anime_episodes(["21"], timeout=3, retry_rate_limits=False)["21"]
        self.assertEqual(result["last_aired"], {"episode": 1180, "airing_at": 1790000000})
        self.assertEqual((result["next"], result["schedule"]), (None, {}))
        self.assertEqual(request.call_count, 2)
        query = request.call_args.kwargs["params"]["query"]
        for part in ("mediaId: 1021", "notYetAired: false", "EPISODE_DESC"):
            self.assertIn(part, query)
        self.assertEqual(request.call_args.kwargs["timeout"], 3)
        self.assertFalse(request.call_args.kwargs["retry_rate_limits"])

    def test_an_100_a_hiatus_is_a_break_too(self):
        last = {"data": {"a1021": {"airingSchedules": []}}}
        with mock.patch.object(services, "api_request", side_effect=[self.page(self.break_media(21, status="HIATUS")), last]):
            self.assertIsNone(anilist.anime_episodes(["21"])["21"]["last_aired"])

    def test_an_100_one_more_request_for_every_title_on_a_break_in_a_chunk(self):
        last = {
            "data": {
                "a1001": {"airingSchedules": [{"episode": 5, "airingAt": 1790000000}]},
                "a1002": {"airingSchedules": []},
            },
        }
        page = self.page(self.break_media(1), self.break_media(2))
        with mock.patch.object(services, "api_request", side_effect=[page, last]) as request:
            result = anilist.anime_episodes(["1", "2"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["1"]["last_aired"]["episode"], 5)
        self.assertIsNone(result["2"]["last_aired"])

    def test_an_100_a_title_with_a_next_episode_or_not_under_way_costs_no_second_request(self):
        for media in (
            self.break_media(1, nextAiringEpisode={"episode": 3, "airingAt": 1790000000}),
            self.break_media(1, airingSchedule={"nodes": [{"episode": 3, "airingAt": 1790000000}]}),
            self.break_media(1, status="FINISHED"),
            self.break_media(1, status="NOT_YET_RELEASED"),
        ):
            with self.subTest(media=media), mock.patch.object(services, "api_request", return_value=self.page(media)) as request:
                self.assertNotIn("last_aired", anilist.anime_episodes(["1"])["1"])
                self.assertEqual(request.call_count, 1)

    def test_an_100_a_failed_last_aired_read_costs_only_the_titles_it_was_for(self):
        for failure in (requests.exceptions.Timeout, ProviderDown, {"errors": []}):
            read_fine = self.break_media(22, status="FINISHED")
            answers = [self.page(self.break_media(21), read_fine), failure]
            with self.subTest(failure=failure), mock.patch.object(services, "api_request", side_effect=answers):
                result = anilist.anime_episodes(["21", "22"])
            self.assertIsNone(result["21"])
            self.assertEqual(result["22"]["schedule"], {})

    def test_an_100_a_failed_last_aired_read_keeps_every_chunk_that_answered(self):
        # 30 titles make calls for 25, for 5, and for the last aired entry of one of those 5.
        first = self.page(*[self.break_media(number, status="FINISHED") for number in range(1, 26)])
        second = self.page(*[self.break_media(number, status="FINISHED") for number in range(26, 30)], self.break_media(30))
        ids = [str(number) for number in range(1, 31)]
        with mock.patch.object(services, "api_request", side_effect=[first, second, requests.exceptions.Timeout]) as request:
            result = anilist.anime_episodes(ids)
        self.assertEqual(request.call_count, 3)
        self.assertIsNone(result["30"])
        self.assertEqual([mal_id for mal_id, answer in result.items() if answer is None], ["30"])

    def test_an_100_a_chunk_that_fails_ends_the_asking_and_keeps_the_ones_before_it(self):
        first = self.page(*[self.break_media(number, status="FINISHED") for number in range(1, 26)])
        ids = [str(number) for number in range(1, 61)]
        with mock.patch.object(services, "api_request", side_effect=[first, requests.exceptions.Timeout]) as request:
            result = anilist.anime_episodes(ids)
        self.assertEqual(request.call_count, 2, "AniList is down or hanging, so the third chunk isn't asked")
        self.assertEqual(len(result), 60)
        self.assertTrue(all(result[str(number)] is not None for number in range(1, 26)))
        self.assertTrue(all(result[str(number)] is None for number in range(26, 61)))

    def test_an_100_a_title_on_a_break_loses_its_answer_when_a_later_page_fails(self):
        first = {
            "data": {"Page": {"pageInfo": {"hasNextPage": True}, "media": [self.break_media(1)]}},
        }
        with mock.patch.object(services, "api_request", side_effect=[first, requests.exceptions.Timeout]):
            result = anilist.anime_episodes(["1", "2"])
        self.assertEqual(result, {"1": None, "2": None})

    def test_an_101_a_title_still_to_answer_is_looked_for_on_the_next_page(self):
        first = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": True},
                    "media": [
                        {"idMal": 1, "nextAiringEpisode": None, "airingSchedule": {"nodes": []}, "streamingEpisodes": []},
                    ],
                },
            },
        }
        second = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        with mock.patch.object(services, "api_request", side_effect=[first, second]) as request:
            result = anilist.anime_episodes(["1", "2"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(list(result), ["1"])


def media_with_a_prequel(own, prequel_list, *, started=(2020, 4, 11), prequel_started=(2019, 1, 12), relation="PREQUEL"):
    """Return one title as AniList answers: its list, its start, and one related title's."""

    def date(parts):
        return dict(zip(("year", "month", "day"), parts, strict=True))

    return {
        "idMal": 40591,
        "nextAiringEpisode": None,
        "airingSchedule": {"nodes": []},
        "startDate": date(started),
        "streamingEpisodes": [{"title": title, "thumbnail": "https://example.com/own.jpg"} for title in own],
        "relations": {
            "edges": [
                {
                    "relationType": relation,
                    "node": {"startDate": date(prequel_started), "streamingEpisodes": [{"title": title} for title in prequel_list]},
                },
            ],
        },
    }


class AnotherSeasonsListTests(SimpleTestCase):
    """AN-101: a list that's the title before's isn't this title's (recorded 2026-11-04)."""

    KAGUYA = ("Episode 1 - I Will Make You Invite Me to a Movie", "Episode 2 - Kaguya Wants to Be Stopped")

    def episodes(self, media):
        answer = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": [media]}}}
        with mock.patch.object(services, "api_request", return_value=answer):
            return anilist.anime_episodes([str(media["idMal"])])[str(media["idMal"])]["episodes"]

    def test_an_101_a_later_season_with_its_prequels_list_has_no_titles_or_images(self):
        # Kaguya-sama S2 and S3, Overlord II and Mob Psycho II all have the first season's.
        self.assertEqual(self.episodes(media_with_a_prequel(self.KAGUYA, self.KAGUYA)), {})

    def test_an_101_a_later_season_with_its_own_list_keeps_it(self):
        # Monogatari Second Season's 26 share nothing with Nisemonogatari's 13.
        own = ("Episode 1 - Tsubasa Tiger Part One", "Episode 2 - Tsubasa Tiger Part Two")
        episodes = self.episodes(media_with_a_prequel(own, ("Episode 1 - Karen Bee Part One",)))
        self.assertEqual(episodes[1]["title"], "Tsubasa Tiger Part One")
        self.assertEqual(episodes[1]["image"], "https://example.com/own.jpg")

    def test_an_101_a_first_seasons_list_stays_though_a_later_ova_is_filed_as_its_prequel(self):
        # Attack on Titan's OVA, out months after Season 1, has Season 1's 25 and is its PREQUEL.
        titan = ("Episode 1 - To You, 2,000 Years in the Future",)
        media = media_with_a_prequel(titan, titan, started=(2013, 4, 7), prequel_started=(2013, 12, 9))
        self.assertEqual(self.episodes(media)[1]["title"], "To You, 2,000 Years in the Future")

    def test_an_101_a_special_out_before_it_counts_as_much_as_a_season(self):
        # Tonikawa Season 2's PREQUEL is the special between the seasons, with Season 1's list.
        own = ("Episode 1 - Marriage",)
        self.assertEqual(self.episodes(media_with_a_prequel(own, own, started=(2023, 4, 8), prequel_started=(2022, 8, 3))), {})

    def test_an_101_one_list_holding_the_other_is_the_same_list(self):
        self.assertEqual(self.episodes(media_with_a_prequel(self.KAGUYA, self.KAGUYA[:1])), {})
        self.assertEqual(self.episodes(media_with_a_prequel(self.KAGUYA[:1], self.KAGUYA)), {})

    def test_an_101_one_title_in_common_isnt_the_same_list(self):
        own = ("Episode 1 - Reunion", "Episode 2 - Theirs")
        theirs = ("Episode 1 - Reunion", "Episode 2 - Mine")
        self.assertEqual(self.episodes(media_with_a_prequel(own, theirs))[2]["title"], "Theirs")

    def test_an_101_only_a_prequel_is_compared(self):
        media = media_with_a_prequel(self.KAGUYA, self.KAGUYA, relation="SEQUEL")
        self.assertEqual(self.episodes(media)[1]["title"], "I Will Make You Invite Me to a Movie")

    def test_an_101_a_prequel_with_no_list_or_no_date_changes_nothing(self):
        self.assertEqual(self.episodes(media_with_a_prequel(self.KAGUYA, ()))[1]["title"], "I Will Make You Invite Me to a Movie")
        media = media_with_a_prequel(self.KAGUYA, self.KAGUYA, prequel_started=(None, None, None))
        self.assertEqual(self.episodes(media)[1]["title"], "I Will Make You Invite Me to a Movie")

    def test_an_101_a_season_not_out_yet_with_its_prequels_list_has_no_titles_either(self):
        media = media_with_a_prequel(self.KAGUYA, self.KAGUYA, started=(None, None, None))
        self.assertEqual(self.episodes(media), {})

    def test_an_101_the_schedule_stays_when_the_list_goes(self):
        media = media_with_a_prequel(self.KAGUYA, self.KAGUYA)
        media["nextAiringEpisode"] = {"episode": 3, "airingAt": 1798985760}
        media["airingSchedule"] = {"nodes": [{"episode": 3, "airingAt": 1798985760}]}
        answer = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": [media]}}}
        with mock.patch.object(services, "api_request", return_value=answer):
            result = anilist.anime_episodes(["40591"])["40591"]
        self.assertEqual(result["episodes"], {})
        self.assertEqual(result["next"], {"episode": 3, "airing_at": 1798985760})
        self.assertEqual(result["schedule"], {3: 1798985760})

    def test_an_101_the_request_asks_for_what_the_comparison_needs(self):
        empty = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        with mock.patch.object(services, "api_request", return_value=empty) as request:
            anilist.anime_episodes(["1"])
        query = request.call_args.kwargs["params"]["query"]
        for part in ("relations", "relationType", "startDate", "streamingEpisodes"):
            self.assertIn(part, query)


class ListingTests(AnimeTestCase):
    """AN-100, AN-101: the copy, a first open and backing off."""

    def test_an_101_a_first_open_tries_once_with_a_short_timeout_then_reads_the_copy(self):
        item = self.title(total=2)
        with mock.patch.object(anilist, "anime_episodes", wraps=anilist.anime_episodes) as episodes:
            anime_listing.get_listing(item)
        self.assertEqual(episodes.call_args.kwargs["timeout"], anime_listing.FIRST_OPEN_TIMEOUT)
        self.assertFalse(episodes.call_args.kwargs["retry_rate_limits"])
        self.fake.calls.clear()
        anime_listing.get_listing(item)
        self.state(item)
        self.rows(item)
        self.assertEqual(self.fake.calls, [])

    def test_an_101_a_list_running_past_the_total_isnt_this_titles(self):
        # AniList lists Attack on Titan's 25 episodes under Season 2's 12.
        item = self.title(
            total=2,
            episodes={1: ("Another season's first", "https://example.com/1.jpg"), 3: ("Another season's third", "")},
        )
        anime_listing.get_listing(item)
        self.assertEqual([row.get("t") for row in self.rows(item)["episodes"]], [None, None], "They read Episode 1")

    def test_an_101_a_failure_stores_the_numbers_and_backs_off(self):
        self.fake.fail("anilist")
        item = self.title(total=2)
        listing = anime_listing.get_listing(item)
        self.assertEqual(listing.total, 2)
        stored = AnimeListing.objects.get(item=item)
        self.assertIsNone(stored.rows_fetched_at)
        self.assertEqual(stored.retry_after, self.clock.moment + anime_listing.FAILURE_BACKOFF)
        self.fake.calls.clear()
        self.fake.recover()
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, [])
        self.clock.advance(minutes=16)
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, ["anilist"])
        self.assertIsNotNone(AnimeListing.objects.get(item=item).rows_fetched_at)

    def test_an_101_a_page_answers_when_every_provider_is_down(self):
        item = self.title()
        self.fake.fail()
        self.assertEqual(self.rows(item)["episodes"], [{"n": 1, "a": 1}])
        state = self.state(item)
        self.assertIsNone(state["aired_count"])

    def test_an_100_with_no_copy_and_mal_down_get_listing_says_unavailable(self):
        item = self.title()
        self.fake.fail("mal")
        with self.assertRaises(AnimeDetailsUnavailable):
            anime_listing.get_listing(item)

    def test_an_100_the_copy_keeps_total_next_episode_and_rows(self):
        moment = self.clock.moment
        item = self.title(
            total=12,
            status="Airing",
            format="TV",
            start_date="2026-10-01",
            next_episode=4,
            next_air_at=moment + timedelta(days=1),
            schedule={4: moment + timedelta(days=1), 5: moment + timedelta(days=8)},
            episodes={1: ("One", "https://example.com/1.jpg")},
        )
        anime_listing.get_listing(item)
        stored = AnimeListing.objects.get(item=item)
        self.assertEqual((stored.total, stored.provider_status, stored.next_episode), (12, "Airing", 4))
        self.assertEqual(stored.rows[0], [1, "One", "https://example.com/1.jpg", None])
        self.assertEqual(stored.rows[1][0], 4)
        self.assertEqual(stored.premiere_date.isoformat(), "2026-10-01")

    def test_an_100_a_partial_start_date_isnt_a_premiere(self):
        item = self.title(status="Upcoming", start_date="2027-03")
        self.assertEqual(self.state(item)["eye"]["message"], "Nothing has aired yet.")

    def test_a_manual_title_has_no_numbers_and_is_never_unavailable(self):
        item = Item.objects.create(source="manual", media_type="anime", media_id="manual-1", title="Mine", image="https://example.com/p.jpg")
        self.fake.fail()
        self.tick(item, 3)
        self.assertEqual(self.row(item).progress, 1)
        self.assertFalse(AnimeListing.objects.filter(item=item).exists())

    def test_an_100_page_metadata_updates_the_numbers_with_no_call(self):
        item = self.title(total=2)
        anime_listing.get_listing(item)
        self.fake.calls.clear()
        anime_listing.update_from_metadata(item, {"max_progress": 5, "details": {"status": "Airing"}})
        stored = AnimeListing.objects.get(item=item)
        self.assertEqual((stored.total, stored.provider_status), (5, "Airing"))
        self.assertEqual(self.fake.calls, [])

    def test_an_100_a_usual_page_view_writes_nothing(self):
        item = self.title(total=2)
        anime_listing.get_listing(item)
        stored = AnimeListing.objects.get(item=item)
        metadata = {"max_progress": 2, "details": {"status": "Finished"}}
        with self.assertNumQueries(1):
            anime_listing.update_from_metadata(item, metadata)
        self.assertEqual(AnimeListing.objects.get(item=item).fetched_at, stored.fetched_at)


class StaleCopyTests(AnimeTestCase):
    """AN-100: a page open refreshes a stale copy, under the same timeout and back-off."""

    def airing_title(self, total=24, next_episode=8):
        return self.fake.add(
            total=total,
            status="Airing",
            next_episode=next_episode,
            next_air_at=self.clock.moment + timedelta(days=3),
        )

    def test_an_100_ninety_days_on_an_untracked_title_counts_what_has_aired(self):
        title = self.airing_title()
        item = self.title(title=title)
        self.assertEqual(self.state(item)["aired_count"], 7)
        self.clock.advance(days=90)
        title.next_episode = 21
        title.next_air_at = self.clock.moment + timedelta(days=3)
        self.assertEqual(self.state(item)["aired_count"], 20)
        self.tick(item, 15)
        self.assertEqual(self.watched(item), {15})

    def test_an_100_a_copy_read_today_makes_no_call(self):
        item = self.title(title=self.airing_title())
        anime_listing.get_listing(item)
        self.fake.calls.clear()
        self.clock.advance(hours=23)
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, [])

    def test_an_100_an_airing_copy_is_stale_after_a_day(self):
        item = self.title(title=self.airing_title())
        anime_listing.get_listing(item)
        self.fake.calls.clear()
        self.clock.advance(hours=25)
        anime_listing.get_listing(item)
        self.assertEqual(sorted(self.fake.calls), ["anilist", "mal"])

    def test_an_100_an_airing_time_passing_makes_it_stale_at_once(self):
        title = self.fake.add(total=24, status="Airing", next_episode=8, next_air_at=self.clock.moment + timedelta(hours=1))
        item = self.title(title=title)
        anime_listing.get_listing(item)
        self.fake.calls.clear()
        self.clock.advance(hours=2)
        title.next_episode = 9
        title.next_air_at = self.clock.moment + timedelta(days=7)
        self.assertEqual(anime_listing.get_listing(item).next_episode, 9)
        self.assertIn("anilist", self.fake.calls)

    def test_an_100_a_finished_copy_waits_a_week(self):
        item = self.title(total=2)
        anime_listing.get_listing(item)
        self.fake.calls.clear()
        self.clock.advance(days=6)
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, [])
        self.clock.advance(days=2)
        anime_listing.get_listing(item)
        self.assertEqual(sorted(self.fake.calls), ["anilist", "mal"])

    def test_an_100_a_stale_read_keeps_to_the_short_timeout(self):
        item = self.title(title=self.airing_title())
        anime_listing.get_listing(item)
        self.clock.advance(days=2)
        with (
            mock.patch.object(anilist, "anime_episodes", wraps=anilist.anime_episodes) as episodes,
            mock.patch.object(anime_listing.mal, "anime", wraps=anime_listing.mal.anime) as numbers,
        ):
            anime_listing.get_listing(item)
        for call in (episodes, numbers):
            self.assertEqual(call.call_args.kwargs["timeout"], anime_listing.FIRST_OPEN_TIMEOUT)
            self.assertFalse(call.call_args.kwargs["retry_rate_limits"])

    def test_an_100_a_stale_read_that_fails_keeps_the_copy_and_backs_off(self):
        title = self.airing_title()
        item = self.title(title=title)
        anime_listing.get_listing(item)
        self.clock.advance(days=2)
        self.fake.fail()
        self.assertEqual(anime_listing.get_listing(item).total, 24)
        self.fake.calls.clear()
        self.fake.recover()
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, [])
        self.clock.advance(minutes=16)
        anime_listing.get_listing(item)
        self.assertEqual(sorted(self.fake.calls), ["anilist", "mal"])

    def test_an_100_a_read_of_only_one_provider_backs_off_only_when_it_fails(self):
        title = self.airing_title()
        item = self.title(title=title)
        anime_listing.get_listing(item)
        self.clock.advance(hours=25)
        anime_listing.get_listing(item)
        self.assertIsNone(AnimeListing.objects.get(item=item).retry_after)


class AiringUnknownTests(AnimeTestCase):
    """AN-100: while MAL says airing and AniList hasn't answered, nobody knows how many aired."""

    def setUp(self):
        super().setUp()
        self.airing = self.fake.add(
            total=12,
            status="Airing",
            next_episode=8,
            next_air_at=self.clock.moment + timedelta(days=3),
        )
        self.item = self.title(title=self.airing)
        self.fake.fail("anilist")

    def numbers(self):
        return [row["n"] for row in self.rows(self.item)["episodes"]]

    def test_an_100_a_first_open_doesnt_count_every_listed_episode(self):
        state = self.state(self.item)
        self.assertIsNone(state["aired_count"])
        self.assertEqual(state["total"], 12)
        self.assertIsNone(state["next_airing"])
        self.assertEqual(self.numbers(), [1])

    def test_an_100_a_tick_still_works_and_the_rows_run_to_one_past_it(self):
        self.tick(self.item, 3)
        self.assertEqual(self.numbers(), [1, 2, 3, 4])
        state = self.state(self.item)
        self.assertEqual((state["watched_count"], state["aired_count"]), (1, None))
        self.assertEqual(self.row(self.item).aired_count, None)

    def test_an_100_the_rows_stop_at_the_total(self):
        self.tick(self.item, 12)
        self.assertEqual(self.numbers(), list(range(1, 13)))

    def test_an_100_a_tick_past_the_total_is_still_refused(self):
        with self.assertRaises(anime_tracking.TVInvalid):
            self.tick(self.item, 13)

    def test_an_100_whatever_fills_the_rest_is_refused_and_retryable(self):
        for name, write in (
            ("eye", lambda: anime_tracking.mark_watched(self.user, self.item)),
            ("stars", lambda: anime_tracking.set_rating(self.user, self.item, 8)),
            ("heart", lambda: anime_tracking.set_like(self.user, self.item, True)),
            ("log", lambda: anime_tracking.create_log(self.user, self.item, consumed_at=OCT_25)),
            ("up to here", lambda: anime_tracking.mark_up_to(self.user, self.item, 4)),
            (
                "written Completed",
                lambda: anime_tracking.apply_written_state(
                    self.user,
                    self.item,
                    {"status": "Completed"},
                    entry_point=anime_tracking.LEGACY_APP,
                ),
            ),
        ):
            with self.subTest(name=name), self.assertRaises(AnimeDetailsUnavailable):
                write()
            self.assertIsNone(self.row(self.item))

    def test_an_100_the_refusal_is_the_retryable_503(self):
        response = self.action(self.item, "mark_watched", expect=503)
        self.assertEqual(response.data["error"]["message"], anime_listing.UNAVAILABLE_MESSAGE)
        self.assertEqual(response.data["error"]["reason"], "tv_details_unavailable")

    def test_an_100_a_webs_written_completed_says_why(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": self.item.media_id,
                "source": self.item.source,
                "media_type": "anime",
                "status": "Completed",
                "progress": 0,
                "score": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.row(self.item))

    def test_an_100_once_anilist_answers_the_eye_fills_only_what_aired(self):
        self.tick(self.item, 1)
        self.clock.advance(minutes=16)
        self.fake.recover()
        self.assertEqual(self.state(self.item)["aired_count"], 7)
        anime_tracking.mark_watched(self.user, self.item)
        row = self.row(self.item)
        self.assertEqual((row.status, row.progress, row.caught_up), ("In progress", 7, True))

    def test_an_100_announced_then_airing_waits_for_anilist(self):
        title = self.fake.add(total=12, status="Upcoming", start_date="2026-12-01")
        item = self.title(title=title)
        self.fake.recover()
        self.state(item)
        self.clock.advance(days=40)
        title.status = "Airing"
        title.next_episode = 4
        title.next_air_at = self.clock.moment + timedelta(days=3)
        self.fake.fail("anilist")
        with self.assertRaises(AnimeDetailsUnavailable):
            anime_tracking.mark_watched(self.user, item)
        self.assertIsNone(self.state(item)["aired_count"])
        self.clock.advance(minutes=16)
        self.fake.recover()
        anime_tracking.mark_watched(self.user, item)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.caught_up), ("In progress", 3, True))

    def test_an_100_a_status_change_on_the_page_makes_anilists_old_answer_count_for_nothing(self):
        title = self.fake.add(total=12, status="Upcoming", start_date="2026-12-01")
        item = self.title(title=title)
        self.fake.recover()
        anime_listing.get_listing(item)
        anime_listing.update_from_metadata(item, {"max_progress": 12, "details": {"status": "Airing"}})
        self.assertTrue(AnimeListing.objects.get(item=item).rows_fetched_at is None)
        self.assertTrue(anime_listing.stored_listing(item).awaiting_schedule)

    def test_an_100_a_finished_title_counts_every_listed_episode_with_anilist_down(self):
        item = self.title(total=12)
        anime_tracking.mark_watched(self.user, item)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 12))

    def test_an_800_an_import_isnt_refused(self):
        anime_listing.get_listing(self.item)
        anime_tracking.import_title(self.user, self.item, status="Completed", count=7)
        row = self.row(self.item)
        self.assertEqual(row.progress, 7)
        self.assertFalse(AnimeMark.objects.filter(anime=row, origin="eye").exists())

    def test_an_500_an_airing_next_title_with_no_answer_goes_by_its_release_date(self):
        anime_listing.get_listing(self.item)
        stored = {"media_id": self.item.media_id, "release_date": "2026-01-01"}
        copy_of_next = anime_tracking._next_title_copy(stored)  # noqa: SLF001
        self.assertIsNotNone(copy_of_next)
        self.assertTrue(copy_of_next.awaiting_schedule)
        self.assertTrue(anime_tracking._next_title_has_aired(stored, OCT_25, copy_of_next))  # noqa: SLF001

    def test_an_501_a_series_item_says_whether_it_is_a_single(self):
        def single(episodes, media_format):
            metadata = {"media_id": "1", "series_format": media_format, "details": {"episodes": episodes}}
            item = mal._anime_series_item(metadata, 1)  # noqa: SLF001
            return anime_listing.next_title_in([{"media_id": "0", "position": 0}, item], "0")["single"]

        self.assertTrue(single(1, "TV"), "A total of one")
        self.assertTrue(single(None, "Movie"), "A movie with no total")
        self.assertTrue(single(1, "Movie"))
        self.assertFalse(single(3, "Movie"), "A film in three parts has rows")
        self.assertFalse(single(12, "TV"))
        self.assertFalse(single(None, "TV"), "Airing, with no total yet")


class EmptyCopyTests(AnimeTestCase):
    """AN-100's last line: a copy MAL's numbers never reached is no copy."""

    def test_an_100_a_job_with_every_provider_down_leaves_no_copy(self):
        item = self.title(total=12)
        self.fake.fail()
        anime_listing.refresh([item])
        self.assertFalse(AnimeListing.objects.filter(item=item).exists())
        self.assertIsNone(anime_listing.stored_listing(item))

    def test_an_100_a_write_on_an_empty_copy_is_refused_when_mal_is_down(self):
        item = self.title(total=12)
        AnimeListing.objects.create(item=item)
        self.fake.fail()
        for write in (
            lambda: anime_tracking.mark_watched(self.user, item),
            lambda: anime_tracking.tick(self.user, item, 1),
            lambda: anime_tracking.create_log(self.user, item, consumed_at=OCT_25),
        ):
            with self.assertRaises(AnimeDetailsUnavailable):
                write()
        self.assertIsNone(self.row(item))

    def test_an_100_an_empty_copy_backing_off_still_reads_mal_for_a_write(self):
        item = self.title(total=12)
        AnimeListing.objects.create(item=item, retry_after=self.clock.moment + timedelta(minutes=15))
        anime_tracking.mark_watched(self.user, item)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 12))

    def test_an_100_anilists_answer_alone_is_no_copy(self):
        item = self.title(total=12)
        self.fake.fail("mal")
        anime_listing.refresh([item])
        self.assertIsNone(anime_listing.stored_listing(item))
        with self.assertRaises(AnimeDetailsUnavailable):
            anime_tracking.mark_watched(self.user, item)
        self.fake.recover()
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.row(item).progress, 12)

    def test_an_100_a_job_works_nobody_out_from_an_empty_copy(self):
        title = self.fake.add(total=12)
        item = self.title(title=title)
        self.tick(item, 1)
        AnimeListing.objects.filter(item=item).update(fetched_at=None, rows_fetched_at=None, total=None)
        before = self.row(item).aired_count
        self.fake.fail()
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(self.row(item).aired_count, before)


class CalendarJobTests(AnimeTestCase):
    """AN-202: the calendar job refreshes the copy and reopens titles."""

    def setUp(self):
        super().setUp()
        schedule = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        patcher = mock.patch.object(services, "api_request", return_value=schedule)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_202_the_calendar_job_reopens_a_caught_up_title(self):
        title = self.fake.add(total=12, status="Airing", next_episode=3, next_air_at=self.clock.moment + timedelta(days=2))
        item = self.title(title=title)
        self.tick(item, 1, 2)
        self.assertTrue(self.row(item).caught_up)
        self.clock.advance(days=3)
        title.next_episode = 4
        title.next_air_at = self.clock.moment + timedelta(days=4)
        with mock.patch.object(calendar_anime, "process_other"):
            calendar_anime.process_anime_bulk([item], [])
        row = self.row(item)
        self.assertEqual((row.caught_up, row.up_next_episode, row.aired_count), (False, 3, 3))

    def test_an_202_a_failing_refresh_never_stops_the_events(self):
        item = self.title(total=2)
        self.fake.fail()
        with mock.patch.object(calendar_anime, "process_other") as other:
            calendar_anime.process_anime_bulk([item], [])
        other.assert_called_once()

    def test_an_202_a_failing_schedule_read_still_reads_a_raised_total(self):
        title = self.fake.add(total=2)
        item = self.title(title=title)
        self.tick(item, 1, 2)
        self.assertEqual(self.row(item).status, "Completed")
        event = Event.objects.create(item=item, content_number=2, datetime=self.clock.moment - timedelta(days=400))
        self.clock.advance(days=8)
        title.total = 4
        for failure in (requests.exceptions.Timeout, ProviderDown, KeyError):
            with (
                self.subTest(failure=failure.__name__),
                mock.patch.object(services, "api_request", side_effect=failure),
                mock.patch.object(calendar_anime, "process_other") as other,
            ):
                events_bulk = []
                calendar_anime.process_anime_bulk([item], events_bulk)
                self.assertEqual(events_bulk, [])
                other.assert_not_called()
        row = self.row(item)
        self.assertEqual((row.status, row.up_next_episode, row.aired_count), ("In progress", 3, 4))
        self.assertTrue(Event.objects.filter(pk=event.pk).exists(), "The events stay until the next run")

    def test_an_202_a_failing_schedule_read_stops_nothing_else_in_the_job(self):
        item = self.title(total=2)
        movie = Item.objects.create(source="tmdb", media_type="movie", media_id="1", title="M", image="https://example.com/m.jpg")
        with (
            mock.patch.object(services, "api_request", side_effect=requests.exceptions.Timeout),
            mock.patch("events.calendar.main.process_other") as other,
        ):
            self.assertEqual(process_items([item, movie]), [])
        other.assert_called_once()
        self.assertEqual(other.call_args.args[0], movie)

    def test_an_202_one_titles_failed_read_keeps_the_others_events(self):
        good = self.title(total=12)
        bad = self.title(total=None)
        airing_at = int(self.clock.moment.timestamp())
        answer = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": int(good.media_id),
                            "endDate": {"year": 2026, "month": 10, "day": 1},
                            "episodes": 12,
                            "airingSchedule": {"nodes": [{"episode": 12, "airingAt": airing_at}]},
                        },
                        {
                            "idMal": int(bad.media_id),
                            "endDate": {"year": None, "month": None, "day": None},
                            "episodes": None,
                            "airingSchedule": {"nodes": [{"episode": 3, "airingAt": airing_at}]},
                        },
                    ],
                },
            },
        }
        with (
            mock.patch.object(services, "api_request", return_value=answer),
            mock.patch.object(services, "get_media_metadata", side_effect=requests.exceptions.Timeout),
        ):
            events_bulk = []
            calendar_anime.process_anime_bulk([good, bad], events_bulk)
        self.assertEqual([(event.item, event.content_number) for event in events_bulk], [(good, 12)])

    def test_an_202_a_failed_read_of_some_titles_keeps_the_answers_of_the_rest(self):
        # Through the real read: the calls for 25 and for 5 answer, the last aired one fails.
        items = [self.title(total=12, status="Airing") for _ in range(30)]
        on_a_break = items[-1]
        anime_tracking.refresh_after_calendar(items[-1:])
        before = AnimeListing.objects.get(item=on_a_break)
        self.clock.advance(days=1)
        media = [
            {
                "id": 1000 + int(item.media_id),
                "idMal": int(item.media_id),
                "status": "RELEASING",
                "nextAiringEpisode": {"episode": 8, "airingAt": int(self.clock.moment.timestamp()) + 86400},
                "airingSchedule": {"nodes": [{"episode": 8, "airingAt": int(self.clock.moment.timestamp()) + 86400}]},
                "streamingEpisodes": [],
            }
            for item in items
        ]
        media[-1].update(nextAiringEpisode=None, airingSchedule={"nodes": []})
        media.sort(key=lambda entry: entry["idMal"])
        pages = [
            {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": media[:25]}}},
            {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": media[25:]}}},
            requests.exceptions.Timeout,
        ]
        with (
            mock.patch.object(anilist, "anime_episodes", REAL_ANIME_EPISODES),
            mock.patch.object(services, "api_request", side_effect=pages),
        ):
            anime_tracking.refresh_after_calendar(items)
        fine = AnimeListing.objects.get(item=items[0])
        self.assertEqual((fine.next_episode, fine.retry_after), (8, None))
        self.assertEqual(fine.rows_fetched_at, self.clock.moment)
        self.assertFalse(anime_listing.stored_listing(items[0]).awaiting_schedule)
        kept = AnimeListing.objects.get(item=on_a_break)
        self.assertEqual(kept.rows_fetched_at, before.rows_fetched_at, "It keeps what it had")
        self.assertIsNotNone(kept.retry_after)

    def test_an_202_a_hanging_mal_costs_one_short_read_in_a_run(self):
        moment = int(self.clock.moment.timestamp())
        no_total = [self.title(total=None, status="Airing") for _ in range(3)]
        no_schedule = [self.title(total=12, status="Airing") for _ in range(3)]
        for name, items, media in (
            # AniList has no total for these, so the job would ask MAL for it, then for the numbers.
            (
                "no total",
                no_total,
                [
                    {
                        "idMal": int(item.media_id),
                        "endDate": {"year": None, "month": None, "day": None},
                        "episodes": None,
                        "airingSchedule": {"nodes": [{"episode": 3, "airingAt": moment}]},
                    }
                    for item in no_total
                ],
            ),
            # AniList has no schedule for these, so the job would ask MAL for releases, then for the numbers.
            ("no schedule", no_schedule, []),
        ):
            answer = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": media}}}
            with (
                self.subTest(name=name),
                mock.patch.object(services, "api_request", return_value=answer),
                mock.patch.object(mal, "anime", side_effect=requests.exceptions.Timeout) as read,
            ):
                events_bulk = []
                calendar_anime.process_anime_bulk(items, events_bulk)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(read.call_args.kwargs["timeout"], anime_listing.JOB_TIMEOUT)
                self.assertEqual(events_bulk, [])

    def test_an_202_a_mal_timeout_in_one_run_doesnt_stop_the_next(self):
        item = self.title(total=2)
        with mock.patch.object(mal, "anime", side_effect=requests.exceptions.Timeout) as read:
            calendar_anime.process_anime_bulk([item], [])
        self.assertEqual(read.call_count, 1)
        self.fake.calls.clear()
        calendar_anime.process_anime_bulk([item], [])
        self.assertGreater(self.fake.count("mal"), 0)
        self.assertEqual(AnimeListing.objects.get(item=item).total, 2)

    def test_an_202_a_refused_mal_keeps_being_asked(self):
        # Only a hang holds up the worker; a refusal costs nothing and one title's says little.
        items = [self.title(total=2) for _ in range(2)]
        with mock.patch.object(mal, "anime", side_effect=ProviderDown) as read:
            anime_tracking.refresh_after_calendar(items)
        self.assertEqual(read.call_count, 2)

    def test_an_202_the_jobs_reads_are_short(self):
        answer = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        item = self.title(total=2)
        with (
            mock.patch.object(services, "api_request", return_value=answer) as schedule,
            mock.patch.object(anilist, "anime_episodes", wraps=anilist.anime_episodes) as episodes,
            mock.patch.object(mal, "anime", wraps=mal.anime) as numbers,
        ):
            calendar_anime.process_anime_bulk([item], [])
        self.assertEqual(schedule.call_args.kwargs["timeout"], anime_listing.JOB_TIMEOUT)
        self.assertEqual(episodes.call_args.kwargs["timeout"], anime_listing.JOB_TIMEOUT)
        self.assertTrue(all(call.kwargs["timeout"] == anime_listing.JOB_TIMEOUT for call in numbers.call_args_list))
        self.assertGreater(numbers.call_count, 0)

    def test_an_202_refreshing_asks_anilist_once_for_many_titles(self):
        items = [self.title(total=2) for _ in range(3)]
        self.fake.calls.clear()
        anime_tracking.refresh_after_calendar(items)
        self.assertEqual(self.fake.count("anilist"), 1)

    def test_an_202_the_job_looks_again_at_a_stored_copy_once_a_week(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        Event.objects.create(item=item, content_number=2, datetime=self.clock.moment - timedelta(days=400))
        selected = filter_items_to_fetch(Item.objects.filter(pk=item.pk))
        self.assertFalse(selected.exists())
        self.clock.advance(days=8)
        selected = filter_items_to_fetch(Item.objects.filter(pk=item.pk))
        self.assertTrue(selected.exists())

    def test_an_100_mal_down_keeps_the_numbers_and_works_the_rest_out(self):
        title = self.fake.add(total=2)
        item = self.title(title=title)
        self.tick(item, 1)
        self.fake.fail("mal")
        title.next_episode = None
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(AnimeListing.objects.get(item=item).total, 2)
        self.assertEqual(self.row(item).progress, 1)


class BreakTests(AnimeTestCase):
    """AN-100: with nothing named next, AniList's last past entry says how far it has aired."""

    def on_break(self, *, last=12, days_ago=10, total=24, **values):
        moment = self.clock.moment - timedelta(days=days_ago)
        return self.title(total=total, status="Airing", last_aired=(last, moment), **values)

    def numbers(self, item):
        return [row["n"] for row in self.rows(item)["episodes"]]

    def test_an_100_a_title_with_a_total_counts_what_aired_before_the_break(self):
        item = self.on_break()
        state = self.state(item)
        self.assertEqual((state["total"], state["aired_count"]), (24, 12))
        self.assertEqual(state["eye"]["count"], 12)
        rows = self.rows(item)["episodes"]
        self.assertEqual([row["n"] for row in rows], list(range(1, 25)))
        self.assertEqual(rows[11]["a"], 1)
        self.assertEqual(rows[12], {"n": 13}, "It reads TBA")
        anime_tracking.mark_watched(self.user, item)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.aired_count, row.caught_up), ("In progress", 12, 12, True))
        self.assertEqual((row.next_air_episode, row.next_air_date), (13, None))

    def test_an_400_a_log_during_a_break_covers_what_aired(self):
        item = self.on_break()
        self.log(item)
        self.assertEqual(self.watched(item), set(range(1, 13)))
        self.assertEqual(self.row(item).status, "In progress")

    def test_an_103_an_episode_after_the_break_isnt_ticked_with_a_total(self):
        item = self.on_break()
        with self.assertRaises(anime_tracking.TVInvalid):
            self.tick(item, 13)

    def test_an_100_a_long_runner_counts_every_episode_up_to_it(self):
        item = self.on_break(total=None, last=1180, episodes={62: ("Sixty-two", ""), 130: ("One thirty", "")})
        state = self.state(item)
        self.assertEqual((state["total"], state["aired_count"]), (None, 1180))
        rows = self.rows(item)["episodes"]
        self.assertEqual([row["n"] for row in rows], list(range(1, 1181)))
        self.assertEqual(rows[61]["t"], "Sixty-two")
        self.assertTrue(all(row["a"] == 1 for row in rows))

    def test_an_101_a_long_runner_on_a_break_can_still_tick_what_anilist_hasnt_listed(self):
        item = self.on_break(total=None, last=3)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.numbers(item), [1, 2, 3, 4])
        self.tick(item, 4)
        self.assertEqual(self.numbers(item), [1, 2, 3, 4, 5])
        state = self.state(item)
        self.assertEqual((state["watched_count"], state["aired_count"], state["caught_up"]), (4, 4, True))

    def test_an_100_a_long_break_still_counts_within_half_a_year(self):
        # Yamato yo Towa ni: REBEL3199: 26 episodes, E19 in June, in theaters a part at a time.
        state = self.state(self.on_break(total=26, last=19, days_ago=131))
        self.assertEqual(state["aired_count"], 19)

    def test_an_100_past_half_a_year_anilist_has_stopped_following_a_title_with_a_total(self):
        # Zhe Tian: 208 episodes, weekly, E160 in April and nothing since.
        state = self.state(self.on_break(total=208, last=160, days_ago=189))
        self.assertEqual(state["aired_count"], 208)

    def test_an_100_past_half_a_year_a_long_runners_entry_is_still_evidence(self):
        # Chibi Maruko-chan: no total, E1462 in 2024, and a list of E1419 to E1448.
        episodes = {number: (f"Title {number}", "") for number in range(1419, 1449)}
        item = self.on_break(total=None, last=1462, days_ago=674, episodes=episodes)
        state = self.state(item)
        self.assertEqual((state["aired_count"], state["eye"]["count"]), (None, 1462))
        self.assertEqual(self.numbers(item), list(range(1, 1463)))
        self.tick(item, 1500)
        self.assertEqual(self.numbers(item), list(range(1, 1502)))

    def test_an_202_the_break_ends_and_the_next_one_is_named(self):
        title = self.fake.add(total=24, status="Airing", last_aired=(12, self.clock.moment - timedelta(days=10)))
        item = self.title(title=title)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.row(item).caught_up, True)
        self.clock.advance(days=2)
        title.last_aired = None
        title.next_episode = 13
        title.next_air_at = self.clock.moment + timedelta(days=1)
        title.schedule = {13: title.next_air_at}
        anime_tracking.refresh_after_calendar([item])
        row = self.row(item)
        self.assertEqual((row.aired_count, row.caught_up, row.next_air_episode), (12, True, 13))
        self.clock.advance(days=2)
        anime_tracking.refresh_after_calendar([item])
        row = self.row(item)
        self.assertEqual((row.aired_count, row.caught_up, row.up_next_episode), (13, False, 13))

    def test_an_100_a_failed_read_during_a_break_keeps_the_count(self):
        item = self.on_break()
        self.assertEqual(self.state(item)["aired_count"], 12)
        self.clock.advance(days=2)
        self.fake.fail("anilist")
        self.assertEqual(self.state(item)["aired_count"], 12)
        self.fake.recover()
        self.clock.advance(minutes=16)
        self.assertEqual(self.state(item)["aired_count"], 12)

    def test_an_100_a_finished_title_ignores_its_old_entry(self):
        moment = self.clock.moment - timedelta(days=10)
        state = self.state(self.title(total=24, status="Finished", last_aired=(12, moment)))
        self.assertEqual(state["aired_count"], 24)


class NoCopyNeededTests(AnimeTestCase):
    """AN-301: what needs no copy works at any time, with no copy and every provider down."""

    def imported(self, **values):
        # An imported title holds its own numbers until the job first reads MAL's.
        item = self.title(total=12)
        anime_tracking.import_title(self.user, item, status="In progress", count=3, total=12, **values)
        self.assertIsNone(anime_listing.stored_listing(item))
        self.fake.fail()
        self.fake.calls.clear()
        return item

    def assert_no_call(self):
        self.assertEqual(self.fake.calls, [])

    def test_an_301_clearing_stars(self):
        item = self.imported(score=8)
        anime_tracking.set_rating(self.user, item, None)
        self.assertIsNone(self.row(item).score)
        self.assert_no_call()

    def test_an_301_turning_the_heart_off(self):
        item = self.imported()
        MediaLike.objects.create(user=self.user, item=item)
        anime_tracking.set_like(self.user, item, False)
        self.assertFalse(self.liked(item))
        self.assert_no_call()

    def test_tv_102_an_untick(self):
        item = self.imported()
        anime_tracking.untick(self.user, item, 3)
        self.assertEqual(self.row(item).progress, 2)
        self.assert_no_call()

    def test_an_300_the_filled_eye(self):
        item = self.imported()
        anime_tracking.unmark(self.user, item, confirm=True)
        self.assertIsNone(self.row(item))
        self.assert_no_call()

    def test_an_600_stopping_a_rewatch(self):
        item = self.title(total=12)
        anime_tracking.import_title(self.user, item, status="In progress", count=3, total=12, earlier=[{"count": 12}])
        self.fake.fail()
        self.fake.calls.clear()
        anime_tracking.stop_rewatch(self.user, item)
        self.assertIsNone(self.row(item).rewatch_started_at)
        self.assert_no_call()

    def test_tv_405_a_log_edit(self):
        item = self.imported()
        entry = anime_tracking.create_log(
            self.user,
            item,
            consumed_at=OCT_25,
            import_source="myanimelist",
            entry_point=anime_tracking.IMPORT,
        ).entry
        self.fake.calls.clear()
        anime_tracking.update_log(entry, {"review": "Fine", "rating": None})
        entry.refresh_from_db()
        self.assertEqual(entry.review, "Fine")
        self.assert_no_call()

    def test_an_900_writing_notes_or_clearing_stars(self):
        item = self.imported(score=8)
        anime_tracking.apply_written_state(self.user, item, {"notes": "Mine"}, entry_point=anime_tracking.WEB)
        anime_tracking.apply_written_state(self.user, item, {"rating": None}, entry_point=anime_tracking.WEB)
        row = self.row(item)
        self.assertEqual((row.notes, row.score), ("Mine", None))
        self.assert_no_call()

    def test_an_301_through_the_api(self):
        item = self.imported(score=8)
        self.action(item, "rate", rating=None)
        self.action(item, "untick", episode_number=3)
        self.write(item, notes="Mine")
        row = self.row(item)
        self.assertEqual((row.score, row.progress, row.notes), (None, 2, "Mine"))
        self.assert_no_call()

    def test_an_100_a_stale_copy_isnt_read_again_for_them_either(self):
        item = self.title(total=12)
        self.tick(item, 1, 2)
        self.clock.advance(days=8)
        self.fake.calls.clear()
        anime_tracking.untick(self.user, item, 2)
        anime_tracking.set_rating(self.user, item, None)
        anime_tracking.apply_written_state(self.user, item, {"notes": "Mine"}, entry_point=anime_tracking.WEB)
        self.assert_no_call()

    def test_an_900_a_lowered_number_is_an_untick(self):
        item = self.imported()
        anime_tracking.write_progress(self.user, item, 2, entry_point=anime_tracking.WEB)
        self.assertEqual(self.watched(item), {1, 2})
        anime_tracking.apply_written_state(self.user, item, {"progress": 1}, entry_point=anime_tracking.WEB)
        self.assertEqual(self.watched(item), {1})
        self.write(item, progress=0)
        self.assertIsNone(self.row(item))
        self.assert_no_call()

    def test_an_900_the_webs_minus_button_is_an_untick(self):
        item = self.imported()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("progress_edit", kwargs={"media_type": "anime", "instance_id": self.row(item).pk}),
            {"operation": "decrease"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row(item).progress, 2)
        self.assert_no_call()

    def test_an_900_a_raised_number_or_a_status_with_it_still_needs_the_numbers(self):
        item = self.imported()
        for write in (
            lambda: anime_tracking.write_progress(self.user, item, 4, entry_point=anime_tracking.WEB),
            lambda: anime_tracking.apply_written_state(self.user, item, {"progress": 4}, entry_point=anime_tracking.WEB),
            lambda: anime_tracking.apply_written_state(self.user, item, {"progress": 3}, entry_point=anime_tracking.WEB),
            lambda: anime_tracking.apply_written_state(
                self.user,
                item,
                {"status": "Paused", "progress": 2},
                entry_point=anime_tracking.WEB,
            ),
        ):
            with self.assertRaises(AnimeDetailsUnavailable):
                write()
        self.assertEqual(self.row(item).progress, 3)

    def stored_completed(self, **values):
        # A file says it's finished and how many isn't known yet: a stored Completed (AN-902).
        item = self.title(total=12)
        anime_tracking.import_title(self.user, item, status="Completed", count=0, **values)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.reconciled_at), ("Completed", 0, None))
        return item

    def test_an_902_a_write_with_no_copy_doesnt_work_a_stored_completed_out(self):
        item = self.stored_completed()
        self.fake.fail()
        anime_tracking.apply_written_state(self.user, item, {"notes": "Mine"}, entry_point=anime_tracking.WEB)
        self.fake.recover()
        row = self.row(item)
        self.assertEqual((row.notes, row.status, row.reconciled_at), ("Mine", "Completed", None))
        anime_tracking.refresh_after_calendar([item])
        state = self.state(item)
        self.assertEqual((state["status"], state["watched_count"], state["aired_count"]), ("Completed", 12, 12))
        self.assertNotEqual(state["eye"]["tap"], "mark")
        anime_tracking.apply_written_state(self.user, item, {"notes": "Still mine"}, entry_point=anime_tracking.WEB)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 12))
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(self.watched(item), set(range(1, 13)))

    def test_an_902_clearing_stars_or_an_untick_with_no_copy_doesnt_either(self):
        for write in (
            lambda item: anime_tracking.set_rating(self.user, item, None),
            lambda item: anime_tracking.set_like(self.user, item, False),
            lambda item: anime_tracking.untick(self.user, item, 1),
        ):
            item = self.stored_completed()
            self.fake.fail()
            write(item)
            self.fake.recover()
            self.assertIsNone(self.row(item).reconciled_at)

    def test_an_902_a_write_with_no_copy_keeps_a_stored_completeds_dates(self):
        finished = datetime(2020, 5, 1, tzinfo=UTC)
        item = self.stored_completed(end_date=finished)
        self.fake.fail()
        anime_tracking.apply_written_state(self.user, item, {"notes": "Mine"}, entry_point=anime_tracking.WEB)
        self.fake.recover()
        self.assertEqual(self.row(item).end_date, finished)

    def test_an_902_a_manual_titles_write_still_works_it_out(self):
        item = Item.objects.create(source="manual", media_type="anime", media_id="manual-1", title="Mine", image="https://example.com/p.jpg")
        self.tick(item, 1)
        self.assertIsNotNone(self.row(item).reconciled_at)

    def test_an_100_what_needs_the_numbers_is_still_refused(self):
        item = self.imported()
        for write in (
            lambda: anime_tracking.mark_watched(self.user, item),
            lambda: anime_tracking.tick(self.user, item, 4),
            lambda: anime_tracking.set_rating(self.user, item, 8),
            lambda: anime_tracking.apply_written_state(self.user, item, {"status": "Paused"}, entry_point=anime_tracking.WEB),
        ):
            with self.assertRaises(AnimeDetailsUnavailable):
                write()
        self.assertEqual(self.row(item).progress, 3)


class ProviderFailureShapesTests(AnimeTestCase):
    """A network failure and a provider error both keep the numbers (AN-101)."""

    def test_an_101_a_timeout_keeps_the_numbers(self):
        item = self.title(total=2)
        with mock.patch.object(anilist, "anime_episodes", side_effect=requests.exceptions.Timeout):
            listing = anime_listing.get_listing(item)
        self.assertEqual(listing.total, 2)

    def test_an_101_a_provider_error_keeps_the_numbers(self):
        item = self.title(total=2)
        with mock.patch.object(anilist, "anime_episodes", side_effect=ProviderDown):
            listing = anime_listing.get_listing(item)
        self.assertEqual(listing.total, 2)

    def test_fake_provider_blocks_real_requests(self):
        with FakeAnimeProvider(), self.assertRaises(AssertionError):
            services.api_request("MAL", "GET", "https://example.com")
        self.assertEqual(datetime(2026, 1, 1, tzinfo=UTC).year, 2026)
