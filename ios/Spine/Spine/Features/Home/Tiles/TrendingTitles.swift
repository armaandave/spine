import Foundation

/// One title in the Trending tile: what you and the people you follow have been logging, newest and most shared first.
struct TrendingTitle: Equatable, Identifiable {
    /// The title whatever its season or episode (`FeedEntries.titleKey`).
    let key: String
    /// The show, never one of its seasons or episodes.
    let media: MediaSummary
    /// Distinct people who logged or progressed it, most recent first. Empty for a fallback title.
    let people: [UserSummary]
    /// You are one of `people`.
    let includesYou: Bool
    /// The mean of the logs' ratings, in stars, only when two or more logs rated it.
    let averageRating: Double?
    /// The newest day it was done, "2026-11-07". Nil for a fallback title.
    let lastDay: String?

    var id: String { key }
}

struct TrendingResult: Equatable {
    /// At most 10.
    let titles: [TrendingTitle]
    /// "This week", "Lately" or "Popular".
    let subtitle: String
}

/// Works out the Trending tile from activity rows already in memory. Only logs, days of progress and starts count: a
/// status move, a Planning day, a rating without a log and a row with no poster are left out.
enum TrendingAggregator {
    static let windowDays = 7
    /// Fewer titles than this in the window widens it to everything given.
    static let minimumTitles = 3
    /// Fewer titles than this are topped up from the fallback list.
    static let wantedTitles = 5
    static let maxTitles = 10

    static func titles(
        feed: [ActivityItem],
        own: [ActivityItem],
        you: UserSummary?,
        fallback: [MediaSummary],
        now: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> TrendingResult {
        let (people, widened) = peopleTitles(feed: feed, own: own, you: you, now: now, calendar: calendar)
        var titles = Array(people.prefix(maxTitles))
        var subtitle = "Popular"
        if people.count >= minimumTitles { subtitle = widened ? "Lately" : "This week" }

        if titles.count < wantedTitles {
            var seen = Set(titles.map(\.key))
            for media in fallback where titles.count < wantedTitles {
                let key = FeedEntries.titleKey(for: media.ref)
                guard media.displayPosterURL != nil, seen.insert(key).inserted else { continue }
                titles.append(TrendingTitle(
                    key: key, media: media, people: [], includesYou: false, averageRating: nil, lastDay: nil
                ))
            }
        }
        return TrendingResult(titles: titles, subtitle: subtitle)
    }

    /// True when the people's own titles are too few (under 5), so the caller should fetch the fallback list.
    static func needsFallback(
        feed: [ActivityItem],
        own: [ActivityItem],
        you: UserSummary? = nil,
        now: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> Bool {
        peopleTitles(feed: feed, own: own, you: you, now: now, calendar: calendar).titles.count < wantedTitles
    }

    // MARK: Ranking

    private struct Row {
        let key: String
        let media: MediaSummary
        let person: UserSummary
        let day: String?
        /// Whole days before today; negative for a day ahead of the phone's.
        let age: Int?
        let id: Int
        /// A log's rating in stars.
        let rating: Double?
    }

    /// The titles people logged, inside the window when that gives enough, else from everything given.
    private static func peopleTitles(
        feed: [ActivityItem],
        own: [ActivityItem],
        you: UserSummary?,
        now: Date,
        calendar: Calendar
    ) -> (titles: [TrendingTitle], widened: Bool) {
        let rows = rows(feed: feed, own: own, you: you, now: now, calendar: calendar)
        let recent = rank(rows.filter { ($0.age ?? Int.max) < windowDays }, you: you)
        if recent.count >= minimumTitles { return (recent, false) }
        return (rank(rows, you: you), true)
    }

    private static func rows(
        feed: [ActivityItem],
        own: [ActivityItem],
        you: UserSummary?,
        now: Date,
        calendar: Calendar
    ) -> [Row] {
        let today = calendar.startOfDay(for: now)
        var seen = Set<String>()
        var rows: [Row] = []
        rows.reserveCapacity(feed.count + own.count)

        for (item, isOwn) in feed.map({ ($0, false) }) + own.map({ ($0, true) }) {
            guard counts(item), let media = item.media, media.displayPosterURL != nil else { continue }
            // A log edited later comes as a second row of the same log.
            guard seen.insert("\(item.object.type):\(item.object.id)").inserted else { continue }
            let day = FeedEntries.dayKey(for: item, calendar: calendar)
            let isLog = item.object.type == "diary"
            rows.append(Row(
                key: FeedEntries.titleKey(for: media.ref),
                media: media,
                person: isOwn ? (you ?? item.actor) : item.actor,
                day: day,
                age: day.flatMap { age(ofDay: $0, from: today, calendar: calendar) },
                id: item.id,
                rating: isLog ? ActivityFeedPresentation.rating(for: item).flatMap { Double($0) }.flatMap { $0 > 0 ? $0 : nil } : nil
            ))
        }
        return rows
    }

    /// A log, or a day of progress with words (an amount or "Started"). A day that only moved a status has none.
    private static func counts(_ item: ActivityItem) -> Bool {
        if item.type == "diary_deleted" { return false }
        switch item.object.type {
        case "diary":
            return true
        case "progress_day":
            return ActivityFeedPresentation.clean(item.object.label) != nil
                || ActivityFeedPresentation.clean(item.object.summary) != nil
        default:
            return false
        }
    }

    private static func age(ofDay key: String, from today: Date, calendar: Calendar) -> Int? {
        let numbers = key.split(separator: "-").compactMap { Int($0) }
        guard numbers.count == 3,
              let date = calendar.date(from: DateComponents(year: numbers[0], month: numbers[1], day: numbers[2]))
        else { return nil }
        return calendar.dateComponents([.day], from: date, to: today).day
    }

    private static func rank(_ rows: [Row], you: UserSummary?) -> [TrendingTitle] {
        var groups: [String: [Row]] = [:]
        for row in rows { groups[row.key, default: []].append(row) }

        let titles = groups.map { key, group -> TrendingTitle in
            // Newest first: the day, then the row (a later row has a larger id).
            let ordered = group.sorted { ($0.day ?? "", $0.id) > ($1.day ?? "", $1.id) }
            var seenPeople = Set<Int>()
            let people = ordered.map(\.person).filter { seenPeople.insert($0.id).inserted }
            let ratings = ordered.compactMap(\.rating)
            let average = ratings.count >= 2 ? ratings.reduce(0, +) / Double(ratings.count) : nil
            // A season or an episode has its show's poster only through the show's own rows, so prefer those.
            let representative = ordered.first { !$0.media.ref.isEpisode && $0.media.ref.mediaType != "season" } ?? ordered[0]
            return TrendingTitle(
                key: key,
                media: showLevel(representative.media),
                people: people,
                includesYou: you.map { you in people.contains { $0.id == you.id } } ?? false,
                averageRating: average,
                lastDay: ordered.first?.day
            )
        }
        return titles.sorted { lhs, rhs in
            if lhs.people.count != rhs.people.count { return lhs.people.count > rhs.people.count }
            let (left, right) = (lhs.lastDay ?? "", rhs.lastDay ?? "")
            if left != right { return left > right }
            let order = lhs.media.title.localizedCaseInsensitiveCompare(rhs.media.title)
            return order == .orderedSame ? lhs.key < rhs.key : order == .orderedAscending
        }
    }

    /// A season or an episode as its show: the show's ref and title, with the poster the row came with.
    private static func showLevel(_ media: MediaSummary) -> MediaSummary {
        guard let parent = media.ref.parentTVRef else { return media }
        let ref = media.ref.source == "mal"
            ? MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: parent.mediaId,
                       seasonNumber: nil, episodeNumber: nil)
            : parent
        return MediaSummary(
            ref: ref,
            title: ActivityFeedPresentation.clean(media.showTitle)
                ?? ActivityFeedPresentation.clean(media.preferredTitle)
                ?? media.title,
            imageUrl: media.imageUrl,
            posterUrl: media.posterUrl,
            customPosterUrl: media.customPosterUrl,
            backdropUrl: media.backdropUrl,
            customBackdropUrl: media.customBackdropUrl,
            posterOrientation: media.posterOrientation,
            posterAspectRatio: media.posterAspectRatio,
            posterWidth: media.posterWidth,
            posterHeight: media.posterHeight,
            posterAccentColor: media.posterAccentColor
        )
    }
}

/// What the Trending tile's panel shows for the title in focus.
struct TrendingTileDetail: Equatable {
    struct Avatar: Equatable {
        let url: String?
        /// The first letter of the name, for a person with no picture.
        let initial: String
    }

    let title: String
    /// "★ 4.3", nil without an average.
    let averageText: String?
    /// Up to three, most recent first.
    let avatars: [Avatar]
    /// "2 logged it", "You logged it", "Popular this week".
    let whoLine: String
    /// "Plan to watch", or "Planned" for a title already in your library.
    let primaryTitle: String
    let isPlanned: Bool
    let secondaryTitle: String
    let accessibilityLabel: String
}

enum TrendingTileModel {
    static func detail(for title: TrendingTitle, planned: Bool) -> TrendingTileDetail {
        let name = title.media.displayTitle
        let average = title.averageRating.map { "\u{2605} \(String(format: "%.1f", ($0 * 10).rounded() / 10))" }
        let avatars = title.people.prefix(3).map { person in
            TrendingTileDetail.Avatar(
                url: person.avatarUrl,
                initial: String((person.displayName.isEmpty ? person.username : person.displayName).prefix(1)).uppercased()
            )
        }
        let who: String
        if title.people.isEmpty {
            who = "Popular this week"
        } else if title.people.count == 1, title.includesYou {
            who = "You logged it"
        } else {
            who = "\(title.people.count) logged it"
        }
        let primary = planned ? "Planned" : planTitle(for: title.media.ref.mediaType)
        return TrendingTileDetail(
            title: name,
            averageText: average,
            avatars: Array(avatars),
            whoLine: who,
            primaryTitle: primary,
            isPlanned: planned,
            secondaryTitle: "Open",
            accessibilityLabel: [name, average.map { "average \($0.dropFirst(2)) stars" }, who, primary]
                .compactMap { $0 }
                .joined(separator: ", ")
        )
    }

    /// Planning's words by type: "Plan to watch", "Plan to read".
    private static func planTitle(for mediaType: String) -> String {
        switch mediaType {
        case "book", "manga", "comic": "Plan to read"
        case "game", "boardgame": "Plan to play"
        case "music": "Plan to listen"
        default: "Plan to watch"
        }
    }
}
