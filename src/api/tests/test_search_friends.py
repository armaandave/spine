"""Who you follow on each search result: the people the title's page lists (AC-6)."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient

from api.services.activity import friends_on_results
from app.models import DiaryEntry, Item, MediaTypes, Sources
from social.models import Follow, FollowStatus


class SearchFriendsTests(TestCase):
    """A result names the people you follow who've done something with it."""

    def setUp(self):
        self.viewer = self.user("viewer")

    def user(self, username):
        return get_user_model().objects.create_user(username=username, password="strong-password-123")

    def friend(self, username):
        person = self.user(username)
        Follow.objects.create(from_user=self.viewer, to_user=person, status=FollowStatus.ACCEPTED)
        return person

    def item(self, media_id="movie-1"):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            media_id=media_id,
            title="Title",
            image="https://example.com/poster.jpg",
        )

    def log(self, item, user):
        return DiaryEntry.objects.create(user=user, item=item, consumed_at=datetime(2026, 10, 14, tzinfo=UTC))

    def result(self, item):
        return {"ref": {"item_id": item.id if item else None, "media_type": MediaTypes.MOVIE.value}, "title": "Title"}

    def test_a_result_shows_the_people_its_page_lists(self):
        item = self.item()
        self.log(item, self.friend("maya"))

        [result] = friends_on_results(self.viewer, [self.result(item)])

        self.assertEqual(result["friends"]["count"], 1)
        self.assertEqual([person["username"] for person in result["friends"]["people"]], ["maya"])

    def test_people_you_dont_follow_and_titles_spine_doesnt_have_show_nobody(self):
        item = self.item()
        self.log(item, self.user("stranger"))
        self.friend("maya")

        results = friends_on_results(self.viewer, [self.result(item), self.result(None)])

        self.assertEqual([result["friends"] for result in results], [None, None])

    def test_titles_nobody_you_follow_touched_add_no_queries(self):
        self.friend("maya")
        items = [self.item(f"movie-{number}") for number in range(6)]

        with CaptureQueriesContext(connection) as one:
            friends_on_results(self.viewer, [self.result(items[0])])
        with CaptureQueriesContext(connection) as six:
            friends_on_results(self.viewer, [self.result(item) for item in items])

        self.assertEqual(len(six), len(one))

    @patch("api.views.media.media_service.search_media")
    def test_search_answers_carry_friends(self, search_media):
        item = self.item()
        self.log(item, self.friend("maya"))
        search_media.return_value = [self.result(item)]
        client = APIClient()
        client.force_authenticate(self.viewer)

        response = client.get("/api/v1/media/search/", {"media_type": MediaTypes.MOVIE.value, "q": "title"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["friends"]["count"], 1)
