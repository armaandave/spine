import Foundation

/// The Service filter on the Library, list detail, Diary, and Discover (ST-300, ST-302).
enum StreamingFilterService: Hashable {
    /// No availability filtering.
    case any
    /// Streamable on at least one of the user's services in their country.
    case mine
    /// Streamable on at least one of these services, by TMDB provider ID.
    case providers([Int])

    var isActive: Bool {
        switch self {
        case .any:
            false
        case .mine:
            true
        case let .providers(ids):
            !ids.isEmpty
        }
    }

    /// `service=mine`, or one repeated `service=<provider_id>` per chosen service.
    var queryItems: [URLQueryItem] {
        switch self {
        case .any:
            []
        case .mine:
            [URLQueryItem(name: "service", value: "mine")]
        case let .providers(ids):
            ids.map { URLQueryItem(name: "service", value: String($0)) }
        }
    }

    func includes(providerId: Int) -> Bool {
        guard case let .providers(ids) = self else { return false }
        return ids.contains(providerId)
    }

    /// Picking a service leaves Any or My services for that one service; unpicking the last one goes back to Any.
    func togglingProvider(_ providerId: Int) -> StreamingFilterService {
        guard case var .providers(ids) = self else { return .providers([providerId]) }
        if let index = ids.firstIndex(of: providerId) {
            ids.remove(at: index)
        } else {
            ids.append(providerId)
        }
        return ids.isEmpty ? .any : .providers(ids)
    }

    /// Keeps only the chosen services the user still has, going back to Any when none are left.
    /// Any and My services are unchanged.
    func pruned(toAvailable services: [StreamingFilterServiceOption]) -> StreamingFilterService {
        guard case let .providers(ids) = self else { return self }
        let available = Set(services.map(\.providerId))
        let kept = ids.filter(available.contains)
        return kept.isEmpty ? .any : .providers(kept)
    }
}

/// The `streaming` object in `/filter-options/`.
struct StreamingFilterOptions: Decodable, Equatable, Hashable {
    /// True when the scope can contain movie or TV items.
    let available: Bool
    let region: String?
    let regionName: String?
    /// The user's own services; empty when they have none.
    let services: [StreamingFilterServiceOption]

    init(
        available: Bool,
        region: String? = nil,
        regionName: String? = nil,
        services: [StreamingFilterServiceOption] = []
    ) {
        self.available = available
        self.region = region
        self.regionName = regionName
        self.services = services
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        available = try container.decodeIfPresent(Bool.self, forKey: .available) ?? false
        region = try container.decodeIfPresent(String.self, forKey: .region)
        regionName = try container.decodeIfPresent(String.self, forKey: .regionName)
        services = try container.decodeIfPresent([StreamingFilterServiceOption].self, forKey: .services) ?? []
    }

    private enum CodingKeys: String, CodingKey {
        case available
        case region
        case regionName
        case services
    }

    /// "Availability in United States · Powered by JustWatch" (ST-003).
    var attributionText: String {
        let country = region.map { StreamingRegion.displayName(code: $0, serverName: regionName) } ?? regionName
        guard let country, !country.isEmpty else {
            return "Powered by JustWatch"
        }
        return "Availability in \(country) · Powered by JustWatch"
    }
}

/// One of the user's services as a filter choice: `{ "value": "8", "label": "Netflix", "logo_url": "…" }`.
struct StreamingFilterServiceOption: Decodable, Hashable, Identifiable {
    let providerId: Int
    let label: String
    let logoURL: String?

    var id: Int { providerId }

    init(providerId: Int, label: String, logoURL: String? = nil) {
        self.providerId = providerId
        self.label = label
        self.logoURL = logoURL
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // `value` is the TMDB provider ID, sent as a string like every other filter choice.
        if let providerId = try? container.decode(Int.self, forKey: .value) {
            self.providerId = providerId
        } else {
            let value = try container.decode(String.self, forKey: .value)
            guard let providerId = Int(value) else {
                throw DecodingError.dataCorruptedError(
                    forKey: .value,
                    in: container,
                    debugDescription: "A service value must be a TMDB provider ID."
                )
            }
            self.providerId = providerId
        }
        label = try container.decode(String.self, forKey: .label)
        logoURL = try container.decodeIfPresent(String.self, forKey: .logoUrl)
    }

    private enum CodingKeys: String, CodingKey {
        case value
        case label
        case logoUrl
    }
}

/// The `streaming` block a paginated collection response adds when the request sent `service`.
struct StreamingFilterPageInfo: Decodable, Equatable, Hashable {
    let region: String?
    /// Movie and TV titles not yet checked for the country; the server is checking them now (ST-301).
    let pendingCount: Int

    init(region: String? = nil, pendingCount: Int = 0) {
        self.region = region
        self.pendingCount = pendingCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        region = try container.decodeIfPresent(String.self, forKey: .region)
        pendingCount = try container.decodeIfPresent(Int.self, forKey: .pendingCount) ?? 0
    }

    private enum CodingKeys: String, CodingKey {
        case region
        case pendingCount
    }
}

enum StreamingFilterRules {
    /// Only movies and TV (with their seasons and episodes) have availability (ST-002). nil means every media type.
    static func supportsService(mediaType: String?) -> Bool {
        guard let mediaType else { return true }
        return ["movie", "tv", "season", "episode"].contains(mediaType)
    }

    /// The Service section appears for movie and TV Library shelves, lists, and the Diary, once the server says
    /// the scope can hold movie or TV items. Person filmographies and company catalogs don't support it.
    /// `mediaType` is the draft's media type for list and Diary scopes.
    static func showsServiceSection(
        scope: MediaFilterScope,
        options: MediaFilterOptionsResponse,
        mediaType: String?
    ) -> Bool {
        guard options.streaming?.available == true else { return false }
        switch scope {
        case let .tracking(libraryMediaType):
            return supportsService(mediaType: libraryMediaType)
        case .diary, .list:
            return supportsService(mediaType: mediaType)
        case .person, .company:
            return false
        }
    }

    /// True when a 400 from the server says the user has to choose their services first.
    static func isChooseServicesError(_ error: Error) -> Bool {
        guard case APIError.httpStatus(400, let body) = error,
              let data = body?.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return false
        }
        // Discover answers `{"service": [...]}`; the API's error envelope nests the same fields.
        if json["service"] != nil {
            return true
        }
        let fields = (json["error"] as? [String: Any])?["fields"] as? [String: Any]
        return fields?["service"] != nil
    }
}

enum StreamingFilterCopy {
    static func pendingNote(pendingCount: Int, service: StreamingFilterService) -> String? {
        guard service.isActive, pendingCount > 0 else { return nil }
        let titles = pendingCount == 1 ? "1 title" : "\(pendingCount.formatted()) titles"
        return "Still checking availability for \(titles) — pull to refresh"
    }

    static func emptyTitle(service: StreamingFilterService, options: StreamingFilterOptions?) -> String {
        "Nothing streaming on \(servicesPhrase(service: service, options: options))"
    }

    static let emptyMessage = "Try other services, or set Service back to Any in Filters."

    /// "your services", "Netflix", "Netflix or Max", or "the services you chose".
    private static func servicesPhrase(service: StreamingFilterService, options: StreamingFilterOptions?) -> String {
        guard case let .providers(ids) = service else { return "your services" }
        let names = ids.compactMap { id in
            options?.services.first { $0.providerId == id }?.label
        }
        guard names.count == ids.count else { return "the services you chose" }
        switch names.count {
        case 1:
            return names[0]
        case 2:
            return "\(names[0]) or \(names[1])"
        default:
            return "the services you chose"
        }
    }
}
