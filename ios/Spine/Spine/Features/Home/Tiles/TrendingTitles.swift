import Foundation

/// One title in the Trending tile: what you and the people you follow have been logging, newest and most shared first.
struct TrendingTitle: Equatable, Identifiable {
    /// The title whatever its season or episode (`FeedEntries.titleKey`).
    let key: String
    /// The show, never one of its seasons or episodes.
    let media: MediaSummary
    /// Distinct people who logged it, most recent first, you included. Empty for a fallback title.
    let people: [UserSummary]
    /// You are one of `people`.
    let includesYou: Bool
    /// The mean of the logs' ratings, in stars, only when two or more logs rated it.
    let averageRating: Double?
    /// The newest day it was done, "2026-11-07". Nil for a fallback title.
    let lastDay: String?
    /// `people` without you: the faces the panel shows.
    let friends: [UserSummary]

    var id: String { key }

    /// `friends` is `people` unless the ranking says which of them is you.
    init(
        key: String,
        media: MediaSummary,
        people: [UserSummary],
        includesYou: Bool,
        averageRating: Double?,
        lastDay: String?,
        friends: [UserSummary]? = nil
    ) {
        self.key = key
        self.media = media
        self.people = people
        self.includesYou = includesYou
        self.averageRating = averageRating
        self.lastDay = lastDay
        self.friends = friends ?? people
    }
}

struct TrendingResult: Equatable {
    /// At most 10.
    let titles: [TrendingTitle]
    /// "This week", "Lately" or "Popular".
    let subtitle: String
}

/// Works out the Trending tile from activity rows already in memory. Only logs count: a person qualifies on a title by
/// having logged it (a log is the finish the app records; marks never reach activity). A day of progress, a start, a
/// status move, a Planning day, a rating without a log and a row with no poster are left out. A day of progress would
/// count only when it reached the title's end, but the wire's `progress_day` object carries no `current` or `max` (only
/// words, an amount and moves), so there is no way to tell: the server would have to send an explicit finished flag.
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

    /// A log that has not been deleted.
    private static func counts(_ item: ActivityItem) -> Bool {
        item.type != "diary_deleted" && item.object.type == "diary"
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
                lastDay: ordered.first?.day,
                friends: people.filter { $0.id != you?.id }
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
        let name: String
    }

    /// The most faces beside the average; the rest are a "+N" chip.
    static let maxFaces = 4

    let title: String
    /// "4.4", nil without an average. The panel adds the star and "avg".
    let averageText: String?
    /// Friends who logged it, most recent first, up to four.
    let avatars: [Avatar]
    /// Friends past the four faces.
    let moreCount: Int
    /// No friends and no average: the row says "Popular this week" so it is never empty.
    let isPopularFallback: Bool
    /// "Severance, average 4.4 out of 5, 2 friends logged or finished it".
    let accessibilityLabel: String
    /// "Maya, Theo and 2 more logged or finished it".
    let facesLabel: String
}

enum TrendingTileModel {
    static func detail(for title: TrendingTitle) -> TrendingTileDetail {
        let name = title.media.displayTitle
        let average = title.averageRating.map { String(format: "%.1f", ($0 * 10).rounded() / 10) }
        let friends = title.friends
        let avatars = friends.prefix(TrendingTileDetail.maxFaces).map { person in
            let shown = displayName(person)
            return TrendingTileDetail.Avatar(url: person.avatarUrl, initial: String(shown.prefix(1)).uppercased(), name: shown)
        }
        let more = max(0, friends.count - TrendingTileDetail.maxFaces)
        let isFallback = friends.isEmpty && average == nil

        var parts = [name]
        if let average { parts.append("average \(average) out of 5") }
        if !friends.isEmpty {
            parts.append("\(friends.count) \(friends.count == 1 ? "friend" : "friends") logged or finished it")
        } else if isFallback {
            parts.append("Popular this week")
        }
        return TrendingTileDetail(
            title: name,
            averageText: average,
            avatars: Array(avatars),
            moreCount: more,
            isPopularFallback: isFallback,
            accessibilityLabel: parts.joined(separator: ", "),
            facesLabel: facesLabel(for: friends.map(displayName))
        )
    }

    private static func displayName(_ person: UserSummary) -> String {
        person.displayName.isEmpty ? person.username : person.displayName
    }

    /// Up to two names spoken, then "N more".
    private static func facesLabel(for names: [String]) -> String {
        let tail = "logged or finished it"
        switch names.count {
        case 0: return ""
        case 1: return "\(names[0]) \(tail)"
        case 2: return "\(names[0]) and \(names[1]) \(tail)"
        default: return "\(names[0]), \(names[1]) and \(names.count - 2) more \(tail)"
        }
    }
}
