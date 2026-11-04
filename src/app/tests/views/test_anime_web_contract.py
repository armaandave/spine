"""The web's forms, progress buttons, delete and log modal (AN-900, TV-207)."""

from datetime import UTC, date, datetime
from unittest import mock

from django.urls import reverse

from app import anime_tracking
from app.models import Anime, Item
from app.tests.anime_fixtures import AnimeTestCase


class AnimeWebContractTests(AnimeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def form(self, item, **fields):
        return {
            "media_id": item.media_id,
            "source": item.source,
            "media_type": "anime",
            "status": "In progress",
            "progress": 0,
            "score": "",
            "notes": "",
            **fields,
        }

    def save(self, item, **fields):
        return self.client.post(reverse("media_save"), self.form(item, **fields))

    def edit(self, row, operation):
        return self.client.post(
            reverse("progress_edit", kwargs={"media_type": "anime", "instance_id": row.pk}),
            {"operation": operation},
        )

    def test_an_100_a_manual_title_saved_completed_with_a_number_stays_completed(self):
        item = Item.objects.create(
            source="manual", media_type="anime", media_id="manual-1", title="Mine", image="https://example.com/p.jpg",
        )
        self.save(item, status="Completed", progress=12)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 12))
        self.client.post(reverse("media_save"), {**self.form(item, status="Completed", progress=12), "notes": "Mine"})
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.notes), ("Completed", 12, "Mine"))

    def test_an_900_the_form_means_its_actions(self):
        item = self.title(total=3)
        self.save(item, status="Completed")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 3))
        self.assertTrue(all(viewing.kind == "fill" for viewing in self.viewings(item)))

    def test_an_900_a_number_in_the_form_means_ticks(self):
        item = self.title(total=5)
        self.save(item, progress=2)
        self.assertEqual(self.watched(item), {1, 2})
        self.assertTrue(all(viewing.kind == "tick" for viewing in self.viewings(item)))

    def test_an_900_a_form_with_a_status_and_a_number_of_0_keeps_the_status(self):
        for status in ("Planning", "In progress", "Paused", "Dropped"):
            with self.subTest(status=status):
                item = self.title(total=5)
                self.tick(item, 1, 2)
                response = self.save(item, status=status, progress=0, instance_id=self.row(item).pk)
                self.assertLess(response.status_code, 400)
                row = self.row(item)
                self.assertEqual((row.status, row.choice, row.progress), (status, status, 0))
                self.assertEqual(self.viewings(item), [])

    def test_an_900_a_finished_titles_form_set_to_0_empties_it(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        self.assertEqual(self.row(item).status, "Completed")
        self.save(item, status="Completed", progress=0, instance_id=self.row(item).pk)
        self.assertIsNone(self.row(item))

    def test_an_900_the_progress_buttons_tick_and_untick(self):
        item = self.title(total=2)
        self.tick(item, 1)
        row = self.row(item)
        response = self.edit(row, "increase")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row(item).progress, 2)
        self.assertEqual(self.row(item).status, "Completed")
        self.clock.advance(minutes=1)
        self.edit(self.row(item), "decrease")
        self.assertEqual(self.row(item).progress, 1)
        self.assertEqual(self.row(item).status, "In progress")

    def test_an_900_the_progress_button_past_the_total_says_why(self):
        item = self.title(total=1)
        self.tick(item, 1)
        response = self.edit(self.row(item), "increase")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "This anime has 1 episode."})

    def test_an_900_lowering_is_refused_when_a_log_covers_it(self):
        item = self.title(total=2)
        self.log(item)
        response = self.edit(self.row(item), "decrease")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "Delete your log first."})

    def test_tv_304_a_form_setting_stars_partway_says_log_this_first(self):
        item = self.title(total=3)
        self.tick(item, 1)
        response = self.save(item, status="In progress", progress=1, score="8")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.row(item).score)

    def test_an_900_saving_notes_changes_nothing_else(self):
        item = self.title(total=3)
        self.tick(item, 1)
        self.save(item, status="In progress", progress=1, notes="Good so far")
        row = self.row(item)
        self.assertEqual((row.notes, row.progress), ("Good so far", 1))
        self.assertEqual(self.watched(item), {1})

    def test_tv_003_delete_keeps_the_log_guard(self):
        item = self.title(total=3)
        self.log(item)
        self.client.post(reverse("media_delete"), {"instance_id": self.row(item).pk, "media_type": "anime"})
        self.assertIsNotNone(self.row(item))
        other = self.title(total=3)
        self.tick(other, 1)
        self.client.post(reverse("media_delete"), {"instance_id": self.row(other).pk, "media_type": "anime"})
        self.assertIsNone(self.row(other))

    def test_tv_007_pause_resume_and_drop_go_through_the_module(self):
        item = self.title(total=3)
        self.tick(item, 1)
        for name, status in (("pause_media", "Paused"), ("resume_media", "In progress"), ("drop_media", "Dropped")):
            response = self.client.post(
                reverse(name, kwargs={"source": item.source, "media_type": "anime", "media_id": item.media_id}),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.row(item).status, status)
            self.assertEqual(self.row(item).progress, 1)

    def test_tv_405_the_webs_log_edit_goes_through_the_module(self):
        item = self.title(total=2)
        self.tick(item, 1)
        entry = self.log(item, rating=8).entry
        viewings = [(viewing.episode_number, viewing.watched_on) for viewing in self.viewings(item)]
        response = self.client.post(
            reverse("update_diary_entry", kwargs={"entry_id": entry.pk}),
            {"watch_date": "2026-10-20", "rating": "4", "review": "Fine"},
        )
        self.assertLess(response.status_code, 400, response.content)
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at.date(), date(2026, 10, 20))
        self.assertEqual(str(entry.rating), "8.0")
        self.assertEqual(entry.review, "Fine")
        self.assertEqual([(viewing.episode_number, viewing.watched_on) for viewing in self.viewings(item)], viewings)

    def test_tv_407_a_future_web_edit_is_refused(self):
        item = self.title(total=2)
        entry = self.log(item).entry
        response = self.client.post(
            reverse("update_diary_entry", kwargs={"entry_id": entry.pk}),
            {"watch_date": "2027-01-01"},
        )
        self.assertEqual(response.status_code, 400)

    def test_tv_406_the_webs_log_delete_goes_through_the_module(self):
        item = self.title(total=2)
        self.tick(item, 1)
        entry = self.log(item).entry
        response = self.client.post(reverse("delete_diary_entry", kwargs={"entry_id": entry.pk}))
        self.assertLess(response.status_code, 400, response.content)
        self.assertEqual(self.watched(item), {1})
        self.assertEqual(self.logs(item), [])

    def test_an_400_the_model_compatibility_routes_use_the_rules(self):
        item = self.title(total=3)
        self.tick(item, 1)
        self.row(item).mark_consumed()
        self.assertEqual(Anime.objects.get(item=item, user=self.user).status, "Completed")
        self.assertEqual(self.row(item).progress, 3)
        self.assertEqual(anime_tracking.state_payload(self.user, item)["eye"]["tap"], "unmark_marks")


class AnimeHistoryPageTests(AnimeTestCase):
    """The web's history page: one row per write, in order, in plain words."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def real_clock(self):
        # As production runs: each history row reads the clock when it's written.
        return mock.patch("django.utils.timezone.now", side_effect=lambda: datetime.now(UTC))

    def timeline(self, item):
        url = reverse("history_modal", kwargs={"source": item.source, "media_type": "anime", "media_id": item.media_id})
        response = self.client.get(f"{url}?return_url=/home")
        self.assertEqual(response.status_code, 200)
        return response.context["timeline"]

    def said(self, item):
        return [change["description"] for entry in self.timeline(item) for change in entry["changes"]]

    def test_an_400_history_opens_after_a_log_is_deleted(self):
        item = self.title(total=12)
        with self.real_clock():
            self.tick(item, 1)
            entry = self.log(item).entry
            anime_tracking.delete_log(self.user, entry)
            self.timeline(item)
            self.timeline(item)
        self.assertEqual(self.row(item).progress, 1)

    def test_an_400_each_history_row_of_a_write_has_its_own_moment_in_order(self):
        item = self.title(total=12)
        with self.real_clock():
            self.tick(item, 1)
            entry = self.log(item).entry
            anime_tracking.delete_log(self.user, entry)
        rows = list(self.row(item).history.all())
        dates = [row.history_date for row in rows]
        self.assertEqual(len(set(dates)), len(dates))
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual([row.history_id for row in rows], sorted((row.history_id for row in rows), reverse=True))

    def test_an_400_the_creation_row_is_dated_first(self):
        item = self.title(total=12)
        with self.real_clock():
            self.tick(item, 1)
        rows = list(self.row(item).history.all())
        self.assertEqual(rows[-1].history_type, "+")
        self.assertEqual(rows[-1].progress, 0)
        self.assertEqual(rows[0].progress, 1)
        with self.real_clock():
            newest = [change["description"] for change in self.timeline(item)[0]["changes"]]
        self.assertIn("Progress set to 1 episode", newest)
        self.assertFalse(any("Removed" in line or "0 episodes" in line for line in newest), newest)

    def test_an_400_rows_at_one_moment_dont_stop_the_page_and_each_shows(self):
        # An older row, or two writes in one microsecond: nothing here may depend on it.
        item = self.title(total=12)
        self.tick(item, 1)
        self.tick(item, 2)
        row = self.row(item)
        moment = row.history.first().history_date
        row.history.update(history_date=moment)
        said = " ".join(self.said(item))
        self.assertIn("Progress set to 1 episode", said)
        self.assertIn("Progress set to 2 episodes", said)

    def test_an_400_internal_fields_arent_shown(self):
        item = self.title(total=2)
        with self.real_clock():
            self.post_like(item)
            self.tick(item, 1)
            anime_tracking.set_rating(self.user, item, 8)
            anime_tracking.choose(self.user, item, "Paused")
            anime_tracking.choose(self.user, item, "In progress")
            entry = self.log(item, rating=9, liked=True).entry
            anime_tracking.start_rewatch(self.user, item)
            self.tick(item, 1)
            anime_tracking.stop_rewatch(self.user, item)
            anime_tracking.delete_log(self.user, entry)
            said = " ".join(self.said(item)).lower()
        for internal in (
            "earlier question",
            "like source",
            "rating source",
            "like is independent",
            "ended choice",
            "choice",
            "rewatch started",
            "pass number",
            "log offered",
        ):
            self.assertNotIn(internal, said)
        self.assertIn("rated", said)
