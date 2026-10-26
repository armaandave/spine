import Foundation
import SwiftUI

@MainActor
@Observable
final class ActivityFeedViewModel {
    var items: [ActivityItem] = []
    var isLoadingInitial = true
    var isLoadingNextPage = false
    var errorMessage: String?
    var nextPageErrorMessage: String?

    private let username: String
    private let pageSize: Int
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void
    private var nextCursorLink: String?
    private var didLoad = false
    private var isLoadingFirstPage = false

    init(
        username: String,
        pageSize: Int = 25,
        activityRepository: ActivityRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.username = username
        self.pageSize = pageSize
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
    }

    var hasMorePages: Bool {
        nextCursorLink != nil
    }

    func loadIfNeeded() async {
        guard !didLoad else { return }
        await load()
    }

    func load() async {
        await loadFirstPage(force: true)
    }

    func refresh() async {
        await loadFirstPage(force: true)
    }

    func loadNextPageIfNeeded(currentItem: ActivityItem) async {
        guard currentItem.id == items.last?.id else { return }
        await loadNextPage()
    }

    func loadNextPage() async {
        guard !isLoadingFirstPage, !isLoadingNextPage, let nextCursorLink else { return }
        isLoadingNextPage = true
        nextPageErrorMessage = nil
        defer { isLoadingNextPage = false }

        do {
            let response = try await activityRepository.userActivityPage(
                username: username,
                pageSize: pageSize,
                cursorLink: nextCursorLink
            )
            var seen = Set(items.map(\.id))
            items += response.results.filter { seen.insert($0.id).inserted }
            self.nextCursorLink = response.nextCursor
        } catch is CancellationError {
            return
        } catch {
            nextPageErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func loadFirstPage(force: Bool) async {
        guard !isLoadingFirstPage, force || !didLoad else { return }
        isLoadingFirstPage = true
        isLoadingInitial = items.isEmpty
        errorMessage = nil
        nextPageErrorMessage = nil
        defer {
            isLoadingFirstPage = false
            isLoadingInitial = false
        }

        do {
            let response = try await activityRepository.userActivityPage(
                username: username,
                pageSize: pageSize,
                cursorLink: nil
            )
            items = Self.deduplicated(response.results)
            nextCursorLink = response.nextCursor
            didLoad = true
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }

    private static func deduplicated(_ activities: [ActivityItem]) -> [ActivityItem] {
        var seen = Set<Int>()
        return activities.filter { seen.insert($0.id).inserted }
    }
}

struct ActivityFeedView: View {
    @State private var viewModel: ActivityFeedViewModel

    private let onSelectActivity: (ActivityItem) -> Void

    init(
        username: String,
        activityRepository: ActivityRepository,
        onUnauthorized: @escaping () -> Void,
        onSelectActivity: @escaping (ActivityItem) -> Void
    ) {
        self.onSelectActivity = onSelectActivity
        _viewModel = State(initialValue: ActivityFeedViewModel(
            username: username,
            activityRepository: activityRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                content
                    .spineContentTransition(value: contentPhase)
                    .padding(.horizontal, 16)
                    .padding(.top, 6)
                    .padding(.bottom, 96)
            }
            .refreshable {
                await viewModel.refresh()
            }
        }
        .navigationTitle("Activity")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(SpinePalette.pageBackground, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .task {
            await viewModel.loadIfNeeded()
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoadingInitial, viewModel.items.isEmpty {
            ActivityFeedSkeleton(rowCount: 5)
        } else if let error = viewModel.errorMessage, viewModel.items.isEmpty {
            ActivityFeedState(
                title: "Could not load activity",
                systemImage: "exclamationmark.triangle",
                message: error,
                actionTitle: "Retry"
            ) {
                Task { await viewModel.load() }
            }
            .frame(minHeight: 360)
        } else if viewModel.items.isEmpty {
            ActivityFeedState(
                title: "No activity yet",
                systemImage: "clock.arrow.circlepath",
                message: "Diary logs and progress updates will appear here."
            )
            .frame(minHeight: 360)
        } else {
            LazyVStack(spacing: 0) {
                ForEach(viewModel.items) { activity in
                    Button {
                        onSelectActivity(activity)
                    } label: {
                        ActivityFeedRow(activity: activity)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint(ActivityFeedPresentation.destinationHint(for: activity))
                    .task {
                        await viewModel.loadNextPageIfNeeded(currentItem: activity)
                    }

                    if activity.id != viewModel.items.last?.id {
                        ActivityFeedDivider()
                    }
                }

                paginationFooter
            }
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingInitial,
            hasContent: !viewModel.items.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    @ViewBuilder
    private var paginationFooter: some View {
        Group {
            if viewModel.isLoadingNextPage {
                ProgressView()
                    .tint(.white)
            } else if let error = viewModel.nextPageErrorMessage {
                VStack(spacing: 8) {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.52))
                        .multilineTextAlignment(.center)

                    Button("Retry") {
                        Task { await viewModel.loadNextPage() }
                    }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.86))
                }
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity, minHeight: 60)
        .spineContentTransition(value: paginationPhase)
    }

    private var paginationPhase: String {
        if viewModel.isLoadingNextPage {
            return "loading"
        }
        if viewModel.nextPageErrorMessage != nil {
            return "error"
        }
        return viewModel.hasMorePages ? "available" : "empty"
    }
}

struct ActivityFeedRow: View {
    let activity: ActivityItem

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                ActivityActorAvatar(user: activity.actor)

                VStack(alignment: .leading, spacing: 2) {
                    Text(activity.actor.displayName)
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    Text(ActivityFeedPresentation.actionText(for: activity))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.52))
                        .lineLimit(1)
                }

                Spacer(minLength: 8)

                if let timestamp = ActivityTimestampFormatter.shortText(from: activity.createdAt) {
                    Text(timestamp)
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white.opacity(0.42))
                        .lineLimit(1)
                        .accessibilityLabel(ActivityTimestampFormatter.fullText(from: activity.createdAt) ?? timestamp)
                }
            }

            if let person = activity.person {
                HStack(alignment: .top, spacing: 12) {
                    PersonArtwork(urlString: person.profileUrl, name: person.name, size: 72)
                        .shadow(color: .black.opacity(0.22), radius: 8, y: 4)

                    VStack(alignment: .leading, spacing: 7) {
                        Text(person.name)
                            .font(.headline.weight(.bold))
                            .foregroundStyle(.white)
                            .lineLimit(2)

                        if let department = ActivityFeedPresentation.clean(person.knownForDepartment) {
                            Text(department)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(.white.opacity(0.58))
                                .lineLimit(1)
                        }

                        if let listName = ActivityFeedPresentation.listName(for: activity) {
                            Text(listName)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(.white.opacity(0.58))
                                .lineLimit(2)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else if let media = activity.media {
                HStack(alignment: .top, spacing: 12) {
                    MediaArtwork(
                        url: media.displayPosterURL,
                        title: media.title,
                        slot: .profileRow,
                        mediaType: media.ref.mediaType,
                        orientation: media.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.22), radius: 8, y: 4)

                    VStack(alignment: .leading, spacing: 7) {
                        Text(media.displayTitle)
                            .font(.headline.weight(.bold))
                            .foregroundStyle(.white)
                            .lineLimit(2)

                        metadata
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else if let listName = ActivityFeedPresentation.listName(for: activity) {
                Text(listName)
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var metadata: some View {
        if let progressDelta = ActivityFeedPresentation.progressDelta(for: activity) {
            ProgressDeltaInlineView(delta: progressDelta)
        } else if ActivityFeedPresentation.rating(for: activity) != nil || activity.object.liked == true {
            HStack(spacing: 6) {
                if let rating = ActivityFeedPresentation.rating(for: activity) {
                    DiaryStarRating(rating: rating, mediaType: activity.media?.ref.mediaType)
                }

                if activity.object.liked == true {
                    Image(systemName: "heart.fill")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.pink)
                        .accessibilityLabel("Liked")
                }
            }
        } else if let listName = ActivityFeedPresentation.listName(for: activity) {
            Text(listName)
                .font(.caption.weight(.medium))
                .foregroundStyle(.white.opacity(0.58))
                .lineLimit(2)
        }
    }
}

struct ActivityFeedDivider: View {
    var body: some View {
        Divider()
            .overlay(.white.opacity(0.07))
    }
}

struct ActivityFeedSkeleton: View {
    var rowCount = 3

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(0 ..< rowCount, id: \.self) { index in
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 10) {
                        Circle()
                            .fill(.white.opacity(0.08))
                            .frame(width: 32, height: 32)

                        VStack(alignment: .leading, spacing: 5) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.white.opacity(0.09))
                                .frame(width: 94, height: 11)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.white.opacity(0.06))
                                .frame(width: 72, height: 9)
                        }
                    }

                    HStack(alignment: .top, spacing: 12) {
                        RoundedRectangle(cornerRadius: PosterSlot.profileRow.cornerRadius, style: .continuous)
                            .fill(.white.opacity(0.08))
                            .frame(width: PosterSlot.profileRow.size.width, height: PosterSlot.profileRow.size.height)

                        VStack(alignment: .leading, spacing: 7) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.white.opacity(0.09))
                                .frame(width: 150, height: 14)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.white.opacity(0.06))
                                .frame(width: 82, height: 10)
                        }
                    }
                }
                .padding(.vertical, 14)

                if index < rowCount - 1 {
                    ActivityFeedDivider()
                }
            }
        }
        .redacted(reason: .placeholder)
        .accessibilityHidden(true)
    }
}

enum ActivityFeedPresentation {
    static func actionText(for activity: ActivityItem) -> String {
        switch activity.type {
        case "progress_updated":
            "updated progress"
        case "rating_updated":
            "rated \(mediaTypePhrase(for: activity.media?.ref.mediaType))"
        case "diary_created":
            activity.media?.ref.mediaType == "music"
                ? "listened to an album"
                : "logged \(mediaTypePhrase(for: activity.media?.ref.mediaType))"
        case "diary_updated":
            "updated a log"
        case "diary_deleted":
            "deleted a log"
        case "list_created":
            "created a list"
        case "list_item_added":
            "added to a list"
        default:
            "shared activity"
        }
    }

    static func destinationHint(for activity: ActivityItem) -> String {
        switch ActivityDestination.resolve(activity) {
        case .diary:
            "Opens log details"
        case .person:
            "Opens person details"
        case .media:
            "Opens media details"
        case .none:
            "No details available"
        }
    }

    static func progressDelta(for activity: ActivityItem) -> ProgressChangeDisplay? {
        guard
            activity.type == "progress_updated",
            let previous = activity.object.previous,
            let current = activity.object.current
        else {
            return nil
        }
        return ProgressChangeState(
            id: activity.object.id,
            previous: previous,
            current: current,
            createdAt: activity.createdAt
        )
        .compactDisplayParts(preferredMode: activity.media.flatMap { ProgressDisplayPreferences.mode(for: $0.ref) })
    }

    static func rating(for activity: ActivityItem) -> String? {
        clean(activity.object.rating)
    }

    static func listName(for activity: ActivityItem) -> String? {
        activity.object.type == "list" ? clean(activity.object.name) : nil
    }

    private static func mediaTypePhrase(for mediaType: String?) -> String {
        switch mediaType {
        case "movie": "a movie"
        case "tv": "a TV show"
        case "season": "a season"
        case "episode": "an episode"
        case "anime": "an anime"
        case "manga": "a manga"
        case "game": "a game"
        case "book": "a book"
        case "comic": "a comic"
        case "music": "an album"
        case "boardgame": "a board game"
        default: "media"
        }
    }

    static func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

enum ActivityDestination: Equatable {
    case diary(Int)
    case person(PersonRef)
    case media(MediaRef)
    case none

    static func resolve(_ activity: ActivityItem) -> ActivityDestination {
        if activity.object.type == "diary" {
            return .diary(activity.object.id)
        }
        if let ref = activity.person?.ref {
            return .person(ref)
        }
        if let ref = activity.media?.ref {
            return .media(ref)
        }
        return .none
    }
}

enum ActivityTimestampFormatter {
    static func shortText(from rawValue: String?, now: Date = Date()) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        let interval = max(0, now.timeIntervalSince(date))

        switch interval {
        case ..<60:
            return "Now"
        case ..<3_600:
            return "\(Int(interval / 60))m"
        case ..<86_400:
            return "\(Int(interval / 3_600))h"
        case ..<604_800:
            return "\(Int(interval / 86_400))d"
        case ..<2_629_800:
            return "\(Int(interval / 604_800))w"
        case ..<31_557_600:
            return "\(Int(interval / 2_629_800))mo"
        default:
            return "\(Int(interval / 31_557_600))y"
        }
    }

    static func fullText(from rawValue: String?) -> String? {
        date(from: rawValue)?.formatted(date: .abbreviated, time: .shortened)
    }

    private static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let fractionalFormatter = ISO8601DateFormatter()
        fractionalFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractionalFormatter.date(from: rawValue) {
            return date
        }
        return ISO8601DateFormatter().date(from: rawValue)
    }
}

private struct ActivityActorAvatar: View {
    let user: UserSummary

    var body: some View {
        SpineAsyncImage(url: URL(string: user.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.fill")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white.opacity(0.54))
            }
        }
        .frame(width: 32, height: 32)
        .background(.white.opacity(0.10), in: Circle())
        .clipShape(Circle())
    }
}

private struct ActivityFeedState: View {
    let title: String
    let systemImage: String
    let message: String
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.title2.weight(.semibold))
                .foregroundStyle(.white.opacity(0.56))

            Text(title)
                .font(.headline.weight(.bold))
                .foregroundStyle(.white)

            Text(message)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.52))
                .multilineTextAlignment(.center)

            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.88))
                    .padding(.top, 2)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
    }
}
