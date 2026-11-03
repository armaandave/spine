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

    /// The comments to show. A spoiler log hides them until it's revealed, except ones just sent from this page.
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
            // Only the like: a comment sent meanwhile still counts.
            engagement.hasLiked = before.hasLiked
            engagement.likeCount = before.likeCount
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
            // Counted from the post's total: the list is short if the others didn't load.
            update(commentCount: engagement.commentCount + 1)
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
            update(commentCount: max(0, engagement.commentCount - 1))
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
    var spacing: CGFloat = 10
    var font: Font = .system(size: 15, weight: .bold)

    var body: some View {
        NavigationLink {
            UserProfileDestination(username: user.username)
        } label: {
            HStack(spacing: spacing) {
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

/// A round speech bubble, drawn to match the heart: the system's bubbles are wider than its heart and read lighter.
struct CommentBubbleShape: Shape {
    func path(in rect: CGRect) -> Path {
        let radius = min(rect.width, rect.height) / 2
        var path = Path()
        path.addArc(
            center: CGPoint(x: rect.midX, y: rect.midY),
            radius: radius,
            startAngle: .degrees(150),
            endAngle: .degrees(112),
            clockwise: false
        )
        path.addLine(to: CGPoint(x: rect.midX - radius * 0.98, y: rect.midY + radius * 0.98))
        path.closeSubpath()
        return path
    }
}

/// A post's like and comment counts on a feed row, just to read: liking and commenting happen on the post's page, which
/// a tap anywhere on the row opens. A count of zero shows just the icon.
struct PostEngagementSummary: View {
    let engagement: PostEngagement

    private let tint = Color.white.opacity(0.5)

    static func accessibilityText(for engagement: PostEngagement) -> String {
        let likes = engagement.likeCount == 1 ? "1 like" : "\(engagement.likeCount.formatted()) likes"
        return "\(likes), \(PostCommentsSection.countText(engagement.commentCount))"
    }

    var body: some View {
        HStack(spacing: 12) {
            item(count: engagement.likeCount) {
                Image(systemName: engagement.hasLiked ? "heart.fill" : "heart")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(tint)
            }

            item(count: engagement.commentCount) {
                CommentBubbleShape()
                    .stroke(tint, style: StrokeStyle(lineWidth: 1.2, lineJoin: .round))
                    .frame(width: 12.5, height: 12.5)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityText(for: engagement))
    }

    private func item(count: Int, @ViewBuilder icon: () -> some View) -> some View {
        HStack(spacing: 3) {
            icon()
            if count > 0 {
                Text(count.formatted())
                    .font(.system(size: 12, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(tint)
            }
        }
    }
}

/// The comments under a post, as its page shows them.
struct PostCommentsSection: View {
    let model: PostEngagementModel
    /// Spoiler logs keep their comments hidden until the page's reveal.
    var hidesComments = false

    static func countText(_ count: Int) -> String {
        count == 1 ? "1 comment" : "\(count.formatted()) comments"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if model.engagement.commentCount > 0 {
                Text(Self.countText(model.engagement.commentCount))
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
            }

            comments
        }
    }

    @ViewBuilder
    private var comments: some View {
        if !hidesComments, model.isLoadingComments, model.comments.isEmpty, model.engagement.commentCount > 0 {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity)
        } else {
            VStack(alignment: .leading, spacing: 14) {
                if !hidesComments, let error = model.commentsErrorMessage {
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
                }

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

/// The bar pinned under a post's page: the heart and the comment box side by side in glass, in reach of the thumb.
struct PostCommentBar: View {
    /// A little light in the glass: the page behind it is too dark and plain to show any of its own.
    private static let glassTint = Color.white.opacity(0.1)

    @Bindable var model: PostEngagementModel
    let isFocused: FocusState<Bool>.Binding

    private var showsSend: Bool {
        model.canSend || model.isSending
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let error = model.actionErrorMessage {
                Text(error)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red)
            }

            GlassEffectContainer(spacing: 8) {
                HStack(alignment: .bottom, spacing: 8) {
                    likeButton

                    TextField("Add a comment…", text: $model.draft, axis: .vertical)
                        .lineLimit(1...5)
                        .font(.system(size: 16))
                        .focused(isFocused)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .frame(minHeight: 44)
                        .glassEffect(.regular.tint(Self.glassTint), in: .rect(cornerRadius: 22))
                        .accessibilityIdentifier("post.comment-field")

                    if showsSend {
                        sendButton
                    }
                }
            }
            .animation(.smooth(duration: 0.25), value: showsSend)
            .shadow(color: .black.opacity(0.4), radius: 14, y: 6)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .sensoryFeedback(.success, trigger: model.sentCount)
    }

    private var likeButton: some View {
        let hasLiked = model.engagement.hasLiked
        return Button {
            // On the tap itself, so it never waits on the server.
            UIImpactFeedbackGenerator(style: hasLiked ? .light : .medium).impactOccurred()
            Task { await model.toggleLike() }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: hasLiked ? "heart.fill" : "heart")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(hasLiked ? AnyShapeStyle(.pink) : AnyShapeStyle(.primary))
                    .symbolEffect(.bounce, value: hasLiked)
                if model.engagement.likeCount > 0 {
                    Text(model.engagement.likeCount.formatted())
                        .font(.system(size: 15, weight: .semibold))
                        .monospacedDigit()
                }
            }
            // With no count it's a circle.
            .padding(.horizontal, model.engagement.likeCount > 0 ? 13 : 0)
            .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.plain)
        .contentShape(.capsule)
        .glassEffect(.regular.tint(hasLiked ? .pink.opacity(0.22) : Self.glassTint).interactive(), in: .capsule)
        .accessibilityLabel(hasLiked ? "Unlike" : "Like")
        .accessibilityValue("\(model.engagement.likeCount) likes")
        .accessibilityIdentifier("post.like")
    }

    private var sendButton: some View {
        Button {
            Task { await model.send() }
        } label: {
            Group {
                if model.isSending {
                    ProgressView()
                        .tint(.black)
                } else {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(.black)
                }
            }
            .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .contentShape(.circle)
        .glassEffect(.regular.tint(.white).interactive(), in: .circle)
        .disabled(model.isSending)
        .accessibilityLabel("Send comment")
        .accessibilityIdentifier("post.comment-send")
    }
}
