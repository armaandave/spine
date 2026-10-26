import XCTest
@testable import Spine

/// Library, Home, Profile and Diary for the show-as-library-unit model (docs/tv-tracking-contract.md).
@MainActor
final class TVTrackingSurfacesTests: XCTestCase {
    private var tracking: SurfacesTrackingRepository!
    private var media: SurfacesMediaRepository!
    private var diary: SurfacesDiaryRepository!

    override func setUp() async throws {
        try await super.setUp()
        tracking = SurfacesTrackingRepository()
        media = SurfacesMediaRepository()
        diary = SurfacesDiaryRepository()
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
            mediaRepository: media,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(tracking.continueWatchingPages, [nil], "Shows come from Continue Watching")
        XCTAssertEqual(tracking.listRequests.map(\.mediaType), ["movie"], "No season rows are requested")
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Heat", "Severance", "The Bear"],
                       "Shows keep the server's order")
        let lines = viewModel.inProgressItems.compactMap(\.tracking.tv).compactMap(TVTrackingPresentation.continueWatchingLine)
        XCTAssertEqual(lines, ["S1 E2 next", "S3 E1 · New"])
    }

    func testTV100HomeTickPostsTickWithAMutationIdAndOpensTheSeasonComposer() async throws {
        let show = Self.showItem("1396", "Breaking Bad", tv: Self.showState(status: "In progress", upNext: (1, 7)))
        tracking.continueWatchingResponse = PagedResponse(count: 1, next: nil, previous: nil, results: [show])
        let viewModel = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            onUnauthorized: {}
        )
        await viewModel.load()

        var pendingDuringTick: Bool?
        tracking.onAction = { pendingDuringTick = viewModel.continueWatching.isPending(show) }
        tracking.actionResponses = [TVActionResponse(
            tracking: Self.trackingState(status: "In progress", tv: Self.showState(status: "In progress", upNext: (2, 1))),
            envelope: TVWriteEnvelope(changed: true, finished: Self.finished(season: 1))
        )]

        await viewModel.tickUpNext(show)

        XCTAssertEqual(pendingDuringTick, true, "The tick shows at once")
        XCTAssertFalse(viewModel.continueWatching.isPending(show))
        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(tracking.actions.count, 1)
        XCTAssertEqual(sent.action, .tick)
        XCTAssertEqual(sent.source, "tmdb")
        XCTAssertEqual(sent.mediaId, "1396")
        XCTAssertEqual(sent.request.seasonNumber, 1)
        XCTAssertEqual(sent.request.episodeNumber, 7)
        XCTAssertNotNil(sent.request.mutationId, "A tick carries a mutation ID (TV-705)")
        XCTAssertNil(sent.request.expectedVersion)
        XCTAssertEqual(tracking.continueWatchingPages.count, 2, "The rail reloads after the tick")

        // TV-403: the season the tick finished opens its composer once, through the shared follow-up.
        let followUp = try XCTUnwrap(viewModel.continueWatching.followUp)
        XCTAssertEqual(followUp.showRef.mediaType, "tv")
        XCTAssertEqual(followUp.showRef.mediaId, "1396")
        await followUp.loading?.value
        let composer = try XCTUnwrap(followUp.composer)
        XCTAssertEqual(composer.finished.seasonNumber, 1)
        XCTAssertEqual(composer.finished.composer.initialTarget?.id, "season:1")
        XCTAssertEqual(media.detailRequests.map(\.mediaType), ["season"])
        XCTAssertEqual(media.detailRequests.map(\.seasonNumber), [1])
    }

    func testTV107HomeTickAsksTheEarlierEpisodesQuestionFirst() async throws {
        let show = Self.showItem("1396", "Breaking Bad", tv: Self.showState(status: "In progress", upNext: (3, 1)))
        let viewModel = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            onUnauthorized: {}
        )
        viewModel.inProgressItems = [show]
        tracking.actionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(
            changed: true,
            finished: Self.finished(season: 3),
            earlierUnwatched: TVEarlierUnwatched(
                from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
                to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 13),
                count: 20,
                seasons: [1, 2],
                question: "Also mark Seasons 1–2 watched?",
                offerSeasonNumber: 3
            )
        ))]

        await viewModel.tickUpNext(show)

        let followUp = try XCTUnwrap(viewModel.continueWatching.followUp)
        XCTAssertEqual(followUp.question?.question, "Also mark Seasons 1–2 watched?")
        XCTAssertNil(followUp.composer, "The question comes before the composer")
        XCTAssertTrue(media.detailRequests.isEmpty)
    }

    func testTV705AFailedTickRestoresTheRow() async {
        let show = Self.showItem("1396", "Breaking Bad", tv: Self.showState(status: "In progress", upNext: (1, 7)))
        let viewModel = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            onUnauthorized: {}
        )
        viewModel.inProgressItems = [show]
        tracking.actionError = APIError.httpStatus(
            503, #"{"error":{"code":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#
        )

        await viewModel.tickUpNext(show)

        XCTAssertFalse(viewModel.continueWatching.isPending(show), "The pending tick goes")
        XCTAssertEqual(viewModel.inProgressItems.first?.tracking.tv?.upNextCode, "S1 E7", "The last confirmed state stays")
        XCTAssertEqual(viewModel.continueWatching.errorMessage, "TV details are unavailable right now")
        XCTAssertNil(viewModel.continueWatching.followUp)
        XCTAssertTrue(tracking.continueWatchingPages.isEmpty, "Nothing reloads after a failure")
    }

    func testTV100OnlyShowsWithAnUpNextCanBeTicked() async {
        let viewModel = HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            onUnauthorized: {}
        )
        await viewModel.tickUpNext(Self.showItem("1", "Caught Up", tv: Self.showState(status: "In progress", caughtUp: true)))
        await viewModel.tickUpNext(Self.item("9", "Heat", mediaType: "movie", updatedAt: nil))
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testTV701ProfileListsShowsAndTicksThemToo() async throws {
        let show = Self.showItem("1396", "Breaking Bad", tv: Self.showState(status: "In progress", upNext: (1, 7)))
        tracking.continueWatchingResponse = PagedResponse(count: 1, next: nil, previous: nil, results: [show])
        let viewModel = ProfileViewModel(
            profileRepository: SurfacesProfileRepository(profile: Self.profile(enabled: ["tv", "season", "episode"])),
            trackingRepository: tracking,
            activityRepository: SurfacesActivityRepository(),
            mediaRepository: media,
            onUnauthorized: {}
        )

        await viewModel.load()
        XCTAssertEqual(tracking.continueWatchingPages, [nil])
        XCTAssertTrue(tracking.listRequests.isEmpty, "No season rows are requested")
        XCTAssertEqual(viewModel.inProgressItems.map(\.media.title), ["Breaking Bad"])

        tracking.actionResponses = [TVActionResponse(
            tracking: Self.trackingState(status: "In progress", tv: Self.showState(status: "In progress", upNext: (1, 8))),
            envelope: TVWriteEnvelope(changed: true)
        )]
        await viewModel.tickUpNext(show)
        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(sent.action, .tick)
        XCTAssertEqual(sent.request.seasonNumber, 1)
        XCTAssertEqual(sent.request.episodeNumber, 7)
        XCTAssertNotNil(sent.request.mutationId)
        XCTAssertEqual(tracking.continueWatchingPages.count, 2)
        XCTAssertNotNil(viewModel.continueWatching.followUp)
        XCTAssertNil(viewModel.continueWatching.followUp?.question)
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
    var actions: [(source: String, mediaId: String, action: TVAction, request: TVActionRequest)] = []
    var actionResponses: [TVActionResponse] = []
    var actionError: Error?
    var onAction: (() -> Void)?
    var detailRequests: [MediaRef] = []
    var detailResult: Result<TrackingState, Error> = .failure(APIError.httpStatus(404, nil))

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listRequests.append(ListRequest(mediaType: mediaType, status: status))
        return listResponses[mediaType] ?? PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func continueWatching(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        continueWatchingPages.append(page)
        return continueWatchingResponse ?? PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        actions.append((source, mediaId, action, request))
        onAction?()
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

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailRequests.append(ref)
        return MediaDetail(ref: ref, title: ref.seasonNumber.map { "Season \($0)" } ?? "Breaking Bad")
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

struct SurfacesFilterOptionsRepository: FilterOptionsRepository {
    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse { .empty }
}
