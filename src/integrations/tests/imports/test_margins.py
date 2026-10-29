from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from app import book_tracking
from app.models import Book, BookSession, DiaryEntry, Item, MediaTypes, Sources, Status
from integrations.imports.margins.importer import MarginsImporter
from integrations.imports.margins.parser import READ, TO_READ, parse_export
from integrations.imports.margins.resolver import MarginsResolver
from social.models import ProgressChange

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "margins" / "sample.csv"


class FakeResolver:
    IDS = {
        "Twice Read": "201",
        "Undated Favourite": "202",
        "Given Up": "203",
        "Current Read": "204",
        "Wishlist Book": "205",
        "Shelf Book": "206",
    }

    def resolve_rows(self, rows):
        return {
            row.index: {
                "media_id": self.IDS[row.title],
                "source": Sources.HARDCOVER.value,
                "title": row.title,
                "image": f"https://example.com/{self.IDS[row.title]}.jpg",
            }
            for row in rows
            if row.title in self.IDS
        }


def _item(media_id, title):
    return Item.objects.create(
        media_id=media_id,
        source=Sources.HARDCOVER.value,
        media_type=MediaTypes.BOOK.value,
        title=title,
        image="https://example.com/existing.jpg",
    )


class ImportMarginsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="margins", password="password")

    def _import(self, mode="new"):
        with FIXTURE.open("rb") as file:
            return MarginsImporter(file.read(), self.user, mode, resolver=FakeResolver()).import_data()

    def _book(self, media_id):
        return Book.objects.get(user=self.user, item__media_id=media_id)

    def test_parse_csv(self):
        with FIXTURE.open("rb") as file:
            rows = parse_export(file.read())

        first = rows[0]
        self.assertEqual(first.state, READ)
        self.assertEqual(first.start_date, date(2025, 3, 1))
        self.assertEqual(first.end_date, date(2025, 3, 4))
        # Quarter stars round up to the next half star: 3.75 is 4 stars.
        self.assertEqual(first.rating, 8)
        self.assertIsNone(rows[1].end_date)
        self.assertEqual(rows[5].authors, ("Author Five", "Co Author"))
        self.assertEqual(rows[6].state, TO_READ)
        self.assertIsNone(rows[9].state)

    def test_future_dates_and_zero_ratings_are_unknown(self):
        rows = parse_export(
            b"Title,Author(s),Status,Start Date,End Date,Rating,Review\n"
            b"Later,Someone,Finished,2999-01-01,2999-01-02,0,\n",
        )

        self.assertIsNone(rows[0].start_date)
        self.assertIsNone(rows[0].end_date)
        self.assertIsNone(rows[0].rating)

    def test_full_import(self):
        counts, warnings = self._import()

        self.assertEqual(
            counts,
            {MediaTypes.BOOK.value: 6, "diary": 1, "ratings": 2, "reviews": 1},
        )
        self.assertEqual(
            warnings.splitlines(),
            [
                "Odd Status by Author Eight: Unknown Margins status 'Borrowed'.",
                "Nameless: Read Apr 29, 2025, 5★. No author in the Margins file.",
                "Missing Book by Author Seven: Read May 11, 2025, 4★, with a review. No matching book found.",
            ],
        )

        twice = self._book("201")
        self.assertEqual(twice.status, Status.COMPLETED.value)
        self.assertEqual(twice.score, 8)
        self.assertTrue(twice.completed_manually)
        entry = DiaryEntry.objects.get(user=self.user, item=twice.item)
        self.assertEqual(entry.consumed_at.date(), date(2025, 3, 4))
        self.assertEqual(entry.rating, 8)
        self.assertEqual(entry.review, "Loved it again.")
        self.assertEqual(entry.import_source, "margins")
        # The undated read counts as the earlier one.
        self.assertTrue(entry.is_rewatch)
        journey = entry.book_journey
        self.assertEqual(journey.start_date.date(), date(2025, 3, 1))
        self.assertEqual(journey.end_date.date(), date(2025, 3, 4))
        self.assertEqual(twice.completion_diary_entry, entry)

        undated = self._book("202")
        self.assertEqual(undated.status, Status.COMPLETED.value)
        self.assertEqual(undated.score, 9)
        self.assertFalse(DiaryEntry.objects.filter(item=undated.item).exists())

        dnf = self._book("203")
        self.assertEqual(dnf.status, Status.DROPPED.value)
        dnf_journey = BookSession.objects.get(related_book=dnf)
        self.assertEqual(dnf_journey.status, Status.DROPPED.value)
        self.assertEqual(dnf_journey.start_date.date(), date(2024, 1, 2))
        self.assertEqual(dnf_journey.end_date.date(), date(2024, 2, 1))
        self.assertFalse(DiaryEntry.objects.filter(item=dnf.item).exists())

        current = self._book("204")
        self.assertEqual(current.status, Status.IN_PROGRESS.value)
        self.assertEqual(current.current_session.start_date.date(), date(2025, 9, 13))

        self.assertEqual(self._book("205").status, Status.PLANNING.value)
        self.assertEqual(self._book("206").status, Status.PLANNING.value)
        # Old starts would show in activity as starting the book today.
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exists())

    def test_a_book_breaking_a_reading_rule_is_reported_and_skipped(self):
        error = ValidationError("Journey starts cannot follow their end date.")
        with patch("app.book_tracking.update_journey", side_effect=error):
            counts, warnings = self._import()

        self.assertEqual(counts, {MediaTypes.BOOK.value: 5, "ratings": 1})
        self.assertFalse(Book.objects.filter(item__media_id="201").exists())
        self.assertFalse(DiaryEntry.objects.filter(item__media_id="201").exists())
        self.assertIn(
            "Twice Read by Author One: Read Mar 4, 2025, 4★, with a review. "
            "Journey starts cannot follow their end date.",
            warnings,
        )

    def test_new_mode_leaves_books_already_in_spine_and_reimports_nothing(self):
        item = _item("201", "Twice Read")
        Book.objects.create(user=self.user, item=item, status=Status.PLANNING.value)

        counts, _ = self._import()
        again, _ = self._import()

        self.assertEqual(counts[MediaTypes.BOOK.value], 5)
        self.assertEqual(self._book("201").status, Status.PLANNING.value)
        self.assertFalse(DiaryEntry.objects.filter(item=item).exists())
        self.assertEqual(again, {})
        self.assertEqual(Book.objects.filter(user=self.user).count(), 6)

    def test_replace_mode_replaces_books_in_the_file_only(self):
        in_file = _item("201", "Twice Read")
        other = _item("299", "Other Book")
        book_tracking.complete(self.user, in_file, completion_date=date(2020, 1, 1), rating=Decimal(2))
        book_tracking.complete(self.user, other, completion_date=date(2020, 1, 1))

        self._import(mode="overwrite")

        twice = self._book("201")
        self.assertEqual(twice.score, 8)
        self.assertEqual(
            [entry.consumed_at.date() for entry in DiaryEntry.objects.filter(item=in_file)],
            [date(2025, 3, 4)],
        )
        self.assertEqual(BookSession.objects.filter(related_book=twice).count(), 1)
        self.assertEqual(self._book("299").status, Status.COMPLETED.value)
        self.assertTrue(DiaryEntry.objects.filter(item=other).exists())


class MarginsResolverTests(TestCase):
    def _rows(self, csv_rows):
        header = b"Title,Author(s),Status,Start Date,End Date,Rating,Review\n"
        return parse_export(header + csv_rows)

    def test_needs_title_and_author_to_match(self):
        rows = self._rows(
            b"Hooked,Asako Yuzuki,Finished,,,,\n"
            b"Hooked,Asako Yuzuki,Want to Read,,,,\n"
            b"Kitchen Confidential,Anthony Bourdain,Reading,,,,\n"
            b"Independence,,Finished,,,,\n"
            b"Unknown Book,Nobody,Unread,,,,\n",
        )
        calls = []
        hardcover = {
            "Hooked Asako Yuzuki": [
                {"media_id": "wrong", "source": "hardcover", "title": "Hooked", "author_name": ["Emily McIntire"]},
                {"media_id": "hooked", "source": "hardcover", "title": "Hooked", "author_name": ["Asako Yuzuki"]},
            ],
            "Kitchen Confidential Anthony Bourdain": [
                {
                    "media_id": "kc",
                    "source": "hardcover",
                    "title": "Kitchen Confidential: Adventures in the Culinary Underbelly",
                    "author_name": ["Anthony Bourdain"],
                },
            ],
        }

        def search(_media_type, query, _page, source, **_kwargs):
            calls.append((query, source))
            if source == Sources.HARDCOVER.value:
                return {"results": hardcover.get(query, [])}
            return {"results": []}

        with patch("integrations.imports.margins.resolver.services.search", side_effect=search):
            resolved = MarginsResolver().resolve_rows(rows)

        self.assertEqual(
            {index: result["media_id"] for index, result in resolved.items()},
            {1: "hooked", 2: "hooked", 3: "kc"},
        )
        self.assertEqual(
            calls,
            [
                ("Hooked Asako Yuzuki", Sources.HARDCOVER.value),
                ("Kitchen Confidential Anthony Bourdain", Sources.HARDCOVER.value),
                ("Unknown Book Nobody", Sources.HARDCOVER.value),
                ("Unknown Book Nobody", Sources.OPENLIBRARY.value),
            ],
        )

    def test_prefers_the_exact_title_and_matches_names_in_any_order(self):
        rows = self._rows(
            b"The Namesake,Jhumpa Lahiri,Unread,,,,\n"
            b"Almond,Sohn Won-Pyung,Finished,,,,\n"
            b"Beloved,Toni Morrison,Unread,,,,\n",
        )
        guide = {"source": "hardcover", "title": "Toni Morrison's Beloved", "author_name": ["Harold Bloom"]}
        hardcover = {
            "The Namesake Jhumpa Lahiri": [
                {
                    "media_id": "film",
                    "source": "hardcover",
                    "title": "The Namesake: A Portrait of the Film",
                    "author_name": ["Mira Nair", "Jhumpa Lahiri"],
                },
                {"media_id": "novel", "source": "hardcover", "title": "The Namesake", "author_name": ["Jhumpa Lahiri"]},
            ],
            "Almond Sohn Won-Pyung": [
                {"media_id": "almond", "source": "hardcover", "title": "Almond", "author_name": ["Won-pyung Sohn"]},
            ],
            # The novel is sixth, after study guides.
            "Beloved Toni Morrison": [
                *({**guide, "media_id": f"guide-{number}"} for number in range(5)),
                {"media_id": "beloved", "source": "hardcover", "title": "Beloved", "author_name": ["Toni Morrison"]},
            ],
        }

        def search(_media_type, query, _page, source, **_kwargs):
            return {"results": hardcover.get(query, []) if source == Sources.HARDCOVER.value else []}

        with patch("integrations.imports.margins.resolver.services.search", side_effect=search):
            resolved = MarginsResolver().resolve_rows(rows)

        self.assertEqual(
            {index: result["media_id"] for index, result in resolved.items()},
            {1: "novel", 2: "almond", 3: "beloved"},
        )

    def test_falls_back_to_open_library_translated_edition(self):
        row = self._rows(b"The Master and Margarita,Mikhail Bulgakov,Stopped,,,,\n")[0]

        def search(_media_type, _query, _page, source, **_kwargs):
            if source == Sources.OPENLIBRARY.value:
                return {
                    "results": [
                        {
                            "media_id": "ol-1",
                            "source": "openlibrary",
                            "title": "Il maestro e Margherita: The Master and Margarita",
                            "author_name": ["M. Bulgakov"],
                        },
                    ],
                }
            return {"results": []}

        with patch("integrations.imports.margins.resolver.services.search", side_effect=search):
            result = MarginsResolver().resolve_one(row)

        self.assertEqual(result["media_id"], "ol-1")


class MarginsApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="api", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_post_queues_task_with_csv(self):
        task = Mock()
        task.delay.return_value = SimpleNamespace(id="margins-task")
        task_map = {"margins": task}

        with FIXTURE.open("rb") as file:
            upload = SimpleUploadedFile("margins-reading.csv", file.read(), content_type="text/csv")

        with patch.dict("api.views.imports.TASKS_BY_SOURCE", task_map, clear=True):
            with patch.dict("api.services.imports.TASKS_BY_SOURCE", task_map, clear=True):
                response = self.client.post(
                    "/api/v1/imports/margins/",
                    {"mode": "overwrite", "file": upload},
                    format="multipart",
                )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "margins-task")
        self.assertTrue(task.delay.call_args.kwargs["file_path"].endswith(".csv"))
        self.assertEqual(task.delay.call_args.kwargs["mode"], "overwrite")

    def test_post_requires_file(self):
        response = self.client.post("/api/v1/imports/margins/", {"mode": "new"}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
