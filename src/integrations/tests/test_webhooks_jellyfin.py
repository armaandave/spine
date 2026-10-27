import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from integrations.webhooks.jellyfin import JellyfinWebhookProcessor
from integrations.tests.webhook_provider_mocks import WebhookProviderMocksMixin


class JellyfinWebhookTests(WebhookProviderMocksMixin, TestCase):
    """Tests for Jellyfin webhook."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "testuser", "token": "test-token"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("jellyfin_webhook", kwargs={"token": "test-token"})
        self.patch_webhook_providers()

    def test_invalid_token(self):
        """Test webhook with invalid token returns 401."""
        url = reverse("jellyfin_webhook", kwargs={"token": "invalid-token"})
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_tv_episode_mark_played(self):
        """Test webhook handles TV episode mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "The One Where Monica Gets a Roommate",
                "ProviderIds": {
                    "Tvdb": "303821",
                    "Imdb": "tt0583459",
                },
                "SeriesName": "Friends",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify objects were created
        tv_item = Item.objects.get(media_type=MediaTypes.TV.value, media_id="1668")
        self.assertEqual(tv_item.title, "Friends")

        tv = TV.objects.get(item=tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

        season = Season.objects.get(
            item__media_id="1668",
            item__season_number=1,
        )
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

        episode = Episode.objects.get(
            item__media_id="1668",
            item__season_number=1,
            item__episode_number=1,
        )
        self.assertIsNotNone(episode.end_date)

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("integrations.webhooks.tv.tvdb_provider.series_tmdb_id")
    @patch("app.providers.tmdb.find")
    @patch("integrations.webhooks.tv.tvdb_provider.episode")
    def test_tv_episode_uses_imdb_fallback_when_tvdb_missing(
        self,
        mock_tvdb_episode,
        mock_tmdb_find,
        mock_series_tmdb_id,
        mock_handle_tv_episode,
    ):
        """Test TV episodes can resolve through IMDB when TVDB is missing."""
        mock_tmdb_find.return_value = {
            "tv_episode_results": [
                {
                    "show_id": 12345,
                    "season_number": 2,
                    "episode_number": 8,
                },
            ],
        }
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "Episode",
                "ProviderIds": {
                    "Tmdb": "",
                    "Imdb": "tt38990690",
                    "Tvdb": "",
                },
                "SeriesName": "Test Show",
                "ParentIndexNumber": 2,
                "IndexNumber": 8,
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_tvdb_episode.assert_not_called()
        mock_series_tmdb_id.assert_not_called()
        mock_tmdb_find.assert_called_once_with("tt38990690", "imdb_id")
        mock_handle_tv_episode.assert_called_once_with(
            12345,
            2,
            8,
            payload,
            self.user,
        )

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("integrations.webhooks.tv.tvdb_provider.series_tmdb_id")
    @patch("app.providers.tmdb.find")
    @patch("integrations.webhooks.tv.tvdb_provider.episode")
    def test_tv_episode_uses_imdb_fallback_when_tvdb_tmdb_lookup_misses(
        self,
        mock_tvdb_episode,
        mock_tmdb_find,
        mock_series_tmdb_id,
        mock_handle_tv_episode,
    ):
        """Test IMDB is used after TVDB when TMDB cannot match the TVDB ID."""
        self.user.anime_enabled = False
        self.user.save(update_fields=["anime_enabled"])
        mock_series_tmdb_id.return_value = None
        mock_tvdb_episode.return_value = {
            "episode_id": 999,
            "series_id": 123,
            "season_number": 2,
            "episode_number": 8,
        }
        mock_tmdb_find.side_effect = [
            {"tv_episode_results": []},
            {
                "tv_episode_results": [
                    {
                        "show_id": 12345,
                        "season_number": 2,
                        "episode_number": 8,
                    },
                ],
            },
        ]
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "Episode",
                "ProviderIds": {
                    "Imdb": "tt38990690",
                    "Tvdb": "999",
                },
                "SeriesName": "Test Show",
                "ParentIndexNumber": 2,
                "IndexNumber": 8,
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_tvdb_episode.assert_called_once_with(999)
        self.assertEqual(mock_tmdb_find.call_count, 2)
        mock_tmdb_find.assert_any_call("999", "tvdb_id")
        mock_tmdb_find.assert_any_call("tt38990690", "imdb_id")
        mock_series_tmdb_id.assert_called_once_with(123)
        mock_handle_tv_episode.assert_called_once_with(
            12345,
            2,
            8,
            payload,
            self.user,
        )

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("integrations.webhooks.tv.tvdb_provider.series_tmdb_id")
    @patch("app.providers.tmdb.find")
    @patch("integrations.webhooks.tv.tvdb_provider.episode")
    def test_tv_episode_uses_tvdb_series_tmdb_id_when_tmdb_find_misses(
        self,
        mock_tvdb_episode,
        mock_tmdb_find,
        mock_series_tmdb_id,
        mock_handle_tv_episode,
    ):
        """Test TVDB series remote IDs are used when TMDB cannot find the episode."""
        self.user.anime_enabled = False
        self.user.save(update_fields=["anime_enabled"])
        mock_tvdb_episode.return_value = {
            "episode_id": 999,
            "series_id": 459821,
            "season_number": 1,
            "episode_number": 3,
        }
        mock_tmdb_find.return_value = {"tv_episode_results": []}
        mock_series_tmdb_id.return_value = "283657"
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "Episode",
                "ProviderIds": {
                    "Imdb": "tt35668375",
                    "Tvdb": "999",
                },
                "SeriesName": "Glory",
                "ParentIndexNumber": 1,
                "IndexNumber": 3,
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_tvdb_episode.assert_called_once_with(999)
        mock_tmdb_find.assert_called_once_with("999", "tvdb_id")
        mock_series_tmdb_id.assert_called_once_with(459821)
        mock_handle_tv_episode.assert_called_once_with(
            "283657",
            1,
            3,
            payload,
            self.user,
        )

    def test_mark_played_ignored_when_disabled(self):
        """Test Jellyfin MarkPlayed events are ignored by default."""
        payload = {
            "Event": "MarkPlayed",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_mark_played_enabled(self):
        """Test Jellyfin MarkPlayed events are handled when enabled."""
        self.user.jellyfin_mark_played_enabled = True
        self.user.save(update_fields=["jellyfin_mark_played_enabled"])
        payload = {
            "Event": "MarkPlayed",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.get(
            item__media_id="603",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    def test_mark_unplayed_enabled_deletes_movie_instance(self):
        """Test Jellyfin MarkUnplayed removes a watched movie when enabled."""
        self.user.jellyfin_mark_played_enabled = True
        self.user.jellyfin_mark_unplayed_enabled = True
        self.user.save(
            update_fields=[
                "jellyfin_mark_played_enabled",
                "jellyfin_mark_unplayed_enabled",
            ],
        )
        payload = {
            "Event": "MarkPlayed",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        payload["Event"] = "MarkUnplayed"
        payload["Item"]["UserData"]["Played"] = False
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_mark_unplayed_enabled_deletes_tv_episode(self):
        """Test Jellyfin MarkUnplayed removes a watched episode when enabled."""
        self.user.jellyfin_mark_played_enabled = True
        self.user.jellyfin_mark_unplayed_enabled = True
        self.user.save(
            update_fields=[
                "jellyfin_mark_played_enabled",
                "jellyfin_mark_unplayed_enabled",
            ],
        )
        payload = {
            "Event": "MarkPlayed",
            "Item": {
                "Type": "Episode",
                "Name": "The One Where Monica Gets a Roommate",
                "ProviderIds": {
                    "Tvdb": "303821",
                    "Imdb": "tt0583459",
                },
                "SeriesName": "Friends",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "UserData": {"Played": True},
            },
        }

        self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        payload["Event"] = "MarkUnplayed"
        payload["Item"]["UserData"]["Played"] = False
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Episode.objects.count(), 0)

    def test_mark_unplayed_completed_episode_updates_parent_statuses(self):
        """Unplaying a finale unticks it, so the finished season and show reopen.

        Rewritten for TV-802 and TV-102: the events tick and untick through
        app.tv_tracking instead of the model cascades, and the worked-out season and
        show statuses follow the viewings that remain (TV-200, TV-201).
        """
        processor = JellyfinWebhookProcessor()
        played = {"Event": "MarkPlayed", "Item": {"UserData": {"Played": True}}}
        unplayed = {"Event": "MarkUnplayed", "Item": {"UserData": {"Played": False}}}
        processor._handle_tv_episode("1668", 1, 1, played, self.user)
        processor._handle_tv_episode("1668", 1, 2, played, self.user)
        tv = TV.objects.get(user=self.user, item__media_id="1668")
        season = Season.objects.get(user=self.user, item__media_id="1668", item__season_number=1)
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertEqual(tv.status, Status.COMPLETED.value)

        processor._handle_tv_episode("1668", 1, 2, unplayed, self.user)

        self.assertEqual(Episode.objects.filter(related_season=season).count(), 1)
        season.refresh_from_db()
        tv.refresh_from_db()
        self.assertEqual(season.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)

    def test_mark_unplayed_untracked_movie_does_not_create_item(self):
        """Test Jellyfin MarkUnplayed does not create metadata for missing media."""
        self.user.jellyfin_mark_unplayed_enabled = True
        self.user.save(update_fields=["jellyfin_mark_unplayed_enabled"])
        payload = {
            "Event": "MarkUnplayed",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": False},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)
        self.assertFalse(
            Item.objects.filter(
                media_id="603",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
            ).exists(),
        )

    def test_mark_unplayed_untracked_anime_does_not_create_item(self):
        """Test anime MarkUnplayed does not create metadata for missing media."""
        payload = {
            "Event": "MarkUnplayed",
            "Item": {
                "UserData": {"Played": False},
            },
        }

        JellyfinWebhookProcessor()._handle_anime("437", 1, payload, self.user)

        self.assertEqual(Anime.objects.count(), 0)
        self.assertFalse(
            Item.objects.filter(
                media_id="437",
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
            ).exists(),
        )

    def test_movie_mark_played(self):
        """Test webhook handles movie mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Movie.objects.get(
            item__media_id="603",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    @patch(
        "integrations.webhooks.anime.app.providers.mal.anime",
        return_value={
            "title": "Perfect Blue",
            "image": "perfect-blue.jpg",
            "max_progress": 1,
        },
    )
    @patch(
        "integrations.webhooks.movie.anime_mappings.get_mal_id_from_imdb",
        return_value=437,
    )
    @patch(
        "integrations.webhooks.movie.anime_mappings.fetch_mapping_data",
        return_value={},
    )
    def test_anime_movie_mark_played(self, *_mocks):
        """Test webhook handles movie mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Name": "Perfect Blue",
                "ProductionYear": 1997,
                "Type": "Movie",
                "ProviderIds": {"Imdb": "tt0156887"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify movie was created and marked as completed
        movie = Anime.objects.get(
            item__media_id="437",
            user=self.user,
        )
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

    @patch(
        "integrations.webhooks.anime.app.providers.mal.anime",
        return_value={
            "title": "Frieren: Beyond Journey's End",
            "image": "frieren.jpg",
            "max_progress": 28,
        },
    )
    @patch(
        "integrations.webhooks.tv.anime_mappings.get_mal_id_from_tvdb",
        return_value=(52991, 1),
    )
    @patch(
        "integrations.webhooks.tv.anime_mappings.fetch_mapping_data",
        return_value={},
    )
    @patch(
        "integrations.webhooks.tv.tvdb_provider.episode",
        return_value={
            "series_id": 424536,
            "season_number": 1,
            "episode_number": 1,
        },
    )
    def test_anime_episode_mark_played(self, *_mocks):
        """Test webhook handles anime episode mark played event."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Episode",
                "Name": "The Journey's End",
                "ProviderIds": {
                    "Tvdb": "9350138",
                    "Imdb": "tt23861604",
                },
                "UserData": {"Played": True},
                "SeriesName": "Frieren: Beyond Journey's End",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        # Verify anime was created and marked as in progress
        anime = Anime.objects.get(
            item__media_id="52991",
            user=self.user,
        )
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(anime.progress, 1)

    def test_ignored_event_types(self):
        """Test webhook ignores irrelevant event types."""
        payload = {
            "Event": "SomeOtherEvent",
            "Item": {
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "12345"},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_missing_tmdb_id(self):
        """Test webhook handles missing TMDB ID gracefully."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "ProviderIds": {},
                "UserData": {"Played": True},
            },
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Movie.objects.count(), 0)

    def test_mark_unplayed(self):
        """Test webhook handles not finished events."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": False},
            },
        }
        self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.get(item__media_id="603")
        self.assertEqual(movie.progress, 0)
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)

    def test_repeated_watch(self):
        """Test webhook handles repeated watches."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "ProductionYear": 1999,
                "Name": "The Matrix",
                "ProviderIds": {"Tmdb": "603"},
                "UserData": {"Played": True},
            },
        }

        # First watch
        self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        # Second watch
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        movie = Movie.objects.filter(item__media_id="603")
        self.assertEqual(movie.count(), 1)
        self.assertEqual(movie[0].status, Status.COMPLETED.value)
        self.assertTrue(movie[0].direct_consumption)
        self.assertIsNone(movie[0].end_date)

    def test_extract_external_ids(self):
        """Test extracting external IDs from provider payload."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {
                    "Tmdb": "603",
                    "Tvdb": "169",
                },
            },
        }

        expected = {
            "tmdb_id": "603",
            "imdb_id": None,
            "tvdb_id": "169",
        }

        result = JellyfinWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_empty(self):
        """Test handling empty provider payload."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
                "ProviderIds": {},
            },
        }

        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = JellyfinWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_extract_external_ids_missing(self):
        """Test handling missing ProviderIds."""
        payload = {
            "Event": "Stop",
            "Item": {
                "Type": "Movie",
                "Name": "The Matrix",
                "ProductionYear": 1999,
            },
        }
        expected = {
            "tmdb_id": None,
            "imdb_id": None,
            "tvdb_id": None,
        }

        result = JellyfinWebhookProcessor()._extract_external_ids(payload)
        if result != expected:
            msg = f"Expected {expected}, got {result}"
            raise AssertionError(msg)

    def test_get_episode_number(self):
        """Test extracting episode number from Jellyfin payload."""
        payload = {
            "Item": {
                "IndexNumber": 7,
            },
        }

        result = JellyfinWebhookProcessor()._get_episode_number(payload)

        self.assertEqual(result, 7)
