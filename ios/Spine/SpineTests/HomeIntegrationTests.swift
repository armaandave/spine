import XCTest
@testable import Spine

/// Home's view model with the pieces of the redesign wired together: the feed's cards rebuilt when the feed, a
/// Planning day or a count changes, paging by item id, what Home asks for and when (today's rows, Trending's own
/// activity), the tiles surviving a rail reload, and the tiles' writes. Fixtures are the ones the card tests use.
@MainActor
final class HomeIntegrationTests: XCTestCase {
    private var activity: HomeRecordingActivityRepository!
    private var tracking: HomeRecordingTrackingRepository!
    private var media: SurfacesMediaRepository!
    private var unauthorized = 0

    override func setUp() async throws {
        try await super.setUp()
        activity = HomeRecordingActivityRepository()
        tracking = HomeRecordingTrackingRepository()
        media = SurfacesMediaRepository()
        unauthorized = 0
    }

    // MARK: Fixtures

    private func profile(following: Int, enabled: [String]) -> UserProfile {
        let base = TVTrackingSurfacesTests.profile(enabled: enabled)
        return UserProfile(
            id: base.id, username: base.username, displayName: base.displayName, email: nil, bio: nil, pronouns: nil,
            location: nil, avatarUrl: nil, profileBackdropUrl: nil, profileBackdropItem: nil, isPrivate: false,
            viewerRelationship: base.viewerRelationship,
            counts: ProfileCounts(followers: 0, following: following, diaryEntries: 0, lists: 0),
            hof: [:], preferences: base.preferences
        )
    }

    private func makeHome(extras: Bool = false, following: Int = 2, enabled: [String] = ["book"]) -> HomeViewModel {
        HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: profile(following: following, enabled: enabled)),
            trackingRepository: tracking,
            activityRepository: activity,
            mediaRepository: media,
            diaryRepository: SurfacesDiaryRepository(),
            loadsTileExtras: extras,
            onUnauthorized: { [unowned self] in unauthorized += 1 }
        )
    }

    private var today: String { CalendarDateCodec.string(from: Date()) }

    /// A movie log with a poster, dated today. Logs with the same `mediaID` are the same title.
    private func log(_ id: Int, title: String = "Perf Movie", mediaID: String = "1", user: String = "perf-ava",
                     actor: Int = 3, rating: String? = "4") throws -> ActivityItem {
        try FeedTestItems.item(
            id: id,
            objectID: id * 10,
            objectFields: FeedTestItems.join([rating.map { "\"rating\": \"\($0)\"" } ?? "", "\"liked\": false", "\"is_rewatch\": false"]),
            actorID: actor,
            username: user,
            mediaType: "movie",
            mediaID: mediaID,
            title: title,
            mediaFields: "\"poster_url\": \"https://p/\(mediaID).jpg\"",
            consumedAt: today
        )
    }

    private func page(_ items: [ActivityItem], next: String? = nil) -> ActivityCursorResponse {
        ActivityCursorResponse(nextCursor: next, previousCursor: nil, results: items)
    }

    private func cards(_ entries: [FeedListEntry]) -> [FeedCardModel] {
        entries.compactMap { if case let .card(model) = $0 { model } else { nil } }
    }

    private func book(_ id: String, pages: Int) -> Spine.LibraryItem {
        InProgressUpdatesTests.item(
            "book", id, "Perf Book \(id)", tracking: InProgressUpdatesTests.bookTracking(status: "In progress", pages: pages)
        )
    }

    private func waitUntil(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0 ..< 400 where !condition() {
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertTrue(condition(), "Timed out waiting", file: file, line: line)
    }

    // MARK: What asks for Planning

    func testOnlyHomesFeedAsksForPlanningRows() async throws {
        PlanningIncludeProtocol.requests = []
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [PlanningIncludeProtocol.self]
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: config)
        )
        client.tokenProvider.accessToken = "access"
        let repository = APIActivityRepository(client: client)

        _ = try await repository.feedPage(pageSize: 25, cursorLink: nil)
        _ = try await repository.userActivityPage(username: "armaan", pageSize: 25, cursorLink: nil)
        _ = try await repository.userActivityList(
            username: "armaan",
            filter: ActivityListFilter(startDate: "2026-11-07", endDate: "2026-11-07"),
            pageSize: 50,
            cursorLink: nil
        )

        let includes = PlanningIncludeProtocol.requests.map { request -> (path: String, include: [String?]) in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            return (request.url!.path, query.filter { $0.name == "include" }.map(\.value))
        }
        XCTAssertEqual(includes.count, 3)
        XCTAssertTrue(includes[0].path.contains("/feed"))
        XCTAssertEqual(includes[0].include, ["planning"], "Home's feed asks")
        XCTAssertEqual(includes[1].include, [], "A profile's rail does not")
        XCTAssertEqual(includes[2].include, [], "Today's rows do not")
    }

    // MARK: The entries

    func testEntriesRebuildWhenALikeComesBackAndOnlyThatCardChanges() async throws {
        activity.feedPages[nil] = page([try log(1, title: "One", mediaID: "a"), try log(2, title: "Two", mediaID: "b")])
        let home = makeHome()
        await home.load()

        let before = cards(home.feedModel.entries)
        XCTAssertEqual(before.map(\.id), [1, 2])
        XCTAssertEqual(home.feedModel.entries.count, 3, "A day header, then two cards")

        let post = try XCTUnwrap(home.feed.items[0].post)
        home.feed.apply(PostEngagement(hasLiked: true, likeCount: 9, commentCount: 4), to: post)

        let after = cards(home.feedModel.entries)
        XCTAssertEqual(after[0].engagement, PostEngagement(hasLiked: true, likeCount: 9, commentCount: 4))
        XCTAssertEqual(after[1], before[1], "The other card is equal, so its row skips drawing")
        XCTAssertEqual(home.feedModel.entries.first?.id, "day-\(today)", "The header kept its id")
    }

    func testEntriesAreRebuiltFromAReloadAndKeepTheirIds() async throws {
        activity.feedPages[nil] = page([try log(1), try log(2, title: "Two", mediaID: "b")])
        let home = makeHome()
        await home.load()
        let first = home.feedModel.entries.map(\.id)

        activity.feedPages[nil] = page([try log(3, title: "Three", mediaID: "c"), try log(1), try log(2, title: "Two", mediaID: "b")])
        await home.reload()

        let second = home.feedModel.entries.map(\.id)
        XCTAssertEqual(first, ["day-\(today)", "card-1", "card-2"])
        XCTAssertEqual(second, ["day-\(today)", "card-3", "card-1", "card-2"], "A newer card is added without renumbering")
    }

    func testAPlanningDayFansItsPostersOnceItsTitlesArrive() async throws {
        let row = try FeedTestItems.planningRow(count: 2)
        activity.feedPages[nil] = page([row])
        activity.planningDays[row.object.id] = try FeedTestItems.planningDay(
            titles: [("A", "https://p/a.jpg"), ("B", "https://p/b.jpg")], count: 2
        )
        let home = makeHome()
        await home.load()
        let card = try XCTUnwrap(cards(home.feedModel.entries).first)
        XCTAssertEqual(card.kind, .planning)
        XCTAssertTrue(card.art.planningPosterURLs.isEmpty, "Only the day's face until its titles load")
        XCTAssertNil(card.engagement, "Planning has no likes or comments")
        XCTAssertTrue(activity.planningRequests.isEmpty, "Nothing is asked until a Planning card appears")

        home.feedModel.cardAppeared(card)
        await waitUntil { !self.cards(home.feedModel.entries).first!.art.planningPosterURLs.isEmpty }

        XCTAssertEqual(activity.planningRequests, [row.object.id])
        XCTAssertEqual(cards(home.feedModel.entries).first?.art.planningPosterURLs.count, 2)
        // A card that is not Planning asks for nothing.
        home.feedModel.cardAppeared(FeedCardPresenter.model(for: try log(5), standing: nil, isNewestOfTitle: false, planning: nil))
        XCTAssertEqual(activity.planningRequests, [row.object.id])
    }

    // MARK: Paging

    func testThePageAfterTheLastCardLoadsByItemIdAndFriendsStandingsAreAskedFor() async throws {
        let cursor = "https://example.com/api/v1/feed/?cursor=p2"
        activity.feedPages[nil] = page([try log(1), try log(2, title: "Two", mediaID: "b")], next: cursor)
        activity.feedPages[cursor] = page([try log(3, title: "Three", mediaID: "c")])
        let standing = PreparingStanding()
        let home = makeHome()
        home.standing = standing
        await home.load()
        XCTAssertEqual(standing.prepared, [[1, 2]], "Standings start after the first page")

        await home.feed.loadNextPageIfNeeded(itemID: 1)
        XCTAssertEqual(activity.feedRequests.count, 1, "Only the last item's row asks for the next page")

        await home.feedModel.loadNextPageIfNeeded(cardID: 2)
        XCTAssertEqual(activity.feedRequests.map(\.cursor), [nil, cursor])
        XCTAssertEqual(activity.feedRequests.map(\.pageSize), [25, 25])
        XCTAssertEqual(cards(home.feedModel.entries).map(\.id), [1, 2, 3])
        XCTAssertEqual(standing.prepared, [[1, 2], [1, 2, 3]], "The new rows' standings start too")

        await home.feedModel.loadNextPageIfNeeded(cardID: 3)
        XCTAssertEqual(activity.feedRequests.count, 2, "No cursor, no request")
    }

    // MARK: Today's rows

    func testTodayIsAskedForAfterTheRailLoadedAlongsideTheFeedAndLightsTheTiles() async throws {
        let item = book("b1", pages: 212)
        tracking.items["book"] = [item]
        activity.feedPages[nil] = page([try log(1)])
        activity.todayRows = [
            try FeedTestItems.day(id: 50, label: "86 pages", unit: "pages", mediaType: "book", source: "hardcover", mediaID: "b1"),
        ]
        activity.watchedRail = { [unowned self] in tracking.listCalls }
        let home = makeHome(extras: true)

        await home.load()
        await home.todayRefresh?.value

        XCTAssertEqual(activity.listRequests.count, 1)
        let request = try XCTUnwrap(activity.listRequests.first)
        XCTAssertEqual(request.username, "armaan")
        XCTAssertEqual(request.filter, ActivityListFilter(startDate: today, endDate: today))
        XCTAssertEqual(request.pageSize, 50)
        XCTAssertEqual(request.railLoadsBefore, 1, "The rail had loaded when today was asked for")
        XCTAssertEqual(
            home.tiles.todayParts[HomeTodayStore.titleKey(for: item.media.ref)],
            TodayPart(unit: .pages, amount: 86)
        )
    }

    func testTodayIsNotAskedForWithoutTheExtras() async throws {
        tracking.items["book"] = [book("b1", pages: 212)]
        activity.feedPages[nil] = page([try log(1)])
        let home = makeHome(extras: false)
        await home.load()
        XCTAssertNil(home.todayRefresh)
        XCTAssertTrue(activity.listRequests.isEmpty)
        XCTAssertTrue(activity.pageRequests.isEmpty)
    }

    func testTodayIsThrottledUnlessForcedAndSkipsHomesOwnEcho() async throws {
        tracking.items["book"] = [book("b1", pages: 212)]
        let home = makeHome(extras: true)
        await home.load()
        await home.todayRefresh?.value
        XCTAssertEqual(activity.listRequests.count, 1)
        XCTAssertEqual(tracking.listCalls, 1)

        // The rail loads again within twenty seconds: today is not asked for again.
        await home.loadInProgress()
        await home.todayRefresh?.value
        XCTAssertEqual(tracking.listCalls, 2)
        XCTAssertEqual(activity.listRequests.count, 1)

        // Coming back after a write forces it.
        await home.loadInProgress(forceToday: true)
        await home.todayRefresh?.value
        XCTAssertEqual(activity.listRequests.count, 2)

        // Home's own write is not a reason to read either again.
        await home.mediaStateDidChange(origin: home.continueWatching.changeOrigin)
        await home.todayRefresh?.value
        XCTAssertEqual(tracking.listCalls, 3, "The rail did not reload")
        XCTAssertEqual(activity.listRequests.count, 2, "And today was not asked for")

        // Another screen's write reloads the rail and reads today at once.
        await home.mediaStateDidChange(origin: UUID())
        await home.todayRefresh?.value
        XCTAssertEqual(tracking.listCalls, 4)
        XCTAssertEqual(activity.listRequests.count, 3)
    }

    func testBaselinesAreTakenWhenTheRailLoadsAndTheGainSinceThenIsLit() async throws {
        tracking.items["book"] = [book("b1", pages: 212)]
        tracking.bookResult = InProgressUpdatesTests.bookTracking(status: "In progress", pages: 232)
        let home = makeHome()
        await home.load()
        let item = try XCTUnwrap(home.tiles.inProgress.first)
        XCTAssertEqual(home.tiles.baselines[item.id], 212)
        XCTAssertNil(home.tiles.detail(for: item).todayText)

        let outcome = await home.runQuickAction(item)

        XCTAssertEqual(outcome, .saved)
        let patched = try XCTUnwrap(home.tiles.inProgress.first)
        XCTAssertEqual(home.tiles.baselines[item.id], 212, "The baseline stays where the rail loaded")
        XCTAssertEqual(home.tiles.detail(for: patched).todayText, "+20 pages")
    }

    // MARK: Tiles and the rail

    func testTilesStateSurvivesARailReloadAndMovesToANeighborWhenItsTitleLeaves() async throws {
        let items = [book("b1", pages: 10), book("b2", pages: 20), book("b3", pages: 30)]
        tracking.items["book"] = items
        let home = makeHome()
        await home.load()
        let ids = home.tiles.ids(for: .inProgress)
        XCTAssertEqual(Set(ids), Set(items.map(\.id)))

        let state = home.tilesState
        state.sync(ids, for: .inProgress)
        state.openTile(.inProgress)
        state.focus(ids[1])

        await home.loadInProgress()
        state.sync(home.tiles.ids(for: .inProgress), for: .inProgress)
        XCTAssertEqual(state.open, .inProgress, "A reload under an open tile leaves it open")
        XCTAssertEqual(state.focusID, ids[1])

        tracking.items["book"] = items.filter { $0.id != ids[1] }
        await home.loadInProgress()
        state.sync(home.tiles.ids(for: .inProgress), for: .inProgress)
        XCTAssertEqual(state.open, .inProgress)
        XCTAssertEqual(state.focusID, ids[2], "The tile follows to the title after the one that left")
    }

    func testTheRailPassThroughKeepsOldCallersWorking() async {
        let home = makeHome()
        let item = book("b1", pages: 10)
        home.inProgressItems = [item]
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [item.id])
        home.isLoadingInProgress = true
        XCTAssertTrue(home.tiles.isLoadingInProgress)
        home.inProgressErrorMessage = "Down"
        XCTAssertEqual(home.tiles.inProgressError, "Down")
    }

    func testTheGlowRestsOnTheFirstTitleInProgressElseTheNewestPost() async throws {
        activity.feedPages[nil] = page([try log(1)])
        let home = makeHome()
        await home.load()
        XCTAssertEqual(home.glowModel.idle?.title, "Perf Movie", "No rail: the newest post's art")

        let item = book("b1", pages: 10)
        home.inProgressItems = [item]
        XCTAssertEqual(home.glowModel.idle, item.media)

        home.inProgressItems = []
        XCTAssertEqual(home.glowModel.idle?.title, "Perf Movie")
    }

    func testTheFollowCountDecidesWhetherEmptyTrendingOffersToFindPeople() async {
        let none = makeHome(following: 0)
        await none.load()
        XCTAssertTrue(none.tiles.followsNobody)

        let some = makeHome(following: 3)
        await some.load()
        XCTAssertFalse(some.tiles.followsNobody)
    }

    // MARK: Trending

    func testTrendingIsBuiltFromTheFeedAndOwnActivityAfterTheFirstPage() async throws {
        activity.feedPages[nil] = page([
            try log(1, title: "Shared", mediaID: "s", user: "perf-ava", actor: 3),
            try log(2, title: "Shared", mediaID: "s", user: "perf-ben", actor: 4),
            try log(3, title: "Solo", mediaID: "t", user: "perf-ava", actor: 3),
        ])
        activity.ownRows = [try log(4, title: "Mine", mediaID: "m", user: "armaan", actor: 1)]
        let home = makeHome(extras: true)
        XCTAssertTrue(home.tiles.isLoadingTrending, "Trending waits for the feed")

        await home.load()
        await home.trendingFill?.value

        XCTAssertEqual(
            activity.events.filter { $0 != "today" }, ["feed", "own"],
            "Your own activity is asked for after the feed's first page"
        )
        XCTAssertEqual(activity.pageRequests, [HomeRecordingActivityRepository.PageRequest(username: "armaan", pageSize: 25)])
        XCTAssertEqual(home.tiles.trending.first?.media.title, "Shared", "Two people first")
        XCTAssertEqual(Set(home.tiles.trending.map(\.media.title)), ["Shared", "Solo", "Mine"])
        XCTAssertEqual(home.tiles.trending.first?.people.count, 2)
        XCTAssertFalse(home.tiles.isLoadingTrending)
        XCTAssertEqual(home.tiles.trendingSubtitle, "This week")
    }

    func testTrendingFollowsTheFeedWhenItChangesAndNeverAsksAgain() async throws {
        let cursor = "https://example.com/api/v1/feed/?cursor=p2"
        activity.feedPages[nil] = page([try log(1, title: "One", mediaID: "a")], next: cursor)
        activity.feedPages[cursor] = page([try log(2, title: "Two", mediaID: "b")])
        let home = makeHome()
        await home.load()
        XCTAssertEqual(home.tiles.trending.map(\.media.title), ["One"])
        XCTAssertFalse(home.tiles.isLoadingTrending)

        await home.feedModel.loadNextPageIfNeeded(cardID: 1)

        XCTAssertEqual(Set(home.tiles.trending.map(\.media.title)), ["One", "Two"])
        XCTAssertTrue(activity.pageRequests.isEmpty, "Nothing beyond the feed without the extras")
    }

    func testPlanToWatchShowsPlannedAtOnceAndGoesBackWhenTheWriteFails() async throws {
        let media = MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil),
            title: "Fight Club", posterUrl: "https://p/fc.jpg", posterOrientation: .portrait
        )
        let title = TrendingTitle(key: "tmdb:movie:550", media: media, people: [], includesYou: false, averageRating: nil, lastDay: nil)
        let home = makeHome()

        let write = home.planToWatch(title)
        XCTAssertTrue(home.tiles.plannedIDs.contains(title.key), "Planned the moment it is tapped")
        XCTAssertTrue(home.tiles.busyItemIDs.contains(title.key))
        await write.value
        XCTAssertEqual(tracking.updateCalls.map(\.status), ["Planning"])
        XCTAssertTrue(home.tiles.plannedIDs.contains(title.key))
        XCTAssertFalse(home.tiles.busyItemIDs.contains(title.key))
        XCTAssertNil(home.writeFailure)

        // The same title again writes nothing.
        await home.planToWatch(title).value
        XCTAssertEqual(tracking.updateCalls.count, 1)

        let other = TrendingTitle(
            key: "tmdb:movie:551", media: media, people: [], includesYou: false, averageRating: nil, lastDay: nil
        )
        let failure = APIError.httpStatus(500, nil)
        tracking.updateError = failure
        await home.planToWatch(other).value
        XCTAssertFalse(home.tiles.plannedIDs.contains(other.key), "A failed write takes the check back")
        XCTAssertEqual(home.writeFailure, failure.localizedDescription)
    }

    // MARK: Quick actions

    func testAQuickActionPatchesItsTileWhereItIsAndMarksTheRailForTheNextVisit() async throws {
        let item = book("b1", pages: 212)
        let other = book("b2", pages: 10)
        tracking.bookResult = InProgressUpdatesTests.bookTracking(status: "In progress", pages: 232)
        let home = makeHome()
        home.inProgressItems = [item, other]
        XCTAssertFalse(home.inProgressNeedsReload)

        let outcome = await home.runQuickAction(item)

        XCTAssertEqual(outcome, .saved)
        XCTAssertEqual(tracking.bookCalls, [232], "An absolute value, worked out from the card")
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [item.id, other.id], "Nothing moved")
        XCTAssertEqual(home.tiles.inProgress[0].tracking.progress?.value, 232)
        XCTAssertTrue(home.inProgressNeedsReload, "The rail re-sorts when Home is next shown")
        XCTAssertTrue(home.tiles.busyItemIDs.isEmpty)
    }

    func testAQuickActionThatWouldReachTheEndOpensTheUpdateUIInstead() async {
        let nearly = book("b1", pages: 470)
        let home = makeHome()
        home.inProgressItems = [nearly]

        let outcome = await home.runQuickAction(nearly)

        XCTAssertEqual(outcome, .opensUpdate)
        XCTAssertTrue(tracking.bookCalls.isEmpty, "Finishing is the sheet's to do")
    }

    func testAFailedQuickActionLeavesTheCardAndSaysWhy() async {
        let item = book("b1", pages: 212)
        tracking.bookResult = nil
        let home = makeHome()
        home.inProgressItems = [item]

        let outcome = await home.runQuickAction(item)

        guard case .failed = outcome else { return XCTFail("Expected a failure, got \(outcome)") }
        XCTAssertNotNil(home.writeFailure)
        XCTAssertEqual(home.tiles.inProgress[0].tracking.progress?.value, 212)
        XCTAssertFalse(home.inProgressNeedsReload)
        XCTAssertTrue(home.tiles.busyItemIDs.isEmpty)
    }

    func testAShowOrAnimeSheetOpensOnHowManyEpisodesWereWatchedToday() async throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 4)
        let home = makeHome()
        home.inProgressItems = [crown]
        home.tiles.baselines = HomeViewModel.baselines(for: [crown])
        XCTAssertEqual(home.tiles.baselines[crown.id], 4)
        XCTAssertEqual(home.todayCount(for: crown), 0)

        home.tiles.todayParts = [HomeTodayStore.titleKey(for: crown.media.ref): TodayPart(unit: .episodes, amount: 2)]
        XCTAssertEqual(home.todayCount(for: crown), 2, "What the server counted today")

        // A tick made since the rail loaded counts too.
        let ticked = TVTrackingSurfacesTests.crown(watched: 5)
        home.continueWatching.onUpdate(crown.id, try XCTUnwrap(ticked.tracking.tv))
        XCTAssertEqual(home.todayCount(for: try XCTUnwrap(home.tiles.inProgress.first)), 3)

        // A book has no episodes.
        let pages = book("b1", pages: 10)
        XCTAssertNil(home.todayCount(for: pages))
    }

    // MARK: Seasons

    private func seasonTracking(_ number: Int, watched: Int, episodes: Int) -> TrackingState {
        var tv = TVTrackingSurfacesTests.showState(status: "In progress", watched: 14, aired: 20, upNext: (number, watched + 1))
        tv.season = TVSeasonState(seasonNumber: number, state: .inProgress, watchedCount: watched, airedCount: episodes,
                                  episodeCount: episodes)
        return TrackingState(trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil, startDate: nil,
                             endDate: nil, notes: nil, updatedAt: nil, tv: tv)
    }

    func testAShowsPanelCountsItsSeasonOnceTheSeasonIsReadAndReadsItOncePerVersion() async throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 14)
        tracking.seasonStates["1396|2"] = seasonTracking(2, watched: 4, episodes: 10)
        let home = makeHome(extras: true, enabled: ["tv"])

        home.inProgressItems = [crown]
        XCTAssertEqual(home.tiles.detail(for: crown).whereText, "Season 2", "Until the read lands")
        await home.seasonRefresh?.value
        XCTAssertEqual(tracking.detailCalls, [TVTrackingSurfacesTests.seasonRef(2)])
        XCTAssertEqual(home.tiles.detail(for: crown).whereText, "S2 \u{00B7} 4 of 10 episodes")
        XCTAssertEqual(home.tiles.detail(for: crown).meter?.kind, .ticks(count: 10))

        // The same show at the same version is not read again.
        home.inProgressItems = [crown]
        await home.seasonRefresh?.value
        XCTAssertEqual(tracking.detailCalls.count, 1)

        // A tick on this phone moves it on at once, and its new version is read.
        let ticked = TVTrackingSurfacesTests.crown(watched: 15, upNext: (2, 6))
        var tv = try XCTUnwrap(ticked.tracking.tv)
        tv.version += 1
        tracking.seasonStates["1396|2"] = seasonTracking(2, watched: 5, episodes: 10)
        home.continueWatching.onUpdate(crown.id, tv)
        let moved = try XCTUnwrap(home.tiles.inProgress.first)
        XCTAssertEqual(home.tiles.detail(for: moved).whereText, "S2 \u{00B7} 5 of 10 episodes")
        await home.seasonRefresh?.value
        XCTAssertEqual(tracking.detailCalls.count, 2)
        XCTAssertEqual(home.tiles.detail(for: moved).whereText, "S2 \u{00B7} 5 of 10 episodes")
    }

    func testASeasonReadThatFailedIsTriedAgainAndNoneAreMadeWithoutTheExtras() async throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 14)
        let home = makeHome(extras: true, enabled: ["tv"])
        home.inProgressItems = [crown]
        await home.seasonRefresh?.value
        XCTAssertEqual(home.tiles.detail(for: crown).whereText, "Season 2")

        tracking.seasonStates["1396|2"] = seasonTracking(2, watched: 4, episodes: 10)
        home.inProgressItems = [crown]
        await home.seasonRefresh?.value
        XCTAssertEqual(tracking.detailCalls.count, 2)
        XCTAssertEqual(home.tiles.detail(for: crown).whereText, "S2 \u{00B7} 4 of 10 episodes")

        let plain = makeHome(extras: false, enabled: ["tv"])
        plain.inProgressItems = [crown]
        XCTAssertNil(plain.seasonRefresh)
        XCTAssertEqual(tracking.detailCalls.count, 2)
    }

    func testASeasonWithNothingWatchedYetIsCountedFromItsPage() async throws {
        // Season 2 just finished: up next is S3 E1, and season 3 has no tracking yet (the server answers 404).
        let finished = TVTrackingSurfacesTests.crown(watched: 20, upNext: (3, 1))
        media.details[TVTrackingSurfacesTests.seasonRef(3).id] = TVTrackingSurfacesTests.crownSeason(3, watched: 0)
        let home = makeHome(extras: true, enabled: ["tv"])

        home.inProgressItems = [finished]
        await home.seasonRefresh?.value
        XCTAssertEqual(tracking.detailCalls, [TVTrackingSurfacesTests.seasonRef(3)])
        XCTAssertEqual(media.detailRequests, [TVTrackingSurfacesTests.seasonRef(3)])
        let detail = home.tiles.detail(for: finished)
        XCTAssertEqual(detail.whereText, "S3 \u{00B7} 0 of 10 episodes")
        XCTAssertEqual(detail.meter?.kind, .ticks(count: 10))
        XCTAssertEqual(detail.meter?.to, 0)

        // Read once: the page answered, so a reload asks for nothing more.
        home.inProgressItems = [finished]
        await home.seasonRefresh?.value
        XCTAssertEqual(tracking.detailCalls.count, 1)
        XCTAssertEqual(media.detailRequests.count, 1)
    }

    func testAShowWithNoCountOfItsOwnIsNotRead() async throws {
        let unknown = TVTrackingSurfacesTests.showItem(
            "77", "Unknown",
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: nil, aired: nil, upNext: (1, 2))
        )
        let home = makeHome(extras: true, enabled: ["tv"])
        home.inProgressItems = [unknown]
        XCTAssertNil(home.seasonRefresh)
        XCTAssertTrue(tracking.detailCalls.isEmpty)
    }

    // MARK: The page top's states

    func testThePageTopShowsFailureLoadingOrContent() {
        XCTAssertEqual(HomeTop.Phase.resolve(hasProfile: false, isLoading: true, error: nil), .loading)
        XCTAssertEqual(HomeTop.Phase.resolve(hasProfile: false, isLoading: false, error: "Offline"), .failed("Offline"))
        XCTAssertEqual(HomeTop.Phase.resolve(hasProfile: true, isLoading: true, error: nil), .content, "A reload keeps the page")
        XCTAssertEqual(HomeTop.Phase.resolve(hasProfile: true, isLoading: false, error: "Offline"), .content)
        XCTAssertEqual(HomeTop.Phase.resolve(hasProfile: false, isLoading: false, error: nil), .content)
    }

    func testTheFeedsStatesStandInOnlyWhileThereAreNoRows() {
        typealias Face = HomeFeedStatus.Face
        XCTAssertEqual(Face.resolve(isLoading: true, isEmpty: false, error: nil, following: 0), .none)
        XCTAssertEqual(Face.resolve(isLoading: false, isEmpty: false, error: "Down", following: 0), .none)
        XCTAssertEqual(Face.resolve(isLoading: true, isEmpty: true, error: nil, following: 5), .loading)
        XCTAssertEqual(Face.resolve(isLoading: false, isEmpty: true, error: "Down", following: 5), .failed("Down"))
        XCTAssertEqual(Face.resolve(isLoading: false, isEmpty: true, error: nil, following: 0), .followNobody)
        XCTAssertEqual(Face.resolve(isLoading: false, isEmpty: true, error: nil, following: 2), .quiet)
    }

    func testHomesPageFailureAndUnauthorizedStillReachTheirHandlers() async {
        let home = HomeViewModel(
            profileRepository: FailingProfileRepository(error: APIError.unauthorized),
            trackingRepository: tracking,
            activityRepository: activity,
            mediaRepository: SurfacesMediaRepository(),
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: { [unowned self] in unauthorized += 1 }
        )
        await home.load()
        XCTAssertNotNil(home.profileErrorMessage)
        XCTAssertNil(home.profile)
        XCTAssertEqual(unauthorized, 1)
        XCTAssertFalse(home.isLoading)
        XCTAssertEqual(HomeTop.Phase.resolve(hasProfile: false, isLoading: home.isLoading, error: home.profileErrorMessage),
                       .failed(home.profileErrorMessage ?? ""))
    }
}

// MARK: - Test doubles

@MainActor
private final class PreparingStanding: FeedStandingProviding {
    var prepared: [[Int]] = []

    func standing(for item: ActivityItem, isNewestOfTitle: Bool) -> FeedStanding? { nil }

    func prepare(for items: [ActivityItem]) {
        prepared.append(items.map(\.id))
    }
}

private struct FailingProfileRepository: ProfileRepository {
    let error: Error

    func me() async throws -> UserProfile { throw error }
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences { fatalError("Not used") }
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] { fatalError("Not used") }
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] { fatalError("Not used") }
}

/// Records what Home asks the activity endpoints for, in order, and answers from what the test set up.
@MainActor
final class HomeRecordingActivityRepository: ActivityRepository {
    struct FeedRequest: Equatable {
        let pageSize: Int
        let cursor: String?
    }

    struct ListRequest: Equatable {
        let username: String
        let filter: ActivityListFilter
        let pageSize: Int
        /// How many times the rail had been read when this was asked for.
        let railLoadsBefore: Int
    }

    struct PageRequest: Equatable {
        let username: String
        let pageSize: Int
    }

    /// Feed pages by the cursor link that asks for them (nil is the first page).
    var feedPages: [String?: ActivityCursorResponse] = [:]
    var todayRows: [ActivityItem] = []
    var ownRows: [ActivityItem] = []
    var planningDays: [Int: PlanningDay] = [:]

    private(set) var feedRequests: [FeedRequest] = []
    private(set) var listRequests: [ListRequest] = []
    private(set) var pageRequests: [PageRequest] = []
    private(set) var planningRequests: [Int] = []
    /// "feed", "today" and "own" in the order they were asked for.
    private(set) var events: [String] = []
    /// How many times the rail has been read so far.
    var watchedRail: () -> Int = { 0 }

    func userActivity(username: String, limit: Int) async throws -> [ActivityItem] {
        pageRequests.append(PageRequest(username: username, pageSize: limit))
        events.append("own")
        return ownRows
    }

    func userActivityPage(username: String, pageSize: Int, cursorLink: String?) async throws -> ActivityCursorResponse {
        pageRequests.append(PageRequest(username: username, pageSize: pageSize))
        events.append("own")
        return ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: ownRows)
    }

    func userActivityList(
        username: String,
        filter: ActivityListFilter,
        pageSize: Int,
        cursorLink: String?
    ) async throws -> ActivityCursorResponse {
        listRequests.append(ListRequest(username: username, filter: filter, pageSize: pageSize, railLoadsBefore: watchedRail()))
        events.append("today")
        return ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: todayRows)
    }

    func feedPage(pageSize: Int, cursorLink: String?) async throws -> ActivityCursorResponse {
        feedRequests.append(FeedRequest(pageSize: pageSize, cursor: cursorLink))
        events.append("feed")
        return feedPages[cursorLink] ?? ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [])
    }

    func planningTitles(id: Int, mediaType: String?) async throws -> PlanningDay {
        planningRequests.append(id)
        guard let day = planningDays[id] else { throw APIError.httpStatus(404, nil) }
        return day
    }
}

/// Answers the rail from what the test set up and records the writes the tiles make.
@MainActor
final class HomeRecordingTrackingRepository: TrackingRepository {
    /// In progress titles by media type.
    var items: [String: [Spine.LibraryItem]] = [:]
    private(set) var listCalls = 0
    var bookResult: TrackingState?
    private(set) var bookCalls: [Decimal] = []
    private(set) var updateCalls: [(ref: MediaRef, status: String?)] = []
    var updateError: Error?

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listCalls += 1
        let results = items[mediaType] ?? []
        return PagedResponse(count: results.count, next: nil, previous: nil, results: results)
    }

    func continueWatching(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    /// A season's tracking by "mediaId|season"; anything else is a 404.
    var seasonStates: [String: TrackingState] = [:]
    private(set) var detailCalls: [MediaRef] = []

    func detail(ref: MediaRef) async throws -> TrackingState {
        detailCalls.append(ref)
        guard let state = seasonStates["\(ref.mediaId)|\(ref.seasonNumber ?? -1)"] else { throw APIError.httpStatus(404, nil) }
        return state
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        updateCalls.append((ref, request.status))
        if let updateError { throw updateError }
        return InProgressUpdatesTests.tracking(status: request.status ?? "Planning")
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        bookCalls.append(value)
        guard let bookResult else { throw APIError.httpStatus(500, nil) }
        return bookResult
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

private final class PlanningIncludeProtocol: URLProtocol {
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"next_cursor": null, "previous_cursor": null, "results": []}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
