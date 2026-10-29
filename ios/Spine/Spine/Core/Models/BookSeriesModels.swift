import Foundation

struct SeriesRef: Codable, Hashable, Identifiable {
    let source: String
    let id: String
    let mediaType: String

    init(source: String, id: String, mediaType: String = "book") {
        self.source = source
        self.id = id
        self.mediaType = mediaType
    }
}

struct MediaSeriesSummary: Decodable, Hashable, Identifiable {
    let id: String
    let source: String
    let mediaType: String
    let name: String
    let itemCount: Int
    let posterUrls: [String]
    let completion: CompletionProgress?
    /// Stats only: members first seen in a dated range (STAT-214); nil everywhere else.
    let seenInRange: Int?

    var ref: SeriesRef {
        SeriesRef(source: source, id: id, mediaType: mediaType)
    }

    var bookCount: Int { itemCount }

    private enum CodingKeys: String, CodingKey {
        case id
        case source
        case mediaType
        case name
        case itemCount
        case bookCount
        case posterUrls
        case completion
        case seenInRange
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        source = try container.decode(String.self, forKey: .source)
        mediaType = try container.decodeIfPresent(String.self, forKey: .mediaType) ?? "book"
        name = try container.decode(String.self, forKey: .name)
        itemCount = try container.decodeIfPresent(Int.self, forKey: .itemCount)
            ?? container.decodeIfPresent(Int.self, forKey: .bookCount)
            ?? 0
        posterUrls = try container.decodeIfPresent([String].self, forKey: .posterUrls) ?? []
        completion = try container.decodeIfPresent(CompletionProgress.self, forKey: .completion)
        seenInRange = (try? container.decodeIfPresent(Int.self, forKey: .seenInRange)) ?? nil
    }
}

typealias BookSeriesSummary = MediaSeriesSummary

struct SeriesDetail: Decodable, Hashable, Identifiable {
    let seriesId: String
    let source: String
    let mediaType: String
    let name: String
    let itemCount: Int
    let items: [MediaSummary]
    let completion: CompletionProgress?

    var id: String { seriesId }

    var ref: SeriesRef {
        SeriesRef(source: source, id: seriesId, mediaType: mediaType)
    }

    var bookCount: Int { itemCount }
    var books: [MediaSummary] { items }

    private enum CodingKeys: String, CodingKey {
        case seriesId
        case id
        case source
        case mediaType
        case name
        case itemCount
        case items
        case bookCount
        case books
        case completion
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let decodedItems = try container.decodeIfPresent([MediaSummary].self, forKey: .items)
            ?? container.decodeIfPresent([MediaSummary].self, forKey: .books)
            ?? []

        seriesId = try container.decodeIfPresent(String.self, forKey: .seriesId)
            ?? container.decode(String.self, forKey: .id)
        source = try container.decode(String.self, forKey: .source)
        mediaType = try container.decodeIfPresent(String.self, forKey: .mediaType)
            ?? decodedItems.first?.ref.mediaType
            ?? "book"
        name = try container.decode(String.self, forKey: .name)
        itemCount = try container.decodeIfPresent(Int.self, forKey: .itemCount)
            ?? container.decodeIfPresent(Int.self, forKey: .bookCount)
            ?? decodedItems.count
        items = decodedItems
        completion = try container.decodeIfPresent(CompletionProgress.self, forKey: .completion)
    }
}
