import SwiftUI

/// The diary narrowed by a filter, such as a stats drill-down ("Sci-Fi & Fantasy · 2025").
/// It pages like the Diary and uses its month cards; pushed onto the caller's navigation stack.
struct FilteredDiaryView: View {
    @State private var viewModel: DiaryViewModel

    private let title: String
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        title: String,
        filter: MediaFilterState,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .profile,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.title = title
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: DiaryViewModel(
            diaryRepository: diaryRepository,
            mediaFilter: filter,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                content
                    .spineContentTransition(value: contentPhase)
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 28)
        }
        .diaryPinnedHeaders()
        .refreshable {
            await viewModel.load()
        }
        .background {
            LogBackdrop(url: URL(string: viewModel.entries.first?.media.displayPosterURL ?? ""))
        }
        .overlay(alignment: .top) {
            DiaryTopSoftScrim()
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .task {
            await viewModel.loadIfNeeded()
        }
        .onReceive(NotificationCenter.default.publisher(for: .diaryEntriesDidChange)) { _ in
            Task { await viewModel.load() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
            Task { await viewModel.load() }
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isBootstrapping || (viewModel.isLoading && viewModel.entries.isEmpty) {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 320)
        } else if let error = viewModel.errorMessage, viewModel.entries.isEmpty {
            DiaryStateCard(
                title: "Couldn't load logs",
                systemImage: "exclamationmark.triangle",
                message: error
            )
        } else if viewModel.entries.isEmpty {
            DiaryStateCard(
                title: "No logs",
                systemImage: "calendar",
                message: "Titles marked as done without a log aren't listed here."
            )
        } else {
            DiaryEntryList(monthSections: viewModel.monthSections) { entry in
                DiaryLogDetailView(
                    entryId: entry.id,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
            paginationFooter
        }
    }

    private var paginationFooter: some View {
        Group {
            if viewModel.isLoadingNextPage {
                ProgressView()
                    .tint(.white)
            } else if let error = viewModel.nextPageErrorMessage {
                DiaryStateCard(title: "Couldn't load more", systemImage: "exclamationmark.triangle", message: error)
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity, minHeight: 56)
        .task(id: viewModel.entries.last?.id) {
            guard let last = viewModel.entries.last else { return }
            await viewModel.loadNextPageIfNeeded(currentEntry: last)
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isBootstrapping || viewModel.isLoading,
            hasContent: !viewModel.entries.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }
}
