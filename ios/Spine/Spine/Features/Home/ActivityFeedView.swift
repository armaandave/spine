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

    /// A profile's username, or nil for the activity of the people the signed-in user follows (Home).
    private let username: String?
    private let pageSize: Int
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void
    private var nextCursorLink: String?
    private var didLoad = false
    private var isLoadingFirstPage = false
    private var likesInFlight = Set<ActivityPostRef>()

    init(
        username: String?,
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
            let response = try await page(cursorLink: nextCursorLink)
            // A refresh landed while this loaded: this page no longer follows what's on screen.
            guard self.nextCursorLink == nextCursorLink else { return }
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

    /// Shows the like at once, then settles on the server's count, or goes back if the request fails.
    func toggleLike(_ activity: ActivityItem) async {
        guard let post = activity.post,
              let current = engagement(of: post),
              likesInFlight.insert(post).inserted
        else { return }
        defer { likesInFlight.remove(post) }
        apply(current.toggledLike, to: post)

        do {
            let state = try await activityRepository.setLike(post: post, liked: !current.hasLiked)
            if let latest = engagement(of: post) {
                apply(latest.applying(state), to: post)
            }
        } catch {
            apply(current, to: post)
            handleUnauthorized(error)
        }
    }

    /// A post's page changed its likes or comments.
    func apply(_ engagement: PostEngagement, to post: ActivityPostRef) {
        guard let index = items.firstIndex(where: { $0.post == post }) else { return }
        items[index].engagement = engagement
    }

    private func engagement(of post: ActivityPostRef) -> PostEngagement? {
        items.first { $0.post == post }?.engagement
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
            let response = try await page(cursorLink: nil)
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

    private func page(cursorLink: String?) async throws -> ActivityCursorResponse {
        if let username {
            return try await activityRepository.userActivityPage(
                username: username,
                pageSize: pageSize,
                cursorLink: cursorLink
            )
        }
        return try await activityRepository.feedPage(pageSize: pageSize, cursorLink: cursorLink)
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

struct ActivityFeedDivider: View {
    var body: some View {
        Divider()
            .overlay(.white.opacity(0.07))
    }
}

struct ActivityFeedSkeleton: View {
    var rowCount = 3

    var body: some View {
        RoundedRectangle(cornerRadius: 22, style: .continuous)
            .fill(.white.opacity(0.06))
            .frame(height: CGFloat(rowCount) * 84)
            .accessibilityHidden(true)
    }
}

enum ActivityFeedPresentation {
    static func actionText(for activity: ActivityItem) -> String {
        switch activity.type {
        case "progress_updated":
            clean(activity.object.summary) ?? "updated progress"
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
        case .progress:
            "Opens this progress"
        case .person:
            "Opens person details"
        case .media:
            "Opens media details"
        case .none:
            "No details available"
        }
    }

    /// "Watched 4 episodes", capitalized for a line of its own.
    static func progressHeadline(for activity: ActivityItem) -> String {
        let summary = clean(activity.object.summary) ?? "updated progress"
        return summary.prefix(1).uppercased() + summary.dropFirst()
    }

    /// What a log's row quotes: the review, or its title when that's all there is.
    static func reviewText(for activity: ActivityItem) -> String? {
        clean(activity.review) ?? clean(activity.reviewTitle)
    }

    /// The stars and heart beside a log's title. The review has its own line, so there's no mark for one.
    static func ratingSummary(for activity: ActivityItem) -> DiaryEntrySummary {
        DiaryEntrySummary(
            stars: rating(for: activity)?.starCount,
            liked: activity.object.liked == true,
            hasReview: false
        )
    }

    static func isRewatch(_ activity: ActivityItem) -> Bool {
        activity.object.isRewatch == true
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
    case progress(Int)
    case person(PersonRef)
    case media(MediaRef)
    case none

    static func resolve(_ activity: ActivityItem) -> ActivityDestination {
        if activity.object.type == "diary" {
            return .diary(activity.object.id)
        }
        if activity.object.type == "progress_day" {
            return .progress(activity.object.id)
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
    /// The log's date, read like the diary reads it; the posting time from older servers.
    static func text(for activity: ActivityItem, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> String? {
        guard activity.consumedAt != nil else { return shortText(from: activity.createdAt, now: now) }
        return logDateText(from: activity.consumedAt, now: now, calendar: calendar)
    }

    static func accessibilityText(for activity: ActivityItem) -> String? {
        guard activity.consumedAt != nil else { return fullText(from: activity.createdAt) }
        return DiaryDateFormatter.date(from: activity.consumedAt)?.formatted(date: .long, time: .omitted)
    }

    /// "Today", "Yesterday", "Oct 24", or "Oct 24, 2019" outside this year.
    static func logDateText(from rawValue: String?, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> String? {
        guard let date = DiaryDateFormatter.date(from: rawValue) else { return nil }
        if calendar.isDate(date, inSameDayAs: now) {
            return "Today"
        }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: yesterday) {
            return "Yesterday"
        }
        var style = Date.FormatStyle.dateTime.month(.abbreviated).day()
        if !calendar.isDate(date, equalTo: now, toGranularity: .year) {
            style = style.year()
        }
        style.calendar = calendar
        style.timeZone = calendar.timeZone
        return date.formatted(style)
    }

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
