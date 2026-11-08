"""Site-wide Home picture: admin chooses, every signed-in user reads."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from app.models import HomePicture, Item, MediaTypes, Sources
from social.models import SocialAuditLog

GOOD = "https://example.com/good.jpg"
OTHER = "https://example.com/other.jpg"
OPTIONS = {"backdrops": [{"url": GOOD}, {"url": OTHER}]}
BACKDROP_OPTIONS = "api.views.home_picture.media_service.backdrop_options"
GET_METADATA = "api.views.home_picture.provider_services.get_media_metadata"


class HomePictureTests(TestCase):
    """GET/PUT/DELETE /home-picture/."""

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.staff = User.objects.create_user(username="boss", password="x", is_staff=True)
        self.viewer = User.objects.create_user(username="viewer", password="x")
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="1",
            title="Dune",
            release_year=2021,
            image="https://example.com/p.jpg",
        )
        self.url = reverse("api-home-picture")
        self.ref = {"source": "tmdb", "media_type": "movie", "media_id": "1"}
        self.client = APIClient()

    def as_user(self, user):
        self.client.force_authenticate(user)

    def put(self, **overrides):
        body = {"ref": self.ref, "backdrop_url": GOOD, **overrides}
        with patch(BACKDROP_OPTIONS, return_value=OPTIONS):
            return self.client.put(self.url, body, format="json")

    def test_unauthenticated(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_get_empty(self):
        self.as_user(self.viewer)
        data = self.client.get(self.url).json()
        self.assertEqual(data, {"picture": None, "can_change": False})
        self.as_user(self.staff)
        self.assertTrue(self.client.get(self.url).json()["can_change"])

    def test_non_staff_cannot_write(self):
        self.as_user(self.viewer)
        self.assertEqual(self.put().status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 403)
        self.assertFalse(HomePicture.objects.exists())

    def test_staff_put_then_viewer_get(self):
        self.as_user(self.staff)
        response = self.put()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["can_change"])
        self.as_user(self.viewer)
        data = self.client.get(self.url).json()
        picture = data["picture"]
        self.assertFalse(data["can_change"])
        self.assertEqual(picture["title"], "Dune")
        self.assertEqual(picture["year"], 2021)
        self.assertEqual(picture["backdrop_url"], GOOD)
        self.assertEqual(picture["set_by"], "boss")
        self.assertEqual(picture["ref"]["media_id"], "1")
        self.assertEqual(picture["ref"]["media_type"], "movie")
        self.assertEqual(picture["ref"]["source"], "tmdb")
        self.assertTrue(picture["updated_at"])

    def test_year_falls_back_to_release_date_then_null(self):
        self.as_user(self.staff)
        Item.objects.filter(pk=self.item.pk).update(release_year=None, release_date=datetime.date(2019, 5, 1))
        self.put()
        self.assertEqual(self.client.get(self.url).json()["picture"]["year"], 2019)
        Item.objects.filter(pk=self.item.pk).update(release_date=None)
        self.assertIsNone(self.client.get(self.url).json()["picture"]["year"])

    def test_put_replaces_single_row(self):
        self.as_user(self.staff)
        self.put()
        Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="2",
            title="Severance",
            release_year=2022,
            image="https://example.com/s.jpg",
        )
        body = {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "2"}, "backdrop_url": OTHER}
        with patch(BACKDROP_OPTIONS, return_value=OPTIONS):
            self.assertEqual(self.client.put(self.url, body, format="json").status_code, 200)
        self.assertEqual(HomePicture.objects.count(), 1)
        self.assertEqual(self.client.get(self.url).json()["picture"]["title"], "Severance")

    def test_url_not_offered(self):
        self.as_user(self.staff)
        response = self.put(backdrop_url="https://example.com/nope.jpg")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "That backdrop is not one of this title's options.")
        self.assertFalse(HomePicture.objects.exists())

    def test_unsupported_media_type(self):
        self.as_user(self.staff)
        ref = {"source": "openlibrary", "media_type": "book", "media_id": "1"}
        response = self.put(ref=ref)
        self.assertEqual(response.status_code, 400)
        self.assertIn("movie, TV, anime or game", response.json()["detail"])
        self.assertFalse(HomePicture.objects.exists())

    def test_backdrop_options_value_error(self):
        self.as_user(self.staff)
        with patch(BACKDROP_OPTIONS, side_effect=ValueError("No backdrops here")):
            response = self.client.put(self.url, {"ref": self.ref, "backdrop_url": GOOD}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "No backdrops here")
        self.assertFalse(HomePicture.objects.exists())

    def test_backdrop_options_failure_is_502(self):
        self.as_user(self.staff)
        with patch(BACKDROP_OPTIONS, side_effect=RuntimeError("boom")):
            response = self.client.put(self.url, {"ref": self.ref, "backdrop_url": GOOD}, format="json")
        self.assertEqual(response.status_code, 502)
        self.assertFalse(HomePicture.objects.exists())

    def test_metadata_miss_is_400(self):
        self.as_user(self.staff)
        ref = {"source": "tmdb", "media_type": "movie", "media_id": "999"}
        with patch(GET_METADATA, side_effect=Exception("not found")):
            response = self.client.put(self.url, {"ref": ref, "backdrop_url": GOOD}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(HomePicture.objects.exists())

    def test_bad_body(self):
        self.as_user(self.staff)
        self.assertEqual(self.client.put(self.url, {"ref": {"source": "tmdb"}}, format="json").status_code, 400)
        self.assertEqual(self.put(backdrop_url="not a url").status_code, 400)
        self.assertFalse(HomePicture.objects.exists())

    def test_put_writes_audit(self):
        self.as_user(self.staff)
        self.put()
        log = SocialAuditLog.objects.get(action="home_picture_set")
        self.assertEqual(log.actor, self.staff)
        self.assertEqual(log.target_type, "item")
        self.assertEqual(log.target_id, self.item.id)
        self.assertEqual(log.metadata["backdrop_url"], GOOD)
        self.assertEqual(log.metadata["ref"]["media_id"], "1")

    def test_delete_clears_and_audits(self):
        self.as_user(self.staff)
        self.put()
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"picture": None, "can_change": True})
        self.as_user(self.viewer)
        self.assertIsNone(self.client.get(self.url).json()["picture"])
        log = SocialAuditLog.objects.get(action="home_picture_clear")
        self.assertEqual(log.target_id, self.item.id)
        self.assertEqual(log.metadata["backdrop_url"], GOOD)

    def test_delete_when_unset_is_noop(self):
        self.as_user(self.staff)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["picture"])
        self.assertFalse(SocialAuditLog.objects.filter(action="home_picture_clear").exists())

    def test_item_delete_cascades(self):
        self.as_user(self.staff)
        self.put()
        self.item.delete()
        self.assertIsNone(self.client.get(self.url).json()["picture"])
