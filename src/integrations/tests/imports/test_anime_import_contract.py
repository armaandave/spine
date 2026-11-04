"""AN-800: imports end in the state the app would produce, and AN-801, AN-802.

Every importer runs against its source's shape with MAL and AniList faked. Imports write
in bulk and never call a provider per episode.
"""

import io
from datetime import UTC, date, datetime
from unittest import mock

import app.forms  # noqa: F401 - the Yamtrack importer reads app.forms

from app import anime_tracking
from app.models import Anime, AnimeMark, AnimeViewing, DiaryEntry, Item
from app.tests.anime_fixtures import AnimeTestCase
from app.tests.test_anime_export_round_trip import round_trip
from integrations import exports
from integrations.imports import anilist, helpers, kitsu, mal, simkl, yamtrack
from integrations.imports.mal_export.importer import MALExportImporter
from social.models import ProgressChange


def mal_entry(title, status, *, count=0, rewatches=0, rewatching=False, score=0):
    return {
        "node": {
            "id": int(title.media_id),
            "title": title.title,
            "main_picture": {"large": "https://example.com/p.jpg"},
            "num_episodes": title.total or 0,
            "start_date": "2013-04-07",
            "average_episode_duration": 1440,
            "genres": [],
        },
        "list_status": {
            "status": status,
            "score": score,
            "num_episodes_watched": count,
            "is_rewatching": rewatching,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "start_date": None,
            "finish_date": None,
            "num_times_rewatched": rewatches,
            "comments": "",
        },
    }


def anilist_entry(title, status, *, count=0, repeat=0, score=0):
    empty = {"year": None, "month": None, "day": None}
    return {
        "media": {
            "title": {"userPreferred": title.title},
            "coverImage": {"large": "https://example.com/p.jpg"},
            "idMal": int(title.media_id),
            "episodes": title.total,
            "chapters": None,
            "startDate": empty,
            "duration": 24,
        },
        "status": status,
        "score": score,
        "progress": count,
        "startedAt": empty,
        "completedAt": empty,
        "updatedAt": 1767225600,
        "repeat": repeat,
        "notes": "",
    }


class ImportTestCase(AnimeTestCase):
    def import_mal(self, *entries, mode="new"):
        importer = mal.MyAnimeListImporter("someone", self.user, mode)
        for entry in entries:
            importer._process_entry(entry, "anime")  # noqa: SLF001
        helpers.cleanup_existing_media(importer.to_delete, self.user)
        helpers.bulk_create_media(importer.bulk_media, self.user, source="myanimelist")
        return helpers.imported_counts(importer.bulk_media)

    def item_of(self, title):
        return Item.objects.get(media_id=title.media_id, media_type="anime")

    def assert_no_activity(self):
        self.assertEqual(self.activity(), [])
        self.assertFalse(
            ProgressChange.objects.filter(actor=self.user)
            .filter(current_progress__kind="start")
            .exists(),
        )


class MALImportTests(ImportTestCase):
    """MAL's API list: one tracking record per title (AN-800)."""

    def test_an_800_a_count_becomes_undated_fills_as_one_mark(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "watching", count=5))
        item = self.item_of(title)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.up_next_episode), ("In progress", 5, 6))
        self.assertEqual(AnimeMark.objects.filter(anime=row, origin="import").count(), 1)
        self.assertTrue(all(viewing.watched_on is None and viewing.kind == "fill" for viewing in self.viewings(item)))
        self.assert_no_activity()

    def test_an_800_completed_is_an_undated_mark_of_everything_aired(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "completed", count=12, score=8))
        row = self.row(self.item_of(title))
        self.assertEqual((row.status, row.progress, str(row.score)), ("Completed", 12, "8.0"))
        self.assertFalse(DiaryEntry.objects.filter(user=self.user).exists())
        self.assert_no_activity()

    def test_an_800_each_rewatch_is_a_finished_earlier_pass_and_one_row(self):
        title = self.fake.add(total=12)
        counts = self.import_mal(mal_entry(title, "completed", count=12, rewatches=2))
        self.assertEqual(counts, {"anime": 1})
        item = self.item_of(title)
        self.assertEqual(Anime.objects.filter(user=self.user, item=item).count(), 1)
        row = self.row(item)
        self.assertEqual((row.pass_number, row.progress, row.rewatch_started_at), (3, 12, None))
        passes = {}
        for viewing in self.viewings(item):
            passes[viewing.pass_number] = passes.get(viewing.pass_number, 0) + 1
        self.assertEqual(passes, {1: 12, 2: 12, 3: 12})
        self.assertEqual(self.stats_time(), (36 * 24, 36))
        self.assertEqual(self.stats_time(2026), (0, 0))

    def test_an_800_a_rewatch_under_way_is_a_rewatch(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "completed", count=4, rewatches=1, rewatching=True))
        item = self.item_of(title)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("In progress", 4))
        self.assertIsNotNone(row.rewatch_started_at)
        self.assertEqual(self.state(item)["rewatch"]["pass_number"], 2)
        self.assertEqual(len(self.viewings(item)), 16)

    def test_an_800_planning_paused_and_dropped_stay_the_users_choice(self):
        for source_status, status in (("plan_to_watch", "Planning"), ("on_hold", "Paused"), ("dropped", "Dropped")):
            title = self.fake.add(total=12)
            self.import_mal(mal_entry(title, source_status, count=0 if status == "Planning" else 3))
            row = self.row(self.item_of(title))
            self.assertEqual((row.status, row.choice), (status, status))

    def test_an_800_in_progress_with_nothing_watched_means_watching(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "watching"))
        row = self.row(self.item_of(title))
        self.assertEqual((row.status, row.choice, row.progress), ("In progress", "In progress", 0))

    def test_an_800_an_import_records_no_progress_change(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "watching", count=5))
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exists())

    def test_an_800_one_history_row_per_title_at_the_sources_date(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "watching", count=5))
        history = self.row(self.item_of(title)).history.all()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].history_type, "+")
        self.assertEqual((history[0].status, history[0].progress), ("In progress", 5))
        self.assertEqual(history[0].history_date, datetime(2026, 1, 1, tzinfo=UTC))

    def test_an_800_the_sources_dates_stay_until_a_dated_fact_moves_them(self):
        title = self.fake.add(total=12)
        entry = mal_entry(title, "completed", count=12)
        entry["list_status"].update({"start_date": "2020-03-01", "finish_date": "2020-04-01"})
        self.import_mal(entry)
        item = self.item_of(title)
        row = self.row(item)
        self.assertEqual(row.start_date.date(), date(2020, 3, 1))
        self.assertEqual(row.end_date.date(), date(2020, 4, 1))

        # A write with no date behind it moves neither.
        anime_tracking.set_rating(self.user, item, 8, entry_point=anime_tracking.WEB)
        row.refresh_from_db()
        self.assertEqual(row.start_date.date(), date(2020, 3, 1))
        self.assertEqual(row.end_date.date(), date(2020, 4, 1))

        # A rewatch finished by a tick has its own end, and the start stays the source's.
        anime_tracking.start_rewatch(self.user, item, entry_point=anime_tracking.WEB)
        row.refresh_from_db()
        self.assertIsNone(row.end_date)
        for number in range(1, 13):
            anime_tracking.tick(self.user, item, number, entry_point=anime_tracking.WEB)
        row.refresh_from_db()
        self.assertEqual(row.status, "Completed")
        self.assertEqual(row.start_date.date(), date(2020, 3, 1))
        self.assertEqual(row.end_date, self.viewings(item)[-1].watched_at)

    def test_an_800_a_source_date_on_a_planned_title_is_no_start(self):
        title = self.fake.add(total=12)
        entry = mal_entry(title, "plan_to_watch")
        entry["list_status"].update({"start_date": "2020-03-01"})
        self.import_mal(entry)
        self.assertIsNone(self.row(self.item_of(title)).start_date)

    def test_an_800_no_provider_call_per_episode_and_no_anilist_at_all(self):
        title = self.fake.add(total=500)
        self.fake.calls.clear()
        self.import_mal(mal_entry(title, "completed", count=500))
        self.assertEqual(self.fake.calls, [])

    def test_an_800_the_question_is_used_up(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "watching", count=5))
        item = self.item_of(title)
        self.assertIsNone(self.tick(item, 8).earlier_unwatched)

    def test_an_800_a_later_tick_counts_only_what_is_new(self):
        title = self.fake.add(total=12)
        self.import_mal(mal_entry(title, "watching", count=5))
        item = self.item_of(title)
        self.tick(item, 6)
        self.assertEqual(self.stats_time(2026), (title.runtime, 1))
        change = ProgressChange.objects.filter(actor=self.user, item=item, current_progress__unit="episode").order_by("-id").first()
        self.assertEqual((change.previous_progress["value"], change.current_progress["value"]), (5, 6))

    def test_an_800_overwrite_replaces_viewings_marks_and_choices_and_keeps_logs(self):
        title = self.fake.add(total=4)
        item = self.title(title=title)
        self.tick(item, 1)
        entry = self.log(item).entry
        anime_tracking.choose(self.user, item, "Dropped")
        before = {viewing.episode_number for viewing in self.viewings(item)}
        self.assertEqual(before, {1, 2, 3, 4})
        self.import_mal(mal_entry(title, "watching", count=1), mode="overwrite")
        row = self.row(item)
        self.assertEqual(row.choice, "")
        self.assertTrue(DiaryEntry.objects.filter(pk=entry.pk).exists())
        # The log's fills are rebuilt, so its date dates them again.
        fills = {viewing.episode_number for viewing in self.viewings(item) if viewing.diary_entry_id == entry.pk}
        self.assertEqual(fills, {1, 2, 3, 4})
        self.assertEqual(row.progress, 4)
        self.assertEqual(self.stats_time(2026)[1], 4)

    def test_an_800_new_mode_leaves_a_tracked_title_alone(self):
        title = self.fake.add(total=4)
        item = self.title(title=title)
        self.tick(item, 1)
        importer = mal.MyAnimeListImporter("someone", self.user, "new")
        importer._process_entry(mal_entry(title, "completed", count=4), "anime")  # noqa: SLF001
        helpers.bulk_create_media(importer.bulk_media, self.user, source="myanimelist")
        self.assertEqual(self.row(item).progress, 1)

    def test_an_800_a_later_job_corrects_the_provisional_numbers(self):
        title = self.fake.add(total=12, status="Airing", next_episode=4)
        self.import_mal(mal_entry(title, "watching", count=2))
        item = self.item_of(title)
        self.assertEqual(self.state(item)["aired_count"], 3)
        self.assertEqual(self.row(item).aired_count, 12)
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(self.row(item).aired_count, 3)

    def test_an_800_a_completed_row_with_no_numbers_keeps_its_shelf(self):
        title = self.fake.add(total=None)
        item = self.title(title=title)
        anime_tracking.import_title(self.user, item, status="Completed")
        self.assertEqual(self.row(item).status, "Completed")
        self.assertIsNone(self.row(item).reconciled_at)

    def test_an_301_an_imported_rating_with_no_status_or_history_marks_it(self):
        title = self.fake.add(total=3)
        item = self.title(title=title)
        self.state(item)
        anime_tracking.import_title(self.user, item, status="", score=8)
        row = self.row(item)
        self.assertEqual((row.status, str(row.score), row.progress), ("Completed", "8.0", 3))

    def test_an_301_an_imported_rating_on_one_with_history_only_sets_the_value(self):
        title = self.fake.add(total=3)
        item = self.title(title=title)
        anime_tracking.import_title(self.user, item, status="In progress", count=1, score=8, total=3)
        row = self.row(item)
        self.assertEqual((row.status, str(row.score), row.progress), ("In progress", "8.0", 1))


class OtherImporterTests(ImportTestCase):
    """AniList, Kitsu, SIMKL, Yamtrack and the MAL export file (AN-800)."""

    def test_an_800_anilist_repeats_are_earlier_passes(self):
        title = self.fake.add(total=12)
        importer = anilist.AniListImporter(None, self.user, "new", "someone")
        importer._process_entry(anilist_entry(title, "COMPLETED", count=12, repeat=1, score=8), "anime")  # noqa: SLF001
        helpers.bulk_create_media(importer.bulk_media, self.user, source="anilist")
        row = self.row(self.item_of(title))
        self.assertEqual((row.status, row.progress, row.pass_number), ("Completed", 12, 2))
        self.assertEqual(self.fake.count("anilist"), 0)

    def test_an_800_anilist_current_means_watching(self):
        title = self.fake.add(total=12)
        importer = anilist.AniListImporter(None, self.user, "new", "someone")
        importer._process_entry(anilist_entry(title, "CURRENT", count=3), "anime")  # noqa: SLF001
        helpers.bulk_create_media(importer.bulk_media, self.user, source="anilist")
        self.assertEqual(self.row(self.item_of(title)).up_next_episode, 4)

    def test_an_800_kitsu_reconsumes_are_earlier_passes(self):
        title = self.fake.add(total=12)
        importer = kitsu.KitsuImporter("1", self.user, "new")
        entry = {
            "attributes": {
                "updatedAt": "2026-01-01T00:00:00.000Z",
                "reconsumeCount": 2,
                "reconsuming": False,
                "ratingTwenty": 16,
                "progress": 12,
                "startedAt": None,
                "finishedAt": None,
                "notes": None,
                "status": "completed",
            },
            "relationships": {"anime": {"data": {"id": "5"}}},
        }
        media = {
            "attributes": {
                "canonicalTitle": title.title,
                "posterImage": {"medium": "https://example.com/p.jpg"},
                "episodeCount": 12,
                "startDate": "2013-04-07",
                "episodeLength": 24,
            },
            "relationships": {"mappings": {"data": [{"id": "9"}]}},
        }
        mapping = {"attributes": {"externalSite": "myanimelist/anime", "externalId": title.media_id}}
        importer._process_entry(entry, "anime", {"5": media}, {"9": mapping})  # noqa: SLF001
        helpers.bulk_create_media(importer.bulk_media, self.user, source="kitsu")
        row = self.row(self.item_of(title))
        self.assertEqual((row.pass_number, row.progress, str(row.score)), (3, 12, "8.0"))

    def test_an_800_simkl_is_one_record_with_undated_fills(self):
        title = self.fake.add(total=12)
        importer = simkl.SimklImporter.__new__(simkl.SimklImporter)
        importer.user = self.user
        importer.mode = "new"
        importer.warnings = []
        importer.existing_media = helpers.get_existing_media(self.user)
        importer.to_delete = helpers.defaultdict(lambda: helpers.defaultdict(set))
        importer.bulk_media = helpers.defaultdict(list)
        importer._process_anime_list(  # noqa: SLF001
            [
                {
                    "show": {"title": title.title, "ids": {"mal": title.media_id}},
                    "status": "watching",
                    "user_rating": 7,
                    "watched_episodes_count": 4,
                    "last_watched_at": None,
                    "memo": {},
                },
            ],
        )
        helpers.bulk_create_media(importer.bulk_media, self.user)
        row = self.row(self.item_of(title))
        self.assertEqual((row.status, row.progress, row.aired_count), ("In progress", 4, 12))

    def test_an_800_an_old_yamtrack_export_with_a_row_per_rewatch_is_one_record(self):
        title = self.fake.add(total=3)
        Item.objects.create(source="mal", media_type="anime", media_id=title.media_id, title="T", image="https://example.com/p.jpg")
        header = ",".join(("media_id", "source", "media_type", "title", "image", "season_number", "episode_number", "score", "progress", "status", "start_date", "end_date", "notes", "progressed_at"))
        rows = [
            f"{title.media_id},mal,anime,T,https://example.com/p.jpg,,,8,3,Completed,,,,2026-02-01T00:00:00+00:00",
            f"{title.media_id},mal,anime,T,https://example.com/p.jpg,,,8,3,Completed,,,,2026-01-01T00:00:00+00:00",
        ]
        file = io.BytesIO("\n".join([header, *rows]).encode())
        yamtrack.YamtrackImporter(file, self.user, "new").import_data()
        row = Anime.objects.get(user=self.user)
        self.assertEqual((row.pass_number, row.progress, row.status), (2, 3, "Completed"))

    def _export(self, *entries, mode="new"):
        from integrations.tests.imports.test_mal_export import _xml

        def metadata(media_type, media_id):
            return {"title": "T", "image": "https://example.com/p.jpg", "max_progress": 12, "details": {}}

        file = io.BytesIO(_xml(*entries))
        self.fake.calls.clear()
        result = MALExportImporter(file, self.user, mode, metadata_fetcher=metadata).import_data()
        # No provider call per title, inside the import's transaction or out of it.
        self.assertEqual(self.fake.calls, [])
        return result

    def test_an_800_the_mal_exports_log_dates_its_fills(self):
        title = self.fake.add(total=12)
        self._export(
            (
                title.media_id,
                "Completed",
                {"my_watched_episodes": "12", "my_finish_date": "2020-05-01", "my_score": "8"},
            ),
        )
        item = self.item_of(title)
        entry = self.logs(item)[0]
        self.assertEqual(entry.consumed_at, datetime(2020, 5, 1, tzinfo=UTC))
        self.assertEqual(entry.import_source, "myanimelist")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 12))
        self.assertTrue(all(viewing.diary_entry_id == entry.pk for viewing in self.viewings(item)))
        self.assertEqual(self.stats_time(2020)[1], 12)
        self.assertEqual(self.stats_time(2026)[1], 0)
        anime_tracking.delete_log(self.user, entry)
        self.assertEqual(self.stats_time(2020)[1], 0)

    def test_an_800_the_mal_exports_rewatches_are_earlier_passes(self):
        title = self.fake.add(total=12)
        self._export(
            (
                title.media_id,
                "Completed",
                {"my_watched_episodes": "12", "my_times_watched": "2"},
            ),
        )
        item = self.item_of(title)
        self.assertEqual(Anime.objects.filter(user=self.user, item=item).count(), 1)
        self.assertEqual(self.row(item).pass_number, 3)
        self.assertEqual(len(self.viewings(item)), 36)

    def test_an_800_the_mal_exports_log_on_a_rewatch_dates_the_pass_it_finished(self):
        title = self.fake.add(total=12)
        self._export(
            (
                title.media_id,
                "Completed",
                {
                    "my_watched_episodes": "12",
                    "my_rewatching": "1",
                    "my_rewatching_ep": "5",
                    "my_finish_date": "2020-05-01",
                },
            ),
        )
        item = self.item_of(title)
        row = self.row(item)
        self.assertEqual((row.status, row.pass_number, row.progress), ("In progress", 2, 5))
        self.assertIsNotNone(row.rewatch_started_at)
        entry = self.logs(item)[0]
        self.assertFalse(entry.is_rewatch)
        finished = [viewing for viewing in self.viewings(item) if viewing.pass_number == 1]
        self.assertEqual(len(finished), 12)
        self.assertTrue(all(viewing.diary_entry_id == entry.pk for viewing in finished))
        current = [viewing for viewing in self.viewings(item) if viewing.pass_number == 2]
        self.assertEqual(sorted(viewing.episode_number for viewing in current), [1, 2, 3, 4, 5])
        self.assertTrue(all(viewing.diary_entry_id is None for viewing in current))
        # Those twelve land on the day they finished, the five nowhere (AN-704).
        self.assertEqual(self.stats_time(2020)[1], 12)
        self.assertEqual(self.stats_time(2026)[1], 0)
        self.assertEqual(self.stats_time()[1], 17)

    def test_an_800_the_mal_exports_overwrite_keeps_one_record_and_one_log(self):
        title = self.fake.add(total=12)
        fields = {"my_watched_episodes": "12", "my_finish_date": "2020-05-01"}
        self._export((title.media_id, "Completed", fields))
        self._export((title.media_id, "Completed", fields), mode="overwrite")
        item = self.item_of(title)
        self.assertEqual(Anime.objects.filter(user=self.user, item=item).count(), 1)
        self.assertEqual(len(self.logs(item)), 1)
        self.assertEqual(self.row(item).progress, 12)


class ExportTests(AnimeTestCase):
    """AN-802: today's columns, one row per title; counts survive the round trip."""

    def test_an_802_one_row_per_title(self):
        item = self.title(total=3)
        self.tick(item, 1, 2)
        lines = list(exports.generate_rows(self.user))
        self.assertEqual(len(lines), 2)

    def test_an_802_the_new_fields_arent_columns(self):
        header = next(iter(exports.generate_rows(self.user)))
        for name in ("pass_number", "log_offered_pass", "choice", "reconciled_at", "up_next_episode"):
            self.assertNotIn(f'"{name}"', header)

    def test_an_802_a_round_trip_keeps_counts_shelves_and_stars(self):
        finished = self.title(total=3)
        anime_tracking.set_rating(self.user, finished, 8)
        partway = self.title(total=10)
        self.tick(partway, 1, 2)
        dropped = self.title(total=10)
        self.tick(dropped, 1)
        anime_tracking.choose(self.user, dropped, "Dropped")
        _, other = round_trip(self.user)
        for item, status, count in ((finished, "Completed", 3), (partway, "In progress", 2), (dropped, "Dropped", 1)):
            row = Anime.objects.get(user=other, item=item)
            self.assertEqual((row.status, row.progress), (status, count))
        self.assertEqual(str(Anime.objects.get(user=other, item=finished).score), "8.0")

    def test_an_802_a_rewatch_in_progress_isnt_exported(self):
        item = self.title(total=3)
        self.tick(item, 1, 2, 3)
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        _, other = round_trip(self.user)
        row = Anime.objects.get(user=other, item=item)
        self.assertEqual((row.status, row.progress, row.rewatch_started_at), ("Completed", 3, None))


class WebhookTests(AnimeTestCase):
    """AN-801: Plex, Jellyfin and Emby events follow TV-802."""

    def event(self, item, number, event):
        return anime_tracking.webhook_event(self.user, item, number, event=event, at=self.clock.moment)

    def test_an_801_a_watch_ticks_at_the_event_time_with_no_question(self):
        item = self.title(total=5)
        moment = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        result = anime_tracking.webhook_event(self.user, item, 3, event="watched", at=moment)
        viewing = self.viewings(item)[0]
        self.assertEqual((viewing.watched_at, viewing.watched_on), (moment, date(2026, 10, 24)))
        self.assertIsNone(result.earlier_unwatched)
        self.assertIsNone(result.finished)
        self.assertEqual(self.watched(item), {3})

    def test_an_801_a_repeated_watch_records_nothing(self):
        item = self.title(total=5)
        self.event(item, 1, "watched")
        self.assertFalse(self.event(item, 1, "watched").changed)
        self.assertEqual(len(self.viewings(item)), 1)

    def test_an_801_an_unplayed_event_unticks(self):
        item = self.title(total=5)
        self.event(item, 1, "watched")
        self.event(item, 2, "watched")
        self.event(item, 2, "unplayed")
        self.assertEqual(self.watched(item), {1})

    def test_an_801_a_play_counts_as_choosing_watching_where_available(self):
        item = self.title(total=5)
        self.event(item, 1, "play")
        self.assertEqual((self.row(item).status, self.row(item).choice), ("In progress", "In progress"))
        self.assertEqual(self.viewings(item), [])
        paused = self.title(total=5)
        anime_tracking.choose(self.user, paused, "Paused")
        self.event(paused, 1, "play")
        self.assertEqual(self.row(paused).status, "In progress")

    def test_an_801_a_play_never_reopens_a_finished_title(self):
        item = self.title(total=1)
        anime_tracking.mark_watched(self.user, item)
        self.event(item, 1, "play")
        self.assertEqual(self.row(item).status, "Completed")
        self.assertEqual(self.row(item).choice, "")

    def test_an_801_a_rejected_event_is_acknowledged(self):
        item = self.title(total=5)
        self.assertIsNone(self.event(item, 99, "watched"))
        self.assertIsNone(self.row(item))
        logged = self.title(total=2)
        self.log(logged)
        self.assertIsNone(self.event(logged, 1, "unplayed"))
        self.assertEqual(len(self.watched(logged)), 2)

    def test_an_801_details_unavailable_is_acknowledged(self):
        item = self.title(total=5)
        self.fake.fail()
        self.assertIsNone(self.event(item, 1, "watched"))
        self.assertFalse(AnimeViewing.objects.exists())

    def test_an_801_the_processor_routes_through_the_module(self):
        from integrations.webhooks.anime import AnimeWebhookMixin

        class Processor(AnimeWebhookMixin):
            played = True
            unplayed = False

            def _is_played(self, _payload):
                return self.played

            def _is_unplayed(self, _payload):
                return self.unplayed

        title = self.fake.add(total=5)
        processor = Processor()
        processor._handle_anime(title.media_id, 2, {}, self.user)  # noqa: SLF001
        item = self.item_of_title(title)
        self.assertEqual(self.viewings(item)[0].kind, "tick")
        processor.played = False
        processor._handle_anime(title.media_id, 3, {}, self.user)  # noqa: SLF001
        self.assertEqual(self.watched(item), {2})
        processor.unplayed = True
        processor._handle_anime(title.media_id, 2, {}, self.user)  # noqa: SLF001
        self.assertEqual(self.watched(item), set())

    def test_an_801_an_unplayed_event_for_an_unknown_title_creates_nothing(self):
        from integrations.webhooks.anime import AnimeWebhookMixin

        class Processor(AnimeWebhookMixin):
            def _is_played(self, _payload):
                return False

            def _is_unplayed(self, _payload):
                return True

        with mock.patch("app.providers.mal.anime") as metadata:
            Processor()._handle_anime("123456", 2, {}, self.user)  # noqa: SLF001
        metadata.assert_not_called()
        self.assertFalse(Item.objects.filter(media_id="123456").exists())

    def item_of_title(self, title):
        return Item.objects.get(media_id=title.media_id, media_type="anime")
