import XCTest
@testable import Spine

/// Phase 4's Activity list: the wire fields it reads (all optional, so older servers still work), the words it
/// prints, the numbers that open it, and how it pages and goes stale.
@MainActor
final class ActivityListTests: XCTestCase {
    private func decode<Value: Decodable>(_ type: Value.Type, _ json: String) throws -> Value {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    // MARK: Fixtures

    private func row(
        id: Int,
        objectId: Int? = nil,
        type: String = "progress_updated",
        objectType: String = "progress_day",
        day: String = "2026-06-02",
        mediaType: String = "book",
        title: String = "Dune",
        summary: String? = "read 40 pages",
        label: String? = "40 pages",
        extra: String = "",
        mediaExtra: String = "",
        seasonNumber: Int? = nil,
        episodeNumber: Int? = nil
    ) -> String {
        func json(_ value: String?) -> String { value.map { "\"\($0)\"" } ?? "null" }
        return """
        {
          "id": \(id), "type": "\(type)", "created_at": "2026-11-07T16:00:00Z", "consumed_at": "\(day)",
          "actor": {"id": 3, "username": "reader", "display_name": "Reader", "avatar_url": null},
          "media": {"ref": {"source": "manual", "media_type": "\(mediaType)", "media_id": "1", "item_id": 7,
                            "season_number": \(seasonNumber.map(String.init) ?? "null"),
                            "episode_number": \(episodeNumber.map(String.init) ?? "null")},
                    "title": "\(title)"\(mediaExtra)},
          "person": null,
          "object": {"type": "\(objectType)", "id": \(objectId ?? abs(id)), "summary": \(json(summary)), "label": \(json(label))\(extra)},
          "viewer": {"can_view": true, "has_liked": false}, "like_count": 0, "comment_count": 0
        }
        """
    }

    private func item(_ json: String) throws -> ActivityItem {
        try decode(ActivityItem.self, json)
    }

    // MARK: Decoding

    func testAProgressRowReadsPhase4sFields() throws {
        let progress = try item(row(
            id: -9,
            summary: "read 40 pages, paused",
            extra: """
            , "amount": 40, "unit": "page", "moves": ["paused"], "percent": null, "time_label": null
            """
        ))
        XCTAssertEqual(progress.object.amount, 40)
        XCTAssertEqual(progress.object.unit, "page")
        XCTAssertEqual(progress.object.moves, ["paused"])
        XCTAssertNil(progress.object.percent)
        XCTAssertNil(progress.object.timeLabel)
    }

    func testAPercentAndATimeLabelDecode() throws {
        let game = try item(row(
            id: -4,
            mediaType: "game",
            summary: "played 2h",
            label: "2h",
            extra: #", "amount": 120, "unit": "minute", "moves": [], "percent": 12.5, "time_label": "2h""#
        ))
        XCTAssertEqual(game.object.amount, 120)
        XCTAssertEqual(game.object.percent, 12.5)
        XCTAssertEqual(game.object.timeLabel, "2h")
        XCTAssertEqual(game.object.moves, [])
    }

    /// What build 61 to 68 get from an older server decodes with every new field nil.
    func testARowFromAnOlderServerDecodesWithNoNewFields() throws {
        let old = try item(row(id: -9))
        XCTAssertNil(old.object.amount)
        XCTAssertNil(old.object.unit)
        XCTAssertNil(old.object.moves)
        XCTAssertNil(old.object.timeLabel)
        XCTAssertNil(old.media?.showTitle)
        XCTAssertNil(old.media?.episodeTitle)
    }

    /// A TV or anime day's id is (person << 44) | (Item << 20) | day ordinal, a signed 63-bit number: it must
    /// survive the JSON decoder exactly, as the id, the negative id and the page's address.
    func testASixtyThreeBitIdSurvivesTheDecoder() throws {
        let person = 5000
        let itemId = 123_456
        let ordinal = 739_800
        let address = (person << 44) | (itemId << 20) | ordinal
        XCTAssertGreaterThanOrEqual(address, 1 << 44)

        let tv = try item(row(id: -address, objectId: address, mediaType: "tv", title: "T9"))
        XCTAssertEqual(tv.id, -address)
        XCTAssertEqual(tv.object.id, address)
        XCTAssertEqual(tv.post, .progress(address))
        XCTAssertEqual(ActivityDestination.resolve(tv), .progress(address))

        let widest = (1 << 62) + 123
        let near = try item(row(id: -widest, objectId: widest))
        XCTAssertEqual(near.object.id, widest)
        XCTAssertEqual(near.id, -widest)

        let top = Int.max - 1
        XCTAssertEqual(try item(row(id: -top, objectId: top)).object.id, top)
    }

    func testAListRowsMediaCarriesTheShowAndEpisodeTitles() throws {
        let log = try item(row(
            id: 12,
            type: "diary_created",
            objectType: "diary",
            mediaType: "episode",
            title: "Pilot",
            summary: nil,
            label: nil,
            mediaExtra: #", "show_title": "T9", "episode_title": "Pilot""#,
            seasonNumber: 1,
            episodeNumber: 2
        ))
        XCTAssertEqual(log.media?.showTitle, "T9")
        XCTAssertEqual(log.media?.episodeTitle, "Pilot")
    }

    func testTheResponseReadsTheTopLineTheTotalAndTheUndatedLine() throws {
        let response = try decode(
            ActivityCursorResponse.self,
            """
            {"next_cursor": null, "previous_cursor": null, "results": [],
             "top_line": "360 pages",
             "total": {"value": 1540, "unit": "pages", "known": 8, "total": 9, "approximate": false, "time_label": null},
             "undated": {"amount": 350, "unit": "pages", "titles": 1, "told": 350,
                         "label": "Includes 1 book and 350 pages with no date"}}
            """
        )
        XCTAssertEqual(response.topLine, "360 pages")
        XCTAssertEqual(response.total?.value, 1540)
        XCTAssertEqual(response.total?.unit, "pages")
        XCTAssertEqual(response.total?.known, 8)
        XCTAssertEqual(response.total?.approximate, false)
        XCTAssertEqual(response.undated?.titles, 1)
        XCTAssertEqual(
            ActivityListPresentation.undatedLine(response.undated),
            "Includes 1 book and 350 pages with no date"
        )
    }

    func testAPlainListOrALoggedOutCallerGetsNoneOfThem() throws {
        let response = try decode(
            ActivityCursorResponse.self,
            #"{"next_cursor": null, "previous_cursor": null, "results": [], "top_line": null, "total": null, "undated": null}"#
        )
        XCTAssertNil(response.topLine)
        XCTAssertNil(response.total)
        XCTAssertNil(response.undated)

        let older = try decode(ActivityCursorResponse.self, #"{"next_cursor": null, "previous_cursor": null, "results": []}"#)
        XCTAssertNil(older.topLine)
        XCTAssertNil(older.undated)
    }

    /// A field of a shape the app doesn't know never costs the list its rows.
    func testAnUnexpectedTotalShapeStillLoadsTheRows() throws {
        let response = try decode(
            ActivityCursorResponse.self,
            """
            {"next_cursor": null, "previous_cursor": null, "results": [\(row(id: -9))],
             "total": {"value": "lots", "unit": 7}, "undated": {"titles": "two", "label": "Includes a book with no date"}}
            """
        )
        XCTAssertEqual(response.results.count, 1)
        XCTAssertNil(response.total?.value)
        XCTAssertNil(response.undated?.titles)
        XCTAssertEqual(response.undated?.label, "Includes a book with no date")
    }

    func testAHeaderDetailReadsItsStreakRange() throws {
        let detail = try decode(
            StatsHeaderDetail.self,
            """
            {"key": "longest_weekly_streak", "label": "Longest weekly streak", "value": 3, "unit": "week",
             "approximate": false, "drill": null, "range": {"start": "2026-05-01", "end": "2026-05-17"}}
            """
        )
        XCTAssertEqual(detail.range, StatsDayRange(start: "2026-05-01", end: "2026-05-17"))
        XCTAssertNil(detail.drill, "A friend gets the days, not the Diary's drill")

        let other = try decode(
            StatsHeaderDetail.self,
            #"{"key": "reviews", "label": "Reviews", "value": 1, "unit": null, "approximate": false, "drill": null, "range": null}"#
        )
        XCTAssertNil(other.range)
    }

    // MARK: The request

    func testTheFilterSendsItselfOnEveryPage() {
        XCTAssertEqual(
            ActivityListFilter.everything.queryItems.map { "\($0.name)=\($0.value ?? "")" },
            ["view=list"]
        )
        let filter = ActivityListFilter(mediaType: "book", startDate: "2026-01-01", endDate: "2026-12-31", counts: .titles)
        XCTAssertEqual(
            filter.queryItems.map { "\($0.name)=\($0.value ?? "")" },
            ["view=list", "media_type=book", "start_date=2026-01-01", "end_date=2026-12-31", "counts=titles"]
        )
        XCTAssertEqual(ActivityListCounts.multiTitleDays.rawValue, "multi_title_days")
    }

    func testTheRepositorySendsTheFilterAndTheCursor() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [ListRequestProtocol.self]
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: config)
        )
        client.tokenProvider.accessToken = "access"
        defer { client.tokenProvider.clear() }
        var seen: [URLComponents] = []
        ListRequestProtocol.handler = { request in
            seen.append(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!)
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                Data(#"{"next_cursor": null, "previous_cursor": null, "results": [], "top_line": "5 books"}"#.utf8)
            )
        }

        let filter = ActivityListFilter(mediaType: "book", startDate: "2026-01-01", endDate: "2026-12-31", counts: .titles)
        let response = try await APIActivityRepository(client: client).userActivityList(
            username: "reader",
            filter: filter,
            pageSize: 40,
            cursorLink: "https://example.com/api/v1/users/reader/activity/?cursor=abc"
        )

        XCTAssertEqual(response.topLine, "5 books")
        let query = Dictionary(uniqueKeysWithValues: (seen.first?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(seen.first?.path, "/api/v1/users/reader/activity/")
        XCTAssertEqual(query["view"], "list")
        XCTAssertEqual(query["media_type"], "book")
        XCTAssertEqual(query["start_date"], "2026-01-01")
        XCTAssertEqual(query["end_date"], "2026-12-31")
        XCTAssertEqual(query["counts"], "titles")
        XCTAssertEqual(query["page_size"], "40")
        XCTAssertEqual(query["cursor"], "abc")
    }

    // MARK: Words

    func testAProgressRowSaysTheServersWordsThenItsTime() throws {
        let tv = try item(row(
            id: -5,
            mediaType: "tv",
            title: "T9",
            summary: "watched 2 episodes",
            label: "2 episodes",
            extra: #", "amount": 120, "unit": "minute", "time_label": "2h""#
        ))
        XCTAssertEqual(ActivityListPresentation.caption(for: tv), "Watched 2 episodes · 2h")

        let paused = try item(row(id: -6, summary: "read 40 pages, paused", extra: #", "moves": ["paused"]"#))
        XCTAssertEqual(ActivityListPresentation.caption(for: paused), "Read 40 pages, paused")
    }

    func testAStatusOnlyDayIsTheMovesWordsAndIsSkippedByTheRail() throws {
        let stopped = try item(row(id: -7, summary: "picked back up", label: nil, extra: #", "moves": ["picked_back_up"]"#))
        XCTAssertTrue(ActivityListPresentation.isStatusOnly(stopped))
        XCTAssertEqual(ActivityListPresentation.caption(for: stopped), "Picked back up")

        let started = try item(row(id: -8, summary: "started reading", label: "Started", extra: #", "moves": []"#))
        XCTAssertFalse(ActivityListPresentation.isStatusOnly(started), "A bare start is a title's newest activity")

        let withProgress = try item(row(id: -9, summary: "read 40 pages, paused", extra: #", "moves": ["paused"]"#))
        XCTAssertFalse(ActivityListPresentation.isStatusOnly(withProgress))

        let oldServer = try item(row(id: -10, summary: nil, label: nil))
        XCTAssertFalse(ActivityListPresentation.isStatusOnly(oldServer), "No moves, so nothing to skip")

        let rail = ProfileRecentActivityRailModel.items(from: [stopped, withProgress])
        XCTAssertEqual(rail.map(\.id), [withProgress.id], "The rail shows the day that moved, not the stop")
    }

    func testALogRowSaysPlusWhatItAddedThenItsTime() throws {
        let book = try item(row(
            id: 12,
            type: "diary_created",
            objectType: "diary",
            summary: nil,
            label: "200 pages",
            extra: #", "rating": "4.5", "liked": true, "is_rewatch": false, "amount": 200, "unit": "page""#
        ))
        XCTAssertEqual(ActivityListPresentation.caption(for: book), "+200 pages")
        XCTAssertEqual(ActivityFeedPresentation.ratingSummary(for: book).stars, "4.5")
        XCTAssertTrue(ActivityFeedPresentation.ratingSummary(for: book).liked)

        let film = try item(row(
            id: 13,
            type: "diary_created",
            objectType: "diary",
            mediaType: "movie",
            summary: nil,
            label: nil,
            extra: #", "rating": "8.0", "amount": 135, "unit": "minute", "time_label": "2h 15m""#
        ))
        XCTAssertEqual(ActivityListPresentation.caption(for: film), "2h 15m", "A film log sends no label, only its time")

        let bare = try item(row(id: 14, type: "diary_created", objectType: "diary", summary: nil, label: nil))
        XCTAssertNil(ActivityListPresentation.caption(for: bare))
    }

    func testTVRowsAreNamedAsTheDiaryNamesThem() throws {
        let season = try item(row(
            id: 20,
            type: "diary_created",
            objectType: "diary",
            mediaType: "season",
            title: "Season 1",
            summary: nil,
            label: nil,
            mediaExtra: #", "show_title": "T9""#,
            seasonNumber: 1
        ))
        XCTAssertEqual(DiaryEntryRow.titleParts(for: season.media!).title, "T9 · Season 1")

        let episode = try item(row(
            id: 21,
            type: "diary_created",
            objectType: "diary",
            mediaType: "episode",
            title: "Second",
            summary: nil,
            label: nil,
            mediaExtra: #", "show_title": "T9", "episode_title": "Second""#,
            seasonNumber: 1,
            episodeNumber: 2
        ))
        XCTAssertEqual(DiaryEntryRow.titleParts(for: episode.media!).title, "T9 · S1 E2 · Second")

        let unnamed = try item(row(
            id: 22,
            type: "diary_created",
            objectType: "diary",
            mediaType: "episode",
            title: "T9",
            summary: nil,
            label: nil,
            seasonNumber: 1,
            episodeNumber: 3
        ))
        XCTAssertEqual(DiaryEntryRow.titleParts(for: unnamed.media!).title, "T9 · S1 E3")

        let show = try item(row(id: -23, mediaType: "tv", title: "T9"))
        XCTAssertEqual(DiaryEntryRow.titleParts(for: show.media!).title, "T9")
    }

    func testMonthsRunNewestFirstWithOneRowPerItem() throws {
        let items = try [
            item(row(id: -1, objectId: 1, day: "2026-06-02")),
            item(row(id: -2, objectId: 2, day: "2026-06-01", title: "Emma")),
            item(row(id: -3, objectId: 3, day: "2026-05-11", title: "Ulysses")),
        ]
        let sections = ActivityListPresentation.monthSections(from: items)
        XCTAssertEqual(sections.map(\.id), ["2026-06", "2026-05"])
        XCTAssertEqual(sections.map { $0.items.count }, [2, 1])
        XCTAssertEqual(sections.first?.items.map(\.id), [-1, -2])
    }

    func testTheTitleNamesTheScopeTheNumberWasTappedIn() {
        let books = ActivityListFilter(mediaType: "book")
        XCTAssertEqual(ActivityListPresentation.title(for: books, period: .year(2026)), "Books · 2026")
        XCTAssertEqual(ActivityListPresentation.title(for: .everything, period: .allTime), "All Media · All Time")
        XCTAssertEqual(ActivityListPresentation.title(for: .everything, period: nil), "Activity")
        XCTAssertEqual(ActivityListPresentation.topLine("  360 pages "), "360 pages")
        XCTAssertNil(ActivityListPresentation.topLine(nil))
        XCTAssertNil(ActivityListPresentation.topLine("  "))
    }

    // MARK: Numbers that open the list

    private let ownBooks2026 = StatsDrillDownContext(
        period: .year(2026), mediaType: "book", isOwnStats: true, activityUsername: "reader"
    )
    private let theirsGlobalAllTime = StatsDrillDownContext(
        period: .allTime, mediaType: nil, isOwnStats: false, activityUsername: "friend"
    )

    func testTheListOpensOnYourStatsAndAFriends() {
        let pages = StatsDrillDown.activity(counts: nil)
        XCTAssertEqual(pages.route(in: ownBooks2026), .activity(pages, ownBooks2026))
        XCTAssertEqual(pages.route(in: theirsGlobalAllTime), .activity(pages, theirsGlobalAllTime))
    }

    /// StatsView.username is nil on your own stats; without the profile's username the route can't say whose rows
    /// they are, so it opens nothing instead of someone else's.
    func testTheListNeedsWhoseActivityItIs() {
        let noOne = StatsDrillDownContext(period: .year(2026), mediaType: "book", isOwnStats: true)
        XCTAssertNil(StatsDrillDown.activity(counts: .titles).route(in: noOne))
    }

    func testTheFilterIsTheTabAndThePeriod() {
        let pages = StatsDrillDown.activity(counts: nil).activityFilter(in: ownBooks2026)
        XCTAssertEqual(pages, ActivityListFilter(mediaType: "book", startDate: "2026-01-01", endDate: "2026-12-31", counts: nil))
        XCTAssertEqual(StatsDrillDown.activity(counts: nil).activityTitle(in: ownBooks2026), "Books · 2026")

        let titles = StatsDrillDown.activity(counts: .titles).activityFilter(in: theirsGlobalAllTime)
        XCTAssertEqual(titles, ActivityListFilter(mediaType: nil, startDate: nil, endDate: nil, counts: .titles), "All Time sends no dates")
        XCTAssertEqual(StatsDrillDown.activity(counts: .titles).activityTitle(in: theirsGlobalAllTime), "All Media · All Time")
    }

    func testAStreakListsItsOwnDaysAndATimeRowItsOwnType() {
        let range = StatsDayRange(start: "2026-06-01", end: "2026-06-02")
        let streak = StatsDrillDown.activity(counts: .days, range: range).activityFilter(in: ownBooks2026)
        XCTAssertEqual(streak, ActivityListFilter(mediaType: "book", startDate: "2026-06-01", endDate: "2026-06-02", counts: .days))

        let movies = StatsDrillDown.activity(counts: nil, mediaType: "movie")
        XCTAssertEqual(movies.activityFilter(in: theirsGlobalAllTime)?.mediaType, "movie")
        XCTAssertEqual(movies.activityTitle(in: theirsGlobalAllTime), "Movies · All Time")
    }

    func testADiaryDrillHasNoActivityFilter() {
        XCTAssertNil(StatsDrillDown.liked.activityFilter(in: ownBooks2026))
        XCTAssertFalse(StatsDrillDown.stat(label: "Logs").opensActivityList)
        XCTAssertTrue(StatsDrillDown.activity(counts: .hours).opensActivityList)
    }

    func testDetailRowsOpenTheListsTheyAddUp() {
        func detail(_ key: String, range: StatsDayRange? = nil) -> StatsHeaderDetail {
            StatsHeaderDetail(key: key, label: key, value: 3, unit: nil, approximate: false, drill: nil, range: range)
        }
        let days = StatsDayRange(start: "2026-06-01", end: "2026-06-02")
        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("days_with_two_plus_titles")), .activity(counts: .multiTitleDays))
        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("episodes")), .activity(counts: .episodes))
        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("current_daily_streak", range: days)), .activity(counts: .days, range: days))
        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("longest_weekly_streak", range: days)), .activity(counts: .weeks, range: days))
        XCTAssertEqual(StatsHeaderSection.drillDown(for: detail("current_weekly_streak", range: days)), .activity(counts: .weeks, range: days))
        XCTAssertNil(StatsHeaderSection.drillDown(for: detail("seasons_finished")), "Seasons finished stays untappable")
        XCTAssertNil(StatsHeaderSection.drillDown(for: detail("did_not_finish")), "Did not finish stays untappable")
        XCTAssertNil(StatsHeaderSection.drillDown(for: detail("reads")), "Reads stays untappable")
    }

    func testLogsRereadsReviewsAndLikesStayInTheDiary() {
        let drills = ["rewatches": ["repeat": "true"], "reviews": ["has_review": "true"], "likes": ["liked": "true"]]
        for (key, drill) in drills {
            let detail = StatsHeaderDetail(key: key, label: key, value: 3, unit: nil, approximate: false, drill: drill)
            XCTAssertEqual(StatsHeaderSection.drillDown(for: detail)?.opensActivityList, false, key)
        }
        let header = StatsHeader(titles: 5, logs: 12, activeDays: 4, longestDailyStreak: 2, details: [])
        XCTAssertEqual(StatsHeaderSection.counts(for: header, mediaType: nil)[1].drillDown, .stat(label: "Logs"))
    }

    func testGlobalsNumbersOpenTheListWithNoType() {
        let header = StatsHeader(titles: 23, logs: 30, activeDays: 11, longestDailyStreak: 2, details: [])
        let counts = StatsHeaderSection.counts(for: header, mediaType: nil)
        XCTAssertEqual(counts[0].drillDown, .activity(counts: .titles))
        XCTAssertEqual(counts[2].drillDown, .activity(counts: .days), "Global's third number is Active days")
        XCTAssertNil(StatsDrillDown.activity(counts: .titles).activityFilter(in: theirsGlobalAllTime)?.mediaType)
    }

    // MARK: Paging and going stale

    func testTheFilterGoesWithEveryPageAndTheUndatedLineStaysOnTheFirst() async throws {
        let next = "https://example.com/api/v1/users/reader/activity/?cursor=two"
        let repository = ScriptedListRepository(pages: [
            ActivityCursorResponse(
                nextCursor: next,
                previousCursor: nil,
                results: [try item(row(id: -1, objectId: 1))],
                topLine: "2 books",
                total: nil,
                undated: ActivityUndated.fixture(label: "Includes 1 book with no date")
            ),
            ActivityCursorResponse(
                nextCursor: nil,
                previousCursor: nil,
                results: [try item(row(id: -2, objectId: 2, day: "2026-05-01"))],
                topLine: "2 books"
            ),
        ])
        let filter = ActivityListFilter(mediaType: "book", counts: .titles)
        let viewModel = ActivityFeedViewModel(
            username: "reader",
            filter: filter,
            pageSize: 1,
            activityRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.load()
        XCTAssertEqual(viewModel.topLine, "2 books")
        XCTAssertEqual(viewModel.undated?.label, "Includes 1 book with no date")
        await viewModel.loadNextPageIfNeeded(currentItem: viewModel.items[0])

        XCTAssertEqual(repository.requests.map(\.filter), [filter, filter], "Page 2 sends the filter again")
        XCTAssertEqual(repository.requests.map(\.cursorLink), [nil, next])
        XCTAssertEqual(viewModel.items.map(\.id), [-1, -2])
        XCTAssertEqual(viewModel.undated?.label, "Includes 1 book with no date", "A later page doesn't take the line away")
    }

    func testAStatusOnlyRowAndAnUndatedLineWithNoRowsBothShow() async throws {
        let repository = ScriptedListRepository(pages: [
            ActivityCursorResponse(
                nextCursor: nil,
                previousCursor: nil,
                results: [try item(row(id: -7, summary: "stopped", label: nil, extra: #", "moves": ["stopped"]"#))],
                topLine: "1 book"
            ),
            ActivityCursorResponse(
                nextCursor: nil,
                previousCursor: nil,
                results: [],
                topLine: "0 books",
                total: nil,
                undated: ActivityUndated.fixture(label: "Includes 2 books with no date")
            ),
        ])
        let viewModel = ActivityFeedViewModel(
            username: "reader",
            filter: ActivityListFilter(mediaType: "book", counts: .titles),
            activityRepository: repository,
            onUnauthorized: {}
        )
        await viewModel.load()
        XCTAssertEqual(ActivityListPresentation.monthSections(from: viewModel.items).flatMap(\.items).count, 1)
        XCTAssertEqual(ActivityListPresentation.caption(for: viewModel.items[0]), "Stopped")

        await viewModel.refresh()
        XCTAssertTrue(viewModel.items.isEmpty)
        XCTAssertEqual(ActivityListPresentation.undatedLine(viewModel.undated), "Includes 2 books with no date")
    }

    func testASaveMarksTheListStaleAndItReloadsOnTheNextAppearance() async throws {
        let repository = ScriptedListRepository(pages: [
            ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [try item(row(id: -1, objectId: 1))]),
            ActivityCursorResponse(
                nextCursor: nil,
                previousCursor: nil,
                results: [try item(row(id: -2, objectId: 2, day: "2026-06-03")), try item(row(id: -1, objectId: 1))]
            ),
        ])
        let viewModel = ActivityFeedViewModel(
            username: "reader",
            filter: .everything,
            activityRepository: repository,
            onUnauthorized: {}
        )
        await viewModel.reloadIfStale()
        XCTAssertTrue(repository.requests.isEmpty, "Nothing loaded yet, nothing stale")

        await viewModel.loadIfNeeded()
        await viewModel.reloadIfStale()
        XCTAssertEqual(repository.requests.count, 1, "Fresh, so it doesn't walk again")

        viewModel.markStale()
        XCTAssertEqual(repository.requests.count, 1, "A write only marks it")
        await viewModel.reloadIfStale()
        XCTAssertEqual(viewModel.items.map(\.id), [-2, -1], "The new row is at the top with no pull")
        await viewModel.reloadIfStale()
        XCTAssertEqual(repository.requests.count, 2, "Reloading clears the mark")
    }

    func testARepositoryWithNoListEndpointFallsBackToThePlainPage() async throws {
        let repository = PlainOnlyRepository()
        let response = try await repository.userActivityList(
            username: "reader",
            filter: .everything,
            pageSize: 5,
            cursorLink: nil
        )
        XCTAssertTrue(response.results.isEmpty)
        XCTAssertEqual(repository.plainCalls, 1)
    }
}

// MARK: - Test doubles

private extension ActivityUndated {
    static func fixture(label: String) -> ActivityUndated {
        // Built through the decoder: the type reads leniently and has no memberwise init.
        try! JSONDecoder.api.decode(ActivityUndated.self, from: Data(#"{"label": "\#(label)"}"#.utf8))
    }
}

private struct ListRequest: Equatable {
    let username: String
    let filter: ActivityListFilter
    let cursorLink: String?
}

private final class ScriptedListRepository: ActivityRepository {
    private var pages: [ActivityCursorResponse]
    private(set) var requests: [ListRequest] = []

    init(pages: [ActivityCursorResponse]) {
        self.pages = pages
    }

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] {
        fatalError("The list uses userActivityList")
    }

    func userActivityList(
        username: String,
        filter: ActivityListFilter,
        pageSize _: Int,
        cursorLink: String?
    ) async throws -> ActivityCursorResponse {
        requests.append(ListRequest(username: username, filter: filter, cursorLink: cursorLink))
        return pages.removeFirst()
    }

    func feedPage(pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        fatalError("Not the feed")
    }
}

private final class PlainOnlyRepository: ActivityRepository {
    private(set) var plainCalls = 0

    func userActivity(username _: String, limit _: Int) async throws -> [ActivityItem] { [] }

    func userActivityPage(username _: String, pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        plainCalls += 1
        return ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [])
    }

    func feedPage(pageSize _: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        ActivityCursorResponse(nextCursor: nil, previousCursor: nil, results: [])
    }
}

private final class ListRequestProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with _: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else { return }
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
