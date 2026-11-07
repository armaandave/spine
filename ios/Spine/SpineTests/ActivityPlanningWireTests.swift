import XCTest
@testable import Spine

/// The Home feed asks for Planning rows on every page, the first and each one after a cursor, so a cursor page is
/// not the one that quietly drops them (docs/activity-contract.md, AC-8). The rest of the Planning wire is pinned in
/// `ActivityPhase5Tests`.
@MainActor
final class ActivityPlanningWireTests: XCTestCase {
    override func setUp() {
        super.setUp()
        PlanningWireRequestProtocol.requests = []
    }

    private func repository() -> APIActivityRepository {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [PlanningWireRequestProtocol.self]
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: config)
        )
        client.tokenProvider.accessToken = "access"
        return APIActivityRepository(client: client)
    }

    func testFeedPagesAskForPlanningOnThePageAfterACursorToo() async throws {
        let repository = repository()
        _ = try await repository.feedPage(pageSize: 25, cursorLink: nil)
        _ = try await repository.feedPage(pageSize: 25, cursorLink: "https://example.com/api/v1/feed/?cursor=abc")

        let queries = PlanningWireRequestProtocol.requests.map { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        }
        XCTAssertEqual(queries.count, 2)
        for query in queries {
            XCTAssertEqual(query.filter { $0.name == "include" }.map(\.value), ["planning"])
            XCTAssertEqual(query.first { $0.name == "page_size" }?.value, "25")
        }
        XCTAssertNil(queries[0].first { $0.name == "cursor" })
        XCTAssertEqual(queries[1].first { $0.name == "cursor" }?.value, "abc")
    }
}

private final class PlanningWireRequestProtocol: URLProtocol {
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"next_cursor": null, "previous_cursor": null, "results": []}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
