"""CI's Database checks run the suite in three parts (scripts/test_shards.py). Together
the parts must be exactly the full suite: no test left out, none run twice."""

import importlib.util
from pathlib import Path

from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "test_shards.py"
SRC = SCRIPT.parent.parent / "src"


def load_script():
    spec = importlib.util.spec_from_file_location("test_shards", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ids(labels):
    runner = DiscoverRunner(top_level=str(SRC), verbosity=0)
    return [test.id() for test in runner.build_suite(labels or [str(SRC)])]


class TestShardsTests(SimpleTestCase):
    def test_three_parts_are_the_whole_suite_once(self):
        parts = load_script().split(3)

        everything = sorted(test_ids([]))
        in_parts = sorted(test_id for part in parts for test_id in test_ids(part))

        self.assertTrue(all(parts))
        self.assertEqual(in_parts, everything)
