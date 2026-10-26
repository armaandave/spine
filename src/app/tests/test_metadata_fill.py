"""The paced metadata fill (app.metadata_fill): selection, errors, pacing, chaining and the sweep.

Every provider entry point is patched: services.get_media_metadata and
hardcover.book_metadata_batch, or FakeTVProvider for TV. The clock and the sleep are
fakes, so pacing is exact and nothing waits.
"""

from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from app import metadata_fill
from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    ItemFilterFacet,
    MediaTypes,
    Movie,
    Season,
    Sources,
    TVListing,
)
from app.providers import quota, services
from app.tests.tv_fixtures import FakeTVProvider, make_show

# A whole hour, so every pacing window starts with the test.
START = 1_800_000_000.0
CURRENT = 2


class FakeClock:
    """The fill's clock; sleeping moves it on."""

    def __init__(self):
        self.now = START
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def provider_error(status_code, source=Sources.MAL.value):
    response = mock.Mock(status_code=status_code, text="provider says no")
    return services.ProviderAPIError(source, mock.Mock(response=response))


def movie_metadata(**extra):
    return {"release_date": "2001-11-16", "runtime": 152, "genres": [{"name": "Fantasy"}], **extra}


class FillTestCase(TestCase):
    """Users, Items, tracking rows and the patched clock."""

    counter = 0

    def setUp(self):
        metadata_fill.clear()
        quota.clear()
        cache.clear()
        self.clock = FakeClock()
        for name, fake in (("_now", self.clock.time), ("_sleep", self.clock.sleep)):
            patcher = mock.patch.object(metadata_fill, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user(username="filler", password="x")

    def item(self, media_type=MediaTypes.MOVIE.value, source=Sources.TMDB.value, *, track=True, **fields):
        FillTestCase.counter += 1
        item = Item.objects.create(
            media_id=fields.pop("media_id", str(70000 + FillTestCase.counter)),
            source=source,
            media_type=media_type,
            title=f"Title {FillTestCase.counter}",
            image=settings.IMG_NONE,
            **fields,
        )
        if track:
            self.track(item)
        return item

    def track(self, item, user=None):
        model = apps.get_model("app", item.media_type)
        # bulk_create: Media.save would look the Item up at its provider.
        return model.objects.bulk_create([model(item=item, user=user or self.user)])[0]

    def genre(self, item, *names, facet_type=ItemFilterFacet.FacetType.GENRE):
        ItemFilterFacet.objects.bulk_create(
            [ItemFilterFacet(item=item, facet_type=facet_type, value=name) for name in names],
        )

    def patch_metadata(self, side_effect):
        patcher = mock.patch.object(services, "get_media_metadata", side_effect=side_effect)
        fetch = patcher.start()
        self.addCleanup(patcher.stop)
        return fetch

    def patch_hardcover(self, side_effect):
        patcher = mock.patch("app.providers.hardcover.book_metadata_batch", side_effect=side_effect)
        batch = patcher.start()
        self.addCleanup(patcher.stop)
        return batch

    def patch_schedule(self):
        patcher = mock.patch.object(metadata_fill, "schedule_next_chunk", return_value=True)
        schedule = patcher.start()
        self.addCleanup(patcher.stop)
        return schedule

    def refresh(self, *items):
        for item in items:
            item.refresh_from_db()


class SelectionAndDryRunTests(FillTestCase):
    def test_dry_run_makes_no_provider_calls_and_counts_by_type_and_provider(self):
        now = timezone.now()
        self.item()  # a film to fetch
        self.item(source=Sources.MANUAL.value)  # manual: never filled
        self.item(track=False)  # nobody tracks or logged it
        logged = self.item(track=False)
        DiaryEntry.objects.bulk_create([DiaryEntry(item=logged, user=self.user, consumed_at=now)])
        comic = self.item(MediaTypes.COMIC.value, Sources.COMICVINE.value, release_year=2004)
        self.genre(comic, "Superhero")  # has what ComicVine gives: stamp only
        self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value, release_year=1997)  # no album length
        self.item(MediaTypes.ANIME.value, Sources.MAL.value, metadata_attempted_at=now - timedelta(days=8))
        self.item(MediaTypes.ANIME.value, Sources.MAL.value, metadata_attempted_at=now - timedelta(days=1))
        self.item(metadata_version=CURRENT)  # already current
        fetch = self.patch_metadata(AssertionError("no provider calls in a dry run"))
        batch = self.patch_hardcover(AssertionError("no provider calls in a dry run"))

        out = StringIO()
        call_command("fill_item_metadata", stdout=out)

        fetch.assert_not_called()
        batch.assert_not_called()
        report = metadata_fill.estimate()
        rows = {(row["media_type"], row["provider"]): row for row in report["rows"]}
        self.assertEqual(rows["movie", "tmdb"]["pending"], 2)
        self.assertEqual(rows["movie", "tmdb"]["calls"], 4)
        self.assertEqual(rows["comic", "comicvine"]["stamp_only"], 1)
        self.assertEqual(rows["comic", "comicvine"]["calls"], 0)
        self.assertEqual(rows["music", "musicbrainz"]["calls"], 5)
        self.assertEqual(rows["music", "musicbrainz"]["minutes"], round(5 * 3 / 60, 1))
        self.assertEqual(rows["anime", "mal"]["pending"], 1)
        self.assertEqual(rows["anime", "mal"]["failed_before"], 1)
        self.assertEqual(report["waiting_for_retry"], 1)
        self.assertEqual(report["new_series_members"], 1)  # two films without a series, roughly
        self.assertIsNone(report["status"])
        text = out.getvalue()
        self.assertIn("Dry run: no provider calls", text)
        self.assertIn("Waiting for a retry (failed in the last 7 days): 1", text)
        self.assertIn("musicbrainz", text)

    def test_dry_run_counts_tv_calls_per_show_eight_seasons_a_call(self):
        show = self.item(MediaTypes.TV.value, media_id="5000", track=False)
        tv = self.track(show)
        for number in range(1, 11):
            season = self.item(MediaTypes.SEASON.value, media_id="5000", season_number=number, track=False)
            Season.objects.bulk_create([Season(item=season, user=self.user, related_tv=tv)])
        self.patch_metadata(AssertionError("no provider calls in a dry run"))

        rows = {row["media_type"]: row for row in metadata_fill.estimate()["rows"]}

        self.assertEqual(rows["tv"]["calls"], 2)  # seasons 1-8, then 9-10
        self.assertEqual(rows["season"]["calls"], 0)
        self.assertEqual(rows["season"]["pending"], 10)
        self.assertEqual(show.metadata_version, 0)

    def test_manual_untracked_and_current_items_are_never_fetched(self):
        self.item(source=Sources.MANUAL.value)
        self.item(track=False)
        self.item(metadata_version=CURRENT)
        fetch = self.patch_metadata(AssertionError("nothing is pending"))

        totals = metadata_fill.fill_in_process()

        fetch.assert_not_called()
        self.assertEqual(totals["items"], 0)

    def test_user_and_type_narrow_the_selection(self):
        other = get_user_model().objects.create_user(username="other", password="x")
        mine = self.item()
        theirs = self.item(track=False)
        self.track(theirs, other)
        anime = self.item(MediaTypes.ANIME.value, Sources.MAL.value)

        self.assertEqual(set(metadata_fill.pending_items(user=self.user)), {mine, anime})
        self.assertEqual(set(metadata_fill.pending_items(media_types=["movie"])), {mine, theirs})


class ApplyTests(FillTestCase):
    def test_apply_fills_and_stamps_and_a_rerun_makes_no_calls(self):
        film = self.item()
        fetch = self.patch_metadata(lambda *args, **kwargs: movie_metadata())

        call_command("fill_item_metadata", "--apply", stdout=StringIO())

        fetch.assert_called_once_with(MediaTypes.MOVIE.value, film.media_id, Sources.TMDB.value)
        self.refresh(film)
        self.assertEqual(film.metadata_version, CURRENT)
        self.assertEqual(film.release_year, 2001)
        self.assertEqual(film.runtime_minutes, 152)

        fetch.reset_mock()
        fetch.side_effect = AssertionError("a filled Item is never fetched again")
        call_command("fill_item_metadata", "--apply", stdout=StringIO())
        fetch.assert_not_called()

    def test_expensive_providers_with_what_they_give_are_only_stamped(self):
        comic = self.item(MediaTypes.COMIC.value, Sources.COMICVINE.value, release_year=2004)
        self.genre(comic, "Superhero")
        album = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value, release_year=1997, runtime_minutes=53)
        self.genre(album, "Rock")
        themed = self.item(MediaTypes.GAME.value, Sources.IGDB.value, release_year=2017)
        self.genre(themed, "Adventure")
        self.genre(themed, "Open world", facet_type=ItemFilterFacet.FacetType.THEME)
        unthemed = self.item(MediaTypes.GAME.value, Sources.IGDB.value, release_year=2017)
        self.genre(unthemed, "Adventure")
        fetch = self.patch_metadata(lambda *args, **kwargs: {"genres": ["Adventure"], "themes": ["Fantasy"]})

        totals = metadata_fill.fill_in_process()

        # Only the game without themes is fetched: themes are new for games.
        fetch.assert_called_once_with(MediaTypes.GAME.value, unthemed.media_id, Sources.IGDB.value)
        self.assertEqual(totals["stamp_only"], 3)
        self.refresh(comic, album, themed, unthemed)
        self.assertEqual(
            [comic.metadata_version, album.metadata_version, themed.metadata_version, unthemed.metadata_version],
            [CURRENT] * 4,
        )

    def test_limit_stops_the_in_process_fill(self):
        films = [self.item() for _ in range(3)]
        fetch = self.patch_metadata(lambda *args, **kwargs: movie_metadata())

        call_command("fill_item_metadata", "--apply", "--limit", "2", stdout=StringIO())

        self.assertEqual(fetch.call_count, 2)
        self.refresh(*films)
        self.assertEqual([film.metadata_version for film in films], [CURRENT, CURRENT, 0])

    def test_an_album_without_a_length_waits_a_week(self):
        album = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value)
        self.patch_metadata(lambda *args, **kwargs: {"release_date": "1997-05-21", "genres": ["Rock"]})

        totals = metadata_fill.fill_in_process()

        self.assertEqual(totals["unstamped"], 1)
        self.refresh(album)
        self.assertEqual(album.metadata_version, 0)
        self.assertEqual(album.release_year, 1997)
        self.assertIsNotNone(album.metadata_attempted_at)
        self.assertFalse(metadata_fill.pending_items().exists())

    def test_tv_is_filled_by_show_with_one_call_for_its_seasons(self):
        with FakeTVProvider() as fake:
            show = make_show(fake, seasons={1: 3, 2: 2})
            tv = self.track(show)
            seasons = {}
            for number in (1, 2):
                seasons[number] = self.item(MediaTypes.SEASON.value, media_id=show.media_id, season_number=number, track=False)
                Season.objects.bulk_create([Season(item=seasons[number], user=self.user, related_tv=tv)])
            episode = self.item(
                MediaTypes.EPISODE.value,
                media_id=show.media_id,
                season_number=2,
                episode_number=2,
                track=False,
            )
            Episode.objects.bulk_create(
                [Episode(item=episode, related_season=Season.objects.get(item=seasons[2]))],
            )

            metadata_fill.fill_in_process()

            self.assertEqual(fake.season_requests(show.media_id), [(1, 2)])
            self.assertEqual(fake.count(), 1)
        self.refresh(show, seasons[1], seasons[2], episode)
        for item in (show, seasons[1], seasons[2], episode):
            self.assertEqual(item.metadata_version, CURRENT, item)
        self.assertEqual(show.release_year, 2020)
        self.assertEqual(seasons[2].release_year, 2021)
        self.assertEqual(seasons[2].runtime_minutes, 45)
        self.assertEqual(episode.release_date.isoformat(), "2021-01-12")
        self.assertEqual(episode.runtime_minutes, 45)
        listing = TVListing.objects.get(item=show)
        self.assertEqual(sorted(listing.seasons), ["1", "2"])
        self.assertEqual(len(listing.seasons["2"]["episodes"]), 2)

    def test_a_show_that_tmdb_cannot_serve_waits_with_all_its_items(self):
        with FakeTVProvider() as fake:
            show = make_show(fake, seasons={1: 2})
            tv = self.track(show)
            season = self.item(MediaTypes.SEASON.value, media_id=show.media_id, season_number=1, track=False)
            Season.objects.bulk_create([Season(item=season, user=self.user, related_tv=tv)])
            fake.fail(status_code=503)

            totals = metadata_fill.fill_in_process()

        self.assertEqual(totals["retry_later"], 2)
        self.refresh(show, season)
        for item in (show, season):
            self.assertEqual(item.metadata_version, 0)
            self.assertIsNotNone(item.metadata_attempted_at)
        self.assertFalse(TVListing.objects.filter(item=show).exists())

    def test_hardcover_books_are_fetched_in_one_batch_and_a_missing_one_is_stamped(self):
        found = self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value, media_id="101")
        missing = self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value, media_id="102")
        batch = self.patch_hardcover(
            lambda media_ids: {"101": {"release_date": "2004-03-01", "genres": ["Fantasy"]}},
        )
        self.patch_metadata(AssertionError("books go through the batch"))

        totals = metadata_fill.fill_in_process()

        batch.assert_called_once_with(["101", "102"])
        self.assertEqual((totals["filled"], totals["not_found"]), (1, 1))
        self.refresh(found, missing)
        self.assertEqual((found.metadata_version, found.release_year), (CURRENT, 2004))
        self.assertEqual(missing.metadata_version, CURRENT)

    def test_new_series_members_are_queued_for_ratings_once_on_commit(self):
        film = self.item()
        member = {
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "image": settings.IMG_NONE,
        }
        metadata = movie_metadata(
            details={
                "series_id": "1241",
                "series_source": Sources.TMDB.value,
                "series_media_type": MediaTypes.MOVIE.value,
                "series_name": "Collection",
            },
            related={
                "collection": [
                    {**member, "media_id": film.media_id, "title": "One"},
                    {**member, "media_id": "672", "title": "Two"},
                    {**member, "media_id": "673", "title": "Three"},
                ],
            },
        )
        self.patch_metadata(lambda *args, **kwargs: metadata)

        with (
            mock.patch("app.tasks.enqueue_external_rating_batches") as enqueue,
            mock.patch("app.signals.enrich_external_ratings") as enrich_one,
            self.captureOnCommitCallbacks(execute=True),
        ):
            next_state, _, run = metadata_fill.run_chunk(metadata_fill.initial_state())

        self.assertIsNone(next_state)
        new_ids = sorted(Item.objects.filter(media_id__in=["672", "673"]).values_list("id", flat=True))
        enqueue.assert_called_once_with(new_ids)
        enrich_one.delay.assert_not_called()
        self.assertEqual(run.counts["new_series_members"], 2)


class ErrorTests(FillTestCase):
    def test_classes(self):
        self.assertEqual(metadata_fill.classify(provider_error(404)), metadata_fill.STAMP)
        self.assertEqual(metadata_fill.classify(provider_error(400)), metadata_fill.STAMP)
        self.assertEqual(metadata_fill.classify(KeyError("x")), metadata_fill.STAMP)
        self.assertEqual(metadata_fill.classify(NotImplementedError()), metadata_fill.STAMP)
        for status in (401, 403, 429, 500, 503, None):
            self.assertEqual(metadata_fill.classify(provider_error(status)), metadata_fill.BLOCK, status)
        until = datetime(2026, 10, 27, tzinfo=UTC)
        self.assertEqual(metadata_fill.classify(quota.Deferred("hardcover", until, "spent")), metadata_fill.BLOCK)
        self.assertEqual(metadata_fill.classify(RuntimeError("bug")), metadata_fill.RETRY)

    def test_404_and_type_error_are_stamped_and_a_503_stops_only_that_provider(self):
        not_found = self.item(MediaTypes.ANIME.value, Sources.MAL.value)
        broken = self.item(MediaTypes.ANIME.value, Sources.MAL.value)
        down = self.item(MediaTypes.ANIME.value, Sources.MAL.value)
        later = self.item(MediaTypes.ANIME.value, Sources.MAL.value)
        film = self.item()
        answers = {
            not_found.media_id: provider_error(404),
            broken.media_id: TypeError("bad payload"),
            down.media_id: provider_error(503),
        }

        def fetch_metadata(media_type, media_id, source, *args, **kwargs):
            if media_id in answers:
                raise answers[media_id]
            return movie_metadata()

        fetch = self.patch_metadata(fetch_metadata)

        metadata_fill.fill_in_process()

        fetched = [call.args[1] for call in fetch.call_args_list]
        self.assertEqual(fetched, [not_found.media_id, broken.media_id, down.media_id, film.media_id])
        self.refresh(not_found, broken, down, later, film)
        self.assertEqual((not_found.metadata_version, broken.metadata_version), (CURRENT, CURRENT))
        self.assertEqual(down.metadata_version, 0)
        self.assertIsNotNone(down.metadata_attempted_at)
        # MAL stopped for the run: the next anime waits for the next run, untouched.
        self.assertEqual(later.metadata_version, 0)
        self.assertIsNone(later.metadata_attempted_at)
        self.assertEqual(film.metadata_version, CURRENT)

    def test_deferred_stops_the_provider_without_requeueing(self):
        books = [self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value, media_id=str(200 + n)) for n in range(2)]
        film = self.item()
        until = datetime(2026, 10, 27, tzinfo=UTC)
        self.patch_hardcover(quota.Deferred("hardcover", until, "daily budget is spent"))
        self.patch_metadata(lambda *args, **kwargs: movie_metadata())
        schedule = self.patch_schedule()

        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.chain_chunk()

        schedule.assert_not_called()
        self.refresh(film, *books)
        self.assertEqual(film.metadata_version, CURRENT)
        for book in books:
            self.assertEqual(book.metadata_version, 0)
            self.assertIsNotNone(book.metadata_attempted_at)

    def test_the_fill_runs_inside_its_hardcover_and_open_library_budgets(self):
        self.item(MediaTypes.BOOK.value, Sources.HARDCOVER.value, media_id="301")
        scopes = []

        def batch(media_ids):
            scopes.append(quota._active.get())
            return {}

        self.patch_hardcover(batch)

        metadata_fill.fill_in_process()

        self.assertEqual(scopes[0].name, "metadata_fill")
        self.assertEqual(scopes[0].budgets, {"hardcover": 500, "openlibrary": 1000})


class PacingTests(FillTestCase):
    def test_sleep_spaces_albums_and_music_runs_last(self):
        first = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value)
        second = self.item(MediaTypes.MUSIC.value, Sources.MUSICBRAINZ.value)
        film = self.item()
        album = {"release_date": "1997-05-21", "runtime": 53, "genres": ["Rock"]}
        fetch = self.patch_metadata(
            lambda media_type, *args, **kwargs: album if media_type == MediaTypes.MUSIC.value else movie_metadata(),
        )

        metadata_fill.fill_in_process()

        self.assertEqual(
            [call.args[1] for call in fetch.call_args_list],
            [film.media_id, first.media_id, second.media_id],
        )
        self.assertEqual(self.clock.sleeps, [metadata_fill.MUSICBRAINZ_SECONDS_PER_CALL])

    def test_the_provider_cap_holds_across_two_task_runs(self):
        anime = [self.item(MediaTypes.ANIME.value, Sources.MAL.value) for _ in range(17)]
        fetch = self.patch_metadata(lambda *args, **kwargs: {"release_date": "2010-04-01", "genres": ["Action"]})
        schedule = self.patch_schedule()

        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.chain_chunk()
        self.assertEqual(fetch.call_count, 15)
        (state, countdown), _ = schedule.call_args
        self.assertEqual((state["pass"], countdown), (2, 0))

        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.chain_chunk(cursor=state["after"], state=state)

        # Still the same minute: the second run makes no MAL call and waits for the window.
        self.assertEqual(fetch.call_count, 15)
        (state, countdown), _ = schedule.call_args
        self.assertEqual(state["idle_passes"], 1)
        self.assertGreater(countdown, 0)
        self.assertLessEqual(countdown, 60)
        self.refresh(*anime)
        self.assertEqual(sum(item.metadata_version == CURRENT for item in anime), 15)

    def test_the_chain_stops_when_a_pass_makes_no_progress(self):
        comic = self.item(MediaTypes.COMIC.value, Sources.COMICVINE.value)
        metadata_fill._reserve(Sources.COMICVINE.value, 60)  # this hour's ComicVine calls are spent
        fetch = self.patch_metadata(AssertionError("ComicVine has no calls left this hour"))
        schedule = self.patch_schedule()

        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.chain_chunk()

        fetch.assert_not_called()
        schedule.assert_not_called()
        self.refresh(comic)
        self.assertEqual(comic.metadata_version, 0)
        self.assertIsNone(comic.metadata_attempted_at)

    def test_overall_cap_sleeps_briefly_between_calls(self):
        for _ in range(3):
            self.item()
        self.patch_metadata(lambda *args, **kwargs: movie_metadata())

        metadata_fill.fill_in_process()

        # Two calls a second at most; a film costs two.
        self.assertEqual(len(self.clock.sleeps), 2)
        self.assertTrue(all(0 < seconds <= 1 for seconds in self.clock.sleeps))


class ChainTests(FillTestCase):
    def patch_apply_async(self):
        patcher = mock.patch("app.tasks.fill_item_metadata.apply_async")
        apply_async = patcher.start()
        self.addCleanup(patcher.stop)
        return apply_async

    def test_the_queued_marker_lets_only_one_chunk_wait(self):
        apply_async = self.patch_apply_async()

        self.assertTrue(metadata_fill.start_chain())
        self.assertFalse(metadata_fill.start_chain())
        self.assertFalse(metadata_fill.schedule_next_chunk(metadata_fill.initial_state()))

        apply_async.assert_called_once()
        self.assertEqual(metadata_fill.chain_status(), "queued")

    def test_a_running_chunk_blocks_a_second_chain(self):
        apply_async = self.patch_apply_async()
        self.assertIsNotNone(metadata_fill._acquire_lock())

        self.assertFalse(metadata_fill.start_chain())

        apply_async.assert_not_called()
        self.assertEqual(metadata_fill.chain_status(), "running")

    def test_a_duplicate_message_is_dropped(self):
        self.item()
        fetch = self.patch_metadata(AssertionError("a duplicate never fills"))
        apply_async = self.patch_apply_async()
        metadata_fill.start_chain()
        token = apply_async.call_args.kwargs["kwargs"]["token"]

        metadata_fill.chain_chunk(token="someone-else")

        fetch.assert_not_called()
        self.assertEqual(metadata_fill._redis().get(metadata_fill._key("queued")).decode(), token)

    def test_a_chunk_waits_while_another_fill_holds_the_lock(self):
        self.item()
        fetch = self.patch_metadata(AssertionError("the lock is held"))
        schedule = self.patch_schedule()
        metadata_fill._acquire_lock()

        with self.captureOnCommitCallbacks(execute=True):
            metadata_fill.chain_chunk(cursor=0)

        fetch.assert_not_called()
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.args[1], metadata_fill.LOCK_RETRY_SECONDS)

    def test_the_task_consumes_its_marker_runs_and_releases_the_lock(self):
        film = self.item()
        self.patch_metadata(lambda *args, **kwargs: movie_metadata())
        apply_async = self.patch_apply_async()
        metadata_fill.start_chain()
        kwargs = apply_async.call_args.kwargs["kwargs"]

        with mock.patch.object(metadata_fill, "bump_stats_epoch") as bump:
            from app.tasks import fill_item_metadata  # noqa: PLC0415

            fill_item_metadata.run(**kwargs)

        film.refresh_from_db()
        self.assertEqual(film.metadata_version, CURRENT)
        self.assertIsNone(metadata_fill.chain_status())
        bump.assert_called_once_with()

    def test_queue_command(self):
        apply_async = self.patch_apply_async()
        with self.assertRaises(CommandError):
            call_command("fill_item_metadata", "--queue", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("fill_item_metadata", "--apply", "--queue", "--limit", "5", stdout=StringIO())

        out = StringIO()
        call_command("fill_item_metadata", "--apply", "--queue", stdout=out)
        call_command("fill_item_metadata", "--apply", "--queue", stdout=out)

        apply_async.assert_called_once()
        self.assertIn("Started the fill chain", out.getvalue())
        self.assertIn("Not started: a fill chunk is queued", out.getvalue())


class SweepTests(FillTestCase):
    def test_recent_items_are_filled_first_then_the_backlog_chain_starts(self):
        old = self.item()
        Movie.objects.filter(item=old).update(created_at=timezone.now() - timedelta(days=3))
        recent = self.item()
        fetch = self.patch_metadata(lambda *args, **kwargs: movie_metadata())
        schedule = self.patch_schedule()

        result = metadata_fill.sweep()

        self.assertEqual([call.args[1] for call in fetch.call_args_list], [recent.media_id])
        self.assertEqual(result, {"recent": 1, "chain_started": True})
        schedule.assert_called_once_with()
        self.refresh(old, recent)
        self.assertEqual((old.metadata_version, recent.metadata_version), (0, CURRENT))

    def test_recent_logs_count_too(self):
        logged = self.item(track=False)
        DiaryEntry.objects.bulk_create([DiaryEntry(item=logged, user=self.user, consumed_at=timezone.now())])
        self.patch_metadata(lambda *args, **kwargs: movie_metadata())
        self.patch_schedule()

        self.assertEqual(metadata_fill.sweep()["recent"], 1)

    def test_nothing_happens_when_nothing_is_pending(self):
        self.item(metadata_version=CURRENT)
        fetch = self.patch_metadata(AssertionError("nothing is pending"))
        schedule = self.patch_schedule()

        self.assertEqual(metadata_fill.sweep(), {"recent": 0, "chain_started": False})

        fetch.assert_not_called()
        schedule.assert_not_called()

    def test_a_running_chain_is_left_alone(self):
        self.item()
        fetch = self.patch_metadata(AssertionError("the chain holds the lock"))
        schedule = self.patch_schedule()
        metadata_fill._acquire_lock()

        self.assertEqual(metadata_fill.sweep(), {"recent": 0, "chain_started": False})

        fetch.assert_not_called()
        schedule.assert_not_called()

    def test_the_sweep_is_an_hourly_beat_entry(self):
        entry = settings.CELERY_BEAT_SCHEDULE["sweep_item_metadata"]
        self.assertEqual((entry["task"], entry["schedule"]), ("Sweep item metadata", 60 * 60))


class TrackingRowsTests(FillTestCase):
    def test_tv_rows_count_as_tracking(self):
        show = self.item(MediaTypes.TV.value, track=False)
        TV.objects.bulk_create([TV(item=show, user=self.user)])

        self.assertIn(show, metadata_fill.pending_items(user=self.user))
