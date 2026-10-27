import logging

from django.utils import timezone

import app
from api.services.item_metadata import store_item_metadata
from app import single_weight
from app.models import MediaTypes, Sources, Status

from . import anime_mappings

logger = logging.getLogger(__name__)


class MovieWebhookMixin:
    """Movie-specific webhook processing."""

    def _process_movie(self, payload, user, ids):
        tmdb_id = ids["tmdb_id"]
        imdb_id = ids["imdb_id"]

        if user.anime_enabled:
            mapping_data = anime_mappings.fetch_mapping_data()
            mal_id = None
            source = None

            if tmdb_id:
                mal_id = anime_mappings.get_mal_id_from_tmdb_movie(
                    mapping_data,
                    tmdb_id,
                )
                source = "TMDB"

            if not mal_id and imdb_id:
                mal_id = anime_mappings.get_mal_id_from_imdb(mapping_data, imdb_id)
                source = "IMDB"

            if mal_id:
                logger.info(
                    "Detected anime movie with MAL ID: %s (via %s)",
                    mal_id,
                    source,
                )
                self._handle_anime(mal_id, 1, payload, user)
                return

        if tmdb_id:
            logger.info("Detected movie via TMDB ID: %s", tmdb_id)
            self._handle_movie(tmdb_id, payload, user)
        elif imdb_id:
            logger.debug("No TMDB ID found, looking up via IMDB ID: %s", imdb_id)
            response = app.providers.tmdb.find(imdb_id, "imdb_id")

            if response.get("movie_results"):
                media_id = response["movie_results"][0]["id"]
                logger.info("Found matching TMDB ID: %s", media_id)
                self._handle_movie(media_id, payload, user)
            else:
                logger.warning(
                    "No matching TMDB ID found for IMDB ID: %s",
                    imdb_id,
                )
        else:
            logger.warning("No TMDB or IMDB ID found for movie, skipping processing")

    def _handle_movie(self, media_id, payload, user):
        """Handle movie playback event."""
        if self._is_unplayed(payload):
            current_instance = self._get_current_instance(
                app.models.Movie,
                media_id,
                Sources.TMDB.value,
                MediaTypes.MOVIE.value,
                user,
            )
            self._delete_media_instance(current_instance, "movie")
            return

        movie_metadata = app.providers.tmdb.movie(media_id)
        movie_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": movie_metadata["title"],
                "image": movie_metadata["image"],
            },
        )
        store_item_metadata(movie_item, movie_metadata)

        current_instance = self._get_current_instance(
            app.models.Movie,
            media_id,
            Sources.TMDB.value,
            MediaTypes.MOVIE.value,
            user,
        )
        movie_played = self._is_played(payload)

        if movie_played:
            current_instance = single_weight.mark_consumed(user, movie_item)
        else:
            current_instance = single_weight.apply_tracking_state(
                user,
                movie_item,
                status=Status.IN_PROGRESS.value,
                start_date=timezone.now().replace(second=0, microsecond=0),
            )
        logger.info("Movie tracking status is now: %s", current_instance.status)
