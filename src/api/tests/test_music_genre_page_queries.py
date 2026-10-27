"""A music genre page reads its Items and the viewer's state in fixed queries.

The same number however many albums the page has, on either catalog: the Apple Music
genre page, and the MusicBrainz one the app opens today.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import media_summary_from_provider
from api.services import media as media_service
from app import music_catalog
from app.models import (
    AppleAlbum,
    CustomBackdropPreference,
    CustomPosterPreference,
    DiaryEntry,
    Item,
    MediaLike,
    MediaTypes,
    MusicAlbumLink,
    MusicGenreAlbum,
    Sources,
    Status,
)
from app.models import Music as MusicTracking
from app.providers import services
from app.tests.providers.test_apple_music import AppleMusicTestCase, apple_item
from lists.models import CustomList, CustomListItem

MUSIC = MediaTypes.MUSIC.value
APPLE = Sources.APPLE_MUSIC.value
MUSICBRAINZ = Sources.MUSICBRAINZ.value


class GenrePageQueryTests(AppleMusicTestCase):
    """Genre pages through the API; any request fails the test."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="listener", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.list = CustomList.objects.create(owner=self.user, name="Albums")
        # A MusicBrainz page with fewer than 100 known albums tops up from a search.
        search = patch("app.providers.musicbrainz._search_genre", return_value=[])
        search.start()
        self.addCleanup(search.stop)

    def genre_album(self, genre, mbid, index):
        MusicGenreAlbum.objects.create(
            genre=genre,
            release_group_mbid=mbid,
            title=f"Album {index}",
            artist="Artist",
            release_date="2001-02-03",
            page_views=1000 - index,
            listeners=1,
            listens=1,
        )

    def logged(self, item, index):
        """Track, log, like and list an album, as the viewer."""
        MusicTracking.objects.create(user=self.user, item=item, status=Status.COMPLETED.value, score=8)
        DiaryEntry.objects.create(user=self.user, item=item, consumed_at=timezone.now())
        MediaLike.objects.create(user=self.user, item=item)
        CustomListItem.objects.create(custom_list=self.list, item=item, position=index)
        return item

    def moved_album(self, genre, index, apple_id):
        """Add a logged album the move took to Apple Music, its listing and its row."""
        item = self.logged(apple_item(apple_id, title=f"Album {index}"), index)
        AppleAlbum.objects.create(apple_album_id=apple_id, canonical_id=apple_id)
        MusicAlbumLink.objects.create(
            release_group_mbid=f"{genre}-{index}",
            apple_album_id=apple_id,
            item=item,
            status=MusicAlbumLink.Status.APPLIED,
            tier=1,
            apple_title=item.title,
            apple_image=item.image,
        )
        self.genre_album(genre, f"{genre}-{index}", index)
        return item

    def musicbrainz_album(self, genre, index):
        """Add a logged album still on MusicBrainz."""
        mbid = f"{genre}-{index}"
        item = Item.objects.create(source=MUSICBRAINZ, media_type=MUSIC, media_id=mbid, title=f"Album {index}")
        self.genre_album(genre, mbid, index)
        return self.logged(item, index)

    def page_queries(self, genre, count):
        """Open a genre page with nothing cached; return its query count."""
        cache.clear()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                "/api/v1/media/discover/",
                {"media_type": MUSIC, "genre": genre, "page_size": 100},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        states = [row["user_state"] for row in response.json()["results"]]
        self.assertEqual(len(states), count)
        for state in states:
            self.assertEqual(
                (state["status"], state["diary_count"], state["has_liked"], state["in_lists"]),
                (Status.COMPLETED.value, 1, True, [self.list.id]),
            )
        return len(captured.captured_queries)

    @override_settings(MUSIC_CATALOG_SOURCE=APPLE)
    def test_the_apple_music_page_reads_no_more_queries_for_more_albums(self):
        def queries(count):
            genre = f"pop{count}"
            for index in range(count):
                self.moved_album(genre, index, f"{count}{index:03d}")
            return self.page_queries(genre, count)

        self.assertEqual(queries(2), queries(6))

    def test_the_musicbrainz_page_reads_no_more_queries_for_more_albums(self):
        def queries(count):
            genre = f"rock{count}"
            for index in range(count):
                self.musicbrainz_album(genre, index)
            return self.page_queries(genre, count)

        self.assertEqual(queries(2), queries(6))

    def test_the_page_is_what_one_album_at_a_time_gives(self):
        """Read together, every card is the card each album gets on its own."""
        curator = get_user_model().objects.create_user(username="curator", password="strong-password-123", is_staff=True)
        moved = self.moved_album("soul", 0, "111")
        DiaryEntry.objects.create(
            user=self.user,
            item=moved,
            consumed_at=timezone.now() + timedelta(days=1),
            rating=Decimal("4.5"),
        )
        # A logged MusicBrainz album, with admin picks and the viewer's own poster.
        held = self.musicbrainz_album("soul", 1)
        MusicAlbumLink.objects.create(
            release_group_mbid=held.media_id,
            apple_album_id="222",
            status=MusicAlbumLink.Status.MATCHED,
            tier=3,
        )
        CustomPosterPreference.objects.create(user=curator, item=held, custom_image_url="https://example.com/curated.jpg")
        CustomBackdropPreference.objects.create(user=curator, item=held, custom_image_url="https://example.com/backdrop.jpg")
        CustomPosterPreference.objects.create(user=self.user, item=held, custom_image_url="https://example.com/mine.jpg")
        # A matched album nobody has added.
        MusicAlbumLink.objects.create(
            release_group_mbid="soul-2",
            apple_album_id="333",
            status=MusicAlbumLink.Status.MATCHED,
            tier=1,
            apple_title="Album 2",
            apple_image="https://example.com/333.jpg",
        )
        self.genre_album("soul", "soul-2", 2)

        for catalog, third in ((APPLE, (APPLE, "333")), (MUSICBRAINZ, (MUSICBRAINZ, "soul-2"))):
            with self.subTest(catalog=catalog), override_settings(MUSIC_CATALOG_SOURCE=catalog):
                cache.clear()
                source = media_service.default_source_for(MUSIC)
                page = media_service.discover_media(media_type=MUSIC, genre="soul", user=self.user)["results"]
                rows = music_catalog.canonical_music_payloads(
                    services.discover(MUSIC, source=source, genre="soul")["results"],
                    source=source,
                    media_type=MUSIC,
                )

                self.assertEqual(
                    page,
                    [media_summary_from_provider(row, MUSIC, row["source"], user=self.user) for row in rows],
                )
                self.assertEqual(
                    [(row["ref"]["source"], row["ref"]["media_id"], row["ref"]["item_id"]) for row in page],
                    [(APPLE, "111", moved.id), (MUSICBRAINZ, held.media_id, held.id), (*third, None)],
                )
                self.assertEqual(page[0]["user_state"]["diary_count"], 2)
                self.assertEqual(
                    (page[1]["poster_url"], page[1]["backdrop_url"], page[1]["custom_poster_url"]),
                    ("https://example.com/curated.jpg", "https://example.com/backdrop.jpg", "https://example.com/mine.jpg"),
                )
                self.assertIsNone(page[2]["user_state"])
