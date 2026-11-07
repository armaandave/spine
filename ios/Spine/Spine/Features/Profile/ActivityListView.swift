import SwiftUI

/// A person's Activity: months newest first, one row per title per day, in the Diary's look. The profile's Activity
/// row opens it plain; a Stats number opens it filtered, with the number in words on top and what it includes with no
/// date under that. A row opens the log or the day's progress it is.
struct ActivityListView: View {
    @State private var viewModel: ActivityFeedViewModel

    private let title: String
    private let activityRepository: ActivityRepository
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    /// `username` is required: the list is always one person's. Home's feed is another screen.
    init(
        username: String,
        filter: ActivityListFilter = .everything,
        title: String = "Activity",
        activityRepository: ActivityRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .profile,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.title = title
        self.activityRepository = activityRepository
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ActivityFeedViewModel(
            username: username,
            filter: filter,
            pageSize: 40,
            activityRepository: activityRepository,
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
            await viewModel.refresh()
        }
        .background {
            LogBackdrop(url: URL(string: viewModel.items.first?.media?.displayPosterURL ?? ""))
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
        // A write elsewhere only marks the list stale; it reloads when it is next on screen, not for every write.
        .onAppear {
            Task { await viewModel.reloadIfStale() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .diaryEntriesDidChange)) { _ in
            viewModel.markStale()
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
            viewModel.markStale()
        }
    }

    @ViewBuilder
    private var content: some View {
        let sections = ActivityListPresentation.monthSections(from: viewModel.items)
        let topLine = ActivityListPresentation.topLine(viewModel.topLine)
        let undatedLine = ActivityListPresentation.undatedLine(viewModel.undated)

        if viewModel.isLoadingInitial && viewModel.items.isEmpty {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 320)
        } else if let error = viewModel.errorMessage, viewModel.items.isEmpty {
            DiaryStateCard(title: "Couldn't load activity", systemImage: "exclamationmark.triangle", message: error)
        } else {
            if topLine != nil || undatedLine != nil {
                header(topLine: topLine, undatedLine: undatedLine)
            }
            if sections.isEmpty {
                if topLine == nil, undatedLine == nil {
                    DiaryStateCard(
                        title: "No activity",
                        systemImage: "calendar",
                        message: "Logs and progress show up here."
                    )
                }
            } else {
                ForEach(sections) { section in
                    monthSection(section)
                }
            }
            paginationFooter
        }
    }

    private func header(topLine: String?, undatedLine: String?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if let topLine {
                Text(topLine)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
            }
            if let undatedLine {
                Text(undatedLine)
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.6))
            }
        }
        .padding(.horizontal, 4)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func monthSection(_ section: ActivityMonthSection) -> some View {
        Section {
            // Each row is its own item in the lazy stack, a slice of the month's card: a whole month in one item
            // made the stack's sizes so uneven that it could loop re-laying out the end of the list.
            ForEach(section.items) { item in
                let isFirst = item.id == section.items.first?.id
                let isLast = item.id == section.items.last?.id
                row(item, isFirst: isFirst, isLast: isLast)
            }
        } header: {
            DiaryMonthHeader(title: section.title)
                .id(section.id)
        }
    }

    @ViewBuilder
    private func row(_ item: ActivityItem, isFirst: Bool, isLast: Bool) -> some View {
        NavigationLink {
            destination(for: item)
        } label: {
            ActivityListRow(activity: item, showsSeparator: !isLast)
        }
        .buttonStyle(DiaryRowButtonStyle())
        .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
        .padding(.bottom, isLast ? 18 : 0)
    }

    @ViewBuilder
    private func destination(for item: ActivityItem) -> some View {
        switch item.post {
        case let .log(id):
            DiaryLogDetailView(
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
            ProgressPostDetailView(
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
        case nil:
            if let planning = item.planning {
                PlanningTitlesView(
                    activity: item,
                    planning: planning,
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
        .task(id: viewModel.items.last?.id) {
            guard let last = viewModel.items.last else { return }
            await viewModel.loadNextPageIfNeeded(currentItem: last)
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingInitial,
            hasContent: !viewModel.items.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }
}

/// A day's row in the Activity list, the Diary row's layout: the day, the poster at its real size, the title ("T9 ·
/// S1 E2 · Title" for an episode), then the marks of a log and what the row said. No review, no edit control.
struct ActivityListRow: View {
    let activity: ActivityItem
    var showsSeparator = false

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            DiaryDateBadge(rawValue: activity.consumedAt ?? activity.createdAt, mediaType: mediaType)

            // Laid out at its real size: a larger poster shrunk with scaleEffect overflowed the row's layout.
            MediaArtwork(
                url: activity.media?.displayPosterURL,
                title: activity.media?.title ?? "",
                slot: .diaryEntry,
                mediaType: mediaType,
                orientation: activity.media?.posterOrientation
            )
            .frame(width: 42, height: 63)
            .shadow(color: .black.opacity(0.2), radius: 6, y: 3)

            VStack(alignment: .leading, spacing: 5) {
                Group {
                    // A day of Planning adds reads as its count, not as its first title.
                    if activity.planning != nil {
                        Text(PlanningWords.headline(for: activity))
                    } else {
                        Text(DiaryEntryRow.titleText(titleParts))
                    }
                }
                .font(.system(size: 16, weight: .semibold))
                .lineLimit(2)

                detailLine
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                DiarySeparator()
                    .padding(.leading, 56)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var mediaType: String {
        activity.media?.ref.mediaType ?? "movie"
    }

    private var titleParts: (title: String, year: String?) {
        activity.media.map(DiaryEntryRow.titleParts(for:)) ?? ("", nil)
    }

    private var isLog: Bool {
        if case .log = activity.post { true } else { false }
    }

    @ViewBuilder
    private var detailLine: some View {
        let marks = ActivityFeedPresentation.ratingSummary(for: activity)
        let isRepeat = isLog && ActivityFeedPresentation.isRewatch(activity)
        let caption = ActivityListPresentation.caption(for: activity)
        if isLog && (!marks.isEmpty || isRepeat) || caption != nil {
            HStack(spacing: 6) {
                if isLog && (!marks.isEmpty || isRepeat) {
                    LogRatingMarks(
                        summary: marks,
                        isRepeat: isRepeat,
                        repeatLabel: activity.media?.ref.repeatLabel ?? "Rewatch"
                    )
                }
                if let caption {
                    Text(caption)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.white.opacity(0.62))
                        .lineLimit(2)
                }
            }
        }
    }
}
