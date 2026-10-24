from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from app.providers import services as provider_services
from lists.models import CustomList, CustomListItem, PersonListItem
from social.models import Activity, Block, SocialAuditLog


class PeopleListsAPITests(TestCase):
    """Typed people-list API contract and media-list compatibility."""

    def setUp(self):
        self.client = APIClient()
        self.owner = get_user_model().objects.create_user(username="owner")
        self.collaborator = get_user_model().objects.create_user(
            username="collaborator",
        )
        self.outsider = get_user_model().objects.create_user(
            username="outsider",
        )
        self.people_list = CustomList.objects.create(
            owner=self.owner,
            name="Favorite people",
            list_type=CustomList.ListType.PEOPLE,
        )
        self.people_list.collaborators.add(self.collaborator)
        self.media_list = CustomList.objects.create(
            owner=self.owner,
            name="Favorite media",
        )
        self.client.force_authenticate(self.owner)

    @staticmethod
    def person(person_id="819", name="Edward Norton"):
        return {
            "source": Sources.TMDB.value,
            "person_id": person_id,
            "name": name,
            "image": "https://example.com/person.jpg",
            "known_for_department": "Acting",
            "credits": [],
        }

    def create_person(self, **overrides):
        values = {
            "custom_list": self.people_list,
            "source": Sources.TMDB.value,
            "person_id": "819",
            "name": "Edward Norton",
            "profile_url": "https://example.com/person.jpg",
            "known_for_department": "Acting",
        }
        values.update(overrides)
        return PersonListItem.objects.create(**values)

    def test_existing_lists_default_to_media_and_default_collection_hides_people(self):
        response = self.client.get("/api/v1/lists/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([entry["id"] for entry in response.data["results"]], [
            self.media_list.id,
        ])
        self.assertEqual(response.data["results"][0]["list_type"], "media")
        self.assertEqual(response.data["results"][0]["entries_count"], 0)
        self.assertEqual(response.data["results"][0]["preview_people"], [])

    def test_collection_can_select_people_or_all(self):
        people = self.client.get("/api/v1/lists/", {"list_type": "people"})
        all_lists = self.client.get("/api/v1/lists/", {"list_type": "all"})

        self.assertEqual(
            [entry["id"] for entry in people.data["results"]],
            [self.people_list.id],
        )
        self.assertEqual(
            {entry["id"] for entry in all_lists.data["results"]},
            {self.people_list.id, self.media_list.id},
        )

    def test_people_membership_query_does_not_contact_provider(self):
        self.create_person()

        with patch(
            "api.views.lists.provider_services.get_person_page",
        ) as person_mock:
            response = self.client.get(
                "/api/v1/lists/",
                {
                    "person_ref[source]": Sources.TMDB.value,
                    "person_ref[id]": "819",
                },
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["results"][0]["has_person"])
        self.assertEqual(
            response.data["results"][0]["person_entry_id"],
            self.people_list.person_items.get().id,
        )
        person_mock.assert_not_called()

    @patch("api.views.lists.provider_services.get_person_page")
    def test_adds_provider_validated_snapshot_and_is_idempotent(self, person_mock):
        person_mock.return_value = self.person()
        url = f"/api/v1/lists/{self.people_list.id}/people/"
        payload = {
            "ref": {
                "source": Sources.TMDB.value,
                "id": "819",
            },
        }

        created = self.client.post(url, payload, format="json")
        person_mock.reset_mock()
        duplicate = self.client.post(url, payload, format="json")

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertTrue(created.data["created"])
        self.assertEqual(created.data["person"]["id"], "819")
        self.assertEqual(created.data["person"]["name"], "Edward Norton")
        self.assertEqual(
            created.data["person"]["profile_url"],
            "https://example.com/person.jpg",
        )
        self.assertEqual(
            created.data["person"]["known_for_department"],
            "Acting",
        )
        self.assertEqual(duplicate.status_code, status.HTTP_200_OK)
        self.assertFalse(duplicate.data["created"])
        self.assertEqual(
            PersonListItem.objects.filter(custom_list=self.people_list).count(),
            1,
        )
        self.assertEqual(
            Activity.objects.filter(
                target_id=self.people_list.id,
                verb="list_item_added",
            ).count(),
            1,
        )
        person_mock.assert_not_called()

    @patch("api.views.lists.provider_services.get_person_page")
    def test_collaborator_can_add_person(self, person_mock):
        person_mock.return_value = self.person(person_id="20", name="Someone")
        self.client.force_authenticate(self.collaborator)

        response = self.client.post(
            f"/api/v1/lists/{self.people_list.id}/people/",
            {"ref": {"source": Sources.TMDB.value, "id": "20"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @patch("api.views.lists.provider_services.get_person_page")
    def test_all_supported_person_sources_can_be_added(self, person_mock):
        for index, source in enumerate(
            provider_services.SUPPORTED_PERSON_SOURCES,
            start=1,
        ):
            with self.subTest(source=source):
                custom_list = CustomList.objects.create(
                    owner=self.owner,
                    name=f"{source} people",
                    list_type=CustomList.ListType.PEOPLE,
                )
                person_mock.return_value = {
                    "person_id": str(index),
                    "name": f"{source} person",
                    "credits": [],
                }

                response = self.client.post(
                    f"/api/v1/lists/{custom_list.id}/people/",
                    {"ref": {"source": source, "id": str(index)}},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertTrue(
                    PersonListItem.objects.filter(
                        custom_list=custom_list,
                        source=source,
                        person_id=str(index),
                    ).exists(),
                )

    @patch("api.views.lists.provider_services.get_person_page")
    def test_outsider_cannot_add_and_provider_is_not_called(self, person_mock):
        self.client.force_authenticate(self.outsider)

        response = self.client.post(
            f"/api/v1/lists/{self.people_list.id}/people/",
            {"ref": {"source": Sources.TMDB.value, "id": "20"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        person_mock.assert_not_called()

    def test_blocked_users_cannot_read_public_people_lists_in_either_direction(self):
        self.people_list.visibility = CustomList.Visibility.PUBLIC
        self.people_list.save(update_fields=["visibility"])
        url = f"/api/v1/lists/{self.people_list.id}/people/"

        for blocker, blocked in (
            (self.owner, self.outsider),
            (self.outsider, self.owner),
        ):
            with self.subTest(blocker=blocker.username):
                Block.objects.all().delete()
                Block.objects.create(blocker=blocker, blocked=blocked)
                self.client.force_authenticate(self.outsider)

                response = self.client.get(url)

                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("api.views.lists.provider_services.get_person_page")
    def test_provider_not_found_creates_nothing(self, person_mock):
        person_mock.side_effect = lambda *_args: provider_services.raise_not_found_error(
            Sources.TMDB.value,
            "missing",
            "person",
        )

        response = self.client.post(
            f"/api/v1/lists/{self.people_list.id}/people/",
            {"ref": {"source": Sources.TMDB.value, "id": "missing"}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            PersonListItem.objects.filter(custom_list=self.people_list).exists(),
        )

    @patch("api.views.lists.provider_services.get_person_page")
    def test_provider_outage_creates_nothing(self, person_mock):
        person_mock.side_effect = provider_services.ProviderAPIError(
            Sources.TMDB.value,
            RuntimeError("offline"),
        )

        response = self.client.post(
            f"/api/v1/lists/{self.people_list.id}/people/",
            {"ref": {"source": Sources.TMDB.value, "id": "819"}},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertFalse(
            PersonListItem.objects.filter(custom_list=self.people_list).exists(),
        )

    @patch("api.views.lists.provider_services.get_person_page")
    def test_malformed_provider_person_creates_nothing(self, person_mock):
        person_mock.return_value = {"person_id": "819", "name": ""}

        response = self.client.post(
            f"/api/v1/lists/{self.people_list.id}/people/",
            {"ref": {"source": Sources.TMDB.value, "id": "819"}},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertFalse(
            PersonListItem.objects.filter(custom_list=self.people_list).exists(),
        )

    def test_people_endpoint_rejects_media_list_and_media_endpoint_rejects_people_list(self):
        people_response = self.client.get(
            f"/api/v1/lists/{self.media_list.id}/people/",
        )
        media_response = self.client.get(
            f"/api/v1/lists/{self.people_list.id}/items/",
        )

        self.assertEqual(people_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(media_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            people_response.data["error"]["code"],
            "list_type_mismatch",
        )

    def test_people_read_is_paginated_from_snapshots(self):
        first = self.create_person(person_id="1", name="First", position=2)
        second = self.create_person(person_id="2", name="Second", position=1)

        with patch(
            "api.views.lists.provider_services.get_person_page",
        ) as person_mock:
            response = self.client.get(
                f"/api/v1/lists/{self.people_list.id}/people/",
                {"page_size": 1},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["entry_id"], second.id)
        self.assertNotEqual(first.id, second.id)
        self.assertIsNotNone(response.data["next"])
        person_mock.assert_not_called()

    def test_people_preview_is_ordered_capped_and_keeps_legacy_fields_empty(self):
        for index in range(13):
            self.create_person(
                person_id=str(index),
                name=f"Person {index}",
                position=13 - index,
            )

        response = self.client.get("/api/v1/lists/", {"list_type": "people"})
        result = response.data["results"][0]

        self.assertEqual(result["list_type"], "people")
        self.assertEqual(result["entries_count"], 13)
        self.assertEqual(result["people_count"], 13)
        self.assertEqual(result["items_count"], 0)
        self.assertEqual(result["preview_items"], [])
        self.assertEqual(len(result["preview_people"]), 12)
        self.assertEqual(result["preview_people"][0]["name"], "Person 12")

    def test_ranked_reorder_and_delete_keep_contiguous_positions(self):
        self.people_list.is_ranked = True
        self.people_list.save(update_fields=["is_ranked"])
        people = [
            self.create_person(
                person_id=str(index),
                name=f"Person {index}",
                position=index,
            )
            for index in range(1, 4)
        ]

        reordered = self.client.patch(
            f"/api/v1/lists/{self.people_list.id}/people/reorder/",
            {"entry_ids": [people[2].id, people[0].id, people[1].id]},
            format="json",
        )
        deleted = self.client.delete(
            f"/api/v1/lists/{self.people_list.id}/people/{people[0].id}/",
        )

        self.assertEqual(reordered.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [person["entry_id"] for person in reordered.data["people"]],
            [people[2].id, people[0].id, people[1].id],
        )
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            list(
                PersonListItem.objects.filter(
                    custom_list=self.people_list,
                ).values_list("id", "position"),
            ),
            [(people[2].id, 1), (people[1].id, 2)],
        )

    def test_reorder_requires_exact_entry_set_without_partial_write(self):
        people = [
            self.create_person(person_id=str(index), name=f"Person {index}")
            for index in range(2)
        ]

        response = self.client.patch(
            f"/api/v1/lists/{self.people_list.id}/people/reorder/",
            {"entry_ids": [people[0].id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            all(
                position is None
                for position in PersonListItem.objects.filter(
                    custom_list=self.people_list,
                ).values_list("position", flat=True)
            ),
        )

    def test_list_type_is_immutable(self):
        response = self.client.patch(
            f"/api/v1/lists/{self.people_list.id}/",
            {"list_type": "media"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.people_list.refresh_from_db()
        self.assertEqual(self.people_list.list_type, CustomList.ListType.PEOPLE)

    def test_duplicate_generated_slug_is_a_validation_error(self):
        response = self.client.post(
            "/api/v1/lists/",
            {"name": self.people_list.name, "list_type": "people"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("slug", response.data["error"]["fields"])

    def test_people_list_visibility_change_is_audited(self):
        response = self.client.patch(
            f"/api/v1/lists/{self.people_list.id}/",
            {"visibility": "public"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        audit = SocialAuditLog.objects.get(
            action="list_visibility_update",
            target_id=self.people_list.id,
        )
        self.assertEqual(audit.metadata["previous"], "private")
        self.assertEqual(audit.metadata["current"], "public")
        self.assertEqual(audit.metadata["list_type"], "people")

    @patch("api.serializers.common.resolved_item_backdrop_urls", return_value=(None, None))
    def test_media_list_payload_and_routes_still_use_media_contract(self, _backdrop_mock):
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
        )
        CustomListItem.objects.create(custom_list=self.media_list, item=item)

        detail = self.client.get(f"/api/v1/lists/{self.media_list.id}/")
        items = self.client.get(
            f"/api/v1/lists/{self.media_list.id}/items/",
        )

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["list_type"], "media")
        self.assertEqual(detail.data["items_count"], 1)
        self.assertEqual(detail.data["entries_count"], 1)
        self.assertEqual(detail.data["items"][0]["title"], "Fight Club")
        self.assertEqual(detail.data["people"], [])
        self.assertEqual(items.status_code, status.HTTP_200_OK)
        self.assertEqual(items.data["results"][0]["title"], "Fight Club")
