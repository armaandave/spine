"""Hall of Fame list API: an ordered list of up to eight titles of any media types."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from users import hall_of_fame


def make_item(media_type, media_id, title=None, source=Sources.TMDB.value):
    """Create an Item."""
    return Item.objects.create(
        source=source,
        media_type=media_type,
        media_id=media_id,
        title=title or f"{media_type} {media_id}",
    )


def ref(item):
    """Return the ref the API takes for an Item."""
    return {
        "item_id": item.id,
        "source": item.source,
        "media_type": item.media_type,
        "media_id": item.media_id,
        "season_number": None,
        "episode_number": None,
    }


class HallOfFameApiBase(TestCase):
    """A signed-in user."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="crown", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def titles(self, response):
        return [media["title"] for media in response.data["items"]]


class ListEndpointTests(HallOfFameApiBase):
    """GET and PUT /me/hall-of-fame/."""

    def test_put_replaces_the_list_in_the_order_sent(self):
        movie = make_item(MediaTypes.MOVIE.value, "1", "Movie")
        show = make_item(MediaTypes.TV.value, "2", "Show")
        book = make_item(MediaTypes.BOOK.value, "3", "Book", Sources.HARDCOVER.value)
        hall_of_fame.set_picks(self.user, [movie, show])

        response = self.client.put(
            "/api/v1/me/hall-of-fame/",
            {"refs": [ref(book), ref(movie), ref(show)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.titles(response), ["Book", "Movie", "Show"])
        self.assertEqual(self.titles(self.client.get("/api/v1/me/hall-of-fame/")), ["Book", "Movie", "Show"])

    @patch("api.views.profile.provider_services.get_media_metadata")
    def test_put_creates_a_missing_item_from_provider_metadata(self, metadata_mock):
        metadata_mock.return_value = {"title": "Fight Club", "image": "https://example.com/fc.jpg"}

        response = self.client.put(
            "/api/v1/me/hall-of-fame/",
            {"refs": [{"source": Sources.TMDB.value, "media_type": "movie", "media_id": "550"}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.titles(response), ["Fight Club"])

    def test_put_keeps_picks_of_types_the_owner_turned_off(self):
        self.user.book_enabled = False
        self.user.save(update_fields=["book_enabled"])
        book = make_item(MediaTypes.BOOK.value, "3", "Book", Sources.HARDCOVER.value)

        response = self.client.put("/api/v1/me/hall-of-fame/", {"refs": [ref(book)]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.titles(response), ["Book"])

    def test_put_rejects_a_ninth_title(self):
        items = [make_item(MediaTypes.MOVIE.value, str(i)) for i in range(9)]

        response = self.client.put(
            "/api/v1/me/hall-of-fame/",
            {"refs": [ref(item) for item in items]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_rejects_the_same_title_twice(self):
        movie = make_item(MediaTypes.MOVIE.value, "1")

        response = self.client.put("/api/v1/me/hall-of-fame/", {"refs": [ref(movie), ref(movie)]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(hall_of_fame.picks(self.user), [])

    def test_put_rejects_an_unsupported_media_type(self):
        season = Item.objects.create(source=Sources.TMDB.value, media_type="season", media_id="1399", title="S", season_number=1)

        response = self.client.put("/api/v1/me/hall-of-fame/", {"refs": [ref(season)]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LegacyEndpointTests(HallOfFameApiBase):
    """PUT and DELETE /me/hof/<type>/ for installed builds."""

    def test_put_replaces_the_first_pick_of_that_type_in_place(self):
        old = make_item(MediaTypes.MOVIE.value, "1", "Old")
        show = make_item(MediaTypes.TV.value, "2", "Show")
        other = make_item(MediaTypes.MOVIE.value, "3", "Other")
        new = make_item(MediaTypes.MOVIE.value, "4", "New")
        hall_of_fame.set_picks(self.user, [show, old, other])

        response = self.client.put("/api/v1/me/hof/movie/", {"ref": ref(new)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([i.title for i in hall_of_fame.picks(self.user)], ["Show", "New", "Other"])
        self.assertEqual(response.data["items"]["movie"]["title"], "New")

    def test_put_appends_when_the_user_has_none_of_that_type(self):
        show = make_item(MediaTypes.TV.value, "2", "Show")
        movie = make_item(MediaTypes.MOVIE.value, "1", "Movie")
        hall_of_fame.set_picks(self.user, [show])

        response = self.client.put("/api/v1/me/hof/movie/", {"ref": ref(movie)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([i.title for i in hall_of_fame.picks(self.user)], ["Show", "Movie"])

    def test_put_is_refused_when_full_and_the_type_is_new(self):
        hall_of_fame.set_picks(self.user, [make_item(MediaTypes.TV.value, str(i)) for i in range(8)])
        movie = make_item(MediaTypes.MOVIE.value, "1")

        response = self.client.put("/api/v1/me/hof/movie/", {"ref": ref(movie)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(hall_of_fame.picks(self.user)), 8)

    def test_delete_removes_the_first_pick_of_that_type(self):
        first = make_item(MediaTypes.MOVIE.value, "1", "First")
        show = make_item(MediaTypes.TV.value, "2", "Show")
        second = make_item(MediaTypes.MOVIE.value, "3", "Second")
        hall_of_fame.set_picks(self.user, [first, show, second])

        response = self.client.delete("/api/v1/me/hof/movie/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([i.title for i in hall_of_fame.picks(self.user)], ["Show", "Second"])
        self.assertEqual(response.data["items"]["movie"]["title"], "Second")


class ProfilePayloadTests(HallOfFameApiBase):
    """The profile payload."""

    def test_profile_lists_every_pick_in_order_and_derives_the_old_map(self):
        self.user.book_enabled = False
        self.user.save(update_fields=["book_enabled"])
        picks = [
            make_item(MediaTypes.MOVIE.value, "1", "Movie A"),
            make_item(MediaTypes.BOOK.value, "3", "Book", Sources.HARDCOVER.value),
            make_item(MediaTypes.MOVIE.value, "4", "Movie B"),
        ]
        hall_of_fame.set_picks(self.user, picks)

        response = self.client.get("/api/v1/me/")

        self.assertEqual([m["title"] for m in response.data["hall_of_fame"]], ["Movie A", "Book", "Movie B"])
        self.assertEqual(response.data["hof"]["movie"]["title"], "Movie A")
        self.assertEqual(response.data["hof"]["book"]["title"], "Book")
        self.assertIsNone(response.data["hof"]["tv"])
