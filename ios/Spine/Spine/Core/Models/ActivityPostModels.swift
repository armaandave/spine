import Foundation

/// What a like or comment attaches to: a log, or a day's progress (activity contract AC-6).
enum ActivityPostRef: Hashable, Identifiable {
    case log(Int)
    case progress(Int)

    var id: String {
        switch self {
        case let .log(id): "log-\(id)"
        case let .progress(id): "progress-\(id)"
        }
    }
}

/// A post's likes and comments as the viewer sees them.
struct PostEngagement: Equatable {
    var hasLiked: Bool
    var likeCount: Int
    var commentCount: Int

    var toggledLike: PostEngagement {
        var toggled = self
        toggled.hasLiked.toggle()
        toggled.likeCount = max(0, likeCount + (toggled.hasLiked ? 1 : -1))
        return toggled
    }

    func applying(_ state: LikeState) -> PostEngagement {
        var applied = self
        applied.hasLiked = state.liked
        applied.likeCount = state.likeCount
        return applied
    }
}

/// A comment, or a reply to one. Replies go one level deep: a reply's parent is always a top-level comment.
struct PostComment: Codable, Identifiable, Equatable {
    let id: Int
    let user: UserSummary
    var body: String
    let createdAt: String?
    var canDelete: Bool
    var parentId: Int? = nil
    /// A deleted comment with replies stays, emptied, as "Comment deleted" above them.
    var isDeleted: Bool? = nil

    var deleted: Bool { isDeleted == true }
}

/// A top-level comment and its replies, oldest first.
struct PostCommentThread: Identifiable, Equatable {
    let comment: PostComment
    let replies: [PostComment]

    var id: Int { comment.id }

    /// Groups comments into threads. A reply whose comment isn't in the list shows as its own thread.
    static func threads(from comments: [PostComment]) -> [PostCommentThread] {
        let ids = Set(comments.map(\.id))
        let repliesByParent = Dictionary(grouping: comments.filter { $0.parentId.map(ids.contains) == true }) {
            $0.parentId ?? 0
        }
        return comments
            .filter { $0.parentId.map(ids.contains) != true }
            .map { PostCommentThread(comment: $0, replies: repliesByParent[$0.id] ?? []) }
    }
}

struct PostCommentRequest: Encodable {
    let body: String
    var parentId: Int? = nil
}

extension ActivityItem {
    var post: ActivityPostRef? {
        switch object.type {
        case "diary": .log(object.id)
        case "progress_day": .progress(object.id)
        default: nil
        }
    }

    var engagement: PostEngagement {
        get {
            PostEngagement(
                hasLiked: viewer?.hasLiked ?? false,
                likeCount: likeCount ?? 0,
                commentCount: commentCount ?? 0
            )
        }
        set {
            viewer = ActivityViewer(hasLiked: newValue.hasLiked)
            likeCount = newValue.likeCount
            commentCount = newValue.commentCount
        }
    }
}

extension Notification.Name {
    /// A like or comment changed a post, so every screen showing it can update in place.
    static let activityPostDidChange = Notification.Name("activityPostDidChange")
}

enum ActivityPostChange {
    static func post(_ post: ActivityPostRef, engagement: PostEngagement) {
        NotificationCenter.default.post(
            name: .activityPostDidChange,
            object: nil,
            userInfo: ["post": post, "engagement": engagement]
        )
    }

    static func parse(_ notification: Notification) -> (post: ActivityPostRef, engagement: PostEngagement)? {
        guard let post = notification.userInfo?["post"] as? ActivityPostRef,
              let engagement = notification.userInfo?["engagement"] as? PostEngagement
        else { return nil }
        return (post, engagement)
    }
}
