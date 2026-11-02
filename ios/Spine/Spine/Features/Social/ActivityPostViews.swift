import SwiftUI

/// A post's like and comments, on its own page: a log's page or a day's progress page.
@MainActor
@Observable
final class PostEngagementModel {
    let post: ActivityPostRef
    private(set) var engagement = PostEngagement(hasLiked: false, likeCount: 0, commentCount: 0)
    private(set) var comments: [PostComment] = []
    private(set) var isLoadingComments = false
    private(set) var isSending = false
    /// Counts sent comments, so the page can scroll to the new one and play its haptic.
    private(set) var sentCount = 0
    var draft = ""
    var commentsErrorMessage: String?
    var actionErrorMessage: String?

    private let repository: ActivityRepository
    private let onUnauthorized: () -> Void
    private var isChangingLike = false
    private var hasLoadedComments = false
    /// Comments sent from this page: always in plain view, and kept if an older load lands after them.
    private var sentCommentIds = Set<Int>()

    init(post: ActivityPostRef, repository: ActivityRepository, onUnauthorized: @escaping () -> Void) {
        self.post = post
        self.repository = repository
        self.onUnauthorized = onUnauthorized
    }

    var canSend: Bool {
        !isSending && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    func seed(_ engagement: PostEngagement) {
        self.engagement = engagement
    }

    /// Returning to the page loads them if leaving it cancelled the first try.
    func loadCommentsIfNeeded() async {
        guard !hasLoadedComments else { return }
        await loadComments()
    }

    /// The comments to show. A spoiler log hides everyone else's until it's revealed, but never your own.
    func visibleComments(hidingOthers: Bool) -> [PostComment] {
        hidingOthers ? comments.filter { sentCommentIds.contains($0.id) } : comments
    }

    func loadComments() async {
        isLoadingComments = true
        commentsErrorMessage = nil
        defer { isLoadingComments = false }

        do {
            let loaded = try await repository.comments(post: post)
            let loadedIds = Set(loaded.map(\.id))
            comments = loaded + comments.filter { sentCommentIds.contains($0.id) && !loadedIds.contains($0.id) }
            hasLoadedComments = true
            update(commentCount: comments.count)
        } catch is CancellationError {
            return
        } catch {
            commentsErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func toggleLike() async {
        guard !isChangingLike else { return }
        isChangingLike = true
        defer { isChangingLike = false }
        let before = engagement
        engagement = before.toggledLike
        actionErrorMessage = nil

        do {
            let state = try await repository.setLike(post: post, liked: engagement.hasLiked)
            engagement = engagement.applying(state)
            ActivityPostChange.post(post, engagement: engagement)
        } catch {
            engagement = before
            actionErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func send() async {
        guard canSend else { return }
        isSending = true
        actionErrorMessage = nil
        defer { isSending = false }
        // The box clears now, so anything typed while this sends is the next comment.
        let body = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        draft = ""

        do {
            let comment = try await repository.addComment(post: post, body: body)
            sentCommentIds.insert(comment.id)
            comments.append(comment)
            update(commentCount: comments.count)
            sentCount += 1
        } catch {
            if draft.isEmpty {
                draft = body
            }
            actionErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func delete(_ comment: PostComment) async {
        actionErrorMessage = nil
        do {
            try await repository.deleteComment(id: comment.id)
            comments.removeAll { $0.id == comment.id }
            update(commentCount: comments.count)
        } catch {
            actionErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func update(commentCount: Int) {
        guard engagement.commentCount != commentCount else { return }
        engagement.commentCount = commentCount
        ActivityPostChange.post(post, engagement: engagement)
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

/// A person's picture and username; tapping it opens their profile.
struct PostAuthorLink: View {
    let user: UserSummary
    var avatarSize: CGFloat = 34
    var font: Font = .system(size: 15, weight: .bold)

    var body: some View {
        NavigationLink {
            UserProfileDestination(username: user.username)
        } label: {
            HStack(spacing: 10) {
                UserAvatar(urlString: user.avatarUrl, size: avatarSize)
                Text(user.username)
                    .font(font)
                    .foregroundStyle(.white)
                    .lineLimit(1)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(user.username)
        .accessibilityHint("Opens profile")
    }
}

/// The heart and comment bubble, with their counts. A count of zero shows just the icon.
struct PostActions: View {
    let engagement: PostEngagement
    var iconSize: CGFloat = 20
    var spacing: CGFloat = 12
    let onToggleLike: () -> Void
    let onComment: () -> Void

    var body: some View {
        HStack(spacing: spacing) {
            Button(action: onToggleLike) {
                label(
                    systemName: engagement.hasLiked ? "heart.fill" : "heart",
                    count: engagement.likeCount,
                    iconStyle: engagement.hasLiked ? AnyShapeStyle(.pink) : AnyShapeStyle(.white)
                )
            }
            .buttonStyle(.plain)
            .sensoryFeedback(trigger: engagement.hasLiked) { _, isLiked in
                isLiked ? SensoryFeedback.impact(weight: .medium) : .selection
            }
            .accessibilityLabel(engagement.hasLiked ? "Unlike" : "Like")
            .accessibilityValue("\(engagement.likeCount) likes")

            Button(action: onComment) {
                label(systemName: "bubble.right", count: engagement.commentCount, iconStyle: AnyShapeStyle(.white))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Comment")
            .accessibilityValue("\(engagement.commentCount) comments")
        }
    }

    private func label(systemName: String, count: Int, iconStyle: AnyShapeStyle) -> some View {
        HStack(spacing: 5) {
            Image(systemName: systemName)
                .font(.system(size: iconSize, weight: .medium))
                .foregroundStyle(iconStyle)
                .contentTransition(.symbolEffect(.replace))
            if count > 0 {
                Text(count.formatted())
                    .font(.system(size: 14, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white)
            }
        }
        .frame(minWidth: 44, minHeight: 44, alignment: .leading)
        .contentShape(Rectangle())
    }
}

/// The like and comment row and the comments under it, as a post's page shows them.
struct PostEngagementSection: View {
    let model: PostEngagementModel
    /// Spoiler logs keep their comments hidden until the page's reveal.
    var hidesComments = false
    let onComment: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(spacing: 0) {
                Divider().overlay(.white.opacity(0.1))
                PostActions(
                    engagement: model.engagement,
                    iconSize: 22,
                    spacing: 18,
                    onToggleLike: { Task { await model.toggleLike() } },
                    onComment: onComment
                )
                .frame(maxWidth: .infinity, alignment: .leading)
                Divider().overlay(.white.opacity(0.1))
            }

            comments
        }
    }

    @ViewBuilder
    private var comments: some View {
        if !hidesComments, let error = model.commentsErrorMessage, model.comments.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.62))
                Button("Retry") {
                    Task { await model.loadComments() }
                }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white.opacity(0.86))
            }
        } else if !hidesComments, model.isLoadingComments, model.comments.isEmpty, model.engagement.commentCount > 0 {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity)
        } else {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(model.visibleComments(hidingOthers: hidesComments)) { comment in
                    PostCommentRow(comment: comment) {
                        Task { await model.delete(comment) }
                    }
                }
            }
        }
    }
}

private struct PostCommentRow: View {
    let comment: PostComment
    let onDelete: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            NavigationLink {
                UserProfileDestination(username: comment.user.username)
            } label: {
                UserAvatar(urlString: comment.user.avatarUrl, size: 30)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(comment.user.username)
            .accessibilityHint("Opens profile")

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Text(comment.user.username)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white)
                    if let time = ActivityTimestampFormatter.shortText(from: comment.createdAt) {
                        Text(time)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(.white.opacity(0.62))
                    }
                }
                Text(comment.body)
                    .font(.system(size: 15))
                    .foregroundStyle(.white.opacity(0.88))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .contentShape(Rectangle())
        .contextMenu {
            if comment.canDelete {
                Button("Delete Comment", systemImage: "trash", role: .destructive, action: onDelete)
            }
        }
    }
}

/// The comment box pinned under a post's page.
struct PostCommentBar: View {
    @Bindable var model: PostEngagementModel
    let isFocused: FocusState<Bool>.Binding

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let error = model.actionErrorMessage {
                Text(error)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red)
            }

            HStack(alignment: .bottom, spacing: 8) {
                TextField("Add a comment…", text: $model.draft, axis: .vertical)
                    .lineLimit(1...5)
                    .font(.system(size: 16))
                    .focused(isFocused)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 11)
                    .glassEffect(.regular, in: .rect(cornerRadius: 22))
                    .accessibilityIdentifier("post.comment-field")

                if model.canSend || model.isSending {
                    Button {
                        Task { await model.send() }
                    } label: {
                        Group {
                            if model.isSending {
                                ProgressView()
                            } else {
                                Image(systemName: "arrow.up")
                                    .font(.system(size: 17, weight: .bold))
                            }
                        }
                        .frame(width: 44, height: 44)
                    }
                    .buttonStyle(.plain)
                    .contentShape(.circle)
                    .glassEffect(.regular.interactive(), in: .circle)
                    .disabled(model.isSending)
                    .accessibilityLabel("Send comment")
                    .accessibilityIdentifier("post.comment-send")
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .sensoryFeedback(.success, trigger: model.sentCount)
    }
}
