import SwiftUI

@MainActor
@Observable
final class HomeViewModel {
    var profile: UserProfile?
    var inProgressItems: [LibraryItem] = []
    var isLoading = true
    var isLoadingInProgress = false
    var profileErrorMessage: String?
    var inProgressErrorMessage: String?

    /// The activity of the people you follow; your own is on your profile.
    let feed: ActivityFeedViewModel
    var activityItems: [ActivityItem] { feed.items }
    var activityErrorMessage: String? { feed.errorMessage }

    /// Continue Watching's episode sheet (TV-100, TV-701).
    let continueWatching: ContinueWatchingTicks
    /// Every In progress card's "Update progress ›": shows open the episode sheet, other titles their page's progress
    /// UI (TV-701).
    let progressUpdates: InProgressUpdates

    private let profileRepository: ProfileRepository
    private let trackingRepository: TrackingRepository
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        trackingRepository: TrackingRepository,
        activityRepository: ActivityRepository,
        mediaRepository: MediaRepository? = nil,
        diaryRepository: DiaryRepository? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
        feed = ActivityFeedViewModel(
            username: nil,
            activityRepository: activityRepository,
            onUnauthorized: onUnauthorized
        )
        // The app's own repositories unless given: looked up here, on the main actor, not in a default argument.
        let mediaRepository = mediaRepository ?? AppRepositories.current().media
        let diaryRepository = diaryRepository ?? AppRepositories.current().diary
        continueWatching = ContinueWatchingTicks(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        progressUpdates = InProgressUpdates(
            continueWatching: continueWatching,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        // A write in the episode sheet updates its card where it is; the rail re-sorts when it next loads (TV-701).
        continueWatching.onUpdate = { [weak self] id, tv in
            guard let self else { return }
            inProgressItems = ContinueWatchingTicks.replacingTV(tv, id: id, in: inProgressItems)
        }
        // So does a write from another card's progress UI.
        progressUpdates.onUpdate = { [weak self] id, state in
            guard let self else { return }
            inProgressItems = InProgressUpdates.replacingProgress(state, id: id, in: inProgressItems)
        }
    }

    func load() async {
        guard await loadProfileAndInProgress() else { return }
        await loadActivity()
    }

    /// Returns whether the profile loaded.
    private func loadProfileAndInProgress() async -> Bool {
        isLoading = true
        profileErrorMessage = nil
        defer { isLoading = false }

        do {
            profile = try await profileRepository.me()
        } catch is CancellationError {
            return false
        } catch {
            profileErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return false
        }

        isLoadingInProgress = inProgressItems.isEmpty
        await loadInProgress()
        return true
    }

    func reload() async {
        await load()
    }

    /// You followed, unfollowed or blocked someone: the people in your activity, and the empty state's count, changed.
    func followsDidChange() async {
        guard let refreshed = try? await profileRepository.me() else { return }
        profile = refreshed
        await loadActivity()
    }

    /// You changed a title. A write from a card's own progress UI (a tick in Continue Watching's sheet, or a book's or
    /// game's progress) keeps its card where it is (TV-701); any other write reloads the rail. The feed is only the
    /// people you follow, so your own writes can't change it, and it keeps its place however far down you are.
    func mediaStateDidChange(origin: UUID?) async {
        guard origin != continueWatching.changeOrigin else { return }
        guard await loadProfileAndInProgress() else { return }
        await feed.loadIfNeeded()
    }

    func loadInProgress() async {
        let mediaTypes = InProgressLibraryLoader.mediaTypes(from: profile)
        guard !mediaTypes.isEmpty else {
            inProgressItems = []
            inProgressErrorMessage = nil
            isLoadingInProgress = false
            return
        }

        isLoadingInProgress = true
        inProgressErrorMessage = nil
        defer { isLoadingInProgress = false }

        do {
            inProgressItems = try await InProgressLibraryLoader.load(
                mediaTypes: mediaTypes,
                trackingRepository: trackingRepository,
                limit: 10
            )
            continueWatching.railDidLoad()
            progressUpdates.railDidLoad()
        } catch is CancellationError {
            return
        } catch {
            inProgressErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    /// A write from a card's own progress UI changed a card since the rail last loaded (TV-701).
    var inProgressNeedsReload: Bool {
        continueWatching.needsReload || progressUpdates.needsReload
    }

    func loadActivity() async {
        guard profile != nil else { return }
        await feed.load()
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

struct HomeView: View {
    private static let headerAvatarSize: CGFloat = 40

    @State private var viewModel: HomeViewModel
    @State private var selectedRef: MediaRef?
    /// The season's episodes, when Continue Watching's sheet opened one of them: its page pages through them.
    @State private var selectedEpisodes: MediaBrowsingContext?
    @State private var selectedPerson: PersonRef?
    @State private var selectedPost: ActivityPostSelection?
    @State private var scrollPosition = HomeScrollPosition()

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let activityRepository: ActivityRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onFindPeople: () -> Void
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        activityRepository: ActivityRepository = AppRepositories.current().activity,
        listRepository: ListRepository = AppRepositories.current().lists,
        peopleRepository: PeopleRepository = AppRepositories.current().people,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onFindPeople: @escaping () -> Void = {},
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.activityRepository = activityRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onFindPeople = onFindPeople
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: HomeViewModel(
            profileRepository: profileRepository,
            trackingRepository: trackingRepository,
            activityRepository: activityRepository,
            mediaRepository: mediaRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack(alignment: .top) {
                HomeScrollingAtmosphere(
                    media: viewModel.inProgressItems.first?.media,
                    scrollPosition: scrollPosition
                )

                ScrollView(showsIndicators: false) {
                    // One lazy stack, with the top of the page as one plain item and then one item per post. A frozen
                    // Home was sampled looping in nested lazy stacks' placement of the activity rows, which lazy
                    // stacks size from estimated heights (TV-705).
                    LazyVStack(alignment: .leading, spacing: 0) {
                        VStack(alignment: .leading, spacing: 22) {
                            header
                            content
                                .spineContentTransition(value: contentPhase)
                        }
                        feedPosts
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 18)
                    .padding(.bottom, 92)
                }
                .refreshable {
                    await viewModel.reload()
                }
                .onScrollGeometryChange(for: CGFloat.self) { geometry in
                    max(0, geometry.contentOffset.y)
                } action: { _, offset in
                    scrollPosition.offset = offset
                }

                HomeStatusBarFade(scrollPosition: scrollPosition)
            }
            .background {
                // A faint wash all the way down the feed, for the tab bar's glass to pick up. It's dimmed well past the
                // Diary tab's: the posts sit directly on it, so their text and artwork have to stay crisp.
                LogBackdrop(url: washURL)
                    .overlay {
                        SpinePalette.pageBackground
                            .opacity(0.7)
                            .ignoresSafeArea()
                    }
            }
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                if viewModel.profile == nil {
                    await viewModel.load()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Task { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .storygraphImportDidSucceed)) { _ in
                Task { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .marginsImportDidSucceed)) { _ in
                Task { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .myAnimeListImportDidSucceed)) { _ in
                Task { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .followsDidChange)) { _ in
                Task { await viewModel.followsDidChange() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
                Task { await viewModel.mediaStateDidChange(origin: notification.userInfo?["origin"] as? UUID) }
            }
            .onReceive(NotificationCenter.default.publisher(for: .activityPostDidChange)) { notification in
                if let change = ActivityPostChange.parse(notification) {
                    viewModel.feed.apply(change.engagement, to: change.post)
                }
            }
            .onChange(of: selectedTab) { _, tab in
                // Back on Home after a card's progress changed: the rail re-sorts now, not under the user's finger
                // (TV-701).
                if tab == .home, viewModel.inProgressNeedsReload {
                    Task { await viewModel.loadInProgress() }
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .profileDidUpdate)) { notification in
                if let profile = notification.userInfo?["profile"] as? UserProfile {
                    viewModel.profile = profile
                    Task { await viewModel.loadInProgress() }
                } else {
                    Task { await viewModel.reload() }
                }
            }
            .onChange(of: selectedRef) { oldValue, newValue in
                if oldValue != nil, newValue == nil {
                    Task { await viewModel.loadInProgress() }
                }
            }
            .fullScreenCover(item: $selectedRef, onDismiss: {
                selectedRef = nil
                selectedEpisodes = nil
            }) { ref in
                MediaDetailView(
                    ref: ref,
                    browsingContext: selectedEpisodes,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    listRepository: listRepository,
                    peopleRepository: peopleRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
            .fullScreenCover(item: $selectedPerson, onDismiss: { selectedPerson = nil }) { ref in
                PersonDetailView(
                    ref: ref,
                    peopleRepository: peopleRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    listRepository: listRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
            .continueWatchingPresentations(
                viewModel.continueWatching,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized
            ) { selection in
                selectedEpisodes = selection.context
                selectedRef = selection.ref
            }
            .inProgressUpdatePresentations(
                viewModel.progressUpdates,
                screenOpenedOther: selectedRef != nil || selectedPerson != nil || selectedPost != nil,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized
            ) { ref in
                selectedRef = ref
            }
            .fullScreenCover(item: $selectedPost, onDismiss: { selectedPost = nil }) { selection in
                switch selection.post {
                case let .log(id):
                    DiaryLogDetailNavigationCover(
                        entryId: id,
                        diaryRepository: diaryRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        currentUserId: currentUserId,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                case let .progress(id):
                    ProgressPostNavigationCover(
                        progressId: id,
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
    }

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                onSelectTab(.profile)
            } label: {
                HomeAvatar(profile: viewModel.profile, size: Self.headerAvatarSize)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Profile")

            Text("Home")
                .font(.system(size: 30, weight: .black))
                .foregroundStyle(.white)

            Spacer()
        }
    }

    @ViewBuilder
    private var content: some View {
        if let error = viewModel.profileErrorMessage, viewModel.profile == nil {
            HomeInlineState(
                title: "Could not load home",
                systemImage: "exclamationmark.triangle",
                message: error,
                actionTitle: "Retry"
            ) {
                Task { await viewModel.reload() }
            }
            .frame(minHeight: 360)
        } else if viewModel.isLoading, viewModel.profile == nil {
            VStack(alignment: .leading, spacing: 22) {
                SpineSection(title: "In Progress") {
                    HomeInProgressSkeleton()
                }
                SpineSection(title: "Activity") {
                    ActivityFeedSkeleton()
                }
            }
        } else {
            inProgressSection
            activitySection
        }
    }

    private var inProgressSection: some View {
        SpineSection(title: "In Progress") {
            Group {
                if viewModel.isLoadingInProgress, viewModel.inProgressItems.isEmpty {
                    HomeInProgressSkeleton()
                } else if let error = viewModel.inProgressErrorMessage, viewModel.inProgressItems.isEmpty {
                    HomeInlineState(
                        title: "Could not load progress",
                        systemImage: "exclamationmark.triangle",
                        message: error,
                        actionTitle: "Retry"
                    ) {
                        Task { await viewModel.loadInProgress() }
                    }
                } else if viewModel.inProgressItems.isEmpty {
                    HomeInlineState(
                        title: "Nothing in progress",
                        systemImage: "play.circle",
                        message: "Things you're watching, reading, listening to, and playing will appear here.",
                        actionTitle: "Find something"
                    ) {
                        onSelectTab(.search)
                    }
                } else {
                    SpineRail(items: viewModel.inProgressItems) { item in
                        InProgressCard(
                            item: item,
                            isLoading: viewModel.progressUpdates.loadingItemID == item.id,
                            onOpen: { selectedRef = item.media.ref },
                            onUpdateProgress: { updateProgress(item) }
                        )
                    }
                }
            }
            .spineContentTransition(value: inProgressPhase)
        }
    }

    /// The section's title and any state in place of posts; the posts follow it as their own lazy items.
    private var activitySection: some View {
        SpineSection(title: "Activity") {
            Group {
                if viewModel.feed.isLoadingInitial, viewModel.activityItems.isEmpty {
                    ActivityFeedSkeleton()
                } else if let error = viewModel.activityErrorMessage, viewModel.activityItems.isEmpty {
                    HomeInlineState(
                        title: "Could not load activity",
                        systemImage: "exclamationmark.triangle",
                        message: error,
                        actionTitle: "Retry"
                    ) {
                        Task { await viewModel.loadActivity() }
                    }
                } else if viewModel.activityItems.isEmpty, viewModel.profile?.counts.following ?? 0 == 0 {
                    HomeInlineState(
                        title: "Follow people you know",
                        systemImage: "person.2",
                        message: "What they log and watch will appear here.",
                        actionTitle: "Find People",
                        action: onFindPeople
                    )
                } else if viewModel.activityItems.isEmpty {
                    HomeInlineState(
                        title: "No activity yet",
                        systemImage: "clock.arrow.circlepath",
                        message: "Logs and progress from people you follow will appear here."
                    )
                }
            }
            .spineContentTransition(value: activityPhase)
        }
    }

    @ViewBuilder
    private var feedPosts: some View {
        if viewModel.profile != nil {
            ForEach(viewModel.activityItems) { activity in
                ActivityPostRow(activity: activity, onOpen: { open(activity) })
                .task {
                    await viewModel.feed.loadNextPageIfNeeded(currentItem: activity)
                }
            }

            if viewModel.feed.isLoadingNextPage {
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity, minHeight: 60)
            } else if let error = viewModel.feed.nextPageErrorMessage {
                VStack(spacing: 8) {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.52))
                        .multilineTextAlignment(.center)

                    Button("Retry") {
                        Task { await viewModel.feed.loadNextPage() }
                    }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.86))
                }
                .frame(maxWidth: .infinity, minHeight: 60)
            }
        }
    }

    /// The artwork you're on now, or the newest post's when nothing is in progress.
    private var washURL: URL? {
        HomeArtworkAtmosphereModel.url(for: viewModel.inProgressItems.first?.media)
            ?? URL(string: viewModel.activityItems.first?.media?.displayPosterURL ?? "")
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.profile != nil,
            hasError: viewModel.profileErrorMessage != nil
        )
    }

    private var inProgressPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingInProgress,
            hasContent: !viewModel.inProgressItems.isEmpty,
            hasError: viewModel.inProgressErrorMessage != nil
        )
    }

    private var activityPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.feed.isLoadingInitial,
            hasContent: !viewModel.activityItems.isEmpty,
            hasError: viewModel.activityErrorMessage != nil
        )
    }

    /// A card's title and "Update progress ›": its progress UI over Home, or its page when it has none.
    private func updateProgress(_ item: LibraryItem) {
        Task {
            if let ref = await viewModel.progressUpdates.updateProgress(for: item) {
                selectedRef = ref
            }
        }
    }

    private func open(_ activity: ActivityItem) {
        switch ActivityDestination.resolve(activity) {
        case let .diary(id):
            selectedPost = ActivityPostSelection(post: .log(id))
        case let .progress(id):
            selectedPost = ActivityPostSelection(post: .progress(id))
        case let .person(ref):
            selectedPerson = ref
        case let .media(ref):
            selectedRef = ref
        case .none:
            break
        }
    }
}

enum HomeBackdropMotion {
    private static let fadeDistance: CGFloat = 240

    static func opacity(for scrollOffset: CGFloat) -> Double {
        Double(max(0, 1 - (scrollOffset / fadeDistance)))
    }
}

private struct ActivityPostSelection: Identifiable {
    let post: ActivityPostRef

    var id: String { post.id }
}

/// Home's scroll position. Only the backdrop reads it, so scrolling doesn't redraw the feed.
@MainActor
@Observable
private final class HomeScrollPosition {
    var offset: CGFloat = 0
}

/// Blurs posts under the clock once the page has scrolled, leaving the backdrop alone at the top of the page.
private struct HomeStatusBarFade: View {
    let scrollPosition: HomeScrollPosition

    var body: some View {
        DiaryTopSoftScrim()
            .opacity(min(1, scrollPosition.offset / 40))
    }
}

private struct HomeScrollingAtmosphere: View {
    let media: MediaSummary?
    let scrollPosition: HomeScrollPosition

    var body: some View {
        HomeArtworkAtmosphere(media: media)
            .offset(y: BackdropLayout.topOffset - scrollPosition.offset)
            // Fade the flattened backdrop: opacity on the layers alone also thins the gradient that hides
            // the artwork's bottom edge, which then shows as a hard line while scrolling.
            .compositingGroup()
            .opacity(HomeBackdropMotion.opacity(for: scrollPosition.offset))
    }
}

private struct HomeArtworkAtmosphere: View {
    let media: MediaSummary?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                if let artworkURL {
                    SpineAsyncImage(url: artworkURL) { phase in
                        if case let .success(image) = phase {
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(width: proxy.size.width, height: 420)
                                .clipped()
                        } else {
                            Color.clear
                        }
                    }
                    .blur(radius: 12)
                    .scaleEffect(1.14)
                    .saturation(1.15)
                    .opacity(0.62)
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.28), location: 0),
                        .init(color: .clear, location: 0.35),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

            }
            .frame(width: proxy.size.width, height: 420)
            // Fades out instead of painting the page's color over itself, so the wash shows through below.
            .mask {
                LinearGradient(
                    stops: [
                        .init(color: .black, location: 0.32),
                        .init(color: .black.opacity(0.28), location: 0.72),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
        }
        .frame(height: 420)
        .clipped()
        .ignoresSafeArea(edges: .top)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private var artworkURL: URL? {
        HomeArtworkAtmosphereModel.url(for: media)
    }
}

enum HomeArtworkAtmosphereModel {
    static func url(for media: MediaSummary?) -> URL? {
        URL(string: media?.displayBackdropURL ?? media?.displayPosterURL ?? "")
    }
}

private struct HomeAvatar: View {
    let profile: UserProfile?
    var size: CGFloat = 42

    var body: some View {
        SpineAsyncImage(url: URL(string: profile?.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.crop.circle.fill")
                    .resizable()
                    .foregroundStyle(.white.opacity(0.42))
                    .padding(4)
            }
        }
        .frame(width: size, height: size)
        .background(.white.opacity(0.10), in: Circle())
        .clipShape(Circle())
        .overlay {
            Circle().stroke(.white.opacity(0.12), lineWidth: 1)
        }
    }
}

private struct HomeInlineState: View {
    let title: String
    let systemImage: String
    let message: String
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white.opacity(0.56))
                .frame(width: 30)

            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(.white)

                Text(message)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.52))

                if let actionTitle, let action {
                    Button(actionTitle, action: action)
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white.opacity(0.88))
                        .padding(.top, 2)
                }
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
    }
}

private struct HomeInProgressSkeleton: View {
    var body: some View {
        HStack(spacing: 12) {
            ForEach(0 ..< 3, id: \.self) { _ in
                VStack(alignment: .leading, spacing: 8) {
                    RoundedRectangle(cornerRadius: PosterSlot.carousel.cornerRadius, style: .continuous)
                        .fill(.white.opacity(0.08))
                        .frame(width: PosterSlot.carousel.size.width, height: PosterSlot.carousel.size.height)

                    RoundedRectangle(cornerRadius: 4)
                        .fill(.white.opacity(0.08))
                        .frame(width: 86, height: 12)

                    RoundedRectangle(cornerRadius: 4)
                        .fill(.white.opacity(0.06))
                        .frame(width: 54, height: 10)
                }
            }
        }
        .redacted(reason: .placeholder)
        .accessibilityHidden(true)
    }
}
