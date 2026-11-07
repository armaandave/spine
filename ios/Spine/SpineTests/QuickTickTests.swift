import XCTest
@testable import Spine

/// The headless "Watched next" tick (`ContinueWatchingTicks.quickTick`): the tile's primary button ticks up next with
/// the sheet's own model and write queue, and shows no sheet. What the write asks next, or its failure, is kept on
/// `closedEpisodes` / `closedAnimeEpisodes` for the screen's background presentation.
@MainActor
final class QuickTickTests: XCTestCase {
    private var tracking: SurfacesTrackingRepository!
    private var media: SurfacesMediaRepository!
    private var diary: SurfacesDiaryRepository!
    private var heldGates: [RefreshGate] = []

    override func setUp() async throws {
        try await super.setUp()
        tracking = SurfacesTrackingRepository()
        media = SurfacesMediaRepository()
        diary = SurfacesDiaryRepository()
        // A write's refresh of the page would reload a fixture; hold it.
        let gate = RefreshGate()
        media.detailGate = gate
        heldGates.append(gate)
    }

    override func tearDown() async throws {
        for gate in heldGates { await gate.open() }
        heldGates = []
        try await super.tearDown()
    }

    private func ticks() -> ContinueWatchingTicks {
        ContinueWatchingTicks(mediaRepository: media, trackingRepository: tracking, diaryRepository: diary, onUnauthorized: {})
    }

    private func tickResponse(
        upNext: (Int, Int)? = (2, 6),
        watched: Int = 5,
        ticked: (Int, Int) = (2, 5),
        finished: TVFinished? = nil,
        earlier: TVEarlierUnwatched? = nil
    ) -> TVActionResponse {
        TVTrackingSurfacesTests.tickResponse(
            upNext: upNext, watched: watched, ticked: ticked, viewingId: 501, finished: finished, earlierUnwatched: earlier
        )
    }

    // MARK: TV

    func testATickTicksUpNextOnceAndPatchesTheCardWithNoSheet() async throws {
        let ticks = ticks()
        var patched: [(String, TVShowState)] = []
        ticks.onUpdate = { patched.append(($0, $1)) }
        var origins: [UUID?] = []
        let observer = NotificationCenter.default.addObserver(forName: .mediaStateDidChange, object: nil, queue: nil) { note in
            origins.append(note.userInfo?["origin"] as? UUID)
        }
        defer { NotificationCenter.default.removeObserver(observer) }
        tracking.actionResponses = [tickResponse()]
        let crown = TVTrackingSurfacesTests.crown()

        XCTAssertFalse(ticks.needsReload)
        ticks.quickTick(for: crown)
        await ticks.quickTickSettling?.value

        XCTAssertEqual(tracking.actions.count, 1, "Up next once")
        XCTAssertEqual(tracking.actions.first?.action, .tick)
        XCTAssertEqual(tracking.actions.first?.request.seasonNumber, 2)
        XCTAssertEqual(tracking.actions.first?.request.episodeNumber, 5)
        XCTAssertNil(ticks.episodes, "No sheet")
        XCTAssertNil(ticks.animeEpisodes)
        XCTAssertEqual(patched.map(\.0), [crown.id], "The card is patched in place")
        XCTAssertEqual(patched.first?.1.watchedCount, 5)
        XCTAssertEqual(patched.first?.1.upNext?.episodeNumber, 6)
        XCTAssertTrue(ticks.needsReload, "The rail re-sorts on its next visit")
        XCTAssertEqual(origins.compactMap { $0 }.first, ticks.changeOrigin, "Home recognizes its own post and keeps the card")
        XCTAssertFalse(ticks.isQuickTicking)
        XCTAssertNotNil(ticks.closedEpisodes, "Kept for what the write may ask next")
    }

    func testASecondTapWhileTheFirstIsInFlightIsIgnored() async throws {
        let ticks = ticks()
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        tracking.actionResponses = [tickResponse(), tickResponse(upNext: (2, 7), watched: 6, ticked: (2, 6))]
        let crown = TVTrackingSurfacesTests.crown()

        ticks.quickTick(for: crown)
        XCTAssertTrue(ticks.isQuickTicking)
        ticks.quickTick(for: crown)
        ticks.quickTick(for: crown)
        await gate.open()
        await ticks.quickTickSettling?.value
        XCTAssertEqual(tracking.actions.count, 1, "One tick, never an untick of the first")
        XCTAssertFalse(ticks.isQuickTicking)

        // Once it has settled the button works again, on the card as the rail now has it.
        let next = TVTrackingSurfacesTests.crown(watched: 5, upNext: (2, 6))
        ticks.quickTick(for: next)
        await ticks.quickTickSettling?.value
        XCTAssertEqual(tracking.actions.compactMap { $0.request.episodeNumber }, [5, 6])
    }

    func testAFinaleHandsLogSeasonToTheClosedSheet() async throws {
        let ticks = ticks()
        tracking.actionResponses = [tickResponse(upNext: nil, watched: 10, ticked: (2, 10), finished: TVTrackingSurfacesTests.finished(season: 2))]
        ticks.quickTick(for: TVTrackingSurfacesTests.crown(watched: 9, upNext: (2, 10)))
        await ticks.quickTickSettling?.value

        let closed = try XCTUnwrap(ticks.closedEpisodes)
        XCTAssertNil(ticks.episodes, "No sheet")
        XCTAssertEqual(closed.season.tvFollowUp.composer?.finished.seasonNumber, 2, "The finale's Log Season composer")
    }

    func testTheEarlierEpisodesQuestionIsHandedToTheClosedSheet() async throws {
        let ticks = ticks()
        let question = TVEarlierUnwatched(
            from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 4),
            count: 14,
            question: "Also mark everything before it watched?"
        )
        tracking.actionResponses = [tickResponse(earlier: question)]
        ticks.quickTick(for: TVTrackingSurfacesTests.crown())
        await ticks.quickTickSettling?.value
        XCTAssertEqual(ticks.closedEpisodes?.season.tvFollowUp.question?.question, "Also mark everything before it watched?")
    }

    func testAnotherCardWaitsWhileAQuestionIsPending() async throws {
        let ticks = ticks()
        let question = TVEarlierUnwatched(
            from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 4),
            count: 14,
            question: "Also mark everything before it watched?"
        )
        tracking.actionResponses = [tickResponse(earlier: question), tickResponse()]
        ticks.quickTick(for: TVTrackingSurfacesTests.crown())
        await ticks.quickTickSettling?.value
        let first = try XCTUnwrap(ticks.closedEpisodes)
        XCTAssertTrue(first.season.tvFollowUp.isActive)

        ticks.quickTick(for: TVTrackingSurfacesTests.crown(watched: 5, upNext: (2, 6)))
        await ticks.quickTickSettling?.value
        XCTAssertEqual(tracking.actions.count, 1, "The second card did not write")
        XCTAssertTrue(ticks.closedEpisodes === first, "The first card's question is still there")
        XCTAssertEqual(first.season.tvFollowUp.question?.question, "Also mark everything before it watched?")
    }

    func testAFailedTickLeavesTheCardAndShowsTheExistingAlert() async throws {
        let ticks = ticks()
        var patched = 0
        ticks.onUpdate = { _, _ in patched += 1 }
        tracking.actionError = APIError.httpStatus(503, #"{"error":{"message":"Down for now"}}"#)
        ticks.quickTick(for: TVTrackingSurfacesTests.crown())
        await ticks.quickTickSettling?.value

        let closed = try XCTUnwrap(ticks.closedEpisodes)
        XCTAssertEqual(patched, 0, "The card never moved")
        XCTAssertFalse(ticks.needsReload)
        XCTAssertEqual(closed.season.quickActionErrorMessage, "Down for now", "The screen's background alert shows it")
        XCTAssertEqual(closed.failureTitle, ContinueWatchingEpisodes.tickFailureTitle)
        XCTAssertFalse(ticks.isQuickTicking, "A retry is possible")
    }

    func testACardWithoutUpNextOrOfAnotherTypeDoesNothing() async throws {
        let ticks = ticks()
        ticks.quickTick(for: TVTrackingSurfacesTests.item("9", "Heat", mediaType: "movie", updatedAt: nil))
        ticks.quickTick(for: TVTrackingSurfacesTests.showItem(
            "1", "Caught Up", tv: TVTrackingSurfacesTests.showState(status: "In progress", caughtUp: true)
        ))
        XCTAssertTrue(tracking.actions.isEmpty)
        XCTAssertFalse(ticks.isQuickTicking)
        XCTAssertNil(ticks.closedEpisodes)
    }

    func testTheTickShowsInstantlyThroughThePendingWrite() async throws {
        let ticks = ticks()
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        tracking.actionResponses = [tickResponse()]
        ticks.quickTick(for: TVTrackingSurfacesTests.crown())
        let closed = try XCTUnwrap(ticks.closedEpisodes)
        XCTAssertTrue(closed.season.hasPendingWrites)
        XCTAssertTrue(closed.season.tvIsWatched(TVEpisodeState(seasonNumber: 2, episodeNumber: 5)))
        await gate.open()
        await ticks.quickTickSettling?.value
        XCTAssertFalse(closed.season.hasPendingWrites)
    }

    // MARK: Anime

    func testAnAnimeTickGoesThroughTheAnimeRoute() async throws {
        let animeTracking = AnimeTrackingRepository()
        let ticks = ContinueWatchingTicks(
            mediaRepository: AnimeMediaRepository(), trackingRepository: animeTracking, diaryRepository: diary, onUnauthorized: {}
        )
        var updated: [(MediaRef, AnimeState)] = []
        ticks.onAnimeUpdate = { updated.append(($0, $1)) }
        let ref = MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "16498", seasonNumber: nil, episodeNumber: nil)
        let before = AnimeState(
            version: 1, inLibrary: true, status: "In progress", started: true, watchedCount: 7, airedCount: 24,
            upNext: AnimeEpisodePointer(episodeNumber: 8, title: nil, imageUrl: nil), selectedAction: "watching"
        )
        let after = AnimeState(
            version: 2, inLibrary: true, status: "In progress", started: true, watchedCount: 8, airedCount: 24,
            upNext: AnimeEpisodePointer(episodeNumber: 9, title: nil, imageUrl: nil), selectedAction: "watching"
        )
        let card = Spine.LibraryItem(
            media: MediaSummary(ref: ref, title: "Attack on Titan", imageUrl: "https://img/poster.jpg"),
            tracking: TrackingState(trackingId: 3, status: "In progress", rating: nil, progress: nil, repeats: nil,
                                    startDate: nil, endDate: nil, notes: nil, updatedAt: nil, anime: before)
        )
        animeTracking.actionResponses = [TVActionResponse(
            tracking: nil,
            envelope: TVWriteEnvelope(changed: true, anime: after, row: AnimeRow(number: 8, aired: 1, watched: 1, viewingId: 8))
        )]

        ticks.quickTick(for: card)
        await ticks.quickTickSettling?.value

        XCTAssertEqual(animeTracking.actions.map { $0.action }, [.tick])
        XCTAssertEqual(animeTracking.actions.first?.request.episodeNumber, 8)
        XCTAssertTrue(tracking.actions.isEmpty, "Not the TV route")
        XCTAssertNil(ticks.animeEpisodes, "No sheet")
        XCTAssertNotNil(ticks.closedAnimeEpisodes)
        XCTAssertEqual(updated.map(\.0.id), [ref.id])
        XCTAssertEqual(updated.first?.1.watchedCount, 8)
        XCTAssertTrue(ticks.needsReload)
        XCTAssertFalse(ticks.isQuickTicking)
    }
}
