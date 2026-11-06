"""Imported films and albums: a dated play is a log on its date, the rest is the mark.

Decision 47: Yamtrack's and Simkl's dated plays land on their dates as Letterboxd's and
Trakt's do. A row states no Rewatch, so only an earlier log makes it one (decision 38).
IMDb's dates say when a title was rated or listed, so its rows stay undated marks.
Numbers come through the Stats endpoint, and every file goes through its real importer
with no network.
"""

import io
from datetime import date
from unittest.mock import patch

from django.test import override_settings

from api.tests.test_activity_calc_films import FilmCalcBase
from app import single_weight
from app.models import DiaryEntry, Item, MediaTypes, Movie, Music, Sources
from integrations import exports
from integrations.imports import helpers, imdb, simkl, yamtrack

MOVIE = MediaTypes.MOVIE.value
NEW_YORK = "America/New_York"
HEADER = (
    '"media_id","source","media_type","title","image","season_number","episode_number",'
    '"score","progress","status","start_date","end_date","notes","progressed_at"\n'
)


def yamtrack_file(item, *rows):
    """Return a Yamtrack file of this title's rows, each (status, end_date, score)."""
    lines = [
        f'"{item.media_id}","{item.source}","{item.media_type}","Title","https://example.com/x.jpg","","",'
        f'"{score}","1","{status}","","{end_date}","",""\n'
        for status, end_date, score in rows
    ]
    return io.BytesIO((HEADER + "".join(lines)).encode())


class ImportBase(FilmCalcBase):
    """The base's 2-hour film or 1-hour album, imported."""

    def logs(self):
        entries = DiaryEntry.objects.filter(user=self.user, item=self.item).order_by("consumed_at", "id")
        return [(single_weight.calendar_date(entry.consumed_at), entry.is_rewatch, entry.import_source) for entry in entries]

    def tracking(self):
        model = Movie if self.media_type == MOVIE else Music
        return model.objects.get(user=self.user, item=self.item)


@override_settings(TIME_ZONE=NEW_YORK)
class YamtrackPlayTests(ImportBase):
    """A Yamtrack file's dated Completed row is a play on its date."""

    def load(self, *rows, mode="new"):
        with patch("app.signals.update_daily_statistics.delay"):
            counts, warnings = yamtrack.importer(yamtrack_file(self.item, *rows), self.user, mode)
        self.assertFalse(warnings)
        return counts

    def test_a_dated_row_is_a_log_on_its_date(self):
        self.load(("Completed", "2026-02-01", "8"))
        self.assertEqual(self.logs(), [(date(2026, 2, 1), False, "yamtrack")])
        tracking = self.tracking()
        self.assertFalse(tracking.direct_consumption, "a log, not a mark")
        self.assertEqual(tracking.score, 8)
        self.watches(1, 1)

    def test_a_row_with_no_date_is_the_mark(self):
        self.load(("Completed", "", "8"))
        self.assertEqual(self.logs(), [])
        self.assertTrue(self.tracking().direct_consumption)
        self.watches(0, 1)

    def test_a_dated_row_over_a_marked_title_dates_that_watch(self):
        """A new import skips a title you have, so this is an overwrite."""
        self.eye()
        self.load(("Completed", "2026-02-01", ""), mode="overwrite")
        self.assertEqual(self.logs(), [(date(2026, 2, 1), False, "yamtrack")])
        self.watches(1, 1)

    def test_a_dated_row_after_an_earlier_log_is_a_rewatch(self):
        self.log(rewatch=False, day=date(2026, 1, 10))
        self.load(("Completed", "2026-02-01", ""), mode="overwrite")
        self.assertEqual([is_rewatch for _, is_rewatch, _ in self.logs()], [False, True])
        self.watches(2, 2)

    def test_a_row_still_in_progress_is_no_play(self):
        self.load(("In progress", "2026-02-01", ""))
        self.assertEqual(self.logs(), [])
        self.watches(0, 0)

    def test_the_same_file_again_adds_nothing(self):
        self.load(("Completed", "2026-02-01", ""))
        self.load(("Completed", "2026-02-01", ""), mode="overwrite")
        self.assertEqual(len(self.logs()), 1)
        self.watches(1, 1)

    def test_your_own_export_comes_back_as_it_left(self):
        """SW-504: the export's end date is the newest log's, which is already there."""
        self.eye()
        self.log(rewatch=False, day=date(2026, 1, 10))
        self.log(rewatch=True, day=date(2026, 2, 1))
        before = (self.numbers(), self.logs(), self.tracking().direct_consumption)
        export = "".join(exports.generate_rows(self.user))
        with patch("app.signals.update_daily_statistics.delay"):
            yamtrack.importer(io.BytesIO(export.encode()), self.user, "overwrite")
        self.assertEqual((self.numbers(), self.logs(), self.tracking().direct_consumption), before)


@override_settings(TIME_ZONE=NEW_YORK)
class YamtrackAlbumPlayTests(YamtrackPlayTests):
    """Albums follow films (SW-010)."""

    media_type = MediaTypes.MUSIC.value
    minutes = 60


@override_settings(TIME_ZONE=NEW_YORK)
class SimklPlayTests(ImportBase):
    """Simkl's last watch of a completed film is a play on that day."""

    def setUp(self):
        super().setUp()
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MOVIE,
            media_id="10494",
            title="Perfect Blue",
            image="https://example.com/x.jpg",
            runtime_minutes=self.minutes,
        )

    def load(self, mode="new", **movie):
        entry = {
            "movie": {"title": "Perfect Blue", "ids": {"tmdb": 10494}},
            "status": "completed",
            "user_rating": 9,
            "memo": {},
            **movie,
        }
        listing = {"shows": [], "movies": [entry], "anime": []}
        importer = simkl.SimklImporter(helpers.encrypt("token"), self.user, mode)
        with (
            patch("integrations.imports.simkl.SimklImporter._get_user_list", return_value=listing),
            patch("app.providers.tmdb.movie", return_value={"title": "Perfect Blue", "image": "https://example.com/x.jpg", "runtime": self.minutes}),
            patch("app.signals.update_daily_statistics.delay"),
        ):
            importer.import_data()

    def test_a_watched_film_is_a_log_on_its_last_watch(self):
        self.load(last_watched_at="2026-02-01T21:00:00Z")
        self.assertEqual(self.logs(), [(date(2026, 2, 1), False, "simkl")])
        self.assertEqual(self.tracking().score, 9)
        self.watches(1, 1)

    def test_over_a_marked_film_it_dates_that_watch(self):
        self.eye()
        self.load(mode="overwrite", last_watched_at="2026-02-01T21:00:00Z")
        self.assertEqual(self.logs(), [(date(2026, 2, 1), False, "simkl")])
        self.watches(1, 1)

    def test_with_no_watch_date_it_is_the_mark(self):
        self.load(last_watched_at=None)
        self.assertEqual(self.logs(), [])
        self.assertTrue(self.tracking().direct_consumption)
        self.watches(0, 1)


@override_settings(TIME_ZONE=NEW_YORK)
class IMDbRatingTests(ImportBase):
    """IMDb's rating, created and modified dates are no watch dates (decision 47)."""

    minutes = 142  # the file's runtime, which the import stores

    def setUp(self):
        super().setUp()
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MOVIE,
            media_id="278",
            title="The Shawshank Redemption",
            image="https://example.com/x.jpg",
            runtime_minutes=self.minutes,
        )

    def test_a_rated_film_is_an_undated_mark(self):
        csv = (
            "Position,Const,Created,Modified,Description,Title,Original Title,URL,Title Type,IMDb Rating,"
            "Runtime (mins),Year,Genres,Num Votes,Release Date,Directors,Your Rating,Date Rated\n"
            "1,tt0111161,2026-01-20,2026-02-03,,The Shawshank Redemption,The Shawshank Redemption,"
            "https://www.imdb.com/title/tt0111161/,Movie,9.3,142,1994,Drama,3066765,1994-09-23,"
            "Frank Darabont,9,2026-02-03\n"
        )
        found = {"movie_results": [{"id": 278, "title": "The Shawshank Redemption", "poster_path": "/x.jpg"}], "tv_results": []}
        with patch("integrations.imports.imdb.app.providers.tmdb.find", return_value=found):
            imdb.importer(io.BytesIO(csv.encode()), self.user, "new")
        self.assertEqual(self.logs(), [])
        tracking = self.tracking()
        self.assertEqual((tracking.direct_consumption, tracking.score, tracking.end_date), (True, 9, None))
        self.watches(0, 1)


@override_settings(TIME_ZONE=NEW_YORK)
class ImportQueueTests(ImportBase):
    """A dated play queues its day's statistics, one per log, as a diary import does."""

    def test_each_dated_play_queues_its_day(self):
        days = ("2026-01-10", "2026-02-01")
        others = [
            Item.objects.create(
                source=Sources.MANUAL.value,
                media_type=MOVIE,
                media_id=f"queue-{day}",
                title=day,
                image="https://example.com/x.jpg",
            )
            for day in days
        ]
        rows = "".join(
            f'"{item.media_id}","manual","movie","{item.title}","https://example.com/x.jpg","","","","1",'
            f'"Completed","","{item.title}","",""\n'
            for item in others
        )
        rows += f'"{self.item.media_id}","manual","movie","Title","https://example.com/x.jpg","","","","1","Completed","","","",""\n'
        with (
            patch("app.signals.update_daily_statistics.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            yamtrack.importer(io.BytesIO((HEADER + rows).encode()), self.user, "new")
        queued = sorted(call.kwargs["date_str"][:10] for call in delay.call_args_list)
        self.assertEqual(queued, list(days), "one per log, none for the undated mark")

    def test_an_undated_mark_queues_nothing(self):
        with (
            patch("app.signals.update_daily_statistics.delay") as delay,
            patch("app.single_weight.update_daily_statistics.delay") as direct,
            self.captureOnCommitCallbacks(execute=True),
        ):
            single_weight.mark_consumed(self.user, self.item)
        self.assertEqual((delay.call_count, direct.call_count), (0, 0))
