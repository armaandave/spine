import XCTest
@testable import Spine

@MainActor
final class FeedPlanningStoreTests: XCTestCase {
    private var clock = Date(timeIntervalSince1970: 1_000_000)
    private var unauthorizedCount = 0

    private func makeStore(_ repository: ActivityRepository) -> FeedPlanningStore {
        FeedPlanningStore(
            activityRepository: repository,
            onUnauthorized: { [unowned self] in unauthorizedCount += 1 },
            now: { [unowned self] in clock }
        )
    }

    /// A Planning row. `number` makes its object id (`number`) and its row id (`-number`).
    private func row(_ number: Int, count: Int? = 3, mediaType: String? = "movie") throws -> ActivityItem {
        try FeedTestItems.planningRow(id: -number, count: count, mediaType: mediaType)
    }

    private func day(_ names: [String] = ["A", "B"]) throws -> PlanningDay {
        try FeedTestItems.planningDay(titles: names.map { ($0, "https://p/\($0).jpg") })
    }

    private func settle(_ attempts: Int = 40) async {
        for _ in 0 ..< attempts { await Task.yield() }
    }

    // MARK: Which items load

    func testItIgnoresItemsThatAreNotPlanningRows() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        store.load(for: try FeedTestItems.log(id: 1, rating: "4"))
        store.load(for: try FeedTestItems.day(id: 2, label: "3 episodes", mediaType: "tv"))
        await settle()
        XCTAssertEqual(repository.requests.count, 0)
        XCTAssertNil(FeedPlanningStore.key(for: try FeedTestItems.log(id: 1)))
        XCTAssertTrue(store.days.isEmpty)
    }

    func testTheKeyIsTheObjectIDAndTheCount() throws {
        XCTAssertEqual(FeedPlanningStore.key(for: try row(17_592, count: 6)), "17592-6")
        XCTAssertEqual(FeedPlanningStore.key(for: try row(17_592, count: nil)), "17592-0")
    }

    func testItAsksForTheRowsTitlesWithItsMediaType() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        store.load(for: try row(41, mediaType: "tv"))
        store.load(for: try row(42, mediaType: nil))
        await repository.waitForRequests(2)
        XCTAssertEqual(repository.requests.map(\.id), [41, 42])
        XCTAssertEqual(repository.requests.map(\.mediaType), ["tv", nil])
    }

    // MARK: Once per key

    func testALoadedDayIsNeverLoadedAgain() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        let item = try row(7)
        store.load(for: item)
        await repository.waitForRequests(1)
        repository.answer(0, with: .success(try day()))
        await settle()

        XCTAssertEqual(store.days["7-3"]?.results.count, 2)
        XCTAssertEqual(store.day(for: item)?.results.count, 2)
        store.load(for: item)
        store.load(for: item)
        await settle()
        XCTAssertEqual(repository.requests.count, 1)
    }

    func testARequestOnItsWayIsNotAskedForTwice() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        let item = try row(8)
        store.load(for: item)
        store.load(for: item)
        store.load(for: item)
        await repository.waitForRequests(1)
        await settle()
        XCTAssertEqual(repository.requests.count, 1)
    }

    func testAGrownDayIsAnotherKey() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        store.load(for: try row(9, count: 3))
        await repository.waitForRequests(1)
        repository.answer(0, with: .success(try day(["A", "B", "C"])))
        await settle()

        // Two more titles were added that day: the same object id with a larger count loads again.
        store.load(for: try row(9, count: 5))
        await repository.waitForRequests(2)
        XCTAssertEqual(repository.requests.count, 2)
        repository.answer(1, with: .success(try day(["A", "B", "C", "D", "E"])))
        await settle()
        XCTAssertEqual(store.days["9-3"]?.results.count, 3)
        XCTAssertEqual(store.days["9-5"]?.results.count, 5)
    }

    // MARK: Two at a time

    func testNeverMoreThanTwoRequestsAreInFlight() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        for number in 1 ... 5 { store.load(for: try row(number)) }
        await repository.waitForRequests(2)
        await settle()
        XCTAssertEqual(repository.requests.count, 2, "Two started, three wait")
        XCTAssertEqual(repository.requests.map(\.id), [1, 2])

        repository.answer(0, with: .success(try day()))
        await repository.waitForRequests(3)
        await settle()
        XCTAssertEqual(repository.requests.map(\.id), [1, 2, 3], "An answer lets the next one start, in order")

        repository.answer(2, with: .success(try day()))
        repository.answer(1, with: .success(try day()))
        await repository.waitForRequests(5)
        await settle()
        XCTAssertEqual(repository.requests.map(\.id), [1, 2, 3, 4, 5])
        XCTAssertLessThanOrEqual(repository.maximumInFlight, FeedPlanningStore.maxInFlight)
        repository.answer(3, with: .success(try day()))
        repository.answer(4, with: .success(try day()))
        await settle()
        XCTAssertEqual(store.days.count, 5)
    }

    func testAQueuedRowIsNotQueuedTwice() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        for number in 1 ... 3 { store.load(for: try row(number)) }
        store.load(for: try row(3))
        store.load(for: try row(3))
        await repository.waitForRequests(2)
        repository.answer(0, with: .success(try day()))
        repository.answer(1, with: .success(try day()))
        await repository.waitForRequests(3)
        await settle()
        XCTAssertEqual(repository.requests.map(\.id), [1, 2, 3])
    }

    // MARK: Failures

    func testAFailureIsRememberedForSixtySeconds() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        let item = try row(11)
        store.load(for: item)
        await repository.waitForRequests(1)
        repository.answer(0, with: .failure(APIError.httpStatus(500, nil)))
        await settle()
        XCTAssertNil(store.day(for: item))

        clock.addTimeInterval(59)
        store.load(for: item)
        await settle()
        XCTAssertEqual(repository.requests.count, 1, "Still backing off")

        clock.addTimeInterval(2)
        store.load(for: item)
        await repository.waitForRequests(2)
        XCTAssertEqual(repository.requests.count, 2, "Sixty seconds on, the next appearance may try again")
        repository.answer(1, with: .success(try day()))
        await settle()
        XCTAssertNotNil(store.day(for: item))
    }

    func testAFailureFreesItsSlotForTheNextRow() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        for number in 1 ... 3 { store.load(for: try row(number)) }
        await repository.waitForRequests(2)
        repository.answer(0, with: .failure(APIError.httpStatus(500, nil)))
        await repository.waitForRequests(3)
        XCTAssertEqual(repository.requests.map(\.id), [1, 2, 3])
    }

    func testAnUnauthorizedAnswerCallsBackOnce() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        store.load(for: try row(12))
        await repository.waitForRequests(1)
        repository.answer(0, with: .failure(APIError.unauthorized))
        await settle()
        XCTAssertEqual(unauthorizedCount, 1)

        store.load(for: try row(13))
        await repository.waitForRequests(2)
        repository.answer(1, with: .failure(APIError.httpStatus(500, nil)))
        await settle()
        XCTAssertEqual(unauthorizedCount, 1, "Only a 401 signs out")
    }

    // MARK: The host

    func testTheHostIsToldWhenADayArrives() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        var changes = 0
        store.onChange = { changes += 1 }
        store.load(for: try row(21))
        await repository.waitForRequests(1)
        XCTAssertEqual(changes, 0)
        repository.answer(0, with: .success(try day()))
        await settle()
        XCTAssertEqual(changes, 1)

        store.load(for: try row(22))
        await repository.waitForRequests(2)
        repository.answer(1, with: .failure(APIError.httpStatus(500, nil)))
        await settle()
        XCTAssertEqual(changes, 1, "A failure changes nothing")
    }

    func testDaysByObjectIDFeedsTheEntries() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        let first = try row(31)
        let second = try row(32)
        store.load(for: first)
        await repository.waitForRequests(1)
        repository.answer(0, with: .success(try day(["A", "B", "C"])))
        await settle()

        let byID = store.daysByObjectID(for: [first, second, try FeedTestItems.log(id: 3)])
        XCTAssertEqual(Array(byID.keys), [31])
        XCTAssertEqual(byID[31]?.results.count, 3)
        // And it is the shape the entries take: the Planning card names the titles.
        let entries = FeedEntries.make(items: [first], planning: byID)
        guard case let .card(model) = entries.last else { return XCTFail("Expected a card") }
        XCTAssertEqual(model.art.planningPosterURLs.count, 3)
    }

    func testARequestThatStartedKeepsGoingWhenNobodyIsLookingAnyMore() async throws {
        let repository = GatedPlanningRepository()
        let store = makeStore(repository)
        let item = try row(51)
        store.load(for: item)
        await repository.waitForRequests(1)
        // The card has scrolled off; nothing cancels the request, and its answer is kept for next time.
        repository.answer(0, with: .success(try day()))
        await settle()
        XCTAssertNotNil(store.day(for: item))
    }
}

/// Holds every planning request until the test answers it, in the order they were made.
private final class GatedPlanningRepository: ActivityRepository {
    struct Request: Equatable {
        let id: Int
        let mediaType: String?
    }

    private var waiting: [CheckedContinuation<PlanningDay, Error>] = []
    private(set) var requests: [Request] = []
    private var inFlight = 0
    private(set) var maximumInFlight = 0

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] {
        fatalError("Not used")
    }

    func planningTitles(id: Int, mediaType: String?) async throws -> PlanningDay {
        requests.append(Request(id: id, mediaType: mediaType))
        inFlight += 1
        maximumInFlight = max(maximumInFlight, inFlight)
        defer { inFlight -= 1 }
        return try await withCheckedThrowingContinuation { waiting.append($0) }
    }

    func waitForRequests(_ count: Int) async {
        var turns = 0
        while requests.count < count, turns < 2000 {
            await Task.yield()
            turns += 1
        }
    }

    func answer(_ index: Int, with result: Result<PlanningDay, Error>) {
        waiting[index].resume(with: result)
    }
}
