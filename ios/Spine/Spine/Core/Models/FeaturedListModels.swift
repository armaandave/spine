import Foundation

// Featured lists on Search: `GET /lists/featured/shelves/` (docs/featured-lists/README.md, "API").
//
// Decoding is deliberately tolerant. Every field the contract added is optional, unknown enum values become
// `.unknown`, and a card, shelf or preview item that can't be decoded is dropped instead of failing the whole
// response: a strict failure would blank the Search landing.

enum FeaturedKind: String, CaseIterable, Codable, Hashable {
    case chart
    case award
    case canon
    case community
    case curator
    case editorial
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap(FeaturedKind.init(rawValue:)) ?? .unknown
    }

    /// The see-all section title, as the server names its kind shelves.
    var sectionTitle: String {
        switch self {
        case .chart: "Charts"
        case .award: "Awards"
        case .canon: "Critics & Canon"
        case .community: "Community"
        case .curator: "Curators' Picks"
        case .editorial: "Editorial"
        case .unknown: "Featured"
        }
    }

    var badgeLabel: String {
        self == .unknown ? "FEATURED" : rawValue.uppercased()
    }
}

enum FeaturedCadence: String, Codable, Hashable {
    case live
    case daily
    case weekly
    case monthly
    case yearly
    case `static`
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap(FeaturedCadence.init(rawValue:)) ?? .unknown
    }

    /// "Weekly", or nil for lists that don't refresh on a schedule.
    var label: String? {
        switch self {
        case .live: "Live"
        case .daily: "Daily"
        case .weekly: "Weekly"
        case .monthly: "Monthly"
        case .yearly: "Yearly"
        case .static, .unknown: nil
        }
    }

    /// "Refreshes weekly", for the list detail's metadata line.
    var refreshLabel: String? {
        switch self {
        case .live: "Updates live"
        case .daily, .weekly, .monthly, .yearly: label.map { "Refreshes \($0.lowercased())" }
        case .static, .unknown: nil
        }
    }
}

enum FeaturedLiveStatus: String, Codable, Hashable {
    case upcoming
    case live
    case done
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap(FeaturedLiveStatus.init(rawValue:)) ?? .unknown
    }
}

struct FeaturedLiveState: Codable, Hashable {
    let status: FeaturedLiveStatus
    /// "7 of 23 announced"
    let label: String
    let startsAt: Date?
    let endsAt: Date?

    init(status: FeaturedLiveStatus, label: String = "", startsAt: Date? = nil, endsAt: Date? = nil) {
        self.status = status
        self.label = label
        self.startsAt = startsAt
        self.endsAt = endsAt
    }

    private enum CodingKeys: String, CodingKey {
        case status
        case label
        case startsAt
        case endsAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = container.lenient(FeaturedLiveStatus.self, forKey: .status) ?? .unknown
        label = container.lenient(String.self, forKey: .label) ?? ""
        startsAt = container.featuredDate(forKey: .startsAt)
        endsAt = container.featuredDate(forKey: .endsAt)
    }

    /// Live now: the server says so, or an upcoming window has started and not yet ended.
    func isLive(at now: Date) -> Bool {
        switch status {
        case .live:
            return true
        case .upcoming:
            guard let startsAt, startsAt <= now else { return false }
            return endsAt.map { now < $0 } ?? true
        case .done, .unknown:
            return false
        }
    }
}

struct FeaturedSource: Codable, Hashable {
    let name: String
    let url: String?

    init(name: String, url: String? = nil) {
        self.name = name
        self.url = url
    }

    private enum CodingKeys: String, CodingKey {
        case name
        case url
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = container.lenient(String.self, forKey: .name) ?? ""
        url = container.lenient(String.self, forKey: .url)
    }

    /// The source page, when it's a web link.
    var linkURL: URL? {
        guard let url = url?.trimmingCharacters(in: .whitespacesAndNewlines),
              let parsed = URL(string: url),
              let scheme = parsed.scheme?.lowercased(),
              ["http", "https"].contains(scheme) else { return nil }
        return parsed
    }
}

/// One preview poster: the `MediaSummary` list previews already use, plus the entry's chart note ("▲2") when the
/// server sends one.
struct FeaturedPreview: Codable, Hashable, Identifiable {
    let media: MediaSummary
    let note: String?

    var id: String { media.id }

    init(media: MediaSummary, note: String? = nil) {
        self.media = media
        self.note = note
    }

    private enum CodingKeys: String, CodingKey {
        case media
        case note
    }

    /// The API sends a flat `MediaSummary` object; the on-disk cache stores `{"media": …, "note": …}`.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if container.contains(.media) {
            media = try container.decode(MediaSummary.self, forKey: .media)
        } else {
            media = try MediaSummary(from: decoder)
        }
        note = container.lenient(String.self, forKey: .note)
    }
}

struct FeaturedListCard: Codable, Hashable, Identifiable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let isRanked: Bool
    let itemsCount: Int
    let mediaTypes: [String]
    let kind: FeaturedKind
    let source: FeaturedSource?
    let cadence: FeaturedCadence
    /// "Week of Oct 13", "2026"
    let periodLabel: String?
    let contentChangedAt: Date?
    let lastSyncedAt: Date?
    let live: FeaturedLiveState?
    let previews: [FeaturedPreview]

    var previewItems: [MediaSummary] { previews.map(\.media) }

    init(
        id: Int,
        name: String,
        slug: String = "",
        description: String = "",
        isRanked: Bool = false,
        itemsCount: Int,
        mediaTypes: [String],
        kind: FeaturedKind = .unknown,
        source: FeaturedSource? = nil,
        cadence: FeaturedCadence = .unknown,
        periodLabel: String? = nil,
        contentChangedAt: Date? = nil,
        lastSyncedAt: Date? = nil,
        live: FeaturedLiveState? = nil,
        previews: [FeaturedPreview] = []
    ) {
        self.id = id
        self.name = name
        self.slug = slug
        self.description = description
        self.isRanked = isRanked
        self.itemsCount = itemsCount
        self.mediaTypes = mediaTypes
        self.kind = kind
        self.source = source
        self.cadence = cadence
        self.periodLabel = periodLabel
        self.contentChangedAt = contentChangedAt
        self.lastSyncedAt = lastSyncedAt
        self.live = live
        self.previews = previews
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case name
        case slug
        case description
        case isRanked
        case itemsCount
        case mediaTypes
        case kind
        case source
        case cadence
        case periodLabel
        case contentChangedAt
        case lastSyncedAt
        case live
        case previews
        case previewItems
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        name = container.lenient(String.self, forKey: .name) ?? ""
        slug = container.lenient(String.self, forKey: .slug) ?? ""
        description = container.lenient(String.self, forKey: .description) ?? ""
        isRanked = container.lenient(Bool.self, forKey: .isRanked) ?? false
        let previews = container.lenient(FeaturedLossyArray<FeaturedPreview>.self, forKey: .previewItems)?.elements
            ?? container.lenient(FeaturedLossyArray<FeaturedPreview>.self, forKey: .previews)?.elements
            ?? []
        self.previews = previews
        itemsCount = container.lenient(Int.self, forKey: .itemsCount) ?? previews.count
        let mediaTypes = container.lenient(FeaturedLossyArray<String>.self, forKey: .mediaTypes)?.elements ?? []
        self.mediaTypes = mediaTypes.isEmpty
            ? FeaturedMediaTypes.derived(from: previews.map(\.media))
            : mediaTypes
        kind = container.lenient(FeaturedKind.self, forKey: .kind) ?? .unknown
        let source = container.lenient(FeaturedSource.self, forKey: .source)
        self.source = (source?.name.isEmpty == false || source?.linkURL != nil) ? source : nil
        cadence = container.lenient(FeaturedCadence.self, forKey: .cadence) ?? .unknown
        periodLabel = container.lenient(String.self, forKey: .periodLabel)?.nilIfBlank
        contentChangedAt = container.featuredDate(forKey: .contentChangedAt)
        lastSyncedAt = container.featuredDate(forKey: .lastSyncedAt)
        live = container.lenient(FeaturedLiveState.self, forKey: .live)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(name, forKey: .name)
        try container.encode(slug, forKey: .slug)
        try container.encode(description, forKey: .description)
        try container.encode(isRanked, forKey: .isRanked)
        try container.encode(itemsCount, forKey: .itemsCount)
        try container.encode(mediaTypes, forKey: .mediaTypes)
        try container.encode(kind, forKey: .kind)
        try container.encodeIfPresent(source, forKey: .source)
        try container.encode(cadence, forKey: .cadence)
        try container.encodeIfPresent(periodLabel, forKey: .periodLabel)
        try container.encodeIfPresent(contentChangedAt, forKey: .contentChangedAt)
        try container.encodeIfPresent(lastSyncedAt, forKey: .lastSyncedAt)
        try container.encodeIfPresent(live, forKey: .live)
        try container.encode(previews, forKey: .previews)
    }
}

struct FeaturedShelf: Codable, Hashable, Identifiable {
    static let nowKey = "now"
    static let mixedMediaType = "mixed"

    let key: String
    let title: String
    /// A media type slug, `"mixed"` for "Across Media", or nil for "Now".
    let mediaType: String?
    let count: Int
    let liveCount: Int
    let kindCounts: [String: Int]
    let lists: [FeaturedListCard]
    /// The server's see-all path; nil when the shelf has none ("Now", and every legacy shelf).
    let seeAll: String?

    var id: String { key }
    var isNow: Bool { key == Self.nowKey }
    var isMixed: Bool { mediaType == Self.mixedMediaType || key == Self.mixedMediaType }
    /// The kind of a kind shelf in a media type's grouped response (`?media_type=`), from its key.
    var kind: FeaturedKind { FeaturedKind(rawValue: key) ?? .unknown }

    init(
        key: String,
        title: String,
        mediaType: String?,
        count: Int? = nil,
        liveCount: Int = 0,
        kindCounts: [String: Int] = [:],
        lists: [FeaturedListCard],
        seeAll: String? = nil
    ) {
        self.key = key
        self.title = title
        self.mediaType = mediaType
        self.count = count ?? lists.count
        self.liveCount = liveCount
        self.kindCounts = kindCounts
        self.lists = lists
        self.seeAll = seeAll
    }

    private enum CodingKeys: String, CodingKey {
        case key
        case title
        case mediaType
        case count
        case liveCount
        case kindCounts
        case lists
        case seeAll
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let mediaType = container.lenient(String.self, forKey: .mediaType)?.nilIfBlank
        let title = container.lenient(String.self, forKey: .title)?.nilIfBlank
        guard let key = container.lenient(String.self, forKey: .key)?.nilIfBlank ?? mediaType ?? title else {
            throw DecodingError.dataCorruptedError(forKey: .key, in: container, debugDescription: "A shelf needs a key")
        }
        self.key = key
        self.mediaType = mediaType
        self.title = title ?? MediaTypeTheme.theme(for: mediaType ?? key).displayName
        lists = container.lenient(FeaturedLossyArray<FeaturedListCard>.self, forKey: .lists)?.elements ?? []
        count = container.lenient(Int.self, forKey: .count) ?? lists.count
        liveCount = container.lenient(Int.self, forKey: .liveCount) ?? 0
        kindCounts = container.lenient([String: Int].self, forKey: .kindCounts) ?? [:]
        seeAll = container.lenient(String.self, forKey: .seeAll)?.nilIfBlank
    }
}

struct FeaturedShelvesResponse: Codable, Hashable {
    /// The order the server sends media-type shelves in, which the legacy fallback copies.
    static let mediaTypeOrder = ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"]

    let generatedAt: Date?
    /// Set on a media type's grouped response (`?media_type=movie`).
    let mediaType: String?
    let shelves: [FeaturedShelf]
    /// Built from the legacy `/lists/featured/` endpoint because the server has no shelves endpoint yet.
    let isLegacy: Bool

    init(generatedAt: Date? = nil, mediaType: String? = nil, shelves: [FeaturedShelf], isLegacy: Bool = false) {
        self.generatedAt = generatedAt
        self.mediaType = mediaType
        self.shelves = shelves
        self.isLegacy = isLegacy
    }

    private enum CodingKeys: String, CodingKey {
        case generatedAt
        case mediaType
        case shelves
        case isLegacy
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = container.featuredDate(forKey: .generatedAt)
        mediaType = container.lenient(String.self, forKey: .mediaType)?.nilIfBlank
        shelves = container.lenient(FeaturedLossyArray<FeaturedShelf>.self, forKey: .shelves)?.elements ?? []
        isLegacy = container.lenient(Bool.self, forKey: .isLegacy) ?? false
    }

    var allCards: [FeaturedListCard] {
        shelves.flatMap(\.lists)
    }
}

// MARK: - Legacy fallback

/// Media types for shelves: seasons and episodes are TV.
enum FeaturedMediaTypes {
    static func normalized(_ mediaType: String) -> String {
        ["season", "episode"].contains(mediaType) ? "tv" : mediaType
    }

    /// The distinct media types of `items`, in order of appearance.
    static func derived(from items: [MediaSummary]) -> [String] {
        var seen = Set<String>()
        return items
            .map { normalized($0.ref.mediaType) }
            .filter { seen.insert($0).inserted }
    }

    /// The shelves a list belongs on: one or two types go on their own shelves, more go on "Across Media".
    static func shelfMediaTypes(for mediaTypes: [String]) -> [String] {
        (1 ... 2).contains(mediaTypes.count) ? mediaTypes : [FeaturedShelf.mixedMediaType]
    }
}

extension FeaturedListCard {
    /// A card for a list from the legacy `/lists/featured/` endpoint, which has no featured metadata.
    init(legacy list: CustomListSummary) {
        let previews = (list.previewItems ?? []).map { FeaturedPreview(media: $0) }
        self.init(
            id: list.id,
            name: list.name,
            slug: list.slug,
            description: list.description,
            isRanked: list.isRanked,
            itemsCount: list.itemsCount,
            mediaTypes: FeaturedMediaTypes.derived(from: previews.map(\.media)),
            previews: previews
        )
    }
}

extension FeaturedShelvesResponse {
    /// Groups legacy featured lists into one shelf per media type, by their preview items' media types.
    static func legacy(_ lists: [CustomListSummary]) -> FeaturedShelvesResponse {
        let cards = lists.filter { $0.listType == .media }.map { FeaturedListCard(legacy: $0) }
        let shelfKeys = mediaTypeOrder + [FeaturedShelf.mixedMediaType]
        let shelves = shelfKeys.compactMap { key -> FeaturedShelf? in
            let shelfCards = cards.filter { FeaturedMediaTypes.shelfMediaTypes(for: $0.mediaTypes).contains(key) }
            guard !shelfCards.isEmpty else { return nil }
            let title = key == FeaturedShelf.mixedMediaType ? "Across Media" : MediaTypeTheme.theme(for: key).displayName
            return FeaturedShelf(key: key, title: title, mediaType: key, lists: shelfCards)
        }
        return FeaturedShelvesResponse(shelves: shelves, isLegacy: true)
    }

    /// One media type's layer from legacy featured lists: a single unkinded shelf.
    static func legacy(_ lists: [CustomListSummary], mediaType: String) -> FeaturedShelvesResponse {
        let shelf = legacy(lists).shelves.first { $0.key == mediaType }
        let shelves = shelf.map {
            [FeaturedShelf(key: FeaturedKind.unknown.rawValue, title: "Featured Lists", mediaType: mediaType, lists: $0.lists)]
        } ?? []
        return FeaturedShelvesResponse(mediaType: mediaType, shelves: shelves, isLegacy: true)
    }
}

// MARK: - Decoding helpers

/// An array that drops the elements it can't decode instead of failing.
struct FeaturedLossyArray<Element: Decodable>: Decodable {
    let elements: [Element]

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var elements: [Element] = []
        while !container.isAtEnd {
            if let element = try? container.decode(Element.self) {
                elements.append(element)
            } else {
                // A failed decode doesn't advance the container; skip the element.
                _ = try? container.decode(FeaturedSkippedValue.self)
            }
        }
        self.elements = elements
    }
}

private struct FeaturedSkippedValue: Decodable {
    init(from decoder: Decoder) throws {}
}

/// Parses the API's ISO 8601 timestamps. Django sends microseconds, which `ISO8601DateFormatter` won't read,
/// so fractional seconds are dropped first.
enum FeaturedDate {
    nonisolated(unsafe) private static let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    static func parse(_ raw: String?) -> Date? {
        guard var value = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        if let dot = value.firstIndex(of: ".") {
            let fractionEnd = value[value.index(after: dot)...].firstIndex { !$0.isNumber } ?? value.endIndex
            value.removeSubrange(dot ..< fractionEnd)
        }
        if let date = formatter.date(from: value) {
            return date
        }
        // A timestamp without a zone is UTC.
        return formatter.date(from: value + "Z")
    }
}

private extension KeyedDecodingContainer {
    /// Decodes an optional value, treating a value of the wrong shape as missing.
    func lenient<T: Decodable>(_ type: T.Type, forKey key: Key) -> T? {
        (try? decodeIfPresent(type, forKey: key)) ?? nil
    }

    func featuredDate(forKey key: Key) -> Date? {
        FeaturedDate.parse(lenient(String.self, forKey: key))
    }
}

private extension String {
    var nilIfBlank: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
