import Foundation
@testable import Spine
import XCTest

// A list page's tag opens its owner's lists with that tag (GET /api/v1/lists/tagged/).
@MainActor
final class TaggedListsTests: XCTestCase {
    override func tearDown() {
        TaggedListsURLProtocol.handler = nil
        super.tearDown()
    }

    func testRequestSendsTheTagOwnerAndPageAndDecodesListSummaries() async throws {
        var requestedURL: URL?
        TaggedListsURLProtocol.handler = { request in
            requestedURL = request.url
            return TaggedListsURLProtocol.response(request, body: Self.pageJSON(ids: [4], next: nil))
        }

        let response = try await makeRepository().tagged("C++ & Rock/Roll", owner: "Spine", page: "2")

        let url = try XCTUnwrap(requestedURL)
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertEqual(components.path, "/api/v1/lists/tagged/")
        XCTAssertEqual(components.queryItems?.first { $0.name == "tag" }?.value, "C++ & Rock/Roll")
        XCTAssertEqual(components.queryItems?.first { $0.name == "owner" }?.value, "Spine")
        XCTAssertEqual(components.queryItems?.first { $0.name == "page" }?.value, "2")
        // The server reads a bare "+" as a space.
        XCTAssertFalse(components.percentEncodedQuery?.contains("+") ?? true)
        XCTAssertTrue(components.percentEncodedQuery?.contains("C%2B%2B") ?? false)

        let list = try XCTUnwrap(response.results.first)
        XCTAssertEqual(list.id, 4)
        XCTAssertEqual(list.tags, ["horror", "70s"])
        XCTAssertEqual(list.owner.username, "Spine")
        XCTAssertEqual(list.previewItems?.map(\.title), ["The Shining"])
    }

    func testFirstPageOmitsThePageParameter() async throws {
        var queryNames: [String] = []
        TaggedListsURLProtocol.handler = { request in
            queryNames = request.url
                .flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.queryItems?.map(\.name) } ?? []
            return TaggedListsURLProtocol.response(request, body: Self.pageJSON(ids: [], next: nil))
        }

        _ = try await makeRepository().tagged("horror", owner: "maya", page: nil)

        XCTAssertEqual(queryNames, ["tag", "owner"])
    }

    func testLoadThenNextPageAppendsOnlyNewListsOfTheSameOwner() async throws {
        let repository = ScriptedTaggedListRepository(results: [
            .success(try Self.page(ids: [1, 2], count: 3, next: "https://example.com/api/v1/lists/tagged/?page=2&tag=horror")),
            .success(try Self.page(ids: [2, 3], count: 3, next: nil)),
        ])
        let viewModel = TaggedListsViewModel(tag: "horror", owner: "maya", listRepository: repository, onUnauthorized: {})

        await viewModel.load()

        XCTAssertEqual(viewModel.lists.map(\.id), [1, 2])
        XCTAssertEqual(viewModel.totalCount, 3)
        XCTAssertFalse(viewModel.isLoading)

        await viewModel.loadNextPage()
        await viewModel.loadNextPage()

        XCTAssertEqual(viewModel.lists.map(\.id), [1, 2, 3])
        XCTAssertEqual(repository.requests.map { $0.tag }, ["horror", "horror"])
        XCTAssertEqual(repository.requests.map { $0.owner }, ["maya", "maya"])
        XCTAssertEqual(repository.requests.map { $0.page }, [nil, "2"])
    }

    func testNextPageLoadsOnlyNearTheEnd() async throws {
        let repository = ScriptedTaggedListRepository(results: [
            .success(try Self.page(ids: Array(1...10), count: 12, next: "https://example.com/api/v1/lists/tagged/?page=2&tag=horror")),
            .success(try Self.page(ids: [11, 12], count: 12, next: nil)),
        ])
        let viewModel = TaggedListsViewModel(tag: "horror", owner: "maya", listRepository: repository, onUnauthorized: {})
        await viewModel.load()

        await viewModel.loadNextPageIfNeeded(currentList: viewModel.lists[0])
        XCTAssertEqual(repository.requests.count, 1)

        await viewModel.loadNextPageIfNeeded(currentList: viewModel.lists[6])
        XCTAssertEqual(repository.requests.map { $0.page }, [nil, "2"])
        XCTAssertEqual(viewModel.lists.count, 12)
    }

    func testFailedNextPageKeepsTheListsAndCanRetry() async throws {
        let repository = ScriptedTaggedListRepository(results: [
            .success(try Self.page(ids: [1], count: 2, next: "https://example.com/api/v1/lists/tagged/?page=2&tag=horror")),
            .failure(APIError.httpStatus(500, "Server error")),
            .success(try Self.page(ids: [2], count: 2, next: nil)),
        ])
        let viewModel = TaggedListsViewModel(tag: "horror", owner: "maya", listRepository: repository, onUnauthorized: {})
        await viewModel.load()

        await viewModel.loadNextPage()

        XCTAssertNotNil(viewModel.nextPageErrorMessage)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.lists.map(\.id), [1])

        await viewModel.loadNextPage()

        XCTAssertNil(viewModel.nextPageErrorMessage)
        XCTAssertEqual(viewModel.lists.map(\.id), [1, 2])
    }

    func testCancelledNextPageIsNotAnError() async throws {
        let repository = ScriptedTaggedListRepository(results: [
            .success(try Self.page(ids: [1], count: 2, next: "https://example.com/api/v1/lists/tagged/?page=2&tag=horror")),
            .failure(CancellationError()),
        ])
        let viewModel = TaggedListsViewModel(tag: "horror", owner: "maya", listRepository: repository, onUnauthorized: {})
        await viewModel.load()

        await viewModel.loadNextPage()

        XCTAssertNil(viewModel.nextPageErrorMessage)
        XCTAssertFalse(viewModel.isLoadingNextPage)
    }

    func testFailedLoadShowsAnErrorAndSignsOutWhenUnauthorized() async {
        var signOuts = 0
        let repository = ScriptedTaggedListRepository(results: [.failure(APIError.unauthorized)])
        let viewModel = TaggedListsViewModel(tag: "horror", owner: "maya", listRepository: repository, onUnauthorized: { signOuts += 1 })

        await viewModel.load()

        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertTrue(viewModel.lists.isEmpty)
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertEqual(signOuts, 1)
    }

    func testRefreshReplacesTheLists() async throws {
        let repository = ScriptedTaggedListRepository(results: [
            .success(try Self.page(ids: [1, 2], count: 2, next: nil)),
            .success(try Self.page(ids: [2], count: 1, next: nil)),
        ])
        let viewModel = TaggedListsViewModel(tag: "horror", owner: "maya", listRepository: repository, onUnauthorized: {})
        await viewModel.load()

        await viewModel.load()

        XCTAssertEqual(viewModel.lists.map(\.id), [2])
        XCTAssertEqual(viewModel.totalCount, 1)
    }

    private func makeRepository() -> APIListRepository {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [TaggedListsURLProtocol.self]
        KeychainTokenStore.shared.accessToken = "tagged-access"
        return APIListRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: configuration)
        ))
    }

    private static func page(ids: [Int], count: Int, next: String?) throws -> PagedResponse<CustomListSummary> {
        let decoded = try JSONDecoder.api.decode(
            PagedResponse<CustomListSummary>.self,
            from: Data(pageJSON(ids: ids, next: next).utf8)
        )
        return PagedResponse(count: count, next: decoded.next, previous: nil, results: decoded.results)
    }

    private static func pageJSON(ids: [Int], next: String?) -> String {
        let lists = ids.map { id in
            """
            {"id": \(id), "name": "List \(id)", "slug": "list-\(id)", "description": "", "tags": ["horror", "70s"],
             "visibility": "public", "is_ranked": false, "list_type": "media",
             "owner": {"id": 99, "username": "Spine", "display_name": "Spine", "avatar_url": null},
             "collaborators": [], "image_url": null, "items_count": 1, "people_count": 0, "entries_count": 1,
             "updated_at": "2026-10-23T12:00:00Z", "like_count": 0,
             "preview_items": [{"ref": {"item_id": \(100 + id), "source": "tmdb", "media_type": "movie", "media_id": "694",
                                        "season_number": null, "episode_number": null},
                                "title": "The Shining", "image_url": null, "poster_url": null}],
             "preview_people": []}
            """
        }
        let nextJSON = next.map { "\"\($0)\"" } ?? "null"
        return #"{"count": \#(ids.count), "next": \#(nextJSON), "previous": null, "results": [\#(lists.joined(separator: ","))]}"#
    }
}

private final class ScriptedTaggedListRepository: ListRepository {
    private var results: [Result<PagedResponse<CustomListSummary>, Error>]
    private(set) var requests: [(tag: String, owner: String, page: String?)] = []

    init(results: [Result<PagedResponse<CustomListSummary>, Error>]) {
        self.results = results
    }

    func tagged(_ tag: String, owner: String, page: String?) async throws -> PagedResponse<CustomListSummary> {
        requests.append((tag: tag, owner: owner, page: page))
        return try results.removeFirst().get()
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

private final class TaggedListsURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: APIError.invalidResponse)
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    static func response(_ request: URLRequest, status: Int = 200, body: String) -> (HTTPURLResponse, Data) {
        (
            HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!,
            Data(body.utf8)
        )
    }
}
