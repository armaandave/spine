from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import (
    related_sections_from_payload,
    seasons_from_metadata,
)
from api.services import completion as completion_service
from api.services import media as media_service
from app.models import (
    TV,
    Anime,
    Book,
    Episode,
    Game,
    Item,
    MediaSeries,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.providers import services as provider_services
from lists.models import CustomList, CustomListItem, PersonListItem


class CompletionProgressAPITests(TestCase):
    """Finite collection completion contract."""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="completion")
        self.client.force_authenticate(self.user)

    def item(self, media_id, media_type=MediaTypes.MOVIE.value, source=None):
        return Item.objects.create(
            media_id=str(media_id),
            media_type=media_type,
            source=source or self.source_for(media_type),
            title=f"{media_type} {media_id}",
            image=f"https://example.com/{media_id}.jpg",
        )

    @staticmethod
    def source_for(media_type):
        return {
            MediaTypes.ANIME.value: Sources.MAL.value,
            MediaTypes.BOOK.value: Sources.HARDCOVER.value,
            MediaTypes.GAME.value: Sources.IGDB.value,
            MediaTypes.TV.value: Sources.TMDB.value,
        }.get(media_type, Sources.TMDB.value)

    def test_list_and_library_completion_use_latest_completed_status(self):
        completed_item = self.item("1")
        planned_item = self.item("2")
        Movie.objects.bulk_create([
            Movie(
                user=self.user,
                item=completed_item,
                status=Status.COMPLETED.value,
            ),
            Movie(
                user=self.user,
                item=planned_item,
                status=Status.PLANNING.value,
            ),
        ])
        custom_list = CustomList.objects.create(
            owner=self.user,
            name="Two films",
        )
        CustomListItem.objects.bulk_create([
            CustomListItem(custom_list=custom_list, item=completed_item),
            CustomListItem(custom_list=custom_list, item=planned_item),
        ])

        list_response = self.client.get(
            f"/api/v1/lists/{custom_list.id}/",
            {"include_items": "false"},
        )
        summaries_response = self.client.get("/api/v1/lists/")
        library_response = self.client.get(
            "/api/v1/tracking/",
            {"media_type": MediaTypes.MOVIE.value},
        )

        expected = {"completed_count": 1, "total_count": 2}
        self.assertEqual(list_response.data["completion"], expected)
        self.assertNotIn("completion", summaries_response.data["results"][0])
        self.assertEqual(library_response.data["completion"], expected)

    def test_latest_repeat_status_is_canonical(self):
        item = self.item("anime", MediaTypes.ANIME.value)
        Anime.objects.bulk_create([
            Anime(
                user=self.user,
                item=item,
                status=Status.COMPLETED.value,
            ),
            Anime(
                user=self.user,
                item=item,
                status=Status.IN_PROGRESS.value,
            ),
        ])

        self.assertEqual(
            completion_service.completion_for_items(self.user, [item]),
            {"completed_count": 0, "total_count": 1},
        )

    def test_repeat_episode_rows_count_once(self):
        tv_item = self.item("show", MediaTypes.TV.value)
        season_item = Item.objects.create(
            media_id="show",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            season_number=1,
            title="Show",
            image="https://example.com/show.jpg",
        )
        episode_item = Item.objects.create(
            media_id="show",
            media_type=MediaTypes.EPISODE.value,
            source=Sources.TMDB.value,
            season_number=1,
            episode_number=1,
            title="Pilot",
            image="https://example.com/pilot.jpg",
        )
        tv = TV(
            user=self.user,
            item=tv_item,
            status=Status.IN_PROGRESS.value,
        )
        TV.objects.bulk_create([tv])
        season = Season(
            user=self.user,
            item=season_item,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        Season.objects.bulk_create([season])
        Episode.objects.bulk_create([
            Episode(related_season=season, item=episode_item),
            Episode(related_season=season, item=episode_item),
        ])

        completion = completion_service.completion_for_items(
            self.user,
            [episode_item],
        )
        seasons = seasons_from_metadata(
            {
                "source": Sources.TMDB.value,
                "media_id": "show",
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "name": "Season 1",
                            "episode_count": 3,
                        },
                    ],
                },
            },
            user=self.user,
        )

        self.assertEqual(
            completion,
            {"completed_count": 1, "total_count": 1},
        )
        self.assertEqual(
            seasons[0]["completion"],
            {"completed_count": 1, "total_count": 3},
        )

    def test_related_completion_uses_full_set_not_seven_card_preview(self):
        payloads = []
        for index in range(8):
            item = self.item(str(index))
            if index == 7:
                Movie.objects.bulk_create([
                    Movie(
                        user=self.user,
                        item=item,
                        status=Status.COMPLETED.value,
                    ),
                ])
            payloads.append({
                "media_id": str(index),
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "title": f"Film {index}",
                "image": f"https://example.com/{index}.jpg",
            })

        sections = related_sections_from_payload(
            {"Saga": payloads},
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            user=self.user,
        )

        self.assertEqual(len(sections[0]["items"]), 7)
        self.assertEqual(
            sections[0]["completion"],
            {"completed_count": 1, "total_count": 8},
        )

    @patch("api.services.media.provider_services.get_person_page")
    def test_person_returns_overall_media_type_and_role_completion(
        self,
        person_mock,
    ):
        movie_item = self.item("movie")
        tv_item = self.item("tv", MediaTypes.TV.value)
        Movie.objects.bulk_create([
            Movie(
                user=self.user,
                item=movie_item,
                status=Status.COMPLETED.value,
            ),
        ])
        TV.objects.bulk_create([
            TV(
                user=self.user,
                item=tv_item,
                status=Status.PLANNING.value,
            ),
        ])
        person_mock.return_value = {
            "person_id": "10",
            "name": "Person",
            "credits": [
                {
                    "media_id": "movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "title": "Movie",
                    "credit_roles": ["Actor"],
                },
                {
                    "media_id": "tv",
                    "media_type": MediaTypes.TV.value,
                    "source": Sources.TMDB.value,
                    "title": "TV",
                    "credit_roles": ["Director"],
                },
            ],
        }

        response = self.client.get("/api/v1/people/tmdb/10/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["completion"],
            {"completed_count": 1, "total_count": 2},
        )
        self.assertEqual(
            response.data["media_type_completions"]["movie"],
            {"completed_count": 1, "total_count": 1},
        )
        self.assertEqual(
            response.data["role_completions"]["tv"]["Director"],
            {"completed_count": 0, "total_count": 1},
        )

    @patch("api.services.media.provider_services.get_person_page")
    def test_partial_anilist_person_page_has_no_completion(self, person_mock):
        person_mock.return_value = {
            "person_id": "11",
            "name": "Paged Person",
            "credits_page": 1,
            "credits_next_page": 2,
            "credits": [
                {
                    "media_id": "1",
                    "media_type": MediaTypes.ANIME.value,
                    "source": Sources.MAL.value,
                    "title": "Anime",
                    "credit_roles": ["Voice Actor"],
                },
            ],
        }

        response = self.client.get(
            "/api/v1/people/anilist/11/",
            {"credits_page": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["credits_complete"])
        self.assertFalse(response.data["credits_truncated"])
        self.assertIsNone(response.data["completion"])
        self.assertIsNone(response.data["media_type_completions"])
        self.assertIsNone(response.data["role_completions"])

    @patch("api.services.media.provider_services.get_person_page")
    def test_final_anilist_person_page_has_completion(self, person_mock):
        watched = self.item("1", MediaTypes.ANIME.value, Sources.MAL.value)
        Anime.objects.bulk_create([
            Anime(
                user=self.user,
                item=watched,
                status=Status.COMPLETED.value,
            ),
        ])
        person_mock.return_value = {
            "person_id": "11",
            "name": "Paged Person",
            "credits_page": 2,
            "credits_next_page": None,
            "credits_complete": True,
            "credits_truncated": False,
            "credits": [
                {
                    "media_id": "1",
                    "media_type": MediaTypes.ANIME.value,
                    "source": Sources.MAL.value,
                    "title": "Anime",
                    "credit_roles": ["Voice Actor"],
                },
                {
                    "media_id": "2",
                    "media_type": MediaTypes.ANIME.value,
                    "source": Sources.MAL.value,
                    "title": "Other Anime",
                    "credit_roles": ["Voice Actor"],
                },
            ],
        }

        response = self.client.get(
            "/api/v1/people/anilist/11/",
            {"credits_page": 2},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["credits_complete"])
        self.assertFalse(response.data["credits_truncated"])
        self.assertEqual(
            response.data["completion"],
            {"completed_count": 1, "total_count": 2},
        )

    @patch("api.services.media.provider_services.get_person_page")
    def test_truncated_anilist_person_has_no_completion(self, person_mock):
        person_mock.return_value = {
            "person_id": "11",
            "name": "Prolific Person",
            "credits_page": 20,
            "credits_next_page": None,
            "credits_complete": False,
            "credits_truncated": True,
            "credits": [{
                "media_id": "1",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.MAL.value,
                "title": "Anime",
            }],
        }

        response = self.client.get(
            "/api/v1/people/anilist/11/",
            {"credits_page": 20},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["credits_complete"])
        self.assertTrue(response.data["credits_truncated"])
        self.assertIsNone(response.data["completion"])

    @patch("api.services.media.provider_services.get_book_series")
    def test_series_persists_and_legacy_stats_send_empty_progress(
        self,
        series_mock,
    ):
        first = self.item("book-1", MediaTypes.BOOK.value)
        Book.objects.bulk_create([
            Book(
                user=self.user,
                item=first,
                status=Status.COMPLETED.value,
            ),
        ])
        series_mock.return_value = {
            "series_id": "series-1",
            "source": Sources.HARDCOVER.value,
            "media_type": MediaTypes.BOOK.value,
            "name": "Book Saga",
            "item_count": 2,
            "items": [
                {
                    "media_id": "book-1",
                    "title": "Book 1",
                    "image": "https://example.com/book-1.jpg",
                },
                {
                    "media_id": "book-2",
                    "title": "Book 2",
                    "image": "https://example.com/book-2.jpg",
                },
            ],
        }

        series_response = self.client.get(
            "/api/v1/series/hardcover/series-1/",
        )
        second = Item.objects.get(
            media_type=MediaTypes.BOOK.value,
            media_id="book-2",
        )
        custom_list = CustomList.objects.create(
            owner=self.user,
            name="Reading list",
        )
        CustomListItem.objects.bulk_create([
            CustomListItem(custom_list=custom_list, item=first),
            CustomListItem(custom_list=custom_list, item=second),
        ])
        stats_response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "all", "end_date": "all"},
        )

        expected = {"completed_count": 1, "total_count": 2}
        self.assertEqual(series_response.data["completion"], expected)
        self.assertTrue(
            MediaSeries.objects.filter(
                source=Sources.HARDCOVER.value,
                series_id="series-1",
            ).exists(),
        )
        # STAT-900: installed builds decode both keys but show neither.
        self.assertEqual(stats_response.data["series_progress"], [])
        self.assertEqual(stats_response.data["list_progress"], [])

    @patch("api.services.media.provider_services.get_book_series")
    def test_incomplete_series_payload_has_no_completion(
        self,
        series_mock,
    ):
        series_mock.return_value = {
            "series_id": "partial-series",
            "source": Sources.HARDCOVER.value,
            "media_type": MediaTypes.BOOK.value,
            "name": "Partial Saga",
            "item_count": 2,
            "items": [
                {
                    "media_id": "book-1",
                    "title": "Book 1",
                },
            ],
        }

        response = self.client.get(
            "/api/v1/series/hardcover/partial-series/",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["completion"])
        self.assertFalse(
            MediaSeries.objects.filter(series_id="partial-series").exists(),
        )

    @patch("api.services.media.provider_services.company_catalog_count")
    @patch("api.services.media.provider_services.get_company_catalog")
    @patch("api.services.media.provider_services.get_company")
    def test_company_role_page_and_deduped_completion(
        self,
        company_mock,
        catalog_mock,
        count_mock,
    ):
        first = self.item("game-1", MediaTypes.GAME.value)
        Game.objects.bulk_create([
            Game(
                user=self.user,
                item=first,
                status=Status.COMPLETED.value,
            ),
        ])
        game_one = {
            "media_id": "game-1",
            "title": "Game 1",
            "image": "https://example.com/game-1.jpg",
        }
        game_two = {
            "media_id": "game-2",
            "title": "Game 2",
            "image": "https://example.com/game-2.jpg",
        }
        company_mock.return_value = {
            "id": 7,
            "name": "Studio",
            "logo": {},
            "websites": [],
        }
        catalog_mock.side_effect = [
            [game_one, game_two],
            [game_one],
            [game_one, game_two],
        ]
        count_mock.side_effect = [2, 1]

        detail = self.client.get("/api/v1/companies/igdb/7/")
        page = self.client.get(
            "/api/v1/companies/igdb/7/games/",
            {"role": "developed"},
        )

        self.assertEqual(
            detail.data["completion"],
            {"completed_count": 1, "total_count": 2},
        )
        self.assertEqual(
            detail.data["catalogs"]["developed"]["completion"],
            {"completed_count": 1, "total_count": 2},
        )
        self.assertEqual(
            detail.data["catalogs"]["published"]["completion"],
            {"completed_count": 1, "total_count": 1},
        )
        self.assertEqual(
            page.data["completion"],
            {"completed_count": 1, "total_count": 2},
        )

    @patch("api.services.media.mal.studio_anime_completion_catalog")
    @patch("api.services.media.provider_services.get_company_anime")
    def test_anime_studio_completion_uses_full_catalog_on_first_page(
        self,
        catalog_mock,
        completion_catalog_mock,
    ):
        watched = self.item("1", MediaTypes.ANIME.value, Sources.MAL.value)
        Anime.objects.bulk_create([
            Anime(
                user=self.user,
                item=watched,
                status=Status.COMPLETED.value,
            ),
        ])
        catalog_mock.return_value = {
            "page": 1,
            "previous_page": None,
            "next_page": 2,
            "results": [
                {
                    "media_id": "1",
                    "title": "Anime",
                    "image": "https://example.com/anime.jpg",
                },
            ],
        }
        completion_catalog_mock.return_value = {
            "complete": True,
            "results": [
                {"media_id": "1"},
                {"media_id": "2"},
            ],
        }

        response = self.client.get("/api/v1/companies/mal/1/anime/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["completion"],
            {"completed_count": 1, "total_count": 2},
        )

    @patch("api.services.media.anilist.person_completion_credits")
    @patch("api.services.media.anilist.person_page")
    def test_person_completion_enumerates_finite_anilist_filmography(
        self,
        person_mock,
        completion_credits_mock,
    ):
        watched = self.item("1", MediaTypes.ANIME.value, Sources.MAL.value)
        Anime.objects.bulk_create([
            Anime(
                user=self.user,
                item=watched,
                status=Status.COMPLETED.value,
            ),
        ])
        person_mock.return_value = {
            "person_id": "11",
            "name": "Person",
            "credits_complete": False,
            "credits_next_page": 2,
        }
        completion_credits_mock.return_value = [
            {
                "media_id": "1",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.MAL.value,
            },
            {
                "media_id": "2",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.MAL.value,
            },
        ]

        result = media_service.person_completion(
            source="anilist",
            person_id="11",
            user=self.user,
        )

        self.assertEqual(
            result,
            {"completed_count": 1, "total_count": 2},
        )
        person_mock.assert_called_once_with(
            "11",
            enrich_credit_images=False,
            request_session=provider_services.person_search_session,
        )
        completion_credits_mock.assert_called_once_with("11", person_mock.return_value)

    @patch(
        "api.services.media.anilist.person_completion_credits",
        return_value=None,
    )
    @patch("api.services.media.anilist.person_page")
    def test_person_completion_rejects_unproven_anilist_filmography(
        self,
        person_mock,
        _completion_credits_mock,
    ):
        person_mock.return_value = {
            "credits_complete": False,
            "credits_truncated": True,
            "credits": [{
                "media_id": "1",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.MAL.value,
            }],
        }

        result = media_service.person_completion(
            source="anilist",
            person_id="11",
            user=self.user,
        )

        self.assertIsNone(result)
        person_mock.assert_called_once_with(
            "11",
            enrich_credit_images=False,
            request_session=provider_services.person_search_session,
        )

    @patch("api.views.media.media_service.person_completion")
    def test_person_completion_endpoint_returns_viewer_progress(
        self,
        completion_mock,
    ):
        completion_mock.return_value = {
            "completed_count": 3,
            "total_count": 6,
        }

        response = self.client.get(
            "/api/v1/people/anilist/11/completion/",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["completion"], completion_mock.return_value)
        completion_mock.assert_called_once_with(
            source="anilist",
            person_id="11",
            user=self.user,
        )

    @patch("api.views.media.media_service.person_completion")
    def test_person_completion_endpoint_allows_unproven_null(
        self,
        completion_mock,
    ):
        completion_mock.return_value = None

        response = self.client.get(
            "/api/v1/people/anilist/11/completion/",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["completion"])

    @patch("api.views.media.media_service.person_completion")
    def test_person_completion_endpoint_requires_authentication(
        self,
        completion_mock,
    ):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            "/api/v1/people/anilist/11/completion/",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        completion_mock.assert_not_called()

    @patch("api.views.media.media_service.person_completion")
    def test_person_completion_endpoint_maps_provider_not_found(
        self,
        completion_mock,
    ):
        provider_response = type(
            "ProviderResponse",
            (),
            {"status_code": 404, "text": "missing"},
        )()
        completion_mock.side_effect = provider_services.ProviderAPIError(
            "anilist",
            requests.HTTPError(response=provider_response),
        )

        response = self.client.get(
            "/api/v1/people/anilist/missing/completion/",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data, {"detail": "Person not found."})

    @patch("api.views.media.media_service.person_completion")
    def test_person_completion_endpoint_uses_provider_error_envelope(
        self,
        completion_mock,
    ):
        completion_mock.side_effect = provider_services.ProviderAPIError(
            "anilist",
            requests.Timeout("offline"),
        )

        response = self.client.get(
            "/api/v1/people/anilist/11/completion/",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            response.data["error"]["code"],
            "provider_unavailable",
        )

    def test_person_completion_endpoint_rejects_unknown_provider(self):
        response = self.client.get(
            "/api/v1/people/unknown/11/completion/",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_501_NOT_IMPLEMENTED,
        )

    @patch("api.views.lists.media_service.person_completion")
    def test_people_list_completion_is_opt_in_and_failure_isolated(
        self,
        completion_mock,
    ):
        people_list = CustomList.objects.create(
            owner=self.user,
            name="People",
            list_type=CustomList.ListType.PEOPLE,
        )
        PersonListItem.objects.bulk_create([
            PersonListItem(
                custom_list=people_list,
                source=Sources.TMDB.value,
                person_id="1",
                name="One",
            ),
            PersonListItem(
                custom_list=people_list,
                source=Sources.TMDB.value,
                person_id="2",
                name="Two",
            ),
        ])

        def result(*, person_id, **_kwargs):
            if person_id == "2":
                raise RuntimeError("offline")
            return {"completed_count": 3, "total_count": 4}

        completion_mock.side_effect = result
        response = self.client.get(
            f"/api/v1/lists/{people_list.id}/people/",
            {"include_completion": "true"},
        )
        entries = {entry["id"]: entry for entry in response.data["results"]}

        self.assertEqual(
            entries["1"]["completion"],
            {"completed_count": 3, "total_count": 4},
        )
        self.assertIsNone(entries["2"]["completion"])
