import Foundation
import XCTest
@testable import Spine

@MainActor
final class GoodreadsRatingTests: XCTestCase {
    func testReadsRatingFromPageStructuredData() throws {
        let page = Data("""
        <script type="application/ld+json">{"@context":"https://schema.org","@type":"Book","name":"Project Hail Mary",\
        "aggregateRating":{"@type":"AggregateRating","ratingValue":4.51,"ratingCount":1934391,"reviewCount":265361}}</script>
        """.utf8)

        let rating = try XCTUnwrap(GoodreadsRating.aggregateRating(in: page))

        XCTAssertEqual(rating.value, 4.51, accuracy: 0.0001)
        XCTAssertEqual(rating.count, 1934391)
    }

    func testBotCheckPageHasNoRating() {
        XCTAssertNil(GoodreadsRating.aggregateRating(in: Data()))
        XCTAssertNil(GoodreadsRating.aggregateRating(in: Data("<html><body>Please wait</body></html>".utf8)))
    }

    func testKeepsRatingForAWeek() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "GoodreadsRatingTests"))
        defaults.removePersistentDomain(forName: "GoodreadsRatingTests")
        GoodreadsRatingStubProtocol.page = Data(#""aggregateRating":{"ratingValue":4.2,"ratingCount":10}"#.utf8)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [GoodreadsRatingStubProtocol.self]
        let session = URLSession(configuration: configuration)
        let page = try XCTUnwrap(URL(string: "https://www.goodreads.com/book/show/1"))
        let start = Date(timeIntervalSince1970: 1_000_000)

        let first = await GoodreadsRating.load(page, session: session, defaults: defaults, now: start)
        GoodreadsRatingStubProtocol.page = Data()
        let sixDaysLater = await GoodreadsRating.load(
            page, session: session, defaults: defaults, now: start.addingTimeInterval(6 * 86_400)
        )
        let eightDaysLater = await GoodreadsRating.load(
            page, session: session, defaults: defaults, now: start.addingTimeInterval(8 * 86_400)
        )

        XCTAssertEqual(first?.value, "4.20")
        XCTAssertEqual(first?.voteCount, 10)
        XCTAssertEqual(sixDaysLater, first)
        XCTAssertNil(eightDaysLater)
    }
}

private final class GoodreadsRatingStubProtocol: URLProtocol {
    nonisolated(unsafe) static var page = Data()

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.page)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
