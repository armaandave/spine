import Nuke
import SwiftUI

extension Notification.Name {
    static let diaryEntriesDidChange = Notification.Name("diaryEntriesDidChange")
}

/// How late a log can be dated. Calendar-dated logs, TV logs at every level included, stop at today (TV-407);
/// other types keep a time and have no cap.
enum DiaryLogDateLimit {
    static func latest(for ref: MediaRef?, now: Date = Date()) -> Date {
        ref?.usesCalendarLogDate == true ? now : .distantFuture
    }

    static func range(for ref: MediaRef?, now: Date = Date()) -> ClosedRange<Date> {
        Date.distantPast...latest(for: ref, now: now)
    }
}

@MainActor
@Observable
final class DiaryLogDetailViewModel {
    var entry: DiaryEntry?
    var mediaDetail: MediaDetail?
    var isLoading = true
    var isDeleting = false
    var deleteErrorMessage: String?
    var errorMessage: String?
    var isReviewRevealed = false
    /// The log's likes and comments.
    let engagement: PostEngagementModel
    /// Counts the times the show changed after the delete confirmation: the page reloaded and asks again with the
    /// new count (TV-406, TV-705).
    private(set) var deleteReconfirmations = 0
    /// One mutation ID per confirmed delete, kept for its retries, so a TV log deletion applies at most once (TV-705).
    private var deleteMutationId: UUID?

    private let entryId: Int
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        activityRepository: ActivityRepository? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.entryId = entryId
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        // The app's own repository unless given: looked up here, on the main actor, not in a default argument.
        engagement = PostEngagementModel(
            post: .log(entryId),
            repository: activityRepository ?? AppRepositories.current().activity,
            onUnauthorized: onUnauthorized
        )
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loadedEntry = try await diaryRepository.detail(id: entryId)
            await loadMediaDetail(for: loadedEntry.media.ref)
            entry = loadedEntry
            engagement.seed(PostEngagement(
                hasLiked: loadedEntry.viewerHasLiked,
                likeCount: loadedEntry.likeCount,
                commentCount: loadedEntry.commentCount ?? 0
            ))
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save(_ request: DiaryEntryUpdateRequest) async -> Bool {
        guard let entry else { return false }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            self.entry = try await diaryRepository.update(id: entry.id, request: request)
            if let ref = self.entry?.media.ref {
                await loadMediaDetail(for: ref)
                MediaStateChange.post(ref: ref)
            }
            NotificationCenter.default.post(name: .diaryEntriesDidChange, object: nil)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    /// The delete confirmation's title. A TV log that unmarks episodes says how many (TV-406).
    var deletePrompt: String {
        entry?.trackedLog.flatMap { TVTrackingPresentation.deleteLogPrompt(unmarksCount: $0.unmarksCount) }
            ?? "Delete this diary entry?"
    }

    /// Starts a new confirmed delete: its retries share one mutation ID.
    func confirmDelete() async -> Bool {
        deleteMutationId = nil
        return await delete()
    }

    func delete() async -> Bool {
        guard !isDeleting, let entry else { return false }
        isDeleting = true
        errorMessage = nil
        deleteErrorMessage = nil
        defer { isDeleting = false }

        do {
            let ref = entry.media.ref
            if let tvLog = entry.trackedLog {
                // The version the dialog showed: if the show changed since, nothing is deleted (TV-705).
                let mutationId = deleteMutationId ?? UUID()
                deleteMutationId = mutationId
                try await diaryRepository.delete(id: entry.id, expectedVersion: tvLog.stateVersion, mutationId: mutationId)
            } else {
                try await diaryRepository.delete(id: entry.id)
            }
            deleteMutationId = nil
            NotificationCenter.default.post(name: .diaryEntriesDidChange, object: nil)
            MediaStateChange.post(ref: ref)
            return true
        } catch let error as APIError where error.apiErrorCode == "tv_state_changed" {
            // The show changed since the dialog: refresh the log's counts and ask again.
            deleteMutationId = nil
            do {
                self.entry = try await diaryRepository.detail(id: entry.id)
                deleteReconfirmations += 1
            } catch {
                deleteErrorMessage = error.localizedDescription
                if case APIError.unauthorized = error {
                    onUnauthorized()
                }
            }
            return false
        } catch {
            errorMessage = error.localizedDescription
            deleteErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    private func loadMediaDetail(for ref: MediaRef) async {
        do {
            mediaDetail = try await mediaRepository.detail(ref: ref)
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct DiaryLogDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var viewModel: DiaryLogDetailViewModel
    @State private var presentedRef: MediaRef?
    @State private var isEditing = false
    @State private var isDeleteConfirmationPresented = false
    @State private var edgeDragOffset: CGFloat = 0
    @State private var topSafeAreaInset: CGFloat = 0
    @FocusState private var isCommentFocused: Bool

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void
    private let songPreview = SongPreviewPlayer.shared

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: DiaryLogDetailViewModel(
            entryId: entryId,
            diaryRepository: diaryRepository,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            PostPageBackground(url: pageArtURL, accentHex: pageAccentHex)

            ScrollViewReader { proxy in
                ScrollView(showsIndicators: false) {
                    Group {
                        if viewModel.isLoading, viewModel.entry == nil {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 520)
                        } else if let entry = viewModel.entry {
                            content(entry)
                        } else if let error = viewModel.errorMessage {
                            ContentUnavailableView("Could not load log", systemImage: "exclamationmark.triangle", description: Text(error))
                                .foregroundStyle(.white)
                                .padding()
                        }
                    }
                    .spineContentTransition(value: contentPhase)
                    .padding(.bottom, 24)
                }
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.immediately)
                .scrollEdgeEffectStyle(.hard, for: .bottom)
                .ignoresSafeArea(edges: .top)
                .onChange(of: viewModel.engagement.sentCount) {
                    PostCommentsSection.scrollToSent(viewModel.engagement, with: proxy)
                }
            }

            GlassEffectContainer(spacing: 8) {
                HStack(spacing: 8) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 17, weight: .bold))
                            .frame(width: 38, height: 38)
                    }
                    .buttonStyle(.plain)
                    .contentShape(.circle)
                    .glassEffect(.regular.interactive(), in: .circle)
                    .accessibilityLabel("Back")

                    Spacer()

                    if canManageEntry {
                        Button {
                            isEditing = true
                        } label: {
                            Text("Edit")
                                .font(.system(size: 14, weight: .heavy))
                                .padding(.horizontal, 13)
                                .frame(height: 38)
                        }
                        .buttonStyle(.plain)
                        .contentShape(.capsule)
                        .glassEffect(.regular.interactive(), in: .capsule)
                        .disabled(viewModel.isDeleting)

                        Button(role: .destructive) {
                            isDeleteConfirmationPresented = true
                        } label: {
                            Image(systemName: "trash")
                                .font(.system(size: 16, weight: .bold))
                                .frame(width: 38, height: 38)
                        }
                        .buttonStyle(.plain)
                        .contentShape(.circle)
                        .glassEffect(.regular.interactive(), in: .circle)
                        .accessibilityLabel("Delete")
                        .disabled(viewModel.isDeleting)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
        }
        // Inside the bar's inset, so the swipe's edge doesn't cover the heart, and it ends where the favorite's play
        // circle starts: 16 of page inset and 8 of button padding.
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 24)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .safeAreaBar(edge: .bottom) {
            if viewModel.entry != nil {
                PostCommentBar(model: viewModel.engagement, isFocused: $isCommentFocused)
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.safeAreaInsets.top } action: { topSafeAreaInset = $0 }
        .navigationBarBackButtonHidden()
        .toolbar(.hidden, for: .tabBar)
        .dismissExplorationOnReturnHome()
        .offset(x: edgeDragOffset)
        .task {
            if viewModel.entry == nil {
                await viewModel.load()
            }
            if viewModel.entry != nil {
                await viewModel.engagement.loadCommentsIfNeeded()
            }
        }
        // A cover or sheet over the page doesn't count as disappearing, and takes the stop button out of reach.
        .onChange(of: presentedRef != nil || isEditing) { _, isCovered in
            if isCovered { songPreview.stop() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { songPreview.stop() }
        }
        .onDisappear {
            songPreview.close()
        }
        .fullScreenCover(item: $presentedRef) { ref in
            mediaDestination(ref)
        }
        .sheet(isPresented: $isEditing) {
            if let entry = viewModel.entry {
                DiaryLogEditSheet(
                    entry: entry,
                    diaryRepository: diaryRepository,
                    mediaDetail: viewModel.mediaDetail,
                    mediaRepository: mediaRepository,
                    backdropURL: viewModel.mediaDetail?.displayBackdropURL,
                    errorMessage: { viewModel.errorMessage }
                ) { request in
                    await viewModel.save(request)
                }
            }
        }
        .alert("Could not delete log", isPresented: Binding(
            get: { viewModel.deleteErrorMessage != nil },
            set: { if !$0 { viewModel.deleteErrorMessage = nil } }
        )) {
            Button("Try Again") { Task { if await viewModel.delete() { dismiss() } } }
            Button("Cancel", role: .cancel) { viewModel.deleteErrorMessage = nil }
        } message: { Text(viewModel.deleteErrorMessage ?? "Could not delete the log.") }
        .onChange(of: viewModel.deleteReconfirmations) {
            isDeleteConfirmationPresented = true
        }
        .confirmationDialog(viewModel.deletePrompt, isPresented: $isDeleteConfirmationPresented, titleVisibility: .visible) {
            Button(viewModel.entry?.trackedLog == nil ? "Delete Entry" : "Delete Log", role: .destructive) {
                Task {
                    if await viewModel.confirmDelete() {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(viewModel.entry?.media.ref.mediaType == "game"
                 ? "A direct or older completion also deletes its playthrough and all progress. Deleting the current tracked completion reopens it with latest progress. This cannot be undone."
                 : "This cannot be undone.")
        }
    }

    private var canManageEntry: Bool {
        guard let entry = viewModel.entry, let currentUserId else { return false }
        return entry.user.id == currentUserId
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.entry != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    /// The art behind the whole page: the log's own poster, which the title's details never replace, so the page does
    /// not change tint when they arrive.
    private var pageArtURL: URL? {
        guard let entry = viewModel.entry else { return nil }
        return PostPageStyle.artURL(
            posterURL: entry.media.displayPosterURL,
            detailPosterURL: viewModel.mediaDetail?.displayPosterURL
        )
    }

    private var pageAccentHex: String? {
        viewModel.mediaDetail?.posterAccentColor
    }

    private func content(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            hero(entry)
            logSection(entry)
        }
    }

    private func hero(_ entry: DiaryEntry) -> some View {
        HStack(alignment: .bottom, spacing: 14) {
            PostHeroPoster(
                url: viewModel.mediaDetail?.displayPosterURL ?? entry.media.displayPosterURL,
                title: entry.media.title,
                mediaType: entry.media.ref.mediaType,
                orientation: viewModel.mediaDetail?.posterOrientation ?? entry.media.posterOrientation
            )

            VStack(alignment: .leading, spacing: 8) {
                PostHeroAuthorLine(user: entry.user)

                PostHeroTypeLabel(text: mediaTypeLabel(entry.media.ref.mediaType))

                if let logged = DiaryLogFormat.dateLabel(entry.consumedAt) {
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            if entry.isTrueReread ?? entry.isRewatch {
                                Image(systemName: "arrow.clockwise")
                                    .font(.system(size: 12, weight: .bold))
                                    .foregroundStyle(.white.opacity(0.86))
                                    .accessibilityLabel(entry.media.ref.repeatLabel)
                            }

                            Text("Logged \(logged)")
                                .font(PostPageStyle.loggedFont)
                                .foregroundStyle(.white.opacity(0.86))
                        }

                        if let age = DiaryLogFormat.ageLabel(entry.consumedAt) {
                            Text(age)
                                .font(PostPageStyle.timeFont)
                                .foregroundStyle(.white.opacity(0.72))
                        }
                    }
                }

                if entry.media.ref.mediaType == "game" {
                    let progress = GameProgressValues(totalMinutes: entry.totalMinutes, percentage: entry.completion).summary
                    if !progress.isEmpty { Text("At completion: \(progress)").font(.subheadline) }
                }
                let parts = titleParts(TVTrackingPresentation.diaryTitle(for: entry.media))
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    if let detail = viewModel.mediaDetail {
                        MediaTitleDisplay(
                            detail: detail,
                            title: parts.title,
                            showsLogo: .constant(true),
                            font: PostPageStyle.titleFont,
                            lineLimit: 4,
                            minimumScaleFactor: 0.72,
                            maxLogoHeight: 48,
                            alignment: .leading,
                            onTap: { presentedRef = entry.media.ref },
                            onLongPress: nil
                        )
                    } else {
                        PostHeroTitle(text: parts.title)
                    }

                }

                if let metadata = heroMetadata(entry) {
                    Text(metadata)
                        .font(PostPageStyle.subtitleFont)
                        .foregroundStyle(.white.opacity(0.78))
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .onTapGesture { presentedRef = entry.media.ref }
        .accessibilityLabel("View \(entry.media.displayTitle)")
        .accessibilityAddTraits(.isButton)
        .padding(.horizontal, 16)
        // The top bar ends 50 pt below the safe area; the artwork starts 18 pt under it, whatever its shape.
        .padding(.top, topSafeAreaInset + 68)
        .padding(.bottom, 22)
    }

    private func logSection(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            if hasRatingOrLike(entry) {
                ratingLikeLine(entry)
            }

            if let favorite = entry.favorite, !hidesFavorite(entry) {
                favoriteLine(favorite)
            }

            if let title = clean(entry.reviewTitle) {
                Text(title)
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
            }

            reviewBody(entry)

            if !entry.tags.isEmpty {
                DiaryLogTagsView(
                    tags: entry.tags,
                    diaryRepository: Self.tagRepository(for: entry.user, base: diaryRepository, currentUserId: currentUserId),
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }

            PostHairline()
                .padding(.top, 8)

            PostCommentsSection(model: viewModel.engagement, hidesComments: hidesComments(entry))

            Color.clear
                .frame(height: 1)
                .id("post-end")
        }
        .padding(.horizontal, 16)
    }

    /// A tag page shows its owner's logs, so a tag on someone else's log opens theirs.
    static func tagRepository(for owner: UserSummary, base: DiaryRepository, currentUserId: Int?) -> DiaryRepository {
        owner.id == currentUserId ? base : base.owned(by: owner.username)
    }

    private func hasRatingOrLike(_ entry: DiaryEntry) -> Bool {
        clean(entry.rating) != nil || entry.liked
    }

    private func ratingLikeLine(_ entry: DiaryEntry) -> some View {
        PostRatingLine(rating: clean(entry.rating), liked: entry.liked)
    }

    /// "Favorite song" over a slim glass bar: a play button when the song has a preview, then its name. Episodes read
    /// "S1 E4  Title". Touch and hold opens a song in Apple Music.
    private func favoriteLine(_ favorite: LogFavorite) -> some View {
        let recording = favorite.recording(in: viewModel.mediaDetail)
        let previewURL = recording?.previewURL.flatMap { $0 == songPreview.failedURL ? nil : $0 }
        return VStack(alignment: .leading, spacing: 8) {
            Text(favorite.kind.label)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.56))

            HStack(spacing: 0) {
                if let previewURL {
                    SongPreviewButton(url: previewURL, title: favorite.title, player: songPreview)
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    if let number = favorite.numberLabel {
                        Text(number)
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                    Text(favorite.title)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                }
                .font(.system(size: 15, weight: .medium))
                .padding(.leading, previewURL == nil ? 14 : 2)
                .padding(.trailing, 14)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("diary-log.favorite")
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .glassEffect(.regular.tint(PostCommentBar.glassTint), in: .rect(cornerRadius: 14))
            .contentShape([.interaction, .contextMenuPreview], .rect(cornerRadius: 14))
            .contextMenu {
                if let appleMusicURL = recording?.appleMusicURL {
                    Link(destination: appleMusicURL) {
                        Label("Open in Apple Music", systemImage: "arrow.up.right")
                    }
                }
            }
        }
    }

    private func hidesFavorite(_ entry: DiaryEntry) -> Bool {
        entry.favorite?.isHidden(containsSpoilers: entry.containsSpoilers, revealed: viewModel.isReviewRevealed) == true
    }

    /// Comments on a spoiler log can spoil it too, so they wait for the same reveal.
    private func hidesComments(_ entry: DiaryEntry) -> Bool {
        entry.containsSpoilers && !viewModel.isReviewRevealed
    }

    @ViewBuilder
    private func reviewBody(_ entry: DiaryEntry) -> some View {
        let review = clean(entry.review)
        let hidesReview = review != nil && entry.containsSpoilers && !viewModel.isReviewRevealed
        let hidesAnyComment = hidesComments(entry)
            && viewModel.engagement.engagement.commentCount > viewModel.engagement.visibleComments(hidingOthers: true).count
        if hidesFavorite(entry) || hidesReview || hidesAnyComment {
            Button {
                viewModel.isReviewRevealed = true
            } label: {
                Label(hidesFavorite(entry) || hidesAnyComment ? "Reveal spoilers" : "Reveal spoiler review", systemImage: "eye.slash")
                    .font(.system(size: 14, weight: .heavy))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 18)
                    .background(.white.opacity(0.09), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            .buttonStyle(.plain)
        } else if let review {
            PostReviewBody(text: review)
        } else {
            Text("No review text.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    private func mediaDestination(_ ref: MediaRef) -> some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }

    private func heroMetadata(_ entry: DiaryEntry) -> String? {
        var parts = [titleParts(entry.media.displayTitle).year, DiaryLogFormat.year(viewModel.mediaDetail?.releaseDate), detailString(viewModel.mediaDetail, "runtime")]
            .compactMap { clean($0) }
        var seen = Set<String>()
        parts = parts.filter { seen.insert($0.lowercased()).inserted }
        return parts.isEmpty ? nil : parts.joined(separator: " - ")
    }

    private func detailString(_ detail: MediaDetail?, _ key: String) -> String? {
        guard let value = detail?.details?[key] else { return nil }
        switch value {
        case let .string(string):
            return clean(string)
        case let .number(number):
            return number.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(number))" : "\(number)"
        case let .bool(bool):
            return bool ? "Yes" : "No"
        default:
            return nil
        }
    }

    private func mediaTypeLabel(_ mediaType: String) -> String {
        mediaType == "tv" ? "TV" : mediaType.capitalized
    }

    private func titleParts(_ title: String) -> (title: String, year: String?) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasSuffix(")") else { return (trimmed, nil) }
        let suffix = String(trimmed.suffix(6))
        guard suffix.first == "(", suffix.last == ")" else { return (trimmed, nil) }
        let year = String(suffix.dropFirst().dropLast())
        guard year.count == 4, year.allSatisfy(\.isNumber) else { return (trimmed, nil) }
        return (String(trimmed.dropLast(7)), year)
    }

    private func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct DiaryLogDetailNavigationCover: View {
    let entryId: Int
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        NavigationStack {
            DiaryLogDetailView(
                entryId: entryId,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
    }
}

// MARK: - Post page style (a log's page and a day's progress page)

/// The numbers and rules both post pages share: one baked blurred art layer behind the whole page, a 120x180 poster
/// with the text column beside it, and the review, tags and comments below.
enum PostPageStyle {
    /// The baked art style behind the page.
    static let artStyle = BlurredArt.Style.page

    /// The black wash over the art: clear at the top, 0.14 at 45 percent, 0.48 at the bottom.
    static let washStops: [(opacity: Double, location: Double)] = [(0, 0), (0.14, 0.45), (0.48, 1)]

    static let chipHeight: CGFloat = 30
    static let posterShadowRadius: CGFloat = 22
    static let posterShadowOffset: CGFloat = 22

    static let loggedFont = Font.system(size: 13, weight: .heavy)
    static let timeFont = Font.system(size: 12, weight: .semibold)
    static let titleFont = Font.system(size: 30, weight: .black)
    static let subtitleFont = Font.system(size: 13, weight: .semibold)

    /// The art for a progress post: what its feed card draws (the poster, or an episode's still).
    static func artURL(for media: MediaSummary) -> URL? {
        URL(string: FeedCardPresenter.artURL(for: media) ?? "")
    }

    /// The art for a log: its own poster; the title's details only stand in when the log has none.
    static func artURL(posterURL: String?, detailPosterURL: String?) -> URL? {
        URL(string: posterURL ?? detailPosterURL ?? "")
    }

    /// The tint under the page: the row's own, the title's details only when the row has none, so it never changes
    /// once shown.
    static func accentHex(summary: String?, detail: String?) -> String? {
        summary ?? detail
    }

    /// The request the page's art loads, so a prefetch can ask for exactly the same bitmap.
    static func artRequest(url: URL?) -> ImageRequest? {
        BlurredArt.request(url: url, style: artStyle)
    }

    /// One filled star per whole point and a half star for a half: a 4.5 rating draws four and a half.
    static func starSymbols(rating: String) -> [String] {
        let value = Double(rating.ratingHalfSteps) / 2
        return (0..<5).compactMap { index in
            let remainder = value - Double(index)
            if remainder >= 1 { return "star.fill" }
            if remainder >= 0.5 { return "star.leadinghalf.filled" }
            return nil
        }
    }

    /// A review's paragraphs, split at blank lines.
    static func paragraphs(_ review: String) -> [String] {
        review
            .replacingOccurrences(of: "\r\n", with: "\n")
            .components(separatedBy: "\n\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}

/// The page behind a post: the title's baked blurred art over black, with a wash that deepens toward the bottom.
/// Nothing is blurred live, and nothing grey shows before the title loads.
struct PostPageBackground: View {
    let url: URL?
    let accentHex: String?

    var body: some View {
        ZStack {
            HomeDesign.Palette.pageBackground

            if url != nil || accentHex != nil {
                BlurredArtBackground(url: url, accentHex: accentHex, style: PostPageStyle.artStyle)
            }

            LinearGradient(
                stops: PostPageStyle.washStops.map { .init(color: .black.opacity($0.opacity), location: $0.location) },
                startPoint: .top,
                endPoint: .bottom
            )
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }
}

/// The 120x180 poster on a post page. It is one poster on a page that never repeats, so it takes a real shadow.
struct PostHeroPoster: View {
    let url: String?
    let title: String
    var mediaType: String?
    var orientation: PosterOrientation?

    var body: some View {
        MediaArtwork(url: url, title: title, slot: .postHero, mediaType: mediaType, orientation: orientation)
            .overlay {
                RoundedRectangle(cornerRadius: PosterSlot.postHero.cornerRadius, style: .continuous)
                    .strokeBorder(.white.opacity(0.18), lineWidth: 0.5)
            }
            .shadow(color: .black.opacity(0.5), radius: PostPageStyle.posterShadowRadius, y: PostPageStyle.posterShadowOffset)
    }
}

/// Who posted it: a 24 pt avatar (requested as a thumbnail) and the username; opens their profile.
struct PostHeroAuthorLine: View {
    let user: UserSummary

    var body: some View {
        NavigationLink {
            UserProfileDestination(username: user.username)
        } label: {
            HStack(spacing: 7) {
                SpineAsyncImage(
                    url: URL(string: user.avatarUrl ?? ""),
                    thumbnailSize: CGSize(width: 24, height: 24)
                ) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    } else {
                        Image(systemName: "person.fill")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.white.opacity(0.54))
                    }
                }
                .frame(width: 24, height: 24)
                .background(.white.opacity(0.10), in: Circle())
                .clipShape(Circle())
                .accessibilityHidden(true)

                Text(user.username)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(user.username)
        .accessibilityHint("Opens \(user.username)'s profile")
    }
}

/// "MOVIE", "TV", "BOOK": small, heavy, tracked.
struct PostHeroTypeLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .heavy))
            .tracking(0.88)
            .foregroundStyle(.white.opacity(0.72))
            .textCase(.uppercase)
    }
}

/// The title when it has no logo: 30 black, pulled in a little.
struct PostHeroTitle: View {
    let text: String

    var body: some View {
        Text(text)
            .font(PostPageStyle.titleFont)
            .tracking(-0.6)
            .lineSpacing(-3)
            .foregroundStyle(.white)
            .lineLimit(4)
            .minimumScaleFactor(0.72)
    }
}

/// A log's stars (24 pt gold, none empty) and heart (20 pt).
struct PostRatingLine: View {
    let rating: String?
    let liked: Bool

    var body: some View {
        HStack(spacing: 10) {
            if let rating {
                let symbols = PostPageStyle.starSymbols(rating: rating)
                HStack(spacing: 2) {
                    ForEach(Array(symbols.enumerated()), id: \.offset) { _, symbol in
                        Image(systemName: symbol)
                            .font(.system(size: 24, weight: .bold))
                            .foregroundStyle(HomeDesign.Palette.starGold)
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Rating \(Double(rating.ratingHalfSteps) / 2) out of 5 stars")
            }

            if liked {
                Image(systemName: "heart.fill")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundStyle(HomeDesign.Palette.heart)
                    .accessibilityLabel("Liked")
            }
        }
    }
}

/// A review at 17 pt with 1.5 line height, paragraph by paragraph.
struct PostReviewBody: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(PostPageStyle.paragraphs(text).enumerated()), id: \.offset) { _, paragraph in
                Text(paragraph)
                    .font(.system(size: 17))
                    .foregroundStyle(.white.opacity(0.94))
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A tag under a review: 30 pt tall, fully rounded, a light fill with a hairline edge.
struct PostTagChip: View {
    let text: String

    var body: some View {
        Label {
            Text(text)
                .lineLimit(1)
        } icon: {
            Image(systemName: "tag")
                .font(.system(size: 11, weight: .bold))
        }
        .font(.system(size: 13, weight: .semibold))
        .foregroundStyle(.white)
        .padding(.horizontal, 12)
        .frame(height: PostPageStyle.chipHeight)
        .background(.white.opacity(0.14), in: Capsule())
        .overlay { Capsule().strokeBorder(.white.opacity(0.22), lineWidth: 0.5) }
    }
}

/// The line between a post and its comments.
struct PostHairline: View {
    var body: some View {
        Rectangle()
            .fill(.white.opacity(0.25))
            .frame(height: 0.5)
            .accessibilityHidden(true)
    }
}

@MainActor
@Observable
final class DiaryLogEditViewModel {
    let entry: DiaryEntry
    var consumedAt: Date
    var gameProgress: GameProgressDraft
    let initialGameProgress: GameProgressDraft
    var ratingSteps: Int
    var reviewTitle: String
    var review: String
    let tagDraft: LogTagDraft
    var liked: Bool
    var isRewatch: Bool
    var containsSpoilers: Bool
    var visibility: String
    let favoriteDraft: LogFavoriteDraft
    let favoriteSeasons: [LogFavoriteSeason]
    var isSaving = false
    var errorMessage: String?

    private let diaryRepository: DiaryRepository

    init(entry: DiaryEntry, diaryRepository: DiaryRepository, mediaDetail: MediaDetail? = nil, mediaRepository: MediaRepository? = nil) {
        self.entry = entry
        self.diaryRepository = diaryRepository
        favoriteDraft = LogFavoriteDraft(
            favorite: entry.favorite,
            ref: mediaDetail?.ref ?? entry.media.ref,
            detail: mediaDetail,
            mediaRepository: mediaRepository
        )
        favoriteSeasons = favoriteDraft.seasons(
            for: entry.media.ref,
            showSeasons: LogFavoriteSeason.from(mediaDetail?.seasons ?? [])
        )
        consumedAt = CalendarDateCodec.date(from: entry.consumedAt) ?? Date()
        gameProgress = GameProgressDraft(totalMinutes: entry.totalMinutes, percentage: entry.completion)
        initialGameProgress = GameProgressDraft(totalMinutes: entry.totalMinutes, percentage: entry.completion)
        ratingSteps = entry.rating?.ratingHalfSteps ?? 0
        reviewTitle = entry.reviewTitle ?? ""
        review = entry.review ?? ""
        tagDraft = LogTagDraft(tags: entry.tags)
        liked = entry.liked
        isRewatch = entry.isTrueReread ?? entry.isRewatch
        containsSpoilers = entry.containsSpoilers
        visibility = entry.visibility
    }

    var repeatLabel: String {
        entry.media.ref.repeatLabel
    }

    var favoriteKind: LogFavorite.Kind? {
        favoriteDraft.kind(for: entry.media.ref, seasons: favoriteSeasons)
    }

    func ratingLabel() -> String {
        guard ratingSteps > 0 else { return "No rating" }
        let stars = Double(ratingSteps) / 2
        return stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))/5" : "\(stars)/5"
    }

    func setRating(locationX: CGFloat, width: CGFloat) {
        guard width > 0 else { return }
        let clamped = min(max(locationX, 0), width)
        ratingSteps = max(1, min(10, Int(ceil((clamped / width) * 10))))
    }

    /// Loads the user's own tags once, for suggestions while they type.
    func loadTags() async {
        try? await tagDraft.load(from: diaryRepository)
    }

    func request() throws -> DiaryEntryUpdateRequest {
        let progress = try gameProgress.request(comparedTo: initialGameProgress)
        var request = DiaryEntryUpdateRequest(
            consumedAt: consumedAt,
            rating: ratingSteps > 0 ? Decimal(ratingSteps) / 2 : nil,
            review: review,
            reviewTitle: reviewTitle,
            tags: tagDraft.tags,
            liked: liked,
            isRewatch: isRewatch,
            containsSpoilers: containsSpoilers,
            visibility: entry.media.ref.isSingleWeight ? nil : visibility,
            calendarDateOnly: entry.media.ref.usesCalendarLogDate,
            includesRating: true,
            totalMinutes: progress.totalMinutes, percentage: progress.percentage,
            includesMinutes: entry.media.ref.mediaType == "game" && progress.includesMinutes,
            includesPercentage: entry.media.ref.mediaType == "game" && progress.includesPercentage
        )
        // Only a change is sent, so a log whose tracklist didn't load keeps its favorite.
        if favoriteKind != nil, favoriteDraft.favorite != entry.favorite {
            request.favorite = favoriteDraft.favorite
            request.includesFavorite = true
        }
        return request
    }
}

private struct DiaryLogEditSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryLogEditViewModel
    @FocusState private var focusedField: EditField?
    let backdropURL: String?
    let onSave: (DiaryEntryUpdateRequest) async -> Bool
    let errorMessage: () -> String?

    private enum EditField: Hashable {
        case reviewTitle
        case review
        case tag
        case gameHours
        case gameMinutes
        case gamePercentage
    }

    init(
        entry: DiaryEntry,
        diaryRepository: DiaryRepository,
        mediaDetail: MediaDetail? = nil,
        mediaRepository: MediaRepository? = nil,
        backdropURL: String? = nil,
        errorMessage: @escaping () -> String? = { nil },
        onSave: @escaping (DiaryEntryUpdateRequest) async -> Bool
    ) {
        _viewModel = State(initialValue: DiaryLogEditViewModel(
            entry: entry,
            diaryRepository: diaryRepository,
            mediaDetail: mediaDetail,
            mediaRepository: mediaRepository
        ))
        self.backdropURL = backdropURL
        self.onSave = onSave
        self.errorMessage = errorMessage
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                Form {
                    // A section header, not a row: rows clip to the card's rounded corners,
                    // which cut into the poster's left side.
                    Section {
                    } header: {
                        LogHeader(
                            title: TVTrackingPresentation.diaryTitle(for: viewModel.entry.media),
                            posterURL: viewModel.entry.media.displayPosterURL,
                            mediaType: viewModel.entry.media.ref.mediaType,
                            orientation: viewModel.entry.media.posterOrientation
                        )
                        .textCase(nil)
                        .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 8, trailing: 0))
                    }

                    Section {
                        LogRatingCard(
                            ratingSteps: $viewModel.ratingSteps,
                            liked: $viewModel.liked,
                            isRepeat: $viewModel.isRewatch,
                            repeatLabel: viewModel.repeatLabel,
                            ratingLabel: viewModel.ratingLabel(),
                            ratingIdentifier: "diary-log.rating"
                        ) { locationX, width in
                            viewModel.setRating(locationX: locationX, width: width)
                        }
                    }
                    .logRowBackground()

                    if let kind = viewModel.favoriteKind {
                        LogFavoriteSection(
                            draft: viewModel.favoriteDraft,
                            kind: kind,
                            seasons: viewModel.favoriteSeasons
                        )
                    }

                    Section {
                        DatePicker(
                            viewModel.entry.media.ref.consumedDateLabel,
                            selection: $viewModel.consumedAt,
                            in: DiaryLogDateLimit.range(for: viewModel.entry.media.ref),
                            displayedComponents: .date
                        )

                        if isGame {
                            LogGameProgressRows(
                                draft: $viewModel.gameProgress,
                                focus: $focusedField,
                                hoursFocus: .gameHours,
                                minutesFocus: .gameMinutes,
                                percentageFocus: .gamePercentage
                            )
                        }
                    } footer: {
                        if isGame {
                            Text(LogComposerStyle.gameProgressFootnote)
                        }
                    }
                    .logRowBackground()

                    Section {
                        TextField("Review title", text: $viewModel.reviewTitle)
                            .focused($focusedField, equals: .reviewTitle)
                            .submitLabel(.next)
                            .onSubmit { focusedField = .review }

                        TextField("Add a review…", text: $viewModel.review, axis: .vertical)
                            .lineLimit(4...12)
                            .focused($focusedField, equals: .review)
                    }
                    .logRowBackground()

                    LogTagsSection(draft: viewModel.tagDraft, focus: $focusedField, focusValue: .tag, scrollProxy: proxy)

                    Section {
                        if !viewModel.entry.media.ref.usesCalendarConsumptionDate {
                            Picker("Visibility", selection: $viewModel.visibility) {
                                ForEach(APIConstants.visibilityChoices, id: \.self) { value in
                                    Text(value.capitalized).tag(value)
                                }
                            }
                        }
                        Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
                            .tint(.red)
                    }
                    .logRowBackground()
                }
                .listSectionSpacing(.compact)
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.interactively)
                .disabled(viewModel.isSaving)
            }
            .background { LogBackdrop(url: URL(string: backdropURL ?? viewModel.entry.media.displayPosterURL ?? "")) }
            .navigationTitle("Edit Log")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    } label: {
                        Label("Close", systemImage: "xmark")
                    }
                    .disabled(viewModel.isSaving)
                }

                ToolbarItem(placement: .confirmationAction) {
                    if viewModel.isSaving {
                        ProgressView()
                    } else {
                        Button(role: .confirm) {
                            save()
                        } label: {
                            Label("Save Changes", systemImage: "checkmark")
                        }
                    }
                }

                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") {
                        focusedField = nil
                    }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .glassEffect(.regular, in: .rect(cornerRadius: 18))
                        .padding(.horizontal, 16)
                        .padding(.bottom, 8)
                }
            }
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(viewModel.isSaving)
        .task {
            await viewModel.loadTags()
        }
    }

    private var isGame: Bool {
        viewModel.entry.media.ref.mediaType == "game"
    }

    private func save() {
        guard !viewModel.isSaving else { return }
        focusedField = nil
        viewModel.tagDraft.commitQuery()
        // Set before the task starts, so a quick second tap can't send the update twice.
        viewModel.isSaving = true
        viewModel.errorMessage = nil
        Task {
            defer { viewModel.isSaving = false }
            do {
                if await onSave(try viewModel.request()) {
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                    dismiss()
                } else {
                    viewModel.errorMessage = errorMessage() ?? "Could not save. Try again."
                    UINotificationFeedbackGenerator().notificationOccurred(.error)
                }
            } catch {
                viewModel.errorMessage = error.localizedDescription
                UINotificationFeedbackGenerator().notificationOccurred(.error)
            }
        }
    }
}

private struct DiaryLogTagsView: View {
    let tags: [String]
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    @State private var isExpanded = false
    @State private var fullHeight: CGFloat = 0

    private let rowHeight = PostPageStyle.chipHeight
    private let spacing: CGFloat = 8

    private var collapsedHeight: CGFloat {
        rowHeight * 3 + spacing * 2
    }

    private var canExpand: Bool {
        fullHeight > collapsedHeight + 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            tagLinks
                .frame(maxHeight: isExpanded ? nil : collapsedHeight, alignment: .top)
                .clipped()
                .background {
                    tagChips
                        .fixedSize(horizontal: false, vertical: true)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(key: DiaryLogTagsFullHeightKey.self, value: proxy.size.height)
                            }
                        }
                        .hidden()
                }

            if canExpand {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                } label: {
                    Label(isExpanded ? "READ LESS" : "READ MORE", systemImage: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.62))
                }
                .buttonStyle(.plain)
            }
        }
        .onPreferenceChange(DiaryLogTagsFullHeightKey.self) { fullHeight = $0 }
    }

    private var tagLinks: some View {
        FlowLayout(spacing: spacing) {
            ForEach(tags, id: \.self) { tag in
                NavigationLink {
                    TaggedDiaryView(
                        tag: tag,
                        diaryRepository: diaryRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        currentUserId: currentUserId,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    PostTagChip(text: tag)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var tagChips: some View {
        FlowLayout(spacing: spacing) {
            ForEach(tags, id: \.self) { tag in
                PostTagChip(text: tag)
            }
        }
    }
}

private struct DiaryLogTagsFullHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

/// A log's tag; list pages show their tags with it too.
struct DiaryLogChip: View {
    let text: String
    let systemImage: String?

    var body: some View {
        Label {
            Text(text)
                .lineLimit(1)
        } icon: {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 10, weight: .black))
            }
        }
        .font(.system(size: 12, weight: .heavy))
        .foregroundStyle(.white.opacity(0.82))
        .padding(.horizontal, 10)
        .frame(height: 30)
        .background(.white.opacity(0.11), in: Capsule())
    }
}

enum DiaryLogFormat {
    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let fallbackISOFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let dateOnlyFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale.current
        formatter.dateFormat = "MMM d, yyyy"
        return formatter
    }()

    static func dateLabel(_ rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return displayDateFormatter.string(from: date)
    }

    static func ageLabel(_ rawValue: String?, now: Date = Date(), calendar: Calendar = .current) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        let start = calendar.startOfDay(for: date)
        let end = calendar.startOfDay(for: now)
        guard start < end else { return nil }

        let components = calendar.dateComponents([.year, .month, .day], from: start, to: end)
        let parts = [
            componentLabel(components.year ?? 0, "year"),
            componentLabel(components.month ?? 0, "month"),
            componentLabel(components.day ?? 0, "day"),
        ].compactMap { $0 }

        return "\(joinedWithAnd(parts)) ago"
    }

    static func year(_ rawValue: String?) -> String? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 4 else { return nil }
        return String(trimmed.prefix(4))
    }

    private static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let dateOnly = String(trimmed.prefix(10))
        return dateOnlyFormatter.date(from: dateOnly)
            ?? isoFormatter.date(from: trimmed)
            ?? fallbackISOFormatter.date(from: trimmed)
    }

    private static func componentLabel(_ value: Int, _ unit: String) -> String? {
        guard value > 0 else { return nil }
        return "\(value) \(unit)\(value == 1 ? "" : "s")"
    }

    private static func joinedWithAnd(_ parts: [String]) -> String {
        guard parts.count > 2 else { return parts.joined(separator: " and ") }
        return "\(parts.dropLast().joined(separator: ", ")) and \(parts[parts.count - 1])"
    }
}
