import Foundation

// Where to watch: the user's streaming country and services, and a title's offers in that country.
// Contract: docs/streaming-availability-contract.md

extension Notification.Name {
    /// Posted after the user saves their streaming country or services. `object` is the saved `StreamingPreferences`.
    static let streamingPreferencesDidChange = Notification.Name("spine.streamingPreferencesDidChange")
}

/// A streaming service from TMDB's watch-provider catalog (Netflix is 8).
struct StreamingProvider: Decodable, Hashable, Identifiable {
    let providerId: Int
    let name: String
    let logoUrl: String?
    let displayPriority: Int?

    var id: Int { providerId }
}

/// A country from TMDB's watch-provider regions list.
struct StreamingRegion: Decodable, Hashable, Identifiable {
    let code: String
    let name: String

    var id: String { code }

    /// The server's name for a country unless it is missing, empty, or just the code (what the server sends when it
    /// can't name the country); then the device's name for the code, then the code itself.
    static func displayName(code: String, serverName: String?) -> String {
        if let serverName, !serverName.isEmpty, serverName.caseInsensitiveCompare(code) != .orderedSame {
            return serverName
        }
        return Locale.current.localizedString(forRegionCode: code) ?? code
    }
}

/// GET /streaming/regions/
struct StreamingRegionsResponse: Decodable {
    let results: [StreamingRegion]
}

/// GET /streaming/providers/?region=
struct StreamingProvidersResponse: Decodable {
    let region: String
    let results: [StreamingProvider]
}

struct StreamingAttribution: Decodable, Hashable {
    let name: String
    let url: String

    /// Every surface that shows availability credits JustWatch (ST-003).
    static let justWatch = StreamingAttribution(name: "JustWatch", url: "https://www.justwatch.com")

    var destination: URL? {
        guard let url = URL(string: url), ["http", "https"].contains(url.scheme?.lowercased()) else { return nil }
        return url
    }
}

/// GET and PUT /me/streaming/
struct StreamingPreferences: Decodable, Equatable {
    /// The effective country (ST-100); nil only when availability is turned off.
    let region: String?
    let regionName: String?
    /// False when `region` came from the device region header or the server default rather than a saved choice.
    let regionIsSet: Bool
    /// False only when the user turned availability off on the web.
    let enabled: Bool
    let providerIds: [Int]
    /// The selected services, in display-priority order.
    let providers: [StreamingProvider]
    let attribution: StreamingAttribution?

    /// "Netflix, Hulu +2" for the settings row; "Not set" without services, "Off" when availability is off.
    var servicesSummary: String {
        guard enabled else { return "Off" }
        let names = providers.map(\.name)
        let total = max(providerIds.count, names.count)
        guard total > 0 else { return "Not set" }
        guard !names.isEmpty else { return total == 1 ? "1 service" : "\(total) services" }
        let shown = names.prefix(2)
        let remaining = total - shown.count
        let list = shown.joined(separator: ", ")
        return remaining > 0 ? "\(list) +\(remaining)" : list
    }
}

/// PUT /me/streaming/ body. Both fields are required; the selection is replaced.
struct StreamingPreferencesUpdateRequest: Encodable, Equatable {
    let region: String
    let providerIds: [Int]
}

/// `where_to_watch` on movie, TV, and season detail (ST-200). Null for other items and when availability is off.
struct WhereToWatch: Decodable, Equatable {
    let region: String
    let regionName: String?
    /// TMDB's watch page for the title and country; null when TMDB has none.
    let link: String?
    let hasServices: Bool
    /// True when one of the user's services streams the title (Stream, Free, or Free with ads).
    let onMyServices: Bool
    /// Offer groups in Stream, Free, Free with ads order. Empty means not available to stream in the country.
    let groups: [WhereToWatchGroup]
    let attribution: StreamingAttribution?

    /// Only TMDB movies, shows, and seasons have availability (ST-002).
    static func isAvailable(for ref: MediaRef) -> Bool {
        ref.source == "tmdb" && ["movie", "tv", "season"].contains(ref.mediaType)
    }

    var regionDisplayName: String {
        StreamingRegion.displayName(code: region, serverName: regionName)
    }

    var linkURL: URL? {
        guard let link, let url = URL(string: link), ["http", "https"].contains(url.scheme?.lowercased()) else {
            return nil
        }
        return url
    }

    /// Groups that have at least one offer.
    var visibleGroups: [WhereToWatchGroup] {
        groups.filter { !$0.providers.isEmpty }
    }

    /// The user's services that stream the title, without duplicates, in group order.
    var myStreamingProviders: [WhereToWatchProvider] {
        var seen = Set<Int>()
        return visibleGroups
            .filter(\.isStreamable)
            .flatMap(\.providers)
            .filter { $0.isMine && seen.insert($0.providerId).inserted }
    }

    /// "Streaming on Netflix", or "Streaming on your services" when several of them do; nil unless one does.
    var myServicesHeadline: String? {
        guard onMyServices else { return nil }
        let mine = myStreamingProviders
        if mine.count == 1, let only = mine.first {
            return "Streaming on \(only.name)"
        }
        return "Streaming on your services"
    }
}

struct WhereToWatchGroup: Decodable, Equatable, Identifiable {
    /// Offer types that count as streaming. Rent and Buy never do.
    static let streamableTypes: Set<String> = ["flatrate", "free", "ads"]

    /// `flatrate`, `free`, or `ads`. The API doesn't send Rent or Buy.
    let type: String
    /// "Stream", "Free", or "Free with ads".
    let label: String
    let providers: [WhereToWatchProvider]

    var id: String { type }

    var isStreamable: Bool {
        Self.streamableTypes.contains(type)
    }

    /// The user's services first, then the rest, each keeping the API's display-priority order.
    var orderedProviders: [WhereToWatchProvider] {
        providers.filter(\.isMine) + providers.filter { !$0.isMine }
    }
}

struct WhereToWatchProvider: Decodable, Equatable, Identifiable {
    let providerId: Int
    let name: String
    let logoUrl: String?
    let displayPriority: Int?
    /// True when this is one of the user's services.
    let isMine: Bool

    var id: Int { providerId }

    init(providerId: Int, name: String, logoUrl: String?, displayPriority: Int?, isMine: Bool) {
        self.providerId = providerId
        self.name = name
        self.logoUrl = logoUrl
        self.displayPriority = displayPriority
        self.isMine = isMine
    }

    private enum CodingKeys: String, CodingKey {
        case providerId
        case name
        case logoUrl
        case displayPriority
        case isMine
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        providerId = try container.decode(Int.self, forKey: .providerId)
        name = try container.decode(String.self, forKey: .name)
        logoUrl = try container.decodeIfPresent(String.self, forKey: .logoUrl)
        displayPriority = try container.decodeIfPresent(Int.self, forKey: .displayPriority)
        isMine = try container.decodeIfPresent(Bool.self, forKey: .isMine) ?? false
    }

    func accessibilityLabel(in group: WhereToWatchGroup) -> String {
        isMine ? "\(name), \(group.label), on your services" : "\(name), \(group.label)"
    }
}
