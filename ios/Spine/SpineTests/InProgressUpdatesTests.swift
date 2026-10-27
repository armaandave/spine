import XCTest
@testable import Spine

/// "Update progress ›" on Home's and Profile's In progress cards (TV-701): one line for every media type, opening the
/// title's own page's progress UI over the screen, on the page's model and write paths.
@MainActor
final class InProgressUpdatesTests: XCTestCase {
    private var tracking: UpdatesTrackingRepository!
    private var media: SurfacesMediaRepository!

    override func setUp() async throws {
        try await super.setUp()
        tracking = UpdatesTrackingRepository()
        media = SurfacesMediaRepository()
    }

    // MARK: Where each card goes

    func testEveryTypeOpensItsPagesProgressUIOrItsPage() {
        let show = Self.item("tv", "1396", "Breaking Bad", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 8, aired: 62, upNext: (2, 2))
        ))
        let caughtUp = Self.item("tv", "1", "Caught Up", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", caughtUp: true)
        ))
        XCTAssertEqual(InProgressUpdates.route(for: show), .episodes)
        XCTAssertEqual(InProgressUpdates.route(for: caughtUp), .page, "No up next to open until the rail reloads")
        for mediaType in ["book", "game", "manga", "comic"] {
            XCTAssertEqual(InProgressUpdates.route(for: Self.item(mediaType, "7", "Title")), .progress, mediaType)
        }
        // Their pages have no progress UI: the anime page logs, movies and albums are watched or listened to whole.
        for mediaType in ["anime", "movie", "music"] {
            XCTAssertEqual(InProgressUpdates.route(for: Self.item(mediaType, "7", "Title")), .page, mediaType)
        }
    }

    func testVoiceOverKeepsWhereTheTitleStandsThatTheCardNoLongerShows() {
        let show = Self.item("tv", "1396", "Breaking Bad", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 8, aired: 62, upNext: (2, 2))
        ))
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: show),
                       "Breaking Bad, update progress, S2 E2 next, 8 of 62")
        let book = Self.item("book", "427578", "Project Hail Mary", tracking: Self.tracking(
            progress: ProgressState(kind: "pages", value: 40, max: 476, unit: "page")
        ))
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: book),
                       "Project Hail Mary, update progress, 40 of 476 pages")
        let game = Self.item("game", "113112", "Hades", tracking: Self.tracking(
            game: Self.game(playthrough: Self.playthrough(minutes: 720, percentage: 58))
        ))
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: game), "Hades, update progress, 58%, 12h")
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: Self.item("anime", "1", "Cowboy Bebop")),
                       "Cowboy Bebop, update progress")
    }

    // MARK: Books: the page's progress panel

    func testABooksCardOpensItsPagesPanelAndSavesProgressWithThePagesOneRequest() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        tracking.bookProgressResult = Self.bookTracking(status: "In progress", pages: 120)
        let home = homeViewModel(items: [book])

        let opensPage = await home.progressUpdates.updateProgress(for: book)

        XCTAssertNil(opensPage, "The panel opens over Home")
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertEqual(update.bookPanel?.ref, book.media.ref)
        XCTAssertEqual(media.detailRequests, [book.media.ref], "The page's own load")
        XCTAssertEqual(update.page.changeOrigin, home.continueWatching.changeOrigin,
                       "Its writes post as Home's own, so the card stays where it is")
        XCTAssertEqual(home.progressUpdates.openings, 1)
        XCTAssertFalse(home.progressUpdates.needsReload)

        let saved = await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 120))

        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.bookProgressCalls.map(\.value), [120], "One request")
        XCTAssertEqual(update.page.viewer?.progress?.value, 120)
        XCTAssertTrue(home.progressUpdates.needsReload, "The rail re-sorts on the next visit")
        XCTAssertTrue(home.inProgressNeedsReload)
        XCTAssertEqual(home.inProgressItems.map(\.id), [book.id], "The card stays where it is")
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: home.inProgressItems[0]),
                       "Project Hail Mary, update progress, 120 of 476 pages", "VoiceOver reads the new page")
        XCTAssertTrue(tracking.listRequests.isEmpty, "Nothing reloads the rail now")
    }

    func testFinishingABookFromThePanelOpensThePagesCompletionLog() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        // Finished in the panel's status row, or progress saved at the last page.
        update.openCompletion()

        XCTAssertNil(update.bookPanel, "The panel closes first")
        await waitUntil { update.composer != nil }
        XCTAssertEqual(update.composer?.mode, .finished)
        XCTAssertEqual(update.composer?.completionJourneyId, 9, "The log finishes the current journey")
    }

    func testAFailedPauseFromThePanelShowsAtOnceThenRollsBackAndSaysSo() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        tracking.bookActionError = APIError.httpStatus(500, nil)
        tracking.bookActionGate = RefreshGate()
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        update.performBookAction("pause")
        update.closeBookPanel()

        XCTAssertEqual(update.page.viewer?.status, "Paused", "Shows at once")
        await tracking.bookActionGate?.open()
        _ = await update.page.settled()
        XCTAssertEqual(tracking.bookActions, ["pause"], "One request")
        XCTAssertEqual(update.page.viewer?.status, "In progress", "Rolled back")
        XCTAssertNotNil(update.page.quickActionErrorMessage)
        XCTAssertTrue(update.showsFailure, "The page's alert, over Home")
        XCTAssertFalse(home.progressUpdates.needsReload, "Nothing changed")
    }

    func testABookThatIsntBeingReadOpensItsPage() async {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "Completed", pages: 476)
        let home = homeViewModel(items: [book])

        let opensPage = await home.progressUpdates.updateProgress(for: book)

        XCTAssertEqual(opensPage, book.media.ref, "Its page has no Update Progress either")
        XCTAssertNil(home.progressUpdates.update)
    }

    // MARK: Games: the page's progress sheet

    func testAGamesCardOpensItsPagesSheetWithEditDatesAndFinishFollowingTheSheet() async throws {
        let game = Self.item("game", "113112", "Hades")
        let playthrough = Self.playthrough(minutes: 120, percentage: nil)
        media.details[game.media.ref.id] = MediaDetail(
            ref: game.media.ref,
            title: "Hades",
            userState: UserMediaState(isTracked: true, status: "In progress", game: Self.game(playthrough: playthrough))
        )
        tracking.playthroughResult = Self.tracking(
            status: "In progress", game: Self.game(playthrough: Self.playthrough(minutes: 300, percentage: nil))
        )
        let home = homeViewModel(items: [game])

        let opensPage = await home.progressUpdates.updateProgress(for: game)

        XCTAssertNil(opensPage)
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertEqual(update.gameProgress, playthrough)

        var request = GamePlaythroughWriteRequest(progressedOn: "2026-10-27")
        request.totalMinutes = 300
        request.includesMinutes = true
        let saved = await update.saveGame(playthrough, request)
        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.playthroughUpdates.map(\.id), [playthrough.id], "One request, for its playthrough")
        XCTAssertEqual(update.page.viewer?.game?.currentPlaythrough?.totalMinutes, 300)
        XCTAssertTrue(home.progressUpdates.needsReload)
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: home.inProgressItems[0]),
                       "Hades, update progress, 5h", "VoiceOver reads the new hours")

        // Edit Dates: the playthrough editor once the sheet has gone.
        update.editDates(playthrough)
        XCTAssertNil(update.gameProgress)
        XCTAssertNil(update.gameDates, "Not while the sheet slides away")
        update.sheetDidClose()
        XCTAssertEqual(update.gameDates?.id, playthrough.id)

        // Finish from the editor: the completion log follows the editor's dismissal.
        update.openCompletion()
        XCTAssertNil(update.gameDates)
        XCTAssertTrue(update.isClosing)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertNil(update.composer, "Not while the editor slides away")
        update.sheetDidClose()
        XCTAssertEqual(update.composer?.mode, .finished)
        XCTAssertEqual(update.composer?.completionJourneyId, playthrough.id, "The log finishes the live playthrough")
    }

    // MARK: A card still waiting for its page

    func testAnotherCardTappedWhileAPageLoadsOpensInsteadOfIt() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        let show = Self.item("tv", "1396", "Breaking Bad", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 8, aired: 62, upNext: (2, 2))
        ))
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [book, show])

        let bookTap = Task { await home.progressUpdates.updateProgress(for: book) == nil }
        await waitUntil { home.progressUpdates.loadingItemID == book.id }
        let showOpensPage = await home.progressUpdates.updateProgress(for: show)

        XCTAssertNil(showOpensPage)
        XCTAssertNotNil(home.continueWatching.episodes, "The show's sheet opens")
        XCTAssertNil(home.progressUpdates.loadingItemID, "The book's card no longer waits")
        await gate.open()
        let bookOpensNothing = await bookTap.value
        XCTAssertTrue(bookOpensNothing, "Nor the book's page")
        XCTAssertNil(home.progressUpdates.update, "The book's panel doesn't open over the sheet")
        XCTAssertEqual(home.progressUpdates.openings, 0)
    }

    func testSomethingElseTheScreenOpensCancelsACardStillWaitingForItsPage() async throws {
        let game = Self.item("game", "113112", "Hades")
        media.details[game.media.ref.id] = MediaDetail(
            ref: game.media.ref,
            title: "Hades",
            userState: UserMediaState(isTracked: true, status: "In progress",
                                      game: Self.game(playthrough: Self.playthrough(minutes: 120, percentage: nil)))
        )
        let unread = Self.item("book", "1", "Unread")
        media.details[unread.media.ref.id] = Self.bookDetail(unread.media.ref, status: "Completed", pages: 476)
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [game, unread])

        // The game's line, then its poster (or anything else Home opens, or leaving Home).
        let gameTap = Task { await home.progressUpdates.updateProgress(for: game) == nil }
        await waitUntil { home.progressUpdates.loadingItemID == game.id }
        home.progressUpdates.cancelPendingOpen()
        XCTAssertNil(home.progressUpdates.loadingItemID)
        // A finished book's line would open its page once loaded.
        let unreadTap = Task { await home.progressUpdates.updateProgress(for: unread) == nil }
        await waitUntil { home.progressUpdates.loadingItemID == unread.id }
        home.progressUpdates.cancelPendingOpen()
        await gate.open()

        let gameOpensNothing = await gameTap.value
        let unreadOpensNothing = await unreadTap.value
        XCTAssertTrue(gameOpensNothing)
        XCTAssertTrue(unreadOpensNothing, "Not even the page")
        XCTAssertNil(home.progressUpdates.update, "The game's sheet doesn't open over its page")
        XCTAssertEqual(home.progressUpdates.openings, 0)
    }

    // MARK: Manga and comics: the page's log composer, on Progress

    func testAMangaCardOpensThePagesComposerOnProgress() async throws {
        let manga = Self.item("manga", "2", "Berserk")
        media.details[manga.media.ref.id] = MediaDetail(
            ref: manga.media.ref,
            title: "Berserk",
            userState: UserMediaState(isTracked: true, status: "In progress")
        )
        let home = homeViewModel(items: [manga])

        let opensPage = await home.progressUpdates.updateProgress(for: manga)

        XCTAssertNil(opensPage)
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertEqual(update.composer?.mode, .progress)
        XCTAssertNil(update.composer?.completionJourneyId)
        let composer = MediaLogViewModel(
            detail: try XCTUnwrap(update.page.detail),
            trackingRepository: tracking,
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: {},
            onSaved: {},
            mode: .progress
        )
        XCTAssertEqual(composer.mode, .progress, "The composer opens on Progress")
        let bookComposer = MediaLogViewModel(
            detail: Self.bookDetail(Self.ref("book", "1"), status: "In progress", pages: 1),
            trackingRepository: tracking,
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: {},
            onSaved: {},
            mode: .progress
        )
        XCTAssertEqual(bookComposer.mode, .finished, "Only where the composer offers Progress")
    }

    func testAnAnimeCardOpensItsPageWithoutLoadingIt() async {
        let anime = Self.item("anime", "1", "Cowboy Bebop")
        let home = homeViewModel(items: [anime])

        let opensPage = await home.progressUpdates.updateProgress(for: anime)

        XCTAssertEqual(opensPage, anime.media.ref)
        XCTAssertTrue(media.detailRequests.isEmpty)
        XCTAssertNil(home.progressUpdates.update)
    }

    func testProfilesCardsOpenTheSameProgressUI() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "Paused", pages: 40)
        let profile = ProfileViewModel(
            profileRepository: SurfacesProfileRepository(profile: TVTrackingSurfacesTests.profile(enabled: ["book"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: {}
        )

        let opensPage = await profile.progressUpdates.updateProgress(for: book)

        XCTAssertNil(opensPage, "A paused book's panel opens too, as on its page")
        let update = try XCTUnwrap(profile.progressUpdates.update)
        XCTAssertNotNil(update.bookPanel)
        XCTAssertEqual(update.page.changeOrigin, profile.continueWatching.changeOrigin)
    }

    func testTheCardsReadOneLineInTheNormalWidth() throws {
        // One "Update progress ›" for every type: no per-show line, no condensed width, no count on the card.
        let features = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features", isDirectory: true)
        for path in ["Home/ContinueWatching.swift", "Home/HomeView.swift", "Home/InProgressUpdates.swift",
                     "Profile/ProfileView.swift"] {
            let source = try String(contentsOf: features.appendingPathComponent(path), encoding: .utf8)
            XCTAssertFalse(source.contains("fontWidth(.condensed)"), "\(path) squeezes the line")
            XCTAssertFalse(source.contains("ContinueWatchingLine("), "\(path) still has the show's own line")
        }
        for path in ["Home/HomeView.swift", "Profile/ProfileView.swift"] {
            let source = try String(contentsOf: features.appendingPathComponent(path), encoding: .utf8)
            XCTAssertTrue(source.contains("InProgressCard("), "\(path) builds its own cards")
        }
    }

    // MARK: Fixtures

    private func homeViewModel(items: [Spine.LibraryItem]) -> HomeViewModel {
        let home = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: TVTrackingSurfacesTests.profile(enabled: ["tv", "book"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: {}
        )
        home.inProgressItems = items
        return home
    }

    private func waitUntil(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<400 where !condition() {
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertTrue(condition(), "Timed out waiting", file: file, line: line)
    }

    static func ref(_ mediaType: String, _ id: String) -> MediaRef {
        MediaRef(itemId: nil, source: mediaType == "book" ? "hardcover" : "igdb", mediaType: mediaType, mediaId: id,
                 seasonNumber: nil, episodeNumber: nil)
    }

    static func item(_ mediaType: String, _ id: String, _ title: String, tracking: TrackingState? = nil) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref(mediaType, id), title: title, posterUrl: nil, posterOrientation: .portrait),
            tracking: tracking ?? Self.tracking()
        )
    }

    static func tracking(
        status: String = "In progress",
        progress: ProgressState? = nil,
        book: BookTrackingState? = nil,
        game: GameTrackingState? = nil,
        tv: TVShowState? = nil
    ) -> TrackingState {
        TrackingState(trackingId: 1, status: status, rating: nil, progress: progress, repeats: nil, startDate: nil,
                      endDate: nil, notes: nil, updatedAt: nil, book: book, game: game, tv: tv)
    }

    static func journey(status: String, pages: Int) -> BookJourneyState {
        BookJourneyState(id: 9, status: status, origin: "live", startDate: "2026-10-20",
                         progress: ProgressState(kind: "pages", value: Decimal(pages), max: 476, unit: "page"))
    }

    static func bookTracking(status: String, pages: Int) -> TrackingState {
        tracking(status: status, progress: ProgressState(kind: "pages", value: Decimal(pages), max: 476, unit: "page"),
                 book: BookTrackingState(currentJourney: journey(status: status, pages: pages)))
    }

    static func bookDetail(_ ref: MediaRef, status: String, pages: Int) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: "Project Hail Mary",
            userState: UserMediaState(
                isTracked: true,
                status: status,
                progress: ProgressState(kind: "pages", value: Decimal(pages), max: 476, unit: "page"),
                book: BookTrackingState(currentJourney: journey(status: status, pages: pages))
            ),
            details: ["number_of_pages": .number(476)]
        )
    }

    static func playthrough(minutes: Int?, percentage: Int?) -> GamePlaythroughState {
        GamePlaythroughState(id: 31, status: "In progress", origin: "live", startDate: "2026-10-26", endDate: nil,
                             totalMinutes: minutes, percentage: percentage, completionDiaryEntryId: nil, isReplay: false)
    }

    static func game(playthrough: GamePlaythroughState) -> GameTrackingState {
        GameTrackingState(
            currentPlaythrough: playthrough, playHistory: [], undatedCompletion: nil, statusSource: nil,
            completionDates: [], completedPlaythroughCount: 0, lifetimeCompletionCount: 0, isReplaying: false,
            canRemoveTracking: false, availableActions: [], actionReasons: [:], importedLifetimeMinutes: nil,
            importedLifetimeSource: nil
        )
    }
}

/// Answers the book and game writes a card's progress UI sends.
@MainActor
final class UpdatesTrackingRepository: TrackingRepository {
    struct PlaythroughUpdate {
        let id: Int
        let request: GamePlaythroughWriteRequest
    }

    var listRequests: [String] = []
    var bookProgressCalls: [(type: String, value: Decimal)] = []
    var bookProgressResult: TrackingState?
    var bookActions: [String] = []
    var bookActionError: Error?
    /// Holds each book action's answer until it opens.
    var bookActionGate: RefreshGate?
    var playthroughUpdates: [PlaythroughUpdate] = []
    var playthroughResult: TrackingState?

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listRequests.append(mediaType)
        return PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func continueWatching(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listRequests.append("tv")
        return PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    /// The page's tracking merge: nothing newer than its payload.
    func detail(ref: MediaRef) async throws -> TrackingState { throw APIError.httpStatus(404, nil) }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        bookProgressCalls.append((progressType, value))
        guard let bookProgressResult else { throw APIError.httpStatus(500, nil) }
        return bookProgressResult
    }

    func performBookAction(source: String, mediaId: String, action: String, request: BookActionRequest) async throws -> TrackingState? {
        bookActions.append(action)
        await bookActionGate?.wait()
        if let bookActionError { throw bookActionError }
        return nil
    }

    func updateGamePlaythrough(ref: MediaRef, playthroughId: Int, request: GamePlaythroughWriteRequest) async throws -> TrackingState {
        playthroughUpdates.append(PlaythroughUpdate(id: playthroughId, request: request))
        guard let playthroughResult else { throw APIError.httpStatus(500, nil) }
        return playthroughResult
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}
