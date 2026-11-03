import XCTest
@testable import Spine

/// The manga and comic laws on the phone (docs/manga-comics-tracking-contract.md): the book's screens, counted in
/// chapters or issues. Each test names the laws it pins.
@MainActor
final class SeriesTrackingContractTests: XCTestCase {
    private var tracking: SeriesTrackingRepository!
    private var media: SurfacesMediaRepository!
    private var diary: SurfacesDiaryRepository!

    override func setUp() async throws {
        try await super.setUp()
        tracking = SeriesTrackingRepository()
        media = SurfacesMediaRepository()
        diary = SurfacesDiaryRepository()
        SeriesURLProtocol.lastRequest = nil
        SeriesURLProtocol.lastBody = nil
    }

    // MARK: Decoding (MC-000, MC-500)

    // MC-500: the state arrives under its own key; the app reads it as the book's.
    func testASeriesStateDecodesBesideTheBooksKey() throws {
        let state = try JSONDecoder.api.decode(TrackingState.self, from: Self.trackingResponse)

        XCTAssertNil(state.book)
        let series = try XCTUnwrap(state.reading)
        XCTAssertEqual(series.currentJourney?.id, 41)
        XCTAssertEqual(series.currentJourney?.progress?.value, 120)
        XCTAssertEqual(series.currentJourney?.continues, true)
        XCTAssertEqual(series.total, 380)
        XCTAssertEqual(series.unit, "chapter")
        XCTAssertEqual(series.offersKeepReading, false)
        XCTAssertEqual(series.opensLog, true)
        XCTAssertEqual(state.progress?.max, 380)

        let page = UserMediaState.untracked.applying(state)
        XCTAssertEqual(page.book?.total, 380, "The page holds it as its reading state")
        XCTAssertTrue(page.book?.hasLiveJourney == true)
    }

    // MC-500: a page's user state too.
    func testAPagesUserStateReadsSeriesAsItsReadingState() throws {
        let data = Data(#"{"is_tracked":true,"status":"In progress","in_lists":[],"has_liked":false,"series":{"current_journey":null,"reading_history":[],"undated_read":null,"status_source":"direct_status","completion_dates":[],"completed_journey_count":0,"lifetime_read_count":0,"is_rereading":false,"completion_required":false,"can_remove_tracking":true,"remove_tracking_reason":null,"available_actions":[],"action_reasons":{},"total":54,"unit":"issue","offers_keep_reading":false,"opens_log":false}}"#.utf8)
        let state = try JSONDecoder.api.decode(UserMediaState.self, from: data)
        XCTAssertEqual(state.book?.total, 54)
        XCTAssertEqual(state.book?.unit, "issue")
    }

    // MARK: Words and shelves (MC-000, BK-001)

    // MC-000 (BK-001): five sections with the book's words.
    func testFiveShelvesWithTheBooksWords() {
        for mediaType in ["manga", "comic"] {
            XCTAssertEqual(LibraryShelf.available(for: mediaType), [.planning, .currentlyReading, .paused, .didNotFinish, .read], mediaType)
            XCTAssertTrue(LibraryShelf.usesStatusShelves(mediaType))
            XCTAssertEqual(LibraryShelf.defaultShelf(for: mediaType), .currentlyReading)
            XCTAssertEqual(LibraryShelf.planning.title(mediaType: mediaType), "To Read")
            XCTAssertEqual(LibraryShelf.currentlyReading.title(mediaType: mediaType), "Currently Reading")
            XCTAssertEqual(LibraryShelf.didNotFinish.title(mediaType: mediaType), "Did Not Finish")
            XCTAssertEqual(LibraryShelf.read.title(mediaType: mediaType), "Read")
            let ref = Self.ref(mediaType)
            XCTAssertEqual(ref.trackingStatusLabel("Planning"), "To Read")
            XCTAssertEqual(ref.trackingStatusLabel("In progress"), "Currently Reading")
            XCTAssertEqual(ref.trackingStatusLabel("Paused"), "Paused")
            XCTAssertEqual(ref.trackingStatusLabel("Dropped"), "Did Not Finish")
            XCTAssertEqual(ref.trackingStatusLabel("Completed"), "Read")
            XCTAssertEqual(ref.repeatLabel, "Reread")
        }
        XCTAssertEqual(LibraryShelf.available(for: "anime"), [.tracked, .planning], "Not anime")
    }

    // MC-000 (SW-006, AC-5): calendar dates, and the log's own visibility.
    func testLogsAreCalendarDatesAndKeepTheirVisibility() throws {
        for mediaType in ["manga", "comic"] {
            let ref = Self.ref(mediaType)
            XCTAssertTrue(ref.usesCalendarLogDate)
            XCTAssertFalse(ref.usesCalendarConsumptionDate, "Which also hides the visibility picker")
            let data = try JSONEncoder.api.encode(DiaryEntryWriteRequest(
                ref: ref,
                consumedAt: CalendarDateCodec.date(from: "2026-07-20"),
                rating: nil,
                review: "",
                reviewTitle: "",
                liked: false,
                isRewatch: false,
                autoMarkConsumed: true,
                containsSpoilers: false,
                visibility: "followers",
                tags: []
            ))
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(json["consumed_at"] as? String, "2026-07-20")
            XCTAssertEqual(json["visibility"] as? String, "followers")
        }
    }

    // MARK: Routes

    func testTheRepositoryUsesTheSeriesRoutes() async throws {
        let repository = Self.urlRepository(response: Self.trackingResponse)
        let ref = Self.ref("comic", source: "comicvine", id: "46568")

        let saved = try await repository.updateSeriesProgress(ref: ref, value: 12)
        XCTAssertEqual(saved.reading?.total, 380)
        XCTAssertEqual(SeriesURLProtocol.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(SeriesURLProtocol.lastRequest?.url?.absoluteString, "https://example.com/api/v1/tracking/comicvine/comic/46568/progress/")
        XCTAssertEqual(SeriesURLProtocol.lastBody?["value"] as? Int, 12)

        _ = try await repository.performSeriesAction(ref: ref, action: "keep_reading", request: BookActionRequest())
        XCTAssertEqual(SeriesURLProtocol.lastRequest?.url?.absoluteString, "https://example.com/api/v1/tracking/comicvine/comic/46568/actions/keep_reading/")

        _ = try await repository.updateSeriesAttempt(ref: ref, attemptId: 41, request: BookJourneyWriteRequest(startDate: "2026-07-01", endDate: nil))
        XCTAssertEqual(SeriesURLProtocol.lastRequest?.httpMethod, "PATCH")
        XCTAssertEqual(SeriesURLProtocol.lastRequest?.url?.absoluteString, "https://example.com/api/v1/tracking/comicvine/comic/46568/journeys/41/")

        _ = try await repository.deleteSeriesAttempt(ref: ref, attemptId: 41)
        XCTAssertEqual(SeriesURLProtocol.lastRequest?.httpMethod, "DELETE")
    }

    // MC-102, AC-5: a log's payload.
    func testALogsRequestCarriesItsVisibilityAndItsNumber() throws {
        let mutation = UUID()
        let data = try JSONEncoder.api.encode(SeriesCompletionWriteRequest(
            journeyId: 41,
            completionDate: "2026-07-20",
            rating: MediaLogViewModel.ratingDecimal(for: 9),
            review: "",
            reviewTitle: "",
            liked: true,
            isRewatch: false,
            containsSpoilers: false,
            tags: ["seinen"],
            visibility: "private",
            place: 1100,
            mutationId: mutation
        ))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["journey_id"] as? Int, 41)
        XCTAssertEqual(json["completion_date"] as? String, "2026-07-20")
        XCTAssertEqual(json["rating"] as? Double, 4.5)
        XCTAssertEqual(json["visibility"] as? String, "private")
        XCTAssertEqual(json["place"] as? Int, 1100)
        XCTAssertEqual(json["is_rewatch"] as? Bool, false)
        XCTAssertEqual(json["mutation_id"] as? String, mutation.uuidString)
    }

    // MARK: The log screen (MC-102, MC-103)

    // MC-103: there is no Progress mode.
    func testALogScreenHasNoProgressMode() {
        for mediaType in ["manga", "comic"] {
            XCTAssertFalse(MediaLogViewModel.supportsProgress(mediaType: mediaType))
            let composer = logViewModel(MediaDetail(ref: Self.ref(mediaType), title: "Title"), mode: .progress)
            XCTAssertEqual(composer.mode, .finished, mediaType)
            XCTAssertFalse(composer.supportsProgress)
        }
        XCTAssertTrue(MediaLogViewModel.supportsProgress(mediaType: "boardgame"))
    }

    // MC-102: with no total the log takes the number, prefilled with the place.
    func testWithNoTotalTheLogTakesTheNumber() async throws {
        let ref = Self.ref("manga", id: "13")
        let journey = BookJourneyState(id: 41, status: "In progress", progress: Self.count(1100, max: nil))
        let detail = MediaDetail(ref: ref, title: "One Piece", userState: UserMediaState(
            isTracked: true,
            status: "In progress",
            progress: Self.count(1100, max: nil),
            book: BookTrackingState(currentJourney: journey, unit: "chapter")
        ))
        tracking.completeResult = BookCompletionResponse(tracking: Self.tracking(status: "Completed"), diaryEntry: Self.entry(ref))
        let composer = logViewModel(detail, journey: 41)

        XCTAssertTrue(composer.showsSeriesPlace)
        XCTAssertEqual(composer.seriesPlaceLabel, "Chapters")
        XCTAssertEqual(composer.seriesPlaceText, "1100")
        composer.seriesPlaceText = "1120"
        let saved = await composer.save()

        XCTAssertTrue(saved)
        let request = try XCTUnwrap(tracking.completions.first)
        XCTAssertEqual(request.place, 1120)
        XCTAssertEqual(request.journeyId, 41)
        XCTAssertEqual(tracking.completionRefs, [ref], "Through the series route, not the diary")
    }

    // MC-102: with a total the log sends no number: it sets the place to the total.
    func testWithATotalTheLogHasNoNumber() async throws {
        let ref = Self.ref("comic", id: "46568")
        let detail = MediaDetail(ref: ref, title: "Saga", userState: UserMediaState(
            isTracked: true,
            status: "In progress",
            book: BookTrackingState(total: 54, unit: "issue")
        ))
        tracking.completeResult = BookCompletionResponse(tracking: Self.tracking(status: "Completed"), diaryEntry: Self.entry(ref))
        let composer = logViewModel(detail)
        XCTAssertFalse(composer.showsSeriesPlace)
        _ = await composer.save()
        XCTAssertNil(tracking.completions.first?.place)
    }

    // MC-200, BK-105: what the Reread toggle starts on.
    func testTheRereadToggleStartsFromTheServersWord() {
        func composer(_ state: BookTrackingState?) -> MediaLogViewModel {
            logViewModel(MediaDetail(ref: Self.ref("manga"), title: "Berserk", userState: UserMediaState(isTracked: true, book: state)))
        }
        let keepReading = BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", continues: true),
            lifetimeReadCount: 1,
            isRereading: false
        )
        XCTAssertFalse(composer(keepReading).isRepeat, "Keep Reading's log isn't a reread")
        let reread = BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress"),
            lifetimeReadCount: 1,
            isRereading: true
        )
        XCTAssertTrue(composer(reread).isRepeat)
        XCTAssertTrue(composer(BookTrackingState(lifetimeReadCount: 1)).isRepeat, "A direct log after an earlier read")
        XCTAssertFalse(composer(BookTrackingState()).isRepeat, "A first read")
    }

    // MC-104: the value the stars or heart opened it with.
    func testTheLogScreenOpensWithTheStarsOrHeart() {
        let composer = MediaLogViewModel(
            detail: MediaDetail(ref: Self.ref("manga"), title: "Berserk", userState: UserMediaState(isTracked: true, status: "In progress")),
            trackingRepository: tracking,
            diaryRepository: diary,
            preselectedLiked: true,
            preselectedRatingSteps: 7,
            onUnauthorized: {},
            onSaved: {}
        )
        XCTAssertEqual(composer.ratingSteps, 7)
        XCTAssertTrue(composer.liked)
    }

    // MARK: The progress sheet (MC-001 to MC-005, MC-300)

    // MC-001: one whole number; there's no percent to pick.
    func testTheSheetCountsOneWholeNumber() {
        let sheet = ProgressUpdateViewModel(title: Self.title("manga", total: 380), progress: Self.count(120, max: 380))
        XCTAssertEqual(sheet.mode, .pages)
        XCTAssertEqual(sheet.lastValue, 120)
        XCTAssertEqual(sheet.totalPages, 380)
        sheet.selectMode(.percentage)
        XCTAssertEqual(sheet.mode, .pages, "No percent (MC-001)")
        sheet.updateInput("1")
        sheet.updateInput("13")
        sheet.updateInput("135")
        XCTAssertEqual(sheet.currentValue, 135)
        XCTAssertEqual(sheet.summaryText, "+15 chapters since 120")
        XCTAssertTrue(sheet.canSave)
    }

    // MC-007: a lowered number reads as a correction, and an unmoved one can't save.
    func testTheSummaryCountsChaptersAndIssues() {
        let lowered = ProgressUpdateViewModel(title: Self.title("manga", total: 380), progress: Self.count(120, max: 380))
        lowered.updateInput("1")
        lowered.updateInput("11")
        lowered.updateInput("119")
        XCTAssertEqual(lowered.summaryText, "−1 chapter since 120")
        let comic = ProgressUpdateViewModel(title: Self.title("comic", total: 54), progress: Self.count(12, max: 54, unit: "issue"))
        XCTAssertEqual(comic.summaryText, "Last saved at 12 issues")
        XCTAssertFalse(comic.canSave)
    }

    // MC-003: no total: any number.
    func testWithNoTotalAnyNumberIsAccepted() {
        let sheet = ProgressUpdateViewModel(title: Self.title("manga", total: nil), progress: Self.count(10, max: nil))
        sheet.updateInput("9")
        sheet.updateInput("99")
        sheet.updateInput("999")
        XCTAssertNil(sheet.totalPages)
        XCTAssertNil(sheet.validationMessage)
        XCTAssertTrue(sheet.canSave)
    }

    // MC-004: a manga's number past its total is refused, naming it.
    func testAMangaPastItsTotalIsRefusedHere() {
        let sheet = ProgressUpdateViewModel(title: Self.title("manga", total: 380), progress: Self.count(120, max: 380))
        sheet.updateInput("3")
        sheet.updateInput("38")
        sheet.updateInput("381")
        XCTAssertEqual(sheet.validationMessage, "This manga has 380 chapters.")
        XCTAssertFalse(sheet.canSave)
    }

    // MC-004: a comic's goes to the server, which asks Comic Vine again first.
    func testAComicPastItsTotalGoesToTheServer() {
        let sheet = ProgressUpdateViewModel(title: Self.title("comic", total: 54), progress: Self.count(54, max: 54, unit: "issue"))
        sheet.updateInput("5")
        sheet.updateInput("55")
        XCTAssertNil(sheet.validationMessage)
        XCTAssertTrue(sheet.canSave)
    }

    // MC-004: the server's refusal shows in the sheet, naming the total.
    func testTheServersRefusalShowsItsOwnWords() async throws {
        let ref = Self.ref("comic", id: "46568")
        media.details[ref.id] = MediaDetail(ref: ref, title: "Saga", userState: UserMediaState(isTracked: true, status: "In progress"))
        tracking.progressError = APIError.httpStatus(400, #"{"error":{"code":"invalid","message":"This comic has 54 issues so far."}}"#)
        let page = pageModel(ref)
        await page.load()

        let saved = await page.saveProgress(ProgressUpdateSaveRequest(mode: .pages, value: 60), for: try XCTUnwrap(page.detail)).value

        XCTAssertFalse(saved)
        XCTAssertEqual(page.progressErrorMessage, "This comic has 54 issues so far.")
    }

    // MC-004: the refusal goes with the number it refused (seen in the simulator: it stayed beside a corrected number).
    func testTheServersRefusalGoesWithItsNumber() {
        let refusal = "This comic has 54 issues so far."
        let sheet = ProgressUpdateViewModel(title: Self.title("comic", total: 54), progress: Self.count(12, max: 54, unit: "issue"))
        sheet.updateInput("60")
        sheet.refusedValue = sheet.currentValue
        XCTAssertEqual(sheet.warning(serverMessage: refusal), refusal)

        sheet.updateInput("54")
        XCTAssertNil(sheet.warning(serverMessage: refusal))

        let book = ProgressUpdateViewModel(title: Self.title("book", total: 300), progress: Self.count(12, max: 300, unit: "page"))
        book.updateInput("20")
        XCTAssertEqual(book.warning(serverMessage: "Could not save."), "Could not save.", "A book's stays as it was")
    }

    // MC-005: a total of one has no number.
    func testATotalOfOneHasNoNumber() {
        XCTAssertTrue(BookTrackingState(total: 1).hasNoNumber)
        XCTAssertFalse(BookTrackingState(total: 2).hasNoNumber)
        XCTAssertFalse(BookTrackingState().hasNoNumber)
    }

    // MC-100, MC-300: a save goes through the series route, and the server says whether the log screen follows.
    func testASaveAtTheTotalSaysTheLogScreenFollows() async throws {
        let ref = Self.ref("manga", id: "2")
        media.details[ref.id] = Self.page(ref, status: "In progress", place: 379, total: 380)
        tracking.progressResult = Self.seriesTracking(status: "In progress", place: 380, total: 380, opensLog: true)
        let page = pageModel(ref)
        await page.load()

        let saved = await page.saveProgress(ProgressUpdateSaveRequest(mode: .pages, value: 380), for: try XCTUnwrap(page.detail)).value

        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.progressCalls, [380])
        XCTAssertEqual(page.viewer?.book?.opensLog, true)
        XCTAssertEqual(page.viewer?.status, "In progress", "Not now leaves it Currently Reading")
        XCTAssertEqual(page.viewer?.progress?.value, 380)
        XCTAssertNil(ProgressDisplayPreferences.mode(for: ref), "No page-or-percent preference")
    }

    // MC-101: a later save at the total doesn't.
    func testALaterSaveDoesNotOpenIt() async throws {
        let ref = Self.ref("comic", id: "7")
        media.details[ref.id] = Self.page(ref, status: "In progress", place: 54, total: 55)
        tracking.progressResult = Self.seriesTracking(status: "In progress", place: 55, total: 55, opensLog: false)
        let page = pageModel(ref)
        await page.load()
        _ = await page.saveProgress(ProgressUpdateSaveRequest(mode: .pages, value: 55), for: try XCTUnwrap(page.detail)).value
        XCTAssertEqual(page.viewer?.book?.opensLog, false)
    }

    // MARK: Actions (MC-200, MC-201)

    // MC-201: picking up after Did Not Finish shows the place at once.
    func testPickingUpShowsThePlaceAtOnce() async throws {
        let ref = Self.ref("manga", id: "2")
        media.details[ref.id] = Self.page(ref, status: "Dropped", place: 120, total: 380, journey: BookJourneyState(id: 3, status: "Dropped", progress: Self.count(120, max: 380)))
        tracking.actionGate = RefreshGate()
        tracking.actionResult = Self.seriesTracking(status: "In progress", place: 120, total: 380)
        let page = pageModel(ref)
        await page.load()

        let write = page.performQuickAction(.currently, for: try XCTUnwrap(page.detail))

        XCTAssertEqual(page.viewer?.status, "In progress")
        XCTAssertEqual(page.viewer?.progress?.value, 120, "It continues from the place")
        XCTAssertEqual(page.viewer?.book?.currentJourney?.progress?.value, 120)
        await tracking.actionGate?.open()
        _ = await write.value
        XCTAssertEqual(tracking.actions, ["start"])
    }

    // MC-200: Keep Reading keeps the place; Reread goes to zero.
    func testKeepReadingAndRereadShowAtOnce() async throws {
        let ref = Self.ref("manga", id: "2")
        let read = Self.page(ref, status: "Completed", place: 120, total: nil, offersKeepReading: true)
        media.details[ref.id] = read
        tracking.actionGate = RefreshGate()
        tracking.actionResult = Self.seriesTracking(status: "In progress", place: 120, total: nil)
        let page = pageModel(ref)
        await page.load()

        let keep = page.performBookAction("keep_reading", for: try XCTUnwrap(page.detail))
        XCTAssertEqual(page.viewer?.status, "In progress")
        XCTAssertEqual(page.viewer?.progress?.value, 120)
        XCTAssertEqual(page.viewer?.book?.isRereading, false)
        XCTAssertEqual(page.viewer?.book?.currentJourney?.continues, true)
        await tracking.actionGate?.open()
        _ = await keep.value
        XCTAssertEqual(tracking.actions, ["keep_reading"])

        media.details[ref.id] = read
        let again = pageModel(ref)
        await again.load()
        tracking.actionGate = RefreshGate()
        let reread = again.performBookAction("reread", for: try XCTUnwrap(again.detail))
        XCTAssertNil(again.viewer?.progress, "At zero")
        XCTAssertEqual(again.viewer?.book?.isRereading, true)
        await tracking.actionGate?.open()
        _ = await reread.value
        XCTAssertEqual(tracking.actions, ["keep_reading", "reread"])
    }

    // MC-200: a failed Keep Reading rolls back.
    func testAFailedKeepReadingRollsBack() async throws {
        let ref = Self.ref("manga", id: "2")
        media.details[ref.id] = Self.page(ref, status: "Completed", place: 120, total: nil, offersKeepReading: true)
        tracking.actionError = APIError.httpStatus(400, #"{"error":{"code":"invalid","message":"There's nothing more to read yet."}}"#)
        let page = pageModel(ref)
        await page.load()
        let ok = await page.performBookAction("keep_reading", for: try XCTUnwrap(page.detail)).value
        XCTAssertFalse(ok)
        XCTAssertEqual(page.viewer?.status, "Completed")
        XCTAssertEqual(page.quickActionErrorMessage, "There's nothing more to read yet.")
    }

    // MC-000 (BK-102): the eye's undated read and its undo go through the series route.
    func testTheEyesWritesUseTheSeriesRoute() async throws {
        let ref = Self.ref("comic", id: "2")
        media.details[ref.id] = MediaDetail(ref: ref, title: "Saga", userState: .untracked)
        tracking.actionResult = Self.seriesTracking(status: "Completed", place: 0, total: 54)
        let page = pageModel(ref)
        await page.load()
        _ = await page.performBookAction("mark_read", for: try XCTUnwrap(page.detail)).value
        XCTAssertEqual(page.viewer?.status, "Completed")
        tracking.actionResult = nil
        _ = await page.performBookAction("undo_read", for: try XCTUnwrap(page.detail)).value
        XCTAssertEqual(tracking.actions, ["mark_read", "undo_read"])
        XCTAssertFalse(page.viewer?.isTracked == true, "Removed with the mark")
    }

    // MC-000 (BK-307): editing and deleting attempts.
    func testAttemptsAreEditedAndDeletedOnTheSeriesRoutes() async throws {
        let ref = Self.ref("manga", id: "2")
        let journey = BookJourneyState(id: 3, status: "Dropped", startDate: "2026-01-01", endDate: "2026-02-01")
        media.details[ref.id] = Self.page(ref, status: "Dropped", place: 5, total: 380, journey: journey)
        tracking.attemptResult = Self.seriesTracking(status: "Dropped", place: 5, total: 380)
        let page = pageModel(ref)
        await page.load()
        let detail = try XCTUnwrap(page.detail)
        _ = await page.updateBookJourney(journey, startDate: CalendarDateCodec.date(from: "2026-01-02"), endDate: nil, for: detail).value
        XCTAssertEqual(tracking.attemptUpdates, [3])
        _ = await page.deleteBookJourney(journey, for: detail).value
        XCTAssertEqual(tracking.attemptDeletes, [3])
    }

    // MARK: Home (MC-300)

    // MC-300: Update progress opens the book's sheet over Home.
    func testACardOpensTheProgressSheetOverHome() async throws {
        for mediaType in ["manga", "comic"] {
            let item = Self.item(mediaType, "2", "Title", status: "In progress", place: 12, total: 54)
            media.details[item.media.ref.id] = Self.page(item.media.ref, status: "In progress", place: 12, total: 54)
            tracking.progressResult = Self.seriesTracking(status: "In progress", place: 13, total: 54, unit: mediaType == "comic" ? "issue" : "chapter")
            let home = homeViewModel([item])

            XCTAssertEqual(InProgressUpdates.route(for: item), .progress)
            let opensPage = await home.progressUpdates.updateProgress(for: item)

            XCTAssertNil(opensPage, mediaType)
            let update = try XCTUnwrap(home.progressUpdates.update)
            XCTAssertEqual(update.bookPanel?.ref, item.media.ref)
            XCTAssertNil(update.composer, "Not the log composer")
            XCTAssertEqual(update.bookTitle.totalPages, 54, "The card knows the total")
            XCTAssertEqual(update.bookProgress?.value, 12)

            let saved = await update.saveBookProgress(ProgressUpdateSaveRequest(mode: .pages, value: 13))
            XCTAssertTrue(saved)
            XCTAssertEqual(tracking.progressCalls.last, 13)
            XCTAssertEqual(LibraryPosterBadge.text(for: home.inProgressItems[0]), mediaType == "manga" ? "13/54 chapters" : "13/54 issues")
            XCTAssertEqual(InProgressCardPresentation.progressFraction(for: home.inProgressItems[0]), 13.0 / 54)
        }
    }

    // MC-300: with no attempt to update, the line opens the page.
    func testACardWithNothingToUpdateOpensThePage() async {
        for status in ["Completed", "Planning", "Dropped"] {
            let item = Self.item("manga", "2", "Berserk", status: status, place: 0, total: 380)
            let home = homeViewModel([item])
            let opensPage = await home.progressUpdates.updateProgress(for: item)
            XCTAssertEqual(opensPage, item.media.ref, status)
            XCTAssertNil(home.progressUpdates.update)
        }
    }

    // MC-300: a series paused with no attempt has nothing to update either.
    func testACardWithNoAttemptOpensThePage() async {
        let item = Spine.LibraryItem(
            media: MediaSummary(ref: Self.ref("comic", id: "9"), title: "Saga", posterUrl: nil, posterOrientation: .portrait),
            tracking: TrackingState(
                trackingId: 1,
                status: "Paused",
                rating: nil,
                progress: nil,
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: nil,
                series: BookTrackingState(total: 54)
            )
        )
        let home = homeViewModel([item])
        let opensPage = await home.progressUpdates.updateProgress(for: item)
        XCTAssertEqual(opensPage, item.media.ref)
    }

    // MC-100: reaching the total on the sheet opens the log, with Not now.
    func testReachingTheTotalFromHomeOpensTheLogWithNotNow() async throws {
        let item = Self.item("manga", "2", "Berserk", status: "In progress", place: 379, total: 380)
        media.details[item.media.ref.id] = Self.page(item.media.ref, status: "In progress", place: 379, total: 380, journey: BookJourneyState(id: 41, status: "In progress"))
        let home = homeViewModel([item])
        _ = await home.progressUpdates.updateProgress(for: item)
        let update = try XCTUnwrap(home.progressUpdates.update)
        await waitUntil { update.page.detail != nil }

        update.openCompletion(openedByTotal: true)
        update.sheetDidClose()
        await waitUntil { update.composer != nil }

        XCTAssertEqual(update.composer?.openedByTotal, true)
        XCTAssertEqual(update.composer?.completionJourneyId, 41)
        XCTAssertEqual(update.composer?.mode, .finished)
    }

    // MC-006: the bar needs a total.
    func testTheBarNeedsATotal() {
        XCTAssertEqual(InProgressCardPresentation.progressFraction(for: Self.item("manga", "2", "Berserk", status: "In progress", place: 95, total: 380)), 0.25)
        XCTAssertNil(InProgressCardPresentation.progressFraction(for: Self.item("manga", "3", "One Piece", status: "In progress", place: 1100, total: nil)))
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: Self.item("comic", "4", "Saga", status: "In progress", place: 12, total: 54)), "Saga, update progress, 12 of 54 issues")
    }

    // MARK: Screens read from source (MC-200, MC-300, MC-104)

    func testThePlusMenuOffersKeepReadingAndReread() throws {
        let source = try Self.source("MediaDetail/MediaDetailView.swift")
        XCTAssertTrue(source.contains(#"Button("Keep Reading", systemImage: "book.pages")"#))
        XCTAssertTrue(source.contains(#"Button("Reread", systemImage: "arrow.counterclockwise")"#))
        XCTAssertTrue(source.contains(#"Button(isSeries ? "Remove from Library" : "Remove Tracking""#), "Only for a series in the library")
        XCTAssertTrue(source.contains("detail.ref.readsLikeBook || detail.ref.mediaType == \"game\""), "The rail's + opens the book's menu")
    }

    // MC-104: the cover reads the stars and heart as it opens. Read only inside its closure, they'd keep their old values,
    // and the log screen would open empty (seen in the simulator).
    func testTheLogScreenIsBuiltWithTheValuesJustPicked() throws {
        let source = try Self.source("MediaDetail/MediaDetailView.swift")
        XCTAssertTrue(source.contains("[completionJourneyId, completionLiked, completionRatingSteps, completionOpenedByTotal] in"))
    }

    func testTheSheetLeavesOutTheNumberForATotalOfOne() throws {
        let source = try Self.source("MediaDetail/ProgressUpdateSheet.swift")
        XCTAssertTrue(source.contains("showsNumber: !(title.ref.isSeries && title.totalPages == 1)"))
        XCTAssertTrue(source.contains("} else if showsNumber {"), "No Save without a number")
    }

    // MARK: Fixtures

    private func pageModel(_ ref: MediaRef) -> MediaDetailViewModel {
        MediaDetailViewModel(ref: ref, mediaRepository: media, trackingRepository: tracking, diaryRepository: diary, onUnauthorized: {})
    }

    private func logViewModel(_ detail: MediaDetail, journey: Int? = nil, mode: MediaLogMode = .finished) -> MediaLogViewModel {
        MediaLogViewModel(
            detail: detail,
            trackingRepository: tracking,
            diaryRepository: diary,
            completionJourneyId: journey,
            onUnauthorized: {},
            onSaved: {},
            mode: mode
        )
    }

    private func homeViewModel(_ items: [Spine.LibraryItem]) -> HomeViewModel {
        let home = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: TVTrackingSurfacesTests.profile(enabled: ["manga", "comic"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            diaryRepository: diary,
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

    private static func source(_ path: String) throws -> String {
        try String(
            contentsOf: URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("Spine/Features/\(path)"),
            encoding: .utf8
        )
    }

    private static func ref(_ mediaType: String, source: String = "mal", id: String = "2") -> MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: mediaType, mediaId: id, seasonNumber: nil, episodeNumber: nil)
    }

    private static func count(_ value: Decimal, max: Decimal?, unit: String = "chapter") -> ProgressState {
        ProgressState(kind: "count", value: value, max: max, unit: unit)
    }

    private static func title(_ mediaType: String, total: Decimal?) -> ProgressSheetTitle {
        ProgressSheetTitle(item: item(mediaType, "ms-\(mediaType)", "Title", status: "In progress", place: 0, total: total))
    }

    private static func seriesTracking(status: String, place: Decimal, total: Decimal?, opensLog: Bool = false, unit: String = "chapter") -> TrackingState {
        TrackingState(
            trackingId: 1,
            status: status,
            rating: nil,
            progress: count(place, max: total, unit: unit),
            repeats: nil,
            startDate: nil,
            endDate: nil,
            notes: nil,
            updatedAt: nil,
            series: BookTrackingState(
                currentJourney: BookJourneyState(id: 41, status: status, progress: count(place, max: total)),
                total: total.map { NSDecimalNumber(decimal: $0).intValue },
                opensLog: opensLog
            )
        )
    }

    private static func tracking(status: String) -> TrackingState {
        TrackingState(trackingId: 1, status: status, rating: nil, progress: nil, repeats: nil, startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
    }

    private static func item(_ mediaType: String, _ id: String, _ title: String, status: String, place: Decimal, total: Decimal?) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref(mediaType, id: id), title: title, posterUrl: nil, posterOrientation: .portrait),
            tracking: TrackingState(
                trackingId: 1,
                status: status,
                rating: nil,
                progress: count(place, max: total, unit: mediaType == "comic" ? "issue" : "chapter"),
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: nil
            )
        )
    }

    private static func page(
        _ ref: MediaRef,
        status: String,
        place: Decimal,
        total: Decimal?,
        offersKeepReading: Bool = false,
        journey: BookJourneyState? = nil
    ) -> MediaDetail {
        MediaDetail(ref: ref, title: "Title", userState: UserMediaState(
            isTracked: true,
            status: status,
            progress: count(place, max: total),
            book: BookTrackingState(
                currentJourney: journey ?? BookJourneyState(id: 41, status: status, progress: count(place, max: total)),
                total: total.map { NSDecimalNumber(decimal: $0).intValue },
                offersKeepReading: offersKeepReading
            )
        ))
    }

    private static func entry(_ ref: MediaRef) -> DiaryEntry {
        try! JSONDecoder.api.decode(DiaryEntry.self, from: Data(#"""
        {
          "id": 902,
          "user": {"id":1,"username":"reader","display_name":"Reader","avatar_url":null},
          "media": {"ref":{"item_id":7,"source":"\#(ref.source)","media_type":"\#(ref.mediaType)","media_id":"\#(ref.mediaId)","season_number":null,"episode_number":null},"title":"Title"},
          "consumed_at": "2026-07-20T00:00:00Z",
          "rating": null,
          "review_title": "",
          "review": "",
          "contains_spoilers": false,
          "liked": false,
          "is_rewatch": false,
          "tags": [],
          "visibility": "public",
          "like_count": 0,
          "viewer_has_liked": false
        }
        """#.utf8))
    }

    private static func urlRepository(response: Data) -> APITrackingRepository {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [SeriesURLProtocol.self]
        SeriesURLProtocol.response = response
        return APITrackingRepository(client: APIClient(baseURL: URL(string: "https://example.com")!, session: URLSession(configuration: configuration)))
    }

    private static let trackingResponse = Data(#"""
    {
      "tracking_id": 7,
      "status": "In progress",
      "progress": {"kind":"count","value":120,"max":380,"unit":"chapter"},
      "series": {
        "current_journey": {
          "id": 41,
          "status": "In progress",
          "origin": "live",
          "start_date": "2026-07-01",
          "end_date": null,
          "progress": {"kind":"count","value":120,"max":380,"unit":"chapter","journey_id":41},
          "completion_diary_entry_id": null,
          "is_reread": false,
          "continues": true
        },
        "reading_history": [],
        "undated_read": null,
        "status_source": "journey",
        "completion_dates": ["2025-01-12"],
        "completed_journey_count": 1,
        "lifetime_read_count": 1,
        "is_rereading": false,
        "completion_required": true,
        "can_remove_tracking": false,
        "remove_tracking_reason": "Delete reading attempts and logs before removing this series.",
        "available_actions": ["pause","drop","log","finish"],
        "action_reasons": {"planning":"Pause or mark this attempt Did Not Finish first."},
        "total": 380,
        "unit": "chapter",
        "offers_keep_reading": false,
        "opens_log": true
      }
    }
    """#.utf8)
}

/// Answers a series' writes.
@MainActor
final class SeriesTrackingRepository: TrackingRepository {
    var progressCalls: [Int] = []
    var progressResult: TrackingState?
    var progressError: Error?
    var actions: [String] = []
    var actionResult: TrackingState?
    var actionError: Error?
    var actionGate: RefreshGate?
    var completions: [SeriesCompletionWriteRequest] = []
    var completionRefs: [MediaRef] = []
    var completeResult: BookCompletionResponse?
    var attemptUpdates: [Int] = []
    var attemptDeletes: [Int] = []
    var attemptResult: TrackingState?

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func continueWatching(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    /// The page's tracking merge: nothing newer than its payload.
    func detail(ref: MediaRef) async throws -> TrackingState { throw APIError.httpStatus(404, nil) }

    func updateSeriesProgress(ref: MediaRef, value: Int) async throws -> TrackingState {
        progressCalls.append(value)
        if let progressError { throw progressError }
        guard let progressResult else { throw APIError.httpStatus(500, nil) }
        return progressResult
    }

    func performSeriesAction(ref: MediaRef, action: String, request: BookActionRequest) async throws -> TrackingState? {
        actions.append(action)
        await actionGate?.wait()
        if let actionError { throw actionError }
        return actionResult
    }

    func completeSeries(ref: MediaRef, request: SeriesCompletionWriteRequest) async throws -> BookCompletionResponse {
        completions.append(request)
        completionRefs.append(ref)
        guard let completeResult else { throw APIError.httpStatus(500, nil) }
        return completeResult
    }

    func updateSeriesAttempt(ref: MediaRef, attemptId: Int, request: BookJourneyWriteRequest) async throws -> TrackingState {
        attemptUpdates.append(attemptId)
        guard let attemptResult else { throw APIError.httpStatus(500, nil) }
        return attemptResult
    }

    func deleteSeriesAttempt(ref: MediaRef, attemptId: Int) async throws -> TrackingState? {
        attemptDeletes.append(attemptId)
        return attemptResult
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func performBookAction(source: String, mediaId: String, action: String, request: BookActionRequest) async throws -> TrackingState? { fatalError("A series never uses the book's routes") }
}

private final class SeriesURLProtocol: URLProtocol {
    nonisolated(unsafe) static var response = Data()
    nonisolated(unsafe) static var lastRequest: URLRequest?
    nonisolated(unsafe) static var lastBody: [String: Any]?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequest = request
        if let stream = request.httpBodyStream {
            stream.open()
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let read = stream.read(&buffer, maxLength: buffer.count)
                if read <= 0 { break }
                data.append(buffer, count: read)
            }
            stream.close()
            Self.lastBody = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        } else if let body = request.httpBody {
            Self.lastBody = try? JSONSerialization.jsonObject(with: body) as? [String: Any]
        }
        let http = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.response)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
