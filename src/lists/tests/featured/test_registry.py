from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from app.providers import nytbooks
from lists.featured import overrides, registry
from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import static as static_adapter
from lists.featured.definitions import static as static_definitions
from lists.featured.registry import Guards, LiveWindow, live_window
from lists.featured.types import Entry
from lists.tests.featured.helpers import FIXTURES, fake_adapter, make_definition


class RegistryContractTests(SimpleTestCase):
    """The real registry: every family's definitions must pass these."""

    def test_registry_has_no_problems(self):
        self.assertEqual(registry.problems(), [])

    def test_keys_and_identities_are_unique(self):
        definitions = registry.all_definitions()
        self.assertEqual(len({item.key for item in definitions}), len(definitions))
        self.assertEqual(len({item.identity for item in definitions}), len(definitions))

    def test_the_six_live_lists_keep_their_identities_and_positions(self):
        headline = [item for item in registry.all_definitions() if item.headline]
        self.assertEqual(
            [(item.key, item.identity, item.position) for item in headline],
            [
                ("letterboxd-top-500", ("mdblist", "155837"), 1),
                *[
                    (f"nyt-{chart['slug']}", ("nyt", chart["slug"]), chart["featured_position"])
                    for chart in nytbooks.FEATURED_CHARTS
                ],
            ],
        )
        self.assertEqual(registry.by_identity("mdblist", "155837").key, "letterboxd-top-500")
        self.assertEqual(registry.get("nyt-business-books").tags, ("NYT Best Sellers", "Books"))

    def test_every_static_file_parses(self):
        for definition in registry.all_definitions():
            if definition.adapter == "static":
                with self.subTest(definition.key):
                    static_adapter.validate(definition)


class DefinitionValidationTests(SimpleTestCase):
    def problems(self, **fields):
        with fake_adapter([]):
            return registry.definition_problems(make_definition(**fields), ADAPTERS)

    def test_valid_definition_has_no_problems(self):
        self.assertEqual(self.problems(), [])

    def test_identity_defaults_to_spine_and_key(self):
        self.assertEqual(make_definition().identity, ("spine", "test-list"))

    def test_rejects_bad_fields(self):
        cases = {
            "key must be kebab-case": {"key": "Bad_Key"},
            "unknown media types": {"media_types": ("season",)},
            "media_types is required": {"media_types": ()},
            "kind must be one of": {"kind": "ranking"},
            "unknown adapter": {"adapter": "nope"},
            "cadence must be positive": {"cadence": timedelta(0)},
            "list_type must be one of": {"list_type": "places"},
            "min_resolve_rate": {"guards": Guards(min_resolve_rate=1.5)},
            "soft_time_limit": {"soft_time_limit": 5},
        }
        for expected, fields in cases.items():
            with self.subTest(expected):
                self.assertTrue(any(expected in problem for problem in self.problems(**fields)), self.problems(**fields))

    def test_position_ranges(self):
        self.assertIn("test-list: positions 1-6 are reserved for headline lists", self.problems(position=3))
        self.assertIn("test-list: headline lists must use positions 1-6", self.problems(headline=True))
        self.assertIn("test-list: position 350 is in the books range", self.problems(position=350))
        self.assertIn("test-list: position 50 is outside every range", self.problems(position=50))
        self.assertIn("test-list: positions 900+ are for people lists", self.problems(position=905))
        self.assertEqual(self.problems(position=250, media_types=("movie", "tv")), [])
        self.assertEqual(self.problems(position=850, media_types=("movie", "book", "game")), [])
        self.assertEqual(self.problems(position=905, list_type="people", media_types=("book",)), [])

    def test_adapter_validate_errors_are_problems(self):
        def validate(definition):
            raise ValueError("needs a slug")

        with fake_adapter([], validate=validate):
            found = registry.definition_problems(make_definition(), ADAPTERS)
        self.assertEqual(found, ["test-list: invalid params: needs a slug"])

    def test_live_windows_must_be_utc_and_ordered(self):
        start = datetime(2027, 3, 14, 23, tzinfo=UTC)
        backwards = LiveWindow(starts_at=start, ends_at=start - timedelta(hours=1))
        naive = LiveWindow(starts_at=start.replace(tzinfo=None), ends_at=start.replace(tzinfo=None))
        self.assertIn("test-list: a live window must end after it starts", self.problems(live_windows=(backwards,)))
        self.assertIn("test-list: live windows must be timezone-aware", self.problems(live_windows=(naive,)))

    def test_live_window_helper_converts_local_time_to_utc(self):
        window = live_window("2027-03-14 16:00", "America/Los_Angeles", timedelta(hours=4))
        self.assertEqual(window.starts_at, datetime(2027, 3, 14, 23, tzinfo=UTC))
        self.assertEqual(window.ends_at, datetime(2027, 3, 15, 3, tzinfo=UTC))
        self.assertEqual(window.status(window.starts_at - timedelta(hours=2)), "upcoming")
        self.assertEqual(window.status(window.starts_at + timedelta(hours=1)), "live")
        self.assertEqual(window.status(window.ends_at + timedelta(hours=2)), "done")
        self.assertIsNone(window.status(window.ends_at + timedelta(days=2)))
        self.assertTrue(window.is_active(window.starts_at - timedelta(minutes=19)))
        self.assertFalse(window.is_active(window.starts_at - timedelta(minutes=21)))

    def test_duplicates_are_problems(self):
        first = make_definition()
        found = registry.problems([first, replace(first, name="Again")])
        self.assertIn("Duplicate featured-list key: test-list", found)
        found = registry.problems([first, replace(first, key="other-list")])
        self.assertTrue(any("Duplicate featured-list identity" in problem for problem in found))

    def test_override_definitions_swaps_the_registry(self):
        definition = make_definition()
        with registry.override_definitions([definition]):
            self.assertEqual(registry.all_definitions(), [definition])
            self.assertIs(registry.get("test-list"), definition)
        self.assertIsNone(registry.get("test-list"))

    def test_cadence_labels(self):
        labels = {
            None: "static",
            timedelta(minutes=5): "live",
            timedelta(days=1): "daily",
            timedelta(days=7): "weekly",
            timedelta(days=30): "monthly",
            timedelta(days=365): "yearly",
        }
        for cadence, label in labels.items():
            self.assertEqual(registry.cadence_label(make_definition(cadence=cadence)), label)

    def test_definition_hash_changes_with_fields_and_adapter_version(self):
        definition = make_definition()
        base = registry.definition_hash(definition)
        self.assertEqual(base, registry.definition_hash(make_definition()))
        self.assertNotEqual(base, registry.definition_hash(replace(definition, name="Renamed")))
        with fake_adapter([], version=lambda _definition: "v2") as adapter:
            self.assertNotEqual(base, registry.definition_hash(definition, adapter))
        self.assertNotEqual(base, registry.definition_hash(definition, overrides=[("t:x|", "-")]))


class StaticDefinitionTests(SimpleTestCase):
    def test_manifest_rows_become_static_definitions(self):
        definitions = static_definitions.load(FIXTURES / "static")
        canon, people = definitions
        self.assertEqual(canon.key, "sample-canon")
        self.assertEqual(canon.adapter, "static")
        self.assertEqual(canon.params, {"file": "sample-canon.csv"})
        self.assertEqual(canon.media_types, ("movie",))
        self.assertIsNone(canon.cadence)
        self.assertTrue(canon.is_ranked)
        self.assertEqual(canon.identity, ("spine", "sample-canon"))
        self.assertEqual(people.list_type, "people")
        self.assertFalse(people.is_ranked)

    def test_fixture_definitions_validate(self):
        with patch.object(static_adapter, "DIRECTORY", FIXTURES / "static"):
            definitions = static_definitions.load(FIXTURES / "static")
            self.assertEqual(
                [problem for item in definitions for problem in registry.definition_problems(item, ADAPTERS)],
                [],
            )

    def test_bad_manifest_header_is_rejected(self):
        with TemporaryDirectory() as directory:
            Path(directory, "manifest-bad.csv").write_text("key,name\nx,y\n")
            with self.assertRaisesMessage(ValueError, "header must be"):
                static_definitions.load(directory)


class StaticFileTests(SimpleTestCase):
    def write(self, directory, body):
        path = Path(directory, "list.csv")
        path.write_text("rank,title,year,creator,ref,image,note\n" + body, encoding="utf-8")
        return path

    def test_parses_rows_into_ref_entries(self):
        entries = static_adapter.read_entries(FIXTURES / "static" / "sample-canon.csv")
        self.assertEqual(
            entries[0],
            Entry(
                rank=1,
                title="Citizen Kane",
                year=1941,
                creator="Orson Welles",
                media_type="movie",
                ids={"ref": "tmdb:movie:15"},
                image="https://image.tmdb.org/t/p/w500/sav0jxhqiH0bPr2vZFU0Kjt2nZL.jpg",
            ),
        )
        self.assertEqual(entries[0].key, "ref:tmdb:movie:15")
        self.assertEqual(entries[1].note, "Top of the 2012 poll")

    def test_rejects_bad_rows(self):
        cases = {
            "ref is required": "1,Missing,2000,,,,\n",
            "invalid ref": "1,Bad,2000,,tmdb:movie,,\n",
            "does not provide": "1,Wrong source,2000,,igdb:movie:1,,\n",
            "rank must be a whole number": "first,Bad rank,2000,,tmdb:movie:1,,\n",
            "appears twice": "1,One,2000,,tmdb:movie:1,,\n2,Two,2000,,tmdb:movie:1,,\n",
            "not one of this list's media types": "1,Book,2000,,hardcover:book:1,,\n",
        }
        for expected, body in cases.items():
            with self.subTest(expected), TemporaryDirectory() as directory:
                with self.assertRaisesMessage(ValueError, expected):
                    static_adapter.read_entries(self.write(directory, body), allowed_media_types={"movie"})

    def test_validate_and_version(self):
        definition = make_definition(adapter="static", params={"file": "sample-canon.csv"}, cadence=None)
        with patch.object(static_adapter, "DIRECTORY", FIXTURES / "static"):
            static_adapter.validate(definition)
            version = static_adapter.version(definition)
        self.assertEqual(len(version), 64)
        with self.assertRaisesMessage(ValueError, "does not exist"):
            static_adapter.validate(replace(definition, params={"file": "missing.csv"}))
        with self.assertRaisesMessage(ValueError, "bare <name>.csv"):
            static_adapter.validate(replace(definition, params={"file": "../x.csv"}))


class OverrideFileTests(SimpleTestCase):
    def tearDown(self):
        overrides.clear_cache()

    def load(self, body):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        Path(directory.name, "films.csv").write_text(body, encoding="utf-8")
        overrides.clear_cache()
        rows = overrides._load(directory.name)
        return rows, directory.name

    def test_valid_overrides_and_scopes(self):
        body = (
            "scope,entry_key,ref,note\n"
            "*,t:dune|2021,tmdb:movie:438631,global\n"
            "test,t:dune|2021,tmdb:movie:1,adapter\n"
            "test-list,t:dune|2021,tmdb:movie:2,list\n"
            "test-list,t:skip me|,-,skip\n"
        )
        rows, _ = self.load(body)
        with patch.object(overrides, "_load", lambda: rows):
            found = overrides.for_definition(make_definition())
            self.assertEqual(found, {"t:dune|2021": "tmdb:movie:2", "t:skip me|": "-"})
            self.assertEqual(overrides.problems(list_keys={"test-list"}, adapter_keys={"test"}), [])

    def test_bad_overrides_are_problems(self):
        body = (
            "scope,entry_key,ref,note\n"
            "unknown-list,t:x|,tmdb:movie:1,\n"
            "test-list,t:y|,tmdb:film:1,\n"
            "test-list,t:z|,tmdb:movie:1,\n"
            "test-list,t:z|,tmdb:movie:2,\n"
        )
        rows, _ = self.load(body)
        with patch.object(overrides, "_load", lambda: rows):
            found = overrides.problems(list_keys={"test-list"}, adapter_keys={"test"})
        self.assertEqual(len(found), 3)
        self.assertIn("unknown scope 'unknown-list'", found[0])
        self.assertIn("unknown media type", found[1])
        self.assertIn("conflicts", found[2])


class EntryKeyTests(SimpleTestCase):
    def test_key_prefers_strong_ids_in_a_fixed_order(self):
        self.assertEqual(Entry(1, "X", ids={"ref": "tmdb:movie:1", "imdb": "tt1"}).key, "ref:tmdb:movie:1")
        self.assertEqual(Entry(1, "X", media_type="movie", ids={"tmdb": 550, "imdb": "tt1"}).key, "tmdb:movie:550")
        self.assertEqual(Entry(1, "X", ids={"imdb": "tt0111161"}).key, "imdb:tt0111161")
        self.assertEqual(Entry(1, "X", isbns=("978-0-14-303943-3",)).key, "isbn:9780143039433")
        self.assertEqual(Entry(1, "X", ids={"openlibrary_person": "OL1A"}).key, "openlibrary_person:OL1A")
        self.assertEqual(Entry(1, "  Amélie & Co.: Part 2 ", year=2001).key, "t:amelie and co part 2|2001")
        self.assertEqual(Entry(None, "Untitled").key, "t:untitled|")

    def test_ids_are_strings_and_blank_ids_are_dropped(self):
        entry = Entry(1, "X", ids={"tmdb": 550, "imdb": "", "igdb": None})
        self.assertEqual(entry.ids, {"tmdb": "550"})
