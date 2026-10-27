"""Apple genre chips and charts need no MusicBrainz genre rows or matches."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from app import apple_genres
from app.models import DiaryEntry, ItemFilterFacet, Music, MusicAlbumLink
from app.providers import apple_music, services
from app.tests.providers.test_apple_music import AppleMusicTestCase, apple_item, fixture, genre_facets, query

SOS = "1657869377"
LANA = "1785540920"


def chart(*albums, next_path=None):
    result = {"data": list(albums)}
    if next_path:
        result["next"] = next_path
    return {"results": {"albums": [result]}}


def sos():
    return deepcopy(fixture("page_sos.json")["data"][0])


class AppleGenrePageTests(AppleMusicTestCase):
    def answers(self, *albums):
        return self.apple(
            (200, fixture("chart_genres.json")),
            (200, chart(*albums)),
            (200, {"data": list(albums)}),
        )

    def test_taxonomy_resolves_root_names_and_has_no_broken_parents(self):
        for name, expected in (("Classical", "5"), ("Electronic", "7"), ("Soundtrack", "16"), ("Singer/Songwriter", "10")):
            self.assertEqual(apple_genres.genre_id(name), expected)
        for genre in apple_genres.GENRES:
            self.assertTrue(genre["parent"] is None or genre["parent"] in apple_genres.BY_ID)

    def test_native_genre_chart_with_no_musicbrainz_tables(self):
        fake = self.answers(sos())
        with patch.object(services.musicbrainz_session, "send", side_effect=AssertionError("MusicBrainz requested")):
            data = apple_music.discover(genre="R&B/Soul")
        self.assertEqual([row["media_id"] for row in data["results"]], [SOS])
        self.assertEqual(data["results"][0]["source"], "apple_music")
        self.assertEqual(query(fake.requests[1])["genre"], "15")
        self.assertEqual(data["total_results"], 1)
        self.assertEqual(data["total_pages"], 1)
        self.assertFalse(MusicAlbumLink.objects.exists())

    def test_legacy_names_and_subgenres_use_supported_parent_charts(self):
        for name, expected in (("hip hop", "18"), ("West Coast Rap", "18"), ("R&B/Soul", "15"), ("r&b", "15")):
            with self.subTest(name=name):
                cache.clear()
                fake = self.answers(sos())
                apple_music.discover(genre=name)
                self.assertEqual(query(fake.requests[1])["genre"], expected)

    def test_clean_twins_fold_before_pagination(self):
        explicit = sos()
        clean = deepcopy(explicit)
        clean["id"] = "1658650093"
        clean["attributes"].update(url="https://music.apple.com/us/album/sos/1658650093", contentRating="clean")
        explicit_copy = deepcopy(explicit)
        explicit["views"] = {"other-versions": {"data": [deepcopy(clean)]}}
        clean["views"] = {"other-versions": {"data": [explicit_copy]}}
        other = deepcopy(sos())
        other["id"] = "999"
        other["attributes"].update(name="Different Album", url="https://music.apple.com/us/album/different/999")
        other["views"] = {}
        self.answers(explicit, clean, other)
        first = apple_music.discover(genre="R&B/Soul", page_size=1)
        second = apple_music.discover(genre="R&B/Soul", page_size=1, page=2)
        third = apple_music.discover(genre="R&B/Soul", page_size=1, page=3)
        self.assertEqual([r["media_id"] for r in first["results"]], [SOS])
        self.assertEqual([r["media_id"] for r in second["results"]], ["999"])
        self.assertEqual((first["total_results"], first["total_pages"], third["results"]), (2, 2, []))

    def test_short_chart_pages_follow_next_and_repeated_next_is_bounded(self):
        path = "/v1/catalog/us/charts?types=albums&genre=15&offset=1"
        album = sos()
        fake = self.apple(
            (200, fixture("chart_genres.json")),
            (200, chart(album, next_path=path)),
            (200, {"data": [album], "next": path}),
            (200, {"data": [album]}),
        )
        data = apple_music.discover(genre="R&B/Soul")
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(len(fake.requests), 4)

    def test_failure_is_not_cached_as_an_empty_genre(self):
        self.apple((200, fixture("chart_genres.json")), (500, {"errors": []}))
        with self.assertRaises(services.ProviderAPIError):
            apple_music.discover(genre="R&B/Soul")
        from app.tests.providers.test_apple_music import clear_pause  # noqa: PLC0415
        clear_pause()
        self.apple((200, chart(sos())), (200, {"data": [sos()]}))
        self.assertEqual(len(apple_music.discover(genre="R&B/Soul")["results"]), 1)

    def test_unknown_genres_are_empty_without_network_or_musicbrainz_fallback(self):
        self.assertEqual(apple_music.discover(genre="made up genre")["results"], [])

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_old_musicbrainz_chips_follow_apple_and_empty_charts_stay_apple(self):
        fake = self.apple((200, fixture("chart_genres.json")), (200, chart()))
        with patch.object(services.musicbrainz_session, "send", side_effect=AssertionError("MusicBrainz requested")):
            data = services.discover("music", source="musicbrainz", genre="hip hop")
        self.assertEqual(data["results"], [])
        self.assertEqual(query(fake.requests[1])["genre"], "18")

    @override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
    @patch("app.providers.musicbrainz.discover", return_value={"results": []})
    def test_explicit_musicbrainz_fallback_still_works(self, discover):
        services.discover("music", source="musicbrainz", genre="jazz")
        discover.assert_called_once_with(page=1, page_size=None, genre="jazz")

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_installed_app_gets_apple_cards_and_its_saved_state(self):
        self.answers(sos())
        user = get_user_model().objects.create_user(username="listener", password="pw-12345678")
        item = apple_item(SOS)
        Music.objects.create(item=item, user=user, status="Completed", score=8)
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/v1/media/discover/", {"media_type": "music", "source": "musicbrainz", "genre": "r&b"})
        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertEqual(row["ref"]["source"], "apple_music")
        self.assertEqual(row["ref"]["item_id"], item.pk)
        self.assertEqual(row["user_state"]["status"], "Completed")

    def test_music_discovery_validation_remains_compatible(self):
        with self.assertRaises(ValueError):
            services.discover("music", source="apple_music", genre=" ")
        with self.assertRaisesMessage(ValueError, "year discovery is not supported for music."):
            services.discover("music", source="apple_music", genre="pop", year=2022)

    def test_stored_genres_refresh_preserves_tracking_logs_dates_and_item(self):
        from datetime import date  # noqa: PLC0415

        from django.utils import timezone  # noqa: PLC0415

        user = get_user_model().objects.create_user(username="listener", password="pw-12345678")
        item = apple_item(SOS, release_date=date(2022, 12, 9))
        genre_facets(item, "contemporary r&b", "neo soul")
        tracking = Music.objects.create(user=user, item=item, status="Completed", score=8)
        entry = DiaryEntry.objects.create(user=user, item=item, consumed_at=timezone.now(), rating=4)
        self.assertEqual(apple_genres.refresh_stored_genres()["Apple albums"], 1)
        album = sos()
        deluxe = deepcopy(album)
        deluxe["id"] = "999"
        deluxe["attributes"].update(name="SOS Deluxe", genreNames=["Pop"], trackCount=99, url="https://music.apple.com/us/album/deluxe/999")
        deluxe["views"] = {}
        album.setdefault("views", {}).setdefault("other-versions", {}).setdefault("data", []).append(deluxe)
        self.apple((200, {"data": [album]}), background=True)
        result = apple_genres.refresh_stored_genres(apply=True)
        self.assertEqual(result["updated"], 1)
        item.refresh_from_db()
        tracking.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(item.release_date, date(2022, 12, 9))
        self.assertEqual((tracking.item_id, entry.item_id, entry.rating), (item.pk, item.pk, 4))
        self.assertEqual(list(item.filter_facets.filter(facet_type=ItemFilterFacet.FacetType.GENRE).values_list("value", flat=True)), ["R&B/Soul"])
