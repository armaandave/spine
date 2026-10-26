"""Apple Music album and song responses keep the shape installed app builds decode.

The rendered responses are kept in docs/music/contracts/apple-*.example.json, which
the Swift contract tests decode. After a deliberate change, rewrite them from src/:

    UPDATE_MUSIC_CONTRACTS=1 uv run --locked python manage.py test \
        api.tests.test_music_apple_contract
"""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from api.serializers.common import (
    custom_backdrop_url_for_user,
    custom_poster_url_for_user,
)
from api.services import media as media_service
from app.curation import curated_image_url, music_picks_apply, picked_image_urls
from app.external_ratings import eligible_rating_sources
from app.models import (
    CustomBackdropPreference,
    CustomPosterPreference,
    ExternalRating,
    Item,
    MediaTypes,
    Music,
    Sources,
    Status,
)
from app.templatetags.app_tags import get_user_poster_image
from app.tests.providers.test_apple_music import AppleMusicTestCase, apple_item, fixture

CONTRACTS = Path(__file__).parents[3] / "docs" / "music" / "contracts"
ALBUM_CONTRACT = CONTRACTS / "apple-album-detail.example.json"
SONG_CONTRACT = CONTRACTS / "apple-song-detail.example.json"
ALBUM_ID = "1657869377"
SONG_ID = "1657869393"
SOS_IMAGE = (
    "https://is1-ssl.mzstatic.com/image/thumb/Music122/v4/bd/3b/a9/"
    "bd3ba9fb-9609-144f-bcfe-ead67b5f6ab3/196589564931.jpg/600x600bb.jpg"
)
MUSICBRAINZ_ID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"

# Keys the app's strict decoders require, present and not null
# (ios/Spine/Spine/Core/Models/MediaModels.swift).
REQUIRED = {
    "ref": ("source", "media_type", "media_id"),
    "summary": ("ref", "title"),
    "music": ("release_group_mbid", "cover_art"),
    "cover_art": ("source", "release_group_mbid", "fallback_used"),
    "release": (
        "release_mbid",
        "title",
        "selection_basis",
        "is_deluxe_or_remastered",
        "disc_count",
        "track_count",
    ),
    "streaming_link": ("service", "url"),
    "medium": ("position", "track_count"),
    "track": ("track_mbid", "disc_number", "position", "number", "title", "recording"),
    "recording": ("recording_mbid", "title", "is_video"),
    "artist_credit": ("name", "join_phrase"),
    "song": ("recording_mbid", "title", "is_video", "parent_album", "context_release"),
    "context_release": ("release_mbid", "track"),
    "appearance": ("release_mbid", "title"),
    "credit": ("name",),
}


class AppleMusicContractTests(AppleMusicTestCase):
    """Lock the Apple album and song responses to committed JSON."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            id=951,
            username="apple-contract",
            email="apple-contract@example.com",
            password="strong-password-123",
            display_name="Apple Contract",
        )
        self.item = apple_item(
            ALBUM_ID,
            id=952,
            title="SOS",
            image=SOS_IMAGE,
            release_date=date(2022, 12, 9),
            release_year=2022,
        )
        Music.objects.create(id=953, user=self.user, item=self.item, status=Status.COMPLETED.value)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def assert_required(self, value, kind, path):
        for key in REQUIRED[kind]:
            self.assertIn(key, value, f"{path}.{key}")
            self.assertIsNotNone(value[key], f"{path}.{key}")

    def assert_track(self, track, path):
        self.assert_required(track, "track", path)
        self.assertIsInstance(track["number"], str, f"{path}.number")
        self.assert_required(track["recording"], "recording", f"{path}.recording")
        for index, credit in enumerate(track.get("artist_credit") or []):
            self.assert_artist_credit(credit, f"{path}.artist_credit[{index}]")

    def assert_artist_credit(self, credit, path):
        self.assert_required(credit, "artist_credit", path)
        # Installed builds open artist_mbid as a MusicBrainz artist page.
        self.assertIsNone(credit.get("artist_mbid"), f"{path}.artist_mbid")

    def assert_album(self, album):
        self.assert_required(album, "summary", "album")
        self.assert_required(album["ref"], "ref", "album.ref")
        self.assertIsInstance(album["ref"]["media_id"], str)
        music = album["music"]
        self.assert_required(music, "music", "music")
        self.assert_required(music["cover_art"], "cover_art", "music.cover_art")
        for index, credit in enumerate(music["artist_credit"]):
            self.assert_artist_credit(credit, f"music.artist_credit[{index}]")
        release = music["representative_release"]
        self.assert_required(release, "release", "release")
        for index, link in enumerate(release["streaming_links"]):
            self.assert_required(link, "streaming_link", f"release.streaming_links[{index}]")
        positions, track_ids = [], []
        for disc, medium in enumerate(release["media"]):
            self.assert_required(medium, "medium", f"release.media[{disc}]")
            positions.append(medium["position"])
            for index, track in enumerate(medium["tracks"]):
                self.assert_track(track, f"release.media[{disc}].tracks[{index}]")
                track_ids.append(track["track_mbid"])
        # They are the app's row IDs.
        self.assertEqual(len(positions), len(set(positions)))
        self.assertEqual(len(track_ids), len(set(track_ids)))

    def assert_song(self, song):
        self.assert_required(song, "song", "song")
        self.assert_required(song["parent_album"], "summary", "song.parent_album")
        self.assert_required(song["parent_album"]["ref"], "ref", "song.parent_album.ref")
        self.assert_required(song["context_release"], "context_release", "song.context_release")
        self.assert_track(song["context_release"]["track"], "song.context_release.track")
        for index, release in enumerate(song["releases"]):
            self.assert_required(release, "appearance", f"song.releases[{index}]")
        for index, credit in enumerate(song["credits"]):
            self.assert_required(credit, "credit", f"song.credits[{index}]")
        self.assertEqual(song["works"], [])
        self.assertEqual(song["alternative_recordings"], [])

    def assert_contract(self, path, actual):
        if os.environ.get("UPDATE_MUSIC_CONTRACTS") == "1":
            path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(actual, json.loads(path.read_text(encoding="utf-8")))

    def test_album_and_song_responses_match_the_contract(self):
        # The song page is built from the album's cached page: one request in all.
        fake = self.apple((200, fixture("page_sos.json")))

        album = self.client.get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/")
        song = self.client.get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/recordings/{SONG_ID}/")

        self.assertEqual((album.status_code, song.status_code), (200, 200))
        self.assertEqual(len(fake.requests), 1)
        album, song = album.json(), song.json()
        self.assert_album(album)
        self.assert_song(song)
        self.assertEqual(album["ref"]["item_id"], self.item.id)
        self.assertEqual(album["user_state"]["status"], Status.COMPLETED.value)
        # Other Versions, More by and You Might Also Like come later; until then
        # the page has no related sections.
        self.assertEqual((album["related"], album["related_sections"]), ({}, []))
        self.assertEqual(song["parent_album"]["ref"], album["ref"])
        self.assertEqual(song["context_release"]["track"]["recording"]["recording_mbid"], SONG_ID)
        self.assert_contract(ALBUM_CONTRACT, album)
        self.assert_contract(SONG_CONTRACT, song)

    def test_a_song_not_on_the_album_is_not_found(self):
        self.apple((200, fixture("page_sos.json")))

        response = self.client.get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/recordings/999/")

        self.assertEqual(response.status_code, 404)

    def test_an_unknown_album_is_not_found_on_the_song_route(self):
        self.apple((404, fixture("error_404.json")["body"]))

        response = self.client.get(f"/api/v1/media/apple_music/music/1/recordings/{SONG_ID}/")

        self.assertEqual(response.status_code, 404)

    def test_a_re_upload_answers_as_its_listing(self):
        self.apple((200, fixture("page_lana_38.json")), (200, fixture("page_lana.json")))

        response = self.client.get("/api/v1/media/apple_music/music/1786643044/")

        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["ref"]["media_id"], "1794644675")
        self.assertEqual(detail["music"]["representative_release"]["track_count"], 42)
        self.assert_album(detail)


class AppleAlbumCoverPickTests(AppleMusicTestCase):
    """Apple Music albums show Apple's cover, whatever was picked."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="viewer", password="strong-password-123")
        curator = get_user_model().objects.create_user(
            username="curator",
            password="strong-password-123",
            is_staff=True,
        )
        self.apple_album = apple_item(ALBUM_ID, image=SOS_IMAGE)
        self.musicbrainz_album = Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id=MUSICBRAINZ_ID,
            title="Year Zero",
            image="https://coverartarchive.org/release-group/x/front-500",
        )
        for item in (self.apple_album, self.musicbrainz_album):
            CustomPosterPreference.objects.create(
                user=self.user,
                item=item,
                custom_image_url="https://example.com/mine.jpg",
                accent_color="#123456",
            )
            CustomPosterPreference.objects.create(
                user=curator,
                item=item,
                custom_image_url="https://example.com/curated.jpg",
            )
            CustomBackdropPreference.objects.create(
                user=self.user,
                item=item,
                custom_image_url="https://example.com/backdrop.jpg",
            )

    def test_picks_apply_to_every_item_but_apple_music_albums(self):
        self.assertFalse(music_picks_apply(self.apple_album))
        self.assertTrue(music_picks_apply(self.musicbrainz_album))
        self.assertTrue(music_picks_apply(None))
        self.assertTrue(
            music_picks_apply(Item(source=Sources.APPLE_MUSIC.value, media_type=MediaTypes.BOOK.value)),
        )

    def test_every_reader_ignores_picks_on_apple_albums(self):
        self.assertIsNone(curated_image_url(CustomPosterPreference, self.apple_album))
        self.assertEqual(
            curated_image_url(CustomPosterPreference, self.musicbrainz_album),
            "https://example.com/curated.jpg",
        )
        self.assertEqual(
            picked_image_urls([self.apple_album, self.musicbrainz_album], CustomPosterPreference, self.user),
            {self.musicbrainz_album.pk: "https://example.com/mine.jpg"},
        )
        for reader in (custom_poster_url_for_user, custom_backdrop_url_for_user):
            with self.subTest(reader=reader.__name__):
                self.assertIsNone(reader(self.user, None, item=self.apple_album))
                self.assertIsNotNone(reader(self.user, None, item=self.musicbrainz_album))
        self.assertIsNone(media_service._viewer_poster(self.user, self.apple_album.pk))
        self.assertIsNotNone(media_service._viewer_poster(self.user, self.musicbrainz_album.pk))
        self.assertIsNone(media_service._backdrop_preference_url(self.user, self.apple_album))
        self.assertEqual(get_user_poster_image(self.apple_album, self.user), SOS_IMAGE)
        self.assertEqual(
            get_user_poster_image(self.musicbrainz_album, self.user),
            "https://example.com/mine.jpg",
        )
        # The picks are kept, for a move back to MusicBrainz.
        self.assertEqual(CustomPosterPreference.objects.filter(item=self.apple_album).count(), 2)

    def test_the_album_page_shows_apples_cover(self):
        self.apple((200, fixture("page_sos.json")))
        client = APIClient()
        client.force_authenticate(self.user)

        detail = client.get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/").json()

        self.assertEqual(detail["image_url"], SOS_IMAGE)
        self.assertIsNone(detail["custom_poster_url"])
        self.assertIsNone(detail["custom_backdrop_url"])
        self.assertNotEqual(detail["poster_accent_color"], "#123456")


class AppleAlbumExternalRatingTests(AppleMusicTestCase):
    """Apple Music albums show no external ratings."""

    def rating(self, item):
        return ExternalRating.objects.create(
            item=item,
            rating_source="musicbrainz",
            value=Decimal("4.2"),
            max_value=Decimal(5),
            vote_count=12,
            status=ExternalRating.Status.AVAILABLE,
            last_attempted_at=timezone.now(),
            last_success_at=timezone.now(),
        )

    def test_apple_albums_have_no_external_ratings(self):
        apple_album = apple_item(ALBUM_ID)
        musicbrainz_album = Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id=MUSICBRAINZ_ID,
            title="Year Zero",
            image="https://example.com/year-zero.jpg",
        )
        # A MusicBrainz rating kept from before an album moved to Apple Music.
        self.rating(apple_album)
        self.rating(musicbrainz_album)

        self.assertEqual(eligible_rating_sources(apple_album), [])
        self.assertEqual(media_service._stored_external_ratings(apple_album, {}), [])
        self.assertEqual(
            [rating["source"] for rating in media_service._stored_external_ratings(musicbrainz_album, {})],
            ["MusicBrainz"],
        )

        self.apple((200, fixture("page_sos.json")))
        detail = APIClient().get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/").json()

        self.assertEqual(detail["external_ratings"], [])
        self.assertEqual(detail["external_ratings_preparation"]["state"], "ready")
        self.assertTrue(ExternalRating.objects.filter(item=apple_album).exists())
