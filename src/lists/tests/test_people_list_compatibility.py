from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from lists.admin import CustomListAdmin, CustomListItemAdmin, PersonListItemAdmin
from lists.models import CustomList, CustomListItem, PersonListItem
from social.models import Activity, ContentLike, Follow, FollowStatus


class PeopleListWebCompatibilityTests(TestCase):
    """Keep the media-only web surface away from provider-backed people."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="people-web",
            password="password",
        )
        self.people_list = CustomList.objects.create(
            owner=self.user,
            name="Directors",
            list_type=CustomList.ListType.PEOPLE,
        )
        self.media_list = CustomList.objects.create(
            owner=self.user,
            name="Movies",
        )
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        self.client.login(username=self.user.username, password="password")

    def test_media_list_page_hides_people_lists(self):
        response = self.client.get(reverse("lists"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["custom_lists"].object_list),
            [self.media_list],
        )

    def test_media_only_web_writes_reject_people_lists(self):
        detail = self.client.get(reverse("list_detail", args=[self.people_list.id]))
        edit = self.client.post(
            reverse("list_edit"),
            {
                "list_id": self.people_list.id,
                "name": "Changed",
                "description": "",
            },
        )
        delete = self.client.post(
            reverse("list_delete"),
            {"list_id": self.people_list.id},
        )
        toggle = self.client.post(
            reverse("list_item_toggle"),
            {
                "custom_list_id": self.people_list.id,
                "item_id": self.item.id,
            },
        )

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(edit.status_code, 404)
        self.assertEqual(delete.status_code, 404)
        self.assertEqual(toggle.status_code, 404)
        self.people_list.refresh_from_db()
        self.assertEqual(self.people_list.name, "Directors")
        self.assertFalse(
            CustomListItem.objects.filter(custom_list=self.people_list).exists(),
        )


class PeopleListAdminCompatibilityTests(TestCase):
    """Admin counts and foreign-key choices respect homogeneous list types."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="people-admin",
            email="admin@example.com",
            password="password",
        )
        self.media_list = CustomList.objects.create(owner=self.user, name="Movies")
        self.people_list = CustomList.objects.create(
            owner=self.user,
            name="Directors",
            list_type=CustomList.ListType.PEOPLE,
        )
        PersonListItem.objects.create(
            custom_list=self.people_list,
            source=Sources.TMDB.value,
            person_id="525",
            name="Christopher Nolan",
            profile_url="https://example.com/nolan.jpg",
        )
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.user

    def test_list_admin_counts_people_and_locks_existing_type(self):
        model_admin = CustomListAdmin(CustomList, admin.site)

        self.assertEqual(model_admin.item_count(self.people_list), 1)
        self.assertIn(
            "list_type",
            model_admin.get_readonly_fields(self.request, self.people_list),
        )
        self.assertNotIn(
            "list_type",
            model_admin.get_readonly_fields(self.request),
        )

    def test_membership_admins_only_offer_matching_list_types(self):
        media_admin = CustomListItemAdmin(CustomListItem, admin.site)
        person_admin = PersonListItemAdmin(PersonListItem, admin.site)

        media_field = media_admin.formfield_for_foreignkey(
            CustomListItem._meta.get_field("custom_list"),
            self.request,
        )
        person_field = person_admin.formfield_for_foreignkey(
            PersonListItem._meta.get_field("custom_list"),
            self.request,
        )

        self.assertQuerySetEqual(media_field.queryset, [self.media_list])
        self.assertQuerySetEqual(person_field.queryset, [self.people_list])


class PeopleListCrossSurfaceAPITests(TestCase):
    """People activity is visible while legacy media filters stay guarded."""

    def setUp(self):
        self.client = APIClient()
        self.owner = get_user_model().objects.create_user(username="people-owner")
        self.follower = get_user_model().objects.create_user(username="people-follower")
        Follow.objects.create(
            from_user=self.follower,
            to_user=self.owner,
            status=FollowStatus.ACCEPTED,
        )
        self.people_list = CustomList.objects.create(
            owner=self.owner,
            name="Favorite Directors",
            list_type=CustomList.ListType.PEOPLE,
            visibility=CustomList.Visibility.PUBLIC,
        )

    def test_media_filter_options_reject_people_lists(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            "/api/v1/filter-options/",
            {"scope": "list", "list_id": self.people_list.id},
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["error"]["code"],
            "list_type_mismatch",
        )

    def test_person_list_adds_are_not_feed_activity(self):
        Activity.objects.create(
            actor=self.owner,
            verb="list_item_added",
            target_type=ContentLike.CUSTOM_LIST,
            target_id=self.people_list.id,
            visibility=CustomList.Visibility.PUBLIC,
            snapshot={
                "name": self.people_list.name,
                "list_name": self.people_list.name,
                "list_type": CustomList.ListType.PEOPLE,
                "entry_type": "person",
                "person": {
                    "source": Sources.TMDB.value,
                    "id": "525",
                    "name": "Christopher Nolan",
                    "profile_url": "https://example.com/nolan.jpg",
                    "known_for_department": "Directing",
                },
            },
        )
        self.client.force_authenticate(self.follower)

        response = self.client.get("/api/v1/feed/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])

    def test_itemless_non_person_list_activity_stays_hidden(self):
        Activity.objects.create(
            actor=self.owner,
            verb="list_item_added",
            target_type=ContentLike.CUSTOM_LIST,
            target_id=self.people_list.id,
            visibility=CustomList.Visibility.PUBLIC,
            snapshot={"list_name": self.people_list.name},
        )
        self.client.force_authenticate(self.follower)

        response = self.client.get("/api/v1/feed/")

        self.assertEqual(response.data["results"], [])
