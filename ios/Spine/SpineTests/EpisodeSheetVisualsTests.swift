import SwiftUI
import XCTest
@testable import Spine

/// The tick header above the episode sheets' lists (R3SheetTV, R3SheetLong): what model it is built from, what each
/// tap asks of the existing pipeline, and the compact height the sheets open at. Nothing here writes except through
/// the sheets' own toggle and "Mark watched up to here" question.
@MainActor
final class EpisodeSheetVisualsTests: XCTestCase {
    private var tracking: SurfacesTrackingRepository!
    private var media: SurfacesMediaRepository!
    private var diary: SurfacesDiaryRepository!
    private var heldGates: [RefreshGate] = []

    override func setUp() async throws {
        try await super.setUp()
        tracking = SurfacesTrackingRepository()
        media = SurfacesMediaRepository()
        diary = SurfacesDiaryRepository()
    }

    override func tearDown() async throws {
        for gate in heldGates { await gate.open() }
        heldGates = []
        try await super.tearDown()
    }

    private func ticks() -> ContinueWatchingTicks {
        ContinueWatchingTicks(mediaRepository: media, trackingRepository: tracking, diaryRepository: diary, onUnauthorized: {})
    }

    /// Holds the refresh a write starts, so a test reads the write's own answer and not the fixture again.
    private func holdDetails() {
        let gate = RefreshGate()
        media.detailGate = gate
        heldGates.append(gate)
    }

    /// The Crown's second season, `watched` of 10 episodes ticked, opened and loaded as the sheet does.
    private func loadedSheet(
        watched: Int = 4,
        todayCount: Int? = nil
    ) async throws -> (ContinueWatchingTicks, ContinueWatchingEpisodes) {
        let ticks = ticks()
        let crown = TVTrackingSurfacesTests.crown(watched: watched, upNext: (2, watched + 1))
        media.details[TVTrackingSurfacesTests.seasonRef(2).id] = TVTrackingSurfacesTests.crownSeason(2, watched: watched)
        ticks.openEpisodes(for: crown, todayCount: todayCount)
        let sheet = try XCTUnwrap(ticks.episodes)
        await sheet.season.load()
        sheet.captureBaseline()
        return (ticks, sheet)
    }

    // MARK: The model

    func testTheHeaderIsAbsentBeforeTheSeasonLoadsAndThereAfter() async throws {
        let ticks = ticks()
        media.details[TVTrackingSurfacesTests.seasonRef(2).id] = TVTrackingSurfacesTests.crownSeason(2, watched: 4)
        let gate = RefreshGate()
        heldGates.append(gate)
        media.detailGate = gate
        ticks.openEpisodes(for: TVTrackingSurfacesTests.crown())
        let sheet = try XCTUnwrap(ticks.episodes)
        XCTAssertNil(sheet.tickModel, "Up next's row alone shows while the season loads")

        let load = Task { await sheet.season.load() }
        await gate.open()
        await load.value
        let model = try XCTUnwrap(sheet.tickModel)
        XCTAssertEqual(model.episodes.map(\.number), Array(1...10))
        XCTAssertEqual(model.upTo, 4)
        XCTAssertEqual(model.upNext, 5)
        XCTAssertEqual(model.primaryLabel, "Watched E5")
        XCTAssertEqual(model.summaryLine, "Up to E4", "A title the provider only numbers is left off")
        XCTAssertFalse(model.usesWindow)
    }

    func testTheModelSeparatesWatchedUnwatchedAndUnairedEpisodes() throws {
        let model = ContinueWatchingEpisodes.tickModel(
            episodes: [
                .init(number: 1, state: .watched), .init(number: 2, state: .watched), .init(number: 3, state: .unwatched),
                .init(number: 4, state: .unwatched), .init(number: 5, state: .unaired),
            ],
            todayBase: nil,
            baseline: 2
        )
        XCTAssertEqual(model.upNext, 3, "The first aired, listed episode not watched")
        XCTAssertEqual(model.upTo, 2)
        XCTAssertEqual(model.action(for: 3), .tick)
        XCTAssertEqual(model.action(for: 4), .markUpTo)
        XCTAssertEqual(model.action(for: 2), .untick)
        XCTAssertEqual(model.action(for: 5), .none)
    }

    func testASeasonWhoseEpisodesCarryNoTrackingStateGetsNoHeader() async throws {
        let ticks = ticks()
        let bare = (1...3).map {
            EpisodeSummary(episodeNumber: $0, title: "T\($0)", overview: nil, airDate: nil, runtime: nil, imageUrl: nil,
                           rating: nil, tv: nil)
        }
        media.details[TVTrackingSurfacesTests.seasonRef(2).id] = MediaDetail(
            ref: TVTrackingSurfacesTests.seasonRef(2), title: "The Crown", episodes: bare
        )
        ticks.openEpisodes(for: TVTrackingSurfacesTests.crown())
        let sheet = try XCTUnwrap(ticks.episodes)
        await sheet.season.load()
        XCTAssertNil(sheet.tickModel, "The sheet looks as it did")
    }

    func testTodayLightsWhatTheSheetTicksOnTopOfWhatTheScreenCounted() async throws {
        let (_, sheet) = try await loadedSheet(watched: 4, todayCount: 1)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayCount, 1, "Four are watched, one of them today")
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).tickState(of: 4), .today)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).tickState(of: 3), .before)

        tracking.actionResponses = [TVTrackingSurfacesTests.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]
        sheet.headerPrimary()
        let settled = await sheet.season.settled()
        XCTAssertTrue(settled)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayCount, 2)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).tickState(of: 5), .today)
    }

    func testWithNoCountFromTheScreenOnlyTheSheetsOwnTicksAreToday() async throws {
        let (_, sheet) = try await loadedSheet(watched: 4)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayCount, 0)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayLine, "")

        tracking.actionResponses = [TVTrackingSurfacesTests.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]
        sheet.headerTap(5)
        _ = await sheet.season.settled()
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayLine, "+1 today")
    }

    // MARK: Taps

    func testATapOnUpNextTicksThroughTheToggle() async throws {
        let (_, sheet) = try await loadedSheet()
        tracking.actionResponses = [TVTrackingSurfacesTests.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]
        sheet.headerTap(5)
        XCTAssertTrue(sheet.season.tvIsWatched(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv))), "At once")
        _ = await sheet.season.settled()
        XCTAssertEqual(tracking.actions.map(\.action), [.tick])
        XCTAssertEqual(tracking.actions.first?.request.episodeNumber, 5)
        XCTAssertEqual(sheet.failureTitle, ContinueWatchingEpisodes.tickFailureTitle)
    }

    func testATapBeyondUpNextAsksTheMarkWatchedUpToHereQuestion() async throws {
        let (_, sheet) = try await loadedSheet()
        sheet.headerTap(7)
        let confirmation = try XCTUnwrap(sheet.season.tvConfirmation)
        XCTAssertEqual(confirmation.kind, .markUpTo(TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 7)))
        XCTAssertTrue(tracking.actions.isEmpty, "Nothing is written before the person confirms")
        XCTAssertFalse(sheet.season.tvIsWatched(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[6].tv))))
    }

    func testATapOnAWatchedEpisodeUnticksThroughTheToggle() async throws {
        let (_, sheet) = try await loadedSheet()
        sheet.headerTap(4)
        XCTAssertEqual(sheet.failureTitle, ContinueWatchingEpisodes.untickFailureTitle)
        _ = await sheet.season.settled()
        XCTAssertEqual(tracking.actions.map(\.action), [.untick])
        XCTAssertEqual(tracking.actions.first?.request.viewingId, 404)
    }

    func testATapOnAnUnairedEpisodeDoesNothing() async throws {
        let season = TVTrackingSurfacesTests.crownSeason(2, watched: 4)
        var episodes = try XCTUnwrap(season.episodes)
        let last = episodes.count - 1
        episodes[last] = EpisodeSummary(
            episodeNumber: 10, title: "Episode 10", overview: nil, airDate: nil, runtime: nil, imageUrl: nil, rating: nil,
            tv: TVEpisodeState(seasonNumber: 2, episodeNumber: 10, aired: false)
        )
        media.details[TVTrackingSurfacesTests.seasonRef(2).id] = MediaDetail(
            ref: season.ref, title: season.title, userState: season.userState, episodes: episodes
        )
        let ticks = ticks()
        ticks.openEpisodes(for: TVTrackingSurfacesTests.crown())
        let sheet = try XCTUnwrap(ticks.episodes)
        await sheet.season.load()
        let model = try XCTUnwrap(sheet.tickModel)
        XCTAssertEqual(model.state(of: 10), .unaired)
        sheet.headerTap(10)
        XCTAssertNil(sheet.season.tvConfirmation)
        XCTAssertNil(sheet.season.tvNotice)
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testThePrimaryButtonTicksUpNextThroughTheSameToggle() async throws {
        let (_, sheet) = try await loadedSheet(watched: 4)
        tracking.actionResponses = [TVTrackingSurfacesTests.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]
        sheet.headerPrimary()
        _ = await sheet.season.settled()
        XCTAssertEqual(tracking.actions.map(\.action), [.tick])
        XCTAssertEqual(tracking.actions.first?.request.episodeNumber, 5)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).primaryLabel, "Watched E6")
    }

    func testATypedEpisodeBeyondWhereYouAreAsksTheSameQuestion() async throws {
        let (_, sheet) = try await loadedSheet()
        sheet.headerType(9)
        XCTAssertEqual(sheet.season.tvConfirmation?.kind, .markUpTo(TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 9)))
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testATypedEpisodeAtOrBelowWhereYouAreChangesNothing() async throws {
        let (_, sheet) = try await loadedSheet()
        sheet.headerType(2)
        sheet.headerType(4)
        XCTAssertNil(sheet.season.tvConfirmation)
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testTicksMadeInAFinishedSeasonStayInTodayWhenTheSheetMovesOn() async throws {
        let (_, sheet) = try await loadedSheet(watched: 9, todayCount: 1)
        media.details[TVTrackingSurfacesTests.seasonRef(3).id] = TVTrackingSurfacesTests.crownSeason(3, watched: 0)
        tracking.actionResponses = [TVTrackingSurfacesTests.tickResponse(upNext: (3, 1), watched: 10, ticked: (2, 10), viewingId: 510)]
        sheet.headerPrimary()
        let settled = await sheet.season.settled()
        XCTAssertTrue(settled)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayCount, 2)

        sheet.composerClosed()
        XCTAssertEqual(sheet.seasonNumber, 3)
        await sheet.season.load()
        sheet.captureBaseline()
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayCount, 2, "The one from the screen and the one made here")
    }

    // MARK: Detent

    func testTheCompactDetentHugsTheMeasuredHeader() {
        XCTAssertEqual(EpisodeSheetDetent.compact(header: 300), .height(300 + EpisodeSheetDetent.chrome))
        XCTAssertEqual(EpisodeSheetDetent.compact(header: 0), .height(EpisodeSheetDetent.estimatedHeader + EpisodeSheetDetent.chrome),
                       "Before the header is measured, the height of a strip of ticks")
    }

    func testAWindowTitleOpensAtATallerEstimateThanAStrip() {
        let strip = EpisodeSheetDetent.estimate(episodeCount: EpisodeTickModel.stripLimit)
        let window = EpisodeSheetDetent.estimate(episodeCount: EpisodeTickModel.stripLimit + 1)
        XCTAssertGreaterThan(window, strip)
        XCTAssertEqual(window, EpisodeSheetDetent.estimatedWindowHeader)
        XCTAssertEqual(EpisodeSheetDetent.estimate(episodeCount: nil), strip, "An unknown count opens as a strip")
        XCTAssertEqual(EpisodeSheetDetent.compact(header: 0, episodeCount: 1150),
                       .height(EpisodeSheetDetent.estimatedWindowHeader + EpisodeSheetDetent.chrome))
        XCTAssertEqual(EpisodeSheetDetent.compact(header: 300, episodeCount: 1150), .height(300 + EpisodeSheetDetent.chrome),
                       "A measured header always wins over the estimate")
    }

    // MARK: Anime

    func testAnimeHeaderComesFromTheLoadedRowsAndUsesTheWindowPastFourteen() async throws {
        let animeTracking = AnimeTrackingRepository()
        let ticks = ContinueWatchingTicks(
            mediaRepository: AnimeMediaRepository(),
            trackingRepository: animeTracking,
            diaryRepository: diary,
            onUnauthorized: {}
        )
        let ref = MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "16498", seasonNumber: nil, episodeNumber: nil)
        let state = AnimeState(
            version: 1, inLibrary: true, status: "In progress", started: true, watchedCount: 20, airedCount: 25,
            upNext: AnimeEpisodePointer(episodeNumber: 21, title: nil, imageUrl: nil), selectedAction: "watching"
        )
        let card = Spine.LibraryItem(
            media: MediaSummary(ref: ref, title: "Attack on Titan", imageUrl: "https://img/poster.jpg"),
            tracking: TrackingState(trackingId: 3, status: "In progress", rating: nil, progress: nil, repeats: nil,
                                    startDate: nil, endDate: nil, notes: nil, updatedAt: nil, anime: state)
        )
        let rows = AnimeRows(
            upNext: 21,
            firstUnwatched: 21,
            logMessage: "Delete your log first.",
            episodes: (1...30).map { number in
                number <= 20 ? AnimeRow(number: number, aired: 1, watched: 1, viewingId: number)
                    : AnimeRow(number: number, aired: number <= 25 ? 1 : 0)
            }
        )
        animeTracking.rowsResponses[ref.id] = AnimeRowsResponse(anime: state, rows: rows)
        ticks.openEpisodes(for: card, todayCount: 3)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        XCTAssertNil(sheet.tickModel, "Not before the rows load")
        await sheet.loadRowsIfNeeded()

        let model = try XCTUnwrap(sheet.tickModel)
        XCTAssertTrue(model.usesWindow)
        XCTAssertEqual(model.total, 30)
        XCTAssertEqual(model.upTo, 20)
        XCTAssertEqual(model.upNext, 21)
        XCTAssertEqual(model.state(of: 26), .unaired)
        XCTAssertEqual(model.todayCount, 3)

        // A tap past up next asks; up next ticks through the toggle.
        sheet.headerTap(24)
        XCTAssertNotNil(sheet.page.tvConfirmation)
        XCTAssertTrue(animeTracking.actions.isEmpty)
        sheet.page.tvConfirmation = nil
        animeTracking.actionResponses = [TVActionResponse(
            tracking: nil,
            envelope: TVWriteEnvelope(
                changed: true,
                anime: AnimeState(
                    version: 2, inLibrary: true, status: "In progress", started: true, watchedCount: 21, airedCount: 25,
                    upNext: AnimeEpisodePointer(episodeNumber: 22, title: nil, imageUrl: nil), selectedAction: "watching"
                ),
                row: AnimeRow(number: 21, aired: 1, watched: 1, viewingId: 21)
            )
        )]
        sheet.headerPrimary()
        _ = await sheet.page.settled()
        XCTAssertEqual(animeTracking.actions.map(\.action), [.tick])
        XCTAssertEqual(animeTracking.actions.first?.request.episodeNumber, 21)
        XCTAssertEqual(try XCTUnwrap(sheet.tickModel).todayCount, 4)
    }
}
