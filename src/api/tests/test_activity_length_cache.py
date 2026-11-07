"""A length change moves everyone holding an anime, manga or comic (STAT-006).

Their numbers are worked out from the title's runtime or total when read, so with the
Stats cache on, a length that moves without a bump leaves Stats on the old number for an
hour.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from django.contrib.admin import site
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings

from api.services import stats_cache
from api.services.filters import update_item_filter_metadata
from api.services.item_metadata import store_series_total
from app import tv_tracking
from app.admin import ItemAdmin
from app.models import (
    Anime,
    Comic,
    DiaryEntry,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Music,
    Sources,
    Status,
)
from app.tests.tv_fixtures import make_manual_show, make_user

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "activity-length-cache"}}


@override_settings(TIME_ZONE="America/New_York", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class LengthBumpTests(TestCase):
    """Each length writer moves the version of everyone holding the title."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = make_user(calendar="America/New_York")
        self.other = make_user(calendar="America/New_York")
        # Creating the users queued bumps, in a transaction that never commits here.
        setattr(connection, stats_cache.PENDING_ATTR, (None, None))

    def version(self, user):
        return cache.get(stats_cache.user_version_key(user.pk))

    def title(self, media_type, **fields):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{Item.objects.count()}",
            title="Title",
            image="https://example.com/poster.jpg",
            **fields,
        )

    def hold(self, model, item):
        model.objects.bulk_create([model(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=3)])

    def assert_bumps(self, write, *, bumped=True):
        before = self.version(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            write()
        moved = self.version(self.owner) != before
        self.assertEqual(moved, bumped)

    def test_an_anime_runtime_that_really_changes_bumps_whoever_holds_it(self):
        item = self.title(MediaTypes.ANIME.value, runtime_minutes=24)
        self.hold(Anime, item)
        self.assert_bumps(lambda: update_item_filter_metadata(item, {"runtime": 30}))

    def test_the_same_runtime_bumps_no_one(self):
        item = self.title(MediaTypes.ANIME.value, runtime_minutes=24)
        self.hold(Anime, item)
        update_item_filter_metadata(item, {"runtime": 24})
        self.assert_bumps(lambda: update_item_filter_metadata(item, {"runtime": 24}), bumped=False)

    def test_someone_with_only_a_log_counts_as_holding_it(self):
        item = self.title(MediaTypes.ANIME.value, runtime_minutes=24)
        # On commit, or the bump the log queues hides the one this looks for.
        with mock.patch("app.signals.update_daily_statistics.delay"), self.captureOnCommitCallbacks(execute=True):
            DiaryEntry.objects.create(user=self.owner, item=item, consumed_at=datetime(2026, 2, 1, tzinfo=UTC))
        self.assert_bumps(lambda: update_item_filter_metadata(item, {"runtime": 25}))

    def test_a_film_or_album_runtime_that_really_changes_bumps_whoever_holds_it(self):
        for media_type, model in ((MediaTypes.MOVIE.value, Movie), (MediaTypes.MUSIC.value, Music)):
            with self.subTest(media_type=media_type):
                item = self.title(media_type, runtime_minutes=100)
                model.objects.bulk_create([model(user=self.owner, item=item, status=Status.COMPLETED.value, direct_consumption=True)])
                self.assert_bumps(lambda item=item: update_item_filter_metadata(item, {"runtime": 110}))

    def test_a_runtime_on_any_level_of_a_show_bumps_whoever_holds_the_show(self):
        """An episode falls back to its season's and show's runtime (STAT-201)."""
        show = make_manual_show(seasons={1: 2})
        with self.captureOnCommitCallbacks(execute=True):
            tv_tracking.tick(self.owner, show, 1, 1)
        for media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
            with self.subTest(media_type=media_type):
                item = Item.objects.filter(media_id=show.media_id, media_type=media_type).first()
                self.assert_bumps(lambda item=item: update_item_filter_metadata(item, {"runtime": 40 + Item.objects.count()}))

    def test_a_manga_or_comic_total_that_moves_bumps_whoever_holds_it(self):
        for media_type, model in ((MediaTypes.MANGA.value, Manga), (MediaTypes.COMIC.value, Comic)):
            with self.subTest(media_type=media_type):
                item = self.title(media_type, series_total=10)
                self.hold(model, item)
                self.assert_bumps(lambda item=item: store_series_total(item, {"max_progress": 11}))
                self.assert_bumps(lambda item=item: store_series_total(item, {"max_progress": 11}), bumped=False)

    def test_a_runtime_or_total_edited_in_admin_bumps_whoever_holds_it(self):
        anime = self.title(MediaTypes.ANIME.value, runtime_minutes=24)
        self.hold(Anime, anime)
        manga = self.title(MediaTypes.MANGA.value, series_total=10)
        self.hold(Manga, manga)
        admin_page = ItemAdmin(Item, site)
        for item, field, value in ((anime, "runtime_minutes", 30), (manga, "series_total", 12)):
            with self.subTest(field=field):
                setattr(item, field, value)
                self.assert_bumps(
                    lambda item=item, field=field: admin_page.save_model(
                        None,
                        item,
                        SimpleNamespace(changed_data=[field]),
                        change=True,
                    ),
                )
