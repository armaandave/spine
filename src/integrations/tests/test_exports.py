import csv
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Music,
    Movie,
    Season,
    Sources,
    Status,
    TV,
)


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            Movie.objects.create(
                item=item_movie,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                notes="Nice",
                start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
                end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        item_tv = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="https://image.url",
        )
        tv = TV(
            item=item_tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.save_base(tv)

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            related_tv=tv,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        episode = Episode(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )
        Episode.save_base(episode)

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://image.url",
        )
        anime = Anime(
            item=item_anime,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )
        Anime.save_base(anime)

        item_manga = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="https://image.url",
        )
        manga = Manga(
            item=item_manga,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )
        Manga.save_base(manga)

        item_game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        game = Game(
            item=item_game,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )
        Game.save_base(game)

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        book = Book(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )
        Book.save_base(book)

        item_music = Item.objects.create(
            media_id="3bd76d40-7f0e-36b7-9348-91a33afee20e",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Year Zero",
            image="https://image.url/year-zero.jpg",
        )
        music = Music(
            item=item_music,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            end_date=datetime(2025, 4, 5, 0, 0, tzinfo=UTC),
        )
        Music.save_base(music)

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the streaming content and decode it
        content = b"".join(response.streaming_content).decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(content))

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(movie__user=self.user)
                | Q(season__user=self.user)
                | Q(episode__related_season__user=self.user)
                | Q(anime__user=self.user)
                | Q(manga__user=self.user)
                | Q(game__user=self.user)
                | Q(book__user=self.user)
                | Q(music__user=self.user),
            ).values_list("media_id", flat=True),
        )

        exported_media_ids = set()
        for row in reader:
            media_id = row["media_id"]
            exported_media_ids.add(media_id)
            self.assertIn(media_id, db_media_ids)

        self.assertIn(
            "3bd76d40-7f0e-36b7-9348-91a33afee20e",
            exported_media_ids,
        )


# Today's CSV header (A-66). New tracking fields must never change it (TV-803).
TODAYS_HEADER = [
    "media_id",
    "source",
    "media_type",
    "title",
    "image",
    "season_number",
    "episode_number",
    "poster_accent_color",
    "total_pages",
    "release_date",
    "release_year",
    "runtime_minutes",
    "letterboxd_rating",
    "imdb_rating",
    "rotten_tomatoes_rating",
    "filter_metadata_updated_at",
    "score",
    "status",
    "notes",
    "start_date",
    "end_date",
    "progress",
    "liked",
    "direct_consumption",
    "like_is_independent",
    "legacy_archived",
    "completed_manually",
    "status_history",
    "imported_lifetime_minutes",
    "imported_lifetime_source",
    "undated_read_previous_tracked",
    "undated_read_previous_status",
    "undated_read_previous_override",
    "read_override_active",
    "read_override_previous_tracked",
    "read_override_previous_status",
    "created_at",
    "progressed_at",
]


@patch("app.models.Item.fetch_releases")
class TVExportContractTest(TestCase):
    """TV-803: the export keeps today's columns after the TV tracking fields."""

    def setUp(self):
        """Create a worked-out show with a tick and a fill, and a legacy show."""
        self.credentials = {"username": "tv-export", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        def item(media_id, media_type, season_number=None, episode_number=None):
            return Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=media_type,
                title="Show",
                image="https://image.url",
                season_number=season_number,
                episode_number=episode_number,
            )

        with patch("app.models.Item.fetch_releases"):
            self.tv = TV.objects.create(
                item=item("500", MediaTypes.TV.value),
                user=self.user,
                status=Status.IN_PROGRESS.value,
                like_is_independent=True,
                legacy_pending=False,
                reconciled_at=datetime(2026, 10, 25, tzinfo=UTC),
                watched_count=2,
                aired_count=10,
                choice=Status.PAUSED.value,
            )
            self.season = Season.objects.create(
                item=item("500", MediaTypes.SEASON.value, 1),
                user=self.user,
                related_tv=self.tv,
                status=Status.IN_PROGRESS.value,
                like_is_independent=True,
                is_started=True,
                watched_count=2,
            )
            Episode.objects.create(
                item=item("500", MediaTypes.EPISODE.value, 1, 1),
                related_season=self.season,
                end_date=datetime(2026, 10, 24, 20, 0, tzinfo=UTC),
                watched_on=datetime(2026, 10, 24, tzinfo=UTC).date(),
            )
            Episode.objects.create(
                item=item("500", MediaTypes.EPISODE.value, 1, 4),
                related_season=self.season,
                kind=Episode.Kind.FILL,
            )
            legacy_tv = TV(
                item=item("600", MediaTypes.TV.value),
                user=self.user,
                status=Status.COMPLETED.value,
                legacy_pending=True,
                watched_count=99,
            )
            TV.save_base(legacy_tv)
            legacy_season = Season.objects.create(
                item=item("600", MediaTypes.SEASON.value, 1),
                user=self.user,
                related_tv=legacy_tv,
                status=Status.COMPLETED.value,
            )
            Episode.objects.create(
                item=item("600", MediaTypes.EPISODE.value, 1, 3),
                related_season=legacy_season,
                end_date=datetime(2026, 10, 1, tzinfo=UTC),
            )

    def export(self):
        response = self.client.get(reverse("export_csv"))
        content = b"".join(response.streaming_content).decode("utf-8")
        return list(csv.reader(StringIO(content)))

    def rows(self):
        header, *rows = self.export()
        return [dict(zip(header, row, strict=True)) for row in rows]

    def test_a66_the_header_is_exactly_todays(self, _):
        self.assertEqual(self.export()[0], TODAYS_HEADER)

    def test_tv_803_only_the_tv_tracking_fields_are_left_out(self, _):
        from integrations import exports  # noqa: PLC0415

        track_fields = exports.get_track_fields()
        self.assertFalse(exports.TV_TRACKING_FIELD_NAMES & set(track_fields))

        original = exports.get_model_fields

        def with_new_movie_field(model):
            fields = original(model)
            return [*fields, "new_movie_field"] if model is Movie else fields

        with patch.object(exports, "get_model_fields", with_new_movie_field):
            self.assertIn("new_movie_field", exports.get_track_fields())

    def test_tv_803_tv_and_season_rows_leave_like_is_independent_empty(self, _):
        rows = self.rows()
        for row in rows:
            if row["media_type"] in (MediaTypes.TV.value, MediaTypes.SEASON.value):
                self.assertEqual(row["like_is_independent"], "")

    def test_tv_803_fills_export_as_viewings_with_no_date(self, _):
        episodes = {
            row["episode_number"]: row
            for row in self.rows()
            if row["media_type"] == MediaTypes.EPISODE.value and row["media_id"] == "500"
        }
        self.assertEqual(episodes["4"]["end_date"], "")
        self.assertTrue(episodes["1"]["end_date"].startswith("2026-10-24"))

    def test_tv_803_progress_is_the_worked_out_count(self, _):
        rows = {
            (row["media_id"], row["media_type"]): row
            for row in self.rows()
            if row["media_type"] in (MediaTypes.TV.value, MediaTypes.SEASON.value)
        }
        self.assertEqual(rows[("500", MediaTypes.TV.value)]["progress"], "2")
        self.assertEqual(rows[("500", MediaTypes.SEASON.value)]["progress"], "2")
        # A show not yet worked out exports today's computed progress.
        self.assertEqual(rows[("600", MediaTypes.TV.value)]["progress"], "3")
        self.assertEqual(rows[("600", MediaTypes.SEASON.value)]["progress"], "3")
