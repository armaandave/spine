import Foundation
import XCTest
@testable import Spine

/// Real stats v2 responses, captured from a seeded sample library (`Fixtures/StatsV2/`), decode
/// without losing anything: every section the JSON fills decodes, with the same number of rows,
/// and the page builds its section list from it (STAT-301, STAT-302, STAT-218). The decoders are
/// lossy on purpose (a bad row is dropped, a bad block hidden), so a shape mismatch would
/// otherwise pass silently as an empty section.
///
/// The fixtures are trimmed copies of real responses: long lists are capped, every key is kept.
@MainActor
final class StatsRealPayloadTests: XCTestCase {
    private typealias JSON = [String: Any]

    // MARK: - Every summary fixture

    func testEverySummaryFixtureDecodesEverySectionItHas() throws {
        let names = try Self.fixtureNames().filter { !$0.contains("show-all") }
        XCTAssertGreaterThanOrEqual(names.count, 20, "Expected the captured summary fixtures")

        for name in names {
            let (page, json) = try Self.load(name)
            assertDecodesEverything(page, json, fixture: name)
        }
    }

    func testEverySummaryFixtureBuildsItsSectionList() throws {
        for name in try Self.fixtureNames().filter({ !$0.contains("show-all") }) {
            let (page, json) = try Self.load(name)
            XCTAssertEqual(
                StatsPageSection.sections(for: page),
                Self.expectedSections(json),
                "\(name): the page's sections don't match the payload"
            )
            XCTAssertFalse(page.isEmpty, "\(name): the sample library fills every fixture")
            XCTAssertEqual(StatsPageSection.sections(for: page).first, .header, name)
        }
    }

    func testTheSampleLibraryFillsEverySectionSomewhere() throws {
        var filled = Set<StatsPageSection>()
        for name in try Self.fixtureNames().filter({ !$0.contains("show-all") }) {
            filled.formUnion(StatsPageSection.sections(for: try Self.load(name).page))
        }
        // The fixtures were captured before People (STAT-219) existed.
        XCTAssertEqual(filled, Set(StatsPageSection.typeOrder + StatsPageSection.globalOrder).subtracting([.people]))
    }

    // MARK: - What the sample library shows

    func testGlobalAllTimeTopIsTheOwnersMixedTopAllTimeList() throws {
        let page = try Self.load("global-all-time").page

        XCTAssertEqual(page.audience, .owner)
        XCTAssertTrue(page.range.isAllTime)
        XCTAssertEqual(page.top?.source, .list)
        XCTAssertEqual(page.top?.tag, "topalltime")
        XCTAssertEqual(page.top?.list?.name, "All-Time Favourites")
        XCTAssertTrue(StatsPageSection.sections(for: page).contains(.highestRated))
        XCTAssertNotNil(page.time?.hoursTotal)
        XCTAssertEqual(page.mediaTypes.map(\.mediaType), ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"])
        XCTAssertNil(page.collections, "Collections are null on Global")
        XCTAssertNotNil(page.header?.details.first { $0.key == "current_daily_streak" })
    }

    func testThePrivateTop2025ListShowsOnlyToItsOwner() throws {
        let owner = try Self.load("global-2025").page
        XCTAssertEqual(owner.top?.source, .list)
        XCTAssertTrue(owner.top?.hintPrivate ?? false, "Only you can see this list.")
        XCTAssertNil(owner.header?.details.first { $0.key == "current_daily_streak" }, "2025 doesn't include today")

        let friend = try Self.load("friend-global-2025").page
        XCTAssertEqual(friend.audience, .follower)
        XCTAssertEqual(friend.top?.source, .highestRated)
        XCTAssertNil(friend.top?.tagHint)
        XCTAssertFalse(friend.top?.hintPrivate ?? true)
        XCTAssertTrue(StatsPageSection.sections(for: friend).contains(.highestRated), "Highest rated keeps its place")
        XCTAssertFalse(StatsPageSection.sections(for: friend).contains(.top), "No list and no hint for a friend")
    }

    func testAYearWithoutATaggedListTellsTheOwnerHowToTagOne() throws {
        let global = try Self.load("global-2024").page
        XCTAssertEqual(global.top?.source, .highestRated)
        XCTAssertEqual(global.top?.tagHint, "Tag a ranked list with mixed media top2024")

        let movies = try Self.load("movie-2024").page
        XCTAssertEqual(movies.top?.tagHint, "Tag a ranked movie list top2024")
        XCTAssertEqual(
            StatsTopSection.rows(for: try XCTUnwrap(movies.top)).last,
            .empty("Tag a ranked movie list top2024 to show it here.")
        )
        XCTAssertTrue(StatsPageSection.sections(for: movies).contains(.highestRated), "Highest rated keeps its place")
    }

    func testTheMovieTabHasItsPublicTopListCollectionsAndLanguages() throws {
        let page = try Self.load("movie-2025").page

        XCTAssertEqual(page.top?.list?.name, "My Top Films of 2025")
        XCTAssertEqual(page.top?.items.map(\.rank), [1, 2, 3])
        XCTAssertGreaterThan(page.collections?.completeCount ?? 0, 0)
        XCTAssertGreaterThan(page.collections?.almostCount ?? 0, 0)
        XCTAssertEqual(page.languages?.mediaTypes, ["movie"])
        XCTAssertEqual(page.time?.rows.map(\.mediaType), ["movie"])
        XCTAssertNil(page.time?.hoursTotal)

        let friend = try Self.load("friend-movie-2025").page
        XCTAssertEqual(friend.top?.list?.name, "My Top Films of 2025", "A public list feeds Top for everyone")
    }

    func testTheTVTabRanksSeasonsAndCountsTheShowState() throws {
        let page = try Self.load("tv-all-time").page

        let season = try XCTUnwrap(page.highestRated?.items.first { $0.media.ref.mediaType == "season" })
        let number = try XCTUnwrap(season.media.ref.seasonNumber)
        XCTAssertEqual(season.media.displayTitle, "\(season.media.title) S\(number)")
        XCTAssertFalse(page.highestRated?.items.contains { $0.media.ref.mediaType == "episode" } ?? true)

        let details = Set(page.header?.details.map(\.key) ?? [])
        XCTAssertTrue(details.isSuperset(of: ["episodes", "seasons_finished", "shows_finished"]))
        XCTAssertEqual(page.mostLogged?.label, "Most watched")
        XCTAssertEqual(page.time?.rows.first?.approximate, true, "Season and show runtimes stand in for some episodes")
    }

    func testEachTypeTabLabelsItsOwnDetails() throws {
        XCTAssertEqual(try Self.detail("book-all-time", "rewatches")?.label, "Rereads")
        XCTAssertNotNil(try Self.detail("book-all-time", "reads"))
        XCTAssertNotNil(try Self.detail("book-all-time", "did_not_finish"))
        XCTAssertEqual(try Self.detail("game-all-time", "rewatches")?.label, "Replays")
        XCTAssertNotNil(try Self.detail("game-all-time", "completions"))
        XCTAssertNotNil(try Self.detail("game-all-time", "abandoned"))
        XCTAssertEqual(try Self.detail("music-all-time", "rewatches")?.label, "Relistens")
        XCTAssertEqual(try Self.detail("anime-all-time", "episodes")?.label, "Episodes watched")

        XCTAssertEqual(try Self.load("book-all-time").page.time?.rows.first?.unit, "pages")
        XCTAssertEqual(try Self.load("manga-all-time").page.time?.rows.first?.unit, "chapters")
        XCTAssertEqual(try Self.load("comic-all-time").page.time?.rows.first?.unit, "issues")
        XCTAssertEqual(try Self.load("anime-2025").page.time?.rows.first?.approximate, true)
    }

    // MARK: - Show-all pages (STAT-310)

    func testShowAllPagesDecodeEveryRow() throws {
        for name in try Self.fixtureNames().filter({ $0.contains("show-all") }) {
            let data = try Self.data(name)
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? JSON, name)
            let results = try XCTUnwrap(json["results"] as? [Any], name)
            XCTAssertFalse(results.isEmpty, "\(name): the sample library fills it")

            var decoded = 0
            var count = 0
            if name.contains("highest-rated") {
                let page = try JSONDecoder.api.decode(PagedResponse<StatsRatedTitle>.self, from: data)
                XCTAssertTrue(page.results.allSatisfy { $0.stars != nil }, name)
                decoded = page.results.count
                count = page.count
            } else if name.contains("most-logged") {
                let page = try JSONDecoder.api.decode(PagedResponse<StatsMostLoggedTitle>.self, from: data)
                XCTAssertTrue(page.results.allSatisfy { $0.times >= 2 }, name)
                decoded = page.results.count
                count = page.count
            } else {
                let page = try JSONDecoder.api.decode(PagedResponse<MediaSeriesSummary>.self, from: data)
                XCTAssertTrue(page.results.allSatisfy { $0.completion != nil && !$0.posterUrls.isEmpty }, name)
                decoded = page.results.count
                count = page.count
            }
            XCTAssertEqual(decoded, results.count, "\(name): rows were dropped")
            XCTAssertEqual(count, json["count"] as? Int, name)
        }
    }

    // MARK: - Checks

    private func assertDecodesEverything(_ page: StatsPage, _ json: JSON, fixture name: String) {
        func rows(_ object: Any?, _ key: String) -> [Any] {
            (object as? JSON)?[key] as? [Any] ?? []
        }
        func isNull(_ key: String) -> Bool { json[key] is NSNull || json[key] == nil }

        XCTAssertEqual(page.schemaVersion, 2, name)
        XCTAssertFalse(page.isLegacy, name)
        XCTAssertEqual(page.mediaType, json["media_type"] as? String, name)
        XCTAssertEqual(page.audience?.rawValue, json["audience"] as? String, name)
        XCTAssertEqual(page.years?.current, (json["years"] as? JSON)?["current"] as? Int, name)
        XCTAssertEqual(page.years?.first, (json["years"] as? JSON)?["first"] as? Int, name)
        XCTAssertEqual(page.mediaTypes.count, (json["media_types"] as? [Any])?.count, name)
        XCTAssertEqual(page.range.isAllTime, (json["range"] as? JSON)?["is_all_time"] as? Bool, name)

        // Header
        let header = json["header"] as? JSON
        XCTAssertNotNil(page.header, name)
        XCTAssertEqual(page.header?.titles, header?["titles"] as? Int, name)
        XCTAssertEqual(page.header?.logs, header?["logs"] as? Int, name)
        XCTAssertEqual(page.header?.activeDays, header?["active_days"] as? Int, name)
        XCTAssertEqual(page.header?.longestDailyStreak, header?["longest_daily_streak"] as? Int, name)
        XCTAssertEqual(page.header?.details.map(\.key), rows(header, "details").compactMap { ($0 as? JSON)?["key"] as? String }, name)

        // Time
        let time = json["time"] as? JSON
        XCTAssertNotNil(page.time, name)
        XCTAssertEqual(page.time?.rows.count, rows(time, "rows").count, "\(name): time rows")
        XCTAssertEqual(page.time?.hoursTotal == nil, time?["hours_total"] is NSNull, "\(name): hours_total")

        // Top and Highest rated
        let top = json["top"] as? JSON
        XCTAssertEqual(page.top?.source.rawValue, top?["source"] as? String, name)
        XCTAssertEqual(page.top?.items.count, rows(top, "items").count, "\(name): top items")
        XCTAssertEqual(page.top?.list == nil, top?["list"] is NSNull, "\(name): top list")
        XCTAssertEqual(page.top?.tag, top?["tag"] as? String, name)
        XCTAssertEqual(page.top?.tagHint, top?["tag_hint"] as? String, name)
        XCTAssertEqual(page.top?.hintPrivate, top?["hint_private"] as? Bool, name)
        let highest = json["highest_rated"] as? JSON
        XCTAssertEqual(page.highestRated?.items.count, rows(highest, "items").count, "\(name): highest rated")
        XCTAssertEqual(page.highestRated?.total, highest?["total"] as? Int, name)

        // Activity
        let activity = json["activity"] as? JSON
        XCTAssertNotNil(page.activity, name)
        XCTAssertEqual(page.activity?.months.count, rows(activity, "months").count, "\(name): months")
        XCTAssertEqual(page.activity?.weekdays.count, rows(activity, "weekdays").count, "\(name): weekdays")
        XCTAssertEqual(page.activity?.weekStartsOn, activity?["week_starts_on"] as? String, name)
        XCTAssertEqual(page.activity?.busiestDay == nil, activity?["busiest_day"] is NSNull, "\(name): busiest day")
        XCTAssertEqual(page.activity?.busiestMonth == nil, activity?["busiest_month"] is NSNull, "\(name): busiest month")

        // Ratings
        let ratings = json["ratings"] as? JSON
        XCTAssertEqual(page.ratings?.rated, ratings?["rated"] as? Int, name)
        XCTAssertEqual(page.ratings?.averageStars?.text, ratings?["average_stars"] as? String, name)
        XCTAssertEqual(page.ratings?.distribution.count, 10, name)
        XCTAssertEqual(page.ratings?.distribution.map(\.stars.text), rows(ratings, "distribution").compactMap { ($0 as? JSON)?["stars"] as? String }, name)

        // Rated higher than most
        let compared = json["rated_vs_everyone"] as? JSON
        XCTAssertEqual(page.ratedVsEveryone?.sources.count, rows(compared, "sources").count, "\(name): sources")
        XCTAssertEqual(page.ratedVsEveryone?.higher.count, rows(compared, "higher").count, "\(name): higher")
        XCTAssertEqual(page.ratedVsEveryone?.lower.count, rows(compared, "lower").count, "\(name): lower")
        for row in (page.ratedVsEveryone?.higher ?? []) + (page.ratedVsEveryone?.lower ?? []) {
            XCTAssertNotNil(row.stars, name)
            XCTAssertNotNil(row.everyoneStars, name)
            XCTAssertNotNil(row.difference, name)
        }

        // Release years
        let years = json["release_years"] as? JSON
        XCTAssertEqual(page.releaseYears?.rows.count, rows(years, "rows").count, "\(name): release years")
        XCTAssertEqual(page.releaseYears?.coverage == nil, years?["coverage"] is NSNull, name)
        XCTAssertEqual(page.releaseYears?.newVsOlder == nil, years?["new_vs_older"] is NSNull, name)

        // Genres
        let genres = json["genres"] as? JSON
        XCTAssertEqual(page.genres?.rows.count, rows(genres, "rows").count, "\(name): genres")
        XCTAssertEqual(page.genres?.music.count, rows(genres, "music").count, "\(name): music genres")
        XCTAssertTrue(page.genres?.music.allSatisfy { !($0.values ?? []).isEmpty } ?? true, "\(name): music genre spellings")
        XCTAssertEqual(page.genres?.total, genres?["total"] as? Int, name)
        XCTAssertNotNil(page.genres?.coverage, name)
        if page.mediaType == nil {
            XCTAssertTrue(page.genres?.rows.allSatisfy { $0.key != nil && $0.values == nil } ?? false, "\(name): Global groups")
        } else {
            XCTAssertTrue(page.genres?.rows.allSatisfy { $0.key == nil && !($0.values ?? []).isEmpty } ?? false, "\(name): type genres")
        }

        // Languages
        if isNull("languages") {
            XCTAssertNil(page.languages, name)
        } else {
            let languages = json["languages"] as? JSON
            XCTAssertEqual(page.languages?.rows.count, rows(languages, "rows").count, "\(name): languages")
            XCTAssertEqual(page.languages?.mediaTypes, languages?["media_types"] as? [String], name)
        }

        // People
        if isNull("people") {
            XCTAssertNil(page.people, name)
        } else {
            let groups = rows(json["people"], "groups")
            XCTAssertEqual(page.people?.groups.count, groups.count, "\(name): people groups")
            XCTAssertEqual(page.people?.groups.map(\.rows.count), groups.map { rows($0, "rows").count }, "\(name): people rows")
        }

        // List rings
        XCTAssertEqual(page.listRings.count, (json["list_rings"] as? [Any])?.count, "\(name): list rings")

        // Collections
        if isNull("collections") {
            XCTAssertNil(page.collections, name)
        } else {
            let collections = json["collections"] as? JSON
            XCTAssertEqual(page.collections?.completeCount, collections?["complete_count"] as? Int, name)
            XCTAssertEqual(page.collections?.almostCount, collections?["almost_count"] as? Int, name)
            XCTAssertEqual(page.collections?.complete.count, rows(collections, "complete").count, "\(name): complete")
            XCTAssertEqual(page.collections?.almost.count, rows(collections, "almost").count, "\(name): almost")
        }

        // Most logged and milestones
        let mostLogged = json["most_logged"] as? JSON
        XCTAssertEqual(page.mostLogged?.items.count, rows(mostLogged, "items").count, "\(name): most logged")
        XCTAssertEqual(page.mostLogged?.label, mostLogged?["label"] as? String, name)
        XCTAssertEqual(page.mostLogged?.total, mostLogged?["total"] as? Int, name)
        XCTAssertEqual(page.milestones.count, (json["milestones"] as? [Any])?.count, "\(name): milestones")
        XCTAssertTrue(page.milestones.allSatisfy { $0.entryId != nil }, name)
    }

    /// The sections the payload says the page should show, worked out from the raw JSON alone:
    /// the tab's order (STAT-218), minus null and empty sections (STAT-302), Highest rated only
    /// when Top comes from a list (STAT-205), Collections only on a type tab.
    private static func expectedSections(_ json: JSON) -> [StatsPageSection] {
        func section(_ key: String) -> JSON? { json[key] as? JSON }
        func count(_ object: JSON?, _ key: String) -> Int { (object?[key] as? [Any])?.count ?? 0 }
        func number(_ object: JSON?, _ key: String) -> Int { object?[key] as? Int ?? 0 }

        let isGlobal = json["media_type"] is NSNull
        let top = section("top")
        let shown: [StatsPageSection: Bool] = [
            .header: number(section("header"), "titles") + number(section("header"), "logs") > 0,
            .time: count(section("time"), "rows") > 0,
            // A tagged list's posters, or the owner's tag hint without one.
            .top: top?["source"] as? String == "list"
                ? count(top, "items") > 0
                : !((top?["tag_hint"] as? String) ?? "").trimmingCharacters(in: .whitespaces).isEmpty,
            .highestRated: count(section("highest_rated"), "items") > 0,
            .activity: section("activity")?["busiest_day"] is JSON,
            .ratings: number(section("ratings"), "rated") > 0,
            .ratedVsEveryone: count(section("rated_vs_everyone"), "higher") + count(section("rated_vs_everyone"), "lower") > 0,
            .releaseYears: count(section("release_years"), "rows") > 0,
            .genres: count(section("genres"), "rows") + count(section("genres"), "music") > 0,
            .languages: count(section("languages"), "rows") > 0,
            .people: ((section("people")?["groups"] as? [JSON]) ?? []).contains { count($0, "rows") > 0 },
            .listProgress: !((json["list_rings"] as? [Any]) ?? []).isEmpty,
            .collections: !isGlobal && collectionsShow(section("collections")),
            .mostLogged: count(section("most_logged"), "items") > 0,
            .milestones: !((json["milestones"] as? [Any]) ?? []).isEmpty,
        ]
        let order = isGlobal ? StatsPageSection.globalOrder : StatsPageSection.typeOrder
        return order.filter { shown[$0] ?? false }
    }

    /// A year's own progress when the payload has it (`in_range`), else the lifetime groups.
    private static func collectionsShow(_ collections: JSON?) -> Bool {
        func number(_ object: JSON?, _ key: String) -> Int { object?[key] as? Int ?? 0 }
        if let inRange = collections?["in_range"] as? JSON {
            return number(inRange, "completed_count") + number(inRange, "progressed_count") > 0
        }
        return number(collections, "complete_count") + number(collections, "almost_count") > 0
    }

    // MARK: - Fixtures

    private static var fixturesDirectory: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("Fixtures/StatsV2", isDirectory: true)
    }

    /// Fixture names without the "stats-v2-" prefix and ".json" suffix, sorted.
    private static func fixtureNames() throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: fixturesDirectory.path)
            .filter { $0.hasPrefix("stats-v2-") && $0.hasSuffix(".json") }
            .map { String($0.dropFirst("stats-v2-".count).dropLast(".json".count)) }
            .sorted()
    }

    private static func data(_ name: String) throws -> Data {
        try Data(contentsOf: fixturesDirectory.appendingPathComponent("stats-v2-\(name).json"))
    }

    private static func load(_ name: String) throws -> (page: StatsPage, json: JSON) {
        let data = try data(name)
        let page = try JSONDecoder.api.decode(StatsPage.self, from: data)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? JSON, name)
        return (page, json)
    }

    private static func detail(_ name: String, _ key: String) throws -> StatsHeaderDetail? {
        try load(name).page.header?.details.first { $0.key == key }
    }
}
