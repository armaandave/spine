import XCTest
@testable import Spine

/// Library, Home, Profile and Diary for the show-as-library-unit model (docs/tv-tracking-contract.md).
@MainActor
final class TVTrackingSurfacesTests: XCTestCase {
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

    /// Holds the next page loads (a sheet's season, or the refresh after a write) until the gate opens, so a test
    /// reads a write's own answer rather than the fixture a refresh would reload. Released in tearDown.
    @discardableResult
    private func holdDetails() -> RefreshGate {
        let gate = RefreshGate()
        media.detailGate = gate
        heldGates.append(gate)
        return gate
    }

    /// Waits for work on other tasks, such as a page load reaching the held gate.
    private func waitUntil(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<400 where !condition() {
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertTrue(condition(), "Timed out waiting", file: file, line: line)
    }

    /// Opens the card's episode sheet and loads its season, as the sheet does when it appears.
    private func openLoadedSheet(
        _ viewModel: HomeViewModel,
        for item: Spine.LibraryItem,
        season: MediaDetail
    ) async throws -> ContinueWatchingEpisodes {
        media.detailGate = nil
        media.details[season.ref.id] = season
        viewModel.continueWatching.openEpisodes(for: item)
        let sheet = try XCTUnwrap(viewModel.continueWatching.episodes)
        await sheet.season.load()
        return sheet
    }

    // MARK: Library (TV-001, TV-002, TV-700)

    func testTV002TVHasFiveSectionsInOrder() {
        let shelves = LibraryShelf.available(for: "tv")
        XCTAssertEqual(shelves.map { $0.title(mediaType: "tv") }, ["Watching", "Planning", "Paused", "Dropped", "Completed"])
        XCTAssertEqual(shelves.map(\.apiStatus), ["In progress", "Planning", "Paused", "Dropped", "Completed"])
        for (shelf, status) in zip(shelves, ["In progress", "Planning", "Paused", "Dropped", "Completed"]) {
            XCTAssertTrue(shelf.matches(status: status), "\(shelf) holds \(status)")
            XCTAssertEqual(shelves.filter { $0.matches(status: status) }, [shelf], "Status alone decides the section")
        }
        XCTAssertFalse(shelves.contains(.tracked), "TV has no Tracked section")
        XCTAssertTrue(LibraryShelf.usesStatusShelves("tv"))
        XCTAssertEqual(LibraryShelf.defaultShelf(for: "tv"), .currentlyReading)

        // The Profile's Library link asks for Tracked: TV opens Watching; other types keep what they had.
        XCTAssertEqual(LibraryShelf.resolved(.tracked, for: "tv"), .currentlyReading)
        XCTAssertEqual(LibraryShelf.resolved(.planning, for: "tv"), .planning)
        XCTAssertEqual(LibraryShelf.resolved(.tracked, for: "movie"), .tracked)
        XCTAssertEqual(LibraryShelf.resolved(.tracked, for: "book"), .tracked)

        // Other types are unchanged.
        XCTAssertEqual(LibraryShelf.available(for: "movie"), [.tracked, .planning])
        XCTAssertEqual(LibraryShelf.available(for: "anime"), [.tracked, .planning])
        XCTAssertEqual(LibraryShelf.available(for: "book"), [.planning, .currentlyReading, .paused, .didNotFinish, .read])
        XCTAssertEqual(LibraryShelf.defaultShelf(for: "movie"), .tracked)
        XCTAssertEqual(LibraryShelf.defaultShelf(for: "game"), .currentlyReading)
    }

    func testTV001LibraryListsShowsBySectionAndNeverSeasons() async {
        tracking.listResponses["tv"] = PagedResponse(count: 1, next: nil, previous: nil, results: [
            Self.showItem("1396", "Breaking Bad", tv: Self.showState(status: "In progress")),
        ])
        let viewModel = LibraryViewModel(
            mediaRepository: media,
            trackingRepository: tracking,
            filterOptionsRepository: SurfacesFilterOptionsRepository(),
            onUnauthorized: {}
        )
        XCTAssertFalse(LibraryViewModel.libraryMediaTypes(from: ["movie", "tv", "season", "episode"]).contains("season"))

        await viewModel.selectMediaType("tv")
        XCTAssertEqual(viewModel.shelf, .currentlyReading, "Switching to TV opens Watching")
        XCTAssertEqual(viewModel.displayedItems.map(\.media.title), ["Breaking Bad"])

        viewModel.shelf = .read
        await viewModel.reload()
        viewModel.shelf = .paused
        await viewModel.reload()
        XCTAssertEqual(tracking.listRequests.map(\.mediaType), ["tv", "tv", "tv"])
        XCTAssertEqual(tracking.listRequests.map(\.status), ["In progress", "Completed", "Paused"])

        await viewModel.selectMediaType("movie")
        XCTAssertEqual(viewModel.shelf, .tracked, "Movies have no Watching section")
    }

    func testSwitchingTheLibraryToTVLoadsItsSectionOnce() async {
        let options = CountingFilterOptionsRepository()
        let viewModel = LibraryViewModel(
            mediaRepository: media,
            trackingRepository: tracking,
            filterOptionsRepository: options,
            onUnauthorized: {}
        )

        // Movies' Tracked becomes TV's Watching; the Library's section change handler then runs too.
        await viewModel.selectMediaType("tv")
        await viewModel.shelfDidChange()
        XCTAssertEqual(tracking.listRequests, [.init(mediaType: "tv", status: "In progress")])
        XCTAssertEqual(options.requestCount, 1)

        // A section the user picks still loads, once.
        viewModel.shelf = .paused
        await viewModel.shelfDidChange()
        XCTAssertEqual(tracking.listRequests.map(\.status), ["In progress", "Paused"])
        XCTAssertEqual(options.requestCount, 2)
    }

    func testTV700CardLinesSayWhereYouAre() {
        let today = Self.day("2026-10-26") // a Monday

        func line(_ tv: TVShowState) -> String {
            TVLibraryCard.line(for: Self.showItem("1", "Show", tv: tv), today: today, calendar: Self.utc)
        }

        XCTAssertEqual(line(Self.showState(status: "In progress", watched: 13, aired: 20, upNext: (2, 4))), "S2 E4 next · 13 of 20")
        XCTAssertEqual(line(Self.showState(status: "In progress", watched: 20, aired: 20, caughtUp: true,
                                           nextAiring: TVAiring(seasonNumber: 2, episodeNumber: 7, airDate: "2026-10-29"))),
                       "Caught up · E7 on Thu")
        XCTAssertEqual(line(Self.showState(status: "In progress", watched: 20, aired: 20, caughtUp: true)), "Caught up")
        XCTAssertEqual(line(Self.showState(status: "In progress", watched: 20, aired: 20, upNext: (1, 3),
                                           rewatch: TVRewatch(scope: .show, startedAt: "2026-10-20T20:00:00Z"))),
                       "Rewatching · S1 E3 next")
        XCTAssertEqual(line(Self.showState(status: "Planning", started: false,
                                           premiere: TVAiring(seasonNumber: 1, airDate: "2027-03-12"))),
                       "Premieres Mar 12")
        XCTAssertEqual(line(Self.showState(status: "Paused", watched: 12, aired: 20, upNext: (2, 3))), "Paused at S2 E3")
        XCTAssertEqual(line(Self.showState(status: "Dropped", watched: 13, aired: 20, upNext: (2, 4))), "Stopped at S2 E4")
        XCTAssertEqual(line(Self.showState(status: "Paused", started: false)), "Paused", "Nothing watched names no episode")
        XCTAssertEqual(line(Self.showState(status: "Completed", watched: 20, aired: 20, finished: true, caughtUp: true,
                                           nextAiring: TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: "2027-03-12"))),
                       "Caught up · S3 on Mar 12")
        XCTAssertEqual(line(Self.showState(status: "Completed", watched: 20, aired: 20, finished: true)), "Completed",
                       "A Completed card adds nothing but its section")
        XCTAssertEqual(line(Self.showState(status: "Planning", started: false)), "Planning")

        // A legacy row without TV state still reads its section, with Watching for In progress.
        let legacy = Spine.LibraryItem(media: Self.showItem("2", "Old", tv: nil).media,
                                       tracking: Self.trackingState(status: "In progress", tv: nil))
        XCTAssertEqual(TVLibraryCard.line(for: legacy), "Watching")
    }

    func testTV700GridBadgeNamesUpNextWhileWatchingPausedOrDropped() {
        func badge(_ tv: TVShowState) -> String? { TVLibraryCard.gridBadge(for: Self.showItem("1", "Show", tv: tv)) }

        XCTAssertEqual(badge(Self.showState(status: "In progress", upNext: (2, 4))), "S2 E4")
        XCTAssertEqual(badge(Self.showState(status: "Paused", upNext: (2, 3))), "S2 E3")
        XCTAssertEqual(badge(Self.showState(status: "Dropped", upNext: (2, 4))), "S2 E4")
        XCTAssertNil(badge(Self.showState(status: "In progress", caughtUp: true)))
        XCTAssertNil(badge(Self.showState(status: "Completed", finished: true)))
        XCTAssertNil(badge(Self.showState(status: "Planning", started: false)))
        // A Paused or Dropped show with nothing watched names no episode, though the server stores an up next.
        XCTAssertNil(badge(Self.showState(status: "Paused", started: false, upNext: (1, 1))))
        XCTAssertNil(badge(Self.showState(status: "Dropped", started: false, upNext: (1, 1))))
        XCTAssertEqual(badge(Self.showState(status: "In progress", upNext: (1, 1))), "S1 E1",
                       "Watching with nothing watched still names the first episode (TV-005)")
    }

    func testTV700EmptySectionsDescribeWhatAppearsThere() {
        let titles = LibraryShelf.available(for: "tv").map(LibraryShelf.tvEmptyTitle)
        let messages = LibraryShelf.available(for: "tv").map(LibraryShelf.tvEmptyMessage)
        XCTAssertEqual(titles, ["No shows in Watching", "No shows in Planning", "No paused shows", "No dropped shows",
                                "No completed shows"])
        XCTAssertEqual(messages, [
            "Shows you're watching will appear here.",
            "Shows you plan to watch will appear here.",
            "Shows you pause will appear here.",
            "Shows you drop will appear here.",
            "Shows you finish, or are caught up on between seasons, will appear here.",
        ])
        XCTAssertFalse(messages.contains { $0.localizedCaseInsensitiveContains("read") }, "No reading copy for TV")
    }

    // MARK: Continue Watching (TV-701, TV-100, TV-107, TV-403, TV-705)

    func testTV701HomeListsShowsFromContinueWatchingInTheServersOrder() async {
        tracking.continueWatchingResponse = PagedResponse(count: 2, next: nil, previous: nil, results: [
            // The server's order: by the latest watched day, then the recorded time. Not by updated_at.
            Self.showItem("1", "Severance", tv: Self.showState(status: "In progress", upNext: (1, 2)),
                          updatedAt: "2026-10-20T10:00:00Z"),
            Self.showItem("2", "The Bear", tv: Self.showState(status: "In progress", upNext: (3, 1), isNew: true),
                          updatedAt: "2026-10-25T10:00:00Z"),
        ])
        tracking.listResponses["movie"] = PagedResponse(count: 1, next: nil, previous: nil, results: [
            Self.item("9", "Heat", mediaType: "movie", updatedAt: "2026-10-22T10:00:00Z"),
        ])
        let viewModel = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv", "season", "episode", "movie"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(tracking.continueWatchingPages, [nil], "Shows come from Continue Watching")
        XCTAssertEqual(tracking.listRequests.map(\.mediaType), ["movie"], "No season rows are requested")
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Heat", "Severance", "The Bear"],
                       "Shows keep the server's order")
        let lines = viewModel.inProgressItems.compactMap(\.tracking.tv).compactMap { TVTrackingPresentation.continueWatchingLine(for: $0) }
        XCTAssertEqual(lines, ["S1 E2 next", "S3 E1 · New"])
    }

    // MARK: Continue Watching's episode sheet (TV-701, TV-100, TV-102, TV-107, TV-108, TV-403, TV-705)

    func testTV701TheLineOpensUpNextsSeasonWithItsRowAtOnceThenTheWholeSeason() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        media.details[Self.seasonRef(2).id] = Self.crownSeason(2, watched: 4)
        let gate = holdDetails()

        viewModel.continueWatching.openEpisodes(for: crown)

        let sheet = try XCTUnwrap(viewModel.continueWatching.episodes)
        XCTAssertEqual(sheet.seasonNumber, 2, "Up next's season")
        XCTAssertEqual(sheet.title, "The Crown · Season 2")
        XCTAssertEqual(sheet.rows.map(\.id), ["listed-5"], "Up next's row shows at once, from the card")
        XCTAssertEqual(sheet.rows.first?.title, "Marionettes")
        XCTAssertEqual(sheet.rows.first?.summary?.imageUrl, "https://img/s2e5.jpg")
        XCTAssertEqual(sheet.startRowID, "listed-5", "The list opens on up next")
        XCTAssertFalse(sheet.canLogSeason, "Log Season… waits for the season")
        XCTAssertEqual(sheet.season.changeOrigin, viewModel.continueWatching.changeOrigin,
                       "The sheet's writes post as Home's own, so its card stays put")

        // The rest of the season loads with the season page's own request.
        let load = Task { await sheet.season.load() }
        await gate.open()
        await load.value
        XCTAssertEqual(media.detailRequests, [Self.seasonRef(2)])
        XCTAssertEqual(sheet.rows.map(\.episodeNumber), Array(1...10), "The whole season")
        XCTAssertEqual(sheet.startRowID, "listed-5")
        XCTAssertTrue(sheet.canLogSeason)
        XCTAssertTrue(tracking.actions.isEmpty, "Opening it sends nothing")

        // A provider that names its seasons gives the sheet that name.
        media.details[Self.seasonRef(2).id] = Self.crownSeason(2, watched: 4, name: "Series 2")
        viewModel.continueWatching.episodes = nil
        _ = viewModel.continueWatching.sheetDidClose()
        viewModel.continueWatching.openEpisodes(for: crown)
        let named = try XCTUnwrap(viewModel.continueWatching.episodes)
        XCTAssertEqual(named.title, "The Crown · Season 2", "The season's number until it loads")
        await named.season.load()
        XCTAssertEqual(named.title, "The Crown · Series 2")
    }

    func testTV701TheSheetKeepsUpNextAtTheTopAsItsSeasonLoads() async throws {
        // The list opens on up next's row alone. When the season arrives it is a new list (`listID`), which the sheet
        // shows together with a request for the same row, now with rows above it; ticks change neither. Left to
        // SwiftUI's estimate of the rows arriving above it, the sheet opened rows away from up next (TV-701).
        let crown = Self.crown()
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        media.details[Self.seasonRef(2).id] = Self.crownSeason(2, watched: 4)
        let gate = holdDetails()

        viewModel.continueWatching.openEpisodes(for: crown)
        let sheet = try XCTUnwrap(viewModel.continueWatching.episodes)
        XCTAssertEqual(sheet.listID, "S2-up-next")
        XCTAssertEqual(sheet.rows.map(\.id), ["listed-5"])
        XCTAssertEqual(sheet.startRowID, "listed-5")
        XCTAssertEqual(sheet.rowsFromStart, 1)

        let load = Task { await sheet.season.load() }
        await gate.open()
        await load.value
        XCTAssertEqual(sheet.listID, "S2", "The whole season: the sheet asks for up next's row again")
        XCTAssertNotEqual(sheet.listID, "S2-up-next")
        XCTAssertEqual(sheet.startRowID, "listed-5", "The same row")
        XCTAssertEqual(sheet.rows.firstIndex { $0.id == sheet.startRowID }, 4, "Four rows above it")
        XCTAssertEqual(sheet.rowsFromStart, 6)

        // A tick changes the rows' ticks, not the list, which stays where the user has it.
        holdDetails()
        let ids = sheet.rows.map(\.id)
        tracking.actionResponses = [Self.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]
        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv)))
        _ = await sheet.season.settled()
        XCTAssertEqual(sheet.listID, "S2")
        XCTAssertEqual(sheet.rows.map(\.id), ids)
        XCTAssertEqual(sheet.startRowID, "listed-5")

        // Closed and opened again after the tick: the new up next's row is the one at the top.
        viewModel.continueWatching.episodes = nil
        _ = viewModel.continueWatching.sheetDidClose()
        media.detailGate = nil
        viewModel.continueWatching.openEpisodes(for: viewModel.inProgressItems[0])
        let again = try XCTUnwrap(viewModel.continueWatching.episodes)
        XCTAssertEqual(again.listID, "S2-up-next")
        XCTAssertEqual(again.startRowID, "listed-6")
        await again.season.load()
        XCTAssertEqual(again.listID, "S2")
        XCTAssertEqual(again.startRowID, "listed-6")
    }

    func testTV701UpNextReachesTheTopEvenAtTheEndOfTheSeason() async throws {
        // The list leaves room under its last row for up next's row to reach the top: up next E10 of 10 has only
        // itself below the top.
        let crown = Self.crown(watched: 9, upNext: (2, 10))
        let viewModel = homeViewModel()
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 9))
        XCTAssertEqual(sheet.startRowID, "listed-10")
        XCTAssertEqual(sheet.rowsFromStart, 1)
        // A row and the space under it take 100 points; the half-height sheet's list is about 340 points tall. The
        // room is two rows more than the list's height less the rows from up next down: a new list takes its place
        // before it knows the bars over its top and bottom.
        XCTAssertEqual(ContinueWatchingEpisodes.roomBelowRows(viewportHeight: 340, rowsFromStart: 1), 440)
        XCTAssertEqual(ContinueWatchingEpisodes.roomBelowRows(viewportHeight: 340, rowsFromStart: 3), 240)
        XCTAssertEqual(ContinueWatchingEpisodes.roomBelowRows(viewportHeight: 340, rowsFromStart: 6), 0,
                       "Mid-season, the rows below are room enough")
        XCTAssertEqual(ContinueWatchingEpisodes.roomBelowRows(viewportHeight: 760, rowsFromStart: 6), 360,
                       "At the large height, more")
    }

    func testTV701OnlyAShowWithAnUpNextOpensTheSheet() {
        let ticks = homeViewModel().continueWatching
        ticks.openEpisodes(for: Self.showItem("1", "Caught Up", tv: Self.showState(status: "In progress", caughtUp: true)))
        XCTAssertNil(ticks.episodes, "Without up next the card opens the show")
        ticks.openEpisodes(for: Self.item("9", "Heat", mediaType: "movie", updatedAt: nil))
        XCTAssertNil(ticks.episodes, "Other media are unchanged")
        XCTAssertNil(ContinueWatchingTicks.state(of: Self.item("9", "Heat", mediaType: "movie", updatedAt: nil)))
    }

    func testTV100ATickInTheSheetIsTheSeasonPagesTickAndUpdatesTheCardWhereItIs() async throws {
        let severance = Self.showItem("1", "Severance", tv: Self.showState(status: "In progress", watched: 1, aired: 9, upNext: (1, 2)))
        let heat = Self.item("9", "Heat", mediaType: "movie", updatedAt: nil)
        let crown = Self.crown()
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [severance, heat, crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))
        holdDetails()
        let e5 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv))
        XCTAssertFalse(sheet.season.tvIsWatched(e5))
        let origin = viewModel.continueWatching.changeOrigin
        let posted = expectation(forNotification: .mediaStateDidChange, object: nil) { notification in
            notification.userInfo?["origin"] as? UUID == origin
        }
        posted.assertForOverFulfill = false  // the untick below posts the same way
        tracking.actionResponses = [Self.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]

        sheet.toggle(e5)

        XCTAssertTrue(sheet.season.tvIsWatched(e5), "The tick shows at once")
        let ticked = await sheet.season.settled()
        XCTAssertTrue(ticked)
        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(tracking.actions.count, 1)
        XCTAssertEqual(sent.action, .tick)
        XCTAssertEqual(sent.mediaId, "1396", "Actions go to the show")
        XCTAssertEqual(sent.request.seasonNumber, 2)
        XCTAssertEqual(sent.request.episodeNumber, 5)
        XCTAssertNotNil(sent.request.mutationId, "A tick carries a mutation ID (TV-705)")
        XCTAssertEqual(sent.request.pageSeasonNumber, 2, "It names the season, as the season page does")
        XCTAssertTrue(sheet.season.tvIsWatched(e5), "The row stays ticked")

        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Severance", "Heat", "The Crown"],
                       "The show stays where it is")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[2]), "S2 E6 next · 5 of 10", "The card's line changes in place")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S1 E2 next · 1 of 9")
        XCTAssertTrue(tracking.continueWatchingPages.isEmpty, "Nothing reloads under the user's finger")
        XCTAssertTrue(viewModel.continueWatching.needsReload, "The rail re-sorts on the next visit")
        // The tick posts as Home's own write: Home reloads its activity, not its rail.
        await fulfillment(of: [posted], timeout: 2)

        // Tapping the filled tick unticks it, by the viewing the tick recorded (TV-102): the old undo.
        tracking.actionResponses = [TVActionResponse(
            tracking: Self.trackingState(status: "In progress",
                                         tv: Self.showState(status: "In progress", watched: 4, aired: 10, upNext: (2, 5))),
            envelope: TVWriteEnvelope(episode: TVEpisodeState(seasonNumber: 2, episodeNumber: 5), changed: true)
        )]
        sheet.toggle(e5)
        XCTAssertFalse(sheet.season.tvIsWatched(e5), "The untick shows at once")
        let unticked = await sheet.season.settled()
        XCTAssertTrue(unticked)
        let untick = try XCTUnwrap(tracking.actions.last)
        XCTAssertEqual(untick.action, .untick)
        XCTAssertEqual(untick.request.episodeNumber, 5)
        XCTAssertEqual(untick.request.viewingId, 501, "It names the viewing the tick recorded")
        XCTAssertNotNil(untick.request.mutationId)
        XCTAssertFalse(sheet.season.tvIsWatched(e5))
        XCTAssertEqual(Self.line(viewModel.inProgressItems[2]), "S2 E5 next · 4 of 10", "The card is as it was")
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Severance", "Heat", "The Crown"])

        // The next visit re-sorts the rail.
        viewModel.profile = Self.profile(enabled: ["tv"])
        tracking.continueWatchingResponse = PagedResponse(count: 2, next: nil, previous: nil,
                                                          results: [viewModel.inProgressItems[2], severance])
        await viewModel.loadInProgress()
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["The Crown", "Severance"])
        XCTAssertFalse(viewModel.continueWatching.needsReload)
    }

    func testTV701UpNextsRowTicksBeforeTheSeasonHasLoaded() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        media.details[Self.seasonRef(2).id] = Self.crownSeason(2, watched: 4)
        let loadGate = holdDetails()
        viewModel.continueWatching.openEpisodes(for: crown)
        let sheet = try XCTUnwrap(viewModel.continueWatching.episodes)
        let load = Task { await sheet.season.load() }
        let upNext = try XCTUnwrap(sheet.rows.first?.tv)
        tracking.actionResponses = [Self.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]

        sheet.toggle(upNext)

        XCTAssertTrue(sheet.season.tvIsWatched(upNext), "The tick shows while the season loads")
        let ticked = await sheet.season.settled()
        XCTAssertTrue(ticked)
        XCTAssertEqual(tracking.actions.map(\.action), [.tick])
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S2 E6 next · 5 of 10")

        // The season arrives, from before the tick: its row shows the tick all the same (TV-705).
        await waitUntil { media.detailRequests.count == 1 }
        holdDetails()
        await loadGate.open()
        await load.value
        let e5 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv))
        XCTAssertTrue(sheet.season.tvIsWatched(e5))
        XCTAssertEqual(sheet.season.confirmedTVEpisode(seasonNumber: 2, episodeNumber: 5)?.untickViewingId, 501)
    }

    func testTV108MarkWatchedUpToHereInTheSheetIsTheSeasonPagesLongPress() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))
        holdDetails()
        let e7 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[6].tv))
        let e6 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[5].tv))
        tracking.actionResponses = [TVActionResponse(
            tracking: Self.trackingState(status: "In progress",
                                         tv: Self.showState(status: "In progress", watched: 7, aired: 10, upNext: (2, 8))),
            envelope: TVWriteEnvelope(changed: true)
        )]

        sheet.season.askMarkTVUpTo(e7)
        let confirmation = try XCTUnwrap(sheet.season.tvConfirmation, "It asks first, naming the range")
        XCTAssertEqual(confirmation.prompt, "Mark S1 E1–S2 E7 watched?")
        sheet.confirm(confirmation)

        XCTAssertTrue(sheet.season.tvIsWatched(e6), "The range shows at once")
        let marked = await sheet.season.settled()
        XCTAssertTrue(marked)
        XCTAssertEqual(tracking.actions.map(\.action), [.markUpTo])
        XCTAssertEqual(tracking.actions.first?.request.seasonNumber, 2)
        XCTAssertEqual(tracking.actions.first?.request.episodeNumber, 7)
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S2 E8 next · 7 of 10")
    }

    func testTV403TheSeasonsLastTickOpensLogSeasonAtOnceThenTheSheetMovesToTheNextSeason() async throws {
        let crown = Self.crown(watched: 9, upNext: (2, 10))
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 9))
        media.details[Self.seasonRef(3).id] = Self.crownSeason(3)
        let refreshGate = holdDetails()
        let e10 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[9].tv))
        tracking.actionResponses = [Self.tickResponse(upNext: (3, 1), watched: 10, ticked: (2, 10), viewingId: 510,
                                                      finished: Self.finished(season: 2))]

        sheet.toggle(e10)
        let ticked = await sheet.season.settled()

        XCTAssertTrue(ticked)
        let composer = try XCTUnwrap(sheet.season.tvFollowUp.composer, "Log Season is up as soon as the tick saves")
        XCTAssertNil(sheet.season.tvFollowUp.loading, "Nothing is loaded first")
        XCTAssertEqual(composer.finished.seasonNumber, 2)
        XCTAssertEqual(composer.detail.ref, Self.seasonRef(2), "The season's own page detail")
        XCTAssertEqual(sheet.seasonNumber, 2, "The sheet waits under the composer")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S3 E1 next · 10 of 10")

        // Not now: up next has moved on to Season 3, which has aired, so the sheet shows it.
        sheet.season.tvFollowUp.composerDismissed()
        sheet.composerClosed()
        XCTAssertEqual(sheet.seasonNumber, 3)
        XCTAssertEqual(sheet.title, "The Crown · Season 3")
        XCTAssertEqual(sheet.rows.map(\.id), ["listed-1"], "Season 3 opens on its up next while it loads")
        XCTAssertNil(sheet.season.detail)
        XCTAssertEqual(sheet.season.changeOrigin, viewModel.continueWatching.changeOrigin)

        let load = Task { await sheet.season.load() }
        await waitUntil { media.detailRequests.contains(Self.seasonRef(3)) }  // the season page's request for Season 3
        holdDetails()
        await refreshGate.open()
        await load.value
        XCTAssertEqual(sheet.rows.map(\.episodeNumber), Array(1...10))
        XCTAssertEqual(sheet.startRowID, "listed-1")

        // A tick there reaches the card too.
        tracking.actionResponses = [TVActionResponse(
            tracking: Self.trackingState(status: "In progress",
                                         tv: Self.showState(status: "In progress", watched: 11, aired: 20, upNext: (3, 2))),
            envelope: TVWriteEnvelope(
                episode: TVEpisodeState(seasonNumber: 3, episodeNumber: 1, watched: true, viewingCount: 1,
                                        untickViewingId: 520),
                changed: true
            )
        )]
        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[0].tv)))
        _ = await sheet.season.settled()
        XCTAssertEqual(tracking.actions.last?.request.seasonNumber, 3)
        XCTAssertEqual(tracking.actions.last?.request.pageSeasonNumber, 3)
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S3 E2 next · 11 of 20")
    }

    func testTV403ASeasonThatLeavesTheShowCaughtUpStaysWithEveryTickFilled() async throws {
        let crown = Self.crown(watched: 9, upNext: (2, 10))
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 9))
        holdDetails()
        let e10 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[9].tv))
        let caughtUp = Self.showState(status: "In progress", watched: 10, aired: 10, caughtUp: true,
                                      nextAiring: TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: "2027-03-12"))
        tracking.actionResponses = [TVActionResponse(
            tracking: Self.trackingState(status: "In progress", tv: caughtUp),
            envelope: TVWriteEnvelope(
                tv: caughtUp,
                episode: TVEpisodeState(seasonNumber: 2, episodeNumber: 10, watched: true, viewingCount: 1,
                                        untickViewingId: 510),
                changed: true,
                finished: Self.finished(season: 2)
            )
        )]

        sheet.toggle(e10)
        _ = await sheet.season.settled()
        XCTAssertNotNil(sheet.season.tvFollowUp.composer)
        sheet.season.tvFollowUp.composerDismissed()
        sheet.composerClosed()

        XCTAssertEqual(sheet.seasonNumber, 2, "Nothing has aired after it: the sheet stays")
        let states = sheet.rows.compactMap { sheet.season.tvEpisodeState($0.tv) }
        XCTAssertEqual(states.count, 10)
        XCTAssertTrue(states.allSatisfy { sheet.season.tvIsWatched($0) }, "Every tick filled")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "Caught up · S3 on Mar 12",
                       "The card reads as its Library card until Home loads again")
    }

    func testTV107TheSheetsTickAsksTheEarlierEpisodesQuestionFirstAndItsYesReachesTheCard() async throws {
        let crown = Self.crown(watched: 0, upNext: (3, 1))
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [Self.item("9", "Heat", mediaType: "movie", updatedAt: nil), crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(3))
        holdDetails()
        let question = TVEarlierUnwatched(
            from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 10),
            count: 20,
            question: "Also mark Seasons 1–2 watched?"
        )
        tracking.actionResponses = [
            Self.tickResponse(upNext: (3, 2), watched: 1, ticked: (3, 1), viewingId: 520, earlierUnwatched: question),
            TVActionResponse(
                tracking: Self.trackingState(status: "In progress",
                                             tv: Self.showState(status: "In progress", watched: 21, aired: 30, upNext: (3, 2))),
                envelope: TVWriteEnvelope(changed: true)
            ),
        ]

        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[0].tv)))
        _ = await sheet.season.settled()

        let followUp = sheet.season.tvFollowUp
        XCTAssertEqual(followUp.question?.question, "Also mark Seasons 1–2 watched?")
        XCTAssertNil(followUp.composer, "The question comes before any composer")
        await followUp.answer(yes: true)
        XCTAssertEqual(tracking.actions.map(\.action), [.tick, .markEarlier])
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Heat", "The Crown"])
        XCTAssertEqual(Self.line(viewModel.inProgressItems[1]), "S3 E2 next · 21 of 30")
    }

    func testTV705AFailedTickOrUntickInTheSheetRestoresTheRowAndSaysSo() async throws {
        let crown = Self.crown(watched: 5, upNext: (2, 6))
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 5))
        holdDetails()
        let e6 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[5].tv))
        tracking.actionError = APIError.httpStatus(
            503, #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#
        )

        sheet.toggle(e6)
        XCTAssertTrue(sheet.season.tvIsWatched(e6))
        let ticked = await sheet.season.settled()

        XCTAssertFalse(ticked)
        XCTAssertFalse(sheet.season.tvIsWatched(e6), "A failure restores the row")
        XCTAssertEqual(sheet.failureTitle, "Could not mark watched")
        XCTAssertEqual(sheet.season.quickActionErrorMessage, "TV details are unavailable right now",
                       "The short message the sheet's alert shows, with its error haptic")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S2 E6 next · 5 of 10", "The card is unchanged")
        XCTAssertFalse(viewModel.continueWatching.needsReload)
        XCTAssertNil(sheet.season.tvFollowUp.composer)

        // An untick the server refuses restores the filled tick, with its reason.
        sheet.season.quickActionErrorMessage = nil
        tracking.actionError = APIError.httpStatus(
            409, #"{"error":{"code":"tv_log_blocks","message":"Delete your Season 2 log first."}}"#
        )
        let e5 = try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv))
        sheet.toggle(e5)
        XCTAssertFalse(sheet.season.tvIsWatched(e5))
        let unticked = await sheet.season.settled()
        XCTAssertFalse(unticked)
        XCTAssertTrue(sheet.season.tvIsWatched(e5))
        XCTAssertEqual(sheet.failureTitle, "Could not mark unwatched")
        XCTAssertEqual(sheet.season.quickActionErrorMessage, "Delete your Season 2 log first.")
        XCTAssertEqual(tracking.actions.map(\.action), [.tick, .untick])
        XCTAssertTrue(tracking.continueWatchingPages.isEmpty, "Nothing reloads after a failure")
    }

    func testTV403ASheetClosedWhileItsTickIsOnItsWayStillOpensLogSeason() async throws {
        let crown = Self.crown(watched: 9, upNext: (2, 10))
        let viewModel = homeViewModel()
        let ticks = viewModel.continueWatching
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 9))
        holdDetails()
        let answer = RefreshGate()
        heldGates.append(answer)
        tracking.actionGate = answer
        tracking.actionResponses = [Self.tickResponse(upNext: (3, 1), watched: 10, ticked: (2, 10), viewingId: 510,
                                                      finished: Self.finished(season: 2))]

        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[9].tv)))
        ticks.episodes = nil  // swiped away before the tick answers
        XCTAssertNil(ticks.sheetDidClose(), "A swipe opens no page")
        XCTAssertTrue(ticks.closedEpisodes === sheet, "The screen keeps what the tick will ask")

        await answer.open()
        _ = await sheet.season.settled()
        XCTAssertEqual(ticks.closedEpisodes?.season.tvFollowUp.composer?.finished.seasonNumber, 2,
                       "Log Season still opens, over the screen")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S3 E1 next · 10 of 10")
    }

    func testTV701ARowOrOpenShowOpensItsPageOnceTheSheetHasClosed() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        let ticks = viewModel.continueWatching
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))

        sheet.openEpisode(try XCTUnwrap(sheet.rows[6].summary))

        XCTAssertNil(ticks.episodes, "The sheet closes first")
        let episode = try XCTUnwrap(ticks.sheetDidClose())
        XCTAssertEqual(episode.ref, MediaRef(itemId: nil, source: "tmdb", mediaType: "episode", mediaId: "1396",
                                             seasonNumber: 2, episodeNumber: 7))
        XCTAssertEqual(episode.context.refs.map(\.episodeNumber), Array(1...10).map { Optional($0) },
                       "Its page pages through the season's episodes, as from the season page")
        XCTAssertNil(ticks.closedEpisodes, "Nothing was on its way")

        ticks.openEpisodes(for: crown)
        ticks.episodes?.openShow()
        XCTAssertNil(ticks.episodes)
        XCTAssertEqual(ticks.sheetDidClose()?.ref, crown.media.ref, "Open Show")

        ticks.openEpisodes(for: crown)
        ticks.episodes = nil
        XCTAssertNil(ticks.sheetDidClose(), "✕ or a swipe opens nothing")
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testTV701ARowTappedWhileATickIsOnItsWayOpensOnceTheTickAnswersAndNotWhenItFails() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        let ticks = viewModel.continueWatching
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))
        holdDetails()
        let answer = RefreshGate()
        heldGates.append(answer)
        tracking.actionGate = answer
        tracking.actionResponses = [Self.tickResponse(upNext: (2, 6), watched: 5, ticked: (2, 5), viewingId: 501)]

        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv)))
        sheet.openEpisode(try XCTUnwrap(sheet.rows[6].summary))
        XCTAssertTrue(ticks.episodes === sheet, "The sheet waits for the tick")

        await answer.open()
        await ticks.opening?.value
        XCTAssertNil(ticks.episodes, "The tick asked nothing: the sheet closes for the page")
        XCTAssertEqual(ticks.sheetDidClose()?.ref.episodeNumber, 7)
        XCTAssertNil(ticks.closedEpisodes, "Nothing is left to show over the screen")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S2 E6 next · 5 of 10")

        // A tick that fails keeps the sheet, which shows its message.
        let again = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))
        holdDetails()
        let failure = RefreshGate()
        heldGates.append(failure)
        tracking.actionGate = failure
        tracking.actionError = APIError.httpStatus(
            503, #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#
        )
        again.toggle(try XCTUnwrap(again.season.tvEpisodeState(again.rows[4].tv)))
        again.openShow()
        XCTAssertTrue(ticks.episodes === again)

        await failure.open()
        await ticks.opening?.value
        XCTAssertTrue(ticks.episodes === again, "The page doesn't open over a failure")
        XCTAssertEqual(again.season.quickActionErrorMessage, "TV details are unavailable right now")
        ticks.episodes = nil
        XCTAssertNil(ticks.sheetDidClose(), "The page isn't opened later")
    }

    func testTV403APageTappedWhileTheFinalesTickIsOnItsWayLeavesLogSeasonOverTheSheet() async throws {
        let crown = Self.crown(watched: 9, upNext: (2, 10))
        let viewModel = homeViewModel()
        let ticks = viewModel.continueWatching
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 9))
        media.details[Self.seasonRef(3).id] = Self.crownSeason(3)
        holdDetails()
        let answer = RefreshGate()
        heldGates.append(answer)
        tracking.actionGate = answer
        tracking.actionResponses = [Self.tickResponse(upNext: (3, 1), watched: 10, ticked: (2, 10), viewingId: 510,
                                                      finished: Self.finished(season: 2))]

        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[9].tv)))
        sheet.openShow()
        XCTAssertTrue(ticks.episodes === sheet)

        await answer.open()
        await ticks.opening?.value
        XCTAssertTrue(ticks.episodes === sheet, "Log Season opens over the sheet, not under the show's page")
        XCTAssertEqual(sheet.season.tvFollowUp.composer?.finished.seasonNumber, 2)

        sheet.season.tvFollowUp.composerDismissed()
        sheet.composerClosed()
        XCTAssertTrue(ticks.episodes === sheet, "The show's page doesn't open after the composer")
        XCTAssertEqual(sheet.seasonNumber, 3)
        ticks.episodes = nil
        XCTAssertNil(ticks.sheetDidClose())
    }

    func testTV705ATickThatFailsAfterTheSheetClosedStillSaysSo() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        let ticks = viewModel.continueWatching
        viewModel.inProgressItems = [crown]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))
        holdDetails()
        let answer = RefreshGate()
        heldGates.append(answer)
        tracking.actionGate = answer
        tracking.actionError = APIError.httpStatus(
            503, #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#
        )

        sheet.toggle(try XCTUnwrap(sheet.season.tvEpisodeState(sheet.rows[4].tv)))
        ticks.episodes = nil  // ✕ before the tick answers
        XCTAssertNil(ticks.sheetDidClose())
        XCTAssertTrue(ticks.closedEpisodes === sheet, "The screen keeps the write")

        await answer.open()
        let ticked = await sheet.season.settled()
        XCTAssertFalse(ticked)
        XCTAssertEqual(ticks.closedEpisodes?.failureTitle, "Could not mark watched")
        XCTAssertEqual(ticks.closedEpisodes?.season.quickActionErrorMessage, "TV details are unavailable right now",
                       "The message the screen's alert shows, with its error haptic")
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S2 E5 next · 4 of 10", "The card is unchanged")
    }

    func testTV404LogSeasonInTheSheetsMenuFollowsWhatTheSeasonPageOffers() async throws {
        let crown = Self.crown()
        let viewModel = homeViewModel()
        viewModel.inProgressItems = [crown]
        var season = Self.crownSeason(2, watched: 4)
        season.userState?.tv?.season?.availableActions = ["rate"]
        let sheet = try await openLoadedSheet(viewModel, for: crown, season: season)
        XCTAssertFalse(sheet.canLogSeason, "Not offered when the season's page wouldn't offer it")

        let loggable = try await openLoadedSheet(viewModel, for: crown, season: Self.crownSeason(2, watched: 4))
        XCTAssertTrue(loggable.canLogSeason)

        // A log saved from it moves the sheet on once its composer closes, as Log Season after a tick does.
        media.details[Self.seasonRef(3).id] = Self.crownSeason(3)
        let after = Self.showState(status: "In progress", watched: 10, aired: 10, upNext: (3, 1))
        loggable.season.applyLogSave(MediaLogSaveResult(
            ref: Self.seasonRef(2), tracking: nil, diaryEntry: nil,
            tvEnvelope: TVWriteEnvelope(tv: after, changed: true)
        ))
        XCTAssertEqual(Self.line(viewModel.inProgressItems[0]), "S3 E1 next · 10 of 10", "The card takes the log")
        loggable.season.releaseDeferredTVFollowUp()
        loggable.composerClosed()
        XCTAssertEqual(loggable.seasonNumber, 3)
    }

    func testTV701ProfileListsShowsToo() async {
        // Profile's In progress rail is posters only: it lists shows, not seasons, and has no episode sheet.
        let crown = Self.crown()
        tracking.continueWatchingResponse = PagedResponse(count: 1, next: nil, previous: nil, results: [crown])
        let viewModel = ProfileViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv", "season", "episode"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            onUnauthorized: {}
        )

        await viewModel.load()
        XCTAssertEqual(tracking.continueWatchingPages, [nil])
        XCTAssertTrue(tracking.listRequests.isEmpty, "No season rows are requested")
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["The Crown"])
    }

    func testTV703TheSheetsTickReloadsActivityButLeavesTheRail() async {
        // A tick is the day's progress in activity, so Home reloads its activity after a tick or untick in its own
        // sheet (activity contract AC-4), and leaves the rail where it is (TV-701). Profile has no sheet: any write
        // reloads it.
        let crown = Self.showItem("1396", "The Crown", tv: Self.showState(status: "In progress", watched: 4, aired: 10, upNext: (2, 5)))
        tracking.continueWatchingResponse = PagedResponse(count: 1, next: nil, previous: nil, results: [crown])
        let progress = ActivityItem(
            id: -7,
            type: "progress_updated",
            createdAt: "2026-10-26T20:00:00Z",
            actor: UserSummary(id: 1, username: "armaan", displayName: "Armaan", avatarUrl: nil),
            media: crown.media,
            object: ActivityObject(type: "progress_day", id: -7, previous: nil, current: nil, rating: nil, liked: nil, name: nil)
        )
        let homeActivity = CountingActivityRepository()
        let home = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: homeActivity,
            mediaRepository: media,
            diaryRepository: diary,
            onUnauthorized: {}
        )
        await home.load()
        XCTAssertEqual(homeActivity.requestCount, 1)
        XCTAssertEqual(tracking.continueWatchingPages.count, 1)

        homeActivity.results = [progress]
        await home.mediaStateDidChange(origin: home.continueWatching.changeOrigin)
        XCTAssertEqual(home.activityItems.map(\.id), [-7], "Home's activity shows the tick")
        XCTAssertEqual(tracking.continueWatchingPages.count, 1, "The rail doesn't reload")
        // Any other screen's write reloads Home.
        await home.mediaStateDidChange(origin: UUID())
        XCTAssertEqual(homeActivity.requestCount, 3)
        XCTAssertEqual(tracking.continueWatchingPages.count, 2)
    }

    func testTV701TheCardsHaveNoTouchAndHoldMenu() throws {
        // The touch-and-hold menu is gone from Continue Watching: the card's title and line open the episode sheet.
        let features = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features", isDirectory: true)
        for path in ["Home/ContinueWatching.swift", "Home/HomeView.swift", "Profile/ProfileView.swift"] {
            let source = try String(contentsOf: features.appendingPathComponent(path), encoding: .utf8)
            XCTAssertFalse(source.contains(".contextMenu"), "\(path) has a context menu")
            XCTAssertFalse(source.contains("continueWatchingMenu"), "\(path) still builds the card's menu")
        }
    }

    // MARK: Settings (TV-901)

    func testTV901QuickLogDateIsHiddenAndItsStoredValueKept() async throws {
        XCTAssertEqual(LoggingSettingsRow.allCases.map(\.title), ["Week starts"])
        XCTAssertFalse(LoggingSettingsRow.allCases.contains { $0.title.localizedCaseInsensitiveContains("quick") })

        let profiles = SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"], quickWatchDate: "release_date"))
        let viewModel = ProfileSettingsViewModel(profileRepository: profiles, mediaRepository: media, onUnauthorized: {})
        viewModel.load(profile: profiles.profile)
        viewModel.weekStartDay = "sunday"
        await viewModel.savePreferences()
        let sent = try XCTUnwrap(profiles.preferenceRequests.first)
        XCTAssertEqual(sent.quickWatchDate, "release_date", "The stored preference stays as it was")
    }

    // MARK: Diary (TV-702, TV-407, TV-406, TV-404)

    func testTV702DiaryTVFilterIncludesShowsSeasonsAndEpisodes() async throws {
        let viewModel = DiaryViewModel(
            diaryRepository: diary,
            filterOptionsRepository: SurfacesFilterOptionsRepository(),
            onUnauthorized: {}
        )
        viewModel.filter.mediaType = "tv"
        await viewModel.load()
        let filter = try XCTUnwrap(diary.pageFilters.first)
        XCTAssertNil(filter.mediaType)
        XCTAssertEqual(filter.mediaTypes, ["tv", "season", "episode"])

        viewModel.filter.mediaType = "movie"
        await viewModel.load()
        XCTAssertEqual(diary.pageFilters.last?.mediaType, "movie", "Other types send themselves")
    }

    func testTV702DiaryRowsTitleTVLogsWithTheShow() throws {
        func parts(_ mediaType: String, title: String, show: String? = nil, season: Int? = nil, episode: Int? = nil,
                   episodeTitle: String? = nil) throws -> (title: String, year: String?) {
            DiaryEntryRow.titleParts(for: try Self.entry(mediaType: mediaType, title: title, showTitle: show,
                                                         season: season, episode: episode, episodeTitle: episodeTitle).media)
        }

        let season = try parts("season", title: "Breaking Bad", show: "Breaking Bad", season: 1)
        XCTAssertEqual(season.title, "Breaking Bad · Season 1")
        XCTAssertNil(season.year)
        XCTAssertEqual(try parts("season", title: "Breaking Bad", show: "Breaking Bad", season: 0).title, "Breaking Bad · Specials")
        XCTAssertEqual(try parts("episode", title: "Cancer Man", show: "Breaking Bad", season: 1, episode: 4,
                                 episodeTitle: "Cancer Man").title, "Breaking Bad · S1 E4 · Cancer Man")
        XCTAssertEqual(try parts("episode", title: "Breaking Bad", show: "Breaking Bad", season: 1, episode: 4).title,
                       "Breaking Bad · S1 E4", "No episode title, as from Trakt")
        let show = try parts("tv", title: "Breaking Bad (2008)")
        XCTAssertEqual(show.title, "Breaking Bad")
        XCTAssertEqual(show.year, "2008")
        let movie = try parts("movie", title: "Heat (1995)")
        XCTAssertEqual(movie.title, "Heat")
        XCTAssertEqual(movie.year, "1995")
    }

    func testTV407LogDatesAreCappedAtTodayAndSentAsCalendarDates() throws {
        let now = Date(timeIntervalSince1970: 1_793_000_000)
        for mediaType in ["tv", "season", "episode", "movie", "book"] {
            XCTAssertEqual(DiaryLogDateLimit.latest(for: Self.ref(mediaType), now: now), now, mediaType)
        }
        XCTAssertEqual(DiaryLogDateLimit.latest(for: Self.ref("anime"), now: now), .distantFuture)
        XCTAssertEqual(DiaryLogDateLimit.range(for: Self.ref("episode"), now: now).upperBound, now)

        // Editing a TV log sends the day only.
        let entry = try Self.entry(mediaType: "episode", title: "Fly", showTitle: "Breaking Bad", season: 3, episode: 10,
                                   consumedAt: "2026-10-25T00:00:00Z")
        let edit = DiaryLogEditViewModel(entry: entry, diaryRepository: diary)
        let body = try Self.object(try edit.request())
        XCTAssertEqual(body["consumed_at"] as? String, "2026-10-25")
        XCTAssertEqual(body["visibility"] as? String, "public", "TV keeps per-entry visibility")
    }

    func testTV407DiaryCreatePicksADayForTVAndRefusesTheFuture() async {
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                             onUnauthorized: {})
        viewModel.select(Self.summary("tv"))
        XCTAssertTrue(viewModel.usesCalendarDate)
        XCTAssertLessThanOrEqual(viewModel.latestConsumedDate, Date())

        viewModel.consumedAt = Date().addingTimeInterval(3 * 86_400)
        let saved = await viewModel.create()
        XCTAssertFalse(saved)
        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertTrue(diary.tvCreates.isEmpty)

        viewModel.select(Self.summary("anime"))
        XCTAssertFalse(viewModel.usesCalendarDate)
    }

    func testTV404DiaryCreateUsesCreateTVAndTheServersRewatchDefault() async throws {
        tracking.detailResult = .success(Self.trackingState(status: "Completed", tv: Self.showState(
            status: "Completed", finished: true,
            composer: TVComposer(defaultTarget: "season:2", targets: [
                TVComposerTarget(id: "season:2", level: .season, seasonNumber: 2, title: "Season 2", date: "2026-10-26"),
                TVComposerTarget(id: "show", level: .show, title: "Whole show", date: "2026-10-26", isRewatch: true),
            ])
        )))
        diary.createTVEnvelope = TVWriteEnvelope(changed: true)
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                             onUnauthorized: {})
        viewModel.select(Self.summary("tv"))
        XCTAssertFalse(viewModel.isRewatch, "Nothing is guessed before the server answers")

        await viewModel.loadRewatchDefault()
        XCTAssertEqual(tracking.detailRequests.map(\.mediaType), ["tv"])
        XCTAssertTrue(viewModel.isRewatch, "The show target's server default")
        XCTAssertTrue(viewModel.hasServerRewatchDefault)

        viewModel.consumedAt = Self.day("2026-10-24", calendar: .autoupdatingCurrent)
        let saved = await viewModel.create()
        XCTAssertTrue(saved)
        XCTAssertTrue(diary.creates.isEmpty, "TV logs go through createTV")
        let body = try Self.object(try XCTUnwrap(diary.tvCreates.first))
        XCTAssertEqual(body["is_rewatch"] as? Bool, true)
        XCTAssertEqual(body["consumed_at"] as? String, "2026-10-24", "A calendar date (TV-407)")
        XCTAssertNotNil(body["mutation_id"] as? String, "Applies at most once (TV-705)")
        XCTAssertNil(viewModel.tvFollowUp, "Nothing to follow up")
    }

    func testTV404DiaryCreateLeavesRewatchToTheServerWhenItsDefaultIsUnknown() async throws {
        tracking.detailResult = .failure(APIError.httpStatus(404, nil))
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                             onUnauthorized: {})
        viewModel.select(Self.summary("tv"))
        await viewModel.loadRewatchDefault()
        XCTAssertFalse(viewModel.hasServerRewatchDefault)
        XCTAssertNil(viewModel.isRewatchForRequest, "Unknown default and an untouched toggle: the server decides")
        XCTAssertFalse(viewModel.isRewatch, "Nothing is guessed on screen")
        _ = await viewModel.create()
        let untouched = try Self.object(try XCTUnwrap(diary.tvCreates.last))
        XCTAssertNil(untouched["is_rewatch"], "The request leaves is_rewatch out, so the server's default applies")

        // Touching the toggle sends what it shows.
        viewModel.setRewatch(true)
        XCTAssertEqual(viewModel.isRewatchForRequest, true)
        _ = await viewModel.create()
        let sent = try Self.object(try XCTUnwrap(diary.tvCreates.last))
        XCTAssertEqual(sent["is_rewatch"] as? Bool, true)

        // Other types always send their toggle.
        viewModel.select(Self.summary("anime"))
        XCTAssertEqual(viewModel.isRewatchForRequest, false)
    }

    func testTV404AnEditedToggleIsNotReplacedByALateDefault() async {
        tracking.detailResult = .success(Self.trackingState(status: "Completed", tv: Self.showState(
            status: "Completed", finished: true,
            composer: TVComposer(defaultTarget: "show", targets: [
                TVComposerTarget(id: "show", level: .show, title: "Whole show", date: "2026-10-26", isRewatch: true),
            ])
        )))
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                             onUnauthorized: {})
        viewModel.select(Self.summary("tv"))
        viewModel.setRewatch(false)
        await viewModel.loadRewatchDefault()
        XCTAssertFalse(viewModel.isRewatch)
    }

    func testTV107DiaryCreateHandsItsEnvelopeToTheFollowUp() async throws {
        diary.createTVEnvelope = TVWriteEnvelope(changed: true, finished: Self.finished(season: 1))
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                             onUnauthorized: {})
        viewModel.select(Self.summary("tv"))
        let saved = await viewModel.create()
        XCTAssertTrue(saved)

        let followUp = try XCTUnwrap(viewModel.tvFollowUp)
        XCTAssertEqual(followUp.showRef.mediaId, "1396")
        await followUp.loading?.value
        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 1, "TV-403: the finished season's composer opens")

        // The earlier-episodes question comes first when the response asks it.
        diary.createTVEnvelope = TVWriteEnvelope(changed: true, earlierUnwatched: TVEarlierUnwatched(
            from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 3),
            count: 3,
            question: "Also mark S1 E1–S1 E3 watched?"
        ))
        let second = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                          onUnauthorized: {})
        second.select(Self.summary("tv"))
        _ = await second.create()
        XCTAssertEqual(second.tvFollowUp?.question?.question, "Also mark S1 E1–S1 E3 watched?")
    }

    func testOtherTypesKeepTheirDiaryCreate() async throws {
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking,
                                             onUnauthorized: {})
        viewModel.select(Self.summary("anime"))
        await viewModel.loadRewatchDefault()
        _ = await viewModel.create()
        XCTAssertTrue(diary.tvCreates.isEmpty)
        let body = try Self.object(try XCTUnwrap(diary.creates.first))
        XCTAssertNotNil(body["is_rewatch"])
        XCTAssertNil(body["mutation_id"])
        XCTAssertTrue(tracking.detailRequests.isEmpty)
    }

    func testTV406DeletingALogThatUnmarksEpisodesAsksAndSendsTheVersion() async throws {
        diary.detailResults = [try Self.entry(mediaType: "season", title: "Breaking Bad", showTitle: "Breaking Bad",
                                              season: 1, tvLog: #"{"level":"season","season_number":1,"unmarks_count":7,"covers_count":10,"state_version":1793051512345}"#)]
        let viewModel = DiaryLogDetailViewModel(entryId: 31, diaryRepository: diary, mediaRepository: media, onUnauthorized: {})
        await viewModel.load()

        XCTAssertEqual(viewModel.deletePrompt, "Delete this log? 7 episodes it marked watched will be unmarked.")
        let deleted = await viewModel.confirmDelete()
        XCTAssertTrue(deleted)
        let call = try XCTUnwrap(diary.deletes.first)
        XCTAssertEqual(call.id, 31)
        XCTAssertEqual(call.expectedVersion, 1_793_051_512_345)
        XCTAssertNotNil(call.mutationId)
    }

    func testTV406ALogThatUnmarksNothingAsksTheUsualQuestion() async throws {
        diary.detailResults = [try Self.entry(mediaType: "episode", title: "Fly", showTitle: "Breaking Bad", season: 3,
                                              episode: 10, tvLog: #"{"level":"episode","season_number":3,"episode_number":10,"unmarks_count":0,"covers_count":1,"viewing_id":9912,"state_version":5}"#)]
        let viewModel = DiaryLogDetailViewModel(entryId: 31, diaryRepository: diary, mediaRepository: media, onUnauthorized: {})
        await viewModel.load()
        XCTAssertEqual(viewModel.deletePrompt, "Delete this diary entry?")
        _ = await viewModel.confirmDelete()
        XCTAssertEqual(diary.deletes.first?.expectedVersion, 5, "The version still guards a change since the dialog")
    }

    func testTV705AStaleDeleteRefreshesAndAsksAgainWithTheNewCount() async throws {
        diary.detailResults = [
            try Self.entry(mediaType: "season", title: "Breaking Bad", showTitle: "Breaking Bad", season: 1,
                           tvLog: #"{"level":"season","season_number":1,"unmarks_count":7,"covers_count":10,"state_version":1}"#),
            try Self.entry(mediaType: "season", title: "Breaking Bad", showTitle: "Breaking Bad", season: 1,
                           tvLog: #"{"level":"season","season_number":1,"unmarks_count":9,"covers_count":10,"state_version":2}"#),
        ]
        diary.deleteErrors = [APIError.httpStatus(409, #"{"error":{"code":"tv_state_changed","message":"This show changed.","tv":{"state_version":2,"count":9}}}"#)]
        let viewModel = DiaryLogDetailViewModel(entryId: 31, diaryRepository: diary, mediaRepository: media, onUnauthorized: {})
        await viewModel.load()

        let first = await viewModel.confirmDelete()
        XCTAssertFalse(first, "Nothing is deleted")
        XCTAssertEqual(viewModel.deleteReconfirmations, 1, "The dialog opens again")
        XCTAssertNil(viewModel.deleteErrorMessage)
        XCTAssertEqual(viewModel.deletePrompt, "Delete this log? 9 episodes it marked watched will be unmarked.")

        let second = await viewModel.confirmDelete()
        XCTAssertTrue(second)
        XCTAssertEqual(diary.deletes.map(\.expectedVersion), [1, 2])
        XCTAssertNotEqual(diary.deletes[0].mutationId, diary.deletes[1].mutationId, "Each confirmation is its own action")
    }

    func testTV705RetryingAFailedDeleteReusesItsMutationId() async throws {
        diary.detailResults = [try Self.entry(mediaType: "tv", title: "Breaking Bad",
                                              tvLog: #"{"level":"show","unmarks_count":3,"covers_count":62,"state_version":4}"#)]
        diary.deleteErrors = [URLError(.networkConnectionLost)]
        let viewModel = DiaryLogDetailViewModel(entryId: 31, diaryRepository: diary, mediaRepository: media, onUnauthorized: {})
        await viewModel.load()

        let failed = await viewModel.confirmDelete()
        XCTAssertFalse(failed)
        XCTAssertNotNil(viewModel.deleteErrorMessage)
        let retried = await viewModel.delete()
        XCTAssertTrue(retried)
        XCTAssertEqual(diary.deletes.count, 2)
        XCTAssertEqual(diary.deletes[0].mutationId, diary.deletes[1].mutationId)
    }

    func testOtherLogsDeleteAsBefore() async throws {
        diary.detailResults = [try Self.entry(mediaType: "movie", title: "Heat (1995)")]
        let viewModel = DiaryLogDetailViewModel(entryId: 31, diaryRepository: diary, mediaRepository: media, onUnauthorized: {})
        await viewModel.load()
        XCTAssertEqual(viewModel.deletePrompt, "Delete this diary entry?")
        _ = await viewModel.confirmDelete()
        XCTAssertEqual(diary.plainDeletes, [31])
        XCTAssertTrue(diary.deletes.isEmpty)
    }

    // MARK: Fixtures

    nonisolated static let utc: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }()

    static func day(_ value: String, calendar: Calendar = utc) -> Date {
        TVTrackingPresentation.calendarDate(value, calendar: calendar)!
    }

    static func ref(_ mediaType: String, id: String = "1396") -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: mediaType, mediaId: id, seasonNumber: nil, episodeNumber: nil)
    }

    static func summary(_ mediaType: String) -> MediaSummary {
        MediaSummary(ref: ref(mediaType), title: "Breaking Bad", posterUrl: nil, posterOrientation: .portrait)
    }

    static func showState(
        status: String?,
        started: Bool = true,
        watched: Int? = nil,
        aired: Int? = nil,
        finished: Bool = false,
        caughtUp: Bool = false,
        upNext: (Int, Int)? = nil,
        isNew: Bool = false,
        nextAiring: TVAiring? = nil,
        premiere: TVAiring? = nil,
        rewatch: TVRewatch? = nil,
        composer: TVComposer? = nil
    ) -> TVShowState {
        TVShowState(
            version: 1_793_051_500_000,
            inLibrary: status != nil,
            status: status,
            started: started,
            finished: finished,
            caughtUp: caughtUp,
            watchedCount: watched,
            airedCount: aired,
            upNext: upNext.map { TVEpisodePointer(seasonNumber: $0.0, episodeNumber: $0.1, isNew: isNew) },
            nextAiring: nextAiring,
            premiere: premiere,
            rewatch: rewatch,
            composer: composer
        )
    }

    static func trackingState(status: String?, tv: TVShowState?, updatedAt: String? = nil) -> TrackingState {
        TrackingState(trackingId: 1, status: status, rating: nil, progress: nil, repeats: nil, startDate: nil,
                      endDate: nil, notes: nil, updatedAt: updatedAt, tv: tv)
    }

    static func showItem(_ id: String, _ title: String, tv: TVShowState?, updatedAt: String? = nil) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref("tv", id: id), title: title, posterUrl: nil, posterOrientation: .portrait),
            tracking: trackingState(status: tv?.status ?? "In progress", tv: tv, updatedAt: updatedAt)
        )
    }

    static func item(_ id: String, _ title: String, mediaType: String, updatedAt: String?) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref(mediaType, id: id), title: title, posterUrl: nil, posterOrientation: .portrait),
            tracking: trackingState(status: "In progress", tv: nil, updatedAt: updatedAt)
        )
    }

    func homeViewModel() -> HomeViewModel {
        HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            diaryRepository: diary,
            onUnauthorized: {}
        )
    }

    /// The Crown in Continue Watching: S2 E5 up next, `watched` of 10 aired episodes watched, with up next's title and
    /// still as Continue Watching sends them.
    static func crown(watched: Int = 4, upNext: (Int, Int)? = (2, 5)) -> Spine.LibraryItem {
        var tv = showState(status: "In progress", watched: watched, aired: 10, upNext: upNext)
        tv.upNext?.title = "Marionettes"
        tv.upNext?.imageUrl = "https://img/s2e5.jpg"
        return showItem("1396", "The Crown", tv: tv)
    }

    static func seasonRef(_ number: Int) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1396", seasonNumber: number, episodeNumber: nil)
    }

    /// A season of The Crown as its page loads it: ten aired episodes, the first `watched` of them ticked.
    static func crownSeason(_ number: Int, watched: Int = 0, name: String? = nil) -> MediaDetail {
        let episodes = (1...10).map { episode in
            let isWatched = episode <= watched
            let tv = TVEpisodeState(
                seasonNumber: number,
                episodeNumber: episode,
                watched: isWatched,
                viewingCount: isWatched ? 1 : 0,
                untickViewingId: isWatched ? 400 + episode : nil,
                canMarkUpTo: true,
                markUpToPrompt: "Mark S1 E1–S\(number) E\(episode) watched?"
            )
            return EpisodeSummary(episodeNumber: episode, title: "Episode \(episode)", overview: nil, airDate: nil,
                                  runtime: nil, imageUrl: nil, rating: nil, tv: tv)
        }
        var tv = showState(status: "In progress", watched: watched, aired: 10, upNext: (number, watched + 1))
        tv.season = TVSeasonState(seasonNumber: number, started: watched > 0, status: watched > 0 ? "In progress" : nil,
                                  state: watched > 0 ? .inProgress : .notStarted, watchedCount: watched, airedCount: 10,
                                  episodeCount: 10, availableActions: ["log_season", "rate"])
        return MediaDetail(
            ref: seasonRef(number),
            title: "The Crown",
            userState: UserMediaState(isTracked: watched > 0, tv: tv),
            details: name.map { ["season_title": .string($0)] },
            episodes: episodes
        )
    }

    /// The card's line under the show: "S2 E5 next · 4 of 10".
    static func line(_ item: Spine.LibraryItem) -> String? {
        item.tracking.tv.flatMap { TVTrackingPresentation.continueWatchingLine(for: $0) }
    }

    /// A tick's answer: the show's new state (out of 10 aired), and the ticked episode naming the viewing it recorded.
    static func tickResponse(
        upNext: (Int, Int)?,
        watched: Int,
        ticked: (Int, Int),
        viewingId: Int,
        changed: Bool = true,
        finished: TVFinished? = nil,
        earlierUnwatched: TVEarlierUnwatched? = nil
    ) -> TVActionResponse {
        let tv = showState(status: "In progress", watched: watched, aired: 10, upNext: upNext)
        return TVActionResponse(
            tracking: trackingState(status: "In progress", tv: tv),
            envelope: TVWriteEnvelope(
                tv: tv,
                episode: TVEpisodeState(seasonNumber: ticked.0, episodeNumber: ticked.1, watched: true, viewingCount: 1,
                                        untickViewingId: viewingId),
                changed: changed,
                finished: finished,
                earlierUnwatched: earlierUnwatched
            )
        )
    }

    static func finished(season: Int) -> TVFinished {
        TVFinished(seasonNumber: season, composer: TVComposer(defaultTarget: "season:\(season)", targets: [
            TVComposerTarget(id: "season:\(season)", level: .season, seasonNumber: season, title: "Season \(season)",
                             date: "2026-10-26"),
        ]))
    }

    static func profile(enabled: [String], quickWatchDate: String? = nil) -> UserProfile {
        UserProfile(
            id: 1, username: "armaan", displayName: "Armaan", email: nil, bio: nil, pronouns: nil, location: nil,
            avatarUrl: nil, profileBackdropUrl: nil, profileBackdropItem: nil, isPrivate: false,
            viewerRelationship: ViewerRelationship(following: false, followedBy: false, requested: false, blocked: false),
            counts: ProfileCounts(followers: 0, following: 0, diaryEntries: 0, lists: 0),
            hof: [:],
            preferences: UserPreferences(enabledMediaTypes: enabled, dateFormat: nil, timeFormat: nil, weekStartDay: nil,
                                         quickWatchDate: quickWatchDate, releaseNotificationsEnabled: false,
                                         dailyDigestEnabled: false)
        )
    }

    static func entry(
        mediaType: String,
        title: String,
        showTitle: String? = nil,
        season: Int? = nil,
        episode: Int? = nil,
        episodeTitle: String? = nil,
        consumedAt: String = "2026-10-25T00:00:00Z",
        tvLog: String? = nil
    ) throws -> DiaryEntry {
        func json(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        func json(_ value: Int?) -> String { value.map(String.init) ?? "null" }
        let body = """
        {"id":31,"user":{"id":1,"username":"armaan","display_name":"Armaan","avatar_url":null},
         "media":{"ref":{"item_id":null,"source":"tmdb","media_type":"\(mediaType)","media_id":"1396",
                         "season_number":\(json(season)),"episode_number":\(json(episode))},
                  "title":"\(title)","image_url":null,"show_title":\(json(showTitle)),"episode_title":\(json(episodeTitle))},
         "consumed_at":"\(consumedAt)","rating":null,"review_title":null,"review":"","contains_spoilers":false,
         "liked":false,"is_rewatch":false,"is_true_reread":null,"book_journey_id":null,"tags":[],"visibility":"public",
         "like_count":0,"viewer_has_liked":false,"created_at":null,"updated_at":null,"tv_log":\(tvLog ?? "null")}
        """
        return try JSONDecoder.api.decode(DiaryEntry.self, from: Data(body.utf8))
    }

    static func object<T: Encodable>(_ value: T) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(value)) as? [String: Any])
    }
}

// MARK: Test doubles

@MainActor
final class SurfacesTrackingRepository: TrackingRepository {
    struct ListRequest: Equatable {
        let mediaType: String
        let status: String?
    }

    var listRequests: [ListRequest] = []
    var listResponses: [String: PagedResponse<Spine.LibraryItem>] = [:]
    var continueWatchingResponse: PagedResponse<Spine.LibraryItem>?
    var continueWatchingPages: [String?] = []
    var onContinueWatching: (() -> Void)?
    var actions: [(source: String, mediaId: String, action: TVAction, request: TVActionRequest)] = []
    var actionResponses: [TVActionResponse] = []
    var actionError: Error?
    var onAction: (() -> Void)?
    /// Holds each action's answer until it opens.
    var actionGate: RefreshGate?
    var detailRequests: [MediaRef] = []
    var detailResult: Result<TrackingState, Error> = .failure(APIError.httpStatus(404, nil))

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listRequests.append(ListRequest(mediaType: mediaType, status: status))
        return listResponses[mediaType] ?? PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func continueWatching(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        continueWatchingPages.append(page)
        onContinueWatching?()
        return continueWatchingResponse ?? PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        actions.append((source, mediaId, action, request))
        onAction?()
        await actionGate?.wait()
        if let actionError { throw actionError }
        return actionResponses.isEmpty ? TVActionResponse(tracking: nil, envelope: .empty) : actionResponses.removeFirst()
    }

    func detail(ref: MediaRef) async throws -> TrackingState {
        detailRequests.append(ref)
        return try detailResult.get()
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

final class SurfacesDiaryRepository: DiaryRepository {
    struct DeleteCall {
        let id: Int
        let expectedVersion: Int?
        let mutationId: UUID?
    }

    var pageFilters: [MediaFilterState] = []
    var detailResults: [DiaryEntry] = []
    var creates: [DiaryEntryWriteRequest] = []
    var tvCreates: [DiaryEntryWriteRequest] = []
    var createTVEnvelope = TVWriteEnvelope.empty
    var deletes: [DeleteCall] = []
    var plainDeletes: [Int] = []
    var deleteErrors: [Error] = []

    func page(filter: MediaFilterState, page: String?) async throws -> PagedResponse<DiaryEntry> {
        pageFilters.append(filter)
        return PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func detail(id: Int) async throws -> DiaryEntry {
        guard !detailResults.isEmpty else { fatalError("No scripted entry") }
        return detailResults.count > 1 ? detailResults.removeFirst() : detailResults[0]
    }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry {
        creates.append(request)
        return try TVTrackingSurfacesTests.entry(mediaType: request.ref.mediaType, title: "Logged")
    }

    func createTV(_ request: DiaryEntryWriteRequest) async throws -> (DiaryEntry, TVWriteEnvelope) {
        tvCreates.append(request)
        return (try TVTrackingSurfacesTests.entry(mediaType: request.ref.mediaType, title: "Logged"), createTVEnvelope)
    }

    func delete(id: Int) async throws {
        plainDeletes.append(id)
    }

    func delete(id: Int, expectedVersion: Int?, mutationId: UUID?) async throws {
        deletes.append(DeleteCall(id: id, expectedVersion: expectedVersion, mutationId: mutationId))
        if !deleteErrors.isEmpty { throw deleteErrors.removeFirst() }
    }

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { [] }
}

final class SurfacesMediaRepository: MediaRepository {
    var detailRequests: [MediaRef] = []
    /// Pages by ref id, as a season page loads them; any other ref answers a bare detail.
    var details: [String: MediaDetail] = [:]
    /// Holds detail loads until it opens.
    var detailGate: RefreshGate?
    /// Fails every detail load while set.
    var detailError: Error?

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailRequests.append(ref)
        await detailGate?.wait()
        if let detailError { throw detailError }
        return details[ref.id] ?? MediaDetail(ref: ref, title: ref.seasonNumber.map { "Season \($0)" } ?? "Breaking Bad")
    }

    func reviewPage(ref: MediaRef, page: String?) async throws -> PagedResponse<MediaReview> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func meta() async throws -> MetaResponse { throw URLError(.notConnectedToInternet) }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

final class SurfacesProfileRepository: ProfileRepository {
    let profile: UserProfile
    var preferenceRequests: [PreferencesUpdateRequest] = []

    init(profile: UserProfile) {
        self.profile = profile
    }

    func me() async throws -> UserProfile { profile }

    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences {
        preferenceRequests.append(request)
        return UserPreferences(
            enabledMediaTypes: request.enabledMediaTypes ?? [],
            dateFormat: request.dateFormat,
            timeFormat: request.timeFormat,
            weekStartDay: request.weekStartDay,
            quickWatchDate: request.quickWatchDate,
            releaseNotificationsEnabled: request.releaseNotificationsEnabled ?? false,
            dailyDigestEnabled: request.dailyDigestEnabled ?? false
        )
    }

    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] { fatalError("Not used") }
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] { fatalError("Not used") }
}

struct SurfacesActivityRepository: ActivityRepository {
    func userActivity(username: String, limit: Int) async throws -> [ActivityItem] { [] }
}

final class CountingActivityRepository: ActivityRepository {
    private(set) var requestCount = 0
    var results: [ActivityItem] = []

    func userActivity(username: String, limit: Int) async throws -> [ActivityItem] {
        requestCount += 1
        return results
    }
}

struct SurfacesFilterOptionsRepository: FilterOptionsRepository {
    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse { .empty }
}

final class CountingFilterOptionsRepository: FilterOptionsRepository {
    private(set) var requestCount = 0

    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse {
        requestCount += 1
        return .empty
    }
}
