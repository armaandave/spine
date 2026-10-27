"""Deterministic provider doubles shared by webhook unit tests."""

from datetime import date
from unittest.mock import patch

from app.models import MediaTypes


class WebhookProviderMocksMixin:
    """Keep webhook tests independent from external metadata APIs."""

    def patch_webhook_providers(self):
        patchers = [
            patch("integrations.webhooks.tv.tvdb_provider.episode"),
            patch("integrations.webhooks.tv.tvdb_provider.series_tmdb_id"),
            patch("integrations.webhooks.tv.app.providers.tmdb.find"),
            patch("integrations.webhooks.tv.app.providers.tmdb.tv_with_seasons"),
            patch("integrations.webhooks.movie.app.providers.tmdb.movie"),
            patch("integrations.webhooks.anime.app.providers.mal.anime"),
            patch("app.models.providers.services.get_media_metadata"),
            patch(
                "integrations.webhooks.anime_mappings.fetch_mapping_data",
                return_value={},
            ),
            patch(
                "integrations.webhooks.anime_mappings.get_mal_id_from_tvdb",
                side_effect=self._mock_mal_id_from_tvdb,
            ),
            patch(
                "integrations.webhooks.anime_mappings.get_mal_id_from_anidb",
                side_effect=self._mock_mal_id_from_anidb,
            ),
            patch(
                "integrations.webhooks.anime_mappings.get_mal_id_from_tmdb_movie",
                side_effect=self._mock_mal_id_from_tmdb_movie,
            ),
            patch(
                "integrations.webhooks.anime_mappings.get_mal_id_from_imdb",
                return_value=None,
            ),
        ]
        (
            self.mock_tvdb_episode,
            self.mock_series_tmdb_id,
            self.mock_tmdb_find,
            self.mock_tv_with_seasons,
            self.mock_tmdb_movie,
            self.mock_mal_anime,
            self.mock_get_media_metadata,
            *_,
        ) = [patcher.start() for patcher in patchers]
        for patcher in patchers:
            self.addCleanup(patcher.stop)

        self.mock_tvdb_episode.side_effect = self._mock_tvdb_episode
        self.mock_series_tmdb_id.return_value = "1668"
        self.mock_tmdb_find.side_effect = self._mock_tmdb_find
        self.mock_tv_with_seasons.side_effect = self._mock_tv_with_seasons
        self.mock_tmdb_movie.side_effect = self._mock_tmdb_movie
        self.mock_mal_anime.side_effect = self._mock_mal_anime
        self.mock_get_media_metadata.side_effect = self._mock_media_metadata
        self.patch_tv_provider()

    def patch_tv_provider(self):
        """Serve TMDB TV shows from the shared fake TV provider.

        TV events go through app.tv_tracking, which reads the show's episode lists
        with air dates (TV-103); Friends (1668) lists two aired Season 1 episodes.
        Other media types pass through to the mocks above.
        """
        from app.tests.tv_fixtures import FakeTVProvider  # noqa: PLC0415

        self.fake_tv = FakeTVProvider().__enter__()
        self.addCleanup(self.fake_tv.__exit__, None, None, None)
        self.fake_tv.add_show(
            "1668",
            title="Friends",
            seasons={1: [date(1994, 9, 22), date(1994, 9, 29)]},
        )
        return self.fake_tv

    @staticmethod
    def _mock_tvdb_episode(episode_id):
        if episode_id == 9350138:
            return {"series_id": 424536, "season_number": 1, "episode_number": 1}
        if episode_id == 303821:
            return {"series_id": 79168, "season_number": 1, "episode_number": 1}
        return None

    @staticmethod
    def _mock_mal_id_from_tvdb(
        _mapping_data,
        series_id,
        _season_number,
        episode_number,
    ):
        if series_id == 424536:
            return 52991, episode_number
        return None, None

    @staticmethod
    def _mock_mal_id_from_anidb(_mapping_data, anidb_id, episode_number):
        if str(anidb_id) == "3651":
            return 849, episode_number
        return None, None

    @staticmethod
    def _mock_mal_id_from_tmdb_movie(_mapping_data, tmdb_id):
        return 437 if str(tmdb_id) == "10494" else None

    @staticmethod
    def _mock_tmdb_find(external_id, _external_source):
        if str(external_id) in {"303821", "tt0583459"}:
            return {
                "tv_episode_results": [
                    {"show_id": 1668, "season_number": 1, "episode_number": 1},
                ],
            }
        if external_id == "tt0133093":
            return {"movie_results": [{"id": 603}]}
        return {"movie_results": [], "tv_episode_results": []}

    @staticmethod
    def _mock_tv_with_seasons(media_id, seasons):
        return {
            "title": "Friends",
            "image": "friends.jpg",
            f"season/{seasons[0]}": {
                "image": "friends-season-1.jpg",
                "episodes": [
                    {"episode_number": 1, "still_path": "/friends-s01e01.jpg"},
                    {"episode_number": 2, "still_path": "/friends-s01e02.jpg"},
                ],
            },
        }

    @staticmethod
    def _mock_tmdb_movie(media_id):
        titles = {
            "603": "The Matrix",
            603: "The Matrix",
            "10494": "Perfect Blue",
            10494: "Perfect Blue",
        }
        return {
            "title": titles.get(media_id, f"Movie {media_id}"),
            "image": f"{media_id}.jpg",
            "max_progress": 1,
        }

    @staticmethod
    def _mock_mal_anime(media_id):
        metadata = {
            437: {"title": "Perfect Blue", "max_progress": 1},
            52991: {"title": "Frieren: Beyond Journey's End", "max_progress": 28},
        }
        anime = metadata[int(media_id)]
        return {"image": f"{media_id}.jpg", **anime}

    @staticmethod
    def _mock_media_metadata(media_type, media_id, _source, *_args):
        if media_type in {MediaTypes.TV.value, "tv_with_seasons"} and str(media_id) == "1668":
            return {
                "title": "Friends",
                "max_progress": 1,
                "season/1": {
                    "image": "friends-season-1.jpg",
                    "episodes": [
                        {"episode_number": 1, "still_path": "/friends-s01e01.jpg"},
                        {"episode_number": 2, "still_path": "/friends-s01e02.jpg"},
                    ],
                },
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "first_air_date": "1994-09-22",
                            "image": "friends-season-1.jpg",
                        },
                    ],
                },
            }
        if media_type == MediaTypes.ANIME.value and str(media_id) == "52991":
            return {"max_progress": 28}
        return {"max_progress": 1}
