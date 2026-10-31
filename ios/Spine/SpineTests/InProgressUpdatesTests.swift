import XCTest
@testable import Spine

/// "Update progress ›" on Home's In progress cards (TV-701): one line for every media type, opening the title's own
/// page's progress UI over Home, on the page's model and write paths. Profile's In progress rail is posters only.
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

    // MARK: The bar on the poster

    func testTheBarFillsToHowFarAlongEachTypeIs() throws {
        func fraction(_ item: Spine.LibraryItem) -> Double? { InProgressCardPresentation.progressFraction(for: item) }

        // A show: distinct aired regular episodes watched over aired (TV-104), from the row's stored counts.
        let show = Self.item("tv", "1396", "Breaking Bad", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 8, aired: 62, upNext: (2, 2))
        ))
        XCTAssertEqual(try XCTUnwrap(fraction(show)), 8.0 / 62, accuracy: 0.0001)
        let unknownListing = Self.item("tv", "2", "Unknown", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", upNext: (1, 2))
        ))
        XCTAssertNil(fraction(unknownListing), "No stored count, no invented bar")

        // A book: its current journey's pages, or its percent.
        let pages = Self.item("book", "1", "Pages", tracking: Self.bookTracking(status: "In progress", pages: 119))
        XCTAssertEqual(try XCTUnwrap(fraction(pages)), 119.0 / 476, accuracy: 0.0001)
        let percent = Self.item("book", "2", "Percent", tracking: Self.tracking(
            progress: ProgressState(kind: "percentage", value: 35, max: 100, unit: "percent"),
            book: BookTrackingState(currentJourney: BookJourneyState(
                id: 9, status: "In progress", progress: ProgressState(kind: "percentage", value: 35, max: 100, unit: "percent")
            ))
        ))
        XCTAssertEqual(try XCTUnwrap(fraction(percent)), 0.35, accuracy: 0.0001)
        let pagesOnlyRow = Self.item("book", "3", "List row", tracking: Self.tracking(
            progress: ProgressState(kind: "pages", value: 238, max: 476, unit: "page")
        ))
        XCTAssertEqual(fraction(pagesOnlyRow), 0.5, "A row without the journey uses its progress keys")

        // Manga, comics, anime: the count over the total, when one is known.
        let manga = Self.item("manga", "2", "Berserk", tracking: Self.tracking(
            progress: ProgressState(kind: "count", value: 90, max: 360, unit: "chapter")
        ))
        XCTAssertEqual(fraction(manga), 0.25)
        let ongoing = Self.item("manga", "3", "One Piece", tracking: Self.tracking(
            progress: ProgressState(kind: "count", value: 90, max: nil, unit: "chapter")
        ))
        XCTAssertNil(fraction(ongoing), "No total, no bar")
        let anime = Self.item("anime", "1", "Cowboy Bebop", tracking: Self.tracking(
            progress: ProgressState(kind: "count", value: 13, max: 26, unit: "episode")
        ))
        XCTAssertEqual(fraction(anime), 0.5)

        // A game: its playthrough's percent; hours only, no bar.
        let percentGame = Self.item("game", "1", "Hades", tracking: Self.tracking(
            game: Self.game(playthrough: Self.playthrough(minutes: 720, percentage: 58))
        ))
        XCTAssertEqual(try XCTUnwrap(fraction(percentGame)), 0.58, accuracy: 0.0001)
        let hoursOnly = Self.item("game", "2", "Hours", tracking: Self.tracking(
            progress: ProgressState(kind: "minutes", value: 720, max: nil, unit: "minute"),
            game: Self.game(playthrough: Self.playthrough(minutes: 720, percentage: nil))
        ))
        XCTAssertNil(fraction(hoursOnly))

        // Nothing done yet: no empty track.
        let unread = Self.item("book", "4", "Unread", tracking: Self.bookTracking(status: "In progress", pages: 0))
        XCTAssertNil(fraction(unread))
        let unstarted = Self.item("tv", "3", "Unstarted", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 0, aired: 10, upNext: (1, 1))
        ))
        XCTAssertNil(fraction(unstarted))
        XCTAssertNil(fraction(Self.item("movie", "5", "Movie", tracking: Self.tracking(
            progress: ProgressState(kind: "binary", value: 0, max: 1, unit: "movie")
        ))))
        XCTAssertNil(fraction(Self.item("anime", "6", "No progress")))

        // Past the total (a stale total, or a re-numbered season): full, never more.
        let over = Self.item("manga", "4", "Over", tracking: Self.tracking(
            progress: ProgressState(kind: "count", value: 400, max: 360, unit: "chapter")
        ))
        XCTAssertEqual(fraction(over), 1)
    }

    func testTheBarMovesWhereTheCardIsAfterASave() async throws {
        // A book's page saved from its panel.
        let book = Self.item("book", "427578", "Project Hail Mary", tracking: Self.bookTracking(status: "In progress", pages: 40))
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        tracking.bookProgressResult = Self.bookTracking(status: "In progress", pages: 238)
        let home = homeViewModel(items: [book])
        XCTAssertEqual(try XCTUnwrap(InProgressCardPresentation.progressFraction(for: home.inProgressItems[0])),
                       40.0 / 476, accuracy: 0.0001)

        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)
        let saved = await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 238))

        XCTAssertTrue(saved)
        XCTAssertEqual(home.inProgressItems.map(\.id), [book.id], "The card stays where it is")
        XCTAssertEqual(InProgressCardPresentation.progressFraction(for: home.inProgressItems[0]), 0.5)

        // A tick in a show's episode sheet.
        let show = Self.item("tv", "1396", "Breaking Bad", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 8, aired: 62, upNext: (2, 2))
        ))
        let ticked = ContinueWatchingTicks.replacingTV(
            TVTrackingSurfacesTests.showState(status: "In progress", watched: 31, aired: 62, upNext: (2, 3)),
            id: show.id, in: [show]
        )
        XCTAssertEqual(InProgressCardPresentation.progressFraction(for: ticked[0]), 0.5)
    }

    // MARK: Books: the page's progress sheet

    func testABooksCardOpensItsPagesSheetAndSavesProgressWithThePagesOneRequest() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        tracking.bookProgressResult = Self.bookTracking(status: "In progress", pages: 120)
        let home = homeViewModel(items: [book])

        let opensPage = await home.progressUpdates.updateProgress(for: book)

        XCTAssertNil(opensPage, "The sheet opens over Home")
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertEqual(update.bookPanel?.ref, book.media.ref)
        XCTAssertEqual(update.page.changeOrigin, home.continueWatching.changeOrigin,
                       "Its writes post as Home's own, so the card stays where it is")
        XCTAssertEqual(home.progressUpdates.openings, 1)
        XCTAssertFalse(home.progressUpdates.needsReload)

        let saved = await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 120))

        XCTAssertTrue(saved)
        XCTAssertEqual(media.detailRequests, [book.media.ref], "The page's own load, once")
        XCTAssertEqual(tracking.bookProgressCalls.map(\.value), [120], "One request")
        XCTAssertEqual(update.page.viewer?.progress?.value, 120)
        XCTAssertTrue(home.progressUpdates.needsReload, "The rail re-sorts on the next visit")
        XCTAssertTrue(home.inProgressNeedsReload)
        XCTAssertEqual(home.inProgressItems.map(\.id), [book.id], "The card stays where it is")
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: home.inProgressItems[0]),
                       "Project Hail Mary, update progress, 120 of 476 pages", "VoiceOver reads the new page")
        XCTAssertTrue(tracking.listRequests.isEmpty, "Nothing reloads the rail now")
    }

    func testABooksSheetOpensFromItsCardBeforeItsPageLoadsAndItsSaveWaitsForThePage() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary", tracking: Self.bookTracking(status: "In progress", pages: 40))
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        tracking.bookProgressResult = Self.bookTracking(status: "In progress", pages: 120)
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [book])

        let opensPage = await home.progressUpdates.updateProgress(for: book)

        XCTAssertNil(opensPage)
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertNil(update.page.detail, "The page is still loading")
        XCTAssertNotNil(update.bookPanel, "The sheet is open anyway, as a show's is")
        XCTAssertNil(home.progressUpdates.loadingItemID, "The card doesn't wait")
        XCTAssertEqual(home.progressUpdates.openings, 1)
        XCTAssertEqual(update.bookTitle.title, "Project Hail Mary")
        XCTAssertEqual(update.bookTitle.totalPages, 476, "The card's page progress knows the count, so it counts pages")
        XCTAssertEqual(update.bookProgress?.value, 40, "Where the card says the book stands")
        XCTAssertEqual(ProgressUpdateViewModel(title: update.bookTitle, progress: update.bookProgress).totalPages, 476,
                       "The card's page progress knows the count")

        let save = Task { await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 120)) }
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(tracking.bookProgressCalls.isEmpty, "The save waits for the page it writes to")
        await gate.open()
        let saved = await save.value

        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.bookProgressCalls.map(\.value), [120], "One request")
        XCTAssertEqual(media.detailRequests, [book.media.ref], "One load")
        XCTAssertNotNil(update.bookPanel, "A book still being read keeps its sheet")
    }

    func testABooksSheetCountsPagesOnlyFromPageProgressUntilItsPageKnows() async throws {
        let percent = ProgressState(kind: "percentage", value: 35, max: 100, unit: "percent")
        let book = Self.item("book", "427578", "Project Hail Mary", tracking: Self.tracking(progress: percent))
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        XCTAssertNil(ProgressUpdateViewModel(title: update.bookTitle, progress: update.bookProgress).totalPages,
                     "A percentage's 100 isn't a page count")
        await gate.open()
        await waitUntil { update.page.detail != nil }
        XCTAssertEqual(update.bookTitle.totalPages, 476, "The page knows the count")
    }

    func testASaveWhileThePageWontLoadTriesAgainThenSaysWhyInTheSheet() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.detailError = URLError(.notConnectedToInternet)
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        let saved = await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 120))

        XCTAssertFalse(saved)
        XCTAssertEqual(media.detailRequests.count, 2, "Tried again")
        XCTAssertTrue(tracking.bookProgressCalls.isEmpty)
        XCTAssertNotNil(update.page.progressErrorMessage, "The sheet says why")
        XCTAssertNotNil(update.bookPanel, "The sheet stays for another try")
    }

    func testFinishingABookFromTheSheetOpensThePagesCompletionLogOnceTheSheetHasGone() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        // Finished Reading in the sheet, or progress saved at the last page.
        update.openCompletion()

        XCTAssertNil(update.bookPanel, "The sheet closes first")
        XCTAssertTrue(update.isClosing)
        update.sheetDidClose()
        await waitUntil { update.composer != nil }
        XCTAssertEqual(update.composer?.mode, .finished)
        XCTAssertEqual(update.composer?.completionJourneyId, 9, "The log finishes the current journey")
    }

    func testAFailedPauseFromTheSheetShowsAtOnceThenRollsBackAndSaysSoOnceTheSheetHasGone() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "In progress", pages: 40)
        tracking.bookActionError = APIError.httpStatus(500, nil)
        tracking.bookActionGate = RefreshGate()
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)
        await waitUntil { update.page.detail != nil }

        update.performBookAction("pause")
        // The sheet closes on the tap.
        update.bookPanel = nil

        XCTAssertEqual(update.page.viewer?.status, "Paused", "Shows at once")
        await tracking.bookActionGate?.open()
        _ = await update.page.settled()
        XCTAssertEqual(tracking.bookActions, ["pause"], "One request")
        XCTAssertEqual(update.page.viewer?.status, "In progress", "Rolled back")
        XCTAssertNotNil(update.page.quickActionErrorMessage)
        XCTAssertFalse(update.showsFailure, "Not while the sheet slides away")
        update.sheetDidClose()
        XCTAssertTrue(update.showsFailure, "The page's alert, over Home")
        XCTAssertFalse(home.progressUpdates.needsReload, "Nothing changed")
    }

    func testABookFinishedElsewhereClosesItsSheetAndOpensItsPage() async throws {
        // The card still says the book is being read.
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "Completed", pages: 476)
        let home = homeViewModel(items: [book])

        let opensPage = await home.progressUpdates.updateProgress(for: book)

        XCTAssertNil(opensPage, "The sheet opens from the card")
        let update = try XCTUnwrap(home.progressUpdates.update)
        await waitUntil { update.bookPanel == nil }
        XCTAssertTrue(update.isClosing)
        XCTAssertEqual(update.sheetDidClose(), book.media.ref, "Its page opens once the sheet has gone, saying where it stands")
        XCTAssertNil(update.sheetDidClose(), "Once")
    }

    func testASaveAfterThePageFoundTheBookFinishedElsewhereWritesNothing() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "Completed", pages: 476)
        tracking.bookProgressResult = Self.bookTracking(status: "In progress", pages: 120)
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        let saved = await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 120))

        XCTAssertFalse(saved)
        XCTAssertTrue(tracking.bookProgressCalls.isEmpty, "No progress on a finished book")
    }

    func testAGameRestartedElsewhereClosesItsSheetAndTakesNoWriteOnTheOldPlaythrough() async throws {
        let old = Self.playthrough(minutes: 120, percentage: nil)
        let game = Self.item("game", "113112", "Hades", tracking: Self.tracking(game: Self.game(playthrough: old)))
        let restarted = GamePlaythroughState(id: 32, status: "In progress", origin: "live", startDate: "2026-10-30",
                                             endDate: nil, totalMinutes: nil, percentage: nil,
                                             completionDiaryEntryId: nil, isReplay: false)
        media.details[game.media.ref.id] = MediaDetail(
            ref: game.media.ref,
            title: "Hades",
            userState: UserMediaState(isTracked: true, status: "In progress", game: Self.game(playthrough: restarted))
        )
        let home = homeViewModel(items: [game])
        _ = await home.progressUpdates.updateProgress(for: game)
        let update = try XCTUnwrap(home.progressUpdates.update)

        var request = GamePlaythroughWriteRequest(progressedOn: "2026-10-30")
        request.totalMinutes = 300
        request.includesMinutes = true
        let saved = await update.saveGame(old, request)

        XCTAssertFalse(saved)
        XCTAssertTrue(tracking.playthroughUpdates.isEmpty, "Not on the old attempt")
        await waitUntil { update.gameProgress == nil }
        XCTAssertEqual(update.sheetDidClose(), game.media.ref, "Its page opens, with the new playthrough")
    }

    func testASecondTapBeforeTheSheetIsGoneOpensNothing() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary")
        let game = Self.item("game", "113112", "Hades", tracking: Self.tracking(
            game: Self.game(playthrough: Self.playthrough(minutes: 120, percentage: nil))
        ))
        let home = homeViewModel(items: [book, game])

        _ = await home.progressUpdates.updateProgress(for: book)
        let first = try XCTUnwrap(home.progressUpdates.update)
        let secondOpensPage = await home.progressUpdates.updateProgress(for: game)

        XCTAssertNil(secondOpensPage)
        XCTAssertTrue(home.progressUpdates.update === first, "The first sheet stays")
        XCTAssertNil(first.gameProgress)
        XCTAssertEqual(home.progressUpdates.openings, 1)
    }

    func testABookPausedWithoutAJourneySaysPaused() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary", tracking: Self.tracking(status: "Paused"))
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [book])
        _ = await home.progressUpdates.updateProgress(for: book)
        let update = try XCTUnwrap(home.progressUpdates.update)

        XCTAssertEqual(update.bookJourneyStatus, "Paused", "So the sheet offers Resume, not Pause")
        await gate.open()
    }

    func testACardWithNothingToUpdateOpensItsPageWithoutLoadingIt() async {
        let finished = Self.item("book", "1", "Finished", tracking: Self.tracking(status: "Completed"))
        let unplayed = Self.item("game", "2", "No playthrough")
        let home = homeViewModel(items: [finished, unplayed])

        for item in [finished, unplayed] {
            let opensPage = await home.progressUpdates.updateProgress(for: item)
            XCTAssertEqual(opensPage, item.media.ref, item.media.title)
        }
        XCTAssertTrue(media.detailRequests.isEmpty)
        XCTAssertNil(home.progressUpdates.update)
    }

    // MARK: Games: the page's progress sheet

    func testAGamesCardOpensItsPagesSheetWithEditDatesAndFinishFollowingTheSheet() async throws {
        let playthrough = Self.playthrough(minutes: 120, percentage: nil)
        let game = Self.item("game", "113112", "Hades", tracking: Self.tracking(game: Self.game(playthrough: playthrough)))
        media.details[game.media.ref.id] = MediaDetail(
            ref: game.media.ref,
            title: "Hades",
            userState: UserMediaState(isTracked: true, status: "In progress", game: Self.game(playthrough: playthrough))
        )
        tracking.playthroughResult = Self.tracking(
            status: "In progress", game: Self.game(playthrough: Self.playthrough(minutes: 300, percentage: nil))
        )
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [game])

        let opensPage = await home.progressUpdates.updateProgress(for: game)

        XCTAssertNil(opensPage)
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertEqual(update.gameProgress, playthrough, "The sheet opens from the card, before the page loads")
        XCTAssertNil(update.page.detail)
        await gate.open()

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

    // MARK: A card still waiting for its page (a manga or a comic)

    func testAnotherCardTappedWhileAPageLoadsOpensInsteadOfIt() async throws {
        let manga = Self.item("manga", "2", "Berserk")
        let show = Self.item("tv", "1396", "Breaking Bad", tracking: Self.tracking(
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 8, aired: 62, upNext: (2, 2))
        ))
        media.details[manga.media.ref.id] = MediaDetail(
            ref: manga.media.ref,
            title: "Berserk",
            userState: UserMediaState(isTracked: true, status: "In progress")
        )
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [manga, show])

        let mangaTap = Task { await home.progressUpdates.updateProgress(for: manga) == nil }
        await waitUntil { home.progressUpdates.loadingItemID == manga.id }
        let showOpensPage = await home.progressUpdates.updateProgress(for: show)

        XCTAssertNil(showOpensPage)
        XCTAssertNotNil(home.continueWatching.episodes, "The show's sheet opens")
        XCTAssertNil(home.progressUpdates.loadingItemID, "The manga's card no longer waits")
        await gate.open()
        let mangaOpensNothing = await mangaTap.value
        XCTAssertTrue(mangaOpensNothing, "Nor the manga's page")
        XCTAssertNil(home.progressUpdates.update, "The manga's composer doesn't open over the sheet")
        XCTAssertEqual(home.progressUpdates.openings, 0)
    }

    func testSomethingElseTheScreenOpensCancelsACardStillWaitingForItsPage() async throws {
        let comic = Self.item("comic", "113112", "Saga")
        media.details[comic.media.ref.id] = MediaDetail(
            ref: comic.media.ref,
            title: "Saga",
            userState: UserMediaState(isTracked: true, status: "In progress")
        )
        // A page that doesn't load would open itself, saying why.
        let unloadable = Self.item("manga", "1", "Berserk")
        let gate = RefreshGate()
        media.detailGate = gate
        let home = homeViewModel(items: [comic, unloadable])

        // The comic's line, then its poster (or anything else Home opens, or leaving Home).
        let comicTap = Task { await home.progressUpdates.updateProgress(for: comic) == nil }
        await waitUntil { home.progressUpdates.loadingItemID == comic.id }
        home.progressUpdates.cancelPendingOpen()
        XCTAssertNil(home.progressUpdates.loadingItemID)
        let unloadableTap = Task { await home.progressUpdates.updateProgress(for: unloadable) == nil }
        await waitUntil { home.progressUpdates.loadingItemID == unloadable.id }
        home.progressUpdates.cancelPendingOpen()
        media.detailError = URLError(.notConnectedToInternet)
        await gate.open()

        let comicOpensNothing = await comicTap.value
        let unloadableOpensNothing = await unloadableTap.value
        XCTAssertTrue(comicOpensNothing)
        XCTAssertTrue(unloadableOpensNothing, "Not even the page")
        XCTAssertNil(home.progressUpdates.update, "The comic's composer doesn't open over its page")
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

    func testAPausedBooksCardOpensItsSheetToo() async throws {
        let book = Self.item("book", "427578", "Project Hail Mary", tracking: Self.bookTracking(status: "Paused", pages: 40))
        media.details[book.media.ref.id] = Self.bookDetail(book.media.ref, status: "Paused", pages: 40)
        let home = homeViewModel(items: [book])

        let opensPage = await home.progressUpdates.updateProgress(for: book)

        XCTAssertNil(opensPage, "A paused book's sheet opens too, as on its page")
        let update = try XCTUnwrap(home.progressUpdates.update)
        XCTAssertNotNil(update.bookPanel)
        XCTAssertEqual(update.bookJourneyStatus, "Paused", "The sheet says so")
        XCTAssertEqual(update.page.changeOrigin, home.continueWatching.changeOrigin)
        await waitUntil { update.page.detail != nil }
        XCTAssertNotNil(update.bookPanel, "Its page agrees, so the sheet stays")
    }

    func testProfilesInProgressRailIsPostersOnly() throws {
        // Each card is the poster, which opens the title's page: no title, no "Update progress ›", nothing opens over
        // Profile.
        let source = try String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("Spine/Features/Profile/ProfileView.swift"),
            encoding: .utf8
        )
        XCTAssertTrue(source.contains("InProgressPoster(item: item)"))
        for code in ["InProgressCard(", "InProgressUpdates", "progressUpdates", "inProgressUpdatePresentations",
                     "ContinueWatchingTicks", "continueWatchingPresentations", "InProgressCardPresentation.line",
                     "InProgressBar", "progressFraction"] {
            XCTAssertFalse(source.contains(code), "Profile still has \(code)")
        }
    }

    func testTheCardsReadOneLineInTheNormalWidth() throws {
        // One "Update progress ›" for every type: no per-show line, no condensed width, no count on the card.
        let features = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features", isDirectory: true)
        for path in ["Home/ContinueWatching.swift", "Home/HomeView.swift", "Home/InProgressUpdates.swift"] {
            let source = try String(contentsOf: features.appendingPathComponent(path), encoding: .utf8)
            XCTAssertFalse(source.contains("fontWidth(.condensed)"), "\(path) squeezes the line")
            XCTAssertFalse(source.contains("ContinueWatchingLine("), "\(path) still has the show's own line")
        }
        let home = try String(contentsOf: features.appendingPathComponent("Home/HomeView.swift"), encoding: .utf8)
        XCTAssertTrue(home.contains("InProgressCard("), "Home builds its own cards")
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
