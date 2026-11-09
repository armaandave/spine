import Foundation

/// The titles that fill the Trending tile when few people logged anything: the first 10 of the week's TMDB trending
/// movies, from the featured lists.
///
/// Kept in memory for an hour, and callers that arrive while the list is loading share its one request. Any failure
/// answers an empty list and is not kept, so the next call asks again.
@MainActor
final class TrendingFallbackSource {
    static let slug = "tmdb-trending-movies-week"
    static let limit = 10
    static let lifetime: TimeInterval = 3600

    private let listRepository: ListRepository
    private let now: () -> Date
    private var cached: (items: [MediaSummary], at: Date)?
    private var loading: Task<[MediaSummary], Never>?

    init(listRepository: ListRepository, now: @escaping () -> Date = { Date() }) {
        self.listRepository = listRepository
        self.now = now
    }

    func load() async -> [MediaSummary] {
        if let cached, now().timeIntervalSince(cached.at) < Self.lifetime { return cached.items }
        if let loading { return await loading.value }
        let request = Task { await fetch() }
        loading = request
        let items = await request.value
        loading = nil
        if !items.isEmpty { cached = (items, now()) }
        return items
    }

    private func fetch() async -> [MediaSummary] {
        guard let response = try? await listRepository.featuredShelves(mediaType: "movie"),
              let card = Self.card(in: response) else { return [] }
        if let page = try? await listRepository.items(listId: card.id, page: nil, filter: MediaFilterState()) {
            return Array(page.results.prefix(Self.limit))
        }
        return Array(card.previewItems.prefix(Self.limit))
    }

    /// The week's trending movies, else the first list with "trending" in its address.
    static func card(in response: FeaturedShelvesResponse) -> FeaturedListCard? {
        let cards = response.shelves.flatMap(\.lists)
        return cards.first { $0.slug == slug } ?? cards.first { $0.slug.contains("trending") }
    }
}
