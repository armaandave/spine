import XCTest
@testable import Spine

/// The viewer's state for the Trending titles in focus: what loads and when, what is cached, and what a write posts.
@MainActor
final class TrendingRailStoreTests: XCTestCase {
    private var media: RailMediaRepository!
    private var tracking: RailTrackingRepository!
    private var clock = Date(timeIntervalSince1970: 1_000_000)
    private var sleeps: [Duration] = []
    private var sleepGate: RefreshGate?
    private var unauthorized = 0
    private var stores: [TrendingRailStore] = []

    override func setUp() async throws {
        try await super.setUp()
        media = RailMediaRepository()
        tracking = RailTrackingRepository()
        clock = Date(timeIntervalSince1970: 1_000_000)
        sleeps = []
        sleepGate = nil
        unauthorized = 0
        stores = []
    }

    // MARK: Fixtures

    private func ref(_ id: String, type: String = "movie", source: String = "tmdb") -> MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: type, mediaId: id, seasonNumber: nil, episodeNumber: nil)
    }

    private func title(_ id: String, type: String = "movie", source: String = "tmdb") -> TrendingTitle {
        let media = MediaSummary(ref: ref(id, type: type, source: source), title: "Title \(id)", posterUrl: "https://p/\(id).jpg", posterOrientation: .portrait)
        return TrendingTitle(key: FeedEntries.titleKey(for: media.ref), media: media, people: [], includesYou: false, averageRating: nil, lastDay: nil)
    }

    private func serve(_ title: TrendingTitle, state: UserMediaState?) {
        media.details[title.media.ref.id] = MediaDetail(ref: title.media.ref, title: title.media.title, userState: state)
    }

    private func makeStore() -> TrendingRailStore {
        let store = TrendingRailStore(
            mediaRepository: media,
            trackingRepository: tracking,
            diaryRepository: RailUnusedDiaryRepository(),
            onUnauthorized: { [unowned self] in unauthorized += 1 },
            now: { [unowned self] in clock },
            sleep: { [unowned self] duration in
                sleeps.append(duration)
                await sleepGate?.wait()
            }
        )
        stores.append(store)
        return store
    }

    private func waitUntil(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0 ..< 300 where !condition() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(condition(), "Timed out waiting for the condition.", file: file, line: line)
    }

    private func isReady(_ store: TrendingRailStore, _ title: TrendingTitle) -> Bool {
        store.entry(for: title.key)?.isReady == true
    }

    private var trackedState: UserMediaState {
        UserMediaState(isTracked: true, trackingId: 1, status: "In progress")
    }

    // MARK: What loads

    func testFocusLoadsTheDetailThenTheTrackingOfATrackedTitle() async {
        let tracked = title("1")
        serve(tracked, state: trackedState)
        tracking.states["tmdb:movie:1:_:_"] = TrackingState(
            trackingId: 1, status: "Completed", rating: "4.5", progress: nil, repeats: nil, startDate: nil,
            endDate: nil, notes: nil, updatedAt: nil, liked: true
        )
        let store = makeStore()

        store.focus(tracked)
        XCTAssertEqual(store.entry(for: tracked.key)?.phase, .loading, "The skeleton shows until both have come")
        await waitUntil { self.isReady(store, tracked) }

        XCTAssertEqual(media.detailRequests.map(\.id), ["tmdb:movie:1:_:_"])
        XCTAssertEqual(tracking.detailRequests.map(\.id), ["tmdb:movie:1:_:_"])
        let viewer = store.entry(for: tracked.key)?.viewModel.viewer
        XCTAssertEqual(viewer?.status, "Completed", "The tracking row is merged in, as the page does")
        XCTAssertEqual(viewer?.rating, "4.5")
        XCTAssertEqual(viewer?.hasLiked, true)
        XCTAssertEqual(store.entry(for: tracked.key)?.picker.confirmedHalfSteps, 9, "The picker shows the server's rating")
    }

    func testAnUntrackedTitleLoadsOnlyTheDetail() async {
        let fresh = title("2")
        serve(fresh, state: .untracked)
        let store = makeStore()
        store.focus(fresh)
        await waitUntil { self.isReady(store, fresh) }
        XCTAssertEqual(media.detailRequests.count, 1)
        XCTAssertTrue(tracking.detailRequests.isEmpty)
        XCTAssertEqual(store.entry(for: fresh.key)?.viewModel.viewer, UserMediaState.untracked)
    }

    func testAGameSkipsTheTrackingRead() async {
        let game = title("3", type: "game", source: "igdb")
        serve(game, state: trackedState)
        let store = makeStore()
        store.focus(game)
        await waitUntil { self.isReady(store, game) }
        XCTAssertTrue(tracking.detailRequests.isEmpty, "Games carry everything in the detail")
    }

    func testAMissingTrackingRowLeavesTheDetailsStateAlone() async {
        let tracked = title("4")
        serve(tracked, state: trackedState)
        let store = makeStore()
        store.focus(tracked)
        await waitUntil { self.isReady(store, tracked) }
        XCTAssertEqual(tracking.detailRequests.count, 1)
        XCTAssertEqual(store.entry(for: tracked.key)?.viewModel.viewer?.status, "In progress")
    }

    // MARK: Neighbors

    func testNeighborsLoadAfterTheDelayAndNotDuringAFling() async {
        let (a, b, c, d) = (title("a"), title("b"), title("c"), title("d"))
        for t in [a, b, c, d] { serve(t, state: .untracked) }
        let gate = RefreshGate()
        sleepGate = gate
        let store = makeStore()

        store.focus(a, neighbors: [b, c])
        await waitUntil { self.isReady(store, a) }
        await waitUntil { !self.sleeps.isEmpty }
        XCTAssertEqual(sleeps, [.milliseconds(250)])
        XCTAssertEqual(media.detailRequests.map(\.mediaId), ["a"], "Nothing else while the focus is still fresh")

        // A fling: the focus moves on before the delay is up, so the first focus's neighbors are never asked for.
        store.focus(d)
        await gate.open()
        await waitUntil { self.isReady(store, d) }
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(Set(media.detailRequests.map(\.mediaId)), ["a", "d"])
    }

    func testNeighborsLoadOneAfterTheOtherOnceTheFocusHoldsStill() async {
        let (a, b, c) = (title("a"), title("b"), title("c"))
        for t in [a, b, c] { serve(t, state: .untracked) }
        let store = makeStore()
        store.focus(a, neighbors: [b, c])
        await waitUntil { self.isReady(store, b) && self.isReady(store, c) }
        XCTAssertEqual(media.detailRequests.map(\.mediaId), ["a", "b", "c"], "The focus first, then next, then previous")
    }

    func testAJustOpenedTileLoadsTheNeighborWithTheFocus() async {
        let (a, b) = (title("a"), title("b"))
        for t in [a, b] { serve(t, state: .untracked) }
        sleepGate = RefreshGate()
        let store = makeStore()
        store.focus(a, neighbors: [b], neighborDelay: .zero)
        await waitUntil { self.isReady(store, b) }
        XCTAssertTrue(sleeps.isEmpty, "No wait")
        // The panel then asks for the same focus with the usual delay: nothing changes.
        store.focus(a, neighbors: [b])
        XCTAssertEqual(media.detailRequests.count, 2)
    }

    // MARK: In flight, fresh and stale

    func testAnInFlightTitleIsNeverRequestedTwice() async {
        let a = title("a")
        serve(a, state: .untracked)
        let gate = RefreshGate()
        media.gate = gate
        let store = makeStore()
        store.focus(a)
        store.focus(a)
        store.focus(a)
        await waitUntil { self.media.detailRequests.count == 1 }
        await gate.open()
        await waitUntil { self.isReady(store, a) }
        XCTAssertEqual(media.detailRequests.count, 1)
    }

    func testAFreshEntryIsNotFetchedAgainButAStaleOneIs() async {
        let a = title("a")
        serve(a, state: .untracked)
        let store = makeStore()
        store.focus(a)
        await waitUntil { self.isReady(store, a) }

        clock = clock.addingTimeInterval(59)
        store.focus(a)
        XCTAssertEqual(store.entry(for: a.key)?.isReady, true, "Inside 60 s it shows with no skeleton")
        XCTAssertEqual(media.detailRequests.count, 1)

        clock = clock.addingTimeInterval(2)
        store.focus(a)
        XCTAssertEqual(store.entry(for: a.key)?.phase, .loading, "Older than 60 s shows the skeleton")
        await waitUntil { self.media.detailRequests.count == 2 && self.isReady(store, a) }
    }

    func testAFailureIsFailedAndARefocusRetries() async {
        let a = title("a")
        serve(a, state: .untracked)
        media.error = APIError.httpStatus(503, nil)
        let store = makeStore()
        store.focus(a)
        await waitUntil { store.entry(for: a.key)?.phase == .failed }
        XCTAssertEqual(unauthorized, 0)

        media.error = nil
        store.focus(a)
        await waitUntil { self.isReady(store, a) }
        XCTAssertEqual(media.detailRequests.count, 2)
    }

    func testAnUnauthorizedAnswerSignsOutLikeEveryOtherScreen() async {
        let a = title("a")
        media.error = APIError.unauthorized
        let store = makeStore()
        store.focus(a)
        await waitUntil { store.entry(for: a.key)?.phase == .failed }
        XCTAssertEqual(unauthorized, 1)
    }

    // MARK: The cache

    func testFourEntriesStayAndTheLeastRecentlyFocusedGoesFirst() async {
        let titles = (1 ... 5).map { title("t\($0)") }
        for t in titles { serve(t, state: .untracked) }
        let store = makeStore()
        for t in titles.prefix(4) {
            store.focus(t)
            await waitUntil { self.isReady(store, t) }
        }
        XCTAssertEqual(store.entries.count, 4)

        store.focus(titles[0])
        store.focus(titles[4])
        await waitUntil { self.isReady(store, titles[4]) }
        XCTAssertEqual(store.entries.count, 4)
        XCTAssertNil(store.entry(for: titles[1].key), "The one focused longest ago went")
        XCTAssertNotNil(store.entry(for: titles[0].key), "Refocusing kept the first")
        XCTAssertNotNil(store.entry(for: titles[4].key))
    }

    func testATitleWithAWriteOnItsWayIsNotEvicted() async {
        let titles = (1 ... 6).map { title("t\($0)") }
        for t in titles { serve(t, state: .untracked) }
        let store = makeStore()
        store.focus(titles[0])
        await waitUntil { self.isReady(store, titles[0]) }
        let gate = RefreshGate()
        tracking.updateGate = gate
        let entry = try? XCTUnwrap(store.entry(for: titles[0].key))
        let detail = try? XCTUnwrap(entry?.viewModel.detail)
        if let entry, let detail { entry.viewModel.performQuickAction(.planning, for: detail) }

        for t in titles.dropFirst() {
            store.focus(t)
            await waitUntil { self.isReady(store, t) }
        }
        XCTAssertNotNil(store.entry(for: titles[0].key), "Its write must land on its own model")
        await gate.open()
    }

    // MARK: Changes and writes

    func testAChangeFromAnotherOriginRefreshesAndTheModelsOwnDoesNot() async {
        let a = title("a")
        serve(a, state: .untracked)
        let store = makeStore()
        store.focus(a)
        await waitUntil { self.isReady(store, a) }
        XCTAssertEqual(media.detailRequests.count, 1)
        let origin = try? XCTUnwrap(store.entry(for: a.key)?.viewModel.changeOrigin)

        MediaStateChange.post(ref: a.media.ref, origin: origin)
        try? await Task.sleep(for: .milliseconds(700))
        XCTAssertEqual(media.detailRequests.count, 1, "The model's own post is already applied")

        MediaStateChange.post(ref: a.media.ref, origin: UUID())
        await waitUntil { self.media.detailRequests.count == 2 }
    }

    func testAWritePostsTheModelsOriginAndTheStoreKnowsIt() async throws {
        let a = title("a")
        serve(a, state: .untracked)
        tracking.updateResult = TrackingState(
            trackingId: 5, status: "Planning", rating: nil, progress: nil, repeats: nil, startDate: nil,
            endDate: nil, notes: nil, updatedAt: nil
        )
        let store = makeStore()
        store.focus(a)
        await waitUntil { self.isReady(store, a) }
        let entry = try XCTUnwrap(store.entry(for: a.key))
        let detail = try XCTUnwrap(entry.viewModel.detail)
        var origins: [UUID] = []
        let observer = NotificationCenter.default.addObserver(forName: .mediaStateDidChange, object: nil, queue: .main) { note in
            if let origin = note.userInfo?["origin"] as? UUID { origins.append(origin) }
        }
        defer { NotificationCenter.default.removeObserver(observer) }

        let saved = await entry.viewModel.performQuickAction(.planning, for: detail).value

        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.updates.map(\.status), ["Planning"])
        XCTAssertEqual(origins, [entry.viewModel.changeOrigin])
        XCTAssertTrue(store.changeOrigins.contains(entry.viewModel.changeOrigin))
        XCTAssertEqual(entry.viewModel.viewer?.status, "Planning")
    }

    func testAnEvictedModelsOriginIsStillOurs() async {
        let titles = (1 ... 6).map { title("t\($0)") }
        for t in titles { serve(t, state: .untracked) }
        let store = makeStore()
        store.focus(titles[0])
        await waitUntil { self.isReady(store, titles[0]) }
        let first = store.entry(for: titles[0].key)?.viewModel.changeOrigin
        for t in titles.dropFirst() {
            store.focus(t)
            await waitUntil { self.isReady(store, t) }
        }
        XCTAssertNil(store.entry(for: titles[0].key))
        XCTAssertTrue(first.map { store.changeOrigins.contains($0) } ?? false, "A late post from it must not reload Home")
    }

    func testMessagesFromAnyModelAreTakenOnceAndCleared() async throws {
        let a = title("a")
        serve(a, state: .untracked)
        let store = makeStore()
        store.focus(a)
        await waitUntil { self.isReady(store, a) }
        let model = try XCTUnwrap(store.entry(for: a.key)?.viewModel)
        model.tvNotice = "This episode hasn't aired yet."
        model.quickActionErrorMessage = "Offline"
        XCTAssertEqual(Set(store.pendingMessages), ["This episode hasn't aired yet.", "Offline"])
        XCTAssertEqual(Set(store.takeMessages()), ["This episode hasn't aired yet.", "Offline"])
        XCTAssertTrue(store.pendingMessages.isEmpty)
        XCTAssertNil(model.tvNotice)
    }

    func testTheRatingPickerClosesWhenAskedAndTheTileClosingStopsNeighbors() async {
        let (a, b) = (title("a"), title("b"))
        for t in [a, b] { serve(t, state: .untracked) }
        let gate = RefreshGate()
        sleepGate = gate
        let store = makeStore()
        store.focus(a, neighbors: [b])
        await waitUntil { self.isReady(store, a) }
        store.entry(for: a.key)?.picker.open()
        XCTAssertEqual(store.entry(for: a.key)?.picker.isPresented, true)
        store.entry(for: a.key)?.picker.draftHalfSteps = 3
        store.dismissPicker(for: a.key)
        XCTAssertEqual(store.entry(for: a.key)?.picker.isPresented, false)
        XCTAssertEqual(store.entry(for: a.key)?.picker.draftHalfSteps, store.entry(for: a.key)?.picker.confirmedHalfSteps,
                       "Closing the stars from outside leaves the rating as it was")

        store.tileClosed()
        await gate.open()
        try? await Task.sleep(for: .milliseconds(80))
        XCTAssertEqual(media.detailRequests.map(\.mediaId), ["a"], "A closed tile fetches nothing more")
    }
}

// MARK: - Fakes

@MainActor
final class RailMediaRepository: MediaRepository {
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
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

@MainActor
final class RailTrackingRepository: TrackingRepository {
    /// Tracking rows by ref id; anything else is a 404.
    var states: [String: TrackingState] = [:]
    var updateResult: TrackingState?
    var updateGate: RefreshGate?
    private(set) var detailRequests: [MediaRef] = []
    private(set) var updates: [(ref: MediaRef, status: String?)] = []

    func detail(ref: MediaRef) async throws -> TrackingState {
        detailRequests.append(ref)
        guard let state = states[ref.id] else { throw APIError.httpStatus(404, nil) }
        return state
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        updates.append((ref, request.status))
        await updateGate?.wait()
        guard let updateResult else { throw APIError.httpStatus(500, nil) }
        return updateResult
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private struct RailUnusedDiaryRepository: DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}
