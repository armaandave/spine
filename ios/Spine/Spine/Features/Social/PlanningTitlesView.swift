import SwiftUI

@MainActor
@Observable
final class PlanningTitlesViewModel {
    private(set) var day: PlanningDay?
    private(set) var isLoading = true
    private(set) var errorMessage: String?

    private let planning: PlanningRef
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void

    init(planning: PlanningRef, activityRepository: ActivityRepository, onUnauthorized: @escaping () -> Void) {
        self.planning = planning
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            day = try await activityRepository.planningTitles(id: planning.id, mediaType: planning.mediaType)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

/// The titles a Planning row counted: "Added 3 shows to Planning" on top, the day under it, then the titles in the
/// order they were added. A title opens its page.
struct PlanningTitlesView: View {
    @State private var viewModel: PlanningTitlesViewModel
    @State private var presentedRef: MediaRef?

    private let headline: String
    /// Who added them, as the card shows: their avatar and name, which open their profile.
    private let actor: UserSummary
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        activity: ActivityItem,
        planning: PlanningRef,
        activityRepository: ActivityRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        headline = PlanningWords.headline(for: activity)
        actor = activity.actor
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: PlanningTitlesViewModel(
            planning: planning,
            activityRepository: activityRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ScrollView(showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                content
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 28)
        }
        .background {
            LogBackdrop(url: URL(string: viewModel.day?.results.first?.displayPosterURL ?? ""))
        }
        .overlay(alignment: .top) {
            DiaryTopSoftScrim()
        }
        .navigationTitle("Planning")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .task {
            if viewModel.day == nil {
                await viewModel.load()
            }
        }
        .pushDestination(item: $presentedRef) { ref in
            MediaDetailView(
                ref: ref,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .navigationPage(.page)
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading, viewModel.day == nil {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 320)
        } else if let error = viewModel.errorMessage, viewModel.day == nil {
            DiaryStateCard(title: "Couldn't load these", systemImage: "exclamationmark.triangle", message: error)
        } else if let day = viewModel.day {
            header(day)
            if day.results.isEmpty {
                DiaryStateCard(
                    title: "Nothing here now",
                    systemImage: "bookmark",
                    message: "These titles are no longer in Planning."
                )
            } else {
                VStack(spacing: 0) {
                    ForEach(day.results) { media in
                        let isLast = media.id == day.results.last?.id
                        Button {
                            presentedRef = media.ref
                        } label: {
                            PlanningTitleRow(media: media, showsSeparator: !isLast)
                        }
                        .buttonStyle(DiaryRowButtonStyle())
                    }
                }
                .diaryCard(roundsTop: true, roundsBottom: true)
            }
        }
    }

    private func header(_ day: PlanningDay) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            PostHeroAuthorLine(user: actor)
            VStack(alignment: .leading, spacing: 4) {
                Text(headline)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
                if let date = ActivityTimestampFormatter.logDateText(from: day.consumedAt) {
                    Text(date)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.6))
                }
            }
            .accessibilityElement(children: .combine)
        }
        .padding(.horizontal, 4)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One title of a Planning day: its poster, its name, and its type.
private struct PlanningTitleRow: View {
    let media: MediaSummary
    var showsSeparator = false

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            MediaArtwork(
                url: media.displayPosterURL,
                title: media.title,
                slot: .diaryEntry,
                mediaType: media.ref.mediaType,
                orientation: media.posterOrientation
            )
            .frame(width: 42, height: 63)
            .shadow(color: .black.opacity(0.2), radius: 6, y: 3)

            VStack(alignment: .leading, spacing: 5) {
                Text(DiaryEntryRow.titleText(DiaryEntryRow.titleParts(for: media)))
                    .font(.system(size: 16, weight: .semibold))
                    .lineLimit(2)
                Text(media.ref.mediaType == "tv" ? "TV" : media.ref.mediaType.capitalized)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.62))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                DiarySeparator()
                    .padding(.leading, 68)
            }
        }
        .accessibilityElement(children: .combine)
    }
}
