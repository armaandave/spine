from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from api.views import featured as featured_views
from api.views.featured import Featured, list_payloads
from api.views.lists import list_payload
from app.models import Item, MediaTypes, Sources
from lists.featured import order, registry
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
        self.curate(shelves={}, now=["chart-films", "album-chart"])
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

    def curate(self, *, shelves=None, now=None):
        """Replace the curated order (``lists.featured.order``) for this test."""
        if shelves is not None:
            self.enterContext(patch.object(order, "SHELF_ORDER", shelves))
        if now is not None:
            self.enterContext(patch.object(order, "NOW_PRIORITY", now))

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

    def test_lists_whose_definition_is_gone_are_hidden(self):
        # The registry dropped a list the engine synced (a past live ceremony): it is
        # not "manually featured", even before the dispatcher unfeatures it.
        self.definitions = [item for item in self.definitions if item.key not in ("headline-films", "chart-films")]
        legacy = self.get("/api/v1/lists/featured/", view="all")
        self.assertEqual([row["name"] for row in legacy.data["results"]][:2], ["Staff Picks", "Both Types"])
        names = [row["name"] for row in legacy.data["results"]]
        self.assertNotIn("Headline Films", names)
        self.assertNotIn("Chart Films", names)
        self.assertEqual(
            [row["name"] for row in self.get("/api/v1/lists/featured/").data["results"]],
            ["Staff Picks"],
        )
        slugs = {
            card["slug"]
            for shelf in self.get("/api/v1/lists/featured/shelves/").data["shelves"]
            for card in shelf["lists"]
        }
        self.assertNotIn("headline-films", slugs)
        self.assertNotIn("chart-films", slugs)
        self.assertIn("staff-picks", slugs)
        chart = CustomList.objects.get(import_source_id="chart-films")
        self.assertIsNone(self.get(f"/api/v1/lists/{chart.id}/").data.get("featured"))

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
        # Live first, then charts changed this week, in NOW_PRIORITY order.
        self.assertEqual([card["slug"] for card in now["lists"]], ["awards-live", "chart-films", "album-chart"])
        self.assertEqual(now["live_count"], 1)

        self.assertEqual(movie["title"], "Movies")
        self.assertEqual(movie["see_all"], "/api/v1/lists/featured/shelves/?media_type=movie")
        self.assertEqual(
            [card["slug"] for card in movie["lists"]],
            # live, then (nothing curated here) by position
            ["awards-live", "headline-films", "staff-picks", "chart-films", "both-types"],
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

    def test_people_cards_preview_up_to_five_people(self):
        laureates = CustomList.objects.get(import_source_id="laureates")
        PersonListItem.objects.bulk_create([
            PersonListItem(
                custom_list=laureates,
                source=PersonListItem.Source.OPENLIBRARY,
                person_id=f"OL{number}A",
                name=f"Laureate {number}",
                profile_url=f"https://p/{number}.jpg",
                position=number,
            )
            for number in range(2, 9)
        ])
        drf_request = Request(APIRequestFactory().get("/api/v1/lists/featured/"))
        drf_request.user = self.viewer
        expected = list_payload(laureates, request=drf_request, include_preview_items=True)["preview_people"][:5]

        card = self.shelves()["shelves"][3]["lists"][0]
        self.assertEqual(card["items_count"], 8)
        self.assertEqual(card["preview_items"], [])
        self.assertEqual(card["preview_people"], expected)
        self.assertEqual([person["name"] for person in card["preview_people"]][:2], ["Annie Ernaux", "Laureate 2"])
        self.assertEqual(self.shelves(media_type="book")["shelves"][0]["lists"][0]["preview_people"], expected)
        movie = self.shelves()["shelves"][1]
        self.assertTrue(all("preview_people" not in media_card for media_card in movie["lists"]))
        detail = self.get(f"/api/v1/lists/{laureates.id}/").data
        self.assertNotIn("preview_people", detail["featured"])

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get("/api/v1/lists/featured/shelves/").status_code, 401)


class CuratedOrderTests(FeaturedApiTestCase):
    """Shelves and "Now" follow ``lists.featured.order``."""

    def setUp(self):
        super().setUp()
        extra = [
            make_definition(key="chart-b", name="Chart B", position=102),
            make_definition(key="chart-c", name="Chart C", position=103),
            make_definition(key="duo", name="Duo", position=104, kind="canon", media_types=("movie", "tv")),
        ]
        self.definitions = [*self.definitions, *extra]
        with registry.override_definitions(self.definitions):
            for definition in extra:
                self.assertTrue(self.run_list(definition, movie_entries(definition.key, 2)).ok)
        twin = CustomList.objects.create(
            owner=self.owner,
            name="Alpha Picks",
            visibility=CustomList.Visibility.PUBLIC,
            is_featured=True,
            featured_position=40,
        )
        CustomListItem.objects.create(custom_list=twin, item=Item.objects.get(media_id="hf-1"), position=1)
        self.curate(
            shelves={
                "movie": ["chart-c", "no-such-list", "chart-films", "awards-live"],
                "tv": ["both-types", "duo"],
            },
        )

    def slugs(self, shelf):
        return [card["slug"] for card in shelf["lists"]]

    def landing(self):
        shelves = self.get("/api/v1/lists/featured/shelves/").data["shelves"]
        return {shelf["key"]: shelf for shelf in shelves}

    def test_live_then_curated_then_position_and_name(self):
        shelves = self.landing()
        self.assertEqual(
            self.slugs(shelves["movie"]),
            # live (even though curated last), the curated keys in order (unknown keys
            # ignored), then by position, then name: Alpha and Staff share position 40.
            [
                "awards-live", "chart-c", "chart-films", "headline-films", "alpha-picks", "staff-picks",
                "chart-b", "duo", "both-types",
            ],
        )

    def test_two_type_lists_follow_each_shelfs_order(self):
        shelves = self.landing()
        movie = self.slugs(shelves["movie"])
        self.assertLess(movie.index("duo"), movie.index("both-types"))
        self.assertEqual(self.slugs(shelves["tv"]), ["both-types", "duo"])

    def test_media_type_view_orders_each_kind_the_same_way(self):
        data = self.get("/api/v1/lists/featured/shelves/", media_type="movie").data
        groups = {shelf["key"]: self.slugs(shelf) for shelf in data["shelves"]}
        self.assertEqual(groups["chart"], ["chart-c", "chart-films", "chart-b"])
        self.assertEqual(groups["canon"], ["duo", "both-types"])
        self.assertEqual(groups["editorial"], ["alpha-picks", "staff-picks"])
        tv = self.get("/api/v1/lists/featured/shelves/", media_type="tv").data
        self.assertEqual(self.slugs(tv["shelves"][0]), ["both-types", "duo"])

    def test_now_shows_only_recent_priority_charts_in_order(self):
        self.curate(now=["album-chart", "chart-c", "both-types", "chart-films"])
        FeaturedListSync.objects.filter(key="chart-films").update(content_changed_at=timezone.now() - timedelta(days=8))
        now = self.landing()["now"]
        # chart-b changed this week but isn't a priority chart; chart-films is stale;
        # both-types is a priority key that didn't change this week.
        self.assertEqual(self.slugs(now), ["awards-live", "album-chart", "chart-c"])
        self.assertEqual(now["count"], 3)


def now_card(key, media_types=("movie",), *, live_status=None, position=150, recent=True):
    """Return a ``Featured`` for "Now" tests, without the database."""
    definition = make_definition(key=key, media_types=media_types, position=position)
    custom_list = CustomList(id=position, name=key, featured_position=position)
    return Featured(
        custom_list=custom_list,
        definition=definition,
        sync=None,
        media_types=media_types,
        kind="chart",
        live_status=live_status,
        live_window=None,
        recent=recent,
    )


class NowStripTests(SimpleTestCase):
    """The "Now" caps: two charts per media type, ten cards in all."""

    def test_caps_charts_per_media_type_and_in_total(self):
        types = ("movie", "tv", "book", "game", "music", "anime")
        charts = [now_card(f"{media_type}-{number}", (media_type,), position=200 + index * 3 + number)
                  for index, media_type in enumerate(types) for number in range(3)]
        live = [now_card("ceremony", ("book",), live_status="live", position=1),
                now_card("tomorrow", ("movie",), live_status="upcoming", position=2)]
        priority = [card.definition.key for card in charts]
        with patch.object(order, "NOW_PRIORITY", priority):
            chosen = [card.definition.key for card in featured_views._now_cards([*charts, *live])]
        self.assertEqual(len(chosen), featured_views.NOW_LIMIT)
        self.assertEqual(
            chosen,
            # Live lists don't use up their type's two chart slots.
            ["ceremony", "tomorrow", "movie-0", "movie-1", "tv-0", "tv-1", "book-0", "book-1", "game-0", "game-1"],
        )

    def test_two_type_charts_count_against_both_types(self):
        charts = [now_card("duo", ("movie", "tv")), now_card("movie-a"), now_card("tv-a", ("tv",)), now_card("tv-b", ("tv",))]
        with patch.object(order, "NOW_PRIORITY", ["duo", "movie-a", "tv-a", "tv-b"]):
            chosen = [card.definition.key for card in featured_views._now_cards(charts)]
        self.assertEqual(chosen, ["duo", "movie-a", "tv-a"])


class FeaturedItemNotesTests(FeaturedApiTestCase):
    """Featured lists' items carry their entry's note; other lists' items don't."""

    def rerun(self, key, entries):
        definition = next(item for item in self.definitions if item.key == key)
        with registry.override_definitions(self.definitions):
            result = self.run_list(definition, entries)
        self.assertTrue(result.ok, result.message)
        return result.custom_list

    def awards(self):
        entries = movie_entries("al", 2)
        return self.rerun("awards-live", [
            Entry(1, entries[0].title, media_type="movie", ids=entries[0].ids, image=entries[0].image, note="Winner · Best Picture"),
            entries[1],
        ])

    def test_list_items_carry_notes(self):
        custom_list = self.awards()
        results = self.get(f"/api/v1/lists/{custom_list.id}/items/", sort="position").data["results"]
        self.assertEqual([item["note"] for item in results], ["Winner · Best Picture", None])
        detail = self.get(f"/api/v1/lists/{custom_list.id}/").data
        self.assertEqual([item["note"] for item in detail["items"]], ["Winner · Best Picture", None])

    def test_other_lists_items_have_no_note_key(self):
        own = CustomList.objects.create(owner=self.viewer, name="Mine")
        CustomListItem.objects.create(custom_list=own, item=Item.objects.get(media_id="hf-1"), position=1)
        for custom_list in (own, self.manual):
            self.assertNotIn("note", self.get(f"/api/v1/lists/{custom_list.id}/items/").data["results"][0])
            self.assertNotIn("note", self.get(f"/api/v1/lists/{custom_list.id}/").data["items"][0])

    def test_chart_movement_combines_with_the_entry_note(self):
        entries = movie_entries("cf", 7)
        swapped = [
            Entry(1, "cf 2", media_type="movie", ids={"tmdb": "cf-2"}, image=entries[1].image, note="$12.4M"),
            Entry(2, "cf 1", media_type="movie", ids={"tmdb": "cf-1"}, image=entries[0].image, note="New this week"),
            *entries[2:],
        ]
        custom_list = self.rerun("chart-films", swapped)
        results = self.get(f"/api/v1/lists/{custom_list.id}/items/", sort="position").data["results"]
        self.assertEqual([item["note"] for item in results[:3]], ["▲1 · $12.4M", "New this week", None])
        # Card previews keep the bare movement.
        shelves = self.get("/api/v1/lists/featured/shelves/").data["shelves"]
        movie = next(shelf for shelf in shelves if shelf["key"] == "movie")
        card = next(card for card in movie["lists"] if card["slug"] == "chart-films")
        self.assertEqual([item["note"] for item in card["preview_items"][:3]], ["▲1", "▼1", None])

    def test_people_list_entries_carry_notes(self):
        custom_list = self.rerun("laureates", [Entry(1, "Annie Ernaux", ids={"openlibrary_person": "OL1A"}, image="https://p/1.jpg", note="2022")])
        self.assertEqual(self.get(f"/api/v1/lists/{custom_list.id}/people/").data["results"][0]["note"], "2022")
        self.assertEqual(self.get(f"/api/v1/lists/{custom_list.id}/").data["people"][0]["note"], "2022")


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
