import json
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command

from lists.featured import registry
from lists.featured.registry import Guards
from lists.featured.types import FetchResult
from lists.models import CustomList, FeaturedListSync
from lists.tests.featured.helpers import FeaturedTestCase, entry, fake_adapter, make_definition


def entries():
    return [
        entry(1, "Known", year=2000, media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg"),
        entry(2, "Mystery", year=1999, creator="Nobody"),
    ]


class SyncFeaturedListCommandTests(FeaturedTestCase):
    def call(self, *args):
        out = StringIO()
        definition = make_definition(guards=Guards(max_title_searches=0))
        with fake_adapter(FetchResult(entries=entries(), edition="Week of Oct 13")), registry.override_definitions([definition]):
            call_command("sync_featured_list", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_prints_entries_counts_and_unresolved_rows(self):
        output = self.call("test-list", "--dry-run")
        self.assertIn("Test List (test-list) via test: 2 entries, edition 'Week of Oct 13'", output)
        self.assertIn("#1 Known (2000) -> tmdb:movie:1 [id]", output)
        self.assertIn("#2 Mystery (1999) / Nobody -> UNRESOLVED (no match)", output)
        self.assertIn("Resolved 1/2 (50.0%); 1 unique items; 1 unresolved; 0 title searches.", output)
        self.assertIn("Unresolved:", output)
        self.assertIn("{t:mystery|1999}", output)
        self.assertIn("Dry run, nothing written. guards pass", output)
        self.assertFalse(CustomList.objects.exists())
        self.assertFalse(FeaturedListSync.objects.exists())

    def test_sync_writes_the_list(self):
        output = self.call("test-list")
        self.assertIn("Status ok; list", output)
        self.assertTrue(CustomList.objects.filter(import_source_id="test-list").exists())

    def test_unknown_key(self):
        with self.assertRaisesMessage(CommandError, "Unknown featured list 'nope'"):
            call_command("sync_featured_list", "nope", stdout=StringIO())


class ReportCommandTests(FeaturedTestCase):
    def test_text_and_json_reports(self):
        definition = make_definition()
        self.run_list(definition, entries()[:1])
        with registry.override_definitions([definition]), fake_adapter([]), patch("lists.featured.overrides._load", return_value=()):
            text = StringIO()
            call_command("featured_lists_report", stdout=text)
            raw = StringIO()
            call_command("featured_lists_report", "--json", stdout=raw)

        self.assertIn("1 defined, 1 healthy, 0 failing", text.getvalue())
        self.assertIn("ok        test-list", text.getvalue())
        data = json.loads(raw.getvalue())
        row = data["lists"][0]
        self.assertEqual((row["key"], row["status"], row["resolved_count"], row["entry_count"]), ("test-list", "ok", 1, 1))
        self.assertFalse(row["stale"])
        self.assertEqual(data["problems"], [])
        self.assertEqual(data["orphans"], [])
