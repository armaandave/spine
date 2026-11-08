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
    /// The people you follow, for the Friends row.
    let friends = HomeFriendsModel()

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
    /// The viewer's state for the Trending titles in focus: what the panel's rail is made from and writes through.
    let railStore: TrendingRailStore

    // MARK: The rail, kept where the tiles read it

    var inProgressItems: [LibraryItem] {
        get { tiles.inProgress }
        set {
            tiles.inProgress = newValue
            syncGlowIdle()
            syncSeasons()
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

    /// The Today refresh, the Trending fill and the shows' season reads on their way, so a test can wait for them.
    @ObservationIgnored private(set) var todayRefresh: Task<Void, Never>?
    @ObservationIgnored private(set) var trendingFill: Task<Void, Never>?
    @ObservationIgnored private(set) var seasonRefresh: Task<Void, Never>?
    /// The season read each show's `tiles.seasons` entry came from or is waiting for ("2|14": season, version), by
    /// item id. A show is read again only when its season or its version changes.
    @ObservationIgnored private var seasonReads: [String: String] = [:]

    /// Your own recent activity and the fallback titles, kept for Trending.
    @ObservationIgnored private var ownActivity: [ActivityItem] = []
    @ObservationIgnored private var fallbackTitles: [MediaSummary] = []
    /// A write from a tile changed a card since the rail loaded: the rail re-sorts on its next visit.
    @ObservationIgnored private var railNeedsResort = false

    @ObservationIgnored private let profileRepository: ProfileRepository
    @ObservationIgnored private let trackingRepository: TrackingRepository
    /// For a season with nothing watched yet, which has no tracking: its page still counts its episodes.
    @ObservationIgnored private let seasonPages: MediaRepository
    @ObservationIgnored private let activityRepository: ActivityRepository
    /// Who you follow, for the Friends row. Nil when Home makes no extra reads (`loadsTileExtras`).
    @ObservationIgnored private let socialRepository: SocialRepository?
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
        socialRepository: SocialRepository? = nil,
        loadsTileExtras: Bool = false,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
        self.loadsTileExtras = loadsTileExtras
        self.socialRepository = loadsTileExtras ? socialRepository : nil
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
        seasonPages = mediaRepository
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
        railStore = TrendingRailStore(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        tiles.railStore = railStore
        tiles.isLoadingTrending = true
        // A write in the episode sheet updates its card where it is; the rail re-sorts when it next loads (TV-701).
        // A title the write finished (or paused, or caught up) leaves the rail at once (`patchRail`).
        continueWatching.onUpdate = { [weak self] id, tv in
            guard let self else { return }
            patchRail(ContinueWatchingTicks.replacingTV(tv, id: id, in: inProgressItems), changed: [id])
        }
        continueWatching.onAnimeUpdate = { [weak self] ref, anime in
            guard let self else { return }
            let ids = Set(inProgressItems.filter { $0.media.ref.id == ref.id }.map(\.id))
            patchRail(ContinueWatchingTicks.replacingAnime(anime, ref: ref, in: inProgressItems), changed: ids)
        }
        // So does a write from another card's progress UI.
        progressUpdates.onUpdate = { [weak self] id, state in
            guard let self else { return }
            patchRail(InProgressUpdates.replacingProgress(state, id: id, in: inProgressItems), changed: [id])
        }
        feedModel.onFeedChange = { [weak self] in self?.feedDidChange() }
        feedModel.onNextPage = { [weak self] in self?.standing.prepare(for: self?.feed.items ?? []) }
        // Friends' standings (the meter and the "where" on their progress cards) come from their lists. A view model
        // built to test the rail or the feed keeps none.
        if loadsTileExtras {
            feedModel.standing = FriendStandings(trackingRepository: trackingRepository, onUnauthorized: onUnauthorized)
        }
    }

    // MARK: Loading

    func load() async {
        // The feed's duotone marks are drawn once, before the first card needs one.
        FeedMarkImages.warm()
        tiles.isLoadingTrending = tiles.trending.isEmpty
        guard await loadProfileAndInProgress() else { return }
        async let friendsLoad: Void = loadFriends()
        await loadActivity()
        await friendsLoad
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
        async let friendsLoad: Void = loadFriends()
        await loadActivity()
        await friendsLoad
    }

    /// The first page of the people you follow, for the Friends row. A read that fails leaves the row as it was.
    func loadFriends() async {
        guard let socialRepository, let username = profile?.username else { return }
        guard let page = try? await socialRepository.followList(username: username, followers: false, page: nil),
              !Task.isCancelled else { return }
        friends.setFollowing(page.results, feed: feed.items)
    }

    /// You changed a title. A write from a card's own progress UI (a tick in Continue Watching's sheet, or a book's or
    /// game's progress) keeps its card where it is (TV-701), and so does the tile's own quick action; a write from the
    /// Trending panel's rail leaves everything where it is while the tile is open and re-sorts the rail when it
    /// closes. Any other write reloads the rail. The feed is only the people you follow, so your own writes can't
    /// change it, and it keeps its place however far down you are.
    func mediaStateDidChange(origin: UUID?, ref: MediaRef? = nil) async {
        guard origin != continueWatching.changeOrigin else { return }
        if let origin, railStore.changeOrigins.contains(origin) {
            railNeedsResort = true
            return
        }
        // A title's page finished it: it leaves the rail now, not when the reload below has come back.
        if let ref { await dropFromRailIfNoLongerInProgress(ref) }
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

    /// Reads the current season of each show in the rail (`InProgressTilePresenter.currentSeasonNumber`) that has not
    /// been read at its version, one small tracking read each, so its panel counts the season you are in. A season with
    /// nothing watched yet has no tracking, so its page is read instead. A write's answer that already carries that
    /// season is used as it is. Until a read lands the panel says "Season 2".
    private func syncSeasons() {
        guard loadsTileExtras else { return }
        let items = tiles.inProgress
        let ids = Set(items.map(\.id))
        let kept = tiles.seasons.filter { ids.contains($0.key) }
        if kept.count != tiles.seasons.count { tiles.seasons = kept }
        seasonReads = seasonReads.filter { ids.contains($0.key) }

        var reads: [(id: String, key: String, ref: MediaRef, number: Int, showWatched: Int)] = []
        for item in items {
            // A show with no count of its own has no place to show either (`position(for:)` is nil).
            guard let number = InProgressTilePresenter.currentSeasonNumber(for: item),
                  let tv = item.tracking.tv, let showWatched = tv.watchedCount else { continue }
            let key = "\(number)|\(tv.version)"
            if let state = tv.season, state.seasonNumber == number,
               let season = TileSeason(state: state, showWatched: showWatched) {
                seasonReads[item.id] = key
                if tiles.seasons[item.id] != season { tiles.seasons[item.id] = season }
                continue
            }
            guard seasonReads[item.id] != key else { continue }
            seasonReads[item.id] = key
            let ref = ContinueWatchingEpisodes.seasonRef(show: item.media.ref, seasonNumber: number)
            reads.append((item.id, key, ref, number, showWatched))
        }
        guard !reads.isEmpty else { return }

        seasonRefresh = Task { [weak self] in
            for read in reads {
                // A read a newer one has replaced is not made.
                guard let self, seasonReads[read.id] == read.key else { continue }
                let state: TVSeasonState?
                do {
                    state = try await trackingRepository.detail(ref: read.ref).tv?.season
                } catch APIError.httpStatus(404, _) {
                    // Nothing watched in it yet (the season after the one just finished, or one not aired), so it
                    // has no tracking (TV-205); its page still counts its episodes, with none watched.
                    state = try? await seasonPages.detail(ref: read.ref).userState?.tv?.season
                } catch {
                    state = nil
                }
                guard seasonReads[read.id] == read.key else { continue }
                // A read that failed is tried again the next time the rail changes or loads.
                guard let state else {
                    seasonReads[read.id] = nil
                    continue
                }
                guard state.seasonNumber == read.number,
                      let season = TileSeason(state: state, showWatched: read.showWatched) else { continue }
                if tiles.seasons[read.id] != season { tiles.seasons[read.id] = season }
            }
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
        standing.reload(for: feed.items)
        friends.reorder(feed: feed.items)
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
        friends.reorder(feed: feed.items)
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
        patchRail(inProgressItems.map { item in
            item.id == id ? LibraryItem(media: item.media, tracking: tracking) : item
        }, changed: [id])
        railNeedsResort = true
    }

    // MARK: A write that finishes a title

    /// Sets the rail to `items`, a patch of the write's answer into the cards `changed`. A card that is no longer In
    /// progress (finished, paused, dropped, caught up) leaves the rail now, animated; the open tile's focus moves to a
    /// neighbor (`HomeTilesState.sync`), and the count and the glow follow the rail.
    private func patchRail(_ items: [LibraryItem], changed: Set<String>) {
        let kept = items.filter { !changed.contains($0.id) || InProgressRailMembership.stays($0) }
        guard kept.count != items.count else {
            inProgressItems = items
            return
        }
        withAnimation(TileMotion.frame(reduceMotion: UIAccessibility.isReduceMotionEnabled)) {
            inProgressItems = kept
        }
    }

    /// A title's page changed `ref`, and the answer only says which title. A title in the rail is read once; if it
    /// is no longer In progress it leaves. Nothing else about the card is taken from that read: the reload that
    /// follows brings the rest.
    private func dropFromRailIfNoLongerInProgress(_ ref: MediaRef) async {
        guard let item = inProgressItems.first(where: { $0.media.ref.id == ref.id }),
              let tracking = try? await trackingRepository.detail(ref: item.media.ref),
              !InProgressRailMembership.stays(LibraryItem(media: item.media, tracking: tracking)) else { return }
        patchRail(inProgressItems.filter { $0.id != item.id }, changed: [])
    }

    /// The Trending tile closed: a rail write that changed a title in the In progress rail re-sorts it now, once.
    func trendingTileDidClose() async {
        guard railNeedsResort else { return }
        await loadInProgress(forceToday: true)
    }
}

struct HomeView: View {
    /// The glow is off screen, and the status bar's fade full, long before this: past it the offset stops changing.
    private static let scrollCap = HomeDesign.Metric.glowHeight + 60

    @State private var viewModel: HomeViewModel
    @State private var selectedRef: MediaRef?
    /// What the Trending rail left for the page to finish (a log composer, a sheet), when it opened the page.
    @State private var selectedHandoff: RailHandoff?
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
        socialRepository: SocialRepository = AppRepositories.current().social,
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
            socialRepository: socialRepository,
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
                    // Where the tiles sit in here does not change as the page scrolls; the glow ends by them.
                    .coordinateSpace(.named(HomeGlowModel.contentSpace))
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
                .onScrollGeometryChange(for: CGFloat.self) { geometry in
                    geometry.contentInsets.top
                } action: { _, top in
                    if glow.contentTopInset != top { glow.contentTopInset = top }
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
                selectedHandoff = nil
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
                    onUnauthorized: onUnauthorized,
                    railHandoff: selectedHandoff
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
            onOpenTrending: { selectedHandoff = nil; selectedRef = $0.media.ref },
            onRailHandoff: { title, handoff in
                selectedHandoff = handoff
                selectedRef = title.media.ref
            },
            onRailMessage: { viewModel.writeFailure = $0 },
            onTileClosed: { tile in
                if tile == .trending { Task { await viewModel.trendingTileDidClose() } }
            },
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
                Task {
                    await viewModel.mediaStateDidChange(
                        origin: notification.userInfo?["origin"] as? UUID,
                        ref: notification.userInfo?["ref"] as? MediaRef
                    )
                }
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
