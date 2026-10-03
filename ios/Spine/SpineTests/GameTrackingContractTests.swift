import XCTest
@testable import Spine

@MainActor
final class GameTrackingContractTests: XCTestCase {
    func testProgressDraftPreservesUnknownAndExplicitZero() throws {
        var draft = GameProgressDraft(totalMinutes: nil, percentage: nil)
        XCTAssertNil(try draft.values().totalMinutes)
        XCTAssertNil(try draft.values().percentage)
        draft.hours = "0"
        draft.percentage = "0"
        XCTAssertEqual(try draft.values().totalMinutes, 0)
        XCTAssertEqual(try draft.values().percentage, 0)
        draft.hours = "12"
        draft.minutes = "30"
        XCTAssertEqual(try draft.values().totalMinutes, 750)
        draft.percentage = "100"
        XCTAssertEqual(try draft.values().percentage, 100)
    }

    func testProgressRejectsInvalidNumbers() throws {
        for input in ["-1", "1.5", "101", "x"] {
            XCTAssertThrowsError(try GameProgressDraft(percentage: input).values())
        }
        XCTAssertThrowsError(try GameProgressDraft(hours: "-1").values())
        XCTAssertThrowsError(try GameProgressDraft(minutes: "60").values())
        XCTAssertThrowsError(try GameProgressDraft(hours: "35791394", minutes: "8").values())
        XCTAssertEqual(try GameProgressDraft(hours: "35791394", minutes: "7").values().totalMinutes, Int(Int32.max))
    }

    func testProgressPatchSeparatesOmittedClearedAndZero() throws {
        let request = GamePlaythroughWriteRequest(totalMinutes: nil, percentage: 0, includesMinutes: true, includesPercentage: true)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(request)) as? [String: Any])
        XCTAssertTrue(json["total_minutes"] is NSNull)
        XCTAssertEqual(json["percentage"] as? Int, 0)
        XCTAssertNil(json["start_date"])
        let untouched = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(GamePlaythroughWriteRequest())) as? [String: Any])
        XCTAssertNil(untouched["total_minutes"])
        XCTAssertNil(untouched["percentage"])
    }

    func testGameSummaryShowsOnlySuppliedProgressAndFiveShelves() {
        XCTAssertEqual(GameProgressValues(totalMinutes: nil, percentage: nil).summary, "")
        XCTAssertEqual(GameProgressValues(totalMinutes: 0, percentage: 0).summary, "0% · 0h")
        XCTAssertEqual(GameProgressValues(totalMinutes: 750, percentage: 65).summary, "65% · 12h 30m")
        XCTAssertEqual(LibraryShelf.available(for: "game").map { $0.title(mediaType: "game") }, ["Planning", "Playing", "Paused", "Dropped", "Completed"])
        XCTAssertEqual(MediaLogViewModel.ratingDecimal(for: 9, mediaType: "game"), 4.5)
    }
    func testUnchangedProgressDoesNotBreakIndependentFieldLinks() throws {
        let original = GameProgressDraft(totalMinutes: 2400, percentage: 65)
        var draft = original
        draft.hours = "45"
        let request = try draft.request(comparedTo: original)
        let json = try object(request)
        XCTAssertEqual(json["total_minutes"] as? Int, 2700)
        XCTAssertNil(json["percentage"])
        XCTAssertNil(try object(original.request(comparedTo: original))["total_minutes"])
    }

    func testExplicitClearOfUnknownStillSendsNull() throws {
        let original = GameProgressDraft(totalMinutes: nil, percentage: nil)
        var draft = original
        draft.hours = ""
        let json = try object(draft.request(comparedTo: original))
        XCTAssertTrue(json["total_minutes"] is NSNull)
        XCTAssertNil(json["percentage"])
    }

    func testClearingOneFieldPreservesTheOtherField() throws {
        let original = GameProgressDraft(totalMinutes: 750, percentage: 65)
        var clearTime = original
        clearTime.hours = ""
        clearTime.minutes = ""
        XCTAssertEqual(try clearTime.values().summary, "65%")
        let timeRequest = try object(clearTime.request(comparedTo: original))
        XCTAssertTrue(timeRequest["total_minutes"] is NSNull)
        XCTAssertNil(timeRequest["percentage"])

        var clearPercentage = original
        clearPercentage.percentage = ""
        XCTAssertEqual(try clearPercentage.values().summary, "12h 30m")
        let percentageRequest = try object(clearPercentage.request(comparedTo: original))
        XCTAssertTrue(percentageRequest["percentage"] is NSNull)
        XCTAssertNil(percentageRequest["total_minutes"])
        XCTAssertEqual(GameProgressValues(totalMinutes: nil, percentage: 100).summary, "100%")
    }

    func testDirectPlaythroughStartDateCanBeClearedWithoutChangingProgress() throws {
        let cleared = GamePlaythroughWriteRequest(includesStartDate: true)
        let json = try object(cleared)
        XCTAssertTrue(json["start_date"] is NSNull)
        XCTAssertNil(json["total_minutes"])
        XCTAssertNil(json["percentage"])
    }

    func testDiaryDeleteRetryAcceptsAlreadyDeletedEntry() async throws {
        _ = try makeComposer()
        GameTrackingURLProtocol.status = 404
        try await APIDiaryRepository(client: Self.client()).delete(id: 9)
        XCTAssertEqual(GameTrackingURLProtocol.requests.last?.httpMethod, "DELETE")
        XCTAssertEqual(GameTrackingURLProtocol.requests.last?.url?.absoluteString, "https://example.com/api/v1/diary/9/")
    }

    func testProgressWriteUsesSameLocalCalendarDayAsCompletion() throws {
        let original = GameProgressDraft(totalMinutes: nil, percentage: nil)
        var changed = original
        changed.hours = "41"
        let json = try object(changed.request(comparedTo: original))
        XCTAssertEqual(json["progressed_on"] as? String, CalendarDateCodec.string(from: Date()))
        XCTAssertNil(try object(original.request(comparedTo: original, startDate: "2025-01-01"))["progressed_on"])
    }

    func testDiaryDateOnlyPatchDoesNotReconnectProgress() throws {
        let request = DiaryEntryUpdateRequest(consumedAt: CalendarDateCodec.date(from: "2025-01-01"), rating: nil,
            review: nil, reviewTitle: nil, tags: nil, liked: nil, isRewatch: nil,
            containsSpoilers: nil, visibility: nil, calendarDateOnly: true)
        let json = try object(request)
        XCTAssertEqual(json["consumed_at"] as? String, "2025-01-01")
        XCTAssertNil(json["total_minutes"])
        XCTAssertNil(json["percentage"])
    }

    func testGameStateDecodesUnknownAndCompletedProgressAndHistory() throws {
        let state = try JSONDecoder.api.decode(TrackingState.self, from: Self.tracking)
        XCTAssertEqual(state.game?.currentPlaythrough?.id, 41)
        XCTAssertNil(state.game?.currentPlaythrough?.totalMinutes)
        XCTAssertEqual(state.game?.currentPlaythrough?.percentage, 0)
        XCTAssertTrue(state.game?.hasLivePlaythrough == true)
        XCTAssertTrue(state.game?.canUpdateProgress == true)
        XCTAssertEqual(state.game?.importedLifetimeMinutes, 18000)
        XCTAssertEqual(state.game?.lifetimeCompletionCount, 1)
    }

    func testPlayingActionRequiresAnActualPlayingPlaythroughToDisable() throws {
        let playing = try JSONDecoder.api.decode(TrackingState.self, from: Self.tracking)
        XCTAssertTrue(playing.game?.hasPlayingPlaythrough == true)
        var payload = try XCTUnwrap(JSONSerialization.jsonObject(with: Self.tracking) as? [String: Any])
        var game = try XCTUnwrap(payload["game"] as? [String: Any])
        var playthrough = try XCTUnwrap(game["current_playthrough"] as? [String: Any])
        playthrough["status"] = "Paused"
        game["current_playthrough"] = playthrough
        payload["game"] = game
        let paused = try JSONDecoder.api.decode(TrackingState.self, from: JSONSerialization.data(withJSONObject: payload))
        XCTAssertFalse(paused.game?.hasPlayingPlaythrough == true)
        game["current_playthrough"] = NSNull()
        payload["game"] = game
        let legacy = try JSONDecoder.api.decode(TrackingState.self, from: JSONSerialization.data(withJSONObject: payload))
        XCTAssertEqual(legacy.status, "In progress")
        XCTAssertFalse(legacy.game?.hasPlayingPlaythrough == true)
        XCTAssertFalse(legacy.game?.canUpdateProgress == true)
    }

    func testCompletionDraftUsesVisibleValuesAndOneAtomicRequest() async throws {
        GameTrackingURLProtocol.requests = []
        let model = try makeComposer()
        XCTAssertEqual(model.gameProgress.percentage, "0")
        XCTAssertEqual(model.gameProgress.hours, "")
        XCTAssertTrue(model.isRepeat)
        XCTAssertFalse(model.supportsProgress)
        model.gameProgress = GameProgressDraft(hours: "12", minutes: "30", percentage: "65")
        model.ratingSteps = 9
        model.liked = true
        model.consumedAt = CalendarDateCodec.date(from: "2025-01-01")!
        let saved = await model.save()
        XCTAssertTrue(saved, model.errorMessage ?? "")
        XCTAssertEqual(GameTrackingURLProtocol.requests.count, 1)
        let request = try XCTUnwrap(GameTrackingURLProtocol.requests.first)
        XCTAssertEqual(request.url!.absoluteString, "https://example.com/api/v1/tracking/igdb/game/42/complete/")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Spine-Timezone"), TimeZone.current.identifier)
        let body = try XCTUnwrap(GameTrackingURLProtocol.bodies.first)
        XCTAssertEqual(body["playthrough_id"] as? Int, 41)
        XCTAssertEqual(body["total_minutes"] as? Int, 750)
        XCTAssertEqual(body["percentage"] as? Int, 65)
        XCTAssertEqual(body["rating"] as? Double, 4.5)
        XCTAssertEqual(body["liked"] as? Bool, true)
    }

    func testFailedCompletionPreservesDraftAndRetriesSameMutation() async throws {
        let model = try makeComposer()
        model.gameProgress.hours = "40"
        model.review = "Keep this draft"
        GameTrackingURLProtocol.status = 500
        let failed = await model.save()
        XCTAssertFalse(failed)
        XCTAssertEqual(model.gameProgress.hours, "40")
        XCTAssertEqual(model.review, "Keep this draft")
        XCTAssertFalse(model.isSaving)
        GameTrackingURLProtocol.status = 200
        let saved = await model.save()
        XCTAssertTrue(saved, model.errorMessage ?? "")
        XCTAssertEqual(GameTrackingURLProtocol.bodies.count, 2)
        XCTAssertEqual(GameTrackingURLProtocol.bodies[0]["mutation_id"] as? String, GameTrackingURLProtocol.bodies[1]["mutation_id"] as? String)
    }

    func testCompletionPreselectsTheTappedHeartAndRatingWithoutWriting() throws {
        let model = try makeComposer(liked: true, ratingSteps: 9)
        XCTAssertTrue(model.liked)
        XCTAssertEqual(model.ratingSteps, 9)
        XCTAssertTrue(GameTrackingURLProtocol.requests.isEmpty)
    }

    func testOpeningAndCancellingCompletionDoesNotWrite() throws {
        _ = try makeComposer()
        XCTAssertTrue(GameTrackingURLProtocol.requests.isEmpty)
    }

    func testRepositoryActionAndDeletionAcceptEmptyRestoration() async throws {
        _ = try makeComposer()
        GameTrackingURLProtocol.status = 204
        let repository = APITrackingRepository(client: Self.client())
        let ref = Self.ref
        try await repository.performGameAction(ref: ref, action: "undo_completed", request: BookActionRequest())
        XCTAssertEqual(GameTrackingURLProtocol.requests.last!.url!.absoluteString, "https://example.com/api/v1/tracking/igdb/game/42/actions/undo_completed/")
        try await repository.deleteGamePlaythrough(ref: ref, playthroughId: 41)
        XCTAssertEqual(GameTrackingURLProtocol.requests.last?.httpMethod, "DELETE")
        XCTAssertEqual(GameTrackingURLProtocol.requests.last!.url!.absoluteString, "https://example.com/api/v1/tracking/igdb/game/42/playthroughs/41/")
    }

    private func makeComposer(liked: Bool? = nil, ratingSteps: Int? = nil) throws -> MediaLogViewModel {
        GameTrackingURLProtocol.requests = []
        GameTrackingURLProtocol.bodies = []
        GameTrackingURLProtocol.status = 200
        GameTrackingURLProtocol.response = Self.completion
        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Data(#"{"ref":{"source":"igdb","media_type":"game","media_id":"42"},"title":"Test game","external_links":{}}"#.utf8))
        let client = Self.client()
        return MediaLogViewModel(detail: detail, trackingRepository: APITrackingRepository(client: client),
            diaryRepository: APIDiaryRepository(client: client),
            tracking: try JSONDecoder.api.decode(TrackingState.self, from: Self.tracking),
            preselectedLiked: liked, preselectedRatingSteps: ratingSteps,
            onUnauthorized: {}, onSaved: {})
    }

    private func object<T: Encodable>(_ request: T) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(request)) as? [String: Any])
    }

    private static let ref = MediaRef(itemId: nil, source: "igdb", mediaType: "game", mediaId: "42", seasonNumber: nil, episodeNumber: nil)
    private static func client() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [GameTrackingURLProtocol.self]
        return APIClient(baseURL: URL(string: "https://example.com")!, session: URLSession(configuration: configuration))
    }
    private static let tracking = Data(#"""
    {"tracking_id":7,"status":"In progress","game":{
    "current_playthrough":{"id":41,"status":"In progress","origin":"live","start_date":"2024-01-01","end_date":null,"total_minutes":null,"percentage":0,"completion_diary_entry_id":null,"is_replay":true},
    "play_history":[],"undated_completion":{"id":"undated","status":"Completed","date":null},
    "status_source":"playthrough","completion_dates":[],"completed_playthrough_count":0,"lifetime_completion_count":1,
    "is_replaying":true,"can_remove_tracking":false,"available_actions":["pause","drop"],"action_reasons":{},"imported_lifetime_minutes":18000,"imported_lifetime_source":"steam"}}
    """#.utf8)
    private static let completion = Data(#"""
    {"tracking":{"tracking_id":7,"status":"Completed"},"diary_entry":{"id":9,"user":{"id":1,"username":"test","display_name":"Test"},"media":{"ref":{"source":"igdb","media_type":"game","media_id":"42"},"title":"Test game"},"consumed_at":"2025-01-01","contains_spoilers":false,"liked":true,"is_rewatch":true,"game_playthrough_id":41,"total_minutes":750,"percentage":65,"tags":[],"visibility":"public","like_count":0,"viewer_has_liked":false}}
    """#.utf8)

}

private final class GameTrackingURLProtocol: URLProtocol {
    nonisolated(unsafe) static var requests: [URLRequest] = []
    nonisolated(unsafe) static var bodies: [[String: Any]] = []
    nonisolated(unsafe) static var status = 200
    nonisolated(unsafe) static var response = Data()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
            body = data
        }
        if let body, let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] { Self.bodies.append(json) }
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: nil, headerFields: ["Content-Type":"application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.status == 204 ? Data() : Self.status == 200 ? Self.response : Data(#"{"detail":"Failed save"}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
