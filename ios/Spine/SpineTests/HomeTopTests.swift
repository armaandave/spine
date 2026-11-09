import XCTest
@testable import Spine

/// The page top and the rail's state: a title a write finishes leaves In progress at once, the Friends row's people and
/// their order, and the header that squeezes as a tile opens.
@MainActor
final class HomeTopTests: XCTestCase {
    private var activity: HomeRecordingActivityRepository!
    private var tracking: HomeRecordingTrackingRepository!
    private var social: FriendsSocialRepository!

    override func setUp() async throws {
        try await super.setUp()
        activity = HomeRecordingActivityRepository()
        tracking = HomeRecordingTrackingRepository()
        social = FriendsSocialRepository()
    }

    // MARK: Fixtures

    private func makeHome(extras: Bool = false, enabled: [String] = ["book"]) -> HomeViewModel {
        HomeViewModel(
            profileRepository: SurfacesProfileRepository(profile: TVTrackingSurfacesTests.profile(enabled: enabled)),
            trackingRepository: tracking,
            activityRepository: activity,
            mediaRepository: SurfacesMediaRepository(),
            diaryRepository: SurfacesDiaryRepository(),
            socialRepository: social,
            loadsTileExtras: extras,
            onUnauthorized: {}
        )
    }

    private func book(_ id: String, status: String = "In progress", pages: Int = 100) -> Spine.LibraryItem {
        InProgressUpdatesTests.item(
            "book", id, "Book \(id)", tracking: InProgressUpdatesTests.bookTracking(status: status, pages: pages)
        )
    }

    private func anime(_ id: String, status: String = "In progress", caughtUp: Bool = false, finished: Bool = false, upNext: Int? = 2) -> Spine.LibraryItem {
        let state = AnimeState(
            version: 1, inLibrary: true, status: status, started: true, finished: finished, caughtUp: caughtUp,
            upNext: upNext.map { AnimeEpisodePointer(episodeNumber: $0) }
        )
        return Spine.LibraryItem(
            media: MediaSummary(
                ref: MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: id, seasonNumber: nil, episodeNumber: nil),
                title: "Anime \(id)", posterUrl: nil, posterOrientation: .portrait
            ),
            tracking: TrackingState(
                trackingId: 5, status: status, rating: nil, progress: nil, repeats: nil, startDate: nil, endDate: nil,
                notes: nil, updatedAt: nil, anime: state
            )
        )
    }

    private func user(_ id: Int, _ name: String) -> SocialUser {
        SocialUser(id: id, username: name, displayName: name, avatarUrl: nil, isPrivate: false, viewerRelationship: nil)
    }

    private func post(_ id: Int, by actor: Int, _ name: String) throws -> ActivityItem {
        try FeedTestItems.item(id: id, objectID: id * 10, actorID: actor, username: name, mediaID: "\(id)", title: "Movie \(id)")
    }

    // MARK: What stays in In progress

    func testATitleStaysWhileItIsInProgressAndLeavesWhenAnythingElse() {
        XCTAssertTrue(InProgressRailMembership.stays(book("b1")))
        XCTAssertFalse(InProgressRailMembership.stays(book("b1", status: "Completed")))
        XCTAssertFalse(InProgressRailMembership.stays(book("b1", status: "Paused")))
        XCTAssertFalse(InProgressRailMembership.stays(book("b1", status: "Dropped")))
    }

    func testAShowLeavesWhenItIsFinishedOrCaughtUpOrChangedAndStaysWhenItHasMoreToWatch() {
        let watching = TVTrackingSurfacesTests.crown(watched: 4)
        XCTAssertTrue(InProgressRailMembership.stays(watching))
        // The next season starting is still In progress.
        XCTAssertTrue(InProgressRailMembership.stays(TVTrackingSurfacesTests.crown(watched: 20, upNext: (3, 1))))

        let completed = TVTrackingSurfacesTests.showItem(
            "1396", "The Crown",
            tv: TVTrackingSurfacesTests.showState(status: "Completed", watched: 10, aired: 10, finished: true)
        )
        XCTAssertFalse(InProgressRailMembership.stays(completed))
        let caughtUp = TVTrackingSurfacesTests.showItem(
            "1396", "The Crown",
            tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 6, aired: 6, caughtUp: true)
        )
        XCTAssertFalse(InProgressRailMembership.stays(caughtUp))
        let paused = TVTrackingSurfacesTests.showItem(
            "1396", "The Crown", tv: TVTrackingSurfacesTests.showState(status: "Paused", watched: 4, aired: 10, upNext: (2, 5))
        )
        XCTAssertFalse(InProgressRailMembership.stays(paused))
        // Not caught up but nothing aired is up next: the server's list leaves it out, so a write answer drops it too.
        let nothingOut = TVTrackingSurfacesTests.showItem(
            "1396", "The Crown", tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 6, aired: 6)
        )
        XCTAssertFalse(InProgressRailMembership.stays(nothingOut))
    }

    func testAnAnimeLeavesWhenItIsFinishedOrCaughtUp() {
        XCTAssertTrue(InProgressRailMembership.stays(anime("1")))
        XCTAssertFalse(InProgressRailMembership.stays(anime("1", status: "Completed", finished: true)))
        XCTAssertFalse(InProgressRailMembership.stays(anime("1", caughtUp: true)))
        XCTAssertFalse(InProgressRailMembership.stays(anime("1", status: "Dropped")))
        XCTAssertFalse(InProgressRailMembership.stays(anime("1", upNext: nil)))
    }

    // MARK: A write that finishes a title

    func testAQuickActionThatFinishesATitleRemovesItAtOnceAndTheFocusMovesToANeighbor() async throws {
        let items = [book("b1"), book("b2"), book("b3")]
        let home = makeHome()
        home.inProgressItems = items
        let ids = home.tiles.ids(for: .inProgress)
        home.tilesState.sync(ids, for: .inProgress)
        home.tilesState.openTile(.inProgress)
        home.tilesState.focus(ids[1])
        XCTAssertEqual(home.tiles.subtitle(for: .inProgress), "3 titles")

        // The server answers the page write with the book finished.
        tracking.bookResult = InProgressUpdatesTests.bookTracking(status: "Completed", pages: 476)
        let outcome = await home.runQuickAction(items[1])

        XCTAssertEqual(outcome, .saved)
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [ids[0], ids[2]], "It left the rail without a reload")
        XCTAssertEqual(tracking.listCalls, 0, "...and without asking the server")
        XCTAssertEqual(home.tiles.subtitle(for: .inProgress), "2 titles")
        home.tilesState.sync(home.tiles.ids(for: .inProgress), for: .inProgress)
        XCTAssertEqual(home.tilesState.open, .inProgress)
        XCTAssertEqual(home.tilesState.focusID, ids[2], "The tile follows to the title after the one that left")
        XCTAssertTrue(home.inProgressNeedsReload, "The rail still re-sorts on its next visit")
    }

    func testAQuickActionThatKeepsATitleInProgressKeepsItsCard() async {
        let items = [book("b1"), book("b2")]
        let home = makeHome()
        home.inProgressItems = items
        tracking.bookResult = InProgressUpdatesTests.bookTracking(status: "In progress", pages: 120)

        _ = await home.runQuickAction(items[0])

        XCTAssertEqual(home.tiles.inProgress.map(\.id), items.map(\.id))
    }

    func testFinishingFromTheUpdateSheetAndPausingBothTakeTheCardOffTheRail() {
        let items = [book("b1"), book("b2"), book("b3")]
        let home = makeHome()
        home.inProgressItems = items

        home.progressUpdates.onUpdate(items[0].id, UserMediaState(isTracked: true, status: "Completed"))
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [items[1].id, items[2].id])

        home.progressUpdates.onUpdate(items[1].id, UserMediaState(isTracked: true, status: "Paused"))
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [items[2].id])

        // A save that only moves the progress leaves the card where it is.
        home.progressUpdates.onUpdate(
            items[2].id,
            UserMediaState(isTracked: true, status: "In progress", progress: ProgressState(kind: "pages", value: 200, max: 476, unit: "page"))
        )
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [items[2].id])
        XCTAssertEqual(home.tiles.inProgress.first?.tracking.progress?.value, 200)
    }

    func testTheLastTitleLeavingEmptiesTheRailAndClosesAnOpenTile() {
        let only = book("b1")
        let home = makeHome()
        home.inProgressItems = [only]
        home.tilesState.sync(home.tiles.ids(for: .inProgress), for: .inProgress)
        home.tilesState.openTile(.inProgress)

        home.progressUpdates.onUpdate(only.id, UserMediaState(isTracked: true, status: "Completed"))
        home.tilesState.sync(home.tiles.ids(for: .inProgress), for: .inProgress)

        XCTAssertTrue(home.tiles.inProgress.isEmpty)
        XCTAssertNil(home.tilesState.open)
        XCTAssertEqual(home.tiles.face(for: .inProgress), .offer("Find something"))
    }

    func testAShowSheetTickThatCatchesUpTheShowLeavesWhileAMidSeasonTickStays() throws {
        let crown = TVTrackingSurfacesTests.crown(watched: 4)
        let other = TVTrackingSurfacesTests.showItem(
            "99", "Other", tv: TVTrackingSurfacesTests.showState(status: "In progress", watched: 2, aired: 10, upNext: (1, 3))
        )
        let home = makeHome(enabled: ["tv"])
        home.inProgressItems = [crown, other]

        home.continueWatching.onUpdate(other.id, TVTrackingSurfacesTests.showState(status: "In progress", watched: 3, aired: 10, upNext: (1, 4)))
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [crown.id, other.id])

        home.continueWatching.onUpdate(crown.id, TVTrackingSurfacesTests.showState(status: "In progress", watched: 10, aired: 10, caughtUp: true))
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [other.id], "Caught up: it is not on the server's list either")

        home.continueWatching.onUpdate(other.id, TVTrackingSurfacesTests.showState(status: "Completed", watched: 10, aired: 10, finished: true))
        XCTAssertTrue(home.tiles.inProgress.isEmpty)
    }

    func testAnAnimeSheetTickThatFinishesTheAnimeLeaves() {
        let one = anime("1")
        let two = anime("2")
        let home = makeHome(enabled: ["anime"])
        home.inProgressItems = [one, two]

        home.continueWatching.onAnimeUpdate(
            one.media.ref,
            AnimeState(version: 2, inLibrary: true, status: "In progress", started: true, upNext: AnimeEpisodePointer(episodeNumber: 2))
        )
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [one.id, two.id])

        home.continueWatching.onAnimeUpdate(
            one.media.ref,
            AnimeState(version: 3, inLibrary: true, status: "Completed", started: true, finished: true)
        )
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [two.id])
    }

    func testFinishingOnATitlesPageTakesItOffTheRailBeforeTheReloadComesBack() async {
        let items = [book("b1"), book("b2")]
        let home = makeHome()
        home.inProgressItems = items
        // The title's page finished the first book: the tracking read says so.
        tracking.seasonStates["b1|-1"] = InProgressUpdatesTests.bookTracking(status: "Completed", pages: 476)
        tracking.items["book"] = [items[1]]

        await home.mediaStateDidChange(origin: UUID(), ref: items[0].media.ref)

        XCTAssertEqual(tracking.detailCalls, [items[0].media.ref])
        XCTAssertEqual(home.tiles.inProgress.map(\.id), [items[1].id])
    }

    func testATitlesPageThatLeavesItInProgressKeepsItsCardAndAWriteForNoCardReadsNothing() async {
        let items = [book("b1"), book("b2")]
        let home = makeHome()
        home.inProgressItems = items
        tracking.seasonStates["b1|-1"] = InProgressUpdatesTests.bookTracking(status: "In progress", pages: 300)
        tracking.items["book"] = items

        await home.mediaStateDidChange(origin: UUID(), ref: items[0].media.ref)
        XCTAssertEqual(home.tiles.inProgress.map(\.id), items.map(\.id))

        await home.mediaStateDidChange(origin: UUID(), ref: InProgressUpdatesTests.ref("book", "elsewhere"))
        XCTAssertEqual(tracking.detailCalls.count, 1, "A title that is not on the rail is not read")
    }

    func testHomesOwnWriteIsStillSkippedWhenItEchoes() async {
        let items = [book("b1")]
        let home = makeHome()
        home.inProgressItems = items
        await home.mediaStateDidChange(origin: home.continueWatching.changeOrigin, ref: items[0].media.ref)
        XCTAssertTrue(tracking.detailCalls.isEmpty)
        XCTAssertEqual(tracking.listCalls, 0)
    }

    // MARK: Friends

    func testFriendsWithAPostInTheFeedComeFirstNewestPostFirstThenTheRestAsListed() throws {
        let following = [user(1, "ana"), user(2, "bo"), user(3, "cy"), user(4, "di")]
        let feed = [try post(10, by: 3, "cy"), try post(11, by: 1, "ana"), try post(12, by: 3, "cy")]

        let faces = HomeFriendsModel.order(following, feed: feed)

        XCTAssertEqual(faces.map(\.username), ["cy", "ana", "bo", "di"])
        XCTAssertEqual(HomeFriendsModel.order(following, feed: []).map(\.username), ["ana", "bo", "cy", "di"])
        XCTAssertTrue(HomeFriendsModel.order([], feed: feed).isEmpty)
    }

    func testTheFriendsRowLoadsWithHomeFollowsTheFeedsOrderAndRefreshesOnAFollowChange() async throws {
        social.following = [user(1, "ana"), user(2, "bo")]
        activity.feedPages[nil] = ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [try post(10, by: 2, "bo")])
        let home = makeHome(extras: true)
        XCTAssertTrue(home.friends.faces.isEmpty, "Nothing shows until it has loaded")

        await home.load()
        XCTAssertEqual(home.friends.faces.map(\.username), ["bo", "ana"])
        XCTAssertEqual(social.followListCalls, 1)

        social.following = [user(1, "ana"), user(2, "bo"), user(5, "ed")]
        await home.followsDidChange()
        XCTAssertEqual(home.friends.faces.map(\.username), ["bo", "ana", "ed"])
        XCTAssertEqual(social.followListCalls, 2)

        await home.reload()
        XCTAssertEqual(social.followListCalls, 3, "A pull to refresh reads it again")
    }

    func testAFriendsReadThatFailsLeavesTheRowAsItWasAndHomeWithoutExtrasReadsNothing() async {
        social.following = [user(1, "ana")]
        let home = makeHome(extras: true)
        await home.load()
        XCTAssertEqual(home.friends.faces.map(\.username), ["ana"])

        social.failure = APIError.httpStatus(500, nil)
        await home.loadFriends()
        XCTAssertEqual(home.friends.faces.map(\.username), ["ana"])

        let plain = makeHome(extras: false)
        await plain.load()
        XCTAssertTrue(plain.friends.faces.isEmpty)
        XCTAssertEqual(social.followListCalls, 2, "Only the home that makes the extra reads asked")
    }

    func testTheFriendsAreAskedForThePeopleYouFollowNotYourFollowers() async {
        social.following = [user(1, "ana")]
        let home = makeHome(extras: true)
        await home.load()
        XCTAssertEqual(social.lastFollowers, false)
        XCTAssertEqual(social.lastUsername, home.profile?.username)
    }

    // MARK: The page top, as the source says it

    func testTheFacesAreAQuarterBiggerWithTheSpacingGrownAlongAndTheRowKeepsItsHeight() {
        typealias S = HomeFriendsSpec
        XCTAssertEqual(S.faceScale, 1.25)
        XCTAssertEqual(S.faceSide, (S.capHeight * 1.25 * 2).rounded() / 2)
        XCTAssertEqual(S.faceSide / S.capHeight, 1.25, accuracy: 0.05)
        XCTAssertEqual(S.gap, 7.5)
        XCTAssertEqual(S.headingGap, 15)
        // Still shorter than the heading's line, so the row is no taller and the faces sit on its center line.
        let line = UIFont.systemFont(ofSize: S.headingSize, weight: .heavy).lineHeight
        XCTAssertLessThan(S.faceSide, line)
    }

    func testTheRoomUnderFriendsIsTheRoomAboveIt() throws {
        let sources = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home", isDirectory: true)
        let top = try String(contentsOf: sources.appendingPathComponent("HomeTop.swift"), encoding: .utf8)
        XCTAssertTrue(top.contains("static let friendsToFeed: CGFloat = tilesToFriends"), "one number for both gaps")
        XCTAssertTrue(top.contains(".padding(.bottom, HomeTopSpec.friendsToFeed)"))
        XCTAssertTrue(top.contains(".padding(.top, HomeTopSpec.tilesToFriends)"))
    }

    func testTheHeaderSqueezeReadsOnlyTheOpenTileAndAnimatesWithTheTilesCurve() throws {
        let sources = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home", isDirectory: true)
        let top = try String(contentsOf: sources.appendingPathComponent("HomeTop.swift"), encoding: .utf8)
        XCTAssertTrue(
            top.contains("HomeHeaderSqueeze(state: viewModel.tilesState, glow: viewModel.glowModel)"),
            "The header squeezes with the open tile"
        )
        XCTAssertTrue(top.contains("TileMotion.frame(closing: !squeezed, reduceMotion: reduceMotion)"), "...under the tile's own curve, opening and closing")
        XCTAssertTrue(top.contains("anchor: .topLeading"), "...from its top-leading corner")
        // HomeTop's own body does not read the open tile, or opening one would redraw the whole page top.
        let body = try XCTUnwrap(top.range(of: "var body: some View {"))
        let end = try XCTUnwrap(top.range(of: "private var friendsRow"))
        XCTAssertFalse(String(top[body.upperBound ..< end.lowerBound]).contains("tilesState.open"))
    }

    // MARK: The header above the tiles, under the picture

    func testThePictureHeaderAndTilesSitWhereTheMockPutsThemOnA390PointPhone() {
        typealias S = HomeTopArtSpec
        XCTAssertEqual(S.height(width: 390), 470)
        XCTAssertEqual(S.headerRowTop(width: 390), 232)
        XCTAssertEqual(S.headerRowHeight, 54)
        XCTAssertEqual(S.headerToTiles, 16)
        XCTAssertEqual(S.tilesTop(width: 390), 302)
        XCTAssertEqual(S.tilesTop(width: 390), S.headerRowTop(width: 390) + S.headerRowHeight + S.headerToTiles)
        XCTAssertEqual(HomeBackdropArt.aspect, 390.0 / 470, accuracy: 0.0001)
    }

    func testThePicturesNumbersScaleWithTheWidthWithinLimits() {
        typealias S = HomeTopArtSpec
        XCTAssertEqual(S.scale(width: 390), 1)
        XCTAssertEqual(S.scale(width: 100), 0.9, "clamped from below")
        XCTAssertEqual(S.scale(width: 2000), 1.2, "clamped from above")
        XCTAssertEqual(S.headerRowTop(width: 429), (232 * 429 / 390).rounded())
        XCTAssertEqual(S.height(width: 429), (470 * 429 / 390).rounded())
        XCTAssertEqual(S.headerRowTop(width: 100), (232 * 0.9).rounded())
        XCTAssertEqual(S.height(width: 2000), (470 * 1.2).rounded())
    }

    func testThePicturesShadeKeepsTheStatusBarReadableAndEndsInTheBlackPage() {
        let shade = HomeTopArtSpec.shade
        XCTAssertEqual(shade.first?.opacity, 0.35)
        XCTAssertEqual(shade.first?.location, 0)
        XCTAssertEqual(shade[1].opacity, 0, "gone by 16%")
        XCTAssertEqual(shade[1].location, 0.16)
        XCTAssertEqual(shade.last?.opacity, 1, "solid black at the bottom")
        XCTAssertEqual(shade.last?.location, 1)
        XCTAssertEqual(shade.map(\.location), shade.map(\.location).sorted())
    }

    func testTheRestingHeaderGapPlusTheTilesPaddingIsTheMocksGap() throws {
        let sources = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home", isDirectory: true)
        let top = try String(contentsOf: sources.appendingPathComponent("HomeTop.swift"), encoding: .utf8)
        XCTAssertTrue(top.contains("static let restExtraGap: CGFloat = HomeTopArtSpec.headerToTiles - headerToTiles"))
        XCTAssertTrue(top.contains("static let headerToTiles: CGFloat = 22"), "open, the tiles sit where they always did")
        XCTAssertTrue(top.contains("static let squeezedTop: CGFloat = 17"), "open, the header is where it always was")
    }

    func testTheLabelIsAButtonThatOpensThePicturesTitle() throws {
        let sources = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home", isDirectory: true)
        let top = try String(contentsOf: sources.appendingPathComponent("HomeTop.swift"), encoding: .utf8)
        XCTAssertTrue(top.contains(#".accessibilityLabel("Opens \(art.title)")"#))
        XCTAssertTrue(top.contains("onOpenTitle(art.ref)"))
        XCTAssertTrue(top.contains("onOpenTitle: actions.onOpenTitle"), "the page opens it as it opens any title")
        XCTAssertTrue(top.contains(".dynamicTypeSize(...HomeDesign.Text.maxCategory)"))
        let art = try String(contentsOf: sources.appendingPathComponent("HomeTopArt.swift"), encoding: .utf8)
        XCTAssertTrue(art.contains(".accessibilityHidden(true)"), "the picture is decorative")
    }
}

// MARK: - Test doubles

private final class FriendsSocialRepository: SocialRepository, @unchecked Sendable {
    var following: [SocialUser] = []
    var failure: Error?
    private(set) var followListCalls = 0
    private(set) var lastFollowers: Bool?
    private(set) var lastUsername: String?

    func followList(username: String, followers: Bool, page: String?) async throws -> PagedResponse<SocialUser> {
        followListCalls += 1
        lastFollowers = followers
        lastUsername = username
        if let failure { throw failure }
        return PagedResponse(count: following.count, next: nil, previous: nil, results: following)
    }

    func searchUsers(query: String) async throws -> [SocialUser] { fatalError("Not used") }
    func follow(username: String) async throws -> FollowState { fatalError("Not used") }
    func unfollow(username: String) async throws { fatalError("Not used") }
    func followRequests() async throws -> [FollowRequest] { fatalError("Not used") }
    func answerFollowRequest(id: Int, accept: Bool) async throws { fatalError("Not used") }
    func removeFollower(username: String) async throws { fatalError("Not used") }
    func block(username: String) async throws { fatalError("Not used") }
    func unblock(username: String) async throws { fatalError("Not used") }
    func report(username: String) async throws { fatalError("Not used") }
    func registerPushDevice(_ registration: PushDeviceRegistration) async throws { fatalError("Not used") }
    func unregisterPushDevice(token: String) async throws { fatalError("Not used") }
}
