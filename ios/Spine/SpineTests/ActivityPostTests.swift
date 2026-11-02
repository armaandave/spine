import CoreGraphics
import XCTest
@testable import Spine

final class ActivityPostTests: XCTestCase {
    private func activity(_ json: String) throws -> ActivityItem {
        try JSONDecoder.api.decode(ActivityItem.self, from: Data(json.utf8))
    }

    private func log(id: Int = 7, hasLiked: Bool = false, likeCount: Int = 2, commentCount: Int = 1) throws -> ActivityItem {
        try activity("""
        {
          "id": \(id), "type": "diary_created", "created_at": "2026-11-01T12:00:00Z", "consumed_at": "2026-11-01",
          "actor": {"id": 2, "username": "maya", "display_name": "Maya", "avatar_url": null},
          "media": {"ref": {"source": "tmdb", "media_type": "movie", "media_id": "1"}, "title": "Past Lives"},
          "person": null,
          "object": {"type": "diary", "id": \(id), "rating": "4.5", "liked": true, "is_rewatch": false},
          "viewer": {"can_view": true, "has_liked": \(hasLiked)},
          "like_count": \(likeCount), "comment_count": \(commentCount),
          "review_title": "", "review": "Wrecked me.", "contains_spoilers": true
        }
        """)
    }

    private func progress(id: Int = 9, summary: String = "watched 4 episodes", title: String = "Severance") throws -> ActivityItem {
        try activity("""
        {
          "id": -\(id), "type": "progress_updated", "created_at": "2026-11-01T12:00:00Z", "consumed_at": "2026-11-01",
          "actor": {"id": 3, "username": "jonah", "display_name": "Jonah", "avatar_url": null},
          "media": {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "2"}, "title": "\(title)"},
          "person": null,
          "object": {"type": "progress_day", "id": \(id), "summary": "\(summary)", "label": "4 episodes"},
          "viewer": {"can_view": true, "has_liked": false},
          "like_count": 0, "comment_count": 0
        }
        """)
    }

    // MARK: The wire shape

    func testALogDecodesItsReviewAndEngagement() throws {
        let item = try log(hasLiked: true)

        XCTAssertEqual(item.post, .log(7))
        XCTAssertEqual(item.engagement, PostEngagement(hasLiked: true, likeCount: 2, commentCount: 1))
        XCTAssertEqual(item.review, "Wrecked me.")
        XCTAssertEqual(item.containsSpoilers, true)
        XCTAssertEqual(ActivityFeedPresentation.reviewText(for: item), "Wrecked me.")
        // The author's own heart on the title isn't a like of the post.
        XCTAssertEqual(item.object.liked, true)
    }

    func testAnOlderServersPostHasNoLikesOrReview() throws {
        let item = try activity("""
        {
          "id": 7, "type": "diary_created", "created_at": null,
          "actor": {"id": 2, "username": "maya", "display_name": "Maya", "avatar_url": null},
          "media": null, "person": null,
          "object": {"type": "diary", "id": 7}
        }
        """)

        XCTAssertEqual(item.engagement, PostEngagement(hasLiked: false, likeCount: 0, commentCount: 0))
        XCTAssertNil(ActivityFeedPresentation.reviewText(for: item))
    }

    func testAProgressPostOpensItsOwnPage() throws {
        let item = try progress()

        XCTAssertEqual(item.post, .progress(9))
        XCTAssertEqual(ActivityDestination.resolve(item), .progress(9))
        XCTAssertEqual(ActivityFeedPresentation.destinationHint(for: item), "Opens this progress")
    }

    func testAProgressPostReadsAsASentence() throws {
        let cases = [
            ("watched 4 episodes", "Severance", "Watched 4 episodes of Severance"),
            ("read 40 pages", "Dune", "Read 40 pages of Dune"),
            ("played 2h 30m", "Hades", "Played 2h 30m of Hades"),
            ("started reading", "Dune", "Started reading Dune"),
            ("started playing", "Hades", "Started playing Hades"),
            ("progressed 5%", "Hades", "Progressed 5% in Hades"),
        ]
        for (summary, title, sentence) in cases {
            XCTAssertEqual(ActivityFeedPresentation.progressSentence(for: try progress(summary: summary, title: title)), sentence)
        }
        XCTAssertEqual(ActivityFeedPresentation.progressHeadline(for: try progress()), "Watched 4 episodes")
    }

    // MARK: The feed

    @MainActor
    private func loadedFeed(_ items: [ActivityItem], repository: FakePostRepository) async -> ActivityFeedViewModel {
        repository.feed = items
        let viewModel = ActivityFeedViewModel(username: nil, activityRepository: repository, onUnauthorized: {})
        await viewModel.load()
        return viewModel
    }

    @MainActor
    func testLikingFromTheFeedSettlesOnTheServersCount() async throws {
        let repository = FakePostRepository()
        repository.likeResult = .success(LikeState(liked: true, likeCount: 5))
        let item = try log(hasLiked: false, likeCount: 2)
        let viewModel = await loadedFeed([item], repository: repository)

        await viewModel.toggleLike(item)

        XCTAssertEqual(repository.likes.first?.post, .log(7))
        XCTAssertEqual(repository.likes.first?.liked, true)
        XCTAssertEqual(viewModel.items[0].engagement, PostEngagement(hasLiked: true, likeCount: 5, commentCount: 1))
    }

    @MainActor
    func testAFailedLikeGoesBack() async throws {
        let repository = FakePostRepository()
        repository.likeResult = .failure(URLError(.notConnectedToInternet))
        let item = try log(hasLiked: true, likeCount: 2)
        let viewModel = await loadedFeed([item], repository: repository)

        await viewModel.toggleLike(item)

        XCTAssertEqual(repository.likes.first?.liked, false)
        XCTAssertEqual(viewModel.items[0].engagement, PostEngagement(hasLiked: true, likeCount: 2, commentCount: 1))
    }

    @MainActor
    func testAPostsPageUpdatesTheFeedInPlace() async throws {
        let viewModel = await loadedFeed([try log(), try progress()], repository: FakePostRepository())

        viewModel.apply(PostEngagement(hasLiked: true, likeCount: 1, commentCount: 3), to: .progress(9))

        XCTAssertEqual(viewModel.items[1].engagement.commentCount, 3)
        XCTAssertEqual(viewModel.items[0].engagement.commentCount, 1, "other posts stay as they were")
    }

    @MainActor
    func testARefreshDuringAPageLoadDropsThatPage() async throws {
        let repository = FakePostRepository()
        let viewModel = ActivityFeedViewModel(username: nil, activityRepository: repository, onUnauthorized: {})
        repository.feedResponses = [
            ActivityCursorResponse(nextCursor: "https://example.com/?cursor=two", previousCursor: nil, results: [try log(id: 1)]),
            ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [try log(id: 2)]),
            ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [try log(id: 3)]),
        ]
        await viewModel.load()
        repository.beforeFeedResponse = { [weak viewModel] in
            repository.beforeFeedResponse = nil
            await viewModel?.load()
        }

        await viewModel.loadNextPage()

        XCTAssertEqual(viewModel.items.map(\.id), [3], "The refreshed first page stands; the older page 2 is dropped")
    }

    // MARK: A post's page

    private func comment(_ id: Int, by username: String = "maya", canDelete: Bool = true) -> PostComment {
        PostComment(
            id: id,
            user: UserSummary(id: id, username: username, displayName: username, avatarUrl: nil),
            body: "Comment \(id)",
            createdAt: "2026-11-01T12:00:00Z",
            canDelete: canDelete
        )
    }

    @MainActor
    private func model(_ repository: FakePostRepository) -> PostEngagementModel {
        PostEngagementModel(post: .log(7), repository: repository, onUnauthorized: {})
    }

    @MainActor
    func testSendingAddsTheCommentClearsTheBoxAndTellsTheFeed() async {
        let repository = FakePostRepository()
        repository.nextComment = comment(5)
        let model = model(repository)
        model.seed(PostEngagement(hasLiked: false, likeCount: 0, commentCount: 0))
        model.draft = "  Finally!  "
        let told = expectation(forNotification: .activityPostDidChange, object: nil) { notification in
            ActivityPostChange.parse(notification)?.engagement.commentCount == 1
        }

        await model.send()

        XCTAssertEqual(repository.sent, ["Finally!"])
        XCTAssertEqual(model.comments.map(\.id), [5])
        XCTAssertEqual(model.draft, "")
        XCTAssertEqual(model.sentCount, 1)
        await fulfillment(of: [told], timeout: 1)
    }

    @MainActor
    func testWhatsTypedWhileSendingIsKept() async {
        let repository = FakePostRepository()
        repository.nextComment = comment(5)
        let model = model(repository)
        model.draft = "First"
        repository.whileAdding = { model.draft = "Second" }

        await model.send()

        XCTAssertEqual(repository.sent, ["First"])
        XCTAssertEqual(model.draft, "Second")
    }

    @MainActor
    func testASlowLoadDoesntEraseACommentJustSent() async {
        let repository = FakePostRepository()
        repository.nextComment = comment(5)
        repository.comments = [comment(1)]
        let model = model(repository)
        model.draft = "Sent before the load came back"
        await model.send()

        await model.loadComments()

        XCTAssertEqual(model.comments.map(\.id), [1, 5])
        XCTAssertEqual(model.engagement.commentCount, 2)
    }

    @MainActor
    func testASpoilerLogHidesOthersCommentsButNotYourOwn() async {
        let repository = FakePostRepository()
        repository.comments = [comment(1)]
        repository.nextComment = comment(5)
        let model = model(repository)
        await model.loadComments()
        model.draft = "No spoilers, watching tonight"
        await model.send()

        XCTAssertEqual(model.visibleComments(hidingOthers: true).map(\.id), [5])
        XCTAssertEqual(model.visibleComments(hidingOthers: false).map(\.id), [1, 5])
    }

    @MainActor
    func testCommentsLoadOnReturnIfLeavingCancelledThem() async {
        let repository = FakePostRepository()
        repository.commentsError = CancellationError()
        let model = model(repository)

        await model.loadCommentsIfNeeded()
        XCTAssertNil(model.commentsErrorMessage, "Leaving the page isn't a failure")

        repository.commentsError = nil
        repository.comments = [comment(1)]
        await model.loadCommentsIfNeeded()
        XCTAssertEqual(model.comments.map(\.id), [1])

        repository.comments = []
        await model.loadCommentsIfNeeded()
        XCTAssertEqual(model.comments.map(\.id), [1], "Once loaded, coming back doesn't load them again")
    }

    @MainActor
    func testAFailedSendKeepsTheDraft() async {
        let repository = FakePostRepository()
        repository.nextComment = nil
        let model = model(repository)
        model.draft = "Keep me"

        await model.send()

        XCTAssertEqual(model.draft, "Keep me")
        XCTAssertNotNil(model.actionErrorMessage)
        XCTAssertTrue(model.comments.isEmpty)
    }

    @MainActor
    func testAnEmptyDraftCantBeSent() async {
        let repository = FakePostRepository()
        let model = model(repository)
        model.draft = "   "

        XCTAssertFalse(model.canSend)
        await model.send()

        XCTAssertTrue(repository.sent.isEmpty)
    }

    @MainActor
    func testLoadingCommentsCorrectsTheCount() async {
        let repository = FakePostRepository()
        repository.comments = [comment(1), comment(2)]
        let model = model(repository)
        model.seed(PostEngagement(hasLiked: false, likeCount: 0, commentCount: 5))

        await model.loadComments()

        XCTAssertEqual(model.comments.map(\.id), [1, 2])
        XCTAssertEqual(model.engagement.commentCount, 2)
    }

    @MainActor
    func testDeletingRemovesTheComment() async {
        let repository = FakePostRepository()
        repository.comments = [comment(1), comment(2)]
        let model = model(repository)
        await model.loadComments()

        await model.delete(comment(1))

        XCTAssertEqual(repository.deleted, [1])
        XCTAssertEqual(model.comments.map(\.id), [2])
        XCTAssertEqual(model.engagement.commentCount, 1)
    }

    @MainActor
    func testAFailedLikeOnThePageGoesBack() async {
        let repository = FakePostRepository()
        repository.likeResult = .failure(URLError(.timedOut))
        let model = model(repository)
        model.seed(PostEngagement(hasLiked: false, likeCount: 3, commentCount: 0))

        await model.toggleLike()

        XCTAssertEqual(model.engagement, PostEngagement(hasLiked: false, likeCount: 3, commentCount: 0))
        XCTAssertNotNil(model.actionErrorMessage)
    }

    // MARK: Tags on someone else's log

    @MainActor
    func testATagOnSomeoneElsesLogOpensTheirTagPage() {
        let repository = OwnerRecordingDiaryRepository()
        let maya = UserSummary(id: 2, username: "maya", displayName: "Maya", avatarUrl: nil)

        _ = DiaryLogDetailView.tagRepository(for: maya, base: repository, currentUserId: 1)

        XCTAssertEqual(repository.owners, ["maya"])
    }

    @MainActor
    func testATagOnYourOwnLogStaysYours() {
        let repository = OwnerRecordingDiaryRepository()
        let me = UserSummary(id: 1, username: "me", displayName: "Me", avatarUrl: nil)

        _ = DiaryLogDetailView.tagRepository(for: me, base: repository, currentUserId: 1)

        XCTAssertTrue(repository.owners.isEmpty)
    }

    // MARK: The gradient

    /// A poster with one color over another; green and blue here, 0-1 RGB.
    private func poster(top: (Double, Double, Double), bottom: (Double, Double, Double)) throws -> CGImage {
        let context = try XCTUnwrap(CGContext(
            data: nil,
            width: 40,
            height: 60,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: try XCTUnwrap(CGColorSpace(name: CGColorSpace.sRGB)),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(red: top.0, green: top.1, blue: top.2, alpha: 1)
        context.fill(CGRect(x: 0, y: 30, width: 40, height: 30))
        context.setFillColor(red: bottom.0, green: bottom.1, blue: bottom.2, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 40, height: 30))
        return try XCTUnwrap(context.makeImage())
    }

    func testATwoColorPosterGivesATwoColorGradient() throws {
        let image = try poster(top: (0.1, 0.3, 0.9), bottom: (0.1, 0.8, 0.3))

        let colors = try XCTUnwrap(PosterGradientColors.colors(in: image))

        XCTAssertGreaterThan(abs(colors.lead.hue - colors.trail.hue), 0.1)
    }

    func testAOneColorPosterStaysOneHue() throws {
        let image = try poster(top: (0.9, 0.2, 0.1), bottom: (0.9, 0.2, 0.1))

        let colors = try XCTUnwrap(PosterGradientColors.colors(in: image))

        XCTAssertEqual(colors.lead.hue, colors.trail.hue, accuracy: 0.02)
    }

    func testTheGradientStaysDarkEnoughForWhiteText() throws {
        let brightest = try poster(top: (1, 0.9, 0.2), bottom: (1, 1, 1))

        let colors = try XCTUnwrap(PosterGradientColors.colors(in: brightest))

        XCTAssertLessThanOrEqual(colors.lead.brightness, 0.56)
        XCTAssertLessThanOrEqual(colors.trail.brightness, 0.25)
    }

    func testAMostlyGrayPosterTakesItsColorFromItsVividPart() throws {
        let context = try XCTUnwrap(CGContext(
            data: nil,
            width: 40,
            height: 60,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: try XCTUnwrap(CGColorSpace(name: CGColorSpace.sRGB)),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(red: 0.85, green: 0.85, blue: 0.85, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 40, height: 60))
        context.setFillColor(red: 0.9, green: 0.4, blue: 0.1, alpha: 1)
        context.fill(CGRect(x: 10, y: 15, width: 20, height: 30))

        let colors = try XCTUnwrap(PosterGradientColors.colors(in: try XCTUnwrap(context.makeImage())))

        XCTAssertGreaterThan(colors.lead.saturation, 0.3)
    }

    func testAGrayPosterStaysNeutral() throws {
        let image = try poster(top: (0.5, 0.5, 0.5), bottom: (0.2, 0.2, 0.2))

        let colors = try XCTUnwrap(PosterGradientColors.colors(in: image))

        XCTAssertLessThanOrEqual(colors.lead.saturation, 0.2)
        XCTAssertLessThanOrEqual(colors.trail.saturation, 0.2)
    }
}

private final class FakePostRepository: ActivityRepository {
    var feed: [ActivityItem] = []
    /// Answered in order, when a test scripts its pages; otherwise `feed`.
    var feedResponses: [ActivityCursorResponse] = []
    var beforeFeedResponse: (() async -> Void)?
    var commentsError: Error?
    var whileAdding: (() -> Void)?
    var likeResult: Result<LikeState, Error> = .success(LikeState(liked: true, likeCount: 1))
    var likes: [(post: ActivityPostRef, liked: Bool)] = []
    var comments: [PostComment] = []
    var nextComment: PostComment?
    var sent: [String] = []
    var deleted: [Int] = []

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] {
        []
    }

    func feedPage(pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        guard !feedResponses.isEmpty else {
            return ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: feed)
        }
        let response = feedResponses.removeFirst()
        await beforeFeedResponse?()
        return response
    }

    func setLike(post: ActivityPostRef, liked: Bool) async throws -> LikeState {
        likes.append((post, liked))
        return try likeResult.get()
    }

    func comments(post _: ActivityPostRef) async throws -> [PostComment] {
        if let commentsError {
            throw commentsError
        }
        return comments
    }

    func addComment(post _: ActivityPostRef, body: String) async throws -> PostComment {
        guard let nextComment else { throw URLError(.notConnectedToInternet) }
        whileAdding?()
        sent.append(body)
        return nextComment
    }

    func deleteComment(id: Int) async throws {
        deleted.append(id)
    }
}

/// Only `owned(by:)` is ever called; the rest of the protocol is out of these tests' way.
private final class OwnerRecordingDiaryRepository: DiaryRepository {
    var owners: [String] = []

    func owned(by username: String) -> DiaryRepository {
        owners.append(username)
        return self
    }

    func list(tag _: String?) async throws -> [DiaryEntry] {
        []
    }

    func detail(id _: Int) async throws -> DiaryEntry {
        throw URLError(.badURL)
    }

    func create(_: DiaryEntryWriteRequest) async throws -> DiaryEntry {
        throw URLError(.badURL)
    }

    func setLike(entryId _: Int, liked _: Bool) async throws -> LikeState {
        throw URLError(.badURL)
    }

    func tags(query _: String) async throws -> [DiaryTagSuggestion] {
        []
    }
}
