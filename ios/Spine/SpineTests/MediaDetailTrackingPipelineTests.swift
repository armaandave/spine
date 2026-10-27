import XCTest
@testable import Spine

@MainActor
final class MediaDetailTrackingPipelineTests: XCTestCase {
    // MARK: Model folds

    func testApplyingTrackingKeepsDiaryFieldsAndLists() {
        let state = Self.loggedState.applying(TrackingState(
            trackingId: 7, status: "Completed", rating: "4.5", progress: nil, repeats: 1,
            startDate: "2026-01-02T10:00:00Z", endDate: nil, notes: "", updatedAt: nil,
            liked: true, directConsumption: true, ratingSourceDiaryEntryId: 90, diaryCount: 3
        ))
        XCTAssertTrue(state.isTracked)
        XCTAssertEqual(state.trackingId, 7)
        XCTAssertEqual(state.status, "Completed")
        XCTAssertEqual(state.rating, "4.5")
        XCTAssertEqual(state.startDate, "2026-01-02T10:00:00Z")
        XCTAssertTrue(state.hasLiked)
        XCTAssertEqual(state.diaryCount, 3)
        XCTAssertEqual(state.directConsumption, true)
        XCTAssertEqual(state.ratingSourceDiaryEntryId, 90)
        XCTAssertEqual(state.diaryEntryId, 90)
        XCTAssertEqual(state.diaryRating, "4.0")
        XCTAssertEqual(state.diaryConsumedAt, "2026-03-01")
        XCTAssertEqual(state.inLists, [3, 5])
    }

    func testApplyingTrackingWithoutAHeartPayloadKeepsTheHeart() {
        let state = Self.loggedState.applying(TrackingState(
            trackingId: 7, status: "In progress", rating: nil, progress: nil, repeats: 1,
            startDate: nil, endDate: nil, notes: "", updatedAt: nil
        ))
        XCTAssertTrue(state.hasLiked)
        XCTAssertEqual(state.diaryCount, 2)
        XCTAssertNil(state.rating)
    }

    func testGameStartDateComesFromTheCurrentPlaythrough() throws {
        let tracking = try JSONDecoder.api.decode(TrackingState.self, from: Self.gameTracking)
        let state = UserMediaState.untracked.applying(tracking)
        XCTAssertNil(state.startDate, "A game's tracking start_date is a datetime; the playthrough's plain date wins.")
        XCTAssertEqual(state.displayStartDate, "2024-01-01")
        XCTAssertEqual(state.game?.currentPlaythrough?.id, 41)
    }

    func testRemovedClearsOpinionsOnly() throws {
        let tracking = try JSONDecoder.api.decode(TrackingState.self, from: Self.gameTracking)
        let state = Self.loggedState.applying(tracking).removed()
        XCTAssertFalse(state.isTracked)
        XCTAssertNil(state.trackingId)
        XCTAssertNil(state.status)
        XCTAssertNil(state.rating)
        XCTAssertNil(state.game)
        XCTAssertNil(state.displayStartDate)
        XCTAssertFalse(state.hasLiked)
        XCTAssertNil(state.ratingSourceDiaryEntryId)
        XCTAssertNil(state.likeSourceDiaryEntryId)
        XCTAssertEqual(state.diaryEntryId, 90)
        XCTAssertEqual(state.diaryCount, 2)
        XCTAssertEqual(state.diaryConsumedAt, "2026-03-01")
        XCTAssertEqual(state.inLists, [3, 5])
    }

    func testNewerLogBecomesTheLatestLog() throws {
        let state = Self.loggedState.applying(diaryEntry: try Self.entry(id: 95, consumedAt: "2026-03-01", rating: "3.5"))
        XCTAssertEqual(state.diaryEntryId, 95, "Same day: the newer id is the latest, as in the server's order.")
        XCTAssertEqual(state.diaryRating, "3.5")
        let first = UserMediaState.untracked.applying(diaryEntry: try Self.entry(id: 96, consumedAt: "2026-04-02", rating: nil))
        XCTAssertEqual(first.diaryEntryId, 96)
        XCTAssertEqual(first.diaryConsumedAt, "2026-04-02")
        XCTAssertNil(first.diaryRating)
    }

    func testBackdatedLogKeepsTheNewerLatestLog() throws {
        let state = Self.loggedState.applying(diaryEntry: try Self.entry(id: 99, consumedAt: "2025-12-31", rating: "1.0"))
        XCTAssertEqual(state, Self.loggedState)
    }

    func testStartDateIsClientOnly() throws {
        var state = try JSONDecoder.api.decode(UserMediaState.self, from: Data(#"{"is_tracked":true,"start_date":"2026-01-01"}"#.utf8))
        XCTAssertNil(state.startDate)
        state.startDate = "2026-01-01"
        XCTAssertEqual(state.replacingHasLiked(true).startDate, "2026-01-01")
        XCTAssertEqual(state.replacingIsTracked(false).startDate, "2026-01-01")
        let encoded = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(state)) as? [String: Any])
        XCTAssertNil(encoded["start_date"])
    }

    // MARK: Projections

    func testStartingAnUntrackedGameShowsAPendingPlaythroughStartedToday() {
        var state = UserMediaState.untracked
        TrackingProjection.startGame(today: "2026-10-23")(&state)
        XCTAssertEqual(state.status, "In progress")
        XCTAssertTrue(state.isTracked)
        let playthrough = state.game?.currentPlaythrough
        XCTAssertTrue(playthrough?.isPending == true)
        XCTAssertNil(playthrough?.totalMinutes)
        XCTAssertNil(playthrough?.percentage)
        XCTAssertTrue(state.game?.hasLivePlaythrough == true, "Planning is disabled and the rail routes to the composer.")
        XCTAssertFalse(state.game?.canRemoveTracking == true)
        XCTAssertFalse(state.game?.isReplaying == true)
        XCTAssertEqual(state.displayStartDate, "2026-10-23")
    }

    func testStartingAPausedGameResumesItsPlaythrough() throws {
        var state = UserMediaState.untracked.applying(try JSONDecoder.api.decode(TrackingState.self, from: Self.gameTracking))
        TrackingProjection.pauseGame()(&state)
        XCTAssertEqual(state.status, "Paused")
        XCTAssertEqual(state.game?.currentPlaythrough?.status, "Paused")
        TrackingProjection.startGame(today: "2026-10-23")(&state)
        XCTAssertEqual(state.game?.currentPlaythrough?.id, 41)
        XCTAssertEqual(state.game?.currentPlaythrough?.status, "In progress")
        XCTAssertEqual(state.displayStartDate, "2024-01-01")
        TrackingProjection.dropGame(today: "2026-10-23")(&state)
        XCTAssertEqual(state.status, "Dropped")
        XCTAssertEqual(state.game?.currentPlaythrough?.endDate, "2026-10-23")
    }

    func testStartingAGameAfterACompletionShowsAReplay() throws {
        var state = UserMediaState.untracked.applying(try JSONDecoder.api.decode(TrackingState.self, from: Self.gameTracking))
        TrackingProjection.dropGame(today: "2026-10-22")(&state)
        TrackingProjection.startGame(today: "2026-10-23")(&state)
        XCTAssertTrue(state.game?.currentPlaythrough?.isPending == true)
        XCTAssertTrue(state.game?.currentPlaythrough?.isReplay == true)
        XCTAssertTrue(state.game?.isReplaying == true)
    }

    func testStartingABookShowsAPendingJourneyWithNoProgress() {
        var state = UserMediaState(isTracked: true, status: "Planning", progress: ProgressState(kind: "pages", value: 40, max: 300, unit: "page"))
        TrackingProjection.startBook(today: "2026-10-23")(&state)
        XCTAssertEqual(state.status, "In progress")
        XCTAssertTrue(state.book?.currentJourney?.isPending == true)
        XCTAssertTrue(state.book?.hasLiveJourney == true)
        XCTAssertNil(state.progress)
        XCTAssertEqual(state.displayStartDate, "2026-10-23")
        TrackingProjection.pauseBook()(&state)
        XCTAssertEqual(state.book?.currentJourney?.status, "Paused")
        TrackingProjection.startBook(today: "2026-10-24")(&state)
        XCTAssertEqual(state.book?.currentJourney?.status, "In progress")
        XCTAssertEqual(state.displayStartDate, "2026-10-23", "Resuming keeps the journey's start.")
    }

    func testRatingAndHeartProjections() {
        var state = UserMediaState.untracked
        TrackingProjection.rating("4.0", impliesCompletion: true)(&state)
        XCTAssertEqual(state.rating, "4.0")
        XCTAssertEqual(state.status, "Completed")
        var live = UserMediaState(isTracked: true, status: "In progress")
        TrackingProjection.rating("4.0", impliesCompletion: false)(&live)
        XCTAssertEqual(live.status, "In progress")
        TrackingProjection.rating(nil, impliesCompletion: true)(&live)
        XCTAssertNil(live.rating)
        XCTAssertEqual(live.status, "In progress")

        var liked = UserMediaState.untracked
        TrackingProjection.like(true, impliesCompletion: true)(&liked)
        XCTAssertTrue(liked.hasLiked)
        XCTAssertEqual(liked.status, "Completed")
        TrackingProjection.like(false, impliesCompletion: true)(&liked)
        XCTAssertFalse(liked.hasLiked)
        XCTAssertEqual(liked.status, "Completed")

        TrackingProjection.removed(&liked)
        XCTAssertFalse(liked.isTracked)
    }

    // MARK: Pipeline

    func testPlayingShowsTodayAtOnceThenAppliesTheResponse() async {
        let media = CountingMediaRepository(detail: Self.gameDetail(state: nil))
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.success(Self.gameState(status: "In progress", playthroughStatus: "In progress"))]
        let model = Self.model(media: media, tracking: tracking)
        await model.load()
        let posts = PostRecorder(ref: Self.gameRef)
        let gate = RefreshGate()
        tracking.gate = gate

        let task = model.performQuickAction(.currently, for: Self.gameDetail(state: nil))

        XCTAssertEqual(model.viewer?.status, "In progress")
        XCTAssertTrue(model.viewer?.game?.currentPlaythrough?.isPending == true)
        XCTAssertEqual(model.viewer?.displayStartDate, Self.today)
        await gate.open()
        let succeeded = await task.value
        await model.refreshFinished()

        XCTAssertTrue(succeeded)
        XCTAssertEqual(model.viewer?.game?.currentPlaythrough?.id, 41)
        XCTAssertEqual(model.viewer?.displayStartDate, Self.today)
        XCTAssertEqual(media.detailCount, 1)
        XCTAssertEqual(tracking.count("tracking.detail"), 0)
        XCTAssertEqual(media.communityCount, 0)
        XCTAssertEqual(posts.origins, [model.changeOrigin])
    }

    func testPlanningShowsAtOnceWithOnePatch() async {
        let media = CountingMediaRepository(detail: Self.gameDetail(state: nil))
        let tracking = ScriptedTrackingRepository()
        tracking.responses["update"] = [.success(Self.gameState(status: "Planning", playthroughStatus: nil))]
        let model = Self.model(media: media, tracking: tracking)
        await model.load()
        let gate = RefreshGate()
        tracking.gate = gate

        let task = model.performQuickAction(.planning, for: Self.gameDetail(state: nil))

        XCTAssertEqual(model.viewer?.status, "Planning")
        await gate.open()
        _ = await task.value
        await model.refreshFinished()
        XCTAssertEqual(tracking.calls, ["update"])
        XCTAssertEqual(tracking.updates.first?.status, "Planning")
        XCTAssertEqual(media.detailCount, 1)
        XCTAssertEqual(model.detail?.userState?.status, "Planning")
    }

    func testProgressSheetPauseIsOneRequest() async {
        let playing = Self.gameDetail(state: Self.gameState(status: "In progress", playthroughStatus: "In progress"))
        let media = CountingMediaRepository(detail: playing)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.pause"] = [.success(Self.gameState(status: "Paused", playthroughStatus: "Paused"))]
        let model = Self.model(media: media, tracking: tracking)
        model.detail = playing

        let succeeded = await model.performGameAction("pause", for: playing).value
        await model.refreshFinished()

        XCTAssertTrue(succeeded)
        XCTAssertEqual(tracking.calls, ["game.pause"])
        XCTAssertEqual(media.detailCount, 0)
        XCTAssertEqual(model.viewer?.status, "Paused")
        XCTAssertEqual(model.viewer?.game?.currentPlaythrough?.status, "Paused")
    }

    func testFailedWriteFallsBackToConfirmedStateAndRetriesWithSameId() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [
            .failure(APIError.httpStatus(503, nil)),
            .success(Self.gameState(status: "In progress", playthroughStatus: "In progress")),
        ]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail

        let first = await model.performQuickAction(.currently, for: detail).value

        XCTAssertFalse(first)
        XCTAssertEqual(model.viewer, UserMediaState.untracked)
        XCTAssertNotNil(model.quickActionErrorMessage)

        let retry = await model.performQuickAction(.currently, for: detail).value

        XCTAssertTrue(retry)
        XCTAssertNil(model.quickActionErrorMessage)
        XCTAssertEqual(tracking.actions.count, 2)
        XCTAssertNotNil(tracking.actions[0].mutationId)
        XCTAssertEqual(tracking.actions[0].mutationId, tracking.actions[1].mutationId)
    }

    func testAnySuccessClearsRetainedMutationIds() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.failure(APIError.httpStatus(503, nil))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail

        _ = await model.performQuickAction(.currently, for: detail).value
        _ = await model.performQuickAction(.paused, for: detail).value
        _ = await model.performQuickAction(.currently, for: detail).value

        XCTAssertEqual(tracking.calls, ["game.start", "game.pause", "game.start"])
        XCTAssertNotEqual(tracking.actions[0].mutationId, tracking.actions[2].mutationId)
    }

    func testQueuedWritesRunInTapOrder() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.success(Self.gameState(status: "In progress", playthroughStatus: "In progress"))]
        tracking.responses["game.pause"] = [.success(Self.gameState(status: "Paused", playthroughStatus: "Paused"))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        let playing = model.performQuickAction(.currently, for: detail)
        let paused = model.performQuickAction(.paused, for: detail)
        await Self.waitUntil { tracking.calls.count == 1 }

        XCTAssertEqual(tracking.calls, ["game.start"], "Paused waits for Playing's response.")
        XCTAssertEqual(model.viewer?.status, "Paused")
        await gate.open()
        let results = (await playing.value, await paused.value)

        XCTAssertTrue(results.0)
        XCTAssertTrue(results.1)
        XCTAssertEqual(tracking.calls, ["game.start", "game.pause"])
        XCTAssertEqual(model.viewer?.status, "Paused")
        XCTAssertEqual(model.detail?.userState?.game?.currentPlaythrough?.status, "Paused")
    }

    func testQueuedBookResumeIsRoutedFromConfirmedState() async {
        let reading = Self.bookState(status: "In progress")
        let detail = Self.bookDetail(state: reading)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["book.pause"] = [.success(Self.bookState(status: "Paused"))]
        tracking.responses["book.resume"] = [.success(Self.bookState(status: "In progress"))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        let paused = model.performQuickAction(.paused, for: detail)
        let resumed = model.performQuickAction(.currently, for: detail)
        XCTAssertEqual(model.viewer?.status, "In progress")
        await gate.open()
        _ = await paused.value
        _ = await resumed.value

        XCTAssertEqual(tracking.calls, ["book.pause", "book.resume"])
        XCTAssertEqual(model.viewer?.book?.currentJourney?.status, "In progress")
    }

    func testPendingPlaythroughDisablesPlanningAndRoutesTheRailToTheComposer() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        let task = model.performQuickAction(.currently, for: detail)

        XCTAssertTrue(model.viewer?.game?.hasLivePlaythrough == true)
        XCTAssertTrue(model.hasPendingWrites)
        await gate.open()
        _ = await task.value
        XCTAssertFalse(model.hasPendingWrites)
    }

    func testSettledWaitsForTheRealPlaythroughId() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.success(Self.gameState(status: "In progress", playthroughStatus: "In progress"))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        model.performQuickAction(.currently, for: detail)
        XCTAssertTrue(model.viewer?.game?.currentPlaythrough?.isPending == true)
        XCTAssertNil(model.detail?.userState?.game, "The placeholder never reaches confirmed state.")
        Task { await gate.open() }
        await model.settled()

        XCTAssertEqual(model.detail?.userState?.game?.currentPlaythrough?.id, 41)
    }

    func testPendingPlaythroughIsResolvedFromConfirmedStateWhenSent() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.success(Self.gameState(status: "In progress", playthroughStatus: "In progress"))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail

        model.performQuickAction(.currently, for: detail)
        let pending = model.viewer?.game?.currentPlaythrough ?? .pending(startDate: Self.today, isReplay: false)
        XCTAssertTrue(pending.isPending)
        let saved = await model.updateGamePlaythrough(
            pending,
            request: GamePlaythroughWriteRequest(totalMinutes: 90),
            for: detail
        ).value

        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.playthroughIds, [41])
    }

    func testCompletionSaveFoldsTrackingAndDiaryEntryAtOnce() async throws {
        let playing = Self.gameDetail(state: Self.gameState(status: "In progress", playthroughStatus: "In progress"))
        let media = CountingMediaRepository(detail: playing)
        let model = Self.model(media: media, tracking: ScriptedTrackingRepository())
        model.detail = playing
        let completed = Self.gameState(status: "Completed", playthroughStatus: "Completed", rating: "4.5", liked: true)
        let entry = try Self.entry(id: 120, consumedAt: Self.today, rating: "4.5")

        model.applyLogSave(MediaLogSaveResult(ref: playing.ref, tracking: completed, diaryEntry: entry))

        XCTAssertEqual(model.viewer?.status, "Completed")
        XCTAssertEqual(model.viewer?.rating, "4.5")
        XCTAssertEqual(model.viewer?.hasLiked, true)
        XCTAssertEqual(model.viewer?.diaryEntryId, 120)
        XCTAssertEqual(model.viewer?.diaryConsumedAt, Self.today)
        await model.refreshFinished()
        XCTAssertEqual(media.communityCount, 1)
        XCTAssertEqual(media.reviewPageCount, 1)
        XCTAssertEqual(media.detailCount, 0)
        XCTAssertEqual(model.detail?.community?.ratingCount, 1)
    }

    func testLogForAnotherItemRefreshesFully() async {
        let detail = Self.gameDetail(state: nil)
        let media = CountingMediaRepository(detail: detail)
        let model = Self.model(media: media, tracking: ScriptedTrackingRepository())
        model.detail = detail
        let other = MediaRef(itemId: nil, source: "igdb", mediaType: "game", mediaId: "1", seasonNumber: nil, episodeNumber: nil)

        model.applyLogSave(MediaLogSaveResult(ref: other, tracking: nil, diaryEntry: nil))
        await model.refreshFinished()

        XCTAssertEqual(media.detailCount, 1)
    }

    func testGamePagesNeverFetchTrackingAndRefreshKeepsGameStateAndReviews() async {
        let playing = Self.gameDetail(state: Self.gameState(status: "In progress", playthroughStatus: "In progress"))
        let media = CountingMediaRepository(detail: playing)
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(media: media, tracking: tracking)
        await model.load()
        XCTAssertEqual(model.reviewCount, 2)
        let gate = RefreshGate()
        let reviewGate = RefreshGate()
        media.detailGates[2] = gate
        media.reviewGates[2] = reviewGate

        model.scheduleRefresh(.full, after: .zero)
        await Self.waitUntil { media.detailCount == 2 }

        XCTAssertEqual(model.viewer?.game?.currentPlaythrough?.id, 41)
        XCTAssertEqual(model.reviewCount, 2)
        await gate.open()
        await Self.waitUntil { media.reviewPageCount == 2 }
        XCTAssertEqual(model.reviewCount, 2, "The detail response doesn't reset reviews while they reload.")
        XCTAssertEqual(model.viewer?.game?.currentPlaythrough?.id, 41)
        await reviewGate.open()
        await model.refreshFinished()
        XCTAssertEqual(model.viewer?.game?.currentPlaythrough?.id, 41)
        XCTAssertEqual(model.reviewCount, 2)
        XCTAssertEqual(tracking.count("tracking.detail"), 0)
        XCTAssertEqual(media.detailCount, 2)
    }

    func testRefreshThatLosesToAWriteKeepsTheWrite() async {
        let detail = Self.gameDetail(state: nil)
        let media = CountingMediaRepository(detail: detail)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["update"] = [.success(Self.gameState(status: "Planning", playthroughStatus: nil))]
        let model = Self.model(media: media, tracking: tracking)
        await model.load()
        let stale = RefreshGate()
        let next = RefreshGate()
        media.detailGates[2] = stale
        media.detailGates[3] = next

        model.scheduleRefresh(.full, after: .zero)
        await Self.waitUntil { media.detailCount == 2 }
        _ = await model.performQuickAction(.planning, for: detail).value
        media.response = Self.gameDetail(state: Self.gameState(status: "Planning", playthroughStatus: nil))
        await stale.open()
        await Self.waitUntil { media.detailCount == 3 }

        XCTAssertEqual(model.viewer?.status, "Planning", "The refresh that started before the write didn't overwrite it.")
        await next.open()
        await model.refreshFinished()
        XCTAssertEqual(model.viewer?.status, "Planning")
        XCTAssertEqual(media.detailCount, 3, "A refresh that lost the race schedules one more.")
    }

    func testOwnNotificationsAreIgnoredAndForeignOnesCoalesce() async {
        let detail = Self.gameDetail(state: nil)
        let media = CountingMediaRepository(detail: detail)
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(media: media, tracking: tracking)
        model.detail = detail
        let posts = PostRecorder(ref: Self.gameRef)
        let observer = PageObserver(model: model)

        _ = await model.performQuickAction(.planning, for: detail).value
        await model.refreshFinished()
        XCTAssertEqual(posts.origins, [model.changeOrigin])
        XCTAssertEqual(observer.received, 1)
        XCTAssertEqual(media.detailCount, 0, "The page skips posts carrying its own origin.")

        MediaStateChange.post(ref: Self.gameRef)
        MediaStateChange.post(ref: Self.gameRef, origin: UUID())
        await model.refreshFinished()
        XCTAssertEqual(media.detailCount, 1, "Two foreign posts coalesce into one refresh.")

        let other = MediaRef(itemId: nil, source: "igdb", mediaType: "game", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        MediaStateChange.post(ref: other)
        await model.refreshFinished()
        XCTAssertEqual(media.detailCount, 1, "Another item's change doesn't refresh this page.")
    }

    func testRemoveShowsUntrackedAtOnce() async {
        let rated = Self.gameDetail(state: Self.gameState(status: "Completed", playthroughStatus: "Completed", rating: "4.0", liked: true))
        let media = CountingMediaRepository(detail: rated)
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(media: media, tracking: tracking)
        model.detail = rated
        let gate = RefreshGate()
        tracking.gate = gate

        let task = model.removeTracking(for: rated)

        XCTAssertEqual(model.viewer?.isTracked, false)
        XCTAssertNil(model.viewer?.rating)
        XCTAssertEqual(model.viewer?.hasLiked, false)
        XCTAssertNil(model.viewer?.game)
        await gate.open()
        _ = await task.value
        await model.refreshFinished()
        XCTAssertEqual(model.detail?.userState?.isTracked, false)
        XCTAssertEqual(media.communityCount, 1)
        XCTAssertEqual(media.detailCount, 0)
    }

    func testHeartIsOptimisticAndRollsBack() async {
        let detail = Self.gameDetail(state: nil)
        let media = CountingMediaRepository(detail: detail)
        media.likeError = APIError.httpStatus(503, nil)
        let gate = RefreshGate()
        media.likeGate = gate
        let model = Self.model(media: media, tracking: ScriptedTrackingRepository())
        model.detail = detail

        let task = model.toggleMediaLike(for: detail)

        XCTAssertEqual(model.viewer?.hasLiked, true)
        XCTAssertEqual(model.viewer?.status, "Completed")
        await gate.open()
        let succeeded = await task.value
        XCTAssertFalse(succeeded)
        XCTAssertEqual(model.viewer?.hasLiked, false)
        XCTAssertNil(model.viewer?.status)
        XCTAssertNotNil(model.likeErrorMessage)
    }

    func testMovieEyeThenRatingStayOptimistic() async {
        let detail = Self.movieDetail
        let tracking = ScriptedTrackingRepository()
        tracking.responses["consume"] = [.success(Self.movieState(rating: nil))]
        tracking.responses["update"] = [.success(Self.movieState(rating: "4.0"))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        let watched = model.markConsumed(for: detail)
        let rated = model.setCurrentRating(for: detail, halfSteps: 8)

        XCTAssertEqual(model.viewer?.status, "Completed")
        XCTAssertEqual(model.viewer?.rating, "4.0")
        await gate.open()
        _ = await watched.value
        XCTAssertEqual(model.viewer?.rating, "4.0", "The confirmed watch keeps the pending rating on top.")
        _ = await rated.value
        XCTAssertEqual(model.viewer?.status, "Completed")
        XCTAssertEqual(model.viewer?.rating, "4.0")
        XCTAssertEqual(tracking.updates.first?.rating, Decimal(4))
    }

    func testPlaythroughDeleteThatFindsItGoneRefreshesFully() async {
        let playing = Self.gameDetail(state: Self.gameState(status: "In progress", playthroughStatus: "In progress"))
        let media = CountingMediaRepository(detail: playing)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["playthrough.delete"] = [.failure(APIError.httpStatus(404, nil))]
        let model = Self.model(media: media, tracking: tracking)
        model.detail = playing
        let playthrough = playing.userState!.game!.currentPlaythrough!

        let succeeded = await model.deleteGamePlaythrough(playthrough, for: playing).value
        await model.refreshFinished()

        XCTAssertTrue(succeeded)
        XCTAssertEqual(media.detailCount, 1)
    }

    func testStartedLineUsesTheCurrentPlaythrough() throws {
        let tracking = try JSONDecoder.api.decode(TrackingState.self, from: Self.gameTracking)
        let state = UserMediaState.untracked.applying(tracking)
        let lines = TrackingSummarySection.lines(detail: Self.gameDetail(state: nil), state: state, preferredMode: nil)
        let started = lines.filter { $0.hasPrefix("Started ") }
        XCTAssertEqual(started.count, 1)
        XCTAssertTrue(started.first?.contains("2024") == true)

        var pending = UserMediaState.untracked
        TrackingProjection.startGame(today: "2026-10-23")(&pending)
        let pendingLines = TrackingSummarySection.lines(detail: Self.gameDetail(state: nil), state: pending, preferredMode: nil)
        XCTAssertTrue(pendingLines.contains { $0.hasPrefix("Started ") && $0.contains("2026") })
    }

    func testSummaryLinesFollowTheViewerState() {
        let reading = UserMediaState.untracked.applying(TrackingState(
            trackingId: 8, status: "In progress", rating: "4.0",
            progress: ProgressState(kind: "pages", value: 120, max: 300, unit: "pages"), repeats: 0,
            startDate: "2026-10-01", endDate: nil, notes: nil, updatedAt: nil
        ))
        let lines = TrackingSummarySection.lines(detail: Self.bookDetail(state: nil), state: reading, preferredMode: .pages)
        XCTAssertTrue(lines.contains { $0.contains("120") }, "\(lines)")
        XCTAssertFalse(lines.contains { $0.hasPrefix("Rated ") }, "The rating has its own line under the status.")
        XCTAssertEqual(TrackingSummarySection.stars(detail: Self.bookDetail(state: nil), state: reading), "4")
        XCTAssertTrue(lines.contains { $0.hasPrefix("Started ") && $0.contains("2026") }, "\(lines)")

        var paused = reading
        TrackingProjection.pauseBook()(&paused)
        let pausedLines = TrackingSummarySection.lines(detail: Self.bookDetail(state: nil), state: paused, preferredMode: .pages)
        XCTAssertTrue(pausedLines.contains { $0.contains("120") }, "A paused book keeps its progress line.")

        let movie = UserMediaState.untracked.applying(Self.movieState(rating: "3.5"))
        let movieLines = TrackingSummarySection.lines(detail: Self.movieDetail, state: movie, preferredMode: nil)
        XCTAssertEqual(movieLines.filter { $0.hasPrefix("Started ") }, [])
        XCTAssertEqual(TrackingSummarySection.stars(detail: Self.movieDetail, state: movie), "3.5")
        XCTAssertEqual(TrackingSummarySection.lines(detail: Self.movieDetail, state: .untracked, preferredMode: nil), [])
        XCTAssertNil(TrackingSummarySection.stars(detail: Self.movieDetail, state: .untracked))
    }

    func testLoggedAlbumKeepsItsLogDateUnderTheRating() {
        let album = Self.musicDetail(title: "The Life of Pablo")
        let state = UserMediaState(
            isTracked: true, status: "Completed", rating: "3.5",
            diaryEntryId: 91, diaryCount: 1, diaryConsumedAt: "2023-03-24", hasLiked: true
        )
        let firstLine = TrackingSummarySection.lines(detail: album, state: state, preferredMode: nil).first

        XCTAssertEqual(TrackingSummarySection.stars(detail: album, state: state), "3.5")
        XCTAssertTrue(firstLine?.hasPrefix("Logged on ") == true && firstLine?.contains("24, 2023") == true, "\(String(describing: firstLine))")
    }

    func testComposerFoldsTheCompletionBeforeItClosesAndPostsOnceWithThePagesOrigin() async throws {
        let playing = Self.gameDetail(state: Self.gameState(status: "Paused", playthroughStatus: "Paused", rating: "4.5"))
        let media = CountingMediaRepository(detail: playing)
        let tracking = ScriptedTrackingRepository()
        tracking.completion = BookCompletionResponse(
            tracking: Self.gameState(status: "Completed", playthroughStatus: "Completed", rating: "4.5", liked: true),
            diaryEntry: try Self.entry(id: 120, consumedAt: Self.today, rating: "4.5")
        )
        let page = Self.model(media: media, tracking: tracking)
        page.detail = playing
        let posts = PostRecorder(ref: Self.gameRef)
        var statusWhenClosing: String?
        let composer = MediaLogViewModel(
            detail: playing,
            trackingRepository: tracking,
            diaryRepository: UnusedDiaryRepository(),
            completionJourneyId: 41,
            preselectedRatingSteps: 9,
            stateChangeOrigin: page.changeOrigin,
            onResult: { page.applyLogSave($0) },
            onUnauthorized: {},
            onSaved: { statusWhenClosing = page.viewer?.status }
        )

        let saved = await composer.save()
        await page.refreshFinished()

        XCTAssertTrue(saved)
        XCTAssertEqual(statusWhenClosing, "Completed", "The page shows the completion before the composer closes.")
        XCTAssertEqual(page.viewer?.rating, "4.5")
        XCTAssertEqual(page.viewer?.hasLiked, true)
        XCTAssertEqual(page.viewer?.diaryEntryId, 120)
        XCTAssertEqual(posts.origins, [page.changeOrigin])
        XCTAssertEqual(media.detailCount, 0)
        XCTAssertEqual(media.communityCount, 1)
        XCTAssertEqual(media.reviewPageCount, 1)
    }

    func testComposerDiaryLogCarriesNoTrackingSoThePageRefreshesFully() async throws {
        let detail = Self.movieDetail
        let media = CountingMediaRepository(detail: detail)
        let page = Self.model(ref: detail.ref, media: media, tracking: ScriptedTrackingRepository())
        page.detail = detail
        let diary = CreatingDiaryRepository(entry: try Self.entry(id: 130, consumedAt: Self.today, rating: nil))
        var results: [MediaLogSaveResult] = []
        let composer = MediaLogViewModel(
            detail: detail,
            trackingRepository: ScriptedTrackingRepository(),
            diaryRepository: diary,
            stateChangeOrigin: page.changeOrigin,
            onResult: { results.append($0); page.applyLogSave($0) },
            onUnauthorized: {},
            onSaved: {}
        )

        let saved = await composer.save()
        await page.refreshFinished()

        XCTAssertTrue(saved)
        XCTAssertEqual(results.count, 1)
        XCTAssertNil(results.first?.tracking)
        XCTAssertEqual(results.first?.diaryEntry?.id, 130)
        XCTAssertEqual(media.detailCount, 1)
    }

    func testSettledReportsWhetherTheWritesItWaitedForSucceeded() async {
        let detail = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.failure(APIError.httpStatus(503, nil))]
        let model = Self.model(media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        model.performQuickAction(.currently, for: detail)
        let settled = Task { await model.settled() }
        await Self.waitUntil { tracking.calls == ["game.start"] }
        await gate.open()

        let succeeded = await settled.value
        XCTAssertFalse(succeeded, "A screen routed by the failed start doesn't open.")
        let settledAgain = await model.settled()
        XCTAssertTrue(settledAgain, "An earlier failure doesn't block a later screen.")
    }

    func testSavedLogClearsRetainedMutationIds() async throws {
        let game = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.failure(APIError.network(URLError(.timedOut)))]
        let model = Self.model(media: CountingMediaRepository(detail: game), tracking: tracking)
        model.detail = game

        _ = await model.performQuickAction(.currently, for: game).value
        model.applyLogSave(MediaLogSaveResult(
            ref: game.ref,
            tracking: Self.gameState(status: "Completed", playthroughStatus: "Completed"),
            diaryEntry: try Self.entry(id: 120, consumedAt: Self.today, rating: nil)
        ))
        _ = await model.performQuickAction(.currently, for: game).value
        await model.refreshFinished()

        XCTAssertEqual(tracking.calls, ["game.start", "game.start"])
        XCTAssertNotEqual(tracking.actions[0].mutationId, tracking.actions[1].mutationId, "A replay isn't deduped against the lost start.")

        let book = Self.bookDetail(state: nil)
        let bookTracking = ScriptedTrackingRepository()
        bookTracking.responses["update"] = [.failure(APIError.httpStatus(503, nil))]
        let bookModel = Self.model(ref: book.ref, media: CountingMediaRepository(detail: book), tracking: bookTracking)
        bookModel.detail = book

        _ = await bookModel.performQuickAction(.currently, for: book).value
        bookModel.applyLogSave(MediaLogSaveResult(
            ref: book.ref,
            tracking: Self.bookState(status: "Completed"),
            diaryEntry: try Self.entry(id: 121, consumedAt: Self.today, rating: nil)
        ))
        _ = await bookModel.performQuickAction(.currently, for: book).value
        await bookModel.refreshFinished()

        XCTAssertEqual(bookTracking.updates.count, 2)
        XCTAssertNotEqual(bookTracking.updates[0].mutationId, bookTracking.updates[1].mutationId, "A reread isn't deduped against the lost start.")
    }

    func testBookCurrentlyReadingRetryReusesTheMutationId() async {
        let detail = Self.bookDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["update"] = [
            .failure(APIError.httpStatus(503, nil)),
            .success(Self.bookState(status: "In progress")),
        ]
        let model = Self.model(ref: detail.ref, media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail

        let first = await model.performQuickAction(.currently, for: detail).value
        let retry = await model.performQuickAction(.currently, for: detail).value

        XCTAssertFalse(first)
        XCTAssertTrue(retry)
        XCTAssertEqual(tracking.updates.count, 2)
        XCTAssertNotNil(tracking.updates[0].mutationId)
        XCTAssertEqual(tracking.updates[0].mutationId, tracking.updates[1].mutationId)
    }

    func testQueuedBookStartIsRoutedFromStateConfirmedAtSendAfterAFailure() async {
        let detail = Self.bookDetail(state: Self.bookState(status: "In progress"))
        let tracking = ScriptedTrackingRepository()
        tracking.responses["book.pause"] = [.failure(APIError.httpStatus(409, nil))]
        tracking.responses["update"] = [.success(Self.bookState(status: "In progress"))]
        let model = Self.model(ref: detail.ref, media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail
        let gate = RefreshGate()
        tracking.gate = gate

        let paused = model.performQuickAction(.paused, for: detail)
        let reading = model.performQuickAction(.currently, for: detail)
        XCTAssertEqual(model.viewer?.status, "In progress")
        await gate.open()
        let results = (await paused.value, await reading.value)

        XCTAssertFalse(results.0)
        XCTAssertTrue(results.1)
        XCTAssertEqual(tracking.calls, ["book.pause", "update"], "The rejected pause leaves the book In progress, so no resume is sent.")
        XCTAssertEqual(tracking.updates.last?.status, "In progress")
        XCTAssertEqual(model.viewer?.book?.currentJourney?.status, "In progress")
    }

    func testBookPausedOnAClosedJourneyAssignsTheStatus() async {
        let detail = Self.bookDetail(state: Self.bookState(status: "Dropped"))
        let tracking = ScriptedTrackingRepository()
        tracking.responses["update"] = [.success(Self.bookState(status: "Paused"))]
        let model = Self.model(ref: detail.ref, media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail

        let succeeded = await model.performQuickAction(.paused, for: detail).value

        XCTAssertTrue(succeeded)
        XCTAssertEqual(tracking.calls, ["update"], "The pause action only takes an open journey.")
        XCTAssertEqual(tracking.updates.first?.status, "Paused")
        XCTAssertEqual(model.viewer?.status, "Paused")
    }

    func testPendingIdsFailWithoutSendingWhenTheStartFails() async throws {
        let game = Self.gameDetail(state: nil)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.failure(APIError.httpStatus(503, nil))]
        let model = Self.model(media: CountingMediaRepository(detail: game), tracking: tracking)
        model.detail = game

        model.performQuickAction(.currently, for: game)
        let playthrough = try XCTUnwrap(model.viewer?.game?.currentPlaythrough)
        XCTAssertTrue(playthrough.isPending)
        let saved = await model.updateGamePlaythrough(playthrough, request: GamePlaythroughWriteRequest(totalMinutes: 90), for: game).value
        XCTAssertNotNil(model.formErrorMessage)
        let deleted = await model.deleteGamePlaythrough(playthrough, for: game).value

        XCTAssertFalse(saved)
        XCTAssertFalse(deleted)
        XCTAssertEqual(tracking.playthroughIds, [])
        XCTAssertEqual(tracking.calls, ["game.start"])

        let book = Self.bookDetail(state: nil)
        let bookTracking = ScriptedTrackingRepository()
        bookTracking.responses["update"] = [.failure(APIError.httpStatus(503, nil))]
        let bookModel = Self.model(ref: book.ref, media: CountingMediaRepository(detail: book), tracking: bookTracking)
        bookModel.detail = book

        bookModel.performQuickAction(.currently, for: book)
        let journey = try XCTUnwrap(bookModel.viewer?.book?.currentJourney)
        XCTAssertTrue(journey.isPending)
        let journeySaved = await bookModel.updateBookJourney(journey, startDate: Date(), endDate: nil, for: book).value
        XCTAssertNotNil(bookModel.formErrorMessage)
        let journeyDeleted = await bookModel.deleteBookJourney(journey, for: book).value

        XCTAssertFalse(journeySaved)
        XCTAssertFalse(journeyDeleted)
        XCTAssertEqual(bookTracking.calls, ["update"])
    }

    func testNetworkFailureRefreshesFully() async {
        let detail = Self.gameDetail(state: nil)
        let media = CountingMediaRepository(detail: detail)
        let tracking = ScriptedTrackingRepository()
        tracking.responses["game.start"] = [.failure(APIError.network(URLError(.networkConnectionLost)))]
        let model = Self.model(media: media, tracking: tracking)
        await model.load()
        media.response = Self.gameDetail(state: Self.gameState(status: "In progress", playthroughStatus: "In progress"))

        let succeeded = await model.performQuickAction(.currently, for: detail).value
        await model.refreshFinished()

        XCTAssertFalse(succeeded)
        XCTAssertEqual(media.detailCount, 2, "A lost response may have landed, so the page reloads.")
        XCTAssertEqual(model.viewer?.status, "In progress")
        XCTAssertEqual(model.viewer?.game?.currentPlaythrough?.id, 41)
    }

    func testHeartAppliesTheServerStateAndKeepsClientOnlyFields() async {
        let reading = TrackingState(
            trackingId: 8, status: "In progress", rating: nil,
            progress: ProgressState(kind: "pages", value: 120, max: 300, unit: "page"), repeats: 0,
            startDate: "2026-10-01", endDate: nil, notes: nil, updatedAt: nil,
            book: BookTrackingState(currentJourney: BookJourneyState(id: 9, status: "In progress", origin: "live", startDate: "2026-10-01"))
        )
        let detail = Self.bookDetail(state: reading)
        let media = CountingMediaRepository(detail: detail)
        var summary = MediaSummary(ref: detail.ref, title: "Fixture")
        summary.userState = UserMediaState(
            isTracked: true, trackingId: 8, status: "Completed", hasLiked: true,
            book: BookTrackingState(currentJourney: BookJourneyState(id: 9, status: "Completed", origin: "live", startDate: "2026-10-01"))
        )
        media.likeResponse = MediaLikeResponse(liked: true, media: summary)
        let model = Self.model(ref: detail.ref, media: media, tracking: ScriptedTrackingRepository())
        model.detail = detail

        let succeeded = await model.toggleMediaLike(for: detail).value
        await model.refreshFinished()

        XCTAssertTrue(succeeded)
        XCTAssertEqual(model.viewer?.hasLiked, true)
        XCTAssertEqual(model.viewer?.status, "Completed")
        XCTAssertEqual(model.viewer?.book?.currentJourney?.status, "Completed")
        XCTAssertEqual(model.viewer?.startDate, "2026-10-01", "The client-only start date survives the server's state.")
        XCTAssertEqual(model.viewer?.progress?.value, 120)
        XCTAssertEqual(media.communityCount, 1)
        XCTAssertEqual(media.detailCount, 0)
    }

    func testSingleWeightUnwatchWithLogsIsRefusedWithoutSending() async {
        var detail = Self.movieDetail
        detail.userState = UserMediaState(isTracked: true, trackingId: 10, status: "Completed", rating: "4.0", diaryCount: 2, hasLiked: true)
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(ref: detail.ref, media: CountingMediaRepository(detail: detail), tracking: tracking)
        model.detail = detail

        let removed = await model.removeTracking(for: detail).value

        XCTAssertFalse(removed)
        XCTAssertEqual(tracking.calls, [])
        XCTAssertEqual(model.viewer?.status, "Completed", "The page never shows an unwatch the server must refuse (SW-008).")
        XCTAssertEqual(model.viewer?.rating, "4.0")
        XCTAssertEqual(model.viewer?.hasLiked, true)
        XCTAssertEqual(model.quickActionErrorMessage, "Delete your logs before marking this as unwatched.")
    }

    /// TV-705: the episode tick now goes through the same write queue as the heart (it used to bypass it), so a
    /// heart tapped while the tick is sent shows at once, is sent after it, and both stay.
    func testEpisodeWatchKeepsAHeartThatConfirmedWhileItWasSent() async {
        let episode = Self.episodeDetail
        let media = CountingMediaRepository(detail: episode)
        media.detailError = APIError.httpStatus(503, nil)
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(ref: episode.ref, media: media, tracking: tracking)
        model.detail = episode
        let gate = RefreshGate()
        tracking.gate = gate

        let watch = model.tickTVEpisode(seasonNumber: 3, episodeNumber: 2)
        await Self.waitUntil { tracking.calls == ["tv.tick"] }
        let like = model.toggleMediaLike(for: episode)
        XCTAssertEqual(model.viewer?.hasLiked, true, "The heart shows while the tick is sent.")
        XCTAssertEqual(model.viewer?.isTracked, true)
        await gate.open()
        let watched = await watch.value
        let liked = await like.value
        await model.refreshFinished()

        XCTAssertTrue(liked)
        XCTAssertTrue(watched)
        XCTAssertEqual(tracking.calls, ["tv.tick"], "The heart goes through the likes endpoint after the tick.")
        XCTAssertEqual(model.detail?.userState?.isTracked, true)
        XCTAssertEqual(model.detail?.userState?.hasLiked, true, "The watch doesn't restore the state from before the heart.")
    }

    func testMusicEnrichmentKeepsAWriteMadeWhileItLoaded() async {
        let album = Self.musicDetail(title: "Album")
        let media = CountingMediaRepository(detail: album)
        media.enriched = Self.musicDetail(title: "Enriched album")
        let enrichment = RefreshGate()
        media.enrichmentGate = enrichment
        let tracking = ScriptedTrackingRepository()
        tracking.responses["update"] = [.success(Self.movieState(rating: "4.0"))]
        let model = Self.model(ref: album.ref, media: media, tracking: tracking)

        let loading = Task { await model.load() }
        await Self.waitUntil { media.enrichmentCount == 1 }
        _ = await model.setCurrentRating(for: album, halfSteps: 8).value
        await enrichment.open()
        await loading.value

        XCTAssertEqual(model.musicEnrichmentState, .loaded)
        XCTAssertEqual(model.detail?.title, "Enriched album")
        XCTAssertEqual(model.viewer?.rating, "4.0", "The enrichment doesn't bring back the state from before the rating.")

        model.scheduleRefresh(.full, after: .zero)
        await model.refreshFinished()

        XCTAssertEqual(media.detailCount, 2)
        XCTAssertEqual(model.detail?.title, "Enriched album", "A full refresh patches only the state, so the enrichment stays.")
    }

    func testMovedAlbumAdoptsItsAppleRefSkipsEnrichmentAndRefreshesAfterALog() async throws {
        let musicBrainzRef = Self.musicDetail(title: "SOS").ref
        let apple = Self.appleMusicDetail(title: "SOS")
        let media = CountingMediaRepository(detail: apple)
        let model = Self.model(ref: musicBrainzRef, media: media, tracking: ScriptedTrackingRepository())
        let observer = PageObserver(model: model)

        await model.load()

        XCTAssertEqual(model.ref, apple.ref, "The page adopts the ref the album answered with.")
        XCTAssertEqual(media.enrichmentCount, 0, "Apple Music albums have no enrichment stage.")
        XCTAssertEqual(model.musicEnrichmentState, .idle)

        // A log saved from another screen posts the album's Apple ref.
        MediaStateChange.post(ref: apple.ref, origin: UUID())
        await model.refreshFinished()
        XCTAssertEqual(observer.received, 1)
        XCTAssertEqual(media.detailCount, 2, "The page refreshes after a change to its album.")
        XCTAssertEqual(media.detailRefs.map(\.id), [musicBrainzRef.id, apple.ref.id])

        model.applyLogSave(MediaLogSaveResult(
            ref: apple.ref,
            tracking: Self.movieState(rating: "4.0"),
            diaryEntry: try Self.entry(id: 123, consumedAt: Self.today, rating: nil)
        ))
        await model.refreshFinished()
        XCTAssertEqual(model.viewer?.rating, "4.0", "The page's own log folds in without a full reload.")
        XCTAssertEqual(media.detailCount, 2)
    }

    func testBareAppleAlbumRetriesOnceAndFillsInTheWholePage() async {
        // While Apple Music fails, the server opens a saved album from its Item, with no music.
        let bare = Self.appleMusicDetail(title: "SOS", music: nil)
        let media = CountingMediaRepository(detail: Self.appleMusicDetail(title: "SOS"))
        media.queuedResponses = [bare]
        let model = Self.model(ref: bare.ref, media: media, tracking: ScriptedTrackingRepository())

        await model.load()
        await model.refreshFinished()

        XCTAssertEqual(media.detailCount, 2, "One quiet retry")
        XCTAssertNotNil(model.detail?.music, "The retry fills in the tracks, buttons and related albums.")
        XCTAssertEqual(media.enrichmentCount, 0)

        let stillBare = CountingMediaRepository(detail: bare)
        let page = Self.model(ref: bare.ref, media: stillBare, tracking: ScriptedTrackingRepository())
        await page.load()
        await page.refreshFinished()
        XCTAssertEqual(stillBare.detailCount, 2, "Only one retry")
        XCTAssertNil(page.detail?.music)

        stillBare.response = Self.appleMusicDetail(title: "SOS")
        page.scheduleRefresh(.full, after: .zero)
        await page.refreshFinished()
        XCTAssertNotNil(page.detail?.music, "A later refresh, after a log, fills it in too.")
    }

    func testAppleAlbumRefreshReplacesThePageButNeverWithTheBareOne() async {
        let album = Self.appleMusicDetail(title: "SOS")
        let media = CountingMediaRepository(detail: album)
        let model = Self.model(ref: album.ref, media: media, tracking: ScriptedTrackingRepository())
        await model.load()
        await model.refreshFinished()
        XCTAssertEqual(media.detailCount, 1, "A whole page doesn't retry.")

        media.response = Self.appleMusicDetail(title: "SOS, opened from its Item", music: nil)
        model.scheduleRefresh(.full, after: .zero)
        await model.refreshFinished()
        XCTAssertNotNil(model.detail?.music, "The bare page never replaces one that has its music.")
        XCTAssertEqual(model.detail?.title, "SOS")

        // A page that opened partial (say without its related albums) gets the whole page on its next refresh.
        media.response = Self.appleMusicDetail(title: "SOS, whole page")
        model.scheduleRefresh(.full, after: .zero)
        await model.refreshFinished()
        XCTAssertEqual(model.detail?.title, "SOS, whole page")
        XCTAssertEqual(media.enrichmentCount, 0)
    }

    /// TV-504: season stars go through the TV `rate` action, in whole steps on the 0-10 wire with the season number
    /// (they used to PATCH the season's tracking with no season number). Season pages still refresh the whole detail.
    func testSeasonPageWritesRefreshTheWholeDetail() async throws {
        let season = Self.seasonDetail(title: "Season 3")
        let media = CountingMediaRepository(detail: Self.seasonDetail(title: "Season 3, refreshed"))
        let tracking = ScriptedTrackingRepository()
        let model = Self.model(ref: season.ref, media: media, tracking: tracking)
        model.detail = season

        _ = await model.setCurrentRating(for: season, halfSteps: 8).value
        await model.refreshFinished()

        XCTAssertEqual(tracking.calls, ["tv.rate"])
        XCTAssertEqual(tracking.tvRequests.first?.rating, 8)
        XCTAssertEqual(tracking.tvRequests.first?.seasonNumber, 3)
        XCTAssertEqual(tracking.tvRequests.first?.includesRating, true)
        XCTAssertTrue(tracking.updates.isEmpty)
        XCTAssertEqual(media.detailCount, 1)
        XCTAssertEqual(media.communityCount, 0)
        XCTAssertEqual(model.detail?.title, "Season 3, refreshed", "Season pages replace the whole detail.")

        model.applyLogSave(MediaLogSaveResult(
            ref: season.ref,
            tracking: Self.movieState(rating: "4.0"),
            diaryEntry: try Self.entry(id: 122, consumedAt: Self.today, rating: nil)
        ))
        await model.refreshFinished()

        XCTAssertEqual(media.detailCount, 2)
        XCTAssertEqual(media.communityCount, 0)
    }

    func testBookComposerPostsOnceWithThePagesOriginAndFoldsTheCompletion() async throws {
        let reading = Self.bookDetail(state: Self.bookState(status: "In progress"))
        let media = CountingMediaRepository(detail: reading)
        let tracking = ScriptedTrackingRepository()
        tracking.completion = BookCompletionResponse(
            tracking: Self.bookState(status: "Completed"),
            diaryEntry: try Self.entry(id: 121, consumedAt: Self.today, rating: nil)
        )
        let page = Self.model(ref: reading.ref, media: media, tracking: tracking)
        page.detail = reading
        let posts = PostRecorder(ref: reading.ref)
        var statusWhenClosing: String?
        let composer = MediaLogViewModel(
            detail: reading,
            trackingRepository: tracking,
            diaryRepository: UnusedDiaryRepository(),
            completionJourneyId: 9,
            stateChangeOrigin: page.changeOrigin,
            onResult: { page.applyLogSave($0) },
            onUnauthorized: {},
            onSaved: { statusWhenClosing = page.viewer?.status }
        )

        let saved = await composer.save()
        await page.refreshFinished()

        XCTAssertTrue(saved)
        XCTAssertEqual(tracking.calls, ["book.complete_log"])
        XCTAssertEqual(statusWhenClosing, "Completed")
        XCTAssertEqual(page.viewer?.diaryEntryId, 121)
        XCTAssertEqual(posts.origins, [page.changeOrigin])
        XCTAssertEqual(media.detailCount, 0)
        XCTAssertEqual(media.communityCount, 1)
        XCTAssertEqual(media.reviewPageCount, 1)
    }

    // MARK: Pipeline helpers

    private static let gameRef = MediaRef(itemId: 301, source: "igdb", mediaType: "game", mediaId: "408819", seasonNumber: nil, episodeNumber: nil)
    private static let today = CalendarDateCodec.string(from: Date())

    private static func model(ref: MediaRef? = nil, media: MediaRepository, tracking: TrackingRepository) -> MediaDetailViewModel {
        MediaDetailViewModel(
            ref: ref ?? gameRef,
            mediaRepository: media,
            trackingRepository: tracking,
            diaryRepository: UnusedDiaryRepository(),
            onUnauthorized: {},
            writeRefreshDelay: .zero,
            externalRefreshDelay: .zero,
            bareAlbumRetryDelay: .zero
        )
    }

    private static func waitUntil(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<300 where !condition() {
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(condition(), "Timed out waiting for the condition.", file: file, line: line)
    }

    private static func detail(ref: String, state: TrackingState?, title: String = "Fixture", music: String? = nil) -> MediaDetail {
        var detail = try! JSONDecoder.api.decode(MediaDetail.self, from: Data("""
        {"ref":\(ref),"title":"\(title)","music":\(music ?? "null"),"subtitle":null,"overview":null,"image_url":null,"poster_accent_color":null,
         "release_date":null,"default_source":null,"user_state":null,"backdrop_url":null,"details":{},"related":{},
         "providers":null,"community":null,"external_ratings":null,"reviews":null,"cast":null,"crew":null,
         "related_sections":null,"episodes":null,"seasons":null,"custom_poster_url":null}
        """.utf8))
        detail.userState = state.map { UserMediaState.untracked.applying($0) }
        return detail
    }

    private static func gameDetail(state: TrackingState?) -> MediaDetail {
        detail(ref: #"{"item_id":301,"source":"igdb","media_type":"game","media_id":"408819","season_number":null,"episode_number":null}"#, state: state)
    }

    private static func bookDetail(state: TrackingState?) -> MediaDetail {
        detail(ref: #"{"item_id":302,"source":"openlibrary","media_type":"book","media_id":"OL1M","season_number":null,"episode_number":null}"#, state: state)
    }

    private static func musicDetail(title: String) -> MediaDetail {
        detail(ref: #"{"item_id":305,"source":"musicbrainz","media_type":"music","media_id":"album-1","season_number":null,"episode_number":null}"#, state: nil, title: title)
    }

    /// A whole Apple Music album page by default; `music: nil` is the page the server builds from the Item.
    private static func appleMusicDetail(title: String, music: String? = MediaDetailTrackingPipelineTests.appleMusic) -> MediaDetail {
        detail(ref: #"{"item_id":305,"source":"apple_music","media_type":"music","media_id":"1657869377","season_number":null,"episode_number":null}"#, state: nil, title: title, music: music)
    }

    private nonisolated static let appleMusic = #"{"release_group_mbid":"1657869377","content_rating":"explicit","cover_art":{"source":"apple_music","release_group_mbid":"1657869377","fallback_used":true}}"#

    private static func seasonDetail(title: String) -> MediaDetail {
        detail(ref: #"{"item_id":306,"source":"tmdb","media_type":"season","media_id":"1399","season_number":3,"episode_number":null}"#, state: nil, title: title)
    }

    private static let episodeDetail = detail(
        ref: #"{"item_id":307,"source":"tmdb","media_type":"episode","media_id":"1399","season_number":3,"episode_number":2}"#,
        state: nil
    )

    private static let movieDetail = detail(
        ref: #"{"item_id":303,"source":"tmdb","media_type":"movie","media_id":"550","season_number":null,"episode_number":null}"#,
        state: nil
    )

    private static func gameState(status: String, playthroughStatus: String?, rating: String? = nil, liked: Bool = false) -> TrackingState {
        let playthrough = playthroughStatus.map {
            #"{"id":41,"status":"\#($0)","origin":"live","start_date":"\#(today)","end_date":null,"total_minutes":null,"percentage":null,"completion_diary_entry_id":null,"is_replay":false}"#
        } ?? "null"
        let rating = rating.map { #""\#($0)""# } ?? "null"
        return try! JSONDecoder.api.decode(TrackingState.self, from: Data(#"""
        {"tracking_id":7,"status":"\#(status)","rating":\#(rating),"start_date":"\#(today)T09:30:00Z","liked":\#(liked),"diary_count":0,"game":{
        "current_playthrough":\#(playthrough),"play_history":[],"undated_completion":null,"status_source":"playthrough",
        "completion_dates":[],"completed_playthrough_count":0,"lifetime_completion_count":0,"is_replaying":false,
        "can_remove_tracking":false,"available_actions":[],"action_reasons":{},"imported_lifetime_minutes":null,"imported_lifetime_source":null}}
        """#.utf8))
    }

    private static func bookState(status: String) -> TrackingState {
        TrackingState(
            trackingId: 8, status: status, rating: nil, progress: nil, repeats: 0,
            startDate: "2026-10-01", endDate: nil, notes: nil, updatedAt: nil,
            book: BookTrackingState(currentJourney: BookJourneyState(id: 9, status: status, origin: "live", startDate: "2026-10-01"))
        )
    }

    private static func movieState(rating: String?) -> TrackingState {
        TrackingState(
            trackingId: 10, status: "Completed", rating: rating, progress: nil, repeats: 0,
            startDate: nil, endDate: nil, notes: nil, updatedAt: nil, liked: false
        )
    }

    // MARK: Fixtures

    private static let loggedState = UserMediaState(
        isTracked: false,
        diaryEntryId: 90,
        diaryCount: 2,
        diaryRating: "4.0",
        diaryConsumedAt: "2026-03-01",
        inLists: [3, 5],
        hasLiked: true,
        ratingSourceDiaryEntryId: 90,
        likeSourceDiaryEntryId: 90
    )

    private static func entry(id: Int, consumedAt: String, rating: String?) throws -> DiaryEntry {
        let rating = rating.map { #""\#($0)""# } ?? "null"
        return try JSONDecoder.api.decode(DiaryEntry.self, from: Data(#"""
        {"id":\#(id),"user":{"id":1,"username":"test","display_name":"Test"},"media":{"ref":{"source":"igdb","media_type":"game","media_id":"42"},"title":"Test game"},"consumed_at":"\#(consumedAt)","rating":\#(rating),"contains_spoilers":false,"liked":false,"is_rewatch":false,"tags":[],"visibility":"public","like_count":0,"viewer_has_liked":false}
        """#.utf8))
    }

    private static let gameTracking = Data(#"""
    {"tracking_id":7,"status":"In progress","start_date":"2024-01-01T09:30:00Z","liked":false,"diary_count":2,"game":{
    "current_playthrough":{"id":41,"status":"In progress","origin":"live","start_date":"2024-01-01","end_date":null,"total_minutes":null,"percentage":0,"completion_diary_entry_id":null,"is_replay":true},
    "play_history":[],"undated_completion":null,
    "status_source":"playthrough","completion_dates":["2023-05-01"],"completed_playthrough_count":1,"lifetime_completion_count":1,
    "is_replaying":true,"can_remove_tracking":false,"available_actions":["pause","drop"],"action_reasons":{},"imported_lifetime_minutes":null,"imported_lifetime_source":null}}
    """#.utf8)
}

private final class PostRecorder: @unchecked Sendable {
    private(set) var origins: [UUID?] = []
    private var observer: NSObjectProtocol?

    init(ref: MediaRef) {
        observer = NotificationCenter.default.addObserver(forName: .mediaStateDidChange, object: nil, queue: nil) { [weak self] note in
            guard (note.userInfo?["ref"] as? MediaRef)?.id == ref.id else { return }
            self?.origins.append(note.userInfo?["origin"] as? UUID)
        }
    }

    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
    }
}

/// Forwards MediaStateChange posts to the page's handler, as MediaDetailView's observer does.
private final class PageObserver: @unchecked Sendable {
    private(set) var received = 0
    private var observer: NSObjectProtocol?

    init(model: MediaDetailViewModel) {
        observer = NotificationCenter.default.addObserver(forName: .mediaStateDidChange, object: nil, queue: nil) { [weak self] note in
            self?.received += 1
            MainActor.assumeIsolated { model.handleMediaStateChange(note.userInfo) }
        }
    }

    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
    }
}

/// Records each tracking call; every call can wait on `gate`, and answers from `responses` in order.
@MainActor
private final class ScriptedTrackingRepository: TrackingRepository {
    var gate: RefreshGate?
    var responses: [String: [Result<TrackingState?, Error>]] = [:]
    var completion: BookCompletionResponse?
    private(set) var calls: [String] = []
    private(set) var actions: [BookActionRequest] = []
    private(set) var updates: [TrackingWriteRequest] = []
    private(set) var playthroughIds: [Int] = []
    private(set) var tvRequests: [TVActionRequest] = []

    private static let fallback = TrackingState(
        trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil,
        startDate: nil, endDate: nil, notes: nil, updatedAt: nil
    )

    func count(_ call: String) -> Int { calls.filter { $0 == call }.count }

    /// Unscripted calls answer a generic state, or nil (a 204) when `untracked`.
    private func respond(_ call: String, untracked: Bool = false) async throws -> TrackingState? {
        calls.append(call)
        await gate?.wait()
        guard var queue = responses[call], !queue.isEmpty else { return untracked ? nil : Self.fallback }
        let result = queue.removeFirst()
        responses[call] = queue
        return try result.get()
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        fatalError("Not used")
    }

    func detail(ref: MediaRef) async throws -> TrackingState {
        calls.append("tracking.detail")
        return Self.fallback
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        updates.append(request)
        return try await respond("update") ?? Self.fallback
    }

    func delete(ref: MediaRef) async throws {
        _ = try await respond("delete", untracked: true)
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState {
        try await respond("consume") ?? Self.fallback
    }

    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }

    func watchEpisode(source: String, mediaId: String, seasonNumber: Int, episodeNumber: Int, watchedAt: Date?) async throws -> TrackingState {
        try await respond("episode.watch") ?? Self.fallback
    }

    /// A TV action answers with the state of the episode it names, ticked or not.
    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        tvRequests.append(request)
        _ = try await respond("tv.\(action.rawValue)")
        let episode = request.episodeNumber.flatMap { number in
            request.seasonNumber.map {
                TVEpisodeState(seasonNumber: $0, episodeNumber: number, watched: action == .tick,
                               viewingCount: action == .tick ? 1 : 0, untickViewingId: action == .tick ? 1 : nil)
            }
        }
        return TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(episode: episode, changed: true))
    }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        try await respond("book.progress") ?? Self.fallback
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        try await respond("book.complete") ?? Self.fallback
    }

    func performBookAction(source: String, mediaId: String, action: String, request: BookActionRequest) async throws -> TrackingState? {
        actions.append(request)
        return try await respond("book.\(action)")
    }

    func undoBookRead(source: String, mediaId: String) async throws -> TrackingState? {
        try await respond("book.undo_read", untracked: true)
    }

    func updateBookJourney(source: String, mediaId: String, journeyId: Int, request: BookJourneyWriteRequest) async throws -> TrackingState {
        try await respond("journey.update") ?? Self.fallback
    }

    func deleteBookJourney(source: String, mediaId: String, journeyId: Int) async throws -> TrackingState? {
        try await respond("journey.delete")
    }

    func performGameAction(ref: MediaRef, action: String, request: BookActionRequest) async throws -> TrackingState? {
        actions.append(request)
        return try await respond("game.\(action)")
    }

    func updateGamePlaythrough(ref: MediaRef, playthroughId: Int, request: GamePlaythroughWriteRequest) async throws -> TrackingState {
        playthroughIds.append(playthroughId)
        return try await respond("playthrough.update") ?? Self.fallback
    }

    func deleteGamePlaythrough(ref: MediaRef, playthroughId: Int) async throws -> TrackingState? {
        playthroughIds.append(playthroughId)
        return try await respond("playthrough.delete")
    }

    func completeBook(source: String, mediaId: String, request: BookCompletionWriteRequest) async throws -> BookCompletionResponse {
        calls.append("book.complete_log")
        guard let completion else { fatalError("Not scripted") }
        return completion
    }

    func completeGame(ref: MediaRef, request: GameCompletionWriteRequest) async throws -> BookCompletionResponse {
        calls.append("game.complete")
        await gate?.wait()
        guard let completion else { fatalError("Not scripted") }
        return completion
    }
}

/// Counts detail, community and review requests. A detail call waits on the gate keyed by its call number.
@MainActor
private final class CountingMediaRepository: MediaRepository {
    var response: MediaDetail
    var detailGates: [Int: RefreshGate] = [:]
    var detailError: Error?
    var reviewGates: [Int: RefreshGate] = [:]
    var enriched: MediaDetail?
    var enrichmentGate: RefreshGate?
    var likeGate: RefreshGate?
    var likeError: Error?
    var likeResponse: MediaLikeResponse?
    /// Answers detail calls in turn before `response`.
    var queuedResponses: [MediaDetail] = []
    private(set) var detailCount = 0
    private(set) var detailRefs: [MediaRef] = []
    private(set) var communityCount = 0
    private(set) var reviewPageCount = 0
    private(set) var enrichmentCount = 0

    init(detail: MediaDetail) {
        response = detail
    }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailCount += 1
        detailRefs.append(ref)
        let detail = queuedResponses.isEmpty ? response : queuedResponses.removeFirst()
        await detailGates[detailCount]?.wait()
        if let detailError { throw detailError }
        return detail
    }

    func enrichedMusicDetail(ref: MediaRef) async throws -> MediaDetail {
        enrichmentCount += 1
        await enrichmentGate?.wait()
        return enriched ?? response
    }

    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }

    func reviewPage(ref: MediaRef, page: String?) async throws -> PagedResponse<MediaReview> {
        reviewPageCount += 1
        await reviewGates[reviewPageCount]?.wait()
        return PagedResponse(count: 2, next: nil, previous: nil, results: [])
    }

    func community(ref: MediaRef) async throws -> CommunityStats {
        communityCount += 1
        return CommunityStats(averageRating: "4.5", ratingCount: 1, diaryCount: 1, reviewCount: 0, likedCount: 1)
    }

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        await likeGate?.wait()
        if let likeError { throw likeError }
        return likeResponse ?? MediaLikeResponse(liked: liked, media: nil)
    }

    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

private struct CreatingDiaryRepository: DiaryRepository {
    let entry: DiaryEntry

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { entry }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private struct UnusedDiaryRepository: DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}
