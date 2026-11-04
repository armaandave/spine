"""The stored copy of a title's numbers and rows (AN-100, AN-101), and the job (AN-202)."""

import copy
from datetime import UTC, datetime, timedelta
from unittest import mock

import requests
from django.test import SimpleTestCase
from django.urls import reverse

from app import anime_listing, anime_tracking
from app.anime_listing import AnimeDetailsUnavailable
from app.models import AnimeListing, AnimeMark, Item
from app.providers import anilist, mal, services
from app.tests.anime_fixtures import OCT_25, AnimeTestCase, FakeAnimeProvider
from app.tests.tv_fixtures import ProviderDown
from events.calendar import anime as calendar_anime
from events.calendar.selectors import filter_items_to_fetch
from events.models import Event

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
