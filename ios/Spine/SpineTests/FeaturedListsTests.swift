import Foundation
@testable import Spine
import XCTest

// Featured shelves on Search (docs/featured-lists/README.md, "API" and "iOS: shelves on Search").

@MainActor
final class FeaturedListsDecodingTests: XCTestCase {
    func testDecodesContractShelvesResponse() throws {
        let response = try decode(FeaturedFixtures.shelvesJSON)

        XCTAssertFalse(response.isLegacy)
        XCTAssertEqual(response.generatedAt, FeaturedFixtures.date("2026-10-21T18:02:00Z"))
        XCTAssertEqual(response.shelves.map(\.key), ["now", "movie", "mixed"])

        let now = response.shelves[0]
        XCTAssertTrue(now.isNow)
        XCTAssertNil(now.mediaType)
        XCTAssertEqual(now.liveCount, 1)
        XCTAssertNil(now.seeAll)

        let movies = response.shelves[1]
        XCTAssertEqual(movies.title, "Movies")
        XCTAssertEqual(movies.mediaType, "movie")
        XCTAssertEqual(movies.count, 42)
        XCTAssertEqual(movies.kindCounts, ["chart": 6, "award": 9, "canon": 14, "community": 10, "curator": 3])
        XCTAssertEqual(movies.seeAll, "/api/v1/lists/featured/shelves/?media_type=movie")
        XCTAssertTrue(response.shelves[2].isMixed)

        let card = try XCTUnwrap(movies.lists.first)
        XCTAssertEqual(card.id, 12)
        XCTAssertEqual(card.name, "Netflix Top 10: Films")
        XCTAssertEqual(card.slug, "netflix-top-10-films")
        XCTAssertTrue(card.isRanked)
        XCTAssertEqual(card.itemsCount, 10)
        XCTAssertEqual(card.mediaTypes, ["movie"])
        XCTAssertEqual(card.kind, .chart)
        XCTAssertEqual(card.source, FeaturedSource(name: "Netflix", url: "https://www.netflix.com/tudum/top10"))
        XCTAssertEqual(card.cadence, .weekly)
        XCTAssertEqual(card.periodLabel, "Week of Oct 13")
        // Django sends microseconds.
        XCTAssertEqual(card.contentChangedAt, FeaturedFixtures.date("2026-10-21T16:00:00Z"))
        XCTAssertEqual(card.lastSyncedAt, FeaturedFixtures.date("2026-10-21T17:30:00Z"))
        XCTAssertNil(card.live)
        XCTAssertEqual(card.previewItems.map(\.title), ["One", "Two", "Three"])
        XCTAssertEqual(card.previewItems.first?.position, 1)
        XCTAssertEqual(card.previews.map(\.note), ["▲2", nil, nil])

        let live = try XCTUnwrap(now.lists.first?.live)
        XCTAssertEqual(live.status, .live)
        XCTAssertEqual(live.label, "7 of 23 announced")
        XCTAssertEqual(live.startsAt, FeaturedFixtures.date("2026-10-21T17:00:00Z"))
        XCTAssertEqual(live.endsAt, FeaturedFixtures.date("2026-10-21T21:00:00Z"))
    }

    func testUnknownEnumValuesFallBackToUnknown() throws {
        let card = try decodeCard("""
        {"id": 3, "name": "Odd", "kind": "podcast", "cadence": "hourly",
         "live": {"status": "paused", "label": "", "starts_at": null, "ends_at": null}}
        """)

        XCTAssertEqual(card.kind, .unknown)
        XCTAssertEqual(card.cadence, .unknown)
        XCTAssertEqual(card.live?.status, .unknown)
        XCTAssertEqual(card.live?.isLive(at: .now), false)
    }

    func testNullLiveAndMissingOptionalFieldsUseDefaults() throws {
        let card = try decodeCard("""
        {"id": 5, "name": "Bare", "live": null,
         "preview_items": [\(FeaturedFixtures.mediaJSON(id: "1", type: "book", title: "A Book"))]}
        """)

        XCTAssertNil(card.live)
        XCTAssertEqual(card.slug, "")
        XCTAssertEqual(card.description, "")
        XCTAssertFalse(card.isRanked)
        XCTAssertEqual(card.kind, .unknown)
        XCTAssertEqual(card.cadence, .unknown)
        XCTAssertNil(card.source)
        XCTAssertNil(card.periodLabel)
        XCTAssertNil(card.contentChangedAt)
        XCTAssertEqual(card.itemsCount, 1)
        // Without media_types, they come from the preview items.
        XCTAssertEqual(card.mediaTypes, ["book"])
    }

    func testMalformedEntriesAreDroppedInsteadOfFailingTheResponse() throws {
        let response = try decode("""
        {"shelves": [
          {"key": "movie", "media_type": "movie", "title": "Movies", "kind_counts": "oops",
           "lists": [
             {"name": "No id"},
             {"id": 7, "name": "Kept", "source": "Netflix", "content_changed_at": "yesterday", "items_count": "ten",
              "preview_items": [{"title": "No ref"}, \(FeaturedFixtures.mediaJSON(id: "9", type: "movie", title: "Nine"))]}
           ]},
          {"title": null},
          42
        ]}
        """)

        XCTAssertNil(response.generatedAt)
        XCTAssertEqual(response.shelves.map(\.key), ["movie"])
        let shelf = response.shelves[0]
        XCTAssertEqual(shelf.kindCounts, [:])
        XCTAssertEqual(shelf.count, 1)
        let card = try XCTUnwrap(shelf.lists.first)
        XCTAssertEqual(card.id, 7)
        XCTAssertNil(card.source)
        XCTAssertNil(card.contentChangedAt)
        XCTAssertEqual(card.previewItems.map(\.title), ["Nine"])
        XCTAssertEqual(card.itemsCount, 1)
    }

    func testMissingShelvesDecodesEmpty() throws {
        XCTAssertEqual(try decode("{}").shelves, [])
    }

    func testDecodesMediaTypeLayerGroupedByKind() throws {
        let response = try decode("""
        {"generated_at": "2026-10-21T18:02:00.5Z", "media_type": "movie", "shelves": [
          {"key": "chart", "title": "Charts", "media_type": "movie", "count": 1, "lists": [\(FeaturedFixtures.cardJSON(id: 1, kind: "chart"))]},
          {"key": "canon", "title": "Critics & Canon", "media_type": "movie", "count": 1, "lists": [\(FeaturedFixtures.cardJSON(id: 2, kind: "canon"))]},
          {"key": "zine", "title": "Zines", "media_type": "movie", "lists": []}
        ]}
        """)

        XCTAssertEqual(response.mediaType, "movie")
        XCTAssertEqual(response.generatedAt, FeaturedFixtures.date("2026-10-21T18:02:00Z"))
        XCTAssertEqual(response.shelves.map(\.kind), [.chart, .canon, .unknown])
        XCTAssertEqual(response.shelves[1].title, "Critics & Canon")
    }

    func testDiskCacheRoundTripsAResponse() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = FeaturedShelvesDiskCache(directory: directory)
        let response = try decode(FeaturedFixtures.shelvesJSON)
        let savedAt = FeaturedFixtures.date("2026-10-21T18:05:00Z")

        cache.save(FeaturedShelvesCacheEntry(savedAt: savedAt, response: response), key: "landing")
        let entry = try XCTUnwrap(cache.load(key: "landing"))

        XCTAssertEqual(entry.savedAt, savedAt)
        XCTAssertEqual(entry.response, response)
        XCTAssertEqual(entry.response.shelves[1].lists.first?.previews.first?.note, "▲2")
        XCTAssertNil(cache.load(key: "movie"))
    }

    func testDatesParseWithAndWithoutFractionsOrZone() {
        let expected = FeaturedFixtures.date("2026-10-21T18:02:00Z")
        XCTAssertEqual(FeaturedDate.parse("2026-10-21T18:02:00Z"), expected)
        XCTAssertEqual(FeaturedDate.parse("2026-10-21T18:02:00.123456Z"), expected)
        XCTAssertEqual(FeaturedDate.parse("2026-10-21T20:02:00.1+02:00"), expected)
        XCTAssertEqual(FeaturedDate.parse("2026-10-21T18:02:00"), expected)
        XCTAssertNil(FeaturedDate.parse("soon"))
        XCTAssertNil(FeaturedDate.parse(nil))
    }

    func testPeopleCardsDecodePreviewPeopleAndStackThem() throws {
        let card = try decodeCard("""
        {"id": 60, "name": "Nobel Prize in Literature", "is_ranked": true, "items_count": 121, "media_types": ["book"],
         "kind": "award", "preview_items": [],
         "preview_people": [
           \(FeaturedFixtures.personJSON(entryId: 1, name: "Annie Ernaux", position: 1)),
           {"entry_id": 2, "name": "No id or source"},
           \(FeaturedFixtures.personJSON(entryId: 3, name: "Abdulrazak Gurnah", profileURL: nil, position: nil)),
           "oops"
         ]}
        """)

        XCTAssertEqual(card.previews, [])
        XCTAssertEqual(card.previewPeople.map(\.name), ["Annie Ernaux", "Abdulrazak Gurnah"])
        XCTAssertEqual(card.previewPeople.first?.profileUrl, "https://example.com/people/1.jpg")
        XCTAssertEqual(card.previewPeople.first?.ref, PersonRef(source: "openlibrary", id: "OL1A"))
        XCTAssertEqual(card.stackPeople.map(\.entryId), [1, 3])
        XCTAssertEqual(card.itemsCount, 121)
        // A ranked list's rank chip uses each person's position, else their place in the preview.
        XCTAssertEqual(card.rank(at: 0), 1)
        XCTAssertEqual(card.rank(at: 1), 2)
        XCTAssertNil(card.rank(at: 2))
        XCTAssertNil(FeaturedPersonPhoto.url(for: card.previewPeople[1]))
        XCTAssertEqual(card.tileArtworkRequests().count, 1)
    }

    func testMissingOrMalformedPreviewPeopleDecodeEmpty() throws {
        let missing = try decodeCard(#"{"id": 1, "name": "Films", "preview_items": []}"#)
        let malformed = try decodeCard(#"{"id": 2, "name": "People", "preview_people": "oops"}"#)

        XCTAssertEqual(missing.previewPeople, [])
        XCTAssertEqual(malformed.previewPeople, [])
        XCTAssertEqual(malformed.itemsCount, 0)
    }

    func testMediaPreviewsWinOverPeopleInTheStack() throws {
        let card = try decodeCard("""
        {"id": 4, "name": "Both", "preview_items": [\(FeaturedFixtures.mediaJSON(id: "1", type: "movie", title: "One"))],
         "preview_people": [\(FeaturedFixtures.personJSON(entryId: 1, name: "Someone", position: 1))]}
        """)

        XCTAssertEqual(card.previewPeople.count, 1)
        XCTAssertEqual(card.stackPeople, [])
    }

    func testDiskCacheRoundTripsPreviewPeople() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = FeaturedShelvesDiskCache(directory: directory)
        let response = try decode("""
        {"shelves": [{"key": "book", "title": "Books", "media_type": "book", "lists": [
          {"id": 60, "name": "Nobel", "media_types": ["book"], "items_count": 2, "preview_items": [],
           "preview_people": [\(FeaturedFixtures.personJSON(entryId: 1, name: "Annie Ernaux", position: 1))]}
        ]}]}
        """)

        cache.save(FeaturedShelvesCacheEntry(savedAt: .now, response: response), key: "landing")
        let entry = try XCTUnwrap(cache.load(key: "landing"))

        XCTAssertEqual(entry.response, response)
        XCTAssertEqual(entry.response.allCards.first?.previewPeople.map(\.name), ["Annie Ernaux"])
    }

    func testListDetailDecodesItsFeaturedBlock() throws {
        let detail = try decodeDetail(featured: """
        {"id": 12, "name": "Netflix Top 10: Films", "slug": "netflix-top-10-films", "is_ranked": true, "items_count": 10,
         "media_types": ["movie"], "kind": "chart", "source": {"name": "Netflix", "url": "https://www.netflix.com/tudum/top10"},
         "cadence": "weekly", "period_label": "Week of Oct 13", "content_changed_at": "2026-10-21T16:00:00.123456Z",
         "last_synced_at": "2026-10-21T17:30:00Z",
         "live": {"status": "live", "label": "7 of 23 announced", "starts_at": "2026-10-21T15:00:00Z", "ends_at": null}}
        """)

        let featured = try XCTUnwrap(detail.featured)
        XCTAssertEqual(featured.kind, .chart)
        XCTAssertEqual(featured.source?.linkURL, URL(string: "https://www.netflix.com/tudum/top10"))
        XCTAssertEqual(
            featured.metadataLine(now: FeaturedFixtures.date("2026-10-21T18:00:00Z")),
            "CHART · Netflix · Updated 2h ago · Refreshes weekly"
        )
        XCTAssertEqual(featured.live?.isLive(at: FeaturedFixtures.date("2026-10-21T18:00:00Z")), true)
        XCTAssertEqual(featured.previews, [])
        XCTAssertEqual(detail.name, "Netflix Top 10: Films")
    }

    func testListDetailWithoutOrWithABadFeaturedBlockStillDecodes() throws {
        XCTAssertNil(try decodeDetail(featured: nil).featured)
        XCTAssertNil(try decodeDetail(featured: "null").featured)
        XCTAssertNil(try decodeDetail(featured: #"{"name": "No id"}"#).featured)
        XCTAssertNil(try decodeDetail(featured: #""oops""#).featured)
    }

    private func decodeDetail(featured: String?) throws -> CustomListDetail {
        let featuredJSON = featured.map { #", "featured": \#($0)"# } ?? ""
        let json = """
        {"id": 12, "name": "Netflix Top 10: Films", "slug": "netflix-top-10-films", "description": "", "visibility": "public",
         "is_ranked": true, "list_type": "media",
         "owner": {"id": 1, "username": "Spine", "display_name": "Spine", "avatar_url": null},
         "items_count": 0, "like_count": 0, "items": []\(featuredJSON)}
        """
        return try JSONDecoder.api.decode(CustomListDetail.self, from: Data(json.utf8))
    }

    private func decode(_ json: String) throws -> FeaturedShelvesResponse {
        try JSONDecoder.api.decode(FeaturedShelvesResponse.self, from: Data(json.utf8))
    }

    private func decodeCard(_ json: String) throws -> FeaturedListCard {
        try JSONDecoder.api.decode(FeaturedListCard.self, from: Data(json.utf8))
    }
}

@MainActor
final class FeaturedLegacyFallbackTests: XCTestCase {
    override func tearDown() {
        FeaturedURLProtocol.handler = nil
        KeychainTokenStore.shared.clear()
        super.tearDown()
    }

    func testShelvesFallBackToLegacyEndpointOn404() async throws {
        var paths: [String] = []
        FeaturedURLProtocol.handler = { request in
            let path = FeaturedURLProtocol.path(of: request)
            paths.append(path)
            if path.hasSuffix("/lists/featured/shelves/") {
                return FeaturedURLProtocol.response(request, status: 404, body: #"{"detail":"Not found."}"#)
            }
            if path.hasSuffix("/lists/featured/") {
                return FeaturedURLProtocol.response(request, status: 200, body: FeaturedFixtures.legacyListsJSON)
            }
            XCTFail("Unexpected request \(path)")
            return FeaturedURLProtocol.response(request, status: 500)
        }

        let response = try await makeRepository().featuredShelves()

        XCTAssertEqual(paths, ["/api/v1/lists/featured/shelves/", "/api/v1/lists/featured/"])
        XCTAssertTrue(response.isLegacy)
        // Movies and TV each get the two-type list; the three-type list goes to Across Media; people lists are left out.
        XCTAssertEqual(response.shelves.map(\.key), ["movie", "tv", "book", "mixed"])
        XCTAssertEqual(response.shelves[0].lists.map(\.name), ["Top 500 Films", "Screen Picks"])
        XCTAssertEqual(response.shelves[1].lists.map(\.name), ["Screen Picks"])
        XCTAssertEqual(response.shelves[2].lists.map(\.name), ["NYT Fiction"])
        XCTAssertEqual(response.shelves[3].title, "Across Media")
        XCTAssertEqual(response.shelves[3].lists.map(\.name), ["Everything"])
        XCTAssertEqual(response.shelves[1].lists.first?.mediaTypes, ["movie", "tv"])
        XCTAssertTrue(response.shelves.allSatisfy { $0.seeAll == nil })
    }

    func testMediaTypeLayerFallsBackToLegacyEndpointOn404() async throws {
        var shelvesQuery: [URLQueryItem] = []
        FeaturedURLProtocol.handler = { request in
            let path = FeaturedURLProtocol.path(of: request)
            if path.hasSuffix("/lists/featured/shelves/") {
                shelvesQuery = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
                return FeaturedURLProtocol.response(request, status: 404)
            }
            return FeaturedURLProtocol.response(request, status: 200, body: FeaturedFixtures.legacyListsJSON)
        }

        let response = try await makeRepository().featuredShelves(mediaType: "tv")

        XCTAssertEqual(shelvesQuery, [URLQueryItem(name: "media_type", value: "tv")])
        XCTAssertTrue(response.isLegacy)
        XCTAssertEqual(response.mediaType, "tv")
        XCTAssertEqual(response.shelves.map(\.kind), [.unknown])
        XCTAssertEqual(response.shelves.first?.lists.map(\.name), ["Screen Picks"])
    }

    func testServerErrorsOtherThan404DoNotFallBack() async {
        var requests = 0
        FeaturedURLProtocol.handler = { request in
            requests += 1
            return FeaturedURLProtocol.response(request, status: 503)
        }

        do {
            _ = try await makeRepository().featuredShelves()
            XCTFail("Expected an error")
        } catch APIError.httpStatus(let status, _) {
            XCTAssertEqual(status, 503)
        } catch {
            XCTFail("Unexpected error \(error)")
        }
        XCTAssertEqual(requests, 1)
    }

    func testShelvesEndpointIsUsedWhenItExists() async throws {
        FeaturedURLProtocol.handler = { request in
            XCTAssertEqual(FeaturedURLProtocol.path(of: request), "/api/v1/lists/featured/shelves/")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer featured-access")
            return FeaturedURLProtocol.response(request, status: 200, body: FeaturedFixtures.shelvesJSON)
        }

        let response = try await makeRepository().featuredShelves()

        XCTAssertFalse(response.isLegacy)
        XCTAssertEqual(response.shelves.map(\.key), ["now", "movie", "mixed"])
    }

    private func makeRepository() -> APIListRepository {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FeaturedURLProtocol.self]
        KeychainTokenStore.shared.accessToken = "featured-access"
        return APIListRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: configuration)
        ))
    }
}

@MainActor
final class FeaturedPresentationTests: XCTestCase {
    private let now = FeaturedFixtures.date("2026-10-21T18:00:00Z")

    func testBadgePriorityIsLiveThenRecentThenPeriodThenKind() {
        let live = FeaturedLiveState(status: .live, label: "7 of 23 announced")
        let recent = now.addingTimeInterval(-2 * 3600)
        let old = now.addingTimeInterval(-8 * 86400)

        XCTAssertEqual(
            FeaturedBadge.resolve(for: card(live: live, changed: recent, period: "2026"), now: now),
            .live(label: "7 of 23 announced")
        )
        XCTAssertEqual(FeaturedBadge.resolve(for: card(changed: recent, period: "2026"), now: now), .updated(recent))
        XCTAssertEqual(FeaturedBadge.resolve(for: card(changed: old, period: "2026"), now: now), .period("2026"))
        XCTAssertEqual(FeaturedBadge.resolve(for: card(kind: .award), now: now), .kind(.award))
    }

    func testUpcomingWindowCountsAsLiveOnceItStarts() {
        let window = FeaturedLiveState(
            status: .upcoming,
            startsAt: now.addingTimeInterval(-60),
            endsAt: now.addingTimeInterval(3600)
        )
        XCTAssertTrue(window.isLive(at: now))
        XCTAssertFalse(window.isLive(at: now.addingTimeInterval(-120)))
        XCTAssertFalse(window.isLive(at: now.addingTimeInterval(7200)))
        XCTAssertFalse(FeaturedLiveState(status: .done).isLive(at: now))
    }

    func testRelativeTimeAndUpdatedLabel() {
        XCTAssertEqual(FeaturedRelativeTime.short(from: now.addingTimeInterval(-30), now: now), "now")
        XCTAssertEqual(FeaturedRelativeTime.short(from: now.addingTimeInterval(-12 * 60), now: now), "12m")
        XCTAssertEqual(FeaturedRelativeTime.short(from: now.addingTimeInterval(-2 * 3600), now: now), "2h")
        XCTAssertEqual(FeaturedRelativeTime.short(from: now.addingTimeInterval(-3 * 86400), now: now), "3d")
        XCTAssertEqual(FeaturedRelativeTime.short(from: now.addingTimeInterval(-15 * 86400), now: now), "2w")
        XCTAssertEqual(FeaturedRelativeTime.updatedLabel(from: now.addingTimeInterval(-2 * 3600), now: now), "Updated 2h ago")
        XCTAssertEqual(FeaturedRelativeTime.updatedLabel(from: now, now: now), "Updated just now")
    }

    func testMovementFromNote() {
        XCTAssertEqual(FeaturedMovement(note: "▲2"), .up(2))
        XCTAssertEqual(FeaturedMovement(note: "Week 3 · ▼11"), .down(11))
        XCTAssertEqual(FeaturedMovement(note: "new"), .new)
        XCTAssertNil(FeaturedMovement(note: "Best Picture · 2027"))
        XCTAssertNil(FeaturedMovement(note: nil))
    }

    func testCountLabelAndMetadataLine() {
        let netflix = FeaturedListCard(
            id: 1,
            name: "Netflix Top 10: Films",
            itemsCount: 10,
            mediaTypes: ["movie"],
            kind: .chart,
            source: FeaturedSource(name: "Netflix"),
            cadence: .weekly,
            contentChangedAt: now.addingTimeInterval(-2 * 3600)
        )
        XCTAssertEqual(netflix.countLabel, "10 films · Weekly")
        XCTAssertEqual(netflix.metadataLine(now: now), "CHART · Netflix · Updated 2h ago · Refreshes weekly")

        let mixed = FeaturedListCard(id: 2, name: "Mixed", itemsCount: 1, mediaTypes: ["movie", "book", "game"], cadence: .static)
        XCTAssertEqual(mixed.countLabel, "1 title")
        XCTAssertEqual(mixed.metadataLine(now: now), "")
    }

    func testVisibleShelvesHideDisabledMediaTypes() {
        let movie = FeaturedListCard(id: 1, name: "Films", itemsCount: 1, mediaTypes: ["movie"])
        let music = FeaturedListCard(id: 2, name: "Albums", itemsCount: 1, mediaTypes: ["music"])
        let mixed = FeaturedListCard(id: 3, name: "All", itemsCount: 1, mediaTypes: ["music", "book", "game"])
        let response = FeaturedShelvesResponse(shelves: [
            FeaturedShelf(key: "now", title: "Now", mediaType: nil, lists: [movie, music]),
            FeaturedShelf(key: "movie", title: "Movies", mediaType: "movie", lists: [movie]),
            FeaturedShelf(key: "music", title: "Music", mediaType: "music", lists: [music]),
            FeaturedShelf(key: "mixed", title: "Across Media", mediaType: "mixed", lists: [mixed]),
        ])

        let visible = response.visibleShelves(enabledMediaTypes: ["movie", "book"])

        XCTAssertEqual(visible.map(\.key), ["now", "movie", "mixed"])
        XCTAssertEqual(visible[0].lists.map(\.id), [1])
        XCTAssertEqual(visible[0].count, 1)
        XCTAssertEqual(visible[2].lists.map(\.id), [3])
        XCTAssertEqual(response.visibleShelves(enabledMediaTypes: ["tv"]).map(\.key), [])
    }

    func testSummaryLine() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let response = FeaturedShelvesResponse(shelves: [
            FeaturedShelf(key: "chart", title: "Charts", mediaType: "movie", count: 40, lists: [
                card(live: FeaturedLiveState(status: .live), changed: now.addingTimeInterval(-3600)),
            ]),
            FeaturedShelf(key: "canon", title: "Critics & Canon", mediaType: "movie", count: 2, lists: [card(kind: .canon)]),
        ])

        XCTAssertEqual(response.summaryLine(now: now, calendar: calendar), "42 lists · 1 live · updated today")
    }

    private func card(
        live: FeaturedLiveState? = nil,
        changed: Date? = nil,
        period: String? = nil,
        kind: FeaturedKind = .chart
    ) -> FeaturedListCard {
        FeaturedListCard(
            id: Int.random(in: 1 ... 1_000_000),
            name: "List",
            itemsCount: 10,
            mediaTypes: ["movie"],
            kind: kind,
            periodLabel: period,
            contentChangedAt: changed,
            live: live
        )
    }
}

@MainActor
final class FeaturedListsStoreTests: XCTestCase {
    private var clock = FeaturedFixtures.date("2026-10-21T18:00:00Z")

    func testShowsCachedLandingAtOnceAndSkipsTheNetworkWhileFresh() async {
        let cache = FeaturedMemoryCache()
        cache.entries["landing"] = FeaturedShelvesCacheEntry(
            savedAt: clock.addingTimeInterval(-5 * 60),
            response: FeaturedFixtures.response(name: "Cached")
        )
        let repository = FeaturedShelvesRepository()
        let store = makeStore(repository: repository, cache: cache)

        await store.load(mediaType: nil)

        XCTAssertEqual(store.layer(mediaType: nil).response?.allCards.map(\.name), ["Cached"])
        XCTAssertEqual(repository.landingCalls, 0)
    }

    func testRevalidatesStaleCacheAndSavesTheResult() async {
        let cache = FeaturedMemoryCache()
        cache.entries["landing"] = FeaturedShelvesCacheEntry(
            savedAt: clock.addingTimeInterval(-11 * 60),
            response: FeaturedFixtures.response(name: "Cached")
        )
        let repository = FeaturedShelvesRepository(landing: [.success(FeaturedFixtures.response(name: "Fresh"))])
        let store = makeStore(repository: repository, cache: cache)

        await store.load(mediaType: nil)

        XCTAssertEqual(repository.landingCalls, 1)
        XCTAssertEqual(store.layer(mediaType: nil).response?.allCards.map(\.name), ["Fresh"])
        XCTAssertEqual(store.layer(mediaType: nil).loadedAt, clock)
        XCTAssertEqual(cache.entries["landing"]?.response.allCards.map(\.name), ["Fresh"])
    }

    func testForceRefetchesFreshData() async {
        let repository = FeaturedShelvesRepository(landing: [
            .success(FeaturedFixtures.response(name: "First")),
            .success(FeaturedFixtures.response(name: "Second")),
        ])
        let store = makeStore(repository: repository)

        await store.load(mediaType: nil)
        await store.load(mediaType: nil)
        XCTAssertEqual(repository.landingCalls, 1)

        await store.load(mediaType: nil, force: true)
        XCTAssertEqual(repository.landingCalls, 2)
        XCTAssertEqual(store.layer(mediaType: nil).response?.allCards.map(\.name), ["Second"])
    }

    func testFailureKeepsWhatWasShownAndReportsTheError() async {
        let repository = FeaturedShelvesRepository(landing: [
            .success(FeaturedFixtures.response(name: "Shown")),
            .failure(APIError.httpStatus(503, nil)),
        ])
        let store = makeStore(repository: repository)

        await store.load(mediaType: nil)
        await store.load(mediaType: nil, force: true)

        let layer = store.layer(mediaType: nil)
        XCTAssertEqual(layer.response?.allCards.map(\.name), ["Shown"])
        XCTAssertNotNil(layer.errorMessage)
        XCTAssertFalse(layer.isLoading)
    }

    func testUnauthorizedSignsOut() async {
        var unauthorized = 0
        let repository = FeaturedShelvesRepository(landing: [.failure(APIError.unauthorized)])
        let store = FeaturedListsStore(
            repository: repository,
            cache: FeaturedMemoryCache(),
            now: { [unowned self] in clock },
            onUnauthorized: { unauthorized += 1 }
        )

        await store.load(mediaType: nil)

        XCTAssertEqual(unauthorized, 1)
    }

    func testMediaTypeLayersLoadSeparately() async {
        let repository = FeaturedShelvesRepository(byType: ["movie": FeaturedFixtures.response(name: "Movie Layer")])
        let store = makeStore(repository: repository)

        await store.load(mediaType: "movie")

        XCTAssertEqual(repository.mediaTypeCalls, ["movie"])
        XCTAssertEqual(store.layer(mediaType: "movie").response?.allCards.map(\.name), ["Movie Layer"])
        XCTAssertNil(store.layer(mediaType: nil).response)
    }

    func testConcurrentLoadsShareOneRequest() async {
        let repository = FeaturedShelvesRepository(landing: [.success(FeaturedFixtures.response(name: "Once"))])
        let store = makeStore(repository: repository)

        async let first: Void = store.load(mediaType: nil)
        async let second: Void = store.load(mediaType: nil)
        _ = await (first, second)

        XCTAssertEqual(repository.landingCalls, 1)
    }

    func testPollsOnlyWhileAListIsLive() async {
        let live = FeaturedFixtures.response(name: "Oscars", live: FeaturedLiveState(status: .live, label: "1 of 23"))
        let repository = FeaturedShelvesRepository(landing: [
            .success(live),
            .success(live),
            .success(FeaturedFixtures.response(name: "Oscars", live: FeaturedLiveState(status: .done))),
        ])
        var sleeps: [Duration] = []
        let store = FeaturedListsStore(
            repository: repository,
            cache: FeaturedMemoryCache(),
            now: { [unowned self] in clock },
            sleep: { sleeps.append($0) }
        )

        await store.load(mediaType: nil)
        XCTAssertTrue(store.hasLiveList(mediaType: nil))
        await store.pollWhileLive(mediaType: nil)

        XCTAssertEqual(repository.landingCalls, 3)
        XCTAssertEqual(sleeps, [.seconds(60), .seconds(60)])
        XCTAssertFalse(store.hasLiveList(mediaType: nil))
    }

    func testDoesNotPollWithoutALiveList() async {
        let repository = FeaturedShelvesRepository(landing: [.success(FeaturedFixtures.response(name: "Quiet"))])
        var sleeps = 0
        let store = FeaturedListsStore(
            repository: repository,
            cache: FeaturedMemoryCache(),
            now: { [unowned self] in clock },
            sleep: { _ in sleeps += 1 }
        )

        await store.load(mediaType: nil)
        await store.pollWhileLive(mediaType: nil)

        XCTAssertEqual(sleeps, 0)
        XCTAssertEqual(repository.landingCalls, 1)
    }

    func testRepositoryWithoutShelvesEndpointGroupsLegacyFeaturedLists() async throws {
        let repository = LegacyOnlyListRepository()

        let response = try await repository.featuredShelves()

        XCTAssertTrue(response.isLegacy)
        XCTAssertEqual(response.shelves.map(\.key), ["book"])
    }

    private func makeStore(
        repository: FeaturedShelvesRepository,
        cache: FeaturedMemoryCache = FeaturedMemoryCache()
    ) -> FeaturedListsStore {
        FeaturedListsStore(repository: repository, cache: cache, now: { [unowned self] in clock })
    }
}

// MARK: - Fixtures and fakes

// Featured lists' rows carry their entry's note ("Winner · Best Picture", "▲2 · $12.4M").
@MainActor
final class FeaturedEntryNoteTests: XCTestCase {
    func testListItemsDecodeTheirNoteTolerantly() throws {
        let json = """
        {"count": 4, "next": null, "previous": null, "results": [
          \(FeaturedFixtures.mediaJSON(id: "1", type: "movie", title: "One", position: 1, note: "▲2 · $12.4M")),
          {"ref": {"item_id": 2, "source": "tmdb", "media_type": "movie", "media_id": "2"}, "title": "Two", "position": 2, "note": null},
          {"ref": {"item_id": 3, "source": "tmdb", "media_type": "movie", "media_id": "3"}, "title": "Three", "position": 3},
          {"ref": {"item_id": 4, "source": "tmdb", "media_type": "movie", "media_id": "4"}, "title": "Four", "position": 4, "note": 7}
        ]}
        """
        let page = try JSONDecoder.api.decode(PagedResponse<MediaSummary>.self, from: Data(json.utf8))

        XCTAssertEqual(page.results.map(\.title), ["One", "Two", "Three", "Four"])
        XCTAssertEqual(page.results.map(\.note), ["▲2 · $12.4M", nil, nil, nil])
    }

    func testPeopleDecodeTheirNoteTolerantly() throws {
        func person(_ note: String) throws -> PersonListEntry {
            let json = """
            {"entry_id": 1, "id": "OL1A", "source": "openlibrary", "name": "Annie Ernaux", "profile_url": null,
             "known_for_department": null, "position": 1, "date_added": "2026-10-21T18:00:00Z"\(note)}
            """
            return try JSONDecoder.api.decode(PersonListEntry.self, from: Data(json.utf8))
        }

        XCTAssertEqual(try person(#", "note": "2022""#).note, "2022")
        XCTAssertNil(try person("").note)
        XCTAssertNil(try person(#", "note": ["2022"]"#).note)
    }

    func testNoteTextIgnoresBlankNotes() {
        XCTAssertEqual(FeaturedEntryNote.text("  Nominee · Fiction "), "Nominee · Fiction")
        XCTAssertNil(FeaturedEntryNote.text("   "))
        XCTAssertNil(FeaturedEntryNote.text(nil))
    }

    func testListDetailKeepsEachItemsNote() async throws {
        let repository = FeaturedNotesListRepository(items: [
            MediaSummary(ref: try ref(1), title: "Winner", position: 1, note: "Winner · Best Picture"),
            MediaSummary(ref: try ref(2), title: "Nominee", position: 2),
        ])
        let viewModel = ProfileListDetailViewModel(
            listId: 12,
            listRepository: repository,
            filterOptionsRepository: FeaturedNotesFilterRepository(),
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.displayedItems.map(\.note), ["Winner · Best Picture", nil])
        XCTAssertEqual(viewModel.list?.items.map(\.note), ["Winner · Best Picture", nil])
    }

    private func ref(_ itemId: Int) throws -> MediaRef {
        let json = #"{"item_id": \#(itemId), "source": "tmdb", "media_type": "movie", "media_id": "\#(itemId)"}"#
        return try JSONDecoder.api.decode(MediaRef.self, from: Data(json.utf8))
    }
}

@MainActor
private final class FeaturedNotesListRepository: ListRepository {
    let items: [MediaSummary]

    init(items: [MediaSummary]) {
        self.items = items
    }

    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary] { [] }
    func peopleLists(membershipFor ref: PersonRef) async throws -> [CustomListSummary] { [] }

    func detail(id: Int) async throws -> CustomListDetail {
        let json = """
        {"id": 12, "name": "Oscars", "slug": "oscars", "description": "", "visibility": "public",
         "is_ranked": true, "list_type": "media",
         "owner": {"id": 1, "username": "Spine", "display_name": "Spine", "avatar_url": null},
         "items_count": \(items.count), "like_count": 0, "items": []}
        """
        return try JSONDecoder.api.decode(CustomListDetail.self, from: Data(json.utf8))
    }

    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary> {
        PagedResponse(count: items.count, next: nil, previous: nil, results: items)
    }

    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary { fatalError("unused") }
    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail { fatalError("unused") }
    func delete(id: Int) async throws {}
    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary { fatalError("unused") }
    func removeItem(listId: Int, itemId: Int) async throws {}
    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail { fatalError("unused") }

    func people(listId: Int, page: String?) async throws -> PagedResponse<PersonListEntry> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func addPerson(listId: Int, ref: PersonRef) async throws -> PersonListEntry { fatalError("unused") }
    func removePerson(listId: Int, entryId: Int) async throws {}
    func reorderPeople(listId: Int, entryIds: [Int]) async throws -> CustomListDetail { fatalError("unused") }
}

@MainActor
private final class FeaturedNotesFilterRepository: FilterOptionsRepository {
    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse {
        .empty
    }
}

@MainActor
private enum FeaturedFixtures {
    static func date(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }

    static func mediaJSON(id: String, type: String, title: String, position: Int? = nil, note: String? = nil) -> String {
        let noteJSON = note.map { #", "note": "\#($0)""# } ?? ""
        return """
        {"ref": {"item_id": null, "source": "tmdb", "media_type": "\(type)", "media_id": "\(id)", "season_number": null, "episode_number": null},
         "title": "\(title)", "poster_url": "https://image.tmdb.org/t/p/w342/\(id).jpg", "position": \(position.map(String.init) ?? "null")\(noteJSON)}
        """
    }

    /// A person as `person_list_item_payload` sends them.
    static func personJSON(
        entryId: Int,
        name: String,
        profileURL: String? = "https://example.com/people/1.jpg",
        position: Int?
    ) -> String {
        """
        {"entry_id": \(entryId), "id": "OL\(entryId)A", "source": "openlibrary", "name": "\(name)",
         "profile_url": \(profileURL.map { "\"\($0)\"" } ?? "null"), "known_for_department": null,
         "position": \(position.map(String.init) ?? "null"), "date_added": "2026-10-21T18:00:00.123456Z", "completion": null}
        """
    }

    static func cardJSON(id: Int, kind: String) -> String {
        #"{"id": \#(id), "name": "List \#(id)", "kind": "\#(kind)", "media_types": ["movie"], "items_count": 3, "preview_items": []}"#
    }

    static let shelvesJSON = """
    {
      "generated_at": "2026-10-21T18:02:00Z",
      "shelves": [
        {"key": "now", "title": "Now", "media_type": null, "count": 1, "live_count": 1, "kind_counts": {},
         "lists": [
           {"id": 30, "name": "Oscars 2027", "slug": "oscars-2027", "description": "Winners as they're announced.",
            "is_ranked": false, "items_count": 7, "media_types": ["movie"], "kind": "award",
            "source": {"name": "The Academy", "url": "https://www.oscars.org"},
            "cadence": "live", "period_label": "2027", "content_changed_at": "2026-10-21T17:58:00.000001Z",
            "last_synced_at": "2026-10-21T18:01:00Z",
            "live": {"status": "live", "label": "7 of 23 announced", "starts_at": "2026-10-21T17:00:00Z", "ends_at": "2026-10-21T21:00:00Z"},
            "preview_items": [\(mediaJSON(id: "500", type: "movie", title: "Winner"))]}
         ],
         "see_all": null},
        {"key": "movie", "title": "Movies", "media_type": "movie", "count": 42, "live_count": 0,
         "kind_counts": {"chart": 6, "award": 9, "canon": 14, "community": 10, "curator": 3},
         "lists": [
           {"id": 12, "name": "Netflix Top 10: Films", "slug": "netflix-top-10-films", "description": "This week's most watched films.",
            "is_ranked": true, "items_count": 10, "media_types": ["movie"], "kind": "chart",
            "source": {"name": "Netflix", "url": "https://www.netflix.com/tudum/top10"},
            "cadence": "weekly", "period_label": "Week of Oct 13",
            "content_changed_at": "2026-10-21T16:00:00.123456Z", "last_synced_at": "2026-10-21T17:30:00.654321Z",
            "live": null,
            "preview_items": [
              \(mediaJSON(id: "1", type: "movie", title: "One", position: 1, note: "▲2")),
              \(mediaJSON(id: "2", type: "movie", title: "Two", position: 2)),
              \(mediaJSON(id: "3", type: "movie", title: "Three", position: 3))
            ]}
         ],
         "see_all": "/api/v1/lists/featured/shelves/?media_type=movie"},
        {"key": "mixed", "title": "Across Media", "media_type": "mixed", "count": 1, "live_count": 0, "kind_counts": {"award": 1},
         "lists": [
           {"id": 40, "name": "Pulitzer Winners", "is_ranked": false, "items_count": 3, "media_types": ["book", "music", "tv"],
            "kind": "award", "cadence": "yearly", "live": null, "preview_items": []}
         ],
         "see_all": "/api/v1/lists/featured/shelves/?media_type=mixed"}
      ]
    }
    """

    static let legacyListsJSON: String = {
        func list(_ id: Int, _ name: String, listType: String = "media", previews: [String]) -> String {
            """
            {"id": \(id), "name": "\(name)", "slug": "list-\(id)", "description": "", "visibility": "public",
             "is_ranked": true, "list_type": "\(listType)",
             "owner": {"id": 1, "username": "Spine", "display_name": "Spine", "avatar_url": null},
             "image_url": null, "preview_items": [\(previews.joined(separator: ","))], "preview_people": [],
             "items_count": \(previews.count), "updated_at": null, "like_count": 0}
            """
        }
        let lists = [
            list(1, "Top 500 Films", previews: [mediaJSON(id: "1", type: "movie", title: "A")]),
            list(2, "Screen Picks", previews: [mediaJSON(id: "2", type: "movie", title: "B"), mediaJSON(id: "3", type: "episode", title: "C")]),
            list(3, "NYT Fiction", previews: [mediaJSON(id: "4", type: "book", title: "D")]),
            list(4, "Everything", previews: [
                mediaJSON(id: "5", type: "movie", title: "E"),
                mediaJSON(id: "6", type: "book", title: "F"),
                mediaJSON(id: "7", type: "game", title: "G"),
            ]),
            list(5, "Directors", listType: "people", previews: []),
        ]
        return #"{"count": 5, "next": null, "previous": null, "results": [\#(lists.joined(separator: ","))]}"#
    }()

    static func response(name: String, live: FeaturedLiveState? = nil) -> FeaturedShelvesResponse {
        FeaturedShelvesResponse(shelves: [
            FeaturedShelf(key: "movie", title: "Movies", mediaType: "movie", lists: [
                FeaturedListCard(id: 1, name: name, itemsCount: 1, mediaTypes: ["movie"], live: live),
            ]),
        ])
    }
}

private final class FeaturedMemoryCache: FeaturedShelvesCaching {
    var entries: [String: FeaturedShelvesCacheEntry] = [:]

    func load(key: String) -> FeaturedShelvesCacheEntry? {
        entries[key]
    }

    func save(_ entry: FeaturedShelvesCacheEntry, key: String) {
        entries[key] = entry
    }
}

private final class FeaturedShelvesRepository: ListRepository {
    var landingCalls = 0
    var mediaTypeCalls: [String] = []
    private var landing: [Result<FeaturedShelvesResponse, Error>]
    private let byType: [String: FeaturedShelvesResponse]

    init(landing: [Result<FeaturedShelvesResponse, Error>] = [], byType: [String: FeaturedShelvesResponse] = [:]) {
        self.landing = landing
        self.byType = byType
    }

    func featuredShelves() async throws -> FeaturedShelvesResponse {
        landingCalls += 1
        await Task.yield()
        guard !landing.isEmpty else { throw APIError.invalidResponse }
        return try landing.removeFirst().get()
    }

    func featuredShelves(mediaType: String) async throws -> FeaturedShelvesResponse {
        mediaTypeCalls.append(mediaType)
        guard let response = byType[mediaType] else { throw APIError.invalidResponse }
        return response
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

/// A repository that only knows the legacy endpoint, so it uses the protocol's default shelves.
private struct LegacyOnlyListRepository: ListRepository {
    func featured() async throws -> [CustomListSummary] {
        try JSONDecoder.api.decode(PagedResponse<CustomListSummary>.self, from: Data(FeaturedFixtures.legacyListsJSON.utf8))
            .results
            .filter { $0.name == "NYT Fiction" }
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

private final class FeaturedURLProtocol: URLProtocol {
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

    /// The request's path with its trailing slash, which `URL.path` drops.
    static func path(of request: URLRequest) -> String {
        request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.path } ?? ""
    }

    static func response(_ request: URLRequest, status: Int, body: String = "") -> (HTTPURLResponse, Data) {
        (
            HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!,
            Data(body.utf8)
        )
    }
}
