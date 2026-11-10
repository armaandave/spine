import Foundation

/// A percent as it's typed and shown: whole unless someone wants decimals, and then up to two.
nonisolated enum ProgressPercent {
    /// What a percent field keeps of what's typed: three digits, a decimal point and two decimals. A comma, from a
    /// keypad in a region that uses one, becomes the point.
    static func sanitized(_ text: String) -> String {
        var whole = ""
        var decimals = ""
        var hasPoint = false
        for character in text {
            if let digit = character.wholeNumberValue {
                if !hasPoint, whole.count < 3 {
                    whole += String(digit)
                } else if hasPoint, decimals.count < 2 {
                    decimals += String(digit)
                }
            } else if separators.contains(String(character)) {
                hasPoint = true
            }
        }
        return hasPoint ? "\(whole).\(decimals)" : whole
    }

    /// The number in a field's text, or nil when it isn't one. "42." is 42 and ".5" is 0.5.
    static func value(_ text: String) -> Decimal? {
        var normalized = ""
        for character in text.trimmingCharacters(in: .whitespacesAndNewlines) {
            if let digit = character.wholeNumberValue {
                normalized += String(digit)
            } else if separators.contains(String(character)) {
                normalized += "."
            } else {
                return nil
            }
        }
        guard normalized.contains(where: \.isNumber), normalized.filter({ $0 == "." }).count <= 1 else { return nil }
        return Decimal(string: normalized, locale: Locale(identifier: "en_US_POSIX"))
    }

    private static var separators: Set<String> { [".", ",", Locale.current.decimalSeparator ?? "."] }

    static func rounded(_ value: Decimal) -> Decimal {
        var value = value
        var result = Decimal()
        NSDecimalRound(&result, &value, 2, .plain)
        return result
    }

    /// "65" or "42.5", never "65.0".
    static func text(_ value: Decimal) -> String {
        NSDecimalNumber(decimal: rounded(value)).stringValue
    }
}

enum ProgressUpdateMode: String, CaseIterable, Identifiable {
    case pages
    case percentage

    var id: String { rawValue }

    var title: String {
        switch self {
        case .pages: "Pages"
        case .percentage: "Percent"
        }
    }

    var apiValue: String {
        switch self {
        case .pages: "pages"
        case .percentage: "percentage"
        }
    }

    var unit: String {
        switch self {
        case .pages: "page"
        case .percentage: "percent"
        }
    }
}

struct TrackingState: Codable, Equatable {
    let trackingId: Int
    let status: String?
    let rating: String?
    let progress: ProgressState?
    let latestProgressChange: ProgressChangeState?
    let repeats: Int?
    let startDate: String?
    let endDate: String?
    let notes: String?
    let updatedAt: String?
    let liked: Bool?
    let directConsumption: Bool?
    let ratingSourceDiaryEntryId: Int?
    let likeSourceDiaryEntryId: Int?
    let likeIsIndependent: Bool?
    let diaryCount: Int?
    let book: BookTrackingState?
    let game: GameTrackingState?
    /// The show's TV state (detail variant on `GET tracking/{source}/{tv|season|episode}/{id}/` and in TV write
    /// responses, list variant in `GET tracking/?media_type=tv` rows). Nil for other media types.
    var tv: TVShowState? = nil
    /// A manga's or a comic's reading state: the book's shape, plus its total (MC-000). Read it through `reading`.
    var series: BookTrackingState? = nil
    /// An anime title's list state, from stored values (AN-700, AN-701). Nil for other media types, or when it can't be
    /// read: one bad state doesn't cost Home's rail every other title.
    @LossyAnimeValue var anime: AnimeState? = nil

    init(
        trackingId: Int,
        status: String?,
        rating: String?,
        progress: ProgressState?,
        latestProgressChange: ProgressChangeState? = nil,
        repeats: Int?,
        startDate: String?,
        endDate: String?,
        notes: String?,
        updatedAt: String?,
        liked: Bool? = nil,
        directConsumption: Bool? = nil,
        ratingSourceDiaryEntryId: Int? = nil,
        likeSourceDiaryEntryId: Int? = nil,
        likeIsIndependent: Bool? = nil,
        diaryCount: Int? = nil,
        book: BookTrackingState? = nil,
        game: GameTrackingState? = nil,
        tv: TVShowState? = nil,
        series: BookTrackingState? = nil,
        anime: AnimeState? = nil
    ) {
        self.trackingId = trackingId
        self.status = status
        self.rating = rating
        self.progress = progress
        self.latestProgressChange = latestProgressChange
        self.repeats = repeats
        self.startDate = startDate
        self.endDate = endDate
        self.notes = notes
        self.updatedAt = updatedAt
        self.liked = liked
        self.directConsumption = directConsumption
        self.ratingSourceDiaryEntryId = ratingSourceDiaryEntryId
        self.likeSourceDiaryEntryId = likeSourceDiaryEntryId
        self.likeIsIndependent = likeIsIndependent
        self.diaryCount = diaryCount
        self.book = book
        self.game = game
        self.tv = tv
        self.series = series
        self.anime = anime
    }

    /// The reading state of a book, a manga or a comic.
    nonisolated var reading: BookTrackingState? {
        book ?? series
    }

    func replacingProgress(_ progress: ProgressState?) -> TrackingState {
        TrackingState(
            trackingId: trackingId,
            status: status,
            rating: rating,
            progress: progress,
            latestProgressChange: latestProgressChange,
            repeats: repeats,
            startDate: startDate,
            endDate: endDate,
            notes: notes,
            updatedAt: updatedAt,
            liked: liked,
            directConsumption: directConsumption,
            ratingSourceDiaryEntryId: ratingSourceDiaryEntryId,
            likeSourceDiaryEntryId: likeSourceDiaryEntryId,
            likeIsIndependent: likeIsIndependent,
            diaryCount: diaryCount,
            book: book,
            game: game,
            tv: tv,
            series: series,
            anime: anime
        )
    }
}

nonisolated struct BookJourneyState: Codable, Equatable, Hashable, Identifiable {
    var id: Int
    var status: String
    var origin: String?
    var startDate: String?
    var endDate: String?
    /// The day of the latest progress save; older servers don't send it.
    var progressedOn: String? = nil
    var progress: ProgressState?
    var completionDiaryEntryId: Int?
    var isReread: Bool
    /// A manga or comic read that carries on from an earlier one, so it isn't a reread (MC-200).
    var continues: Bool? = nil

    init(
        id: Int,
        status: String,
        origin: String? = nil,
        startDate: String? = nil,
        endDate: String? = nil,
        progressedOn: String? = nil,
        progress: ProgressState? = nil,
        completionDiaryEntryId: Int? = nil,
        isReread: Bool = false,
        continues: Bool? = nil
    ) {
        self.id = id
        self.status = status
        self.origin = origin
        self.startDate = startDate
        self.endDate = endDate
        self.progressedOn = progressedOn
        self.progress = progress
        self.completionDiaryEntryId = completionDiaryEntryId
        self.isReread = isReread
        self.continues = continues
    }

    /// The earliest day an Update Progress save can be dated: the later of the start and the last save (decision 66).
    var progressDateFloor: Date? {
        ProgressDateFloor.date(startDate: startDate, progressedOn: progressedOn)
    }
}

enum ProgressDateFloor {
    /// The later of the two days, whichever is known; nil when neither is (no lower bound).
    static func date(startDate: String?, progressedOn: String?, calendar: Calendar = .autoupdatingCurrent) -> Date? {
        let days = [startDate, progressedOn].compactMap { CalendarDateCodec.date(from: $0, calendar: calendar) }
        return days.max()
    }
}

nonisolated struct BookUndatedReadState: Codable, Equatable, Hashable {
    let id: String
    let status: String
    let date: String?

    init(id: String = "undated", status: String = "Completed", date: String? = nil) {
        self.id = id
        self.status = status
        self.date = date
    }
}

nonisolated struct BookTrackingState: Codable, Equatable, Hashable {
    var currentJourney: BookJourneyState?
    var readingHistory: [BookJourneyState]
    var undatedRead: BookUndatedReadState?
    var statusSource: String?
    var completionDates: [String]
    var completedJourneyCount: Int
    var lifetimeReadCount: Int
    var isRereading: Bool
    var completionRequired: Bool
    var canRemoveTracking: Bool
    var removeTrackingReason: String?
    var availableActions: [String]
    var actionReasons: [String: String]
    /// A manga's chapters or a comic's issues so far, when the provider lists them (MC-002, MC-003).
    var total: Int? = nil
    /// "chapter" or "issue".
    var unit: String? = nil
    /// A Read series with more to read offers Keep Reading (MC-200).
    var offersKeepReading: Bool? = nil
    /// This save reached the total for the first time in the attempt: the log screen follows (MC-100, MC-101).
    var opensLog: Bool? = nil

    init(
        currentJourney: BookJourneyState? = nil,
        readingHistory: [BookJourneyState] = [],
        undatedRead: BookUndatedReadState? = nil,
        statusSource: String? = nil,
        completionDates: [String] = [],
        completedJourneyCount: Int = 0,
        lifetimeReadCount: Int = 0,
        isRereading: Bool = false,
        completionRequired: Bool = false,
        canRemoveTracking: Bool = true,
        removeTrackingReason: String? = nil,
        availableActions: [String] = [],
        actionReasons: [String: String] = [:],
        total: Int? = nil,
        unit: String? = nil,
        offersKeepReading: Bool? = nil,
        opensLog: Bool? = nil
    ) {
        self.currentJourney = currentJourney
        self.readingHistory = readingHistory
        self.undatedRead = undatedRead
        self.statusSource = statusSource
        self.completionDates = completionDates
        self.completedJourneyCount = completedJourneyCount
        self.lifetimeReadCount = lifetimeReadCount
        self.isRereading = isRereading
        self.completionRequired = completionRequired
        self.canRemoveTracking = canRemoveTracking
        self.removeTrackingReason = removeTrackingReason
        self.availableActions = availableActions
        self.actionReasons = actionReasons
        self.total = total
        self.unit = unit
        self.offersKeepReading = offersKeepReading
        self.opensLog = opensLog
    }

    var hasLiveJourney: Bool {
        guard let status = currentJourney?.status.lowercased() else { return false }
        return status == "in progress" || status == "paused"
    }

    func supports(_ action: String) -> Bool {
        availableActions.isEmpty || availableActions.contains(action)
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(currentJourney)
        hasher.combine(readingHistory)
        hasher.combine(undatedRead)
        hasher.combine(statusSource)
        hasher.combine(completionDates)
        hasher.combine(completedJourneyCount)
        hasher.combine(lifetimeReadCount)
        hasher.combine(isRereading)
        hasher.combine(completionRequired)
        hasher.combine(canRemoveTracking)
        hasher.combine(removeTrackingReason)
        hasher.combine(availableActions)
        hasher.combine(total)
        hasher.combine(unit)
        hasher.combine(offersKeepReading)
        hasher.combine(opensLog)
        for key in actionReasons.keys.sorted() {
            hasher.combine(key)
            hasher.combine(actionReasons[key])
        }
    }
}

struct ProgressChangeState: Codable, Equatable {
    let id: Int
    let previous: ProgressState
    let current: ProgressState
    let createdAt: String?
}

struct ProgressChangeDisplay: Equatable {
    let previous: String
    let current: String
}

extension ProgressChangeState {
    func compactDisplayParts(preferredMode: ProgressUpdateMode?) -> ProgressChangeDisplay? {
        guard
            let previousText = previous.compactDisplayText(preferredMode: preferredMode),
            let currentText = current.compactDisplayText(preferredMode: preferredMode),
            previousText != currentText
        else {
            return nil
        }
        return ProgressChangeDisplay(previous: previousText, current: currentText)
    }
}

nonisolated struct ProgressState: Codable, Hashable {
    let kind: String
    let value: Decimal?
    let max: Decimal?
    let unit: String
    /// The percent saved alongside a page position, which is rounded and can't be worked back to it.
    var percentageExact: Decimal? = nil
}

extension ProgressState {
    var compactDisplayText: String? {
        compactDisplayText(preferredMode: nil)
    }

    var detailDisplayText: String? {
        detailDisplayText(preferredMode: nil)
    }

    func compactDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = savedPercentage {
            guard value > 0 else { return nil }
            return "\(ProgressPercent.text(value))%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            guard value > 0 else { return nil }
            let maxText = max.map { "/\(Self.display($0))" } ?? ""
            let unitText = value == 1 && max == nil ? "page" : "pages"
            return "\(Self.display(value))\(maxText) \(unitText)"
        }
        guard let value else { return nil }
        guard value > 0 else { return nil }
        if isPercentage {
            return "\(ProgressPercent.text(value))%"
        }
        let maxText = max.map { "/\(Self.display($0))" } ?? ""
        return "\(Self.display(value))\(maxText) \(pluralizedUnit(for: max ?? value))"
    }

    func detailDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = savedPercentage {
            guard value > 0 else { return nil }
            return "\(ProgressPercent.text(value))%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            guard value > 0 else { return nil }
            let pages = Self.display(value)
            if let max {
                return "\(pages) of \(Self.display(max)) pages"
            }
            return "\(pages) pages"
        }
        guard let value else { return nil }
        guard value > 0 else { return nil }
        if isPercentage {
            return "\(ProgressPercent.text(value))%"
        }
        if let max {
            return "\(Self.display(value)) of \(Self.display(max)) \(pluralizedUnit(for: max))"
        }
        return "\(Self.display(value)) \(pluralizedUnit(for: value))"
    }

    var mode: ProgressUpdateMode {
        let kind = kind.lowercased()
        let unit = unit.lowercased()
        if kind.contains("percent") || unit.contains("percent") || unit == "%" {
            return .percentage
        }
        return .pages
    }

    /// A percent saved by someone tracking this title by percent. Someone tracking by pages gets `value(in:)`'s whole
    /// number instead of the 44.54% a page works out to.
    var savedPercentage: Decimal? {
        percentageExact.map(ProgressPercent.rounded) ?? value(in: .percentage)
    }

    /// Pages are whole. A saved percent keeps its decimals, and a percent worked out from pages is whole.
    func value(in requestedMode: ProgressUpdateMode) -> Decimal? {
        guard let value else { return nil }
        let number = NSDecimalNumber(decimal: value).doubleValue
        guard mode != requestedMode else {
            return mode == .percentage ? ProgressPercent.rounded(value) : Decimal(Int(number.rounded()))
        }
        guard let maxValue = max.map({ NSDecimalNumber(decimal: $0).doubleValue }), maxValue > 0 else {
            return nil
        }
        switch (mode, requestedMode) {
        case (.pages, .percentage):
            return Decimal(Int((number.rounded() / maxValue * 100).rounded()))
        case (.percentage, .pages):
            return Decimal(Int((number / 100 * maxValue).rounded()))
        default:
            return value
        }
    }

    private var isPercentage: Bool {
        mode == .percentage
    }

    private func pluralizedUnit(for value: Decimal) -> String {
        if value == 1 || unit.hasSuffix("s") {
            return unit
        }
        return "\(unit)s"
    }

    var pluralizedUnit: String {
        unit.hasSuffix("s") ? unit : "\(unit)s"
    }

    fileprivate static func display(_ value: Decimal) -> String {
        NSDecimalNumber(decimal: value).stringValue
    }
}

enum ProgressDisplayPreferences {
    private static let prefix = "progress.display.mode."

    static func mode(for ref: MediaRef) -> ProgressUpdateMode? {
        UserDefaults.standard.string(forKey: key(for: ref)).flatMap(ProgressUpdateMode.init(rawValue:))
    }

    static func setMode(_ mode: ProgressUpdateMode, for ref: MediaRef) {
        UserDefaults.standard.set(mode.rawValue, forKey: key(for: ref))
    }

    static func removeMode(for ref: MediaRef) {
        UserDefaults.standard.removeObject(forKey: key(for: ref))
    }

    /// The mode a game's progress sheet last saved in. Kept apart from `mode`, which captions read: a game saved by
    /// percent still tells its hours in activity.
    static func gameMode(for ref: MediaRef) -> GameProgressMode? {
        UserDefaults.standard.string(forKey: gameKey(for: ref)).flatMap(GameProgressMode.init(rawValue:))
    }

    static func setGameMode(_ mode: GameProgressMode?, for ref: MediaRef) {
        UserDefaults.standard.set(mode?.rawValue, forKey: gameKey(for: ref))
    }

    private static func key(for ref: MediaRef) -> String {
        "\(prefix)\(ref.id)"
    }

    private static func gameKey(for ref: MediaRef) -> String {
        "progress.game.mode.\(ref.id)"
    }
}

struct TrackingWriteRequest: Encodable {
    let status: String?
    let rating: Decimal?
    let progress: Int?
    let notes: String?
    let startDate: String?
    let endDate: String?
    let mutationId: UUID?
    let includesRating: Bool

    init(
        status: String? = nil,
        rating: Decimal? = nil,
        progress: Int? = nil,
        notes: String? = nil,
        startDate: String? = nil,
        endDate: String? = nil,
        mutationId: UUID? = nil,
        includesRating: Bool = false
    ) {
        self.status = status
        self.rating = rating
        self.progress = progress
        self.notes = notes
        self.startDate = startDate
        self.endDate = endDate
        self.mutationId = mutationId
        self.includesRating = includesRating || rating != nil
    }

    enum CodingKeys: String, CodingKey {
        case status
        case rating
        case progress
        case notes
        case startDate
        case endDate
        case mutationId
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(status, forKey: .status)
        if includesRating {
            try container.encode(rating, forKey: .rating)
        }
        try container.encodeIfPresent(progress, forKey: .progress)
        try container.encodeIfPresent(notes, forKey: .notes)
        try container.encodeIfPresent(startDate, forKey: .startDate)
        try container.encodeIfPresent(endDate, forKey: .endDate)
        try container.encodeIfPresent(mutationId, forKey: .mutationId)
    }
}

struct TrackingConsumeRequest: Encodable {
    let consumedAt: Date?
}

struct EpisodeWatchRequest: Encodable {
    let watchedAt: Date?
}

struct BookProgressRequest: Encodable {
    let progressType: String
    let value: Decimal
    let notes: String
    /// The day of the save; sent only when the sheet named one, so older builds' requests are unchanged.
    var progressedOn: String? = nil
}

struct BookCompleteRequest: Encodable {
    let completedAt: Date?
}

struct BookActionRequest: Encodable, Equatable {
    let mutationId: UUID?
    let startDate: String?
    let endDate: String?

    init(mutationId: UUID? = UUID(), startDate: String? = nil, endDate: String? = nil) {
        self.mutationId = mutationId
        self.startDate = startDate
        self.endDate = endDate
    }
}

struct SeriesProgressRequest: Encodable, Equatable {
    let value: Int
    /// The same on a retry, so a lost answer at the total still opens the log screen (MC-100, MC-101).
    let mutationId: UUID
    var progressedOn: String? = nil
}

/// A manga's or a comic's log: the book's, with its own visibility (AC-5) and the optional number a log with no total
/// has (MC-102). A nil `isRewatch` lets the server work it out from the reads before it.
struct SeriesCompletionWriteRequest: Encodable, Equatable {
    let journeyId: Int?
    let completionDate: String
    let rating: Decimal?
    let review: String
    let reviewTitle: String
    let liked: Bool
    let isRewatch: Bool?
    let containsSpoilers: Bool
    let tags: [String]
    let visibility: String
    let place: Int?
    let mutationId: UUID
}

struct BookJourneyWriteRequest: Encodable, Equatable {
    let startDate: String?
    let endDate: String?
}

struct BookCompletionWriteRequest: Encodable, Equatable {
    let journeyId: Int?
    let completionDate: String
    let rating: Decimal?
    let review: String
    let reviewTitle: String
    let liked: Bool
    let isRewatch: Bool
    let containsSpoilers: Bool
    let tags: [String]
    let mutationId: UUID
}

struct BookCompletionResponse: Decodable {
    let tracking: TrackingState
    let diaryEntry: DiaryEntry
}

extension Notification.Name {
    static let mediaStateDidChange = Notification.Name("mediaStateDidChange")
}

enum MediaStateChange {
    /// `origin` lets the screen that made the change recognize, and skip, its own post.
    static func post(ref: MediaRef, origin: UUID? = nil) {
        var userInfo: [String: Any] = ["ref": ref]
        if let origin { userInfo["origin"] = origin }
        NotificationCenter.default.post(name: .mediaStateDidChange, object: nil, userInfo: userInfo)
    }
}

struct LibraryItem: Decodable, Identifiable {
    let media: MediaSummary
    let tracking: TrackingState

    var id: String { media.id }
}
