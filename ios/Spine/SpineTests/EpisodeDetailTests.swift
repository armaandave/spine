import Foundation
import XCTest
@testable import Spine

@MainActor
final class EpisodeDetailTests: XCTestCase {
    override func tearDown() {
        EpisodeRequestCaptureURLProtocol.handler = nil
        super.tearDown()
    }

    func testEpisodeRefBuildsParentContext() throws {
        let season = MediaRef(
            itemId: nil,
            source: "tmdb",
            mediaType: "season",
            mediaId: "1399",
            seasonNumber: 3,
            episodeNumber: nil
        )

        let episode = try XCTUnwrap(season.episodeRef(episodeNumber: 2, itemId: 912))

        XCTAssertTrue(episode.isEpisode)
        XCTAssertEqual(episode.itemId, 912)
        XCTAssertEqual(episode.episodeCode, "S03E02")
        XCTAssertEqual(episode.parentSeasonRef, season)
        XCTAssertEqual(
            episode.parentTVRef,
            MediaRef(
                itemId: nil,
                source: "tmdb",
                mediaType: "tv",
                mediaId: "1399",
                seasonNumber: nil,
                episodeNumber: nil
            )
        )
    }

    func testEpisodeMediaDetailDecoding() throws {
        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Self.episodeDetailData)

        XCTAssertEqual(detail.ref.mediaType, "episode")
        XCTAssertEqual(detail.ref.seasonNumber, 3)
        XCTAssertEqual(detail.ref.episodeNumber, 2)
        XCTAssertEqual(detail.title, "Dark Wings, Dark Words")
        XCTAssertEqual(detail.subtitle, "Game of Thrones")
        XCTAssertEqual(detail.synopsis, "Arya meets the Brotherhood Without Banners.")
        XCTAssertNil(detail.imageUrl)
        XCTAssertNil(detail.posterUrl)
        XCTAssertNil(detail.posterOrientation)
        XCTAssertEqual(detail.episodeStillURL, "https://image.tmdb.org/t/p/original/episode-still.jpg")
        XCTAssertEqual(detail.details?["runtime"], .string("58 min"))

        let tmdb = try XCTUnwrap(detail.externalRatings?.first { $0.source == "TMDB" })
        XCTAssertEqual(tmdb.value, "8.6")
        XCTAssertEqual(tmdb.voteCount, 321)
        XCTAssertEqual(tmdb.maxValue, "10")
        XCTAssertEqual(
            tmdb.destinationURL?.absoluteString,
            "https://www.themoviedb.org/tv/1399/season/3/episode/2"
        )

        let imdb = try XCTUnwrap(detail.externalRatings?.first { $0.source == "IMDb" })
        XCTAssertEqual(imdb.source, "IMDb")
        XCTAssertEqual(imdb.value, "")
        XCTAssertNil(imdb.voteCount)
        XCTAssertEqual(imdb.maxValue, "10")
        XCTAssertEqual(imdb.destinationURL?.absoluteString, "https://www.imdb.com/title/tt2178784/")
    }

    func testEpisodeExternalRatingPresentationIncludesTMDB() throws {
        XCTAssertTrue(MediaExternalRatingPresentation.includes(source: "TMDB", mediaType: "episode"))
        XCTAssertTrue(MediaExternalRatingPresentation.includes(source: "IMDb", mediaType: "episode"))
        XCTAssertFalse(MediaExternalRatingPresentation.includes(source: "TMDB", mediaType: "movie"))
        XCTAssertFalse(MediaExternalRatingPresentation.includes(source: "TMDB", mediaType: "tv"))
        XCTAssertTrue(MediaExternalRatingPresentation.includes(source: "TMDB", mediaType: "season"))
        XCTAssertFalse(MediaExternalRatingPresentation.includes(source: "IMDb", mediaType: "season"))
        XCTAssertFalse(MediaExternalRatingPresentation.includes(source: "Rotten Tomatoes", mediaType: "season"))
        XCTAssertTrue(MediaExternalRatingPresentation.includes(source: "IMDb", mediaType: "tv"))
        XCTAssertTrue(MediaExternalRatingPresentation.includes(source: "Rotten Tomatoes", mediaType: "tv"))
        XCTAssertFalse(MediaExternalRatingPresentation.includes(source: "MusicBrainz", mediaType: "music"))
        XCTAssertFalse(MediaExternalRatingPresentation.includes(source: "Google Books", mediaType: "book"))
    }

    func testEpisodeIMDbRatingDecodesWhenBackendEnrichesIt() throws {
        let rating = try JSONDecoder.api.decode(
            ExternalRating.self,
            from: Data(
                #"{"source":"IMDb","value":"8.5","vote_count":12000,"max_value":"10","url":"https://www.imdb.com/title/tt2178784/"}"#.utf8
            )
        )

        XCTAssertEqual(rating.value, "8.5")
        XCTAssertEqual(rating.voteCount, 12000)
        XCTAssertEqual(rating.destinationURL?.absoluteString, "https://www.imdb.com/title/tt2178784/")
    }

    func testMediaRepositoryBuildsEpisodeDetailRequest() async throws {
        let repository = makeMediaRepository()
        let ref = Self.episodeRef

        EpisodeRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })

            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(components.path, "/api/v1/media/tmdb/episode/1399/")
            XCTAssertEqual(query["season_number"], "3")
            XCTAssertEqual(query["episode_number"], "2")

            return Self.response(for: request, data: Self.episodeDetailData)
        }

        let detail = try await repository.detail(ref: ref)

        XCTAssertEqual(detail.ref, ref)
        XCTAssertEqual(detail.title, "Dark Wings, Dark Words")
    }

    func testTrackingRepositoryScopesEpisodeDetailRequest() async throws {
        let repository = makeTrackingRepository()

        EpisodeRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })

            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(components.path, "/api/v1/tracking/tmdb/episode/1399/")
            XCTAssertEqual(query["season_number"], "3")
            XCTAssertEqual(query["episode_number"], "2")

            return Self.response(for: request, data: Self.trackingStateData)
        }

        let state = try await repository.detail(ref: Self.episodeRef)

        XCTAssertEqual(state.trackingId, 44)
        XCTAssertEqual(state.progress?.value, 2)
    }

    func testTrackingRepositoryBuildsEpisodeWatchRequest() async throws {
        let repository = makeTrackingRepository()
        let watchedAt = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-07-14T19:30:00Z"))

        EpisodeRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))

            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                components.path,
                "/api/v1/tracking/tmdb/tv/1399/seasons/3/episodes/2/watch/"
            )
            XCTAssertNil(components.query)
            XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")

            let body = try JSONDecoder().decode(EpisodeWatchBody.self, from: episodeRequestBodyData(for: request))
            XCTAssertEqual(body.watchedAt, "2026-07-14T19:30:00Z")

            return Self.response(for: request, data: Self.trackingStateData)
        }

        let state = try await repository.watchEpisode(
            source: "tmdb",
            mediaId: "1399",
            seasonNumber: 3,
            episodeNumber: 2,
            watchedAt: watchedAt
        )

        XCTAssertEqual(state.trackingId, 44)
    }

    private func makeMediaRepository() -> APIMediaRepository {
        APIMediaRepository(client: makeClient())
    }

    private func makeTrackingRepository() -> APITrackingRepository {
        APITrackingRepository(client: makeClient())
    }

    private func makeClient() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [EpisodeRequestCaptureURLProtocol.self]
        let session = URLSession(configuration: configuration)
        return APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: session
        )
    }

    private static let episodeRef = MediaRef(
        itemId: 912,
        source: "tmdb",
        mediaType: "episode",
        mediaId: "1399",
        seasonNumber: 3,
        episodeNumber: 2
    )

    private static let episodeDetailData = Data(
        """
        {
          "ref": {
            "item_id": 912,
            "source": "tmdb",
            "media_type": "episode",
            "media_id": "1399",
            "season_number": 3,
            "episode_number": 2
          },
          "title": "Dark Wings, Dark Words",
          "subtitle": "Game of Thrones",
          "overview": "Arya meets the Brotherhood Without Banners.",
          "synopsis": "Arya meets the Brotherhood Without Banners.",
          "image_url": null,
          "poster_url": null,
          "poster_orientation": null,
          "poster_aspect_ratio": null,
          "poster_width": null,
          "poster_height": null,
          "release_date": "2013-04-07",
          "backdrop_url": "https://image.tmdb.org/t/p/original/episode-still.jpg",
          "details": {
            "format": "Episode",
            "series_title": "Game of Thrones",
            "season_title": "Season 3",
            "air_date": "2013-04-07",
            "runtime": "58 min"
          },
          "external_ratings": [
            {
              "source": "TMDB",
              "value": "8.6",
              "vote_count": 321,
              "max_value": "10",
              "url": "https://www.themoviedb.org/tv/1399/season/3/episode/2"
            },
            {
              "source": "IMDb",
              "value": "",
              "vote_count": null,
              "max_value": "10",
              "url": "https://www.imdb.com/title/tt2178784/"
            }
          ],
          "reviews": [],
          "cast": [],
          "crew": [],
          "related_sections": [],
          "episodes": [],
          "seasons": []
        }
        """.utf8
    )

    private static let trackingStateData = Data(
        """
        {
          "tracking_id": 44,
          "status": "In progress",
          "rating": null,
          "progress": {
            "kind": "episodes",
            "value": 2,
            "max": 10,
            "unit": "episode"
          },
          "latest_progress_change": null,
          "repeats": 1,
          "start_date": "2026-07-01",
          "end_date": null,
          "notes": "",
          "updated_at": "2026-07-14T19:30:00Z"
        }
        """.utf8
    )

    private static func response(for request: URLRequest, data: Data) -> (HTTPURLResponse, Data) {
        (
            HTTPURLResponse(
                url: request.url!,
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!,
            data
        )
    }
}

private struct EpisodeWatchBody: Decodable {
    let watchedAt: String?

    enum CodingKeys: String, CodingKey {
        case watchedAt = "watched_at"
    }
}

private func episodeRequestBodyData(for request: URLRequest) -> Data {
    if let body = request.httpBody {
        return body
    }
    guard let stream = request.httpBodyStream else {
        return Data()
    }

    let bufferSize = 1_024
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
    defer { buffer.deallocate() }

    var data = Data()
    stream.open()
    defer { stream.close() }
    while stream.hasBytesAvailable {
        let count = stream.read(buffer, maxLength: bufferSize)
        guard count > 0 else { break }
        data.append(buffer, count: count)
    }
    return data
}

private final class EpisodeRequestCaptureURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
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
}
