import SwiftUI

typealias StatsPeoplePageModel = StatsTitlesPageModel<StatsPersonRow>

extension StatsTitlesPageModel where Item == StatsPersonRow {
    /// One People group's pages from the people endpoint (STAT-219, STAT-310), in the order the
    /// section showed.
    static func people(
        profileRepository: ProfileRepository,
        username: String?,
        period: StatsPeriod,
        mediaType: String,
        role: String,
        sort: StatsPeopleSort,
        expectedTotal: Int = 0,
        onUnauthorized: @escaping () -> Void
    ) -> StatsPeoplePageModel {
        StatsPeoplePageModel(expectedTotal: expectedTotal, onUnauthorized: onUnauthorized) { page, pageSize in
            let response = try await profileRepository.statsPeople(
                username: username,
                period: period,
                mediaType: mediaType,
                role: role,
                sort: sort,
                page: page,
                pageSize: pageSize
            )
            return Page(response)
        }
    }
}

/// Every person of one People group (STAT-219): a row per person with their headshot, name and
/// count or average on the right, loading the next page as the end comes near. Each row is its
/// own lazy item. A row opens what a headshot opens on the page.
struct StatsPeopleListView: View {
    @State private var viewModel: StatsPeoplePageModel
    let group: StatsPeopleGroup
    let sort: StatsPeopleSort
    let mediaType: String
    /// Whose stats and which scope, for what a row opens.
    let drillDownContext: StatsDrillDownContext
    let services: StatsRouteServices

    @State private var pushedRoute: StatsRoute?
    @State private var coverRoute: StatsRoute?

    init(
        viewModel: StatsPeoplePageModel,
        group: StatsPeopleGroup,
        sort: StatsPeopleSort,
        mediaType: String,
        drillDownContext: StatsDrillDownContext,
        services: StatsRouteServices
    ) {
        _viewModel = State(initialValue: viewModel)
        self.group = group
        self.sort = sort
        self.mediaType = mediaType
        self.drillDownContext = drillDownContext
        self.services = services
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
            LogBackdrop(url: nil)
        }
        .navigationTitle(group.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .environment(\.colorScheme, .dark)
        .toolbar(.hidden, for: .tabBar)
        .task {
            if viewModel.items.isEmpty, viewModel.errorMessage == nil {
                await viewModel.load()
            }
        }
        .navigationDestination(item: $pushedRoute) { route in
            StatsRouteDestination(route: route, services: services)
        }
        .fullScreenCover(item: $coverRoute) { route in
            StatsRouteDestination(route: route, services: services)
        }
    }

    private var headline: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(StatsChartCopy.count(viewModel.totalCount, "person", "people"))
                .font(.title2.weight(.bold))
                .monospacedDigit()
                .foregroundStyle(.white)
                .contentTransition(.numericText())
            Text(Self.caption(sort: sort, mediaType: mediaType, period: drillDownContext.period))
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.55))
        }
        .padding(.leading, 4)
        .accessibilityElement(children: .combine)
    }

    /// "Most watched · Movies · 2025".
    static func caption(sort: StatsPeopleSort, mediaType: String, period: StatsPeriod) -> String {
        "\(StatsPeopleData.sortTitle(sort, mediaType: mediaType)) · \(StatsPageSection.scopeTitle(mediaType: mediaType, period: period))"
    }

    @ViewBuilder
    private var contentRows: some View {
        if let error = viewModel.errorMessage, viewModel.items.isEmpty {
            VStack(spacing: 16) {
                DiaryStateCard(title: "Couldn't load people", systemImage: "exclamationmark.triangle", message: error)
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
                title: "No one here yet",
                systemImage: "person.2",
                message: "People from what you log will appear here."
            )
        } else {
            let items = viewModel.items
            ForEach(Array(items.enumerated()), id: \.element.id) { index, row in
                personRow(row, isFirst: index == 0, isLast: index == items.count - 1)
                    .onAppear {
                        Task { await viewModel.loadNextPageIfNeeded(currentItemID: row.id) }
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

    @ViewBuilder
    private func personRow(_ row: StatsPersonRow, isFirst: Bool, isLast: Bool) -> some View {
        let route = StatsDrillDown.person(row.person, role: group.role).route(in: drillDownContext)
        Group {
            if let route {
                Button {
                    if route.presentsFullScreen {
                        coverRoute = route
                    } else {
                        pushedRoute = route
                    }
                } label: {
                    personRowContent(row, showsChevron: true, isLast: isLast)
                }
                .buttonStyle(DiaryRowButtonStyle())
            } else {
                personRowContent(row, showsChevron: false, isLast: isLast)
            }
        }
        .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
    }

    private func personRowContent(_ row: StatsPersonRow, showsChevron: Bool, isLast: Bool) -> some View {
        HStack(alignment: .center, spacing: 12) {
            PersonArtwork(urlString: row.person.imageUrl, name: row.person.name, size: 44)

            Text(row.person.name)
                .font(.body)
                .foregroundStyle(.white)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)

            Text(StatsPeopleData.caption(row, sort: sort, mediaType: mediaType))
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.7))
                .multilineTextAlignment(.trailing)

            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.35))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if !isLast {
                DiarySeparator()
                    .padding(.leading, 16 + 44 + 12)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(StatsPeopleData.accessibilityLabel(row, sort: sort, mediaType: mediaType))
    }
}
