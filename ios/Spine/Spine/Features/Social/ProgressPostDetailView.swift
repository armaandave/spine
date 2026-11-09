import SwiftUI

@MainActor
@Observable
final class ProgressPostDetailViewModel {
    private(set) var activity: ActivityItem?
    private(set) var mediaDetail: MediaDetail?
    private(set) var isLoading = true
    private(set) var errorMessage: String?
    let engagement: PostEngagementModel
    /// Edit and Delete, for your own row (AC-9); made once the row has loaded.
    private(set) var editor: ProgressRowEditor?
    /// What the page offers its viewer, as the server said.
    var controls: ProgressRowControls { ProgressRowControls(activity: activity) }

    private let progressId: Int
    private let activityRepository: ActivityRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository?
    private let onUnauthorized: () -> Void
    /// The feed card this page was opened from; nil from anywhere else.
    private let feedCard: FeedCardModel?
    /// Where the person stands on the title now, loaded here when the card did not bring it (or there is no card), and
    /// whether this day is their newest on the title. A standing is where they are now, so only the newest day has one.
    private var standing: FeedStanding?
    private var isNewestOfTitle: Bool?
    @ObservationIgnored private var standings: FriendStandings?
    @ObservationIgnored private var standingTask: Task<Void, Never>?

    /// What the page says about the day: the card's own model when it has one, else the same presenter run on the
    /// page's own row and the standing loaded here.
    var pageContent: ProgressPageContent? {
        activity.map {
            ProgressPageContent(model: ProgressPageContent.model(
                for: $0, card: feedCard, standing: standing, isNewestOfTitle: isNewestOfTitle
            ))
        }
    }

    init(
        progressId: Int,
        activityRepository: ActivityRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository? = nil,
        feedCard: FeedCardModel? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.progressId = progressId
        self.trackingRepository = trackingRepository
        self.feedCard = feedCard
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
            if let media = loaded.media, let trackingRepository {
                editor = ProgressRowEditor(
                    progressId: progressId,
                    media: media,
                    activityRepository: activityRepository,
                    trackingRepository: trackingRepository,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                )
            }
            standingTask?.cancel()
            standingTask = Task { [weak self] in await self?.loadStanding(for: loaded) }
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

    /// Where the person stands on the title, from the lists the feed reads (`FriendStandings`), when the page was not
    /// opened from a card that already had it. Only the person's newest day on the title gets one, as on the feed, so
    /// their recent activity is read first to see whether this is it. Anything that fails leaves the page with just
    /// the day's own amount.
    private func loadStanding(for loaded: ActivityItem) async {
        guard let trackingRepository,
              FriendStandings.want(for: loaded) != nil,
              !ProgressPageContent.hasStanding(feedCard)
        else { return }
        let recent = (try? await activityRepository.userActivity(username: loaded.actor.username, limit: 50)) ?? []
        guard !Task.isCancelled else { return }
        let newest = ProgressPageContent.isNewestOfTitle(loaded, among: recent)
        isNewestOfTitle = newest
        guard newest else { return }
        let provider = FriendStandings(
            trackingRepository: trackingRepository,
            seasonPages: mediaRepository,
            onUnauthorized: onUnauthorized
        )
        provider.setOnChange { [weak self] in
            guard let self else { return }
            standing = standings?.standing(for: loaded, isNewestOfTitle: true)
        }
        standings = provider
        provider.prepare(for: [loaded])
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
    @State private var isEditing = false
    @State private var isDeleteConfirmationPresented = false
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
        feedCard: FeedCardModel? = nil,
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
            trackingRepository: trackingRepository,
            feedCard: feedCard,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            PostPageBackground(url: pageArtURL, accentHex: pageAccentHex)

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

            topButtons
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
        // The buttons are drawn by the page, so no navigation bar adds an inset of its own.
        .toolbar(.hidden, for: .navigationBar)
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
        .sheet(isPresented: $isEditing, onDismiss: leaveIfChanged) {
            if let editor = viewModel.editor, let edit = viewModel.activity?.viewer?.edit, let media = viewModel.activity?.media {
                ProgressRowEditSheet(
                    title: media.displayTitle,
                    edit: edit,
                    mediaType: media.ref.mediaType,
                    onSave: { input, date in await editor.save(input: input, date: date, edit: edit) }
                )
            }
        }
        .confirmationDialog(
            "Delete this day's progress?",
            isPresented: $isDeleteConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                Task {
                    await viewModel.editor?.delete()
                    leaveIfChanged()
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This takes back what you saved that day and puts your place back where it was.")
        }
        .alert(
            "Couldn't delete",
            isPresented: Binding(
                get: { viewModel.editor?.deleteErrorMessage != nil },
                set: { if !$0 { viewModel.editor?.deleteErrorMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel.editor?.deleteErrorMessage ?? "")
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

    /// Back, and for your own book, manga, comic or game row Edit and Delete (the server says what the page offers,
    /// AC-9): one row at one inset, as on a log's page.
    private var topButtons: some View {
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

                if viewModel.controls.showsEdit {
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
                    .disabled(viewModel.editor?.isDeleting == true)
                }

                if viewModel.controls.showsDelete {
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
                    .disabled(viewModel.editor?.isDeleting == true)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
    }

    /// An edit or a delete went through: Stats, the list and Home were told, and the row may be gone, so the page closes.
    private func leaveIfChanged() {
        if viewModel.editor?.didChange == true {
            dismiss()
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

    /// The art behind the whole page: the card's own art for the title, which the title's details never replace, so the
    /// page does not change tint when they arrive.
    private var pageArtURL: URL? {
        viewModel.activity?.media.flatMap(PostPageStyle.artURL(for:))
    }

    private var pageAccentHex: String? {
        PostPageStyle.accentHex(
            summary: viewModel.activity?.media?.posterAccentColor,
            detail: viewModel.mediaDetail?.posterAccentColor
        )
    }

    private func content(_ activity: ActivityItem) -> some View {
        let page = viewModel.pageContent
        return VStack(alignment: .leading, spacing: 18) {
            if let media = activity.media {
                hero(activity, media: media, page: page)
            }

            VStack(alignment: .leading, spacing: 18) {
                if let page, page.hasHeadline {
                    headline(page, activity: activity)
                } else {
                    Text(ActivityFeedPresentation.progressHeadline(for: activity))
                        .font(.system(size: 21, weight: .heavy))
                        .foregroundStyle(.white)
                        .fixedSize(horizontal: false, vertical: true)
                }

                // The bar the day's card draws, once the person's place on the title is known.
                if let page, let meter = page.meter {
                    PostProgressMeterView(meter: meter, art: page.art)
                }

                PostHairline()
                    .padding(.top, 6)

                PostCommentsSection(model: viewModel.engagement)

                Color.clear
                    .frame(height: 1)
                    .id("post-end")
            }
            .padding(.horizontal, 16)
        }
    }

    /// What the card says under its title, with room to say all of it: the amount in the poster's accent (or "Started"),
    /// the status pills beside it, then where the person stands in a muted line, even next to pills.
    private func headline(_ page: ProgressPageContent, activity: ActivityItem) -> some View {
        let ink = ArtInk.dark
        let accent = ink.accent(posterAccentHex: page.art.accentHex)
        let lead = HStack(alignment: .center, spacing: 10) {
            if let amount = page.amount {
                Text(amount)
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(accent)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .layoutPriority(2)
            }
            if page.isStart {
                Text("Started")
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(accent)
            }
            if let words = page.words {
                Text(words)
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        let pills = HStack(alignment: .center, spacing: 10) {
            ForEach(page.pills, id: \.self) { FeedStatusPill(word: $0, ink: ink) }
        }
        return VStack(alignment: .leading, spacing: 8) {
            // Beside each other when they fit; a long amount with two pills drops the pills under it.
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 10) { lead; pills }
                VStack(alignment: .leading, spacing: 8) { lead; pills }
            }

            if page.isStart {
                // Which verb it was: "Started reading".
                Text(ActivityFeedPresentation.progressHeadline(for: activity))
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
            }

            if let standing = page.standing {
                Text(standing)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(page.spokenHeadline)
    }

    private func hero(_ activity: ActivityItem, media: MediaSummary, page: ProgressPageContent?) -> some View {
        HStack(alignment: .bottom, spacing: 14) {
            PostHeroPoster(
                url: viewModel.mediaDetail?.displayPosterURL ?? media.displayPosterURL,
                title: media.title,
                mediaType: media.ref.mediaType,
                orientation: viewModel.mediaDetail?.posterOrientation ?? media.posterOrientation
            )

            VStack(alignment: .leading, spacing: 8) {
                PostHeroAuthorLine(user: activity.actor)

                PostHeroTypeLabel(text: media.ref.mediaType == "tv" ? "TV" : media.ref.mediaType.capitalized)

                if let date = ActivityTimestampFormatter.text(for: activity) {
                    Text(date)
                        .font(PostPageStyle.loggedFont)
                        .foregroundStyle(.white.opacity(0.86))
                }

                PostHeroTitle(text: page?.title ?? media.displayTitle)

                // "S2 E7", "E7", "Season 2": which part of the show the day was.
                if let tag = page?.tag {
                    Text(tag)
                        .font(PostPageStyle.subtitleFont)
                        .foregroundStyle(.white.opacity(0.78))
                }
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
    var feedCard: FeedCardModel? = nil
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
                feedCard: feedCard,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
    }
}
