from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem, PersonListItem
from social.models import Block, Follow, FollowStatus

URL = "/api/v1/lists/tagged/"
PUBLIC = CustomList.Visibility.PUBLIC
UNLISTED = CustomList.Visibility.UNLISTED
PRIVATE = CustomList.Visibility.PRIVATE


class TaggedListsApiTests(TestCase):
    """GET /api/v1/lists/tagged/: the list page's tag links."""

    def setUp(self):
        self.viewer = self.user("viewer")
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)

    def user(self, username, *, private=False):
        return get_user_model().objects.create_user(username=username, profile_private=private)

    def make_list(self, owner, name, tags, visibility=PUBLIC, **fields):
        return CustomList.objects.create(owner=owner, name=name, tags=tags, visibility=visibility, **fields)

    def names(self, tag="horror", **params):
        response = self.client.get(URL, {"tag": tag, **params})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [result["name"] for result in response.data["results"]]

    def test_requires_a_tag(self):
        for params in ({}, {"tag": ""}, {"tag": "   "}):
            response = self.client.get(URL, params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_sign_in(self):
        self.assertIn(
            APIClient().get(URL, {"tag": "horror"}).status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_matches_whole_tags_ignoring_case_and_outer_spaces(self):
        self.make_list(self.viewer, "Shouty", ["HORROR"])
        self.make_list(self.viewer, "Padded", [" Horror ", "70s"])
        self.make_list(self.viewer, "Comedy", ["horror comedy"])
        self.make_list(self.viewer, "Untagged", [])

        self.assertEqual(self.names(), ["Padded", "Shouty"])
        self.assertEqual(self.names(tag="  hOrRoR "), ["Padded", "Shouty"])

    def test_matches_non_ascii_tags(self):
        self.make_list(self.viewer, "Paris", ["Ciné Français"])

        self.assertEqual(self.names(tag="ciné français"), ["Paris"])
        self.assertEqual(self.names(tag="cine francais"), [])

    def test_shows_only_lists_the_viewer_can_browse(self):
        stranger = self.user("stranger")
        self.make_list(self.viewer, "Mine, private", ["horror"], PRIVATE)
        self.make_list(self.viewer, "Mine, unlisted", ["horror"], UNLISTED)
        self.make_list(stranger, "Public", ["horror"])
        self.make_list(stranger, "Unlisted", ["horror"], UNLISTED)
        self.make_list(stranger, "Private", ["horror"], PRIVATE)
        shared = self.make_list(stranger, "Shared with me", ["horror"], PRIVATE)
        shared.collaborators.add(self.viewer)

        self.assertEqual(self.names(), ["Mine, private", "Mine, unlisted", "Shared with me", "Public"])

    def test_private_accounts_show_their_lists_to_accepted_followers_only(self):
        followed = self.user("followed", private=True)
        pending = self.user("pending", private=True)
        unknown = self.user("unknown", private=True)
        Follow.objects.create(from_user=self.viewer, to_user=followed, status=FollowStatus.ACCEPTED)
        Follow.objects.create(from_user=self.viewer, to_user=pending, status=FollowStatus.PENDING)
        self.make_list(followed, "Followed", ["horror"])
        self.make_list(pending, "Pending", ["horror"])
        self.make_list(unknown, "Unknown", ["horror"])

        self.assertEqual(self.names(), ["Followed"])

    def test_blocks_hide_lists_both_ways(self):
        blocker = self.user("blocker")
        blocked = self.user("blocked")
        Block.objects.create(blocker=blocker, blocked=self.viewer)
        Block.objects.create(blocker=self.viewer, blocked=blocked)
        self.make_list(blocker, "From blocker", ["horror"])
        self.make_list(blocked, "From blocked", ["horror"])
        shared = self.make_list(blocker, "Shared by blocker", ["horror"], PRIVATE)
        shared.collaborators.add(self.viewer)

        self.assertEqual(self.names(), [])

    def test_public_featured_lists_show_like_on_search(self):
        spine = self.user("Spine", private=True)
        self.make_list(spine, "Hardcover Fiction", ["NYT Best Sellers", "Books"], is_featured=True)
        self.make_list(spine, "Hidden chart", ["NYT Best Sellers"], PRIVATE, is_featured=True)
        self.make_list(spine, "Not featured", ["NYT Best Sellers"])

        self.assertEqual(self.names(tag="nyt best sellers"), ["Hardcover Fiction"])

    def test_lists_the_viewers_own_first_then_a_to_z(self):
        other = self.user("other")
        self.make_list(other, "apple", ["horror"])
        self.make_list(other, "Zebra", ["horror"])
        self.make_list(self.viewer, "yours", ["horror"])
        self.make_list(self.viewer, "Banana", ["horror"])
        shared = self.make_list(other, "Cherry", ["horror"], PRIVATE)
        shared.collaborators.add(self.viewer)

        self.assertEqual(self.names(), ["Banana", "Cherry", "yours", "apple", "Zebra"])

    def test_pages_like_other_collections(self):
        for number in range(30):
            self.make_list(self.viewer, f"List {number:02d}", ["horror"])

        first = self.client.get(URL, {"tag": "horror"}).data
        self.assertEqual(first["count"], 30)
        self.assertEqual(len(first["results"]), 25)
        self.assertIn("page=2", first["next"])
        self.assertIn("tag=horror", first["next"])

        second = self.client.get(URL, {"tag": "horror", "page": 2}).data
        self.assertEqual([result["name"] for result in second["results"]], [f"List {number}" for number in range(25, 30)])
        self.assertIsNone(second["next"])

    def test_results_have_the_list_summary_shape(self):
        movies = self.make_list(self.viewer, "Movies", ["horror"], is_ranked=True)
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="694",
            title="The Shining",
            image="https://example.com/shining.jpg",
        )
        CustomListItem.objects.create(custom_list=movies, item=item, position=1)
        people = self.make_list(self.viewer, "People", ["horror"], list_type=CustomList.ListType.PEOPLE)
        PersonListItem.objects.create(custom_list=people, source="tmdb", person_id="3636", name="Shelley Duvall", position=1)

        response = self.client.get(URL, {"tag": "horror"})

        movie_list, people_list = response.data["results"]
        self.assertEqual(movie_list["id"], movies.id)
        self.assertEqual(movie_list["tags"], ["horror"])
        self.assertEqual(movie_list["owner"]["username"], "viewer")
        self.assertTrue(movie_list["is_ranked"])
        self.assertEqual(movie_list["list_type"], "media")
        self.assertEqual(movie_list["items_count"], 1)
        self.assertEqual(movie_list["entries_count"], 1)
        self.assertEqual([preview["title"] for preview in movie_list["preview_items"]], ["The Shining"])
        self.assertEqual(people_list["list_type"], "people")
        self.assertEqual(people_list["entries_count"], 1)
        self.assertEqual([person["name"] for person in people_list["preview_people"]], ["Shelley Duvall"])

    def test_query_count_does_not_grow_with_lists(self):
        other = self.user("other")

        def add_lists(start, count):
            for number in range(start, start + count):
                custom_list = self.make_list(other if number % 2 else self.viewer, f"List {number}", ["horror"])
                custom_list.collaborators.add(self.user(f"helper-{number}"))
                item = Item.objects.create(
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    media_id=f"movie-{number}",
                    title=f"Movie {number}",
                    image="https://example.com/poster.jpg",
                )
                CustomListItem.objects.create(custom_list=custom_list, item=item, position=1)

        def count_queries():
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.client.get(URL, {"tag": "horror"}).status_code, status.HTTP_200_OK)
            return len(context)

        add_lists(0, 2)
        few = count_queries()
        add_lists(2, 8)
        self.assertEqual(count_queries(), few)
