import Foundation

// The v2 stats payload (`docs/stats-contract.md`, STAT-300 onward). Every section is decoded on
// its own, so one malformed block hides one section instead of failing the page, and lists drop
// the rows they can't read. A server that ignores `schema=2` answers with the legacy payload; the
// page then fills what it can from the legacy keys (STAT-902).

// MARK: - Page

struct StatsPage: Decodable, Hashable {
    let schemaVersion: Int
    let range: StatsRange
    /// The tab: nil on Global.
    let mediaType: String?
    /// Nil on the legacy payload, which doesn't say.
    let audience: StatsAudience?
    /// Nil on the legacy payload; the pills then use the cached years (STAT-301).
    let years: StatsYears?
    /// Every exposed primary type, in server order, whatever the tab. Empty on the legacy payload.
    let mediaTypes: [StatsMediaTypeCount]
    let header: StatsHeader?
    let time: StatsTime?
    let top: StatsTop?
    let highestRated: StatsHighestRated?
    let activity: StatsEventActivity?
    let ratings: StatsRatings?
    let ratedVsEveryone: StatsRatedVsEveryone?
    let releaseYears: StatsReleaseYears?
    let genres: StatsGenres?
    let languages: StatsLanguages?
    let listRings: [StatsListRing]
    let collections: StatsCollections?
    let mostLogged: StatsMostLogged?
    let milestones: [StatsMilestone]
    /// The legacy payload this page was built from, when the server answered without schema 2.
    let legacy: StatsSummary?

    init(
        schemaVersion: Int = 2,
        range: StatsRange = .empty,
        mediaType: String? = nil,
        audience: StatsAudience? = .owner,
        years: StatsYears? = nil,
        mediaTypes: [StatsMediaTypeCount] = [],
        header: StatsHeader? = nil,
        time: StatsTime? = nil,
        top: StatsTop? = nil,
        highestRated: StatsHighestRated? = nil,
        activity: StatsEventActivity? = nil,
        ratings: StatsRatings? = nil,
        ratedVsEveryone: StatsRatedVsEveryone? = nil,
        releaseYears: StatsReleaseYears? = nil,
        genres: StatsGenres? = nil,
        languages: StatsLanguages? = nil,
        listRings: [StatsListRing] = [],
        collections: StatsCollections? = nil,
        mostLogged: StatsMostLogged? = nil,
        milestones: [StatsMilestone] = [],
        legacy: StatsSummary? = nil
    ) {
        self.schemaVersion = schemaVersion
        self.range = range
        self.mediaType = mediaType
        self.audience = audience
        self.years = years
        self.mediaTypes = mediaTypes
        self.header = header
        self.time = time
        self.top = top
        self.highestRated = highestRated
        self.activity = activity
        self.ratings = ratings
        self.ratedVsEveryone = ratedVsEveryone
        self.releaseYears = releaseYears
        self.genres = genres
        self.languages = languages
        self.listRings = listRings
        self.collections = collections
        self.mostLogged = mostLogged
        self.milestones = milestones
        self.legacy = legacy
    }

    var isLegacy: Bool { legacy != nil }

    /// True when every section is missing or empty (STAT-302): the scope has nothing yet.
    var isEmpty: Bool {
        (header?.isEmpty ?? true)
            && (time?.isEmpty ?? true)
            && (top?.isEmpty ?? true)
            && (highestRated?.isEmpty ?? true)
            && (activity?.isEmpty ?? true)
            && (ratings?.isEmpty ?? true)
            && (ratedVsEveryone?.isEmpty ?? true)
            && (releaseYears?.isEmpty ?? true)
            && (genres?.isEmpty ?? true)
            && (languages?.isEmpty ?? true)
            && listRings.isEmpty
            && (collections?.isEmpty ?? true)
            && (mostLogged?.isEmpty ?? true)
            && milestones.isEmpty
    }

    /// The first title the page shows, for the backdrop wash: Top's #1, else Highest rated's,
    /// else Most logged's, else the newest milestone's.
    var featuredMedia: MediaSummary? {
        top?.items.first?.media
            ?? highestRated?.items.first?.media
            ?? mostLogged?.items.first?.media
            ?? milestones.last?.media
    }

    /// A legacy page rebuilt for the tab that was asked for, because the legacy payload doesn't
    /// echo `media_type`. A v2 page is returned as it is.
    func scoped(to mediaType: String?) -> StatsPage {
        guard let legacy else { return self }
        return StatsPage(legacy: legacy, mediaType: mediaType)
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case range
        case mediaType
        case audience
        case years
        case mediaTypes
        case header
        case time
        case top
        case highestRated
        case activity
        case ratings
        case ratedVsEveryone
        case releaseYears
        case genres
        case languages
        case listRings
        case collections
        case mostLogged
        case milestones
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let schemaVersion = container.statsValue(Int.self, forKey: .schemaVersion) ?? 1
        guard schemaVersion >= 2 else {
            let legacy = (try? StatsSummary(from: decoder)) ?? StatsSummary(schemaVersion: schemaVersion)
            self.init(legacy: legacy, mediaType: nil)
            return
        }
        self.init(
            schemaVersion: schemaVersion,
            range: container.statsValue(StatsRange.self, forKey: .range) ?? .empty,
            mediaType: container.statsValue(String.self, forKey: .mediaType),
            audience: container.statsValue(String.self, forKey: .audience).flatMap(StatsAudience.init(rawValue:)),
            years: container.statsValue(StatsYears.self, forKey: .years),
            mediaTypes: container.statsArray(StatsMediaTypeCount.self, forKey: .mediaTypes),
            header: container.statsValue(StatsHeader.self, forKey: .header),
            time: container.statsValue(StatsTime.self, forKey: .time),
            top: container.statsValue(StatsTop.self, forKey: .top),
            highestRated: container.statsValue(StatsHighestRated.self, forKey: .highestRated),
            activity: container.statsValue(StatsEventActivity.self, forKey: .activity),
            ratings: container.statsValue(StatsRatings.self, forKey: .ratings),
            ratedVsEveryone: container.statsValue(StatsRatedVsEveryone.self, forKey: .ratedVsEveryone),
            releaseYears: container.statsValue(StatsReleaseYears.self, forKey: .releaseYears),
            genres: container.statsValue(StatsGenres.self, forKey: .genres),
            languages: container.statsValue(StatsLanguages.self, forKey: .languages),
            listRings: container.statsArray(StatsListRing.self, forKey: .listRings),
            collections: container.statsValue(StatsCollections.self, forKey: .collections),
            mostLogged: container.statsValue(StatsMostLogged.self, forKey: .mostLogged),
            milestones: container.statsArray(StatsMilestone.self, forKey: .milestones),
            legacy: nil
        )
    }
}

// MARK: - Shared small types

/// A number the stats API sends as a decimal string (STAT-303): stars ("4.0", "3.65"),
/// everyone's stars and differences ("3.4", "-1.1"), and stored bounds ("6.5"). `text` keeps
/// the server's spelling, so a drill-down can send a bound back exactly as it came.
nonisolated struct StatsDecimal: Decodable, Hashable, CustomStringConvertible {
    let text: String
    let value: Double

    init?(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard let value = Double(trimmed), value.isFinite else { return nil }
        self.text = trimmed
        self.value = value
    }

    init(_ value: Double) {
        self.value = value
        text = Self.format(value)
    }

    var description: String { text }

    /// Two decimals at most, trailing zeros dropped down to one: 4 → "4.0", 3.65 → "3.65".
    static func format(_ value: Double) -> String {
        var text = String(format: "%.2f", value)
        if text.hasSuffix("0") { text.removeLast() }
        return text
    }

    /// Reads a decimal string as a number, for code that holds the raw string.
    static func value(_ text: String?) -> Double? {
        text.flatMap(StatsDecimal.init).map(\.value)
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let text = try? container.decode(String.self) {
            guard let decimal = StatsDecimal(text) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Not a decimal: \(text)")
            }
            self = decimal
        } else {
            self.init(try container.decode(Double.self))
        }
    }
}

enum StatsAudience: String, Hashable {
    case owner
    case follower
    case `public`
}

struct StatsYears: Codable, Hashable {
    /// The earliest year the viewer can count, of any type; nil when there is nothing.
    let first: Int?
    /// Today's year on the owner's calendar.
    let current: Int
}

struct StatsMediaTypeCount: Decodable, Hashable, Identifiable {
    let mediaType: String
    let titles: Int
    let logs: Int

    var id: String { mediaType }
}

struct StatsCoverage: Decodable, Hashable {
    let known: Int
    let total: Int
}

// MARK: - Header (STAT-200)

struct StatsHeader: Decodable, Hashable {
    let titles: Int
    let logs: Int
    let activeDays: Int
    let longestDailyStreak: Int
    /// Label/value rows. The app must not depend on which keys are present.
    let details: [StatsHeaderDetail]

    var isEmpty: Bool { titles == 0 && logs == 0 }
}

struct StatsHeaderDetail: Decodable, Hashable, Identifiable {
    let key: String
    /// Ready to show.
    let label: String
    let value: Int
    /// "day", "week" or nil; the app pluralizes.
    let unit: String?
    /// The app shows "≈".
    let approximate: Bool

    var id: String { key }
}

// MARK: - Time (STAT-201, STAT-202)

struct StatsTime: Decodable, Hashable {
    let rows: [StatsTimeRow]
    /// Global only.
    let hoursTotal: StatsHoursTotal?

    var isEmpty: Bool { rows.isEmpty }
}

struct StatsTimeRow: Decodable, Hashable, Identifiable {
    let mediaType: String
    /// "minutes", "pages", "chapters" or "issues".
    let unit: String
    let value: Int
    let known: Int
    let total: Int
    let approximate: Bool

    var id: String { mediaType }
}

struct StatsHoursTotal: Decodable, Hashable {
    let minutes: Int
    let mediaTypes: [String]
    let leavesOut: [String]
    let approximate: Bool
}

// MARK: - Top and Highest rated (STAT-205 to STAT-208)

enum StatsTopSource: String, Hashable {
    case list
    case highestRated = "highest_rated"
}

struct StatsTop: Decodable, Hashable {
    let source: StatsTopSource
    /// The wanted tag; nil for a range that has none.
    let tag: String?
    /// Owner only, fallback only.
    let tagHint: String?
    /// Owner only: the winning list isn't public.
    let hintPrivate: Bool
    /// The winning list; nil in the fallback.
    let list: StatsTopList?
    let items: [StatsTopItem]

    var isEmpty: Bool { items.isEmpty }

    /// Highest rated shows only when Top comes from a list, so the two never repeat (STAT-205).
    var isFromList: Bool { source == .list && list != nil }
}

struct StatsTopList: Decodable, Hashable, Identifiable {
    let id: Int
    let name: String
    let total: Int
}

struct StatsTopItem: Decodable, Hashable, Identifiable {
    let rank: Int
    let media: MediaSummary
    /// The title rating, or nil.
    let stars: StatsDecimal?

    var id: String { media.id }
}

struct StatsHighestRated: Decodable, Hashable {
    let total: Int
    let items: [StatsRatedTitle]

    var isEmpty: Bool { items.isEmpty }
}

/// A Highest rated row, in the section and on its show-all pages.
struct StatsRatedTitle: Decodable, Hashable, Identifiable {
    let media: MediaSummary
    let stars: StatsDecimal?
    /// The rating log's day, "YYYY-MM-DD"; nil for a current rating.
    let day: String?

    var id: String { media.id }
}

// MARK: - Activity (STAT-203)

struct StatsEventActivity: Decodable, Hashable {
    let months: [StatsEventMonth]
    /// Seven event counts, Monday first, whatever `weekStartsOn` says.
    let weekdays: [Int]
    /// "monday" or "sunday".
    let weekStartsOn: String
    let busiestDay: StatsBusiestDay?
    let busiestMonth: StatsBusiestMonth?

    var isEmpty: Bool { busiestDay == nil }
}

struct StatsEventMonth: Decodable, Hashable, Identifiable {
    /// "YYYY-MM".
    let month: String
    let count: Int
    /// Events per type bucket; types with none are left out.
    let byType: [String: Int]

    var id: String { month }
}

struct StatsBusiestDay: Decodable, Hashable {
    let date: String
    let count: Int
}

struct StatsBusiestMonth: Decodable, Hashable {
    let month: String
    let count: Int
}

// MARK: - Ratings (STAT-204)

struct StatsRatings: Decodable, Hashable {
    let averageStars: StatsDecimal?
    let rated: Int
    /// Ten buckets, "0.5" to "5.0".
    let distribution: [StatsRatingsBucket]

    var isEmpty: Bool { rated == 0 }
}

struct StatsRatingsBucket: Decodable, Hashable, Identifiable {
    let stars: StatsDecimal
    let count: Int
    /// Inclusive stored bounds on the 0–10 scale, sent back as they are by a drill-down.
    let ratingMin: StatsDecimal
    let ratingMax: StatsDecimal
    let byType: [String: Int]

    var id: String { stars.text }
}

// MARK: - Rated higher than most (STAT-217)

struct StatsRatedVsEveryone: Decodable, Hashable {
    let sources: [StatsCommunitySource]
    let higher: [StatsComparedTitle]
    let lower: [StatsComparedTitle]

    var isEmpty: Bool { higher.isEmpty && lower.isEmpty }
}

struct StatsCommunitySource: Decodable, Hashable, Identifiable {
    let mediaType: String
    let source: String
    let label: String

    var id: String { mediaType }
}

struct StatsComparedTitle: Decodable, Hashable, Identifiable {
    let media: MediaSummary
    /// Yours.
    let stars: StatsDecimal?
    let everyoneStars: StatsDecimal?
    let difference: StatsDecimal?
    let source: String

    var id: String { media.id }
}

// MARK: - Release years (STAT-209)

struct StatsReleaseYears: Decodable, Hashable {
    /// Oldest first, one per year with titles. The app folds decades itself.
    let rows: [StatsReleaseYearRow]
    let coverage: StatsCoverage?
    let newVsOlder: StatsNewVsOlder?

    var isEmpty: Bool { rows.isEmpty }
}

struct StatsReleaseYearRow: Decodable, Hashable, Identifiable {
    let year: Int
    let titles: Int
    let logs: Int
    let rated: Int
    let averageStars: StatsDecimal?
    /// Titles per type bucket.
    let byType: [String: Int]

    var id: Int { year }
}

struct StatsNewVsOlder: Decodable, Hashable {
    let new: Int
    let recent: Int
    let older: Int
}

// MARK: - Genres and languages (STAT-210 to STAT-212)

struct StatsGenres: Decodable, Hashable {
    let rows: [StatsGenreRow]
    /// Rows before the 50-row cap.
    let total: Int
    let coverage: StatsCoverage?
    /// Global only: the top music genres.
    let music: [StatsMusicGenre]

    var isEmpty: Bool { rows.isEmpty && music.isEmpty }
}

struct StatsGenreRow: Decodable, Hashable, Identifiable {
    /// The group key on Global; nil on type tabs.
    let key: String?
    let name: String
    /// Type tabs: every stored spelling, for the drill-down. Nil on Global.
    let values: [String]?
    let titles: Int
    let rated: Int
    let averageStars: StatsDecimal?
    let byType: [String: Int]

    var id: String { key ?? name }
}

struct StatsMusicGenre: Decodable, Hashable, Identifiable {
    let name: String
    let titles: Int

    var id: String { name }
}

struct StatsLanguages: Decodable, Hashable {
    let rows: [StatsLanguageRow]
    let total: Int
    let coverage: StatsCoverage?
    /// The types counted, for "From movies and TV".
    let mediaTypes: [String]

    var isEmpty: Bool { rows.isEmpty }
}

struct StatsLanguageRow: Decodable, Hashable, Identifiable {
    /// The stored English name, which the drill-down sends as it is.
    let name: String
    let titles: Int
    let rated: Int
    let averageStars: StatsDecimal?
    let byType: [String: Int]

    var id: String { name }
}

// MARK: - List progress and collections (STAT-213, STAT-214)

struct StatsListRing: Decodable, Hashable, Identifiable {
    /// The `CustomList` ID, which the app opens.
    let id: Int
    let name: String
    /// Nil for a ring with no single type.
    let mediaType: String?
    let kind: String?
    let seen: Int
    let total: Int

    /// Seen share from 0 to 1.
    var fraction: Double {
        total > 0 ? min(max(Double(seen) / Double(total), 0), 1) : 0
    }
}

struct StatsCollections: Decodable, Hashable {
    let completeCount: Int
    let almostCount: Int
    let complete: [MediaSeriesSummary]
    let almost: [MediaSeriesSummary]

    var isEmpty: Bool { completeCount == 0 && almostCount == 0 }
}

/// The collections show-all endpoint's `group`.
enum StatsCollectionGroup: String, Hashable, CaseIterable {
    case complete
    case almost
}

// MARK: - Most logged and milestones (STAT-215, STAT-216)

struct StatsMostLogged: Decodable, Hashable {
    /// "Most watched", "Most read", ... ready to show.
    let label: String
    let total: Int
    let items: [StatsMostLoggedTitle]

    var isEmpty: Bool { items.isEmpty }
}

/// A Most logged row (TV folded to the show), in the section and on its schema-2 show-all pages.
struct StatsMostLoggedTitle: Decodable, Hashable, Identifiable {
    let media: MediaSummary
    let times: Int

    var id: String { media.id }
}

struct StatsMilestone: Decodable, Hashable, Identifiable {
    let number: Int
    /// "YYYY-MM-DD".
    let day: String
    /// The milestone log, which the viewer may open.
    let entryId: Int?
    /// Its type bucket.
    let mediaType: String
    /// Its title, TV as its show.
    let media: MediaSummary

    var id: Int { number }
}

// MARK: - Decoding

// Each block's decoder lives in an extension so the memberwise initializers stay available.
// Counts default to 0 and lists to empty, so a missing field never drops a row; a row without
// its `media` (or other identity) is dropped by the lossy list around it.

extension StatsYears {
    private enum CodingKeys: String, CodingKey { case first, current }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        first = c.statsValue(Int.self, forKey: .first)
        current = try c.decode(Int.self, forKey: .current)
    }
}

extension StatsMediaTypeCount {
    private enum CodingKeys: String, CodingKey { case mediaType, titles, logs }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mediaType = try c.decode(String.self, forKey: .mediaType)
        titles = c.statsInt(forKey: .titles)
        logs = c.statsInt(forKey: .logs)
    }
}

extension StatsCoverage {
    private enum CodingKeys: String, CodingKey { case known, total }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        known = c.statsInt(forKey: .known)
        total = c.statsInt(forKey: .total)
    }
}

extension StatsHeader {
    private enum CodingKeys: String, CodingKey { case titles, logs, activeDays, longestDailyStreak, details }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        titles = c.statsInt(forKey: .titles)
        logs = c.statsInt(forKey: .logs)
        activeDays = c.statsInt(forKey: .activeDays)
        longestDailyStreak = c.statsInt(forKey: .longestDailyStreak)
        details = c.statsArray(StatsHeaderDetail.self, forKey: .details)
    }
}

extension StatsHeaderDetail {
    private enum CodingKeys: String, CodingKey { case key, label, value, unit, approximate }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        label = try c.decode(String.self, forKey: .label)
        value = c.statsInt(forKey: .value)
        unit = c.statsValue(String.self, forKey: .unit)
        approximate = c.statsBool(forKey: .approximate)
    }
}

extension StatsTime {
    private enum CodingKeys: String, CodingKey { case rows, hoursTotal }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rows = c.statsArray(StatsTimeRow.self, forKey: .rows)
        hoursTotal = c.statsValue(StatsHoursTotal.self, forKey: .hoursTotal)
    }
}

extension StatsTimeRow {
    private enum CodingKeys: String, CodingKey { case mediaType, unit, value, known, total, approximate }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mediaType = try c.decode(String.self, forKey: .mediaType)
        unit = c.statsValue(String.self, forKey: .unit) ?? "minutes"
        value = c.statsInt(forKey: .value)
        known = c.statsInt(forKey: .known)
        total = c.statsInt(forKey: .total)
        approximate = c.statsBool(forKey: .approximate)
    }
}

extension StatsHoursTotal {
    private enum CodingKeys: String, CodingKey { case minutes, mediaTypes, leavesOut, approximate }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        minutes = c.statsInt(forKey: .minutes)
        mediaTypes = c.statsArray(String.self, forKey: .mediaTypes)
        leavesOut = c.statsArray(String.self, forKey: .leavesOut)
        approximate = c.statsBool(forKey: .approximate)
    }
}

extension StatsTop {
    private enum CodingKeys: String, CodingKey { case source, tag, tagHint, hintPrivate, list, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = c.statsValue(String.self, forKey: .source).flatMap(StatsTopSource.init(rawValue:)) ?? .highestRated
        tag = c.statsValue(String.self, forKey: .tag)
        tagHint = c.statsValue(String.self, forKey: .tagHint)
        hintPrivate = c.statsBool(forKey: .hintPrivate)
        list = c.statsValue(StatsTopList.self, forKey: .list)
        items = c.statsArray(StatsTopItem.self, forKey: .items)
    }
}

extension StatsTopList {
    private enum CodingKeys: String, CodingKey { case id, name, total }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = c.statsValue(String.self, forKey: .name) ?? ""
        total = c.statsInt(forKey: .total)
    }
}

extension StatsTopItem {
    private enum CodingKeys: String, CodingKey { case rank, media, stars }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rank = c.statsInt(forKey: .rank)
        media = try c.decode(MediaSummary.self, forKey: .media)
        stars = c.statsValue(StatsDecimal.self, forKey: .stars)
    }
}

extension StatsHighestRated {
    private enum CodingKeys: String, CodingKey { case total, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = c.statsArray(StatsRatedTitle.self, forKey: .items)
        total = max(c.statsInt(forKey: .total), items.count)
    }
}

extension StatsRatedTitle {
    private enum CodingKeys: String, CodingKey { case media, stars, day }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        media = try c.decode(MediaSummary.self, forKey: .media)
        stars = c.statsValue(StatsDecimal.self, forKey: .stars)
        day = c.statsValue(String.self, forKey: .day)
    }
}

extension StatsEventActivity {
    private enum CodingKeys: String, CodingKey { case months, weekdays, weekStartsOn, busiestDay, busiestMonth }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        months = c.statsArray(StatsEventMonth.self, forKey: .months)
        let weekdays = c.statsValue([Int].self, forKey: .weekdays) ?? []
        self.weekdays = weekdays.count == 7 ? weekdays : []
        weekStartsOn = c.statsValue(String.self, forKey: .weekStartsOn) ?? "monday"
        busiestDay = c.statsValue(StatsBusiestDay.self, forKey: .busiestDay)
        busiestMonth = c.statsValue(StatsBusiestMonth.self, forKey: .busiestMonth)
    }
}

extension StatsEventMonth {
    private enum CodingKeys: String, CodingKey { case month, count, byType }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        month = try c.decode(String.self, forKey: .month)
        count = c.statsInt(forKey: .count)
        byType = c.statsValue([String: Int].self, forKey: .byType) ?? [:]
    }
}

extension StatsBusiestDay {
    private enum CodingKeys: String, CodingKey { case date, count }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        count = c.statsInt(forKey: .count)
    }
}

extension StatsBusiestMonth {
    private enum CodingKeys: String, CodingKey { case month, count }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        month = try c.decode(String.self, forKey: .month)
        count = c.statsInt(forKey: .count)
    }
}

extension StatsRatings {
    private enum CodingKeys: String, CodingKey { case averageStars, rated, distribution }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        averageStars = c.statsValue(StatsDecimal.self, forKey: .averageStars)
        rated = c.statsInt(forKey: .rated)
        distribution = c.statsArray(StatsRatingsBucket.self, forKey: .distribution)
    }
}

extension StatsRatingsBucket {
    private enum CodingKeys: String, CodingKey { case stars, count, ratingMin, ratingMax, byType }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        stars = try c.decode(StatsDecimal.self, forKey: .stars)
        count = c.statsInt(forKey: .count)
        ratingMin = try c.decode(StatsDecimal.self, forKey: .ratingMin)
        ratingMax = try c.decode(StatsDecimal.self, forKey: .ratingMax)
        byType = c.statsValue([String: Int].self, forKey: .byType) ?? [:]
    }
}

extension StatsRatedVsEveryone {
    private enum CodingKeys: String, CodingKey { case sources, higher, lower }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sources = c.statsArray(StatsCommunitySource.self, forKey: .sources)
        higher = c.statsArray(StatsComparedTitle.self, forKey: .higher)
        lower = c.statsArray(StatsComparedTitle.self, forKey: .lower)
    }
}

extension StatsCommunitySource {
    private enum CodingKeys: String, CodingKey { case mediaType, source, label }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mediaType = try c.decode(String.self, forKey: .mediaType)
        source = try c.decode(String.self, forKey: .source)
        label = c.statsValue(String.self, forKey: .label) ?? source
    }
}

extension StatsComparedTitle {
    private enum CodingKeys: String, CodingKey { case media, stars, everyoneStars, difference, source }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        media = try c.decode(MediaSummary.self, forKey: .media)
        stars = c.statsValue(StatsDecimal.self, forKey: .stars)
        everyoneStars = c.statsValue(StatsDecimal.self, forKey: .everyoneStars)
        difference = c.statsValue(StatsDecimal.self, forKey: .difference)
        source = c.statsValue(String.self, forKey: .source) ?? ""
    }
}

extension StatsReleaseYears {
    private enum CodingKeys: String, CodingKey { case rows, coverage, newVsOlder }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rows = c.statsArray(StatsReleaseYearRow.self, forKey: .rows)
        coverage = c.statsValue(StatsCoverage.self, forKey: .coverage)
        newVsOlder = c.statsValue(StatsNewVsOlder.self, forKey: .newVsOlder)
    }
}

extension StatsReleaseYearRow {
    private enum CodingKeys: String, CodingKey { case year, titles, logs, rated, averageStars, byType }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        year = try c.decode(Int.self, forKey: .year)
        titles = c.statsInt(forKey: .titles)
        logs = c.statsInt(forKey: .logs)
        rated = c.statsInt(forKey: .rated)
        averageStars = c.statsValue(StatsDecimal.self, forKey: .averageStars)
        byType = c.statsValue([String: Int].self, forKey: .byType) ?? [:]
    }
}

extension StatsNewVsOlder {
    private enum CodingKeys: String, CodingKey { case new, recent, older }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        new = c.statsInt(forKey: .new)
        recent = c.statsInt(forKey: .recent)
        older = c.statsInt(forKey: .older)
    }
}

extension StatsGenres {
    private enum CodingKeys: String, CodingKey { case rows, total, coverage, music }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rows = c.statsArray(StatsGenreRow.self, forKey: .rows)
        total = max(c.statsInt(forKey: .total), rows.count)
        coverage = c.statsValue(StatsCoverage.self, forKey: .coverage)
        music = c.statsArray(StatsMusicGenre.self, forKey: .music)
    }
}

extension StatsGenreRow {
    private enum CodingKeys: String, CodingKey { case key, name, values, titles, rated, averageStars, byType }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = c.statsValue(String.self, forKey: .key)
        name = try c.decode(String.self, forKey: .name)
        values = c.statsValue([String].self, forKey: .values)
        titles = c.statsInt(forKey: .titles)
        rated = c.statsInt(forKey: .rated)
        averageStars = c.statsValue(StatsDecimal.self, forKey: .averageStars)
        byType = c.statsValue([String: Int].self, forKey: .byType) ?? [:]
    }
}

extension StatsMusicGenre {
    private enum CodingKeys: String, CodingKey { case name, titles }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        titles = c.statsInt(forKey: .titles)
    }
}

extension StatsLanguages {
    private enum CodingKeys: String, CodingKey { case rows, total, coverage, mediaTypes }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rows = c.statsArray(StatsLanguageRow.self, forKey: .rows)
        total = max(c.statsInt(forKey: .total), rows.count)
        coverage = c.statsValue(StatsCoverage.self, forKey: .coverage)
        mediaTypes = c.statsArray(String.self, forKey: .mediaTypes)
    }
}

extension StatsLanguageRow {
    private enum CodingKeys: String, CodingKey { case name, titles, rated, averageStars, byType }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        titles = c.statsInt(forKey: .titles)
        rated = c.statsInt(forKey: .rated)
        averageStars = c.statsValue(StatsDecimal.self, forKey: .averageStars)
        byType = c.statsValue([String: Int].self, forKey: .byType) ?? [:]
    }
}

extension StatsListRing {
    private enum CodingKeys: String, CodingKey { case id, name, mediaType, kind, seen, total }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        mediaType = c.statsValue(String.self, forKey: .mediaType)
        kind = c.statsValue(String.self, forKey: .kind)
        seen = c.statsInt(forKey: .seen)
        total = c.statsInt(forKey: .total)
    }
}

extension StatsCollections {
    private enum CodingKeys: String, CodingKey { case completeCount, almostCount, complete, almost }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        complete = c.statsArray(MediaSeriesSummary.self, forKey: .complete)
        almost = c.statsArray(MediaSeriesSummary.self, forKey: .almost)
        completeCount = max(c.statsInt(forKey: .completeCount), complete.count)
        almostCount = max(c.statsInt(forKey: .almostCount), almost.count)
    }
}

extension StatsMostLogged {
    private enum CodingKeys: String, CodingKey { case label, total, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.statsValue(String.self, forKey: .label) ?? "Most logged"
        items = c.statsArray(StatsMostLoggedTitle.self, forKey: .items)
        total = max(c.statsInt(forKey: .total), items.count)
    }
}

extension StatsMostLoggedTitle {
    private enum CodingKeys: String, CodingKey { case media, times }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        media = try c.decode(MediaSummary.self, forKey: .media)
        times = c.statsInt(forKey: .times)
    }
}

extension StatsMilestone {
    private enum CodingKeys: String, CodingKey { case number, day, entryId, mediaType, media }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        number = try c.decode(Int.self, forKey: .number)
        day = try c.decode(String.self, forKey: .day)
        entryId = c.statsValue(Int.self, forKey: .entryId)
        media = try c.decode(MediaSummary.self, forKey: .media)
        mediaType = c.statsValue(String.self, forKey: .mediaType) ?? media.ref.mediaType
    }
}

/// An array that drops the elements it can't decode instead of failing.
private struct StatsLossyArray<Element: Decodable>: Decodable {
    let elements: [Element]

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var elements: [Element] = []
        while !container.isAtEnd {
            if let element = try? container.decode(Element.self) {
                elements.append(element)
            } else if (try? container.decode(StatsSkippedValue.self)) == nil {
                // A failed decode doesn't advance the container, so skip the element; if even
                // that fails, stop rather than spin on the same element.
                break
            }
        }
        self.elements = elements
    }
}

private struct StatsSkippedValue: Decodable {
    init(from decoder: Decoder) throws {}
}

private extension KeyedDecodingContainer {
    /// An optional value, treating a value of the wrong shape as missing.
    func statsValue<T: Decodable>(_ type: T.Type, forKey key: Key) -> T? {
        (try? decodeIfPresent(type, forKey: key)) ?? nil
    }

    /// A list that drops the rows it can't read; missing or malformed is empty.
    func statsArray<T: Decodable>(_ type: T.Type, forKey key: Key) -> [T] {
        statsValue(StatsLossyArray<T>.self, forKey: key)?.elements ?? []
    }

    func statsInt(forKey key: Key) -> Int {
        statsValue(Int.self, forKey: key) ?? 0
    }

    func statsBool(forKey key: Key) -> Bool {
        statsValue(Bool.self, forKey: key) ?? false
    }
}

// MARK: - Legacy fallback (STAT-902)

extension StatsPage {
    /// A page built from the legacy payload: the header from `overview` (or the tab's
    /// `media_types[]` entry), activity from the legacy months and days on Global, and genres
    /// from `top_genres`. The sections the legacy payload can't fill, Ratings included, stay
    /// hidden.
    init(legacy summary: StatsSummary, mediaType: String?) {
        let typeSummary = mediaType.flatMap(summary.mediaTypeSummary(for:))
        self.init(
            schemaVersion: summary.schemaVersion,
            range: summary.range,
            mediaType: mediaType,
            audience: nil,
            years: nil,
            mediaTypes: [],
            header: Self.legacyHeader(summary, mediaType: mediaType, typeSummary: typeSummary),
            activity: mediaType == nil ? Self.legacyActivity(summary.activity) : nil,
            genres: Self.legacyGenres(summary, mediaType: mediaType, typeSummary: typeSummary),
            legacy: summary
        )
    }

    private static func legacyHeader(
        _ summary: StatsSummary,
        mediaType: String?,
        typeSummary: StatsMediaTypeSummary?
    ) -> StatsHeader {
        func detail(_ key: String, _ label: String, _ value: Int, unit: String? = nil) -> StatsHeaderDetail {
            StatsHeaderDetail(key: key, label: label, value: value, unit: unit, approximate: false)
        }

        guard let mediaType else {
            let overview = summary.overview
            var details: [StatsHeaderDetail] = []
            if overview.currentStreakDays > 0 {
                details.append(detail("current_daily_streak", "Current daily streak", overview.currentStreakDays, unit: "day"))
            }
            details += [
                detail("rewatches", repeatsLabel(for: nil), overview.repeatCount),
                detail("reviews", "Reviews", overview.reviewCount),
                detail("likes", "Likes", overview.likedCount),
            ]
            return StatsHeader(
                titles: overview.uniqueLoggedCount,
                logs: overview.diaryEntryCount,
                activeDays: overview.activeDays,
                longestDailyStreak: overview.longestStreakDays,
                details: details
            )
        }

        // The legacy type entries carry no days or streaks.
        guard let typeSummary else {
            return StatsHeader(titles: 0, logs: 0, activeDays: 0, longestDailyStreak: 0, details: [])
        }
        return StatsHeader(
            titles: typeSummary.uniqueLoggedCount,
            logs: typeSummary.diaryEntryCount,
            activeDays: 0,
            longestDailyStreak: 0,
            details: [
                detail("rewatches", repeatsLabel(for: mediaType), typeSummary.repeatCount),
                detail("reviews", "Reviews", typeSummary.reviewCount),
                detail("likes", "Likes", typeSummary.likedCount),
            ]
        )
    }

    /// The `rewatches` label per tab (STAT-200).
    static func repeatsLabel(for mediaType: String?) -> String {
        switch mediaType {
        case nil: "Repeats"
        case "book", "manga", "comic": "Rereads"
        case "game": "Replays"
        case "music": "Relistens"
        default: "Rewatches"
        }
    }

    private static func legacyActivity(_ activity: StatsActivity) -> StatsEventActivity? {
        guard !activity.days.isEmpty || !activity.months.isEmpty else { return nil }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        var weekdays = Array(repeating: 0, count: 7)
        for day in activity.days {
            guard let date = day.parsedDate else { continue }
            // Calendar weekdays run Sunday = 1 ... Saturday = 7; the payload's run Monday first.
            let index = (calendar.component(.weekday, from: date) + 5) % 7
            weekdays[index] += day.count
        }

        // Ties go to the later one (STAT-203). Days and months sort as strings.
        let busiestDay = activity.days
            .filter { $0.count > 0 }
            .max { ($0.count, $0.date) < ($1.count, $1.date) }
        let busiestMonth = activity.months
            .filter { $0.count > 0 }
            .max { ($0.count, $0.month) < ($1.count, $1.month) }

        return StatsEventActivity(
            months: activity.months.map { StatsEventMonth(month: $0.month, count: $0.count, byType: [:]) },
            weekdays: activity.days.isEmpty ? [] : weekdays,
            weekStartsOn: "monday",
            busiestDay: busiestDay.map { StatsBusiestDay(date: $0.date, count: $0.count) },
            busiestMonth: busiestMonth.map { StatsBusiestMonth(month: $0.month, count: $0.count) }
        )
    }

    private static func legacyGenres(
        _ summary: StatsSummary,
        mediaType: String?,
        typeSummary: StatsMediaTypeSummary?
    ) -> StatsGenres? {
        let genres = mediaType == nil ? summary.topGenres : (typeSummary?.topGenres ?? [])
        let coverage = mediaType == nil ? summary.metadataCoverage : (typeSummary?.metadataCoverage ?? .empty)
        guard !genres.isEmpty else { return nil }
        let rows = genres.map { genre in
            StatsGenreRow(
                key: nil,
                name: genre.name,
                values: [genre.name],
                titles: genre.count,
                rated: 0,
                averageStars: nil,
                byType: mediaType.map { [$0: genre.count] } ?? [:]
            )
        }
        return StatsGenres(
            rows: rows,
            total: rows.count,
            coverage: coverage.totalItems > 0
                ? StatsCoverage(known: coverage.genreItems, total: coverage.totalItems)
                : nil,
            music: []
        )
    }
}

// MARK: - Year pills

extension StatsPeriod {
    /// The pills after All Time: every year from the first the viewer can count to this year,
    /// newest first. Before a response, `years` is the last cached block for that person; with
    /// none, the recent years.
    static func pillYears(
        _ years: StatsYears?,
        from date: Date = .now,
        calendar: Calendar = .current
    ) -> [StatsPeriod] {
        guard let years else { return recentYears(from: date, calendar: calendar) }
        let thisYear = calendar.component(.year, from: date)
        let last = max(years.current, thisYear)
        // A bad first year can't make a pill per year back to year 1.
        let first = min(max(years.first ?? last, last - 150), last)
        return (first ... last).reversed().map { .year($0) }
    }
}
