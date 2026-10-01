import SwiftUI

@MainActor
@Observable
final class HomeViewModel {
    var profile: UserProfile?
    var inProgressItems: [LibraryItem] = []
    var activityItems: [ActivityItem] = []
    var isLoading = true
    var isLoadingInProgress = false
    var isLoadingActivity = false
    var profileErrorMessage: String?
    var inProgressErrorMessage: String?
    var activityErrorMessage: String?

    private let profileRepository: ProfileRepository
    private let trackingRepository: TrackingRepository
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        trackingRepository: TrackingRepository,
        activityRepository: ActivityRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        profileErrorMessage = nil
        defer { isLoading = false }

        do {
            profile = try await profileRepository.me()
        } catch is CancellationError {
            return
        } catch {
            profileErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return
        }

        isLoadingInProgress = inProgressItems.isEmpty
        isLoadingActivity = activityItems.isEmpty
        await loadInProgress()
        await loadActivity()
    }

    func reload() async {
        await load()
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
        } catch is CancellationError {
            return
        } catch {
            inProgressErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func loadActivity() async {
        guard let username = profile?.username else {
            activityItems = []
            activityErrorMessage = nil
            isLoadingActivity = false
            return
        }
        isLoadingActivity = true
        activityErrorMessage = nil
        defer { isLoadingActivity = false }

        do {
            activityItems = try await activityRepository.userActivity(username: username, limit: 6)
        } catch is CancellationError {
            return
        } catch {
            activityErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
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
    @State private var selectedPerson: PersonRef?
    @State private var selectedActivityEntry: ActivityEntrySelection?
    @State private var scrollOffset: CGFloat = 0

    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let activityRepository: ActivityRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
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
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: HomeViewModel(
            profileRepository: profileRepository,
            trackingRepository: trackingRepository,
            activityRepository: activityRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack(alignment: .top) {
                SpinePageBackground()
                HomeArtworkAtmosphere(media: viewModel.inProgressItems.first?.media)
                    .offset(y: BackdropLayout.topOffset - scrollOffset)
                    .opacity(HomeBackdropMotion.opacity(for: scrollOffset))

                ScrollView(showsIndicators: false) {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        header
                        content
                            .spineContentTransition(value: contentPhase)
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
                    scrollOffset = offset
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
            .onReceive(NotificationCenter.default.publisher(for: .myAnimeListImportDidSucceed)) { _ in
                Task { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
                Task { await viewModel.reload() }
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
            .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
                MediaDetailView(
                    ref: ref,
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
            .fullScreenCover(item: $selectedActivityEntry, onDismiss: { selectedActivityEntry = nil }) { selection in
                DiaryLogDetailNavigationCover(
                    entryId: selection.id,
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
                HomeSection(title: "In Progress") {
                    HomeInProgressSkeleton()
                }
                HomeSection(title: "Activity") {
                    ActivityFeedSkeleton()
                }
            }
        } else {
            inProgressSection
            activitySection
        }
    }

    private var inProgressSection: some View {
        HomeSection(title: "In Progress") {
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
                    HomeInProgressRail(items: viewModel.inProgressItems) { item in
                        selectedRef = item.media.ref
                    }
                }
            }
            .spineContentTransition(value: inProgressPhase)
        }
    }

    private var activitySection: some View {
        HomeSection(title: "Activity") {
            Group {
                if viewModel.isLoadingActivity, viewModel.activityItems.isEmpty {
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
                } else if viewModel.activityItems.isEmpty {
                    HomeInlineState(
                        title: "No activity yet",
                        systemImage: "clock.arrow.circlepath",
                        message: "Diary logs and progress updates will appear here."
                    )
                } else {
                    LazyVStack(spacing: 0) {
                        ForEach(viewModel.activityItems) { activity in
                            Button {
                                handleActivityTap(activity)
                            } label: {
                                ActivityFeedRow(activity: activity)
                            }
                            .buttonStyle(.plain)
                            .accessibilityHint(ActivityFeedPresentation.destinationHint(for: activity))

                            if activity.id != viewModel.activityItems.last?.id {
                                ActivityFeedDivider()
                            }
                        }

                        if let username = viewModel.profile?.username {
                            ActivityFeedDivider()

                            NavigationLink {
                                ActivityFeedView(
                                    username: username,
                                    activityRepository: activityRepository,
                                    onUnauthorized: onUnauthorized,
                                    onSelectActivity: handleActivityTap
                                )
                            } label: {
                                HStack(spacing: 8) {
                                    Text("View all activity")
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .font(.caption.weight(.bold))
                                }
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.74))
                                .padding(.vertical, 15)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .spineContentTransition(value: activityPhase)
        }
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
            isLoading: viewModel.isLoadingActivity,
            hasContent: !viewModel.activityItems.isEmpty,
            hasError: viewModel.activityErrorMessage != nil
        )
    }

    private func handleActivityTap(_ activity: ActivityItem) {
        switch ActivityDestination.resolve(activity) {
        case let .diary(id):
            selectedActivityEntry = ActivityEntrySelection(id: id)
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

private struct ActivityEntrySelection: Identifiable {
    let id: Int
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

                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.32),
                        .init(color: SpinePalette.pageBackground.opacity(0.72), location: 0.72),
                        .init(color: SpinePalette.pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .frame(width: proxy.size.width, height: 420)
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

private struct HomeSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title.uppercased())
                .font(.system(size: 13, weight: .black))
                .foregroundStyle(.white)
                .tracking(0.8)

            content()
        }
    }
}

private struct HomeInProgressRail: View {
    let items: [LibraryItem]
    let action: (LibraryItem) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(alignment: .top, spacing: 12) {
                ForEach(items) { item in
                    HomeInProgressPoster(item: item) {
                        action(item)
                    }
                }
            }
            .padding(.trailing, 14)
        }
    }
}

private struct HomeInProgressPoster: View {
    let item: LibraryItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                MediaArtwork(
                    url: item.media.displayPosterURL,
                    title: item.media.title,
                    slot: .carousel,
                    mediaType: item.media.ref.mediaType,
                    orientation: item.media.posterOrientation
                )
                .shadow(color: .black.opacity(0.26), radius: 10, y: 5)

                VStack(alignment: .leading, spacing: 3) {
                    Text(item.media.displayTitle)
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white)
                        .lineLimit(2)

                    if let progressDelta {
                        ProgressDeltaInlineView(delta: progressDelta)
                    } else {
                        Text(metadataText)
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.white.opacity(0.54))
                            .lineLimit(1)
                    }
                }
            }
            .frame(width: PosterSlot.carousel.size.width, alignment: .leading)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View \(item.media.displayTitle), \(metadataText)")
    }

    private var progressDelta: ProgressChangeDisplay? {
        item.tracking.latestProgressChange?.compactDisplayParts(
            preferredMode: ProgressDisplayPreferences.mode(for: item.media.ref)
        )
    }

    private var metadataText: String {
        item.tracking.homeProgressText(preferredMode: ProgressDisplayPreferences.mode(for: item.media.ref))
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
