"""An Apple Music album page's related sections, as the app receives them."""

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from api.serializers.common import related_sections_from_payload
from api.services import media as media_service
from app.models import AppleAlbum, MusicAlbumLink, Status
from app.models import Music as MusicTracking
from app.tests.providers.test_apple_music import (
    AppleMusicTestCase,
    apple_item,
    clear_pause,
    fixture,
    related_answers,
)

SOS = "1657869377"
SOS_CLEAN = "1658650093"
LANA = "1794644675"
CTRL = "1628387909"
SOS_MBID = "1a5c4d2e-3b6f-4a7d-9e8c-0f1b2a3c4d5e"
SZA = {"music": {"artist_credit": [{"name": "SZA", "join_phrase": ""}]}}


def row(media_id, title="An album", **fields):
    """Return a related album as the provider sends it."""
    return {
        "media_id": media_id,
        "source": "apple_music",
        "media_type": "music",
        "title": title,
        "image": f"https://example.com/{media_id}.jpg",
        "poster_width": 600,
        "poster_height": 600,
        "subtitle": "SZA · 2024 · Album",
        **fields,
    }


def refs(section):
    """Return a section's albums as (media ID, Item ID)."""
    return [(item["ref"]["media_id"], item["ref"]["item_id"]) for item in section["items"]]


class AppleAlbumRelatedSectionTests(AppleMusicTestCase):
    """Other Versions, More by and You Might Also Like on Apple Music album pages."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="listener", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def sections(self, related, metadata=SZA):
        return related_sections_from_payload(related, "music", "apple_music", user=self.user, metadata=metadata)

    def test_the_album_page_has_three_sections_with_their_badges(self):
        lana = apple_item(LANA, title="SOS Deluxe: LANA")
        MusicTracking.objects.create(user=self.user, item=lana, status=Status.COMPLETED.value)
        self.apple((200, fixture("page_sos.json")), *related_answers())

        detail = self.client.get(f"/api/v1/media/apple_music/music/{SOS}/").json()

        other_versions, more_by, similar = detail["related_sections"]
        self.assertEqual(
            [(section["id"], section["title"]) for section in detail["related_sections"]],
            [
                ("other_versions", "Other Versions"),
                ("more_by_artist", "More by SZA"),
                ("related_albums", "You Might Also Like"),
            ],
        )
        (deluxe,) = other_versions["items"]
        self.assertEqual(
            (deluxe["ref"]["media_id"], deluxe["ref"]["item_id"], deluxe["title"], deluxe["relation"]),
            (LANA, lana.id, "SOS Deluxe: LANA", "Deluxe"),
        )
        self.assertEqual(deluxe["content_rating"], "explicit")
        self.assertEqual(deluxe["user_state"]["status"], Status.COMPLETED.value)
        self.assertEqual(refs(more_by), [(LANA, lana.id), (CTRL, None)])
        self.assertEqual(len(similar["items"]), 10)
        self.assertNotIn("relation", similar["items"][0])

    def test_old_builds_get_the_sections_in_both_stages_of_a_moved_album(self):
        # The second stage's payload replaces the first one's in installed builds.
        MusicAlbumLink.objects.create(
            release_group_mbid=SOS_MBID,
            apple_album_id=SOS,
            item=apple_item(SOS),
            status=MusicAlbumLink.Status.APPLIED,
            method=MusicAlbumLink.Method.UPC_EXACT,
            tier=1,
            applied_at=timezone.now(),
        )
        fake = self.apple((200, fixture("page_sos.json")), *related_answers())

        for stage in ("basic", "enrichment"):
            with self.subTest(stage):
                response = self.client.get(f"/api/v1/media/musicbrainz/music/{SOS_MBID}/{stage}/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [section["id"] for section in response.json()["related_sections"]],
                    ["other_versions", "more_by_artist", "related_albums"],
                )
        # Both stages share the album's cached page and related lookups.
        self.assertEqual(len(fake.requests), 4)

    def test_the_refresh_task_keeps_the_sections(self):
        self.apple((200, fixture("page_sos.json")), *related_answers())

        media_service.refresh_music_detail(source="apple_music", media_id=SOS, include_music_enrichment=True)
        cache_key = media_service._detail_cache_key(
            source="apple_music",
            media_type="music",
            media_id=SOS,
            season_number=None,
            episode_number=None,
            include_music_enrichment=True,
        )

        self.assertEqual(sorted(cache.get(cache_key)["related"]), ["more_by_artist", "other_versions", "related_albums"])

    def test_a_refresh_while_apple_fails_for_a_moment_keeps_the_page_it_had(self):
        self.apple((200, fixture("page_sos.json")), *related_answers())
        media_service.refresh_music_detail(source="apple_music", media_id=SOS, include_music_enrichment=True)
        cache_key = media_service._detail_cache_key(
            source="apple_music",
            media_type="music",
            media_id=SOS,
            season_number=None,
            episode_number=None,
            include_music_enrichment=True,
        )
        good = cache.get(cache_key)
        # A day on: the provider's caches are gone and the page is stale.
        cache.delete_many(
            [
                f"{cache_key}:validation",
                f"apple_music_v1_album_page_{SOS}",
                f"apple_music_v1_editions_{SOS}",
                "apple_music_v1_artist_albums_605800394",
            ],
        )
        # The shared-songs lookup times out, which pauses the artist's albums too.
        fake = self.apple((200, fixture("page_sos.json")), requests.exceptions.ReadTimeout("read timed out"))

        result = media_service.refresh_music_detail(source="apple_music", media_id=SOS, include_music_enrichment=True)

        self.assertEqual(result, {"outcome": "partial_page"})
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(cache.get(cache_key), good)
        self.assertIsNone(cache.get(f"{cache_key}:validation"))

    def test_a_page_built_while_apple_fails_for_a_moment_isnt_cached(self):
        # A 503 for the shared songs pauses Apple, so More by is skipped too.
        self.apple((200, fixture("page_sos.json")), (503, {"errors": []}))

        first = self.client.get(f"/api/v1/media/apple_music/music/{SOS}/").json()

        self.assertEqual([section["id"] for section in first["related_sections"]], ["related_albums"])
        self.assertNotIn("partial_page", first)
        # The next read builds the page again, and it is kept once Apple answers.
        clear_pause()
        fake = self.apple(*related_answers())
        again = self.client.get(f"/api/v1/media/apple_music/music/{SOS}/").json()
        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(
            [section["id"] for section in again["related_sections"]],
            ["other_versions", "more_by_artist", "related_albums"],
        )
        self.apple()  # any request fails the test
        self.assertEqual(self.client.get(f"/api/v1/media/apple_music/music/{SOS}/").status_code, 200)

    def test_an_album_the_app_couldnt_decode_is_left_out(self):
        bad = [
            row(""),
            row("2", title=None),
            row("3", title="   "),
            row("4", title=7),
            row("5", subtitle={"year": 2024}),
            row("6", roles=[1]),
            row("7", relation=["Deluxe"]),
            row("8", genres="hip hop"),
            row("9", release_date=20241220),
            "not an album",
        ]

        sections = self.sections(
            {
                "other_versions": [*bad, row(LANA, title="SOS Deluxe: LANA", relation="Deluxe")],
                "related_albums": bad,
            },
        )

        (section,) = sections
        self.assertEqual(section["id"], "other_versions")
        self.assertEqual([item["title"] for item in section["items"]], ["SOS Deluxe: LANA"])

    def test_albums_go_under_their_canonical_refs_once_each(self):
        sos = apple_item(SOS)
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)

        (section,) = self.sections({"more_by_artist": [row(SOS_CLEAN, title="SOS"), row(SOS, title="SOS"), row(CTRL)]})

        self.assertEqual(refs(section), [(SOS, sos.id), (CTRL, None)])

    def test_at_most_ten_albums_a_section(self):
        (section,) = self.sections({"related_albums": [row(str(number)) for number in range(1, 16)]})

        self.assertEqual([item["ref"]["media_id"] for item in section["items"]], [str(number) for number in range(1, 11)])

    def test_the_more_by_title_names_the_first_artist(self):
        more_by = {"more_by_artist": [row(CTRL)]}
        two_artists = {
            "music": {
                "artist_credit": [
                    {"name": "Drake", "join_phrase": " & "},
                    {"name": "21 Savage", "join_phrase": ""},
                ],
            },
        }

        self.assertEqual(self.sections(more_by, two_artists)[0]["title"], "More by Drake")
        self.assertEqual(self.sections(more_by, {})[0]["title"], "More by This Artist")

    def test_items_and_viewer_state_are_read_in_a_fixed_number_of_queries(self):
        def queries(count):
            albums = [apple_item(f"{count}{number}", title=f"Album {number}") for number in range(count)]
            for album in albums:
                MusicTracking.objects.create(user=self.user, item=album, status=Status.COMPLETED.value)
            related = {"related_albums": [row(album.media_id, title=album.title) for album in albums]}
            with CaptureQueriesContext(connection) as captured:
                (section,) = self.sections(related)
            self.assertEqual(
                {item["user_state"]["status"] for item in section["items"]},
                {Status.COMPLETED.value},
            )
            return len(captured.captured_queries)

        self.assertEqual(queries(2), queries(6))
