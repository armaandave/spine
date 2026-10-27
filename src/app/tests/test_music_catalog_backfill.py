"""The music data move: ``manage.py music_catalog`` map, apply, revert, check, report and link.

MusicBrainz and Apple Music answer from recorded fixtures
(``scripts/music/record_apple_fixtures.py --only backfill``), and any other request
fails the test instead of reaching the network.
"""

import copy
import csv
import io
import os
import stat
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.serializers.common import custom_poster_url_for_user
from api.services import media as media_service
from app import curation, music_catalog, music_move
from app.mixins import disable_fetch_releases
from app.models import (
    AppleAlbum,
    BasicMedia,
    CustomPosterPreference,
    DiaryEntry,
    ExternalRating,
    Item,
    ItemFilterFacet,
    MediaLike,
    MediaTypes,
    Music,
    MusicAlbumLink,
    Sources,
    Status,
)
from app.providers import services
from app.tests.providers.test_apple_music import (
    AppleMusicTestCase,
    FakeApple,
    apple_item,
    fixture,
    genre_facets,
    query,
)
from app.tests.test_music_links import FakeClock, genre_album, without_spotify
from lists.featured import dispatch
from lists.models import CustomList, CustomListItem
from social.models import Activity, ProgressChange

SOS_MBID = "1646286a-d0ad-4288-bfab-34b0fb7b22c1"
FIRST_LOVE_MBID = "c60fcd45-9420-30aa-98e5-2bffb894021a"
NO_FENCES_MBID = "d4377787-0d92-3902-997a-feb75f4dee2d"
CTRL_MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"
SOS = "1657869377"
SOS_CLEAN = "1658650093"
LANA = "1794644675"
FIRST_LOVE = "1440763349"
FIRST_LOVE_ALIAS = "1319421264"
SOS_SPOTIFY = "https://open.spotify.com/album/07w0rG5TETcyihsEIZR3qG"
SOS_COVER = "https://coverartarchive.org/release-group/1646286a-d0ad-4288-bfab-34b0fb7b22c1/front-500"
SOS_GENRES = ["contemporary r&b", "r&b"]
SOS_PICK = "https://coverartarchive.org/release/sos-pick/front-500.jpg"


def browse(name):
    """Return the recorded MusicBrainz release browse answers, page by page."""
    return [(200, page) for page in fixture(f"musicbrainz_releases_{name}.json")["pages"]]


def barcodes(name):
    """Return Apple Music's recorded answers to an album's barcodes."""
    return [(200, response) for response in fixture(f"backfill_upc_{name}.json")["responses"]]


def family(name):
    return (200, fixture(f"backfill_family_{name}.json"))


def sos_map_answers():
    """Apple Music's answers when map matches SOS: its barcodes, then its family."""
    return (*barcodes("sos"), family("sos"))


def sos_clean_twin_family():
    """Apple Music's family answer for SOS's clean twin: SOS's, turned round.

    Apple's version family is symmetric (``apple_music._family``).
    """
    explicit = fixture("backfill_family_sos.json")["data"][0]
    clean = explicit.pop("views")["other-versions"]["data"][0]
    return (200, {"data": [{**clean, "views": {"other-versions": {"data": [explicit]}}}]})


def without_digital(pages):
    """Return browse answers with the digital releases left out: only physical barcodes."""
    pages = copy.deepcopy(pages)
    for _, page in pages:
        page["releases"] = [
            release
            for release in page["releases"]
            if not any(medium.get("format") == "Digital Media" for medium in release["media"])
        ]
        page["release-count"] = len(page["releases"])
    return pages


class BackfillTestCase(AppleMusicTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="listener", password="strong-password-123")
        self.other = get_user_model().objects.create_user(username="friend", password="strong-password-123")
        self.clock = FakeClock()
        dispatch.resume_music_lists()

    def musicbrainz(self, *answers):
        fake = FakeApple(*answers)
        patcher = patch.object(services.musicbrainz_session, "send", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def map(self, **options):
        return music_move.map_albums(clock=self.clock.monotonic, sleep=self.clock.sleep, **options)

    def musicbrainz_item(self, mbid=SOS_MBID, title="SOS", **fields):
        fields.setdefault("image", SOS_COVER if mbid == SOS_MBID else f"https://coverartarchive.org/release-group/{mbid}/front-500")
        return Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id=mbid,
            title=title,
            **fields,
        )

    def sos_item(self):
        """SOS as a MusicBrainz album, with its accent colour, dates and genres."""
        item = self.musicbrainz_item(
            poster_accent_color="#2a6f9e",
            release_date=date(2022, 12, 9),
            release_year=2022,
            filter_metadata_updated_at=timezone.now() - timedelta(days=3),
        )
        genre_facets(item, *SOS_GENRES)
        return item

    def track(self, item, user=None, **fields):
        with disable_fetch_releases():
            return Music.objects.create(user=user or self.user, item=item, status=Status.COMPLETED.value, **fields)

    def all_user_rows(self, item, user=None):
        """Give ``user`` a row on ``item`` in every table a move moves; return the rows."""
        user = user or self.user
        music = self.track(item, user, score=Decimal("9.0"))
        entry = DiaryEntry.objects.create(user=user, item=item, consumed_at=timezone.now(), rating=Decimal("4.5"), review="Still great.")
        like = MediaLike.objects.create(user=user, item=item)
        own_list = CustomList.objects.create(owner=user, name="Favourites")
        list_row = CustomListItem.objects.create(item=item, custom_list=own_list)
        activity = Activity.objects.create(actor=user, verb="diary_created", target_type="diary", target_id=entry.id, item=item)
        progress = ProgressChange.record_start(user, item)
        get_user_model().objects.filter(pk=user.pk).update(hof_music=item, profile_backdrop_item=item)
        return [music, entry, like, list_row, activity, progress]

    def matched(
        self,
        mbid=SOS_MBID,
        apple_album_id=SOS,
        *,
        tier=1,
        method=MusicAlbumLink.Method.UPC_EXACT,
        status=MusicAlbumLink.Status.MATCHED,
        **fields,
    ):
        """Write the link row map writes for a match, and the listing's alias rows."""
        AppleAlbum.objects.get_or_create(apple_album_id=apple_album_id, defaults={"canonical_id": apple_album_id})
        fields.setdefault("apple_title", "SOS")
        fields.setdefault("apple_image", "https://is1-ssl.mzstatic.com/image/thumb/sos/600x600bb.jpg")
        fields.setdefault("genres", SOS_GENRES)
        return MusicAlbumLink.objects.create(
            release_group_mbid=mbid,
            apple_album_id=apple_album_id,
            status=status,
            method=method,
            tier=tier,
            checked_at=timezone.now(),
            **fields,
        )

    def apply(self, *answers, **options):
        """Run apply, with Apple Music answering its album batch with SOS by default."""
        self.apple(*(answers or (family("sos"),)), background=True)
        return music_move.apply(**options)

    def user_rows(self):
        """Every user row as ``(table, pk, Item pk)``, and each user's two picks."""
        rows = []
        for table in music_move.user_tables():
            if table.model is get_user_model():
                continue
            rows += [(table.label, pk, item) for pk, item in table.model.objects.values_list("pk", table.field)]
        rows += list(get_user_model().objects.values_list("pk", "hof_music", "profile_backdrop_item"))
        return sorted(rows, key=str)


class BarcodeTests(BackfillTestCase):
    def test_barcodes_compare_as_thirteen_digits(self):
        self.assertEqual(music_move.barcode13("602547582812"), "0602547582812")
        self.assertEqual(music_move.barcode13("00602547582812"), "0602547582812")
        self.assertEqual(music_move.barcode13("4547366616026"), "4547366616026")
        self.assertEqual(music_move.barcode13("1 96589-564931"), "0196589564931")
        self.assertEqual(music_move.barcode13("19658956493"), "")  # 11 digits
        self.assertEqual(music_move.barcode13("12345678901234"), "")  # a 14-digit code that isn't padding
        self.assertEqual(music_move.barcode13(None), "")


class SpotifyLinkTests(SimpleTestCase):
    """The exact Spotify link map and the album bridge take, on Short n' Sweet's releases.

    Apple's explicit album's own release has no Spotify link. A clean and an explicit
    copy with its title and track count have one, the clean one first.
    """

    def releases(self):
        return fixture("musicbrainz_releases_short_n_sweet.json")["pages"][0]["releases"]

    def album(self, **attributes):
        return {
            "attributes": {
                "name": "Short n' Sweet",
                "upc": "00602465891386",
                "trackCount": 12,
                "contentRating": "explicit",
                **attributes,
            },
        }

    def test_an_explicit_album_takes_the_explicit_copys_link(self):
        self.assertEqual(
            music_move._spotify_link(self.releases(), self.album()),
            "https://open.spotify.com/album/3iPSVi54hsacKKl1xIR2eH",
        )

    def test_a_clean_album_takes_the_clean_copys_link(self):
        self.assertEqual(
            music_move._spotify_link(self.releases(), self.album(upc="", contentRating="clean")),
            "https://open.spotify.com/album/4B4Elma4nNDUyl6D5PvQkj",
        )


class MapAlbumsWithUserDataTests(BackfillTestCase):
    def test_sos_matches_tier_one_by_its_own_barcode(self):
        item = self.sos_item()
        self.track(item)
        rows_before = self.user_rows()
        musicbrainz = self.musicbrainz(*browse("sos"))
        apple = self.apple(*sos_map_answers(), background=True)

        counts = self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual(
            (link.status, link.method, link.tier, link.apple_album_id, link.apple_title),
            ("matched", "upc_exact", 1, SOS, "SOS"),
        )
        self.assertTrue(link.apple_image.startswith("https://is1-ssl.mzstatic.com/"))
        self.assertEqual(link.spotify_url, SOS_SPOTIFY)
        self.assertEqual(link.genres, SOS_GENRES)
        self.assertIsNotNone(link.checked_at)
        self.assertEqual(counts["group 1: matched tier 1"], 1)
        # The barcodes go out as 13 digits, the clean twin folds into its listing, and
        # nothing a user made changes.
        self.assertIn("0196589564931", query(apple.requests[0])["filter[upc]"])
        self.assertEqual(AppleAlbum.objects.get(apple_album_id=SOS_CLEAN).canonical_id, SOS)
        self.assertEqual(len(musicbrainz.requests), 1)
        self.assertEqual(self.user_rows(), rows_before)
        item.refresh_from_db()
        self.assertEqual((item.source, item.media_id), ("musicbrainz", SOS_MBID))

    def test_spotify_never_comes_from_a_clean_twin(self):
        # Were SOS's own release without its link, the first release with SOS's title
        # and track count that has one would be the clean twin, which MusicBrainz marks
        # "clean".
        self.track(self.sos_item())
        pages = browse("sos")
        for _, page in pages:
            page["releases"] = [
                without_spotify(release) if release["barcode"] == "196589564931" else release
                for release in page["releases"]
            ]
        self.musicbrainz(*pages)
        self.apple(*sos_map_answers(), background=True)

        self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.tier, link.apple_album_id, link.spotify_url), (1, SOS, ""))

    def test_spotify_is_the_album_the_page_shows_when_only_its_twin_has_a_barcode(self):
        # Were SOS's explicit releases without their barcode, the barcode pick would be
        # the clean twin, while the listing's identity and page are the explicit album.
        # The link is the explicit release's, not the clean twin's barcode's.
        self.track(self.sos_item())
        pages = browse("sos")
        for _, page in pages:
            for release in page["releases"]:
                if release["barcode"] == "196589564931":
                    release["barcode"] = None
        self.musicbrainz(*pages)
        apple = self.apple(*barcodes("sos"), sos_clean_twin_family(), background=True)

        self.map()

        self.assertNotIn("0196589564931", query(apple.requests[0])["filter[upc]"])
        self.assertEqual(query(apple.requests[1])["ids"], SOS_CLEAN)
        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.tier, link.apple_album_id, link.spotify_url), (1, SOS, SOS_SPOTIFY))

    def test_a_second_map_writes_the_same_row(self):
        self.track(self.sos_item())
        # The release browse is cached for a day, so the second run asks Apple only.
        self.musicbrainz(*browse("sos"))
        self.apple(*sos_map_answers(), *sos_map_answers(), background=True)
        self.map()
        first = MusicAlbumLink.objects.values().get()

        self.map()

        second = MusicAlbumLink.objects.values().get()
        self.assertGreaterEqual(second.pop("checked_at"), first.pop("checked_at"))
        self.assertEqual(second, first)

    def test_map_re_matches_a_genre_fill_row_by_barcode(self):
        item = self.sos_item()
        self.track(item)
        self.matched(tier=3, method=MusicAlbumLink.Method.SEARCH, apple_album_id=LANA)
        self.musicbrainz(*browse("sos"))
        self.apple(*sos_map_answers(), background=True)

        self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.method, link.tier, link.apple_album_id), ("upc_exact", 1, SOS))

    def test_only_a_family_barcode_is_tier_two(self):
        item = self.sos_item()
        self.track(item)
        self.musicbrainz(*without_digital(browse("sos")))
        self.apple(*sos_map_answers(), background=True)

        self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.status, link.method, link.tier, link.apple_album_id), ("matched", "upc_family", 2, SOS))

    def test_a_name_in_another_script_matches_through_the_sort_name(self):
        # 宇多田ヒカル on MusicBrainz, Hikaru Utada on Apple Music; Apple's old
        # duplicate ID is folded into the album's own.
        item = self.musicbrainz_item(FIRST_LOVE_MBID, "First Love")
        self.track(item)
        self.musicbrainz(*browse("first_love"))
        self.apple(*barcodes("first_love"), family("first_love"), background=True)

        self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=FIRST_LOVE_MBID)
        self.assertEqual(
            (link.status, link.tier, link.apple_album_id, link.apple_title),
            ("matched", 1, FIRST_LOVE, "First Love (Remastered 2014)"),
        )
        self.assertEqual(AppleAlbum.objects.get(apple_album_id=FIRST_LOVE_ALIAS).canonical_id, FIRST_LOVE)

    def test_through_the_family_only_the_sort_name_vouches_for_the_artist(self):
        # With no digital release, no barcode is Apple's album's own, so the artist
        # must match. Every release here credits her in Japanese or Korean, so only
        # her sort name, "Utada, Hikaru", reads Hikaru Utada.
        item = self.musicbrainz_item(FIRST_LOVE_MBID, "First Love")
        self.track(item)
        pages = without_digital(browse("first_love"))
        for release in pages[0][1]["releases"]:
            for credit in release["artist-credit"]:
                if credit["name"] == "Hikaru Utada":
                    credit["name"] = "宇多田ヒカル"
        self.musicbrainz(*pages)
        self.apple(*barcodes("first_love"), family("first_love"), background=True)

        self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=FIRST_LOVE_MBID)
        self.assertEqual((link.status, link.method, link.tier, link.apple_album_id), ("matched", "upc_family", 2, FIRST_LOVE))

    def test_no_fences_is_unmatched(self):
        # No barcode, none of its songs and no search result is Garth Brooks's album:
        # only tributes.
        item = self.musicbrainz_item(NO_FENCES_MBID, "No Fences")
        self.track(item)
        self.musicbrainz(*browse("no_fences"), (200, fixture("musicbrainz_release_isrcs_no_fences.json")))
        apple = self.apple(
            *barcodes("no_fences"),
            (200, fixture("backfill_isrc_no_fences.json")),
            (200, fixture("backfill_search_no_fences.json")),
            background=True,
        )

        counts = self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=NO_FENCES_MBID)
        self.assertEqual((link.status, link.tier, link.apple_album_id), ("unmatched", None, ""))
        self.assertEqual(counts["group 1: unmatched"], 1)
        self.assertEqual(query(apple.requests[2])["term"], "Garth Brooks No Fences")

    def test_an_unrelated_album_with_a_barcode_isnt_a_match(self):
        # One of AM's barcodes on MusicBrainz is another album's on Apple Music.
        item = self.sos_item()
        self.track(item)
        stranger = {
            "id": "1500000001",
            "type": "albums",
            "attributes": {
                "name": "Politics Can't Stop God",
                "artistName": "Stide Prince",
                "upc": "196589564931",
                "trackCount": 23,
                "url": "https://music.apple.com/us/album/politics-cant-stop-god/1500000001",
                "playParams": {"id": "1500000001"},
            },
        }
        no_songs = {"id": "b82458a3-7d3a-495f-861f-8698731c4dd0", "media": [{"tracks": []}]}
        self.musicbrainz(*browse("sos"), (200, no_songs))
        self.apple((200, {"data": [stranger]}), (200, {"results": {}}), background=True)

        self.map()

        self.assertEqual(MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID).status, "unmatched")

    def test_a_barcode_match_with_another_track_count_waits_for_review(self):
        item = self.sos_item()
        self.track(item)
        pages = browse("sos")
        for release in pages[0][1]["releases"]:
            for medium in release["media"]:
                medium["track-count"] = 30
        self.musicbrainz(*pages)
        self.apple(*sos_map_answers(), background=True)

        self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.status, link.tier, link.apple_album_id), ("review", 3, SOS))

    def test_musicbrainz_requests_are_three_seconds_apart_and_wait_out_a_busy_musicbrainz(self):
        self.track(self.sos_item())
        self.track(self.musicbrainz_item(FIRST_LOVE_MBID, "First Love"))
        busy = (503, {"error": "busy"})
        musicbrainz = self.musicbrainz(*browse("sos"), busy, *browse("first_love"))
        self.apple(*sos_map_answers(), *barcodes("first_love"), family("first_love"), background=True)

        counts = self.map()

        self.assertEqual(MusicAlbumLink.objects.filter(status="matched").count(), 2)
        # One request a try: MusicBrainz's own client doesn't retry the 503 at once.
        self.assertEqual(counts["MusicBrainz requests"], 3)
        self.assertEqual(len(musicbrainz.requests), 3)
        # Three seconds after SOS's request, then 30 more after MusicBrainz was busy.
        self.assertEqual(self.clock.sleeps, [music_move.MUSICBRAINZ_SPACING, music_move.MUSICBRAINZ_BACKOFF])

    def test_a_musicbrainz_busy_on_every_try_stops_the_run(self):
        self.track(self.sos_item())
        busy = (503, {"error": "busy"})
        musicbrainz = self.musicbrainz(busy, busy, busy)

        counts = self.map()

        self.assertEqual(counts["stopped"], f"a musicbrainz request failed at {SOS_MBID}")
        self.assertEqual((counts["MusicBrainz requests"], len(musicbrainz.requests)), (3, 3))
        self.assertEqual(self.clock.sleeps, [music_move.MUSICBRAINZ_BACKOFF, 2 * music_move.MUSICBRAINZ_BACKOFF])
        self.assertFalse(MusicAlbumLink.objects.exists())

    def test_an_album_musicbrainz_no_longer_has_doesnt_stop_the_run(self):
        gone = "00000000-0000-0000-0000-000000000404"
        self.track(self.musicbrainz_item(gone, "Gone"))
        self.track(self.sos_item())
        self.musicbrainz((404, {"error": "Not Found"}), *browse("sos"))
        self.apple(*sos_map_answers(), background=True)

        counts = self.map()

        self.assertEqual(counts["stopped"], "")
        self.assertEqual(counts["group 1: MusicBrainz has no such album"], 1)
        self.assertEqual(counts["group 1: matched tier 1"], 1)
        link = MusicAlbumLink.objects.get(release_group_mbid=gone)
        self.assertEqual(link.status, "unmatched")
        self.assertIsNotNone(link.checked_at)
        # Checked now, so the next run looks at it after the albums it hasn't checked.
        self.track(self.musicbrainz_item(FIRST_LOVE_MBID, "First Love"))
        self.musicbrainz(*browse("first_love"))
        self.apple(*barcodes("first_love"), family("first_love"), background=True)
        self.assertEqual(self.map(limit=1)["group 1: matched tier 1"], 1)

    def test_a_failed_request_stops_the_run_and_the_next_carries_on(self):
        self.track(self.sos_item())
        self.track(self.musicbrainz_item(FIRST_LOVE_MBID, "First Love"))
        self.musicbrainz(*browse("sos"), *browse("first_love"))
        self.apple(*barcodes("sos"), (500, {"errors": []}), background=True)
        with patch("app.providers.services.time.sleep"):
            counts = self.map()

        self.assertIn("apple_music", counts["stopped"])
        self.assertIn(SOS_MBID, counts["stopped"])
        self.assertFalse(MusicAlbumLink.objects.exists())

    def test_manual_and_applied_rows_are_left_and_the_limit_holds(self):
        sos = self.sos_item()
        self.track(sos)
        self.matched(method=MusicAlbumLink.Method.MANUAL, tier=None)
        love = self.musicbrainz_item(FIRST_LOVE_MBID, "First Love")
        self.track(love)
        no_fences = self.musicbrainz_item(NO_FENCES_MBID, "No Fences")
        self.track(no_fences)
        self.musicbrainz(*browse("first_love"))
        self.apple(*barcodes("first_love"), family("first_love"), background=True)

        counts = self.map(limit=1)

        self.assertEqual(counts["looked at"], 1)
        self.assertEqual(MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID).method, "manual")
        self.assertTrue(MusicAlbumLink.objects.filter(release_group_mbid=FIRST_LOVE_MBID).exists())
        self.assertFalse(MusicAlbumLink.objects.filter(release_group_mbid=NO_FENCES_MBID).exists())

    @override_settings(APPLE_MUSIC_KEY_ID="")
    def test_map_needs_the_key(self):
        with self.assertRaisesMessage(music_move.MoveError, "Apple Music isn't configured"):
            self.map()


class MapAlbumsWithoutUserDataTests(BackfillTestCase):
    def test_a_featured_only_album_is_matched_by_search_without_musicbrainz(self):
        item = self.sos_item()
        featured = CustomList.objects.create(owner=self.user, name="Top albums", is_featured=True)
        CustomListItem.objects.create(item=item, custom_list=featured)
        genre_album(SOS_MBID, "SOS", "SZA", "r&b")
        self.musicbrainz()  # none
        apple = self.apple(
            (200, fixture("search_albums_sza_sos_1.json")),
            (200, fixture("search_families_sza_sos_1.json")),
            background=True,
        )

        counts = self.map()

        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.status, link.method, link.tier, link.apple_album_id), ("matched", "search", 3, SOS))
        self.assertEqual(counts["group 2: matched tier 3"], 1)
        self.assertEqual(query(apple.requests[0])["term"], "SZA SOS")

    def test_an_album_with_no_known_artist_is_left(self):
        self.musicbrainz_item(CTRL_MBID, "Ctrl")
        self.musicbrainz()
        self.apple(background=True)

        counts = self.map()

        self.assertEqual(counts["group 3: no artist known"], 1)
        self.assertFalse(MusicAlbumLink.objects.exists())

    def test_an_unmatched_album_is_searched_again_after_thirty_days(self):
        self.musicbrainz_item(CTRL_MBID, "Ctrl")
        genre_album(CTRL_MBID, "Ctrl", "SZA", "r&b")
        link = MusicAlbumLink.objects.create(
            release_group_mbid=CTRL_MBID,
            status=MusicAlbumLink.Status.UNMATCHED,
            method=MusicAlbumLink.Method.SEARCH,
            checked_at=timezone.now() - timedelta(days=29),
        )
        self.musicbrainz()
        self.apple(background=True)
        self.assertEqual(self.map()["looked at"], 0)

        MusicAlbumLink.objects.filter(pk=link.pk).update(checked_at=timezone.now() - timedelta(days=31))
        self.apple((200, {"results": {}}), background=True)
        self.assertEqual(self.map()["group 3: unmatched"], 1)


class ApplyTests(BackfillTestCase):
    def test_apply_re_keys_the_album_in_place(self):
        item = self.sos_item()
        rows = self.all_user_rows(item)
        CustomPosterPreference.objects.create(user=self.user, item=item, custom_image_url=SOS_PICK)
        ExternalRating.objects.create(
            item=item,
            rating_source="musicbrainz",
            status=ExternalRating.Status.UNAVAILABLE,
            last_attempted_at=timezone.now(),
        )
        self.musicbrainz(*browse("sos"))
        self.apple(*sos_map_answers(), background=True)
        self.map()

        with (
            patch("app.signals.update_daily_statistics.delay") as statistics,
            self.captureOnCommitCallbacks(execute=True),
        ):
            counts = self.apply()

        self.assertEqual(counts["moved"], 1)
        statistics.assert_not_called()
        self.assertFalse(dispatch.music_lists_paused())
        moved = Item.objects.get(pk=item.pk)
        self.assertEqual((moved.source, moved.media_id, moved.title, moved.poster_accent_color), ("apple_music", SOS, "SOS", ""))
        self.assertTrue(moved.image.startswith("https://is1-ssl.mzstatic.com/"))
        self.assertEqual(moved.release_date, date(2022, 12, 9))
        self.assertEqual(sorted(moved.filter_facets.values_list("value", flat=True)), ["R&B/Soul"])
        # Every row still points at the same pk; the old ref finds the album.
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.item_id, item.pk)
        self.user.refresh_from_db()
        self.assertEqual((self.user.hof_music_id, self.user.profile_backdrop_item_id), (item.pk, item.pk))
        self.assertEqual(ExternalRating.objects.get().item_id, item.pk)
        self.assertEqual(
            music_catalog.canonical_music_ref("musicbrainz", "music", SOS_MBID),
            ("apple_music", "music", SOS),
        )
        link = MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID)
        self.assertEqual((link.status, link.item_id), ("applied", item.pk))
        self.assertEqual(
            {key: link.previous[key] for key in ("source", "media_id", "title", "image", "poster_accent_color", "release_date")},
            {
                "source": "musicbrainz",
                "media_id": SOS_MBID,
                "title": "SOS",
                "image": SOS_COVER,
                "poster_accent_color": "#2a6f9e",
                "release_date": "2022-12-09",
            },
        )
        bridge = AppleAlbum.objects.get(apple_album_id=SOS)
        self.assertEqual(
            (bridge.release_group_mbid, bridge.same_title, bridge.genres, bridge.spotify_url, bridge.spotify_exact),
            (SOS_MBID, True, SOS_GENRES, SOS_SPOTIFY, True),
        )
        self.assertIsNotNone(bridge.bridge_checked_at)
        # The cover pick stays saved but doesn't apply to an Apple Music album.
        self.assertFalse(curation.music_picks_apply(moved))
        self.assertIsNone(custom_poster_url_for_user(self.user, None, item=moved))

    def test_featured_music_lists_are_paused_while_it_runs(self):
        self.track(self.sos_item())
        self.matched()
        seen = []
        original = music_move._apply_one

        def spy(*args):
            seen.append(dispatch.music_lists_paused())
            return original(*args)

        with patch.object(music_move, "_apply_one", spy):
            self.apply()

        self.assertEqual(seen, [True])
        self.assertFalse(dispatch.music_lists_paused())

    def test_both_refs_cached_pages_are_dropped_once_it_commits(self):
        self.track(self.sos_item())
        self.matched()
        keys = []
        with (
            patch("api.services.media.drop_music_detail_cache", side_effect=lambda *ref: keys.append(ref)),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.apply()

        self.assertEqual(keys, [("musicbrainz", SOS_MBID), ("apple_music", SOS)])

    def test_the_limit_moves_the_album_with_the_most_user_rows_first(self):
        sos = self.sos_item()
        self.all_user_rows(sos)
        love = self.musicbrainz_item(FIRST_LOVE_MBID, "First Love")
        self.track(love)
        self.matched()
        self.matched(FIRST_LOVE_MBID, FIRST_LOVE, apple_title="First Love (Remastered 2014)")

        counts = self.apply(limit=1)

        self.assertEqual(counts["moved"], 1)
        self.assertEqual(Item.objects.get(pk=sos.pk).source, "apple_music")
        self.assertEqual(Item.objects.get(pk=love.pk).source, "musicbrainz")

    def test_tier_three_moves_only_albums_without_user_data(self):
        logged = self.sos_item()
        self.track(logged)
        self.matched(tier=3, method=MusicAlbumLink.Method.SEARCH)
        listed = self.musicbrainz_item(FIRST_LOVE_MBID, "First Love")
        self.matched(FIRST_LOVE_MBID, FIRST_LOVE, tier=3, method=MusicAlbumLink.Method.SEARCH, apple_title="First Love")

        self.apply(family("first_love"))

        self.assertEqual(Item.objects.get(pk=logged.pk).source, "musicbrainz")
        self.assertEqual(Item.objects.get(pk=listed.pk).source, "apple_music")

    def test_a_review_row_waits_for_a_pick(self):
        self.track(self.sos_item())
        self.matched(tier=3, status=MusicAlbumLink.Status.REVIEW)

        counts = self.apply()

        self.assertEqual(counts, {"repair: albums": 0, "repair: rows moved": 0, "repair: users kept on both": 0})
        self.assertEqual(MusicAlbumLink.objects.get().status, "review")

    def test_an_album_apple_music_no_longer_has_waits(self):
        item = self.sos_item()
        self.track(item)
        self.matched()

        counts = self.apply((200, {"data": []}))

        self.assertEqual(counts["skipped: not on Apple Music"], 1)
        self.assertEqual(Item.objects.get(pk=item.pk).source, "musicbrainz")

    def test_an_album_first_added_from_an_apple_page_takes_the_users_rows(self):
        # The friend logged SOS from its Apple page; the listener has it from before.
        apple = apple_item(SOS)
        self.track(apple, self.other)
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        old = self.sos_item()
        rows = self.all_user_rows(old)
        self.matched(apple_album_id=SOS_CLEAN)

        counts = self.apply()

        self.assertEqual(counts["joined"], 1)
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.item_id, apple.pk)
        self.assertEqual(Music.objects.get(user=self.other).item_id, apple.pk)
        old.refresh_from_db()
        self.assertEqual((old.source, old.media_id), ("musicbrainz", SOS_MBID))
        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.item_id, link.apple_album_id), ("applied", apple.pk, SOS))
        self.assertEqual(
            music_catalog.canonical_music_ref("musicbrainz", "music", SOS_MBID),
            ("apple_music", "music", SOS),
        )

    def test_a_featured_only_album_joins_an_existing_apple_item(self):
        apple = apple_item(SOS)
        old = self.sos_item()
        featured = CustomList.objects.create(owner=self.user, name="Top albums", is_featured=True)
        featured_row = CustomListItem.objects.create(item=old, custom_list=featured)
        self.matched(tier=3, method=MusicAlbumLink.Method.SEARCH)

        self.apply()

        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.item_id, link.moved_rows), ("applied", apple.pk, []))
        # The next featured sync moves its list rows.
        featured_row.refresh_from_db()
        self.assertEqual(featured_row.item_id, old.pk)

    def test_users_with_rows_on_both_keep_theirs_and_revert_puts_the_rest_back(self):
        apple = apple_item(SOS)
        self.track(apple, self.other)
        old = self.sos_item()
        mine = self.all_user_rows(old)
        theirs = DiaryEntry.objects.create(user=self.other, item=old, consumed_at=timezone.now())
        self.matched()

        self.apply()

        for row in mine:
            row.refresh_from_db()
            self.assertEqual(row.item_id, apple.pk)
        theirs.refresh_from_db()
        self.assertEqual(theirs.item_id, old.pk)
        link = MusicAlbumLink.objects.get()
        self.assertEqual(link.previous["kept_users"], [self.other.pk])

        counts = music_move.revert(release_group_mbid=SOS_MBID)

        self.assertEqual(counts, {"joined album reverted": 1})
        for row in mine:
            row.refresh_from_db()
            self.assertEqual(row.item_id, old.pk)
        self.user.refresh_from_db()
        self.assertEqual(self.user.hof_music_id, old.pk)
        self.assertEqual(MusicAlbumLink.objects.get().status, "reverted")

    def test_two_musicbrainz_albums_that_are_one_apple_album(self):
        # The one with more user rows is re-keyed; the other's users join it.
        first = self.sos_item()
        self.all_user_rows(first)
        second = self.musicbrainz_item(CTRL_MBID, "SOS")
        joined = self.track(second, self.other)
        self.matched()
        self.matched(CTRL_MBID)

        counts = self.apply()

        self.assertEqual((counts["moved"], counts["joined"]), (1, 1))
        self.assertEqual(Item.objects.get(pk=first.pk).source, "apple_music")
        joined.refresh_from_db()
        self.assertEqual(joined.item_id, first.pk)
        self.assertEqual(MusicAlbumLink.objects.get(release_group_mbid=CTRL_MBID).item_id, first.pk)

    def test_a_second_run_changes_nothing(self):
        item = self.sos_item()
        self.all_user_rows(item)
        self.matched()
        self.apply()
        rows = self.user_rows()
        link = MusicAlbumLink.objects.values().get()

        counts = self.apply((200, {"data": []}))

        self.assertNotIn("moved", counts)
        self.assertEqual(self.user_rows(), rows)
        self.assertEqual(MusicAlbumLink.objects.values().get(), link)

    def test_the_post_check_names_a_moved_album_that_lost_its_ref(self):
        self.track(self.sos_item())
        self.matched()
        self.apply()
        link = MusicAlbumLink.objects.get()
        self.assertEqual(music_move.post_check([link.pk]), [])

        Item.objects.filter(pk=link.item_id).update(media_id=SOS_CLEAN)

        self.assertEqual(music_move.post_check([link.pk]), [f"{SOS_MBID}: Item {link.item_id} isn't the Apple album"])


class RevertTests(BackfillTestCase):
    def test_revert_all_is_refused_while_the_catalog_is_apple_music(self):
        with (
            override_settings(MUSIC_CATALOG_SOURCE="apple_music"),
            self.assertRaisesMessage(music_move.MoveError, "set MUSIC_CATALOG_SOURCE=musicbrainz first"),
        ):
            music_move.revert(everything=True)

    def test_revert_restores_the_album_and_its_cover_pick(self):
        item = self.sos_item()
        self.track(item)
        CustomPosterPreference.objects.create(user=self.user, item=item, custom_image_url=SOS_PICK)
        state = Item.objects.values().get(pk=item.pk)
        self.matched()
        self.apply()

        with self.captureOnCommitCallbacks(execute=True):
            counts = music_move.revert(everything=True)

        self.assertEqual(counts, {"reverted": 1})
        self.assertEqual(Item.objects.values().get(pk=item.pk), state)
        item.refresh_from_db()
        self.assertEqual(sorted(item.filter_facets.values_list("value", flat=True)), SOS_GENRES)
        self.assertTrue(curation.music_picks_apply(item))
        self.assertEqual(custom_poster_url_for_user(self.user, None, item=item), SOS_PICK)
        # Rule 1: the Apple refs it was known by reach the MusicBrainz album again.
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )
        self.assertEqual(music_move.revert(everything=True), {})

    def test_revert_all_re_keys_an_album_first_added_from_an_apple_page_through_its_bridge(self):
        apple = apple_item(SOS, image="https://is1-ssl.mzstatic.com/image/thumb/sos/600x600bb.jpg")
        entry = DiaryEntry.objects.create(user=self.user, item=apple, consumed_at=timezone.now())
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS, release_group_mbid=SOS_MBID, same_title=True)
        lana = apple_item(LANA, title="SOS Deluxe: LANA")
        AppleAlbum.objects.create(apple_album_id=LANA, canonical_id=LANA, release_group_mbid=SOS_MBID, same_title=False)
        musicbrainz = self.musicbrainz((200, fixture("musicbrainz_release_group_sos.json")))

        counts = music_move.revert(everything=True, clock=self.clock.monotonic, sleep=self.clock.sleep)

        self.assertEqual(counts, {"Apple album re-keyed to MusicBrainz": 1, "left on Apple Music: no bridge": 1})
        self.assertEqual(len(musicbrainz.requests), 1)
        apple.refresh_from_db()
        self.assertEqual((apple.source, apple.media_id, apple.title, apple.image), ("musicbrainz", SOS_MBID, "SOS", SOS_COVER))
        entry.refresh_from_db()
        self.assertEqual(entry.item_id, apple.pk)
        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.method, link.apple_album_id, link.item_id), ("reverted", "bridge", SOS, apple.pk))
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )
        lana.refresh_from_db()
        self.assertEqual(lana.source, "apple_music")

    def test_an_apple_page_album_joins_the_musicbrainz_album_that_joined_it(self):
        apple = apple_item(SOS)
        friend = self.track(apple, self.other)
        old = self.sos_item()
        self.track(old)
        self.matched()
        self.apply()

        counts = music_move.revert(everything=True)

        self.assertEqual(counts, {"joined album reverted": 1, "Apple album joined its MusicBrainz album": 1})
        friend.refresh_from_db()
        self.assertEqual(friend.item_id, old.pk)
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )

    def test_a_manual_pick_for_an_apple_page_album_reverts_by_hand(self):
        apple = apple_item(SOS)
        self.track(apple)
        self.matched(method=MusicAlbumLink.Method.MANUAL, tier=None)
        self.musicbrainz((200, fixture("musicbrainz_release_group_sos.json")))

        counts = music_move.revert(release_group_mbid=SOS_MBID, clock=self.clock.monotonic, sleep=self.clock.sleep)

        self.assertEqual(counts, {"Apple album re-keyed to MusicBrainz": 1})
        apple.refresh_from_db()
        self.assertEqual((apple.source, apple.media_id), ("musicbrainz", SOS_MBID))
        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.method), ("reverted", "manual"))

    def test_a_stray_that_took_the_old_id_gives_it_back(self):
        item = self.sos_item()
        mine = self.track(item)
        state = Item.objects.values().get(pk=item.pk)
        self.matched()
        self.apply()
        stray = self.musicbrainz_item()

        counts = music_move.revert(release_group_mbid=SOS_MBID)

        self.assertEqual(counts, {"reverted, with a stray's rows": 1})
        self.assertEqual(Item.objects.values().get(pk=item.pk), state)
        mine.refresh_from_db()
        self.assertEqual(mine.item_id, item.pk)
        stray.refresh_from_db()
        self.assertEqual((stray.source, stray.media_id), ("apple_music", SOS))
        self.assertEqual(MusicAlbumLink.objects.get().item_id, item.pk)
        for ref in (("apple_music", "music", SOS), ("musicbrainz", "music", SOS_MBID)):
            self.assertEqual(music_catalog.canonical_music_ref(*ref), ("musicbrainz", "music", SOS_MBID))
        # The empty stray isn't an album for --all to give back.
        self.assertEqual(music_move.revert(everything=True), {})

    def test_after_repair_revert_gives_the_album_back_with_the_strays_rows(self):
        item = self.sos_item()
        mine = self.track(item)
        CustomPosterPreference.objects.create(user=self.user, item=item, custom_image_url=SOS_PICK)
        state = Item.objects.values().get(pk=item.pk)
        self.matched()
        self.apply()
        stray = self.musicbrainz_item()
        theirs = DiaryEntry.objects.create(user=self.other, item=stray, consumed_at=timezone.now())
        self.assertEqual(music_move.repair()["rows moved"], 1)

        with self.captureOnCommitCallbacks(execute=True):
            counts = music_move.revert(everything=True)

        self.assertEqual(counts, {"reverted, with a stray's rows": 1})
        self.assertEqual(Item.objects.values().get(pk=item.pk), state)
        for row in (mine, theirs):
            row.refresh_from_db()
            self.assertEqual(row.item_id, item.pk)
        item.refresh_from_db()
        self.assertEqual(custom_poster_url_for_user(self.user, None, item=item), SOS_PICK)
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )
        # The saved fields stay on the row.
        self.assertEqual(MusicAlbumLink.objects.get().previous["image"], SOS_COVER)
        self.assertEqual(music_move.revert(everything=True), {})

    def test_a_stray_with_rows_of_a_user_on_both_gives_the_album_back_and_keeps_them(self):
        item = self.sos_item()
        mine = self.track(item)
        self.matched()
        self.apply()
        # A web page opened before the move liked the old ref.
        stray = self.musicbrainz_item()
        like = MediaLike.objects.create(user=self.user, item=stray)

        counts = music_move.revert(everything=True)

        self.assertEqual(counts, {music_move.STRAY_KEPT: 1})
        item.refresh_from_db()
        self.assertEqual((item.source, item.media_id, item.title), ("musicbrainz", SOS_MBID, "SOS"))
        mine.refresh_from_db()
        self.assertEqual(mine.item_id, item.pk)
        # The like stays on the stray, which took the Apple ID and keeps its ref.
        like.refresh_from_db()
        self.assertEqual(like.item_id, stray.pk)
        stray.refresh_from_db()
        self.assertEqual((stray.source, stray.media_id), ("apple_music", SOS))
        self.assertEqual(music_catalog.canonical_music_ref("apple_music", "music", SOS), ("apple_music", "music", SOS))
        self.assertEqual(
            music_catalog.canonical_music_ref("musicbrainz", "music", SOS_MBID),
            ("musicbrainz", "music", SOS_MBID),
        )
        self.apple((200, {"data": []}), background=True)
        result = music_move.check()
        self.assertEqual((result["albums kept on both"], result["users kept on both"]), (1, 1))
        self.assertEqual(music_move.report()["users kept on both"], 1)

        # Once the like is gone, the next run sends the Apple ref to the album too.
        like.delete()
        self.assertEqual(music_move.revert(everything=True), {"Apple album joined its MusicBrainz album": 1})
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )
        self.assertEqual(music_move.report()["users kept on both"], 0)
        self.assertEqual(music_move.revert(everything=True), {})

    def test_reverting_one_joined_album_leaves_the_album_it_joined_its_ref(self):
        first = self.sos_item()
        self.all_user_rows(first)
        second = self.musicbrainz_item(CTRL_MBID, "SOS")
        theirs = self.track(second, self.other)
        self.matched()
        self.matched(CTRL_MBID)
        self.apply()

        counts = music_move.revert(release_group_mbid=CTRL_MBID)

        self.assertEqual(counts, {"joined album reverted": 1})
        theirs.refresh_from_db()
        self.assertEqual(theirs.item_id, second.pk)
        self.assertEqual(MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID).status, "applied")
        for ref in (("apple_music", "music", SOS), ("musicbrainz", "music", SOS_MBID)):
            self.assertEqual(music_catalog.canonical_music_ref(*ref), ("apple_music", "music", SOS))
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get(f"/api/v1/tracking/apple_music/music/{SOS}/").status_code, 200)

    def test_reverting_an_album_that_joined_an_apple_page_album_leaves_that_album_its_ref(self):
        apple = apple_item(SOS)
        friend = self.track(apple, self.other)
        old = self.sos_item()
        self.track(old)
        self.matched()
        self.apply()

        self.assertEqual(music_move.revert(release_group_mbid=SOS_MBID), {"joined album reverted": 1})

        friend.refresh_from_db()
        self.assertEqual(friend.item_id, apple.pk)
        self.assertEqual(music_catalog.canonical_music_ref("apple_music", "music", SOS), ("apple_music", "music", SOS))

    def test_revert_all_sends_the_apple_ref_to_the_re_keyed_album_not_the_one_that_joined_it(self):
        # The re-keyed album's release group sorts after the joined one's.
        rekeyed = self.musicbrainz_item(CTRL_MBID, "SOS")
        self.all_user_rows(rekeyed)
        joined = self.sos_item()
        self.track(joined, self.other)
        self.matched()
        self.matched(CTRL_MBID)
        self.assertEqual(self.apply()["moved"], 1)
        self.assertEqual(Item.objects.get(pk=rekeyed.pk).source, "apple_music")

        counts = music_move.revert(everything=True)

        self.assertEqual(counts, {"joined album reverted": 1, "reverted": 1})
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", CTRL_MBID),
        )

    def test_revert_all_keeps_a_reverted_albums_row_when_an_apple_page_album_names_it_too(self):
        item = self.sos_item()
        self.track(item)
        self.matched()
        self.apply()
        # Another listing of SOS, logged from its Apple page, whose bridge names SOS.
        listing = "1999999999"
        page_album = apple_item(listing)
        entry = DiaryEntry.objects.create(user=self.other, item=page_album, consumed_at=timezone.now())
        AppleAlbum.objects.create(apple_album_id=listing, canonical_id=listing, release_group_mbid=SOS_MBID, same_title=True)
        self.musicbrainz()  # any request fails the test

        counts = music_move.revert(everything=True, clock=self.clock.monotonic, sleep=self.clock.sleep)

        self.assertEqual(
            counts,
            {"reverted": 1, "left on Apple Music: its MusicBrainz album was reverted already": 1},
        )
        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.method, link.apple_album_id), ("reverted", "upc_exact", SOS))
        self.assertEqual(link.previous["title"], "SOS")
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", listing),
            ("apple_music", "music", listing),
        )
        entry.refresh_from_db()
        self.assertEqual(entry.item_id, page_album.pk)

    def test_an_apple_page_album_a_user_has_rows_on_both_keeps_its_ref(self):
        # The listener logged SOS from its Apple page and tracks the MusicBrainz album.
        apple = apple_item(SOS)
        entry = DiaryEntry.objects.create(user=self.user, item=apple, consumed_at=timezone.now())
        old = self.sos_item()
        mine = self.track(old)
        self.matched()
        self.apply()

        counts = music_move.revert(everything=True)

        self.assertEqual(counts, {"joined album reverted": 1, music_move.KEPT_ON_BOTH: 1})
        entry.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((entry.item_id, mine.item_id), (apple.pk, old.pk))
        self.assertEqual(music_catalog.canonical_music_ref("apple_music", "music", SOS), ("apple_music", "music", SOS))
        self.assertEqual(music_move.report()["users kept on both"], 1)

    def test_through_its_bridge_too_an_apple_page_album_a_user_has_rows_on_both_keeps_its_ref(self):
        apple = apple_item(SOS)
        entry = DiaryEntry.objects.create(user=self.user, item=apple, consumed_at=timezone.now())
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS, release_group_mbid=SOS_MBID, same_title=True)
        self.track(self.sos_item())

        counts = music_move.revert(everything=True)

        self.assertEqual(counts, {music_move.KEPT_ON_BOTH: 1})
        entry.refresh_from_db()
        self.assertEqual(entry.item_id, apple.pk)
        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.method, link.apple_album_id), ("reverted", "bridge", ""))
        self.assertEqual(music_catalog.canonical_music_ref("apple_music", "music", SOS), ("apple_music", "music", SOS))
        self.assertEqual(music_move.report()["users kept on both"], 1)

    def test_reverting_a_manual_pick_leaves_an_apple_album_a_moved_album_holds(self):
        item = self.sos_item()
        mine = self.track(item)
        self.matched()
        self.apply()
        ctrl = self.musicbrainz_item(CTRL_MBID, "Ctrl")
        theirs = self.track(ctrl, self.other)
        # A mistaken pick: Ctrl is SOS.
        self.matched(CTRL_MBID, method=MusicAlbumLink.Method.MANUAL, tier=None)

        counts = music_move.revert(release_group_mbid=CTRL_MBID)

        self.assertEqual(counts, {"nothing to revert: a moved album holds its Apple album": 1})
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual((mine.item_id, theirs.item_id), (item.pk, ctrl.pk))
        item.refresh_from_db()
        self.assertEqual((item.source, item.media_id), ("apple_music", SOS))
        self.assertEqual(MusicAlbumLink.objects.get(release_group_mbid=SOS_MBID).status, "applied")
        self.assertEqual(MusicAlbumLink.objects.get(release_group_mbid=CTRL_MBID).status, "matched")
        self.assertEqual(music_catalog.canonical_music_ref("apple_music", "music", SOS), ("apple_music", "music", SOS))

    def test_revert_needs_an_album(self):
        with self.assertRaisesMessage(music_move.MoveError, "No link row"):
            music_move.revert(release_group_mbid=SOS_MBID)
        with self.assertRaisesMessage(music_move.MoveError, "Pass --mbid or --all"):
            music_move.revert()


class CheckAndRepairTests(BackfillTestCase):
    def moved_sos(self):
        item = self.sos_item()
        self.track(item)
        self.matched()
        self.apply()
        return item

    def test_check_tests_the_key_and_counts(self):
        self.moved_sos()
        genre_album(SOS_MBID, "SOS", "SZA", "r&b")
        for number in range(25):
            genre_album(f"00000000-0000-0000-0000-{number:012d}", f"Album {number}", "Someone", "soul")
        apple = self.apple((200, {"data": []}), background=True)

        result = music_move.check()

        self.assertEqual(result["Apple Music key"], "works")
        self.assertEqual(query(apple.requests[0])["ids"], music_move.KEY_CHECK_ALBUM)
        self.assertEqual(result["link rows applied"], 1)
        self.assertEqual((result["strays"], result["rows in other tables"]), (0, 0))
        self.assertEqual(result["Apple Music albums not from the move"], 0)
        self.assertEqual((result["genre pages"], result["genre pages under the gate"]), (2, 1))
        self.assertEqual(result["genre soul"], "0 of 25 linked")

    def test_check_names_a_failing_or_missing_key(self):
        self.apple((401, fixture("error_401.json")["body"]), background=True)
        self.assertEqual(music_move.check()["Apple Music key"], "failed (HTTP 401)")
        with override_settings(APPLE_MUSIC_TEAM_ID=""):
            self.assertEqual(music_move.check()["Apple Music key"], "not configured")

    def test_check_counts_rows_in_a_table_the_move_doesnt_know(self):
        item = self.moved_sos()
        stray = self.musicbrainz_item()
        BasicMedia.objects.bulk_create([BasicMedia(user=self.user, item=stray)])
        self.apple((200, {"data": []}), background=True)

        result = music_move.check()

        self.assertEqual(result["rows in other tables"], 1)
        self.assertEqual(result["rows in app.basicmedia.item"], 1)
        self.assertEqual(result["strays"], 0)
        self.assertNotEqual(stray.pk, item.pk)

    def test_repair_joins_a_strays_rows_and_they_stay_with_the_album_after_revert(self):
        item = self.moved_sos()
        stray = self.musicbrainz_item()
        theirs = self.track(stray, self.other)
        mine = DiaryEntry.objects.create(user=self.user, item=stray, consumed_at=timezone.now())
        self.apple((200, {"data": []}), background=True)
        self.assertEqual(music_move.check()["strays"], 1)

        self.apple((200, {"data": []}), background=True)
        result = music_move.check(repair_strays=True)

        self.assertEqual((result["repair: albums"], result["repair: rows moved"]), (1, 1))
        self.assertEqual((result["strays"], result["albums kept on both"], result["users kept on both"]), (0, 1, 1))
        theirs.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((theirs.item_id, mine.item_id), (item.pk, stray.pk))

        self.assertEqual(music_move.revert(release_group_mbid=SOS_MBID), {music_move.STRAY_KEPT: 1})

        # The album is MusicBrainz's again with the friend's rows; the listener's log
        # stays on the stray, which took the Apple ID, and is listed.
        theirs.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((theirs.item_id, mine.item_id), (item.pk, stray.pk))
        self.assertEqual(Item.objects.get(pk=item.pk).source, "musicbrainz")
        self.apple((200, {"data": []}), background=True)
        result = music_move.check()
        self.assertEqual((result["albums kept on both"], result["users kept on both"]), (1, 1))


class ReportTests(BackfillTestCase):
    def test_report_writes_every_album_as_csv_and_counts_to_the_console(self):
        sos = self.sos_item()
        self.track(sos)
        self.matched()
        self.apply()
        no_fences = self.musicbrainz_item(NO_FENCES_MBID, "No Fences")
        self.track(no_fences)
        MusicAlbumLink.objects.create(release_group_mbid=NO_FENCES_MBID, status="unmatched", checked_at=timezone.now())
        apple_item(LANA, title="SOS Deluxe: LANA")
        out, err = io.StringIO(), io.StringIO()

        call_command("music_catalog", "report", "--csv", "-", stdout=out, stderr=err)

        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual([row["group"] for row in rows], ["group 1", "moved", "apple page"])
        self.assertEqual((rows[0]["title"], rows[0]["status"], rows[0]["users"]), ("No Fences", "unmatched", "1"))
        self.assertEqual((rows[1]["apple_album_id"], rows[1]["status"]), (SOS, "applied"))
        self.assertEqual(rows[2]["note"], "no bridge")
        console = err.getvalue()
        self.assertIn("group 1 unmatched: 1", console)
        self.assertIn("moved: 1", console)
        self.assertIn("Apple Music albums with no bridge: 1", console)
        self.assertNotIn("No Fences", console)

    def test_a_report_file_is_readable_by_its_owner_only(self):
        self.track(self.sos_item())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.csv"
            out = io.StringIO()

            call_command("music_catalog", "report", "--csv", str(path), stdout=out)

            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertIn("SOS", path.read_text())
        self.assertIn("group 1 no row: 1", out.getvalue())


class LinkTests(BackfillTestCase):
    def test_link_records_a_manual_pick_as_its_listings_identity(self):
        item = self.sos_item()
        self.track(item)
        out = io.StringIO()
        # Apple Music answers for the clean twin, whose family is the explicit album.
        explicit = fixture("backfill_family_sos.json")["data"][0]
        clean = explicit["views"]["other-versions"]["data"][0]
        clean["views"] = {"other-versions": {"data": [{key: explicit[key] for key in ("id", "type", "attributes")}]}}
        self.apple((200, {"data": [clean]}), background=True)

        call_command("music_catalog", "link", "--mbid", SOS_MBID, "--apple", SOS_CLEAN, stdout=out)

        link = MusicAlbumLink.objects.get()
        self.assertEqual((link.status, link.method, link.tier, link.apple_album_id), ("matched", "manual", None, SOS))
        self.assertEqual(link.genres, SOS_GENRES)
        self.assertIn(f"apple album: {SOS}", out.getvalue())

    def test_link_refuses_a_moved_album_and_bad_ids(self):
        self.track(self.sos_item())
        self.matched()
        self.apply()
        with self.assertRaisesMessage(CommandError, "has moved: revert it first"):
            call_command("music_catalog", "link", "--mbid", SOS_MBID, "--apple", SOS)
        with self.assertRaisesMessage(CommandError, "Not a MusicBrainz release group ID"):
            call_command("music_catalog", "link", "--mbid", "sos", "--apple", SOS)
        with self.assertRaisesMessage(CommandError, "Not an Apple Music album ID"):
            call_command("music_catalog", "link", "--mbid", CTRL_MBID, "--apple", "sos")


class CommandTests(BackfillTestCase):
    def test_each_step_prints_its_counts(self):
        self.track(self.sos_item())
        self.musicbrainz(*browse("sos"))
        self.apple(*sos_map_answers(), family("sos"), background=True)
        out = io.StringIO()

        with patch("app.music_move.time.sleep"):
            call_command("music_catalog", "map", stdout=out)
        call_command("music_catalog", "apply", "--limit", "5", stdout=out)
        call_command("music_catalog", "revert", "--all", stdout=out)

        lines = out.getvalue().splitlines()
        self.assertIn("group 1: matched tier 1: 1", lines)
        self.assertIn("moved: 1", lines)
        self.assertIn("reverted: 1", lines)

    def test_a_step_that_cant_run_is_a_command_error(self):
        with override_settings(APPLE_MUSIC_KEY_ID=""), self.assertRaisesMessage(CommandError, "isn't configured"):
            call_command("music_catalog", "apply")


class FallbackEndToEndTests(BackfillTestCase):
    """Move SOS, switch the catalog to Apple Music and back, revert: as it started."""

    def musicbrainz_page(self):
        return {
            "media_id": SOS_MBID,
            "source": "musicbrainz",
            "media_type": "music",
            "title": "SOS",
            "image": SOS_COVER,
            "max_progress": 1,
            "release_date": "2022-12-09",
            "genres": SOS_GENRES,
            "details": {"first_release_date": "2022-12-09"},
        }

    def pages(self):
        """Return the MusicBrainz album page and search summaries the user sees."""
        cache.clear()
        search = {"page": 1, "total_results": 1, "total_pages": 1, "results": [self.musicbrainz_page()]}
        with (
            patch("app.providers.services.musicbrainz.music", return_value=self.musicbrainz_page()),
            patch("app.providers.services.musicbrainz.search", return_value=search),
        ):
            detail = self.client.get(f"/api/v1/media/musicbrainz/music/{SOS_MBID}/").json()
            summaries = media_service.search_media(media_type="music", query="sos", source="musicbrainz", user=self.user)
        return detail, summaries

    def item_state(self, item):
        return (
            Item.objects.values().get(pk=item.pk),
            sorted(ItemFilterFacet.objects.filter(item=item).values_list("facet_type", "value")),
        )

    def test_apply_switch_back_and_revert_all_restore_the_start(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        item = self.sos_item()
        self.all_user_rows(item)
        DiaryEntry.objects.create(user=self.other, item=item, consumed_at=timezone.now())
        CustomPosterPreference.objects.create(user=self.user, item=item, custom_image_url=SOS_PICK)
        start_pages = self.pages()
        start_item = self.item_state(item)
        start_rows = self.user_rows()
        self.assertEqual(start_pages[0]["ref"]["item_id"], item.pk)

        self.musicbrainz(*browse("sos"))
        self.apple(*sos_map_answers(), family("sos"), background=True)
        self.map()
        music_move.apply()
        self.assertEqual(Item.objects.get(pk=item.pk).source, "apple_music")
        self.assertEqual(self.user_rows(), start_rows)

        with override_settings(MUSIC_CATALOG_SOURCE="apple_music"):
            # With the catalog switched, the old ref opens the Apple album, and the
            # fallback waits for the switch back.
            self.assertEqual(
                music_catalog.canonical_music_ref("musicbrainz", "music", SOS_MBID),
                ("apple_music", "music", SOS),
            )
            with self.assertRaises(music_move.MoveError):
                music_move.revert(everything=True)

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(music_move.revert(everything=True), {"reverted": 1})

        self.assertEqual(self.item_state(item), start_item)
        self.assertEqual(self.user_rows(), start_rows)
        self.assertEqual(self.pages(), start_pages)
        self.assertEqual(
            music_catalog.canonical_music_ref("apple_music", "music", SOS),
            ("musicbrainz", "music", SOS_MBID),
        )
