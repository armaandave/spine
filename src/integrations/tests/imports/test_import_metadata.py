"""Importers and webhooks store the provider metadata they already hold (stats plan 1.3).

Full records go through store_item_metadata and stamp the Item; partial importers store
only what costs no extra call, never stamp and never queue a task (the hourly metadata
fill completes them); new series members reach import_media's ratings collector.
Every provider entry point is patched.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.forms  # noqa: F401 - the Yamtrack importer reads app.forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from api.services.item_metadata import CURRENT_METADATA_VERSION
from app.mixins import collect_external_rating_item_ids
from app.models import Item, ItemFilterFacet, MediaSeries, MediaTypes, Sources
from integrations.imports import helpers
from integrations.imports.anilist import AniListImporter
from integrations.imports.goodreads import GoodReadsImporter
from integrations.imports.hltb import HowLongToBeatImporter
from integrations.imports.imdb import IMDBImporter
from integrations.imports.kitsu import KitsuImporter
from integrations.imports.letterboxd.importer import LetterboxdImporter
from integrations.imports.mal import MyAnimeListImporter
from integrations.imports.mal_export.importer import MALExportImporter, backfill_metadata
from integrations.imports.simkl import SimklImporter
from integrations.imports.steam import SteamImporter
from integrations.imports.storygraph.importer import StoryGraphImporter
from integrations.imports.trakt import TraktImporter
from integrations.imports.yamtrack import YamtrackImporter
from integrations.tasks import import_media
from integrations.webhooks.plex import PlexWebhookProcessor

GENRE = ItemFilterFacet.FacetType.GENRE


def movie_metadata(**extra):
    """A TMDB movie record as tmdb.movie() gives it."""
    return {
        "title": "Heat",
        "image": "https://example.com/heat.jpg",
        "details": {"release_date": "1995-12-15", "runtime": "2h 50m"},
        "genres": [{"name": "Crime"}, {"name": "Drama"}],
        **extra,
    }


class ImportMetadataTestCase(TestCase):
    """A user plus checks on what an Item holds."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="metadata", password="password")

    def genres(self, item):
        return set(
            ItemFilterFacet.objects.filter(item=item, facet_type=GENRE).values_list("value", flat=True),
        )

    def assert_stamped(self, item, stamped=True):
        item.refresh_from_db()
        current = CURRENT_METADATA_VERSION[item.media_type]
        self.assertEqual(item.metadata_version, current if stamped else 0)


class FullMetadataImportTests(ImportMetadataTestCase):
    """Importers that fetch the provider's full record store and stamp it."""

    def test_letterboxd_stores_and_stamps_the_tmdb_record(self):
        importer = LetterboxdImporter(None, self.user, "new", resolver=Mock())
        with patch(
            "integrations.imports.letterboxd.importer.tmdb.movie",
            return_value=movie_metadata(),
        ):
            item = importer._get_or_create_item("949", {"name": "Heat"})

        item.refresh_from_db()
        self.assertEqual((item.release_year, item.runtime_minutes), (1995, 170))
        self.assertEqual(self.genres(item), {"Crime", "Drama"})
        self.assert_stamped(item)

    def test_letterboxd_failed_fetch_is_not_stamped(self):
        importer = LetterboxdImporter(None, self.user, "new", resolver=Mock())
        with patch(
            "integrations.imports.letterboxd.importer.tmdb.movie",
            side_effect=RuntimeError("TMDB down"),
        ):
            item = importer._get_or_create_item("949", {"name": "Heat"})

        item.refresh_from_db()
        self.assertEqual(item.title, "Heat")
        self.assertIsNone(item.release_year)
        self.assertIsNone(item.filter_metadata_updated_at)
        self.assert_stamped(item, stamped=False)

    def test_trakt_stores_show_and_season_and_gives_episodes_their_date_and_length(self):
        def metadata(media_type, _tmdb_id, _title, _season_number=None):
            if media_type == MediaTypes.TV.value:
                return {
                    "title": "Breaking Bad",
                    "image": "tv.jpg",
                    "details": {"first_air_date": "2008-01-20"},
                    "genres": [{"name": "Drama"}],
                    "max_progress": 7,
                }
            return {
                "title": "Breaking Bad",
                "image": "season.jpg",
                "details": {"first_air_date": "2008-01-20", "runtime": "47m"},
                "episodes": [
                    {"episode_number": 1, "still_path": None, "air_date": "2008-01-20", "runtime": 58},
                    {"episode_number": 2, "still_path": None, "air_date": "2008-01-27", "runtime": 48},
                ],
                "max_progress": 2,
            }

        entry = {
            "episode": {"season": 1, "number": 2},
            "show": {"title": "Breaking Bad", "ids": {"tmdb": 1396}},
            "watched_at": "2024-01-01T00:00:00.000Z",
        }
        importer = TraktImporter("trakt", self.user, "new")
        with patch.object(TraktImporter, "_get_metadata", side_effect=metadata):
            importer.process_watched_episode(entry)

        show = Item.objects.get(media_type=MediaTypes.TV.value, media_id="1396")
        season = Item.objects.get(media_type=MediaTypes.SEASON.value, media_id="1396")
        episode = Item.objects.get(media_type=MediaTypes.EPISODE.value, media_id="1396")
        self.assertEqual(self.genres(show), {"Drama"})
        self.assertEqual((season.release_year, season.runtime_minutes), (2008, 47))
        self.assertEqual((episode.release_date, episode.runtime_minutes), (date(2008, 1, 27), 48))
        self.assertEqual(self.genres(episode), set())
        for item in (show, season, episode):
            self.assert_stamped(item)

    def test_simkl_stores_show_season_episode_and_movie(self):
        show_metadata = {
            "title": "Breaking Bad",
            "image": "tv.jpg",
            "details": {"first_air_date": "2008-01-20"},
            "genres": [{"name": "Drama"}],
            "season/1": {
                "title": "Breaking Bad",
                "image": "season.jpg",
                "max_progress": 1,
                "details": {"first_air_date": "2008-01-20", "runtime": "58m"},
                "episodes": [
                    {"episode_number": 1, "still_path": "/e1.jpg", "air_date": "2008-01-20", "runtime": 58},
                ],
            },
        }
        tv = {
            "show": {"title": "Breaking Bad", "ids": {"tmdb": 1396}},
            "status": "completed",
            "seasons": [{"number": 1, "episodes": [{"number": 1, "watched_at": "2024-01-01T00:00:00Z"}]}],
            "user_rating": None,
            "memo": {},
            "last_watched_at": "2024-01-01T00:00:00Z",
        }
        movie = {
            "movie": {"title": "Heat", "ids": {"tmdb": 949}},
            "status": "completed",
            "user_rating": 8,
            "memo": {},
            "last_watched_at": "2024-01-02T00:00:00Z",
        }
        importer = SimklImporter(helpers.encrypt("token"), self.user, "new")
        with (
            patch("app.providers.tmdb.tv_with_seasons", return_value=show_metadata),
            patch("app.providers.tmdb.movie", return_value=movie_metadata()),
        ):
            importer._process_tv_list([tv])
            importer._process_movie_list([movie])

        episode = Item.objects.get(media_type=MediaTypes.EPISODE.value, media_id="1396")
        self.assertEqual((episode.release_date, episode.runtime_minutes), (date(2008, 1, 20), 58))
        self.assertEqual(Item.objects.get(media_type=MediaTypes.SEASON.value).runtime_minutes, 58)
        movie_item = Item.objects.get(media_type=MediaTypes.MOVIE.value, media_id="949")
        self.assertEqual(movie_item.release_year, 1995)
        for item in Item.objects.all():
            self.assert_stamped(item)

    @override_settings(STEAM_API_KEY="test-key")
    def test_steam_stores_the_igdb_record_it_fetched(self):
        game = {
            "title": "Counter-Strike 2",
            "image": "cs2.jpg",
            "details": {"release_date": "2023-09-27"},
            "genres": [{"name": "Shooter"}],
            "themes": [{"name": "Action"}],
        }
        importer = SteamImporter("76561198000000000", self.user, "new")
        with (
            patch("integrations.imports.steam.external_game", return_value=1),
            patch("integrations.imports.steam.services.get_media_metadata", return_value=game),
        ):
            importer._process_game({"appid": 730, "name": "Counter-Strike 2", "playtime_forever": 30})

        item = Item.objects.get(source=Sources.IGDB.value, media_id="1")
        self.assertEqual(item.release_year, 2023)
        self.assertEqual(self.genres(item), {"Shooter"})
        self.assert_stamped(item)

    def test_mal_export_stores_fetched_records_and_the_backfill_stores_them_too(self):
        anime = {
            "title": "Steins;Gate",
            "image": "sg.jpg",
            "details": {"start_date": "2011-04-06", "runtime": "24 min"},
            "genres": ["Sci-Fi"],
        }
        entry = SimpleNamespace(media_type=MediaTypes.ANIME.value, media_id="9253", title="Steins;Gate")
        items = {}
        MALExportImporter(None, self.user, "new")._ensure_items(
            [entry],
            items,
            {(MediaTypes.ANIME.value, "9253"): anime},
        )
        item = items[(MediaTypes.ANIME.value, "9253")]
        item.refresh_from_db()
        self.assertEqual((item.release_year, item.runtime_minutes), (2011, 24))
        self.assert_stamped(item)

        placeholder = Item.objects.create(
            media_id="5114",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="FMA",
            image=settings.IMG_NONE,
        )
        failed = backfill_metadata(
            [placeholder.id],
            fetcher=lambda _media_type, _media_id: {**anime, "image": "fma.jpg"},
        )
        self.assertEqual(failed, [])
        placeholder.refresh_from_db()
        self.assertEqual((placeholder.image, placeholder.release_year), ("fma.jpg", 2011))
        self.assert_stamped(placeholder)

    def test_yamtrack_stores_the_record_fetched_for_a_missing_title(self):
        row = {
            "media_id": "949",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "",
            "image": "",
            "season_number": "",
            "episode_number": "",
            "score": "",
            "progress": "",
            "status": "Completed",
            "start_date": "",
            "end_date": "",
            "notes": "",
        }
        with patch(
            "integrations.imports.yamtrack.services.get_media_metadata",
            return_value=movie_metadata(),
        ):
            YamtrackImporter(None, self.user, "new")._process_row(row)

        item = Item.objects.get(media_id="949")
        self.assertEqual((item.title, item.release_year), ("Heat", 1995))
        self.assert_stamped(item)

    def test_mal_list_asks_for_the_stored_fields_and_stamps_them(self):
        content = {
            "node": {
                "id": 9253,
                "title": "Steins;Gate",
                "main_picture": {"large": "sg.jpg"},
                "num_episodes": 24,
                "start_date": "2011-04-06",
                "average_episode_duration": 1460,
                "genres": [{"id": 24, "name": "Sci-Fi"}],
            },
            "list_status": {
                "status": "completed",
                "score": 9,
                "num_episodes_watched": 24,
                "num_times_rewatched": 0,
                "is_rewatching": False,
                "comments": "",
                "updated_at": "2024-01-01T00:00:00+00:00",
            },
        }
        importer = MyAnimeListImporter("mal-user", self.user, "new")
        with patch.object(
            MyAnimeListImporter,
            "_get_whole_response",
            return_value={"data": [content], "paging": {}},
        ) as fetch:
            importer._process_media_type(MediaTypes.ANIME.value)

        fields = fetch.call_args.args[1]["fields"]
        for field in ("start_date", "average_episode_duration", "genres"):
            self.assertIn(field, fields)
        item = Item.objects.get(media_id="9253")
        self.assertEqual((item.release_year, item.runtime_minutes), (2011, 24))
        self.assertEqual(self.genres(item), {"Sci-Fi"})
        self.assert_stamped(item)


class SeriesCollectorTests(ImportMetadataTestCase):
    """New series members join import_media's one ratings batch."""

    def test_series_members_reach_import_medias_collector(self):
        metadata = movie_metadata(
            details={
                "release_date": "1995-12-15",
                "series_id": "9999",
                "series_source": Sources.TMDB.value,
                "series_media_type": MediaTypes.MOVIE.value,
                "series_name": "Heat Collection",
            },
            related={
                "collection": [
                    {
                        "media_id": media_id,
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": f"Film {media_id}",
                        "image": settings.IMG_NONE,
                    }
                    for media_id in ("949", "950", "951")
                ],
                "recommendations": [],
            },
        )

        def run_import(_identifier, user, mode):
            LetterboxdImporter(None, user, mode, resolver=Mock())._get_or_create_item("949", {"name": "Heat"})
            return {}, None

        with (
            patch("integrations.imports.letterboxd.importer.tmdb.movie", return_value=metadata),
            patch("integrations.tasks.enqueue_external_rating_batches") as import_batch,
            patch("app.tasks.enqueue_external_rating_batches") as own_batch,
            patch("app.signals.enrich_external_ratings") as enrich_one,
            patch("integrations.tasks.events.tasks.reload_calendar"),
            patch("integrations.tasks.sweep_item_metadata"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            import_media(run_import, None, self.user.id, "new")

        self.assertEqual(MediaSeries.objects.get(series_id="9999").memberships.count(), 3)
        ids = sorted(Item.objects.filter(media_id__in=["949", "950", "951"]).values_list("id", flat=True))
        import_batch.assert_called_once_with(ids)
        own_batch.assert_not_called()
        enrich_one.delay.assert_not_called()


class ImportStartsMetadataFillTests(ImportMetadataTestCase):
    """An import's titles get their details soon after, not at the next hourly sweep."""

    def test_an_import_starts_the_fill_a_minute_after_it_finishes(self):
        with (
            patch("integrations.tasks.events.tasks.reload_calendar"),
            patch("integrations.tasks.sweep_item_metadata") as sweep,
        ):
            import_media(lambda _identifier, _user, _mode: ({}, None), None, self.user.id, "new")

        sweep.apply_async.assert_called_once_with(countdown=60)


class PartialMetadataImportTests(ImportMetadataTestCase):
    """Partial importers store free fields only: never stamped, nothing queued."""

    def run_partial(self, function):
        """Run an import step as import_media does and return the queued callbacks."""
        with collect_external_rating_item_ids(), self.captureOnCommitCallbacks() as callbacks:
            function()
        return callbacks

    def test_anilist_stores_start_date_and_length(self):
        content = {
            "media": {
                "title": {"userPreferred": "Steins;Gate"},
                "coverImage": {"large": "sg.jpg"},
                "idMal": 9253,
                "episodes": 24,
                "startDate": {"year": 2011, "month": 4, "day": None},
                "duration": 24,
            },
            "status": "COMPLETED",
            "score": 9,
            "progress": 24,
            "startedAt": {"year": None, "month": None, "day": None},
            "completedAt": {"year": None, "month": None, "day": None},
            "updatedAt": 0,
            "repeat": 0,
            "notes": "",
        }
        importer = AniListImporter(None, self.user, "new", "anilist-user")

        callbacks = self.run_partial(lambda: importer._process_entry(content, MediaTypes.ANIME.value))

        self.assertEqual(callbacks, [])
        item = Item.objects.get(media_id="9253")
        self.assertEqual((item.release_date, item.runtime_minutes), (date(2011, 4, 1), 24))
        self.assert_stamped(item, stamped=False)

    def test_kitsu_stores_start_date_and_episode_length(self):
        kitsu = {
            "attributes": {
                "canonicalTitle": "Steins;Gate",
                "posterImage": {"medium": "sg.jpg"},
                "startDate": "2011-04-06",
                "episodeLength": 24,
            },
            "relationships": {"mappings": {"data": [{"id": "m1"}]}},
        }
        mappings = {"m1": {"attributes": {"externalSite": "myanimelist/anime", "externalId": "9253"}}}
        importer = KitsuImporter("1", self.user, "new")

        callbacks = self.run_partial(
            lambda: importer._create_or_get_item(MediaTypes.ANIME.value, kitsu, mappings),
        )

        self.assertEqual(callbacks, [])
        item = Item.objects.get(media_id="9253")
        self.assertEqual((item.release_year, item.runtime_minutes), (2011, 24))
        self.assert_stamped(item, stamped=False)

    def test_imdb_stores_tmdbs_date_and_the_exports_runtime(self):
        row = {
            "Const": "tt0113277",
            "Title": "Heat",
            "Title Type": "Movie",
            "Runtime (mins)": "170",
            "Your Rating": "9",
            "Created": "2024-01-01",
            "Modified": "",
            "Date Rated": "",
        }
        found = {"movie_results": [{"id": 949, "title": "Heat", "poster_path": "/heat.jpg", "release_date": "1995-12-15"}]}
        importer = IMDBImporter(None, self.user, "new")

        with patch("integrations.imports.imdb.app.providers.tmdb.find", return_value=found):
            callbacks = self.run_partial(lambda: importer._process_second_pass(row, {949: 1}))

        self.assertEqual(callbacks, [])
        item = Item.objects.get(media_id="949")
        self.assertEqual((item.release_date, item.runtime_minutes), (date(1995, 12, 15), 170))
        self.assert_stamped(item, stamped=False)

    def test_goodreads_stores_the_original_publication_year(self):
        row = SimpleNamespace(title="Dune", number_of_pages=412, original_publication_year=1965)
        result = {"media_id": "312", "source": Sources.HARDCOVER.value, "title": "Dune", "image": "dune.jpg"}
        importer = GoodReadsImporter(None, self.user, "new", resolver=Mock())

        callbacks = self.run_partial(lambda: importer._get_or_create_item(row, result))

        self.assertEqual(callbacks, [])
        item = Item.objects.get(media_id="312")
        self.assertEqual((item.release_year, item.release_date), (1965, None))
        self.assert_stamped(item, stamped=False)

    def test_storygraph_stores_the_search_results_year(self):
        row = SimpleNamespace(title="Dune")
        result = {
            "media_id": "312",
            "source": Sources.HARDCOVER.value,
            "title": "Dune",
            "image": "dune.jpg",
            "first_publish_year": 1965,
        }
        importer = StoryGraphImporter(None, self.user, "new", resolver=Mock())

        callbacks = self.run_partial(lambda: importer._get_or_create_item(row, result))

        self.assertEqual(callbacks, [])
        self.assertEqual(Item.objects.get(media_id="312").release_year, 1965)
        self.assert_stamped(Item.objects.get(media_id="312"), stamped=False)

    def test_hltb_stores_the_search_results_release_date(self):
        game = {"media_id": 1942, "title": "The Witcher 3", "image": "w3.jpg", "release_date": "2015-05-19"}
        importer = HowLongToBeatImporter(None, self.user, "new")

        with (
            patch.object(HowLongToBeatImporter, "_search_game", return_value=game),
            patch.object(HowLongToBeatImporter, "_create_media_instance"),
        ):
            callbacks = self.run_partial(lambda: importer._process_second_pass({"Title": "W3"}, {1942: 1}))

        self.assertEqual(callbacks, [])
        item = Item.objects.get(media_id="1942")
        self.assertEqual(item.release_date, date(2015, 5, 19))
        self.assert_stamped(item, stamped=False)

    def test_yamtrack_search_result_keeps_the_stored_languages(self):
        item = Item.objects.create(
            media_id="949",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Heat",
            image="heat.jpg",
        )
        for language in ("English", "Spanish"):
            ItemFilterFacet.objects.create(item=item, facet_type=ItemFilterFacet.FacetType.LANGUAGE, value=language)
        row = {
            "media_id": "",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Heat",
            "image": "",
            "season_number": "",
            "episode_number": "",
            "score": "",
            "progress": "",
            "status": "Completed",
            "start_date": "",
            "end_date": "",
            "notes": "",
        }
        # A TMDB search result: its languages are only the original language.
        result = {
            "media_id": "949",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Heat",
            "image": "heat.jpg",
            "release_date": "1995-12-15",
            "genres": ["Crime"],
            "languages": ["English"],
        }

        with patch("integrations.imports.yamtrack.services.search", return_value={"results": [result]}):
            callbacks = self.run_partial(lambda: YamtrackImporter(None, self.user, "new")._process_row(row))

        self.assertEqual(callbacks, [])
        languages = set(
            ItemFilterFacet.objects.filter(
                item=item,
                facet_type=ItemFilterFacet.FacetType.LANGUAGE,
            ).values_list("value", flat=True),
        )
        self.assertEqual(languages, {"English", "Spanish"})
        self.assertEqual(self.genres(item), {"Crime"})
        self.assert_stamped(item, stamped=False)

    def test_free_fields_never_touch_an_item_holding_its_full_record(self):
        item = Item.objects.create(
            media_id="312",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Dune",
            image="dune.jpg",
            release_year=1965,
            metadata_version=CURRENT_METADATA_VERSION[MediaTypes.BOOK.value],
        )

        helpers.store_free_metadata(item, {"first_publish_year": 1990})

        item.refresh_from_db()
        self.assertEqual(item.release_year, 1965)


class WebhookMetadataTests(ImportMetadataTestCase):
    """The movie, anime and TV webhooks store the record they fetched."""

    def setUp(self):
        super().setUp()
        self.processor = PlexWebhookProcessor()
        patcher = patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_movie_webhook_stores_the_tmdb_record(self):
        with (
            patch("integrations.webhooks.movie.app.providers.tmdb.movie", return_value=movie_metadata()),
            patch.object(PlexWebhookProcessor, "_is_unplayed", return_value=False),
            patch.object(PlexWebhookProcessor, "_is_played", return_value=True),
            patch("integrations.webhooks.movie.single_weight.mark_consumed"),
        ):
            self.processor._handle_movie("949", {}, self.user)

        item = Item.objects.get(media_id="949")
        self.assertEqual(item.release_year, 1995)
        self.assert_stamped(item)

    def test_anime_webhook_stores_the_mal_record(self):
        anime = {
            "title": "Steins;Gate",
            "image": "sg.jpg",
            "max_progress": 24,
            "details": {"start_date": "2011-04-06", "runtime": "24 min"},
        }
        with (
            patch("integrations.webhooks.anime.app.providers.mal.anime", return_value=anime),
            patch.object(PlexWebhookProcessor, "_is_unplayed", return_value=False),
            patch.object(PlexWebhookProcessor, "_is_played", return_value=True),
        ):
            self.processor._handle_anime("9253", 1, {}, self.user)

        item = Item.objects.get(media_id="9253")
        self.assertEqual((item.release_year, item.runtime_minutes), (2011, 24))
        self.assert_stamped(item)

    def test_tv_webhook_stores_the_show_record(self):
        show = {"title": "Breaking Bad", "image": "tv.jpg", "details": {"first_air_date": "2008-01-20"}}
        with patch("integrations.webhooks.tv.services.get_media_metadata", return_value=show):
            item = self.processor._get_tv_show_item("1396")

        item.refresh_from_db()
        self.assertEqual(item.release_year, 2008)
        self.assert_stamped(item)
