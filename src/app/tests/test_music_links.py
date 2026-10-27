from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from django_celery_beat.schedulers import ModelEntry

from api.services import media as media_service
from app import music_links
from app.models import AppleAlbum, Item, MediaTypes, MusicAlbumLink, MusicGenreAlbum, Sources
from app.music_catalog import store_wikidata_apple_ids
from app.providers import apple_music, services
from app.signals import create_task_result_on_publish
from app.tasks import fill_apple_album_bridges, link_genre_albums_to_apple
from app.tests.providers.test_apple_music import (
    AppleMusicTestCase,
    FakeApple,
    apple_item,
    fixture,
    genre_facets,
    page,
    query,
)
from config.celery import app as celery_app

SOS = "1657869377"
SOS_CLEAN = "1658650093"
LANA = "1794644675"
THRILLER = "269572838"
THRILLER_40 = "1641780996"
SOS_GROUP = "1646286a-d0ad-4288-bfab-34b0fb7b22c1"
THRILLER_GROUP = "f32fab67-77dd-3937-addc-9062e28e4c37"
SOS_GENRES = ["contemporary r&b", "pop", "r&b", "soul"]
SOS_SPOTIFY = "https://open.spotify.com/album/07w0rG5TETcyihsEIZR3qG"
LANA_SPOTIFY = "https://open.spotify.com/album/1Q5WON8dw2JFLP5JVQXu1e"


class FakeClock:
    """A monotonic clock that moves only when slept on, or by ``step`` per request."""

    def __init__(self, step=0):
        self.now = 1000.0
        self.step = step
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def queue(*album_ids):
    services.redis_db.sadd(apple_music.bridge_queue_key(), *album_ids)


def queued():
    return {member.decode() for member in services.redis_db.smembers(apple_music.bridge_queue_key())}


def daily_key():
    return apple_music.redis_key(music_links.BRIDGE_DAILY_KEY.format(day=timezone.localdate().isoformat()))


def apple_album(album_id, upc="", **fields):
    return AppleAlbum.objects.create(apple_album_id=album_id, canonical_id=fields.pop("canonical_id", album_id), upc=upc, **fields)


def album_batch(*names):
    """Return an albums?ids= answer holding the album of each recorded page."""
    return {"data": [fixture(name)["data"][0] for name in names]}


def sos_answers():
    return (
        (200, fixture("musicbrainz_barcode_sos.json")),
        (200, fixture("musicbrainz_release_sos.json")),
        (200, fixture("musicbrainz_release_group_sos.json")),
    )


def lana_answers():
    return (
        (200, fixture("musicbrainz_barcode_lana.json")),
        (200, fixture("musicbrainz_release_lana.json")),
        (200, fixture("musicbrainz_release_group_lana.json")),
    )


def genre_album(mbid, title, artist, *genres, page_views=0):
    for genre in genres:
        MusicGenreAlbum.objects.create(
            genre=genre,
            release_group_mbid=mbid,
            title=title,
            artist=artist,
            release_date="2022-12-09",
            page_views=page_views,
            listeners=1,
            listens=1,
        )


class MusicLinksTestCase(AppleMusicTestCase):
    def setUp(self):
        super().setUp()
        services.redis_db.delete(apple_music.bridge_queue_key(), daily_key())

    def fill(self, clock=None):
        """Run the bridge task on a fake clock, so its pauses take no time."""
        clock = clock or FakeClock()
        return music_links.fill_bridges(clock=clock.monotonic, sleep=clock.sleep)

    def musicbrainz(self, *answers, clock=None):
        """Answer MusicBrainz requests in turn; with ``clock``, note when each was sent."""
        fake = FakeApple(*answers)
        fake.times = []

        def send(request, **options):
            fake.times.append(clock.now if clock else None)
            if clock is not None:
                clock.now += clock.step
            return fake(request, **options)

        patcher = patch.object(services.musicbrainz_session, "send", send)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class BridgeQueueTests(MusicLinksTestCase):
    def test_an_album_page_queues_its_missing_bridge_once(self):
        self.apple((200, page("page_sos.json")))

        apple_music.music(SOS)
        apple_music.music(SOS)  # the cached page: the set holds it once

        self.assertEqual(queued(), {SOS})

    def test_a_fresh_bridge_isnt_queued_and_a_month_old_one_is(self):
        self.apple((200, page("page_sos.json")))
        apple_music.music(SOS)
        services.redis_db.delete(apple_music.bridge_queue_key())

        AppleAlbum.objects.filter(apple_album_id=SOS).update(bridge_checked_at=timezone.now() - timedelta(days=29))
        apple_music.music(SOS)
        self.assertEqual(queued(), set())

        AppleAlbum.objects.filter(apple_album_id=SOS).update(bridge_checked_at=timezone.now() - timedelta(days=31))
        apple_music.music(SOS)
        self.assertEqual(queued(), {SOS})


class FillBridgesTests(MusicLinksTestCase):
    def test_the_barcode_finds_the_release_group_genres_date_and_spotify(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)
        apple = self.apple((200, album_batch("page_sos.json")), background=True)
        musicbrainz = self.musicbrainz(*sos_answers())
        clock = FakeClock()

        result = self.fill(clock)

        self.assertEqual(result, {"filled": 1, "requests": 3, "queued": 0, "stopped": ""})
        row = AppleAlbum.objects.get(apple_album_id=SOS)
        self.assertEqual(row.release_group_mbid, SOS_GROUP)
        self.assertTrue(row.same_title)
        self.assertEqual(row.first_release_date, "2022-12-09")
        self.assertEqual(row.genres, SOS_GENRES)
        self.assertEqual((row.spotify_url, row.spotify_exact), (SOS_SPOTIFY, True))
        self.assertIsNotNone(row.bridge_checked_at)
        self.assertEqual(query(apple.requests[0])["ids"], SOS)
        self.assertEqual(
            [urlsplit(request.url).path for request in musicbrainz.requests],
            [
                "/ws/2/release",
                "/ws/2/release/b82458a3-7d3a-495f-861f-8698731c4dd0",
                f"/ws/2/release-group/{SOS_GROUP}",
            ],
        )
        self.assertEqual(query(musicbrainz.requests[1])["inc"], "url-rels+release-groups")
        self.assertEqual(query(musicbrainz.requests[2])["inc"], "genres")

    def test_the_barcode_is_asked_for_as_12_and_13_digits(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        musicbrainz = self.musicbrainz(*sos_answers())

        self.fill()

        self.assertEqual(query(musicbrainz.requests[0])["query"], "barcode:(196589564931 OR 0196589564931)")

    def test_requests_are_three_seconds_apart(self):
        apple_album(SOS, upc="196589564931")
        apple_album(LANA, upc="196872790276")
        queue(SOS, LANA)
        self.apple((200, album_batch("page_sos.json", "page_lana.json")), background=True)
        clock = FakeClock()
        musicbrainz = self.musicbrainz(*sos_answers(), *lana_answers(), clock=clock)

        result = self.fill(clock)

        self.assertEqual((result["filled"], result["requests"]), (2, 6))
        gaps = [later - earlier for earlier, later in zip(musicbrainz.times, musicbrainz.times[1:], strict=False)]
        self.assertEqual(len(gaps), 5)
        self.assertTrue(all(gap >= music_links.BRIDGE_REQUEST_SPACING for gap in gaps), gaps)

    def test_a_deluxe_gets_its_release_groups_genres_but_not_its_date(self):
        apple_album(LANA, upc="196872790276")
        lana = apple_item(LANA, title="SOS Deluxe: LANA")
        genre_facets(lana, "r&b")
        queue(LANA)
        self.apple((200, album_batch("page_lana.json")), background=True)
        self.musicbrainz(*lana_answers())

        self.fill()

        row = AppleAlbum.objects.get(apple_album_id=LANA)
        self.assertEqual((row.release_group_mbid, row.same_title), (SOS_GROUP, False))
        self.assertEqual(row.genres, SOS_GENRES)
        self.assertEqual(row.spotify_url, LANA_SPOTIFY)
        lana.refresh_from_db()
        # SOS's 2022 date isn't LANA's: the release group has another title.
        self.assertEqual(lana.release_date, date(2024, 12, 20))
        self.assertEqual(
            list(lana.filter_facets.filter(facet_type="genre").values_list("value", flat=True)),
            SOS_GENRES,
        )

    def test_an_items_filters_take_musicbrainzs_genres_and_its_cached_pages_are_dropped(self):
        apple_album(SOS, upc="196589564931")
        sos = apple_item(SOS)
        genre_facets(sos, "alternative r&b", "contemporary r&b", "neo soul", "r&b", "soul")
        keys = [
            media_service._detail_cache_key(
                source=Sources.APPLE_MUSIC.value,
                media_type=MediaTypes.MUSIC.value,
                media_id=SOS,
                season_number=None,
                episode_number=None,
                include_music_enrichment=stage,
            )
            for stage in (False, True)
        ]
        for key in keys:
            cache.set(key, {"title": "SOS"})
            cache.set(f"{key}:validation", {"validated_at": 1})
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        self.musicbrainz(*sos_answers())

        self.fill()

        # Apple's broader genres give way to MusicBrainz's, even when they are fewer.
        self.assertEqual(
            sorted(sos.filter_facets.filter(facet_type="genre").values_list("value", flat=True)),
            SOS_GENRES,
        )
        sos.refresh_from_db()
        self.assertEqual(sos.release_date, date(2022, 12, 9))
        for key in keys:
            self.assertIsNone(cache.get(key))
            self.assertIsNone(cache.get(f"{key}:validation"))

    def test_an_item_with_fewer_genres_gets_musicbrainzs(self):
        apple_album(SOS, upc="196589564931")
        sos = apple_item(SOS)
        genre_facets(sos, "r&b")
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        self.musicbrainz(*sos_answers())

        self.fill()

        self.assertEqual(
            list(sos.filter_facets.filter(facet_type="genre").values_list("value", flat=True)),
            SOS_GENRES,
        )

    def test_a_link_row_fills_the_bridge_without_musicbrainz(self):
        apple_album(SOS, upc="196589564931")
        apple_album(SOS_CLEAN, canonical_id=SOS)
        MusicAlbumLink.objects.create(
            release_group_mbid=SOS_GROUP,
            apple_album_id=SOS_CLEAN,  # an alias of the album queued
            status=MusicAlbumLink.Status.MATCHED,
            method=MusicAlbumLink.Method.SEARCH,
            tier=3,
            genres=["r&b", "soul"],
        )
        genre_album(SOS_GROUP, "SOS", "SZA", "r&b")
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        self.musicbrainz()  # any request fails the test

        result = self.fill()

        self.assertEqual((result["filled"], result["requests"]), (1, 0))
        row = AppleAlbum.objects.get(apple_album_id=SOS)
        self.assertEqual((row.release_group_mbid, row.genres, row.same_title), (SOS_GROUP, ["r&b", "soul"], True))
        self.assertEqual((row.first_release_date, row.spotify_exact), ("2022-12-09", False))

    def test_an_album_musicbrainz_doesnt_know_is_checked_and_leaves_the_queue(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        musicbrainz = self.musicbrainz((200, {"releases": []}))

        result = self.fill()

        self.assertEqual(result, {"unmatched": 1, "requests": 1, "queued": 0, "stopped": ""})
        self.assertEqual(len(musicbrainz.requests), 1)
        row = AppleAlbum.objects.get(apple_album_id=SOS)
        self.assertIsNotNone(row.bridge_checked_at)
        self.assertEqual(row.release_group_mbid, "")

    def test_another_artists_release_isnt_the_album(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        releases = fixture("musicbrainz_barcode_sos.json")
        for release in releases["releases"]:
            release["artist-credit"] = [{"name": "Stide Prince", "artist": {"name": "Stide Prince"}}]
        self.musicbrainz((200, releases))

        result = self.fill()

        self.assertEqual((result["unmatched"], result["requests"]), (1, 1))

    def test_an_album_without_a_barcode_makes_no_request(self):
        apple_album(SOS)
        queue(SOS)
        without_barcode = album_batch("page_sos.json")
        del without_barcode["data"][0]["attributes"]["upc"]
        self.apple((200, without_barcode), background=True)
        self.musicbrainz()

        result = self.fill()

        self.assertEqual((result["unmatched"], result["requests"], result["queued"]), (1, 0, 0))

    def test_todays_cap_stops_the_run_and_keeps_the_album_queued(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)
        services.redis_db.set(daily_key(), music_links.BRIDGE_DAILY_REQUESTS - 1)
        self.apple((200, album_batch("page_sos.json")), background=True)
        musicbrainz = self.musicbrainz(*sos_answers())

        result = self.fill()

        self.assertEqual(result["stopped"], "today's MusicBrainz requests are spent")
        self.assertEqual(len(musicbrainz.requests), 1)
        self.assertEqual(queued(), {SOS})
        self.assertIsNone(AppleAlbum.objects.get(apple_album_id=SOS).bridge_checked_at)

    def test_a_run_works_a_minute_at_most(self):
        apple_album(SOS, upc="196589564931")
        apple_album(LANA, upc="196872790276")
        queue(SOS, LANA)
        self.apple((200, album_batch("page_sos.json", "page_lana.json")), background=True)
        clock = FakeClock(step=25)  # each MusicBrainz request takes 25 seconds
        self.musicbrainz(*sos_answers(), clock=clock)

        result = self.fill(clock)

        self.assertEqual((result["filled"], result["stopped"]), (1, "out of time"))
        self.assertEqual(queued(), {LANA})

    def test_a_musicbrainz_failure_ends_the_run_and_keeps_the_album_queued(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)
        self.apple((200, album_batch("page_sos.json")), background=True)
        self.musicbrainz((503, {"error": "busy"}))

        result = self.fill()

        self.assertEqual(result["stopped"], "a request failed")
        self.assertEqual(queued(), {SOS})
        self.assertIsNone(AppleAlbum.objects.get(apple_album_id=SOS).bridge_checked_at)

    def test_an_album_musicbrainz_always_fails_for_is_checked_and_the_next_gets_its_turn(self):
        # SOS's link row names a release group MusicBrainz has since removed.
        apple_album(SOS, upc="196589564931")
        apple_album(LANA, upc="196872790276")
        MusicAlbumLink.objects.create(
            release_group_mbid="00000000-0000-0000-0000-000000000404",
            apple_album_id=SOS,
            status=MusicAlbumLink.Status.MATCHED,
            method=MusicAlbumLink.Method.SEARCH,
            tier=3,
        )
        queue(SOS, LANA)
        self.apple((200, album_batch("page_sos.json", "page_lana.json")), background=True)
        self.musicbrainz((404, {"error": "Not Found"}), *lana_answers())

        result = self.fill()

        self.assertEqual(result, {"unmatched": 1, "filled": 1, "requests": 4, "queued": 0, "stopped": ""})
        sos = AppleAlbum.objects.get(apple_album_id=SOS)
        self.assertIsNotNone(sos.bridge_checked_at)
        self.assertEqual(sos.release_group_mbid, "")
        self.assertEqual(AppleAlbum.objects.get(apple_album_id=LANA).release_group_mbid, SOS_GROUP)

    def test_fresh_and_unknown_albums_leave_the_queue_without_a_request(self):
        apple_album(SOS, upc="196589564931", bridge_checked_at=timezone.now())
        queue(SOS, "999")
        self.apple(background=True)
        self.musicbrainz()

        result = self.fill()

        self.assertEqual(result, {"skipped": 2, "requests": 0, "queued": 0, "stopped": ""})

    @override_settings(APPLE_MUSIC_TEAM_ID="", APPLE_MUSIC_KEY_ID="", APPLE_MUSIC_PRIVATE_KEY="")
    def test_nothing_happens_without_the_apple_music_key(self):
        apple_album(SOS, upc="196589564931")
        queue(SOS)

        self.assertEqual(music_links.fill_bridges(), {"skipped": "Apple Music isn't configured"})
        self.assertEqual(queued(), {SOS})


class BarcodeFormTests(SimpleTestCase):
    def test_12_and_13_digits_without_apples_padding(self):
        self.assertEqual(music_links.barcode_forms("196589564931"), ["196589564931", "0196589564931"])
        self.assertEqual(music_links.barcode_forms("074643811224"), ["074643811224", "0074643811224"])
        self.assertEqual(music_links.barcode_forms("00881061707059"), ["881061707059", "0881061707059"])
        self.assertEqual(music_links.barcode_forms("5099902894829"), ["5099902894829"])

    def test_no_barcode_has_no_forms(self):
        for value in ("", None, "0000", "123456789012345"):
            with self.subTest(value=value):
                self.assertEqual(music_links.barcode_forms(value), [])


class MatchAlbumTests(AppleMusicTestCase):
    def test_the_album_by_its_title_and_artist(self):
        apple = self.apple(
            (200, fixture("search_albums_sza_sos_1.json")),
            (200, fixture("search_families_sza_sos_1.json")),
            background=True,
        )

        match = apple_music.match_album("SOS", "SZA")

        self.assertEqual((match["media_id"], match["title"]), (SOS, "SOS"))
        self.assertTrue(match["image"].startswith("https://"))
        self.assertEqual(query(apple.requests[0])["term"], "SZA SOS")
        # Only the hits that count are batched: the explicit album and its clean twin.
        self.assertEqual(query(apple.requests[1])["ids"], f"{SOS},{SOS_CLEAN}")

    def test_the_album_beats_its_bracketed_deluxe(self):
        self.apple(
            (200, fixture("search_albums_donda.json")),
            (200, fixture("search_families_donda.json")),
            background=True,
        )

        self.assertEqual(apple_music.match_album("Donda", "Kanye West")["media_id"], "1587795158")

    def test_the_album_beats_its_anniversary_edition(self):
        apple = self.apple(
            (200, fixture("search_albums_thriller.json")),
            (200, fixture("search_families_thriller.json")),
            background=True,
        )

        self.assertEqual(apple_music.match_album("Thriller", "Michael Jackson")["media_id"], THRILLER)
        # "Thriller (25th Anniversary) [Deluxe Edition]" counts too, and loses.
        self.assertEqual(query(apple.requests[1])["ids"], f"{THRILLER},273048762")

    def test_a_bracketed_tag_counts_only_on_apples_side(self):
        self.apple(
            (200, fixture("search_albums_thriller.json")),
            (200, fixture("search_albums_thriller.json")),
            (200, fixture("search_families_thriller.json")),
            background=True,
        )

        # A MusicBrainz title with its own tag needs that tag on Apple's album.
        self.assertIsNone(apple_music.match_album("Thriller (Live)", "Michael Jackson"))
        self.assertEqual(apple_music.match_album("Thriller", "Michael Jackson")["media_id"], THRILLER)

    def test_two_equal_albums_are_no_match(self):
        def album(album_id):
            return {
                "id": album_id,
                "type": "albums",
                "attributes": {
                    "name": "Stratosphere",
                    "artistName": "Duster",
                    "url": f"https://music.apple.com/us/album/stratosphere/{album_id}",
                    "playParams": {"id": album_id},
                    "isComplete": True,
                },
            }

        hits = [album("1388806946"), album("1734297746")]
        self.apple((200, {"results": {"albums": {"data": hits}}}), (200, {"data": hits}), background=True)

        self.assertIsNone(apple_music.match_album("Stratosphere", "Duster"))

    def test_singles_and_other_artists_never_count(self):
        apple = self.apple(
            (200, fixture("search_albums_thriller.json")),
            (200, fixture("search_albums_thriller.json")),
            background=True,
        )

        self.assertIsNone(apple_music.match_album("Thriller (Steve Aoki Midnight Hour Remix)", "Michael Jackson"))
        self.assertIsNone(apple_music.match_album("Thriller", "Lambchop Tribute Band"))
        self.assertEqual(len(apple.requests), 2)  # nothing counted, so no batches

    def test_a_blank_title_or_artist_asks_nothing(self):
        self.apple(background=True)

        self.assertIsNone(apple_music.match_album("", "SZA"))
        self.assertIsNone(apple_music.match_album("SOS", " "))


class LinkGenreAlbumsTests(MusicLinksTestCase):
    def link(self, mbid):
        return MusicAlbumLink.objects.get(release_group_mbid=mbid)

    def test_wikidata_ids_are_checked_in_one_batch(self):
        genre_album(SOS_GROUP, "SOS", "SZA", "r&b", "contemporary r&b", page_views=10)
        MusicAlbumLink.objects.create(
            release_group_mbid=SOS_GROUP,
            apple_album_id=SOS,
            status=MusicAlbumLink.Status.UNMATCHED,
            method=MusicAlbumLink.Method.WIKIDATA,
        )
        apple = self.apple((200, fixture("search_families_sza_sos_1.json")), background=True)

        result = music_links.link_genre_albums()

        self.assertEqual(result, {"matched": 1, "stopped": ""})
        self.assertEqual(query(apple.requests[0]), {"ids": SOS, "views": "other-versions"})
        link = self.link(SOS_GROUP)
        self.assertEqual(
            (link.status, link.tier, link.method, link.apple_album_id, link.apple_title),
            ("matched", 3, "wikidata", SOS, "SOS"),
        )
        self.assertTrue(link.apple_image.startswith("https://"))
        self.assertEqual(link.genres, ["contemporary r&b", "r&b"])
        self.assertIsNotNone(link.checked_at)
        self.assertEqual(AppleAlbum.objects.get(apple_album_id=SOS_CLEAN).canonical_id, SOS)

    def test_search_links_an_album_wikidata_has_no_id_for(self):
        genre_album(THRILLER_GROUP, "Thriller", "Michael Jackson", "pop")
        apple = self.apple(
            (200, fixture("search_albums_thriller.json")),
            (200, fixture("search_families_thriller.json")),
            background=True,
        )

        self.assertEqual(music_links.link_genre_albums(), {"matched": 1, "stopped": ""})

        self.assertEqual(query(apple.requests[0])["term"], "Michael Jackson Thriller")
        link = self.link(THRILLER_GROUP)
        self.assertEqual((link.status, link.tier, link.method, link.apple_album_id), ("matched", 3, "search", THRILLER))

    def test_a_wikidata_id_for_another_edition_falls_back_to_search(self):
        genre_album(THRILLER_GROUP, "Thriller", "Michael Jackson", "pop")
        MusicAlbumLink.objects.create(
            release_group_mbid=THRILLER_GROUP,
            apple_album_id=THRILLER_40,
            status=MusicAlbumLink.Status.UNMATCHED,
            method=MusicAlbumLink.Method.WIKIDATA,
        )
        apple = self.apple(
            (200, fixture("search_families_thriller.json")),
            (200, fixture("search_albums_thriller.json")),
            (200, fixture("search_families_thriller.json")),
            background=True,
        )

        music_links.link_genre_albums()

        self.assertEqual(len(apple.requests), 3)
        link = self.link(THRILLER_GROUP)
        self.assertEqual((link.method, link.apple_album_id), ("search", THRILLER))

    def test_no_match_is_unmatched_and_searched_again_a_month_later(self):
        genre_album(SOS_GROUP, "Not on Apple Music", "Nobody", "r&b")
        self.apple((200, fixture("search_albums_thriller.json")), background=True)

        self.assertEqual(music_links.link_genre_albums(), {"unmatched": 1, "stopped": ""})
        link = self.link(SOS_GROUP)
        self.assertEqual((link.status, link.tier, link.method, link.genres), ("unmatched", None, "search", ["r&b"]))

        self.apple(background=True)  # checked today, so not asked again
        self.assertEqual(music_links.link_genre_albums(), {"stopped": ""})

        MusicAlbumLink.objects.filter(pk=link.pk).update(checked_at=timezone.now() - timedelta(days=31))
        self.apple((200, fixture("search_albums_thriller.json")), background=True)
        self.assertEqual(music_links.link_genre_albums(), {"unmatched": 1, "stopped": ""})

    def test_rows_the_data_move_wrote_are_left_alone(self):
        Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id="mbid-held",
            title="Held",
        )
        old = timezone.now() - timedelta(days=90)
        rows = [
            ("mbid-applied", {"status": "applied", "item": apple_item(SOS), "apple_album_id": SOS}),
            ("mbid-review", {"status": "review", "apple_album_id": LANA, "checked_at": old}),
            ("mbid-tier-1", {"status": "matched", "tier": 1, "method": "upc_exact", "apple_album_id": LANA}),
            ("mbid-manual", {"status": "matched", "method": "manual", "apple_album_id": LANA}),
            ("mbid-held", {"status": "unmatched", "method": "upc_exact", "checked_at": old}),
        ]
        for mbid, fields in rows:
            genre_album(mbid, "SOS", "SZA", "r&b")
            MusicAlbumLink.objects.create(release_group_mbid=mbid, **fields)
        before = list(MusicAlbumLink.objects.order_by("pk").values())
        self.apple(background=True)  # any request fails the test

        self.assertEqual(music_links.link_genre_albums(), {"stopped": ""})

        self.assertEqual(list(MusicAlbumLink.objects.order_by("pk").values()), before)

    def test_an_apple_failure_ends_the_run_without_writing(self):
        genre_album(SOS_GROUP, "SOS", "SZA", "r&b", page_views=2)
        genre_album(THRILLER_GROUP, "Thriller", "Michael Jackson", "pop", page_views=1)
        self.apple((500, {"errors": []}), background=True)

        self.assertEqual(music_links.link_genre_albums(), {"stopped": "a request failed"})
        self.assertFalse(MusicAlbumLink.objects.exists())

    def test_a_run_works_ten_minutes_at_most(self):
        genre_album(SOS_GROUP, "SOS", "SZA", "r&b", page_views=2)
        genre_album(THRILLER_GROUP, "Thriller", "Michael Jackson", "pop", page_views=1)
        clock = FakeClock()
        answers = iter([(200, fixture("search_albums_sza_sos_1.json")), (200, fixture("search_families_sza_sos_1.json"))])
        fake = FakeApple(*answers)

        def send(request, **options):
            clock.now += 301  # each Apple request takes five minutes
            return fake(request, **options)

        patcher = patch.object(services.apple_music_background_session, "send", send)
        patcher.start()
        self.addCleanup(patcher.stop)

        result = music_links.link_genre_albums(clock=clock.monotonic)

        self.assertEqual(result, {"matched": 1, "stopped": "out of time"})
        self.assertFalse(MusicAlbumLink.objects.filter(release_group_mbid=THRILLER_GROUP).exists())

    @override_settings(APPLE_MUSIC_TEAM_ID="", APPLE_MUSIC_KEY_ID="", APPLE_MUSIC_PRIVATE_KEY="")
    def test_nothing_happens_without_the_apple_music_key(self):
        genre_album(SOS_GROUP, "SOS", "SZA", "r&b")

        self.assertEqual(music_links.link_genre_albums(), {"skipped": "Apple Music isn't configured"})
        self.assertFalse(MusicAlbumLink.objects.exists())


class StoreWikidataAppleIdsTests(MusicLinksTestCase):
    def test_new_rows_wait_for_their_check_and_the_data_moves_rows_stay(self):
        Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id="mbid-held",
            title="Held",
        )
        old = timezone.now() - timedelta(days=3)
        MusicAlbumLink.objects.create(release_group_mbid="mbid-matched", apple_album_id="1", status="matched", tier=1)
        MusicAlbumLink.objects.create(release_group_mbid="mbid-held", status="unmatched", checked_at=old)
        MusicAlbumLink.objects.create(
            release_group_mbid="mbid-unmatched",
            apple_album_id="5",
            status="unmatched",
            method="search",
            checked_at=old,
        )

        result = store_wikidata_apple_ids(
            {
                "mbid-new": ["11", "12"],
                "mbid-matched": ["2"],
                "mbid-held": ["3"],
                "mbid-unmatched": ["6"],
            },
        )

        self.assertEqual(result, {"added": 1, "changed": 1})
        links = {link.release_group_mbid: link for link in MusicAlbumLink.objects.all()}
        new = links["mbid-new"]
        self.assertEqual((new.apple_album_id, new.status, new.method, new.checked_at), ("11", "unmatched", "wikidata", None))
        self.assertEqual(links["mbid-matched"].apple_album_id, "1")
        self.assertEqual((links["mbid-held"].apple_album_id, links["mbid-held"].checked_at), ("", old))
        unmatched = links["mbid-unmatched"]
        self.assertEqual((unmatched.apple_album_id, unmatched.method, unmatched.checked_at), ("6", "wikidata", None))

        # The same IDs a week later change nothing.
        self.assertEqual(store_wikidata_apple_ids({"mbid-new": ["11"], "mbid-unmatched": ["6"]}), {"added": 0, "changed": 0})


class MusicLinkTaskRegistrationTests(SimpleTestCase):
    def test_beat_runs_both_tasks_on_their_queues(self):
        bridges = settings.CELERY_BEAT_SCHEDULE["fill_apple_album_bridges"]
        genres = settings.CELERY_BEAT_SCHEDULE["link_genre_albums_to_apple"]
        self.assertEqual((bridges["task"], bridges["schedule"]), ("Fill Apple album bridges", 300))
        self.assertEqual((genres["task"], genres["schedule"]), ("Link genre albums to Apple", 3600))
        # django_celery_beat's DatabaseScheduler keeps "expire_seconds", so a backlog drops.
        self.assertEqual(ModelEntry._unpack_options(**bridges["options"])["expire_seconds"], 240)
        self.assertEqual(ModelEntry._unpack_options(**genres["options"])["expire_seconds"], 3000)
        self.assertNotIn("Fill Apple album bridges", settings.CELERY_TASK_ROUTES)  # the default queue
        self.assertEqual(settings.CELERY_TASK_ROUTES["Link genre albums to Apple"], {"queue": "featured"})
        self.assertEqual(celery_app.tasks["Fill Apple album bridges"].name, fill_apple_album_bridges.name)
        self.assertEqual(celery_app.tasks["Link genre albums to Apple"].name, link_genre_albums_to_apple.name)
        self.assertTrue(fill_apple_album_bridges.ignore_result)

    def test_the_bridge_task_publishes_no_task_result(self):
        with patch("app.signals.TaskResult.objects.store_result") as store:
            create_task_result_on_publish(headers={"task": "Fill Apple album bridges", "id": "1"})
            store.assert_not_called()
            create_task_result_on_publish(headers={"task": "Link genre albums to Apple", "id": "2"})
            store.assert_called_once()

    def test_both_tasks_enqueue_and_run(self):
        # Tests run tasks eagerly; without the key both return at once.
        self.assertEqual(fill_apple_album_bridges.delay().get(), {"skipped": "Apple Music isn't configured"})
        self.assertEqual(link_genre_albums_to_apple.delay().get(), {"skipped": "Apple Music isn't configured"})
        with patch("app.music_links.link_genre_albums", return_value={"matched": 2}) as link:
            self.assertEqual(link_genre_albums_to_apple.apply_async().get(), {"matched": 2})
        link.assert_called_once_with()
