from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from api.views.featured import list_payloads
from api.views.lists import list_payload
from app.models import Item, MediaTypes, Sources
from lists.featured import registry
from lists.featured.registry import LiveWindow
from lists.featured.types import Entry
from lists.models import CustomList, CustomListItem, FeaturedListSync, PersonListItem
from lists.tests.featured.helpers import FeaturedTestCase, make_definition
from social.models import ContentLike

QUERY_CEILING = 16


def movie_entries(prefix, count, media_type="movie", source="tmdb"):
    """Return ``count`` entries with direct ids and TMDB-style posters."""
    id_name = {"tmdb": "tmdb", "hardcover": "hardcover", "musicbrainz": "mbid_rg"}[source]
    return [
        Entry(
            rank,
            f"{prefix} {rank}",
            media_type=media_type,
            ids={id_name: f"{prefix}-{rank}"},
            image=f"https://image.tmdb.org/t/p/w500/{prefix}-{rank}.jpg",
        )
        for rank in range(1, count + 1)
    ]


class FeaturedApiTestCase(FeaturedTestCase):
    """Featured lists built through the engine, plus a viewer."""

    def setUp(self):
        super().setUp()
        self.viewer = get_user_model().objects.create_user(username="viewer")
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        now = timezone.now()
        window = LiveWindow(starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=2), label="Ceremony")
        self.definitions = [
            make_definition(key="headline-films", name="Headline Films", position=1, headline=True, kind="community"),
            make_definition(key="chart-films", name="Chart Films", position=101, cadence=timedelta(days=7)),
            make_definition(key="both-types", name="Both Types", position=150, kind="canon", media_types=("movie", "tv")),
            make_definition(key="awards-live", name="Awards Live", position=160, kind="award", live_windows=(window,)),
            make_definition(key="across", name="Across", position=850, kind="award", media_types=("movie", "tv", "book")),
            make_definition(key="album-chart", name="Album Chart", position=701, media_types=("music",)),
            make_definition(key="laureates", name="Laureates", position=950, kind="award", list_type="people", media_types=("book",)),
        ]
        entries = {
            "headline-films": movie_entries("hf", 2),
            "chart-films": movie_entries("cf", 7),
            "both-types": [*movie_entries("bm", 1), *movie_entries("bt", 1, media_type="tv")],
            "awards-live": movie_entries("al", 2),
            "across": [*movie_entries("am", 1), *movie_entries("ab", 1, media_type="book", source="hardcover")],
            "album-chart": movie_entries("mu", 2, media_type="music", source="musicbrainz"),
            "laureates": [Entry(1, "Annie Ernaux", ids={"openlibrary_person": "OL1A"}, image="https://p/1.jpg")],
        }
        with registry.override_definitions(self.definitions):
            for definition in self.definitions:
                result = self.run_list(definition, entries[definition.key])
                self.assertTrue(result.ok, result.message)
        FeaturedListSync.objects.filter(key="both-types").update(content_changed_at=now - timedelta(days=30))
        self.manual = CustomList.objects.create(
            owner=self.owner,
            name="Staff Picks",
            visibility=CustomList.Visibility.PUBLIC,
            is_featured=True,
            featured_position=40,
        )
        CustomListItem.objects.create(custom_list=self.manual, item=Item.objects.get(media_id="hf-1"), position=1)

    def get(self, path, **params):
        with registry.override_definitions(self.definitions):
            return self.client.get(path, params)


class LegacyFeaturedEndpointTests(FeaturedApiTestCase):
    """GET /api/v1/lists/featured/ keeps today's shape for installed builds."""

    def test_default_shows_headline_and_manual_lists_only(self):
        response = self.get("/api/v1/lists/featured/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["name"] for row in response.data["results"]], ["Headline Films", "Staff Picks"])
        self.assertEqual(response.data["count"], 2)
        headline, manual = response.data["results"]
        self.assertEqual(headline["featured"]["kind"], "community")
        self.assertEqual(headline["featured"]["source"], {"name": None, "url": None})
        self.assertNotIn("preview_items", headline["featured"])
        self.assertEqual((manual["featured"]["kind"], manual["featured"]["cadence"]), ("editorial", "static"))
        self.assertEqual(headline["preview_items"][0]["title"], "hf 1")

    def test_list_detail_carries_the_featured_block(self):
        chart = CustomList.objects.get(import_source_id="chart-films")
        response = self.get(f"/api/v1/lists/{chart.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["featured"]["slug"], "chart-films")
        self.assertEqual(response.data["featured"]["items_count"], 7)
        self.assertNotIn("preview_items", response.data["featured"])
        private = CustomList.objects.create(owner=self.viewer, name="Mine")
        self.assertNotIn("featured", self.get(f"/api/v1/lists/{private.id}/").data)

    def test_view_all_returns_every_featured_media_list(self):
        response = self.get("/api/v1/lists/featured/", view="all")
        self.assertEqual(
            [row["name"] for row in response.data["results"]],
            ["Headline Films", "Staff Picks", "Chart Films", "Both Types", "Awards Live", "Album Chart", "Across"],
        )
        response = self.get("/api/v1/lists/featured/", view="all", list_type="all")
        self.assertIn("Laureates", [row["name"] for row in response.data["results"]])

    def test_batched_payload_matches_list_payload(self):
        collaborator = get_user_model().objects.create_user(username="helper")
        chart = CustomList.objects.get(import_source_id="chart-films")
        chart.collaborators.add(collaborator)
        ContentLike.objects.create(user=self.viewer, target_type=ContentLike.CUSTOM_LIST, target_id=chart.id)
        lists = list(CustomList.objects.filter(is_featured=True).order_by("featured_position", "id"))
        drf_request = Request(APIRequestFactory().get("/api/v1/lists/featured/"))
        drf_request.user = self.viewer
        expected = [list_payload(item, request=drf_request, include_preview_items=True) for item in lists]
        self.assertEqual(list_payloads(lists, drf_request), expected)

    def test_query_count_does_not_grow_with_lists(self):
        def count_queries():
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.get("/api/v1/lists/featured/", view="all", list_type="all").status_code, 200)
            return len(context)

        small = count_queries()
        more = []
        for number in range(6):
            definition = make_definition(key=f"extra-{number}", position=110 + number)
            more.append(definition)
            with registry.override_definitions([*self.definitions, definition]):
                self.run_list(definition, movie_entries(f"x{number}", 3))
        self.definitions = [*self.definitions, *more]
        with self.assertNumQueries(small):
            self.get("/api/v1/lists/featured/", view="all", list_type="all")
        self.assertLessEqual(small, QUERY_CEILING)


class ShelvesEndpointTests(FeaturedApiTestCase):
    """GET /api/v1/lists/featured/shelves/."""

    def shelves(self, **params):
        response = self.get("/api/v1/lists/featured/shelves/", **params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_landing_shelves_order_and_shape(self):
        data = self.shelves()
        self.assertIn("generated_at", data)
        self.assertEqual([shelf["key"] for shelf in data["shelves"]], ["now", "movie", "tv", "book", "music", "mixed"])
        now, movie, tv, book, music, mixed = data["shelves"]

        self.assertEqual(now["title"], "Now")
        self.assertIsNone(now["media_type"])
        self.assertIsNone(now["see_all"])
        self.assertEqual(now["kind_counts"], {})
        # Live first, then charts changed this week, newest change first.
        self.assertEqual([card["slug"] for card in now["lists"]], ["awards-live", "album-chart", "chart-films"])
        self.assertEqual(now["live_count"], 1)

        self.assertEqual(movie["title"], "Movies")
        self.assertEqual(movie["see_all"], "/api/v1/lists/featured/shelves/?media_type=movie")
        self.assertEqual(
            [card["slug"] for card in movie["lists"]],
            # live, then changed in the last 7 days, then by position
            ["awards-live", "headline-films", "chart-films", "staff-picks", "both-types"],
        )
        self.assertEqual(movie["kind_counts"], {"chart": 1, "award": 1, "canon": 1, "community": 1, "editorial": 1})
        self.assertEqual(movie["count"], 5)
        self.assertEqual([card["slug"] for card in tv["lists"]], ["both-types"])
        self.assertEqual([card["slug"] for card in book["lists"]], ["laureates"])
        self.assertEqual([card["slug"] for card in music["lists"]], ["album-chart"])
        self.assertEqual(mixed["title"], "Across Media")
        self.assertEqual([card["slug"] for card in mixed["lists"]], ["across"])

    def test_card_shape(self):
        movie = self.shelves()["shelves"][1]
        live = movie["lists"][0]
        self.assertEqual(
            set(live),
            {
                "id", "name", "slug", "description", "is_ranked", "items_count", "media_types", "kind", "source",
                "cadence", "period_label", "content_changed_at", "last_synced_at", "live", "preview_items",
            },
        )
        self.assertEqual(live["live"]["status"], "live")
        self.assertEqual(live["live"]["label"], "Ceremony")
        self.assertEqual(live["cadence"], "daily")
        chart = next(card for card in movie["lists"] if card["slug"] == "chart-films")
        self.assertEqual(chart["items_count"], 7)
        self.assertEqual(chart["cadence"], "weekly")
        self.assertEqual(len(chart["preview_items"]), 5)
        self.assertEqual(chart["preview_items"][0]["poster_url"], "https://image.tmdb.org/t/p/w342/cf-1.jpg")
        self.assertEqual(chart["preview_items"][0]["ref"]["media_id"], "cf-1")
        self.assertIsNone(chart["live"])
        manual = next(card for card in movie["lists"] if card["slug"] == "staff-picks")
        self.assertEqual((manual["kind"], manual["cadence"], manual["source"]), ("editorial", "static", {"name": None, "url": None}))

    def test_chart_previews_carry_movement_notes(self):
        reordered = movie_entries("cf", 7)
        reordered[0], reordered[1] = (
            Entry(1, "cf 2", media_type="movie", ids={"tmdb": "cf-2"}, image=reordered[1].image),
            Entry(2, "cf 1", media_type="movie", ids={"tmdb": "cf-1"}, image=reordered[0].image),
        )
        reordered[2] = Entry(3, "new", media_type="movie", ids={"tmdb": "cf-new"}, image="https://image.tmdb.org/t/p/w500/n.jpg")
        chart = next(item for item in self.definitions if item.key == "chart-films")
        with registry.override_definitions(self.definitions):
            self.assertEqual(self.run_list(chart, reordered).status, "ok")
        movie = self.shelves()["shelves"][1]
        card = next(card for card in movie["lists"] if card["slug"] == "chart-films")
        self.assertEqual([item["note"] for item in card["preview_items"]], ["▲1", "▼1", "NEW", None, None])
        canon = next(card for card in movie["lists"] if card["slug"] == "both-types")
        self.assertEqual({item["note"] for item in canon["preview_items"]}, {None})

    def test_media_type_view_groups_by_kind(self):
        data = self.shelves(media_type="movie")
        self.assertEqual(data["media_type"], "movie")
        self.assertEqual(
            [(shelf["key"], shelf["title"]) for shelf in data["shelves"]],
            [("chart", "Charts"), ("award", "Awards"), ("canon", "Critics & Canon"), ("community", "Community"), ("editorial", "Editorial")],
        )
        self.assertEqual(self.shelves(media_type="mixed")["shelves"][0]["lists"][0]["slug"], "across")

    def test_bad_and_hidden_media_types(self):
        self.assertEqual(self.get("/api/v1/lists/featured/shelves/", media_type="podcast").status_code, 400)
        with override_settings(MUSIC_ENABLED=False):
            self.assertEqual(self.get("/api/v1/lists/featured/shelves/", media_type="music").status_code, 404)
            keys = [shelf["key"] for shelf in self.shelves()["shelves"]]
        self.assertNotIn("music", keys)

    def test_query_count_does_not_grow_with_lists(self):
        def count_queries(**params):
            with CaptureQueriesContext(connection) as context:
                self.shelves(**params)
            return len(context)

        landing, by_type = count_queries(), count_queries(media_type="movie")
        extra = [make_definition(key=f"more-{number}", position=120 + number, media_types=("movie", "tv")) for number in range(8)]
        for definition in extra:
            with registry.override_definitions([*self.definitions, definition]):
                self.run_list(definition, movie_entries(definition.key, 6))
        self.definitions = [*self.definitions, *extra]
        with self.assertNumQueries(landing):
            self.shelves()
        with self.assertNumQueries(by_type):
            self.shelves(media_type="movie")
        self.assertLessEqual(max(landing, by_type), QUERY_CEILING)

    def test_people_cards_count_people(self):
        book = self.shelves()["shelves"][3]
        self.assertEqual(book["lists"][0]["items_count"], PersonListItem.objects.count())
        self.assertEqual(book["lists"][0]["preview_items"], [])

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get("/api/v1/lists/featured/shelves/").status_code, 401)


@override_settings(FEATURED_REPORT_TOKEN="report-token-value")
class FeaturedHealthEndpointTests(FeaturedApiTestCase):
    """GET /api/v1/lists/featured/health/ is for staff or the report token only."""

    path = "/api/v1/lists/featured/health/"

    def health(self, client, **headers):
        with registry.override_definitions(self.definitions):
            return client.get(self.path, **headers)

    def test_hidden_from_everyone_else(self):
        self.assertEqual(self.health(APIClient()).status_code, 404)
        self.assertEqual(self.health(self.client).status_code, 404)
        self.assertEqual(self.health(APIClient(), HTTP_X_SPINE_REPORT_TOKEN="wrong").status_code, 404)

    def test_staff_and_token_can_read(self):
        staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        client = APIClient()
        client.force_authenticate(staff)
        response = self.health(client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["totals"]["definitions"], len(self.definitions))
        row = next(row for row in response.data["lists"] if row["key"] == "chart-films")
        self.assertEqual((row["status"], row["resolved_count"], row["entry_count"]), ("ok", 7, 7))
        self.assertEqual(self.health(APIClient(), HTTP_X_SPINE_REPORT_TOKEN="report-token-value").status_code, 200)

    @override_settings(FEATURED_REPORT_TOKEN="")
    def test_empty_setting_disables_the_token(self):
        self.assertEqual(self.health(APIClient(), HTTP_X_SPINE_REPORT_TOKEN="").status_code, 404)


class ManualFeaturedListTests(FeaturedTestCase):
    """Lists featured by hand still appear with derived media types."""

    def test_manual_list_media_types_come_from_items(self):
        viewer = get_user_model().objects.create_user(username="viewer")
        manual = CustomList.objects.create(owner=self.owner, name="Mixed Bag", visibility=CustomList.Visibility.PUBLIC, is_featured=True)
        for media_id, media_type in (("1", MediaTypes.MOVIE.value), ("2", MediaTypes.SEASON.value)):
            item = Item.objects.create(
                source=Sources.TMDB.value,
                media_type=media_type,
                media_id=media_id,
                season_number=1 if media_type == MediaTypes.SEASON.value else None,
                title=media_id,
                image="https://x/1.jpg",
            )
            CustomListItem.objects.create(custom_list=manual, item=item)
        client = APIClient()
        client.force_authenticate(viewer)
        with registry.override_definitions([]):
            data = client.get("/api/v1/lists/featured/shelves/").data
        self.assertEqual([shelf["key"] for shelf in data["shelves"]], ["movie", "tv"])
        self.assertEqual(data["shelves"][0]["lists"][0]["media_types"], ["movie", "tv"])
