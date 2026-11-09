import XCTest
@testable import Spine

final class SocialTests: XCTestCase {
    func testTappedPushesOpenAProfileOrYourFollowRequests() {
        XCTAssertEqual(
            PushRoute(userInfo: ["aps": [:], "route": ["kind": "profile", "username": "saiuri"]]),
            .profile(username: "saiuri")
        )
        XCTAssertEqual(PushRoute(userInfo: ["route": ["kind": "follow_requests"]]), .followRequests)
        XCTAssertNil(PushRoute(userInfo: ["route": ["kind": "profile"]]))
        XCTAssertNil(PushRoute(userInfo: ["route": ["kind": "likes"]]))
        XCTAssertNil(PushRoute(userInfo: ["aps": [:]]))
    }

    func testFollowButtonSaysFollowBackOnlyBeforeYouFollow() {
        let followsYou = SocialRelationship(following: false, requested: false, followedBy: true)

        XCTAssertEqual(FollowState(relationship: followsYou).title(followedBy: true), "Follow Back")
        XCTAssertEqual(FollowState(relationship: nil).title(followedBy: false), "Follow")
        XCTAssertEqual(
            FollowState(relationship: SocialRelationship(following: false, requested: true, followedBy: true)),
            .requested
        )
        XCTAssertEqual(FollowState.following.title(followedBy: true), "Following")
        XCTAssertEqual(FollowState(apiState: "requested"), .requested)
        XCTAssertEqual(FollowState(apiState: "none"), .none)
    }

    func testProfilesDecodeRestrictedAndOlderBackendsCountAsOpen() throws {
        let restricted = try JSONDecoder.api.decode(UserProfile.self, from: profileJSON(restricted: "true"))
        let older = try JSONDecoder.api.decode(UserProfile.self, from: profileJSON(restricted: nil))

        XCTAssertTrue(restricted.isRestricted)
        XCTAssertFalse(older.isRestricted)
        XCTAssertTrue(restricted.replacingHallOfFame([:]).isRestricted)
    }

    func testFollowListRowsDecodeTheirConnectionToYou() throws {
        let page = try JSONDecoder.api.decode(PagedResponse<SocialUser>.self, from: Data("""
        {
          "count": 1,
          "next": "http://127.0.0.1:8010/api/v1/users/saiuri/followers/?page=2",
          "previous": null,
          "results": [{
            "id": 2,
            "username": "armaan",
            "display_name": "Armaan",
            "avatar_url": null,
            "is_private": true,
            "viewer_relationship": { "following": false, "requested": true, "followed_by": true }
          }]
        }
        """.utf8))
        let searched = try JSONDecoder.api.decode(UserSearchResponse.self, from: Data("""
        { "results": [{ "id": 3, "username": "sai", "display_name": "Sai", "avatar_url": null, "is_private": false }] }
        """.utf8))

        XCTAssertEqual(page.results.first?.followState, .requested)
        XCTAssertEqual(page.results.first?.viewerRelationship?.followedBy, true)
        XCTAssertEqual(searched.results.first?.followState, FollowState.none)
    }

    func testSomeoneElsesPagesReadTheirUserPaths() {
        XCTAssertEqual(userScopedPath("diary/", username: nil), "/diary/")
        XCTAssertEqual(userScopedPath("diary/tags/", username: "saiuri"), "/users/saiuri/diary/tags/")
        XCTAssertEqual(userScopedPath("tracking/", username: "a b"), "/users/a%20b/tracking/")
    }

    @MainActor
    func testUsersLensSearchesPeopleNotMedia() async {
        let social = RecordingSocialRepository()
        let viewModel = SearchViewModel(mediaRepository: UnusedMediaRepository(), socialRepository: social, onUnauthorized: {})

        await viewModel.search("sai", mediaType: SearchViewModel.usersScope)

        XCTAssertEqual(social.searches, ["sai"])
        XCTAssertEqual(viewModel.users.map(\.username), ["saiuri"])
        XCTAssertTrue(viewModel.results.isEmpty)
    }

    @MainActor
    func testHomesSeeAllFeedReadsThePeopleYouFollow() async {
        let activity = RecordingActivityRepository()
        let viewModel = ActivityFeedViewModel(username: nil, pageSize: 25, activityRepository: activity, onUnauthorized: {})

        await viewModel.load()

        XCTAssertEqual(activity.feedRequests, 1)
        XCTAssertEqual(activity.profileRequests, 0)
    }

    @MainActor
    func testRemovingAFollowerTakesThemOffYourList() async {
        let social = RecordingSocialRepository()
        let viewModel = FollowListViewModel(
            username: "armaan",
            showsFollowers: true,
            isOwnFollowers: true,
            repository: social,
            onUnauthorized: {}
        )
        await viewModel.load()

        await viewModel.remove(viewModel.users[0])

        XCTAssertTrue(viewModel.users.isEmpty)
        XCTAssertEqual(social.removed, ["saiuri"])
        XCTAssertEqual(viewModel.requests.map(\.id), [7])
    }

    @MainActor
    func testApprovingARequestReloadsYourFollowers() async {
        let social = RecordingSocialRepository()
        let viewModel = FollowListViewModel(
            username: "armaan",
            showsFollowers: true,
            isOwnFollowers: true,
            repository: social,
            onUnauthorized: {}
        )
        await viewModel.load()

        await viewModel.answer(viewModel.requests[0], accept: true)

        XCTAssertEqual(social.answers.first?.id, 7)
        XCTAssertEqual(social.answers.first?.accept, true)
        XCTAssertEqual(social.listLoads, 2)
    }

    private func profileJSON(restricted: String?) -> Data {
        Data("""
        {
          "id": 2,
          "username": "saiuri",
          "display_name": "Sai",
          "email": null,
          "bio": "",
          "pronouns": "",
          "location": "",
          "avatar_url": null,
          "is_private": true,
          \(restricted.map { "\"restricted\": \($0)," } ?? "")
          "viewer_relationship": { "following": false, "followed_by": false, "requested": false, "blocked": false },
          "counts": { "followers": 1, "following": 1, "diary_entries": 4, "lists": 0 },
          "hof": {},
          "preferences": {
            "enabled_media_types": ["movie"],
            "date_format": "YYYY-MM-DD",
            "time_format": "24h",
            "week_start_day": "monday",
            "quick_watch_date": "today",
            "release_notifications_enabled": false,
            "daily_digest_enabled": false
          }
        }
        """.utf8)
    }
}

private final class RecordingSocialRepository: SocialRepository {
    var searches: [String] = []
    var removed: [String] = []
    var answers: [(id: Int, accept: Bool)] = []
    var listLoads = 0

    private let friend = SocialUser(
        id: 3,
        username: "saiuri",
        displayName: "Sai",
        avatarUrl: nil,
        isPrivate: false,
        viewerRelationship: SocialRelationship(following: true, requested: false, followedBy: true)
    )

    func searchUsers(query: String) async throws -> [SocialUser] {
        searches.append(query)
        return [friend]
    }

    func follow(username _: String) async throws -> FollowState { .following }

    func unfollow(username _: String) async throws {}

    func followList(username _: String, followers _: Bool, page _: String?) async throws -> PagedResponse<SocialUser> {
        listLoads += 1
        return PagedResponse(count: 1, next: nil, previous: nil, results: [friend])
    }

    func followRequests() async throws -> [FollowRequest] {
        [FollowRequest(id: 7, fromUser: UserSummary(id: 4, username: "asks", displayName: "Asks", avatarUrl: nil))]
    }

    func answerFollowRequest(id: Int, accept: Bool) async throws {
        answers.append((id, accept))
    }

    func removeFollower(username: String) async throws {
        removed.append(username)
    }

    func block(username _: String) async throws {}

    func unblock(username _: String) async throws {}

    func report(username _: String) async throws {}

    func registerPushDevice(_: PushDeviceRegistration) async throws {}

    func unregisterPushDevice(token _: String) async throws {}
}

private struct UnusedMediaRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query _: String, mediaType _: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref _: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref _: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref _: MediaRef, posterURL _: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref _: MediaRef, backdropURL _: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func homePicture() async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func setHomePicture(ref _: MediaRef, backdropURL _: String) async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

private final class RecordingActivityRepository: ActivityRepository {
    var feedRequests = 0
    var profileRequests = 0

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] {
        profileRequests += 1
        return []
    }

    func userActivityPage(username _: String, pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        profileRequests += 1
        return ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [])
    }

    func feedPage(pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        feedRequests += 1
        return ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [])
    }
}
