import Foundation

struct DiaryEntry: Codable, Identifiable {
    let id: Int
    let user: UserSummary
    let media: DiaryMedia
    let consumedAt: String?
    let rating: String?
    let reviewTitle: String?
    let review: String?
    let containsSpoilers: Bool
    let liked: Bool
    let isRewatch: Bool
    let isTrueReread: Bool?
    let bookJourneyId: Int?
    var gamePlaythroughId: Int? = nil
    var totalMinutes: Int? = nil
    /// Rounded down to a whole number, for builds that read only those.
    var percentage: Int? = nil
    var percentageExact: Decimal? = nil
    var completion: Decimal? { percentageExact ?? percentage.map { Decimal($0) } }
    var isTrueReplay: Bool? = nil
    let tags: [String]
    /// The log's favorite song (album logs) or episode (show and season logs).
    var favorite: LogFavorite? = nil
    let visibility: String
    let likeCount: Int
    var commentCount: Int? = nil
    let viewerHasLiked: Bool
    let createdAt: String?
    let updatedAt: String?
    /// The owner's view of a TV log: its level, coverage and what deleting it unmarks (TV-406). Nil for others.
    var tvLog: TVLogInfo? = nil
    /// A TV log's create and update responses add the write envelope keys (plan section 2.3.7): `tv` scoped to the
    /// page the log was saved from, and the log target's own `season` and `episode`.
    var tv: TVShowState? = nil
    var season: TVSeasonState? = nil
    var episode: TVEpisodeState? = nil
    var seasonEpisodes: [TVEpisodeState]? = nil
    var finished: TVFinished? = nil
    var earlierUnwatched: TVEarlierUnwatched? = nil
}

/// A log's favorite: a song from the album's tracklist, or an episode of the show or season.
struct LogFavorite: Codable, Hashable {
    let kind: Kind
    let title: String
    /// Songs: the tracklist's recording ID and the song's number on the album.
    var id: String? = nil
    var number: String? = nil
    var seasonNumber: Int? = nil
    var episodeNumber: Int? = nil

    enum Kind: String, Codable {
        case song
        case episode

        /// "Favorite Song" or "Favorite Episode".
        var label: String {
            self == .song ? "Favorite Song" : "Favorite Episode"
        }
    }

    static func song(_ track: MusicTrack) -> LogFavorite {
        LogFavorite(kind: .song, title: track.title, id: track.recording.recordingMbid, number: track.number)
    }

    /// TMDB sometimes has no name for an episode yet.
    static func episode(_ episode: EpisodeSummary, seasonNumber: Int) -> LogFavorite {
        LogFavorite(
            kind: .episode,
            title: episode.title.isEmpty ? "Episode \(episode.episodeNumber)" : episode.title,
            seasonNumber: seasonNumber,
            episodeNumber: episode.episodeNumber
        )
    }

    /// A favorite episode can give away a log marked as spoilers, so it waits behind the same reveal as the review.
    /// A song can't.
    func isHidden(containsSpoilers: Bool, revealed: Bool) -> Bool {
        kind == .episode && containsSpoilers && !revealed
    }

    /// "S1 E4" for an episode; a song's own number.
    var numberLabel: String? {
        switch kind {
        case .song:
            number.flatMap { $0.isEmpty ? nil : $0 }
        case .episode:
            seasonNumber.flatMap { season in episodeNumber.map { "S\(season) E\($0)" } }
        }
    }

    /// The same song or episode, whatever its stored title. A deluxe album can list one recording twice, so a song
    /// is also its place on the album.
    func matches(_ other: LogFavorite?) -> Bool {
        guard let other, kind == other.kind else { return false }
        return kind == .song
            ? id == other.id && number == other.number
            : seasonNumber == other.seasonNumber && episodeNumber == other.episodeNumber
    }
}

struct DiaryMedia: Codable {
    let ref: MediaRef
    let title: String
    let imageUrl: String?
    let posterUrl: String?
    let customPosterUrl: String?
    let posterOrientation: PosterOrientation?
    /// The show's title for season and episode logs (TV-702).
    var showTitle: String? = nil
    /// An episode log's episode title from the stored listing; nil when unknown (TV-702).
    var episodeTitle: String? = nil

    var displayPosterURL: String? {
        customPosterUrl ?? posterUrl ?? imageUrl
    }

    var displayTitle: String {
        ref.displayTitle(title)
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case title
        case imageUrl
        case posterUrl
        case customPosterUrl
        case posterOrientation
        case showTitle
        case episodeTitle
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        ref = try container.decode(MediaRef.self, forKey: .ref)
        title = try container.decode(String.self, forKey: .title)
        self.imageUrl = imageUrl
        posterUrl = try container.decodeIfPresent(String.self, forKey: .posterUrl) ?? imageUrl
        customPosterUrl = try container.decodeIfPresent(String.self, forKey: .customPosterUrl)
        posterOrientation = try container.decodeIfPresent(PosterOrientation.self, forKey: .posterOrientation)
        showTitle = try container.decodeIfPresent(String.self, forKey: .showTitle)
        episodeTitle = try container.decodeIfPresent(String.self, forKey: .episodeTitle)
    }
}

struct DiaryEntryWriteRequest: Encodable {
    let ref: MediaRef
    let consumedAt: Date?
    let rating: Decimal?
    let review: String
    let reviewTitle: String
    let liked: Bool
    let isRewatch: Bool
    let autoMarkConsumed: Bool
    let containsSpoilers: Bool
    let visibility: String
    let tags: [String]
    let journeyId: Int?
    let mutationId: UUID?
    var favorite: LogFavorite?
    /// False leaves `is_rewatch` out, so the server's TV-404 default applies (a TV log whose default hasn't loaded).
    var includesIsRewatch = true
    /// A TV log saved from a season or episode page names that page, so the response's `tv` carries the page's
    /// state and composer (TV-404, TV-705).
    var pageSeasonNumber: Int?
    var pageEpisodeNumber: Int?

    init(
        ref: MediaRef,
        consumedAt: Date?,
        rating: Decimal?,
        review: String,
        reviewTitle: String,
        liked: Bool,
        isRewatch: Bool,
        autoMarkConsumed: Bool,
        containsSpoilers: Bool,
        visibility: String,
        tags: [String],
        journeyId: Int? = nil,
        mutationId: UUID? = nil
    ) {
        self.ref = ref
        self.consumedAt = consumedAt
        self.rating = rating
        self.review = review
        self.reviewTitle = reviewTitle
        self.liked = liked
        self.isRewatch = isRewatch
        self.autoMarkConsumed = autoMarkConsumed
        self.containsSpoilers = containsSpoilers
        self.visibility = visibility
        self.tags = tags
        self.journeyId = journeyId
        self.mutationId = mutationId
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case consumedAt
        case rating
        case review
        case reviewTitle
        case liked
        case isRewatch
        case autoMarkConsumed
        case containsSpoilers
        case visibility
        case tags
        case journeyId
        case mutationId
        case favorite
        case pageSeasonNumber
        case pageEpisodeNumber
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(ref, forKey: .ref)
        if let consumedAt {
            // TV logs are calendar dates too (TV-407); installed builds' datetimes are converted by the server.
            if ref.usesCalendarLogDate {
                try container.encode(CalendarDateCodec.string(from: consumedAt), forKey: .consumedAt)
            } else {
                try container.encode(consumedAt, forKey: .consumedAt)
            }
        }
        try container.encodeIfPresent(rating, forKey: .rating)
        try container.encode(review, forKey: .review)
        try container.encode(reviewTitle, forKey: .reviewTitle)
        try container.encode(liked, forKey: .liked)
        if includesIsRewatch {
            try container.encode(isRewatch, forKey: .isRewatch)
        }
        try container.encode(autoMarkConsumed, forKey: .autoMarkConsumed)
        try container.encode(containsSpoilers, forKey: .containsSpoilers)
        try container.encode(visibility, forKey: .visibility)
        try container.encode(tags, forKey: .tags)
        try container.encodeIfPresent(journeyId, forKey: .journeyId)
        try container.encodeIfPresent(mutationId, forKey: .mutationId)
        try container.encodeIfPresent(favorite, forKey: .favorite)
        try container.encodeIfPresent(pageSeasonNumber, forKey: .pageSeasonNumber)
        try container.encodeIfPresent(pageEpisodeNumber, forKey: .pageEpisodeNumber)
    }
}

struct DiaryEntryUpdateRequest: Encodable {
    let consumedAt: Date?
    let rating: Decimal?
    let review: String?
    let reviewTitle: String?
    let tags: [String]?
    let liked: Bool?
    let isRewatch: Bool?
    let containsSpoilers: Bool?
    let visibility: String?
    let calendarDateOnly: Bool
    let includesRating: Bool
    let totalMinutes: Int?
    let percentage: Decimal?
    let includesMinutes: Bool
    let includesPercentage: Bool
    /// Sent only when `includesFavorite`, so nil clears it.
    var favorite: LogFavorite?
    var includesFavorite = false

    init(
        consumedAt: Date?,
        rating: Decimal?,
        review: String?,
        reviewTitle: String?,
        tags: [String]?,
        liked: Bool?,
        isRewatch: Bool?,
        containsSpoilers: Bool?,
        visibility: String?,
        calendarDateOnly: Bool = false,
        includesRating: Bool = false,
        totalMinutes: Int? = nil,
        percentage: Decimal? = nil,
        includesMinutes: Bool = false,
        includesPercentage: Bool = false
    ) {
        self.consumedAt = consumedAt
        self.rating = rating
        self.review = review
        self.reviewTitle = reviewTitle
        self.tags = tags
        self.liked = liked
        self.isRewatch = isRewatch
        self.containsSpoilers = containsSpoilers
        self.visibility = visibility
        self.calendarDateOnly = calendarDateOnly
        self.includesRating = includesRating || rating != nil
        self.totalMinutes = totalMinutes
        self.percentage = percentage
        self.includesMinutes = includesMinutes
        self.includesPercentage = includesPercentage
    }

    enum CodingKeys: String, CodingKey {
        case consumedAt
        case rating
        case review
        case reviewTitle
        case tags
        case liked
        case isRewatch
        case containsSpoilers
        case visibility
        case totalMinutes
        case percentage
        case favorite
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if let consumedAt {
            if calendarDateOnly {
                try container.encode(CalendarDateCodec.string(from: consumedAt), forKey: .consumedAt)
            } else {
                try container.encode(consumedAt, forKey: .consumedAt)
            }
        }
        if includesRating {
            try container.encode(rating, forKey: .rating)
        }
        try container.encodeIfPresent(review, forKey: .review)
        try container.encodeIfPresent(reviewTitle, forKey: .reviewTitle)
        try container.encodeIfPresent(tags, forKey: .tags)
        try container.encodeIfPresent(liked, forKey: .liked)
        try container.encodeIfPresent(isRewatch, forKey: .isRewatch)
        try container.encodeIfPresent(containsSpoilers, forKey: .containsSpoilers)
        try container.encodeIfPresent(visibility, forKey: .visibility)
        if includesMinutes { try container.encode(totalMinutes, forKey: .totalMinutes) }
        if includesPercentage { try container.encode(percentage, forKey: .percentage) }
        if includesFavorite { try container.encode(favorite, forKey: .favorite) }
    }
}

/// The day a log opens on. A movie, episode or album finished in the small hours belongs to the day it started:
/// logged before 6 AM, it takes the day before when its length reaches back past midnight. With no length it stays
/// today, and the log screen can always change the date.
nonisolated enum LogStartDay {
    private static let cutoffHour = 6

    static func date(lengthMinutes: Int?, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> Date {
        guard
            let lengthMinutes, lengthMinutes > 0,
            calendar.component(.hour, from: now) < cutoffHour,
            let started = calendar.date(byAdding: .minute, value: -lengthMinutes, to: now),
            started < calendar.startOfDay(for: now),
            let yesterday = calendar.date(byAdding: .day, value: -1, to: now)
        else { return now }
        return yesterday
    }

    /// Minutes from the text the API sends: "2h 7m", "48m", "58 min", "120". Nil when there's no usable length.
    static func minutes(runtime: String?) -> Int? {
        guard let text = runtime?.lowercased().trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        let hours = amount(in: text, unit: "h")
        let minutes = amount(in: text, unit: "m")
        let total = hours == nil && minutes == nil ? Double(text) : (hours ?? 0) * 60 + (minutes ?? 0)
        guard let total, let rounded = Int(exactly: total.rounded()), rounded > 0 else { return nil }
        return rounded
    }

    private static func amount(in text: String, unit: String) -> Double? {
        guard let match = text.range(of: #"\d+(\.\d+)?\s*"# + unit, options: .regularExpression) else { return nil }
        return Double(text[match].dropLast(unit.count).trimmingCharacters(in: .whitespaces))
    }
}

enum CalendarDateCodec {
    static func string(from date: Date, calendar: Calendar = .autoupdatingCurrent) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    static func date(from value: String?, calendar: Calendar = .autoupdatingCurrent) -> Date? {
        guard let value else { return nil }
        let parts = value.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return ISO8601DateFormatter().date(from: value) }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    static func isFuture(_ date: Date, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> Bool {
        calendar.startOfDay(for: date) > calendar.startOfDay(for: now)
    }
}

struct DiaryTagSuggestion: Codable, Hashable {
    let name: String
    let usageCount: Int
}

struct DiaryTagSuggestionsResponse: Codable {
    let results: [DiaryTagSuggestion]
}

struct ActivityItem: Codable, Identifiable {
    let id: Int
    let type: String
    let createdAt: String?
    /// The log's date, which orders activity; older servers don't send it.
    let consumedAt: String?
    let actor: UserSummary
    let media: MediaSummary?
    let person: ActivityPersonSnapshot?
    let object: ActivityObject
    /// Whether the viewer liked this post, and how many people did. Older servers don't send them.
    var viewer: ActivityViewer? = nil
    var likeCount: Int? = nil
    var commentCount: Int? = nil
    /// A log's review, which older servers don't send either.
    var reviewTitle: String? = nil
    var review: String? = nil
    var containsSpoilers: Bool? = nil

    init(
        id: Int,
        type: String,
        createdAt: String?,
        consumedAt: String? = nil,
        actor: UserSummary,
        media: MediaSummary?,
        person: ActivityPersonSnapshot? = nil,
        object: ActivityObject
    ) {
        self.id = id
        self.type = type
        self.createdAt = createdAt
        self.consumedAt = consumedAt
        self.actor = actor
        self.media = media
        self.person = person
        self.object = object
    }
}

struct ActivityViewer: Codable {
    let hasLiked: Bool
}

struct ActivityPersonSnapshot: Codable, Hashable {
    let source: String
    let id: String
    let name: String
    let profileUrl: String?
    let knownForDepartment: String?

    var ref: PersonRef {
        PersonRef(source: source, id: id)
    }
}

struct ActivityObject: Codable {
    let type: String
    let id: Int
    let previous: ProgressState?
    let current: ProgressState?
    let rating: String?
    let liked: Bool?
    let name: String?
    /// A log that was a rewatch or reread; older servers don't send it.
    var isRewatch: Bool? = nil
    /// A day's progress, whole ("watched 4 episodes") and short ("4 episodes").
    var summary: String? = nil
    var label: String? = nil
}

struct ActivityCursorResponse: Codable {
    let nextCursor: String?
    let previousCursor: String?
    let results: [ActivityItem]
}
