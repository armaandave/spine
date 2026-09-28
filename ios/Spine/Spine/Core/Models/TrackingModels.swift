import Foundation

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
        game: GameTrackingState? = nil
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
            game: game
        )
    }

    func homeProgressText(preferredMode: ProgressUpdateMode?) -> String {
        if let game {
            let text = game.currentPlaythrough?.progress.summary ?? ""
            return text.isEmpty ? (status == "In progress" ? "Playing" : status ?? "Playing") : text
        }
        if let changeText = latestProgressChange?.compactDisplayText(preferredMode: preferredMode) {
            return changeText
        }
        if let progressText = progress?.compactDisplayText(preferredMode: preferredMode) {
            return progressText
        }
        return status == "In progress" ? "Started" : status ?? "In progress"
    }
}

struct BookJourneyState: Codable, Equatable, Hashable, Identifiable {
    let id: Int
    let status: String
    let origin: String?
    let startDate: String?
    let endDate: String?
    let progress: ProgressState?
    let completionDiaryEntryId: Int?
    let isReread: Bool

    init(
        id: Int,
        status: String,
        origin: String? = nil,
        startDate: String? = nil,
        endDate: String? = nil,
        progress: ProgressState? = nil,
        completionDiaryEntryId: Int? = nil,
        isReread: Bool = false
    ) {
        self.id = id
        self.status = status
        self.origin = origin
        self.startDate = startDate
        self.endDate = endDate
        self.progress = progress
        self.completionDiaryEntryId = completionDiaryEntryId
        self.isReread = isReread
    }
}

struct BookUndatedReadState: Codable, Equatable, Hashable {
    let id: String
    let status: String
    let date: String?

    init(id: String = "undated", status: String = "Completed", date: String? = nil) {
        self.id = id
        self.status = status
        self.date = date
    }
}

struct BookTrackingState: Codable, Equatable, Hashable {
    let currentJourney: BookJourneyState?
    let readingHistory: [BookJourneyState]
    let undatedRead: BookUndatedReadState?
    let statusSource: String?
    let completionDates: [String]
    let completedJourneyCount: Int
    let lifetimeReadCount: Int
    let isRereading: Bool
    let completionRequired: Bool
    let canRemoveTracking: Bool
    let removeTrackingReason: String?
    let availableActions: [String]
    let actionReasons: [String: String]

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
        actionReasons: [String: String] = [:]
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

    func compactDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        guard let parts = compactDisplayParts(preferredMode: preferredMode) else {
            return nil
        }
        return "\(parts.previous) → \(parts.current)"
    }

    func compactDeltaText(preferredMode: ProgressUpdateMode?) -> String? {
        let mode = preferredMode ?? current.mode
        guard
            let previousValue = previous.value(in: mode),
            let currentValue = current.value(in: mode)
        else {
            return nil
        }

        let delta = currentValue - previousValue
        guard delta != 0 else { return nil }

        let prefix = delta > 0 ? "+" : ""
        if mode == .percentage {
            return "\(prefix)\(delta)%"
        }
        let unit = abs(delta) == 1 ? current.unit : current.pluralizedUnit
        return "\(prefix)\(delta) \(unit)"
    }
}

struct ProgressState: Codable, Hashable {
    let kind: String
    let value: Decimal?
    let max: Decimal?
    let unit: String
}

extension ProgressState {
    var compactDisplayText: String? {
        compactDisplayText(preferredMode: nil)
    }

    var detailDisplayText: String? {
        detailDisplayText(preferredMode: nil)
    }

    func compactDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = value(in: .percentage) {
            guard value > 0 else { return nil }
            return "\(Self.display(Decimal(value)))%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            guard value > 0 else { return nil }
            let maxText = max.map { "/\(Self.display($0))" } ?? ""
            let unitText = value == 1 && max == nil ? "page" : "pages"
            return "\(value)\(maxText) \(unitText)"
        }
        guard let value else { return nil }
        guard value > 0 else { return nil }
        if isPercentage {
            return "\(Self.display(value))%"
        }
        let maxText = max.map { "/\(Self.display($0))" } ?? ""
        return "\(Self.display(value))\(maxText) \(pluralizedUnit(for: max ?? value))"
    }

    func detailDisplayText(preferredMode: ProgressUpdateMode?) -> String? {
        if preferredMode == .percentage, let value = value(in: .percentage) {
            guard value > 0 else { return nil }
            return "\(value)%"
        }
        if preferredMode == .pages, let value = value(in: .pages) {
            guard value > 0 else { return nil }
            if let max {
                return "\(value) of \(Self.display(max)) pages"
            }
            return "\(value) pages"
        }
        guard let value else { return nil }
        guard value > 0 else { return nil }
        if isPercentage {
            return "\(Self.display(value))%"
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

    func value(in requestedMode: ProgressUpdateMode) -> Int? {
        guard let value else { return nil }
        let intValue = Int(NSDecimalNumber(decimal: value).doubleValue.rounded())
        guard mode != requestedMode else { return intValue }
        guard let maxValue = max.map({ NSDecimalNumber(decimal: $0).doubleValue }), maxValue > 0 else {
            return nil
        }
        switch (mode, requestedMode) {
        case (.pages, .percentage):
            return Int((Double(intValue) / maxValue * 100).rounded())
        case (.percentage, .pages):
            return Int((Double(intValue) / 100 * maxValue).rounded())
        default:
            return intValue
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

    private static func display(_ value: Decimal) -> String {
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

    private static func key(for ref: MediaRef) -> String {
        "\(prefix)\(ref.id)"
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
    static func post(ref: MediaRef) {
        NotificationCenter.default.post(name: .mediaStateDidChange, object: nil, userInfo: ["ref": ref])
    }
}

struct LibraryItem: Decodable, Identifiable {
    let media: MediaSummary
    let tracking: TrackingState

    var id: String { media.id }
}
