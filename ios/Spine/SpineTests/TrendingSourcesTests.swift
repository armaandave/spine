import XCTest
@testable import Spine

/// The list that fills the Trending tile when few people logged anything.
@MainActor
final class TrendingSourcesTests: XCTestCase {
    // MARK: Fallback list

    private func card(
        _ id: Int, slug: String, previews: [MediaSummary] = []
    ) -> FeaturedListCard {
        FeaturedListCard(
            id: id, name: "List \(id)", slug: slug, itemsCount: previews.count, mediaTypes: ["movie"],
            previews: previews.map { FeaturedPreview(media: $0) }
        )
    }

    private func movie(_ id: Int) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "\(id)", seasonNumber: nil, episodeNumber: nil),
            title: "Movie \(id)", posterUrl: "https://p/\(id).jpg", posterOrientation: .portrait
        )
    }

    private func response(_ cards: [FeaturedListCard]) -> FeaturedShelvesResponse {
        FeaturedShelvesResponse(mediaType: "movie", shelves: [FeaturedShelf(key: "chart", title: "Charts", mediaType: "movie", lists: cards)])
    }

    private func ids(_ items: [MediaSummary]) -> [String] { items.map(\.ref.mediaId) }

    func testTheWeeksTrendingMoviesListWinsOverAnyOtherTrendingList() async {
        let lists = FallbackListRepository(
            shelves: .success(response([
                card(1, slug: "popular-movies"),
                card(2, slug: "tmdb-trending-movies-day"),
                card(3, slug: "tmdb-trending-movies-week"),
            ])),
            items: [1: [movie(11)], 2: [movie(22)], 3: [movie(33), movie(34)]]
        )
        let loaded = await TrendingFallbackSource(listRepository: lists).load()
        XCTAssertEqual(ids(loaded), ["33", "34"])
        XCTAssertEqual(lists.shelfRequests, ["movie"])
        XCTAssertEqual(lists.itemRequests, [3])
    }

    func testWithoutTheWeekListTheFirstTrendingListIsUsed() async {
        let lists = FallbackListRepository(
            shelves: .success(response([card(1, slug: "popular-movies"), card(2, slug: "tmdb-trending-movies-day"), card(3, slug: "x-trending-y")])),
            items: [2: [movie(22)], 3: [movie(33)]]
        )
        let loaded = await TrendingFallbackSource(listRepository: lists).load()
        XCTAssertEqual(ids(loaded), ["22"])
    }

    func testNoTrendingListGivesNothing() async {
        let lists = FallbackListRepository(shelves: .success(response([card(1, slug: "popular-movies")])), items: [1: [movie(11)]])
        let loaded = await TrendingFallbackSource(listRepository: lists).load()
        XCTAssertTrue(loaded.isEmpty)
        XCTAssertTrue(lists.itemRequests.isEmpty)
    }

    func testOnlyTheFirstTenTitlesAreKept() async {
        let lists = FallbackListRepository(
            shelves: .success(response([card(3, slug: "tmdb-trending-movies-week")])),
            items: [3: (1 ... 14).map(movie)]
        )
        let loaded = await TrendingFallbackSource(listRepository: lists).load()
        XCTAssertEqual(ids(loaded), (1 ... 10).map(String.init))
    }

    func testThePreviewsStandInWhenTheItemsFail() async {
        let lists = FallbackListRepository(
            shelves: .success(response([card(3, slug: "tmdb-trending-movies-week", previews: [movie(41), movie(42), movie(43)])])),
            items: [:]
        )
        let loaded = await TrendingFallbackSource(listRepository: lists).load()
        XCTAssertEqual(ids(loaded), ["41", "42", "43"])
    }

    func testEveryFailureGivesAnEmptyListAndIsNotKept() async {
        let lists = FallbackListRepository(shelves: .failure(APIError.httpStatus(503, nil)), items: [:])
        let source = TrendingFallbackSource(listRepository: lists)
        let first = await source.load()
        XCTAssertTrue(first.isEmpty)
        // The server is back: the next call asks again.
        lists.shelves = .success(response([card(3, slug: "tmdb-trending-movies-week")]))
        lists.items = [3: [movie(33)]]
        let second = await source.load()
        XCTAssertEqual(ids(second), ["33"])
        XCTAssertEqual(lists.shelfRequests.count, 2)
    }

    func testTheListIsKeptForAnHour() async {
        let lists = FallbackListRepository(
            shelves: .success(response([card(3, slug: "tmdb-trending-movies-week")])), items: [3: [movie(33)]]
        )
        var clock = Date(timeIntervalSince1970: 1_800_000_000)
        let source = TrendingFallbackSource(listRepository: lists, now: { clock })
        _ = await source.load()
        clock = clock.addingTimeInterval(3599)
        let again = await source.load()
        XCTAssertEqual(ids(again), ["33"])
        XCTAssertEqual(lists.shelfRequests.count, 1, "kept")
        clock = clock.addingTimeInterval(2)
        _ = await source.load()
        XCTAssertEqual(lists.shelfRequests.count, 2, "an hour on, it asks again")
    }

    func testCallersWhileItLoadsShareOneRequest() async {
        let lists = FallbackListRepository(
            shelves: .success(response([card(3, slug: "tmdb-trending-movies-week")])), items: [3: [movie(33)]]
        )
        let gate = RefreshGate()
        lists.gate = gate
        let source = TrendingFallbackSource(listRepository: lists)
        let first = Task { await source.load() }
        let second = Task { await source.load() }
        for _ in 0 ..< 50 where lists.shelfRequests.isEmpty { await Task.yield() }
        for _ in 0 ..< 10 { await Task.yield() }
        await gate.open()
        let results = [await first.value, await second.value]
        XCTAssertEqual(results.map(ids), [["33"], ["33"]])
        XCTAssertEqual(lists.shelfRequests.count, 1)
        XCTAssertEqual(lists.itemRequests.count, 1)
    }
}

private final class OriginBox: @unchecked Sendable {
    private let lock = NSLock()
    private var posted: [UUID?] = []

    func add(_ origin: UUID?) { lock.withLock { posted.append(origin) } }
    var origins: [UUID?] { lock.withLock { posted } }
}

/// Answers the featured shelves and a list's items from what a test sets, and counts the asks.
@MainActor
private final class FallbackListRepository: ListRepository {
    var shelves: Result<FeaturedShelvesResponse, Error>
    /// A list id's items. A list with no entry fails.
    var items: [Int: [MediaSummary]]
    var shelfRequests: [String] = []
    var itemRequests: [Int] = []
    var gate: RefreshGate?

    init(shelves: Result<FeaturedShelvesResponse, Error>, items: [Int: [MediaSummary]]) {
        self.shelves = shelves
        self.items = items
    }

    func featuredShelves(mediaType: String) async throws -> FeaturedShelvesResponse {
        shelfRequests.append(mediaType)
        await gate?.wait()
        return try shelves.get()
    }

    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary> {
        itemRequests.append(listId)
        guard let results = items[listId] else { throw APIError.httpStatus(500, nil) }
        return PagedResponse(count: results.count, next: nil, previous: nil, results: results)
    }

    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary] { fatalError("Not used") }
    func peopleLists(membershipFor ref: PersonRef) async throws -> [CustomListSummary] { fatalError("Not used") }
    func detail(id: Int) async throws -> CustomListDetail { fatalError("Not used") }
    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary { fatalError("Not used") }
    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail { fatalError("Not used") }
    func delete(id: Int) async throws {}
    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary { fatalError("Not used") }
    func removeItem(listId: Int, itemId: Int) async throws {}
    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail { fatalError("Not used") }
    func people(listId: Int, page: String?) async throws -> PagedResponse<PersonListEntry> { fatalError("Not used") }
    func addPerson(listId: Int, ref: PersonRef) async throws -> PersonListEntry { fatalError("Not used") }
    func removePerson(listId: Int, entryId: Int) async throws {}
    func reorderPeople(listId: Int, entryIds: [Int]) async throws -> CustomListDetail { fatalError("Not used") }
}
