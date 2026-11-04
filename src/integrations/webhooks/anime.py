import logging

import app
from api.services.item_metadata import store_item_metadata
from app import anime_tracking, user_calendar
from app.models import MediaTypes, Sources

logger = logging.getLogger(__name__)


class AnimeWebhookMixin:
    """Anime-specific webhook processing (AN-801)."""

    def _handle_anime(self, media_id, episode_number, payload, user):
        """Apply a playback event through the same rules as the app's (AN-801, TV-802).

        A watch ticks the episode at the event time, an unplayed event unticks it, and
        any other play counts as choosing Watching. A rejected event is logged and
        acknowledged. Webhooks never ask a question or open anything.
        """
        if self._is_unplayed(payload):
            event = "unplayed"
        elif self._is_played(payload):
            event = "watched"
        else:
            event = "play"

        item = self._get_anime_item(media_id, create=event != "unplayed")
        if item is None:
            logger.debug("Ignoring anime %s event for a title that isn't tracked: %s", event, media_id)
            return

        result = anime_tracking.webhook_event(
            user,
            item,
            episode_number,
            event=event,
            at=user_calendar.now(),
        )
        if result is not None and result.changed:
            logger.info("Applied anime %s event: %s E%s", event, item.title, episode_number)
        else:
            logger.debug("Anime %s event changed nothing: %s E%s", event, item.title, episode_number)

    def _get_anime_item(self, media_id, *, create):
        lookup = {"media_id": media_id, "source": Sources.MAL.value, "media_type": MediaTypes.ANIME.value}
        item = app.models.Item.objects.filter(**lookup).first()
        if item is not None or not create:
            return item
        metadata = app.providers.mal.anime(media_id)
        item, _ = app.models.Item.objects.get_or_create(
            **lookup,
            defaults={"title": metadata["title"], "image": metadata["image"]},
        )
        store_item_metadata(item, metadata)
        return item
