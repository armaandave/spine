from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from api.services.media import default_source_for
from app import config, exposure
from app.models import AppleAlbum, Item, MediaTypes, MusicAlbumLink, Sources
from app.music_catalog import (
    apple_genre_names,
    canonical_media_ref,
    canonical_music_payloads,
    canonical_music_ref,
    canonical_music_refs,
)

MUSIC = MediaTypes.MUSIC.value
APPLE = Sources.APPLE_MUSIC.value
MUSICBRAINZ = Sources.MUSICBRAINZ.value
SOS = "1657869377"
SOS_CLEAN = "1658650093"
SOS_MBID = "1a5c4d2e-3b6f-4a7d-9e8c-0f1b2a3c4d5e"
OTHER_MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"


class MusicCatalogSettingTests(SimpleTestCase):
    def test_tests_follow_the_codes_default(self):
        self.assertEqual(settings.MUSIC_CATALOG_DEFAULT, "musicbrainz")
        self.assertEqual(settings.MUSIC_CATALOG_SOURCE, settings.MUSIC_CATALOG_DEFAULT)
        self.assertEqual(config.get_sources(MediaTypes.MUSIC.value), [Sources.MUSICBRAINZ])
        self.assertEqual(config.get_default_source_name(MediaTypes.MUSIC.value), Sources.MUSICBRAINZ)

    def test_music_follows_the_setting_at_call_time(self):
        with override_settings(MUSIC_CATALOG_SOURCE="apple_music"):
            self.assertEqual(config.get_sources(MediaTypes.MUSIC.value), [Sources.APPLE_MUSIC])
            self.assertEqual(config.get_default_source_name(MediaTypes.MUSIC.value), Sources.APPLE_MUSIC)
            self.assertEqual(default_source_for(MediaTypes.MUSIC.value), "apple_music")
            self.assertEqual(exposure.source_map()[MediaTypes.MUSIC.value], ["apple_music"])
        self.assertEqual(default_source_for(MediaTypes.MUSIC.value), "musicbrainz")

    def test_anything_but_apple_music_means_musicbrainz(self):
        for value in ("musicbrainz", "apple-music", "Apple Music", ""):
            with self.subTest(value=value), override_settings(MUSIC_CATALOG_SOURCE=value):
                self.assertEqual(config.music_catalog_source(), Sources.MUSICBRAINZ)

    def test_other_media_types_keep_their_sources(self):
        with override_settings(MUSIC_CATALOG_SOURCE="apple_music"):
            self.assertEqual(
                config.get_sources(MediaTypes.BOOK.value),
                [Sources.HARDCOVER, Sources.OPENLIBRARY],
            )
            self.assertEqual(config.get_default_source_name(MediaTypes.MOVIE.value), Sources.TMDB)


class AppleGenreNamesTests(SimpleTestCase):
    def test_apple_genres_become_musicbrainz_genres(self):
        self.assertEqual(apple_genre_names(["Hip-Hop/Rap", "Music"]), ["hip hop"])
        self.assertEqual(apple_genre_names(["R&B/Soul", "Music", "Contemporary R&B"]), ["r&b", "contemporary r&b"])
        self.assertEqual(
            apple_genre_names(["Hard Rock", "Music", "Rock", "Arena Rock", "Rock & Roll"]),
            ["hard rock", "rock", "arena rock", "rock and roll"],
        )
        self.assertEqual(apple_genre_names(["Singer/Songwriter", "Alternative"]), ["singer-songwriter", "alternative rock"])

    def test_each_genre_once_and_nothing_for_music_alone(self):
        self.assertEqual(apple_genre_names(["Hip-Hop/Rap", "Rap", " hip-hop/rap "]), ["hip hop"])
        self.assertEqual(apple_genre_names(["Music"]), [])
        self.assertEqual(apple_genre_names(None), [])


def music_item(source, media_id):
    return Item.objects.create(source=source, media_type=MUSIC, media_id=media_id, title="SOS")


def link(status, *, apple_album_id=SOS, item=None, mbid=SOS_MBID, **fields):
    return MusicAlbumLink.objects.create(
        release_group_mbid=mbid,
        apple_album_id=apple_album_id,
        status=status,
        item=item,
        **fields,
    )


class CanonicalMusicRefTests(TestCase):
    """The six rules, in order, and their batch form."""

    def assert_canonical(self, ref, expected):
        self.assertEqual(canonical_music_ref(*ref), expected)

    def test_nothing_changes_while_nothing_is_linked(self):
        music_item(MUSICBRAINZ, SOS_MBID)

        for ref in ((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, OTHER_MBID), (APPLE, MUSIC, SOS)):
            with self.subTest(ref=ref):
                self.assert_canonical(ref, ref)

    def test_other_media_types_need_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(canonical_music_ref("tmdb", "movie", 603), ("tmdb", "movie", "603"))
            self.assertEqual(canonical_music_ref(APPLE, "book", SOS), (APPLE, "book", SOS))

    def test_rule_1_an_apple_alias_goes_to_its_listing(self):
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS)

        self.assert_canonical((APPLE, MUSIC, SOS_CLEAN), (APPLE, MUSIC, SOS))
        self.assert_canonical((APPLE, MUSIC, SOS), (APPLE, MUSIC, SOS))

    def test_rule_1_a_reverted_album_goes_back_to_musicbrainz(self):
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        item = music_item(MUSICBRAINZ, SOS_MBID)
        link(MusicAlbumLink.Status.REVERTED, item=item, tier=1)

        for album_id in (SOS, SOS_CLEAN):
            with self.subTest(album_id=album_id):
                self.assert_canonical((APPLE, MUSIC, album_id), (MUSICBRAINZ, MUSIC, SOS_MBID))
        # The MusicBrainz ref itself stays, through rule 3.
        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, SOS_MBID))

    def test_rule_1_only_while_the_musicbrainz_item_exists(self):
        link(MusicAlbumLink.Status.REVERTED, tier=1)

        self.assert_canonical((APPLE, MUSIC, SOS), (APPLE, MUSIC, SOS))

    def test_rule_2_a_moved_album_goes_to_the_item_that_holds_it(self):
        item = music_item(APPLE, SOS)
        link(MusicAlbumLink.Status.APPLIED, item=item, tier=1)

        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (APPLE, MUSIC, SOS))

    def test_rule_2_beats_a_stray_musicbrainz_item(self):
        item = music_item(APPLE, SOS)
        link(MusicAlbumLink.Status.APPLIED, item=item, tier=1)
        music_item(MUSICBRAINZ, SOS_MBID)

        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (APPLE, MUSIC, SOS))

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_rule_3_a_musicbrainz_item_keeps_its_ref(self):
        # Unmoved data stays together, whatever the match or the bridge says.
        music_item(MUSICBRAINZ, SOS_MBID)
        music_item(APPLE, SOS)
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS, release_group_mbid=SOS_MBID, same_title=True)
        for status in (MusicAlbumLink.Status.MATCHED, MusicAlbumLink.Status.REVIEW):
            with self.subTest(status=status):
                MusicAlbumLink.objects.all().delete()
                link(status, tier=1)
                self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, SOS_MBID))

    def test_rule_4_an_album_first_added_from_apple_music(self):
        music_item(APPLE, SOS)
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS, release_group_mbid=SOS_MBID, same_title=True)

        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (APPLE, MUSIC, SOS))

    def test_rule_4_needs_the_same_title_and_an_item(self):
        AppleAlbum.objects.create(apple_album_id=SOS, canonical_id=SOS, release_group_mbid=SOS_MBID, same_title=True)
        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, SOS_MBID))

        music_item(APPLE, SOS)
        AppleAlbum.objects.filter(apple_album_id=SOS).update(same_title=False)
        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, SOS_MBID))

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_rule_5_strong_matches_on_the_apple_catalog(self):
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        cases = [
            ({"tier": 1}, SOS, (APPLE, MUSIC, SOS)),
            ({"tier": 2}, SOS, (APPLE, MUSIC, SOS)),
            ({"method": MusicAlbumLink.Method.MANUAL}, SOS, (APPLE, MUSIC, SOS)),
            # A match to an alias goes to its listing.
            ({"tier": 1}, SOS_CLEAN, (APPLE, MUSIC, SOS)),
            ({"tier": 3, "method": MusicAlbumLink.Method.SEARCH}, SOS, (MUSICBRAINZ, MUSIC, SOS_MBID)),
        ]
        for fields, apple_album_id, expected in cases:
            with self.subTest(fields=fields, apple_album_id=apple_album_id):
                MusicAlbumLink.objects.all().delete()
                link(MusicAlbumLink.Status.MATCHED, apple_album_id=apple_album_id, **fields)
                self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), expected)

    def test_rule_5_waits_for_the_apple_catalog(self):
        link(MusicAlbumLink.Status.MATCHED, tier=1)

        self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, SOS_MBID))

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_rule_6_anything_else_stays(self):
        for status in (MusicAlbumLink.Status.REVIEW, MusicAlbumLink.Status.UNMATCHED):
            with self.subTest(status=status):
                MusicAlbumLink.objects.all().delete()
                link(status, apple_album_id="" if status == MusicAlbumLink.Status.UNMATCHED else SOS, tier=1)
                self.assert_canonical((MUSICBRAINZ, MUSIC, SOS_MBID), (MUSICBRAINZ, MUSIC, SOS_MBID))

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_a_page_of_refs_takes_two_queries(self):
        apple_item = music_item(APPLE, SOS)
        link(MusicAlbumLink.Status.APPLIED, item=apple_item, tier=1)
        AppleAlbum.objects.create(apple_album_id=SOS_CLEAN, canonical_id=SOS)
        refs = [
            (MUSICBRAINZ, MUSIC, SOS_MBID),
            (APPLE, MUSIC, SOS_CLEAN),
            ("tmdb", "movie", "603"),
            *((MUSICBRAINZ, MUSIC, f"00000000-0000-4000-8000-{index:012d}") for index in range(25)),
            *((APPLE, MUSIC, str(index)) for index in range(25)),
        ]

        with self.assertNumQueries(2):
            canonical = canonical_music_refs(refs)

        self.assertEqual(canonical[(MUSICBRAINZ, MUSIC, SOS_MBID)], (APPLE, MUSIC, SOS))
        self.assertEqual(canonical[(APPLE, MUSIC, SOS_CLEAN)], (APPLE, MUSIC, SOS))
        self.assertEqual(canonical[("tmdb", "movie", "603")], ("tmdb", "movie", "603"))
        self.assertEqual(len(canonical), len(refs))

    def test_api_refs_and_provider_results(self):
        apple_item = music_item(APPLE, SOS)
        link(MusicAlbumLink.Status.APPLIED, item=apple_item, tier=1)
        ref = {"item_id": None, "source": MUSICBRAINZ, "media_type": MUSIC, "media_id": SOS_MBID, "season_number": None}
        movie = {"source": "tmdb", "media_type": "movie", "media_id": "603"}

        self.assertEqual(canonical_media_ref(ref), {**ref, "source": APPLE, "media_id": SOS})
        self.assertIs(canonical_media_ref(movie), movie)

        results = canonical_music_payloads(
            [
                {"media_id": SOS_MBID, "title": "SOS"},
                {"media_id": OTHER_MBID, "title": "Year Zero"},
                {"media_id": OTHER_MBID, "title": "Year Zero"},
                {"media_id": SOS, "source": APPLE, "title": "SOS"},
            ],
            source=MUSICBRAINZ,
            media_type=MUSIC,
        )

        # The moved album is shown once, where it first appears; other repeats stay.
        self.assertEqual(
            [(result.get("source"), result["media_id"]) for result in results],
            [(APPLE, SOS), (None, OTHER_MBID), (None, OTHER_MBID)],
        )
        self.assertEqual(results[0]["title"], "SOS")
