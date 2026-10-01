import gzip
import importlib
import io
import os
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Anime,
    DiaryEntry,
    Item,
    Manga,
    MediaTypes,
    Sources,
    Status,
    Tag,
)
from integrations import tasks
from integrations.imports.helpers import MediaImportError
from integrations.imports.mal_export import parser
from integrations.imports.mal_export.importer import IMPORT_SOURCE, MALExportImporter
from integrations.imports.mal_export.parser import parse_export, parse_mal_date
from social.models import Activity

mal_export_importer = importlib.import_module("integrations.imports.mal_export.importer")

MOCK_DATA = Path(__file__).resolve().parent.parent / "mock_data"
ANIME_FIXTURE = MOCK_DATA / "import_mal_export_anime.xml"
MANGA_FIXTURE = MOCK_DATA / "import_mal_export_manga.xml"


def _metadata(media_type, media_id):
    return {
        "media_id": media_id,
        "title": f"MAL {media_type} {media_id}",
        "image": f"https://cdn.myanimelist.net/images/{media_type}/{media_id}.jpg",
        "max_progress": None,
    }


def _anime_metadata(media_id, **_kwargs):
    return _metadata(MediaTypes.ANIME.value, media_id)


def _manga_metadata(media_id):
    return _metadata(MediaTypes.MANGA.value, media_id)


def _xml(*entries, root="myanimelist"):
    """Build a tiny anime export from (id, status, extra-fields) tuples."""
    body = []
    for media_id, status, extra in entries:
        fields = {
            "series_animedb_id": media_id,
            "series_title": f"<![CDATA[Anime {media_id}]]>",
            "series_episodes": "12",
            "my_watched_episodes": "3",
            "my_start_date": "0000-00-00",
            "my_finish_date": "0000-00-00",
            "my_score": "0",
            "my_status": status,
            **extra,
        }
        body.append(
            "<anime>" + "".join(f"<{k}>{v}</{k}>" for k, v in fields.items()) + "</anime>",
        )
    return f'<?xml version="1.0" encoding="UTF-8" ?><{root}>{"".join(body)}</{root}>'.encode()


class MALExportParserTests(TestCase):
    def test_parse_anime_export(self):
        export = parse_export(ANIME_FIXTURE.read_bytes())

        entries = {entry.media_id: entry for entry in export.entries}
        self.assertEqual(len(entries), 9)
        bebop = entries["1"]
        self.assertEqual(bebop.media_type, MediaTypes.ANIME.value)
        self.assertEqual(bebop.title, "Cowboy Bebop")
        self.assertEqual(bebop.status, Status.COMPLETED.value)
        self.assertEqual(bebop.progress, 26)
        self.assertEqual(bebop.total, 26)
        self.assertEqual(bebop.start_date, date(2021, 1, 2))
        self.assertEqual(bebop.finish_date, date(2021, 1, 20))
        self.assertEqual(bebop.score, Decimal(9))
        self.assertEqual(bebop.comments, "Private note: rewatch the finale")
        self.assertEqual(bebop.times_consumed, 1)
        self.assertEqual(bebop.tags, ["classic", "space"])
        self.assertEqual(bebop.series_type, "TV")

        self.assertEqual(entries["5114"].start_date, date(2021, 1, 1))
        self.assertEqual(entries["5114"].finish_date, date(2021, 3, 1))
        self.assertIsNone(entries["5114"].score)
        self.assertEqual(entries["21"].status, Status.IN_PROGRESS.value)
        self.assertEqual(entries["30"].status, Status.PAUSED.value)
        self.assertEqual(entries["1535"].status, Status.DROPPED.value)
        self.assertEqual(entries["6547"].status, Status.PLANNING.value)

        rewatch = entries["457"]
        self.assertTrue(rewatch.repeating)
        self.assertEqual(rewatch.status, Status.IN_PROGRESS.value)
        self.assertEqual(rewatch.progress, 5)
        self.assertTrue(rewatch.completed_once)

        self.assertIn("Broken Row: Missing or invalid MyAnimeList ID 'not-a-number'", export.warnings)
        self.assertIn("Kimi no Na wa.: Unknown MyAnimeList status 'Binge-watching'", export.warnings)

    def test_parse_manga_export(self):
        export = parse_export(MANGA_FIXTURE.read_bytes())

        entries = {entry.media_id: entry for entry in export.entries}
        self.assertEqual(set(entries), {"2", "11", "1706", "13"})
        self.assertTrue(all(entry.media_type == MediaTypes.MANGA.value for entry in entries.values()))
        self.assertEqual(entries["2"].status, Status.IN_PROGRESS.value)
        self.assertEqual(entries["2"].progress, 120)
        self.assertFalse(entries["2"].repeating)
        naruto = entries["11"]
        self.assertEqual(naruto.status, Status.COMPLETED.value)
        self.assertEqual(naruto.total, 700)
        self.assertEqual(naruto.times_consumed, 2)
        self.assertEqual(naruto.start_date, date(2018, 1, 1))
        self.assertEqual(entries["1706"].status, Status.PLANNING.value)
        rereading = entries["13"]
        self.assertTrue(rereading.repeating)
        self.assertEqual(rereading.status, Status.IN_PROGRESS.value)
        self.assertEqual(export.warnings, [])

    def test_gzip_detected_by_magic_bytes(self):
        compressed = gzip.compress(ANIME_FIXTURE.read_bytes())

        from_bytes = parse_export(compressed)
        from_file = parse_export(io.BytesIO(compressed))

        self.assertEqual(len(from_bytes.entries), 9)
        self.assertEqual(
            [entry.media_id for entry in from_file.entries],
            [entry.media_id for entry in from_bytes.entries],
        )

    def test_partial_and_invalid_dates(self):
        self.assertIsNone(parse_mal_date("0000-00-00"))
        self.assertIsNone(parse_mal_date(""))
        self.assertIsNone(parse_mal_date(None))
        self.assertEqual(parse_mal_date("2021-03-00"), date(2021, 3, 1))
        self.assertEqual(parse_mal_date("2021-00-00"), date(2021, 1, 1))
        self.assertEqual(parse_mal_date("2021-00-15"), date(2021, 1, 15))
        self.assertIsNone(parse_mal_date("0000-03-04"))
        self.assertIsNone(parse_mal_date("2021-02-30"))
        self.assertIsNone(parse_mal_date("2021-13-01"))
        self.assertIsNone(parse_mal_date("garbage"))

    def test_status_mapping_including_numeric_codes(self):
        cases = {
            "Watching": Status.IN_PROGRESS.value,
            "reading": Status.IN_PROGRESS.value,
            " COMPLETED ": Status.COMPLETED.value,
            "On-Hold": Status.PAUSED.value,
            "on hold": Status.PAUSED.value,
            "Dropped": Status.DROPPED.value,
            "Plan to Watch": Status.PLANNING.value,
            "Plan to Read": Status.PLANNING.value,
            "1": Status.IN_PROGRESS.value,
            "2": Status.COMPLETED.value,
            "3": Status.PAUSED.value,
            "4": Status.DROPPED.value,
            "6": Status.PLANNING.value,
        }
        payload = _xml(*[(str(index), status, {}) for index, status in enumerate(cases, start=1)])

        export = parse_export(payload)

        self.assertEqual([entry.status for entry in export.entries], list(cases.values()))
        unknown = parse_export(_xml(("99", "5", {})))
        self.assertEqual(unknown.entries, [])
        self.assertEqual(unknown.warnings, ["Anime 99: Unknown MyAnimeList status '5'"])

    def test_scores(self):
        export = parse_export(
            _xml(
                ("1", "Completed", {"my_score": "0"}),
                ("2", "Completed", {"my_score": ""}),
                ("3", "Completed", {"my_score": "10"}),
                ("4", "Completed", {"my_score": "11"}),
                ("5", "Completed", {"my_score": "abc"}),
            ),
        )

        self.assertEqual(
            [entry.score for entry in export.entries],
            [None, None, Decimal(10), None, None],
        )

    def test_unfriendly_files_raise_friendly_errors(self):
        billion_laughs = (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
            b"<myanimelist><anime><series_title>&lol2;</series_title></anime></myanimelist>"
        )
        external_entity = (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b"<myanimelist>&xxe;</myanimelist>"
        )
        cases = {
            "wrong root": (b"<goodreads><book/></goodreads>", "doesn't look like a MyAnimeList export"),
            "not xml": (b"Title,Author\nDune,Herbert\n", "doesn't look like a MyAnimeList export"),
            "binary": (b"PK\x03\x04\x00\x00garbage", "doesn't look like a MyAnimeList export"),
            "truncated": (b"<myanimelist><anime>", "doesn't look like a MyAnimeList export"),
            "empty": (b"", "empty"),
            "whitespace": (b"  \n\t ", "empty"),
            "billion laughs": (billion_laughs, "aren't allowed"),
            "external entity": (external_entity, "aren't allowed"),
            "corrupt gzip": (b"\x1f\x8b\x08\x00garbage", "couldn't be decompressed"),
        }
        for label, (payload, message) in cases.items():
            with self.subTest(label), self.assertRaises(MediaImportError) as context:
                parse_export(payload)
            self.assertIn(message, str(context.exception))

    def test_gzip_bomb_is_capped(self):
        bomb = gzip.compress(b"<myanimelist>" + b" " * 4096 + b"</myanimelist>")

        with patch.object(parser, "MAX_DECOMPRESSED_BYTES", 1024):
            with self.assertRaises(MediaImportError) as context:
                parse_export(bomb)

        self.assertIn("too large", str(context.exception))

    def test_illegal_control_characters_are_tolerated(self):
        payload = _xml(("1", "Completed", {"my_comments": "<![CDATA[bad\x07char]]>"}))

        export = parse_export(payload)

        self.assertEqual(export.entries[0].comments, "badchar")


@patch("app.providers.mal.manga", side_effect=_manga_metadata)
@patch("app.providers.mal.anime", side_effect=_anime_metadata)
class MALExportImporterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="malexport", password="password")

    def _import(self, fixture=ANIME_FIXTURE, mode="new", user=None):
        with fixture.open("rb") as file:
            return MALExportImporter(file, user or self.user, mode).import_data()

    def test_full_anime_import(self, anime_mock, _manga_mock):
        counts, warnings = self._import()

        self.assertEqual(counts, {"anime": 9, "diary": 3, "ratings": 6})
        self.assertIn("Broken Row: Missing or invalid MyAnimeList ID", warnings)
        self.assertIn("Kimi no Na wa.: Unknown MyAnimeList status 'Binge-watching'", warnings)
        self.assertEqual(anime_mock.call_count, 9)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 9)
        self.assertEqual(
            Anime.objects.filter(user=self.user).values("item").distinct().count(),
            9,
        )

        bebop = Anime.objects.get(user=self.user, item__media_id="1")
        self.assertEqual(bebop.item.source, Sources.MAL.value)
        self.assertEqual(bebop.item.title, "MAL anime 1")
        self.assertEqual(bebop.item.image, "https://cdn.myanimelist.net/images/anime/1.jpg")
        self.assertEqual(bebop.status, Status.COMPLETED.value)
        self.assertEqual(bebop.progress, 26)
        self.assertEqual(bebop.score, Decimal(9))
        self.assertEqual(bebop.start_date, datetime(2021, 1, 2, tzinfo=UTC))
        self.assertEqual(bebop.end_date, datetime(2021, 1, 20, tzinfo=UTC))
        self.assertEqual(
            bebop.notes,
            "Imported from MyAnimeList\nPrivate note: rewatch the finale\n"
            "Times watched: 1\nTags: classic, space",
        )
        self.assertEqual(bebop.history.count(), 1)

        fmab = Anime.objects.get(user=self.user, item__media_id="5114")
        self.assertEqual(fmab.progress, 64)
        self.assertIsNone(fmab.score)
        self.assertEqual(fmab.start_date, datetime(2021, 1, 1, tzinfo=UTC))

        one_piece = Anime.objects.get(user=self.user, item__media_id="21")
        self.assertEqual(one_piece.status, Status.IN_PROGRESS.value)
        self.assertEqual(one_piece.progress, 72)
        self.assertIsNone(one_piece.end_date)
        self.assertEqual(one_piece.notes, "Imported from MyAnimeList")
        self.assertEqual(Anime.objects.get(user=self.user, item__media_id="30").status, Status.PAUSED.value)
        self.assertEqual(Anime.objects.get(user=self.user, item__media_id="1535").status, Status.DROPPED.value)
        self.assertEqual(Anime.objects.get(user=self.user, item__media_id="6547").status, Status.PLANNING.value)
        rewatch = Anime.objects.get(user=self.user, item__media_id="457")
        self.assertEqual(rewatch.status, Status.IN_PROGRESS.value)
        self.assertEqual(rewatch.progress, 5)
        future = Anime.objects.get(user=self.user, item__media_id="9253")
        self.assertEqual(future.end_date, datetime(2099, 1, 1, tzinfo=UTC))

    def test_diary_only_for_completed_dated_entries(self, *_mocks):
        self._import()

        entries = {
            entry.item.media_id: entry
            for entry in DiaryEntry.objects.filter(user=self.user).select_related("item")
        }
        self.assertEqual(set(entries), {"1", "5114", "457"})

        bebop = entries["1"]
        self.assertEqual(bebop.consumed_at, datetime(2021, 1, 20, tzinfo=UTC))
        self.assertEqual(bebop.rating, Decimal(9))
        self.assertEqual(bebop.review, "")
        self.assertFalse(bebop.is_rewatch)
        self.assertEqual(bebop.import_source, IMPORT_SOURCE)
        self.assertEqual(bebop.import_source_id, "anime:1:finish")
        self.assertEqual(sorted(tag.name for tag in bebop.tags.all()), ["classic", "space"])

        self.assertEqual(entries["5114"].consumed_at, datetime(2021, 3, 1, tzinfo=UTC))
        self.assertIsNone(entries["5114"].rating)
        self.assertEqual(entries["457"].consumed_at, datetime(2020, 12, 25, tzinfo=UTC))
        self.assertFalse(Activity.objects.exists())

    def test_manga_import(self, _anime_mock, manga_mock):
        counts, warnings = self._import(MANGA_FIXTURE)

        self.assertEqual(counts, {"manga": 4, "diary": 2, "ratings": 3})
        self.assertIsNone(warnings)
        self.assertEqual(manga_mock.call_count, 4)
        naruto = Manga.objects.get(user=self.user, item__media_id="11")
        self.assertEqual(naruto.item.media_type, MediaTypes.MANGA.value)
        self.assertEqual(naruto.progress, 700)
        self.assertIn("Times read: 2", naruto.notes)
        self.assertEqual(Manga.objects.get(user=self.user, item__media_id="2").progress, 120)
        self.assertEqual(Manga.objects.get(user=self.user, item__media_id="13").status, Status.IN_PROGRESS.value)
        entry = DiaryEntry.objects.get(user=self.user, item__media_id="11")
        self.assertEqual(entry.import_source_id, "manga:11:finish")
        self.assertEqual(entry.consumed_at, datetime(2019, 5, 10, tzinfo=UTC))
        self.assertEqual([tag.name for tag in entry.tags.all()], ["shonen"])

    def test_importing_twice_is_idempotent(self, anime_mock, _manga_mock):
        self._import()
        anime_mock.reset_mock()

        counts, _warnings = self._import()
        self.assertEqual(counts, {})
        self.assertEqual(anime_mock.call_count, 0)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 9)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user).count(), 3)

        for _ in range(2):
            counts, _warnings = self._import(mode="overwrite")
            self.assertEqual(counts, {"anime": 9, "diary": 3, "ratings": 6})
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 9)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user).count(), 3)
        self.assertEqual(Item.objects.filter(source=Sources.MAL.value).count(), 9)
        self.assertEqual(Tag.objects.get(name="classic").usage_count, 1)
        self.assertEqual(anime_mock.call_count, 0)

    def test_new_mode_skips_titles_already_tracked(self, *_mocks):
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://example.com/bebop.jpg",
        )
        Anime.objects.bulk_create(
            [Anime(user=self.user, item=item, status=Status.PLANNING.value, score=3, notes="keep me")],
        )

        counts, _warnings = self._import()

        self.assertEqual(counts["anime"], 8)
        self.assertEqual(counts["diary"], 2)
        existing = Anime.objects.get(user=self.user, item=item)
        self.assertEqual(existing.status, Status.PLANNING.value)
        self.assertEqual(existing.score, 3)
        self.assertEqual(existing.notes, "keep me")
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
        self.assertEqual(Item.objects.get(pk=item.pk).image, "https://example.com/bebop.jpg")

    def test_overwrite_replaces_only_titles_in_file(self, *_mocks):
        bebop = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://example.com/bebop.jpg",
        )
        untouched = Item.objects.create(
            media_id="999",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Not In File",
            image="https://example.com/other.jpg",
        )
        Anime.objects.bulk_create(
            [
                Anime(user=self.user, item=bebop, status=Status.PLANNING.value),
                Anime(user=self.user, item=bebop, status=Status.COMPLETED.value),
                Anime(user=self.user, item=untouched, status=Status.DROPPED.value),
            ],
        )
        with patch("app.signals.update_daily_statistics.delay"):
            manual = DiaryEntry.objects.create(
                user=self.user,
                item=bebop,
                consumed_at=datetime(2020, 5, 5, 18, tzinfo=UTC),
                review="Hand-written",
            )
            untouched_entry = DiaryEntry.objects.create(
                user=self.user,
                item=untouched,
                consumed_at=datetime(2020, 5, 5, tzinfo=UTC),
                import_source=IMPORT_SOURCE,
                import_source_id="anime:999:finish",
            )

        self._import(mode="overwrite")
        self._import(mode="overwrite")

        tracking = Anime.objects.get(user=self.user, item=bebop)
        self.assertEqual(tracking.status, Status.COMPLETED.value)
        self.assertEqual(tracking.score, Decimal(9))
        self.assertTrue(DiaryEntry.objects.filter(id=manual.id).exists())
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=bebop).count(), 2)
        self.assertTrue(Anime.objects.filter(user=self.user, item=untouched).exists())
        self.assertTrue(DiaryEntry.objects.filter(id=untouched_entry.id).exists())

    def test_existing_same_day_log_is_not_duplicated(self, *_mocks):
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://example.com/bebop.jpg",
        )
        with patch("app.signals.update_daily_statistics.delay"):
            DiaryEntry.objects.create(
                user=self.user,
                item=item,
                consumed_at=datetime(2021, 1, 20, 21, 30, tzinfo=UTC),
            )

        counts, _warnings = self._import()

        self.assertEqual(counts["diary"], 2)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 1)

    def test_metadata_failure_still_imports(self, anime_mock, _manga_mock):
        anime_mock.side_effect = RuntimeError("MAL is down")
        Item.objects.create(
            media_id="21",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="One Piece",
            image=settings.IMG_NONE,
        )

        counts, _warnings = self._import()

        self.assertEqual(counts["anime"], 9)
        bebop = Item.objects.get(media_id="1", source=Sources.MAL.value)
        self.assertEqual(bebop.title, "Cowboy Bebop")
        self.assertEqual(bebop.image, settings.IMG_NONE)
        self.assertEqual(Item.objects.get(media_id="21").image, settings.IMG_NONE)

    def test_metadata_refreshes_placeholder_images_only(self, anime_mock, _manga_mock):
        placeholder = Item.objects.create(
            media_id="21",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="One Piece",
            image=settings.IMG_NONE,
        )
        Item.objects.create(
            media_id="30",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Evangelion",
            image="https://example.com/eva.jpg",
        )

        self._import()

        fetched = {call.args[0] for call in anime_mock.call_args_list}
        self.assertIn("21", fetched)
        self.assertNotIn("30", fetched)
        placeholder.refresh_from_db()
        self.assertEqual(placeholder.image, "https://cdn.myanimelist.net/images/anime/21.jpg")
        self.assertEqual(placeholder.title, "One Piece")

    def test_metadata_budget_falls_back_without_fetching(self, anime_mock, _manga_mock):
        with patch.object(mal_export_importer, "METADATA_BUDGET_SECONDS", -1):
            counts, _warnings = self._import()

        self.assertEqual(counts["anime"], 9)
        self.assertEqual(anime_mock.call_count, 0)
        self.assertEqual(Item.objects.get(media_id="1").image, settings.IMG_NONE)

    def test_placeholder_titles_are_queued_for_backfill(self, anime_mock, _manga_mock):
        anime_mock.side_effect = RuntimeError("MAL is down")

        with (
            patch("integrations.tasks.backfill_mal_metadata.delay") as backfill,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self._import()

        backfill.assert_called_once()
        queued = backfill.call_args.args[0]
        self.assertEqual(
            set(queued),
            set(
                Item.objects.filter(
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                ).values_list("id", flat=True),
            ),
        )
        self.assertEqual(len(queued), len(set(queued)))

    def test_no_backfill_when_every_poster_was_fetched(self, *_mocks):
        with (
            patch("integrations.tasks.backfill_mal_metadata.delay") as backfill,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self._import()

        backfill.assert_not_called()

    def test_invalid_file_raises_without_writes(self, *_mocks):
        with self.assertRaises(MediaImportError):
            MALExportImporter(io.BytesIO(b"not xml"), self.user, "new").import_data()

        self.assertFalse(Anime.objects.exists())

    def test_other_users_are_isolated(self, *_mocks):
        other = get_user_model().objects.create_user(username="other", password="password")
        self._import(user=other)

        counts, _warnings = self._import()

        self.assertEqual(counts["anime"], 9)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 9)
        self.assertEqual(Anime.objects.filter(user=other).count(), 9)


@patch("app.providers.mal.manga", side_effect=_manga_metadata)
@patch("app.providers.mal.anime", side_effect=_anime_metadata)
@patch("events.tasks.reload_calendar.delay")
class MALExportTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="maltask", password="password")

    def _temp_copy(self, payload):
        fd, path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
        return path

    def test_task_imports_gzip_and_unlinks_temp_file(self, *_mocks):
        path = self._temp_copy(gzip.compress(ANIME_FIXTURE.read_bytes()))

        message = tasks.import_mal_export(path, self.user.id, "new")

        self.assertFalse(Path(path).exists())
        self.assertTrue(message.startswith("Imported 9 Anime, 3 diary entries and 6 ratings."))
        self.assertIn("Unknown MyAnimeList status", message)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 9)

    def test_task_unlinks_temp_file_on_failure(self, *_mocks):
        path = self._temp_copy(b"<library/>")

        with self.assertRaises(MediaImportError):
            tasks.import_mal_export(path, self.user.id, "new")

        self.assertFalse(Path(path).exists())


class MALExportHistoryTests(TestCase):
    def test_import_history_and_source_display(self):
        from django_celery_results.models import TaskResult

        from users.templatetags.user_tags import source_display

        user = get_user_model().objects.create_user(username="history", password="password")
        TaskResult.objects.create(
            task_id="mal-export-task",
            task_name="Import from MyAnimeList export",
            task_kwargs=f"{{'file_path': '/tmp/x.xml', 'user_id': {user.id}, 'mode': 'new'}}",
            status="SUCCESS",
            result='"Imported 1 anime."',
            date_done=timezone.now(),
        )

        results = user.get_import_tasks()["results"]

        self.assertEqual([result["source"] for result in results], ["mal_export"])
        self.assertEqual(results[0]["summary"], "Imported 1 anime.")
        self.assertIn("MyAnimeList", source_display("mal_export"))


class MALMetadataBackfillTests(TestCase):
    def _item(self, media_id, image=settings.IMG_NONE, media_type=MediaTypes.ANIME.value):
        return Item.objects.create(
            media_id=media_id,
            source=Sources.MAL.value,
            media_type=media_type,
            title=f"Title {media_id}",
            image=image,
        )

    def test_fills_placeholders_and_reports_failures(self):
        ok = self._item("1")
        manga = self._item("2", media_type=MediaTypes.MANGA.value)
        failing = self._item("3")
        has_image = self._item("4", image="https://example.com/keep.jpg")

        def fetcher(media_type, media_id):
            if media_id == "3":
                raise RuntimeError("429")
            return _metadata(media_type, media_id)

        failed = mal_export_importer.backfill_metadata(
            [ok.id, manga.id, failing.id, has_image.id],
            fetcher=fetcher,
        )

        self.assertEqual(failed, [failing.id])
        ok.refresh_from_db()
        manga.refresh_from_db()
        failing.refresh_from_db()
        has_image.refresh_from_db()
        self.assertEqual(ok.image, "https://cdn.myanimelist.net/images/anime/1.jpg")
        self.assertEqual(manga.image, "https://cdn.myanimelist.net/images/manga/2.jpg")
        self.assertEqual(failing.image, settings.IMG_NONE)
        self.assertEqual(has_image.image, "https://example.com/keep.jpg")
        self.assertEqual(ok.title, "Title 1")

    def test_task_works_in_chunks_and_retries_failures(self):
        with (
            patch.object(tasks.mal_export, "backfill_metadata", return_value=[7]) as backfill,
            patch.object(tasks.backfill_mal_metadata, "apply_async") as requeue,
        ):
            ids = list(range(1, tasks.MAL_BACKFILL_CHUNK_SIZE + 6))
            tasks.backfill_mal_metadata(ids)

        backfill.assert_called_once_with(ids[: tasks.MAL_BACKFILL_CHUNK_SIZE])
        requeue.assert_any_call(args=[ids[tasks.MAL_BACKFILL_CHUNK_SIZE :], 1], countdown=1)
        requeue.assert_any_call(args=[[7], 2], countdown=120)

    def test_task_stops_retrying_after_max_attempts(self):
        with (
            patch.object(tasks.mal_export, "backfill_metadata", return_value=[7]),
            patch.object(tasks.backfill_mal_metadata, "apply_async") as requeue,
        ):
            tasks.backfill_mal_metadata([7], attempt=tasks.MAL_BACKFILL_MAX_ATTEMPTS)

        requeue.assert_not_called()
