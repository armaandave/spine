import Foundation

struct EmptyResponse: Codable, Equatable {}

struct CompletionProgress: Codable, Equatable, Hashable {
    let completedCount: Int
    let totalCount: Int

    var normalizedCompletedCount: Int {
        min(max(completedCount, 0), max(totalCount, 0))
    }

    var isVisible: Bool {
        totalCount > 0
    }

    var percentage: Int {
        guard totalCount > 0 else { return 0 }
        return Int((Double(normalizedCompletedCount) * 100 / Double(totalCount)).rounded())
    }

    var percentageText: String {
        "\(percentage)%"
    }

    var countText: String {
        "\(normalizedCompletedCount) of \(max(totalCount, 0))"
    }
}

struct PagedResponse<T: Decodable>: Decodable {
    let count: Int
    let next: String?
    let previous: String?
    let results: [T]
    let completion: CompletionProgress?

    init(
        count: Int,
        next: String?,
        previous: String?,
        results: [T],
        completion: CompletionProgress? = nil
    ) {
        self.count = count
        self.next = next
        self.previous = previous
        self.results = results
        self.completion = completion
    }
}

struct MediaSearchResponse: Decodable, Equatable {
    let results: [MediaSummary]
    let unavailableMediaTypes: [String]

    init(results: [MediaSummary], unavailableMediaTypes: [String] = []) {
        self.results = results
        self.unavailableMediaTypes = unavailableMediaTypes
    }

    private enum CodingKeys: String, CodingKey {
        case results
        case unavailableMediaTypes
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        results = try container.decode([MediaSummary].self, forKey: .results)
        unavailableMediaTypes = try container.decodeIfPresent([String].self, forKey: .unavailableMediaTypes) ?? []
    }
}

struct MetaResponse: Decodable {
    let version: String
    let mediaTypes: [String]
    let enabledMediaTypes: [String]?
    let sources: [String: [String]]
    let statusChoices: [String]
    let sourceChoices: [String]
    let dateFormats: [PreferenceChoice]?
    let timeFormats: [PreferenceChoice]?
    let weekStartDays: [PreferenceChoice]?
    let quickWatchDates: [PreferenceChoice]?

    var settingsOptions: SettingsOptions? {
        guard let dateFormats, let timeFormats, let weekStartDays, let quickWatchDates else {
            return nil
        }
        return SettingsOptions(
            dateFormats: dateFormats,
            timeFormats: timeFormats,
            weekStartDays: weekStartDays,
            quickWatchDates: quickWatchDates
        )
    }
}

enum APIConstants {
    static let allMedia = "all"
    // ponytail: boardgame removed from fallback list; add back if board games return
    static let fallbackMediaTypes = ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"]
    static let statusChoices = ["Completed", "In progress", "Planning", "Paused", "Dropped"]
    static let visibilityChoices = ["public", "followers", "private"]
}
