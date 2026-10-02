import SwiftUI

@MainActor
@Observable
final class StatsMostLoggedViewModel {
    static let pageSize = 48
    private static let prefetchDistance = 8

    private(set) var items: [StatsMostLoggedItem] = []
    private(set) var totalCount: Int
    private(set) var isLoadingInitial = false
    private(set) var isLoadingNextPage = false
    private(set) var errorMessage: String?
    private(set) var nextPageErrorMessage: String?

    let period: StatsPeriod
    let mediaType: String?

    private let profileRepository: ProfileRepository
    private let username: String?
    private let onUnauthorized: () -> Void
    private var nextPage: String?
    private var requestGeneration = 0

    init(
        profileRepository: ProfileRepository,
        username: String?,
        period: StatsPeriod,
        mediaType: String?,
        expectedTotal: Int = 0,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.username = username
        self.period = period
        self.mediaType = mediaType
        self.totalCount = expectedTotal
        self.onUnauthorized = onUnauthorized
    }

    var hasMorePages: Bool {
        nextPage != nil
    }

    func load() async {
        requestGeneration &+= 1
        let generation = requestGeneration
        isLoadingInitial = true
        errorMessage = nil
        nextPageErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingInitial = false
            }
        }

        do {
            let response = try await fetch(page: nil)
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

    func loadNextPageIfNeeded(currentItemID: StatsMostLoggedItem.ID) async {
        guard let currentIndex = items.firstIndex(where: { $0.id == currentItemID }),
              currentIndex >= items.count - Self.prefetchDistance else { return }
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
            let response = try await fetch(page: page)
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

    private func fetch(page: String?) async throws -> PagedResponse<StatsMostLoggedItem> {
        try await profileRepository.statsMostLogged(
            username: username,
            period: period,
            mediaType: mediaType,
            page: page,
            pageSize: Self.pageSize
        )
    }

    private func apply(_ response: PagedResponse<StatsMostLoggedItem>, replacingItems: Bool) {
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

/// Every title logged at least twice in the current Stats scope, loaded a
/// page at a time as the grid scrolls.
struct StatsMostLoggedView: View {
    @State private var viewModel: StatsMostLoggedViewModel
    @State private var selectedRef: MediaRef?

    private let scopeTitle: String
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        viewModel: StatsMostLoggedViewModel,
        scopeTitle: String,
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
        self.scopeTitle = scopeTitle
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
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 22) {
                    StatsHeadline(
                        value: viewModel.totalCount.formatted(),
                        unit: viewModel.totalCount == 1 ? "title" : "titles",
                        caption: "Logged twice or more · \(scopeTitle)"
                    )

                    content
                        .spineContentTransition(value: contentPhase)
                }
                .padding(.horizontal, 16)
                .padding(.top, 10)
                .padding(.bottom, 40)
            }
            .scrollEdgeEffectStyle(.soft, for: .top)
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle("Most Logged")
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
    }

    @ViewBuilder
    private var content: some View {
        if let error = viewModel.errorMessage, viewModel.items.isEmpty {
            VStack(spacing: 16) {
                ContentUnavailableView(
                    "Could not load titles",
                    systemImage: "exclamationmark.triangle",
                    description: Text(error)
                )
                .foregroundStyle(.white)

                retryButton {
                    Task { await viewModel.load() }
                }
            }
            .frame(maxWidth: .infinity, minHeight: 320)
        } else if viewModel.items.isEmpty, viewModel.isLoadingInitial || viewModel.totalCount > 0 {
            StatsPosterGridSkeleton(count: min(max(viewModel.totalCount, 8), 16))
        } else if viewModel.items.isEmpty {
            ContentUnavailableView(
                "Nothing logged twice yet",
                systemImage: "arrow.clockwise",
                description: Text("Titles you log more than once will appear here.")
            )
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 320)
        } else {
            VStack(spacing: 0) {
                StatsPosterGrid(
                    items: viewModel.items.map {
                        StatsPosterItem(
                            media: $0.media,
                            caption: "\($0.logCount.formatted())×",
                            accessibilityCaption: "\($0.logCount.formatted()) \($0.logCount == 1 ? "log" : "logs")"
                        )
                    },
                    onItemAppear: { item in
                        Task { await viewModel.loadNextPageIfNeeded(currentItemID: item.id) }
                    }
                ) { media in
                    selectedRef = media.ref
                }

                paginationFooter
            }
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
                    Text("Could not load more")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.6))
                    retryButton {
                        Task { await viewModel.loadNextPage() }
                    }
                }
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity, minHeight: 64)
    }

    private func retryButton(action: @escaping () -> Void) -> some View {
        Button("Try Again", action: action)
            .font(.system(size: 15, weight: .bold))
            .foregroundStyle(.black)
            .padding(.horizontal, 22)
            .frame(minHeight: 44)
            .background(.white.opacity(0.92), in: Capsule())
            .buttonStyle(.plain)
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingInitial,
            hasContent: !viewModel.items.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }
}

private struct StatsPosterGridSkeleton: View {
    let count: Int

    @State private var isDimmed = false

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)

    var body: some View {
        LazyVGrid(columns: columns, spacing: 10) {
            ForEach(0 ..< count, id: \.self) { _ in
                RoundedRectangle(cornerRadius: PosterSlot.tagGrid.cornerRadius, style: .continuous)
                    .fill(.white.opacity(0.07))
                    .frame(width: PosterSlot.tagGrid.size.width, height: PosterSlot.tagGrid.size.height)
            }
        }
        .opacity(isDimmed ? 0.55 : 1)
        .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: isDimmed)
        .onAppear { isDimmed = true }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading titles")
    }
}
