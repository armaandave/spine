import SwiftUI

@MainActor
@Observable
final class HomeViewModel {
    var profile: UserProfile? {
        didSet { tiles.followsNobody = (profile?.counts.following ?? 0) == 0 }
    }

    var isLoading = true
    var profileErrorMessage: String?
    /// Why a write from a tile failed, for an alert. Nil when there is nothing to say.
    var writeFailure: String?

    // MARK: What the page is made of

    /// The two tiles' data, the open tile's state and the glow behind the page. Each is read only by the small views
    /// that draw it, so none of them redraws the page.
    let tiles = HomeTilesModel()
    let tilesState = HomeTilesState()
    let glowModel = HomeGlowModel()

    /// The activity of the people you follow; your own is on your profile.
    let feed: ActivityFeedViewModel
    /// The feed's day headers and cards, built from `feed` when it changes.
    let feedModel: HomeFeedModel
    /// What you did today on each title, for the lit part of the tiles' meters.
    let todayStore: HomeTodayStore

    /// Continue Watching's episode sheet (TV-100, TV-701).
    let continueWatching: ContinueWatchingTicks
    /// Every tile's Update button: shows open the episode sheet, other titles their page's progress UI (TV-701).
    let progressUpdates: InProgressUpdates
    /// Trending's "Plan to watch".
    let planWriter: TrendingPlanWriter

    // MARK: The rail, kept where the tiles read it

    var inProgressItems: [LibraryItem] {
        get { tiles.inProgress }
        set {
            tiles.inProgress = newValue
            syncGlowIdle()
        }
    }

    var isLoadingInProgress: Bool {
        get { tiles.isLoadingInProgress }
        set { tiles.isLoadingInProgress = newValue }
    }

    var inProgressErrorMessage: String? {
        get { tiles.inProgressError }
        set { tiles.inProgressError = newValue }
    }

    var activityItems: [ActivityItem] { feed.items }
    var activityErrorMessage: String? { feed.errorMessage }

    /// Where each friend stands on a title (WP17 plugs in here). It starts as nothing: no meter and no "where".
    var standing: FeedStandingProviding {
        get { feedModel.standing }
        set { feedModel.standing = newValue }
    }

    /// The main button's write for books, games, manga and comics: one at a time, the card patched from the answer.
    @ObservationIgnored private(set) lazy var quickActions = InProgressQuickActions(
        trackingRepository: trackingRepository,
        changeOrigin: continueWatching.changeOrigin,
        onUpdate: { [weak self] id, tracking in self?.railItemDidChange(id: id, tracking: tracking) },
        onUnauthorized: onUnauthorized
    )

    /// The Today refresh and the Trending fill on their way, so a test can wait for them.
    @ObservationIgnored private(set) var todayRefresh: Task<Void, Never>?
    @ObservationIgnored private(set) var trendingFill: Task<Void, Never>?

    /// Your own recent activity and the fallback titles, kept for Trending.
    @ObservationIgnored private var ownActivity: [ActivityItem] = []
    @ObservationIgnored private var fallbackTitles: [MediaSummary] = []
    /// A write from a tile changed a card since the rail loaded: the rail re-sorts on its next visit.
    @ObservationIgnored private var railNeedsResort = false

    @ObservationIgnored private let profileRepository: ProfileRepository
    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let activityRepository: ActivityRepository
    @ObservationIgnored private let onUnauthorized: () -> Void
    /// Whether Home asks for the extras the tiles show beyond the rail and the feed: today's rows, your own activity
    /// and the fallback titles for Trending. A view model built to test the rail or the feed makes none of them.
    @ObservationIgnored private let loadsTileExtras: Bool
    @ObservationIgnored private let trendingFallback: TrendingFallbackSource?

    init(
        profileRepository: ProfileRepository,
        trackingRepository: TrackingRepository,
        activityRepository: ActivityRepository,
        mediaRepository: MediaRepository? = nil,
        diaryRepository: DiaryRepository? = nil,
        listRepository: ListRepository? = nil,
        loadsTileExtras: Bool = false,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
        self.loadsTileExtras = loadsTileExtras
        trendingFallback = loadsTileExtras ? listRepository.map { TrendingFallbackSource(listRepository: $0) } : nil
        let feed = ActivityFeedViewModel(
            username: nil,
            activityRepository: activityRepository,
            onUnauthorized: onUnauthorized
        )
        self.feed = feed
        feedModel = HomeFeedModel(
            feed: feed,
            planning: FeedPlanningStore(activityRepository: activityRepository, onUnauthorized: onUnauthorized)
        )
        todayStore = HomeTodayStore(activityRepository: activityRepository, onUnauthorized: onUnauthorized)
        // The app's own repositories unless given: looked up here, on the main actor, not in a default argument.
        let mediaRepository = mediaRepository ?? AppRepositories.current().media
        let diaryRepository = diaryRepository ?? AppRepositories.current().diary
        let continueWatching = ContinueWatchingTicks(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        self.continueWatching = continueWatching
        progressUpdates = InProgressUpdates(
            continueWatching: continueWatching,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        planWriter = TrendingPlanWriter(
            trackingRepository: trackingRepository,
            changeOrigin: continueWatching.changeOrigin,
            onUnauthorized: onUnauthorized
        )
        tiles.isLoadingTrending = true
        // A write in the episode sheet updates its card where it is; the rail re-sorts when it next loads (TV-701).
        continueWatching.onUpdate = { [weak self] id, tv in
            guard let self else { return }
            inProgressItems = ContinueWatchingTicks.replacingTV(tv, id: id, in: inProgressItems)
        }
        continueWatching.onAnimeUpdate = { [weak self] ref, anime in
            guard let self else { return }
            inProgressItems = ContinueWatchingTicks.replacingAnime(anime, ref: ref, in: inProgressItems)
        }
        // So does a write from another card's progress UI.
        progressUpdates.onUpdate = { [weak self] id, state in
            guard let self else { return }
            inProgressItems = InProgressUpdates.replacingProgress(state, id: id, in: inProgressItems)
        }
        feedModel.onFeedChange = { [weak self] in self?.feedDidChange() }
        feedModel.onNextPage = { [weak self] in self?.standing.prepare(for: self?.feed.items ?? []) }
    }

    // MARK: Loading

    func load() async {
        tiles.isLoadingTrending = tiles.trending.isEmpty
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

        await loadInProgress(forceToday: true)
        return true
    }

    /// The first load, when the screen appears: nothing happens once the profile is in.
    func loadIfNeeded() async {
        guard profile == nil else { return }
        await load()
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
    /// game's progress) keeps its card where it is (TV-701), and so does the tile's own quick action; any other write
    /// reloads the rail. The feed is only the people you follow, so your own writes can't change it, and it keeps its
    /// place however far down you are.
    func mediaStateDidChange(origin: UUID?) async {
        guard origin != continueWatching.changeOrigin else { return }
        guard await loadProfileAndInProgress() else { return }
        await feed.loadIfNeeded()
    }

    /// Loads the rail, and then today's rows for the lit part of the tiles. `forceToday` skips the twenty-second
    /// throttle: a pull to refresh, or coming back after a write.
    func loadInProgress(forceToday: Bool = false) async {
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
            let items = try await InProgressLibraryLoader.load(
                mediaTypes: mediaTypes,
                trackingRepository: trackingRepository,
                limit: 10
            )
            tiles.baselines = Self.baselines(for: items)
            inProgressItems = items
            railNeedsResort = false
            continueWatching.railDidLoad()
            progressUpdates.railDidLoad()
            refreshToday(force: forceToday)
        } catch is CancellationError {
            return
        } catch {
            inProgressErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    /// A write from a card's own progress UI changed a card since the rail last loaded (TV-701).
    var inProgressNeedsReload: Bool {
        continueWatching.needsReload || progressUpdates.needsReload || railNeedsResort
    }

    func loadActivity() async {
        guard profile != nil else { return }
        await feed.load()
        feedDidLoad()
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }

    // MARK: Today

    /// Where each title stood when the rail loaded, in its tile's unit: gains since then are lit with today's.
    nonisolated static func baselines(for items: [LibraryItem]) -> [String: Double] {
        var result: [String: Double] = [:]
        for item in items {
            if let position = InProgressTilePresenter.position(for: item) { result[item.id] = position.current }
        }
        return result
    }

    /// Asks for today's rows after the rail has loaded, alongside the feed. Your own writes from the tiles never
    /// come here: the local change covers them.
    private func refreshToday(force: Bool) {
        guard loadsTileExtras, let username = profile?.username else { return }
        todayRefresh = Task { [weak self] in
            guard let self else { return }
            await todayStore.refresh(username: username, force: force)
            if tiles.todayParts != todayStore.parts { tiles.todayParts = todayStore.parts }
        }
    }

    /// How many episodes of a show or an anime were watched today, for its sheet to open on. Nil for the rest.
    func todayCount(for item: LibraryItem) -> Int? {
        let mediaType = item.media.ref.mediaType
        guard mediaType == "tv" || mediaType == "anime",
              let position = InProgressTilePresenter.position(for: item) else { return nil }
        let amount = TileTodayMath.today(
            server: tiles.todayParts[HomeTodayStore.titleKey(for: item.media.ref)],
            baseline: tiles.baselines[item.id],
            current: position.current,
            unit: position.unit,
            total: position.pagesTotal ?? position.total
        )
        return Int(amount.rounded())
    }

    /// All of today's progress on a title as its tile counts it (the server's rows plus what moved on this phone since
    /// Home loaded), in the tile's unit: what a book's or a game's progress sheet lights beside its number.
    func todayPart(for ref: MediaRef) -> TodayPart? {
        guard let item = tiles.inProgress.first(where: { $0.media.ref.id == ref.id }),
              let position = InProgressTilePresenter.position(for: item) else { return nil }
        let amount = TileTodayMath.today(
            server: tiles.todayParts[HomeTodayStore.titleKey(for: ref)],
            baseline: tiles.baselines[item.id],
            current: position.current,
            unit: position.unit,
            total: position.pagesTotal ?? position.total
        )
        return TodayPart(unit: position.unit, amount: amount)
    }

    // MARK: Trending and the glow

    /// The first page of the feed is in: friends' standings start loading, and Trending reads what it can now and
    /// fills in from your own activity and the fallback list as they arrive.
    private func feedDidLoad() {
        standing.prepare(for: feed.items)
        recomputeTrending()
        guard loadsTileExtras, let username = profile?.username else {
            tiles.isLoadingTrending = false
            return
        }
        trendingFill?.cancel()
        trendingFill = Task { [weak self] in await self?.fillTrending(username: username) }
    }

    private func fillTrending(username: String) async {
        defer { tiles.isLoadingTrending = false }
        if let page = try? await activityRepository.userActivityPage(username: username, pageSize: 25, cursorLink: nil),
           !Task.isCancelled {
            ownActivity = page.results
            recomputeTrending()
        }
        guard !Task.isCancelled, let trendingFallback,
              TrendingAggregator.needsFallback(feed: feed.items, own: ownActivity, you: youSummary) else { return }
        let titles = await trendingFallback.load()
        guard !Task.isCancelled else { return }
        fallbackTitles = titles
        recomputeTrending()
    }

    /// The feed's items changed: Trending and the glow follow, and the cards were rebuilt before this.
    private func feedDidChange() {
        recomputeTrending()
        syncGlowIdle()
    }

    private var youSummary: UserSummary? {
        profile.map { UserSummary(id: $0.id, username: $0.username, displayName: $0.displayName, avatarUrl: $0.avatarUrl) }
    }

    private func recomputeTrending() {
        let result = TrendingAggregator.titles(
            feed: feed.items,
            own: ownActivity,
            you: youSummary,
            fallback: fallbackTitles
        )
        if tiles.trending != result.titles { tiles.trending = result.titles }
        if tiles.trendingSubtitle != result.subtitle { tiles.trendingSubtitle = result.subtitle }
        // The right of the glow at rest.
        let lead = tiles.trending.first?.media
        if glowModel.trending != lead { glowModel.trending = lead }
    }

    /// The glow rests on the first In progress title, else the newest post's art.
    private func syncGlowIdle() {
        let media = tiles.inProgress.first?.media ?? feed.items.first?.media
        if glowModel.idle != media { glowModel.idle = media }
    }

    // MARK: Writes from the tiles

    /// What a tile's main button did.
    enum QuickActionOutcome: Equatable {
        /// Written, or the screen already says why not.
        case saved
        /// Nothing was written: the title's own progress UI opens instead (it reached its end, or has no quick write).
        case opensUpdate
        /// The write failed and the card is as it was. The message is nil when the screen already shows it.
        case failed(String?)
    }

    /// The In progress tile's main button for `item`: a show's or an anime's next episode, or pages, percent, hours or
    /// chapters. Writes nothing for a plan that would reach the end.
    func runQuickAction(_ item: LibraryItem) async -> QuickActionOutcome {
        guard let plan = QuickActionPlan.make(for: item) else { return .opensUpdate }
        switch plan {
        case .openSheet:
            return .opensUpdate
        case .tickUpNext:
            tiles.busyItemIDs.insert(item.id)
            defer { tiles.busyItemIDs.remove(item.id) }
            continueWatching.quickTick(for: item)
            await continueWatching.quickTickSettling?.value
            let failed = item.media.ref.isAnime
                ? continueWatching.closedAnimeEpisodes?.page.quickActionErrorMessage
                : continueWatching.closedEpisodes?.season.quickActionErrorMessage
            return failed == nil ? .saved : .failed(nil)
        default:
            tiles.busyItemIDs.insert(item.id)
            defer { tiles.busyItemIDs.remove(item.id) }
            switch await quickActions.run(plan, on: item) {
            case .done:
                return .saved
            case .openSheet:
                return .opensUpdate
            case let .failed(message):
                writeFailure = message
                return .failed(message)
            }
        }
    }

    private func railItemDidChange(id: String, tracking: TrackingState) {
        inProgressItems = inProgressItems.map { item in
            item.id == id ? LibraryItem(media: item.media, tracking: tracking) : item
        }
        railNeedsResort = true
    }

    /// Trending's "Plan to watch": the title shows as planned at once and goes back if the write fails. The task is
    /// done when the write has settled.
    @discardableResult
    func planToWatch(_ title: TrendingTitle) -> Task<Void, Never> {
        let write = planWriter.plan(title)
        tiles.plannedIDs = planWriter.plannedIDs
        tiles.busyItemIDs.insert(title.key)
        return Task { [weak self] in
            _ = await write.value
            guard let self else { return }
            tiles.busyItemIDs.remove(title.key)
            tiles.plannedIDs = planWriter.plannedIDs
            if let message = planWriter.errorMessage {
                writeFailure = message
                planWriter.dismissError()
            }
        }
    }
}

struct HomeView: View {
    /// The glow is off screen, and the status bar's fade full, long before this: past it the offset stops changing.
    private static let scrollCap = HomeDesign.Metric.glowHeight + 60

    @State private var viewModel: HomeViewModel
    @State private var selectedRef: MediaRef?
    /// The season's episodes, when Continue Watching's sheet opened one of them: its page pages through them.
    @State private var selectedEpisodes: MediaBrowsingContext?
    @State private var selectedPerson: PersonRef?
    @State private var selectedPost: ActivityPostSelection?
    /// A day of Planning adds, open on its titles.
    @State private var selectedPlanning: ActivityItem?

    // The repositories and closures are the session's and the shell's own, so a shell that runs again does not run the
    // page again (`StableInput`); the user and the selected tab are what the page redraws for.
    @StableInput private var mediaRepository: MediaRepository
    @StableInput private var trackingRepository: TrackingRepository
    @StableInput private var diaryRepository: DiaryRepository
    @StableInput private var activityRepository: ActivityRepository
    @StableInput private var listRepository: ListRepository
    @StableInput private var peopleRepository: PeopleRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    @StableInput private var onSelectTab: (AppTab) -> Void
    @StableInput private var onFindPeople: () -> Void
    @StableInput private var onUnauthorized: () -> Void

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
            listRepository: listRepository,
            loadsTileExtras: true,
            onUnauthorized: onUnauthorized
        ))
    }

    /// Reads only the covers' selection. The tiles, the feed and the glow are handed to the views that draw them as
    /// objects, and none of their properties is read here, so scrolling, swiping a carousel and a quick action
    /// do not run this again.
    var body: some View {
        let _ = PerfCounters.bump(.homeBody)
        let glow = viewModel.glowModel
        NavigationStack {
            ZStack(alignment: .top) {
                HomeGlow(model: glow)

                ScrollView(showsIndicators: false) {
                    // One lazy stack, with the top of the page as one plain item and then one item per day header
                    // or card. A frozen Home was sampled looping in nested lazy stacks' placement of the activity
                    // rows, which lazy stacks size from estimated heights (TV-705).
                    LazyVStack(alignment: .leading, spacing: 0) {
                        HomeTop(
                            viewModel: viewModel,
                            actions: tileActions,
                            onOpenProfile: { onSelectTab(.profile) },
                            onFindPeople: onFindPeople
                        )
                        HomeFeedRows(model: viewModel.feedModel, onOpen: openCard)
                        HomeFeedFooter(feed: viewModel.feed)
                    }
                    .padding(.bottom, 92)
                }
                .refreshable {
                    await viewModel.reload()
                }
                .onScrollGeometryChange(for: CGFloat.self) { geometry in
                    // How far the page has moved, without the inset that holds it below the status bar.
                    min(max(0, geometry.contentOffset.y + geometry.contentInsets.top), Self.scrollCap)
                } action: { _, offset in
                    glow.scrollOffset = offset
                }

                HomeStatusBarFade(model: glow)
            }
            .background {
                HomeDesign.Palette.pageBackground
                    .ignoresSafeArea()
            }
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                await viewModel.loadIfNeeded()
            }
            .modifier(HomeRefreshTriggers(viewModel: viewModel, selectedTab: selectedTab))
            .modifier(HomeWriteFailureAlert(viewModel: viewModel))
            .onChange(of: selectedRef) { oldValue, newValue in
                if oldValue != nil, newValue == nil {
                    Task { await viewModel.loadInProgress(forceToday: true) }
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
                screenOpenedOther: selectedRef != nil || selectedPerson != nil || selectedPost != nil || selectedPlanning != nil,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized
            ) { ref in
                selectedRef = ref
            }
            // The book's and the game's progress sheets light all of today's part, as the tile does.
            .environment(\.progressSheetTodayPart, { [viewModel] ref in viewModel.todayPart(for: ref) })
            .fullScreenCover(item: $selectedPlanning, onDismiss: { selectedPlanning = nil }) { activity in
                if let planning = activity.planning {
                    NavigationStack {
                        PlanningTitlesView(
                            activity: activity,
                            planning: planning,
                            activityRepository: activityRepository,
                            diaryRepository: diaryRepository,
                            mediaRepository: mediaRepository,
                            trackingRepository: trackingRepository,
                            currentUserId: currentUserId,
                            selectedTab: selectedTab,
                            onSelectTab: onSelectTab,
                            onUnauthorized: onUnauthorized,
                            onClose: { selectedPlanning = nil }
                        )
                    }
                }
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

    // MARK: Actions

    /// What the open tile's buttons do. Built here, where the covers are, and handed to the tiles.
    private var tileActions: HomeTilesActions {
        HomeTilesActions(
            onOpenTitle: { selectedRef = $0 },
            onUpdate: { updateProgress($0) },
            onQuickAction: { await quickAction($0) },
            onPlanToWatch: { viewModel.planToWatch($0) },
            onOpenTrending: { selectedRef = $0.media.ref },
            onFindSomething: { onSelectTab(.search) },
            onFindPeople: onFindPeople
        )
    }

    /// A tile's Update: the title's progress UI over Home, or its page when it has none.
    private func updateProgress(_ item: LibraryItem) {
        let todayCount = viewModel.todayCount(for: item)
        Task {
            if let ref = await viewModel.progressUpdates.updateProgress(for: item, todayCount: todayCount) {
                selectedRef = ref
            }
        }
    }

    /// A tile's main button. Returns whether it was saved, for the success or error haptic.
    private func quickAction(_ item: LibraryItem) async -> Bool {
        switch await viewModel.runQuickAction(item) {
        case .saved:
            return true
        case .opensUpdate:
            updateProgress(item)
            return true
        case .failed:
            return false
        }
    }

    /// A card's tap. The row is looked up now, so a refreshed row never opens stale data.
    private func openCard(_ id: Int) {
        guard let activity = viewModel.feedModel.item(forCardID: id) else { return }
        open(activity)
    }

    private func open(_ activity: ActivityItem) {
        switch ActivityDestination.resolve(activity) {
        case let .diary(id):
            selectedPost = ActivityPostSelection(post: .log(id))
        case let .progress(id):
            selectedPost = ActivityPostSelection(post: .progress(id))
        case .planning:
            selectedPlanning = activity
        case let .person(ref):
            selectedPerson = ref
        case let .media(ref):
            selectedRef = ref
        case .none:
            break
        }
    }
}

/// What makes Home load again: an import, a follow, a change to a title, a profile edit, and coming back to the tab
/// after a card's progress changed. A modifier of its own, so the page's body stays short.
private struct HomeRefreshTriggers: ViewModifier {
    let viewModel: HomeViewModel
    let selectedTab: AppTab

    func body(content: Content) -> some View {
        content
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
                    Task { await viewModel.loadInProgress(forceToday: true) }
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
    }
}

/// The alert for a write from a tile that failed. It reads the failure, so the page does not.
private struct HomeWriteFailureAlert: ViewModifier {
    let viewModel: HomeViewModel

    func body(content: Content) -> some View {
        content
            .alert("Tracking Update Failed", isPresented: Binding(
                get: { viewModel.writeFailure != nil },
                set: { if !$0 { viewModel.writeFailure = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(viewModel.writeFailure ?? "")
            }
            .sensoryFeedback(.error, trigger: viewModel.writeFailure) { _, message in message != nil }
    }
}

private struct ActivityPostSelection: Identifiable {
    let post: ActivityPostRef

    var id: String { post.id }
}

/// Blurs posts under the clock once the page has scrolled, leaving the glow alone at the top of the page.
private struct HomeStatusBarFade: View {
    let model: HomeGlowModel

    var body: some View {
        DiaryTopSoftScrim()
            .opacity(min(1, model.scrollOffset / 40))
    }
}

enum HomeArtworkAtmosphereModel {
    static func url(for media: MediaSummary?) -> URL? {
        URL(string: media?.displayBackdropURL ?? media?.displayPosterURL ?? "")
    }
}
