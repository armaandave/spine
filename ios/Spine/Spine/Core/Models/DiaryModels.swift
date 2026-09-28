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
    var percentage: Int? = nil
    var isTrueReplay: Bool? = nil
    let tags: [String]
    let visibility: String
    let likeCount: Int
    let viewerHasLiked: Bool
    let createdAt: String?
    let updatedAt: String?
}

struct DiaryMedia: Codable {
    let ref: MediaRef
    let title: String
    let imageUrl: String?
    let posterUrl: String?
    let customPosterUrl: String?
    let posterOrientation: PosterOrientation?

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
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(ref, forKey: .ref)
        if let consumedAt {
            if ref.usesCalendarConsumptionDate {
                try container.encode(CalendarDateCodec.string(from: consumedAt), forKey: .consumedAt)
            } else {
                try container.encode(consumedAt, forKey: .consumedAt)
            }
        }
        try container.encodeIfPresent(rating, forKey: .rating)
        try container.encode(review, forKey: .review)
        try container.encode(reviewTitle, forKey: .reviewTitle)
        try container.encode(liked, forKey: .liked)
        try container.encode(isRewatch, forKey: .isRewatch)
        try container.encode(autoMarkConsumed, forKey: .autoMarkConsumed)
        try container.encode(containsSpoilers, forKey: .containsSpoilers)
        try container.encode(visibility, forKey: .visibility)
        try container.encode(tags, forKey: .tags)
        try container.encodeIfPresent(journeyId, forKey: .journeyId)
        try container.encodeIfPresent(mutationId, forKey: .mutationId)
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
    let percentage: Int?
    let includesMinutes: Bool
    let includesPercentage: Bool

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
        percentage: Int? = nil,
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
    let actor: UserSummary
    let media: MediaSummary?
    let person: ActivityPersonSnapshot?
    let object: ActivityObject

    init(
        id: Int,
        type: String,
        createdAt: String?,
        actor: UserSummary,
        media: MediaSummary?,
        person: ActivityPersonSnapshot? = nil,
        object: ActivityObject
    ) {
        self.id = id
        self.type = type
        self.createdAt = createdAt
        self.actor = actor
        self.media = media
        self.person = person
        self.object = object
    }
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
}

struct ActivityCursorResponse: Codable {
    let nextCursor: String?
    let previousCursor: String?
    let results: [ActivityItem]
}
