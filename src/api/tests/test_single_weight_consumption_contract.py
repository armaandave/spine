from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app import single_weight
from app.models import DiaryEntry, Item, MediaLike, MediaTypes, Movie, Music, Sources
from lists.models import CustomList, CustomListItem
from social.models import Activity


class SingleWeightDomainTests(TestCase):
    """Exercise the canonical single-weight domain transitions."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="single-weight",
            password="strong-password-123",
        )

    def item(self, media_type=MediaTypes.MOVIE.value, media_id="one"):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{media_id}",
            title=f"{media_type} {media_id}",
            image="https://example.com/poster.jpg",
        )

    def log(self, item, day, **kwargs):
        return single_weight.create_log(
            self.user,
            item,
            consumed_at=day,
            **kwargs,
        )[0]

    def test_direct_consumption_rating_and_like_are_undated_and_unique(self):
        for media_type, model in ((MediaTypes.MOVIE.value, Movie), (MediaTypes.MUSIC.value, Music)):
            with self.subTest(media_type=media_type):
                item = self.item(media_type)
                tracking = single_weight.mark_consumed(self.user, item)
                single_weight.mark_consumed(self.user, item)

                self.assertEqual(model.objects.filter(user=self.user, item=item).count(), 1)
                self.assertTrue(tracking.direct_consumption)
                self.assertIsNone(tracking.end_date)
                self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())

                single_weight.set_rating(self.user, item, Decimal(8))
                single_weight.set_like(self.user, item, liked=True)
                tracking.refresh_from_db()
                self.assertEqual(tracking.score, Decimal(8))
                self.assertIsNone(tracking.rating_source_id)
                self.assertTrue(tracking.like_is_independent)
                self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())

                single_weight.set_rating(self.user, item, None)
                single_weight.set_like(self.user, item, liked=False)
                tracking.refresh_from_db()
                self.assertIsNone(tracking.score)
                self.assertTrue(tracking.direct_consumption)
                self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

    def test_repeat_defaults_duplicates_and_last_date_follow_evidence(self):
        item = self.item()
        first = self.log(item, date(2026, 1, 2))
        same_day = self.log(item, date(2026, 1, 2))
        newer = self.log(item, date(2026, 2, 3))
        backdated = self.log(item, date(2025, 12, 31))
        tracking = Movie.objects.get(user=self.user, item=item)

        self.assertFalse(first.is_rewatch)
        self.assertTrue(same_day.is_rewatch)
        self.assertTrue(newer.is_rewatch)
        self.assertTrue(backdated.is_rewatch)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 4)
        self.assertEqual(tracking.end_date.date(), date(2026, 2, 3))

        single_weight.delete_log(self.user, newer)
        tracking.refresh_from_db()
        self.assertEqual(tracking.end_date.date(), date(2026, 1, 2))

        direct_item = self.item(media_id="direct-repeat")
        single_weight.mark_consumed(self.user, direct_item)
        self.assertTrue(self.log(direct_item, date(2026, 1, 4)).is_rewatch)

        planned_item = self.item(media_id="planned-not-repeat")
        Movie.objects.create(user=self.user, item=planned_item, status="Planning")
        self.assertFalse(self.log(planned_item, date(2026, 1, 5)).is_rewatch)

    def test_rating_couples_decouples_edits_clears_and_deletes_without_rollback(self):
        item = self.item()
        log_a = self.log(item, date(2026, 1, 1), rating=Decimal(8))
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(8), log_a.id))

        single_weight.update_log(log_a, {"rating": Decimal(10)})
        tracking.refresh_from_db()
        self.assertEqual(tracking.score, Decimal(10))

        single_weight.set_rating(self.user, item, Decimal(6))
        single_weight.update_log(log_a, {"rating": Decimal(4)})
        tracking.refresh_from_db()
        log_a.refresh_from_db()
        self.assertEqual((tracking.score, log_a.rating), (Decimal(6), Decimal(4)))
        self.assertIsNone(tracking.rating_source_id)

        log_b = self.log(item, date(2026, 1, 2), rating=Decimal(8))
        tracking.refresh_from_db()
        self.assertEqual(tracking.rating_source_id, log_b.id)
        single_weight.delete_log(self.user, log_b)
        tracking.refresh_from_db()
        self.assertEqual(tracking.score, Decimal(8))
        self.assertIsNone(tracking.rating_source_id)
        single_weight.set_rating(self.user, item, None)
        log_a.refresh_from_db()
        self.assertEqual(log_a.rating, Decimal(4))

    def test_unrated_and_non_source_logs_preserve_current_rating_and_source_clear_is_scoped(self):
        item = self.item()
        source = self.log(item, date(2026, 1, 1), rating=Decimal(8))
        unrated = self.log(item, date(2026, 1, 2))
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(8), source.id))

        single_weight.update_log(unrated, {"rating": Decimal(2)})
        tracking.refresh_from_db()
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(8), source.id))
        single_weight.delete_log(self.user, unrated)
        tracking.refresh_from_db()
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(8), source.id))

        single_weight.update_log(source, {"rating": None})
        tracking.refresh_from_db()
        source.refresh_from_db()
        self.assertIsNone(source.rating)
        self.assertIsNone(tracking.score)
        self.assertIsNone(tracking.rating_source_id)

    def test_rating_an_unrated_log_fills_an_empty_current_rating(self):
        item = self.item()
        older = self.log(item, date(2026, 1, 1))
        newer = self.log(item, date(2026, 1, 2))
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertIsNone(tracking.score)

        single_weight.update_log(newer, {"rating": Decimal(6)})
        tracking.refresh_from_db()
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(6), newer.id))

        # Once filled, other logs follow SW-104 and leave it alone.
        single_weight.update_log(older, {"rating": Decimal(2)})
        tracking.refresh_from_db()
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(6), newer.id))

        # A deliberately cleared rating stays cleared when a log is re-saved unchanged,
        # but a log rating the user changes fills it again.
        single_weight.set_rating(self.user, item, None)
        single_weight.update_log(older, {"rating": Decimal(2), "review": "Still good"})
        tracking.refresh_from_db()
        self.assertIsNone(tracking.score)
        single_weight.update_log(older, {"rating": Decimal(4)})
        tracking.refresh_from_db()
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(4), older.id))

    def test_heart_couples_including_off_and_direct_change_decouples(self):
        item = self.item()
        single_weight.set_like(self.user, item, liked=True)
        entry = self.log(item, date(2026, 1, 1), liked=False)
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertEqual(tracking.like_source_id, entry.id)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

        single_weight.update_log(entry, {"liked": True})
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())
        single_weight.set_like(self.user, item, liked=False)
        single_weight.update_log(entry, {"liked": True})
        tracking.refresh_from_db()
        self.assertIsNone(tracking.like_source_id)
        self.assertTrue(tracking.like_is_independent)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

    def test_source_heart_edits_and_deletion_preserve_current_without_selecting_older(self):
        item = self.item()
        older = self.log(item, date(2026, 1, 1), liked=True)
        source = self.log(item, date(2026, 1, 2), liked=False)
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertEqual(tracking.like_source_id, source.id)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

        single_weight.update_log(older, {"liked": False})
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())
        single_weight.update_log(source, {"liked": True})
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())

        single_weight.delete_log(self.user, source)
        tracking.refresh_from_db()
        self.assertIsNone(tracking.like_source_id)
        self.assertTrue(tracking.like_is_independent)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())

    def test_unwatch_is_blocked_and_final_log_respects_direct_evidence(self):
        item = self.item()
        entry = self.log(item, date(2026, 1, 1), rating=Decimal(8), liked=True)
        with self.assertRaises(single_weight.DiaryHistoryExists):
            single_weight.unwatch(self.user, item)
        single_weight.delete_log(self.user, entry)
        self.assertFalse(Movie.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

        direct_item = self.item(media_id="direct")
        single_weight.mark_consumed(self.user, direct_item)
        direct_log = self.log(direct_item, date(2026, 1, 1), rating=Decimal(10))
        single_weight.delete_log(self.user, direct_log)
        tracking = Movie.objects.get(user=self.user, item=direct_item)
        self.assertTrue(tracking.direct_consumption)
        self.assertIsNone(tracking.end_date)
        self.assertEqual(tracking.score, Decimal(10))

        single_weight.unwatch(self.user, direct_item)
        self.assertFalse(Movie.objects.filter(user=self.user, item=direct_item).exists())

    def test_unwatch_preserves_custom_list_membership_and_clears_current_state(self):
        item = self.item()
        custom_list = CustomList.objects.create(owner=self.user, name="Keep Me")
        membership = CustomListItem.objects.create(custom_list=custom_list, item=item)
        single_weight.set_rating(self.user, item, Decimal(7))
        single_weight.set_like(self.user, item, liked=True)

        single_weight.unwatch(self.user, item)

        self.assertFalse(Movie.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())
        self.assertTrue(CustomListItem.objects.filter(pk=membership.pk, item=item).exists())

    def test_dates_are_mandatory_and_not_future(self):
        item = self.item()
        with self.assertRaisesMessage(Exception, "required"):
            self.log(item, None)
        with self.assertRaisesMessage(Exception, "future"):
            self.log(item, timezone.localdate() + timedelta(days=1))

    def test_legacy_tracking_writes_cannot_add_dates_or_downgrade_consumed_state(self):
        item = self.item()
        tracking = single_weight.apply_tracking_state(
            self.user,
            item,
            status="Completed",
            rating=Decimal(9),
            start_date=timezone.now(),
        )
        self.assertTrue(tracking.direct_consumption)
        self.assertIsNone(tracking.end_date)
        self.assertEqual(tracking.score, Decimal(9))

        entry = self.log(item, date(2026, 1, 1))
        tracking = single_weight.apply_tracking_state(
            self.user,
            item,
            status="Paused",
        )
        self.assertEqual(tracking.status, "Completed")
        self.assertEqual(tracking.end_date.date(), date(2026, 1, 1))
        with self.assertRaises(single_weight.DiaryHistoryExists):
            single_weight.unwatch(self.user, item)
        self.assertTrue(DiaryEntry.objects.filter(pk=entry.pk).exists())

    def test_log_creation_rolls_back_all_state_on_failure(self):
        item = self.item()
        with (
            patch("app.single_weight._create_diary_activity", side_effect=RuntimeError("stop")),
            self.assertRaises(RuntimeError),
            transaction.atomic(),
        ):
            self.log(item, date(2026, 1, 1), rating=Decimal(8), liked=True)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(Movie.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

    def test_direct_like_is_atomic_and_creates_no_feed_activity(self):
        item = self.item()
        with (
            patch("app.single_weight._audit_like", side_effect=RuntimeError("stop")),
            self.assertRaises(RuntimeError),
        ):
            single_weight.set_like(self.user, item, liked=True)
        self.assertFalse(Movie.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

        single_weight.set_like(self.user, item, liked=True)
        self.assertTrue(Movie.objects.get(user=self.user, item=item).direct_consumption)
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())

    def test_import_order_idempotency_repeats_and_independent_state(self):
        item = self.item()
        rows = [
            {
                "source_id": "a",
                "source_order": 1,
                "consumed_at": date(2026, 1, 1),
                "rating": Decimal(6),
                "liked": True,
            },
            {
                "source_id": "b",
                "source_order": 2,
                "consumed_at": date(2026, 1, 1),
                "rating": Decimal(8),
                "liked": False,
            },
        ]
        created = single_weight.import_logs(self.user, item, rows, source="test")
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertEqual(len(created), 2)
        self.assertFalse(created[0].is_rewatch)
        self.assertTrue(created[1].is_rewatch)
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(8), created[1].id))
        self.assertEqual(tracking.like_source_id, created[1].id)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())
        self.assertEqual(single_weight.import_logs(self.user, item, rows, source="test"), [])

        single_weight.set_rating(self.user, item, Decimal(10))
        single_weight.set_like(self.user, item, liked=False)
        single_weight.import_logs(
            self.user,
            item,
            [
                {
                    "source_id": "c",
                    "source_order": 3,
                    "consumed_at": date(2026, 2, 1),
                    "rating": Decimal(2),
                    "liked": True,
                },
            ],
            source="test",
        )
        tracking.refresh_from_db()
        self.assertEqual(tracking.score, Decimal(10))
        self.assertIsNone(tracking.rating_source_id)
        self.assertTrue(tracking.like_is_independent)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

    def test_import_combines_local_history_orders_same_day_and_trusts_repeat(self):
        item = self.item()
        local = self.log(item, date(2026, 1, 1), rating=Decimal(2), liked=True)
        Activity.objects.all().delete()
        imported = single_weight.import_logs(
            self.user,
            item,
            [
                {
                    "source_id": "later",
                    "consumed_at": date(2026, 1, 1),
                    "rating": Decimal(8),
                    "liked": False,
                    "is_rewatch": False,
                },
                {
                    "source_id": "last",
                    "consumed_at": date(2026, 1, 1),
                    "rating": Decimal(10),
                    "liked": True,
                },
            ],
            source="ordered",
        )
        tracking = Movie.objects.get(user=self.user, item=item)
        self.assertEqual([entry.is_rewatch for entry in imported], [False, True])
        self.assertEqual((tracking.score, tracking.rating_source_id), (Decimal(10), imported[1].id))
        self.assertEqual(tracking.like_source_id, imported[1].id)
        self.assertNotEqual(tracking.rating_source_id, local.id)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())

    def test_imported_undated_title_state_is_direct_independent_and_silent(self):
        item = self.item()
        tracking = single_weight.import_title_state(
            self.user,
            item,
            consumed=True,
            rating=Decimal(9),
            liked=True,
        )
        self.assertTrue(tracking.direct_consumption)
        self.assertIsNone(tracking.end_date)
        self.assertIsNone(tracking.rating_source_id)
        self.assertTrue(tracking.like_is_independent)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())

    def test_full_movie_music_transition_parity(self):
        for media_type, model in ((MediaTypes.MOVIE.value, Movie), (MediaTypes.MUSIC.value, Music)):
            with self.subTest(media_type=media_type):
                item = self.item(media_type, media_id="parity")
                first = self.log(item, date(2026, 3, 1), rating=Decimal(7), liked=True)
                tracking = model.objects.get(user=self.user, item=item)
                self.assertEqual(tracking.progress, 1)
                self.assertEqual(tracking.rating_source_id, first.id)
                self.assertEqual(tracking.like_source_id, first.id)
                with self.assertRaises(single_weight.DiaryHistoryExists):
                    single_weight.unwatch(self.user, item)
                single_weight.delete_log(self.user, first)
                self.assertFalse(model.objects.filter(user=self.user, item=item).exists())


class SingleWeightAPITests(TestCase):
    """Verify the wire contract and cross-surface API behavior."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="single-api",
            password="strong-password-123",
        )
        self.other = get_user_model().objects.create_user(
            username="single-other",
            password="strong-password-123",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.metadata = {"title": "API Movie", "image": "https://example.com/poster.jpg"}

    @patch("api.services.diary.provider_services.get_media_metadata")
    @patch("api.services.tracking.provider_services.get_media_metadata")
    def test_wire_ratings_canonical_state_conflict_social_and_authorization(
        self,
        tracking_metadata,
        diary_metadata,
    ):
        tracking_metadata.return_value = self.metadata
        diary_metadata.return_value = self.metadata
        ref = {
            "source": "tmdb",
            "media_type": "movie",
            "media_id": "api-movie",
        }
        path = "/api/v1/tracking/tmdb/movie/api-movie/"

        consumed = self.client.post(f"{path}actions/consume/", {}, format="json")
        self.assertEqual(consumed.status_code, 200)
        self.assertIsNone(consumed.data["end_date"])
        self.assertTrue(consumed.data["direct_consumption"])

        rated = self.client.patch(path, {"rating": "3.0"}, format="json")
        self.assertEqual(rated.data["rating"], "3.0")
        self.assertEqual(Activity.objects.filter(actor=self.user, verb="rating_updated").count(), 1)

        logged = self.client.post(
            "/api/v1/diary/",
            {
                "ref": ref,
                "consumed_at": "2026-01-01",
                "rating": "4.5",
                "liked": False,
            },
            format="json",
        )
        self.assertEqual(logged.status_code, 201)
        self.assertEqual(logged.data["rating"], "4.5")
        self.assertEqual(logged.data["consumed_at"], "2026-01-01")
        self.assertEqual(Activity.objects.filter(actor=self.user, verb="diary_created").count(), 1)
        self.assertEqual(Activity.objects.filter(actor=self.user, verb="rating_updated").count(), 1)

        blocked = self.client.delete(path)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data["error"]["code"], "diary_history_exists")

        self.client.force_authenticate(self.other)
        forbidden = self.client.patch(
            f"/api/v1/diary/{logged.data['id']}/",
            {"rating": "1.0"},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_rating_an_imported_unrated_log_shows_on_the_media_page(self):
        item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="imported-unrated",
            title="Imported Unrated",
            image="https://example.com/poster.jpg",
        )
        imported = single_weight.import_logs(
            self.user,
            item,
            [{"source_id": "lb-1", "consumed_at": date(2026, 10, 16), "review": "brittle"}],
            source="letterboxd",
        )[0]

        edited = self.client.patch(
            f"/api/v1/diary/{imported.id}/",
            {"consumed_at": "2026-10-16", "rating": "3.0", "review": "brittle", "liked": False},
            format="json",
        )
        self.assertEqual(edited.status_code, 200)

        tracking = self.client.get("/api/v1/tracking/manual/movie/imported-unrated/")
        community = self.client.get("/api/v1/media/manual/movie/imported-unrated/community/")
        self.assertEqual(tracking.data["rating"], "3.0")
        self.assertEqual(community.data["rating_count"], 1)
        self.assertEqual(community.data["average_rating"], "3.00")
        self.assertEqual(community.data["review_count"], 1)

    @patch("api.services.diary.provider_services.get_media_metadata")
    def test_api_rejects_missing_future_and_invalid_half_star_dates(self, metadata):
        metadata.return_value = self.metadata
        ref = {"source": "tmdb", "media_type": "movie", "media_id": "invalid"}
        missing = self.client.post("/api/v1/diary/", {"ref": ref}, format="json")
        future = self.client.post(
            "/api/v1/diary/",
            {
                "ref": ref,
                "consumed_at": (timezone.localdate() + timedelta(days=1)).isoformat(),
            },
            format="json",
        )
        invalid = self.client.post(
            "/api/v1/diary/",
            {"ref": ref, "consumed_at": "2026-01-01", "rating": "4.2"},
            format="json",
        )
        self.assertEqual((missing.status_code, future.status_code, invalid.status_code), (400, 400, 400))

    def test_account_privacy_governs_reviews_community_and_single_weight_stats_use_wire_scale(self):
        item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="privacy-movie",
            title="Privacy Movie",
            image="https://example.com/poster.jpg",
        )
        public_entry = single_weight.create_log(
            self.user,
            item,
            consumed_at=date(2026, 1, 1),
            rating=Decimal(8),
            liked=True,
            review="Public profile, legacy private row",
        )[0]
        DiaryEntry.objects.filter(pk=public_entry.pk).update(visibility="private")

        self.other.profile_private = True
        self.other.save(update_fields=["profile_private"])
        private_entry = single_weight.create_log(
            self.other,
            item,
            consumed_at=date(2026, 1, 2),
            rating=Decimal(10),
            liked=True,
            review="Private profile, public row",
        )[0]

        community = self.client.get("/api/v1/media/manual/movie/privacy-movie/community/")
        reviews = self.client.get("/api/v1/media/manual/movie/privacy-movie/reviews/")
        stats = self.client.get("/api/v1/stats/me/summary/?start_date=all&end_date=all")

        self.assertEqual(community.status_code, 200)
        self.assertEqual(community.data["diary_count"], 1)
        self.assertEqual(community.data["liked_count"], 1)
        self.assertEqual(community.data["average_rating"], "4.00")
        self.assertEqual(reviews.status_code, 200)
        self.assertEqual(reviews.data["count"], 1)
        self.assertEqual(reviews.data["results"][0]["id"], public_entry.id)
        self.assertNotEqual(reviews.data["results"][0]["id"], private_entry.id)

        movie_stats = next(
            value for value in stats.data["media_types"] if value["media_type"] == "movie"
        )
        self.assertEqual(movie_stats["average_rating"], "4.0")
        self.assertEqual(
            next(bucket["count"] for bucket in movie_stats["rating_distribution"] if bucket["rating"] == "4.0"),
            1,
        )
        self.assertEqual(stats.data["diary_top_rated"][0]["rating"], "4.0")
        self.assertEqual(stats.data["top_rated"][0]["rating"], "4.0")

    @patch("api.services.diary.provider_services.get_media_metadata")
    @patch("api.services.tracking.provider_services.get_media_metadata")
    def test_movie_and_music_api_transitions_have_matching_canonical_state(
        self,
        tracking_metadata,
        diary_metadata,
    ):
        tracking_metadata.return_value = self.metadata
        diary_metadata.return_value = self.metadata
        for media_type in ("movie", "music"):
            with self.subTest(media_type=media_type):
                media_id = f"api-{media_type}-parity"
                path = f"/api/v1/tracking/manual/{media_type}/{media_id}/"
                consumed = self.client.post(f"{path}actions/consume/", {}, format="json")
                rated = self.client.patch(path, {"rating": "2.5"}, format="json")
                liked = self.client.post(
                    "/api/v1/me/liked-media/",
                    {
                        "ref": {
                            "source": "manual",
                            "media_type": media_type,
                            "media_id": media_id,
                        },
                    },
                    format="json",
                )
                logged = self.client.post(
                    "/api/v1/diary/",
                    {
                        "ref": {
                            "source": "manual",
                            "media_type": media_type,
                            "media_id": media_id,
                        },
                        "consumed_at": "2026-02-01",
                        "rating": "4.0",
                        "liked": False,
                    },
                    format="json",
                )
                self.assertEqual(
                    (consumed.status_code, rated.status_code, liked.status_code, logged.status_code),
                    (200, 200, 200, 201),
                )
                self.assertTrue(consumed.data["direct_consumption"])
                self.assertEqual(rated.data["rating"], "2.5")
                self.assertTrue(liked.data["liked"])
                self.assertEqual(logged.data["rating"], "4.0")
                canonical = self.client.get(path)
                self.assertEqual(canonical.data["rating"], "4.0")
                self.assertFalse(canonical.data["liked"])
                self.assertEqual(canonical.data["diary_count"], 1)
