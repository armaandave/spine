import XCTest
@testable import Spine

/// The Trending tile's "Plan to watch" write and the list that fills the tile when few people logged anything.
@MainActor
final class TrendingSourcesTests: XCTestCase {
    // MARK: Plan to watch

    private var repository: QuickActionRecordingRepository!
    private var unauthorized = 0
    private let origin = UUID()

    override func setUp() {
        super.setUp()
        repository = QuickActionRecordingRepository()
        unauthorized = 0
    }

    private func writer() -> TrendingPlanWriter {
        TrendingPlanWriter(
            trackingRepository: repository, changeOrigin: origin, onUnauthorized: { [unowned self] in unauthorized += 1 }
        )
    }

    private func title(_ type: String, _ id: String, source: String = "tmdb") -> TrendingTitle {
        TrendingTitle(
            key: "\(source):\(type):\(id)",
            media: MediaSummary(
                ref: MediaRef(itemId: nil, source: source, mediaType: type, mediaId: id, seasonNumber: nil, episodeNumber: nil),
                title: "Title \(id)", posterUrl: "https://p/\(id).jpg", posterOrientation: .portrait
            ),
            people: [], includesYou: false, averageRating: nil, lastDay: nil
        )
    }

    private func posts(for ref: MediaRef, during body: () async -> Void) async -> [UUID?] {
        let box = OriginBox()
        let token = NotificationCenter.default.addObserver(forName: .mediaStateDidChange, object: nil, queue: nil) { note in
            guard let posted = note.userInfo?["ref"] as? MediaRef, posted == ref else { return }
            box.add(note.userInfo?["origin"] as? UUID)
        }
        defer { NotificationCenter.default.removeObserver(token) }
        await body()
        return box.origins
    }

    func testAShowIsPlannedThroughTheTVAction() async {
        let show = title("tv", "100")
        let writer = writer()
        let took = await writer.plan(show).value
        XCTAssertTrue(took)
        XCTAssertEqual(repository.tvCalls, [.init(source: "tmdb", mediaId: "100", action: .planning)])
        XCTAssertTrue(repository.animeCalls.isEmpty)
        XCTAssertTrue(repository.updateCalls.isEmpty)
        XCTAssertTrue(writer.isPlanned(show))
    }

    func testAnAnimeIsPlannedThroughTheAnimeAction() async {
        let anime = title("anime", "200", source: "mal")
        let took = await writer().plan(anime).value
        XCTAssertTrue(took)
        XCTAssertEqual(repository.animeCalls, [.init(source: "mal", mediaId: "200", action: .planning)])
        XCTAssertTrue(repository.tvCalls.isEmpty)
        XCTAssertTrue(repository.updateCalls.isEmpty)
    }

    func testEveryOtherTypeGetsAPlanningStatusUpdate() async {
        let writer = writer()
        for (type, source) in [("movie", "tmdb"), ("book", "hardcover"), ("game", "igdb"), ("manga", "mal"), ("music", "apple")] {
            let took = await writer.plan(title(type, "x-\(type)", source: source)).value
            XCTAssertTrue(took, type)
        }
        XCTAssertEqual(repository.updateCalls.map(\.status), Array(repeating: "Planning", count: 5))
        XCTAssertEqual(repository.updateCalls.map(\.ref.mediaType), ["movie", "book", "game", "manga", "music"])
        XCTAssertTrue(repository.tvCalls.isEmpty)
        XCTAssertTrue(repository.animeCalls.isEmpty)
    }

    func testTheTitleShowsPlannedBeforeTheServerAnswers() async {
        let movie = title("movie", "300")
        let gate = RefreshGate()
        repository.gate = gate
        let writer = writer()
        let write = writer.plan(movie)
        XCTAssertTrue(writer.isPlanned(movie), "optimistic, in the same turn as the tap")
        XCTAssertTrue(writer.plannedIDs.contains(movie.id))
        await gate.open()
        let took = await write.value
        XCTAssertTrue(took)
        XCTAssertTrue(writer.isPlanned(movie))
        XCTAssertNil(writer.errorMessage)
    }

    func testAFailureRollsTheTitleBackWithAnError() async {
        let movie = title("movie", "400")
        repository.error = APIError.httpStatus(500, nil)
        let writer = writer()
        var took: Bool?
        let origins = await posts(for: movie.media.ref) { took = await writer.plan(movie).value }
        XCTAssertEqual(took, false)
        XCTAssertFalse(writer.isPlanned(movie))
        XCTAssertEqual(writer.errorMessage, APIError.httpStatus(500, nil).localizedDescription)
        XCTAssertTrue(origins.isEmpty, "a failed write changed nothing to reload")
        // Trying again sends again.
        repository.error = nil
        let retried = await writer.plan(movie).value
        XCTAssertTrue(retried)
        XCTAssertTrue(writer.isPlanned(movie))
        XCTAssertNil(writer.errorMessage)
        XCTAssertEqual(repository.updateCalls.count, 2)
    }

    func testAnExpiredSessionCallsOnUnauthorized() async {
        repository.error = APIError.unauthorized
        let movie = title("movie", "500")
        let writer = writer()
        _ = await writer.plan(movie).value
        XCTAssertEqual(unauthorized, 1)
        XCTAssertFalse(writer.isPlanned(movie))
    }

    func testASuccessPostsMediaStateChangeWithTheOrigin() async {
        let show = title("tv", "600")
        let origins = await posts(for: show.media.ref) { _ = await writer().plan(show).value }
        XCTAssertEqual(origins, [origin])
    }

    func testPlanningTwiceSendsOnce() async {
        let movie = title("movie", "700")
        let gate = RefreshGate()
        repository.gate = gate
        let writer = writer()
        let first = writer.plan(movie)
        let second = writer.plan(movie)
        await gate.open()
        let results = [await first.value, await second.value]
        XCTAssertEqual(results, [true, true])
        XCTAssertEqual(repository.updateCalls.count, 1)
    }

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
