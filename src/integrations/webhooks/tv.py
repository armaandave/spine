import logging

from django.conf import settings

import app
from app import tv_listing, tv_tracking, user_calendar
from app.models import MediaTypes, Sources
from app.providers import services
from app.providers import tvdb as tvdb_provider

from . import anime_mappings

logger = logging.getLogger(__name__)


class TVWebhookMixin:
    """TV-specific webhook processing."""

    def _process_tv(self, payload, user, ids):
        anidb_id = ids.get("anidb_id")
        if user.anime_enabled and anidb_id:
            mapping_data = anime_mappings.fetch_mapping_data()
            episode_number = self._get_episode_number(payload)
            mal_id = None
            mal_episode_number = None

            if not episode_number:
                logger.warning(
                    "No episode number found for AniDB ID: %s",
                    anidb_id,
                )
            else:
                mal_id, mal_episode_number = anime_mappings.get_mal_id_from_anidb(
                    mapping_data,
                    anidb_id,
                    episode_number,
                )

            if episode_number and not mal_id:
                logger.info(
                    "AniDB ID %s not found in mapping, "
                    "falling through to TV processing",
                    anidb_id,
                )
            elif episode_number:
                logger.info(
                    "Detected anime via AniDB ID: %s. Matching MAL ID: %s, Episode: %d",
                    anidb_id,
                    mal_id,
                    mal_episode_number,
                )
                self._handle_anime(
                    mal_id,
                    mal_episode_number,
                    payload,
                    user,
                )
                return

        tvdb_episode_id = ids.get("tvdb_id")
        if tvdb_episode_id:
            tvdb_episode = tvdb_provider.episode(int(tvdb_episode_id))
            if not tvdb_episode:
                logger.warning(
                    "No TVDB episode metadata found for TVDB episode ID: %s",
                    tvdb_episode_id,
                )
            elif self._process_tvdb_episode(
                tvdb_episode, tvdb_episode_id, payload, user
            ):
                return
        else:
            logger.debug("No TVDB episode ID found for TV episode")

        imdb_id = ids.get("imdb_id")
        if imdb_id and self._process_imdb_episode(imdb_id, payload, user):
            return

        if imdb_id:
            logger.warning("No matching TMDB ID found for IMDB ID: %s", imdb_id)
        else:
            logger.warning("No TVDB or IMDB ID found for TV episode")

    def _process_tvdb_episode(self, tvdb_episode, tvdb_episode_id, payload, user):
        """Process a TV episode using TVDB metadata."""
        if user.anime_enabled:
            mapping_data = anime_mappings.fetch_mapping_data()
            mal_id, episode_offset = anime_mappings.get_mal_id_from_tvdb(
                mapping_data,
                tvdb_episode["series_id"],
                tvdb_episode["season_number"],
                tvdb_episode["episode_number"],
            )
            if mal_id:
                logger.info(
                    "Detected anime episode via MAL ID: %s, Episode: %d",
                    mal_id,
                    episode_offset,
                )
                self._handle_anime(mal_id, episode_offset, payload, user)
                return True

        media_id, season_number, episode_number = self._find_tv_media_id(
            tvdb_episode_id,
            "tvdb_id",
        )
        if not media_id:
            media_id = tvdb_provider.series_tmdb_id(tvdb_episode["series_id"])
            season_number = tvdb_episode["season_number"]
            episode_number = tvdb_episode["episode_number"]

        if not media_id:
            logger.warning(
                "No matching TMDB ID found for TVDB episode ID: %s", tvdb_episode_id
            )
            return False

        logger.info(
            "Detected TV episode via TMDB ID: %s, Season: %d, Episode: %d",
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)
        return True

    def _process_imdb_episode(self, imdb_id, payload, user):
        """Process a TV episode using IMDB as a TVDB fallback."""
        media_id, season_number, episode_number = self._find_tv_media_id(
            imdb_id,
            "imdb_id",
        )
        if not media_id:
            return False

        logger.info(
            "Detected TV episode via IMDB ID: %s, TMDB ID: %s, Season: %d, Episode: %d",
            imdb_id,
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)
        return True

    def _find_tv_media_id(self, external_id, external_source):
        """Find TMDB TV episode metadata from an external ID."""
        if external_id:
            response = app.providers.tmdb.find(external_id, external_source)
            if response.get("tv_episode_results"):
                result = response["tv_episode_results"][0]
                return (
                    result.get("show_id"),
                    result.get("season_number"),
                    result.get("episode_number"),
                )
        return None, None, None

    def _handle_tv_episode(
        self,
        media_id,
        season_number,
        episode_number,
        payload,
        user,
    ):
        """Apply a TV episode event the way the app would (TV-802).

        A watch ticks the episode at the event time, an unplayed event unticks it, and
        any other play counts as choosing Watching. Nothing else is created: no season
        tracking and no status of its own. A rejected event is logged and acknowledged.
        """
        if self._is_unplayed(payload):
            event = "unplayed"
        elif self._is_played(payload):
            event = "watched"
        else:
            event = "play"

        show_item = self._get_tv_show_item(media_id, create=event != "unplayed")
        if show_item is None:
            logger.debug(
                "Ignoring TV %s event for a show that isn't tracked: %s S%02dE%02d",
                event,
                media_id,
                season_number,
                episode_number,
            )
            return

        result = tv_tracking.webhook_event(
            user,
            show_item,
            season_number,
            episode_number,
            event=event,
            at=user_calendar.now(),
        )
        if result is not None and result.changed:
            logger.info(
                "Applied TV %s event: %s S%02dE%02d",
                event,
                show_item.title,
                season_number,
                episode_number,
            )
        else:
            logger.debug(
                "TV %s event changed nothing: %s S%02dE%02d",
                event,
                show_item.title,
                season_number,
                episode_number,
            )

    def _get_tv_show_item(self, media_id, *, create=True):
        """Return the TMDB show's tv Item, creating it from its metadata when needed."""
        lookup = {
            "media_id": str(media_id),
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "season_number": None,
            "episode_number": None,
        }
        show_item = app.models.Item.objects.filter(**lookup).first()
        if show_item is not None or not create:
            return show_item
        try:
            metadata = services.get_media_metadata(
                MediaTypes.TV.value,
                str(media_id),
                Sources.TMDB.value,
            )
        except tv_listing.PROVIDER_FAILURES:
            logger.warning("Could not fetch TV details for TMDB ID %s", media_id)
            return None
        show_item, _ = app.models.Item.objects.get_or_create(
            **lookup,
            defaults={
                "title": metadata.get("title") or "",
                "image": metadata.get("image") or settings.IMG_NONE,
            },
        )
        return show_item
