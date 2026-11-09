import XCTest
@testable import Spine

/// The In progress tile's main button for books, manga, comics and games, on the titles the perf data backend seeds
/// (a book 212 of 400, a game at 14h, a manga 41 of 90) and the edges around them.
@MainActor
final class InProgressQuickActionsTests: XCTestCase {
    private var touchedRefs: [MediaRef] = []
    private var repository: QuickActionRecordingRepository!
    private var updates: [(id: String, tracking: TrackingState)] = []
    private var unauthorized = 0
    private let origin = UUID()
    private let fixedDay = Date(timeIntervalSince1970: 1_794_000_000)

    override func setUp() {
        super.setUp()
        repository = QuickActionRecordingRepository()
        updates = []
        unauthorized = 0
    }

    override func tearDown() {
        for ref in touchedRefs {
            ProgressDisplayPreferences.removeMode(for: ref)
            ProgressDisplayPreferences.setGameMode(nil, for: ref)
        }
        touchedRefs = []
        super.tearDown()
    }

    private func makeActions() -> InProgressQuickActions {
        InProgressQuickActions(
            trackingRepository: repository,
            changeOrigin: origin,
            onUpdate: { [unowned self] id, tracking in updates.append((id, tracking)) },
            onUnauthorized: { [unowned self] in unauthorized += 1 },
            today: { [fixedDay] in fixedDay }
        )
    }

    // MARK: Fixtures

    private func ref(_ mediaType: String, _ id: String, source: String = "hardcover") -> MediaRef {
        let ref = MediaRef(itemId: nil, source: source, mediaType: mediaType, mediaId: "qa-\(id)",
                           seasonNumber: nil, episodeNumber: nil)
        touchedRefs.append(ref)
        return ref
    }

    private func item(
        _ mediaType: String,
        _ id: String,
        progress: ProgressState? = nil,
        book: BookTrackingState? = nil,
        series: BookTrackingState? = nil,
        game: GameTrackingState? = nil
    ) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref(mediaType, id), title: "Title \(id)", posterUrl: nil, posterOrientation: .portrait),
            tracking: TrackingState(
                trackingId: 1, status: "In progress", rating: nil, progress: progress, repeats: nil, startDate: nil,
                endDate: nil, notes: nil, updatedAt: nil, book: book, game: game, series: series
            )
        )
    }

    private func pages(_ value: Int, of max: Int = 400) -> ProgressState {
        ProgressState(kind: "pages", value: Decimal(value), max: Decimal(max), unit: "page")
    }

    private func book(_ id: String, pages value: Int, of max: Int = 400) -> Spine.LibraryItem {
        let progress = pages(value, of: max)
        return item("book", id, progress: progress, book: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress)
        ))
    }

    private func bookPercent(_ id: String, _ value: Int) -> Spine.LibraryItem {
        let progress = ProgressState(kind: "percentage", value: Decimal(value), max: 100, unit: "percent")
        return item("book", id, progress: progress, book: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress)
        ))
    }

    private func manga(_ id: String, _ value: Int, of total: Int = 90) -> Spine.LibraryItem {
        let progress = ProgressState(kind: "count", value: Decimal(value), max: Decimal(total), unit: "chapter")
        return item("manga", id, progress: progress, series: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress), total: total, unit: "chapter"
        ))
    }

    private func game(_ id: String, minutes: Int?, percent: Int?) -> Spine.LibraryItem {
        let playthrough = GamePlaythroughState(
            id: 31, status: "In progress", origin: "live", startDate: "2026-10-26", endDate: nil, totalMinutes: minutes,
            percentage: percent, completionDiaryEntryId: nil, isReplay: false
        )
        return item("game", id, game: GameTrackingState(
            currentPlaythrough: playthrough, playHistory: [], undatedCompletion: nil, statusSource: nil,
            completionDates: [], completedPlaythroughCount: 0, lifetimeCompletionCount: 0, isReplaying: false,
            canRemoveTracking: false, availableActions: [], actionReasons: [:], importedLifetimeMinutes: nil,
            importedLifetimeSource: nil
        ))
    }

    /// What the server answers to a book save: the journey at the new position.
    private func bookResponse(pages value: Int, of max: Int = 400) -> TrackingState {
        let progress = pages(value, of: max)
        return TrackingState(
            trackingId: 1, status: "In progress", rating: nil, progress: progress, repeats: nil, startDate: nil,
            endDate: nil, notes: nil, updatedAt: nil,
            book: BookTrackingState(currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress))
        )
    }

    private func gameResponse(minutes: Int?, percent: Int?) -> TrackingState {
        game("response", minutes: minutes, percent: percent).tracking
    }

    private func mangaResponse(_ value: Int, of total: Int = 90, opensLog: Bool = false) -> TrackingState {
        let progress = ProgressState(kind: "count", value: Decimal(value), max: Decimal(total), unit: "chapter")
        return TrackingState(
            trackingId: 1, status: "In progress", rating: nil, progress: progress, repeats: nil, startDate: nil,
            endDate: nil, notes: nil, updatedAt: nil,
            series: BookTrackingState(
                currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress),
                total: total, unit: "chapter", opensLog: opensLog
            )
        )
    }

    /// The origins of the MediaStateChange posts for `ref` while `body` runs.
    private func posts(for ref: MediaRef, during body: () async -> Void) async -> [UUID?] {
        let box = PostBox()
        let token = NotificationCenter.default.addObserver(forName: .mediaStateDidChange, object: nil, queue: nil) { note in
            guard let posted = note.userInfo?["ref"] as? MediaRef, posted == ref else { return }
            box.add(note.userInfo?["origin"] as? UUID)
        }
        defer { NotificationCenter.default.removeObserver(token) }
        await body()
        return box.origins
    }

    private func decimal(_ value: Int) -> Decimal { Decimal(value) }

    // MARK: Books

    func testBookPagesSendsAnAbsoluteValueAndPatchesTheCard() async {
        let card = book("pages", pages: 212)
        repository.bookResult = bookResponse(pages: 232)
        let actions = makeActions()
        var result: InProgressQuickActions.Result?
        let origins = await posts(for: card.media.ref) { result = await actions.run(.addPages(20), on: card) }
        XCTAssertEqual(result, .done)
        XCTAssertEqual(repository.bookCalls.count, 1)
        XCTAssertEqual(repository.bookCalls.first?.type, "pages")
        XCTAssertEqual(repository.bookCalls.first?.value, decimal(232))
        XCTAssertEqual(updates.map(\.id), [card.id])
        XCTAssertEqual(updates.first?.tracking.progress?.value, decimal(232))
        XCTAssertEqual(updates.first?.tracking.progress?.kind, "pages")
        XCTAssertEqual(updates.first?.tracking.progress?.max, decimal(400))
        XCTAssertEqual(origins, [origin])
        XCTAssertNil(actions.busyItemID)
    }

    func testBookPagesNeverReachesTheTotal() async {
        repository.bookResult = bookResponse(pages: 400)
        let actions = makeActions()
        let nearEnd = book("near", pages: 381)
        let reached = await actions.run(.addPages(20), on: nearEnd)
        XCTAssertEqual(reached, .openSheet, "381 + 20 passes 400")
        let exact = await actions.run(.addPages(20), on: book("exact", pages: 380))
        XCTAssertEqual(exact, .openSheet, "380 + 20 is 400, the end")
        XCTAssertTrue(repository.bookCalls.isEmpty)
        XCTAssertTrue(updates.isEmpty)
    }

    func testBookPercentSendsPercentage() async {
        let card = bookPercent("percent", 40)
        repository.bookResult = TrackingState(
            trackingId: 1, status: "In progress", rating: nil,
            progress: ProgressState(kind: "percentage", value: 45, max: 100, unit: "percent"), repeats: nil,
            startDate: nil, endDate: nil, notes: nil, updatedAt: nil,
            book: BookTrackingState(currentJourney: BookJourneyState(id: 1, status: "In progress"))
        )
        let result = await makeActions().run(.addPercent(5), on: card)
        XCTAssertEqual(result, .done)
        XCTAssertEqual(repository.bookCalls.first?.type, "percentage")
        XCTAssertEqual(repository.bookCalls.first?.value, decimal(45))
        XCTAssertEqual(updates.first?.tracking.progress?.kind, "percentage")
        XCTAssertEqual(updates.first?.tracking.progress?.value, decimal(45))
    }

    func testBookPercentKeepsItsDecimalsAndStopsBeforeTheEnd() async {
        let progress = ProgressState(kind: "percentage", value: Decimal(string: "12.34"), max: 100, unit: "percent")
        let card = item("book", "decimals", progress: progress, book: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress)
        ))
        repository.bookResult = bookResponse(pages: 1)
        _ = await makeActions().run(.addPercent(5), on: card)
        XCTAssertEqual(repository.bookCalls.first?.value, Decimal(string: "17.34"))
        let atEnd = await makeActions().run(.addPercent(5), on: bookPercent("end", 96))
        XCTAssertEqual(atEnd, .openSheet)
        XCTAssertEqual(repository.bookCalls.count, 1)
    }

    func testAPlanThatNoLongerMatchesTheCardOpensTheSheet() async {
        // The card now counts percent, so adding pages would write a percent as pages.
        let result = await makeActions().run(.addPages(20), on: bookPercent("mismatch", 40))
        XCTAssertEqual(result, .openSheet)
        XCTAssertTrue(repository.bookCalls.isEmpty)
    }

    // MARK: Games

    func testGameHoursAddsAnHourToTheTotalMinutes() async {
        let card = game("hours", minutes: 14 * 60, percent: nil)
        repository.playthroughResult = gameResponse(minutes: 15 * 60, percent: nil)
        let result = await makeActions().run(.addHours(1), on: card)
        XCTAssertEqual(result, .done)
        XCTAssertEqual(repository.playthroughCalls.count, 1)
        let call = repository.playthroughCalls[0]
        XCTAssertEqual(call.id, 31)
        XCTAssertEqual(call.request.totalMinutes, 15 * 60)
        XCTAssertTrue(call.request.includesMinutes)
        XCTAssertFalse(call.request.includesPercentage)
        XCTAssertEqual(call.request.progressedOn, CalendarDateCodec.string(from: fixedDay))
        XCTAssertEqual(updates.first?.tracking.game?.currentPlaythrough?.totalMinutes, 15 * 60)
    }

    func testGamePercentAddsToTheCurrentPercent() async {
        let card = game("percent", minutes: nil, percent: 30)
        repository.playthroughResult = gameResponse(minutes: nil, percent: 32)
        let result = await makeActions().run(.addPercent(2), on: card)
        XCTAssertEqual(result, .done)
        let call = repository.playthroughCalls[0]
        XCTAssertEqual(call.request.percentage, decimal(32))
        XCTAssertTrue(call.request.includesPercentage)
        XCTAssertFalse(call.request.includesMinutes)
        XCTAssertEqual(call.request.progressedOn, CalendarDateCodec.string(from: fixedDay))
    }

    func testGamePercentNeverReaches100() async {
        let result = await makeActions().run(.addPercent(2), on: game("end", minutes: nil, percent: 98))
        XCTAssertEqual(result, .openSheet)
        XCTAssertTrue(repository.playthroughCalls.isEmpty)
    }

    // MARK: Manga and comics

    func testMangaAddsOneChapter() async {
        let card = manga("chapters", 41)
        repository.seriesResult = mangaResponse(42)
        let origins = await posts(for: card.media.ref) {
            let result = await makeActions().run(.addChapters(1, unit: "chapter"), on: card)
            XCTAssertEqual(result, .done)
        }
        XCTAssertEqual(repository.seriesCalls.map(\.value), [42])
        XCTAssertEqual(updates.first?.tracking.series?.currentJourney?.progress?.value, decimal(42))
        XCTAssertEqual(origins, [origin])
    }

    func testMangaNeverWritesTheTotal() async {
        let result = await makeActions().run(.addChapters(1, unit: "chapter"), on: manga("last", 89))
        XCTAssertEqual(result, .openSheet, "89 + 1 is the total, 90")
        XCTAssertTrue(repository.seriesCalls.isEmpty)
        XCTAssertTrue(updates.isEmpty)
    }

    func testAResponseThatOpensTheLogOpensTheSheetAfterPatchingTheCard() async {
        // The total was unknown when the card was drawn, so the plan wrote; the server says it was the last one.
        let progress = ProgressState(kind: "count", value: 41, max: nil, unit: "chapter")
        let card = item("manga", "opens-log", progress: progress, series: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress), unit: "chapter"
        ))
        repository.seriesResult = mangaResponse(42, opensLog: true)
        let result = await makeActions().run(.addChapters(1, unit: "chapter"), on: card)
        XCTAssertEqual(result, .openSheet)
        XCTAssertEqual(repository.seriesCalls.map(\.value), [42])
        XCTAssertEqual(updates.map(\.id), [card.id])
    }

    func testComicsCountIssues() async {
        let progress = ProgressState(kind: "count", value: 3, max: 12, unit: "issue")
        let card = item("comic", "issues", progress: progress, series: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress), total: 12, unit: "issue"
        ))
        repository.seriesResult = mangaResponse(4, of: 12)
        let result = await makeActions().run(.addChapters(1, unit: "issue"), on: card)
        XCTAssertEqual(result, .done)
        XCTAssertEqual(repository.seriesCalls.map(\.value), [4])
    }

    // MARK: Sheet plans, failure, busy

    func testOpenSheetAndTicksWriteNothing() async {
        let card = book("sheet", pages: 100)
        let actions = makeActions()
        let opens = await actions.run(.openSheet(label: "Update"), on: card)
        XCTAssertEqual(opens, .openSheet)
        let tick = await actions.run(.tickUpNext(season: 1, episode: 2), on: card)
        XCTAssertEqual(tick, .openSheet, "ticks are ContinueWatchingTicks.quickTick's")
        XCTAssertTrue(repository.bookCalls.isEmpty)
        XCTAssertTrue(updates.isEmpty)
    }

    func testAFailureLeavesTheCardAndReturnsTheMessage() async {
        let card = book("fails", pages: 212)
        repository.error = APIError.httpStatus(500, nil)
        let actions = makeActions()
        var result: InProgressQuickActions.Result?
        let origins = await posts(for: card.media.ref) { result = await actions.run(.addPages(20), on: card) }
        XCTAssertEqual(result, .failed(APIError.httpStatus(500, nil).localizedDescription))
        XCTAssertTrue(updates.isEmpty)
        XCTAssertTrue(origins.isEmpty)
        XCTAssertNil(actions.busyItemID)
        XCTAssertEqual(unauthorized, 0)
        // The next tap works.
        repository.error = nil
        repository.bookResult = bookResponse(pages: 232)
        let retried = await actions.run(.addPages(20), on: card)
        XCTAssertEqual(retried, .done)
    }

    func testAnExpiredSessionCallsOnUnauthorized() async {
        repository.error = APIError.unauthorized
        let result = await makeActions().run(.addPages(20), on: book("401", pages: 10))
        XCTAssertEqual(result, .failed(APIError.unauthorized.localizedDescription))
        XCTAssertEqual(unauthorized, 1)
        XCTAssertTrue(updates.isEmpty)
    }

    func testASecondTapWhileAWriteIsOnItsWaySendsNothing() async {
        let card = book("busy", pages: 212)
        repository.bookResult = bookResponse(pages: 232)
        let gate = RefreshGate()
        repository.gate = gate
        let actions = makeActions()
        let first = Task { await actions.run(.addPages(20), on: card) }
        for _ in 0 ..< 50 where actions.busyItemID == nil { await Task.yield() }
        XCTAssertEqual(actions.busyItemID, card.id)
        let second = await actions.run(.addPages(20), on: card)
        XCTAssertEqual(second, .done)
        await gate.open()
        let firstResult = await first.value
        XCTAssertEqual(firstResult, .done)
        XCTAssertEqual(repository.bookCalls.count, 1)
        XCTAssertEqual(updates.count, 1)
        XCTAssertNil(actions.busyItemID, "busy clears when the write lands")
    }
}

private final class PostBox: @unchecked Sendable {
    private let lock = NSLock()
    private var posted: [UUID?] = []

    func add(_ origin: UUID?) { lock.withLock { posted.append(origin) } }
    var origins: [UUID?] { lock.withLock { posted } }
}

/// Records the writes the tile's quick actions and Plan to watch send, and answers them from what a test sets.
@MainActor
final class QuickActionRecordingRepository: TrackingRepository {
    struct PlaythroughCall {
        let id: Int
        let request: GamePlaythroughWriteRequest
    }

    struct PlanCall: Equatable {
        let source: String
        let mediaId: String
        let action: TVAction
    }

    var bookCalls: [(type: String, value: Decimal)] = []
    var playthroughCalls: [PlaythroughCall] = []
    var seriesCalls: [(ref: MediaRef, value: Int)] = []
    var updateCalls: [(ref: MediaRef, status: String?)] = []
    var tvCalls: [PlanCall] = []
    var animeCalls: [PlanCall] = []

    var bookResult: TrackingState?
    var playthroughResult: TrackingState?
    var seriesResult: TrackingState?
    var updateResult: TrackingState?
    /// Fails every write.
    var error: Error?
    /// Holds every write until it opens.
    var gate: RefreshGate?

    private func settle() async throws {
        await gate?.wait()
        if let error { throw error }
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func detail(ref: MediaRef) async throws -> TrackingState { throw APIError.httpStatus(404, nil) }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        bookCalls.append((progressType, value))
        try await settle()
        guard let bookResult else { throw APIError.httpStatus(500, nil) }
        return bookResult
    }

    func updateGamePlaythrough(ref: MediaRef, playthroughId: Int, request: GamePlaythroughWriteRequest) async throws -> TrackingState {
        playthroughCalls.append(PlaythroughCall(id: playthroughId, request: request))
        try await settle()
        guard let playthroughResult else { throw APIError.httpStatus(500, nil) }
        return playthroughResult
    }

    func updateSeriesProgress(ref: MediaRef, value: Int, mutationId: UUID) async throws -> TrackingState {
        seriesCalls.append((ref, value))
        try await settle()
        guard let seriesResult else { throw APIError.httpStatus(500, nil) }
        return seriesResult
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        updateCalls.append((ref, request.status))
        try await settle()
        guard let updateResult else {
            return TrackingState(
                trackingId: 1, status: request.status, rating: nil, progress: nil, repeats: nil, startDate: nil,
                endDate: nil, notes: nil, updatedAt: nil
            )
        }
        return updateResult
    }

    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        tvCalls.append(PlanCall(source: source, mediaId: mediaId, action: action))
        try await settle()
        return TVActionResponse(tracking: nil, envelope: .empty)
    }

    func performAnimeAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        animeCalls.append(PlanCall(source: source, mediaId: mediaId, action: action))
        try await settle()
        return TVActionResponse(tracking: nil, envelope: .empty)
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}
