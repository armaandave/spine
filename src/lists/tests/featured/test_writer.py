from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models import Item, MediaTypes, Sources
from app.providers import services
from lists.featured import ratings
from lists.featured.registry import Guards, live_window
from lists.featured.writer import combine_notes
from lists.featured.types import Entry, FetchResult
from lists.models import CustomList, CustomListItem, FeaturedListSnapshot, FeaturedListSync, PersonListItem
from lists.tests.featured.helpers import FeaturedTestCase, entry, make_definition


CEREMONY = live_window("2027-03-14 16:00", "America/Los_Angeles", timedelta(hours=4))


def movies(*media_ids, start=1):
    return [
        entry(rank, f"Movie {media_id}", media_type="movie", ids={"tmdb": media_id}, image=f"https://image.tmdb.org/t/p/w500/{media_id}.jpg")
        for rank, media_id in enumerate(media_ids, start=start)
    ]


class WriterTests(FeaturedTestCase):
    def test_first_run_creates_a_public_featured_ranked_list(self):
        definition = make_definition(
            description="Desc",
            source_url="https://example.com/list",
            tags=("Tag",),
            position=120,
        )
        result = self.run_list(definition, movies("1", "2", "3"))

        self.assertEqual(result.status, "ok")
        custom_list = result.custom_list
        self.assertEqual(
            (custom_list.name, custom_list.slug, custom_list.description, custom_list.tags),
            ("Test List", "test-list", "Desc", ["Tag"]),
        )
        self.assertEqual((custom_list.import_source, custom_list.import_source_id), ("spine", "test-list"))
        self.assertTrue(custom_list.is_featured and custom_list.is_ranked)
        self.assertEqual(custom_list.visibility, CustomList.Visibility.PUBLIC)
        self.assertEqual(custom_list.featured_position, 120)
        self.assertEqual(custom_list.import_source_url, "https://example.com/list")
        self.assertIsNotNone(custom_list.last_synced_at)
        self.assertListRefs(custom_list, ["tmdb:movie:1", "tmdb:movie:2", "tmdb:movie:3"])
        self.assertEqual(Item.objects.get(media_id="2").image, "https://image.tmdb.org/t/p/w500/2.jpg")

        sync = FeaturedListSync.objects.get(key="test-list")
        self.assertEqual(sync.custom_list, custom_list)
        self.assertEqual((sync.last_status, sync.entry_count, sync.resolved_count), ("ok", 3, 3))
        self.assertEqual(len(sync.fingerprint), 64)
        self.assertIsNotNone(sync.content_changed_at)
        snapshot = FeaturedListSnapshot.objects.get(sync=sync)
        self.assertEqual([row[:2] for row in snapshot.entries], [[1, "tmdb:movie:1"], [2, "tmdb:movie:2"], [3, "tmdb:movie:3"]])
        self.assertEqual(snapshot.entries[0][2], Item.objects.get(media_id="1").pk)

    def test_unchanged_content_keeps_rows_and_content_date(self):
        definition = make_definition()
        first = self.run_list(definition, movies("1", "2"))
        sync = FeaturedListSync.objects.get(key="test-list")
        row_ids = list(CustomListItem.objects.values_list("id", flat=True))
        changed_at = sync.content_changed_at

        second = self.run_list(definition, movies("1", "2"), now=sync.content_changed_at + timedelta(days=1))

        self.assertEqual(second.status, "unchanged")
        self.assertEqual(second.custom_list.pk, first.custom_list.pk)
        self.assertEqual(list(CustomListItem.objects.values_list("id", flat=True)), row_ids)
        sync.refresh_from_db()
        self.assertEqual(sync.content_changed_at, changed_at)
        self.assertEqual(FeaturedListSnapshot.objects.count(), 1)

    def test_changes_replace_items_and_upsert_one_snapshot_per_day(self):
        definition = make_definition()
        day = datetime(2026, 10, 21, 9, tzinfo=UTC)
        self.run_list(definition, movies("1", "2"), now=day)
        self.run_list(definition, movies("2", "1"), now=day + timedelta(hours=3))
        result = self.run_list(definition, movies("3", "1"), now=day + timedelta(days=1))

        self.assertListRefs(result.custom_list, ["tmdb:movie:3", "tmdb:movie:1"])
        snapshots = list(FeaturedListSnapshot.objects.order_by("taken_on"))
        self.assertEqual([snapshot.taken_on.isoformat() for snapshot in snapshots], ["2026-10-21", "2026-10-22"])
        self.assertEqual([row[1] for row in snapshots[0].entries], ["tmdb:movie:2", "tmdb:movie:1"])
        sync = FeaturedListSync.objects.get(key="test-list")
        self.assertEqual(sync.content_changed_at, day + timedelta(days=1))

    def test_duplicate_matches_keep_the_first_rank(self):
        entries = [
            entry(1, "A", isbns=("111",), media_type="book"),
            entry(2, "A again", isbns=("222",), media_type="book"),
            entry(3, "B", ids={"hardcover": "9"}, media_type="book", image="https://c/9.jpg"),
        ]
        hit = {"source": "hardcover", "media_id": "5", "title": "A", "image": "https://c/5.jpg"}
        with patch("app.providers.hardcover.lookup_book_by_isbn", return_value=hit):
            result = self.run_list(make_definition(media_types=("book",), position=350), entries)
        self.assertEqual((result.stored_count, result.resolved_count), (2, 3))
        self.assertEqual(list(result.custom_list.customlistitem_set.values_list("position", flat=True)), [1, 3])

    def test_unranked_lists_use_source_order(self):
        result = self.run_list(make_definition(is_ranked=False), [entry(None, "B", media_type="movie", ids={"tmdb": "2"}, image="https://x/2.jpg"), entry(None, "A", media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg")])
        self.assertFalse(result.custom_list.is_ranked)
        self.assertListRefs(result.custom_list, ["tmdb:movie:2", "tmdb:movie:1"])

    def test_existing_list_with_the_identity_is_reused(self):
        existing = CustomList.objects.create(owner=self.owner, name="Old", slug="old-slug", import_source="spine", import_source_id="test-list")
        result = self.run_list(make_definition(), movies("1"))
        self.assertEqual(result.custom_list.pk, existing.pk)
        self.assertEqual(result.custom_list.slug, "old-slug")
        self.assertEqual(result.custom_list.name, "Test List")

    def test_new_items_wait_for_the_ratings_trickle(self):
        Item.objects.create(source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value, media_id="1", title="Existing", image="https://x/1.jpg")
        with (
            patch("app.tasks.enqueue_external_rating_batches") as enqueue,
            patch("app.tasks.enrich_external_ratings_batch.delay") as batch,
            patch("app.signals.enrich_external_ratings.delay") as single,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.run_list(make_definition(), movies("1", "2", "3"))
        new_ids = sorted(Item.objects.filter(media_id__in=["2", "3"]).values_list("id", flat=True))
        # Nothing reaches the default queue from the write itself (lists.featured.ratings).
        enqueue.assert_not_called()
        batch.assert_not_called()
        single.assert_not_called()
        self.assertEqual(ratings.pending_count(), 2)
        self.assertEqual(sorted(int(value) for value in services.redis_db.smembers(ratings.PENDING_KEY)), new_ids)

    def test_missing_art_is_fetched_once_and_failures_become_unresolved(self):
        entries = [
            entry(1, "", media_type="movie", ids={"tmdb": "10"}),
            entry(2, "Gone", media_type="movie", ids={"tmdb": "404"}),
            entry(3, "Has art", media_type="movie", ids={"tmdb": "11"}, image="https://x/11.jpg"),
        ]

        def metadata(media_type, media_id, source, *args, **kwargs):
            if media_id == "404":
                raise services.ProviderAPIError("tmdb", Exception("missing"))
            return {"title": "Fetched", "image": "https://image.tmdb.org/t/p/w500/10.jpg"}

        with patch("app.providers.services.get_media_metadata", side_effect=metadata) as get_metadata:
            result = self.run_list(make_definition(guards=Guards(min_resolve_rate=0.5)), entries)
        self.assertEqual(get_metadata.call_count, 2)
        self.assertListRefs(result.custom_list, ["tmdb:movie:10", "tmdb:movie:11"])
        self.assertEqual(Item.objects.get(media_id="10").title, "Fetched")
        sync = FeaturedListSync.objects.get(key="test-list")
        self.assertEqual(sync.unresolved[0]["key"], "tmdb:movie:404")
        self.assertIn("metadata lookup failed", sync.unresolved[0]["reason"])

    def test_every_entry_note_is_stored_per_item(self):
        entries = [
            entry(1, "A", media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg", note="Winner · Best Picture"),
            entry(2, "A again", media_type="movie", ids={"ref": "tmdb:movie:1"}, image="https://x/1.jpg", note="Nominee · Best Director"),
            entry(3, "B", media_type="movie", ids={"tmdb": "2"}, image="https://x/2.jpg"),
        ]
        # A live ceremony: one film in two categories is one row.
        definition = make_definition(kind="award", live_windows=(CEREMONY,))
        self.run_list(definition, entries)
        sync = FeaturedListSync.objects.get(key="test-list")
        first = str(Item.objects.get(media_id="1").pk)
        self.assertEqual(sync.entry_notes, {first: "Winner · Best Picture; Nominee · Best Director"})
        self.assertEqual(sync.item_notes, {})
        self.assertEqual(
            list(CustomListItem.objects.values_list("note", flat=True)),
            ["Winner · Best Picture; Nominee · Best Director", ""],
        )

        # Notes refresh even when the content is unchanged.
        changed = [replace(entries[0], note="Winner · Best Picture · 2027"), entries[2]]
        self.assertEqual(self.run_list(definition, changed).status, "unchanged")
        sync.refresh_from_db()
        self.assertEqual(sync.entry_notes, {first: "Winner · Best Picture · 2027"})
        self.assertEqual(list(CustomListItem.objects.values_list("note", flat=True)), ["Winner · Best Picture · 2027", ""])

    def test_award_history_keeps_a_row_per_win(self):
        def show(media_id, note):
            return entry(None, f"Show {media_id}", media_type="tv", ids={"tmdb": media_id}, image=f"https://x/{media_id}.jpg", note=note)

        entries = [
            show("1", "2023"),
            show("2", "2021 · tie"),
            show("3", "2021 · tie"),
            show("1", "2022"),
            show("1", "2022"),  # the same win twice in the source: one row
            show("1", "2020"),
        ]
        definition = make_definition(kind="award", media_types=("tv",), is_ranked=False, cadence=timedelta(days=7))
        result = self.run_list(definition, entries)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.stored_count, 5)
        self.assertListRefs(result.custom_list, ["tmdb:tv:1", "tmdb:tv:2", "tmdb:tv:3", "tmdb:tv:1", "tmdb:tv:1"])
        self.assertEqual(
            list(result.custom_list.customlistitem_set.values_list("position", "occurrence", "note")),
            [(1, 0, "2023"), (2, 0, "2021 · tie"), (3, 0, "2021 · tie"), (4, 1, "2022"), (5, 2, "2020")],
        )
        self.assertEqual(Item.objects.filter(media_id="1").count(), 1)

        # A note that changes without the rows changing is rewritten on its own row.
        changed = [replace(entries[0], note="2023 · tie"), *entries[1:]]
        self.assertEqual(self.run_list(definition, changed).status, "unchanged")
        self.assertEqual(
            list(result.custom_list.customlistitem_set.values_list("occurrence", "note")),
            [(0, "2023 · tie"), (0, "2021 · tie"), (0, "2021 · tie"), (1, "2022"), (2, "2020")],
        )

    def test_only_award_histories_keep_repeats(self):
        repeated = [
            entry(1, "A", media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg", note="Week 1"),
            entry(2, "A", media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg", note="Week 2"),
        ]
        self.assertTrue(make_definition(kind="award").keeps_repeats)
        for definition in (
            make_definition(),  # a chart
            make_definition(kind="award", live_windows=(CEREMONY,)),
        ):
            with self.subTest(kind=definition.kind, live=bool(definition.live_windows)):
                self.assertFalse(definition.keeps_repeats)
                result = self.run_list(definition, repeated)
                self.assertListRefs(result.custom_list, ["tmdb:movie:1"])
        self.assertFalse(make_definition(kind="award", list_type="people", media_types=("book",), position=950).keeps_repeats)

    def test_chart_movement_and_notes_combine(self):
        definition = make_definition()
        self.run_list(definition, movies("1", "2", "3"))
        swapped = [replace(movie, rank=rank) for movie, rank in zip(movies("2", "1", "4"), (1, 2, 3), strict=True)]
        swapped[0] = replace(swapped[0], note="$12.4M")
        self.run_list(definition, swapped)
        sync = FeaturedListSync.objects.get(key="test-list")
        ids = {media_id: str(Item.objects.get(media_id=media_id).pk) for media_id in ("1", "2", "4")}
        self.assertEqual(sync.item_notes, {ids["2"]: "▲1", ids["1"]: "▼1", ids["4"]: "NEW"})
        self.assertEqual(sync.entry_notes, {ids["2"]: "▲1 · $12.4M", ids["1"]: "▼1", ids["4"]: "NEW"})

    def test_combine_notes(self):
        self.assertEqual(combine_notes("▲2", "$12.4M"), "▲2 · $12.4M")
        self.assertEqual(combine_notes("▲2", ""), "▲2")
        self.assertEqual(combine_notes("", "Joint 21"), "Joint 21")
        # A source note that already says how the entry moved stands alone.
        self.assertEqual(combine_notes("▲2", "▲2"), "▲2")
        self.assertEqual(combine_notes("NEW", "New this week"), "New this week")

    def test_people_lists_write_person_rows(self):
        definition = make_definition(list_type="people", media_types=("book",), position=950, kind="award")
        entries = [
            Entry(1, "Annie Ernaux", ids={"openlibrary_person": "OL1393513A"}, image="https://p/1.jpg"),
            Entry(2, "Jon Fosse", ids={"ref": "openlibrary:person:OL1A"}),
        ]
        result = self.run_list(definition, entries)
        self.assertEqual(result.custom_list.list_type, CustomList.ListType.PEOPLE)
        self.assertEqual(
            list(PersonListItem.objects.order_by("position").values_list("source", "person_id", "name", "profile_url", "position")),
            [("openlibrary", "OL1393513A", "Annie Ernaux", "https://p/1.jpg", 1), ("openlibrary", "OL1A", "Jon Fosse", "", 2)],
        )
        self.assertEqual(FeaturedListSnapshot.objects.get().entries, [[1, "openlibrary_person:OL1393513A", None], [2, "ref:openlibrary:person:OL1A", None]])


class GuardTests(FeaturedTestCase):
    def assertGuarded(self, result, reason, status="guarded"):  # noqa: N802
        self.assertEqual(result.status, status)
        self.assertEqual(result.error.reason, reason)
        self.assertIn("the existing list was not changed", result.message)

    def existing_list(self, count):
        self.run_list(make_definition(), movies(*[str(number) for number in range(1, count + 1)]))
        return CustomList.objects.get(import_source_id="test-list")

    def test_empty_fetch(self):
        result = self.run_list(make_definition(), [])
        self.assertGuarded(result, "empty", status="empty")
        self.assertFalse(CustomList.objects.exists())

    def test_exact_count(self):
        definition = make_definition(guards=Guards(require_exact_count=True))
        result = self.run_list(definition, fetch_result=FetchResult(entries=movies("1", "2"), expected_count=3))
        self.assertGuarded(result, "exact_count")
        self.assertEqual(result.error.details, {"expected": 3, "actual": 2})
        self.assertEqual(self.run_list(definition, fetch_result=FetchResult(entries=movies("1", "2"), expected_count=2)).status, "ok")

    def test_zero_resolved_preserves_the_list(self):
        custom_list = self.existing_list(2)
        result = self.run_list(make_definition(guards=Guards(max_title_searches=0)), [entry(1, "Unknown")])
        self.assertGuarded(result, "zero_resolved")
        self.assertListRefs(custom_list, ["tmdb:movie:1", "tmdb:movie:2"])
        sync = FeaturedListSync.objects.get(key="test-list")
        self.assertEqual((sync.last_status, sync.consecutive_failures, sync.entry_count, sync.resolved_count), ("guarded", 1, 1, 0))
        self.assertEqual(sync.unresolved[0]["title"], "Unknown")
        self.assertIsNotNone(sync.last_success_at)

    def test_min_resolve_rate(self):
        definition = make_definition(guards=Guards(min_resolve_rate=0.75, max_title_searches=0))
        result = self.run_list(definition, [*movies("1", "2"), entry(3, "Unknown")])
        self.assertGuarded(result, "resolve_rate")
        self.assertIn("Only 2 of 3 entries resolved (67%, minimum 75%)", result.message)

    def test_skipped_entries_do_not_count_against_the_rate(self):
        definition = make_definition(guards=Guards(min_resolve_rate=1.0, max_title_searches=0))
        with patch("lists.featured.overrides.table", return_value={("test-list", "t:unknown|"): "-"}):
            result = self.run_list(definition, [*movies("1"), entry(2, "Unknown")])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.custom_list.items.count(), 1)

    def test_drop_guard_only_after_the_minimum_previous_count(self):
        custom_list = self.existing_list(4)
        definition = make_definition(guards=Guards(max_drop=0.5, drop_check_min_previous=4))
        result = self.run_list(definition, movies("1"))
        self.assertGuarded(result, "drop")
        self.assertEqual(custom_list.items.count(), 4)
        self.assertEqual(self.run_list(definition, movies("1", "2")).status, "ok")
        small = make_definition(guards=Guards(max_drop=0.5, drop_check_min_previous=20))
        self.assertEqual(self.run_list(small, movies("1")).status, "ok")
