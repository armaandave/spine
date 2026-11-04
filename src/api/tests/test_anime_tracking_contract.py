"""The acceptance scenarios of docs/anime-tracking-contract.md, through the API.

Every scenario N-01 to N-30 has its own test, named for it, driven through the routes
the app and installed builds call, and through the web, a webhook or an import where
one applies (those run in their own files too). The server runs on America/New_York
and the account calendar on America/Los_Angeles; MAL and AniList are faked.
"""

import io
from datetime import UTC, date, datetime
from unittest import mock
from uuid import uuid4

import requests
from rest_framework.test import APIClient

from app import anime_tracking
from app.models import Anime, AnimeListing, AnimeMark, Item
from app.providers import mal
from app.tests.anime_fixtures import OCT_25, AnimeTestCase
from app.tests.tv_fixtures import make_user
from integrations.imports.mal import MyAnimeListImporter
from social.models import Follow, FollowStatus, ProgressChange


def line(state):
    """Return the card's line for a Watching title, as the app builds it (AN-700)."""
    if state["caught_up"]:
        return "Caught up"
    parts = []
    if state["up_next"]:
        parts.append(f"E{state['up_next']['episode_number']} next")
    count = state["watched_count"]
    if state["aired_count"] is not None:
        parts.append(f"{count} of {state['aired_count']}")
    else:
        parts.append(f"{count} episode{'' if count == 1 else 's'}")
    return " · ".join(parts)


class AcceptanceTests(AnimeTestCase):
    """N-01 to N-30."""

    def test_n_01_tick_e1_of_an_untracked_title(self):
        item = self.title()
        response = self.action(item, "tick", episode_number=1)
        state = response.data["anime"]
        self.assertEqual(state["status"], "In progress")
        self.assertEqual(line(state), "E2 next · 1 of 25")
        self.assertEqual(self.logs(item), [])
        activity = self.activity()
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0]["object"]["summary"], "watched 1 episode")

    def test_n_02_a_double_tap_is_one_viewing(self):
        item = self.title()
        self.action(item, "tick", episode_number=1)
        second = self.action(item, "tick", episode_number=1)
        self.assertFalse(second.data["changed"])
        self.assertEqual(len(self.viewings(item)), 1)

    def test_n_03_tick_e5_first_and_answer_yes(self):
        item = self.title()
        first = self.action(item, "tick", episode_number=5)
        earlier = first.data["earlier_unwatched"]
        self.assertEqual(earlier["question"], "Also mark E1–E4 watched?")  # noqa: RUF001
        self.assertEqual(earlier["from"], {"episode_number": 1})
        self.assertEqual(earlier["to"], {"episode_number": 4})
        yes = self.action(
            item,
            "mark_earlier",
            mutation_id=str(uuid4()),
            **{"from": earlier["from"], "to": earlier["to"]},
        )
        self.assertEqual(line(yes.data["anime"]), "E6 next · 5 of 25")
        rows = {viewing.episode_number: viewing for viewing in self.viewings(item)}
        self.assertEqual(set(rows), {1, 2, 3, 4, 5})
        self.assertIsNone(rows[1].watched_on)
        self.assertEqual(rows[1].kind, "fill")
        self.assertEqual(rows[5].kind, "tick")
        self.assertIsNotNone(rows[5].watched_on)
        again = self.action(item, "tick", episode_number=7)
        self.assertIsNone(again.data["earlier_unwatched"])

    def test_n_04_tick_e5_first_and_answer_no(self):
        item = self.title()
        self.assertIsNotNone(self.action(item, "tick", episode_number=5).data["earlier_unwatched"])
        second = self.action(item, "tick", episode_number=6)
        self.assertIsNone(second.data["earlier_unwatched"])
        self.assertEqual(line(second.data["anime"]), "E7 next · 2 of 25")

    def test_n_05_tick_then_untick_the_only_episode_leaves_the_library(self):
        item = self.title()
        self.action(item, "tick", episode_number=1)
        response = self.action(item, "untick", episode_number=1)
        self.assertFalse(response.data["anime"]["in_library"])
        self.assertIsNone(response.data["tracking"])
        self.assertIsNone(self.row(item))

    def test_n_06_ticking_the_last_episode_opens_the_log_screen_once(self):
        item = self.title(total=3)
        self.action(item, "tick", episode_number=1)
        self.action(item, "tick", episode_number=2)
        last = self.action(item, "tick", episode_number=3)
        finished = last.data["finished"]
        self.assertEqual(finished["level"], "title")
        self.assertEqual(finished["composer"]["targets"][0]["date"], OCT_25.isoformat())
        self.assertEqual(last.data["anime"]["status"], "Completed")
        # Not now saves nothing: the app simply doesn't log.
        self.assertEqual(self.logs(item), [])
        self.assertIn("log", last.data["anime"]["available_actions"])
        self.action(item, "untick", episode_number=3)
        again = self.action(item, "tick", episode_number=3)
        self.assertIsNone(again.data["finished"])
        self.assertEqual(again.data["anime"]["status"], "Completed")

    def test_n_07_skip_e4_and_tick_the_rest(self):
        item = self.title()
        for number in [1, 2, 3, *range(5, 26)]:
            response = self.action(item, "tick", episode_number=number)
        state = response.data["anime"]
        self.assertEqual((state["watched_count"], state["aired_count"]), (24, 25))
        self.assertEqual(state["first_gap_episode"], 4)
        self.assertEqual(state["status"], "In progress")
        self.assertFalse(state["finished"])
        self.assertIsNone(response.data["finished"])

    def _skipped_e4(self, item):
        for number in [1, 2, 3, *range(5, 26)]:
            self.action(item, "tick", episode_number=number)

    def test_n_08_then_log(self):
        item = self.title()
        self._skipped_e4(item)
        response = self.post_log(item, mutation_id=str(uuid4()))
        self.assertEqual(response.data["anime"]["status"], "Completed")
        rows = {viewing.episode_number: viewing for viewing in self.viewings(item)}
        self.assertEqual(rows[4].kind, "fill")
        self.assertIsNone(rows[4].watched_on)
        self.assertEqual(rows[4].diary_entry_id, self.logs(item)[0].pk)
        self.assertEqual(rows[5].kind, "tick")
        self.assertIsNotNone(rows[5].watched_on)

    def test_n_09_eye_on_an_untracked_title_then_the_filled_eye(self):
        item = self.title()
        marked = self.action(item, "mark_watched")
        self.assertEqual(marked.data["anime"]["status"], "Completed")
        self.assertEqual(self.logs(item), [])
        self.assertEqual(self.activity(), [])
        self.assertEqual(marked.data["anime"]["eye"]["tap"], "unmark_marks")
        undone = self.action(item, "unmark")
        self.assertFalse(undone.data["anime"]["in_library"])
        self.assertIsNone(self.row(item))
        self.assertEqual(self.viewings(item), [])

    def test_n_10_tick_e1_to_e3_eye_then_the_filled_eye(self):
        item = self.title()
        for number in (1, 2, 3):
            self.action(item, "tick", episode_number=number)
        self.action(item, "mark_watched")
        undone = self.action(item, "unmark")
        self.assertEqual(self.watched(item), {1, 2, 3})
        self.assertEqual(undone.data["anime"]["up_next"]["episode_number"], 4)
        self.assertEqual(undone.data["anime"]["status"], "In progress")

    def test_n_11_stars_on_an_untracked_title(self):
        item = self.title()
        before_all_time = self.stats_time()
        response = self.action(item, "rate", rating=8)
        state = response.data["anime"]
        self.assertEqual(state["status"], "Completed")
        self.assertIsNone(response.data["finished"])
        self.assertEqual(str(self.row(item).score), "8.0")
        self.assertEqual(self.activity(), [])
        self.assertEqual(self.stats_time()[1], before_all_time[1] + 25)
        self.assertEqual(self.stats_time()[0], 25 * 24)
        for year in (2013, 2025, 2026):
            self.assertEqual(self.stats_time(year), (0, 0))

    def test_n_12_stars_at_e3_cancel_then_stars_and_save(self):
        item = self.title()
        for number in (1, 2, 3):
            self.action(item, "tick", episode_number=number)
        state = self.state(item)
        self.assertEqual(state["opinion_action"], "log")
        # Cancelling is the app not saving: nothing has changed.
        self.assertEqual(self.watched(item), {1, 2, 3})
        self.assertIsNone(self.row(item).score)
        response = self.post_log(item, rating=8, mutation_id=str(uuid4()))
        self.assertEqual(response.data["anime"]["status"], "Completed")
        self.assertEqual(len(self.logs(item)), 1)
        self.assertEqual(len(self.watched(item)), 25)
        self.assertEqual(str(self.row(item).score), "8.0")

    def test_n_13_direct_stars_through_the_api_at_e3(self):
        item = self.title()
        for number in (1, 2, 3):
            self.action(item, "tick", episode_number=number)
        version = self.row(item).state_version
        refused = self.action(item, "rate", expect=409, rating=8)
        self.assertEqual(refused.data["error"]["message"], "Log this first.")
        patched = self.write(item, expect=409, rating=8)
        self.assertEqual(patched.data["error"]["message"], "Log this first.")
        heart = self.post_like(item, expect=409)
        self.assertEqual(heart.data["error"]["message"], "Log this first.")
        row = self.row(item)
        self.assertIsNone(row.score)
        self.assertEqual(row.state_version, version)
        self.assertFalse(self.liked(item))

    def test_n_14_stars_on_a_caught_up_title_then_e8_airs_then_stars_again(self):
        item = self.airing()
        self.action(item, "mark_watched")
        saved = self.action(item, "rate", rating=8)
        self.assertEqual(saved.data["anime"]["opinion_action"], "save")
        self.assertTrue(saved.data["anime"]["caught_up"])
        self.clock.set(datetime(2026, 10, 30, 12, 0, tzinfo=UTC))
        state = self.state(item)
        self.assertEqual(str(self.row(item).score), "8.0")
        self.assertEqual(state["opinion_action"], "log")
        self.assertEqual(state["up_next"]["episode_number"], 8)
        refused = self.action(item, "rate", expect=409, rating=6)
        self.assertEqual(refused.data["error"]["message"], "Log this first.")
        self.assertEqual(str(self.row(item).score), "8.0")

    def test_n_15_log_an_airing_title_with_7_of_12_aired(self):
        item = self.airing()
        response = self.post_log(item, mutation_id=str(uuid4()))
        state = response.data["anime"]
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["caught_up"])
        self.assertEqual(state["next_airing"], {"episode_number": 8, "air_date": "2026-10-29"})
        self.assertEqual(self.watched(item), set(range(1, 8)))
        self.assertTrue(all(viewing.diary_entry_id for viewing in self.viewings(item)))

    def test_n_16_delete_that_log(self):
        item = self.airing()
        self.action(item, "tick", episode_number=1)
        logged = self.post_log(item, mutation_id=str(uuid4()))
        version = logged.data["anime"]["version"]
        anime_log = self.api.get(f"/api/v1/diary/{logged.data['id']}/").data["anime_log"]
        self.assertEqual(anime_log["unmarks_count"], 6)
        response = self.api.delete(f"/api/v1/diary/{logged.data['id']}/?expected_version={version}")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.watched(item), {1})
        state = self.state(item)
        self.assertEqual(state["up_next"]["episode_number"], 2)
        self.assertFalse(state["caught_up"])

    def test_n_17_tick_to_the_end_of_a_long_runner(self):
        item = self.runner(aired=3)
        for number in (1, 2):
            self.action(item, "tick", episode_number=number)
        last = self.action(item, "tick", episode_number=3)
        state = last.data["anime"]
        self.assertTrue(state["caught_up"])
        self.assertFalse(state["finished"])
        self.assertIsNone(last.data["finished"])
        self.assertIn("log", state["available_actions"])
        logged = self.post_log(item, mutation_id=str(uuid4()))
        self.assertEqual(logged.data["anime"]["status"], "In progress")

    def test_n_18_no_total_and_no_schedule(self):
        item = self.title(total=None, status="Airing")
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1])
        response = self.action(item, "tick", episode_number=1)
        rows = response.data["rows"]["episodes"]
        self.assertEqual([row["n"] for row in rows], [1, 2])
        state = response.data["anime"]
        self.assertIsNone(state["aired_count"])
        self.assertEqual(state["watched_count"], 1)
        self.assertEqual(self.row(item).progress, 1)
        # Any number is accepted.
        self.action(item, "tick", episode_number=40)
        self.assertEqual(self.row(item).progress, 2)

    def _next_title_pair(self):
        sequel = self.fake.add(title="Attack on Titan Season 2", total=12, start_date="2017-04-01")
        sequel_item = self.title(title=sequel)
        first = self.fake.add(
            total=2,
            series=[
                {"media_id": "x", "position": 0, "title": "placeholder"},
                {"media_id": sequel.media_id, "position": 2, "title": "Attack on Titan Season 2", "image": "https://example.com/s2.jpg", "release_date": "2017-04-01"},
            ],
        )
        first.series[0]["media_id"] = first.media_id
        item = self.title(title=first)
        # The job or a page stores the series order; here, the calendar job.
        anime_tracking.refresh_after_calendar([item])
        return item, sequel_item

    def test_n_19_finish_from_homes_sheet_with_a_next_title_aired(self):
        item, sequel = self._next_title_pair()
        self.action(item, "tick", episode_number=1)
        last = self.action(item, "tick", episode_number=2)
        self.assertIsNotNone(last.data["finished"])
        next_title = last.data["anime"]["next_title"]
        self.assertEqual(next_title["media_id"], sequel.media_id)
        self.assertTrue(next_title["has_aired"])
        # Not now: the sheet shows the next title's rows; closing adds nothing.
        sheet = self.get_rows(sequel)
        self.assertEqual(sheet["rows"]["up_next"], 1)
        self.assertIsNone(self.row(sequel))
        tick = self.action(sequel, "tick", episode_number=1)
        self.assertEqual(tick.data["anime"]["status"], "In progress")

    def test_n_20_a_finished_titles_page_offers_next(self):
        item, sequel = self._next_title_pair()
        self.action(item, "mark_watched")
        self.assertEqual(self.state(item)["next_title"]["title"], "Attack on Titan Season 2")
        self.assertIsNone(self.row(sequel))

    def _series_pair(self):
        # A title with a sequel, and no job has read its place in the series yet.
        sequel = self.fake.add(title="Attack on Titan Season 2", total=12, start_date="2017-04-01")
        sequel_item = self.title(title=sequel)
        first = self.fake.add(
            total=2,
            series=[
                {"media_id": "x", "position": 1, "title": "Attack on Titan"},
                {"media_id": sequel.media_id, "position": 2, "title": "Attack on Titan Season 2", "image": "https://example.com/s2.jpg", "release_date": "2017-04-01"},
            ],
        )
        first.series[0]["media_id"] = first.media_id
        return self.title(title=first), sequel_item

    def test_n_20_opening_the_page_stores_the_next_title_with_no_job(self):
        item, sequel = self._series_pair()
        self.action(item, "mark_watched")
        self.assertIsNone(self.state(item)["next_title"])
        response = self.api.get(f"/api/v1/media/mal/anime/{item.media_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user_state"]["anime"]["next_title"]["media_id"], sequel.media_id)
        stored = AnimeListing.objects.get(item=item)
        self.assertEqual(stored.next_title["media_id"], sequel.media_id)
        self.assertIsNotNone(stored.series_checked_at)
        self.assertIsNone(self.row(sequel))

    def test_n_20_a_page_with_cached_details_stores_the_next_title_too(self):
        item, sequel = self._series_pair()
        url = f"/api/v1/media/mal/anime/{item.media_id}/"
        with mock.patch.object(mal, "anime_series", side_effect=requests.exceptions.Timeout):
            self.assertEqual(self.api.get(url).status_code, 200)
        self.assertIsNone(AnimeListing.objects.get(item=item).series_checked_at)
        reads = self.fake.count("mal")
        self.assertEqual(self.api.get(url).status_code, 200)
        self.assertEqual(self.fake.count("mal"), reads, "The details came from the cache")
        self.assertEqual(AnimeListing.objects.get(item=item).next_title["media_id"], sequel.media_id)

    def test_n_20_a_usual_page_view_doesnt_write_the_next_title_again(self):
        item, _ = self._series_pair()
        url = f"/api/v1/media/mal/anime/{item.media_id}/"
        self.assertEqual(self.api.get(url).status_code, 200)
        checked = AnimeListing.objects.get(item=item).series_checked_at
        self.clock.advance(days=1)
        self.assertEqual(self.api.get(url).status_code, 200)
        self.assertEqual(AnimeListing.objects.get(item=item).series_checked_at, checked)
        self.clock.advance(days=7)
        self.assertEqual(self.api.get(url).status_code, 200)
        self.assertGreater(AnimeListing.objects.get(item=item).series_checked_at, checked)

    def test_n_21_rewatch_then_stop(self):
        item = self.title()
        self.action(item, "mark_watched")
        start = self.action(item, "rewatch")
        self.assertEqual(start.data["anime"]["up_next"]["episode_number"], 1)
        self.assertEqual(start.data["anime"]["status"], "In progress")
        self.assertEqual(self.viewings(item)[0].pass_number, 1)
        first = self.action(item, "tick", episode_number=1)
        self.assertEqual((first.data["anime"]["watched_count"], first.data["anime"]["aired_count"]), (1, 25))
        second = self.action(item, "tick", episode_number=2)
        self.assertEqual(second.data["anime"]["watched_count"], 2)
        self.assertEqual(self.stats_time()[1], 27)
        self.assertEqual(self.stats_time(2026), (48, 2))
        stopped = self.action(item, "stop_rewatch")
        self.assertEqual(stopped.data["anime"]["status"], "Completed")
        self.assertEqual(stopped.data["anime"]["watched_count"], 25)
        self.assertEqual(len(self.viewings(item)), 27)
        self.assertEqual(self.stats_time(2026), (48, 2))

    def test_n_22_pause_at_e8_tick_e8_by_mistake_then_untick(self):
        item = self.title()
        for number in range(1, 8):
            self.action(item, "tick", episode_number=number)
        self.clock.advance(minutes=1)
        self.action(item, "pause")
        self.clock.advance(minutes=1)
        ticked = self.action(item, "tick", episode_number=8)
        self.assertEqual(ticked.data["anime"]["status"], "In progress")
        undone = self.action(item, "untick", episode_number=8)
        state = undone.data["anime"]
        self.assertEqual(state["status"], "Paused")
        self.assertEqual(state["up_next"]["episode_number"], 8)

    def test_n_23_eye_on_a_movie_then_stars_then_log(self):
        item = self.title(title=self.fake.add(total=1, format="Movie", title="Your Name"))
        marked = self.action(item, "mark_watched")
        self.assertEqual(marked.data["anime"]["status"], "Completed")
        self.assertTrue(marked.data["anime"]["single"])
        self.assertIsNone(marked.data["anime"]["up_next"])
        self.assertEqual(marked.data["rows"]["episodes"], [])
        rated = self.action(item, "rate", rating=9)
        self.assertEqual(rated.data["anime"]["status"], "Completed")
        self.assertEqual(rated.data["anime"]["opinion_action"], "save")
        logged = self.post_log(item, mutation_id=str(uuid4()))
        self.assertEqual(logged.data["anime"]["status"], "Completed")
        self.assertEqual(len(self.logs(item)), 1)
        self.assertEqual(sorted(logged.data["anime"]["available_actions"]), ["log", "rate"])

    def _mal_list_entry(self, title, *, status, count, rewatches=0, rewatching=False):
        return {
            "node": {
                "id": int(title.media_id),
                "title": title.title,
                "main_picture": {"large": "https://example.com/p.jpg"},
                "num_episodes": title.total,
                "start_date": "2013-04-07",
                "average_episode_duration": 1440,
                "genres": [],
            },
            "list_status": {
                "status": status,
                "score": 0,
                "num_episodes_watched": count,
                "is_rewatching": rewatching,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "start_date": None,
                "finish_date": None,
                "num_times_rewatched": rewatches,
                "comments": "",
            },
        }

    def _import_mal(self, *entries):
        importer = MyAnimeListImporter("someone", self.user, "new")
        for entry in entries:
            importer._process_entry(entry, "anime")
        from integrations.imports import helpers

        helpers.cleanup_existing_media(importer.to_delete, self.user)
        helpers.bulk_create_media(importer.bulk_media, self.user, source="myanimelist")
        return importer

    def test_n_24_a_mal_row_with_2_rewatches_and_a_count_of_12(self):
        title = self.fake.add(total=12)
        self._import_mal(self._mal_list_entry(title, status="completed", count=12, rewatches=2))
        item = Item.objects.get(media_id=title.media_id, media_type="anime")
        self.assertEqual(Anime.objects.filter(user=self.user, item=item).count(), 1)
        row = self.row(item)
        self.assertEqual(row.status, "Completed")
        self.assertEqual(row.progress, 12)
        self.assertEqual(row.pass_number, 3)
        passes = {viewing.pass_number for viewing in self.viewings(item)}
        self.assertEqual(passes, {1, 2, 3})
        self.assertEqual(len(self.viewings(item)), 36)
        self.assertEqual(AnimeMark.objects.filter(anime=row).count(), 3)
        # Counts and marks are no progress and no start; the next case has a dated log.
        self.assertEqual(self.activity(), [])
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exclude(previous_progress__value=12).exists())
        self.assertEqual(self.fake.count("anilist"), 0)

    def test_n_24_a_dated_row_of_the_mal_export_is_a_log_on_its_own_date(self):
        from integrations.imports.mal_export.importer import MALExportImporter
        from integrations.tests.imports.test_mal_export import _xml

        title = self.fake.add(total=12)
        file = io.BytesIO(
            _xml(
                (
                    title.media_id,
                    "Completed",
                    {"my_watched_episodes": "12", "my_times_watched": "2", "my_finish_date": "2020-05-01"},
                ),
            ),
        )
        MALExportImporter(
            file,
            self.user,
            "new",
            metadata_fetcher=lambda _type, _id: {"title": "T", "image": "https://example.com/p.jpg", "max_progress": 12, "details": {}},
        ).import_data()
        item = Item.objects.get(media_id=title.media_id, media_type="anime")
        self.assertEqual(self.row(item).pass_number, 3)
        self.assertEqual(
            [(row["type"], row["consumed_at"]) for row in self.activity()],
            [("diary_created", "2020-05-01")],
        )
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exists())

    def test_n_25_import_with_a_count_of_5_in_progress(self):
        title = self.fake.add(total=12)
        self._import_mal(self._mal_list_entry(title, status="watching", count=5))
        item = Item.objects.get(media_id=title.media_id, media_type="anime")
        state = self.state(item)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual(line(state), "E6 next · 5 of 12")
        rows = self.viewings(item)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(viewing.kind == "fill" and viewing.watched_on is None for viewing in rows))
        self.assertEqual(self.stats_time(2026), (0, 0))
        self.assertEqual(self.stats_time()[1], 5)

    def test_n_26_webhook_watched_event_for_e4_of_an_untracked_title(self):
        item = self.title()
        moment = self.clock.moment
        result = anime_tracking.webhook_event(self.user, item, 4, event="watched", at=moment)
        self.assertTrue(result.changed)
        self.assertIsNone(result.earlier_unwatched)
        rows = self.viewings(item)
        self.assertEqual([(row.episode_number, row.kind, row.watched_at) for row in rows], [(4, "tick", moment)])

    def test_n_27_a_progress_write_of_5_then_3(self):
        item = self.title()
        raised = self.write(item, progress=5)
        self.assertEqual(raised.data["progress"]["value"], 5)
        self.assertEqual(self.watched(item), {1, 2, 3, 4, 5})
        self.assertTrue(all(viewing.kind == "tick" and viewing.watched_on == OCT_25 for viewing in self.viewings(item)))
        self.clock.advance(minutes=1)
        lowered = self.write(item, progress=3)
        self.assertEqual(lowered.data["progress"]["value"], 3)
        self.assertEqual(self.row(item).progress, 3)
        self.assertEqual(len(self.viewings(item)), 3)

    def test_n_28_log_from_an_installed_builds_request_shape(self):
        item = self.title()
        response = self.post_log(
            item,
            consumed_at="2026-10-25T23:30:00-04:00",
            auto_mark_consumed=False,
            is_rewatch=False,
            rating="8.0",
        )
        self.assertEqual(Anime.objects.filter(user=self.user, item=item).count(), 1)
        self.assertEqual(self.row(item).status, "Completed")
        entry = self.logs(item)[0]
        # 23:30 in New York is 20:30 in Los Angeles: the same calendar day there.
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, 0, 0, tzinfo=UTC))
        self.assertEqual(response.data["anime"]["watched_count"], 25)
        self.assertEqual(len(self.viewings(item)), 25)
        self.assertIsNone(response.data["finished"])

    def test_n_29_the_old_codes_insert_shape(self):
        """test_anime_schema holds the raw inserts against the pre-0095 models."""
        from app.tests.test_anime_schema import AN901RollbackInsertTests

        self.assertTrue(hasattr(AN901RollbackInsertTests, "test_n_29_old_inserts_save_by_the_database_defaults"))

    def test_n_30_export_then_import_into_an_empty_account(self):
        from app.tests.test_anime_export_round_trip import round_trip

        finished = self.title(total=3)
        watching = self.title(total=10)
        planned = self.title(total=10)
        self.action(finished, "mark_watched")
        self.action(finished, "rate", rating=8)
        for number in (1, 2):
            self.action(watching, "tick", episode_number=number)
        self.action(planned, "planning")
        header, other = round_trip(self.user)
        self.assertIn('"media_id"', header)
        for item, status, count, score in (
            (finished, "Completed", 3, "8.0"),
            (watching, "In progress", 2, None),
            (planned, "Planning", 0, None),
        ):
            row = Anime.objects.get(user=other, item=item)
            self.assertEqual(row.status, status)
            self.assertEqual(row.progress, count)
            self.assertEqual(str(row.score) if row.score is not None else None, score)


class OtherPeoplesListsTests(AnimeTestCase):
    """AC-5, AC-6: a list says where a title stands, not what a hidden log said."""

    def setUp(self):
        super().setUp()
        self.stranger = make_user()
        self.other_api = APIClient()
        self.other_api.force_authenticate(self.stranger)
        self.item = self.title(total=12)

    def listed(self, api=None):
        response = (api or self.other_api).get(
            f"/api/v1/users/{self.user.username}/tracking/",
            {"media_type": "anime"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"][0]["tracking"]

    def log(self, visibility):
        anime_tracking.create_log(self.user, self.item, consumed_at=date(2026, 10, 20), rating=8, visibility=visibility)

    def assert_hides_the_log(self, tracking):
        self.assertIsNone(tracking["rating"])
        self.assertIsNone(tracking["start_date"])
        self.assertIsNone(tracking["end_date"])
        self.assertIsNone(tracking["anime"]["last_watched_on"])
        self.assertEqual(tracking["status"], "Completed")
        self.assertEqual(tracking["anime"]["watched_count"], 12)
        for key in ("liked", "diary_count", "rating_source_diary_entry_id", "like_source_diary_entry_id"):
            self.assertNotIn(key, tracking)

    def test_ac_5_a_followers_only_log_says_nothing_to_a_stranger(self):
        self.log("followers")
        self.assert_hides_the_log(self.listed())

    def test_ac_5_a_private_log_says_nothing_to_a_stranger_or_a_follower(self):
        self.log("private")
        self.assert_hides_the_log(self.listed())
        Follow.objects.create(from_user=self.stranger, to_user=self.user, status=FollowStatus.ACCEPTED)
        self.assert_hides_the_log(self.listed())

    def test_ac_5_a_follower_sees_a_followers_only_log(self):
        self.log("followers")
        Follow.objects.create(from_user=self.stranger, to_user=self.user, status=FollowStatus.ACCEPTED)
        tracking = self.listed()
        self.assertIsNotNone(tracking["rating"])
        self.assertEqual(tracking["anime"]["last_watched_on"], "2026-10-20")

    def test_ac_5_anyone_sees_a_public_log(self):
        self.log("public")
        tracking = self.listed()
        self.assertIsNotNone(tracking["rating"])
        self.assertIsNotNone(tracking["end_date"])
        self.assertEqual(tracking["anime"]["last_watched_on"], "2026-10-20")

    def test_ac_5_the_owner_still_sees_everything(self):
        self.log("private")
        tracking = self.listed(self.api)
        self.assertIsNotNone(tracking["rating"])
        self.assertIsNotNone(tracking["end_date"])
        self.assertEqual(tracking["anime"]["last_watched_on"], "2026-10-20")

    def test_ac_5_ticks_stay_visible_with_no_hidden_log(self):
        self.tick(self.item, 1)
        tracking = self.listed()
        self.assertIsNotNone(tracking["start_date"])
        self.assertEqual(tracking["anime"]["last_watched_on"], "2026-10-25")


class RoutesTests(AnimeTestCase):
    """AN-000: TV's route shapes with no season. AN-701, AN-702, AN-900."""

    def test_an_702_the_detail_payload_has_state_and_rows(self):
        item = self.title(total=3, episodes={1: ("One", None)})
        self.tick(item, 1)
        response = self.api.get(f"/api/v1/media/mal/anime/{item.media_id}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user_state"]["anime"]["up_next"]["episode_number"], 2)
        self.assertEqual(response.data["anime_rows"]["episodes"][0]["t"], "One")
        self.assertEqual(response.data["anime_rows"]["count"], 3)

    def test_an_702_the_detail_payload_for_a_title_not_tracked_still_has_them(self):
        item = self.title(total=3)
        response = self.api.get(f"/api/v1/media/mal/anime/{item.media_id}/")
        self.assertEqual(response.data["user_state"]["anime"]["in_library"], False)
        self.assertEqual(response.data["user_state"]["anime"]["opinion_action"], "mark")
        self.assertEqual(len(response.data["anime_rows"]["episodes"]), 3)

    def test_an_702_signed_out_has_no_state_and_no_rows(self):
        item = self.title(total=3)
        self.api.force_authenticate(None)
        response = self.api.get(f"/api/v1/media/mal/anime/{item.media_id}/")
        self.assertNotIn("anime_rows", response.data)

    def test_an_701_the_rows_route_never_waits_on_anilist(self):
        item = self.title(total=2)
        self.fake.fail()
        data = self.get_rows(item)
        self.assertEqual(data["rows"]["episodes"], [{"n": 1, "a": 1}])
        self.assertEqual(data["anime"]["in_library"], False)

    def test_an_701_in_progress_lists_watching_with_an_aired_up_next(self):
        watching = self.title(total=3, episodes={2: ("Two", "https://example.com/2.jpg")})
        self.tick(watching, 1)
        finished = self.title(total=1)
        anime_tracking.mark_watched(self.user, finished)
        caught_up = self.airing(aired=1)
        self.tick(caught_up, 1)
        paused = self.title(total=3)
        self.tick(paused, 1)
        anime_tracking.choose(self.user, paused, "Paused")
        response = self.api.get("/api/v1/tracking/", {"media_type": "anime", "continue_watching": "true"})
        results = response.data["results"]
        self.assertEqual([result["media"]["ref"]["media_id"] for result in results], [watching.media_id])
        # The app sends In progress too, for a server older than this one to read.
        with_status = self.api.get(
            "/api/v1/tracking/",
            {"media_type": "anime", "continue_watching": "true", "status": "In progress"},
        )
        self.assertEqual([result["media"]["ref"]["media_id"] for result in with_status.data["results"]], [watching.media_id])
        up_next = results[0]["tracking"]["anime"]["up_next"]
        self.assertEqual((up_next["episode_number"], up_next["title"], up_next["image_url"]), (2, "Two", "https://example.com/2.jpg"))

    def test_an_701_most_recently_watched_first(self):
        first = self.title(total=3)
        second = self.title(total=3)
        self.tick(first, 1)
        self.clock.advance(minutes=1)
        self.tick(second, 1)
        self.clock.advance(minutes=1)
        self.tick(first, 2)
        response = self.api.get("/api/v1/tracking/", {"media_type": "anime", "continue_watching": "true"})
        self.assertEqual(
            [result["media"]["ref"]["media_id"] for result in response.data["results"]],
            [first.media_id, second.media_id],
        )

    def test_routes_an_unknown_action_is_a_404(self):
        self.action(self.title(), "dance", expect=404)

    def test_routes_tick_needs_its_number(self):
        response = self.action(self.title(), "tick", expect=400)
        self.assertEqual(response.data["error"]["code"], "validation_error")

    def test_routes_a_refused_tick_is_a_400_naming_the_total(self):
        response = self.action(self.title(total=3), "tick", expect=400, episode_number=4)
        self.assertEqual(response.data["error"]["message"], "This anime has 3 episodes.")

    def test_routes_an_absurd_episode_number_is_a_clean_400(self):
        item = self.title(total=None)
        self.action(item, "tick", episode_number=3000000000, expect=400)
        self.action(item, "mark_up_to", episode_number=30000, expect=400)
        self.action(item, "untick", episode_number=3000000000, expect=400)
        self.write(item, progress=2000000000, expect=400)
        self.assertIsNone(self.row(item))

    def test_routes_a_single_refuses_watching_pause_and_drop_with_a_reason(self):
        item = self.title(total=1)
        for name in ("watching", "resume", "pause", "drop"):
            response = self.action(item, name, expect=409)
            self.assertEqual(response.data["error"]["message"], anime_tracking.MSG_SINGLE_CHOICE)
        self.assertIsNone(self.row(item))

    def test_routes_details_unavailable_is_a_retryable_503(self):
        item = self.title()
        self.fake.fail()
        response = self.action(item, "tick", expect=503, episode_number=1)
        self.assertEqual(response.data["error"]["code"], "provider_unavailable")
        self.assertEqual(response.data["error"]["message"], "Anime details are unavailable right now")

    def test_routes_a_title_with_no_item_and_mal_timing_out_is_a_retryable_503(self):
        url = "/api/v1/tracking/mal/anime/424242/"
        log = {"ref": {"source": "mal", "media_type": "anime", "media_id": "424242"}, "consumed_at": "2026-10-25"}
        for failure in (requests.exceptions.Timeout, requests.exceptions.ConnectionError, ValueError):
            with mock.patch.object(mal, "anime", side_effect=failure), self.subTest(failure=failure.__name__):
                for response in (
                    self.api.post(f"{url}actions/tick/", {"episode_number": 1}, format="json"),
                    self.api.post("/api/v1/diary/", log, format="json"),
                    self.api.get(f"{url}rows/"),
                ):
                    self.assertEqual(response.status_code, 503, getattr(response, "data", None))
                    self.assertEqual(response.data["error"]["code"], "provider_unavailable")
        self.assertFalse(Item.objects.filter(media_id="424242").exists())

    def test_routes_a_stale_version_says_the_new_count(self):
        item = self.title(total=2)
        self.action(item, "tick", episode_number=1)
        self.action(item, "tick", episode_number=2)
        response = self.action(item, "unmark", expect=409, confirm=True, expected_version=1)
        self.assertEqual(response.data["error"]["code"], "tv_state_changed")
        self.assertEqual(response.data["error"]["tv"]["count"], 2)

    def test_routes_every_write_answers_the_envelope(self):
        item = self.title(total=2)
        response = self.action(item, "tick", episode_number=1)
        for key in ("tracking", "anime", "row", "rows", "changed", "finished", "earlier_unwatched"):
            self.assertIn(key, response.data)
        self.assertEqual(response.data["row"], {"n": 1, "a": 1, "w": 1, "u": self.viewings(item)[0].pk})
        self.assertIsNone(response.data["rows"])

    def test_routes_a_like_answers_state_and_marks(self):
        item = self.title(total=2)
        response = self.post_like(item)
        self.assertTrue(response.data["liked"])
        self.assertEqual(response.data["anime"]["status"], "Completed")
        self.assertEqual(response.data["media"]["user_state"]["anime"]["status"], "Completed")
        off = self.delete_like(item)
        self.assertFalse(off.data["liked"])
        self.assertEqual(self.row(item).status, "Completed")

    def test_routes_removal_needs_a_mutation_id_from_the_new_build(self):
        item = self.title(total=2)
        self.action(item, "tick", episode_number=1)
        self.action(item, "remove", expect=400)
        self.action(item, "remove", mutation_id=str(uuid4()))
        self.assertIsNone(self.row(item))

    def test_routes_rewatch_and_stop_answer(self):
        item = self.title(total=1)
        self.action(item, "rewatch", expect=409)
        other = self.title(total=2)
        self.action(other, "mark_watched")
        self.assertEqual(self.action(other, "rewatch").data["anime"]["rewatch"]["pass_number"], 2)
        self.assertIsNone(self.action(other, "stop_rewatch").data["anime"]["rewatch"])

    def test_routes_log_edit_and_delete(self):
        item = self.title(total=2)
        logged = self.post_log(item, rating=8, mutation_id=str(uuid4()))
        self.assertEqual(logged.data["consumed_at"], datetime(2026, 10, 25, tzinfo=UTC))
        edited = self.api.patch(f"/api/v1/diary/{logged.data['id']}/", {"consumed_at": "2026-10-20"}, format="json")
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data["anime"]["status"], "Completed")
        self.assertEqual(self.api.delete(f"/api/v1/diary/{logged.data['id']}/").status_code, 204)
        self.assertIsNone(self.row(item))

    def test_routes_a_future_log_is_a_400(self):
        item = self.title(total=2)
        response = self.post_log(item, expect=400, consumed_at="2027-01-01")
        self.assertEqual(response.data["error"]["message"], "Log dates can't be in the future.")

    def test_routes_star_scale_requests_rate_in_stars(self):
        item = self.title(total=2)
        response = self.api.post(
            self.url(item, "actions/rate/"),
            {"rating": 4},
            format="json",
            HTTP_X_SPINE_RATING_SCALE="stars",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(str(self.row(item).score), "8.0")
        self.assertEqual(response.data["tracking"]["rating"], "4.0")

    def test_non_anime_payloads_have_no_anime_keys(self):
        tv = Item.objects.create(source="manual", media_type="movie", media_id="m1", title="A film", image="https://example.com/p.jpg")
        response = self.api.get(f"/api/v1/media/manual/movie/{tv.media_id}/")
        self.assertNotIn("anime_rows", response.data)
        self.assertNotIn("anime", response.data.get("user_state") or {})


class InstalledBuildTests(AnimeTestCase):
    """AN-900: what installed builds send works; what they read keeps its meaning."""

    def test_an_900_consume_pause_resume_and_drop_keep_a_top_level_tracking_state(self):
        item = self.title(total=2)
        for name, status_after in (("pause", "Paused"), ("resume", "In progress"), ("drop", "Dropped"), ("consume", "Completed")):
            response = self.action(item, name)
            self.assertEqual(response.data["status"], status_after, name)
            self.assertIn("progress", response.data)
            self.assertIn("tracking_id", response.data)

    def test_an_900_the_keys_installed_builds_read_are_all_there(self):
        item = self.title(total=3)
        data = self.write(item, status="In progress").data
        for key in ("tracking_id", "status", "rating", "progress", "repeats", "start_date", "end_date", "notes", "updated_at", "latest_progress_change"):
            self.assertIn(key, data)
        self.assertEqual(set(data["progress"]), {"kind", "value", "max", "unit"})
        self.assertEqual(data["progress"]["unit"], "episode")

    def test_an_900_a_patch_that_reaches_the_total_with_in_progress_is_a_200(self):
        item = self.title(total=12)
        response = self.write(item, status="In progress", progress=12)
        self.assertEqual((response.data["status"], response.data["progress"]["value"]), ("Completed", 12))
        self.assertEqual(self.row(item).progress, 12)

    def test_an_900_a_put_with_a_status_works(self):
        item = self.title(total=3)
        response = self.api.put(self.url(item), {"status": "Planning"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.row(item).status, "Planning")

    def test_an_900_tracking_removal_goes_through_these_rules(self):
        item = self.title(total=3)
        self.tick(item, 1)
        response = self.api.delete(self.url(item))
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.row(item))
        logged = self.title(total=3)
        self.log(logged)
        refused = self.api.delete(self.url(logged))
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.data["error"]["message"], "Delete this anime's logs first.")

    def test_an_900_a_log_ignores_auto_mark_consumed(self):
        item = self.title(total=3)
        self.post_log(item, consumed_at="2026-10-20T12:00:00Z", auto_mark_consumed=False)
        self.assertEqual(self.row(item).status, "Completed")
        self.assertEqual(self.row(item).progress, 3)

    def test_an_900_a_retried_log_from_them_can_duplicate_as_today(self):
        item = self.title(total=3)
        self.post_log(item, consumed_at="2026-10-20T12:00:00Z")
        self.post_log(item, consumed_at="2026-10-20T12:00:00Z")
        self.assertEqual(len(self.logs(item)), 2)
        self.assertEqual(len(self.viewings(item)), 3)

    def test_an_900_stars_on_a_partway_title_show_the_servers_reason(self):
        """Installed builds show error.message of a 409 (AN-900, TV-304)."""
        item = self.title(total=3)
        self.tick(item, 1)
        response = self.api.patch(self.url(item), {"rating": 4}, format="json", HTTP_X_SPINE_RATING_SCALE="stars")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["message"], "Log this first.")

    def test_an_900_their_progress_max_is_the_aired_count(self):
        item = self.airing(aired=3)
        response = self.write(item, progress=1)
        self.assertEqual(response.data["progress"]["max"], 3)
