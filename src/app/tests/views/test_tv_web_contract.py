"""The web reaches the same TV state as the app (docs/tv-tracking-contract.md, TV-207).

Each test drives the routed Django views with a session client that enforces CSRF,
and runs the same steps through app.tv_tracking with entry point APP, as the API's
endpoints do, for a second account; both must end in the same state. Full-page posts
and HTMX posts (HX-Request) are both exercised, and HTMX ones must return their
fragment (AGENTS.md section 6.3).

The server runs on America/New_York and both accounts' calendars are
America/Los_Angeles; the clock is patched through django.utils.timezone.now.
"""

from datetime import UTC, date, datetime
from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.contrib.messages import get_messages
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app import tv_tracking
from app.models import TV, DiaryEntry, Episode, Item, MediaTypes, Season, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from social.models import Activity

NOON_PACIFIC = datetime(2026, 10, 25, 12, 0)
OCT_25 = date(2026, 10, 25)
ACTIONS = "app/components/media_actions.html"
EPISODE_CARD = "app/components/media/episode_card.html"


def snapshot(user, show):
    """Return what both entry points must agree on for a user's show."""
    tv = TV.objects.filter(user=user, item=show).first()
    if tv is None:
        return None
    seasons = {
        season.item.season_number: (season.status, season.watched_count, season.aired_count)
        for season in Season.objects.filter(related_tv=tv, is_started=True).select_related("item")
    }
    viewings = sorted(
        (
            viewing.item.season_number,
            viewing.item.episode_number,
            viewing.kind,
            viewing.diary_entry_id is not None,
            viewing.watched_on,
        )
        for viewing in Episode.objects.filter(related_season__related_tv=tv).select_related("item")
    )
    return {
        "status": tv.status,
        "choice": tv.choice,
        "ended_choice": tv.ended_choice,
        "finished": tv.finished,
        "caught_up": tv.caught_up,
        "counts": (tv.watched_count, tv.aired_count),
        "up_next": (tv.up_next_season, tv.up_next_episode),
        "seasons": seasons,
        "viewings": viewings,
        "logs": DiaryEntry.objects.filter(
            user=user,
            item__media_id=show.media_id,
            item__media_type__in=tv_tracking.TV_TYPES,
        ).count(),
    }


@override_settings(TIME_ZONE="America/New_York")
class TVWebTestCase(TestCase):
    """A fake TMDB, a fixed clock and a CSRF-enforcing web client."""

    seasons = None

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(NOON_PACIFIC, tz="America/Los_Angeles").__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = make_user(calendar="America/Los_Angeles")
        self.twin = make_user(calendar="America/Los_Angeles")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.csrf = _get_new_csrf_string()
        self.client.cookies[settings.CSRF_COOKIE_NAME] = self.csrf
        self.show = make_show(self.fake, seasons=self.seasons or {1: 3, 2: 3}, title="Web Show")

    # Requests

    def post(self, name, *args, data=None, htmx=False):
        """POST a routed view as the browser does: a form field, or HTMX's header."""
        self.clock.advance(minutes=1)
        data = dict(data or {})
        headers = {}
        if htmx:
            headers = {"HX-Request": "true", "X-CSRFToken": self.csrf}
        else:
            data["csrfmiddlewaretoken"] = self.csrf
        return self.client.post(reverse(name, args=args), data, headers=headers)

    def tv_args(self, media_type=MediaTypes.TV.value):
        return (self.show.source, media_type, self.show.media_id)

    def episode_args(self, season, episode):
        return (self.show.source, MediaTypes.EPISODE.value, self.show.media_id, season, episode)

    def season_item(self, number):
        item, _ = Item.objects.get_or_create(
            media_id=self.show.media_id,
            source=self.show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            defaults={"title": self.show.title, "image": self.show.image},
        )
        return item

    def app(self, function, *args, **kwargs):
        """Run the same step through app.tv_tracking as the API does, for the twin."""
        self.clock.advance(minutes=1)
        return function(self.twin, self.show, *args, entry_point=tv_tracking.APP, **kwargs)

    # Assertions

    def assert_fragment(self, response, template=ACTIONS):
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTemplateUsed(response, template)

    def assert_same_state(self):
        self.assertEqual(snapshot(self.user, self.show), snapshot(self.twin, self.show))

    def tv(self, user=None):
        return TV.objects.filter(user=user or self.user, item=self.show).first()

    def viewings(self, user=None):
        return Episode.objects.filter(
            related_season__related_tv__user=user or self.user,
            related_season__related_tv__item=self.show,
        )

    def feed_verbs(self, user=None):
        return sorted(Activity.objects.filter(actor=user or self.user).values_list("verb", flat=True))


class TVWebEpisodeTests(TVWebTestCase):
    """Ticks and unticks from the season page's episode cards."""

    def test_tv_100_a_web_tick_matches_the_app_and_posts_nothing(self):
        response = self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        self.assert_fragment(response, EPISODE_CARD)
        self.app(tv_tracking.tick, 1, 1)

        self.assert_same_state()
        tv = self.tv()
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 2))
        self.assertEqual(self.viewings().get().watched_on, OCT_25)
        self.assertEqual(list(Season.objects.filter(related_tv=tv).values_list("item__season_number", flat=True)), [1])
        self.assertEqual(self.feed_verbs(), [])  # TV-703

    def test_tv_101_a_second_web_tick_records_nothing(self):
        self.assert_fragment(self.post("watch_episode", *self.episode_args(1, 1)), EPISODE_CARD)
        self.assert_fragment(self.post("watch_episode", *self.episode_args(1, 1), htmx=True), EPISODE_CARD)
        self.assertEqual(self.viewings().count(), 1)

    def test_tv_102_a_web_untick_removes_the_tick_and_the_show_leaves(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        response = self.post("unwatch_episode", *self.episode_args(1, 1), htmx=True)
        self.assert_fragment(response, EPISODE_CARD)
        self.app(tv_tracking.tick, 1, 1)
        self.app(tv_tracking.untick, 1, 1)

        self.assert_same_state()
        self.assertIsNone(self.tv())  # TV-208, A-03

    def test_tv_102_a_season_log_blocks_the_web_untick_with_409(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        tv_tracking.create_log(self.user, self.season_item(1), consumed_at=OCT_25)

        response = self.post("unwatch_episode", *self.episode_args(1, 1), htmx=True)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "Delete your Season 1 log first."})
        self.assertEqual(self.viewings().filter(item__episode_number=1).count(), 1)

    def test_tv_106_an_unlisted_episode_is_rejected_with_400(self):
        response = self.post("watch_episode", *self.episode_args(1, 9), htmx=True)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "This episode isn't listed for Season 1."})
        self.assertIsNone(self.tv())

    def test_tv_100_the_episode_form_dates_the_tick_with_the_typed_day(self):
        form = {
            "media_id": self.show.media_id,
            "source": self.show.source,
            "season_number": 1,
            "episode_number": 2,
            "end_date": "2026-10-20",
        }
        response = self.post("episode_save", data=form)
        self.assertEqual(response.status_code, 302)
        viewing = self.viewings().get()
        self.assertEqual(viewing.watched_on, date(2026, 10, 20))
        self.assertEqual(viewing.kind, Episode.Kind.TICK)
        self.assertFalse(Season.objects.filter(related_tv=self.tv(), item__season_number=2).exists())

        # A future day on the user's calendar is rejected and changes nothing (TV-106).
        self.post("episode_save", data={**form, "episode_number": 3, "end_date": "2026-10-26"})
        self.assertEqual(self.viewings().count(), 1)

    def test_tv_102_deleting_an_older_watch_from_the_episode_form_changes_nothing(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        first = self.viewings().get()
        self.clock.advance(minutes=1)
        # A second play of the episode, as an import records it (TV-800).
        second = Episode.objects.create(
            item=first.item,
            related_season=first.related_season,
            end_date=self.clock.moment,
            watched_on=OCT_25,
        )

        response = self.post("media_delete", data={"instance_id": first.pk, "media_type": "episode"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            ["Remove your most recent watch of this episode first."],
        )
        self.assertTrue(Episode.objects.filter(pk=first.pk).exists())
        self.post("media_delete", data={"instance_id": second.pk, "media_type": "episode"})
        self.assertEqual(list(self.viewings().values_list("pk", flat=True)), [first.pk])

    def test_tv_102_an_episode_logs_own_viewing_cant_be_deleted_from_the_form(self):
        episode_item = Item.objects.create(
            media_id=self.show.media_id,
            source=self.show.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            image=self.show.image,
        )
        tv_tracking.create_log(self.user, episode_item, consumed_at=OCT_25)
        logged = self.viewings().get()

        response = self.post("media_delete", data={"instance_id": logged.pk, "media_type": "episode"})

        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            ["Delete your log of this episode first."],
        )
        self.assertTrue(Episode.objects.filter(pk=logged.pk).exists())  # TV-400

    def test_tv_100_progress_buttons_tick_up_next_and_untick_the_latest(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        season = Season.objects.get(related_tv=self.tv(), item__season_number=1)

        response = self.post(
            "progress_edit", MediaTypes.SEASON.value, season.pk, data={"operation": "increase"}, htmx=True,
        )
        self.assert_fragment(response, "app/components/progress_changer.html")
        self.post("progress_edit", MediaTypes.SEASON.value, season.pk, data={"operation": "increase"})
        self.post("progress_edit", MediaTypes.SEASON.value, season.pk, data={"operation": "decrease"}, htmx=True)
        for episode in (1, 2, 3):
            self.app(tv_tracking.tick, 1, episode)
        self.app(tv_tracking.untick, 1, 3)

        self.assert_same_state()
        self.assertEqual((self.tv().up_next_season, self.tv().up_next_episode), (1, 3))

    def test_tv_102_a_progress_decrease_blocked_by_a_log_answers_409(self):
        tv_tracking.create_log(self.user, self.season_item(1), consumed_at=OCT_25)
        season = Season.objects.get(related_tv=self.tv(), item__season_number=1)

        response = self.post(
            "progress_edit", MediaTypes.SEASON.value, season.pk, data={"operation": "decrease"}, htmx=True,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.viewings().count(), 3)


class TVWebEyeTests(TVWebTestCase):
    """The show and season eyes on the web (TV-300, TV-302)."""

    def test_a_23_a_24_marking_and_unmarking_the_show_matches_the_app(self):
        response = self.post("mark_tv_watched", *self.tv_args(), htmx=True)
        self.assert_fragment(response)
        self.app(tv_tracking.mark_watched)
        self.assert_same_state()
        tv = self.tv()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(self.viewings().filter(kind=Episode.Kind.FILL, diary_entry=None).count(), 6)
        self.assertEqual(self.feed_verbs(), [])

        self.assert_fragment(self.post("unmark_tv_watched", *self.tv_args()))
        self.app(tv_tracking.unmark)
        self.assert_same_state()
        self.assertIsNone(self.tv())

    def test_tv_302_a_show_log_blocks_the_web_eye_with_409(self):
        tv_tracking.create_log(self.user, self.show, consumed_at=OCT_25)

        response = self.post("unmark_tv_watched", *self.tv_args(), htmx=True)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "Delete your show log first."})
        self.assertEqual(self.viewings().count(), 6)

    def test_tv_302_case_3_the_web_eye_unmarks_a_show_finished_by_ticking(self):
        for season, episode in ((1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)):
            self.post("watch_episode", *self.episode_args(season, episode), htmx=True)
            self.app(tv_tracking.tick, season, episode)
        self.assertEqual(self.tv().status, Status.COMPLETED.value)

        self.assert_fragment(self.post("unmark_tv_watched", *self.tv_args(), htmx=True))
        self.app(tv_tracking.unmark, confirm=True, mutation_id=uuid4())

        self.assert_same_state()
        self.assertIsNone(self.tv())

    def test_tv_300_season_eyes_match_the_app(self):
        self.assert_fragment(self.post("mark_season_watched", *self.tv_args(), 1, htmx=True))
        self.app(tv_tracking.mark_watched, season_number=1)
        self.assert_same_state()
        self.assertEqual(self.tv().status, Status.IN_PROGRESS.value)
        self.assertEqual((self.tv().up_next_season, self.tv().up_next_episode), (2, 1))
        self.assertFalse(Season.objects.filter(related_tv=self.tv(), item__season_number=2).exists())

        self.assert_fragment(self.post("unmark_season_watched", *self.tv_args(), 1))
        self.app(tv_tracking.unmark, season_number=1)
        self.assert_same_state()
        self.assertIsNone(self.tv())


class TVWebChoiceTests(TVWebTestCase):
    """Watching, Pause, Resume and Drop on the web (TV-005, TV-007, TV-204)."""

    def test_tv_005_start_watching_creates_no_season_tracking(self):
        self.assert_fragment(self.post("start_tracking_tv", *self.tv_args(), htmx=True))
        self.app(tv_tracking.choose, Status.IN_PROGRESS.value)
        self.assert_same_state()
        tv = self.tv()
        self.assertEqual((tv.status, tv.choice), (Status.IN_PROGRESS.value, Status.IN_PROGRESS.value))
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 1))
        self.assertFalse(Season.objects.filter(related_tv=tv).exists())  # TV-205

        response = self.post(
            "start_tracking_season", self.show.source, MediaTypes.SEASON.value, self.show.media_id, 2,
        )
        self.assert_fragment(response)
        self.assertFalse(Season.objects.filter(related_tv=tv).exists())
        self.assertFalse(self.viewings().exists())

    def test_tv_005_watching_is_unavailable_once_finished_with_409(self):
        tv_tracking.mark_watched(self.user, self.show)

        response = self.post("start_tracking_tv", *self.tv_args(), htmx=True)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "You've watched everything that has aired."})
        self.assertEqual(self.tv().status, Status.COMPLETED.value)

    def test_tv_007_pause_resume_and_drop_match_the_app(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        self.app(tv_tracking.tick, 1, 1)
        self.assert_fragment(self.post("pause_media", *self.tv_args(), htmx=True))
        self.app(tv_tracking.choose, Status.PAUSED.value)
        self.assert_same_state()
        self.assertEqual(self.tv().status, Status.PAUSED.value)

        self.assert_fragment(self.post("resume_media", *self.tv_args()))
        self.app(tv_tracking.choose, Status.IN_PROGRESS.value)
        self.assert_same_state()
        self.assertEqual(self.tv().status, Status.IN_PROGRESS.value)

        self.assert_fragment(self.post("drop_media", *self.tv_args(), htmx=True))
        self.app(tv_tracking.choose, Status.DROPPED.value)
        self.assert_same_state()
        self.assertEqual(self.tv().status, Status.DROPPED.value)
        self.assertEqual(self.viewings().count(), 1)  # progress is kept

    def test_a_54_dropping_a_finished_show_then_resuming_gives_completed(self):
        tv_tracking.mark_watched(self.user, self.show)
        self.post("drop_media", *self.tv_args(), htmx=True)
        self.assertEqual(self.tv().status, Status.DROPPED.value)

        self.post("resume_media", *self.tv_args(), htmx=True)

        self.assertEqual(self.tv().status, Status.COMPLETED.value)

    def test_tv_204_seasons_answer_pause_or_drop_the_show_instead(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        before = snapshot(self.user, self.show)
        for name in ("pause_media", "resume_media", "drop_media"):
            response = self.post(name, *self.tv_args(MediaTypes.SEASON.value), data={"season_number": 1}, htmx=True)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {"error": "Pause or drop the show instead."})
        self.assertEqual(snapshot(self.user, self.show), before)


class TVWebFormTests(TVWebTestCase):
    """The tracking form, deletes and manual entries (TV-003, TV-207)."""

    def save(self, **fields):
        data = {
            "media_id": self.show.media_id,
            "source": self.show.source,
            "media_type": MediaTypes.TV.value,
            "score": "",
            "notes": "",
            **fields,
        }
        return self.post("media_save", data=data)

    def test_tv_207_a_written_completed_marks_the_show_like_the_eye(self):
        response = self.save(status=Status.COMPLETED.value)
        self.assertEqual(response.status_code, 302)
        self.app(tv_tracking.mark_watched)
        self.assert_same_state()

    def test_tv_207_a_written_completed_season_marks_that_season(self):
        response = self.save(
            media_type=MediaTypes.SEASON.value,
            season_number="2",
            status=Status.COMPLETED.value,
        )
        self.assertEqual(response.status_code, 302)
        self.app(tv_tracking.mark_watched, season_number=2)
        self.assert_same_state()
        self.assertEqual(self.viewings().filter(item__season_number=2).count(), 3)

    def test_tv_207_a_written_planning_on_a_started_show_changes_nothing(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        tv = self.tv()
        before = snapshot(self.user, self.show)

        self.save(instance_id=tv.pk, status=Status.PLANNING.value)

        self.assertEqual(snapshot(self.user, self.show), before)  # TV-006

    def test_tv_207_saving_notes_never_reapplies_the_status(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        self.post("pause_media", *self.tv_args(), htmx=True)
        self.post("watch_episode", *self.episode_args(1, 2), htmx=True)
        tv = self.tv()
        self.assertEqual((tv.status, tv.ended_choice), (Status.IN_PROGRESS.value, Status.PAUSED.value))

        self.save(instance_id=tv.pk, status=tv.status, notes="Great so far")

        tv.refresh_from_db()
        self.assertEqual(tv.notes, "Great so far")
        self.assertEqual((tv.choice, tv.ended_choice), ("", Status.PAUSED.value))  # TV-208 intact

    def test_tv_303_stars_from_the_form_mark_an_unstarted_show(self):
        self.save(status=Status.PLANNING.value, score="8")
        self.app(tv_tracking.choose, Status.PLANNING.value)
        self.app(tv_tracking.set_rating, 8)
        self.assert_same_state()
        tv = self.tv()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(tv.score, 8)

    def test_a_57_a_written_season_dropped_is_ignored_on_the_web(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        season = Season.objects.get(related_tv=self.tv(), item__season_number=1)
        before = snapshot(self.user, self.show)

        response = self.post("media_save", data={
            "media_id": self.show.media_id,
            "source": self.show.source,
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "instance_id": season.pk,
            "status": Status.DROPPED.value,
            "score": "",
            "notes": "",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(snapshot(self.user, self.show), before)  # TV-204
        self.assertEqual(self.tv().status, Status.IN_PROGRESS.value)

    def test_tv_003_remove_is_blocked_by_logs_and_otherwise_matches_the_app(self):
        self.post("watch_episode", *self.episode_args(1, 1), htmx=True)
        log = tv_tracking.create_log(self.user, self.season_item(2), consumed_at=OCT_25).entry
        before = snapshot(self.user, self.show)

        response = self.post("media_delete", data={"instance_id": self.tv().pk, "media_type": "tv"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(snapshot(self.user, self.show), before)
        notes = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertEqual(notes, ["Delete this show's logs first."])

        tv_tracking.delete_log(self.user, log)
        self.post("media_delete", data={"instance_id": self.tv().pk, "media_type": "tv"})
        self.app(tv_tracking.tick, 1, 1)
        self.app(tv_tracking.remove, mutation_id=uuid4())
        self.assertIsNone(self.tv())
        self.assert_same_state()

    def test_tv_207_a_season_delete_is_blocked_by_its_log_and_otherwise_removes_it(self):
        for episode in (1, 2):
            self.post("watch_episode", *self.episode_args(1, episode), htmx=True)
        self.post("watch_episode", *self.episode_args(2, 1), htmx=True)
        season = Season.objects.get(related_tv=self.tv(), item__season_number=1)
        log = tv_tracking.create_log(self.user, self.season_item(1), consumed_at=OCT_25).entry

        self.post("media_delete", data={"instance_id": season.pk, "media_type": "season"})
        self.assertTrue(Season.objects.filter(pk=season.pk).exists())

        tv_tracking.delete_log(self.user, log)
        self.post("media_delete", data={"instance_id": season.pk, "media_type": "season"})

        self.assertFalse(Season.objects.filter(pk=season.pk).exists())
        self.assertEqual(list(self.viewings().values_list("item__season_number", flat=True)), [2])
        self.assertEqual(self.tv().status, Status.IN_PROGRESS.value)

    def test_csrf_is_enforced_on_tv_writes(self):
        self.clock.advance(minutes=1)
        response = self.client.post(reverse("mark_tv_watched", args=self.tv_args()))
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.tv())


class TVWebLogTests(TVWebTestCase):
    """Web logs: dates, one diary activity, no cascades (TV-400, TV-407, TV-703)."""

    def log(self, item, **fields):
        args = [item.source, item.media_type, item.media_id]
        if item.media_type == MediaTypes.SEASON.value:
            args.append(item.season_number)
        data = {"watch_date": "2026-10-25", "rating": "4", "mutation_id": str(uuid4()), **fields}
        return self.post("add_movie_diary_entry", *args, data=data)

    def test_tv_400_a_web_season_log_matches_the_app_and_posts_one_activity(self):
        mutation_id = str(uuid4())
        response = self.log(self.season_item(1), mutation_id=mutation_id)
        self.assertEqual(response.status_code, 200, response.content)
        # The log modal's retry applies at most once (TV-705).
        self.assertEqual(self.log(self.season_item(1), mutation_id=mutation_id).status_code, 200)
        self.clock.advance(minutes=1)
        tv_tracking.create_log(
            self.twin, self.season_item(1), consumed_at=OCT_25, rating=8, liked=False, is_rewatch=False,
        )

        self.assert_same_state()
        entry = DiaryEntry.objects.get(user=self.user)
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertEqual(entry.rating, 8)
        self.assertEqual(self.feed_verbs(), ["diary_created"])
        self.assertEqual(self.viewings().filter(diary_entry=entry, kind=Episode.Kind.FILL).count(), 3)
        self.assertEqual((self.tv().up_next_season, self.tv().up_next_episode), (2, 1))

    def test_tv_407_a_web_log_dated_tomorrow_on_the_users_calendar_is_rejected(self):
        # 11 p.m. Pacific is 2 a.m. the next day in New York.
        self.clock.set(datetime(2026, 10, 25, 23, 0), tz="America/Los_Angeles")

        response = self.log(self.show, watch_date="2026-10-26")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Log dates can't be in the future."})
        self.assertIsNone(self.tv())
        self.assertFalse(DiaryEntry.objects.filter(user=self.user).exists())

    def test_tv_405_editing_a_web_log_stores_the_plain_date(self):
        entry = tv_tracking.create_log(self.user, self.season_item(1), consumed_at=OCT_25).entry
        self.clock.advance(minutes=1)

        response = self.post("update_diary_entry", entry.pk, data={
            "watch_date": "2026-10-20", "rating": "5", "review": "", "liked": "false",
        })

        self.assertEqual(response.status_code, 200, response.content[:300])
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 20, tzinfo=UTC))
        self.assertEqual(entry.rating, 10)
        self.assertFalse(self.viewings().filter(watched_on__isnull=False).exists())

    def test_tv_407_editing_a_web_log_to_tomorrow_is_rejected(self):
        entry = tv_tracking.create_log(self.user, self.season_item(1), consumed_at=OCT_25).entry

        response = self.post("update_diary_entry", entry.pk, data={"watch_date": "2026-10-26"})

        self.assertEqual(response.status_code, 400)
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))


class TVWebLogModalTests(TVWebTestCase):
    """TV-404: the web log modal starts from the server's composer defaults.

    These open the show's modal: the season modal's template names a URL that no
    longer exists (add_season_diary_entry), which predates this contract.
    """

    def open_modal(self):
        response = self.client.get(
            reverse("log_modal", args=(self.show.source, MediaTypes.TV.value, self.show.media_id)),
        )
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_tv_404_a_show_watched_only_by_marks_defaults_rewatch_on(self):
        tv_tracking.mark_watched(self.user, self.show)
        context = self.open_modal()
        self.assertTrue(context["is_repeat"])
        self.assertEqual(context["today"], OCT_25)

    def test_tv_404_a_show_watched_by_ticks_defaults_rewatch_off(self):
        for episode in (1, 2, 3):
            self.clock.advance(minutes=1)
            tv_tracking.tick(self.user, self.show, 1, episode)
        self.assertFalse(self.open_modal()["is_repeat"])

    def test_tv_404_a_second_show_log_defaults_rewatch_on(self):
        self.assertFalse(self.open_modal()["is_repeat"])
        tv_tracking.create_log(self.user, self.show, consumed_at=OCT_25)
        self.assertTrue(self.open_modal()["is_repeat"])

    def test_tv_407_the_default_date_is_today_on_the_users_calendar(self):
        """11 p.m. Pacific is already the next day in New York; the modal keeps Oct 25."""
        tv_tracking.mark_watched(self.user, self.show)
        self.clock.advance(hours=11)
        self.assertEqual(self.open_modal()["today"], OCT_25)
