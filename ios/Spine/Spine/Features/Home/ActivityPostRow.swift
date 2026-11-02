import SwiftUI

/// One post in the activity feed, the same size whatever it is: a log, a day's progress, or a start. The title always
/// sits on the second line, so the feed reads as one even list. A post's full review and spoilers are on its own page.
struct ActivityPostRow: View {
    let activity: ActivityItem
    let showsSeparator: Bool
    let onOpen: () -> Void
    let onToggleLike: () -> Void
    let onComment: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            MediaArtwork(
                url: activity.media?.displayPosterURL,
                title: activity.media?.title ?? "",
                slot: .listPreview,
                mediaType: activity.media?.ref.mediaType,
                orientation: activity.media?.posterOrientation
            )

            VStack(alignment: .leading, spacing: 3) {
                authorLine
                titleLine
                detailLine
            }
            .frame(maxWidth: .infinity, minHeight: PosterSlot.listPreview.size.height, alignment: .topLeading)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 10)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .overlay(alignment: .bottom) {
            if showsSeparator {
                DiarySeparator()
                    .padding(.leading, 65)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Open", onOpen)
        .accessibilityHint(ActivityFeedPresentation.destinationHint(for: activity))
    }

    private var authorLine: some View {
        HStack(spacing: 6) {
            PostAuthorLink(user: activity.actor, avatarSize: 20, spacing: 6, font: .system(size: 13, weight: .semibold))

            Spacer(minLength: 4)

            PostActions(
                engagement: activity.engagement,
                iconSize: 18,
                slotWidth: 35,
                spacing: 4,
                countSize: 13,
                onToggleLike: onToggleLike,
                onComment: onComment
            )
            // The buttons keep their full height to tap without making the line any taller.
            .padding(.vertical, -12)
        }
    }

    /// The title gives way to the marks: a long title cuts off, and the stars always show.
    private var titleLine: some View {
        HStack(spacing: 6) {
            Text(activity.media?.displayTitle ?? "")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(1)

            LogRatingMarks(
                summary: ActivityFeedPresentation.ratingSummary(for: activity),
                isRepeat: ActivityFeedPresentation.isRewatch(activity),
                repeatLabel: activity.media?.ref.repeatLabel ?? "Rewatch"
            )
            .fixedSize()
        }
    }

    @ViewBuilder
    private var detailLine: some View {
        Group {
            if case .progress = activity.post {
                Text(ActivityFeedPresentation.progressHeadline(for: activity))
            } else if let review = ActivityFeedPresentation.reviewText(for: activity) {
                if activity.containsSpoilers == true {
                    HStack(spacing: 5) {
                        Image(systemName: "eye.slash")
                            .font(.system(size: 11, weight: .semibold))
                        Text("Contains spoilers")
                    }
                } else {
                    Text(review)
                }
            }
        }
        .font(.system(size: 13))
        .foregroundStyle(.white.opacity(0.74))
        .lineLimit(1)
    }
}

/// The feed as Home draws it: a header for each day, then that day's posts as one glass panel.
enum ActivityFeedEntry: Identifiable {
    case day(key: String, title: String, isFirst: Bool)
    case post(ActivityItem, isFirst: Bool, isLast: Bool)

    var id: String {
        switch self {
        case let .day(key, _, _): "day-\(key)"
        case let .post(activity, _, _): "post-\(activity.id)"
        }
    }

    static func entries(from items: [ActivityItem], now: Date = Date()) -> [ActivityFeedEntry] {
        let days = Dictionary(grouping: items) { dayKey(for: $0) }
        var seen = Set<String>()
        var entries: [ActivityFeedEntry] = []
        for key in items.map({ dayKey(for: $0) }) where seen.insert(key).inserted {
            let title = ActivityTimestampFormatter.logDateText(from: key, now: now) ?? key
            entries.append(.day(key: key, title: title, isFirst: seen.count == 1))
            let posts = days[key] ?? []
            for (index, post) in posts.enumerated() {
                entries.append(.post(post, isFirst: index == 0, isLast: index == posts.count - 1))
            }
        }
        return entries
    }

    /// The post's calendar day; older servers only send when it was written.
    private static func dayKey(for activity: ActivityItem) -> String {
        String((activity.consumedAt ?? activity.createdAt ?? "").prefix(10))
    }
}
