import XCTest
@testable import Spine

final class ActivityPostTests: XCTestCase {
    private func activity(_ json: String) throws -> ActivityItem {
        try JSONDecoder.api.decode(ActivityItem.self, from: Data(json.utf8))
    }

    private func log(
        id: Int = 7,
        day: String = "2026-11-01",
        hasLiked: Bool = false,
        likeCount: Int = 2,
        commentCount: Int = 1,
        isRewatch: Bool = false
    ) throws -> ActivityItem {
        try activity("""
        {
          "id": \(id), "type": "diary_created", "created_at": "2026-11-01T12:00:00Z", "consumed_at": "\(day)",
          "actor": {"id": 2, "username": "maya", "display_name": "Maya", "avatar_url": null},
          "media": {"ref": {"source": "tmdb", "media_type": "movie", "media_id": "1"}, "title": "Past Lives"},
          "person": null,
          "object": {"type": "diary", "id": \(id), "rating": "4.5", "liked": true, "is_rewatch": \(isRewatch)},
          "viewer": {"can_view": true, "has_liked": \(hasLiked)},
          "like_count": \(likeCount), "comment_count": \(commentCount),
          "review_title": "", "review": "Wrecked me.", "contains_spoilers": true
        }
        """)
    }

    private func progress(id: Int = 9, day: String = "2026-11-01", summary: String = "watched 4 episodes") throws -> ActivityItem {
        try activity("""
        {
          "id": -\(id), "type": "progress_updated", "created_at": "2026-11-01T12:00:00Z", "consumed_at": "\(day)",
          "actor": {"id": 3, "username": "jonah", "display_name": "Jonah", "avatar_url": null},
          "media": {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "2"}, "title": "Severance"},
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

    func testAProgressPostsDetailIsItsSummary() throws {
        XCTAssertEqual(ActivityFeedPresentation.progressHeadline(for: try progress()), "Watched 4 episodes")
        XCTAssertEqual(
            ActivityFeedPresentation.progressHeadline(for: try progress(summary: "started reading")),
            "Started reading"
        )
    }

    // MARK: Rows

    func testALogsMarksAreItsStarsAndHeartWithNoReviewMark() throws {
        let summary = ActivityFeedPresentation.ratingSummary(for: try log())

        XCTAssertEqual(summary.starSymbols.count, 5)
        XCTAssertEqual(summary.starSymbols.last, "star.leadinghalf.filled")
        XCTAssertTrue(summary.liked)
        XCTAssertFalse(summary.hasReview, "The review has its own line")
    }

    func testTheRewatchMarkShowsOnlyOnARewatch() throws {
        XCTAssertFalse(ActivityFeedPresentation.isRewatch(try log()))
        XCTAssertTrue(ActivityFeedPresentation.isRewatch(try log(isRewatch: true)))
        XCTAssertFalse(ActivityFeedPresentation.isRewatch(try progress()))
    }

    // MARK: Day groups

    private func describe(_ entries: [ActivityFeedEntry]) -> [String] {
        entries.map { entry in
            switch entry {
            case let .day(_, title, isFirst):
                "\(title)\(isFirst ? " first" : "")"
            case let .post(activity, isFirst, isLast):
                "\(activity.id)\(isFirst ? " top" : "")\(isLast ? " bottom" : "")"
            }
        }
    }

    func testPostsGroupUnderTheirDayAsOnePanel() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let now = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 11, day: 2, hour: 12)))

        let entries = ActivityFeedEntry.entries(
            from: [
                try log(id: 1, day: "2026-11-02"),
                try progress(id: 9, day: "2026-11-02"),
                try log(id: 2, day: "2026-11-02"),
                try log(id: 3, day: "2026-11-01"),
                try log(id: 4, day: "2026-10-30"),
                try progress(id: 8, day: "2026-10-30"),
            ],
            now: now
        )

        XCTAssertEqual(describe(entries), [
            "Today first", "1 top", "-9", "2 bottom",
            "Yesterday", "3 top bottom",
            "Oct 30", "4 top", "-8 bottom",
        ])
        XCTAssertEqual(Set(entries.map(\.id)).count, entries.count, "Every lazy item has its own identity")
    }

    func testAnEmptyFeedHasNoHeaders() {
        XCTAssertTrue(ActivityFeedEntry.entries(from: []).isEmpty)
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
