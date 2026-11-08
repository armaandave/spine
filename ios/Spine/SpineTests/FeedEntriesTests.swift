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

    private func make(_ items: [ActivityItem], zone: String = "UTC") -> [FeedCardModel] {
        FeedEntries.make(items: items, now: now, calendar: calendar(zone))
    }

    /// Each card's day, in feed order.
    private func days(_ cards: [FeedCardModel]) -> [String?] {
        cards.map(\.dayLabel)
    }

    private func ids(_ cards: [FeedCardModel]) -> [Int] {
        cards.map(\.id)
    }

    // MARK: Day labels

    func testTodayYesterdayWeekdayAndDatedLabels() throws {
        let cards = make([
            try log(1, on: "2026-11-07"),
            try log(2, on: "2026-11-06"),
            try log(3, on: "2026-11-05"),
            try log(4, on: "2026-11-03"),
            try log(5, on: "2026-11-01"),
            try log(6, on: "2026-10-31"),
            try log(7, on: "2025-12-25"),
        ])
        XCTAssertEqual(days(cards), ["Today", "Yesterday", "Thursday", "Tuesday", "Sunday", "Oct 31", "Dec 25, 2025"])
    }

    func testTheSameInstantIsTwoDaysInTwoTimeZones() throws {
        let items = [try log(1, on: "2026-11-06")]
        XCTAssertEqual(days(make(items, zone: utc)), ["Yesterday"])
        XCTAssertEqual(days(make(items, zone: newYork)), ["Today"])
        let later = [try log(2, on: "2026-11-07")]
        XCTAssertEqual(days(make(later, zone: utc)), ["Today"])
        // 2026-11-07 is still tomorrow in New York at this instant.
        XCTAssertEqual(days(make(later, zone: newYork)), ["Nov 7"])
        let weekday = [try log(3, on: "2026-11-03")]
        XCTAssertEqual(days(make(weekday, zone: utc)), ["Tuesday"])
        XCTAssertEqual(days(make(weekday, zone: newYork)), ["Tuesday"])
    }

    func testAWeekAgoIsADate() throws {
        XCTAssertEqual(days(make([try log(1, on: "2026-10-31")], zone: utc)), ["Oct 31"])
        XCTAssertEqual(days(make([try log(1, on: "2026-11-01")], zone: utc)), ["Sunday"])
        XCTAssertEqual(days(make([try log(1, on: "2026-10-31")], zone: newYork)), ["Saturday"])
    }

    func testThisYearHasNoYearAndEarlierYearsDo() throws {
        let cards = make([try log(1, on: "2026-01-02"), try log(2, on: "2025-01-02"), try log(3, on: "2024-02-29")])
        XCTAssertEqual(days(cards), ["Jan 2", "Jan 2, 2025", "Feb 29, 2024"])
    }

    func testEveryCardCarriesItsOwnDayNoMatterWhatNeighboursSay() throws {
        let cards = make([try log(1, on: "2026-11-07"), try log(2, on: "2026-11-07"), try log(3, on: "2026-11-06")])
        XCTAssertEqual(days(cards), ["Today", "Today", "Yesterday"])
        XCTAssertEqual(ids(cards), [1, 2, 3], "One entry per item, no header rows")
    }

    func testTheDayIsInTheCardsVoiceOverLabelAfterTheUsername() throws {
        let card = try XCTUnwrap(make([try log(1, on: "2026-11-06")]).first)
        let words = card.accessibilityLabel.components(separatedBy: ", ")
        XCTAssertEqual(words.prefix(2).map { $0 }, [card.username, "Yesterday"])
    }

    // MARK: Which day a card belongs to

    func testDaysComeFromConsumedAtNotFromWhenItWasPosted() throws {
        // Posted today, dated three days ago (a Phase 5 dated save).
        let cards = make([try log(1, on: "2026-11-02", created: "2026-11-07T01:00:00Z")])
        XCTAssertEqual(days(cards), ["Monday"])
    }

    func testWithoutAConsumedDateTheDayItWasPostedOnIsUsedOnThePhonesCalendar() throws {
        let item = try log(1, on: nil, created: "2026-11-06T02:30:00Z")
        XCTAssertEqual(FeedEntries.dayKey(for: item, calendar: calendar(utc)), "2026-11-06")
        XCTAssertEqual(FeedEntries.dayKey(for: item, calendar: calendar(newYork)), "2026-11-05")
        XCTAssertEqual(days(make([item], zone: utc)), ["Yesterday"])
        // Posted Nov 5 at 21:30 in New York, where it is Nov 6 at 22:00 now.
        XCTAssertEqual(days(make([item], zone: newYork)), ["Yesterday"])
        let fractional = try log(2, on: nil, created: "2026-11-06T02:30:00.123456Z")
        XCTAssertEqual(FeedEntries.dayKey(for: fractional, calendar: calendar(utc)), "2026-11-06")
    }

    func testAnImpossibleDateFallsBackAndNoDateGivesNoDay() throws {
        let impossible = try log(1, on: "2026-02-30", created: "2026-11-06T12:00:00Z")
        XCTAssertEqual(days(make([impossible])), ["Yesterday"])
        let none = try log(2, on: nil, created: nil)
        XCTAssertEqual(days(make([none])), [nil])
        let garbage = try log(3, on: "soon", created: "later")
        XCTAssertEqual(days(make([garbage])), [nil])
    }

    func testADateTimeInConsumedAtUsesItsDatePart() throws {
        let cards = make([try log(1, on: "2026-11-07T23:30:00Z")])
        XCTAssertEqual(days(cards), ["Today"])
    }

    func testDayTitleOfAKeyThatIsNotADayIsTheKey() {
        let calendar = calendar(utc)
        XCTAssertEqual(FeedEntries.dayTitle(forKey: "2026-11-07", now: now, calendar: calendar), "Today")
        XCTAssertEqual(FeedEntries.dayTitle(forKey: "2026-11-02", now: now, calendar: calendar), "Monday")
        XCTAssertEqual(FeedEntries.dayTitle(forKey: "not-a-day", now: now, calendar: calendar), "not-a-day")
    }

    // MARK: Ids and order

    func testCardsKeepTheFeedsOrderAndIdsAreUnique() throws {
        let cards = make([
            try log(10, on: "2026-11-07"),
            try log(20, on: "2026-11-06"),
            try log(30, on: "2026-11-07"),
            try FeedTestItems.day(id: 2, label: "3 episodes", mediaType: "tv"),
            try FeedTestItems.planningRow(id: -17_592_186_044_420),
        ])
        XCTAssertEqual(ids(cards), [10, 20, 30, 2, -17_592_186_044_420])
        XCTAssertEqual(Set(ids(cards)).count, cards.count)
        // A later build with a new card in front leaves every other card equal, so its row skips drawing.
        let again = make([try log(40, on: "2026-11-07"), try log(10, on: "2026-11-07")])
        XCTAssertEqual(again[1], cards[0])
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

        let cards = entries
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
        guard let model = entries.last else { return XCTFail("No card") }
        XCTAssertEqual(model.art.planningPosterURLs, ["https://p/a.jpg", "https://p/b.jpg"])
        XCTAssertEqual(model.details, [.quiet(text: "A and B", lines: 2)])

        let unloaded = FeedEntries.make(items: [row], now: now, calendar: calendar(utc))
        guard let face = unloaded.last else { return XCTFail("No card") }
        XCTAssertEqual(face.art.planningPosterURLs, [])
    }

    func testAnEmptyFeedHasNoEntries() {
        XCTAssertEqual(make([]), [])
    }
}
