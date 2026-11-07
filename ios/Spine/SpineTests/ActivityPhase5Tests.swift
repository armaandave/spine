import XCTest
@testable import Spine

/// Activity Phase 5 (AC-8, AC-9): the Planning row, the date on a progress save, and fixing or taking back a row.
/// Every wire field is optional, so a shape from an older server still decodes.
@MainActor
final class ActivityPhase5Tests: XCTestCase {
    override func setUp() {
        super.setUp()
        Phase5RequestProtocol.handler = nil
        Phase5RequestProtocol.requests = []
    }

    // MARK: Fixtures

    private func ref(_ source: String, _ mediaType: String, _ mediaId: String) -> MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: mediaType, mediaId: mediaId, seasonNumber: nil, episodeNumber: nil)
    }

    private func decode<Value: Decodable>(_ type: Value.Type, _ json: String) throws -> Value {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private func planningRow(
        count: Int? = 3,
        mediaType: String? = "tv",
        summary: String? = "added 3 shows to Planning",
        day: String = "2026-11-06",
        titles: [String]? = ["Severance", "The Wire", "Chernobyl"],
        posters: [String]? = nil
    ) throws -> ActivityItem {
        func json(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        let names = titles.map { "[" + $0.map { json($0) }.joined(separator: ", ") + "]" } ?? "null"
        let covers = posters.map { "[" + $0.map { json($0) }.joined(separator: ", ") + "]" } ?? "null"
        return try decode(ActivityItem.self, """
        {
          "id": -17592186044420, "type": "planning_added", "created_at": "2026-11-06T16:00:00Z", "consumed_at": "\(day)",
          "actor": {"id": 3, "username": "reader", "display_name": "Reader", "avatar_url": null},
          "media": {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "1", "item_id": 7,
                            "season_number": null, "episode_number": null}, "title": "Severance"},
          "person": null,
          "object": {"type": "planning", "id": 17592186044420, "summary": \(json(summary)), "label": null,
                     "count": \(count.map(String.init) ?? "null"), "media_type": \(json(mediaType)),
                     "titles": \(names), "posters": \(covers)},
          "viewer": {"can_view": true, "has_liked": false}, "like_count": 0, "comment_count": 0
        }
        """)
    }

    private func progressPage(viewer: String) throws -> ActivityItem {
        try decode(ActivityItem.self, """
        {
          "id": -9, "type": "progress_updated", "created_at": "2026-11-06T16:00:00Z", "consumed_at": "2026-11-06",
          "actor": {"id": 3, "username": "reader", "display_name": "Reader", "avatar_url": null},
          "media": {"ref": {"source": "hardcover", "media_type": "book", "media_id": "5", "item_id": 7,
                            "season_number": null, "episode_number": null}, "title": "Dune"},
          "person": null,
          "object": {"type": "progress_day", "id": 9, "summary": "read 40 pages", "label": "40 pages"},
          "viewer": \(viewer), "like_count": 0, "comment_count": 0
        }
        """)
    }

    private func client() -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [Phase5RequestProtocol.self]
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: config)
        )
        client.tokenProvider.accessToken = "access"
        return client
    }

    /// The request's path as sent, trailing slash and all (`URL.path` drops it).
    private static func path(of request: URLRequest) -> String? {
        request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.path }
    }

    private func respond(status: Int = 200, _ body: String = "{}") {
        Phase5RequestProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, Data(body.utf8))
        }
    }

    // MARK: The Planning row

    func testAPlanningRowDecodesAndSaysItsWords() throws {
        let row = try planningRow()
        XCTAssertEqual(row.object.count, 3)
        XCTAssertEqual(row.object.mediaType, "tv")
        XCTAssertEqual(row.planning, PlanningRef(id: 17_592_186_044_420, mediaType: "tv"))
        XCTAssertNil(row.post, "It has no post page, no likes and no comments")
        XCTAssertEqual(PlanningWords.headline(for: row), "Added 3 shows to Planning")
        XCTAssertEqual(ActivityFeedPresentation.actionText(for: row), "Added 3 shows to Planning")
        XCTAssertEqual(ActivityFeedPresentation.statusText(for: row), "Added 3 shows to Planning")
        // The count is the row's title, and the names sit under it.
        XCTAssertEqual(PlanningWords.names(for: row), "Severance, The Wire, Chernobyl")
        XCTAssertEqual(ActivityListPresentation.caption(for: row), "Severance, The Wire, Chernobyl")
        XCTAssertEqual(ActivityFeedPresentation.destinationHint(for: row), "Opens the titles added")
        XCTAssertEqual(ActivityDestination.resolve(row), .planning(PlanningRef(id: 17_592_186_044_420, mediaType: "tv")))
    }

    func testAMixedDayReadsTitlesAndALineTheServerLeftOutIsBuilt() throws {
        let mixed = try planningRow(count: 6, mediaType: nil, summary: "added 6 titles to Planning")
        XCTAssertNil(mixed.planning?.mediaType)
        XCTAssertEqual(PlanningWords.headline(for: mixed), "Added 6 titles to Planning")

        let bare = try planningRow(count: 12, mediaType: "movie", summary: nil)
        XCTAssertEqual(PlanningWords.headline(for: bare), "Added 12 movies to Planning")
        let one = try planningRow(count: 1, mediaType: "book", summary: nil)
        XCTAssertEqual(PlanningWords.headline(for: one), "Added 1 book to Planning")
        let unknown = try planningRow(count: nil, mediaType: nil, summary: nil)
        XCTAssertEqual(PlanningWords.headline(for: unknown), "Added titles to Planning")
    }

    func testSeveralPostersFanOutAndOneKeepsTheRowsOwnPoster() throws {
        let three = try planningRow(posters: ["https://img/1.jpg", "https://img/2.jpg", "https://img/3.jpg"])
        XCTAssertEqual(PlanningWords.fanPosters(for: three)?.count, 3)
        let one = try planningRow(count: 1, posters: ["https://img/1.jpg"])
        XCTAssertNil(PlanningWords.fanPosters(for: one), "A single add keeps its one poster")
        XCTAssertNil(PlanningWords.fanPosters(for: try planningRow()), "An older server sends no posters")
        XCTAssertNil(PlanningWords.fanPosters(for: try progressPage(viewer: #"{"can_view": true, "has_liked": false}"#)))
    }

    func testAnOlderServerSendsNoNamesSoTheRowShowsOnlyItsCount() throws {
        let row = try planningRow(titles: nil)
        XCTAssertNil(PlanningWords.names(for: row))
        XCTAssertNil(ActivityListPresentation.caption(for: row))
        XCTAssertEqual(PlanningWords.headline(for: row), "Added 3 shows to Planning")
    }

    func testAPlanningRowSitsInItsMonthOnTheList() throws {
        let row = try planningRow(day: "2026-11-06")
        let sections = ActivityListPresentation.monthSections(from: [row])
        XCTAssertEqual(sections.map(\.items.count), [1])
        XCTAssertEqual(sections.first?.items.first?.id, row.id)
    }

    func testAnObjectFromAnOlderServerHasNoPlanningFields() throws {
        let item = try decode(ActivityItem.self, """
        {
          "id": -9, "type": "progress_updated", "created_at": null,
          "actor": {"id": 3, "username": "reader", "display_name": "Reader", "avatar_url": null},
          "media": null, "person": null,
          "object": {"type": "progress_day", "id": 9, "summary": "read 40 pages", "label": "40 pages"}
        }
        """)
        XCTAssertNil(item.object.count)
        XCTAssertNil(item.object.mediaType)
        XCTAssertNil(item.planning)
        XCTAssertNil(item.viewer)
        XCTAssertEqual(ProgressRowControls(activity: item), ProgressRowControls(activity: nil))
    }

    func testThePlanningTitlesDecode() throws {
        let day = try decode(PlanningDay.self, """
        {"actor": {"id": 3, "username": "reader", "display_name": "Reader", "avatar_url": null},
         "consumed_at": "2026-11-06", "summary": "added 2 shows to Planning", "count": 2,
         "results": [
           {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "1", "item_id": 7,
                    "season_number": null, "episode_number": null}, "title": "Severance"},
           {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "2", "item_id": 8,
                    "season_number": null, "episode_number": null}, "title": "Andor"}
         ]}
        """)
        XCTAssertEqual(day.results.map(\.title), ["Severance", "Andor"])
        XCTAssertEqual(day.count, 2)
        XCTAssertEqual(try decode(PlanningDay.self, #"{"count": 2}"#).results.count, 0)
        let bare = try decode(PlanningDay.self, #"{"results": []}"#)
        XCTAssertTrue(bare.results.isEmpty)
    }

    // MARK: Only a build that asks gets Planning rows

    func testOnlyAPlainListAndHomeAskForPlanning() async throws {
        respond(#"{"next_cursor": null, "previous_cursor": null, "results": []}"#)
        let repository = APIActivityRepository(client: client())

        _ = try await repository.feedPage(pageSize: 25, cursorLink: nil)
        _ = try await repository.userActivityList(username: "reader", filter: .everything, pageSize: 40, cursorLink: nil)
        let number = ActivityListFilter(mediaType: "book", startDate: "2026-01-01", endDate: "2026-12-31", counts: .titles)
        _ = try await repository.userActivityList(username: "reader", filter: number, pageSize: 40, cursorLink: nil)
        _ = try await repository.userActivityPage(username: "reader", pageSize: 10, cursorLink: nil)

        let includes = Phase5RequestProtocol.requests.map { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?
                .first { $0.name == "include" }?.value
        }
        XCTAssertEqual(includes, ["planning", "planning", nil, nil], "Home and a plain list ask; a number and the rail don't")
        XCTAssertEqual(
            Phase5RequestProtocol.requests.map { Self.path(of: $0) },
            ["/api/v1/feed/", "/api/v1/users/reader/activity/", "/api/v1/users/reader/activity/", "/api/v1/users/reader/activity/"]
        )
        XCTAssertTrue(ActivityListFilter.everything.asksForPlanning)
        XCTAssertFalse(number.asksForPlanning)
        XCTAssertFalse(ActivityListFilter(mediaType: "movie").asksForPlanning)
    }

    func testThePlanningTitlesAreAskedForWithTheRowsType() async throws {
        respond(#"{"results": []}"#)
        let repository = APIActivityRepository(client: client())
        _ = try await repository.planningTitles(id: 17_592_186_044_420, mediaType: "tv")
        _ = try await repository.planningTitles(id: 17_592_186_044_420, mediaType: nil)

        let urls = Phase5RequestProtocol.requests.compactMap { $0.url }
        XCTAssertEqual(urls.map { Self.path(of: URLRequest(url: $0)) }, ["/api/v1/planning/17592186044420/", "/api/v1/planning/17592186044420/"])
        XCTAssertEqual(URLComponents(url: urls[0], resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "tv")
        XCTAssertNil(URLComponents(url: urls[1], resolvingAgainstBaseURL: false)?.queryItems)
    }

    // MARK: Which controls show

    func testTheOwnersNewestRowOffersEditAndDelete() throws {
        let page = try progressPage(viewer: """
        {"can_view": true, "has_liked": false, "can_edit": true, "can_delete": true,
         "edit": {"mode": "save", "value": 200, "unit": "page", "kind": "pages", "date": "2025-12-27", "min": null, "max": 400}}
        """)
        XCTAssertEqual(ProgressRowControls(activity: page), ProgressRowControls(showsEdit: true, showsDelete: true))
        let edit = try XCTUnwrap(page.viewer?.edit)
        XCTAssertTrue(edit.isSave)
        XCTAssertEqual(edit.value, 200)
        XCTAssertEqual(edit.date, "2025-12-27")
        XCTAssertNil(edit.min)
        XCTAssertEqual(edit.max, 400)
    }

    func testAnOlderRowOffersEditOnly() throws {
        let page = try progressPage(viewer: """
        {"can_view": true, "has_liked": false, "can_edit": true, "can_delete": false,
         "edit": {"mode": "number", "value": 120, "unit": "page", "kind": "pages", "date": "2025-12-20", "min": 100, "max": 200}}
        """)
        XCTAssertEqual(ProgressRowControls(activity: page), ProgressRowControls(showsEdit: true, showsDelete: false))
        XCTAssertTrue(try XCTUnwrap(page.viewer?.edit).isNumber)
    }

    func testAnAdminSeesDeleteOnlyAndEveryoneElseNothing() throws {
        let admin = try progressPage(viewer: #"{"can_view": true, "has_liked": false, "can_edit": false, "can_delete": true, "edit": null}"#)
        XCTAssertEqual(ProgressRowControls(activity: admin), ProgressRowControls(showsEdit: false, showsDelete: true))
        let follower = try progressPage(viewer: #"{"can_view": true, "has_liked": false, "can_edit": false, "can_delete": false, "edit": null}"#)
        XCTAssertFalse(ProgressRowControls(activity: follower).showsAny)
        let older = try progressPage(viewer: #"{"can_view": true, "has_liked": false}"#)
        XCTAssertFalse(ProgressRowControls(activity: older).showsAny, "An older server sends none, so nothing shows")
        let noEdit = try progressPage(viewer: #"{"can_view": true, "has_liked": false, "can_edit": true, "can_delete": false, "edit": null}"#)
        XCTAssertFalse(ProgressRowControls(activity: noEdit).showsEdit, "Edit needs what it changes")
    }

    func testALikeKeepsThePagesControls() throws {
        var page = try progressPage(viewer: """
        {"can_view": true, "has_liked": false, "can_edit": true, "can_delete": true,
         "edit": {"mode": "save", "value": 200, "unit": "page", "kind": "pages", "date": "2025-12-27", "min": null, "max": null}}
        """)
        page.engagement = PostEngagement(hasLiked: true, likeCount: 1, commentCount: 0)
        XCTAssertEqual(page.viewer?.hasLiked, true)
        XCTAssertEqual(ProgressRowControls(activity: page), ProgressRowControls(showsEdit: true, showsDelete: true))
    }

    // MARK: What Edit says and allows

    func testAnEditsNumberIsHeldBetweenTheSavesEitherSide() throws {
        let edit = try decode(ProgressEdit.self, """
        {"mode": "number", "value": 120, "unit": "page", "kind": "pages", "date": "2025-12-20", "min": 100, "max": 200}
        """)
        let presentation = ProgressRowEditPresentation(edit: edit, mediaType: "book")
        XCTAssertEqual(presentation.numberLabel, "Page")
        XCTAssertEqual(presentation.unitText, "of 200")
        XCTAssertEqual(presentation.initialText, "120")
        XCTAssertEqual(presentation.footer, "Between 100 and 200, the saves either side of it.")

        XCTAssertNil(presentation.validation(for: "110"))
        XCTAssertEqual(presentation.validation(for: "99"), "Enter 100 or more.")
        XCTAssertEqual(presentation.validation(for: "201"), "Enter 200 or less.")
        XCTAssertTrue(presentation.canSave(input: "110"))
        XCTAssertTrue(presentation.canSave(input: "100"), "The number before it empties the day")
        XCTAssertFalse(presentation.canSave(input: "120"), "No change")
        XCTAssertFalse(presentation.canSave(input: "99"))
        XCTAssertFalse(presentation.canSave(input: ""))
        XCTAssertEqual(presentation.sanitized("1a1.5"), "115")

        XCTAssertEqual(presentation.patch(for: "110"), ProgressEditRequest(value: 110, unit: "page"))
        XCTAssertNil(presentation.patch(for: "120"))
    }

    func testTheNewestRowNeedsANewNumberToSave() throws {
        let edit = try decode(ProgressEdit.self, """
        {"mode": "save", "value": 200, "unit": "page", "kind": "pages", "date": "2025-12-27", "min": null, "max": 400}
        """)
        let presentation = ProgressRowEditPresentation(edit: edit, mediaType: "book")
        XCTAssertNil(presentation.footer)
        XCTAssertNil(presentation.validation(for: "150"), "A drop is fine on the newest row")
        XCTAssertEqual(presentation.validation(for: "401"), "Enter 400 or less.")
        XCTAssertFalse(presentation.canSave(input: "200"))
        XCTAssertTrue(presentation.canSave(input: "199"), "A new day alone writes nothing on the server, so it doesn't save")
        XCTAssertNil(presentation.patch(for: "150"), "The newest row saves again; it has no PATCH")
    }

    func testEachTypeSaysItsOwnUnit() throws {
        func presentation(_ unit: String, _ mediaType: String, max: String = "null") throws -> ProgressRowEditPresentation {
            ProgressRowEditPresentation(
                edit: try decode(ProgressEdit.self, """
                {"mode": "number", "value": 10.5, "unit": "\(unit)", "kind": "x", "date": "2026-01-01", "min": 0, "max": \(max)}
                """),
                mediaType: mediaType
            )
        }
        let percent = try presentation("percent", "game", max: "100")
        XCTAssertEqual(percent.numberLabel, "Percent complete")
        XCTAssertEqual(percent.unitText, "%")
        XCTAssertTrue(percent.allowsDecimal)
        XCTAssertEqual(percent.initialText, "10.5")
        let bookPercent = try presentation("percent", "book", max: "100")
        XCTAssertEqual(bookPercent.numberLabel, "Progress")
        XCTAssertEqual(try presentation("minute", "game").numberLabel, "Minutes played")
        XCTAssertEqual(try presentation("minute", "game").unitText, "min")
        XCTAssertEqual(try presentation("chapter", "manga", max: "40").numberLabel, "Chapters")
        XCTAssertEqual(try presentation("chapter", "manga", max: "40").unitText, "of 40")
        XCTAssertEqual(try presentation("issue", "comic").numberLabel, "Issues")
        XCTAssertFalse(try presentation("page", "book").allowsDecimal)
    }

    func testAnEditFromADifferentServerShapeStillDecodes() throws {
        let bare = try decode(ProgressEdit.self, "{}")
        XCTAssertNil(bare.mode)
        XCTAssertFalse(bare.isSave)
        XCTAssertFalse(bare.isNumber)
        let viewer = try decode(ActivityViewer.self, #"{"has_liked": true, "can_edit": true}"#)
        XCTAssertEqual(viewer.canEdit, true)
        XCTAssertNil(viewer.canDelete)
        XCTAssertNil(viewer.edit)
    }

    func testThePatchBodyIsTheNumberAndItsUnit() throws {
        let data = try JSONEncoder.api.encode(ProgressEditRequest(value: Decimal(string: "15.25")!, unit: "percent"))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["value"] as? Double, 15.25)
        XCTAssertEqual(json["unit"] as? String, "percent")
    }

    // MARK: The date on a save

    func testTheDateRowSaysTodayYesterdayOrTheDay() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        let now = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 11, day: 7, hour: 9)))
        func day(_ y: Int, _ m: Int, _ d: Int) -> Date { calendar.date(from: DateComponents(year: y, month: m, day: d, hour: 23))! }
        XCTAssertEqual(ProgressDateWords.text(for: day(2026, 11, 7), now: now, calendar: calendar), "Today")
        XCTAssertEqual(ProgressDateWords.text(for: day(2026, 11, 6), now: now, calendar: calendar), "Yesterday")
        XCTAssertEqual(ProgressDateWords.text(for: day(2026, 10, 30), now: now, calendar: calendar), "Oct 30")
        XCTAssertTrue(ProgressDateWords.text(for: day(2025, 12, 20), now: now, calendar: calendar).contains("2025"))
    }

    func testASheetsSaveCarriesTheDayItNamed() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        let late = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 12, day: 20, hour: 23, minute: 30)))
        let request = ProgressUpdateSaveRequest(mode: .pages, value: 120)
        XCTAssertNil(request.progressedOn, "A save from elsewhere sends no day")
        let now = try XCTUnwrap(calendar.date(from: DateComponents(year: 2027, month: 1, day: 2, hour: 9)))
        XCTAssertEqual(request.dated(late, now: now, calendar: calendar).progressedOn, "2026-12-20")
        XCTAssertEqual(request.dated(late, now: now, calendar: calendar).value, 120)
        XCTAssertNil(request.dated(now, now: now, calendar: calendar).progressedOn, "Today sends no day; the server's today is the owner's")
    }

    func testABookAndASeriesSaveSendTheDayOnlyWhenTheyHaveOne() async throws {
        respond(#"{"id": 1}"#)
        // The reply isn't a tracking state: only the requests are read.
        let repository = APITrackingRepository(client: client())
        _ = try? await repository.updateBookProgress(
            source: "hardcover", mediaId: "5", progressType: "pages", value: 120, notes: "", progressedOn: "2026-12-20"
        )
        _ = try? await repository.updateBookProgress(
            source: "hardcover", mediaId: "5", progressType: "pages", value: 130, notes: ""
        )
        let mangaRef = ref("mangadex", "manga", "9")
        _ = try? await repository.updateSeriesProgress(ref: mangaRef, value: 12, mutationId: UUID(), progressedOn: "2026-12-20")
        _ = try? await repository.updateSeriesProgress(ref: mangaRef, value: 13, mutationId: UUID())

        let bodies = Phase5RequestProtocol.requests.map(Phase5RequestProtocol.body(of:))
        XCTAssertEqual(bodies.count, 4)
        XCTAssertEqual(bodies[0]["progressed_on"] as? String, "2026-12-20")
        XCTAssertNil(bodies[1]["progressed_on"])
        XCTAssertEqual(bodies[2]["progressed_on"] as? String, "2026-12-20")
        XCTAssertNil(bodies[3]["progressed_on"])
        XCTAssertEqual(
            Phase5RequestProtocol.requests.map { Self.path(of: $0) },
            [
                "/api/v1/tracking/hardcover/book/5/progress/", "/api/v1/tracking/hardcover/book/5/progress/",
                "/api/v1/tracking/mangadex/manga/9/progress/", "/api/v1/tracking/mangadex/manga/9/progress/",
            ]
        )
    }

    // MARK: Fixing and deleting

    private func editor(media: String = "book") -> ProgressRowEditor {
        let titleRef = media == "manga" ? ref("mangadex", "manga", "9") : ref("hardcover", "book", "5")
        let summary = MediaSummary(ref: titleRef, title: "Dune")
        let client = client()
        return ProgressRowEditor(
            progressId: 9,
            media: summary,
            activityRepository: APIActivityRepository(client: client),
            trackingRepository: APITrackingRepository(client: client),
            mediaRepository: APIMediaRepository(client: client),
            onUnauthorized: {}
        )
    }

    private func edit(_ json: String) throws -> ProgressEdit {
        try decode(ProgressEdit.self, json)
    }

    func testDeleteSendsDELETEAndTellsEveryScreen() async throws {
        respond(status: 204, "")
        let editor = editor()
        let told = expectation(forNotification: .mediaStateDidChange, object: nil) { notification in
            (notification.userInfo?["ref"] as? MediaRef)?.mediaId == "5"
        }
        await editor.delete()
        await fulfillment(of: [told], timeout: 1)

        XCTAssertEqual(Phase5RequestProtocol.requests.first?.httpMethod, "DELETE")
        XCTAssertEqual(Self.path(of: Phase5RequestProtocol.requests[0]), "/api/v1/progress/9/")
        XCTAssertTrue(editor.didChange)
        XCTAssertNil(editor.deleteErrorMessage)
    }

    func testARefusedDeleteTellsNobodyAndShowsWhy() async throws {
        respond(status: 409, #"{"detail": "A newer save has been made since."}"#)
        let editor = editor()
        let silent = expectation(forNotification: .mediaStateDidChange, object: nil)
        silent.isInverted = true
        await editor.delete()
        await fulfillment(of: [silent], timeout: 0.3)

        XCTAssertFalse(editor.didChange, "Nothing changed, so the page stays")
        XCTAssertNotNil(editor.deleteErrorMessage)
    }

    func testAnOlderRowsFixIsAPATCHAndTellsEveryScreen() async throws {
        respond(status: 204, "")
        let editor = editor()
        let older = try edit("""
        {"mode": "number", "value": 120, "unit": "page", "kind": "pages", "date": "2025-12-20", "min": 100, "max": 200}
        """)
        let told = expectation(forNotification: .mediaStateDidChange, object: nil)
        let failure = await editor.save(input: "110", date: Date(), edit: older)
        await fulfillment(of: [told], timeout: 1)

        XCTAssertNil(failure)
        let request = try XCTUnwrap(Phase5RequestProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(Self.path(of: request), "/api/v1/progress/9/")
        let body = Phase5RequestProtocol.body(of: request)
        XCTAssertEqual(body["value"] as? Int, 110)
        XCTAssertEqual(body["unit"] as? String, "page")
        XCTAssertTrue(editor.didChange)
    }

    func testAFixPastItsNeighborsNeverLeavesTheApp() async throws {
        let editor = editor()
        let older = try edit("""
        {"mode": "number", "value": 120, "unit": "page", "kind": "pages", "date": "2025-12-20", "min": 100, "max": 200}
        """)
        let failure = await editor.save(input: "250", date: Date(), edit: older)
        XCTAssertNotNil(failure)
        XCTAssertTrue(Phase5RequestProtocol.requests.isEmpty)
        XCTAssertFalse(editor.didChange)
    }

    func testTheNewestBookRowSavesItsNumberAgainOnItsDay() async throws {
        respond(#"{"id": 1}"#)
        let editor = editor()
        let newest = try edit("""
        {"mode": "save", "value": 200, "unit": "page", "kind": "pages", "date": "2025-12-27", "min": null, "max": 400}
        """)
        let day = try XCTUnwrap(CalendarDateCodec.date(from: "2025-12-27"))
        // The reply isn't a tracking state, so the save reports a failure; the request is what this reads.
        _ = await editor.save(input: "150", date: day, edit: newest)

        let request = try XCTUnwrap(Phase5RequestProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(Self.path(of: request), "/api/v1/tracking/hardcover/book/5/progress/")
        let body = Phase5RequestProtocol.body(of: request)
        XCTAssertEqual(body["progress_type"] as? String, "pages")
        XCTAssertEqual(body["value"] as? Int, 150)
        XCTAssertEqual(body["progressed_on"] as? String, "2025-12-27")
    }

    func testTheNewestMangaRowSavesAWholeNumberOnItsDay() async throws {
        respond(#"{"id": 1}"#)
        let editor = editor(media: "manga")
        let newest = try edit("""
        {"mode": "save", "value": 12, "unit": "chapter", "kind": "count", "date": "2026-01-05", "min": null, "max": 40}
        """)
        let day = try XCTUnwrap(CalendarDateCodec.date(from: "2026-01-04"))
        _ = await editor.save(input: "11", date: day, edit: newest)

        let request = try XCTUnwrap(Phase5RequestProtocol.requests.first)
        XCTAssertEqual(Self.path(of: request), "/api/v1/tracking/mangadex/manga/9/progress/")
        let body = Phase5RequestProtocol.body(of: request)
        XCTAssertEqual(body["value"] as? Int, 11)
        XCTAssertEqual(body["progressed_on"] as? String, "2026-01-04")
    }
}

/// Answers every request the test makes with one canned response, and keeps what it was asked.
private final class Phase5RequestProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with _: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if !data.isEmpty {
                client?.urlProtocol(self, didLoad: data)
            }
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    /// A request's JSON body; URLProtocol sees it as a stream.
    static func body(of request: URLRequest) -> [String: Any] {
        guard let stream = request.httpBodyStream else { return [:] }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }
}
