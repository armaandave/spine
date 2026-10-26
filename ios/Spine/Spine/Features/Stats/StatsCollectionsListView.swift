import SwiftUI

typealias StatsCollectionsPageModel = StatsTitlesPageModel<MediaSeriesSummary>

extension StatsTitlesPageModel where Item == MediaSeriesSummary {
    /// One Collections group's pages from the collections endpoint (STAT-310). Collections are
    /// lifetime, so there are no dates.
    static func collections(
        profileRepository: ProfileRepository,
        username: String?,
        mediaType: String,
        group: StatsCollectionGroup,
        expectedTotal: Int = 0,
        onUnauthorized: @escaping () -> Void
    ) -> StatsCollectionsPageModel {
        StatsCollectionsPageModel(expectedTotal: expectedTotal, onUnauthorized: onUnauthorized) { page, pageSize in
            let response = try await profileRepository.statsCollections(
                username: username,
                mediaType: mediaType,
                group: group,
                page: page,
                pageSize: pageSize
            )
            return Page(response)
        }
    }
}

/// Every complete or almost complete series of one type (STAT-214), in tiles of three posters
/// with "N of M", four to a row (each row its own lazy item), loading the next page as the end
/// comes near. A tile opens the series page.
struct StatsCollectionsListView: View {
    @State private var viewModel: StatsCollectionsPageModel
    let group: StatsCollectionGroup
    let services: StatsRouteServices

    @State private var route: StatsRoute?

    init(viewModel: StatsCollectionsPageModel, group: StatsCollectionGroup, services: StatsRouteServices) {
        _viewModel = State(initialValue: viewModel)
        self.group = group
        self.services = services
    }

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(alignment: .leading, spacing: 0) {
                Text(StatsChartCopy.count(viewModel.totalCount, "collection", "collections"))
                    .font(.title2.weight(.bold))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                    .contentTransition(.numericText())
                    .padding(.leading, 4)
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
            LogBackdrop(url: URL(string: viewModel.items.first?.posterUrls.first ?? ""))
        }
        .navigationTitle(group.displayTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.items.isEmpty, viewModel.errorMessage == nil {
                await viewModel.load()
            }
        }
        .fullScreenCover(item: $route) { route in
            StatsRouteDestination(route: route, services: services)
        }
    }

    @ViewBuilder
    private var contentRows: some View {
        if let error = viewModel.errorMessage, viewModel.items.isEmpty {
            VStack(spacing: 16) {
                DiaryStateCard(title: "Couldn't load collections", systemImage: "exclamationmark.triangle", message: error)
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
            DiaryStateCard(
                title: "No collections yet",
                systemImage: "square.stack",
                message: "Series you finish, or nearly finish, will appear here."
            )
        } else {
            ForEach(StatsPosterGridLayout.rows(viewModel.items, idPrefix: "collectionsAll")) { row in
                StatsPosterGridRowView(row: row) { series in
                    StatsCollectionTile(series: series) {
                        route = .series(series.ref)
                    }
                }
                .padding(.bottom, StatsPosterGridLayout.rowSpacing)
                .onAppear {
                    guard let last = row.items.last else { return }
                    Task { await viewModel.loadNextPageIfNeeded(currentItemID: last.id) }
                }
            }

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
}
