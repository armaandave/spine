import XCTest
@testable import Spine

@MainActor
final class FeedEntriesTests: XCTestCase {
    private func calendar(_ zone: String) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: zone)!
        calendar.locale = Locale(identifier: "en_US")
        return calendar
    }

    private let newYork = "America/New_York"
    private let utc = "UTC"

    /// Saturday 2026-11-07 03:00 UTC, which is still Friday 2026-11-06 at 22:00 in New York.
    private var now: Date {
        ISO8601DateFormatter().date(from: "2026-11-07T03:00:00Z")!
    }

    private func log(_ id: Int, on day: String?, created: String? = "2026-11-07T16:00:00Z") throws -> ActivityItem {
        try FeedTestItems.log(id: id, rating: "4", consumedAt: day, createdAt: created)
    }

    private func make(_ items: [ActivityItem], zone: String = "UTC") -> [FeedListEntry] {
        FeedEntries.make(items: items, now: now, calendar: calendar(zone))
    }

    private func headers(_ entries: [FeedListEntry]) -> [String] {
        entries.compactMap { if case let .day(_, title) = $0 { title } else { nil } }
    }

    private func ids(_ entries: [FeedListEntry]) -> [String] {
        entries.map(\.id)
    }

    // MARK: Header titles

    func testTodayYesterdayWeekdayAndDatedHeaders() throws {
        let entries = make([
            try log(1, on: "2026-11-07"),
            try log(2, on: "2026-11-06"),
            try log(3, on: "2026-11-03"),
            try log(4, on: "2026-11-01"),
            try log(5, on: "2026-10-31"),
            try log(6, on: "2025-12-25"),
        ])
        XCTAssertEqual(headers(entries), ["Today", "Yesterday", "Tuesday", "Sunday", "Oct 31", "Dec 25, 2025"])
    }

    func testTheSameInstantIsTwoDaysInTwoTimeZones() throws {
        let items = [try log(1, on: "2026-11-06")]
        XCTAssertEqual(headers(make(items, zone: utc)), ["Yesterday"])
        XCTAssertEqual(headers(make(items, zone: newYork)), ["Today"])
        let later = [try log(2, on: "2026-11-07")]
        XCTAssertEqual(headers(make(later, zone: utc)), ["Today"])
        // 2026-11-07 is still tomorrow in New York at this instant.
        XCTAssertEqual(headers(make(later, zone: newYork)), ["Nov 7"])
        let weekday = [try log(3, on: "2026-11-03")]
        XCTAssertEqual(headers(make(weekday, zone: utc)), ["Tuesday"])
        XCTAssertEqual(headers(make(weekday, zone: newYork)), ["Tuesday"])
    }

    func testAWeekAgoIsADate() throws {
        XCTAssertEqual(headers(make([try log(1, on: "2026-10-31")], zone: utc)), ["Oct 31"])
        XCTAssertEqual(headers(make([try log(1, on: "2026-11-01")], zone: utc)), ["Sunday"])
        XCTAssertEqual(headers(make([try log(1, on: "2026-10-31")], zone: newYork)), ["Saturday"])
    }

    // MARK: Which day a card belongs to

    func testDaysComeFromConsumedAtNotFromWhenItWasPosted() throws {
        // Posted today, dated three days ago (a Phase 5 dated save).
        let entries = make([try log(1, on: "2026-11-02", created: "2026-11-07T01:00:00Z")])
        XCTAssertEqual(ids(entries), ["day-2026-11-02", "card-1"])
        XCTAssertEqual(headers(entries), ["Monday"])
    }

    func testWithoutAConsumedDateTheDayItWasPostedOnIsUsedOnThePhonesCalendar() throws {
        let item = try log(1, on: nil, created: "2026-11-06T02:30:00Z")
        XCTAssertEqual(ids(make([item], zone: utc)), ["day-2026-11-06", "card-1"])
        XCTAssertEqual(ids(make([item], zone: newYork)), ["day-2026-11-05", "card-1"])
        let fractional = try log(2, on: nil, created: "2026-11-06T02:30:00.123456Z")
        XCTAssertEqual(ids(make([fractional], zone: utc)), ["day-2026-11-06", "card-2"])
    }

    func testAnImpossibleDateFallsBackAndNoDateGivesNoHeader() throws {
        let impossible = try log(1, on: "2026-02-30", created: "2026-11-06T12:00:00Z")
        XCTAssertEqual(ids(make([impossible])), ["day-2026-11-06", "card-1"])
        let none = try log(2, on: nil, created: nil)
        XCTAssertEqual(ids(make([none])), ["card-2"])
        let garbage = try log(3, on: "soon", created: "later")
        XCTAssertEqual(ids(make([garbage])), ["card-3"])
        // An undated card joins the run above it.
        let mixed = make([try log(4, on: "2026-11-07"), none, try log(5, on: "2026-11-06")])
        XCTAssertEqual(ids(mixed), ["day-2026-11-07", "card-4", "card-2", "day-2026-11-06", "card-5"])
    }

    func testADateTimeInConsumedAtUsesItsDatePart() throws {
        let entries = make([try log(1, on: "2026-11-07T23:30:00Z")])
        XCTAssertEqual(ids(entries), ["day-2026-11-07", "card-1"])
    }

    func testCardsOfOneDayShareOneHeader() throws {
        let entries = make([try log(1, on: "2026-11-07"), try log(2, on: "2026-11-07"), try log(3, on: "2026-11-06")])
        XCTAssertEqual(ids(entries), ["day-2026-11-07", "card-1", "card-2", "day-2026-11-06", "card-3"])
    }

    // MARK: Stable ids

    func testRepeatedDayAfterADatedInsertKeepsItsFirstHeaderId() throws {
        let a = try log(10, on: "2026-11-07")
        let b = try log(20, on: "2026-11-06")
        let c = try log(30, on: "2026-11-07")
        let entries = make([a, b, c])
        XCTAssertEqual(ids(entries), ["day-2026-11-07", "card-10", "day-2026-11-06", "card-20", "day-2026-11-07-30", "card-30"])
        XCTAssertEqual(headers(entries), ["Today", "Yesterday", "Today"])
    }

    func testIdsAreStableAcrossTwoBuildsAPrependAndADatedMidListInsert() throws {
        let a = try log(10, on: "2026-11-07")
        let b = try log(20, on: "2026-11-06")
        let c = try log(30, on: "2026-11-07")
        let before = ids(make([a, b, c]))
        XCTAssertEqual(before, ids(make([a, b, c])), "Two builds of the same items")

        // A new card on today's first run joins the same header.
        let d = try log(40, on: "2026-11-07")
        let prepended = ids(make([d, a, b, c]))
        XCTAssertEqual(prepended, ["day-2026-11-07", "card-40", "card-10", "day-2026-11-06", "card-20", "day-2026-11-07-30", "card-30"])
        XCTAssertTrue(Set(before).isSubset(of: Set(prepended)), "Every earlier id is still there")

        // A dated row landing in the middle, on a day not seen yet, adds only its own header.
        let e = try log(50, on: "2026-11-05")
        let inserted = ids(make([a, b, e, c]))
        XCTAssertEqual(inserted, ["day-2026-11-07", "card-10", "day-2026-11-06", "card-20", "day-2026-11-05", "card-50", "day-2026-11-07-30", "card-30"])
        XCTAssertTrue(Set(before).isSubset(of: Set(inserted)))

        for list in [before, prepended, inserted] {
            XCTAssertEqual(Set(list).count, list.count, "No id repeats: \(list)")
        }
    }

    func testCardIdsAreUniqueAcrossKinds() throws {
        let entries = make([
            try log(1, on: "2026-11-07"),
            try FeedTestItems.day(id: 2, label: "3 episodes", mediaType: "tv"),
            try FeedTestItems.planningRow(id: -17_592_186_044_420),
        ])
        let all = ids(entries)
        XCTAssertEqual(Set(all).count, all.count)
        XCTAssertTrue(all.contains("card--17592186044420"))
    }

    // MARK: Newest of a title and standings

    @MainActor
    private final class RecordingStanding: FeedStandingProviding {
        var calls: [(id: Int, newest: Bool)] = []
        var prepared: [Int] = []

        func standing(for item: ActivityItem, isNewestOfTitle: Bool) -> FeedStanding? {
            calls.append((item.id, isNewestOfTitle))
            return FeedStanding(unit: .pages, from: 10, to: 40, total: 400, whereText: "p. 40")
        }

        func prepare(for items: [ActivityItem]) {
            prepared = items.map(\.id)
        }
    }

    func testOnlyTheFirstProgressOrStatusCardOfAPersonsTitleIsNewest() throws {
        let provider = RecordingStanding()
        let items = [
            try FeedTestItems.day(id: 1, label: "30 pages", mediaID: "7"),
            try FeedTestItems.log(id: 2, rating: "3", mediaType: "book", source: "manual"),
            try FeedTestItems.day(id: 3, label: "20 pages", mediaID: "7"),
            try FeedTestItems.day(id: 4, label: "20 pages", mediaID: "7", username: "perf-ben", actorID: 4),
            try FeedTestItems.day(id: 5, moves: ["paused"], mediaID: "8"),
            try FeedTestItems.day(id: 6, label: "20 pages", mediaID: "8"),
        ]
        let entries = FeedEntries.make(items: items, standing: provider, now: now, calendar: calendar(utc))
        XCTAssertEqual(provider.calls.map(\.id), [1, 3, 4, 5, 6], "Logs are never asked")
        XCTAssertEqual(provider.calls.map(\.newest), [true, false, true, true, false])

        let cards = entries.compactMap { entry -> FeedCardModel? in if case let .card(model) = entry { model } else { nil } }
        XCTAssertNotNil(cards.first { $0.id == 1 }?.meter)
        XCTAssertNil(cards.first { $0.id == 3 }?.meter, "An older card of the same title has no meter")
        XCTAssertNotNil(cards.first { $0.id == 4 }?.meter, "Another person's card is their own title")
        XCTAssertNotNil(cards.first { $0.id == 5 }?.meter)
        XCTAssertEqual(cards.first { $0.id == 5 }?.meter?.from, cards.first { $0.id == 5 }?.meter?.to)
        XCTAssertNil(cards.first { $0.id == 2 }?.meter)
    }

    func testAShowsEpisodesAndSeasonsAreTheShow() throws {
        let provider = RecordingStanding()
        let items = [
            try FeedTestItems.day(id: 1, label: "2 episodes", mediaType: "tv", source: "tmdb", mediaID: "5"),
            try FeedTestItems.day(id: 2, label: "1 episode", mediaType: "episode", source: "tmdb", mediaID: "5", season: 2, episode: 3),
            try FeedTestItems.day(id: 3, label: "1 episode", mediaType: "season", source: "tmdb", mediaID: "5", season: 2),
            try FeedTestItems.day(id: 4, label: "1 episode", mediaType: "tv", source: "tmdb", mediaID: "6"),
        ]
        _ = FeedEntries.make(items: items, standing: provider, now: now, calendar: calendar(utc))
        XCTAssertEqual(provider.calls.map(\.newest), [true, false, false, true])

        func key(_ type: String, _ source: String = "tmdb", season: Int? = nil, episode: Int? = nil) -> String {
            FeedEntries.titleKey(for: MediaRef(
                itemId: nil, source: source, mediaType: type, mediaId: "5", seasonNumber: season, episodeNumber: episode
            ))
        }
        XCTAssertEqual(key("tv"), key("season", season: 1))
        XCTAssertEqual(key("tv"), key("episode", season: 1, episode: 2))
        XCTAssertNotEqual(key("tv"), key("movie"))
        XCTAssertEqual(key("anime", "mal"), key("episode", "mal", season: 1, episode: 4))
    }

    func testPlanningRowsReceiveTheirLoadedDay() throws {
        let row = try FeedTestItems.planningRow(count: 2)
        let day = try FeedTestItems.planningDay(titles: [("A", "https://p/a.jpg"), ("B", "https://p/b.jpg")])
        let entries = FeedEntries.make(
            items: [row], planning: [row.object.id: day, 12_345: try FeedTestItems.planningDay(titles: [("Other", nil)])],
            now: now, calendar: calendar(utc)
        )
        guard case let .card(model)? = entries.last else { return XCTFail("No card") }
        XCTAssertEqual(model.art.planningPosterURLs, ["https://p/a.jpg", "https://p/b.jpg"])
        XCTAssertEqual(model.details, [.quiet(text: "A and B", lines: 2)])

        let unloaded = FeedEntries.make(items: [row], now: now, calendar: calendar(utc))
        guard case let .card(face)? = unloaded.last else { return XCTFail("No card") }
        XCTAssertEqual(face.art.planningPosterURLs, [])
    }

    func testAnEmptyFeedHasNoEntries() {
        XCTAssertEqual(make([]), [])
    }
}
