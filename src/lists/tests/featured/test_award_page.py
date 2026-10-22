"""The award_page adapter: winners read from Wikipedia ceremony pages, held for 60 s.

The replay tests use real revision sequences recorded from the MediaWiki API (trimmed;
see each fixture's ``note``): The Game Awards 2025 and the 2025 Booker Prize.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from lists.featured import engine, registry
from lists.featured.adapters import ADAPTERS, award_page
from lists.featured.resolve import Resolver
from lists.featured.text import normalize_title
from lists.featured.types import AdapterError, Entry
from lists.featured.wikitext import NONE, REVIEW, WINNERS, Nominee, evaluate, read_page
from lists.models import FeaturedListSync
from lists.tests.featured.helpers import FIXTURES, FeaturedTestCase, api_routes, load_fixture, make_definition

NOMINEES = FIXTURES / "award_page"
POLL = timedelta(seconds=30)


def when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeWiki:
    """Answer MediaWiki API requests from recorded revisions, as of ``clock``."""

    def __init__(self, title, pageid, revisions):
        self.title, self.pageid = title, pageid
        self.revisions = sorted(revisions, key=lambda revision: revision["revid"])
        self.clock = None
        self.requests = []
        self.redirects = set()

    @classmethod
    def from_fixture(cls, name):
        data = load_fixture("award_page", name)
        revisions = [{**row, "content": data["contents"][row["content"]]} for row in data["revisions"]]
        return cls(data["title"], data["pageid"], revisions)

    @classmethod
    def from_sequence(cls, title, sequence, *, first_revid=1000):
        """``sequence``: ``[(datetime, content)]``."""
        revisions = [
            {"revid": first_revid + index, "parentid": first_revid + index - 1, "timestamp": stamp(moment), "content": content}
            for index, (moment, content) in enumerate(sequence)
        ]
        return cls(title, 42, revisions)

    def visible(self):
        return [revision for revision in self.revisions if when(revision["timestamp"]) <= self.clock]

    def __call__(self, url, params):
        self.requests.append(dict(params))
        if "titles" in params:
            pages = []
            for title in params["titles"].split("|"):
                visible = self.visible()
                if title in self.redirects:
                    pages.append({"pageid": 7, "ns": 0, "title": title, "redirect": True, "revisions": [{"revid": 5, "parentid": 4, "timestamp": "2026-01-01T00:00:00Z"}]})
                elif title != self.title or not visible:
                    pages.append({"ns": 0, "title": title, "missing": True})
                else:
                    latest = visible[-1]
                    pages.append({"pageid": self.pageid, "ns": 0, "title": title, "revisions": [{key: latest[key] for key in ("revid", "parentid", "timestamp")}]})
            return {"batchcomplete": True, "query": {"pages": pages}}
        newest_first = list(reversed(self.visible()))
        if "rvend" in params:
            newest_first = [revision for revision in newest_first if revision["timestamp"] >= params["rvend"]]
        newest_first = newest_first[: int(params["rvlimit"])]
        return {
            "query": {
                "pages": [
                    {
                        "pageid": self.pageid,
                        "ns": 0,
                        "title": self.title,
                        "revisions": [
                            {
                                "revid": revision["revid"],
                                "parentid": revision["parentid"],
                                "timestamp": revision["timestamp"],
                                "slots": {"main": {"contentmodel": "wikitext", "content": revision["content"]}},
                            }
                            for revision in newest_first
                        ],
                    },
                ],
            },
        }


def award_definition(**fields):
    values = {
        "key": "the-game-awards-2025",
        "name": "The Game Awards 2025",
        "media_types": ("game",),
        "kind": "award",
        "position": 590,
        "adapter": "award_page",
        "params": {"pages": ["The Game Awards 2025"], "ceremony": "tga-2025", "edition": "2025"},
        "cadence": timedelta(days=1),
        "is_ranked": False,
    }
    values.update(fields)
    return make_definition(**values)


def winners(result):
    return {(entry.note, entry.ids.get("ref")) for entry in result.entries if entry.note.startswith("Winner")}


class ReplayCase(FeaturedTestCase):
    def poll(self, wiki, definition, moment):
        wiki.clock = moment
        with (
            patch.object(award_page, "DIRECTORY", NOMINEES),
            api_routes({"/w/api.php": wiki}),
            patch.object(award_page, "now", lambda: wiki.clock),
        ):
            return award_page.fetch(definition)

    def replay(self, wiki, definition, start, end, step=POLL):
        """Poll every ``step``; return ``({(note, ref): first poll showing it}, [(time, result, state)])``."""
        first, polls = {}, []
        moment = start
        while moment <= end:
            result = self.poll(wiki, definition, moment)
            for shown in winners(result):
                first.setdefault(shown, moment)
            polls.append((moment, result, cache.get(award_page.state_key(definition))))
            moment += step
        return first, polls


class GameAwardsReplayTests(ReplayCase):
    """The Game Awards 2025, 00:20–04:30 UTC on 2025-12-12, polled every 30 s."""

    GOTY = ("Winner · Game of the Year", "igdb:game:305152")
    FAMILY = ("Winner · Best Family Game", "igdb:game:338106")
    VANDAL = ("Winner · Game of the Year", "igdb:game:338106")

    def setUp(self):
        super().setUp()
        self.wiki = FakeWiki.from_fixture("tga-2025-revisions.json")
        self.definition = award_definition()
        self.first, self.polls = self.replay(
            self.wiki,
            self.definition,
            datetime(2025, 12, 12, 0, 20, tzinfo=UTC),
            datetime(2025, 12, 12, 4, 30, tzinfo=UTC),
        )

    def marked_at(self, revid):
        return when(next(row["timestamp"] for row in self.wiki.revisions if row["revid"] == revid))

    def test_game_of_the_year_publishes_about_sixty_seconds_after_it_appears(self):
        appeared = self.marked_at(1327017160)  # 04:02:11, "Update." bolds Clair Obscur and fills the infobox
        delay = (self.first[self.GOTY] - appeared).total_seconds()
        self.assertGreaterEqual(delay, award_page.HOLD_SECONDS)
        self.assertLess(delay, award_page.HOLD_SECONDS + POLL.total_seconds())
        self.assertEqual(delay, 79)

    def test_family_game_counts_from_the_first_bold_edit_not_the_dagger(self):
        bold = self.marked_at(1326988226)  # 00:34:31 bolds Donkey Kong Bananza; 00:34:53 adds the dagger
        delay = (self.first[self.FAMILY] - bold).total_seconds()
        self.assertGreaterEqual(delay, award_page.HOLD_SECONDS)
        self.assertLess(delay, award_page.HOLD_SECONDS + POLL.total_seconds())

    def test_the_vandal_edit_reverted_in_24_seconds_never_publishes(self):
        # 00:57:25 an IP marks Donkey Kong Bananza as Game of the Year; Rhain reverts at 00:57:49.
        self.assertNotIn(self.VANDAL, self.first)
        seen = [
            moment
            for moment, _, state in self.polls
            if state["categories"]["Game of the Year"]["winners"] == ["igdb:game:338106"]
        ]
        self.assertEqual(seen, [datetime(2025, 12, 12, 0, 57, 30, tzinfo=UTC)])  # observed, held 5 s, dropped
        for _, result, _ in self.polls:
            notes = {entry.ids.get("ref"): entry.note for entry in result.entries}
            self.assertNotEqual(notes["igdb:game:338106"], "Winner · Game of the Year")

    def test_live_label_and_list_shape_through_the_night(self):
        labels = {moment.strftime("%H:%M:%S"): result.live_label for moment, result, _ in self.polls}
        self.assertEqual(labels["00:30:00"], "0 of 2 announced")
        self.assertEqual(labels["01:00:00"], "1 of 2 announced")
        self.assertEqual(labels["04:30:00"], "2 of 2 announced")
        before = self.polls[0][1]
        self.assertEqual(before.entries[0].note, "Nominee · Game of the Year")
        self.assertEqual(before.edition, "2025")
        after = self.polls[-1][1]
        self.assertEqual(
            [(entry.ids["ref"], entry.note) for entry in after.entries[:3]],
            [
                ("igdb:game:305152", "Winner · Game of the Year"),
                ("igdb:game:338106", "Winner · Best Family Game"),
                ("igdb:game:228530", "Nominee · Game of the Year"),
            ],
        )
        self.assertEqual(len(after.entries), 11)  # 12 nominations, Donkey Kong Bananza listed once

    def test_each_poll_is_one_request_unless_the_page_changed(self):
        batched = [params for params in self.wiki.requests if "titles" in params]
        content = [params for params in self.wiki.requests if "pageids" in params]
        self.assertEqual(len(batched), len(self.polls))
        self.assertEqual({params["prop"] for params in batched}, {"revisions|info"})
        self.assertEqual({params["rvprop"] for params in batched}, {"ids|timestamp"})
        self.assertLess(len(content), len(self.polls) / 3)
        self.assertNotIn("redirects", batched[0])


class BookerReplayTests(ReplayCase):
    def test_winner_publishes_a_minute_after_the_row_turns_gold(self):
        wiki = FakeWiki.from_fixture("booker-2025-revisions.json")
        definition = award_definition(
            key="booker-prize-2025",
            media_types=("book",),
            position=390,
            params={"pages": ["2025 Booker Prize"], "ceremony": "booker-2025", "aliases": {"The Booker Prize": ["Nominees"]}},
        )
        first, polls = self.replay(
            wiki,
            definition,
            datetime(2025, 11, 10, 21, 50, tzinfo=UTC),
            datetime(2025, 11, 10, 22, 10, tzinfo=UTC),
        )
        gold = when("2025-11-10T21:56:11Z")  # the edit adding the gold legend and row
        published = first[("Winner · The Booker Prize", "hardcover:book:1676691")]
        self.assertEqual((published - gold).total_seconds(), 79)
        self.assertEqual(polls[-1][1].live_label, "1 of 1 announced")
        self.assertEqual(len(first), 1)


def goty_page(*lines, family=("* ''[[Lego Party]]''",)):
    body = "\n".join(lines)
    family_body = "\n".join(family)
    return (
        "{| class=wikitable\n|-\n! [[The Game Award for Game of the Year|Game of the Year]]\n! Best Family Game\n"
        f"|-\n| valign=\"top\" |\n{body}\n| valign=\"top\" |\n{family_body}\n|}}\n"
    )


CLAIR = "* ''[[Clair Obscur: Expedition 33]]'' – Sandfall Interactive"
CLAIR_WON = "* '''''[[Clair Obscur: Expedition 33]]'' – Sandfall Interactive'''{{double-dagger}}"
HADES = "** ''[[Hades II]]'' – Supergiant Games"
HADES_WON = "* '''''[[Hades II]]'' – Supergiant Games'''{{double-dagger}}"
T0 = datetime(2026, 12, 11, 3, 0, tzinfo=UTC)


class HoldRuleTests(ReplayCase):
    def run_sequence(self, sequence, polls, **definition_fields):
        wiki = FakeWiki.from_sequence("The Game Awards 2025", sequence)
        definition = award_definition(**definition_fields)
        return wiki, [self.poll(wiki, definition, moment) for moment in polls]

    def test_a_published_winner_survives_a_missing_marker_and_yields_to_a_held_one(self):
        sequence = [
            (T0, goty_page(CLAIR, HADES)),
            (T0 + timedelta(seconds=10), goty_page(CLAIR_WON, HADES)),
            (T0 + timedelta(seconds=200), goty_page(CLAIR, HADES)),  # marker removed
            (T0 + timedelta(seconds=400), goty_page(CLAIR, HADES_WON)),  # a correction
        ]
        polls = [T0 + timedelta(seconds=second) for second in (30, 75, 230, 330, 430, 470)]
        _, results = self.run_sequence(sequence, polls)
        goty = [
            next((entry.ids["ref"] for entry in result.entries if entry.note == "Winner · Game of the Year"), None)
            for result in results
        ]
        self.assertEqual(
            goty,
            [None, "igdb:game:305152", "igdb:game:305152", "igdb:game:305152", "igdb:game:305152", "igdb:game:228525"],
        )

    def test_a_marked_entry_that_is_not_a_nominee_goes_to_review(self):
        stranger = "* '''''[[Elden Ring]]'''''"
        _, results = self.run_sequence(
            [(T0, goty_page(stranger, CLAIR))],
            [T0 + timedelta(minutes=5)],
        )
        result = results[0]
        self.assertFalse(winners(result))
        review = [entry for entry in result.entries if "review" in entry.ids]
        self.assertEqual(len(review), 1)
        self.assertIn("not a pre-matched nominee: Elden Ring", review[0].ids["review"])
        self.assertEqual(review[0].note, "Review · Game of the Year")
        resolution = Resolver(award_definition()).resolve_one(review[0])
        self.assertIsNone(resolution.match)
        self.assertEqual(resolution.how, "review")

    def test_two_winners_need_a_tie_marker(self):
        both = [CLAIR_WON, HADES_WON]
        _, results = self.run_sequence([(T0, goty_page(*both))], [T0 + timedelta(minutes=5)])
        self.assertFalse(winners(results[0]))
        tied = [CLAIR_WON + " (tie)", HADES_WON + " (tie)"]
        _, results = self.run_sequence([(T0, goty_page(*tied))], [T0 + timedelta(minutes=5)], key="tie-list")
        self.assertEqual(
            winners(results[0]),
            {("Winner · Game of the Year", "igdb:game:305152"), ("Winner · Game of the Year", "igdb:game:228525")},
        )

    def test_a_gap_longer_than_one_read_restarts_the_hold(self):
        # Clair Obscur is marked at T0; then 12 revisions land between two polls, more
        # than one read covers, so the adapter can't tell whether the marker held.
        sequence = [(T0, goty_page(CLAIR, HADES)), (T0 + timedelta(seconds=5), goty_page(CLAIR_WON, HADES))]
        sequence += [(T0 + timedelta(seconds=40 + index), goty_page(CLAIR_WON, HADES) + f"\n{index}") for index in range(12)]
        polls = [T0 + timedelta(seconds=20), T0 + timedelta(seconds=70), T0 + timedelta(seconds=100), T0 + timedelta(seconds=120)]
        _, results = self.run_sequence(sequence, polls)
        shown = [bool(winners(result)) for result in results]
        # Without the gap it would publish at +70 s (marked at +5 s); after it, the
        # hold counts from the oldest revision read (+42 s), so it publishes at +120 s.
        self.assertEqual(shown, [False, False, False, True])

    def test_lost_state_republishes_a_winner_that_has_held(self):
        sequence = [(T0, goty_page(CLAIR, HADES)), (T0 + timedelta(seconds=5), goty_page(CLAIR_WON, HADES))]
        wiki = FakeWiki.from_sequence("The Game Awards 2025", sequence)
        definition = award_definition()
        self.assertTrue(winners(self.poll(wiki, definition, T0 + timedelta(seconds=90))))
        cache.clear()
        self.assertTrue(winners(self.poll(wiki, definition, T0 + timedelta(seconds=120))))

    def test_redirects_and_missing_pages_are_never_read(self):
        wiki = FakeWiki.from_sequence("The Game Awards 2025", [(T0, goty_page(CLAIR_WON))])
        wiki.redirects.add("The Game Awards 2026")
        definition = award_definition(params={"pages": ["The Game Awards 2026", "Not A Page"], "ceremony": "tga-2025"})
        result = self.poll(wiki, definition, T0 + timedelta(hours=1))
        self.assertFalse(winners(result))
        self.assertEqual(result.live_label, "0 of 2 announced")
        self.assertEqual([params for params in wiki.requests if "pageids" in params], [])
        self.assertEqual(len(wiki.requests), 1)

    def test_api_errors_are_adapter_errors(self):
        definition = award_definition()
        with (
            patch.object(award_page, "DIRECTORY", NOMINEES),
            api_routes({"/w/api.php": {"error": {"code": "maxlag", "info": "Waiting for a database server"}}}),
            self.assertRaisesMessage(AdapterError, "Wikipedia API error: maxlag"),
        ):
            award_page.fetch(definition)

    def test_an_empty_nominee_file_makes_no_requests(self):
        definition = award_definition(params={"pages": ["The Game Awards 2026"], "ceremony": "empty"}, featured=False)
        with patch.object(award_page, "DIRECTORY", NOMINEES / "empty"), api_routes({}):
            result = award_page.fetch(definition)
        self.assertEqual(result.entries, [])
        self.assertEqual(result.live_label, "")


class EngineReplayTests(ReplayCase):
    def test_the_engine_writes_winners_first(self):
        wiki = FakeWiki.from_fixture("tga-2025-revisions.json")
        definition = award_definition()
        metadata = {"title": "Game", "image": "https://images.igdb.com/x.jpg"}

        def run_at(moment):
            wiki.clock = moment
            with (
                patch.object(award_page, "DIRECTORY", NOMINEES),
                api_routes({"/w/api.php": wiki}),
                patch.object(award_page, "now", lambda: wiki.clock),
                patch("app.providers.services.get_media_metadata", return_value=metadata),
                registry.override_definitions([definition]),
            ):
                return engine.run(definition, self.owner, trigger="test", now=moment)

        before = run_at(datetime(2025, 12, 12, 0, 30, tzinfo=UTC))
        self.assertEqual(before.status, "ok")
        refs = [f"igdb:game:{number}" for number in (305152, 228530, 338106, 228525, 115289, 298526)]
        refs += [f"igdb:game:{number}" for number in (347661, 347646, 338067, 325608, 325594)]
        self.assertListRefs(before.custom_list, refs)
        run_at(datetime(2025, 12, 12, 4, 2, 30, tzinfo=UTC))
        run_at(datetime(2025, 12, 12, 4, 3, tzinfo=UTC))
        after = run_at(datetime(2025, 12, 12, 4, 3, 30, tzinfo=UTC))
        self.assertListRefs(after.custom_list, ["igdb:game:305152", "igdb:game:338106", *[ref for ref in refs if ref not in ("igdb:game:305152", "igdb:game:338106")]])
        sync = FeaturedListSync.objects.get(key=definition.key)
        self.assertEqual((sync.live_label, sync.edition, sync.unresolved), ("2 of 2 announced", "2025", []))
        self.assertFalse(after.custom_list.is_ranked)


class NomineeFileTests(SimpleTestCase):
    def test_validate(self):
        with patch.object(award_page, "DIRECTORY", NOMINEES):
            award_page.validate(award_definition())
            cases = {
                "unknown params": {"pages": ["X"], "ceremony": "tga-2025", "page": "X"},
                "pages must be": {"pages": "The Game Awards 2025", "ceremony": "tga-2025"},
                "does not exist": {"pages": ["X"], "ceremony": "nope"},
                "wiki must be": {"pages": ["X"], "ceremony": "tga-2025", "wiki": "evil.example.com"},
                "is not a category": {"pages": ["X"], "ceremony": "tga-2025", "aliases": {"Best Picture": ["X"]}},
                "section must be": {"pages": [{"title": "X", "section": ""}], "ceremony": "tga-2025"},
            }
            for message, params in cases.items():
                with self.subTest(message), self.assertRaisesMessage(ValueError, message):
                    award_page.validate(award_definition(params=params))
            with self.assertRaisesMessage(ValueError, "is not one of this list's media types"):
                award_page.validate(award_definition(media_types=("movie",), position=190))

    def test_featured_lists_need_nominees(self):
        with patch.object(award_page, "DIRECTORY", NOMINEES / "empty"):
            params = {"pages": ["X"], "ceremony": "empty"}
            award_page.validate(award_definition(params=params, featured=False))
            with self.assertRaisesMessage(ValueError, "keep featured=False"):
                award_page.validate(award_definition(params=params))

    def test_version_follows_the_file(self):
        with patch.object(award_page, "DIRECTORY", NOMINEES):
            self.assertNotEqual(award_page.version(award_definition()), "missing")
            other = award_definition(params={"pages": ["X"], "ceremony": "booker-2025"})
            self.assertNotEqual(award_page.version(award_definition()), award_page.version(other))

    def test_registered(self):
        self.assertIs(ADAPTERS["award_page"], award_page.ADAPTER)


def labels(*names, **aliases):
    found = {normalize_title(name): name for name in names}
    for category, values in aliases.items():
        for value in values:
            found[normalize_title(value)] = category.replace("_", " ")
    return found


def nominee(category, title):
    return Nominee(category=category, rank=None, title=title, ref=f"test:{category}:{title}")


class ReaderTests(SimpleTestCase):
    def test_award_category_cells_and_daggers(self):
        page = (
            '{| class="wikitable"\n| style="width:50%;"| {{Award category|#F9EFAA|[[Academy Award for Best Picture|Best Picture]]}}\n'
            "* '''''[[One Battle After Another]]'' – producers ‡'''\n** ''[[Sinners (2025 film)|Sinners]]''\n"
            "| {{Award category|#F9EFAA|[[Academy Award for Best Director|Best Directing]]}}\n"
            "* [[Ryan Coogler]] – ''[[Sinners (2025 film)|Sinners]]''\n|}"
        )
        reading = read_page(page, labels("Best Picture", "Best Director", Best_Director=["Best Directing"]))
        self.assertEqual(reading.found, {"Best Picture", "Best Director"})
        self.assertEqual([(mark.category, mark.kind) for mark in reading.marks], [("Best Picture", "bold")])
        rows = [nominee("Best Picture", "One Battle After Another"), nominee("Best Picture", "Sinners")]
        outcome = evaluate(reading, [*rows, nominee("Best Director", "Sinners")])
        self.assertEqual(outcome["Best Picture"].winners, (rows[0].ref,))
        self.assertEqual(outcome["Best Director"].state, NONE)

    def test_row_labels_and_bold_winner_cells(self):
        page = (
            '{| class="wikitable"\n! Category\n! Winner\n! Shortlist\n|-\n'
            "| align=\"center\" | [[National Book Award for Fiction|Fiction]]\n"
            "| '''[[Rabih Alameddine]], ''The True True Story of Raja the Gullible (and His Mother)'''''\n"
            "|\n* [[Karen Russell]], ''The Antidote''\n|}"
        )
        reading = read_page(page, labels("Fiction"))
        rows = [nominee("Fiction", "The True True Story of Raja the Gullible (and His Mother)"), nominee("Fiction", "The Antidote")]
        self.assertEqual(evaluate(reading, rows)["Fiction"].winners, (rows[0].ref,))

    def test_winner_columns_inside_one_section(self):
        page = (
            "=== 2024 ===\n{| class=\"wikitable\"\n!'''Award'''\n!'''Winner'''\n|-\n|'''Best Storytelling'''\n|''[[Old Game]]''\n|}\n"
            "=== 2025 ===\n{| class=\"wikitable\"\n!'''Award'''\n!'''Winner''' <ref name=x/>\n|-\n|'''Best Storytelling'''\n"
            "|''[[Clair Obscur: Expedition 33]]'' ([[Sandfall Interactive]])\n|}\n"
        )
        rows = [nominee("Best Storytelling", "Clair Obscur: Expedition 33"), nominee("Best Storytelling", "Old Game")]
        outcome = evaluate(read_page(page, labels("Best Storytelling"), section="2025"), rows)
        self.assertEqual(outcome["Best Storytelling"].winners, (rows[0].ref,))
        self.assertEqual(read_page(page, labels("Best Storytelling"), section="2026").found, set())

    def test_legend_shading_marks_a_row(self):
        page = (
            "==Nominees==\n{{legend|gold|indicates the winner}}\n{{legend|lightgrey|indicates shortlisted}}\n"
            '{| class="wikitable"\n! Author\n! Title\n|-\n|-style="background: lightgrey"\n| [[Susan Choi]]\n| \'\'[[Flashlight (novel)|Flashlight]]\'\'\n'
            '|-\n|-style="background: gold"\n| [[David Szalay]]\n| \'\'[[Flesh (Szalay novel)|Flesh]]\'\'\n|}'
        )
        reading = read_page(page, labels("The Booker Prize", The_Booker_Prize=["Nominees"]))
        self.assertEqual([mark.kind for mark in reading.marks], ["shaded"])
        rows = [nominee("The Booker Prize", "Flashlight"), nominee("The Booker Prize", "Flesh")]
        self.assertEqual(evaluate(reading, rows)["The Booker Prize"].winners, (rows[1].ref,))

    def test_bold_credits_under_a_winner_are_not_extra_winners(self):
        page = (
            '{|class="wikitable"\n| colspan="2" | {{Award category|#FFFFFF|[[Grammy Award for Album of the Year|Album of the Year]]}}\n'
            "* '''''[[Debí Tirar Más Fotos]]'' – [[Bad Bunny]]'''\n** '''Tainy, producer'''\n"
            "* ''[[Mayhem (Lady Gaga album)|Mayhem]]'' '''–''' [[Lady Gaga]]\n** Andrew Watt, producer\n|}"
        )
        reading = read_page(page, labels("Album of the Year"))
        rows = [nominee("Album of the Year", "Debí Tirar Más Fotos"), nominee("Album of the Year", "Mayhem")]
        outcome = evaluate(reading, rows)["Album of the Year"]
        self.assertEqual((outcome.state, outcome.winners), (WINNERS, (rows[0].ref,)))

    def test_infobox_pairs_and_mapped_fields(self):
        page = (
            "{{Infobox award\n| award1_type = [[The Game Award for Game of the Year|Game of the Year]]\n"
            "| award1_winner = ''Clair Obscur: Expedition&nbsp;33''\n| best_picture = \n}}\nText."
        )
        rows = [nominee("Game of the Year", "Clair Obscur: Expedition 33"), nominee("Best Picture", "Hamnet")]
        outcome = evaluate(read_page(page, labels("Game of the Year"), infobox={"best_picture": "Best Picture"}), rows)
        self.assertEqual(outcome["Game of the Year"].winners, (rows[0].ref,))
        self.assertEqual(outcome["Best Picture"].state, NONE)

    def test_conflicting_marks_are_reviewed(self):
        page = (
            "{{Infobox award\n| award1_type = Game of the Year\n| award1_winner = ''[[Hades II]]''\n}}\n"
            + goty_page(CLAIR_WON, HADES)
        )
        rows = [nominee("Game of the Year", "Clair Obscur: Expedition 33"), nominee("Game of the Year", "Hades II")]
        self.assertEqual(evaluate(read_page(page, labels("Game of the Year")), rows)["Game of the Year"].state, REVIEW)

    def test_plain_lines_and_unknown_categories_are_ignored(self):
        page = (
            "Winners are listed first, highlighted in '''boldface''', and indicated with a double dagger (‡).\n"
            "== Presenters ==\n* '''[[Someone]]''' presented [[Academy Award for Best Picture|Best Picture]]\n"
            + goty_page(CLAIR)
        )
        reading = read_page(page, labels("Game of the Year"))
        self.assertEqual(reading.marks, [])
        self.assertEqual(reading.found, {"Game of the Year"})

    def test_the_page_before_the_announcement_shows_no_winner(self):
        rows = [nominee("The Booker Prize", "Flesh"), nominee("The Booker Prize", "Flashlight")]
        page = load_fixture("award_page", "booker-2025-revisions.json")["contents"][0]
        outcome = evaluate(read_page(page, labels("The Booker Prize", The_Booker_Prize=["Nominees"])), rows)
        self.assertEqual(outcome["The Booker Prize"].state, NONE)


class ReviewIdTests(SimpleTestCase):
    def test_review_entries_stay_unresolved_unless_overridden(self):
        definition = award_definition()
        row = Entry(rank=None, title="Elden Ring", ids={"review": "not a nominee"})
        self.assertEqual(row.key, "t:elden ring|")
        resolution = Resolver(definition, overrides={}).resolve_one(row)
        self.assertEqual((resolution.match, resolution.reason), (None, "not a nominee"))
        overridden = Resolver(definition, overrides={row.key: "igdb:game:119133"}).resolve_one(row)
        self.assertEqual(overridden.match.ref, "igdb:game:119133")
