import XCTest
@testable import Spine

@MainActor
final class MediaFriendsTests: XCTestCase {
    func testDecodesTheServersShape() throws {
        let json = """
        {"count": 1, "results": [{
            "user": {"id": 7, "username": "maya", "display_name": "Maya Chen", "avatar_url": null},
            "diary_entry_id": 12,
            "consumed_at": "2026-10-14",
            "rating": "4.5",
            "liked": true,
            "is_rewatch": false,
            "review_title": "",
            "review": "Wrecked me.",
            "contains_spoilers": false,
            "status": "Completed",
            "progress_label": null
        }]}
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = try decoder.decode(MediaFriendsResponse.self, from: Data(json.utf8))

        XCTAssertEqual(response.count, 1)
        let friend = try XCTUnwrap(response.results.first)
        XCTAssertEqual(friend.id, 7)
        XCTAssertEqual(friend.user.displayName, "Maya Chen")
        XCTAssertEqual(friend.diaryEntryId, 12)
        XCTAssertEqual(friend.rating, "4.5")
        XCTAssertTrue(friend.liked)
        XCTAssertTrue(friend.showsReview)
    }

    func testStandingUsesEachTypesWords() {
        let cases: [(String, String, String)] = [
            ("In progress", "book", "Currently Reading"),
            ("In progress", "game", "Playing"),
            ("In progress", "music", "Listening"),
            ("In progress", "manga", "Reading"),
            ("In progress", "comic", "Reading"),
            ("In progress", "tv", "Watching"),
            ("In progress", "season", "Watching"),
            ("In progress", "anime", "Watching"),
            ("Completed", "book", "Read"),
            ("Completed", "music", "Listened"),
            ("Completed", "movie", "Completed"),
            ("Completed", "season", "Watched"),
            ("Completed", "episode", "Watched"),
            ("Completed", "tv", "Completed"),
            ("Dropped", "book", "Did Not Finish"),
            ("Dropped", "music", "Stopped"),
            ("Dropped", "tv", "Dropped"),
            ("Paused", "book", "Paused"),
        ]
        for (status, mediaType, label) in cases {
            XCTAssertEqual(friend(status: status).standingLabel(for: ref(mediaType)), label, "\(status) \(mediaType)")
        }
    }

    func testALogHidesWhereTheyStand() {
        XCTAssertNil(friend(diaryEntryId: 3, status: "In progress").standingLabel(for: ref("book")))
    }

    func testRatedWithoutAStatusHasNoStanding() {
        XCTAssertNil(friend(rating: "4.0").standingLabel(for: ref("movie")))
    }

    func testLogWithoutAReviewSaysWhenItWasLogged() {
        let logged = friend(diaryEntryId: 3, consumedAt: "2024-03-02")
        XCTAssertEqual(logged.logLine, "Logged Mar 2, 2024")
        XCTAssertNil(friend(diaryEntryId: 3, consumedAt: "2024-03-02", review: "Words.").logLine)
        XCTAssertNil(friend(consumedAt: nil).logLine)
    }

    func testSpoilerReviewKeepsItsWordsOffTheCard() {
        let spoiler = friend(diaryEntryId: 3, consumedAt: "2024-03-02", review: "He dies.", containsSpoilers: true)
        XCTAssertFalse(spoiler.showsReview)
        XCTAssertTrue(spoiler.hidesReview)
        XCTAssertEqual(spoiler.logLine, "Logged Mar 2, 2024")
    }

    func testSpoilerFlagWithoutAReviewHasNothingToHide() {
        XCTAssertFalse(friend(diaryEntryId: 3, containsSpoilers: true).hidesReview)
    }

    func testRatingStatsChartOnlyTheFriendsWhoRated() throws {
        let friends = [
            friend(rating: "4.5"),
            friend(rating: "4.0"),
            friend(rating: "4.0"),
            friend(rating: "3.5"),
            friend(status: "In progress"),
        ]
        let stats = try XCTUnwrap(friends.ratingStats)
        XCTAssertEqual(stats.ratingCount, 4)
        XCTAssertEqual(stats.averageRating, "4.00")
        XCTAssertEqual(stats.reviewCount, 0)
        XCTAssertEqual(
            Dictionary(uniqueKeysWithValues: stats.ratingDistribution.map { ($0.rating, $0.count) }),
            ["4.5": 1, "4.0": 2, "3.5": 1]
        )
    }

    func testNoChartWhenNoFriendRated() {
        XCTAssertNil([friend(status: "In progress"), friend(diaryEntryId: 3)].ratingStats)
    }

    func testHeaderOpensTheFullListFromThreeFriends() {
        XCTAssertFalse(MediaFriendsSection.linksToFullList(count: 1))
        XCTAssertFalse(MediaFriendsSection.linksToFullList(count: 2))
        XCTAssertTrue(MediaFriendsSection.linksToFullList(count: 3))
    }

    func testLoadFriendsFillsTheSection() async {
        let viewModel = makeViewModel(FriendsRepository(result: .success(MediaFriendsResponse(count: 1, results: [friend(status: "Paused")]))))
        await viewModel.loadFriends()
        XCTAssertEqual(viewModel.friends.map(\.id), [7])
    }

    func testFailedLoadKeepsTheSectionAway() async {
        var signedOut = false
        let viewModel = makeViewModel(FriendsRepository(result: .failure(APIError.httpStatus(500, nil)))) { signedOut = true }
        await viewModel.loadFriends()
        XCTAssertTrue(viewModel.friends.isEmpty)
        XCTAssertFalse(signedOut)
    }

    func testUnauthorizedLoadSignsOut() async {
        var signedOut = false
        let viewModel = makeViewModel(FriendsRepository(result: .failure(APIError.unauthorized))) { signedOut = true }
        await viewModel.loadFriends()
        XCTAssertTrue(signedOut)
    }

    private func makeViewModel(
        _ repository: FriendsRepository,
        onUnauthorized: @escaping () -> Void = {}
    ) -> MediaDetailViewModel {
        MediaDetailViewModel(
            ref: ref("book"),
            mediaRepository: repository,
            trackingRepository: SurfacesTrackingRepository(),
            diaryRepository: SurfacesDiaryRepository(),
            onUnauthorized: onUnauthorized
        )
    }

    private func ref(_ mediaType: String) -> MediaRef {
        MediaRef(itemId: nil, source: "manual", mediaType: mediaType, mediaId: "1", seasonNumber: nil, episodeNumber: nil)
    }

    private func friend(
        diaryEntryId: Int? = nil,
        consumedAt: String? = nil,
        rating: String? = nil,
        review: String = "",
        containsSpoilers: Bool = false,
        status: String? = nil
    ) -> MediaFriend {
        MediaFriend(
            user: UserSummary(id: 7, username: "maya", displayName: "Maya", avatarUrl: nil),
            diaryEntryId: diaryEntryId,
            consumedAt: consumedAt,
            rating: rating,
            liked: false,
            isRewatch: false,
            reviewTitle: nil,
            review: review,
            containsSpoilers: containsSpoilers,
            status: status,
            progressLabel: nil
        )
    }
}

private struct FriendsRepository: MediaRepository {
    let result: Result<MediaFriendsResponse, Error>

    func friends(ref _: MediaRef) async throws -> MediaFriendsResponse { try result.get() }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query _: String, mediaType _: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref _: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref _: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref _: MediaRef, posterURL _: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref _: MediaRef, backdropURL _: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}
