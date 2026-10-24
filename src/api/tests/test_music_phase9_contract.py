import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, ItemFilterFacet, MediaTypes, Music, Sources, Status

RELEASE_GROUP_MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"
RELEASE_MBID = "2d0bad69-f735-484b-bc0b-2ea54c76225e"
RECORDING_MBID = "35518724-a25a-4627-a2cc-0786dd1d2272"
FIXED_NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
CONTRACT_PATH = Path(__file__).parents[3] / "docs" / "music" / "contracts" / "phase-9-api-responses.example.json"


def album_metadata():
    """Return the deterministic album provider payload used by the contract."""
    track = {
        "track_mbid": "5e10ea28-e7fe-4eeb-a7aa-f61ee36e822f",
        "disc_number": 1,
        "position": 1,
        "number": "1",
        "title": "HYPERPOWER!",
        "length_ms": 101790,
        "artist_credit": [
            {
                "artist_mbid": "b7ffd2af-418f-4be2-bdd1-22f8b48613da",
                "name": "Nine Inch Nails",
                "join_phrase": "",
            },
        ],
        "recording": {
            "recording_mbid": RECORDING_MBID,
            "title": "HYPERPOWER!",
            "length_ms": 102000,
            "disambiguation": None,
            "first_release_date": "2007-04-13",
            "is_video": False,
            "isrcs": ["USUM70727128"],
        },
    }
    representative = {
        "release_mbid": RELEASE_MBID,
        "title": "Year Zero",
        "status": "Official",
        "date": "2016-09-02",
        "country": "XW",
        "barcode": "00602547582812",
        "selection_basis": "streaming_standard_edition",
        "format": "Digital Media",
        "is_deluxe_or_remastered": False,
        "streaming_links": [
            {
                "service": "music.apple.com",
                "url": "https://music.apple.com/album/year-zero/1440766015",
            },
        ],
        "disc_count": 1,
        "track_count": 1,
        "labels": [],
        "media": [
            {
                "medium_mbid": "b05740f0-cf72-3ea2-aabf-e00c85065525",
                "position": 1,
                "title": None,
                "format": "Digital Media",
                "track_count": 1,
                "tracks": [track],
            },
        ],
    }
    return {
        "media_id": RELEASE_GROUP_MBID,
        "source": Sources.MUSICBRAINZ.value,
        "media_type": MediaTypes.MUSIC.value,
        "source_url": f"https://musicbrainz.org/release-group/{RELEASE_GROUP_MBID}",
        "title": "Year Zero",
        "subtitle": "Nine Inch Nails",
        "image": f"https://coverartarchive.org/release-group/{RELEASE_GROUP_MBID}/front-500",
        "poster_width": 500,
        "poster_height": 500,
        "poster_aspect_ratio": 1.0,
        "release_date": "2007-04-13",
        "max_progress": 1,
        "genres": ["industrial rock"],
        "languages": ["eng"],
        "score": 4.25,
        "score_count": 20,
        "details": {
            "artist": "Nine Inch Nails",
            "artist_credits": [
                {
                    "artist_mbid": "b7ffd2af-418f-4be2-bdd1-22f8b48613da",
                    "name": "Nine Inch Nails",
                    "join_phrase": "",
                },
            ],
            "primary_type": "Album",
            "secondary_types": [],
            "first_release_date": "2007-04-13",
            "release_count": 13,
        },
        "external_links": {
            "MusicBrainz": f"https://musicbrainz.org/release-group/{RELEASE_GROUP_MBID}",
        },
        "music": {
            "release_group_mbid": RELEASE_GROUP_MBID,
            "primary_type": "Album",
            "secondary_types": [],
            "disambiguation": None,
            "annotation": None,
            "first_release_date": "2007-04-13",
            "release_count": 13,
            "artist_credit": [
                {
                    "artist_mbid": "b7ffd2af-418f-4be2-bdd1-22f8b48613da",
                    "name": "Nine Inch Nails",
                    "join_phrase": "",
                },
            ],
            "cover_art": {
                "source": "cover_art_archive",
                "release_group_mbid": RELEASE_GROUP_MBID,
                "fallback_used": False,
            },
            "representative_release": representative,
        },
    }


def recording_metadata():
    """Return the deterministic recording payload used by the contract."""
    return {
        "recording_mbid": RECORDING_MBID,
        "title": "HYPERPOWER!",
        "artist_credit": [
            {
                "artist_mbid": "b7ffd2af-418f-4be2-bdd1-22f8b48613da",
                "name": "Nine Inch Nails",
                "join_phrase": "",
            },
        ],
        "length_ms": 102000,
        "isrcs": ["USUM70727128"],
        "disambiguation": None,
        "first_release_date": "2007-04-13",
        "is_video": False,
        "genres": ["industrial rock"],
        "rating": {"value": 4.5, "votes_count": 8, "max_value": 5},
        "annotation": None,
        "works": [],
        "alternative_recordings": [],
        "source_url": f"https://musicbrainz.org/recording/{RECORDING_MBID}",
        "external_links": {
            "MusicBrainz": f"https://musicbrainz.org/recording/{RECORDING_MBID}",
        },
        "albums": [],
        "releases": [
            {
                "release_mbid": RELEASE_MBID,
                "title": "Year Zero",
                "status": "Official",
                "date": "2016-09-02",
                "country": "XW",
                "barcode": "00602547582812",
                "release_group_mbid": RELEASE_GROUP_MBID,
            },
        ],
    }


class MusicPhase9ContractTests(TransactionTestCase):
    """Lock the rendered iOS music API responses to committed JSON."""

    reset_sequences = True

    def setUp(self):
        enqueue_patcher = patch("app.signals.enrich_external_ratings.delay")
        enqueue_patcher.start()
        self.addCleanup(enqueue_patcher.stop)
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            id=901,
            username="phase9",
            email="phase9@example.com",
            password="strong-password-123",
            display_name="Phase Nine",
        )
        self.item = Item.objects.create(
            id=902,
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id=RELEASE_GROUP_MBID,
            title="Year Zero",
            image=f"https://coverartarchive.org/release-group/{RELEASE_GROUP_MBID}/front-500",
            release_date="2007-04-13",
            release_year=2007,
        )
        ItemFilterFacet.objects.create(
            item=self.item,
            facet_type=ItemFilterFacet.FacetType.GENRE,
            value="industrial rock",
        )
        ItemFilterFacet.objects.create(
            item=self.item,
            facet_type=ItemFilterFacet.FacetType.LANGUAGE,
            value="eng",
        )
        self.client.force_authenticate(self.user)

    @patch("django.utils.timezone.now", return_value=FIXED_NOW)
    @patch("api.services.media.musicbrainz.recording", side_effect=lambda _mbid: recording_metadata())
    @patch("api.services.diary.provider_services.get_media_metadata", side_effect=lambda *_args, **_kwargs: album_metadata())
    @patch("api.services.tracking.provider_services.get_media_metadata", side_effect=lambda *_args, **_kwargs: album_metadata())
    @patch("api.services.media.provider_services.get_media_metadata", side_effect=lambda *_args, **_kwargs: album_metadata())
    @patch(
        "api.services.media.provider_services.search",
        return_value={
            "results": [album_metadata()],
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
        },
    )
    def test_ios_music_contracts_match_frozen_responses(
        self,
        _search_mock,
        _media_metadata_mock,
        _tracking_metadata_mock,
        _diary_metadata_mock,
        _recording_mock,
        _now_mock,
    ):
        ref = {
            "source": Sources.MUSICBRAINZ.value,
            "media_type": MediaTypes.MUSIC.value,
            "media_id": RELEASE_GROUP_MBID,
        }
        detail_url = f"/api/v1/media/musicbrainz/music/{RELEASE_GROUP_MBID}/"
        recording_url = f"{detail_url}recordings/{RECORDING_MBID}/"
        tracking_url = f"/api/v1/tracking/musicbrainz/music/{RELEASE_GROUP_MBID}/"

        responses = {
            "meta": self.client.get("/api/v1/meta/"),
            "music_search": self.client.get(
                "/api/v1/media/search/",
                {"media_type": "music", "q": "year zero"},
            ),
            "album_detail": self.client.get(detail_url),
            "recording_detail": self.client.get(recording_url),
        }

        Music.objects.create(
            id=903,
            user=self.user,
            item=self.item,
            status=Status.PLANNING.value,
        )
        responses["tracking_detail"] = self.client.get(tracking_url)
        responses["tracking_update"] = self.client.patch(
            tracking_url,
            {
                "status": Status.IN_PROGRESS.value,
                "rating": "4.5",
                "start_date": "2026-07-15T12:00:00Z",
                "notes": "Headphones recommended.",
            },
            format="json",
        )
        responses["tracking_action"] = self.client.post(
            f"{tracking_url}actions/consume/",
            {"consumed_at": "2026-07-16T12:00:00Z"},
            format="json",
        )
        responses["diary_create"] = self.client.post(
            "/api/v1/diary/",
            {
                "ref": ref,
                "consumed_at": "2026-07-16T12:00:00Z",
                "rating": "4.5",
                "review_title": "A loud future",
                "review": "Still sounds dangerous.",
                "liked": True,
                "is_rewatch": False,
                "contains_spoilers": False,
                "visibility": "public",
                "tags": ["industrial"],
                "auto_mark_consumed": False,
            },
            format="json",
        )
        diary_id = responses["diary_create"].data["id"]
        responses["diary_detail"] = self.client.get(f"/api/v1/diary/{diary_id}/")
        responses["diary_list"] = self.client.get("/api/v1/diary/", {"media_type": "music"})

        created_list = self.client.post(
            "/api/v1/lists/",
            {"name": "Favorite Albums", "visibility": "public", "is_ranked": True},
            format="json",
        )
        list_id = created_list.data["id"]
        responses["list_create"] = created_list
        responses["list_item_add"] = self.client.post(
            f"/api/v1/lists/{list_id}/items/",
            {"ref": ref},
            format="json",
        )
        responses["lists"] = self.client.get("/api/v1/lists/")
        responses["list_detail"] = self.client.get(f"/api/v1/lists/{list_id}/")

        responses["hall_of_fame"] = self.client.put(
            "/api/v1/me/hof/music/",
            {"ref": ref},
            format="json",
        )
        responses["profile"] = self.client.get("/api/v1/me/")
        responses["preferences"] = self.client.patch(
            "/api/v1/me/preferences/",
            {"quick_watch_date": "current_date"},
            format="json",
        )
        responses["activity"] = self.client.get(f"/api/v1/users/{self.user.username}/activity/")
        responses["statistics"] = self.client.get("/api/v1/stats/me/summary/")
        # Streaming region names come from TMDB; pin them so the contract is offline.
        with patch("streaming.services.region_names", return_value={"US": "United States"}):
            responses["filter_options"] = self.client.get(
                "/api/v1/filter-options/",
                {"scope": "tracking", "media_type": "music"},
            )

        self.assertTrue(
            all(
                response.status_code
                in {status.HTTP_200_OK, status.HTTP_201_CREATED}
                for response in responses.values()
            ),
        )
        actual = {name: response.json() for name, response in responses.items()}
        for key in ["tracking_detail", "tracking_update", "tracking_action"]:
            actual[key]["updated_at"] = "2026-07-16T12:00:00Z"

        self.assertEqual(actual, json.loads(CONTRACT_PATH.read_text()))
