import XCTest
@testable import Spine

/// The show, season and episode pages, their prompts and the TV composer (implementation plan 1.8). Each test
/// names the law it checks, as the book and game contract tests do.
@MainActor
final class TVTrackingPagesTests: XCTestCase {
    private var tracking: PagesTrackingRepository!
    private var media: PagesMediaRepository!

    private var heldRefreshes: [RefreshGate] = []

    override func setUp() async throws {
        try await super.setUp()
        tracking = PagesTrackingRepository()
        media = PagesMediaRepository()
    }

    override func tearDown() async throws {
        for gate in heldRefreshes { await gate.open() }
        heldRefreshes = []
        try await super.tearDown()
    }

    /// Holds the refresh that follows each write, so a test reads the write's own answer rather than the fixture
    /// the refresh would reload. Released in tearDown.
    private func holdRefreshes() {
        let gate = RefreshGate()
        media.detailGate = gate
        heldRefreshes.append(gate)
    }

    // MARK: Ticks (TV-100, TV-102, TV-705)

    func testTV100TickGoesThroughTheQueueShowsAtOnceAndRollsBackOnFailure() async throws {
        let e1 = Fixtures.episode(1)
        let page = model(Fixtures.seasonDetail(episodes: [e1]))
        let gate = RefreshGate()
        tracking.gate = gate
        tracking.responses = [.failure(Fixtures.unavailable)]

        page.toggleTVEpisode(e1)

        XCTAssertTrue(page.viewer?.tvIsWatched(e1) == true, "The tick shows before the server answers.")
        XCTAssertTrue(page.hasPendingWrites)
        await gate.open()
        let failed = await page.settled()
        XCTAssertFalse(failed)
        XCTAssertFalse(page.viewer?.tvIsWatched(e1) == true, "A failure restores the last confirmed state.")
        XCTAssertEqual(page.quickActionErrorMessage, "TV details are unavailable right now")

        let sent = try XCTUnwrap(tracking.sent.first)
        XCTAssertEqual(sent.action, .tick)
        XCTAssertEqual(sent.source, "tmdb")
        XCTAssertEqual(sent.mediaId, "1399", "Actions go to the show.")
        XCTAssertEqual(sent.request.seasonNumber, 1)
        XCTAssertEqual(sent.request.episodeNumber, 1)
        XCTAssertNotNil(sent.request.mutationId)

        tracking.responses = [.success(Fixtures.envelopeResponse(episode: Fixtures.episode(1, watched: true, viewing: 41)))]
        let result1 = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 1).value
        XCTAssertTrue(result1)
        XCTAssertEqual(tracking.sent[1].request.mutationId, sent.request.mutationId, "A retry keeps its mutation ID.")
    }

    func testTV705AConfirmedTickStaysWatchedUntilTheRefreshReplacesTheList() async throws {
        let e1 = Fixtures.episode(1)
        let stale = Fixtures.seasonDetail(episodes: [e1])
        let fresh = Fixtures.seasonDetail(episodes: [Fixtures.episode(1, watched: true, viewing: 41)])
        media.details = [fresh]
        let refreshGate = RefreshGate()
        media.detailGate = refreshGate
        let page = model(stale)
        tracking.responses = [.success(Fixtures.envelopeResponse(episode: Fixtures.episode(1, watched: true, viewing: 41)))]

        let result2 = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 1).value
        XCTAssertTrue(result2)

        XCTAssertEqual(page.detail?.episodes?.first?.tv?.watched, false, "The loaded list predates the tick.")
        let shown = try XCTUnwrap(page.tvEpisodeState(page.detail?.episodes?.first?.tv))
        XCTAssertTrue(page.viewer?.tvIsWatched(shown) == true, "The confirmed tick doesn't flicker back.")
        XCTAssertEqual(page.confirmedTVEpisode(seasonNumber: 1, episodeNumber: 1)?.untickViewingId, 41)

        await refreshGate.open()
        await page.refreshFinished()
        XCTAssertEqual(page.tvConfirmed, TVConfirmedWrites(), "The fresh list takes over.")
        XCTAssertEqual(page.detail?.episodes?.first?.tv?.watched, true)
    }

    func testTV102UntickSendsTheViewingTheEpisodeNames() async throws {
        let e2 = Fixtures.episode(2, watched: true, viewing: 77)
        let page = model(Fixtures.seasonDetail(episodes: [e2]))
        tracking.responses = [.success(Fixtures.envelopeResponse(episode: Fixtures.episode(2)))]

        page.toggleTVEpisode(e2)
        XCTAssertFalse(page.viewer?.tvIsWatched(e2) == true, "The untick shows at once.")
        let result3 = await page.settled()
        XCTAssertTrue(result3)

        let sent = try XCTUnwrap(tracking.sent.first)
        XCTAssertEqual(sent.action, .untick)
        XCTAssertEqual(sent.request.viewingId, 77)
        XCTAssertEqual(sent.request.seasonNumber, 1)
        XCTAssertEqual(sent.request.episodeNumber, 2)
        XCTAssertNotNil(sent.request.mutationId)
    }

    func testTV102AnUntickBlockedByALogSaysWhichLogAndSendsNothing() {
        let e3 = Fixtures.episode(3, watched: true, viewing: nil, untickMessage: "Delete your Season 1 log first.")
        let page = model(Fixtures.seasonDetail(episodes: [e3]))

        page.toggleTVEpisode(e3)

        XCTAssertEqual(page.tvNotice, "Delete your Season 1 log first.")
        XCTAssertTrue(tracking.sent.isEmpty)
        XCTAssertFalse(page.hasPendingWrites)
    }

    func testTV102AnUntickQueuedBehindItsTickNamesTheTicksViewing() async throws {
        let e1 = Fixtures.episode(1)
        let page = model(Fixtures.seasonDetail(episodes: [e1]))
        let gate = RefreshGate()
        tracking.gate = gate
        tracking.responses = [
            .success(Fixtures.envelopeResponse(episode: Fixtures.episode(1, watched: true, viewing: 41))),
            .success(Fixtures.envelopeResponse(episode: Fixtures.episode(1))),
        ]

        page.toggleTVEpisode(e1)
        page.toggleTVEpisode(e1)
        XCTAssertFalse(page.viewer?.tvIsWatched(e1) == true)
        await gate.open()
        let result4 = await page.settled()
        XCTAssertTrue(result4)

        XCTAssertEqual(tracking.sent.map(\.action), [.tick, .untick])
        XCTAssertEqual(tracking.sent[1].request.viewingId, 41, "The untick reads the tick's confirmed answer when sent.")
    }

    func testTV100TheEpisodeEyeTicksAndUnticks() async throws {
        holdRefreshes()
        let page = model(Fixtures.episodeDetail(Fixtures.episode(3)))
        tracking.responses = [
            .success(Fixtures.envelopeResponse(episode: Fixtures.episode(3, watched: true, viewing: 5))),
            .success(Fixtures.envelopeResponse(episode: Fixtures.episode(3))),
        ]

        page.tapTVEye()
        let result5 = await page.settled()
        XCTAssertTrue(result5)
        XCTAssertEqual(tracking.sent.last?.action, .tick)
        XCTAssertEqual(page.viewer?.tv?.episode?.watched, true)
        XCTAssertEqual(page.viewer?.isTracked, true)

        page.tapTVEye()
        let result6 = await page.settled()
        XCTAssertTrue(result6)
        XCTAssertEqual(tracking.sent.last?.action, .untick)
        XCTAssertEqual(tracking.sent.last?.request.viewingId, 5)
        XCTAssertEqual(page.viewer?.tv?.episode?.watched, false)
    }

    func testTV100AnUnairedEpisodeSaysSoInsteadOfTicking() {
        let page = model(Fixtures.episodeDetail(Fixtures.episode(9, aired: false, airDate: "2027-03-12")))

        page.tapTVEye()

        XCTAssertEqual(page.tvNotice, "This episode hasn't aired yet.")
        XCTAssertTrue(tracking.sent.isEmpty)
    }

    func testTV105TheUpNextRowTicksThatEpisode() async throws {
        holdRefreshes()
        let upNext = TVEpisodePointer(seasonNumber: 2, episodeNumber: 4, title: "Fly")
        let page = model(Fixtures.showDetail(tv: Fixtures.show(upNext: upNext)))
        var after = Fixtures.show(upNext: TVEpisodePointer(seasonNumber: 2, episodeNumber: 5))
        after.watchedCount = 14
        tracking.responses = [.success(TVActionResponse(tracking: Fixtures.showTracking, envelope: TVWriteEnvelope(tv: after, changed: true)))]

        page.toggleTVUpNext(upNext)
        XCTAssertEqual(page.viewer?.tvPending.episodes[upNext.coordinate], true)
        let result7 = await page.settled()
        XCTAssertTrue(result7)

        XCTAssertEqual(tracking.sent.first?.action, .tick)
        XCTAssertEqual(tracking.sent.first?.request.seasonNumber, 2)
        XCTAssertEqual(tracking.sent.first?.request.episodeNumber, 4)
        XCTAssertEqual(page.viewer?.tv?.upNext?.episodeNumber, 5, "Up next moves with the server's answer.")
        XCTAssertEqual(page.viewer?.tv?.watchedCount, 14)
    }

    // MARK: Follow-up (TV-107, TV-403)

    func testTV107AndTV403ATicksEnvelopeGoesToTheFollowUp() async throws {
        let page = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(10)]))
        media.details = [Fixtures.seasonDetail(episodes: [Fixtures.episode(10, watched: true, viewing: 3)])]
        tracking.responses = [.success(Fixtures.envelopeResponse(
            episode: Fixtures.episode(10, watched: true, viewing: 3),
            finished: Fixtures.finished(season: 1),
            earlierUnwatched: Fixtures.question
        ))]

        let result8 = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 10).value
        XCTAssertTrue(result8)

        XCTAssertEqual(page.tvFollowUp.question?.question, "Also mark S1 E1–S1 E9 watched?")
        XCTAssertNil(page.tvFollowUp.composer, "The question comes before the composer.")
        await page.tvFollowUp.answer(yes: false)
        XCTAssertEqual(page.tvFollowUp.composer?.finished.seasonNumber, 1)
        XCTAssertEqual(page.tvFollowUp.composer?.finished.composer.initialTarget?.date, "2026-10-24")
        XCTAssertEqual(tracking.sent.map(\.action), [.tick], "No sends nothing.")
    }

    func testTV403ASavedLogsFollowUpWaitsForItsComposerToClose() async throws {
        let page = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(10)]))
        let entry = try Fixtures.entry()

        page.applyLogSave(MediaLogSaveResult(
            ref: Fixtures.episodeRef(10),
            tracking: nil,
            diaryEntry: entry,
            tvEnvelope: TVWriteEnvelope(tv: Fixtures.show(), changed: true, finished: Fixtures.finished(season: 1))
        ))
        XCTAssertNil(page.tvFollowUp.composer)
        XCTAssertNil(page.tvFollowUp.loading, "Nothing opens over the closing composer.")

        page.releaseDeferredTVFollowUp()
        await page.tvFollowUp.loading?.value
        XCTAssertEqual(page.tvFollowUp.composer?.finished.seasonNumber, 1)

        page.tvFollowUp.composerDismissed()
        page.releaseDeferredTVFollowUp()
        XCTAssertNil(page.tvFollowUp.composer, "The envelope is handed over once.")
    }

    func testTV303AHeartThatStartsTheShowAsksAboutEarlierEpisodes() async throws {
        holdRefreshes()
        var season = Fixtures.season()
        season.started = true
        season.liked = true
        season.status = "Completed"
        season.state = .watched
        media.likeResponse = MediaLikeResponse(
            liked: true,
            media: nil,
            tv: Fixtures.show(),
            season: season,
            earlierUnwatched: Fixtures.question
        )
        let page = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(1)]))

        let liked = await page.toggleMediaLike(for: page.detail!).value

        XCTAssertTrue(liked)
        XCTAssertEqual(media.likeRequests.map(\.liked), [true])
        XCTAssertEqual(page.tvFollowUp.question?.question, "Also mark S1 E1–S1 E9 watched?")
        XCTAssertEqual(page.viewer?.hasLiked, true)
        XCTAssertEqual(page.viewer?.status, "Completed", "The season heart marked the season watched.")
        XCTAssertEqual(page.viewer?.tv?.season?.state, .watched)
    }

    // MARK: Eyes (TV-300, TV-302)

    func testTV300ASeasonEyeMarksWithoutADialog() async throws {
        let page = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(1)], eye: TVEyeState(filled: false, tap: .mark)))

        page.tapTVEye()

        XCTAssertNil(page.tvConfirmation)
        XCTAssertTrue(page.viewer.map { $0.tvIsMarked($0.tv!.season!) } == true, "The mark shows at once.")
        let result9 = await page.settled()
        XCTAssertTrue(result9)
        XCTAssertEqual(tracking.sent.first?.action, .markWatched)
        XCTAssertEqual(tracking.sent.first?.request.seasonNumber, 1)
    }

    func testTV300TheShowEyeAsksFirstWithTheServersCount() async throws {
        let eye = TVEyeState(filled: false, tap: .markConfirm, count: 62, prompt: "Mark all 62 aired episodes watched?")
        let page = model(Fixtures.showDetail(tv: Fixtures.show(eye: eye)))
        tracking.responses = [.success(TVActionResponse(tracking: Fixtures.showTracking, envelope: TVWriteEnvelope(tv: Fixtures.show(), changed: true)))]

        page.tapTVEye()

        let confirmation = try XCTUnwrap(page.tvConfirmation)
        XCTAssertEqual(confirmation.prompt, "Mark all 62 aired episodes watched?")
        XCTAssertEqual(confirmation.confirmTitle, "Mark Watched")
        XCTAssertTrue(tracking.sent.isEmpty, "Nothing is sent before the confirmation.")

        page.confirmTV(confirmation)
        XCTAssertNil(page.tvConfirmation)
        let result10 = await page.settled()
        XCTAssertTrue(result10)
        XCTAssertEqual(tracking.sent.first?.action, .markWatched)
        XCTAssertNil(tracking.sent.first?.request.seasonNumber)
    }

    func testTV302MarkFillsUnmarkWithoutADialog() async throws {
        holdRefreshes()
        let page = model(Fixtures.showDetail(tv: Fixtures.show(eye: TVEyeState(filled: true, tap: .unmarkMarks))))
        tracking.responses = [.success(TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(tv: TVShowState(), changed: true)))]

        page.tapTVEye()

        XCTAssertNil(page.tvConfirmation)
        let result11 = await page.settled()
        XCTAssertTrue(result11)
        let sent = try XCTUnwrap(tracking.sent.first)
        XCTAssertEqual(sent.action, .unmark)
        XCTAssertNil(sent.request.confirm)
        XCTAssertNil(sent.request.expectedVersion)
        XCTAssertEqual(page.viewer?.isTracked, false, "TV-208: an undo that leaves nothing removes the show.")
    }

    func testTV302CaseThreeConfirmsAndSendsTheVersionItShowed() async throws {
        let eye = TVEyeState(filled: true, tap: .unmarkConfirm, count: 62, prompt: "Mark all 62 episodes unwatched?")
        let page = model(Fixtures.showDetail(tv: Fixtures.show(version: 7, eye: eye)))
        tracking.responses = [.success(TVActionResponse(tracking: Fixtures.showTracking, envelope: TVWriteEnvelope(tv: Fixtures.show(), changed: true)))]

        page.tapTVEye()
        let confirmation = try XCTUnwrap(page.tvConfirmation)
        XCTAssertEqual(confirmation.prompt, "Mark all 62 episodes unwatched?")
        XCTAssertEqual(confirmation.confirmTitle, "Mark Unwatched")
        XCTAssertTrue(confirmation.isDestructive)
        page.confirmTV(confirmation)
        let result12 = await page.settled()
        XCTAssertTrue(result12)

        let sent = try XCTUnwrap(tracking.sent.first)
        XCTAssertEqual(sent.action, .unmark)
        XCTAssertEqual(sent.request.confirm, true)
        XCTAssertEqual(sent.request.expectedVersion, 7)
        XCTAssertNotNil(sent.request.mutationId)
    }

    func testTV302BlockedAndNothingAiredEyesExplainAndSendNothing() {
        let blocked = model(Fixtures.showDetail(tv: Fixtures.show(eye: TVEyeState(filled: true, tap: .blocked, message: "Delete your show log first."))))
        blocked.tapTVEye()
        XCTAssertEqual(blocked.tvNotice, "Delete your show log first.")

        let unaired = model(Fixtures.seasonDetail(
            episodes: [],
            eye: TVEyeState(filled: false, tap: .nothingAired, message: "S1 premieres Mar 12")
        ))
        unaired.tapTVEye()
        XCTAssertEqual(unaired.tvNotice, "S1 premieres Mar 12")
        XCTAssertTrue(tracking.sent.isEmpty)
    }

    // MARK: Stale counts (TV-705)

    func testTV705AStaleUnmarkAsksAgainWithTheNewCount() async throws {
        let eye = TVEyeState(filled: true, tap: .unmarkConfirm, count: 62, prompt: "Mark all 62 episodes unwatched?")
        let page = model(Fixtures.showDetail(tv: Fixtures.show(version: 7, eye: eye)))
        tracking.responses = [
            .failure(Fixtures.stateChanged(version: 9, count: 61)),
            .success(TVActionResponse(tracking: Fixtures.showTracking, envelope: TVWriteEnvelope(tv: Fixtures.show(), changed: true))),
        ]

        page.tapTVEye()
        page.confirmTV(try XCTUnwrap(page.tvConfirmation))
        let result13 = await page.settled()
        XCTAssertFalse(result13)
        await page.refreshFinished()

        let again = try XCTUnwrap(page.tvConfirmation, "The page asks again.")
        XCTAssertEqual(again.prompt, "Mark all 61 episodes unwatched?")
        XCTAssertEqual(again.expectedVersion, 9)
        XCTAssertNil(page.quickActionErrorMessage, "A stale count is asked again, not reported as a failure.")
        XCTAssertEqual(media.detailRequests.count, 1, "The page refreshes.")

        page.confirmTV(again)
        let result14 = await page.settled()
        XCTAssertTrue(result14)
        XCTAssertEqual(tracking.sent[1].request.expectedVersion, 9)
        XCTAssertNotEqual(tracking.sent[1].request.mutationId, tracking.sent[0].request.mutationId)
    }

    func testTV705AStaleRemoveAsksAgainWithTheNewCount() async throws {
        let page = model(Fixtures.showDetail(tv: Fixtures.show(version: 7)))
        tracking.responses = [.failure(Fixtures.stateChanged(version: 9, count: 14))]

        let removal = page.removeTVShow(expectedVersion: 7)
        XCTAssertEqual(page.viewer?.isTracked, false, "Removal shows at once.")
        let result15 = await removal.value
        XCTAssertFalse(result15)

        XCTAssertEqual(page.viewer?.isTracked, true, "A refused removal restores the show.")
        let again = try XCTUnwrap(page.tvConfirmation)
        XCTAssertEqual(again.kind, .remove)
        XCTAssertEqual(again.prompt, "Remove this show? Your 14 watched episodes will be unmarked.")
        XCTAssertEqual(again.expectedVersion, 9)
    }

    func testTV003RemoveSendsTheVersionAndAMutationID() async throws {
        holdRefreshes()
        let page = model(Fixtures.showDetail(tv: Fixtures.show(version: 7)))
        tracking.responses = [.success(TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(tv: TVShowState(), changed: true)))]

        let result16 = await page.removeTVShow(expectedVersion: 7).value
        XCTAssertTrue(result16)

        let sent = try XCTUnwrap(tracking.sent.first)
        XCTAssertEqual(sent.action, .remove)
        XCTAssertEqual(sent.request.expectedVersion, 7)
        XCTAssertNotNil(sent.request.mutationId)
        XCTAssertEqual(page.viewer?.isTracked, false)
        XCTAssertEqual(page.viewer?.tv?.inLibrary, false)
    }

    // MARK: + menu (TV-005, TV-007, TV-600)

    func testTV007PauseShowsAtOnceAndDropSendsDrop() async throws {
        let page = model(Fixtures.showDetail(tv: Fixtures.show()))
        let gate = RefreshGate()
        tracking.gate = gate

        page.performTVAction(.pause)
        XCTAssertEqual(page.viewer?.status, "Paused", "Pause saves at once.")
        page.performTVAction(.drop)
        XCTAssertEqual(page.viewer?.status, "Dropped")
        await gate.open()
        _ = await page.settled()

        XCTAssertEqual(tracking.sent.map(\.action), [.pause, .drop])
        XCTAssertEqual(TVTrackingPresentation.dropPrompt, "Drop this show? Your progress will be kept.")
    }

    func testTV600RewatchSeasonSendsRewatchWithItsSeason() async throws {
        let page = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(1, watched: true, viewing: 3)]))

        let result17 = await page.performTVAction(.rewatch, seasonNumber: 1).value
        XCTAssertTrue(result17)

        XCTAssertEqual(tracking.sent.first?.action, .rewatch)
        XCTAssertEqual(tracking.sent.first?.request.seasonNumber, 1)
    }

    func testTV108MarkWatchedUpToHereAsksThenSendsTheRange() async throws {
        let e1 = Fixtures.episode(1)
        let e4 = Fixtures.episode(4, markUpToPrompt: "Mark S1 E1–S1 E4 watched?")
        let page = model(Fixtures.seasonDetail(episodes: [e1, Fixtures.episode(2), Fixtures.episode(3), e4]))
        let gate = RefreshGate()
        tracking.gate = gate

        page.askMarkTVUpTo(Fixtures.episode(2))
        XCTAssertNil(page.tvConfirmation, "Not offered when nothing would be marked.")
        page.askMarkTVUpTo(e4)
        let confirmation = try XCTUnwrap(page.tvConfirmation)
        XCTAssertEqual(confirmation.prompt, "Mark S1 E1–S1 E4 watched?")
        page.confirmTV(confirmation)

        XCTAssertTrue(page.viewer?.tvIsWatched(e1) == true, "The range shows at once.")
        await gate.open()
        let result18 = await page.settled()
        XCTAssertTrue(result18)
        XCTAssertEqual(tracking.sent.first?.action, .markUpTo)
        XCTAssertEqual(tracking.sent.first?.request.seasonNumber, 1)
        XCTAssertEqual(tracking.sent.first?.request.episodeNumber, 4)
    }

    // MARK: Stars (TV-504, TV-502)

    func testTV504ShowAndSeasonStarsSendWholeStepsToRate() async throws {
        let show = model(Fixtures.showDetail(tv: Fixtures.show()))
        let rated = show.setCurrentRating(for: show.detail!, halfSteps: 7)
        XCTAssertEqual(show.viewer?.rating, "7.0", "The stars show at once.")
        let result19 = await rated.value
        XCTAssertTrue(result19)
        let result20 = await show.setCurrentRating(for: show.detail!, halfSteps: 0).value
        XCTAssertTrue(result20)

        XCTAssertEqual(tracking.sent.map(\.action), [.rate, .rate])
        XCTAssertEqual(tracking.sent[0].request.rating, 7)
        XCTAssertNil(tracking.sent[0].request.seasonNumber)
        let cleared = try Fixtures.json(tracking.sent[1].request)
        XCTAssertTrue(cleared["rating"] is NSNull, "Clearing sends rating null.")

        let season = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(1)]))
        let result21 = await season.setCurrentRating(for: season.detail!, halfSteps: 8).value
        XCTAssertTrue(result21)
        XCTAssertEqual(tracking.sent[2].request.rating, 8)
        XCTAssertEqual(tracking.sent[2].request.seasonNumber, 1)
    }

    func testTV502EpisodeStarsOpenLogEpisodeWithThoseStars() async throws {
        let diary = PagesDiaryRepository()
        var episode = Fixtures.episode(3, watched: true, viewing: 5)
        episode.userRating = "8.0"
        episode.composer = Fixtures.composer([Fixtures.target("episode:1:3", level: .episode, season: 1, episode: 3)])
        let detail = Fixtures.episodeDetail(episode)
        let composer = MediaLogViewModel(
            detail: detail,
            trackingRepository: tracking,
            diaryRepository: diary,
            preselectedRatingSteps: 8,
            onUnauthorized: {},
            onSaved: {}
        )

        XCTAssertEqual(composer.ratingSteps, 8)
        XCTAssertEqual(composer.primaryActionTitle, "Log Episode")
        let result22 = await composer.save()
        XCTAssertTrue(result22)
        XCTAssertEqual(diary.created.first?.rating, Decimal(8))
        XCTAssertEqual(diary.created.first?.ref.mediaType, "episode")
        XCTAssertEqual(diary.created.first?.ref.episodeNumber, 3)
    }

    // MARK: Composer (TV-404, TV-407, TV-705)

    func testTV404TheComposerTakesTheServersDefaults() throws {
        let composer = logModel(Fixtures.showDetail(tv: Fixtures.show(composer: Fixtures.showComposer)))

        XCTAssertEqual(composer.selectedRef.mediaType, "season")
        XCTAssertEqual(composer.selectedRef.seasonNumber, 1)
        XCTAssertEqual(composer.ratingSteps, 8)
        XCTAssertTrue(composer.liked)
        XCTAssertFalse(composer.isRepeat)
        XCTAssertEqual(CalendarDateCodec.string(from: composer.consumedAt), "2026-10-20")
        XCTAssertEqual(composer.tvTargets.map(\.id), ["season:1", "season:2", "season:0", "show"],
                       "Seasons, Specials last, then Whole show.")
        XCTAssertEqual(composer.selectedTitle, "The Show · Season 1")
    }

    func testTV404SeasonLogsNameTheSeasonFromTheSeasonPage() {
        // Log Season from the season's + menu.
        let fromMenu = logModel(Fixtures.seasonDetail(episodes: [Fixtures.episode(1)]))
        XCTAssertEqual(fromMenu.selectedTitle, "The Show · Season 1")

        // The season-finish composer opens on the season's own detail (TV-403).
        let afterFinishing = MediaLogViewModel(
            detail: Fixtures.seasonDetail(episodes: [Fixtures.episode(1, watched: true)]),
            trackingRepository: tracking,
            diaryRepository: PagesDiaryRepository(),
            onUnauthorized: {},
            onSaved: {},
            tvComposer: Fixtures.finished(season: 1).composer
        )
        XCTAssertEqual(afterFinishing.selectedTitle, "The Show · Season 1")
    }

    func testTV404RewatchIsTheServersDefaultNotTheLogCount() {
        var tv = Fixtures.show(composer: Fixtures.showComposer)
        tv.started = true
        var detail = Fixtures.showDetail(tv: tv)
        detail.userState?.diaryCount = 3
        let composer = logModel(detail)

        XCTAssertFalse(composer.isRepeat, "Installed builds turned Rewatch on whenever a log existed; the server decides now.")
    }

    func testTV403TheFinishComposersDefaultsComeFromTheWrite() {
        let finished = Fixtures.finished(season: 1).composer
        let composer = MediaLogViewModel(
            detail: Fixtures.showDetail(tv: Fixtures.show(composer: Fixtures.showComposer)),
            trackingRepository: tracking,
            diaryRepository: PagesDiaryRepository(),
            onUnauthorized: {},
            onSaved: {},
            tvComposer: finished
        )

        XCTAssertEqual(CalendarDateCodec.string(from: composer.consumedAt), "2026-10-24", "The finishing tick's day.")
        XCTAssertEqual(composer.selectedRef.seasonNumber, 1)
        XCTAssertFalse(composer.supportsTVTargetPicker)
    }

    func testTV404SwitchingTheTargetKeepsWhatTheUserChanged() {
        let composer = logModel(Fixtures.showDetail(tv: Fixtures.show(composer: Fixtures.showComposer)))
        composer.ratingSteps = 6

        composer.selectedTVTargetId = "show"

        XCTAssertEqual(composer.selectedRef.mediaType, "tv")
        XCTAssertEqual(composer.ratingSteps, 6, "An edited value stays.")
        XCTAssertFalse(composer.liked, "An untouched value takes the new target's default.")
        XCTAssertTrue(composer.isRepeat)
        XCTAssertEqual(CalendarDateCodec.string(from: composer.consumedAt), CalendarDateCodec.string(from: Date()))
        XCTAssertEqual(composer.primaryActionTitle, "Log TV Show")
    }

    func testTV407TheComposerRefusesAFutureDateAndSendsACalendarDate() async throws {
        let diary = PagesDiaryRepository()
        let composer = logModel(Fixtures.showDetail(tv: Fixtures.show(composer: Fixtures.showComposer)), diary: diary)
        composer.consumedAt = Calendar.autoupdatingCurrent.date(byAdding: .day, value: 1, to: Date())!

        let result23 = await composer.save()
        XCTAssertFalse(result23)
        XCTAssertEqual(composer.errorMessage, "Log dates can't be in the future.")
        XCTAssertTrue(diary.created.isEmpty)

        composer.consumedAt = Date()
        let result24 = await composer.save()
        XCTAssertTrue(result24)
        let body = try Fixtures.json(try XCTUnwrap(diary.created.first))
        XCTAssertEqual(body["consumed_at"] as? String, CalendarDateCodec.string(from: Date()))
        XCTAssertEqual(body["is_rewatch"] as? Bool, false, "Rewatch is always sent.")
        XCTAssertNotNil(body["mutation_id"] as? String)
        XCTAssertEqual(body["rating"] as? Double, 8, "TV stars stay on the 1-10 scale.")
    }

    func testTV705OneMutationIDPerComposerSessionAndTheEnvelopeGoesBack() async throws {
        let diary = PagesDiaryRepository()
        diary.errors = [Fixtures.unavailable]
        diary.envelope = TVWriteEnvelope(tv: Fixtures.show(), changed: true, finished: Fixtures.finished(season: 1))
        var results: [MediaLogSaveResult] = []
        let composer = MediaLogViewModel(
            detail: Fixtures.seasonDetail(episodes: [Fixtures.episode(1)]),
            trackingRepository: tracking,
            diaryRepository: diary,
            onResult: { results.append($0) },
            onUnauthorized: {},
            onSaved: {}
        )

        let result25 = await composer.save()
        XCTAssertFalse(result25)
        XCTAssertEqual(composer.errorMessage, "TV details are unavailable right now")
        let result26 = await composer.save()
        XCTAssertTrue(result26)

        XCTAssertEqual(diary.created.count, 2)
        XCTAssertNotNil(diary.created[0].mutationId)
        XCTAssertEqual(diary.created[0].mutationId, diary.created[1].mutationId)
        XCTAssertEqual(results.first?.tvEnvelope?.finished?.seasonNumber, 1)
        XCTAssertNil(results.first?.tracking)
    }

    // MARK: Page scope after a write (TV-404, TV-705)

    func testTV404AnActionFromASeasonPageNamesThePageAndTakesItsFreshComposer() async throws {
        // A-56: the Season 1 eye, then Log Season at once, defaults to Rewatch: the response carries the page's
        // composer, so nothing waits for the reload.
        holdRefreshes()
        let page = model(Fixtures.seasonDetail(episodes: [Fixtures.episode(1)]))
        XCTAssertEqual(page.viewer?.tv?.composer?.initialTarget?.isRewatch, false)
        var tv = Fixtures.show()
        var season = Fixtures.season(eye: TVEyeState(filled: true, tap: .unmarkMarks))
        season.state = .watched
        tv.season = season
        tv.composer = Fixtures.composer([Fixtures.target("season:1", level: .season, season: 1, isRewatch: true)])
        tracking.responses = [.success(TVActionResponse(tracking: Fixtures.showTracking,
                                                        envelope: TVWriteEnvelope(tv: tv, season: season, changed: true)))]

        let marked = await page.markTVWatched(seasonNumber: 1).value

        XCTAssertTrue(marked)
        let sent = try XCTUnwrap(tracking.sent.first)
        XCTAssertEqual(sent.request.pageSeasonNumber, 1)
        XCTAssertNil(sent.request.pageEpisodeNumber)
        XCTAssertEqual(try Fixtures.json(sent.request)["page_season_number"] as? Int, 1)
        XCTAssertEqual(page.viewer?.tv?.composer?.initialTarget?.isRewatch, true, "The composer has the mark's defaults")
        XCTAssertEqual(page.viewer?.tv?.season?.state, .watched)

        // The show page names no page; a response about another page never replaces this page's level.
        let showPage = model(Fixtures.showDetail(tv: Fixtures.show(composer: Fixtures.showComposer)))
        tracking.responses = [.success(TVActionResponse(tracking: Fixtures.showTracking,
                                                        envelope: TVWriteEnvelope(tv: tv, changed: true)))]
        let rated = await showPage.rateTV(steps: 8).value
        XCTAssertTrue(rated)
        XCTAssertNil(tracking.sent.last?.request.pageSeasonNumber)
        XCTAssertNil(showPage.viewer?.tv?.season, "A season-scoped state doesn't turn the show page into a season page")
        XCTAssertEqual(showPage.viewer?.tv?.composer, Fixtures.showComposer)
    }

    func testTV400ALogSavedOnItsEpisodePageShowsTheEpisodeWatchedAtOnce() async throws {
        holdRefreshes()
        let page = model(Fixtures.episodeDetail(Fixtures.episode(3)))
        var tv = Fixtures.show()
        let watched = Fixtures.episode(3, watched: true, viewing: 41)
        tv.episode = watched
        tv.composer = Fixtures.composer([Fixtures.target("episode:1:3", level: .episode, season: 1, episode: 3, isRewatch: true)])

        page.applyLogSave(MediaLogSaveResult(
            ref: Fixtures.episodeRef(3),
            tracking: nil,
            diaryEntry: try Fixtures.entry(),
            tvEnvelope: TVWriteEnvelope(tv: tv, episode: watched, changed: true)
        ))

        XCTAssertEqual(page.viewer?.tv?.episode?.watched, true, "The eye fills without waiting for the reload")
        XCTAssertEqual(page.viewer?.isTracked, true)
        XCTAssertEqual(page.confirmedTVEpisode(seasonNumber: 1, episodeNumber: 3)?.untickViewingId, 41)
        XCTAssertEqual(page.viewer?.tv?.composer?.initialTarget?.isRewatch, true, "Logging it again defaults to Rewatch")
    }

    func testTV401ALogSavedOnItsSeasonPageShowsEveryEpisodeWatchedAtOnce() async throws {
        holdRefreshes()
        let rows = [Fixtures.episode(1, watched: true, viewing: 5), Fixtures.episode(2), Fixtures.episode(3)]
        let page = model(Fixtures.seasonDetail(episodes: rows))
        var season = Fixtures.season(eye: TVEyeState(filled: true, tap: .blocked, message: "Delete your Season 1 log first."))
        season.state = .watched
        season.watchedCount = 3
        season.started = true
        season.status = "Completed"
        var tv = Fixtures.show()
        tv.season = season

        page.applyLogSave(MediaLogSaveResult(
            ref: Fixtures.seasonRef,
            tracking: nil,
            diaryEntry: try Fixtures.entry(),
            tvEnvelope: TVWriteEnvelope(tv: tv, season: season, changed: true)
        ))

        let viewer = try XCTUnwrap(page.viewer)
        XCTAssertEqual(viewer.status, "Completed")
        XCTAssertEqual(viewer.tv?.season?.watchedCount, 3)
        for row in rows {
            XCTAssertTrue(viewer.tvIsWatched(try XCTUnwrap(page.tvEpisodeState(row))), "E\(row.episodeNumber) is filled")
        }

        // The composer names its page, so the response carries that page's state.
        let diary = PagesDiaryRepository()
        let composer = MediaLogViewModel(
            detail: Fixtures.seasonDetail(episodes: rows),
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            onSaved: {},
            tvPage: Fixtures.seasonRef
        )
        let saved = await composer.save()
        XCTAssertTrue(saved)
        XCTAssertEqual(diary.created.first?.pageSeasonNumber, 1)
        XCTAssertNil(diary.created.first?.pageEpisodeNumber)
        XCTAssertEqual(try Fixtures.json(try XCTUnwrap(diary.created.first))["page_season_number"] as? Int, 1)
    }

    func testTV302AnUndoFromASeasonPageKeepsTheTicksTheServerKept() async throws {
        // Tick E1, mark the season with its eye, then undo the mark (case 2): E1 stays ticked at once.
        holdRefreshes()
        let rows = [Fixtures.episode(1), Fixtures.episode(2)]
        let page = model(Fixtures.seasonDetail(episodes: rows, eye: TVEyeState(filled: true, tap: .unmarkMarks)))
        let ticked = Fixtures.episode(1, watched: true, viewing: 41)
        tracking.responses = [.success(Fixtures.envelopeResponse(episode: ticked))]
        let tick = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 1).value
        XCTAssertTrue(tick)
        var season = Fixtures.season()
        season.watchedCount = 1
        season.started = true
        season.state = .inProgress
        tracking.responses = [.success(TVActionResponse(tracking: Fixtures.showTracking, envelope: TVWriteEnvelope(
            tv: Fixtures.show(), season: season, changed: true, seasonEpisodes: [ticked, Fixtures.episode(2)]
        )))]

        page.tapTVEye()
        let undone = await page.settled()

        XCTAssertTrue(undone)
        let viewer = try XCTUnwrap(page.viewer)
        XCTAssertTrue(viewer.tvIsWatched(try XCTUnwrap(page.tvEpisodeState(rows[0]))), "The kept tick stays ticked")
        XCTAssertFalse(viewer.tvIsWatched(try XCTUnwrap(page.tvEpisodeState(rows[1]))))
        XCTAssertEqual(page.confirmedTVEpisode(seasonNumber: 1, episodeNumber: 1)?.untickViewingId, 41)
        XCTAssertEqual(page.tvSeasonState(Fixtures.season())?.watchedCount, 1)
    }

    func testTV705AnEpisodePageRefreshesWhenItsShowChangesElsewhere() async {
        // The show page opened over an episode page marks the show watched (TV-300) and posts the show's ref.
        let page = model(Fixtures.episodeDetail(Fixtures.episode(3)))
        XCTAssertTrue(media.detailRequests.isEmpty)

        page.handleMediaStateChange(["ref": Fixtures.showRef])
        await page.refreshFinished()

        XCTAssertEqual(media.detailRequests.map(\.id), [Fixtures.episodeRef(3).id])
    }

    // MARK: Lists and lines (TV-004, TV-103, TV-200, TV-202, TV-600)

    func testTV004SeasonCardsPutSpecialsLastThenSeasonsNoLongerListed() {
        let seasons = [
            Fixtures.seasonSummary(0, title: "Specials"),
            Fixtures.seasonSummary(1, tv: TVSeasonState(seasonNumber: 1, started: true, status: "In progress", state: .inProgress,
                                                        watchedCount: 9, airedCount: 10, firstGapEpisode: 4)),
            Fixtures.seasonSummary(2, tv: TVSeasonState(seasonNumber: 2, state: .notStarted, episodeCount: 10)),
            Fixtures.seasonSummary(3, tv: TVSeasonState(seasonNumber: 3, started: true, state: .airing, nextAirDate: "2027-03-12")),
            Fixtures.seasonSummary(4, tv: TVSeasonState(seasonNumber: 4, state: .unknown)),
        ]
        let unlisted = [TVUnlistedSeason(seasonNumber: 7, title: "Season 7",
                                         tv: TVSeasonState(seasonNumber: 7, listed: false, started: true, state: .unknown))]

        let cards = TVSeasonCardModel.cards(seasons: seasons, unlisted: unlisted)

        XCTAssertEqual(cards.map(\.seasonNumber), [1, 2, 3, 4, 0, 7])
        XCTAssertEqual(cards.map { $0.label() }, ["9 of 10 · E4 not watched", "10 episodes", "Airing", nil, "10 episodes", nil])
        XCTAssertFalse(cards.last!.listed)

        var pending = TVPendingWatches()
        pending.markedShow = true
        XCTAssertEqual(cards[1].label(pending: pending), "Watched", "A pending show mark reads Watched.")
        XCTAssertEqual(cards[4].label(pending: pending), "10 episodes", "Specials are left alone (TV-004).")
    }

    func testTV103UnlistedEpisodesComeLastAndUnairedOnesShowTheirDate() {
        let rows = TVEpisodeRowModel.rows(
            episodes: [Fixtures.episodeSummary(Fixtures.episode(1)), Fixtures.episodeSummary(Fixtures.episode(2))],
            unlisted: [
                TVUnlistedEpisode(episodeNumber: 14, title: "Episode 14", tv: Fixtures.episode(14, listed: false)),
                TVUnlistedEpisode(episodeNumber: 12, title: "Episode 12", tv: Fixtures.episode(12, watched: true, viewing: 8, listed: false)),
            ]
        )
        XCTAssertEqual(rows.map(\.episodeNumber), [1, 2, 12, 14])
        XCTAssertTrue(rows[1].listed)
        XCTAssertFalse(rows[2].listed)

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        XCTAssertEqual(TVEpisodeTrailing.resolve(Fixtures.episode(9, aired: false, airDate: "2027-03-12"), isWatched: false, calendar: calendar),
                       .airDate("Mar 12"))
        XCTAssertEqual(TVEpisodeTrailing.resolve(Fixtures.episode(9, aired: false), isWatched: false), .airDate("TBA"))
        XCTAssertEqual(TVEpisodeTrailing.resolve(Fixtures.episode(1), isWatched: false), .tick(watched: false))
        XCTAssertEqual(TVEpisodeTrailing.resolve(Fixtures.episode(1), isWatched: true), .tick(watched: true))
        XCTAssertEqual(TVEpisodeTrailing.resolve(Fixtures.episode(14, listed: false), isWatched: false), .none,
                       "An unlisted episode has no tick, only an untick for its viewings.")
        XCTAssertEqual(TVEpisodeTrailing.resolve(Fixtures.episode(12, watched: true, viewing: 8, listed: false), isWatched: true),
                       .tick(watched: true))

        // The row's VoiceOver label says when an unaired episode airs, including "TBA".
        XCTAssertEqual(TVEpisodeTrailing.airDate("TBA").accessibilityAirDate, "Air date to be announced")
        XCTAssertEqual(TVEpisodeTrailing.airDate("Mar 12").accessibilityAirDate, "Airs Mar 12")
        XCTAssertNil(TVEpisodeTrailing.tick(watched: false).accessibilityAirDate)
        XCTAssertNil(TVEpisodeTrailing.none.accessibilityAirDate)
    }

    func testTV004SpecialsReadAsSpecials() {
        func season(_ number: Int) -> MediaRef {
            MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "903", seasonNumber: number, episodeNumber: nil)
        }
        XCTAssertEqual(season(0).displayTitle("Black Books"), "Black Books Specials")
        XCTAssertEqual(season(2).displayTitle("Black Books"), "Black Books S2")
        XCTAssertEqual(TVTrackingPresentation.seasonTitle(0), "Specials")
    }

    func testTV000WatchingAndTheCaughtUpRewatchAndProgressLines() {
        var watching = Fixtures.show()
        watching.watchedCount = 13
        watching.airedCount = 20
        let detail = Fixtures.showDetail(tv: watching)
        XCTAssertEqual(TrackingSummarySection.tvStatus(detail: detail, tv: watching, state: detail.userState), "Watching")
        XCTAssertEqual(TrackingSummarySection.tvLines(detail: detail, tv: watching), ["13 of 20 watched"])

        var caughtUp = watching
        caughtUp.status = "Completed"
        caughtUp.finished = true
        caughtUp.caughtUp = true
        caughtUp.watchedCount = 20
        caughtUp.nextAiring = TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: nil)
        XCTAssertEqual(TrackingSummarySection.tvLines(detail: detail, tv: caughtUp), ["Caught up"])

        var rewatching = watching
        rewatching.rewatch = TVRewatch(scope: .show, startedAt: "2026-10-20T10:00:00Z")
        rewatching.upNext = TVEpisodePointer(seasonNumber: 1, episodeNumber: 3)
        XCTAssertEqual(TrackingSummarySection.tvLines(detail: detail, tv: rewatching), ["Rewatching · S1 E3 next", "13 of 20 watched"])

        XCTAssertEqual(TrackingSummarySection.tvUpNext(detail: detail, state: detail.userState)?.episodeNumber, 4)
        XCTAssertNil(TrackingSummarySection.tvUpNext(detail: Fixtures.showDetail(tv: TVShowState()), state: nil))
    }

    func testTV200TheSeasonPageReadsItsSeasonLabel() {
        let detail = Fixtures.seasonDetail(episodes: [])
        var season = Fixtures.season()
        season.started = true
        season.state = .inProgress
        season.watchedCount = 6
        season.airedCount = 10
        var tv = Fixtures.show()
        tv.season = season
        XCTAssertEqual(TrackingSummarySection.tvStatus(detail: detail, tv: tv, state: nil), "6 of 10")
        season.state = .watched
        tv.season = season
        XCTAssertEqual(TrackingSummarySection.tvStatus(detail: detail, tv: tv, state: nil), "Watched")
    }

    // MARK: Helpers

    private func model(_ detail: MediaDetail) -> MediaDetailViewModel {
        let page = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: media,
            trackingRepository: tracking,
            diaryRepository: PagesDiaryRepository(),
            onUnauthorized: {},
            writeRefreshDelay: .zero,
            externalRefreshDelay: .zero
        )
        page.detail = detail
        if media.details.isEmpty { media.details = [detail] }
        return page
    }

    private func logModel(_ detail: MediaDetail, diary: PagesDiaryRepository? = nil) -> MediaLogViewModel {
        MediaLogViewModel(
            detail: detail,
            trackingRepository: tracking,
            diaryRepository: diary ?? PagesDiaryRepository(),
            onUnauthorized: {},
            onSaved: {}
        )
    }
}

// MARK: Fixtures

@MainActor
private enum Fixtures {
    static let showRef = MediaRef(itemId: 500, source: "tmdb", mediaType: "tv", mediaId: "1399",
                                  seasonNumber: nil, episodeNumber: nil)
    static let seasonRef = MediaRef(itemId: 501, source: "tmdb", mediaType: "season", mediaId: "1399",
                                    seasonNumber: 1, episodeNumber: nil)

    static func episodeRef(_ episode: Int) -> MediaRef {
        MediaRef(itemId: 502, source: "tmdb", mediaType: "episode", mediaId: "1399", seasonNumber: 1, episodeNumber: episode)
    }

    static let showTracking = TrackingState(
        trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil,
        startDate: nil, endDate: nil, notes: nil, updatedAt: nil
    )

    static let unavailable = APIError.httpStatus(
        503,
        #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"TV details are unavailable right now","fields":{},"request_id":"r1"}}"#
    )

    static func stateChanged(version: Int, count: Int) -> APIError {
        .httpStatus(409, #"{"error":{"code":"tv_state_changed","message":"This show changed. Try again.","fields":{},"request_id":"r2","tv":{"state_version":\#(version),"count":\#(count)}}}"#)
    }

    static let question = TVEarlierUnwatched(
        from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
        to: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 9),
        count: 9,
        question: "Also mark S1 E1–S1 E9 watched?",
        offerSeasonNumber: 1
    )

    static func target(
        _ id: String,
        level: TVLevel,
        season: Int? = nil,
        episode: Int? = nil,
        title: String = "",
        date: String? = nil,
        rating: String? = nil,
        liked: Bool = false,
        isRewatch: Bool = false
    ) -> TVComposerTarget {
        TVComposerTarget(id: id, level: level, seasonNumber: season, episodeNumber: episode, title: title,
                         date: date ?? CalendarDateCodec.string(from: Date()),
                         rating: rating, liked: liked, isRewatch: isRewatch)
    }

    static func composer(_ targets: [TVComposerTarget], default defaultTarget: String? = nil) -> TVComposer {
        TVComposer(defaultTarget: defaultTarget ?? targets.first?.id ?? "", targets: targets)
    }

    /// The show page's targets as the server sends them: seasons, Specials last, then Whole show.
    static let showComposer = composer([
        target("season:1", level: .season, season: 1, title: "Season 1", date: "2026-10-20", rating: "8.0", liked: true),
        target("season:2", level: .season, season: 2, title: "Season 2"),
        target("season:0", level: .season, season: 0, title: "Specials"),
        target("show", level: .show, title: "Whole show", rating: "9.0", isRewatch: true),
    ], default: "season:1")

    static func finished(season: Int) -> TVFinished {
        TVFinished(seasonNumber: season, composer: composer([
            target("season:\(season)", level: .season, season: season, title: "Season \(season)", date: "2026-10-24"),
        ]))
    }

    static func show(
        version: Int = 7,
        eye: TVEyeState = TVEyeState(filled: false, tap: .markConfirm, count: 20),
        upNext: TVEpisodePointer? = TVEpisodePointer(seasonNumber: 2, episodeNumber: 4, title: "Fly"),
        composer: TVComposer? = nil
    ) -> TVShowState {
        TVShowState(
            version: version,
            inLibrary: true,
            status: "In progress",
            selectedAction: "watching",
            started: true,
            watchedCount: 13,
            airedCount: 20,
            upNext: upNext,
            removeCount: 13,
            eye: eye,
            availableActions: ["pause", "drop", "log", "remove", "rate"],
            actionReasons: ["planning": "You've started this show. Pause or drop it instead."],
            composer: composer
        )
    }

    static func season(eye: TVEyeState = TVEyeState(filled: false, tap: .mark)) -> TVSeasonState {
        TVSeasonState(seasonNumber: 1, state: .notStarted, watchedCount: 0, airedCount: 10, episodeCount: 10, eye: eye,
                      availableActions: ["log_season", "rate"])
    }

    static func episode(
        _ number: Int,
        watched: Bool = false,
        viewing: Int? = nil,
        untickMessage: String? = nil,
        aired: Bool = true,
        airDate: String? = nil,
        listed: Bool = true,
        markUpToPrompt: String? = nil
    ) -> TVEpisodeState {
        TVEpisodeState(
            seasonNumber: 1,
            episodeNumber: number,
            listed: listed,
            aired: aired,
            airDate: airDate,
            watched: watched,
            viewingCount: watched ? 1 : 0,
            untickViewingId: viewing,
            untickMessage: untickMessage,
            canMarkUpTo: aired && listed,
            markUpToPrompt: markUpToPrompt,
            availableActions: aired ? ["log_episode"] : [],
            actionReasons: aired ? [:] : ["log_episode": "This episode hasn't aired yet."]
        )
    }

    static func episodeSummary(_ tv: TVEpisodeState) -> EpisodeSummary {
        EpisodeSummary(episodeNumber: tv.episodeNumber, title: "Episode \(tv.episodeNumber)", overview: nil,
                       airDate: tv.airDate, runtime: nil, imageUrl: nil, rating: nil, tv: tv)
    }

    static func seasonSummary(_ number: Int, title: String? = nil, tv: TVSeasonState? = nil) -> SeasonSummary {
        SeasonSummary(seasonNumber: number, title: title ?? "Season \(number)", episodeCount: 10, imageUrl: nil,
                      releaseDate: nil, completion: nil, tv: tv)
    }

    static func showDetail(tv: TVShowState, seasons: [SeasonSummary] = []) -> MediaDetail {
        MediaDetail(
            ref: showRef,
            title: "The Show",
            userState: UserMediaState(isTracked: tv.inLibrary, trackingId: 1, status: tv.status, tv: tv),
            seasons: seasons
        )
    }

    static func seasonDetail(episodes: [TVEpisodeState], eye: TVEyeState = TVEyeState(filled: false, tap: .mark)) -> MediaDetail {
        var tv = show()
        tv.season = season(eye: eye)
        tv.composer = composer([target("season:1", level: .season, season: 1, title: "Season 1")])
        return MediaDetail(
            ref: seasonRef,
            title: "The Show",
            userState: UserMediaState(isTracked: false, tv: tv),
            episodes: episodes.map(episodeSummary)
        )
    }

    static func episodeDetail(_ episode: TVEpisodeState) -> MediaDetail {
        var tv = show()
        tv.episode = episode
        return MediaDetail(
            ref: episodeRef(episode.episodeNumber),
            title: "Episode \(episode.episodeNumber)",
            userState: UserMediaState(isTracked: episode.viewingCount > 0, tv: tv)
        )
    }

    static func envelopeResponse(
        episode: TVEpisodeState? = nil,
        finished: TVFinished? = nil,
        earlierUnwatched: TVEarlierUnwatched? = nil
    ) -> TVActionResponse {
        TVActionResponse(
            tracking: showTracking,
            envelope: TVWriteEnvelope(tv: show(), episode: episode, changed: true, finished: finished,
                                      earlierUnwatched: earlierUnwatched)
        )
    }

    static func entry() throws -> DiaryEntry {
        try JSONDecoder.api.decode(DiaryEntry.self, from: Data(#"""
        {"id":321,"user":{"id":1,"username":"test","display_name":"Test"},"media":{"ref":{"source":"tmdb","media_type":"episode","media_id":"1399","season_number":1,"episode_number":10},"title":"The Show"},"consumed_at":"2026-10-24T00:00:00Z","rating":null,"contains_spoilers":false,"liked":false,"is_rewatch":false,"tags":[],"visibility":"public","like_count":0,"viewer_has_liked":false}
        """#.utf8))
    }

    static func json(_ value: some Encodable) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(value)) as? [String: Any])
    }
}

// MARK: Test doubles

@MainActor
private final class PagesTrackingRepository: TrackingRepository {
    struct Sent {
        let source: String
        let mediaId: String
        let action: TVAction
        let request: TVActionRequest
    }

    var gate: RefreshGate?
    /// Answers in order; with none left, a changed envelope for the show.
    var responses: [Result<TVActionResponse, Error>] = []
    private(set) var sent: [Sent] = []

    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        sent.append(Sent(source: source, mediaId: mediaId, action: action, request: request))
        await gate?.wait()
        guard !responses.isEmpty else {
            return TVActionResponse(tracking: Fixtures.showTracking, envelope: TVWriteEnvelope(tv: Fixtures.show(), changed: true))
        }
        return try responses.removeFirst().get()
    }

    /// TV pages fetch the tracking only to fold it in; an unstarted level answers 404.
    func detail(ref: MediaRef) async throws -> TrackingState { throw APIError.httpStatus(404, nil) }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("TV writes use actions") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

@MainActor
private final class PagesMediaRepository: MediaRepository {
    /// Detail answers in order; the last one repeats.
    var details: [MediaDetail] = []
    var detailGate: RefreshGate?
    var likeResponse: MediaLikeResponse?
    private(set) var detailRequests: [MediaRef] = []
    private(set) var likeRequests: [(ref: MediaRef, liked: Bool)] = []

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailRequests.append(ref)
        await detailGate?.wait()
        let detail = details.count > 1 ? details.removeFirst() : details[0]
        guard detail.ref.id != ref.id else { return detail }
        // The follow-up loads a season the page isn't showing.
        return MediaDetail(ref: ref, title: ref.seasonNumber.map { "Season \($0)" } ?? "The Show")
    }

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        likeRequests.append((ref, liked))
        return likeResponse ?? MediaLikeResponse(liked: liked, media: nil)
    }

    func reviewPage(ref: MediaRef, page: String?) async throws -> PagedResponse<MediaReview> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func community(ref: MediaRef) async throws -> CommunityStats {
        CommunityStats(averageRating: nil, ratingCount: 0, diaryCount: 0, reviewCount: 0, likedCount: 0)
    }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

@MainActor
private final class PagesDiaryRepository: DiaryRepository {
    /// Thrown by the next creates, in order.
    var errors: [Error] = []
    var envelope = TVWriteEnvelope.empty
    private(set) var created: [DiaryEntryWriteRequest] = []

    func createTV(_ request: DiaryEntryWriteRequest) async throws -> (DiaryEntry, TVWriteEnvelope) {
        created.append(request)
        if !errors.isEmpty { throw errors.removeFirst() }
        return (try Fixtures.entry(), envelope)
    }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("TV logs go through createTV") }
    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}
