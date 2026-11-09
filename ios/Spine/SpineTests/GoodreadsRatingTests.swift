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

    func testKeepsLastRatingUntilANewerOneReplacesIt() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "GoodreadsRatingTests"))
        defaults.removePersistentDomain(forName: "GoodreadsRatingTests")
        GoodreadsRatingStubProtocol.page = Data(#""aggregateRating":{"ratingValue":4.2,"ratingCount":10}"#.utf8)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [GoodreadsRatingStubProtocol.self]
        let session = URLSession(configuration: configuration)
        let page = try XCTUnwrap(URL(string: "https://www.goodreads.com/book/show/1"))
        let start = Date(timeIntervalSince1970: 1_000_000)

        let noBrowser: (URL) async -> (value: Double, count: Int?)? = { _ in nil }

        let eightDays = start.addingTimeInterval(8 * 86_400)

        let loaded = await GoodreadsRating.load(
            page, session: session, defaults: defaults, now: start, readInBrowser: noBrowser
        )
        let first = try XCTUnwrap(loaded)
        XCTAssertEqual(first.value, "4.2")
        XCTAssertEqual(first.voteCount, 10)
        XCTAssertEqual(
            GoodreadsRating.saved(page, defaults: defaults, now: start.addingTimeInterval(6 * 86_400)),
            GoodreadsRating.Saved(rating: first, isFresh: true)
        )

        // A week on it is read again; a failed read leaves the last rating in place.
        GoodreadsRatingStubProtocol.page = Data()
        let failedRead = await GoodreadsRating.load(
            page, session: session, defaults: defaults, now: eightDays, readInBrowser: noBrowser
        )
        XCTAssertNil(failedRead)
        XCTAssertEqual(
            GoodreadsRating.saved(page, defaults: defaults, now: eightDays),
            GoodreadsRating.Saved(rating: first, isFresh: false)
        )

        // A newer rating replaces it.
        GoodreadsRatingStubProtocol.page = Data(#""aggregateRating":{"ratingValue":4.3,"ratingCount":12}"#.utf8)
        _ = await GoodreadsRating.load(page, session: session, defaults: defaults, now: eightDays, readInBrowser: noBrowser)
        XCTAssertEqual(GoodreadsRating.saved(page, defaults: defaults, now: eightDays)?.rating.value, "4.3")
    }

    func testBotCheckFallsBackToTheBrowserView() async throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "GoodreadsRatingBotCheckTests"))
        defaults.removePersistentDomain(forName: "GoodreadsRatingBotCheckTests")
        GoodreadsRatingStubProtocol.page = Data("<html>AwsWafIntegration challenge</html>".utf8)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [GoodreadsRatingStubProtocol.self]
        let page = try XCTUnwrap(URL(string: "https://www.goodreads.com/book/show/2"))
        var browserPages: [URL] = []

        let rating = await GoodreadsRating.load(
            page,
            session: URLSession(configuration: configuration),
            defaults: defaults,
            readInBrowser: { url in
                browserPages.append(url)
                return (value: 4.4, count: 7)
            }
        )

        XCTAssertEqual(browserPages, [page])
        XCTAssertEqual(rating?.value, "4.4")
        XCTAssertEqual(rating?.voteCount, 7)
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
