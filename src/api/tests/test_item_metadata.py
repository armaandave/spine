"""Item metadata: parsing, no overwrites, facets, version stamps and the fill skip."""

from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from api.services import filters
from api.services.filters import (
    ensure_filter_metadata,
    filter_options_for_items,
    update_item_filter_metadata,
)
from api.services.item_metadata import (
    CURRENT_METADATA_VERSION,
    is_metadata_current,
    store_item_metadata,
)
from app.mixins import collect_external_rating_item_ids
from app.models import Item, ItemFilterFacet, MediaSeries, MediaTypes, Sources

GENRE = ItemFilterFacet.FacetType.GENRE
LANGUAGE = ItemFilterFacet.FacetType.LANGUAGE
THEME = ItemFilterFacet.FacetType.THEME
ORIGINAL_LANGUAGE = ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE


class MetadataTestCase(TestCase):
    """Shared Item and facet helpers."""

    counter = 0

    def item(self, media_type=MediaTypes.MOVIE.value, source=Sources.TMDB.value, **fields):
        MetadataTestCase.counter += 1
        return Item.objects.create(
            media_id=f"meta-{MetadataTestCase.counter}",
            source=source,
            media_type=media_type,
            title=f"Title {MetadataTestCase.counter}",
            image=settings.IMG_NONE,
            **fields,
        )

    def facets(self, item, facet_type):
        return set(
            ItemFilterFacet.objects.filter(item=item, facet_type=facet_type).values_list("value", flat=True),
        )


class RuntimeParseTests(TestCase):
    """Runtime text from every provider becomes minutes."""

    def test_runtime_text_forms(self):
        cases = {
            "24 min": 24,
            "24 min per ep": 24,
            "45m": 45,
            "1 hr 30 min": 90,
            "1h 30m": 90,
            "2h": 120,
            "1.5 hours": 90,
            "90": 90,
            90: 90,
            90.4: 90,
            "0 min": None,
            "": None,
            None: None,
            "unknown": None,
        }
        for value, minutes in cases.items():
            with self.subTest(value=value):
                self.assertEqual(filters._runtime_value_minutes(value), minutes)

    def test_runtime_is_read_from_details(self):
        self.assertEqual(filters._runtime_minutes({"details": {"runtime": "24 min"}}), 24)


class ReleaseYearParseTests(TestCase):
    """Release dates and years from every place providers put them."""

    def test_details_start_date_is_read(self):
        # MAL anime and manga, and ComicVine's bare year.
        for value, expected in (
            ("2011-04-06", date(2011, 4, 6)),
            ("2006-04", date(2006, 4, 1)),
            ("2011", date(2011, 1, 1)),
            (2011, date(2011, 1, 1)),
        ):
            with self.subTest(value=value):
                metadata = {"details": {"start_date": value}}
                release_date = filters._release_date(metadata)
                self.assertEqual(release_date, expected)
                self.assertEqual(filters._release_year(metadata, release_date), expected.year)

    def test_first_publish_year_is_a_year_fallback(self):
        for metadata in ({"first_publish_year": 1965}, {"details": {"first_publish_year": "1965"}}):
            with self.subTest(metadata=metadata):
                self.assertIsNone(filters._release_date(metadata))
                self.assertEqual(filters._release_year(metadata, None), 1965)

    def test_text_date_gives_its_year_only(self):
        metadata = {"details": {"publish_date": "March 2004"}}

        release_date = filters._release_date(metadata)

        self.assertIsNone(release_date)
        self.assertEqual(filters._release_year(metadata, release_date), 2004)

    def test_igdb_integer_first_release_date_is_ignored(self):
        # IGDB search results carry a Unix timestamp here, not a date.
        metadata = {"first_release_date": 1577836800, "details": {"release_date": "2020-01-01"}}

        self.assertEqual(filters._release_date(metadata), date(2020, 1, 1))
        self.assertIsNone(filters._release_year({"first_release_date": 1577836800}, None))

    def test_original_release_date_is_read_first_only_for_books(self):
        metadata = {"original_release_date": "1954-07-29", "release_date": "2012-09-25"}

        self.assertEqual(filters._release_date(metadata, MediaTypes.BOOK.value), date(1954, 7, 29))
        self.assertEqual(filters._release_date(metadata, MediaTypes.GAME.value), date(2012, 9, 25))
        self.assertEqual(filters._release_date(metadata, MediaTypes.MUSIC.value), date(2012, 9, 25))
        self.assertEqual(
            filters._release_date({"details": {"original_release_date": "1954"}}, MediaTypes.BOOK.value),
            date(1954, 1, 1),
        )

    def test_a_bad_year_does_not_hide_a_later_one(self):
        self.assertEqual(filters._release_year({"year": "n/a", "first_publish_year": 1999}, None), 1999)


class UpdateItemFilterMetadataTests(MetadataTestCase):
    """Provider values fill or replace stored ones, never erase them."""

    def test_stored_values_survive_missing_ones(self):
        item = self.item(release_date=date(1999, 10, 15), release_year=1999, runtime_minutes=139)

        update_item_filter_metadata(item, {"title": "Fight Club", "genres": ["Drama"]})

        item.refresh_from_db()
        self.assertEqual(item.release_date, date(1999, 10, 15))
        self.assertEqual(item.release_year, 1999)
        self.assertEqual(item.runtime_minutes, 139)

    def test_empty_facet_lists_keep_stored_facets(self):
        item = self.item()
        update_item_filter_metadata(item, {"genres": ["Drama"], "languages": ["English"]})

        update_item_filter_metadata(item, {"genres": [], "languages": [], "runtime": 100})

        self.assertEqual(self.facets(item, GENRE), {"Drama"})
        self.assertEqual(self.facets(item, LANGUAGE), {"English"})

    def test_new_provider_values_replace_stored_ones(self):
        item = self.item(release_date=date(1999, 1, 1), release_year=1999, runtime_minutes=100)
        update_item_filter_metadata(item, {"genres": ["Drama"]})

        update_item_filter_metadata(
            item,
            {"release_date": "1999-10-15", "runtime": 139, "genres": ["Thriller", "Drama"]},
        )

        item.refresh_from_db()
        self.assertEqual(item.release_date, date(1999, 10, 15))
        self.assertEqual(item.runtime_minutes, 139)
        self.assertEqual(self.facets(item, GENRE), {"Drama", "Thriller"})

    def test_unchanged_metadata_writes_nothing(self):
        item = self.item()
        metadata = {
            "release_date": "1999-10-15",
            "runtime": 139,
            "genres": ["Drama"],
            "languages": ["English"],
        }
        self.assertTrue(update_item_filter_metadata(item, metadata))
        item.refresh_from_db()
        stamp = item.filter_metadata_updated_at
        self.assertIsNotNone(stamp)

        # One read of the stored facets; no UPDATE, DELETE or INSERT.
        with self.assertNumQueries(1):
            self.assertFalse(update_item_filter_metadata(item, metadata))
        # Nothing new and no facets: not even a read.
        with self.assertNumQueries(0):
            self.assertFalse(update_item_filter_metadata(item, {"title": "Fight Club"}))

        item.refresh_from_db()
        self.assertEqual(item.filter_metadata_updated_at, stamp)

    def test_missing_stamp_is_written_once(self):
        item = self.item(release_year=1999)

        with self.assertNumQueries(1):
            self.assertTrue(update_item_filter_metadata(item, {"year": 1999}))

        item.refresh_from_db()
        self.assertIsNotNone(item.filter_metadata_updated_at)

    def test_long_facet_values_are_cut_and_deduplicated_before_comparing(self):
        item = self.item()
        long_value = "x" * 150
        metadata = {"genres": [long_value, long_value[:100], " Drama ", "Drama"]}
        update_item_filter_metadata(item, metadata)

        self.assertEqual(self.facets(item, GENRE), {"x" * 100, "Drama"})
        with self.assertNumQueries(1):
            self.assertFalse(update_item_filter_metadata(item, metadata))

    def test_themes_are_theme_rows_never_genres(self):
        item = self.item(media_type=MediaTypes.GAME.value, source=Sources.IGDB.value)

        update_item_filter_metadata(
            item,
            {"genres": ["Role-playing (RPG)"], "details": {"themes": ["Fantasy", "Open world"]}},
        )

        self.assertEqual(self.facets(item, THEME), {"Fantasy", "Open world"})
        self.assertEqual(self.facets(item, GENRE), {"Role-playing (RPG)"})

    def test_original_language_is_its_own_facet(self):
        item = self.item()

        update_item_filter_metadata(
            item,
            {"original_language": "Japanese", "languages": ["Japanese", "English"]},
        )

        self.assertEqual(self.facets(item, ORIGINAL_LANGUAGE), {"Japanese"})
        self.assertEqual(self.facets(item, LANGUAGE), {"Japanese", "English"})

    def test_a_year_without_a_date_keeps_the_stored_dates_year(self):
        dated = self.item(release_date=date(2010, 5, 1), release_year=2010)
        undated_year = self.item(release_date=date(2010, 5, 1))

        for item in (dated, undated_year):
            update_item_filter_metadata(item, {"first_publish_year": 1950})

        self.refresh_items(dated, undated_year)
        # The year and the date agree; a stored date's year fills a missing year.
        self.assertEqual((dated.release_date, dated.release_year), (date(2010, 5, 1), 2010))
        self.assertEqual((undated_year.release_date, undated_year.release_year), (date(2010, 5, 1), 2010))

    def test_a_year_alone_fills_an_item_without_a_date(self):
        item = self.item(release_year=2010)

        update_item_filter_metadata(item, {"first_publish_year": 1950})

        item.refresh_from_db()
        self.assertEqual((item.release_date, item.release_year), (None, 1950))

    def test_exclude_matches_type_and_value_on_the_same_row(self):
        themed = self.item(MediaTypes.GAME.value, Sources.IGDB.value)
        update_item_filter_metadata(themed, {"genres": ["Shooter"], "themes": ["Horror"]})
        horror = self.item(MediaTypes.GAME.value, Sources.IGDB.value)
        update_item_filter_metadata(horror, {"genres": ["Horror"]})
        spoken = self.item()
        update_item_filter_metadata(spoken, {"languages": ["English"], "original_language": "Japanese"})

        def kept(params):
            return set(filters.apply_item_filters(Item.objects.all(), params, item_path=""))

        # A game's "Horror" theme doesn't hide it from exclude_genre=Horror.
        self.assertEqual(kept({"exclude_genre": "Horror"}), {themed, spoken})
        self.assertEqual(kept({"exclude_language": "Japanese"}), {themed, horror, spoken})
        self.assertEqual(kept({"exclude_language": "English"}), {themed, horror})

    def refresh_items(self, *items):
        for item in items:
            item.refresh_from_db()

    def test_themes_and_original_language_stay_out_of_filter_options(self):
        item = self.item()
        update_item_filter_metadata(
            item,
            {
                "genres": ["Drama"],
                "languages": ["French"],
                "themes": ["Survival"],
                "original_language": "French",
            },
        )

        options = filter_options_for_items(Item.objects.filter(id=item.id), item_path="")

        self.assertEqual(options["genres"], [{"value": "Drama", "label": "Drama"}])
        self.assertEqual(options["languages"], [{"value": "French", "label": "French"}])
        self.assertNotIn("themes", options)
        self.assertNotIn("original_languages", options)


class StoreItemMetadataTests(MetadataTestCase):
    """The shared store helper: stamps, series and external ratings."""

    def test_every_media_type_has_a_current_version(self):
        self.assertEqual(set(CURRENT_METADATA_VERSION), set(MediaTypes.values))
        for media_type in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
        ):
            self.assertEqual(CURRENT_METADATA_VERSION[media_type], 2)

    def test_complete_metadata_stamps_the_current_version(self):
        item = self.item()

        self.assertTrue(store_item_metadata(item, {"release_date": "1999-10-15", "runtime": 139}))

        item.refresh_from_db()
        self.assertEqual(item.metadata_version, CURRENT_METADATA_VERSION[MediaTypes.MOVIE.value])
        self.assertEqual(item.release_year, 1999)
        self.assertTrue(is_metadata_current(item))

    def test_partial_or_empty_metadata_is_never_stamped(self):
        item = self.item()

        self.assertFalse(store_item_metadata(item, {}))
        self.assertFalse(store_item_metadata(item, {"year": 1999}, complete=False))

        item.refresh_from_db()
        self.assertEqual(item.metadata_version, 0)
        self.assertEqual(item.release_year, 1999)

    def test_music_needs_an_album_length_to_be_stamped(self):
        item = self.item(media_type=MediaTypes.MUSIC.value, source=Sources.MUSICBRAINZ.value)

        self.assertFalse(store_item_metadata(item, {"release_date": "2007-04-17", "genres": ["industrial"]}))
        item.refresh_from_db()
        self.assertEqual(item.metadata_version, 0)

        self.assertTrue(store_item_metadata(item, {"release_date": "2007-04-17", "runtime": 64}))
        item.refresh_from_db()
        self.assertEqual(item.metadata_version, CURRENT_METADATA_VERSION[MediaTypes.MUSIC.value])
        self.assertEqual(item.runtime_minutes, 64)

    def test_storing_current_unchanged_metadata_writes_nothing(self):
        item = self.item()
        metadata = {"release_date": "1999-10-15", "genres": ["Drama"]}
        store_item_metadata(item, metadata)

        with self.assertNumQueries(1):
            self.assertTrue(store_item_metadata(item, metadata))

    @staticmethod
    def series_metadata(*member_ids):
        return {
            "release_date": "2001-11-16",
            "details": {
                "series_id": "1241",
                "series_source": Sources.TMDB.value,
                "series_media_type": MediaTypes.MOVIE.value,
                "series_name": "Harry Potter Collection",
            },
            "related": {
                "collection": [
                    {
                        "media_id": member_id,
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": f"Film {member_id}",
                        "image": settings.IMG_NONE,
                    }
                    for member_id in member_ids
                ],
                "recommendations": [],
            },
        }

    def test_series_members_are_queued_for_ratings_in_one_batch_on_commit(self):
        item = self.item()
        metadata = self.series_metadata(item.media_id, "672", "673")

        with (
            patch("app.tasks.enqueue_external_rating_batches") as enqueue,
            patch("app.signals.enrich_external_ratings") as enrich_one,
            self.captureOnCommitCallbacks(execute=True),
        ):
            store_item_metadata(item, metadata)

        series = MediaSeries.objects.get(series_id="1241")
        self.assertEqual(series.memberships.count(), 3)
        new_ids = sorted(
            Item.objects.filter(media_id__in=["672", "673"]).values_list("id", flat=True),
        )
        enqueue.assert_called_once_with(new_ids)
        enrich_one.delay.assert_not_called()

    def test_series_members_join_an_active_collector(self):
        item = self.item()
        metadata = self.series_metadata(item.media_id, "772")

        with (
            patch("app.tasks.enqueue_external_rating_batches") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
            collect_external_rating_item_ids() as collected,
        ):
            store_item_metadata(item, metadata)

        self.assertEqual(collected, {Item.objects.get(media_id="772").id})
        enqueue.assert_not_called()

    def test_series_can_be_left_out(self):
        item = self.item()

        store_item_metadata(item, self.series_metadata(item.media_id, "872"), persist_series=False)

        self.assertFalse(MediaSeries.objects.exists())
        self.assertFalse(Item.objects.filter(media_id="872").exists())


class EnsureFilterMetadataTests(MetadataTestCase):
    """Filtered requests fetch only Items that aren't current."""

    def test_current_items_are_never_fetched_again(self):
        item = self.item(metadata_version=CURRENT_METADATA_VERSION[MediaTypes.MOVIE.value])

        with patch(
            "app.providers.services.get_media_metadata",
            side_effect=AssertionError("provider called"),
        ):
            ensure_filter_metadata(Item.objects.filter(id=item.id), {"genre": ["Drama"]}, item_path="")

    def test_outdated_item_is_fetched_and_stamped(self):
        item = self.item()

        with patch(
            "app.providers.services.get_media_metadata",
            return_value={"release_date": "1999-10-15", "genres": ["Drama"]},
        ) as metadata_mock:
            ensure_filter_metadata(Item.objects.filter(id=item.id), {"genre": ["Drama"]}, item_path="")

        metadata_mock.assert_called_once()
        self.assertTrue(metadata_mock.call_args.kwargs["include_music_enrichment"])
        item.refresh_from_db()
        self.assertEqual(self.facets(item, GENRE), {"Drama"})
        self.assertTrue(is_metadata_current(item))

    def test_music_is_fetched_without_enrichment_and_left_unstamped(self):
        item = self.item(media_type=MediaTypes.MUSIC.value, source=Sources.MUSICBRAINZ.value)

        with patch(
            "app.providers.services.get_media_metadata",
            return_value={"release_date": "2007-04-17", "genres": ["industrial"], "runtime": 64},
        ) as metadata_mock:
            ensure_filter_metadata(Item.objects.filter(id=item.id), {"genre": ["industrial"]}, item_path="")

        self.assertFalse(metadata_mock.call_args.kwargs["include_music_enrichment"])
        item.refresh_from_db()
        self.assertEqual(self.facets(item, GENRE), {"industrial"})
        self.assertEqual(item.metadata_version, 0)
