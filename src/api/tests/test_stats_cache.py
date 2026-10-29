"""The stats v2 response cache and its invalidation (STAT-006, PLAN.md 2.6, M2 test 15).

Every receiver and every explicit bump moves the owner's version, so the next request
misses; a version moves once per transaction, on commit; version keys never expire;
two followers share one cached answer and the owner has their own.

The test suite's cache is shared across tests, so the stats cache is off by default
under tests; these tests turn it on over a fresh LocMemCache.
"""

from datetime import UTC, date, datetime
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models.signals import post_delete, post_save
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.services import stats_cache, stats_v2_ratings
from api.services.stats_scope import build_scope
from api.views import stats as stats_views
from app import (
    book_tracking,
    metadata_fill,
    services,
    single_weight,
    tv_repair,
    tv_tracking,
)
from app.models import (
    TV,
    Book,
    BookSession,
    CustomBackdropPreference,
    CustomPosterPreference,
    DiaryEntry,
    DiaryEntryTag,
    Episode,
    Game,
    GameSession,
    Item,
    Media,
    MediaLike,
    MediaTypes,
    Movie,
    Sources,
    Status,
    Tag,
)
from app.tests.test_tv_tracking_viewings import TVTrackingTestCase
from integrations import tasks as import_tasks
from lists.models import CustomList, CustomListItem
from social.models import Follow, FollowStatus

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "stats-cache-tests"}}
ALL_TIME = {"start_date": "all", "end_date": "all"}
MAR_2025 = datetime(2025, 3, 2, tzinfo=UTC)
SUMMARY = "/api/v1/stats/me/summary/"


class VersionAssertions:
    """Read and compare an owner's stats version."""

    def version(self, user):
        return stats_cache._versions(user.pk)[1]

    def epoch(self):
        return stats_cache._versions(0)[0]

    def forget_pending_bumps(self):
        """Drop bumps queued by fixtures: a TestCase's transaction never commits."""
        setattr(transaction.get_connection(), stats_cache.PENDING_ATTR, (None, None))

    def assertBumps(self, user, write):  # noqa: N802
        """Run ``write``, commit, and assert the owner's version moved."""
        before = self.version(user)
        with self.captureOnCommitCallbacks(execute=True):
            write()
        self.assertGreater(self.version(user), before)

    def assertNoBump(self, user, write):  # noqa: N802
        before = self.version(user)
        with self.captureOnCommitCallbacks(execute=True):
            write()
        self.assertEqual(self.version(user), before)


@override_settings(CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class StatsCacheBase(VersionAssertions, TestCase):
    """An owner, a collaborator, two followers and a stranger; a cleared cache."""

    def setUp(self):
        super().setUp()
        cache.clear()
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.other = users.create_user(username="collaborator", password="strong-password-123")
        self.follower = users.create_user(username="follower", password="strong-password-123")
        self.follower_two = users.create_user(username="follower2", password="strong-password-123")
        self.stranger = users.create_user(username="stranger", password="strong-password-123")
        for follower in (self.follower, self.follower_two):
            Follow.objects.create(from_user=follower, to_user=self.owner, status=FollowStatus.ACCEPTED)
        self.movie_item = self.item(MediaTypes.MOVIE.value, "603")
        self.forget_pending_bumps()

    def item(self, media_type, media_id, source=Sources.TMDB.value):
        return Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            title=f"{media_type} {media_id}",
            image="https://example.com/poster.jpg",
        )

    def log(self, item=None, user=None):
        return DiaryEntry.objects.create(
            user=user or self.owner,
            item=item or self.movie_item,
            consumed_at=MAR_2025,
        )

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class ReceiverTests(StatsCacheBase):
    """Every post_save and post_delete receiver bumps the row's owner."""

    def test_a_log_saved_edited_and_deleted_bumps_its_owner(self):
        entry = None

        def create():
            nonlocal entry
            entry = self.log()

        self.assertBumps(self.owner, create)

        def edit():
            entry.rating = 8
            entry.save()

        self.assertBumps(self.owner, edit)
        self.assertBumps(self.owner, entry.delete)

    def test_a_log_tag_saved_and_deleted_bumps_the_logs_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            entry = self.log()
            tag = Tag.objects.create(name="rewatch")
        entry = DiaryEntry.objects.get(pk=entry.pk)  # the relation is not loaded
        link = None

        def create():
            nonlocal link
            link = DiaryEntryTag.objects.create(diary_entry_id=entry.pk, tag=tag)

        self.assertBumps(self.owner, create)
        self.assertBumps(self.owner, DiaryEntryTag.objects.get(pk=link.pk).delete)

    def tagged_log(self, count):
        with self.captureOnCommitCallbacks(execute=True):
            entry = self.log()
            for index in range(count):
                tag, _ = Tag.objects.get_or_create(name=f"tag {index}")
                DiaryEntryTag.objects.create(diary_entry=entry, tag=tag)
        return DiaryEntry.objects.get(pk=entry.pk)

    def queries_to(self, write):
        with self.captureOnCommitCallbacks(execute=True), CaptureQueriesContext(connection) as queries:
            write()
        return len(queries)

    def test_deleting_a_log_reads_no_parent_per_tag(self):
        # The cascade's tag rows are skipped; the log's own receiver bumps the owner.
        few = self.queries_to(self.tagged_log(1).delete)
        many = self.queries_to(self.tagged_log(6).delete)
        self.assertEqual(few, many)
        self.assertBumps(self.owner, self.tagged_log(3).delete)

    def test_clearing_a_logs_tags_reads_the_log_once(self):
        few = self.queries_to(DiaryEntryTag.objects.filter(diary_entry=self.tagged_log(1)).delete)
        many = self.queries_to(DiaryEntryTag.objects.filter(diary_entry=self.tagged_log(6)).delete)
        self.assertEqual(few, many)
        entry = self.tagged_log(3)
        self.assertBumps(self.owner, DiaryEntryTag.objects.filter(diary_entry=entry).delete)

    def test_a_poster_or_backdrop_pick_bumps_its_owner(self):
        for model in (CustomPosterPreference, CustomBackdropPreference):
            with self.subTest(model=model.__name__):
                self.assertBumps(
                    self.owner,
                    lambda model=model: model.objects.update_or_create(
                        user=self.owner,
                        item=self.movie_item,
                        defaults={"custom_image_url": "https://example.com/new.jpg"},
                    ),
                )
                self.assertBumps(self.owner, model.objects.filter(user=self.owner).delete)

    def test_a_like_saved_and_deleted_bumps_its_owner(self):
        self.assertBumps(self.owner, lambda: MediaLike.objects.create(user=self.owner, item=self.movie_item))
        self.assertBumps(self.owner, MediaLike.objects.filter(user=self.owner).delete)

    def test_game_and_book_sessions_bump_their_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            game = Game.objects.create(
                user=self.owner,
                item=self.item(MediaTypes.GAME.value, "1942", Sources.IGDB.value),
                status=Status.PLANNING.value,
            )
            book = Book.objects.create(
                user=self.owner,
                item=self.item(MediaTypes.BOOK.value, "OL1W", Sources.OPENLIBRARY.value),
                status=Status.IN_PROGRESS.value,
            )
        game = Game.objects.get(pk=game.pk)
        book = Book.objects.get(pk=book.pk)
        session = None

        def play():
            nonlocal session
            session = GameSession.objects.create(
                related_game_id=game.pk,
                status=Status.IN_PROGRESS.value,
                start_date=date(2025, 3, 2),
            )

        self.assertBumps(self.owner, play)
        self.assertBumps(self.owner, GameSession.objects.get(pk=session.pk).delete)
        self.assertBumps(
            self.owner,
            lambda: BookSession.objects.create(related_book_id=book.pk, status=Status.IN_PROGRESS.value),
        )

    def test_a_tracking_row_saved_and_deleted_bumps_its_owner(self):
        movie = None

        def track():
            nonlocal movie
            movie = Movie.objects.create(user=self.owner, item=self.movie_item, status=Status.COMPLETED.value)

        self.assertBumps(self.owner, track)
        self.assertBumps(self.owner, movie.delete)

    def test_every_media_subclass_has_both_receivers(self):
        models = [model for model in apps.get_models() if issubclass(model, Media)]
        self.assertGreaterEqual(len(models), 9)
        for model in models:
            for signal, suffix in ((post_save, "saved"), (post_delete, "deleted")):
                with self.subTest(model=model.__name__, signal=suffix):
                    receiver = self.stats_receiver(signal, model, suffix)
                    self.assertBumps(
                        self.owner,
                        lambda receiver=receiver, model=model: receiver(
                            sender=model,
                            instance=model(user_id=self.owner.pk),
                        ),
                    )

    def stats_receiver(self, signal, model, suffix):
        uid = f"stats_cache_{model._meta.label_lower}_{suffix}"
        receivers = [receiver for (key, _), receiver, _ in signal.receivers if key == uid]
        self.assertEqual(len(receivers), 1, uid)
        return receivers[0]

    def test_a_raw_fixture_save_bumps_nothing(self):
        self.assertNoBump(
            self.owner,
            lambda: self.stats_receiver(post_save, Movie, "saved")(
                sender=Movie,
                instance=Movie(user_id=self.owner.pk),
                raw=True,
            ),
        )

    def test_a_list_saved_and_deleted_bumps_its_owner(self):
        custom_list = None

        def create():
            nonlocal custom_list
            custom_list = CustomList.objects.create(name="Top", owner=self.owner)

        self.assertBumps(self.owner, create)
        self.assertBumps(self.owner, custom_list.delete)

    def test_a_reorder_bumps_the_owner_through_the_updated_at_touch(self):
        with self.captureOnCommitCallbacks(execute=True):
            custom_list = CustomList.objects.create(name="Top", owner=self.owner, is_ranked=True)
            other_item = self.item(MediaTypes.MOVIE.value, "604")
            CustomListItem.objects.create(custom_list=custom_list, item=self.movie_item, position=1)
            CustomListItem.objects.create(custom_list=custom_list, item=other_item, position=2)
        client = self.client_for(self.owner)

        def reorder():
            response = client.patch(
                f"/api/v1/lists/{custom_list.pk}/items/reorder/",
                {"item_ids": [other_item.pk, self.movie_item.pk]},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.content)

        self.assertBumps(self.owner, reorder)

    def test_a_collaborators_edit_bumps_the_lists_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            custom_list = CustomList.objects.create(name="Shared", owner=self.owner)
            custom_list.collaborators.add(self.other)
        collaborator_version = self.version(self.other)
        client = self.client_for(self.other)

        def rename():
            response = client.patch(f"/api/v1/lists/{custom_list.pk}/", {"name": "Renamed"}, format="json")
            self.assertEqual(response.status_code, 200, response.content)

        self.assertBumps(self.owner, rename)
        self.assertEqual(self.version(self.other), collaborator_version)

    def test_a_settings_change_bumps_the_user(self):
        def change():
            self.owner.calendar_timezone = "Pacific/Auckland"
            self.owner.save()

        self.assertBumps(self.owner, change)

    def test_a_write_bumps_only_its_owner(self):
        self.assertNoBump(self.follower, self.log)


def fail():
    """Roll the savepoint back."""
    raise IntegrityError


class TransactionTests(StatsCacheBase):
    """A version moves once per transaction, when it commits; never before."""

    def test_many_writes_in_one_transaction_queue_one_bump_per_owner(self):
        before = self.version(self.owner)
        with self.captureOnCommitCallbacks(execute=False) as callbacks, transaction.atomic():
            for media_id in ("1", "2", "3"):
                self.log(self.item(MediaTypes.MOVIE.value, media_id))
            MediaLike.objects.create(user=self.owner, item=self.movie_item)
            self.log(user=self.follower)
        bumps = [callback for callback in callbacks if getattr(callback, "__name__", "") == "bump_on_commit"]
        self.assertEqual(len(bumps), 2)  # the owner once, the follower once
        self.assertEqual(self.version(self.owner), before)  # not before the commit
        for callback in callbacks:
            callback()
        self.assertEqual(self.version(self.owner), before + 1)

    def test_a_rolled_back_savepoint_does_not_swallow_a_later_bump(self):
        before = self.version(self.owner)
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.log()
                fail()
            MediaLike.objects.create(user=self.owner, item=self.movie_item)
        self.assertEqual(self.version(self.owner), before + 1)

    def test_outside_a_transaction_the_bump_is_immediate(self):
        before = self.version(self.owner)
        with mock.patch.object(transaction.get_connection(), "in_atomic_block", new=False):
            stats_cache.bump_user(self.owner)
        self.assertEqual(self.version(self.owner), before + 1)


class VersionKeyTests(StatsCacheBase):
    """Version and epoch keys never expire; responses expire after an hour."""

    def test_locmem_version_and_epoch_keys_have_no_expiry(self):
        with self.captureOnCommitCallbacks(execute=True):
            stats_cache.bump_user(self.owner.pk)
            stats_cache.bump_epoch()
        for key in (stats_cache.user_version_key(self.owner.pk), stats_cache.EPOCH_KEY):
            with self.subTest(key=key):
                self.assertIsNone(cache._expire_info[cache.make_key(key)])

    def test_a_lost_version_key_comes_back_newer(self):
        before = self.version(self.owner)
        cache.delete(stats_cache.user_version_key(self.owner.pk))
        self.assertGreater(self.version(self.owner), before)

    def test_a_response_is_stored_for_an_hour(self):
        scope = build_scope(self.owner, self.owner, ALL_TIME)
        with mock.patch.object(stats_cache.cache, "set", wraps=cache.set) as stored:
            stats_cache.cached(scope, "summary", dict)
        self.assertEqual(stored.call_args.kwargs["timeout"], 3600)


@override_settings(STATS_CACHE_ENABLED=True)
class RedisVersionKeyTests(VersionAssertions, TestCase):
    """django-redis (fakeredis here, Redis in production) keeps version keys forever."""

    def test_redis_version_and_epoch_keys_have_no_ttl(self):
        user_pk = 987654321
        keys = (stats_cache.user_version_key(user_pk), stats_cache.EPOCH_KEY)
        self.addCleanup(cache.delete_many, list(keys))
        cache.delete_many(list(keys))
        with self.captureOnCommitCallbacks(execute=True):
            stats_cache.bump_user(user_pk)
            stats_cache.bump_epoch()
        for key in keys:
            with self.subTest(key=key):
                self.assertIsNotNone(cache.get(key))
                self.assertIsNone(cache.ttl(key))


class ExplicitBumpTests(StatsCacheBase):
    """Writes that send no signal bump the owner themselves."""

    def test_an_import_bumps_its_user_even_when_it_fails(self):
        def importer(identifier, user, mode, **kwargs):
            return {}, ""

        def failing(identifier, user, mode, **kwargs):
            raise RuntimeError

        with (
            mock.patch.object(import_tasks.events.tasks.reload_calendar, "delay"),
            mock.patch.object(import_tasks, "sweep_item_metadata"),
        ):
            self.assertBumps(self.owner, lambda: import_tasks.import_media(importer, "x", self.owner.pk, "new"))

            def fail():
                with self.assertRaises(RuntimeError):
                    import_tasks.import_media(failing, "x", self.owner.pk, "new")

            self.assertBumps(self.owner, fail)

    def test_a_like_copied_onto_logs_with_update_bumps_the_owner(self):
        anime = self.item(MediaTypes.ANIME.value, "1", Sources.MAL.value)
        with self.captureOnCommitCallbacks(execute=True):
            self.log(anime)
            MediaLike.objects.create(user=self.owner, item=anime)
        # The like exists already, so only the update() onto logs writes.
        self.assertBumps(self.owner, lambda: services.set_media_like(self.owner, anime, liked=True, audit=False))

    def test_a_single_weight_like_row_written_with_update_bumps_the_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            Movie.objects.create(user=self.owner, item=self.movie_item, status=Status.COMPLETED.value)
            MediaLike.objects.create(user=self.owner, item=self.movie_item)
        self.assertBumps(
            self.owner,
            lambda: single_weight._set_like_row(self.owner, self.movie_item, liked=True),
        )

    def test_a_spliced_reading_session_bumps_the_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            book = Book.objects.create(
                user=self.owner,
                item=self.item(MediaTypes.BOOK.value, "OL1W", Sources.OPENLIBRARY.value),
                status=Status.IN_PROGRESS.value,
            )
            session = BookSession.objects.create(related_book=book, status=Status.COMPLETED.value)
        self.assertBumps(self.owner, lambda: book_tracking._splice_session(book, session))

    def test_a_finished_fill_chain_bumps_the_epoch(self):
        before = self.epoch()
        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.finish_chain(0)
        self.assertEqual(self.epoch(), before)
        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.finish_chain(3)
        self.assertGreater(self.epoch(), before)

    def test_an_epoch_bump_misses_every_owner(self):
        scope = build_scope(self.owner, self.owner, ALL_TIME)
        builds = mock.Mock(return_value={"n": 1})
        stats_cache.cached(scope, "summary", builds)
        with self.captureOnCommitCallbacks(execute=True):
            stats_cache.bump_epoch()
        stats_cache.cached(scope, "summary", builds)
        self.assertEqual(builds.call_count, 2)


@override_settings(CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class TVBumpTests(VersionAssertions, TVTrackingTestCase):
    """TV writes bulk-create viewings and raw-delete them, so app.tv_tracking bumps."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.show = self.make_show({1: 3})
        with self.captureOnCommitCallbacks(execute=True):
            self.tick(self.show, 1, 1)
            self.tick(self.show, 1, 2)
        self.forget_pending_bumps()

    def test_a_tick_and_an_untick_bump_the_owner(self):
        self.assertBumps(self.user, lambda: self.tick(self.show, 1, 3))
        self.assertBumps(self.user, lambda: self.untick(self.show, 1, 3))

    def test_a_write_that_changes_nothing_bumps_nothing(self):
        self.assertNoBump(self.user, lambda: self.untick(self.show, 1, 3))

    def test_a_viewing_saved_or_deleted_outside_tv_tracking_bumps_the_owner(self):
        viewing = Episode.objects.filter(related_season__user=self.user).first()

        def move():
            viewing.watched_on = date(2026, 10, 1)
            viewing.save()

        self.assertBumps(self.user, move)
        self.assertBumps(self.user, Episode.objects.get(pk=viewing.pk).delete)

    def test_a_reconcile_that_changes_state_bumps_the_owner(self):
        tv = self.tv(self.show)
        TV.objects.filter(pk=tv.pk).update(watched_count=99)
        tv.refresh_from_db()

        def reconcile():
            with transaction.atomic():
                tv_tracking.reconcile_show(tv, entry_point=tv_tracking.JOB)

        self.assertBumps(self.user, reconcile)
        self.assertNoBump(self.user, reconcile)

    def test_filling_empty_stars_bumps_the_owner(self):
        with self.captureOnCommitCallbacks(execute=True):
            DiaryEntry.objects.create(user=self.user, item=self.show, consumed_at=MAR_2025, rating=8)
        tv = TV.objects.get(user=self.user, item=self.show)
        TV.objects.filter(pk=tv.pk).update(score=None, rating_source=None)
        TV.history.filter(id=tv.pk).update(score=None)
        filled = []
        self.assertBumps(self.user, lambda: filled.extend(tv_repair.fill_empty_stars(self.user, self.show)))
        self.assertEqual(len(filled), 1)
        self.assertNoBump(self.user, lambda: tv_repair.fill_empty_stars(self.user, self.show))

    def test_reconciling_imported_shows_bumps_the_owner(self):
        self.assertBumps(self.user, lambda: tv_tracking.reconcile_imported(self.user, [self.show]))


class ResponseCacheTests(StatsCacheBase):
    """One answer per audience, range, tab and zone, missed after a bump."""

    def summary(self, user, params=None, path=SUMMARY):
        response = self.client_for(user).get(path, {"schema": "2", **ALL_TIME, **(params or {})})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def counting_builds(self):
        patcher = mock.patch.object(stats_views, "build_stats_v2", wraps=stats_views.build_stats_v2)
        builds = patcher.start()
        self.addCleanup(patcher.stop)
        return builds

    def test_the_owners_summary_is_cached_until_the_owner_writes(self):
        builds = self.counting_builds()
        first = self.summary(self.owner)
        self.assertEqual(self.summary(self.owner), first)
        self.assertEqual(builds.call_count, 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.log()
        self.summary(self.owner)
        self.assertEqual(builds.call_count, 2)

    def test_two_followers_get_the_same_cached_answer_and_the_owner_their_own(self):
        builds = self.counting_builds()
        path = "/api/v1/users/owner/stats/summary/"
        follower_answer = self.summary(self.follower, path=path)
        self.assertEqual(self.summary(self.follower_two, path=path), follower_answer)
        self.assertEqual(builds.call_count, 1)
        self.assertEqual(follower_answer["audience"], "follower")
        self.assertEqual(self.summary(self.owner)["audience"], "owner")
        self.assertEqual(self.summary(self.stranger, path=path)["audience"], "public")
        self.assertEqual(builds.call_count, 3)

    def test_a_write_misses_for_every_viewer(self):
        builds = self.counting_builds()
        path = "/api/v1/users/owner/stats/summary/"
        self.summary(self.owner)
        self.summary(self.follower, path=path)
        with self.captureOnCommitCallbacks(execute=True):
            self.log()
        self.summary(self.owner)
        self.summary(self.follower, path=path)
        self.assertEqual(builds.call_count, 4)

    def test_range_tab_and_zone_each_get_their_own_answer(self):
        builds = self.counting_builds()
        self.summary(self.owner)
        self.summary(self.owner, {"start_date": "2025-01-01", "end_date": "2025-12-31"})
        self.summary(self.owner, {"media_type": "movie"})
        self.assertEqual(builds.call_count, 3)
        self.summary(self.owner, {"media_type": "movie"})
        self.assertEqual(builds.call_count, 3)
        # The owner's app zone is their calendar; storing it is a settings write.
        response = self.client_for(self.owner).get(
            SUMMARY,
            {"schema": "2", **ALL_TIME},
            HTTP_X_SPINE_TIMEZONE="Pacific/Auckland",
        )
        self.assertEqual(response.json()["range"]["timezone"], "Pacific/Auckland")
        self.assertEqual(builds.call_count, 4)

    def test_the_legacy_payload_is_never_cached(self):
        with mock.patch.object(stats_cache, "cached", wraps=stats_cache.cached) as cached:
            response = self.client_for(self.owner).get(SUMMARY, ALL_TIME)
        self.assertEqual(response.status_code, 200)
        cached.assert_not_called()

    def test_show_all_pages_are_cached_per_page_and_missed_after_a_write(self):
        with mock.patch.object(
            stats_v2_ratings,
            "highest_rated_page",
            return_value={"count": 60, "results": []},
        ) as pages:
            client = self.client_for(self.owner)
            path = "/api/v1/stats/me/highest-rated/"
            first = client.get(path, {"schema": "2", **ALL_TIME, "page": 2}).json()
            again = client.get(path, {"schema": "2", **ALL_TIME, "page": 2}).json()
            client.get(path, {"schema": "2", **ALL_TIME, "page": 1})
            self.assertEqual(pages.call_count, 2)
            self.assertEqual(again, first)
            self.assertIsNotNone(first["next"])
            with self.captureOnCommitCallbacks(execute=True):
                self.log()
            client.get(path, {"schema": "2", **ALL_TIME, "page": 2})
            self.assertEqual(pages.call_count, 3)

    def test_a_cache_failure_still_answers(self):
        builds = self.counting_builds()
        broken = mock.Mock(**{f"{name}.side_effect": ConnectionError for name in ("get", "get_many", "add", "incr", "set")})
        with mock.patch.object(stats_cache, "cache", broken):
            self.summary(self.owner)
            with self.captureOnCommitCallbacks(execute=True):
                self.log()  # a write never fails because of the cache
        self.assertEqual(builds.call_count, 1)
        broken.incr.assert_not_called()  # add failed first
        broken.add.assert_called()

    def test_a_cached_answer_costs_fewer_queries(self):
        self.summary(self.owner)
        with self.assertNumQueries(0):
            self.summary(self.owner)


class DisabledUnderTestsTests(TestCase):
    """Under tests the cache is off unless a test turns it on; elsewhere it is on."""

    def test_off_by_default_under_tests(self):
        self.assertFalse(stats_cache.enabled())

    @override_settings(TESTING=False)
    def test_on_outside_tests(self):
        self.assertTrue(stats_cache.enabled())
