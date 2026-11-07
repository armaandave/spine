import SwiftUI

@MainActor
@Observable
final class ProgressPostDetailViewModel {
    private(set) var activity: ActivityItem?
    private(set) var mediaDetail: MediaDetail?
    private(set) var isLoading = true
    private(set) var errorMessage: String?
    let engagement: PostEngagementModel

    private let progressId: Int
    private let activityRepository: ActivityRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        progressId: Int,
        activityRepository: ActivityRepository,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.progressId = progressId
        self.activityRepository = activityRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        engagement = PostEngagementModel(
            post: .progress(progressId),
            repository: activityRepository,
            onUnauthorized: onUnauthorized
        )
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loaded = try await activityRepository.progressPost(id: progressId)
            // Shown right away: the title's details only add artwork, and can be slow to arrive.
            activity = loaded
            engagement.seed(loaded.engagement)
            if let ref = loaded.media?.ref {
                mediaDetail = try? await mediaRepository.detail(ref: ref)
            }
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

/// A day's progress on its own page, laid out like a log's page: the title, who it was, what they did, then its likes
/// and comments.
struct ProgressPostDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ProgressPostDetailViewModel
    @State private var presentedRef: MediaRef?
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

    init(
        progressId: Int,
        activityRepository: ActivityRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
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
        _viewModel = State(initialValue: ProgressPostDetailViewModel(
            progressId: progressId,
            activityRepository: activityRepository,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollViewReader { proxy in
                ScrollView(showsIndicators: false) {
                    Group {
                        if viewModel.isLoading, viewModel.activity == nil {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 520)
                        } else if let activity = viewModel.activity {
                            content(activity)
                        } else if let error = viewModel.errorMessage {
                            ContentUnavailableView(
                                "Could not load this",
                                systemImage: "exclamationmark.triangle",
                                description: Text(error)
                            )
                            .foregroundStyle(.white)
                            .padding()
                        }
                    }
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
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.top, 12)
        }
        // Inside the bar's inset, so the swipe's edge doesn't cover the heart.
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .safeAreaBar(edge: .bottom) {
            if viewModel.activity != nil {
                PostCommentBar(model: viewModel.engagement, isFocused: $isCommentFocused)
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.safeAreaInsets.top } action: { topSafeAreaInset = $0 }
        .navigationBarBackButtonHidden()
        .toolbar(.hidden, for: .tabBar)
        .dismissExplorationOnReturnHome()
        .offset(x: edgeDragOffset)
        .task {
            if viewModel.activity == nil {
                await viewModel.load()
            }
            if viewModel.activity != nil {
                await viewModel.engagement.loadCommentsIfNeeded()
            }
        }
        .fullScreenCover(item: $presentedRef) { ref in
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

    private func content(_ activity: ActivityItem) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            if let media = activity.media {
                hero(activity, media: media)
            }

            VStack(alignment: .leading, spacing: 18) {
                Text(ActivityFeedPresentation.progressHeadline(for: activity))
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)

                PostCommentsSection(model: viewModel.engagement)

                Color.clear
                    .frame(height: 1)
                    .id("post-end")
            }
            .padding(.horizontal, 16)
        }
    }

    private func hero(_ activity: ActivityItem, media: MediaSummary) -> some View {
        HStack(alignment: .bottom, spacing: 14) {
            MediaArtwork(
                url: viewModel.mediaDetail?.displayPosterURL ?? media.displayPosterURL,
                title: media.title,
                slot: .hero,
                mediaType: media.ref.mediaType,
                orientation: viewModel.mediaDetail?.posterOrientation ?? media.posterOrientation
            )
            .shadow(color: .black.opacity(0.48), radius: 22, y: 12)

            VStack(alignment: .leading, spacing: 10) {
                PostAuthorLink(user: activity.actor, avatarSize: 24, spacing: 7, font: .system(size: 14, weight: .semibold))

                Text(media.ref.mediaType == "tv" ? "TV" : media.ref.mediaType.capitalized)
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.56))
                    .textCase(.uppercase)

                if let date = ActivityTimestampFormatter.text(for: activity) {
                    Text(date)
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.72))
                }

                Text(media.displayTitle)
                    .font(.system(size: 31, weight: .black))
                    .foregroundStyle(.white)
                    .lineLimit(4)
                    .minimumScaleFactor(0.72)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .contentShape(Rectangle())
        .onTapGesture { presentedRef = media.ref }
        .accessibilityLabel("View \(media.displayTitle)")
        .accessibilityAddTraits(.isButton)
        .padding(.horizontal, 16)
        // The back button ends 50 pt below the safe area; the artwork starts 18 pt under it, as on a log's page.
        .padding(.top, topSafeAreaInset + 68)
        .padding(.bottom, 4)
        .background {
            LogHeroArtwork(
                mediaType: media.ref.mediaType,
                posterURL: media.displayPosterURL,
                detail: viewModel.mediaDetail
            )
            .offset(y: BackdropLayout.topOffset)
        }
    }
}

struct ProgressPostNavigationCover: View {
    let progressId: Int
    let activityRepository: ActivityRepository
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        NavigationStack {
            ProgressPostDetailView(
                progressId: progressId,
                activityRepository: activityRepository,
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
