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
    """GET /api/v1/lists/tagged/: a list page's tag links, scoped to its owner."""

    def setUp(self):
        self.viewer = self.user("viewer")
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)

    def user(self, username, *, private=False):
        return get_user_model().objects.create_user(username=username, profile_private=private)

    def make_list(self, owner, name, tags, visibility=PUBLIC, **fields):
        return CustomList.objects.create(owner=owner, name=name, tags=tags, visibility=visibility, **fields)

    def names(self, owner, tag="horror", **params):
        response = self.client.get(URL, {"tag": tag, "owner": owner.username, **params})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [result["name"] for result in response.data["results"]]

    def test_requires_a_tag_and_an_owner(self):
        for params in ({}, {"owner": "viewer"}, {"tag": "horror"}, {"tag": " ", "owner": "viewer"}):
            response = self.client.get(URL, params)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_owner_is_not_found(self):
        response = self.client.get(URL, {"tag": "horror", "owner": "nobody"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_sign_in(self):
        self.assertIn(
            APIClient().get(URL, {"tag": "horror", "owner": "viewer"}).status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_shows_only_the_owners_lists(self):
        other = self.user("other")
        self.make_list(self.viewer, "Mine", ["horror"])
        self.make_list(other, "Theirs", ["horror"])
        shared = self.make_list(other, "Theirs, shared with me", ["horror"])
        shared.collaborators.add(self.viewer)

        self.assertEqual(self.names(self.viewer), ["Mine"])
        self.assertEqual(self.names(other), ["Theirs", "Theirs, shared with me"])

    def test_matches_whole_tags_ignoring_case_and_outer_spaces(self):
        self.make_list(self.viewer, "Shouty", ["HORROR"])
        self.make_list(self.viewer, "Padded", [" Horror ", "70s"])
        self.make_list(self.viewer, "Comedy", ["horror comedy"])
        self.make_list(self.viewer, "Untagged", [])

        self.assertEqual(self.names(self.viewer), ["Padded", "Shouty"])
        self.assertEqual(self.names(self.viewer, tag="  hOrRoR "), ["Padded", "Shouty"])

    def test_matches_non_ascii_tags(self):
        self.make_list(self.viewer, "Paris", ["Ciné Français"])

        self.assertEqual(self.names(self.viewer, tag="ciné français"), ["Paris"])
        self.assertEqual(self.names(self.viewer, tag="cine francais"), [])

    def test_owner_sees_every_list_of_theirs(self):
        self.make_list(self.viewer, "Private", ["horror"], PRIVATE)
        self.make_list(self.viewer, "Unlisted", ["horror"], UNLISTED)
        self.make_list(self.viewer, "Public", ["horror"])

        self.assertEqual(self.names(self.viewer), ["Private", "Public", "Unlisted"])

    def test_others_see_public_lists_and_ones_they_help_edit(self):
        owner = self.user("owner")
        self.make_list(owner, "Public", ["horror"])
        self.make_list(owner, "Unlisted", ["horror"], UNLISTED)
        self.make_list(owner, "Private", ["horror"], PRIVATE)
        shared = self.make_list(owner, "Private, shared with me", ["horror"], PRIVATE)
        shared.collaborators.add(self.viewer, self.user("helper"))

        self.assertEqual(self.names(owner), ["Private, shared with me", "Public"])

    def test_private_accounts_show_lists_to_accepted_followers_only(self):
        followed = self.user("followed", private=True)
        pending = self.user("pending", private=True)
        Follow.objects.create(from_user=self.viewer, to_user=followed, status=FollowStatus.ACCEPTED)
        Follow.objects.create(from_user=self.viewer, to_user=pending, status=FollowStatus.PENDING)
        self.make_list(followed, "Followed", ["horror"])
        self.make_list(pending, "Pending", ["horror"])

        self.assertEqual(self.names(followed), ["Followed"])
        self.assertEqual(self.names(pending), [])

    def test_lists_you_help_edit_show_even_on_a_private_account(self):
        owner = self.user("owner", private=True)
        self.make_list(owner, "Public", ["horror"])
        shared = self.make_list(owner, "Shared with me", ["horror"], PRIVATE)
        shared.collaborators.add(self.viewer)

        self.assertEqual(self.names(owner), ["Shared with me"])

    def test_blocks_hide_lists_both_ways(self):
        blocker = self.user("blocker")
        blocked = self.user("blocked")
        Block.objects.create(blocker=blocker, blocked=self.viewer)
        Block.objects.create(blocker=self.viewer, blocked=blocked)
        self.make_list(blocker, "From blocker", ["horror"])
        shared = self.make_list(blocker, "Shared by blocker", ["horror"], PRIVATE)
        shared.collaborators.add(self.viewer)
        self.make_list(blocked, "From blocked", ["horror"])

        self.assertEqual(self.names(blocker), [])
        self.assertEqual(self.names(blocked), [])

    def test_featured_lists_belong_to_their_owner(self):
        spine = self.user("Spine")
        self.make_list(spine, "Hardcover Fiction", ["NYT Best Sellers", "Books"], is_featured=True)
        self.make_list(spine, "Hidden chart", ["NYT Best Sellers"], PRIVATE, is_featured=True)
        self.make_list(self.viewer, "My Best Sellers", ["NYT Best Sellers"])

        self.assertEqual(self.names(spine, tag="nyt best sellers"), ["Hardcover Fiction"])

    def test_lists_a_to_z(self):
        for name in ["banana", "Cherry", "apple"]:
            self.make_list(self.viewer, name, ["horror"])

        self.assertEqual(self.names(self.viewer), ["apple", "banana", "Cherry"])

    def test_pages_like_other_collections(self):
        for number in range(30):
            self.make_list(self.viewer, f"List {number:02d}", ["horror"])

        first = self.client.get(URL, {"tag": "horror", "owner": "viewer"}).data
        self.assertEqual(first["count"], 30)
        self.assertEqual(len(first["results"]), 25)
        self.assertIn("page=2", first["next"])
        self.assertIn("tag=horror", first["next"])
        self.assertIn("owner=viewer", first["next"])

        second = self.client.get(URL, {"tag": "horror", "owner": "viewer", "page": 2}).data
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

        response = self.client.get(URL, {"tag": "horror", "owner": "viewer"})

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
        owner = self.user("owner")

        def add_lists(start, count):
            for number in range(start, start + count):
                custom_list = self.make_list(owner, f"List {number}", ["horror"])
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
                response = self.client.get(URL, {"tag": "horror", "owner": "owner"})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
            return len(context)

        add_lists(0, 2)
        few = count_queries()
        add_lists(2, 8)
        self.assertEqual(count_queries(), few)
