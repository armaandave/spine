import SwiftUI

typealias StatsHighestRatedViewModel = StatsTitlesPageModel<StatsRatedTitle>

extension StatsTitlesPageModel where Item == StatsRatedTitle {
    /// Highest rated's show-all pages, in the server's order (STAT-205, STAT-310).
    static func highestRated(
        profileRepository: ProfileRepository,
        username: String?,
        period: StatsPeriod,
        mediaType: String?,
        expectedTotal: Int = 0,
        onUnauthorized: @escaping () -> Void
    ) -> StatsHighestRatedViewModel {
        StatsHighestRatedViewModel(expectedTotal: expectedTotal, onUnauthorized: onUnauthorized) { page, pageSize in
            let response = try await profileRepository.statsHighestRated(
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

/// Every rated title in the current Stats scope (STAT-205), highest first, with gold stars
/// under each poster.
struct StatsHighestRatedListView: View {
    @State private var viewModel: StatsHighestRatedViewModel
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

    /// `scopeTitle` names the tab and range ("Movies · 2025"). `drillDownContext` makes a
    /// poster open the same thing it opens on the page (STAT-401); nil opens the title page.
    init(
        viewModel: StatsHighestRatedViewModel,
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
            title: "Highest rated",
            caption: "Highest first · \(scopeTitle)",
            emptyTitle: "Nothing rated yet",
            emptyMessage: "Titles you rate will appear here.",
            gridID: "highestRatedAll",
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
            StatsRatedPosterCell(title: item, action: open)
        }
    }
}
