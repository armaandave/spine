import SwiftUI

/// Continue Watching's inline tick on Home and Profile (TV-100, TV-701). It ticks a show's up-next episode with a
/// mutation ID, shows the tick at once, and restores the row when the tick fails (TV-705). The response goes to the
/// show's `TVWriteFollowUp`, which asks the earlier-episodes question and opens the season-finish composer (TV-107,
/// TV-403). Ticks on several shows each keep their follow-up; they show one at a time, oldest first.
@MainActor
@Observable
final class ContinueWatchingTicks {
    /// Rows with a tick in flight: their tick shows as done until the server answers.
    private(set) var pendingIDs: Set<String> = []
    var errorMessage: String?
    /// One follow-up per show, in the order their ticks answered.
    private(set) var followUps: [TVWriteFollowUp] = []
    /// The follow-up for the screen to present: the oldest one with something to show, else the latest, so a
    /// composer that is closing keeps its host.
    var followUp: TVWriteFollowUp? { followUps.first(where: \.isActive) ?? followUps.last }
    /// Recognizes this screen's own state-change posts.
    let changeOrigin = UUID()

    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private let onUnauthorized: () -> Void

    init(trackingRepository: TrackingRepository, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.trackingRepository = trackingRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func isPending(_ item: LibraryItem) -> Bool {
        pendingIDs.contains(item.id)
    }

    /// Ticks the show's up-next episode. Returns the server's answer, or nil when there was nothing to tick or the
    /// tick failed (the row then shows its last confirmed state).
    func tick(_ item: LibraryItem) async -> TVActionResponse? {
        let ref = item.media.ref
        guard ref.mediaType == "tv", let upNext = item.tracking.tv?.upNext, !pendingIDs.contains(item.id) else {
            return nil
        }
        pendingIDs.insert(item.id)
        errorMessage = nil
        defer { pendingIDs.remove(item.id) }

        do {
            let response = try await trackingRepository.performTVAction(
                source: ref.source,
                mediaId: ref.mediaId,
                action: .tick,
                request: TVActionRequest(
                    seasonNumber: upNext.seasonNumber,
                    episodeNumber: upNext.episodeNumber,
                    mutationId: UUID()
                )
            )
            followUp(for: item).handle(response.envelope)
            MediaStateChange.post(ref: ref, origin: changeOrigin)
            return response
        } catch is CancellationError {
            return nil
        } catch {
            errorMessage = (error as? APIError)?.apiErrorMessage ?? error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return nil
        }
    }

    /// The show's follow-up. A show without one gets a new one, after the follow-ups still showing or loading
    /// something, so a tick on another show never drops a question or composer (TV-403); idle ones go.
    private func followUp(for item: LibraryItem) -> TVWriteFollowUp {
        let ref = item.media.ref
        let showRef = ref.parentTVRef ?? ref
        if let existing = followUps.first(where: { $0.showRef == showRef }) {
            return existing
        }
        followUps.removeAll { !$0.isActive }
        let next = TVWriteFollowUp(showRef: ref, trackingRepository: trackingRepository, mediaRepository: mediaRepository)
        next.showTitle = item.media.title
        followUps.append(next)
        return next
    }
}

/// The row text and tick under a show in Continue Watching: "S1 E2 next" or "S3 E1 · New" (TV-701).
struct ContinueWatchingTickLine: View {
    let tv: TVShowState
    let isPending: Bool
    /// The narrow Profile rail: a smaller tick, and text that shrinks to fit.
    var compact = false
    let onTick: () -> Void

    var body: some View {
        if let upNext = tv.upNext, let line = TVTrackingPresentation.continueWatchingLine(for: tv) {
            HStack(spacing: compact ? 2 : 5) {
                Button(action: onTick) {
                    Image(systemName: isPending ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: compact ? 13 : 15, weight: .semibold))
                        .foregroundStyle(isPending ? Color.green : Color.white.opacity(0.72))
                        .contentTransition(.symbolEffect(.replace))
                        .frame(width: compact ? 20 : 24, height: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isPending)
                .sensoryFeedback(.success, trigger: isPending) { _, pending in pending }
                .accessibilityLabel("Mark \(TVTrackingPresentation.episodeCode(season: upNext.seasonNumber, episode: upNext.episodeNumber)) watched")

                Text(line)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(upNext.isNew ? Color.white : Color.white.opacity(0.54))
                    .lineLimit(1)
                    .minimumScaleFactor(compact ? 0.7 : 1)
            }
        }
    }
}

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

    /// Continue Watching's inline tick (TV-100, TV-701).
    let continueWatching: ContinueWatchingTicks

    private let profileRepository: ProfileRepository
    private let trackingRepository: TrackingRepository
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void

    init(
        profileRepository: ProfileRepository,
        trackingRepository: TrackingRepository,
        activityRepository: ActivityRepository,
        mediaRepository: MediaRepository? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
        continueWatching = ContinueWatchingTicks(
            trackingRepository: trackingRepository,
            mediaRepository: mediaRepository ?? AppRepositories.current().media,
            onUnauthorized: onUnauthorized
        )
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

    /// Ticks a show's up-next episode from Continue Watching. The row takes the server's state at once; then the
    /// rail reloads, since the show may move or leave (caught up or finished, TV-701).
    func tickUpNext(_ item: LibraryItem) async {
        guard let response = await continueWatching.tick(item) else { return }
        if let tracking = response.tracking, let index = inProgressItems.firstIndex(where: { $0.id == item.id }) {
            inProgressItems[index] = LibraryItem(media: inProgressItems[index].media, tracking: tracking)
        }
        await loadInProgress()
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
            mediaRepository: mediaRepository,
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
            .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
                // A Continue Watching tick refreshes its own rail.
                if notification.userInfo?["origin"] as? UUID == viewModel.continueWatching.changeOrigin { return }
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
            .background {
                // The earlier-episodes question, then the season-finish composer, after a Continue Watching tick.
                if let followUp = viewModel.continueWatching.followUp {
                    Color.clear
                        .tvWriteFollowUp(followUp) { item in
                            MediaLogView(
                                detail: item.detail,
                                trackingRepository: trackingRepository,
                                diaryRepository: diaryRepository,
                                onUnauthorized: onUnauthorized,
                                onSaved: {},
                                tvComposer: item.finished.composer
                            )
                        }
                }
            }
            .alert("Could not mark watched", isPresented: Binding(
                get: { viewModel.continueWatching.errorMessage != nil },
                set: { if !$0 { viewModel.continueWatching.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(viewModel.continueWatching.errorMessage ?? "")
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
                        HomeInProgressPoster(
                            item: item,
                            isTickPending: viewModel.continueWatching.isPending(item),
                            onTick: { Task { await viewModel.tickUpNext(item) } }
                        ) {
                            selectedRef = item.media.ref
                        }
                    }
                }
            }
            .spineContentTransition(value: inProgressPhase)
        }
    }

    private var activitySection: some View {
        SpineSection(title: "Activity") {
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

private struct HomeInProgressPoster: View {
    let item: LibraryItem
    var isTickPending = false
    var onTick: () -> Void = {}
    let action: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
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

                        if continueWatching == nil {
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
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("View \(item.media.displayTitle), \(metadataText)")

            // A show in Continue Watching: its up next with a tick (TV-701), outside the poster's button.
            if let continueWatching {
                ContinueWatchingTickLine(tv: continueWatching, isPending: isTickPending, onTick: onTick)
            }
        }
        .frame(width: PosterSlot.carousel.size.width, alignment: .leading)
    }

    private var continueWatching: TVShowState? {
        guard item.media.ref.mediaType == "tv", let tv = item.tracking.tv, tv.upNext != nil else { return nil }
        return tv
    }

    private var progressDelta: ProgressChangeDisplay? {
        guard item.media.ref.mediaType != "game" else { return nil }
        return item.tracking.latestProgressChange?.compactDisplayParts(
            preferredMode: ProgressDisplayPreferences.mode(for: item.media.ref)
        )
    }

    private var metadataText: String {
        if let continueWatching, let line = TVTrackingPresentation.continueWatchingLine(for: continueWatching) {
            return line
        }
        return item.tracking.homeProgressText(preferredMode: ProgressDisplayPreferences.mode(for: item.media.ref))
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
