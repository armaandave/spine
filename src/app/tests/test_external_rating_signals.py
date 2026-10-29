from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from integrations.tasks import import_media


class ExternalRatingItemSignalTests(TestCase):
    @patch("app.signals.enrich_external_ratings.delay")
    def test_supported_item_enqueues_only_after_commit(self, delay_mock):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            item = Item.objects.create(
                media_id="1",
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
                title="Cowboy Bebop",
            )
            delay_mock.assert_not_called()

        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        delay_mock.assert_called_once_with(item.pk)

    @patch("app.signals.enrich_external_ratings.delay")
    def test_updates_manual_and_unsupported_items_do_not_enqueue(self, delay_mock):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            supported = Item.objects.create(
                media_id="1",
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
                title="Cowboy Bebop",
            )
        callbacks.clear()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            supported.title = "Updated"
            supported.save(update_fields=["title"])
            Item.objects.create(
                media_id=Item.generate_manual_id(),
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title="Manual",
            )
            Item.objects.create(
                media_id="comic",
                source=Sources.COMICVINE.value,
                media_type=MediaTypes.COMIC.value,
                title="Comic",
            )

        self.assertEqual(callbacks, [])
        delay_mock.assert_not_called()


class ExternalRatingImportEnqueueTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="importer")

    @patch("integrations.tasks.sweep_item_metadata")
    @patch("integrations.tasks.events.tasks.reload_calendar.delay")
    @patch("integrations.tasks.enqueue_external_rating_batches")
    @patch("app.signals.enrich_external_ratings.delay")
    def test_import_collects_eligible_items_into_one_post_commit_enqueue(
        self,
        item_delay_mock,
        batch_enqueue_mock,
        _calendar_mock,
        _sweep_mock,
    ):
        created_ids = []

        def importer(_identifier, _user, _mode):
            for index in range(205):
                item = Item.objects.create(
                    media_id=str(index),
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title=f"Anime {index}",
                )
                created_ids.append(item.pk)
            Item.objects.create(
                media_id=Item.generate_manual_id(),
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title="Manual",
            )
            return {MediaTypes.ANIME.value: 205}, []

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            import_media(importer, "source", self.user.pk, "new")
            item_delay_mock.assert_not_called()
            batch_enqueue_mock.assert_not_called()

        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        batch_enqueue_mock.assert_called_once_with(sorted(created_ids))
        item_delay_mock.assert_not_called()
