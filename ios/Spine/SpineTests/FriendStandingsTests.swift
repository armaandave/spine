import XCTest
@testable import Spine

// MARK: Test doubles

/// Holds requests until a test lets them go, one at a time or all together.
@MainActor
final class StandingsGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    var waiting: Int { waiters.count }

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        isOpen = true
        let pending = waiters
        waiters = []
        pending.forEach { $0.resume() }
    }

    func close() {
        isOpen = false
    }

    func releaseNext() {
        guard !waiters.isEmpty else { return }
        waiters.removeFirst().resume()
    }
}

/// What a friend's list answers, by "username|type|status" and the page ("nil" is the first).
@MainActor
final class StandingsRepoRoot {
    struct Request: Equatable {
        let username: String
        let mediaType: String
        let status: String?
        let page: String?
    }

    private(set) var requests: [Request] = []
    private(set) var running = 0
    private(set) var peakRunning = 0
    var pages: [String: [String?: PagedResponse<Spine.LibraryItem>]] = [:]
    var errors: [String: Error] = [:]
    var gate: StandingsGate?

    static func key(_ username: String, _ mediaType: String, _ status: String?) -> String {
        "\(username)|\(mediaType)|\(status ?? "")"
    }

    /// One page, with no next link.
    func set(_ username: String, _ mediaType: String, _ status: String = "In progress", rows: [Spine.LibraryItem]) {
        pages[Self.key(username, mediaType, status)] = [nil: PagedResponse(count: rows.count, next: nil, previous: nil, results: rows)]
    }

    func respond(_ request: Request) async throws -> PagedResponse<Spine.LibraryItem> {
        requests.append(request)
        running += 1
        peakRunning = max(peakRunning, running)
        defer { running -= 1 }
        let key = Self.key(request.username, request.mediaType, request.status)
        // The answer is the one standing when the request was made, not when it is let go.
        let answer = pages[key]?[request.page] ?? PagedResponse(count: 0, next: nil, previous: nil, results: [])
        let error = errors[key]
        await gate?.wait()
        if let error { throw error }
        return answer
    }
}

/// A tracking repository scoped to one friend, answering from the root.
@MainActor
final class StandingsRepository: TrackingRepository {
    let root: StandingsRepoRoot
    let username: String?

    init(root: StandingsRepoRoot, username: String? = nil) {
        self.root = root
        self.username = username
    }

    func owned(by username: String) -> TrackingRepository {
        StandingsRepository(root: root, username: username)
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        try await root.respond(.init(username: username ?? "", mediaType: mediaType, status: status, page: page))
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

// MARK: Tests

@MainActor
final class FriendStandingsTests: XCTestCase {
    private var root: StandingsRepoRoot!
    private var clock = Date(timeIntervalSince1970: 2_000_000)
    private var unauthorized = 0
    private var changes = 0
    private var seenChanges = 0

    override func setUp() {
        super.setUp()
        root = StandingsRepoRoot()
        clock = Date(timeIntervalSince1970: 2_000_000)
        unauthorized = 0
        changes = 0
        seenChanges = 0
    }

    private func provider(
        maxInFlight: Int = 3,
        maxPages: Int = 3,
        notifyDelay: Duration = .milliseconds(5)
    ) -> FriendStandings {
        let provider = FriendStandings(
            trackingRepository: StandingsRepository(root: root),
            onUnauthorized: { [unowned self] in unauthorized += 1 },
            now: { [unowned self] in clock },
            maxInFlight: maxInFlight,
            maxPages: maxPages,
            notifyDelay: notifyDelay
        )
        provider.setOnChange { [unowned self] in changes += 1 }
        return provider
    }

    // MARK: Builders

    private func ref(_ type: String, _ source: String, _ id: String) -> MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: type, mediaId: id, seasonNumber: nil, episodeNumber: nil)
    }

    private func row(_ ref: MediaRef, _ tracking: TrackingState) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref, title: "Title \(ref.mediaId)", posterUrl: nil, posterOrientation: .portrait),
            tracking: tracking
        )
    }

    private func bookRow(_ id: String, page: Int, of total: Int = 448) -> Spine.LibraryItem {
        row(ref("book", "manual", id), InProgressUpdatesTests.tracking(
            progress: ProgressState(kind: "pages", value: Decimal(page), max: Decimal(total), unit: "page")
        ))
    }

    private func showRow(_ id: String, watched: Int, aired: Int) -> Spine.LibraryItem {
        row(ref("tv", "tmdb", id), InProgressUpdatesTests.tracking(
            tv: TVShowState(inLibrary: true, status: "In progress", watchedCount: watched, airedCount: aired)
        ))
    }

    private func mangaRow(_ id: String, chapter: Int, of total: Int) -> Spine.LibraryItem {
        row(ref("manga", "mangadex", id), InProgressUpdatesTests.tracking(
            progress: ProgressState(kind: "chapters", value: Decimal(chapter), max: Decimal(total), unit: "chapter")
        ))
    }

    private func gameRow(_ id: String, minutes: Int) -> Spine.LibraryItem {
        row(ref("game", "igdb", id), InProgressUpdatesTests.tracking(
            game: InProgressUpdatesTests.game(
                playthrough: InProgressUpdatesTests.playthrough(minutes: minutes, percentage: nil)
            )
        ))
    }

    /// A friend's progress day on a book by default.
    private func day(
        _ id: Int,
        _ label: String? = "28 pages",
        moves: [String]? = nil,
        type: String = "book",
        source: String = "manual",
        title id2: String = "7",
        season: Int? = nil,
        episode: Int? = nil,
        user: String = "a",
        actor: Int = 3
    ) throws -> ActivityItem {
        try FeedTestItems.day(
            id: id, label: label, moves: moves, unit: type == "book" ? "pages" : "minutes", mediaType: type, source: source,
            mediaID: id2, season: season, episode: episode, username: user, actorID: actor
        )
    }

    private func until(_ condition: @autoclosure () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0 ..< 400 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("Timed out waiting", file: file, line: line)
    }

    /// Waits for what landed to be told to Home.
    private func landed(_ provider: FriendStandings, file: StaticString = #filePath, line: UInt = #line) async {
        await until(changes > seenChanges, file: file, line: line)
        seenChanges = changes
    }

    private func pair(_ user: String, _ type: String, _ status: String = "In progress") -> FriendStandings.Pair {
        .init(username: user, mediaType: type, status: status)
    }

    // MARK: 1. Grouping

    func testOneRequestPerFriendTypeAndStatusHoweverManyCards() async throws {
        let items = [
            try day(1, "30 pages", title: "7"),
            try day(2, "10 pages", title: "8"),
            try day(3, "2 episodes", type: "tv", source: "tmdb", title: "5"),
            try day(4, "20 pages", title: "7", user: "b", actor: 4),
            try day(5, "4 chapters", type: "manga", source: "mangadex", title: "9", user: "c", actor: 5),
            try day(6, "5 pages", title: "7", user: "c", actor: 5),
            try day(7, "20 pages", title: "7"),
            try FeedTestItems.log(id: 8, rating: "4", username: "a", actorID: 3),
        ]
        let provider = provider()
        provider.prepare(for: items)
        await until(root.requests.count == 5 && root.running == 0)
        let keys = Set(root.requests.map { StandingsRepoRoot.key($0.username, $0.mediaType, $0.status) })
        XCTAssertEqual(keys, [
            "a|book|In progress", "a|tv|In progress", "b|book|In progress", "c|manga|In progress", "c|book|In progress",
        ])
        XCTAssertEqual(root.requests.count, 5, "The log and the older card of a title ask for nothing")

        await landed(provider)
        provider.prepare(for: items)
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(root.requests.count, 5, "A second prepare of the same items asks for nothing")
    }

    func testTypesWithNoPositionAndStartedCardsAreNotWanted() throws {
        let movie = try day(1, "1 movie", type: "movie", source: "tmdb")
        let started = try day(2, "Started", title: "8")
        let startedThenMore = [try day(3, "Started", title: "9"), try day(4, "30 pages", title: "9")]
        XCTAssertNil(FriendStandings.want(for: movie))
        XCTAssertNil(FriendStandings.want(for: started))
        XCTAssertTrue(FriendStandings.wants(in: startedThenMore).isEmpty, "An older card is not newest once a start is")
        XCTAssertTrue(FriendStandings.wants(in: [try FeedTestItems.log(id: 5, rating: "4")]).isEmpty)
    }

    func testAPlanningRowIsNotWanted() throws {
        let planning = try FeedTestItems.planningRow(id: -30, count: 6, mediaType: "book")
        XCTAssertTrue(FriendStandings.wants(in: [planning]).isEmpty)
    }

    // MARK: 2. Order and concurrency

    func testRequestsStartInCardOrderAndNeverMoreThanThreeAtOnce() async throws {
        let gate = StandingsGate()
        root.gate = gate
        let users = ["a", "b", "c", "d", "e"]
        let items = try users.enumerated().map { index, user in
            try day(index + 1, "10 pages", user: user, actor: 10 + index)
        }
        let provider = provider()
        provider.prepare(for: items)
        await until(root.requests.count == 3)
        XCTAssertEqual(root.requests.map(\.username), ["a", "b", "c"], "The cards nearest the top load first")
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(root.requests.count, 3, "The fourth waits for a free place")

        gate.open()
        await until(root.requests.count == 5 && root.running == 0)
        XCTAssertEqual(root.requests.map(\.username), users)
        XCTAssertEqual(root.peakRunning, 3)
    }

    // MARK: 3. The math

    func testAPageBookGivesItsRangeItsEndAndItsPage() throws {
        let position = TilePosition(unit: .pages, current: 212, total: 448, pagesTotal: 448)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .pages, amount: 28)))
        XCTAssertEqual(standing, FeedStanding(unit: .pages, from: 184, to: 212, total: 448, whereText: "p. 212"))
    }

    func testAShowGivesItsEpisodes() throws {
        let position = TilePosition(unit: .episodes, current: 63, total: 70, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .episodes, amount: 3)))
        XCTAssertEqual(standing, FeedStanding(unit: .episodes, from: 60, to: 63, total: 70, whereText: "63 of 70 episodes"))
    }

    func testAShowWithNoKnownEndSaysHowManyAndDrawsNoMeter() throws {
        let position = TilePosition(unit: .episodes, current: 9, total: nil, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: nil))
        XCTAssertEqual(standing.whereText, "9 episodes")
        XCTAssertNil(standing.total)
        XCTAssertNil(standing.from)
    }

    func testAPercentBookWithNoPageCountLightsNothing() throws {
        let position = TilePosition(unit: .percent, current: 71, total: 100, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .pages, amount: 20)))
        XCTAssertNil(standing.from, "Pages cannot go on a percent bar without the book's length")
        XCTAssertEqual(standing.to, 71)
        XCTAssertEqual(standing.total, 100)
        XCTAssertEqual(standing.whereText, "now 71%")
    }

    func testAPercentBookWithItsPageCountConvertsTheDay() throws {
        let position = TilePosition(unit: .percent, current: 71, total: 100, pagesTotal: 400)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .pages, amount: 20)))
        XCTAssertEqual(standing.from, 66, "20 of 400 pages is 5 percent")
        XCTAssertEqual(standing.to, 71)
    }

    func testAGameCountedInHoursHasNoEndAndSaysItsTime() throws {
        let position = TilePosition(unit: .minutes, current: 840, total: nil, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .minutes, amount: 90)))
        XCTAssertNil(standing.total)
        XCTAssertEqual(standing.whereText, "14h")
        XCTAssertNil(ProgressMeterModel.make(unit: standing.unit, from: standing.from, to: standing.to, total: standing.total))

        let short = TilePosition(unit: .minutes, current: 90, total: nil, pagesTotal: nil)
        XCTAssertEqual(FeedStandingMath.standing(position: short, told: nil)?.whereText, "1h 30m")
    }

    func testMangaAndComicsCountChaptersAndIssues() throws {
        let manga = TilePosition(unit: .chapters, current: 41, total: 90, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: manga, told: TodayPart(unit: .chapters, amount: 5)))
        XCTAssertEqual(standing.from, 36)
        XCTAssertEqual(standing.whereText, "41 of 90 chapters")
        let comic = TilePosition(unit: .issues, current: 3, total: 12, pagesTotal: nil)
        XCTAssertEqual(FeedStandingMath.standing(position: comic, told: nil)?.whereText, "3 of 12 issues")
    }

    func testADayLargerThanThePositionStartsAtZero() throws {
        let position = TilePosition(unit: .pages, current: 40, total: 300, pagesTotal: 300)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .pages, amount: 500)))
        XCTAssertEqual(standing.from, 0)
    }

    func testNothingToSayGivesNoStanding() {
        XCTAssertNil(FeedStandingMath.standing(position: TilePosition(unit: .pages, current: 0, total: nil, pagesTotal: nil), told: nil))
    }

    func testAnAnimeAndAShowCountAiredEpisodesFromTheirLists() throws {
        let show = try XCTUnwrap(InProgressTilePresenter.position(for: showRow("5", watched: 63, aired: 70)))
        XCTAssertEqual(show.total, 70)
        XCTAssertEqual(FeedStandingMath.standing(position: show, told: nil)?.total, 70)
    }

    // MARK: 4. Status routing

    func testTheDaysLastMoveDecidesWhichListIsAsked() throws {
        func status(_ moves: [String]?) throws -> String? {
            FriendStandings.want(for: try day(1, moves == nil ? "10 pages" : nil, moves: moves))?.pair.status
        }
        XCTAssertEqual(try status(["paused"]), "Paused")
        XCTAssertEqual(try status(["stopped"]), "Dropped")
        XCTAssertEqual(try status(["paused", "picked_back_up"]), "In progress")
        XCTAssertEqual(try status(nil), "In progress")
        XCTAssertEqual(try status([]), "In progress")
        XCTAssertEqual(try status(["picked_back_up", "paused"]), "Paused")
    }

    func testAPausedCardAsksForTheFriendsPausedList() async throws {
        root.set("a", "book", "Paused", rows: [bookRow("7", page: 212)])
        let item = try day(1, nil, moves: ["paused"])
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.status), ["Paused"])
        let standing = try XCTUnwrap(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertEqual(standing.whereText, "p. 212")
        XCTAssertNil(standing.from, "A status-only day tells no amount")
        let card = FeedCardPresenter.model(for: item, standing: standing, isNewestOfTitle: true)
        XCTAssertEqual(card.meter?.from, card.meter?.to, "A status card lights nothing")
    }

    // MARK: 5. Title keys

    func testSeasonsAndEpisodesOfAShowAreTheShowsListRow() throws {
        let rowKey = FeedEntries.titleKey(for: ref("tv", "tmdb", "5"))
        let season = try day(1, "1 episode", type: "season", source: "tmdb", title: "5", season: 2)
        let episode = try day(2, "1 episode", type: "episode", source: "tmdb", title: "5", season: 2, episode: 3)
        XCTAssertEqual(FriendStandings.want(for: season)?.titleKey, rowKey)
        XCTAssertEqual(FriendStandings.want(for: episode)?.titleKey, rowKey)
        XCTAssertEqual(FriendStandings.want(for: season)?.pair.mediaType, "tv")
    }

    func testAMalEpisodeIsTheAnimeRow() throws {
        let rowKey = FeedEntries.titleKey(for: ref("anime", "mal", "52"))
        let episode = try day(1, "1 episode", type: "episode", source: "mal", title: "52", season: 1, episode: 4)
        let want = try XCTUnwrap(FriendStandings.want(for: episode))
        XCTAssertEqual(want.titleKey, rowKey)
        XCTAssertEqual(want.pair.mediaType, "anime")
    }

    // MARK: 6. A title that is not in the list

    func testATitleMissingFromTheListGetsNoStandingAndNoOtherRequest() async throws {
        root.set("a", "book", rows: [bookRow("99", page: 10)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true))
        let card = FeedCardPresenter.model(for: item, standing: nil, isNewestOfTitle: true)
        XCTAssertNil(card.meter)
        XCTAssertEqual(root.requests.count, 1, "No other status is probed for it")
    }

    func testOnlyTheNewestCardOfATitleGetsItsStanding() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        let newest = try day(1, "30 pages")
        let older = try day(2, "20 pages")
        let provider = provider()
        provider.prepare(for: [newest, older])
        await landed(provider)
        XCTAssertNotNil(provider.standing(for: newest, isNewestOfTitle: true))
        XCTAssertNil(provider.standing(for: older, isNewestOfTitle: false))
    }

    // MARK: 7. Paging

    func testAMissingTitleSendsTheSearchToTheNextPageAndStopsOnceFound() async throws {
        root.pages["a|book|In progress"] = [
            nil: PagedResponse(count: 2, next: "https://t/api/users/a/tracking/?page=2", previous: nil, results: [bookRow("99", page: 5)]),
            "2": PagedResponse(count: 2, next: "https://t/api/users/a/tracking/?page=3", previous: nil, results: [bookRow("7", page: 212)]),
        ]
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.page), [nil, "2"])
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 212)
    }

    func testTheSearchGivesUpAfterThreePages() async throws {
        root.pages["a|book|In progress"] = [
            nil: PagedResponse(count: 9, next: "https://t/x/?page=2", previous: nil, results: [bookRow("90", page: 5)]),
            "2": PagedResponse(count: 9, next: "https://t/x/?page=3", previous: nil, results: [bookRow("91", page: 5)]),
            "3": PagedResponse(count: 9, next: "https://t/x/?page=4", previous: nil, results: [bookRow("92", page: 5)]),
            "4": PagedResponse(count: 9, next: nil, previous: nil, results: [bookRow("7", page: 5)]),
        ]
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.count, 3)
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true))
    }

    // MARK: 8. Errors

    func testAnAccountThatIsPrivateNowIsLeftAloneUntilTheFeedReloads() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertNotNil(provider.standing(for: item, isNewestOfTitle: true))

        root.errors["a|book|In progress"] = APIError.httpStatus(404, nil)
        provider.reload(for: [item])
        await until(root.requests.count == 2 && root.running == 0)
        await landed(provider)
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true), "Nothing of a private account is shown")

        clock = clock.addingTimeInterval(3 * 3600)
        provider.prepare(for: [item])
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(root.requests.count, 2, "A prepare does not retry an unavailable account")

        root.errors = [:]
        provider.reload(for: [item])
        await until(root.requests.count == 3)
        await landed(provider)
        XCTAssertNotNil(provider.standing(for: item, isNewestOfTitle: true), "A reload asks again")
    }

    func testAnExpiredSessionIsReportedOnce() async throws {
        root.errors["a|book|In progress"] = APIError.unauthorized
        let provider = provider()
        provider.prepare(for: [try day(1, "30 pages"), try day(2, "3 pages", title: "8")])
        await landed(provider)
        XCTAssertEqual(unauthorized, 1)
    }

    func testAFailureIsNotRetriedWithinAMinuteAndKeepsWhatWasKnown() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)

        root.errors["a|book|In progress"] = URLError(.notConnectedToInternet)
        provider.reload(for: [item])
        await until(root.requests.count == 2 && root.running == 0)
        await landed(provider)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 212, "The earlier standing stays")

        clock = clock.addingTimeInterval(30)
        provider.prepare(for: [item])
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(root.requests.count, 2, "Not within 60 seconds")

        root.errors = [:]
        root.set("a", "book", rows: [bookRow("7", page: 260)])
        clock = clock.addingTimeInterval(31)
        provider.prepare(for: [item])
        await until(root.requests.count == 3)
        await landed(provider)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 260)
    }

    func testACancelledRequestLeavesThePairToBeAskedAgain() async throws {
        root.errors["a|book|In progress"] = CancellationError()
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await until(root.requests.count == 1 && root.running == 0)
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(changes, 0, "A cancellation tells nobody")

        root.errors = [:]
        provider.prepare(for: [item])
        await until(root.requests.count == 2)
        await landed(provider)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 212)
    }

    // MARK: 9. A reload keeps what was drawn

    func testAReloadKeepsTheOldStandingWhileItRunsAndDropsAStaleAnswer() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 100)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 100)

        let gate = StandingsGate()
        gate.close()
        root.gate = gate
        root.set("a", "book", rows: [bookRow("7", page: 150)])
        provider.reload(for: [item])
        await until(gate.waiting == 1)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 100, "The old meter stays while the new one loads")
        gate.releaseNext()
        await landed(provider)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 150)

        // Two reloads in a row: the first one's answer is stale when it arrives.
        root.set("a", "book", rows: [bookRow("7", page: 200)])
        provider.reload(for: [item])
        await until(gate.waiting == 1)
        root.set("a", "book", rows: [bookRow("7", page: 300)])
        provider.reload(for: [item])
        await until(gate.waiting == 2)
        gate.releaseNext()
        try? await Task.sleep(for: .milliseconds(40))
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 150, "The first answer is dropped")
        gate.releaseNext()
        await landed(provider)
        XCTAssertEqual(provider.standing(for: item, isNewestOfTitle: true)?.to, 300)
    }

    // MARK: 10. Coalescing

    func testEightPairsLandingTogetherTellHomeOnce() async throws {
        let users = (0 ..< 8).map { "friend\($0)" }
        for user in users { root.set(user, "book", rows: [bookRow("7", page: 50)]) }
        let items = try users.enumerated().map { index, user in try day(index + 1, "10 pages", user: user, actor: 20 + index) }
        let provider = provider(notifyDelay: .milliseconds(300))
        provider.prepare(for: items)
        await until(root.requests.count == 8 && root.running == 0)
        await landed(provider)
        XCTAssertEqual(changes, 1)
        for item in items { XCTAssertNotNil(provider.standing(for: item, isNewestOfTitle: true)) }
    }

    // MARK: 11. In the feed model

    func testOnlyTheCardWithAStandingChangesWhenItLands() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        let newest = try day(1, "28 pages")
        let older = try day(2, "20 pages")
        let log = try FeedTestItems.log(id: 3, rating: "4", username: "b", actorID: 4)
        let planning = try FeedTestItems.planningRow(id: -30, count: 6, mediaType: "movie")
        let items = [newest, older, log, planning]

        let activity = HomeRecordingActivityRepository()
        let provider = provider()
        let feed = ActivityFeedViewModel(username: nil, activityRepository: activity, onUnauthorized: {})
        let model = HomeFeedModel(
            feed: feed,
            planning: FeedPlanningStore(activityRepository: activity, onUnauthorized: {}),
            standing: provider
        )
        feed.items = items
        let before = model.entries
        XCTAssertNil(before.first { $0.id == 1 }?.meter)

        provider.prepare(for: items)
        await landed(provider)
        let after = model.entries
        XCTAssertEqual(before.count, after.count)

        let changed = zip(before, after).filter { $0 != $1 }.map(\.0.id)
        XCTAssertEqual(changed, [1], "Only the newest card of the title redraws")
        let card = try XCTUnwrap(after.first { $0.id == 1 })
        XCTAssertNotNil(card.meter)
        XCTAssertEqual(card.meter?.accessibilityLabel.isEmpty, false)
        guard case let .progress(amount, tail, _) = card.headline else { return XCTFail("A progress headline") }
        XCTAssertEqual(amount, "+28 pages")
        XCTAssertEqual(tail, "p. 212")
        XCTAssertNil(after.first { $0.id == 2 }?.meter)
        XCTAssertEqual(after.first { $0.id == -30 }?.kind, .planning)
    }

    // MARK: Wiring

    func testAViewModelBuiltForTheRailKeepsNoStandings() {
        let activity = HomeRecordingActivityRepository()
        let plain = HomeFeedModel(
            feed: ActivityFeedViewModel(username: nil, activityRepository: activity, onUnauthorized: {}),
            planning: FeedPlanningStore(activityRepository: activity, onUnauthorized: {})
        )
        XCTAssertTrue(plain.standing is NoFeedStanding)
    }

    func testThePlainProviderTakesTheNewRequirementsAsNoOps() {
        let none = NoFeedStanding()
        none.reload(for: [])
        none.setOnChange { XCTFail("Nothing lands") }
    }
}
