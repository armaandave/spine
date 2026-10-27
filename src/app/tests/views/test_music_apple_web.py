from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.templatetags.static import static
from django.test import override_settings
from django.urls import reverse

from app.models import Music, Status
from app.tests.providers.test_apple_music import SOS_URL, AppleMusicTestCase, apple_item, fixture

SOS = "1657869377"
MUSICBRAINZ_ID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"
BADGE = static("img/listen-on-apple-music.svg")
CREDIT = "Data from Apple Music"
TRADEMARK = "Apple and Apple Music are trademarks of Apple Inc., registered in the U.S. and other countries."
HTMX = {"HX-Request": "true"}


class AppleAlbumWebPageTests(AppleMusicTestCase):
    """The web album page credits Apple Music on Apple albums only."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="web-listener", password="strong-password-123")
        self.client.force_login(self.user)
        self.url = reverse("media_details", args=["apple_music", "music", SOS, "sos"])

    def test_an_apple_album_shows_the_badge_the_credit_and_the_trademark_line(self):
        fake = self.apple((200, fixture("page_sos.json")))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")
        self.assertContains(response, f'href="{SOS_URL}"')
        self.assertContains(response, f'src="{BADGE}"')
        self.assertContains(response, 'alt="Listen on Apple Music"')
        self.assertContains(response, CREDIT, count=1)
        self.assertContains(response, TRADEMARK, count=1)
        # The web shows no related sections, so it asks Apple for the page only.
        self.assertEqual(len(fake.requests), 1)

    def test_an_album_opened_from_its_item_keeps_the_credit_without_a_link(self):
        apple_item(SOS)
        self.apple((401, fixture("error_401.json")["body"]))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, BADGE)
        self.assertContains(response, CREDIT)
        self.assertContains(response, TRADEMARK)

    def test_a_musicbrainz_album_has_none_of_it(self):
        payload = {
            "media_id": MUSICBRAINZ_ID,
            "source": "musicbrainz",
            "media_type": "music",
            "title": "Year Zero",
            "image": "https://example.com/year-zero.jpg",
            "max_progress": 1,
            "details": {},
        }
        with patch("app.providers.services.musicbrainz.music", return_value=payload):
            response = self.client.get(reverse("media_details", args=["musicbrainz", "music", MUSICBRAINZ_ID, "y"]))

        self.assertEqual(response.status_code, 200)
        for text in (BADGE, CREDIT, TRADEMARK):
            self.assertNotContains(response, text)

    def test_the_pages_htmx_modals_open_for_an_apple_album(self):
        album = apple_item(SOS)
        Music.objects.create(user=self.user, item=album, status=Status.COMPLETED.value)
        fake = self.apple()

        log = self.client.get(reverse("log_modal", args=["apple_music", "music", SOS]), headers=HTMX)
        track = self.client.get(
            reverse("track_modal", args=["apple_music", "music", SOS]),
            {"return_url": self.url},
            headers=HTMX,
        )

        self.assertEqual((log.status_code, track.status_code), (200, 200))
        self.assertContains(log, "Log Album")
        self.assertContains(track, 'name="media_id"')
        self.assertContains(track, SOS)
        # Both read the saved album.
        self.assertEqual(fake.requests, [])


class MusicSearchTabTests(AppleMusicTestCase):
    """The web search page's source tabs follow the music catalog."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="web-searcher", password="strong-password-123")
        self.client.force_login(self.user)

    def search(self):
        return self.client.get(reverse("search"), {"media_type": "music", "q": "sos"})

    def test_musicbrainz_by_default(self):
        results = {"page": 1, "total_results": 0, "total_pages": 1, "results": []}
        with patch("app.views.services.search", return_value=results) as search:
            response = self.search()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(search.call_args.args, ("music", "sos", 1, "musicbrainz"))
        self.assertContains(response, "source=musicbrainz")
        self.assertNotContains(response, "source=apple_music")

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_apple_music_when_it_is_the_catalog(self):
        self.apple((200, fixture("search_albums_sos.json")), (200, fixture("search_families_sos.json")))

        response = self.search()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "source=apple_music")
        self.assertNotContains(response, "source=musicbrainz")
        self.assertContains(response, reverse("media_details", args=["apple_music", "music", SOS, "sos"]))
