import XCTest
@testable import Spine

final class ToldAmountTests: XCTestCase {
    private func parse(_ label: String?, unit: String? = nil, amount: Double? = nil, percent: Double? = nil) -> TodayPart? {
        ToldAmount.parse(label: label, unit: unit, amount: amount, percent: percent)
    }

    func testCountedUnitsReadTheNumberFromTheLabel() {
        XCTAssertEqual(parse("3 episodes", unit: "minutes", amount: 132), TodayPart(unit: .episodes, amount: 3))
        XCTAssertEqual(parse("1 episode", unit: "minutes", amount: 44), TodayPart(unit: .episodes, amount: 1))
        XCTAssertEqual(parse("86 pages", unit: "pages", amount: 86), TodayPart(unit: .pages, amount: 86))
        XCTAssertEqual(parse("1 page", unit: "page", amount: 1), TodayPart(unit: .pages, amount: 1))
        XCTAssertEqual(parse("4 chapters", unit: "chapters", amount: 4), TodayPart(unit: .chapters, amount: 4))
        XCTAssertEqual(parse("2 issues", unit: "issue", amount: 2), TodayPart(unit: .issues, amount: 2))
    }

    func testEpisodesNeverUseTheMinutesAmount() {
        XCTAssertEqual(parse("3 episodes", unit: "minutes", amount: 132)?.amount, 3)
        XCTAssertEqual(parse("3 episodes", unit: nil, amount: 999)?.amount, 3)
    }

    func testThousandsSeparators() {
        XCTAssertEqual(parse("1,089 episodes"), TodayPart(unit: .episodes, amount: 1089))
        XCTAssertEqual(parse("12,345 pages"), TodayPart(unit: .pages, amount: 12345))
    }

    func testPercentFromTheLabelOrTheField() {
        XCTAssertEqual(parse("15%"), TodayPart(unit: .percent, amount: 15))
        XCTAssertEqual(parse("12.5%"), TodayPart(unit: .percent, amount: 12.5))
        XCTAssertEqual(parse(nil, percent: 40), TodayPart(unit: .percent, amount: 40))
        XCTAssertEqual(parse("", percent: 40), TodayPart(unit: .percent, amount: 40))
        XCTAssertEqual(parse("15%", unit: "minutes", amount: 90, percent: 15), TodayPart(unit: .percent, amount: 15))
    }

    func testMinutesFromTheLabelOrThePreferredAmount() {
        XCTAssertEqual(parse("2h 30m"), TodayPart(unit: .minutes, amount: 150))
        XCTAssertEqual(parse("2h"), TodayPart(unit: .minutes, amount: 120))
        XCTAssertEqual(parse("45m"), TodayPart(unit: .minutes, amount: 45))
        XCTAssertEqual(parse("1 hour 5 minutes"), TodayPart(unit: .minutes, amount: 65))
        // The unit says minutes: the number is trusted over the words.
        XCTAssertEqual(parse("2h 30m", unit: "minutes", amount: 151), TodayPart(unit: .minutes, amount: 151))
        XCTAssertEqual(parse("2h 30m", unit: "minute", amount: 151), TodayPart(unit: .minutes, amount: 151))
        // Not minutes: the number is some other unit's, so the words win.
        XCTAssertEqual(parse("2h 30m", unit: "pages", amount: 9), TodayPart(unit: .minutes, amount: 150))
    }

    func testAPlusSignAndCaseAreTolerated() {
        XCTAssertEqual(parse("+3 Episodes"), TodayPart(unit: .episodes, amount: 3))
        XCTAssertEqual(parse("  86 PAGES "), TodayPart(unit: .pages, amount: 86))
    }

    func testABareNumberTakesTheRowsUnit() {
        XCTAssertEqual(parse("40", unit: "pages"), TodayPart(unit: .pages, amount: 40))
        XCTAssertEqual(parse("3", unit: "chapter"), TodayPart(unit: .chapters, amount: 3))
        XCTAssertNil(parse("40", unit: "minutes"))
        XCTAssertNil(parse("40"))
    }

    func testStartedNilAndUnknownShapesGiveNil() {
        XCTAssertNil(parse("Started"))
        XCTAssertNil(parse("started", unit: "minutes", amount: 30, percent: 5))
        XCTAssertNil(parse(nil))
        XCTAssertNil(parse(nil, unit: "minutes", amount: 30))
        XCTAssertNil(parse(""))
        XCTAssertNil(parse("   "))
        XCTAssertNil(parse("a few songs"))
        XCTAssertNil(parse("3 songs"))
        XCTAssertNil(parse("3 episodes and a movie"))
        XCTAssertNil(parse("1.2.3 pages"))
        XCTAssertNil(parse("%"))
        XCTAssertNil(parse(","))
        XCTAssertNil(parse("0 pages"))
        XCTAssertNil(parse("0%"))
    }
}

@MainActor
final class HomeTodayStoreTests: XCTestCase {
    // MARK: Fixtures

    private func row(
        id: Int,
        type: String = "progress_updated",
        objectType: String = "progress_day",
        mediaType: String = "book",
        mediaId: String = "1",
        season: Int? = nil,
        episode: Int? = nil,
        label: String? = "40 pages",
        unit: String? = "pages",
        withMedia: Bool = true
    ) throws -> ActivityItem {
        func quoted(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        let media = withMedia
            ? """
            {"ref": {"source": "manual", "media_type": "\(mediaType)", "media_id": "\(mediaId)",
                     "season_number": \(season.map(String.init) ?? "null"),
                     "episode_number": \(episode.map(String.init) ?? "null")}, "title": "Title"}
            """
            : "null"
        let json = """
        {
          "id": \(id), "type": "\(type)", "created_at": "2026-11-07T16:00:00Z", "consumed_at": "2026-11-07",
          "actor": {"id": 3, "username": "claude", "display_name": "Claude", "avatar_url": null},
          "media": \(media), "person": null,
          "object": {"type": "\(objectType)", "id": \(abs(id)), "label": \(quoted(label)), "unit": \(quoted(unit))},
          "viewer": {"can_view": true, "has_liked": false}, "like_count": 0, "comment_count": 0
        }
        """
        return try JSONDecoder.api.decode(ActivityItem.self, from: Data(json.utf8))
    }

    private func response(_ rows: [ActivityItem]) -> ActivityCursorResponse {
        ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: rows)
    }

    private func ref(_ type: String, _ id: String = "1", season: Int? = nil, episode: Int? = nil) -> MediaRef {
        MediaRef(itemId: nil, source: "manual", mediaType: type, mediaId: id, seasonNumber: season, episodeNumber: episode)
    }

    private func calendar(_ zone: String) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: zone)!
        return calendar
    }

    private func date(_ calendar: Calendar, day: Int, hour: Int, minute: Int) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
    }

    private func makeStore(_ repository: ActivityRepository, unauthorized: @escaping () -> Void = {}) -> HomeTodayStore {
        HomeTodayStore(activityRepository: repository, onUnauthorized: unauthorized)
    }

    // MARK: The request

    func testTheRequestAsksForTodayOnThePhonesCalendarUnderTwoTimeZones() async throws {
        let newYork = calendar("America/New_York")
        let utc = calendar("UTC")
        let cases: [(Calendar, Date, String)] = [
            (newYork, date(newYork, day: 6, hour: 23, minute: 30), "2026-11-06"),
            (newYork, date(newYork, day: 7, hour: 0, minute: 30), "2026-11-07"),
            (utc, date(utc, day: 6, hour: 23, minute: 30), "2026-11-06"),
            (utc, date(utc, day: 7, hour: 0, minute: 30), "2026-11-07"),
        ]
        for (calendar, now, expected) in cases {
            let repository = ScriptedTodayRepository(answers: [.success(response([]))])
            await makeStore(repository).refresh(username: "claude", now: now, calendar: calendar)

            XCTAssertEqual(repository.requests.count, 1)
            let request = try XCTUnwrap(repository.requests.first)
            XCTAssertEqual(request.username, "claude")
            XCTAssertEqual(request.filter.startDate, expected)
            XCTAssertEqual(request.filter.endDate, expected)
            XCTAssertNil(request.filter.mediaType)
            XCTAssertNil(request.filter.counts)
            XCTAssertEqual(request.pageSize, 50)
            XCTAssertNil(request.cursorLink)
            // Planning is not asked for: a plain list is the one that would carry it.
            XCTAssertFalse(request.filter.asksForPlanning)
            XCTAssertFalse(request.filter.queryItems.contains { $0.name == "include" })
        }
    }

    func testTheSameInstantIsTwoDaysInTwoTimeZones() async throws {
        // 04:30 UTC on the 7th is 23:30 on the 6th in New York.
        let utc = calendar("UTC")
        let instant = date(utc, day: 7, hour: 4, minute: 30)
        let newYorkRepository = ScriptedTodayRepository(answers: [.success(response([]))])
        await makeStore(newYorkRepository).refresh(username: "claude", now: instant, calendar: calendar("America/New_York"))
        let utcRepository = ScriptedTodayRepository(answers: [.success(response([]))])
        await makeStore(utcRepository).refresh(username: "claude", now: instant, calendar: utc)

        XCTAssertEqual(newYorkRepository.requests.first?.filter.startDate, "2026-11-06")
        XCTAssertEqual(utcRepository.requests.first?.filter.startDate, "2026-11-07")
    }

    // MARK: What counts

    func testTwoRowsOfOneTitleSum() async throws {
        let repository = ScriptedTodayRepository(answers: [.success(response([
            try row(id: -1, label: "40 pages"),
            try row(id: -2, label: "46 pages"),
        ]))])
        let store = makeStore(repository)
        await store.refresh(username: "claude")

        XCTAssertEqual(store.part(for: ref("book")), TodayPart(unit: .pages, amount: 86))
    }

    func testLogsOtherTitlesAndRowsWithNoMediaDoNotCount() async throws {
        let repository = ScriptedTodayRepository(answers: [.success(response([
            try row(id: -1, label: "40 pages"),
            try row(id: 5, type: "diary_created", objectType: "diary", label: "100 pages"),
            try row(id: -3, mediaId: "2", label: "7 pages"),
            try row(id: -4, label: "9 pages", withMedia: false),
            try row(id: -5, label: "Started"),
            try row(id: -6, label: nil),
        ]))])
        let store = makeStore(repository)
        await store.refresh(username: "claude")

        XCTAssertEqual(store.part(for: ref("book")), TodayPart(unit: .pages, amount: 40))
        XCTAssertEqual(store.part(for: ref("book", "2")), TodayPart(unit: .pages, amount: 7))
        XCTAssertNil(store.part(for: ref("book", "3")))
        XCTAssertEqual(store.parts.count, 2)
    }

    func testASeasonAndEpisodeFoldIntoTheirShow() async throws {
        let repository = ScriptedTodayRepository(answers: [.success(response([
            try row(id: -1, mediaType: "episode", mediaId: "9", season: 2, episode: 7, label: "1 episode", unit: "minutes"),
            try row(id: -2, mediaType: "tv", mediaId: "9", label: "2 episodes", unit: "minutes"),
            try row(id: -3, mediaType: "season", mediaId: "9", season: 2, label: "3 episodes", unit: "minutes"),
        ]))])
        let store = makeStore(repository)
        await store.refresh(username: "claude")

        XCTAssertEqual(store.part(for: ref("tv", "9")), TodayPart(unit: .episodes, amount: 6))
        XCTAssertEqual(store.part(for: ref("season", "9", season: 2)), TodayPart(unit: .episodes, amount: 6))
        XCTAssertEqual(store.part(for: ref("episode", "9", season: 2, episode: 7)), TodayPart(unit: .episodes, amount: 6))
        XCTAssertEqual(store.parts.count, 1)
    }

    func testAnimeStaysItsOwnTitleAndTheSameIdInAnotherTypeIsAnotherTitle() async throws {
        let repository = ScriptedTodayRepository(answers: [.success(response([
            try row(id: -1, mediaType: "anime", mediaId: "9", label: "2 episodes", unit: "minutes"),
            try row(id: -2, mediaType: "tv", mediaId: "9", label: "1 episode", unit: "minutes"),
        ]))])
        let store = makeStore(repository)
        await store.refresh(username: "claude")

        XCTAssertEqual(store.part(for: ref("anime", "9")), TodayPart(unit: .episodes, amount: 2))
        XCTAssertEqual(store.part(for: ref("tv", "9")), TodayPart(unit: .episodes, amount: 1))
    }

    func testARowInAnotherUnitIsLeftOutOfTheSum() async throws {
        let repository = ScriptedTodayRepository(answers: [.success(response([
            try row(id: -1, mediaType: "game", label: "30m", unit: "minutes"),
            try row(id: -2, mediaType: "game", label: "10%", unit: "minutes"),
            try row(id: -3, mediaType: "game", label: "1h", unit: "minutes"),
        ]))])
        let store = makeStore(repository)
        await store.refresh(username: "claude")

        XCTAssertEqual(store.part(for: ref("game")), TodayPart(unit: .minutes, amount: 90))
    }

    // MARK: Failure, staleness and the interval

    func testAFailedRefreshKeepsTheLastPartsAndShowsNothing() async throws {
        let repository = ScriptedTodayRepository(answers: [
            .success(response([try row(id: -1)])),
            .failure(URLError(.notConnectedToInternet)),
        ])
        var unauthorized = 0
        let store = makeStore(repository) { unauthorized += 1 }
        let now = Date()
        await store.refresh(username: "claude", now: now)
        await store.refresh(username: "claude", now: now, force: true)

        XCTAssertEqual(repository.requests.count, 2)
        XCTAssertEqual(store.part(for: ref("book")), TodayPart(unit: .pages, amount: 40))
        XCTAssertEqual(unauthorized, 0)
    }

    func testAFailureDoesNotStartTheTwentySecondClock() async throws {
        let repository = ScriptedTodayRepository(answers: [
            .failure(URLError(.timedOut)),
            .success(response([try row(id: -1)])),
        ])
        let store = makeStore(repository)
        let now = Date()
        await store.refresh(username: "claude", now: now)
        await store.refresh(username: "claude", now: now.addingTimeInterval(1))

        XCTAssertEqual(repository.requests.count, 2)
        XCTAssertEqual(store.part(for: ref("book"))?.amount, 40)
    }

    func testUnauthorizedCallsTheCallback() async throws {
        let repository = ScriptedTodayRepository(answers: [.failure(APIError.unauthorized)])
        var unauthorized = 0
        let store = makeStore(repository) { unauthorized += 1 }
        await store.refresh(username: "claude")

        XCTAssertEqual(unauthorized, 1)
        XCTAssertTrue(store.parts.isEmpty)
    }

    func testACallInsideTwentySecondsIsSkippedUnlessForced() async throws {
        let repository = ScriptedTodayRepository(answers: [
            .success(response([try row(id: -1, label: "40 pages")])),
            .success(response([try row(id: -1, label: "50 pages")])),
            .success(response([try row(id: -1, label: "60 pages")])),
            .success(response([try row(id: -1, label: "70 pages")])),
        ])
        let store = makeStore(repository)
        let start = Date()
        await store.refresh(username: "claude", now: start)
        await store.refresh(username: "claude", now: start.addingTimeInterval(19))
        XCTAssertEqual(repository.requests.count, 1, "Inside 20 s nothing is asked")
        XCTAssertEqual(store.part(for: ref("book"))?.amount, 40)

        await store.refresh(username: "claude", now: start.addingTimeInterval(19), force: true)
        XCTAssertEqual(repository.requests.count, 2, "Forced asks anyway")
        XCTAssertEqual(store.part(for: ref("book"))?.amount, 50)

        // The clock restarts at the forced success, not the first one.
        await store.refresh(username: "claude", now: start.addingTimeInterval(38))
        XCTAssertEqual(repository.requests.count, 2)
        await store.refresh(username: "claude", now: start.addingTimeInterval(39))
        XCTAssertEqual(repository.requests.count, 3)
        XCTAssertEqual(store.part(for: ref("book"))?.amount, 60)
    }

    func testAnotherDayOrPersonAsksAgainInsideTwentySeconds() async throws {
        let utc = calendar("UTC")
        let repository = ScriptedTodayRepository(answers: [
            .success(response([try row(id: -1)])),
            .success(response([])),
            .success(response([try row(id: -1, label: "5 pages")])),
        ])
        let store = makeStore(repository)
        let evening = date(utc, day: 7, hour: 23, minute: 59)
        await store.refresh(username: "claude", now: evening, calendar: utc)
        XCTAssertNotNil(store.part(for: ref("book")))

        // After midnight yesterday's parts are not today's, even inside the interval.
        await store.refresh(username: "claude", now: evening.addingTimeInterval(120), calendar: utc)
        XCTAssertEqual(repository.requests.count, 2)
        XCTAssertEqual(repository.requests.last?.filter.startDate, "2026-11-08")
        XCTAssertTrue(store.parts.isEmpty)

        await store.refresh(username: "other", now: evening.addingTimeInterval(121), calendar: utc)
        XCTAssertEqual(repository.requests.count, 3)
        XCTAssertEqual(repository.requests.last?.username, "other")
    }

    func testAStaleAnswerNeverOverwritesANewerOne() async throws {
        let repository = GatedTodayRepository()
        let store = makeStore(repository)
        let now = Date()

        let older = Task { await store.refresh(username: "claude", now: now) }
        await repository.waitForRequests(1)
        let newer = Task { await store.refresh(username: "claude", now: now, force: true) }
        await repository.waitForRequests(2)

        // The newer answer arrives first, then the older one.
        repository.answer(1, with: .success(response([try row(id: -1, label: "50 pages")])))
        await newer.value
        repository.answer(0, with: .success(response([try row(id: -1, label: "10 pages")])))
        await older.value

        XCTAssertEqual(store.part(for: ref("book")), TodayPart(unit: .pages, amount: 50))
    }

    func testOnlyOneRefreshRunsAtATimeUnlessForced() async throws {
        let repository = GatedTodayRepository()
        let store = makeStore(repository)
        let now = Date()

        let first = Task { await store.refresh(username: "claude", now: now) }
        await repository.waitForRequests(1)
        await store.refresh(username: "claude", now: now.addingTimeInterval(30))
        XCTAssertEqual(repository.requestCount, 1, "A second call waits for the one in flight")

        repository.answer(0, with: .success(response([try row(id: -1)])))
        await first.value
        XCTAssertEqual(store.part(for: ref("book"))?.amount, 40)
    }
}

// MARK: - Test doubles

private struct TodayRequest {
    let username: String
    let filter: ActivityListFilter
    let pageSize: Int
    let cursorLink: String?
}

private final class ScriptedTodayRepository: ActivityRepository {
    private var answers: [Result<ActivityCursorResponse, Error>]
    private(set) var requests: [TodayRequest] = []

    init(answers: [Result<ActivityCursorResponse, Error>]) {
        self.answers = answers
    }

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] {
        fatalError("Today uses userActivityList")
    }

    func userActivityList(
        username: String,
        filter: ActivityListFilter,
        pageSize: Int,
        cursorLink: String?
    ) async throws -> ActivityCursorResponse {
        requests.append(TodayRequest(username: username, filter: filter, pageSize: pageSize, cursorLink: cursorLink))
        return try answers.removeFirst().get()
    }

    func feedPage(pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        fatalError("Not the feed")
    }
}

/// Holds every request until the test answers it, so answers can arrive out of order.
private final class GatedTodayRepository: ActivityRepository {
    private var waiting: [CheckedContinuation<ActivityCursorResponse, Error>] = []

    var requestCount: Int { waiting.count }

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] {
        fatalError("Today uses userActivityList")
    }

    func userActivityList(
        username _: String,
        filter _: ActivityListFilter,
        pageSize _: Int,
        cursorLink _: String?
    ) async throws -> ActivityCursorResponse {
        try await withCheckedThrowingContinuation { waiting.append($0) }
    }

    func feedPage(pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        fatalError("Not the feed")
    }

    func waitForRequests(_ count: Int) async {
        while waiting.count < count { await Task.yield() }
    }

    func answer(_ index: Int, with result: Result<ActivityCursorResponse, Error>) {
        waiting[index].resume(with: result)
    }
}
