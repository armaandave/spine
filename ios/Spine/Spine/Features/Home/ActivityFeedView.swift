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
    /// An Activity list opened from a number: the number in words, the type's total, and what it includes with no
    /// date. They come with the first page only.
    var topLine: String?
    var total: ActivityListTotal?
    var undated: ActivityUndated?

    /// A profile's username, or nil for the activity of the people the signed-in user follows (Home).
    private let username: String?
    /// What an Activity list asks for, sent with every page; nil for Home and the profile's rail.
    private let filter: ActivityListFilter?
    private let pageSize: Int
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void
    private var nextCursorLink: String?
    private var didLoad = false
    private var isLoadingFirstPage = false
    /// Something changed elsewhere since the last load: reload when the screen next appears.
    private var isStale = false

    init(
        username: String?,
        filter: ActivityListFilter? = nil,
        pageSize: Int = 25,
        activityRepository: ActivityRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.username = username
        self.filter = filter
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

    /// A save or a log elsewhere changed what this list would say. It reloads on the next `reloadIfStale`, not at
    /// once, so a screen behind another doesn't walk the history for every write.
    func markStale() {
        isStale = true
    }

    func reloadIfStale() async {
        guard isStale, didLoad else { return }
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

    /// A post's page changed its likes or comments.
    func apply(_ engagement: PostEngagement, to post: ActivityPostRef) {
        guard let index = items.firstIndex(where: { $0.post == post }) else { return }
        items[index].engagement = engagement
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
            topLine = response.topLine
            total = response.total
            undated = response.undated
            didLoad = true
            isStale = false
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func page(cursorLink: String?) async throws -> ActivityCursorResponse {
        if let username, let filter {
            return try await activityRepository.userActivityList(
                username: username,
                filter: filter,
                pageSize: pageSize,
                cursorLink: cursorLink
            )
        }
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
        VStack(spacing: 0) {
            ForEach(0 ..< rowCount, id: \.self) { _ in
                HStack(alignment: .top, spacing: 12) {
                    RoundedRectangle(cornerRadius: PosterSlot.activityPost.cornerRadius, style: .continuous)
                        .frame(width: PosterSlot.activityPost.size.width, height: PosterSlot.activityPost.size.height)

                    VStack(alignment: .leading, spacing: 10) {
                        bar(width: 90, height: 12)
                        bar(width: 150, height: 16)
                        bar(width: 80, height: 11)
                        bar(width: 180, height: 12)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.vertical, 14)
            }
        }
        .foregroundStyle(.white.opacity(0.07))
        .accessibilityHidden(true)
    }

    private func bar(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 4)
            .frame(width: width, height: height)
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
        case "planning_added":
            PlanningWords.headline(for: activity)
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
        case .planning:
            "Opens the titles added"
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
        capitalizingFirstLetter(clean(activity.object.summary) ?? "updated progress")
    }

    /// The words under a post's title when it has no marks to show there: a day's progress, or what a log with no
    /// rating, heart or rewatch is. Nil means the marks go there.
    static func statusText(for activity: ActivityItem) -> String? {
        if case .progress = activity.post {
            return progressHeadline(for: activity)
        }
        guard ratingSummary(for: activity).isEmpty, !isRewatch(activity) else { return nil }
        return capitalizingFirstLetter(actionText(for: activity))
    }

    /// What a log's row quotes: the review, or its title when that's all there is.
    static func reviewText(for activity: ActivityItem) -> String? {
        clean(activity.review) ?? clean(activity.reviewTitle)
    }

    /// The stars and heart under a log's title. The review has its own line, so there's no mark for one.
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

    private static func capitalizingFirstLetter(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
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
    case planning(PlanningRef)
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
        if let planning = activity.planning {
            return .planning(planning)
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
