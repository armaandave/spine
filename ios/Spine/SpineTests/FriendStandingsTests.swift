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
        var query: String?
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
        try await root.respond(.init(username: username ?? "", mediaType: mediaType, status: status, page: page, query: query))
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

/// Season pages by the season ref's id; anything else is an empty page.
@MainActor
final class StandingsSeasonPages: MediaRepository {
    var details: [String: MediaDetail] = [:]
    var error: Error?
    var gate: RefreshGate?
    private(set) var detailRequests: [MediaRef] = []

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailRequests.append(ref)
        await gate?.wait()
        if let error { throw error }
        return details[ref.id] ?? MediaDetail(ref: ref, title: "Fixture")
    }

    func reviews(ref: MediaRef) async throws -> [MediaReview] { [] }
    func meta() async throws -> MetaResponse { throw URLError(.notConnectedToInternet) }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func homePicture() async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func setHomePicture(ref _: MediaRef, backdropURL _: String) async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
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
        notifyDelay: Duration = .milliseconds(5),
        seasons: MediaRepository? = nil
    ) -> FriendStandings {
        let provider = FriendStandings(
            trackingRepository: StandingsRepository(root: root),
            seasonPages: seasons,
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
        root.set("a", "book", rows: [bookRow("7", page: 10), bookRow("8", page: 10)])
        root.set("b", "book", rows: [bookRow("7", page: 10)])
        root.set("c", "manga", rows: [mangaRow("9", chapter: 4, of: 90)])
        root.set("c", "book", rows: [bookRow("7", page: 10)])
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
        for user in users { root.set(user, "book", rows: [bookRow("7", page: 10)]) }
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
        XCTAssertEqual(standing, FeedStanding(unit: .episodes, from: 60, to: 63, total: 70, whereText: "63/70"))
    }

    func testAShowWithNoKnownEndSaysHowManyAndDrawsNoMeter() throws {
        let position = TilePosition(unit: .episodes, current: 9, total: nil, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: nil))
        XCTAssertEqual(standing.whereText, "9 episodes")
        XCTAssertNil(standing.total)
        XCTAssertNil(standing.from)
    }

    func testADayAFinishingLogFollowedStopsAtItsOwnPlace() throws {
        // Read 248 pages to p. 893, then Finished: the log took the last 99, and the friend now stands at the end.
        let position = TilePosition(unit: .pages, current: 992, total: 992, pagesTotal: 992)
        let standing = try XCTUnwrap(FeedStandingMath.standing(
            position: position, told: TodayPart(unit: .pages, amount: 248), place: ["pages": 893]
        ))
        XCTAssertEqual(standing, FeedStanding(unit: .pages, from: 645, to: 893, total: 992, whereText: "p. 893"))
    }

    func testADaysPlaceInPercentGoesOnAPageBarThroughThePageCount() throws {
        let position = TilePosition(unit: .pages, current: 400, total: 400, pagesTotal: 400)
        let standing = try XCTUnwrap(FeedStandingMath.standing(
            position: position, told: TodayPart(unit: .pages, amount: 40), place: ["percent": 75]
        ))
        XCTAssertEqual(standing.to, 300)
        XCTAssertEqual(standing.from, 260)
        XCTAssertNil(FeedStandingMath.dayEnd(["percent": 75], in: TilePosition(unit: .pages, current: 1, total: nil, pagesTotal: nil)))
    }

    func testTheProviderReadsTheDaysPlaceFromTheCard() async throws {
        root.set("a", "book", rows: [])
        root.set("a", "book", "Completed", rows: [bookRow("7", page: 992, of: 992)])
        let item = try FeedTestItems.item(
            type: "progress_updated", objectType: "progress_day", objectID: 10,
            objectFields: "\"label\": \"248 pages\", \"unit\": \"pages\", \"place\": {\"pages\": 893}",
            username: "a", source: "manual", mediaType: "book", mediaID: "7", title: "Perf My Book"
        )
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        let standing = try XCTUnwrap(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertEqual(standing.whereText, "p. 893")
        XCTAssertEqual(standing.from, 645)
    }

    func testAPercentBookWithNoPageCountLightsNothing() throws {
        let position = TilePosition(unit: .percent, current: 71, total: 100, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(position: position, told: TodayPart(unit: .pages, amount: 20)))
        XCTAssertNil(standing.from, "Pages cannot go on a percent bar without the book's length")
        XCTAssertEqual(standing.to, 71)
        XCTAssertEqual(standing.total, 100)
        XCTAssertEqual(standing.whereText, "total 71%")
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
        XCTAssertEqual(standing.whereText, "41/90")
        let comic = TilePosition(unit: .issues, current: 3, total: 12, pagesTotal: nil)
        XCTAssertEqual(FeedStandingMath.standing(position: comic, told: nil)?.whereText, "3/12")
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
        XCTAssertEqual(root.requests.map(\.status), ["In progress", "Completed"], "Only Completed is tried after In progress")
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

    // MARK: 6b. A title finished that day

    func testAFriendWhoFinishedABookThatDayGetsTheEndFromTheCompletedList() async throws {
        root.set("a", "book", rows: [])
        root.set("a", "book", "Completed", rows: [bookRow("7", page: 476, of: 476)])
        let item = try day(1, "304 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)

        XCTAssertEqual(root.requests.map(\.status), ["In progress", "Completed"])
        XCTAssertEqual(root.requests.last?.query, "Perf My Book", "Asked for by name, first page only")
        XCTAssertNil(root.requests.last?.page)
        let standing = try XCTUnwrap(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertEqual(standing.whereText, "p. 476")
        XCTAssertEqual(standing.from, 172, "The day's 304 pages are lit at the end")
        XCTAssertEqual(standing.to, 476)
        XCTAssertEqual(standing.total, 476, "A full bar")
        let card = FeedCardPresenter.model(for: item, standing: standing, isNewestOfTitle: true)
        XCTAssertNotNil(card.meter)

        provider.prepare(for: [item])
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(root.requests.count, 2, "Asked once while the answer is fresh")
    }

    func testOnlyTheTitleMissingFromTheListIsAskedForInCompleted() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        root.set("a", "book", "Completed", rows: [bookRow("8", page: 300, of: 300), bookRow("9", page: 10, of: 10)])
        let inProgress = try day(1, "30 pages", title: "7")
        let finished = try day(2, "50 pages", title: "8")
        let provider = provider()
        provider.prepare(for: [inProgress, finished])
        await landed(provider)

        XCTAssertEqual(root.requests.map(\.status), ["In progress", "Completed"], "One Completed request, for one title")
        XCTAssertEqual(provider.standing(for: inProgress, isNewestOfTitle: true)?.to, 212)
        XCTAssertEqual(provider.standing(for: finished, isNewestOfTitle: true)?.to, 300)
        XCTAssertEqual(provider.standing(for: finished, isNewestOfTitle: true)?.whereText, "p. 300")
    }

    func testATitleFoundInProgressMakesNoCompletedRequest() async throws {
        root.set("a", "book", rows: [bookRow("7", page: 212)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.status), ["In progress"])
    }

    func testATitleInNeitherListGetsNoStanding() async throws {
        root.set("a", "book", "Completed", rows: [bookRow("99", page: 300, of: 300)])
        let item = try day(1, "30 pages")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.status), ["In progress", "Completed"])
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true), "A search hit for another title is not it")
    }

    func testAPausedCardNeverAsksTheCompletedList() async throws {
        let item = try day(1, nil, moves: ["paused"])
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.status), ["Paused"])
    }

    func testAFinishedShowIsLeftWithoutAPlaceAndNotAsked() async throws {
        // A finished show's row has no episode up next, so the season it ended in cannot be known from it.
        root.set("a", "tv", "Completed", rows: [seasonShowRow("5", watched: 24, aired: 24)])
        let media = StandingsSeasonPages()
        let item = try day(1, "3 episodes", type: "tv", source: "tmdb", title: "5")
        let provider = provider(seasons: media)
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.status), ["In progress"], "No request per finished show")
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertTrue(media.detailRequests.isEmpty)
    }

    func testAFinishedMangaGetsItsChaptersFromTheCompletedList() async throws {
        root.set("a", "manga", "Completed", rows: [mangaRow("9", chapter: 90, of: 90)])
        let item = try day(1, "5 chapters", type: "manga", source: "mangadex", title: "9")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertEqual(root.requests.map(\.status), ["In progress", "Completed"])
        let standing = try XCTUnwrap(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertEqual(standing.whereText, "90/90")
        XCTAssertEqual(standing.to, 90)
        XCTAssertEqual(standing.total, 90)
    }

    func testAPercentPositionSaysTotal() {
        let position = TilePosition(unit: .percent, current: 63, total: 100, pagesTotal: nil)
        XCTAssertEqual(FeedStandingText.whereText(for: position), "total 63%")
        XCTAssertEqual(FeedStandingText.whereText(for: TilePosition(unit: .percent, current: 63.5, total: 100, pagesTotal: nil)), "total 63.5%")
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
        XCTAssertEqual(root.requests.map(\.status), ["In progress", "In progress", "In progress", "Completed"])
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
        // Home's feed model takes the provider's change callback for itself, so wait on what it rebuilds.
        await until(model.entries.first { $0.id == 1 }?.meter != nil)
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

    // MARK: 12. A show is counted by its season

    private func seasonShowRow(
        _ id: String,
        watched: Int = 3,
        aired: Int = 24,
        upNext: (season: Int, episode: Int)? = nil,
        airing: TVAiring? = nil
    ) -> Spine.LibraryItem {
        row(ref("tv", "tmdb", id), InProgressUpdatesTests.tracking(tv: TVShowState(
            inLibrary: true,
            status: "In progress",
            watchedCount: watched,
            airedCount: aired,
            upNext: upNext.map { TVEpisodePointer(seasonNumber: $0.season, episodeNumber: $0.episode) },
            nextAiring: airing
        )))
    }

    private func serveSeason(_ media: StandingsSeasonPages, show id: String, season: Int, episodes: Int) {
        let seasonRef = ContinueWatchingEpisodes.seasonRef(show: ref("tv", "tmdb", id), seasonNumber: season)
        media.details[seasonRef.id] = MediaDetail(
            ref: seasonRef,
            title: "Season \(season)",
            episodes: (0 ..< episodes).map {
                EpisodeSummary(
                    episodeNumber: $0 + 1, title: "Episode \($0 + 1)", overview: nil, airDate: nil, runtime: nil,
                    imageUrl: nil, rating: nil
                )
            }
        )
    }

    func testTheSeasonAFriendIsInComesFromTheEpisodeUpNext() {
        func place(_ row: Spine.LibraryItem) -> FriendStandings.SeasonPlace? { FriendStandings.seasonPlace(for: row) }
        let midSeason = place(seasonShowRow("5", upNext: (1, 4)))
        XCTAssertEqual(midSeason?.number, 1)
        XCTAssertEqual(midSeason?.watched, 3, "Every episode before the one up next is watched")
        XCTAssertEqual(place(seasonShowRow("5", upNext: (3, 7)))?.watched, 6)

        let started = place(seasonShowRow("5", watched: 0, upNext: (1, 1)))
        XCTAssertEqual(started?.number, 1)
        XCTAssertEqual(started?.watched, 0)

        // Up next is the first episode of a later season: they have just finished the one before.
        let finished = place(seasonShowRow("5", upNext: (2, 1)))
        XCTAssertEqual(finished?.number, 1)
        XCTAssertNil(finished?.watched, "Complete")
    }

    func testACaughtUpFriendIsInTheSeasonOfTheNextEpisodeToAir() {
        let airing = TVAiring(seasonNumber: 2, episodeNumber: 5, airDate: nil)
        let place = FriendStandings.seasonPlace(for: seasonShowRow("5", airing: airing))
        XCTAssertEqual(place?.number, 2)
        XCTAssertEqual(place?.watched, 4)
        let premiere = FriendStandings.seasonPlace(for: seasonShowRow("5", airing: TVAiring(seasonNumber: 3, episodeNumber: 1)))
        XCTAssertEqual(premiere?.number, 2)
        XCTAssertNil(premiere?.watched)
    }

    func testAShowWhoseSeasonCannotBeKnownHasNoPlace() {
        XCTAssertNil(FriendStandings.seasonPlace(for: seasonShowRow("5")))
        XCTAssertNil(FriendStandings.seasonPlace(for: seasonShowRow("5", airing: TVAiring(seasonNumber: 2, episodeNumber: nil))))
        XCTAssertNil(FriendStandings.seasonPlace(for: bookRow("7", page: 10)), "Only shows are counted by a season")
    }

    func testASeasonCountIsTheSeasonsLitTicksAndWords() throws {
        let standing = try XCTUnwrap(FeedStandingMath.seasonStanding(
            season: 1, watched: 3, total: 12, told: TodayPart(unit: .episodes, amount: 3)
        ))
        XCTAssertEqual(standing, FeedStanding(unit: .episodes, from: 0, to: 3, total: 12, whereText: "S1 \u{00B7} 3/12"))

        let later = try XCTUnwrap(FeedStandingMath.seasonStanding(
            season: 2, watched: 7, total: 10, told: TodayPart(unit: .episodes, amount: 2)
        ))
        XCTAssertEqual(later.from, 5)
        XCTAssertEqual(later.to, 7)

        let bigDay = try XCTUnwrap(FeedStandingMath.seasonStanding(
            season: 1, watched: 2, total: 12, told: TodayPart(unit: .episodes, amount: 5)
        ))
        XCTAssertEqual(bigDay.from, 0, "Never more lit than the season has watched")

        let complete = try XCTUnwrap(FeedStandingMath.seasonStanding(season: 1, watched: nil, total: 8, told: nil))
        XCTAssertEqual(complete.to, 8)
        XCTAssertNil(complete.from)
        XCTAssertEqual(complete.whereText, "S1 \u{00B7} 8/8")

        XCTAssertNil(FeedStandingMath.seasonStanding(season: 1, watched: 3, total: 0, told: nil), "No end, no meter")
    }

    func testTheSeasonsWordsNameSpecialsAndUseASlash() {
        XCTAssertEqual(FeedStandingText.seasonText(season: 1, current: 3, total: 12), "S1 \u{00B7} 3/12")
        XCTAssertEqual(FeedStandingText.spoken("S1 \u{00B7} 3/12"), "S1 \u{00B7} 3 of 12", "Never read as a date")
        XCTAssertEqual(FeedStandingText.spoken("1,204/1,500"), "1,204 of 1,500")
        XCTAssertEqual(FeedStandingText.spoken("p. 212"), "p. 212")
        XCTAssertEqual(FeedStandingText.seasonText(season: 2, current: 1, total: 1), "S2 \u{00B7} 1/1")
        XCTAssertEqual(FeedStandingText.seasonText(season: 0, current: 1, total: 4), "Specials \u{00B7} 1/4")
    }

    func testAFriendsShowCardCountsTheSeasonNotTheWholeShow() async throws {
        // Andor: 3 watched of the 24 aired in all, three episodes into a season of 12.
        root.set("a", "tv", rows: [seasonShowRow("5", watched: 3, aired: 24, upNext: (1, 4))])
        let media = StandingsSeasonPages()
        serveSeason(media, show: "5", season: 1, episodes: 12)
        let item = try day(1, "3 episodes", type: "tv", source: "tmdb", title: "5")
        let provider = provider(seasons: media)
        provider.prepare(for: [item])
        await until(provider.standing(for: item, isNewestOfTitle: true) != nil)

        let standing = try XCTUnwrap(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertEqual(standing, FeedStanding(unit: .episodes, from: 0, to: 3, total: 12, whereText: "S1 \u{00B7} 3/12"))
        XCTAssertFalse(standing.whereText?.contains("24") ?? true, "Never the whole show's numbers")
        XCTAssertEqual(media.detailRequests.map(\.mediaType), ["season"])
        XCTAssertEqual(media.detailRequests.first?.seasonNumber, 1)

        let card = FeedCardPresenter.model(for: item, standing: standing, isNewestOfTitle: true)
        XCTAssertNotNil(card.meter)
        guard case let .progress(amount, tail, _) = card.headline else { return XCTFail("A progress headline") }
        XCTAssertEqual(amount, "+3 episodes")
        XCTAssertEqual(tail, "S1 \u{00B7} 3/12")
    }

    func testAShowDayThatSaysWhereItEndedIsCountedByThatSeasonWithNoListRequest() async throws {
        // Ticked S1 E1 to E3, then logged Season 1 the next day: the friend is in season 2 now, the card's day is not.
        root.set("a", "tv", rows: [seasonShowRow("5", watched: 12, aired: 22, upNext: (2, 1))])
        let media = StandingsSeasonPages()
        serveSeason(media, show: "5", season: 1, episodes: 12)
        let item = try FeedTestItems.item(
            type: "progress_updated", objectType: "progress_day", objectID: 10,
            objectFields: "\"label\": \"3 episodes\", \"unit\": \"minutes\", \"place\": {\"season\": 1, \"episodes\": 3}",
            username: "a", source: "tmdb", mediaType: "tv", mediaID: "5"
        )
        let provider = provider(seasons: media)
        provider.prepare(for: [item])
        await until(provider.standing(for: item, isNewestOfTitle: true) != nil)

        let standing = try XCTUnwrap(provider.standing(for: item, isNewestOfTitle: true))
        XCTAssertEqual(standing, FeedStanding(unit: .episodes, from: 0, to: 3, total: 12, whereText: "S1 \u{00B7} 3/12"))
        XCTAssertTrue(root.requests.isEmpty, "The card says its season: the friend's list is not asked")
        XCTAssertEqual(media.detailRequests.map(\.seasonNumber), [1])
    }

    func testAnAnimeDayStopsAtItsOwnEpisode() throws {
        let position = TilePosition(unit: .episodes, current: 12, total: 12, pagesTotal: nil)
        let standing = try XCTUnwrap(FeedStandingMath.standing(
            position: position, told: TodayPart(unit: .episodes, amount: 3), place: ["episodes": 3]
        ))
        XCTAssertEqual(standing, FeedStanding(unit: .episodes, from: 0, to: 3, total: 12, whereText: "3/12"))
    }

    func testUntilTheSeasonIsKnownTheCardHasNoMeterAndNoWords() async throws {
        root.set("a", "tv", rows: [seasonShowRow("5", upNext: (1, 4))])
        let media = StandingsSeasonPages()
        serveSeason(media, show: "5", season: 1, episodes: 12)
        let gate = RefreshGate()
        media.gate = gate
        let item = try day(1, "3 episodes", type: "tv", source: "tmdb", title: "5")
        let provider = provider(seasons: media)
        provider.prepare(for: [item])
        await until(media.detailRequests.count == 1)
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true), "Nothing of the whole show is drawn meanwhile")
        await gate.open()
        await until(provider.standing(for: item, isNewestOfTitle: true) != nil)
    }

    func testWithoutSeasonPagesAShowGetsNothing() async throws {
        root.set("a", "tv", rows: [seasonShowRow("5", upNext: (1, 4))])
        let item = try day(1, "3 episodes", type: "tv", source: "tmdb", title: "5")
        let provider = provider()
        provider.prepare(for: [item])
        await landed(provider)
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true))
    }

    func testASeasonIsReadOnceHoweverManyFriendsAreInIt() async throws {
        root.set("a", "tv", rows: [seasonShowRow("5", upNext: (1, 4))])
        root.set("b", "tv", rows: [seasonShowRow("5", watched: 7, upNext: (1, 8))])
        let media = StandingsSeasonPages()
        serveSeason(media, show: "5", season: 1, episodes: 12)
        let first = try day(1, "3 episodes", type: "tv", source: "tmdb", title: "5")
        let second = try day(2, "2 episodes", type: "tv", source: "tmdb", title: "5", user: "b", actor: 4)
        let provider = provider(seasons: media)
        provider.prepare(for: [first, second])
        await until(
            provider.standing(for: first, isNewestOfTitle: true) != nil
                && provider.standing(for: second, isNewestOfTitle: true) != nil
        )
        XCTAssertEqual(media.detailRequests.count, 1)
        XCTAssertEqual(provider.standing(for: second, isNewestOfTitle: true)?.to, 7)
        XCTAssertEqual(provider.standing(for: second, isNewestOfTitle: true)?.whereText, "S1 \u{00B7} 7/12")
    }

    func testAFailedSeasonReadIsLeftAloneForAMinuteThenAskedAgain() async throws {
        root.set("a", "tv", rows: [seasonShowRow("5", upNext: (1, 4))])
        let media = StandingsSeasonPages()
        media.error = URLError(.notConnectedToInternet)
        let item = try day(1, "3 episodes", type: "tv", source: "tmdb", title: "5")
        let provider = provider(seasons: media)
        provider.prepare(for: [item])
        await until(media.detailRequests.count == 1)
        await landed(provider)
        XCTAssertNil(provider.standing(for: item, isNewestOfTitle: true))

        provider.prepare(for: [item])
        try await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(media.detailRequests.count, 1, "Left alone within the minute")

        clock = clock.addingTimeInterval(FriendStandings.retryAfterFailure + 1)
        media.error = nil
        serveSeason(media, show: "5", season: 1, episodes: 12)
        provider.prepare(for: [item])
        await until(provider.standing(for: item, isNewestOfTitle: true) != nil)
        XCTAssertEqual(media.detailRequests.count, 2)
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
