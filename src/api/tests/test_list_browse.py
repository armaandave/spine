from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem, PersonListItem
from social.models import ContentLike


class ListBrowseAPITests(TestCase):
    """Pages of lists sort and filter on the server.

    GET /lists/, /users/<u>/lists/ and /lists/tagged/.
    """

    def setUp(self):
        self.client = APIClient()
        users = get_user_model().objects
        self.owner = users.create_user(username="owner")
        self.friend = users.create_user(username="friend")
        self.client.force_authenticate(self.owner)
        self.item_ids = 0

    def make_list(self, name, *, updated_days_ago=0, visibility="public", tags=(), **extra):
        custom_list = CustomList.objects.create(
            owner=self.owner,
            name=name,
            visibility=visibility,
            tags=list(tags),
            **extra,
        )
        CustomList.objects.filter(pk=custom_list.pk).update(
            updated_at=timezone.now() - timedelta(days=updated_days_ago),
        )
        return custom_list

    def add(self, custom_list, media_type, **extra):
        self.item_ids += 1
        item = Item.objects.create(
            source=Sources.MAL.value if media_type == MediaTypes.ANIME.value else Sources.TMDB.value,
            media_type=media_type,
            media_id=str(self.item_ids),
            title=f"{media_type} {self.item_ids}",
            **extra,
        )
        CustomListItem.objects.create(custom_list=custom_list, item=item)

    def names(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [row["name"] for row in response.data["results"]]

    def test_without_a_sort_lists_keep_their_a_to_z_order(self):
        self.make_list("Banana", updated_days_ago=5)
        self.make_list("Apple", updated_days_ago=1)
        self.make_list("Cherry", updated_days_ago=0)

        response = self.client.get("/api/v1/lists/", {"list_type": "all"})

        self.assertEqual(self.names(response), ["Apple", "Banana", "Cherry"])

    def test_last_edited_sorts_newest_first_by_default(self):
        self.make_list("Old", updated_days_ago=9)
        self.make_list("New", updated_days_ago=0)
        self.make_list("Middle", updated_days_ago=3)

        newest = self.client.get("/api/v1/lists/", {"list_type": "all", "sort": "updated"})
        oldest = self.client.get(
            "/api/v1/lists/",
            {"list_type": "all", "sort": "updated", "direction": "asc"},
        )

        self.assertEqual(self.names(newest), ["New", "Middle", "Old"])
        self.assertEqual(self.names(oldest), ["Old", "Middle", "New"])

    def test_name_created_items_and_likes_sorts(self):
        first = self.make_list("beta")
        second = self.make_list("Alpha")
        third = self.make_list("gamma")
        self.add(first, MediaTypes.MOVIE.value)
        for _ in range(3):
            self.add(third, MediaTypes.MOVIE.value)
        people = self.make_list("People", list_type=CustomList.ListType.PEOPLE)
        for index in range(2):
            PersonListItem.objects.create(
                custom_list=people,
                source="tmdb",
                person_id=str(index),
                name=f"Person {index}",
            )
        ContentLike.objects.create(user=self.friend, target_type=ContentLike.CUSTOM_LIST, target_id=second.id)

        def names(**params):
            return self.names(self.client.get("/api/v1/lists/", {"list_type": "all", **params}))

        self.assertEqual(names(sort="name"), ["Alpha", "beta", "gamma", "People"])
        self.assertEqual(names(sort="name", direction="desc"), ["People", "gamma", "beta", "Alpha"])
        self.assertEqual(names(sort="created"), ["People", "gamma", "Alpha", "beta"])
        self.assertEqual(names(sort="created", direction="asc"), ["beta", "Alpha", "gamma", "People"])
        # People count for a people list; ties go A to Z.
        self.assertEqual(names(sort="items"), ["gamma", "People", "beta", "Alpha"])
        self.assertEqual(names(sort="likes"), ["Alpha", "beta", "gamma", "People"])

    def test_media_type_keeps_lists_holding_only_that_type(self):
        movies = self.make_list("Movies")
        self.add(movies, MediaTypes.MOVIE.value)
        mixed = self.make_list("Mixed")
        self.add(mixed, MediaTypes.MOVIE.value)
        self.add(mixed, MediaTypes.BOOK.value)
        shows = self.make_list("Shows")
        self.add(shows, MediaTypes.TV.value)
        self.add(shows, MediaTypes.SEASON.value, season_number=1)
        self.add(shows, MediaTypes.EPISODE.value, season_number=1, episode_number=2)
        self.make_list("Empty")
        self.make_list("People", list_type=CustomList.ListType.PEOPLE)

        def names(media_type):
            return self.names(
                self.client.get("/api/v1/lists/", {"list_type": "all", "media_type": media_type}),
            )

        self.assertEqual(names("movie"), ["Movies"])
        self.assertEqual(names("tv"), ["Shows"])
        self.assertEqual(names("book"), [])

    def test_visibility_filter(self):
        self.make_list("Shared", visibility="public")
        self.make_list("Mine", visibility="private")

        public = self.client.get("/api/v1/lists/", {"list_type": "all", "visibility": "public"})
        private = self.client.get("/api/v1/lists/", {"list_type": "all", "visibility": "private"})

        self.assertEqual(self.names(public), ["Shared"])
        self.assertEqual(self.names(private), ["Mine"])

    def test_bad_values_are_rejected(self):
        for params in (
            {"sort": "popular"},
            {"sort": "name", "direction": "up"},
            {"media_type": "season"},
            {"visibility": "secret"},
        ):
            with self.subTest(params=params):
                response = self.client.get("/api/v1/lists/", params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_to_list_membership_lists_sort_too(self):
        self.make_list("Old", updated_days_ago=4)
        self.make_list("New", updated_days_ago=1)

        response = self.client.get(
            "/api/v1/lists/",
            {
                "ref[source]": Sources.TMDB.value,
                "ref[media_type]": MediaTypes.MOVIE.value,
                "ref[media_id]": "550",
                "sort": "updated",
            },
        )

        self.assertEqual(self.names(response), ["New", "Old"])

    def test_another_users_lists_sort_and_filter(self):
        self.make_list("Old", updated_days_ago=4)
        self.make_list("New", updated_days_ago=1)
        self.make_list("Hidden", visibility="private")
        self.client.force_authenticate(self.friend)

        response = self.client.get("/api/v1/users/owner/lists/", {"list_type": "all", "sort": "updated"})

        self.assertEqual(self.names(response), ["New", "Old"])

    def test_tagged_lists_sort(self):
        self.make_list("Alpha", updated_days_ago=4, tags=["horror"])
        self.make_list("Beta", updated_days_ago=1, tags=["horror"])
        self.make_list("Gamma", updated_days_ago=0, tags=["comedy"])

        default = self.client.get("/api/v1/lists/tagged/", {"tag": "horror", "owner": "owner"})
        updated = self.client.get(
            "/api/v1/lists/tagged/",
            {"tag": "horror", "owner": "owner", "sort": "updated"},
        )

        self.assertEqual(self.names(default), ["Alpha", "Beta"])
        self.assertEqual(self.names(updated), ["Beta", "Alpha"])

    def test_tagged_lists_filter_by_list_type(self):
        self.make_list("Films", tags=["fav"])
        self.make_list("Actors", tags=["fav"], list_type=CustomList.ListType.PEOPLE)

        every = self.client.get("/api/v1/lists/tagged/", {"tag": "fav", "owner": "owner"})
        people = self.client.get(
            "/api/v1/lists/tagged/",
            {"tag": "fav", "owner": "owner", "list_type": "people"},
        )

        self.assertEqual(self.names(every), ["Actors", "Films"])
        self.assertEqual(self.names(people), ["Actors"])

    def test_tagged_lists_report_their_media_types(self):
        movies = self.make_list("Films", tags=["fav"])
        self.add(movies, MediaTypes.MOVIE.value)
        self.add(movies, MediaTypes.EPISODE.value, season_number=1, episode_number=1)

        response = self.client.get("/api/v1/lists/tagged/", {"tag": "fav", "owner": "owner"})

        self.assertEqual(response.data["results"][0]["media_types"], ["movie", "tv"])
