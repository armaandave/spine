from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from app.models import AppleAlbum, Item, MediaTypes, MusicAlbumLink, MusicGenreAlbum, Sources
from app.providers import apple_music, services
from app.tests.providers.test_apple_music import AppleMusicTestCase, apple_item

MUSIC = MediaTypes.MUSIC.value
APPLE = Sources.APPLE_MUSIC.value
MUSICBRAINZ = Sources.MUSICBRAINZ.value
SOS = "1657869377"
SOS_CLEAN = "1658650093"
SOS_GROUP = "1646286a-d0ad-4288-bfab-34b0fb7b22c1"


def genre_album(mbid, title, page_views, genre="r&b"):
    return MusicGenreAlbum.objects.create(
        genre=genre,
        release_group_mbid=mbid,
        title=title,
        artist="SZA",
        release_date="2022-12-09",
        page_views=page_views,
        listeners=1,
        listens=1,
    )


def link(mbid, status, apple_album_id, **fields):
    return MusicAlbumLink.objects.create(
        release_group_mbid=mbid,
        apple_album_id=apple_album_id,
        status=status,
        tier=fields.pop("tier", 3),
        **fields,
    )


class AppleGenrePageTests(AppleMusicTestCase):
    """Genre pages from the genre table and link rows; any request fails the test."""

    def setUp(self):
        super().setUp()
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS)
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        # Most read about first: SOS (through its clean twin's ID), a second
        # MusicBrainz entry for SOS, an album someone logged before it moved, a moved
        # album, and three albums the page leaves out.
        genre_album(SOS_GROUP, "SOS", 100)
        link(SOS_GROUP, "matched", SOS_CLEAN, apple_title="SOS", apple_image="https://example.com/sos.jpg")
        genre_album("sos-duplicate", "SOS", 90)
        link("sos-duplicate", "matched", SOS)
        genre_album("held", "Held", 80)
        link("held", "matched", "111", apple_title="Held")
        Item.objects.create(source=MUSICBRAINZ, media_type=MUSIC, media_id="held", title="Held")
        moved = apple_item("222", title="Moved", image="https://example.com/moved.jpg")
        genre_album("moved", "Moved (MusicBrainz)", 70)
        link("moved", "applied", "222", item=moved)
        genre_album("unlinked", "Unlinked", 60)
        genre_album("review", "In Review", 50)
        link("review", "review", "333")
        genre_album("unmatched", "Unmatched", 40)
        link("unmatched", "unmatched", "444")
        genre_album(SOS_GROUP, "SOS", 100, genre="pop")

    def refs(self, data):
        return [(row["source"], row["media_id"], row["title"]) for row in data["results"]]

    def test_linked_albums_under_their_canonical_refs_from_the_database(self):
        with self.assertNumQueries(6):
            data = services.discover(MUSIC, source=APPLE, genre=" R&B ")

        self.assertEqual(
            self.refs(data),
            [(APPLE, SOS, "SOS"), (MUSICBRAINZ, "held", "Held"), (APPLE, "222", "Moved")],
        )
        self.assertEqual((data["total_results"], data["per_page"]), (3, settings.PER_PAGE))
        sos, held, moved = data["results"]
        self.assertEqual(sos["image"], "https://example.com/sos.jpg")
        self.assertEqual((sos["poster_width"], sos["subtitle"]), (600, "SZA · 2022 · Album"))
        self.assertEqual(held["image"], "https://coverartarchive.org/release-group/held/front-500")
        self.assertEqual(moved["image"], "https://example.com/moved.jpg")

    def test_pages_count_each_album_once(self):
        first = services.discover(MUSIC, source=APPLE, genre="r&b", page_size=2)
        second = services.discover(MUSIC, source=APPLE, genre="r&b", page=2, page_size=2)

        self.assertEqual([row["media_id"] for row in first["results"]], [SOS, "held"])
        self.assertEqual([row["media_id"] for row in second["results"]], ["222"])
        self.assertEqual((first["total_results"], first["total_pages"]), (3, 2))

    def test_a_genre_with_no_linked_albums_is_empty(self):
        self.assertEqual(services.discover(MUSIC, source=APPLE, genre="jazz")["results"], [])

    def test_music_discovery_still_needs_a_genre_and_no_year(self):
        with self.assertRaisesMessage(ValueError, "year discovery is not supported for music."):
            services.discover(MUSIC, source=APPLE, genre="r&b", year=2022)
        with self.assertRaises(ValueError):
            services.discover(MUSIC, source=APPLE, genre=" ")

    @patch("app.providers.services.musicbrainz.discover", return_value={"results": []})
    def test_genre_pages_follow_the_catalog(self, musicbrainz_discover):
        services.discover(MUSIC, source=MUSICBRAINZ, genre="r&b")
        musicbrainz_discover.assert_called_once_with(page=1, page_size=None, genre="r&b")

        with (
            override_settings(MUSIC_CATALOG_SOURCE=APPLE),
            patch.object(apple_music, "discover", return_value={"results": []}) as apple_discover,
        ):
            # An older album's genre chip still sends its MusicBrainz source.
            services.discover(MUSIC, source=MUSICBRAINZ, genre="r&b")
            services.discover(MUSIC, genre="r&b", source=APPLE)
        self.assertEqual(apple_discover.call_count, 2)
        musicbrainz_discover.assert_called_once()

    def test_the_api_serves_the_apple_genre_page(self):
        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_user(username="listener", password="pw-12345678"))

        response = client.get("/api/v1/media/discover/?media_type=music&source=apple_music&genre=r%26b")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(
            [(row["ref"]["source"], row["ref"]["media_id"]) for row in response.data["results"]],
            [(APPLE, SOS), (MUSICBRAINZ, "held"), (APPLE, "222")],
        )
