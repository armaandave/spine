"""Imports end in the state the app would produce (TV-800).

Imports bulk-write TV, season and episode rows as their sources describe them, then
integrations.imports.helpers works each imported show out once through
app.tv_tracking.reconcile_imported. These tests drive the Yamtrack CSV importer (new and
overwrite modes), Trakt's watch history and Simkl's lists against the shared fake TMDB.

The server runs on America/New_York and the account calendar is America/Los_Angeles.
"""

import csv
import io
from datetime import date, datetime
from unittest import mock

from django.test import TestCase, override_settings

from app import tv_tracking, user_calendar
from app.models import TV, DiaryEntry, Episode, Item, MediaTypes, Season, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from events import tv_airing
from integrations.imports import helpers, yamtrack
from integrations.imports.simkl import SimklImporter
from integrations.imports.trakt import TraktImporter
from social.models import Activity

NOON_PACIFIC = datetime(2026, 10, 25, 12, 0)
HEADER = [
    "media_id", "source", "media_type", "title", "image", "season_number",
    "episode_number", "score", "progress", "status", "start_date", "end_date",
    "notes", "progressed_at",
]


def show_snapshot(user, show):
    """Return a user's show as the contract describes it, without ids or times."""
    tv = TV.objects.filter(user=user, item=show).first()
    if tv is None:
        return None
    return {
        "status": tv.status,
        "choice": tv.choice,
        "finished": tv.finished,
        "score": tv.score,
        "up_next": (tv.up_next_season, tv.up_next_episode),
        "seasons": sorted(
            (season.item.season_number, season.status, season.is_started)
            for season in Season.objects.filter(related_tv=tv).select_related("item")
        ),
        "viewings": sorted(
            (
                viewing.item.season_number,
                viewing.item.episode_number,
                viewing.kind,
                viewing.diary_entry_id,
                viewing.watched_on,
            )
            for viewing in Episode.objects.filter(related_season__related_tv=tv).select_related("item")
        ),
    }


@override_settings(TIME_ZONE="America/New_York")
class TVImportContractTests(TestCase):
    """Yamtrack CSV imports, new and overwrite (TV-800, A-43, A-57)."""

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
        self.show = make_show(self.fake, seasons={1: 3, 2: 3}, title="Import Show")

    # CSV

    def row(self, media_type, *, show=None, season="", episode="", status="", score="", end_date=""):
        show = show or self.show
        return {
            "media_id": show.media_id,
            "source": show.source,
            "media_type": media_type,
            "title": show.title,
            "image": show.image,
            "season_number": season,
            "episode_number": episode,
            "score": score,
            "progress": "",
            "status": status,
            "start_date": "",
            "end_date": end_date,
            "notes": "",
            "progressed_at": "2024-02-09T12:00:00Z",
        }

    def import_csv(self, rows, mode="new"):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=HEADER, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
        self.clock.advance(minutes=1)
        counts, warnings = yamtrack.importer(io.BytesIO(buffer.getvalue().encode()), self.user, mode)
        self.assertEqual(warnings, "")
        return counts

    def tv(self, show=None):
        return TV.objects.filter(user=self.user, item=show or self.show).first()

    def viewings(self, show=None):
        return Episode.objects.filter(
            related_season__related_tv__user=self.user,
            related_season__related_tv__item=show or self.show,
        )

    # Tests

    def test_a_43_an_imported_completed_show_is_one_mark_and_reimporting_adds_nothing(self):
        rows = [self.row("tv", status=Status.COMPLETED.value)]
        self.import_csv(rows)

        tv = self.tv()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(tv.choice, "")
        fills = self.viewings()
        self.assertEqual(fills.count(), 6)
        self.assertTrue(all(viewing.kind == Episode.Kind.FILL for viewing in fills))
        self.assertTrue(all(viewing.diary_entry_id is None and viewing.watched_on is None for viewing in fills))
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())
        first = show_snapshot(self.user, self.show)

        self.import_csv(rows)
        self.assertEqual(show_snapshot(self.user, self.show), first)
        self.import_csv(rows, mode="overwrite")
        self.assertEqual(show_snapshot(self.user, self.show), first)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)

    def test_tv_800_an_imported_completed_season_is_a_mark_of_its_missing_episodes(self):
        self.import_csv([
            self.row("tv", status=Status.IN_PROGRESS.value),
            self.row("season", season=1, status=Status.COMPLETED.value),
            self.row("episode", season=1, episode=3, end_date="2024-02-09T20:00:00Z"),
        ])

        tv = self.tv()
        self.assertEqual((tv.status, tv.choice), (Status.IN_PROGRESS.value, Status.IN_PROGRESS.value))
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (2, 1))
        by_episode = {viewing.item.episode_number: viewing for viewing in self.viewings()}
        self.assertEqual(sorted(by_episode), [1, 2, 3])
        self.assertEqual(by_episode[1].kind, Episode.Kind.FILL)
        self.assertEqual(by_episode[3].kind, Episode.Kind.TICK)
        self.assertEqual(by_episode[3].watched_on, date(2024, 2, 9))
        season = Season.objects.get(related_tv=tv, item__season_number=1)
        self.assertEqual(season.status, Status.COMPLETED.value)

    def test_a_57_an_imported_season_dropped_under_an_in_progress_show_is_ignored(self):
        self.import_csv([
            self.row("tv", status=Status.IN_PROGRESS.value),
            self.row("season", season=1, status=Status.DROPPED.value),
            self.row("season", season=2, status=Status.IN_PROGRESS.value),
            self.row("episode", season=1, episode=1, end_date="2024-02-09T20:00:00Z"),
        ])

        tv = self.tv()
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        seasons = {
            season.item.season_number: season
            for season in Season.objects.filter(related_tv=tv).select_related("item")
        }
        self.assertEqual(seasons[1].status, Status.IN_PROGRESS.value)  # TV-204
        self.assertFalse(seasons[2].is_started)  # TV-205
        self.assertEqual(self.viewings().count(), 1)

    def test_tv_800_imported_planning_paused_and_dropped_become_the_choice(self):
        shows = {
            status: make_show(self.fake, seasons={1: 3}, title=f"{status} show")
            for status in (Status.PLANNING.value, Status.PAUSED.value, Status.DROPPED.value)
        }
        rows = []
        for status, show in shows.items():
            rows.append(self.row("tv", show=show, status=status))
            if status != Status.PLANNING.value:
                rows.append(self.row("season", show=show, season=1, status=status))
                rows.append(self.row("episode", show=show, season=1, episode=1, end_date="2024-02-09T20:00:00Z"))
        self.import_csv(rows)

        for status, show in shows.items():
            tv = self.tv(show)
            self.assertEqual((tv.status, tv.choice), (status, status))
        self.assertFalse(self.viewings(shows[Status.PLANNING.value]).exists())

    def test_tv_800_each_imported_play_is_its_own_viewing_dated_or_not(self):
        self.import_csv([
            self.row("tv", status=Status.IN_PROGRESS.value),
            self.row("season", season=1, status=Status.IN_PROGRESS.value),
            self.row("episode", season=1, episode=1, end_date="2024-02-09T20:00:00Z"),
            self.row("episode", season=1, episode=1, end_date="2024-03-09T20:00:00Z"),
            self.row("episode", season=1, episode=2, end_date=""),
        ])

        days = sorted(
            (viewing.item.episode_number, viewing.watched_on or date.min)
            for viewing in self.viewings()
        )
        self.assertEqual(days, [(1, date(2024, 2, 9)), (1, date(2024, 3, 9)), (2, date.min)])
        self.assertEqual(self.tv().watched_count, 2)  # TV-104 counts distinct episodes

    def test_tv_800_imported_dates_land_on_the_users_calendar(self):
        # 05:00 UTC on Feb 10 is 9 p.m. on Feb 9 in Los Angeles and midnight in New York.
        self.import_csv([
            self.row("tv", status=Status.IN_PROGRESS.value),
            self.row("season", season=1, status=Status.IN_PROGRESS.value),
            self.row("episode", season=1, episode=1, end_date="2024-02-10T05:00:00Z"),
        ])

        self.assertEqual(self.viewings().get().watched_on, date(2024, 2, 9))
        self.assertEqual(self.tv().last_watched_on, date(2024, 2, 9))

    def test_tv_107_an_import_starting_partway_marks_nothing_earlier(self):
        self.import_csv([
            self.row("tv", status=Status.IN_PROGRESS.value),
            self.row("season", season=2, status=Status.IN_PROGRESS.value),
            self.row("episode", season=2, episode=1, end_date="2024-02-09T20:00:00Z"),
        ])

        self.assertEqual(list(self.viewings().values_list("kind", flat=True)), [Episode.Kind.TICK])
        self.assertEqual((self.tv().up_next_season, self.tv().up_next_episode), (2, 2))
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())

    def test_tv_800_a_rating_only_completed_show_is_a_mark_that_keeps_its_stars(self):
        self.import_csv([self.row("tv", status=Status.COMPLETED.value, score="8.0")])

        tv = self.tv()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(tv.score, 8)
        self.assertEqual(self.viewings().filter(kind=Episode.Kind.FILL).count(), 6)

    def test_tv_800_overwrite_replaces_spine_marks_and_choices_and_rebuilds_logs(self):
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, self.show, season_number=1)
        season_2 = Item.objects.create(
            media_id=self.show.media_id,
            source=self.show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=2,
            title=self.show.title,
            image=self.show.image,
        )
        self.clock.advance(minutes=1)
        log = tv_tracking.create_log(self.user, season_2, consumed_at=user_calendar.today(self.user)).entry
        self.clock.advance(minutes=1)
        tv_tracking.choose(self.user, self.show, Status.PAUSED.value)
        self.assertEqual(self.viewings().count(), 6)

        self.import_csv(
            [
                self.row("tv", status=Status.IN_PROGRESS.value),
                self.row("season", season=1, status=Status.IN_PROGRESS.value),
                self.row("episode", season=1, episode=1, end_date="2024-02-09T20:00:00Z"),
            ],
            mode="overwrite",
        )

        tv = self.tv()
        self.assertEqual((tv.status, tv.choice), (Status.IN_PROGRESS.value, Status.IN_PROGRESS.value))
        season_1 = {
            viewing.item.episode_number: viewing.kind
            for viewing in self.viewings().filter(item__season_number=1)
        }
        self.assertEqual(season_1, {1: Episode.Kind.TICK})  # the Spine mark is gone
        self.assertTrue(DiaryEntry.objects.filter(pk=log.pk).exists())
        season_2_fills = self.viewings().filter(item__season_number=2)
        self.assertEqual(season_2_fills.count(), 3)
        self.assertTrue(all(viewing.diary_entry_id == log.pk for viewing in season_2_fills))  # TV-400
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 2))

    def test_tv_800_overwrite_rebuilds_an_episode_logs_viewing_from_an_imported_play(self):
        episode_item = Item.objects.create(
            media_id=self.show.media_id,
            source=self.show.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            image=self.show.image,
        )
        self.clock.advance(minutes=1)
        log = tv_tracking.create_log(self.user, episode_item, consumed_at=date(2026, 10, 20)).entry

        self.import_csv(
            [
                self.row("tv", status=Status.IN_PROGRESS.value),
                self.row("season", season=1, status=Status.IN_PROGRESS.value),
                # 11 a.m. Pacific on Oct 20, the log's calendar day.
                self.row("episode", season=1, episode=2, end_date="2026-10-20T18:00:00Z"),
            ],
            mode="overwrite",
        )

        viewing = self.viewings().get()
        self.assertEqual(viewing.diary_entry_id, log.pk)
        self.assertEqual(viewing.watched_on, date(2026, 10, 20))

    def test_tv_800_a_listing_failure_leaves_one_show_for_the_hourly_job(self):
        other = make_show(self.fake, seasons={1: 2}, title="Unreachable Show")
        self.fake.fail(other.media_id)
        self.import_csv([
            self.row("tv", status=Status.COMPLETED.value),
            self.row("tv", show=other, status=Status.COMPLETED.value),
        ])

        self.assertIsNotNone(self.tv().reconciled_at)
        stuck = self.tv(other)
        self.assertIsNone(stuck.reconciled_at)
        self.assertEqual(stuck.status, Status.COMPLETED.value)
        self.assertFalse(self.viewings(other).exists())

        self.fake.recover()
        self.clock.advance(hours=1)
        tv_airing.refresh_aired_shows()

        stuck.refresh_from_db()
        self.assertIsNotNone(stuck.reconciled_at)
        self.assertEqual(self.viewings(other).filter(kind=Episode.Kind.FILL).count(), 2)

    def test_tv_800_an_overwrite_whose_listing_fails_gets_its_logs_back_from_the_job(self):
        season_1 = Item.objects.create(
            media_id=self.show.media_id,
            source=self.show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title=self.show.title,
            image=self.show.image,
        )
        self.clock.advance(minutes=1)
        log = tv_tracking.create_log(self.user, season_1, consumed_at=user_calendar.today(self.user)).entry
        tv_listing_row = self.show.tv_listing
        tv_listing_row.delete()
        self.fake.fail(self.show.media_id)

        self.import_csv([self.row("tv", status=Status.IN_PROGRESS.value)], mode="overwrite")

        self.assertIsNone(self.tv().reconciled_at)
        self.assertFalse(self.viewings().exists())
        self.fake.recover()
        self.clock.advance(hours=1)
        tv_airing.refresh_aired_shows()

        tv = self.tv()
        self.assertIsNotNone(tv.reconciled_at)
        self.assertEqual(self.viewings().filter(diary_entry=log, kind=Episode.Kind.FILL).count(), 3)
        self.assertEqual((tv.status, tv.choice), (Status.IN_PROGRESS.value, Status.IN_PROGRESS.value))

    def season_item(self, show, number):
        item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            defaults={"title": show.title, "image": show.image},
        )
        return item

    def pre_contract_log(self, show, number):
        """A season log saved before the contract: no stamp, a date and time."""
        return DiaryEntry.objects.create(
            user=self.user,
            item=self.season_item(show, number),
            consumed_at=datetime(2026, 10, 1, 20, 0, tzinfo=user_calendar.calendar_tz(self.user)),
        )

    def test_tv_801_an_overwrite_over_a_pre_contract_log_leaves_the_show_legacy(self):
        """The overwrite records its facts and leaves the show for the repair or the next write.

        Whether TMDB answers during the import or only when the hourly job retries, the
        show ends the same: legacy, its log not yet converted. The user's next write
        converts the log, so Season 1 is watched (TV-800, TV-801).
        """
        reachable = self.show
        unreachable = make_show(self.fake, seasons={1: 3, 2: 3}, title="Unreachable Show")
        logs = {}
        for show in (reachable, unreachable):
            TV.objects.create(user=self.user, item=show, status=Status.IN_PROGRESS.value, legacy_pending=True)
            logs[show.pk] = self.pre_contract_log(show, 1)
        self.fake.fail(unreachable.media_id)
        rows = [
            self.row("tv", show=show, status=Status.IN_PROGRESS.value)
            for show in (reachable, unreachable)
        ]
        rows += [
            self.row("season", show=show, season=2, status=Status.IN_PROGRESS.value)
            for show in (reachable, unreachable)
        ]
        rows += [
            self.row("episode", show=show, season=2, episode=1, end_date="2026-10-20T18:00:00Z")
            for show in (reachable, unreachable)
        ]
        self.import_csv(rows, mode="overwrite")
        self.fake.recover()
        self.clock.advance(hours=1)
        tv_airing.refresh_aired_shows()

        snapshots = {}
        for show in (reachable, unreachable):
            tv = self.tv(show)
            logs[show.pk].refresh_from_db()
            snapshots[show.pk] = (show_snapshot(self.user, show), tv.legacy_pending, tv.reconciled_at)
            self.assertTrue(tv.legacy_pending)
            self.assertIsNone(tv.reconciled_at)
            self.assertIsNone(logs[show.pk].progress_snapshot)
        self.assertEqual(snapshots[reachable.pk], snapshots[unreachable.pk])

        self.clock.advance(minutes=1)
        tv_tracking.tick(self.user, reachable, 2, 2)

        tv = self.tv()
        logs[reachable.pk].refresh_from_db()
        self.assertFalse(tv.legacy_pending)
        self.assertEqual(logs[reachable.pk].progress_snapshot["tv"]["covers"], {"1": [1, 2, 3]})
        fills = self.viewings().filter(item__season_number=1)
        self.assertEqual(sorted(viewing.item.episode_number for viewing in fills), [1, 2, 3])
        self.assertTrue(all(viewing.diary_entry_id == logs[reachable.pk].pk for viewing in fills))
        self.assertEqual((tv.status, tv.up_next_season, tv.up_next_episode), (Status.IN_PROGRESS.value, 2, 3))

    def test_tv_800_an_overwrite_converts_a_pre_contract_log_beside_a_stamped_one(self):
        """Both logs survive the overwrite with their fills (TV-800), the old one converted."""
        season_2 = self.season_item(self.show, 2)
        self.clock.advance(minutes=1)
        stamped = tv_tracking.create_log(self.user, season_2, consumed_at=user_calendar.today(self.user)).entry
        old = self.pre_contract_log(self.show, 1)

        self.import_csv([self.row("tv", status=Status.IN_PROGRESS.value)], mode="overwrite")

        tv = self.tv()
        old.refresh_from_db()
        self.assertFalse(tv.legacy_pending)
        self.assertIsNotNone(tv.reconciled_at)
        self.assertEqual(old.progress_snapshot["tv"]["covers"], {"1": [1, 2, 3]})
        owners = {
            (viewing.item.season_number, viewing.item.episode_number): viewing.diary_entry_id
            for viewing in self.viewings()
        }
        self.assertEqual(owners, {
            **{(1, episode): old.pk for episode in (1, 2, 3)},
            **{(2, episode): stamped.pk for episode in (1, 2, 3)},
        })
        self.assertEqual(tv.status, Status.COMPLETED.value)

    def test_tv_800_the_daily_overwrite_import_run_twice_gives_the_same_snapshot(self):
        rows = [
            self.row("tv", status=Status.IN_PROGRESS.value, score="7.0"),
            self.row("season", season=1, status=Status.COMPLETED.value),
            self.row("episode", season=1, episode=2, end_date="2024-02-09T20:00:00Z"),
            self.row("episode", season=1, episode=2, end_date="2024-03-09T20:00:00Z"),
        ]
        self.import_csv(rows, mode="overwrite")
        first = show_snapshot(self.user, self.show)
        first["viewings"] = [viewing[:3] + viewing[4:] for viewing in first["viewings"]]

        self.clock.advance(days=1)
        self.import_csv(rows, mode="overwrite")
        second = show_snapshot(self.user, self.show)
        second["viewings"] = [viewing[:3] + viewing[4:] for viewing in second["viewings"]]

        self.assertEqual(second, first)


@override_settings(TIME_ZONE="America/New_York")
class TVImportSourceTests(TestCase):
    """Trakt and Simkl statuses become the same facts (TV-800)."""

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
        self.show = make_show(self.fake, seasons={1: 3, 2: 3}, title="Source Show")

    def viewings(self):
        return Episode.objects.filter(related_season__related_tv__user=self.user)

    def test_tv_800_a_trakt_finale_only_completed_season_is_a_mark_with_fills(self):
        importer = TraktImporter("someone", self.user, "new")
        importer.process_watched_episode({
            "type": "episode",
            "episode": {"season": 1, "number": 3, "title": "Finale"},
            "show": {"title": self.show.title, "ids": {"tmdb": int(self.show.media_id)}},
            "watched_at": "2024-02-09T20:00:00.000Z",
        })
        helpers.cleanup_existing_media(importer.to_delete, self.user)
        helpers.bulk_create_media(importer.bulk_media, self.user)

        tv = TV.objects.get(user=self.user, item=self.show)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (2, 1))
        kinds = dict(self.viewings().values_list("item__episode_number", "kind"))
        self.assertEqual(kinds, {1: Episode.Kind.FILL, 2: Episode.Kind.FILL, 3: Episode.Kind.TICK})
        season = Season.objects.get(related_tv=tv, item__season_number=1)
        self.assertEqual(season.status, Status.COMPLETED.value)

    def test_tv_800_simkl_inherited_season_statuses_count_only_as_completed_marks(self):
        paused = make_show(self.fake, seasons={1: 3}, title="Held Show")
        importer = SimklImporter(helpers.encrypt("token"), self.user, "new")

        def entry(show, status, seasons):
            return {
                "show": {"title": show.title, "ids": {"tmdb": int(show.media_id)}},
                "status": status,
                "user_rating": None,
                "memo": {},
                "last_watched_at": "2024-02-09T20:00:00Z",
                "seasons": [
                    {
                        "number": number,
                        "episodes": [
                            {"number": episode, "watched_at": "2024-02-09T20:00:00Z"}
                            for episode in episodes
                        ],
                    }
                    for number, episodes in seasons.items()
                ],
            }

        importer._process_tv_list([
            # Season 2 inherits the show's Completed: a mark that fills E2 and E3.
            entry(self.show, "completed", {1: [1, 2, 3], 2: [1]}),
            # Season 1 inherits Paused: not a season choice; the show is Paused.
            entry(paused, "hold", {1: [1]}),
        ])
        helpers.cleanup_existing_media(importer.to_delete, self.user)
        helpers.bulk_create_media(importer.bulk_media, self.user)

        tv = TV.objects.get(user=self.user, item=self.show)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        season_2 = dict(
            self.viewings().filter(related_season__related_tv=tv, item__season_number=2)
            .values_list("item__episode_number", "kind"),
        )
        self.assertEqual(season_2, {1: Episode.Kind.TICK, 2: Episode.Kind.FILL, 3: Episode.Kind.FILL})

        held = TV.objects.get(user=self.user, item=paused)
        self.assertEqual((held.status, held.choice), (Status.PAUSED.value, Status.PAUSED.value))
        self.assertEqual(self.viewings().filter(related_season__related_tv=held).count(), 1)
        self.assertEqual(
            Season.objects.get(related_tv=held).status,
            Status.PAUSED.value,  # the show's Paused, shown on the started season (TV-204)
        )
