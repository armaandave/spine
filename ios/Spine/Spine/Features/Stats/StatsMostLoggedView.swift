import SwiftUI

/// A row of a Stats show-all page: anything with a poster.
protocol StatsPosterTitle: Identifiable {
    var media: MediaSummary { get }
}

extension StatsMostLoggedTitle: StatsPosterTitle {}
extension StatsRatedTitle: StatsPosterTitle {}

/// The pages of a Stats show-all list (STAT-310), loaded a page at a time as the grid scrolls.
@MainActor
@Observable
final class StatsTitlesPageModel<Item: Identifiable> {
    struct Page {
        let count: Int
        let next: String?
        let results: [Item]
    }

    /// Loads one page: `page` is nil for the first, then the next page's number.
    typealias Fetch = @MainActor (_ page: String?, _ pageSize: Int) async throws -> Page

    let pageSize: Int
    private(set) var items: [Item] = []
    private(set) var totalCount: Int
    private(set) var isLoadingInitial = false
    private(set) var isLoadingNextPage = false
    private(set) var errorMessage: String?
    private(set) var nextPageErrorMessage: String?

    @ObservationIgnored private let fetch: Fetch
    @ObservationIgnored private let onUnauthorized: () -> Void
    private var nextPage: String?
    private var requestGeneration = 0

    init(
        pageSize: Int = 48,
        expectedTotal: Int = 0,
        onUnauthorized: @escaping () -> Void,
        fetch: @escaping Fetch
    ) {
        self.pageSize = pageSize
        self.totalCount = expectedTotal
        self.onUnauthorized = onUnauthorized
        self.fetch = fetch
    }

    var hasMorePages: Bool {
        nextPage != nil
    }

    func load() async {
        requestGeneration &+= 1
        let generation = requestGeneration
        isLoadingInitial = true
        // A next page still on its way belongs to the old generation and won't clear its own
        // flag, so clear it here or paging would stay blocked.
        isLoadingNextPage = false
        errorMessage = nil
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingInitial = false
            }
        }

        do {
            let response = try await fetch(nil, pageSize)
            guard generation == requestGeneration else { return }
            apply(response, replacingItems: true)
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            errorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    /// Loads the next page once `currentItemID` is within the artwork prefetch's reach of the
    /// end, so the next page's posters can be prefetched before they're reached.
    func loadNextPageIfNeeded(currentItemID: Item.ID) async {
        guard let currentIndex = items.firstIndex(where: { $0.id == currentItemID }),
              currentIndex >= items.count - ArtworkPrefetch.lookahead else { return }
        await loadNextPage()
    }

    func loadNextPage() async {
        guard !isLoadingInitial, !isLoadingNextPage, let page = nextPage else { return }

        let generation = requestGeneration
        isLoadingNextPage = true
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingNextPage = false
            }
        }

        do {
            let response = try await fetch(page, pageSize)
            guard generation == requestGeneration else { return }
            apply(response, replacingItems: false)
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            nextPageErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func apply(_ response: Page, replacingItems: Bool) {
        totalCount = response.count
        nextPage = APIPageCursor.nextPage(from: response.next)

        if replacingItems {
            items = response.results
        } else {
            let existingIDs = Set(items.map(\.id))
            items += response.results.filter { !existingIDs.contains($0.id) }
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

extension StatsTitlesPageModel.Page where Item: Decodable {
    init(_ response: PagedResponse<Item>) {
        self.init(count: response.count, next: response.next, results: response.results)
    }
}

typealias StatsMostLoggedViewModel = StatsTitlesPageModel<StatsMostLoggedTitle>

extension StatsTitlesPageModel where Item == StatsMostLoggedTitle {
    /// Most logged's show-all pages, folded to the show (`schema=2`, STAT-215).
    static func mostLogged(
        profileRepository: ProfileRepository,
        username: String?,
        period: StatsPeriod,
        mediaType: String?,
        expectedTotal: Int = 0,
        onUnauthorized: @escaping () -> Void
    ) -> StatsMostLoggedViewModel {
        StatsMostLoggedViewModel(expectedTotal: expectedTotal, onUnauthorized: onUnauthorized) { page, pageSize in
            let response = try await profileRepository.statsMostLoggedTitles(
                username: username,
                period: period,
                mediaType: mediaType,
                page: page,
                pageSize: pageSize
            )
            return Page(response)
        }
    }
}

/// Every title logged at least twice in the current Stats scope (STAT-215), with "5 times"
/// under each poster.
struct StatsMostLoggedView: View {
    @State private var viewModel: StatsMostLoggedViewModel
    let title: String
    let scopeTitle: String
    let drillDownContext: StatsDrillDownContext?
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let listRepository: ListRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    /// `title` is the section's label ("Most watched"); `scopeTitle` names the tab and range
    /// ("Movies · 2025"). `drillDownContext` makes a poster open the same thing it opens on the
    /// page (STAT-401); nil opens the title page.
    init(
        viewModel: StatsMostLoggedViewModel,
        title: String = "Most logged",
        scopeTitle: String,
        drillDownContext: StatsDrillDownContext? = nil,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository,
        currentUserId: Int?,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: viewModel)
        self.title = title
        self.scopeTitle = scopeTitle
        self.drillDownContext = drillDownContext
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        StatsTitlesGridScreen(
            viewModel: viewModel,
            title: title,
            caption: "Logged twice or more · \(scopeTitle)",
            emptyTitle: "Nothing logged twice yet",
            emptyMessage: "Titles you log more than once will appear here.",
            gridID: "mostLoggedAll",
            drillDownContext: drillDownContext,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        ) { item, open in
            StatsMostLoggedPosterCell(title: item, action: open)
        }
    }
}

/// A Stats show-all page: the count, then the posters in rows of four (each row its own lazy
/// item), loading the next page as the end comes near. Tapping a poster opens what it opens on
/// the page: on your own stats the title's logs, otherwise the title page (STAT-401).
struct StatsTitlesGridScreen<Item: StatsPosterTitle, Cell: View>: View {
    let viewModel: StatsTitlesPageModel<Item>
    let title: String
    let caption: String
    let emptyTitle: String
    let emptyMessage: String
    let gridID: String
    /// Whose stats and which scope; nil always opens the title page.
    let drillDownContext: StatsDrillDownContext?
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let listRepository: ListRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void
    /// The cell for an item; call `open` to open it.
    let cell: (_ item: Item, _ open: @escaping () -> Void) -> Cell

    @State private var selectedRef: MediaRef?
    @State private var diaryRoute: StatsRoute?

    init(
        viewModel: StatsTitlesPageModel<Item>,
        title: String,
        caption: String,
        emptyTitle: String,
        emptyMessage: String,
        gridID: String,
        drillDownContext: StatsDrillDownContext? = nil,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository,
        currentUserId: Int?,
        selectedTab: AppTab,
        onSelectTab: @escaping (AppTab) -> Void,
        onUnauthorized: @escaping () -> Void,
        @ViewBuilder cell: @escaping (_ item: Item, _ open: @escaping () -> Void) -> Cell
    ) {
        self.viewModel = viewModel
        self.title = title
        self.caption = caption
        self.emptyTitle = emptyTitle
        self.emptyMessage = emptyMessage
        self.gridID = gridID
        self.drillDownContext = drillDownContext
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        self.cell = cell
    }

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(alignment: .leading, spacing: 0) {
                headline
                    .padding(.bottom, 20)

                contentRows
            }
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 40)
        }
        .refreshable {
            await viewModel.load()
        }
        .background {
            LogBackdrop(url: URL(string: viewModel.items.first?.media.displayPosterURL ?? ""))
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.items.isEmpty, viewModel.errorMessage == nil {
                await viewModel.load()
            }
        }
        .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
            MediaDetailView(
                ref: ref,
                browsingContext: MediaBrowsingContext(
                    refs: viewModel.items.map(\.media.ref),
                    selected: ref
                ),
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
        .navigationDestination(item: $diaryRoute) { route in
            if case let .diary(drillDown, context) = route {
                FilteredDiaryView(
                    title: drillDown.diaryTitle(in: context),
                    filter: drillDown.diaryFilter(in: context),
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

    /// The page's drill-down for a title: its logs on your own stats, else the title page.
    private func open(_ item: Item) {
        if let drillDownContext,
           case let .diary(drillDown, context)? = StatsDrillDown.title(item.media).route(in: drillDownContext) {
            diaryRoute = .diary(drillDown, context)
        } else {
            selectedRef = item.media.ref
        }
    }

    private var headline: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(viewModel.totalCount.formatted()) \(viewModel.totalCount == 1 ? "title" : "titles")")
                .font(.title2.weight(.bold))
                .monospacedDigit()
                .foregroundStyle(.white)
                .contentTransition(.numericText())
            Text(caption)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.55))
        }
        .padding(.leading, 4)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var contentRows: some View {
        if let error = viewModel.errorMessage, viewModel.items.isEmpty {
            VStack(spacing: 16) {
                DiaryStateCard(title: "Couldn't load titles", systemImage: "exclamationmark.triangle", message: error)
                Button("Try Again") {
                    Task { await viewModel.load() }
                }
                .buttonStyle(.bordered)
                .tint(.white)
            }
        } else if viewModel.items.isEmpty, viewModel.isLoadingInitial || viewModel.totalCount > 0 {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 320)
        } else if viewModel.items.isEmpty {
            DiaryStateCard(title: emptyTitle, systemImage: "square.grid.2x2", message: emptyMessage)
        } else {
            let items = viewModel.items
            ForEach(StatsPosterGridLayout.rows(items, idPrefix: gridID)) { row in
                StatsPosterGridRowView(row: row) { item in
                    cell(item) { open(item) }
                }
                .padding(.bottom, StatsPosterGridLayout.rowSpacing)
                .onAppear {
                    guard let last = row.items.last else { return }
                    ArtworkPrefetch.prefetch(
                        ArtworkPrefetch.upcoming(after: last, in: items)
                            .compactMap { $0.media.artworkRequest(slot: StatsPosterGridLayout.slot) }
                    )
                    Task { await viewModel.loadNextPageIfNeeded(currentItemID: last.id) }
                }
            }

            paginationFooter
        }
    }

    @ViewBuilder
    private var paginationFooter: some View {
        Group {
            if viewModel.isLoadingNextPage {
                ProgressView()
                    .tint(.white)
            } else if viewModel.nextPageErrorMessage != nil {
                VStack(spacing: 8) {
                    Text("Couldn't load more")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.6))
                    Button("Try Again") {
                        Task { await viewModel.loadNextPage() }
                    }
                    .buttonStyle(.bordered)
                    .tint(.white)
                }
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity, minHeight: 64)
    }
}
