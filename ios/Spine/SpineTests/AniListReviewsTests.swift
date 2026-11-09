import XCTest
@testable import Spine

@MainActor
final class AniListReviewsTests: XCTestCase {
    func testReviewPageDecodesSnakeCasePayload() throws {
        let data = Data(
            """
            {
              "current_page": 1,
              "next_page": 2,
              "results": [{
                "id": "12",
                "user": {
                  "id": "7",
                  "name": "reviewer",
                  "avatar_url": "https://img.example/avatar.jpg",
                  "profile_url": "https://anilist.co/user/reviewer"
                },
                "score": 90,
                "summary": "Excellent",
                "body": "Review body",
                "community_rating": 42,
                "community_rating_count": 50,
                "url": "https://anilist.co/review/12",
                "created_at": "2023-11-14T22:13:20+00:00"
              }]
            }
            """.utf8
        )

        let page = try JSONDecoder.api.decode(AniListReviewPage.self, from: data)

        XCTAssertEqual(page.currentPage, 1)
        XCTAssertEqual(page.nextPage, 2)
        XCTAssertEqual(page.results.first?.user.name, "reviewer")
        XCTAssertEqual(page.results.first?.communityRatingCount, 50)
    }

    func testRatingSummaryIsAnimeOnlyAndZeroFillsHistogram() throws {
        let rating: [String: JSONValue] = [
            "average_score": .number(84),
            "rating_count": .number(1_000_000),
            "score_distribution": .array([
                .object(["score": .number(10), "count": .number(2)]),
                .object(["score": .number(100), "count": .number(8)]),
            ]),
            "has_reviews": .bool(true),
            "url": .string("https://anilist.co/anime/1"),
        ]
        let anime = makeDetail(mediaType: "anime", rating: rating)
        let movie = makeDetail(mediaType: "movie", rating: rating)

        let summary = try XCTUnwrap(AniListRatingSummary(detail: anime))

        XCTAssertEqual(summary.averageScore, 84)
        XCTAssertEqual(summary.ratingCount, 10)
        XCTAssertEqual(summary.buckets.count, 10)
        XCTAssertEqual(summary.buckets[1].count, 0)
        XCTAssertEqual(summary.buckets.last?.count, 8)
        XCTAssertEqual(summary.bucket(at: 0, width: 100)?.score, 10)
        XCTAssertEqual(summary.bucket(at: 49, width: 100)?.score, 50)
        XCTAssertEqual(summary.bucket(at: 100, width: 100)?.score, 100)
        XCTAssertTrue(summary.hasReviews)
        XCTAssertNil(AniListRatingSummary(detail: movie))
    }

    func testPaginationDeduplicatesAndPreventsConcurrentRequests() async {
        let repository = AniListReviewRepositoryStub(results: [
            .success(page(number: 1, next: 2, ids: ["1", "2"])),
            .success(page(number: 2, next: nil, ids: ["2", "3"])),
        ])
        let viewModel = AniListReviewsViewModel(
            ref: makeDetail(mediaType: "anime", rating: [:]).ref,
            mediaRepository: repository
        )

        await viewModel.loadInitial()
        async let first: Void = viewModel.loadNextPage()
        async let second: Void = viewModel.loadNextPage()
        _ = await (first, second)

        XCTAssertEqual(viewModel.reviews.map(\.id), ["1", "2", "3"])
        XCTAssertNil(viewModel.nextPage)
        let requestedPages = await repository.requestedPages
        XCTAssertEqual(requestedPages, [1, 2])
    }

    func testPaginationFailurePreservesReviewsAndRetries() async {
        let repository = AniListReviewRepositoryStub(results: [
            .success(page(number: 1, next: 2, ids: ["1"])),
            .failure(AniListReviewTestError.failed),
            .success(page(number: 2, next: nil, ids: ["2"])),
        ])
        let viewModel = AniListReviewsViewModel(
            ref: makeDetail(mediaType: "anime", rating: [:]).ref,
            mediaRepository: repository
        )

        await viewModel.loadInitial()
        await viewModel.loadNextPage()

        XCTAssertEqual(viewModel.reviews.map(\.id), ["1"])
        XCTAssertNotNil(viewModel.paginationError)

        await viewModel.loadNextPage()

        XCTAssertEqual(viewModel.reviews.map(\.id), ["1", "2"])
        XCTAssertNil(viewModel.paginationError)
    }

    func testMediaReviewPaginationLoadsEveryPageWithoutDuplicates() async {
        let repository = MediaReviewRepositoryStub(results: [
            mediaPage(next: "2", ids: [1, 2]),
            mediaPage(next: nil, ids: [2, 3]),
        ])
        let viewModel = MediaReviewsViewModel(
            ref: makeDetail(mediaType: "movie", rating: [:]).ref,
            mediaRepository: repository
        )

        await viewModel.loadInitial()
        async let first: Void = viewModel.loadNextPage()
        async let second: Void = viewModel.loadNextPage()
        _ = await (first, second)

        XCTAssertEqual(viewModel.reviews.map(\.id), [1, 2, 3])
        XCTAssertEqual(viewModel.reviewCount, 3)
        XCTAssertNil(viewModel.nextPage)
        let requestedPages = await repository.requestedPages
        XCTAssertEqual(requestedPages, [nil, "2"])
    }

    private func makeDetail(
        mediaType: String,
        rating: [String: JSONValue]
    ) -> MediaDetail {
        MediaDetail(
            ref: MediaRef(
                itemId: nil,
                source: "mal",
                mediaType: mediaType,
                mediaId: "1",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Title",
            details: ["anilist_rating": .object(rating)]
        )
    }

    private func page(
        number: Int,
        next: Int?,
        ids: [String]
    ) -> AniListReviewPage {
        AniListReviewPage(
            currentPage: number,
            nextPage: next,
            results: ids.map {
                AniListReview(
                    id: $0,
                    user: AniListReviewUser(
                        id: $0,
                        name: "Reviewer \($0)",
                        avatarUrl: nil,
                        profileUrl: nil
                    ),
                    score: 80,
                    summary: nil,
                    body: "Review \($0)",
                    communityRating: nil,
                    communityRatingCount: nil,
                    url: nil,
                    createdAt: nil
                )
            }
        )
    }

    private func mediaPage(next: String?, ids: [Int]) -> PagedResponse<MediaReview> {
        PagedResponse(
            count: 3,
            next: next.map { "https://example.com/api/v1/media/tmdb/movie/1/reviews/?page=\($0)" },
            previous: nil,
            results: ids.map {
                MediaReview(
                    id: $0,
                    user: UserSummary(id: $0, username: "reviewer\($0)", displayName: "Reviewer \($0)", avatarUrl: nil),
                    rating: "4.5",
                    reviewTitle: nil,
                    review: "Review \($0)",
                    containsSpoilers: false,
                    likeCount: 0,
                    viewerHasLiked: false,
                    consumedAt: nil,
                    createdAt: nil
                )
            }
        )
    }
}

private enum AniListReviewTestError: LocalizedError {
    case failed

    var errorDescription: String? { "Loading failed." }
}

private actor AniListReviewRepositoryStub: MediaRepository {
    private var results: [Result<AniListReviewPage, Error>]
    private(set) var requestedPages: [Int] = []

    init(results: [Result<AniListReviewPage, Error>]) {
        self.results = results
    }

    func anilistReviews(ref _: MediaRef, page: Int) async throws -> AniListReviewPage {
        requestedPages.append(page)
        await Task.yield()
        return try results.removeFirst().get()
    }

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
    func clearHomePicture() async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

private actor MediaReviewRepositoryStub: MediaRepository {
    private var results: [PagedResponse<MediaReview>]
    private(set) var requestedPages: [String?] = []

    init(results: [PagedResponse<MediaReview>]) {
        self.results = results
    }

    func reviewPage(ref _: MediaRef, page: String?) async throws -> PagedResponse<MediaReview> {
        requestedPages.append(page)
        await Task.yield()
        return results.removeFirst()
    }

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
    func clearHomePicture() async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}
