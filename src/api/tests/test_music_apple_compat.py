"""An old MusicBrainz ref of an album that moved to Apple Music reaches the same Item.

Installed builds, recent searches, people lists and old web links keep MusicBrainz
refs after an album moves. Every entry point serves them under the album's
canonical ref, in place: a 200, never a redirect, for the API.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.services import media as media_service
from app import helpers
from app.models import (
    AppleAlbum,
    DiaryEntry,
    ExternalRating,
    Item,
    MediaLike,
    MediaTypes,
    Music,
    MusicAlbumLink,
    Sources,
    Status,
)
from app.tests.providers.test_apple_music import AppleMusicTestCase, apple_item, fixture
from integrations.imports import yamtrack
from lists.models import CustomList, CustomListItem

SOS = "1657869377"
SOS_CLEAN = "1658650093"
SOS_SONG = "1657869393"
SOS_IMAGE = (
    "https://is1-ssl.mzstatic.com/image/thumb/Music122/v4/bd/3b/a9/"
    "bd3ba9fb-9609-144f-bcfe-ead67b5f6ab3/196589564931.jpg/600x600bb.jpg"
)
# The MusicBrainz release group the album was stored under before it moved.
SOS_MBID = "1a5c4d2e-3b6f-4a7d-9e8c-0f1b2a3c4d5e"
OTHER_MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"
MUSICBRAINZ_REF = {"source": "musicbrainz", "media_type": "music", "media_id": SOS_MBID}
MUSICBRAINZ_RESULT = {
    "media_id": SOS_MBID,
    "source": "musicbrainz",
    "media_type": "music",
    "title": "SOS",
    "image": "https://coverartarchive.org/release-group/sos/front-500",
}
YAMTRACK_ROW = (
    '"media_id","source","media_type","title","image","season_number",'
    '"episode_number","score","progress","status","start_date","end_date",'
    '"notes","progressed_at"\n'
    f'"{SOS_MBID}","musicbrainz","music","SOS (MusicBrainz)",'
    '"https://coverartarchive.org/release-group/sos/front-500","","","9","0",'
    '"Completed","","2025-04-05","","2025-04-05T12:00:00Z"\n'
)


class MovedAlbumTestCase(AppleMusicTestCase):
    """SOS, which moved from its MusicBrainz release group to Apple Music."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="listener", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        # Moved in place: the Item kept its pk and took the Apple Music ref.
        self.item = apple_item(SOS, image=SOS_IMAGE, release_date=date(2022, 12, 9), release_year=2022)
        MusicAlbumLink.objects.create(
            release_group_mbid=SOS_MBID,
            apple_album_id=SOS,
            item=self.item,
            status=MusicAlbumLink.Status.APPLIED,
            method=MusicAlbumLink.Method.UPC_EXACT,
            tier=1,
            applied_at=timezone.now(),
        )

    def assert_canonical_ref(self, ref):
        self.assertEqual(
            ref,
            {
                "item_id": self.item.id,
                "source": Sources.APPLE_MUSIC.value,
                "media_type": MediaTypes.MUSIC.value,
                "media_id": SOS,
                "season_number": None,
                "episode_number": None,
            },
        )

    def assert_one_album(self):
        self.assertEqual(list(Item.objects.filter(media_type=MediaTypes.MUSIC.value)), [self.item])
        self.item.refresh_from_db()
        self.assertEqual((self.item.source, self.item.media_id, self.item.image), ("apple_music", SOS, SOS_IMAGE))


class MovedAlbumAPITests(MovedAlbumTestCase):
    """Reads and writes with the old ref, as installed builds send them."""

    def test_every_api_entry_point_lands_on_the_moved_item(self):
        # One Apple request in all, and none to MusicBrainz: the network is closed.
        fake = self.apple((200, fixture("page_sos.json")))
        custom_list = CustomList.objects.create(owner=self.user, name="Favourites")
        url = f"/api/v1/media/musicbrainz/music/{SOS_MBID}"

        def detail_ref(response):
            self.assert_canonical_ref(response.json()["ref"])

        flows = [
            ("detail", lambda: self.client.get(f"{url}/"), detail_ref),
            ("basic", lambda: self.client.get(f"{url}/basic/"), detail_ref),
            ("enrichment", lambda: self.client.get(f"{url}/enrichment/"), detail_ref),
            (
                "tracking",
                lambda: self.client.put(
                    f"/api/v1/tracking/musicbrainz/music/{SOS_MBID}/",
                    {"status": Status.PLANNING.value},
                    format="json",
                ),
                lambda _: self.assertTrue(Music.objects.filter(user=self.user, item=self.item).exists()),
            ),
            (
                "tracking read",
                lambda: self.client.get(f"/api/v1/tracking/musicbrainz/music/{SOS_MBID}/"),
                lambda response: self.assertEqual(response.json()["status"], Status.PLANNING.value),
            ),
            (
                "diary",
                lambda: self.client.post(
                    "/api/v1/diary/",
                    {
                        "ref": MUSICBRAINZ_REF,
                        "consumed_at": "2026-10-20T12:00:00Z",
                        "rating": "4.5",
                        "review": "Still great.",
                    },
                    format="json",
                ),
                lambda _: self.assertTrue(DiaryEntry.objects.filter(user=self.user, item=self.item).exists()),
            ),
            (
                "review",
                lambda: self.client.get(f"{url}/reviews/"),
                lambda response: self.assertEqual(
                    [entry["media"]["ref"]["item_id"] for entry in response.json()["results"]],
                    [self.item.id],
                ),
            ),
            (
                "community",
                lambda: self.client.get(f"{url}/community/"),
                lambda response: self.assertEqual(
                    (response.json()["diary_count"], response.json()["review_count"]),
                    (1, 1),
                ),
            ),
            (
                "list add",
                lambda: self.client.post(
                    f"/api/v1/lists/{custom_list.id}/items/",
                    {"ref": MUSICBRAINZ_REF},
                    format="json",
                ),
                lambda _: self.assertTrue(
                    CustomListItem.objects.filter(custom_list=custom_list, item=self.item).exists(),
                ),
            ),
            (
                "like",
                lambda: self.client.post("/api/v1/me/liked-media/", {"ref": MUSICBRAINZ_REF}, format="json"),
                lambda _: self.assertTrue(MediaLike.objects.filter(user=self.user, item=self.item).exists()),
            ),
            (
                "hall of fame",
                lambda: self.client.put("/api/v1/me/hof/music/", {"ref": MUSICBRAINZ_REF}, format="json"),
                lambda _: self.assertEqual(
                    get_user_model().objects.get(pk=self.user.pk).hof_music_id,
                    self.item.id,
                ),
            ),
        ]
        for name, request, check in flows:
            with self.subTest(name):
                response = request()
                self.assertIn(response.status_code, (200, 201), response.content)
                check(response)

        self.assert_one_album()
        self.assertEqual(Music.objects.filter(user=self.user).count(), 1)
        self.assertEqual(len(fake.requests), 1)

    def test_yamtrack_import_lands_on_the_moved_item(self):
        for mode, existing in (("new", None), ("new", Status.PLANNING.value), ("overwrite", Status.PLANNING.value)):
            with self.subTest(mode=mode, existing=existing):
                Music.objects.filter(user=self.user).delete()
                if existing:
                    Music.objects.create(user=self.user, item=self.item, status=existing)

                counts, warnings = yamtrack.importer(BytesIO(YAMTRACK_ROW.encode()), self.user, mode)

                self.assertEqual(warnings, "")
                music = Music.objects.get(user=self.user)
                self.assertEqual(music.item, self.item)
                # "new" skips an album the person already has; otherwise the file's
                # state lands on the moved album.
                skipped = mode == "new" and existing
                self.assertEqual(counts.get(MediaTypes.MUSIC.value, 0), 0 if skipped else 1)
                self.assertEqual(music.status, existing if skipped else Status.COMPLETED.value)
                # The moved album keeps its own title and cover, not the file's.
                self.assertEqual(Item.objects.get(pk=self.item.pk).title, "SOS")
                self.assert_one_album()

    def test_an_apple_alias_tracks_the_same_album(self):
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        Music.objects.create(user=self.user, item=self.item, status=Status.PLANNING.value)

        response = self.client.put(
            f"/api/v1/tracking/apple_music/music/{SOS_CLEAN}/",
            {"status": Status.COMPLETED.value},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Music.objects.get(user=self.user).status, Status.COMPLETED.value)
        self.assert_one_album()

    def test_song_paths(self):
        self.apple((200, fixture("page_sos.json")))
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)

        for album_id in (SOS, SOS_CLEAN):
            with self.subTest(album_id=album_id):
                response = self.client.get(f"/api/v1/media/apple_music/music/{album_id}/recordings/{SOS_SONG}/")
                self.assertEqual(response.status_code, 200)
                self.assert_canonical_ref(response.json()["parent_album"]["ref"])
        # A MusicBrainz song of the album, from a page opened before it moved, isn't
        # on the Apple album.
        response = self.client.get(f"/api/v1/media/musicbrainz/music/{SOS_MBID}/recordings/{OTHER_MBID}/")
        self.assertEqual(response.status_code, 404)

    def test_old_builds_get_no_musicbrainz_artist_ids_or_ratings(self):
        self.apple((200, fixture("page_sos.json")))
        ExternalRating.objects.create(
            item=self.item,
            rating_source="musicbrainz",
            value=Decimal("4.2"),
            max_value=Decimal(5),
            vote_count=12,
            status=ExternalRating.Status.AVAILABLE,
            last_attempted_at=timezone.now(),
            last_success_at=timezone.now(),
        )

        detail = self.client.get(f"/api/v1/media/musicbrainz/music/{SOS_MBID}/basic/").json()

        self.assertEqual({credit["artist_mbid"] for credit in detail["music"]["artist_credit"]}, {None})
        self.assertEqual(detail["external_ratings"], [])


class MovedAlbumListTests(MovedAlbumTestCase):
    """MusicBrainz lists show the moved album as logged, under its canonical ref."""

    def setUp(self):
        super().setUp()
        Music.objects.create(user=self.user, item=self.item, status=Status.COMPLETED.value)
        self.apple()

    def assert_logged(self, summary):
        self.assert_canonical_ref(summary["ref"])
        self.assertEqual(summary["user_state"]["status"], Status.COMPLETED.value)

    def test_musicbrainz_search_and_genre_pages(self):
        page = {"page": 1, "total_results": 1, "total_pages": 1, "results": [MUSICBRAINZ_RESULT]}
        with (
            patch("app.providers.services.musicbrainz.search", return_value=page),
            patch("app.providers.services.musicbrainz.discover", return_value=page),
        ):
            search = media_service.search_media(media_type="music", query="sos", source="musicbrainz", user=self.user)
            genre = media_service.discover_media(media_type="music", source="musicbrainz", genre="r&b", user=self.user)

        self.assert_logged(search[0])
        self.assert_logged(genre["results"][0])

    @patch("api.services.media.provider_services.get_person_page")
    def test_a_musicbrainz_artist_page(self, person_mock):
        person_mock.return_value = {
            "source": "musicbrainz",
            "person_id": "sza",
            "name": "SZA",
            "credits": [
                {**MUSICBRAINZ_RESULT, "year": "2022", "credit_roles": ["Albums"]},
                {**MUSICBRAINZ_RESULT, "media_id": OTHER_MBID, "title": "Ctrl", "credit_roles": ["Albums"]},
            ],
            "credits_complete": True,
        }

        response = self.client.get("/api/v1/people/musicbrainz/sza/")

        self.assertEqual(response.status_code, 200)
        sos, ctrl = response.json()["credits"]["cast"]
        self.assert_logged(sos)
        self.assertEqual((ctrl["ref"]["source"], ctrl["ref"]["media_id"]), ("musicbrainz", OTHER_MBID))
        self.assertEqual(response.json()["completion"], {"completed_count": 1, "total_count": 2})

    def test_web_search_cards(self):
        request = RequestFactory().get("/search")
        request.user = self.user

        (card,) = helpers.enrich_items_with_user_data(request, [MUSICBRAINZ_RESULT], "search")

        self.assertEqual((card["item"]["source"], card["item"]["media_id"]), ("apple_music", SOS))
        self.assertEqual(card["media"].item, self.item)

    def test_web_details_link_redirects_to_the_album(self):
        web = Client()
        web.force_login(self.user)

        response = web.get(
            reverse("media_details", args=["musicbrainz", "music", SOS_MBID, "sos"]),
            {"tab": "reviews"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("media_details", args=["apple_music", "music", SOS, "sos"]) + "?tab=reviews",
        )
        self.apple((200, fixture("page_sos.json")))
        self.assertEqual(web.get(response["Location"]).status_code, 200)


class UnmovedAlbumTests(MovedAlbumTestCase):
    """An album that hasn't moved keeps its MusicBrainz ref everywhere."""

    def test_musicbrainz_refs_stay_without_a_link(self):
        album = Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id=OTHER_MBID,
            title="Year Zero",
            image="https://example.com/year-zero.jpg",
        )
        apple = self.apple()
        payload = {
            "media_id": OTHER_MBID,
            "source": "musicbrainz",
            "media_type": "music",
            "title": "Year Zero",
            "image": "https://example.com/year-zero.jpg",
            "max_progress": 1,
            "details": {},
        }
        with patch("app.providers.services.musicbrainz.music", return_value=payload) as music:
            response = self.client.get(f"/api/v1/media/musicbrainz/music/{OTHER_MBID}/basic/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (response.json()["ref"]["source"], response.json()["ref"]["media_id"], response.json()["ref"]["item_id"]),
            ("musicbrainz", OTHER_MBID, album.id),
        )
        self.assertEqual(music.call_args.args, (OTHER_MBID,))
        self.assertEqual(apple.requests, [])
        web = Client()
        web.force_login(self.user)
        # The page itself is beside the point: it isn't redirected.
        with patch("app.views.services.get_media_metadata", side_effect=Http404) as metadata:
            web_response = web.get(reverse("media_details", args=["musicbrainz", "music", OTHER_MBID, "year-zero"]))
        self.assertEqual(web_response.status_code, 404)
        self.assertEqual(metadata.call_args.args, ("music", OTHER_MBID, "musicbrainz"))
