import XCTest
@testable import Spine

@MainActor
final class BookTrackingContractTests: XCTestCase {
    override func tearDown() {
        BookTrackingURLProtocol.handler = nil
        BookTrackingURLProtocol.lastRequest = nil
        super.tearDown()
    }

    func testTrackingStateDecodesCurrentJourneyHistoryAndUndatedRead() throws {
        let state = try JSONDecoder.api.decode(TrackingState.self, from: Self.trackingResponse)

        XCTAssertEqual(state.status, "In progress")
        XCTAssertEqual(state.book?.currentJourney?.id, 41)
        XCTAssertEqual(state.book?.currentJourney?.progress?.value, 120)
        XCTAssertEqual(state.book?.readingHistory.map(\.status), ["Completed", "Dropped"])
        XCTAssertEqual(state.book?.readingHistory.first?.completionDiaryEntryId, 901)
        XCTAssertEqual(state.book?.undatedRead?.id, "undated")
        XCTAssertEqual(state.book?.lifetimeReadCount, 3)
        XCTAssertTrue(state.book?.isRereading == true)
        XCTAssertFalse(state.book?.canRemoveTracking == true)
        XCTAssertEqual(state.book?.actionReasons["planning"], "Finish the active journey first.")
    }

    func testBookActionRequestEncodesStableMutationAndCalendarDates() throws {
        let mutation = try XCTUnwrap(UUID(uuidString: "6A38BC71-DC52-4FCA-A0C5-206946FB29B4"))
        let data = try JSONEncoder.api.encode(BookActionRequest(
            mutationId: mutation,
            startDate: "2026-07-01",
            endDate: "2026-07-20"
        ))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: String])

        XCTAssertEqual(json["mutation_id"], mutation.uuidString)
        XCTAssertEqual(json["start_date"], "2026-07-01")
        XCTAssertEqual(json["end_date"], "2026-07-20")
    }

    func testCompletionRequestUsesFiveStarRatingAndJourneyLinkage() throws {
        let mutation = UUID()
        let request = BookCompletionWriteRequest(
            journeyId: 41,
            completionDate: "2026-07-20",
            rating: MediaLogViewModel.ratingDecimal(for: 9, mediaType: "book"),
            review: "Great ending",
            reviewTitle: "A favorite",
            liked: true,
            isRewatch: true,
            containsSpoilers: true,
            tags: ["favorite"],
            mutationId: mutation
        )
        let data = try JSONEncoder.api.encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["journey_id"] as? Int, 41)
        XCTAssertEqual(json["completion_date"] as? String, "2026-07-20")
        XCTAssertEqual(json["rating"] as? Double, 4.5)
        XCTAssertEqual(json["mutation_id"] as? String, mutation.uuidString)
        XCTAssertEqual(json["is_rewatch"] as? Bool, true)
    }

    func testRepositoryUsesExactBookActionAndJourneyRoutes() async throws {
        let repository = Self.repository()

        _ = try await repository.performBookAction(
            source: "openlibrary",
            mediaId: "OL1M",
            action: "pause",
            request: BookActionRequest()
        )
        XCTAssertEqual(BookTrackingURLProtocol.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(
            BookTrackingURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/tracking/openlibrary/book/OL1M/actions/pause/"
        )

        _ = try await repository.updateBookJourney(
            source: "openlibrary",
            mediaId: "OL1M",
            journeyId: 41,
            request: BookJourneyWriteRequest(startDate: "2026-07-01", endDate: nil)
        )
        XCTAssertEqual(BookTrackingURLProtocol.lastRequest?.httpMethod, "PATCH")
        XCTAssertEqual(
            BookTrackingURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/tracking/openlibrary/book/OL1M/journeys/41/"
        )

        _ = try await repository.deleteBookJourney(
            source: "openlibrary",
            mediaId: "OL1M",
            journeyId: 41
        )
        XCTAssertEqual(BookTrackingURLProtocol.lastRequest?.httpMethod, "DELETE")
    }

    func testRepositoryUndoReadAcceptsEmptyUntrackedResponse() async throws {
        let repository = Self.repository()
        Self.respondWithNoContent()

        let state = try await repository.undoBookRead(source: "openlibrary", mediaId: "OL1M")

        XCTAssertNil(state)
        XCTAssertEqual(BookTrackingURLProtocol.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(
            BookTrackingURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/tracking/openlibrary/book/OL1M/actions/undo_read/"
        )
    }

    func testRepositoryJourneyDeleteAcceptsEmptyUntrackedResponse() async throws {
        let repository = Self.repository()
        Self.respondWithNoContent()

        let deleted = try await repository.deleteBookJourney(source: "openlibrary", mediaId: "OL1M", journeyId: 41)
        XCTAssertNil(deleted)
        XCTAssertEqual(BookTrackingURLProtocol.lastRequest?.httpMethod, "DELETE")

        let acted = try await repository.performBookAction(
            source: "openlibrary",
            mediaId: "OL1M",
            action: "delete_undated_read",
            request: BookActionRequest()
        )
        XCTAssertNil(acted)
    }

    func testRepositoryJourneyDeleteDecodesTheRemainingState() async throws {
        let deleted = try await Self.repository().deleteBookJourney(source: "openlibrary", mediaId: "OL1M", journeyId: 40)
        XCTAssertEqual(deleted?.book?.currentJourney?.id, 41)
    }

    private static func respondWithNoContent() {
        BookTrackingURLProtocol.handler = { request in
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 204,
                httpVersion: nil,
                headerFields: nil
            ))
            return (response, Data())
        }
    }

    func testRepositoryCompletionIsAtomicAndDecodesDiaryLinkage() async throws {
        let repository = Self.repository(response: Self.completionResponse)
        let response = try await repository.completeBook(
            source: "openlibrary",
            mediaId: "OL1M",
            request: BookCompletionWriteRequest(
                journeyId: 41,
                completionDate: "2026-07-20",
                rating: 5,
                review: "",
                reviewTitle: "",
                liked: true,
                isRewatch: true,
                containsSpoilers: false,
                tags: [],
                mutationId: UUID()
            )
        )

        XCTAssertEqual(BookTrackingURLProtocol.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(
            BookTrackingURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/tracking/openlibrary/book/OL1M/complete/"
        )
        XCTAssertEqual(response.diaryEntry.bookJourneyId, 41)
        XCTAssertTrue(response.diaryEntry.isTrueReread == true)
        XCTAssertEqual(response.tracking.book?.completedJourneyCount, 2)
    }

    func testBookLabelsAndShelfSetMatchNativeSemantics() {
        let ref = MediaRef(
            itemId: nil,
            source: "openlibrary",
            mediaType: "book",
            mediaId: "OL1M",
            seasonNumber: nil,
            episodeNumber: nil
        )
        XCTAssertEqual(ref.trackingStatusLabel("Planning"), "To Read")
        XCTAssertEqual(ref.trackingStatusLabel("In progress"), "Currently Reading")
        XCTAssertEqual(ref.trackingStatusLabel("Dropped"), "Did Not Finish")
        XCTAssertEqual(ref.trackingStatusLabel("Completed"), "Read")
        XCTAssertTrue(ref.usesFiveStarRatingScale)
        XCTAssertTrue(ref.usesCalendarConsumptionDate)

        XCTAssertEqual(
            LibraryShelf.available(for: "book"),
            [.planning, .currentlyReading, .paused, .didNotFinish, .read]
        )
        XCTAssertTrue(LibraryShelf.planning.matches(status: "Planning"))
        XCTAssertTrue(LibraryShelf.currentlyReading.matches(status: "In progress"))
        XCTAssertTrue(LibraryShelf.paused.matches(status: "Paused"))
        XCTAssertTrue(LibraryShelf.didNotFinish.matches(status: "Dropped"))
        XCTAssertTrue(LibraryShelf.read.matches(status: "Completed"))
        XCTAssertFalse(LibraryShelf.read.matches(status: "Dropped"))
    }

    func testGenericBookDiaryRequestCarriesJourneyAndIdempotencyFields() throws {
        let mutation = UUID()
        let request = DiaryEntryWriteRequest(
            ref: MediaRef(
                itemId: nil,
                source: "openlibrary",
                mediaType: "book",
                mediaId: "OL1M",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            consumedAt: CalendarDateCodec.date(from: "2026-07-20"),
            rating: 4.5,
            review: "",
            reviewTitle: "",
            liked: false,
            isRewatch: false,
            autoMarkConsumed: true,
            containsSpoilers: false,
            visibility: "public",
            tags: [],
            journeyId: 41,
            mutationId: mutation
        )
        let data = try JSONEncoder.api.encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["consumed_at"] as? String, "2026-07-20")
        XCTAssertEqual(json["journey_id"] as? Int, 41)
        XCTAssertEqual(json["mutation_id"] as? String, mutation.uuidString)
    }

    private static func repository(response: Data? = nil) -> APITrackingRepository {
        let responseData = response ?? trackingResponse
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BookTrackingURLProtocol.self]
        let session = URLSession(configuration: configuration)
        BookTrackingURLProtocol.handler = { request in
            let http = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (http, responseData)
        }
        return APITrackingRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            session: session
        ))
    }

    private static let trackingResponse = Data(#"""
    {
      "tracking_id": 7,
      "status": "In progress",
      "book": {
        "current_journey": {
          "id": 41,
          "status": "In progress",
          "origin": "live",
          "start_date": "2026-07-01",
          "end_date": null,
          "progress": {"kind":"pages","value":120,"max":300,"unit":"page"},
          "completion_diary_entry_id": null,
          "is_reread": true
        },
        "reading_history": [
          {"id":39,"status":"Completed","origin":"live","start_date":"2025-01-01","end_date":"2025-01-12","progress":null,"completion_diary_entry_id":901,"is_reread":false},
          {"id":40,"status":"Dropped","origin":"live","start_date":"2025-06-01","end_date":"2025-06-03","progress":{"kind":"pages","value":44,"max":300,"unit":"page"},"completion_diary_entry_id":null,"is_reread":true}
        ],
        "undated_read": {"id":"undated","status":"Completed","date":null},
        "status_source": "journey",
        "completion_dates": ["2025-01-12"],
        "completed_journey_count": 1,
        "lifetime_read_count": 3,
        "is_rereading": true,
        "completion_required": true,
        "can_remove_tracking": false,
        "remove_tracking_reason": "Delete journeys first.",
        "available_actions": ["pause","drop","mark_read"],
        "action_reasons": {"planning":"Finish the active journey first."}
      }
    }
    """#.utf8)

    private static let completionResponse = Data(#"""
    {
      "tracking": {
        "tracking_id": 7,
        "status": "Completed",
        "book": {
          "current_journey": null,
          "reading_history": [],
          "undated_read": null,
          "status_source": "history_override",
          "completion_dates": ["2025-01-12","2026-07-20"],
          "completed_journey_count": 2,
          "lifetime_read_count": 2,
          "is_rereading": false,
          "completion_required": false,
          "can_remove_tracking": false,
          "remove_tracking_reason": "Delete completion logs first.",
          "available_actions": ["restart","undo_read"],
          "action_reasons": {}
        }
      },
      "diary_entry": {
        "id": 902,
        "user": {"id":1,"username":"reader","display_name":"Reader","avatar_url":null},
        "media": {"ref":{"item_id":7,"source":"openlibrary","media_type":"book","media_id":"OL1M","season_number":null,"episode_number":null},"title":"A Book"},
        "consumed_at": "2026-07-20",
        "rating": "5.0",
        "review_title": "",
        "review": "",
        "contains_spoilers": false,
        "liked": true,
        "is_rewatch": true,
        "is_true_reread": true,
        "book_journey_id": 41,
        "tags": [],
        "visibility": "public",
        "like_count": 0,
        "viewer_has_liked": false
      }
    }
    """#.utf8)
}

private final class BookTrackingURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequest = request
        do {
            let (response, data) = try XCTUnwrap(Self.handler)(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
