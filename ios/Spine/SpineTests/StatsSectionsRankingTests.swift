import Foundation
import XCTest
@testable import Spine

/// The ranking and poster sections of the Stats page: their rows, labels, captions, sorting and
/// grid chunking (`docs/stats-contract.md`, STAT-200 to STAT-217), and Highest rated's show-all pages.
@MainActor
final class StatsSectionsRankingTests: XCTestCase {
    // MARK: - Poster grid

    func testGridCutsItemsIntoRowsOfFourWithPrefixedIDs() {
        let items = (1 ... 9).map { Tile(id: $0) }
        let rows = StatsPosterGridLayout.rows(items, idPrefix: "highest")

        XCTAssertEqual(rows.map(\.id), ["highest.0", "highest.1", "highest.2"])
        XCTAssertEqual(rows.map(\.index), [0, 1, 2])
        XCTAssertEqual(rows.map { $0.items.map(\.id) }, [[1, 2, 3, 4], [5, 6, 7, 8], [9]])
    }

    func testGridOfExactRowsHasNoShortRowAndEmptyHasNone() {
        XCTAssertEqual(StatsPosterGridLayout.rows((1 ... 12).map { Tile(id: $0) }, idPrefix: "x").map(\.items.count), [4, 4, 4])
        XCTAssertTrue(StatsPosterGridLayout.rows([Tile](), idPrefix: "x").isEmpty)
        XCTAssertTrue(StatsPosterGridLayout.rows((1 ... 3).map { Tile(id: $0) }, columns: 0, idPrefix: "x").isEmpty)
    }

    func testGridCellsFitFourAcrossTheNarrowestPhone() {
        // 375 pt wide, less the page's 16 pt sides, with at least 4 pt between cells.
        let needed = StatsPosterGridLayout.cellSize.width * 4 + 3 * 4
        XCTAssertLessThanOrEqual(needed, 375 - 32)
    }

    // MARK: - Shared words

    func testStarsDrawWholeAndHalfStarsOnly() {
        XCTAssertEqual(StatsStars.symbols(for: 3.5), ["star.fill", "star.fill", "star.fill", "star.leadinghalf.filled"])
        XCTAssertEqual(StatsStars.symbols(for: 5), Array(repeating: "star.fill", count: 5))
        XCTAssertEqual(StatsStars.symbols(for: 0.5), ["star.leadinghalf.filled"])
        XCTAssertEqual(StatsStars.symbols(for: 3.65).count, 4, "Off-scale values keep the half star below")
        XCTAssertEqual(StatsStars.symbols(for: 3.2), ["star.fill", "star.fill", "star.fill"])
        XCTAssertEqual(StatsStars.symbols(for: 7), Array(repeating: "star.fill", count: 5))
    }

    func testYearComesFromTheReleaseDate() throws {
        XCTAssertEqual(StatsTitleFormat.year(of: try Self.media(id: 1, title: "Dune", releaseDate: "2024-03-01")), "2024")
        XCTAssertNil(StatsTitleFormat.year(of: try Self.media(id: 2, title: "Dune")))
        XCTAssertNil(StatsTitleFormat.year(of: try Self.media(id: 3, title: "Dune", releaseDate: "TBA")))
    }

    func testOrdinals() {
        XCTAssertEqual(
            [1, 2, 3, 4, 11, 12, 13, 21, 22, 100, 113, 250].map { StatsTitleFormat.ordinal($0) },
            ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "100th", "113th", "250th"]
        )
    }

    func testDaysAreCalendarDaysInThePhonesStyle() {
        XCTAssertEqual(StatsTitleFormat.day("2024-03-12", locale: Locale(identifier: "en_GB")), "12 Mar 2024")
        XCTAssertEqual(StatsTitleFormat.day("2024-03-12", locale: Locale(identifier: "en_US")), "Mar 12, 2024")
        XCTAssertEqual(StatsTitleFormat.day("2025-01-01", locale: Locale(identifier: "en_GB")), "1 Jan 2025")
        XCTAssertNil(StatsTitleFormat.day("March 2024"))
    }

    // MARK: - Header

    func testHeaderIsTheCountsCardThenOneRowPerDetail() throws {
        let header = try Self.decode(StatsHeader.self, Self.headerJSON)
        let rows = StatsHeaderSection.rows(for: header)

        XCTAssertEqual(rows.map(\.id), [
            "header.counts",
            "header.detail.current_daily_streak",
            "header.detail.rewatches",
            "header.detail.episodes",
        ])
        guard case let .detail(_, firstIsFirst, firstIsLast) = rows[1],
              case let .detail(_, lastIsFirst, lastIsLast) = rows[3] else {
            return XCTFail("Expected detail rows")
        }
        XCTAssertEqual([firstIsFirst, firstIsLast, lastIsFirst, lastIsLast], [true, false, false, true])
    }

    func testZeroCurrentStreaksAreLeftOut() {
        let detail = { (key: String, value: Int, unit: String?) in
            StatsHeaderDetail(key: key, label: key, value: value, unit: unit, approximate: false)
        }
        let header = StatsHeader(titles: 3, logs: 4, activeDays: 2, longestDailyStreak: 1, details: [
            detail("current_daily_streak", 0, "day"),
            detail("longest_weekly_streak", 0, "week"),
            detail("current_weekly_streak", 2, "week"),
            detail("rewatches", 0, nil),
        ])
        XCTAssertEqual(
            StatsHeaderSection.shownDetails(header).map(\.key),
            ["longest_weekly_streak", "current_weekly_streak", "rewatches"]
        )
        guard case let .detail(_, isFirst, _) = StatsHeaderSection.rows(for: header)[1] else {
            return XCTFail("Expected a detail row")
        }
        XCTAssertTrue(isFirst, "The first shown row rounds the card's top")
    }

    func testHeaderWithoutDetailsIsOnlyTheCounts() {
        let header = StatsHeader(titles: 3, logs: 4, activeDays: 2, longestDailyStreak: 1, details: [])
        XCTAssertEqual(StatsHeaderSection.rows(for: header).map(\.id), ["header.counts"])
    }

    func testHeaderCountsNameTheTab() throws {
        let header = try Self.decode(StatsHeader.self, Self.headerJSON)
        let counts = StatsHeaderSection.counts(for: header, mediaType: nil)

        XCTAssertEqual(counts.map(\.label), ["Titles", "Logs", "Active days", "Longest streak"])
        XCTAssertEqual(counts.map(\.value), [212, 268, 141, 9])
        XCTAssertEqual(counts.map(\.unit), [nil, nil, nil, "days"])
        XCTAssertEqual(StatsHeaderSection.counts(for: header, mediaType: "tv").first?.label, "Shows")
        XCTAssertEqual(StatsHeaderSection.titlesLabel(for: "music"), "Albums")
        XCTAssertEqual(StatsHeaderSection.titlesLabel(for: "book"), "Books")

        let oneDay = StatsHeader(titles: 1, logs: 1, activeDays: 1, longestDailyStreak: 1, details: [])
        XCTAssertEqual(StatsHeaderSection.counts(for: oneDay, mediaType: nil).last?.unit, "day")
    }

    func testATypeTabsThirdNumberIsItsOwnMeasure() {
        let header = StatsHeader(titles: 40, logs: 52, activeDays: 31, longestDailyStreak: 4, details: [])
        let time = StatsTime(
            rows: [Self.timeRow("movie", "minutes", 18240), Self.timeRow("tv", "minutes", 20400, approximate: true), Self.timeRow("book", "pages", 4310)],
            hoursTotal: nil
        )

        let movies = StatsHeaderSection.counts(for: header, mediaType: "movie", time: time)
        XCTAssertEqual(movies.map(\.label), ["Movies", "Logs", "Hours", "Longest streak"])
        XCTAssertEqual(movies[2].value, 304)
        XCTAssertEqual(StatsHeaderSection.countValue(movies[2]), 304.formatted())

        let tv = StatsHeaderSection.counts(for: header, mediaType: "tv", time: time)
        XCTAssertEqual(StatsHeaderSection.countValue(tv[2]), "≈ 340", "An estimate reads ≈")

        XCTAssertEqual(StatsHeaderSection.primaryMeasure(time: time, mediaType: "book")?.label, "Pages")
        XCTAssertEqual(StatsHeaderSection.primaryMeasure(time: time, mediaType: "book")?.value, 4310)
        XCTAssertEqual(StatsHeaderSection.primaryMeasure(time: StatsTime(rows: [Self.timeRow("manga", "chapters", 1)], hoursTotal: nil), mediaType: "manga")?.label, "Chapter")
        XCTAssertEqual(StatsHeaderSection.primaryMeasure(time: StatsTime(rows: [Self.timeRow("comic", "issues", 64)], hoursTotal: nil), mediaType: "comic")?.label, "Issues")
        XCTAssertEqual(StatsHeaderSection.primaryMeasure(time: StatsTime(rows: [Self.timeRow("music", "minutes", 45)], hoursTotal: nil), mediaType: "music")?.label, "Minutes")

        XCTAssertNil(StatsHeaderSection.primaryMeasure(time: time, mediaType: nil), "Global keeps Active days")
        XCTAssertNil(StatsHeaderSection.primaryMeasure(time: time, mediaType: "game"), "No row for the tab")
        XCTAssertNil(StatsHeaderSection.primaryMeasure(time: StatsTime(rows: [Self.timeRow("game", "minutes", 0)], hoursTotal: nil), mediaType: "game"))
        XCTAssertEqual(StatsHeaderSection.counts(for: header, mediaType: nil, time: time)[2].label, "Active days")
        XCTAssertEqual(StatsHeaderSection.counts(for: header, mediaType: "game", time: time)[2].label, "Active days")
    }

    func testActiveDaysMovesIntoTheDetailsWhenTheMeasureTakesItsPlace() {
        let header = StatsHeader(titles: 40, logs: 52, activeDays: 31, longestDailyStreak: 4, details: [
            StatsHeaderDetail(key: "rewatches", label: "Rewatches", value: 3, unit: nil, approximate: false),
        ])
        let time = StatsTime(rows: [Self.timeRow("movie", "minutes", 600)], hoursTotal: nil)

        XCTAssertEqual(
            StatsHeaderSection.rows(for: header, mediaType: "movie", time: time).map(\.id),
            ["header.counts", "header.detail.active_days", "header.detail.rewatches"]
        )
        let activeDays = StatsHeaderSection.shownDetails(header, mediaType: "movie", time: time)[0]
        XCTAssertEqual(activeDays.label, "Active days")
        XCTAssertEqual(StatsHeaderSection.detailValue(activeDays), "31")
        XCTAssertEqual(StatsHeaderSection.drillDown(for: activeDays), .stat(label: "Active days"), "It opens the scope's logs")

        XCTAssertEqual(
            StatsHeaderSection.rows(for: header, mediaType: nil, time: time).map(\.id),
            ["header.counts", "header.detail.rewatches"],
            "Global keeps Active days among the numbers"
        )
    }

    func testHeaderNumbersOpenTheScopesLogsAndTheStreakItsDays() {
        var header = StatsHeader(titles: 40, logs: 52, activeDays: 31, longestDailyStreak: 4, details: [])
        XCTAssertEqual(
            StatsHeaderSection.counts(for: header, mediaType: "movie").map(\.drillDown),
            [.stat(label: "Movies"), .stat(label: "Logs"), .stat(label: "Active days"), nil],
            "Without its days the streak opens nothing"
        )

        header.longestDailyStreakRange = StatsDayRange(start: "2025-03-01", end: "2025-03-04")
        XCTAssertEqual(
            StatsHeaderSection.counts(for: header, mediaType: nil).last?.drillDown,
            .stat(label: "Longest streak", drill: ["watched_from": "2025-03-01", "watched_to": "2025-03-04"])
        )

        let empty = StatsHeader(titles: 0, logs: 0, activeDays: 0, longestDailyStreak: 0, details: [])
        XCTAssertTrue(StatsHeaderSection.counts(for: empty, mediaType: nil).allSatisfy { $0.drillDown == nil }, "Zero opens nothing")
    }

    func testDetailRowsOpenTheirDrill() {
        func detail(_ key: String, value: Int = 3, drill: [String: String]?) -> StatsHeaderDetail {
            StatsHeaderDetail(key: key, label: key.capitalized, value: value, unit: nil, approximate: false, drill: drill)
        }

        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("rewatches", drill: ["repeat": "true"])), .stat(label: "Rewatches", drill: ["repeat": "true"]))
        XCTAssertEqual(
            StatsHeaderSection.drillDown(for: detail("current_daily_streak", drill: ["watched_from": "2026-10-20", "watched_to": "2026-10-29"])),
            .stat(label: "Current_Daily_Streak", drill: ["watched_from": "2026-10-20", "watched_to": "2026-10-29"])
        )
        XCTAssertNil(StatsHeaderSection.drillDown(for: detail("episodes", drill: nil)), "No drill, no diary")
        XCTAssertNil(StatsHeaderSection.drillDown(for: detail("reviews", value: 0, drill: ["has_review": "true"])), "Nothing to open")
        XCTAssertNil(StatsHeaderSection.drillDown(for: detail("reads", drill: ["mystery_param": "1"])), "A param the app can't send opens nothing")
        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("likes", drill: nil)), .liked, "Older servers: Likes still opens the liked logs")
    }

    func testHeaderDetailValuesPluralizeAndMarkEstimates() {
        XCTAssertEqual(StatsHeaderSection.detailValue(Self.detail(value: 3, unit: "day")), "3 days")
        XCTAssertEqual(StatsHeaderSection.detailValue(Self.detail(value: 1, unit: "week")), "1 week")
        XCTAssertEqual(StatsHeaderSection.detailValue(Self.detail(value: 1200, unit: nil)), 1200.formatted())
        XCTAssertEqual(StatsHeaderSection.detailValue(Self.detail(value: 0, unit: "day")), "0 days")
        XCTAssertEqual(StatsHeaderSection.detailValue(Self.detail(value: 48, unit: nil, approximate: true)), "≈ 48")
        XCTAssertEqual(StatsHeaderSection.detailValue(Self.detail(value: 2, unit: " ")), "2")
    }

    // MARK: - Time

    func testTimeValuesUseEachTypesUnit() {
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("movie", "minutes", 18240)), "304 hours")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("tv", "minutes", 20400, approximate: true)), "≈ 340 hours")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("music", "minutes", 45)), "45 minutes")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("music", "minutes", 1)), "1 minute")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("game", "minutes", 90)), "2 hours")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("book", "pages", 4310)), "\(4310.formatted()) pages")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("manga", "chapters", 1, approximate: true)), "≈ 1 chapter")
        XCTAssertEqual(StatsTimeSection.value(for: Self.timeRow("comic", "issues", 64)), "64 issues")
    }

    func testTotalHoursNoteListsWhatItLeavesOut() {
        XCTAssertEqual(StatsTimeSection.leavesOutNote(["manga", "book", "comic"]), "Doesn't include manga, books or comics")
        XCTAssertEqual(StatsTimeSection.leavesOutNote(["book", "comic"]), "Doesn't include books or comics")
        XCTAssertEqual(StatsTimeSection.leavesOutNote(["book"]), "Doesn't include books")
        XCTAssertNil(StatsTimeSection.leavesOutNote([]))
        XCTAssertEqual(
            StatsTimeSection.totalValue(StatsHoursTotal(minutes: 38640, mediaTypes: [], leavesOut: [], approximate: true)),
            "≈ \(644.formatted())"
        )
        XCTAssertEqual(
            StatsTimeSection.totalValue(StatsHoursTotal(minutes: 90, mediaTypes: [], leavesOut: [], approximate: false)),
            "2"
        )
    }

    func testTimeOnGlobalEndsWithTotalHoursAndItsNote() {
        let time = StatsTime(
            rows: [Self.timeRow("movie", "minutes", 600), Self.timeRow("tv", "minutes", 300), Self.timeRow("book", "pages", 90)],
            hoursTotal: StatsHoursTotal(minutes: 900, mediaTypes: ["movie", "tv"], leavesOut: ["book"], approximate: false)
        )
        let rows = StatsTimeSection.rows(for: time, mediaType: nil)

        XCTAssertEqual(rows.map(\.id), ["time.title", "time.type.movie", "time.type.tv", "time.type.book", "time.total", "time.note"])
        XCTAssertEqual(rows.last, .note("Doesn't include books"))
        guard case let .type(_, _, bookIsLast) = rows[3] else { return XCTFail("Expected the book row") }
        XCTAssertFalse(bookIsLast, "Total hours closes the card")
    }

    func testTimeSkipsATotalThatWouldRepeatOneRow() {
        let total = StatsHoursTotal(minutes: 600, mediaTypes: ["movie"], leavesOut: ["book"], approximate: false)
        let global = StatsTime(rows: [Self.timeRow("movie", "minutes", 600), Self.timeRow("book", "pages", 90)], hoursTotal: total)
        XCTAssertEqual(StatsTimeSection.rows(for: global, mediaType: nil).map(\.id), ["time.title", "time.type.movie", "time.type.book"])

        let typeTab = StatsTime(rows: [Self.timeRow("movie", "minutes", 600)], hoursTotal: nil)
        let rows = StatsTimeSection.rows(for: typeTab, mediaType: "movie")
        XCTAssertEqual(rows.map(\.id), ["time.title", "time.type.movie"])
        XCTAssertEqual(rows.last, .type(Self.timeRow("movie", "minutes", 600), isFirst: true, isLast: true))

        XCTAssertTrue(StatsTimeSection.rows(for: StatsTime(rows: [], hoursTotal: total), mediaType: nil).isEmpty)
    }

    // MARK: - Top

    func testTopFromAListShowsHeroRankedRowsAndTheFullList() throws {
        let top = try Self.decode(StatsTop.self, Self.topJSON(source: "list", count: 4, list: true))
        let rows = StatsTopSection.rows(for: top)

        XCTAssertEqual(StatsTopSection.title(for: top), "Top 4")
        let ids = top.items.map(\.id)
        XCTAssertEqual(rows.map(\.id), ["top.title", "top.hero.\(ids[0])", "top.rank.2.\(ids[1])", "top.rank.3.\(ids[2])", "top.rank.4.\(ids[3])", "top.list"])
        guard case let .hero(hero, heroIsLast) = rows[1], case let .ranked(_, lastRankedIsLast) = rows[4] else {
            return XCTFail("Expected hero and ranked rows")
        }
        XCTAssertEqual(hero.rank, 1)
        XCTAssertFalse(heroIsLast)
        XCTAssertFalse(lastRankedIsLast, "See full list closes the card")
        XCTAssertEqual(rows.last, .seeFullList(StatsTopList(id: 81, name: "My 2025", total: 24)))
    }

    func testTopSortsByRank() throws {
        let top = try Self.decode(StatsTop.self, """
        {"source": "list", "tag": "top2025", "tag_hint": null, "hint_private": false,
         "list": {"id": 1, "name": "L", "total": 3},
         "items": [
           {"rank": 3, "media": \(Self.mediaJSON(id: 3, title: "C")), "stars": null},
           {"rank": 1, "media": \(Self.mediaJSON(id: 1, title: "A")), "stars": "5.0"},
           {"rank": 2, "media": \(Self.mediaJSON(id: 2, title: "B")), "stars": "4.5"}
         ]}
        """)
        var ranks: [Int] = []
        for row in StatsTopSection.rows(for: top) {
            switch row {
            case let .hero(item, _), let .ranked(item, _): ranks.append(item.rank)
            default: break
            }
        }
        XCTAssertEqual(ranks, [1, 2, 3])
    }

    func testPrivateListTellsTheOwnerOnlyTheyCanSeeIt() throws {
        let top = try Self.decode(StatsTop.self, Self.topJSON(source: "list", count: 2, list: true, hintPrivate: true))
        let rows = StatsTopSection.rows(for: top)

        XCTAssertTrue(rows.contains(.note("Only you can see this list.", systemImage: "lock", isLast: false)))
        XCTAssertEqual(rows.last, .seeFullList(StatsTopList(id: 81, name: "My 2025", total: 24)))
    }

    func testFallbackIsHighestRatedWithTheOwnersHint() throws {
        let top = try Self.decode(
            StatsTop.self,
            Self.topJSON(source: "highest_rated", count: 3, list: false, tagHint: "Tag a ranked movie list top2025")
        )

        XCTAssertFalse(top.isFromList)
        XCTAssertEqual(StatsTopSection.title(for: top), "Highest rated")
        XCTAssertEqual(StatsTopSection.hintText(for: top), "Tag a ranked movie list top2025")

        let rows = StatsTopSection.rows(for: top, highestRatedTotal: 40)
        XCTAssertEqual(rows.map(\.id), ["top.title", "top.bento", "top.hint"], "The fallback draws Highest rated's bento")
        XCTAssertEqual(rows.first, .title(showsAll: true))
        XCTAssertEqual(rows.last, .hint("Tag a ranked movie list top2025"))
        guard case let .bento(bento, hasHint) = rows[1] else { return XCTFail("Expected the bento") }
        XCTAssertTrue(hasHint)
        XCTAssertEqual([bento.lead.rank, bento.second?.rank] + bento.tiles.map(\.rank), [1, 2, 3])
        XCTAssertEqual(bento.lead.stars, 4.5)

        XCTAssertEqual(StatsTopSection.rows(for: top, highestRatedTotal: 3).first, .title(showsAll: false), "Nothing more to show")
        XCTAssertEqual(StatsTopSection.rows(for: top, highestRatedTotal: 40, canShowAll: false).first, .title(showsAll: false))
    }

    func testFallbackShowsAllOnceHighestRatedHasMoreThanTheBento() throws {
        let top = try Self.decode(StatsTop.self, Self.topJSON(source: "highest_rated", count: 10, list: false))
        let rows = StatsTopSection.rows(for: top, highestRatedTotal: 10)

        XCTAssertEqual(rows.first, .title(showsAll: false), "Top sends 10 and the bento shows all 10")
        guard case let .bento(bento, hasHint) = rows[1] else { return XCTFail("Expected the bento") }
        XCTAssertFalse(hasHint)
        XCTAssertEqual(bento.second?.rank, 2)
        XCTAssertEqual(bento.tiles.count, 8)
    }

    func testOtherViewersSeeTheFallbackWithoutAHint() throws {
        let top = try Self.decode(StatsTop.self, Self.topJSON(source: "highest_rated", count: 1, list: false))
        let rows = StatsTopSection.rows(for: top)

        XCTAssertNil(StatsTopSection.hintText(for: top))
        XCTAssertEqual(rows.map(\.id), ["top.title", "top.bento"])
        guard case let .bento(bento, hasHint) = rows[1] else { return XCTFail("Expected the bento") }
        XCTAssertFalse(hasHint)
        XCTAssertEqual(bento.lead.media.title, "T1")
        XCTAssertNil(bento.second)
        XCTAssertTrue(bento.tiles.isEmpty)
    }

    func testHintIsTheServersTextAndIgnoresBlanks() throws {
        let allTime = try Self.decode(StatsTop.self, Self.topJSON(source: "highest_rated", count: 1, list: false, tagHint: " Tag a ranked list with mixed media topalltime "))
        XCTAssertEqual(StatsTopSection.hintText(for: allTime), "Tag a ranked list with mixed media topalltime")

        let blank = try Self.decode(StatsTop.self, Self.topJSON(source: "highest_rated", count: 1, list: false, tagHint: "  "))
        XCTAssertNil(StatsTopSection.hintText(for: blank))
    }

    func testEmptyTopHasNoRowsAndASingleListItemIsJustTop() throws {
        XCTAssertTrue(StatsTopSection.rows(for: try Self.decode(StatsTop.self, Self.topJSON(source: "list", count: 0, list: true))).isEmpty)
        XCTAssertEqual(StatsTopSection.title(for: try Self.decode(StatsTop.self, Self.topJSON(source: "list", count: 1, list: true))), "Top")
    }

    func testTopAccessibilityReadsRankTitleYearAndStars() throws {
        let item = StatsTopItem(rank: 2, media: try Self.media(id: 1, title: "Dune", releaseDate: "2024-03-01"), stars: StatsDecimal("4.5"))
        XCTAssertEqual(StatsTopSection.accessibilityLabel(for: item), "Number 2, Dune, 2024, Rated 4.5 out of 5 stars")
    }

    // MARK: - Highest rated

    func testHighestRatedShowsOnlyWhenTopIsAList() throws {
        let highest = try Self.decode(StatsHighestRated.self, Self.highestRatedJSON(count: 5, total: 5))
        let listTop = try Self.decode(StatsTop.self, Self.topJSON(source: "list", count: 2, list: true))
        let fallbackTop = try Self.decode(StatsTop.self, Self.topJSON(source: "highest_rated", count: 2, list: false))

        XCTAssertTrue(StatsHighestRatedSection.isShown(top: listTop, highestRated: highest))
        XCTAssertFalse(StatsHighestRatedSection.isShown(top: fallbackTop, highestRated: highest))
        XCTAssertFalse(StatsHighestRatedSection.isShown(top: nil, highestRated: highest))
        XCTAssertFalse(StatsHighestRatedSection.isShown(top: listTop, highestRated: StatsHighestRated(total: 0, items: [])))
        XCTAssertFalse(StatsHighestRatedSection.isShown(top: listTop, highestRated: nil))
    }

    func testHighestRatedIsATitleThenOneBentoInTheServersOrder() throws {
        let highest = try Self.decode(StatsHighestRated.self, Self.highestRatedJSON(count: 6, total: 140))
        let rows = StatsHighestRatedSection.rows(for: highest)

        XCTAssertEqual(rows.map(\.id), ["highest.title", "highest.bento"], "The bento is one lazy row")
        guard case let .bento(bento) = rows[1] else { return XCTFail("Expected the bento") }
        XCTAssertEqual(bento.lead.media.title, "T1")
        XCTAssertEqual(bento.lead.rank, 1)
        XCTAssertEqual(bento.lead.stars, 5)
        XCTAssertEqual(bento.second?.media.title, "T2")
        XCTAssertEqual(bento.tiles.map(\.media.title), ["T3", "T4", "T5", "T6"])
        XCTAssertEqual(bento.tiles.map(\.rank), [3, 4, 5, 6])
        XCTAssertTrue(StatsHighestRatedSection.hasMore(highest))
        XCTAssertFalse(StatsHighestRatedSection.hasMore(try Self.decode(StatsHighestRated.self, Self.highestRatedJSON(count: 6, total: 6))))
        XCTAssertTrue(StatsHighestRatedSection.rows(for: StatsHighestRated(total: 0, items: [])).isEmpty)
    }

    func testBentoTakesTheTopTenAndShowAllCountsTheRest() throws {
        let twelve = try Self.decode(StatsHighestRated.self, Self.highestRatedJSON(count: 12, total: 12))
        guard case let .bento(bento) = StatsHighestRatedSection.rows(for: twelve)[1] else { return XCTFail("Expected the bento") }

        XCTAssertEqual(StatsBento.capacity, 10)
        XCTAssertEqual(bento.second?.rank, 2, "#2 tucks behind the lead")
        XCTAssertEqual(bento.tiles.map(\.rank), Array(3 ... 10), "#3 to #5 finish the first row, #6 to #10 the second")
        XCTAssertTrue(StatsHighestRatedSection.hasMore(twelve), "The payload's 11th and 12th need Show all")
        XCTAssertFalse(StatsHighestRatedSection.hasMore(try Self.decode(StatsHighestRated.self, Self.highestRatedJSON(count: 10, total: 10))))

        let one = try XCTUnwrap(StatsBento([StatsBentoEntry(rank: 1, media: try Self.media(id: 1, title: "A"), stars: nil)], id: "x"))
        XCTAssertNil(one.second)
        XCTAssertTrue(one.tiles.isEmpty)
        XCTAssertNil(StatsBento([], id: "x"))
    }

    func testBentoIsFiveColumnsWithTheLeadStandingAboveTheFirstRow() {
        XCTAssertEqual(StatsBentoMetrics.leadSlot.size.height / StatsBentoMetrics.leadSlot.size.width, 1.5, accuracy: 0.01)
        XCTAssertEqual(StatsBentoMetrics.tileSlot.size.height / StatsBentoMetrics.tileSlot.size.width, 1.5, accuracy: 0.01)
        XCTAssertGreaterThan(StatsBentoMetrics.leadSlot.size.width, StatsBentoMetrics.tileSlot.size.width, "#1 is the big one")

        // The narrowest phone: 375 pt less the page's 16 pt sides.
        XCTAssertGreaterThanOrEqual(StatsBentoMetrics.gap(width: 375 - 32), 4)

        for width: CGFloat in [375 - 32, 402 - 32] {
            let gap = StatsBentoMetrics.gap(width: width)
            let origins = StatsBentoMetrics.origins(width: width, count: 10)
            let tile = StatsBentoMetrics.tileSlot.size
            let lead = StatsBentoMetrics.leadSlot.size
            XCTAssertEqual(origins.count, 10)
            XCTAssertEqual(origins[0], .zero)
            XCTAssertEqual(origins[0].y + lead.height, origins[1].y + tile.height, "#1's bottom is level with the first row's")
            XCTAssertGreaterThan(lead.width, origins[1].x, "#2 sits over #1's bottom-right corner")
            XCTAssertEqual(origins[2].x - origins[1].x, tile.width + gap, accuracy: 0.001, "Even columns")
            XCTAssertEqual(origins[4].x + tile.width, width, accuracy: 0.001, "#5 ends at the edge")
            XCTAssertEqual(origins[5].x, 0, "#6 starts the second row")
            XCTAssertEqual(origins[6].x, origins[1].x, "The rows share their columns")
            XCTAssertEqual(origins[9].x + tile.width, width, accuracy: 0.001, "#10 ends at the edge")
            XCTAssertEqual(origins[5].y, origins[1].y + tile.height + StatsBentoMetrics.rowGap)
            XCTAssertEqual(origins[9].y + tile.height, StatsBentoMetrics.height, accuracy: 0.001)
        }
        XCTAssertEqual(StatsBentoMetrics.origins(width: 370, count: 3).count, 3)
        XCTAssertTrue(StatsBentoMetrics.origins(width: 370, count: 0).isEmpty)
    }

    func testBentoCellsReadTheirRank() throws {
        let entry = StatsBentoEntry(rank: 2, media: try Self.media(id: 1, title: "Dune"), stars: 4.5)
        XCTAssertEqual(StatsBentoCell.accessibilityLabel(for: entry), "Number 2, Dune, Rated 4.5 out of 5 stars")
        let unrated = StatsBentoEntry(rank: 7, media: try Self.media(id: 1, title: "Dune"), stars: nil)
        XCTAssertEqual(StatsBentoCell.accessibilityLabel(for: unrated), "Number 7, Dune")
    }

    func testRatedPosterAccessibilityIncludesStars() throws {
        let rated = StatsRatedTitle(media: try Self.media(id: 1, title: "Dune"), stars: StatsDecimal("4.0"), day: nil)
        XCTAssertEqual(StatsRatedPosterCell.accessibilityText(for: rated), "Dune, Rated 4 out of 5 stars")
        let unrated = StatsRatedTitle(media: try Self.media(id: 1, title: "Dune"), stars: nil, day: nil)
        XCTAssertEqual(StatsRatedPosterCell.accessibilityText(for: unrated), "Dune")
    }

    // MARK: - Most logged

    func testMostLoggedLabelsPerType() {
        XCTAssertEqual(
            [nil, "movie", "tv", "anime", "book", "manga", "comic", "game", "music"].map { StatsMostLoggedSection.label(for: $0) },
            ["Most logged", "Most watched", "Most watched", "Most watched", "Most read", "Most read", "Most read", "Most played", "Most listened"]
        )
    }

    func testMostLoggedTitleUsesThePayloadLabelFirst() {
        let labelled = StatsMostLogged(label: "Most played", total: 1, items: [])
        XCTAssertEqual(StatsMostLoggedSection.title(for: labelled, mediaType: "movie"), "Most played")
        let blank = StatsMostLogged(label: " ", total: 1, items: [])
        XCTAssertEqual(StatsMostLoggedSection.title(for: blank, mediaType: "book"), "Most read")
    }

    func testTimesCaptions() {
        XCTAssertEqual([1, 2, 3, 5, 1200].map { StatsMostLoggedSection.timesCaption($0) }, ["Once", "Twice", "3 times", "5 times", "\(1200.formatted()) times"])
    }

    func testMostLoggedRowsAreGridRows() throws {
        let mostLogged = try Self.decode(StatsMostLogged.self, """
        {"label": "Most watched", "total": 7, "items": [
          {"media": \(Self.mediaJSON(id: 1, title: "A")), "times": 5},
          {"media": \(Self.mediaJSON(id: 2, title: "B")), "times": 2}
        ]}
        """)
        let rows = StatsMostLoggedSection.rows(for: mostLogged)
        XCTAssertEqual(rows.map(\.id), ["mostLogged.title", "mostLogged.0"])
        guard case let .posters(row, isLast) = rows[1] else { return XCTFail("Expected a poster row") }
        XCTAssertEqual(row.items.map(\.times), [5, 2])
        XCTAssertTrue(isLast)
        XCTAssertTrue(StatsMostLoggedSection.rows(for: StatsMostLogged(label: "", total: 0, items: [])).isEmpty)
    }

    // MARK: - Milestones

    func testMilestoneLabels() throws {
        XCTAssertEqual([1, 100, 250, 1000, 1500].map { StatsMilestonesSection.numberLabel($0) }, [
            "1st log", "100th log", "250th log", "\(1000.formatted())th log", "\(1500.formatted())th log",
        ])
        let milestone = StatsMilestone(number: 100, day: "2024-03-12", entryId: 9312, mediaType: "tv", media: try Self.media(id: 1, title: "Severance"))
        XCTAssertEqual(StatsMilestonesSection.accessibilityText(for: milestone, locale: Locale(identifier: "en_GB")), "100th log, Severance, 12 Mar 2024")
        XCTAssertEqual(StatsMilestonesSection.dayLabel("2024-03-12", period: .allTime, locale: Locale(identifier: "en_GB")), "12 Mar 2024")
    }

    func testAYearsMilestonesCountThatYearsLogs() throws {
        let year = StatsPeriod.year(2025)
        XCTAssertEqual(StatsMilestonesSection.numberLabel(100, period: year), "100th log of 2025")
        XCTAssertEqual(StatsMilestonesSection.numberLabel(1, period: year), "1st log of 2025")
        XCTAssertEqual(StatsMilestonesSection.numberLines(100, period: year), ["100th log", "of 2025"], "Every tile breaks in the same place")
        XCTAssertEqual(StatsMilestonesSection.numberLines(100, period: .allTime), ["100th log"])
        XCTAssertEqual(
            StatsMilestonesSection.dayLabel("2025-03-12", period: year, locale: Locale(identifier: "en_GB")),
            "12 Mar",
            "The label already names the year"
        )
        XCTAssertEqual(StatsMilestonesSection.dayLabel("2025-03-12", period: year, locale: Locale(identifier: "en_US")), "Mar 12")

        let milestone = StatsMilestone(number: 250, day: "2025-07-04", entryId: 1, mediaType: "movie", media: try Self.media(id: 1, title: "Heat"))
        XCTAssertEqual(
            StatsMilestonesSection.accessibilityText(for: milestone, period: year, locale: Locale(identifier: "en_GB")),
            "250th log of 2025, Heat, 4 Jul 2025"
        )
    }

    func testMilestonesSortByNumberIntoGridRows() throws {
        let media = try Self.media(id: 1, title: "A")
        let milestones = [500, 1, 250, 100, 750].map {
            StatsMilestone(number: $0, day: "2024-01-01", entryId: $0, mediaType: "movie", media: media)
        }
        let rows = StatsMilestonesSection.rows(for: milestones)

        XCTAssertEqual(rows.map(\.id), ["milestones.title", "milestones.0", "milestones.1"])
        guard case let .posters(first, _) = rows[1], case let .posters(second, isLast) = rows[2] else {
            return XCTFail("Expected poster rows")
        }
        XCTAssertEqual(first.items.map(\.number) + second.items.map(\.number), [1, 100, 250, 500, 750])
        XCTAssertTrue(isLast)
        XCTAssertTrue(StatsMilestonesSection.rows(for: []).isEmpty)
    }

    // MARK: - Rated higher than most

    func testRatedVsEveryoneHasATitleAndPickerThenTheSidesRows() throws {
        let both = try Self.decode(StatsRatedVsEveryone.self, Self.ratedVsEveryoneJSON(higher: 2, lower: 1))
        let rows = StatsRatedVsEveryoneSection.rows(for: both, side: .higher)

        XCTAssertEqual(Array(rows.map(\.id).prefix(2)), ["vs.title", "vs.picker"])
        XCTAssertEqual(Self.comparedTitles(rows), ["H1", "H2"])
        XCTAssertEqual(rows.last, .sources("Everyone's ratings from Letterboxd and IMDb"))
        XCTAssertEqual(StatsRatedVsEveryoneSection.title, "Rated higher than most")

        let lowerRows = StatsRatedVsEveryoneSection.rows(for: both, side: .lower)
        XCTAssertEqual(Self.comparedTitles(lowerRows, withEdges: true), ["L1truetrue"])
    }

    func testRatedVsEveryoneWithOneSideStartsOnItAndKeepsTheTitle() throws {
        let lowerOnly = try Self.decode(StatsRatedVsEveryone.self, Self.ratedVsEveryoneJSON(higher: 0, lower: 2))

        XCTAssertEqual(StatsRatedVsEveryoneSection.effectiveSide(chosen: nil, in: lowerOnly), .lower)
        XCTAssertEqual(StatsRatedVsEveryoneSection.effectiveSide(chosen: .higher, in: lowerOnly), .higher, "The user's pick stands")
        let lowerRows = StatsRatedVsEveryoneSection.rows(for: lowerOnly, side: .lower)
        XCTAssertEqual(Array(lowerRows.map(\.id).prefix(2)), ["vs.title", "vs.picker"])
        XCTAssertEqual(Self.comparedTitles(lowerRows), ["L1", "L2"])

        let higherRows = StatsRatedVsEveryoneSection.rows(for: lowerOnly, side: .higher)
        XCTAssertTrue(higherRows.contains(.empty(.higher)))
        XCTAssertTrue(Self.comparedTitles(higherRows).isEmpty)
        XCTAssertEqual(StatsRatedVsEveryoneSection.emptyText(.higher), "No titles rated higher than most")

        let higherOnly = try Self.decode(StatsRatedVsEveryone.self, Self.ratedVsEveryoneJSON(higher: 1, lower: 0))
        XCTAssertEqual(StatsRatedVsEveryoneSection.effectiveSide(chosen: nil, in: higherOnly), .higher)
        XCTAssertEqual(StatsRatedVsEveryoneSection.effectiveSide(chosen: nil, in: try Self.decode(StatsRatedVsEveryone.self, Self.ratedVsEveryoneJSON(higher: 1, lower: 1))), .higher)

        XCTAssertTrue(StatsRatedVsEveryoneSection.rows(for: StatsRatedVsEveryone(sources: [], higher: [], lower: []), side: .higher).isEmpty)
    }

    func testSeasonsInHighestRatedGetTheirSeasonTag() {
        let season = MediaRef(itemId: 5, source: "tmdb", mediaType: "season", mediaId: "1", seasonNumber: 2, episodeNumber: nil)
        let specials = MediaRef(itemId: 6, source: "tmdb", mediaType: "season", mediaId: "1", seasonNumber: 0, episodeNumber: nil)
        let show = MediaRef(itemId: 7, source: "tmdb", mediaType: "tv", mediaId: "1", seasonNumber: nil, episodeNumber: nil)

        XCTAssertEqual(StatsRatedPosterCell.seasonTag(for: season), "S2")
        XCTAssertEqual(StatsRatedPosterCell.seasonTag(for: specials), "Specials")
        XCTAssertNil(StatsRatedPosterCell.seasonTag(for: show))
    }

    func testRatedVsEveryoneWords() throws {
        let section = try Self.decode(StatsRatedVsEveryone.self, Self.ratedVsEveryoneJSON(higher: 1, lower: 0))
        let item = try XCTUnwrap(section.higher.first)

        XCTAssertEqual(StatsRatedVsEveryoneSection.everyoneText(item), "Everyone 3.1")
        XCTAssertEqual(StatsRatedVsEveryoneSection.accessibilityLabel(for: item), "H1, you rated 4.5, everyone 3.1")
        XCTAssertEqual(
            StatsRatedVsEveryoneSection.sourcesNote([
                StatsCommunitySource(mediaType: "movie", source: "letterboxd", label: "Letterboxd"),
                StatsCommunitySource(mediaType: "tv", source: "imdb", label: "IMDb"),
                StatsCommunitySource(mediaType: "game", source: "igdb", label: "IGDB"),
            ]),
            "Everyone's ratings from Letterboxd, IMDb and IGDB"
        )
        XCTAssertEqual(
            StatsRatedVsEveryoneSection.sourcesNote([
                StatsCommunitySource(mediaType: "anime", source: "mal", label: "MyAnimeList"),
                StatsCommunitySource(mediaType: "manga", source: "mal", label: "MyAnimeList"),
            ]),
            "Everyone's ratings from MyAnimeList"
        )
        XCTAssertNil(StatsRatedVsEveryoneSection.sourcesNote([]))
    }

    // MARK: - Show-all pages

    func testHighestRatedShowAllPagesThroughTheEndpoint() async throws {
        let repository = StatsShowAllRepositoryFake()
        repository.highestRatedHandler = { request in
            try Self.decode(
                PagedResponse<StatsRatedTitle>.self,
                request.page == nil
                    ? Self.pageJSON(results: (1 ... 48).map { Self.ratedJSON($0) }, count: 50, next: "https://example.com/api/v1/stats/me/highest-rated/?page=2")
                    : Self.pageJSON(results: (48 ... 50).map { Self.ratedJSON($0) }, count: 50, next: nil)
            )
        }
        let viewModel = StatsHighestRatedViewModel.highestRated(
            profileRepository: repository,
            username: "mika",
            period: .year(2025),
            mediaType: "movie",
            expectedTotal: 12,
            onUnauthorized: {}
        )

        XCTAssertEqual(viewModel.totalCount, 12)
        await viewModel.load()
        XCTAssertEqual(viewModel.items.count, 48)
        XCTAssertEqual(viewModel.totalCount, 50)
        XCTAssertEqual(viewModel.items.first?.stars?.text, "5.0")

        await viewModel.loadNextPageIfNeeded(currentItemID: viewModel.items[47].id)
        XCTAssertEqual(viewModel.items.count, 50, "The repeated row is dropped")
        XCTAssertFalse(viewModel.hasMorePages)
        XCTAssertEqual(repository.requests, [
            StatsShowAllFakeRequest(kind: "highest", username: "mika", period: .year(2025), mediaType: "movie", page: nil, pageSize: 48),
            StatsShowAllFakeRequest(kind: "highest", username: "mika", period: .year(2025), mediaType: "movie", page: "2", pageSize: 48),
        ])
    }

    func testRefreshingWhileANextPageLoadsKeepsPagingWorking() async throws {
        let gate = RefreshGate()
        var isHolding = false
        var heldOnce = false
        let model = StatsTitlesPageModel<Tile>(pageSize: 2, onUnauthorized: {}) { page, _ in
            if page == "2", !heldOnce {
                heldOnce = true
                isHolding = true
                await gate.wait()
                isHolding = false
            }
            return .init(
                count: 4,
                next: page == nil ? "https://example.com/api/v1/stats/me/highest-rated/?page=2" : nil,
                results: (page == nil ? [1, 2] : [3, 4]).map { Tile(id: $0) }
            )
        }

        await model.load()
        let heldPage = Task { await model.loadNextPage() }
        while !isHolding { await Task.yield() }
        XCTAssertTrue(model.isLoadingNextPage)

        // Pull to refresh while the next page is still on its way.
        await model.load()
        XCTAssertFalse(model.isLoadingNextPage)
        await gate.open()
        await heldPage.value
        XCTAssertEqual(model.items.map(\.id), [1, 2], "The old generation's page is dropped")

        await model.loadNextPage()
        XCTAssertEqual(model.items.map(\.id), [1, 2, 3, 4], "Paging still works after the refresh")
        XCTAssertFalse(model.isLoadingNextPage)
    }

    func testMostLoggedShowAllUsesTheFoldedEndpoint() async throws {
        var didCallUnauthorized = false
        let repository = StatsShowAllRepositoryFake()
        repository.mostLoggedHandler = { _ in
            try Self.decode(
                PagedResponse<StatsMostLoggedTitle>.self,
                Self.pageJSON(results: [#"{"media": \#(Self.mediaJSON(id: 7, title: "Severance", type: "tv")), "times": 3}"#], count: 1, next: nil)
            )
        }
        let viewModel = StatsMostLoggedViewModel.mostLogged(
            profileRepository: repository,
            username: nil,
            period: .allTime,
            mediaType: "tv",
            onUnauthorized: { didCallUnauthorized = true }
        )

        await viewModel.load()
        XCTAssertEqual(viewModel.items.map(\.times), [3])
        XCTAssertEqual(repository.requests.map(\.kind), ["mostLogged"])
        XCTAssertEqual(repository.requests.first?.period, .allTime)

        repository.mostLoggedHandler = { _ in throw APIError.unauthorized }
        await viewModel.load()
        XCTAssertTrue(didCallUnauthorized)
        XCTAssertNotNil(viewModel.errorMessage)
    }

    // MARK: - Helpers

    private struct Tile: Identifiable, Equatable {
        let id: Int
    }

    /// The compared rows' titles, with their first/last flags when `withEdges` ("L1truetrue").
    private static func comparedTitles(_ rows: [StatsRatedVsEveryoneSection.Row], withEdges: Bool = false) -> [String] {
        var titles: [String] = []
        for row in rows {
            guard case let .compared(item, _, isFirst, isLast) = row else { continue }
            titles.append(withEdges ? "\(item.media.title)\(isFirst)\(isLast)" : item.media.title)
        }
        return titles
    }

    private static func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(T.self, from: Data(json.utf8))
    }

    private static func mediaJSON(id: Int, title: String, type: String = "movie", releaseDate: String? = nil) -> String {
        """
        {"ref": {"item_id": \(id), "source": "tmdb", "media_type": "\(type)", "media_id": "\(id)", \
        "season_number": null, "episode_number": null}, \
        "title": "\(title)", "poster_url": "https://example.com/\(id).jpg", \
        "release_date": \(releaseDate.map { "\"\($0)\"" } ?? "null")}
        """
    }

    private static func media(id: Int, title: String, releaseDate: String? = nil) throws -> MediaSummary {
        try decode(MediaSummary.self, mediaJSON(id: id, title: title, releaseDate: releaseDate))
    }

    private static func detail(value: Int, unit: String?, approximate: Bool = false) -> StatsHeaderDetail {
        StatsHeaderDetail(key: "k", label: "Label", value: value, unit: unit, approximate: approximate)
    }

    private static func timeRow(_ type: String, _ unit: String, _ value: Int, approximate: Bool = false) -> StatsTimeRow {
        StatsTimeRow(mediaType: type, unit: unit, value: value, known: 1, total: 1, approximate: approximate)
    }

    private static let headerJSON = """
    {"titles": 212, "logs": 268, "active_days": 141, "longest_daily_streak": 9, "details": [
      {"key": "current_daily_streak", "label": "Current daily streak", "value": 3, "unit": "day", "approximate": false},
      {"key": "rewatches", "label": "Repeats", "value": 17, "unit": null, "approximate": false},
      {"key": "episodes", "label": "Episodes watched", "value": 48, "unit": null, "approximate": true}
    ]}
    """

    private static func topJSON(
        source: String,
        count: Int,
        list: Bool,
        hintPrivate: Bool = false,
        tagHint: String? = nil
    ) -> String {
        let items = (0 ..< count).map { index in
            #"{"rank": \#(index + 1), "media": \#(mediaJSON(id: index + 1, title: "T\(index + 1)")), "stars": "4.5"}"#
        }
        return """
        {"source": "\(source)", "tag": "top2025", "tag_hint": \(tagHint.map { "\"\($0)\"" } ?? "null"),
         "hint_private": \(hintPrivate),
         "list": \(list ? #"{"id": 81, "name": "My 2025", "total": 24}"# : "null"),
         "items": [\(items.joined(separator: ","))]}
        """
    }

    private static func ratedJSON(_ id: Int) -> String {
        #"{"media": \#(mediaJSON(id: id, title: "T\(id)")), "stars": "\#(id == 1 ? 5 : 4).0", "day": "2025-03-02"}"#
    }

    private static func highestRatedJSON(count: Int, total: Int) -> String {
        let items = (0 ..< count).map { ratedJSON($0 + 1) }
        return #"{"total": \#(total), "items": [\#(items.joined(separator: ","))]}"#
    }

    private static func ratedVsEveryoneJSON(higher: Int, lower: Int) -> String {
        let higherRows = (0 ..< higher).map { index in
            #"{"media": \#(mediaJSON(id: 100 + index, title: "H\(index + 1)")), "stars": "4.5", "everyone_stars": "3.1", "difference": "1.4", "source": "letterboxd"}"#
        }
        let lowerRows = (0 ..< lower).map { index in
            #"{"media": \#(mediaJSON(id: 200 + index, title: "L\(index + 1)", type: "tv")), "stars": "2.0", "everyone_stars": "4.2", "difference": "-2.2", "source": "imdb"}"#
        }
        return """
        {"sources": [{"media_type": "movie", "source": "letterboxd", "label": "Letterboxd"},
                     {"media_type": "tv", "source": "imdb", "label": "IMDb"}],
         "higher": [\(higherRows.joined(separator: ","))],
         "lower": [\(lowerRows.joined(separator: ","))]}
        """
    }

    private static func pageJSON(results: [String], count: Int, next: String?) -> String {
        #"{"count": \#(count), "next": \#(next.map { "\"\($0)\"" } ?? "null"), "previous": null, "results": [\#(results.joined(separator: ","))]}"#
    }
}

private struct StatsShowAllFakeRequest: Equatable {
    let kind: String
    let username: String?
    let period: StatsPeriod
    let mediaType: String?
    let page: String?
    let pageSize: Int
}

private final class StatsShowAllRepositoryFake: ProfileRepository {
    var highestRatedHandler: ((StatsShowAllFakeRequest) async throws -> PagedResponse<StatsRatedTitle>)?
    var mostLoggedHandler: ((StatsShowAllFakeRequest) async throws -> PagedResponse<StatsMostLoggedTitle>)?
    private(set) var requests: [StatsShowAllFakeRequest] = []

    func statsHighestRated(
        username: String?,
        period: StatsPeriod,
        mediaType: String?,
        page: String?,
        pageSize: Int
    ) async throws -> PagedResponse<StatsRatedTitle> {
        let request = StatsShowAllFakeRequest(kind: "highest", username: username, period: period, mediaType: mediaType, page: page, pageSize: pageSize)
        requests.append(request)
        return try await highestRatedHandler!(request)
    }

    func statsMostLoggedTitles(
        username: String?,
        period: StatsPeriod,
        mediaType: String?,
        page: String?,
        pageSize: Int
    ) async throws -> PagedResponse<StatsMostLoggedTitle> {
        let request = StatsShowAllFakeRequest(kind: "mostLogged", username: username, period: period, mediaType: mediaType, page: page, pageSize: pageSize)
        requests.append(request)
        return try await mostLoggedHandler!(request)
    }

    func me() async throws -> UserProfile { fatalError("Not used") }
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] { fatalError("Not used") }
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] { fatalError("Not used") }
}
