import Foundation

struct GameProgressValues: Codable, Hashable {
    let totalMinutes: Int?
    let percentage: Int?

    var summary: String {
        [percentage.map { "\($0)%" }, totalMinutes.map { Self.timeLabel($0) }].compactMap { $0 }.joined(separator: " · ")
    }

    static func timeLabel(_ minutes: Int) -> String {
        minutes % 60 == 0 ? "\(minutes / 60)h" : "\(minutes / 60)h \(minutes % 60)m"
    }
}

struct GamePlaythroughState: Codable, Hashable, Identifiable {
    let id: Int
    let status: String
    let origin: String?
    let startDate: String?
    let endDate: String?
    let totalMinutes: Int?
    let percentage: Int?
    let completionDiaryEntryId: Int?
    let isReplay: Bool

    var progress: GameProgressValues { GameProgressValues(totalMinutes: totalMinutes, percentage: percentage) }
    var isUnfinished: Bool { ["in progress", "paused"].contains(status.lowercased()) }
}

struct GameTrackingState: Codable, Hashable {
    let currentPlaythrough: GamePlaythroughState?
    let playHistory: [GamePlaythroughState]
    let undatedCompletion: BookUndatedReadState?
    let statusSource: String?
    let completionDates: [String]
    let completedPlaythroughCount: Int
    let lifetimeCompletionCount: Int
    let isReplaying: Bool
    let canRemoveTracking: Bool
    let availableActions: [String]
    let actionReasons: [String: String]
    let importedLifetimeMinutes: Int?
    let importedLifetimeSource: String?

    var hasLivePlaythrough: Bool { currentPlaythrough?.isUnfinished == true }
    var hasPlayingPlaythrough: Bool { currentPlaythrough?.status == "In progress" }
    var canUpdateProgress: Bool {
        currentPlaythrough.map { $0.isUnfinished || $0.status == "Completed" } ?? false
    }
    func supports(_ action: String) -> Bool { availableActions.contains(action) }
}

struct GameProgressDraft: Equatable {
    private var minutesEdited = false
    private var percentageEdited = false
    var hours: String { didSet { minutesEdited = true } }
    var minutes: String { didSet { minutesEdited = true } }
    var percentage: String { didSet { percentageEdited = true } }

    init(hours: String = "", minutes: String = "", percentage: String = "") {
        self.hours = hours
        self.minutes = minutes
        self.percentage = percentage
    }

    init(totalMinutes: Int?, percentage: Int?) {
        hours = totalMinutes.map { String($0 / 60) } ?? ""
        minutes = totalMinutes.map { String($0 % 60) } ?? ""
        self.percentage = percentage.map(String.init) ?? ""
    }

    func values() throws -> GameProgressValues {
        func number(_ text: String, name: String, maximum: Int) throws -> Int? {
            let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { return nil }
            guard text.allSatisfy({ $0.isASCII && $0.isNumber }), let value = Int(text), (0...maximum).contains(value) else {
                throw GameProgressError.invalid("Enter \(name) as a whole number from 0 to \(maximum).")
            }
            return value
        }
        let maximumMinutes = Int(Int32.max)
        let h = try number(hours, name: "hours", maximum: maximumMinutes / 60)
        let m = try number(minutes, name: "minutes", maximum: 59)
        let p = try number(percentage, name: "percentage", maximum: 100)
        let total = h == nil && m == nil ? nil : (h ?? 0) * 60 + (m ?? 0)
        if let total, total > maximumMinutes {
            throw GameProgressError.invalid("Total playthrough time is too large.")
        }
        return GameProgressValues(totalMinutes: total, percentage: p)
    }

    func request(comparedTo original: GameProgressDraft, startDate: String? = nil, endDate: String? = nil) throws -> GamePlaythroughWriteRequest {
        let values = try values()
        let includesMinutes = minutesEdited || hours != original.hours || minutes != original.minutes
        let includesPercentage = percentageEdited || percentage != original.percentage
        return GamePlaythroughWriteRequest(startDate: startDate, endDate: endDate,
            totalMinutes: values.totalMinutes, percentage: values.percentage,
            includesMinutes: includesMinutes, includesPercentage: includesPercentage,
            progressedOn: includesMinutes || includesPercentage ? CalendarDateCodec.string(from: Date()) : nil)
    }
}

enum GameProgressError: LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case let .invalid(message) = self { message } else { nil } }
}

struct GamePlaythroughWriteRequest: Encodable {
    var startDate: String? = nil
    var endDate: String? = nil
    var totalMinutes: Int? = nil
    var percentage: Int? = nil
    var includesMinutes = false
    var includesPercentage = false
    var includesStartDate = false
    var progressedOn: String? = nil

    enum CodingKeys: String, CodingKey { case startDate, endDate, totalMinutes, percentage, progressedOn }
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if includesStartDate { try container.encode(startDate, forKey: .startDate) }
        else { try container.encodeIfPresent(startDate, forKey: .startDate) }
        try container.encodeIfPresent(endDate, forKey: .endDate)
        try container.encodeIfPresent(progressedOn, forKey: .progressedOn)
        if includesMinutes { try container.encode(totalMinutes, forKey: .totalMinutes) }
        if includesPercentage { try container.encode(percentage, forKey: .percentage) }
    }
}

struct GameCompletionWriteRequest: Encodable {
    let playthroughId: Int?
    let completionDate: String
    let startDate: String?
    let totalMinutes: Int?
    let percentage: Int?
    let rating: Decimal?
    let review: String
    let reviewTitle: String
    let liked: Bool
    let isRewatch: Bool
    let containsSpoilers: Bool
    let tags: [String]
    let mutationId: UUID

    enum CodingKeys: String, CodingKey { case playthroughId, completionDate, startDate, totalMinutes, percentage, rating, review, reviewTitle, liked, isRewatch, containsSpoilers, tags, mutationId }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(playthroughId, forKey: .playthroughId)
        try c.encode(completionDate, forKey: .completionDate)
        try c.encodeIfPresent(startDate, forKey: .startDate)
        try c.encode(totalMinutes, forKey: .totalMinutes)
        try c.encode(percentage, forKey: .percentage)
        try c.encode(rating, forKey: .rating)
        try c.encode(review, forKey: .review)
        try c.encode(reviewTitle, forKey: .reviewTitle)
        try c.encode(liked, forKey: .liked)
        try c.encode(isRewatch, forKey: .isRewatch)
        try c.encode(containsSpoilers, forKey: .containsSpoilers)
        try c.encode(tags, forKey: .tags)
        try c.encode(mutationId, forKey: .mutationId)
    }
}
