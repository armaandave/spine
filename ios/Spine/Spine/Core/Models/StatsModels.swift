import Foundation

enum StatsPeriod: Hashable, Identifiable {
    case allTime
    case year(Int)

    var id: String {
        switch self {
        case .allTime:
            "all-time"
        case let .year(year):
            "year-\(year)"
        }
    }

    var title: String {
        switch self {
        case .allTime:
            "All Time"
        case let .year(year):
            String(year)
        }
    }

    var query: [URLQueryItem] {
        switch self {
        case .allTime:
            [
                URLQueryItem(name: "start_date", value: "all"),
                URLQueryItem(name: "end_date", value: "all"),
            ]
        case let .year(year):
            [
                URLQueryItem(name: "start_date", value: String(format: "%04d-01-01", year)),
                URLQueryItem(name: "end_date", value: String(format: "%04d-12-31", year)),
            ]
        }
    }

    static func recentYears(
        count: Int = 5,
        from date: Date = .now,
        calendar: Calendar = .current
    ) -> [StatsPeriod] {
        guard count > 0 else { return [] }
        let currentYear = calendar.component(.year, from: date)
        return (0 ..< count).map { .year(currentYear - $0) }
    }
}

struct StatsSummary: Decodable, Hashable {
    let schemaVersion: Int
    let range: StatsRange
    let overview: StatsOverview
    let mediaTypes: [StatsMediaTypeSummary]
    let activity: StatsActivity
    let ratingDistribution: [StatsRatingBucket]
    let releaseYears: [StatsReleaseYearBucket]
    let topGenres: [StatsNamedCount]
    let topLanguages: [StatsNamedCount]
    let metadataCoverage: StatsMetadataCoverage
    let topRated: [StatsTopRatedItem]
    let mostLogged: [StatsMostLoggedItem]
    /// Every title logged at least twice; `mostLogged` is only a preview.
    let mostLoggedTotal: Int
    let listProgress: [StatsListProgressItem]
    let seriesProgress: [MediaSeriesSummary]

    init(
        schemaVersion: Int = 1,
        range: StatsRange = .empty,
        overview: StatsOverview = .empty,
        mediaTypes: [StatsMediaTypeSummary] = [],
        activity: StatsActivity = .empty,
        ratingDistribution: [StatsRatingBucket] = [],
        releaseYears: [StatsReleaseYearBucket] = [],
        topGenres: [StatsNamedCount] = [],
        topLanguages: [StatsNamedCount] = [],
        metadataCoverage: StatsMetadataCoverage = .empty,
        topRated: [StatsTopRatedItem] = [],
        mostLogged: [StatsMostLoggedItem] = [],
        mostLoggedTotal: Int? = nil,
        listProgress: [StatsListProgressItem] = [],
        seriesProgress: [MediaSeriesSummary] = []
    ) {
        self.schemaVersion = schemaVersion
        self.range = range
        self.overview = overview
        self.mediaTypes = mediaTypes
        self.activity = activity
        self.ratingDistribution = ratingDistribution
        self.releaseYears = releaseYears
        self.topGenres = topGenres
        self.topLanguages = topLanguages
        self.metadataCoverage = metadataCoverage
        self.topRated = topRated
        self.mostLogged = mostLogged
        self.mostLoggedTotal = max(mostLoggedTotal ?? mostLogged.count, mostLogged.count)
        self.listProgress = listProgress
        self.seriesProgress = seriesProgress
    }

    var isEmpty: Bool {
        let periodDataIsEmpty = overview.diaryEntryCount == 0
            && overview.uniqueLoggedCount == 0
            && overview.reviewCount == 0
            && overview.repeatCount == 0
            && overview.ratedCount == 0
            && activity.days.isEmpty
            && activity.months.isEmpty
            && ratingDistribution.allSatisfy { $0.count == 0 }
            && releaseYears.isEmpty
            && topGenres.isEmpty
            && topLanguages.isEmpty
            && topRated.isEmpty
            && mostLogged.isEmpty
            && listProgress.isEmpty
            && seriesProgress.isEmpty
        if !range.isAllTime {
            return periodDataIsEmpty
        }
        return periodDataIsEmpty
            && overview.isEmpty
            && mediaTypes.allSatisfy(\.isEmpty)
    }

    func mediaTypeSummary(for mediaType: String) -> StatsMediaTypeSummary? {
        mediaTypes.first { $0.mediaType == mediaType }
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case range
        case overview
        case mediaTypes
        case activity
        case ratingDistribution
        case releaseYears
        case topGenres
        case topLanguages
        case metadataCoverage
        case diaryTopRated
        case topRated
        case mostLogged
        case mostLoggedTotal
        case listProgress
        case seriesProgress
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            schemaVersion: try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1,
            range: try container.decodeIfPresent(StatsRange.self, forKey: .range) ?? .empty,
            overview: try container.decodeIfPresent(StatsOverview.self, forKey: .overview) ?? .empty,
            mediaTypes: try container.decodeIfPresent([StatsMediaTypeSummary].self, forKey: .mediaTypes) ?? [],
            activity: try container.decodeIfPresent(StatsActivity.self, forKey: .activity) ?? .empty,
            ratingDistribution: try container.decodeIfPresent([StatsRatingBucket].self, forKey: .ratingDistribution) ?? [],
            releaseYears: try container.decodeIfPresent([StatsReleaseYearBucket].self, forKey: .releaseYears) ?? [],
            topGenres: try container.decodeIfPresent([StatsNamedCount].self, forKey: .topGenres) ?? [],
            topLanguages: try container.decodeIfPresent([StatsNamedCount].self, forKey: .topLanguages) ?? [],
            metadataCoverage: try container.decodeIfPresent(StatsMetadataCoverage.self, forKey: .metadataCoverage) ?? .empty,
            topRated: try container.decodeIfPresent([StatsTopRatedItem].self, forKey: .diaryTopRated)
                ?? container.decodeIfPresent([StatsTopRatedItem].self, forKey: .topRated)
                ?? [],
            mostLogged: try container.decodeIfPresent([StatsMostLoggedItem].self, forKey: .mostLogged) ?? [],
            mostLoggedTotal: try container.decodeIfPresent(Int.self, forKey: .mostLoggedTotal),
            listProgress: try container.decodeIfPresent([StatsListProgressItem].self, forKey: .listProgress) ?? [],
            seriesProgress: try container.decodeIfPresent([MediaSeriesSummary].self, forKey: .seriesProgress) ?? []
        )
    }
}

struct StatsRange: Decodable, Hashable {
    let startDate: String?
    let endDate: String?
    let timezone: String
    let isAllTime: Bool

    static let empty = StatsRange(startDate: nil, endDate: nil, timezone: "UTC", isAllTime: true)

    var parsedStartDate: Date? { startDate.flatMap(StatsActivityDay.parse) }
    var parsedEndDate: Date? { endDate.flatMap(StatsActivityDay.parse) }

    init(startDate: String?, endDate: String?, timezone: String, isAllTime: Bool) {
        self.startDate = startDate
        self.endDate = endDate
        self.timezone = timezone
        self.isAllTime = isAllTime
    }

    private enum CodingKeys: String, CodingKey {
        case startDate
        case endDate
        case timezone
        case isAllTime
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let startDate = try container.decodeIfPresent(String.self, forKey: .startDate)
        let endDate = try container.decodeIfPresent(String.self, forKey: .endDate)
        self.init(
            startDate: startDate,
            endDate: endDate,
            timezone: try container.decodeIfPresent(String.self, forKey: .timezone) ?? "UTC",
            isAllTime: try container.decodeIfPresent(Bool.self, forKey: .isAllTime) ?? (startDate == nil && endDate == nil)
        )
    }
}

struct StatsOverview: Decodable, Hashable {
    let trackedCount: Int
    let completedCount: Int
    let diaryEntryCount: Int
    let uniqueLoggedCount: Int
    let reviewCount: Int
    let repeatCount: Int
    let ratedCount: Int
    let averageRating: String?
    let likedCount: Int
    let activeDays: Int
    let currentStreakDays: Int
    let longestStreakDays: Int
    let completion: CompletionProgress?

    static let empty = StatsOverview()

    var numericAverageRating: Double? {
        averageRating.flatMap(Double.init)
    }

    init(
        trackedCount: Int = 0,
        completedCount: Int = 0,
        diaryEntryCount: Int = 0,
        uniqueLoggedCount: Int = 0,
        reviewCount: Int = 0,
        repeatCount: Int = 0,
        ratedCount: Int = 0,
        averageRating: String? = nil,
        likedCount: Int = 0,
        activeDays: Int = 0,
        currentStreakDays: Int = 0,
        longestStreakDays: Int = 0,
        completion: CompletionProgress? = nil
    ) {
        self.trackedCount = trackedCount
        self.completedCount = completedCount
        self.diaryEntryCount = diaryEntryCount
        self.uniqueLoggedCount = uniqueLoggedCount
        self.reviewCount = reviewCount
        self.repeatCount = repeatCount
        self.ratedCount = ratedCount
        self.averageRating = averageRating
        self.likedCount = likedCount
        self.activeDays = activeDays
        self.currentStreakDays = currentStreakDays
        self.longestStreakDays = longestStreakDays
        self.completion = completion
    }

    var isEmpty: Bool {
        completedCount == 0
            && diaryEntryCount == 0
            && uniqueLoggedCount == 0
            && reviewCount == 0
            && repeatCount == 0
            && ratedCount == 0
    }

    private enum CodingKeys: String, CodingKey {
        case trackedCount
        case completedCount
        case diaryEntryCount
        case uniqueLoggedCount
        case reviewCount
        case repeatCount
        case ratedCount
        case averageRating
        case likedCount
        case activeDays
        case currentStreakDays
        case longestStreakDays
        case completion
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            trackedCount: try container.decodeIfPresent(Int.self, forKey: .trackedCount) ?? 0,
            completedCount: try container.decodeIfPresent(Int.self, forKey: .completedCount) ?? 0,
            diaryEntryCount: try container.decodeIfPresent(Int.self, forKey: .diaryEntryCount) ?? 0,
            uniqueLoggedCount: try container.decodeIfPresent(Int.self, forKey: .uniqueLoggedCount) ?? 0,
            reviewCount: try container.decodeIfPresent(Int.self, forKey: .reviewCount) ?? 0,
            repeatCount: try container.decodeIfPresent(Int.self, forKey: .repeatCount) ?? 0,
            ratedCount: try container.decodeIfPresent(Int.self, forKey: .ratedCount) ?? 0,
            averageRating: try container.decodeIfPresent(String.self, forKey: .averageRating),
            likedCount: try container.decodeIfPresent(Int.self, forKey: .likedCount) ?? 0,
            activeDays: try container.decodeIfPresent(Int.self, forKey: .activeDays) ?? 0,
            currentStreakDays: try container.decodeIfPresent(Int.self, forKey: .currentStreakDays) ?? 0,
            longestStreakDays: try container.decodeIfPresent(Int.self, forKey: .longestStreakDays) ?? 0,
            completion: try container.decodeIfPresent(CompletionProgress.self, forKey: .completion)
        )
    }
}

struct StatsMediaTypeSummary: Decodable, Hashable, Identifiable {
    let mediaType: String
    let trackedCount: Int
    let completedCount: Int
    let diaryEntryCount: Int
    let uniqueLoggedCount: Int
    let reviewCount: Int
    let repeatCount: Int
    let ratedCount: Int
    let averageRating: String?
    let likedCount: Int
    let statuses: [String: Int]
    let ratingDistribution: [StatsRatingBucket]
    let topRated: [StatsTopRatedItem]
    let mostLogged: [StatsMostLoggedItem]
    let mostLoggedTotal: Int
    let releaseYears: [StatsReleaseYearBucket]
    let topGenres: [StatsNamedCount]
    let topLanguages: [StatsNamedCount]
    let metadataCoverage: StatsMetadataCoverage
    let completion: CompletionProgress?

    var id: String { mediaType }

    var numericAverageRating: Double? {
        averageRating.flatMap(Double.init)
    }

    init(
        mediaType: String,
        trackedCount: Int = 0,
        completedCount: Int = 0,
        diaryEntryCount: Int = 0,
        uniqueLoggedCount: Int = 0,
        reviewCount: Int = 0,
        repeatCount: Int = 0,
        ratedCount: Int = 0,
        averageRating: String? = nil,
        likedCount: Int = 0,
        statuses: [String: Int] = [:],
        ratingDistribution: [StatsRatingBucket] = [],
        topRated: [StatsTopRatedItem] = [],
        mostLogged: [StatsMostLoggedItem] = [],
        mostLoggedTotal: Int? = nil,
        releaseYears: [StatsReleaseYearBucket] = [],
        topGenres: [StatsNamedCount] = [],
        topLanguages: [StatsNamedCount] = [],
        metadataCoverage: StatsMetadataCoverage = .empty,
        completion: CompletionProgress? = nil
    ) {
        self.mediaType = mediaType
        self.trackedCount = trackedCount
        self.completedCount = completedCount
        self.diaryEntryCount = diaryEntryCount
        self.uniqueLoggedCount = uniqueLoggedCount
        self.reviewCount = reviewCount
        self.repeatCount = repeatCount
        self.ratedCount = ratedCount
        self.averageRating = averageRating
        self.likedCount = likedCount
        self.statuses = statuses
        self.ratingDistribution = ratingDistribution
        self.topRated = topRated
        self.mostLogged = mostLogged
        self.mostLoggedTotal = max(mostLoggedTotal ?? mostLogged.count, mostLogged.count)
        self.releaseYears = releaseYears
        self.topGenres = topGenres
        self.topLanguages = topLanguages
        self.metadataCoverage = metadataCoverage
        self.completion = completion
    }

    var isEmpty: Bool {
        completedCount == 0
            && diaryEntryCount == 0
            && uniqueLoggedCount == 0
            && reviewCount == 0
            && repeatCount == 0
            && ratedCount == 0
            && ratingDistribution.allSatisfy { $0.count == 0 }
            && topRated.isEmpty
            && mostLogged.isEmpty
            && releaseYears.isEmpty
            && topGenres.isEmpty
            && topLanguages.isEmpty
    }

    private enum CodingKeys: String, CodingKey {
        case mediaType
        case trackedCount
        case completedCount
        case diaryEntryCount
        case uniqueLoggedCount
        case reviewCount
        case repeatCount
        case ratedCount
        case averageRating
        case likedCount
        case statuses
        case ratingDistribution
        case topRated
        case mostLogged
        case mostLoggedTotal
        case releaseYears
        case topGenres
        case topLanguages
        case metadataCoverage
        case completion
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            mediaType: try container.decode(String.self, forKey: .mediaType),
            trackedCount: try container.decodeIfPresent(Int.self, forKey: .trackedCount) ?? 0,
            completedCount: try container.decodeIfPresent(Int.self, forKey: .completedCount) ?? 0,
            diaryEntryCount: try container.decodeIfPresent(Int.self, forKey: .diaryEntryCount) ?? 0,
            uniqueLoggedCount: try container.decodeIfPresent(Int.self, forKey: .uniqueLoggedCount) ?? 0,
            reviewCount: try container.decodeIfPresent(Int.self, forKey: .reviewCount) ?? 0,
            repeatCount: try container.decodeIfPresent(Int.self, forKey: .repeatCount) ?? 0,
            ratedCount: try container.decodeIfPresent(Int.self, forKey: .ratedCount) ?? 0,
            averageRating: try container.decodeIfPresent(String.self, forKey: .averageRating),
            likedCount: try container.decodeIfPresent(Int.self, forKey: .likedCount) ?? 0,
            statuses: try container.decodeIfPresent([String: Int].self, forKey: .statuses) ?? [:],
            ratingDistribution: try container.decodeIfPresent([StatsRatingBucket].self, forKey: .ratingDistribution) ?? [],
            topRated: try container.decodeIfPresent([StatsTopRatedItem].self, forKey: .topRated) ?? [],
            mostLogged: try container.decodeIfPresent([StatsMostLoggedItem].self, forKey: .mostLogged) ?? [],
            mostLoggedTotal: try container.decodeIfPresent(Int.self, forKey: .mostLoggedTotal),
            releaseYears: try container.decodeIfPresent([StatsReleaseYearBucket].self, forKey: .releaseYears) ?? [],
            topGenres: try container.decodeIfPresent([StatsNamedCount].self, forKey: .topGenres) ?? [],
            topLanguages: try container.decodeIfPresent([StatsNamedCount].self, forKey: .topLanguages) ?? [],
            metadataCoverage: try container.decodeIfPresent(StatsMetadataCoverage.self, forKey: .metadataCoverage) ?? .empty,
            completion: try container.decodeIfPresent(CompletionProgress.self, forKey: .completion)
        )
    }
}

struct StatsActivity: Decodable, Hashable {
    let days: [StatsActivityDay]
    let months: [StatsActivityMonth]
    let activeDays: Int
    let currentStreakDays: Int
    let longestStreakDays: Int
    let mostActiveWeekday: StatsMostActiveWeekday?

    static let empty = StatsActivity()

    init(
        days: [StatsActivityDay] = [],
        months: [StatsActivityMonth] = [],
        activeDays: Int = 0,
        currentStreakDays: Int = 0,
        longestStreakDays: Int = 0,
        mostActiveWeekday: StatsMostActiveWeekday? = nil
    ) {
        self.days = days
        self.months = months
        self.activeDays = activeDays
        self.currentStreakDays = currentStreakDays
        self.longestStreakDays = longestStreakDays
        self.mostActiveWeekday = mostActiveWeekday
    }

    private enum CodingKeys: String, CodingKey {
        case days
        case months
        case activeDays
        case currentStreakDays
        case longestStreakDays
        case mostActiveWeekday
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            days: try container.decodeIfPresent([StatsActivityDay].self, forKey: .days) ?? [],
            months: try container.decodeIfPresent([StatsActivityMonth].self, forKey: .months) ?? [],
            activeDays: try container.decodeIfPresent(Int.self, forKey: .activeDays) ?? 0,
            currentStreakDays: try container.decodeIfPresent(Int.self, forKey: .currentStreakDays) ?? 0,
            longestStreakDays: try container.decodeIfPresent(Int.self, forKey: .longestStreakDays) ?? 0,
            mostActiveWeekday: try container.decodeIfPresent(StatsMostActiveWeekday.self, forKey: .mostActiveWeekday)
        )
    }
}

struct StatsActivityDay: Decodable, Hashable, Identifiable {
    let date: String
    let count: Int

    var id: String { date }

    var parsedDate: Date? {
        Self.parse(date)
    }

    static func parse(_ value: String) -> Date? { dateFormatter.date(from: value) }

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}

struct StatsActivityMonth: Decodable, Hashable, Identifiable {
    let month: String
    let count: Int

    var id: String { month }
}

struct StatsMostActiveWeekday: Decodable, Hashable, Identifiable {
    let weekday: Int
    let name: String
    let activeDayCount: Int
    let percentage: Double

    var id: Int { weekday }
}

struct StatsRatingBucket: Decodable, Hashable, Identifiable {
    let rating: String
    let count: Int

    var id: String { rating }

    var numericRating: Double? {
        Double(rating)
    }
}

struct StatsReleaseYearBucket: Decodable, Hashable, Identifiable {
    let year: Int
    let count: Int

    var id: Int { year }
}

struct StatsNamedCount: Decodable, Hashable, Identifiable {
    let name: String
    let count: Int

    var id: String { name }
}

struct StatsMetadataCoverage: Decodable, Hashable {
    let totalItems: Int
    let releaseYearItems: Int
    let genreItems: Int
    let languageItems: Int

    static let empty = StatsMetadataCoverage()

    init(totalItems: Int = 0, releaseYearItems: Int = 0, genreItems: Int = 0, languageItems: Int = 0) {
        self.totalItems = totalItems
        self.releaseYearItems = releaseYearItems
        self.genreItems = genreItems
        self.languageItems = languageItems
    }

    private enum CodingKeys: String, CodingKey {
        case totalItems
        case releaseYearItems
        case genreItems
        case languageItems
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            totalItems: try container.decodeIfPresent(Int.self, forKey: .totalItems) ?? 0,
            releaseYearItems: try container.decodeIfPresent(Int.self, forKey: .releaseYearItems) ?? 0,
            genreItems: try container.decodeIfPresent(Int.self, forKey: .genreItems) ?? 0,
            languageItems: try container.decodeIfPresent(Int.self, forKey: .languageItems) ?? 0
        )
    }
}

struct StatsTopRatedItem: Decodable, Hashable, Identifiable {
    let media: MediaSummary
    let rating: String?

    var id: MediaSummary.ID { media.id }
}

struct StatsMostLoggedItem: Decodable, Hashable, Identifiable {
    let media: MediaSummary
    let logCount: Int

    var id: MediaSummary.ID { media.id }
}

struct StatsListProgressItem: Decodable, Hashable, Identifiable {
    let id: Int
    let name: String
    let mediaType: String?
    let posterUrls: [String]
    let completion: CompletionProgress
}
