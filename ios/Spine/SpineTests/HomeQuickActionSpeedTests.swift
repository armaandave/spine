import XCTest
@testable import Spine

/// The In progress panel's two buttons for a show: the main button shows its new count at once and puts the card
/// back if the write fails; a title's first tick has what it needs read when its tile opens; and Update's sheet
/// never draws the old single up-next row while its season is still loading.
@MainActor
final class HomeQuickActionSpeedTests: XCTestCase {
    private var tracking: SurfacesTrackingRepository!
    private var media: SurfacesMediaRepository!
    private var heldGates: [RefreshGate] = []

    override func setUp() async throws {
        try await super.setUp()
        tracking = SurfacesTrackingRepository()
        media = SurfacesMediaRepository()
        heldGates = []
    }

    override func tearDown() async throws {
        for gate in heldGates { await gate.open() }
        heldGates = []
        try await super.tearDown()
    }

    private func makeHome() -> HomeViewModel {
        HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: TVTrackingSurfacesTests.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: HomeRecordingActivityRepository(),
            mediaRepository: media,
            diaryRepository: SurfacesDiaryRepository(),
            loadsTileExtras: false,
            onUnauthorized: {}
        )
    }

    /// Holds the write's answer, and the page refresh after it, until the test opens them.
    private func holdWrites() -> RefreshGate {
        let gate = RefreshGate()
        tracking.actionGate = gate
        let refreshGate = RefreshGate()
        media.detailGate = refreshGate
        heldGates = [gate, refreshGate]
        return gate
    }

    private func waitForTheWrite() async {
        var started = false
        tracking.onAction = { started = true }
        for _ in 0 ..< 1000 where !started { await Task.yield() }
        XCTAssertTrue(started, "The tick was sent")
    }

    // MARK: The count at once

    func testTheMainButtonShowsTheNewCountBeforeTheServerAnswers() async throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 4)
        let home = makeHome()
        home.inProgressItems = [crown]
        let gate = holdWrites()
        tracking.actionResponses = [TVTrackingSurfacesTests.tickResponse(
            upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501
        )]

        let outcome = Task { await home.runQuickAction(crown) }
        await waitForTheWrite()

        XCTAssertEqual(home.tiles.inProgress.first?.tracking.tv?.watchedCount, 5, "The count moved with no answer yet")
        await gate.open()
        let result = await outcome.value
        XCTAssertEqual(result, .saved)
        XCTAssertEqual(home.tiles.inProgress.first?.tracking.tv?.watchedCount, 5)
        XCTAssertEqual(home.tiles.inProgress.first?.tracking.tv?.upNext?.episodeNumber, 6, "The server's answer replaced it")
    }

    func testAFailedTickPutsTheCardBack() async throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 4)
        let home = makeHome()
        home.inProgressItems = [crown]
        let gate = holdWrites()
        tracking.actionError = APIError.httpStatus(500, nil)

        let outcome = Task { await home.runQuickAction(crown) }
        await waitForTheWrite()
        XCTAssertEqual(home.tiles.inProgress.first?.tracking.tv?.watchedCount, 5)
        await gate.open()
        let result = await outcome.value

        guard case .failed = result else { return XCTFail("Expected a failure, got \(result)") }
        XCTAssertEqual(home.tiles.inProgress.first?.tracking, crown.tracking, "Back to what the card was")
        XCTAssertTrue(home.tiles.busyItemIDs.isEmpty)
    }

    func testOptimisticTrackingCountsOneMoreEpisodeAndNeverPastAired() throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 4)
        let plan = try XCTUnwrap(QuickActionPlan.make(for: crown))
        let next = try XCTUnwrap(plan.optimisticTracking(crown.tracking))
        XCTAssertEqual(next.tv?.watchedCount, 5)
        XCTAssertEqual(next.tv?.version, crown.tracking.tv?.version, "The version stays the card's")
        XCTAssertEqual(next.tv?.upNext, crown.tracking.tv?.upNext)

        var capped = crown.tracking
        capped.tv?.watchedCount = 10
        XCTAssertEqual(plan.optimisticTracking(capped)?.tv?.watchedCount, 10)

        XCTAssertNil(QuickActionPlan.addPages(20).optimisticTracking(crown.tracking), "Only a tick is shown early")
        XCTAssertNil(QuickActionPlan.openSheet(label: "Update").optimisticTracking(crown.tracking))
    }

    // MARK: Warm-up

    func testOpeningATitlesTileReadsItsUpNextSeasonOncePerTitle() async throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 4)
        let home = makeHome()
        home.inProgressItems = [crown]

        home.warmQuickAction(forItemID: crown.id)
        await home.warmup?.value
        XCTAssertEqual(media.detailRequests, [ContinueWatchingEpisodes.seasonRef(show: crown.media.ref, seasonNumber: 2)])

        home.warmQuickAction(forItemID: crown.id)
        await home.warmup?.value
        XCTAssertEqual(media.detailRequests.count, 1, "Once per title")
    }

    func testNothingIsWarmedForATitleWhoseMainButtonOpensTheSheetOrForNoTitle() async throws {
        let nearlyDone = TVTrackingSurfacesTests.crown(watched: 9, upNext: (2, 10))
        let home = makeHome()
        home.inProgressItems = [nearlyDone]

        home.warmQuickAction(forItemID: nearlyDone.id)
        home.warmQuickAction(forItemID: nil)
        home.warmQuickAction(forItemID: "missing")
        await home.warmup?.value

        XCTAssertTrue(media.detailRequests.isEmpty)
    }

    // MARK: Update's sheet

    func testAnUpdateSheetWaitsForItsHeaderOnlyWhileTheLoadCanStillBringIt() {
        XCTAssertTrue(EpisodeSheetDetent.isLoadingHeader(hasHeader: false, isLoaded: false, hasFailed: false),
                      "Loading: a placeholder, not the old row")
        XCTAssertFalse(EpisodeSheetDetent.isLoadingHeader(hasHeader: true, isLoaded: true, hasFailed: false))
        XCTAssertFalse(EpisodeSheetDetent.isLoadingHeader(hasHeader: false, isLoaded: true, hasFailed: false),
                       "Loaded without ticks: the list")
        XCTAssertFalse(EpisodeSheetDetent.isLoadingHeader(hasHeader: false, isLoaded: false, hasFailed: true),
                       "A failed load shows the list and its alert")
    }
}
