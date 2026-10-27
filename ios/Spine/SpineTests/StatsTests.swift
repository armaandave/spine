import Foundation
import XCTest
@testable import Spine

@MainActor
final class StatsTests: XCTestCase {
    private var statsDefaults: UserDefaults?
    private var statsDefaultsName: String?

    override func tearDown() {
        if let statsDefaultsName {
            statsDefaults?.removePersistentDomain(forName: statsDefaultsName)
        }
        statsDefaults = nil
        statsDefaultsName = nil
        StatsRequestCaptureURLProtocol.handler = nil
        KeychainTokenStore.shared.clear()
        super.tearDown()
    }

    private static var utcCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        calendar.locale = Locale(identifier: "en_US_POSIX")
        return calendar
    }

    private static func date(_ value: String) -> Date {
        StatsActivityDay.parse(value)!
    }

    func testStatsPeriodProvidesStableIdentityTitlesAndQueries() {
        XCTAssertEqual(StatsPeriod.allTime.id, "all-time")
        XCTAssertEqual(StatsPeriod.allTime.title, "All Time")
        XCTAssertEqual(queryDictionary(StatsPeriod.allTime.query), [
            "start_date": "all",
            "end_date": "all",
        ])

        let year = StatsPeriod.year(2026)
        XCTAssertEqual(year.id, "year-2026")
        XCTAssertEqual(year.title, "2026")
        XCTAssertEqual(queryDictionary(year.query), [
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        ])
    }

    func testStatsPeriodBuildsRecentYearsNewestFirst() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(secondsFromGMT: 0))
        let date = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 7, day: 14)))

        XCTAssertEqual(
            StatsPeriod.recentYears(count: 4, from: date, calendar: calendar),
            [.year(2026), .year(2025), .year(2024), .year(2023)]
        )
        XCTAssertEqual(StatsPeriod.recentYears(count: 0, from: date, calendar: calendar), [])
    }

    func testStatsSummaryDecodesVersionedContract() throws {
        let summary = try JSONDecoder.api.decode(StatsSummary.self, from: Self.fullSummaryData)

        XCTAssertEqual(summary.schemaVersion, 1)
        XCTAssertTrue(summary.range.isAllTime)
        XCTAssertEqual(summary.range.timezone, "America/Los_Angeles")
        XCTAssertEqual(summary.overview.trackedCount, 42)
        XCTAssertEqual(summary.overview.completedCount, 31)
        XCTAssertEqual(summary.overview.reviewCount, 4)
        XCTAssertEqual(summary.overview.numericAverageRating, 8.25)
        XCTAssertEqual(summary.activity.days.first?.parsedDate, Self.utcDate(year: 2026, month: 7, day: 13))
        XCTAssertEqual(summary.activity.mostActiveWeekday?.name, "Sunday")
        XCTAssertEqual(summary.activity.mostActiveWeekday?.percentage, 42.5)
        XCTAssertEqual(summary.ratingDistribution.first?.numericRating, 8)
        XCTAssertEqual(summary.releaseYears.first?.year, 1999)
        XCTAssertEqual(summary.topGenres.first?.name, "Drama")
        XCTAssertEqual(summary.metadataCoverage.genreItems, 30)
        XCTAssertEqual(summary.topRated.first?.media.title, "Fight Club")
        XCTAssertEqual(summary.topRated.first?.rating, "9.5")
        XCTAssertEqual(summary.mostLogged.first?.logCount, 5)

        let movies = try XCTUnwrap(summary.mediaTypeSummary(for: "movie"))
        XCTAssertEqual(movies.statuses["completed"], 31)
        XCTAssertEqual(movies.numericAverageRating, 8.5)
        XCTAssertEqual(movies.topLanguages.first?.name, "English")
        XCTAssertEqual(movies.metadataCoverage.releaseYearItems, 28)
        XCTAssertFalse(summary.isEmpty)
    }

    func testMusicStatsFixtureDrivesEveryBucketAndExcludesSongs() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "range": {"start_date": null, "end_date": null, "timezone": "UTC", "is_all_time": true},
              "overview": {
                "tracked_count": 3,
                "completed_count": 2,
                "diary_entry_count": 4,
                "unique_logged_count": 2,
                "review_count": 1,
                "repeat_count": 1,
                "rated_count": 2,
                "average_rating": "8.5",
                "liked_count": 1
              },
              "media_types": [
                {
                  "media_type": "music",
                  "tracked_count": 3,
                  "completed_count": 2,
                  "diary_entry_count": 4,
                  "unique_logged_count": 2,
                  "review_count": 1,
                  "repeat_count": 1,
                  "rated_count": 2,
                  "average_rating": "8.5",
                  "liked_count": 1,
                  "statuses": {"in_progress": 1, "completed": 2},
                  "rating_distribution": [{"rating": "9.0", "count": 2}],
                  "release_years": [{"year": 2007, "count": 3}],
                  "top_genres": [{"name": "Industrial", "count": 2}],
                  "top_languages": [{"name": "English", "count": 3}],
                  "metadata_coverage": {"total_items": 3, "release_year_items": 3, "genre_items": 2, "language_items": 3},
                  "top_rated": [
                    {
                      "media": {
                        "ref": {"item_id": 902, "source": "musicbrainz", "media_type": "music", "media_id": "album", "season_number": null, "episode_number": null},
                        "title": "Year Zero",
                        "poster_url": "https://example.com/year-zero.jpg",
                        "poster_orientation": "square"
                      },
                      "rating": "9.0"
                    }
                  ],
                  "most_logged": [
                    {
                      "media": {
                        "ref": {"item_id": 902, "source": "musicbrainz", "media_type": "music", "media_id": "album", "season_number": null, "episode_number": null},
                        "title": "Year Zero",
                        "poster_url": "https://example.com/year-zero.jpg",
                        "poster_orientation": "square"
                      },
                      "log_count": 4
                    }
                  ]
                }
              ],
              "activity": {"days": [{"date": "2026-07-13", "count": 1}], "months": [{"month": "2026-07", "count": 1}]},
              "rating_distribution": [{"rating": "9.0", "count": 2}],
              "release_years": [{"year": 2007, "count": 3}],
              "top_genres": [{"name": "Industrial", "count": 2}],
              "top_languages": [{"name": "English", "count": 3}],
              "metadata_coverage": {"total_items": 3, "release_year_items": 3, "genre_items": 2, "language_items": 3},
              "diary_top_rated": [
                {
                  "media": {
                    "ref": {"item_id": 902, "source": "musicbrainz", "media_type": "music", "media_id": "album", "season_number": null, "episode_number": null},
                    "title": "Year Zero",
                    "poster_url": "https://example.com/year-zero.jpg",
                    "poster_orientation": "square"
                  },
                  "rating": "9.0"
                }
              ],
              "most_logged": [
                {
                  "media": {
                    "ref": {"item_id": 902, "source": "musicbrainz", "media_type": "music", "media_id": "album", "season_number": null, "episode_number": null},
                    "title": "Year Zero",
                    "poster_url": "https://example.com/year-zero.jpg",
                    "poster_orientation": "square"
                  },
                  "log_count": 4
                }
              ]
            }
            """.utf8
        )

        let summary = try JSONDecoder.api.decode(StatsSummary.self, from: data)
        let music = try XCTUnwrap(summary.mediaTypeSummary(for: "music"))

        XCTAssertEqual(summary.overview.trackedCount, 3)
        XCTAssertEqual(summary.overview.diaryEntryCount, 4)
        XCTAssertEqual(summary.mediaTypes.map(\.mediaType), ["music"])
        XCTAssertFalse(summary.mediaTypes.contains { $0.mediaType == "song" })
        XCTAssertEqual(music.statuses, ["in_progress": 1, "completed": 2])
        XCTAssertEqual(music.ratingDistribution.first?.numericRating, 9)
        XCTAssertEqual(music.releaseYears.first?.year, 2007)
        XCTAssertEqual(music.topGenres.first?.name, "Industrial")
        XCTAssertEqual(music.topLanguages.first?.name, "English")
        XCTAssertEqual(music.topRated.first?.media.ref.mediaType, "music")
        XCTAssertEqual(music.topRated.first?.media.posterOrientation, .square)
        XCTAssertEqual(music.mostLogged.first?.logCount, 4)
        XCTAssertEqual(summary.ratingDistribution.first?.count, 2)
        XCTAssertEqual(summary.releaseYears.first?.count, 3)
        XCTAssertEqual(summary.topGenres.first?.count, 2)
        XCTAssertEqual(summary.topLanguages.first?.count, 3)
        XCTAssertEqual(summary.topRated.first?.media.ref.mediaType, "music")
        XCTAssertEqual(summary.mostLogged.first?.media.ref.mediaType, "music")
        XCTAssertEqual(MediaTypeTheme.theme(for: "music").statsColor, MediaTypeTheme.theme(for: "music").accentColor)
    }

    func testStatsSummaryDefaultsAdditiveFieldsAndTreatsZeroBucketsAsEmpty() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "rating_distribution": [
                {"rating": "0.0", "count": 0},
                {"rating": "0.5", "count": 0}
              ],
              "media_types": [
                {
                  "media_type": "movie",
                  "rating_distribution": [{"rating": "0.0", "count": 0}]
                }
              ]
            }
            """.utf8
        )

        let summary = try JSONDecoder.api.decode(StatsSummary.self, from: data)

        XCTAssertEqual(summary.range, .empty)
        XCTAssertEqual(summary.overview, .empty)
        XCTAssertEqual(summary.activity, .empty)
        XCTAssertEqual(summary.mediaTypes.first?.trackedCount, 0)
        XCTAssertTrue(summary.mediaTypes.first?.isEmpty == true)
        XCTAssertTrue(summary.isEmpty)
    }

    func testStatsSummaryDoesNotTreatPlannedPausedOrDroppedTitlesAsCompletedContent() {
        let planned = StatsSummary(
            overview: StatsOverview(trackedCount: 12, likedCount: 2),
            mediaTypes: [
                StatsMediaTypeSummary(
                    mediaType: "movie",
                    trackedCount: 12,
                    likedCount: 2,
                    statuses: ["planning": 10, "paused": 1, "dropped": 1]
                ),
            ]
        )
        let completed = StatsSummary(
            overview: StatsOverview(trackedCount: 12, completedCount: 1),
            mediaTypes: [
                StatsMediaTypeSummary(
                    mediaType: "movie",
                    trackedCount: 12,
                    completedCount: 1,
                    statuses: ["completed": 1, "planning": 11]
                ),
            ]
        )

        XCTAssertTrue(planned.isEmpty)
        XCTAssertFalse(completed.isEmpty)
    }

    func testYearWithOnlyCurrentLibrarySnapshotIsEmpty() throws {
        let data = Data(
            """
            {
              "schema_version": 1,
              "range": {
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
                "timezone": "UTC",
                "is_all_time": false
              },
              "overview": {"tracked_count": 42, "completed_count": 30, "liked_count": 8},
              "media_types": [{"media_type": "movie", "tracked_count": 42, "completed_count": 30, "liked_count": 8}]
            }
            """.utf8
        )

        let summary = try JSONDecoder.api.decode(StatsSummary.self, from: data)

        XCTAssertTrue(summary.isEmpty)
        XCTAssertEqual(summary.range.parsedStartDate, Self.utcDate(year: 2025, month: 1, day: 1))
        XCTAssertEqual(summary.range.parsedEndDate, Self.utcDate(year: 2025, month: 12, day: 31))
    }

    func testAPIProfileRepositoryRequestsOwnAllTimeStatsWithAuthentication() async throws {
        let repository = makeAPIRepository()
        repository.client.tokenProvider.accessToken = "stats-access"
        StatsRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(components.path, "/api/v1/stats/me/summary/")
            XCTAssertEqual(self.queryDictionary(components.queryItems ?? []), [
                "start_date": "all",
                "end_date": "all",
            ])
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer stats-access")
            return Self.response(for: request, data: Self.emptySummaryData)
        }

        let summary = try await repository.statsSummary(username: nil, period: .allTime)

        XCTAssertTrue(summary.isEmpty)
    }

    func testAPIProfileRepositoryRequestsUserYearStats() async throws {
        let repository = makeAPIRepository()
        repository.client.tokenProvider.accessToken = "stats-access"
        StatsRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            XCTAssertEqual(components.path, "/api/v1/users/mika/stats/summary/")
            XCTAssertEqual(self.queryDictionary(components.queryItems ?? []), [
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
            ])
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer stats-access")
            return Self.response(for: request, data: Self.emptySummaryData)
        }

        _ = try await repository.statsSummary(username: "mika", period: .year(2025))
    }

    func testStatsSummaryDecodesMostLoggedTotalsWithPreviewFallback() throws {
        let summary = try JSONDecoder.api.decode(StatsSummary.self, from: Data(
            """
            {
              "most_logged": [\(Self.mostLoggedJSON(id: 1, logCount: 3))],
              "most_logged_total": 37,
              "media_types": [
                {"media_type": "movie", "most_logged": [], "most_logged_total": 20},
                {"media_type": "book", "most_logged": [\(Self.mostLoggedJSON(id: 2, logCount: 2))]}
              ]
            }
            """.utf8
        ))
        let legacy = try JSONDecoder.api.decode(StatsSummary.self, from: Self.fullSummaryData)

        XCTAssertEqual(summary.mostLoggedTotal, 37)
        XCTAssertEqual(summary.mediaTypeSummary(for: "movie")?.mostLoggedTotal, 20)
        XCTAssertEqual(summary.mediaTypeSummary(for: "book")?.mostLoggedTotal, 1)
        XCTAssertEqual(legacy.mostLoggedTotal, legacy.mostLogged.count)
    }

    func testAPIProfileRepositoryRequestsMostLoggedPages() async throws {
        let repository = makeAPIRepository()
        repository.client.tokenProvider.accessToken = "stats-access"
        var paths: [String] = []
        var queries: [[String: String]] = []
        StatsRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            paths.append(components.path)
            queries.append(self.queryDictionary(components.queryItems ?? []))
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer stats-access")
            return Self.response(for: request, data: Self.mostLoggedPageData(ids: [7], count: 1, next: nil))
        }

        let own = try await repository.statsMostLogged(
            username: nil,
            period: .allTime,
            mediaType: nil,
            page: nil,
            pageSize: 48
        )
        _ = try await repository.statsMostLogged(
            username: "mika",
            period: .year(2025),
            mediaType: "movie",
            page: "2",
            pageSize: 48
        )

        XCTAssertEqual(paths, ["/api/v1/stats/me/most-logged/", "/api/v1/users/mika/stats/most-logged/"])
        XCTAssertEqual(queries, [
            ["start_date": "all", "end_date": "all", "page_size": "48"],
            ["start_date": "2025-01-01", "end_date": "2025-12-31", "page_size": "48", "media_type": "movie", "page": "2"],
        ])
        XCTAssertEqual(own.results.first?.logCount, 2)
    }

    func testAPIClientDefaultSessionNeverReplaysCachedResponses() {
        let configuration = APIClient.defaultSession.configuration

        XCTAssertNil(configuration.urlCache)
        XCTAssertEqual(configuration.requestCachePolicy, .reloadIgnoringLocalCacheData)
    }

    // MARK: - v2 requests

    func testAPIProfileRepositoryRequestsSchemaTwoWithMediaType() async throws {
        let repository = makeAPIRepository()
        repository.client.tokenProvider.accessToken = "stats-access"
        var paths: [String] = []
        var queries: [[String: String]] = []
        StatsRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            paths.append(components.path)
            queries.append(self.queryDictionary(components.queryItems ?? []))
            return Self.response(for: request, data: Data(#"{"schema_version": 2, "media_type": "movie", "years": {"first": 2019, "current": 2026}}"#.utf8))
        }

        let own = try await repository.statsSummary(username: nil, period: .year(2025), mediaType: "movie")
        _ = try await repository.statsSummary(username: "mika", period: .allTime, mediaType: nil)

        XCTAssertEqual(paths, ["/api/v1/stats/me/summary/", "/api/v1/users/mika/stats/summary/"])
        XCTAssertEqual(queries, [
            ["schema": "2", "start_date": "2025-01-01", "end_date": "2025-12-31", "media_type": "movie"],
            ["schema": "2", "start_date": "all", "end_date": "all"],
        ])
        XCTAssertFalse(own.isLegacy)
        XCTAssertEqual(own.mediaType, "movie")
        XCTAssertEqual(own.years, StatsYears(first: 2019, current: 2026))
    }

    func testAPIProfileRepositoryScopesALegacyAnswerToTheAskedTab() async throws {
        let repository = makeAPIRepository()
        repository.client.tokenProvider.accessToken = "stats-access"
        StatsRequestCaptureURLProtocol.handler = { request in
            Self.response(for: request, data: Self.fullSummaryData)
        }

        let page = try await repository.statsSummary(username: nil, period: .allTime, mediaType: "movie")

        XCTAssertTrue(page.isLegacy)
        XCTAssertEqual(page.mediaType, "movie")
        XCTAssertNil(page.activity, "Legacy activity isn't split by type, so a type tab hides it")
        XCTAssertNil(page.ratings)
    }

    func testAPIProfileRepositoryRequestsShowAllPages() async throws {
        let repository = makeAPIRepository()
        repository.client.tokenProvider.accessToken = "stats-access"
        var paths: [String] = []
        var queries: [[String: String]] = []
        StatsRequestCaptureURLProtocol.handler = { request in
            let components = try XCTUnwrap(URLComponents(url: try XCTUnwrap(request.url), resolvingAgainstBaseURL: false))
            paths.append(components.path)
            queries.append(self.queryDictionary(components.queryItems ?? []))
            switch components.path {
            case let path where path.hasSuffix("/highest-rated/"):
                return Self.response(for: request, data: Data("""
                {"count": 1, "next": null, "previous": null, "results": [
                  {"media": \(Self.mediaJSON(id: 3)), "stars": "4.5", "day": null}
                ]}
                """.utf8))
            case let path where path.hasSuffix("/collections/"):
                return Self.response(for: request, data: Data("""
                {"count": 1, "next": null, "previous": null, "results": [
                  {"id": "119", "source": "tmdb", "media_type": "movie", "name": "The Lord of the Rings Collection",
                   "item_count": 3, "poster_urls": [], "completion": {"completed_count": 3, "total_count": 3}}
                ]}
                """.utf8))
            default:
                return Self.response(for: request, data: Data("""
                {"count": 1, "next": null, "previous": null, "results": [{"media": \(Self.mediaJSON(id: 4)), "times": 3}]}
                """.utf8))
            }
        }

        let rated = try await repository.statsHighestRated(
            username: nil, period: .year(2025), mediaType: "movie", page: "2", pageSize: 48
        )
        let collections = try await repository.statsCollections(
            username: "mika", mediaType: "movie", group: .almost, page: nil, pageSize: 25
        )
        let mostLogged = try await repository.statsMostLoggedTitles(
            username: nil, period: .allTime, mediaType: nil, page: nil, pageSize: 48
        )

        XCTAssertEqual(paths, [
            "/api/v1/stats/me/highest-rated/",
            "/api/v1/users/mika/stats/collections/",
            "/api/v1/stats/me/most-logged/",
        ])
        XCTAssertEqual(queries, [
            ["start_date": "2025-01-01", "end_date": "2025-12-31", "page_size": "48", "media_type": "movie", "page": "2"],
            ["media_type": "movie", "group": "almost", "page_size": "25"],
            ["schema": "2", "start_date": "all", "end_date": "all", "page_size": "48"],
        ])
        XCTAssertEqual(rated.results.first?.stars?.value, 4.5)
        XCTAssertNil(rated.results.first?.day)
        XCTAssertEqual(collections.results.first?.completion?.completedCount, 3)
        XCTAssertEqual(mostLogged.results.first?.times, 3)
    }

    func testLegacyOnlyRepositoryAnswersTheV2CallWithTheFallbackPage() async throws {
        let legacy = StatsSummary(overview: StatsOverview(diaryEntryCount: 9, uniqueLoggedCount: 7))
        let repository = StatsProfileRepositoryFake { _, _ in legacy }

        let page = try await repository.statsSummary(username: "mika", period: .year(2025), mediaType: nil)

        XCTAssertTrue(page.isLegacy)
        XCTAssertEqual(page.header?.titles, 7)
        XCTAssertEqual(page.header?.logs, 9)
        XCTAssertEqual(repository.requests, [StatsFakeRequest(username: "mika", period: .year(2025))])
    }

    // MARK: - View model

    func testViewModelLoadsTheFirstPageAtOnce() async {
        let repository = StatsPageRepositoryFake { scope in Self.page(for: scope) }
        let viewModel = makeViewModel(repository, debounce: .seconds(30))

        XCTAssertTrue(viewModel.isFirstLoad)
        viewModel.appeared()
        XCTAssertTrue(viewModel.isLoading)
        XCTAssertFalse(viewModel.isDimmed, "Nothing on screen to dim")
        await settle(viewModel)

        XCTAssertEqual(repository.scopes, [StatsScope(period: .allTime, mediaType: nil)])
        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .allTime, mediaType: nil))
        XCTAssertEqual(viewModel.page?.header?.titles, Self.page(for: .init(period: .allTime)).header?.titles)
        XCTAssertFalse(viewModel.isFirstLoad)

        viewModel.appeared()
        await settle(viewModel)
        XCTAssertEqual(repository.scopes.count, 1, "Appearing again with a page on screen doesn't reload")
    }

    func testViewModelDebouncesFastSwitchesIntoOneRequest() async {
        let repository = StatsPageRepositoryFake { scope in Self.page(for: scope) }
        let viewModel = makeViewModel(repository, debounce: .milliseconds(80))
        viewModel.appeared()
        await settle(viewModel)

        viewModel.selectPeriod(.year(2025))
        viewModel.selectPeriod(.year(2024))
        viewModel.selectMediaType("movie")

        XCTAssertEqual(viewModel.selected, StatsScope(period: .year(2024), mediaType: "movie"), "The selection changes at once")
        XCTAssertTrue(viewModel.isDimmed)
        await settle(viewModel)

        XCTAssertEqual(repository.scopes, [
            StatsScope(period: .allTime, mediaType: nil),
            StatsScope(period: .year(2024), mediaType: "movie"),
        ])
        XCTAssertEqual(viewModel.pageScope, viewModel.selected)
        XCTAssertEqual(viewModel.userSwitchCount, 3)
    }

    func testViewModelKeepsTheOldPageDimmedUntilTheNewOneArrives() async {
        let gate = RefreshGate()
        let repository = StatsPageRepositoryFake { scope in
            if scope.period == .year(2025) { await gate.wait() }
            return Self.page(for: scope)
        }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)
        let allTime = viewModel.page

        viewModel.selectPeriod(.year(2025))
        await waitUntil { repository.scopes.count == 2 }

        XCTAssertEqual(viewModel.page, allTime)
        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .allTime, mediaType: nil))
        XCTAssertTrue(viewModel.isLoading)
        XCTAssertTrue(viewModel.isDimmed)

        await gate.open()
        await settle(viewModel)

        XCTAssertFalse(viewModel.isDimmed)
        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .year(2025), mediaType: nil))
    }

    func testViewModelDropsAnAnswerForAScopeNoLongerSelected() async {
        let gate = RefreshGate()
        let repository = StatsPageRepositoryFake { scope in
            // Ignores cancellation, like a response already on its way.
            if scope.period == .year(2025) { await gate.wait() }
            return Self.page(for: scope)
        }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)

        viewModel.selectPeriod(.year(2025))
        await waitUntil { repository.scopes.count == 2 }
        viewModel.selectPeriod(.year(2024))
        await waitUntil { viewModel.pageScope?.period == .year(2024) }
        await gate.open()
        // Give the dropped answer time to arrive.
        try? await Task.sleep(for: .milliseconds(60))
        await settle(viewModel)

        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .year(2024), mediaType: nil))
        XCTAssertEqual(viewModel.page?.header?.titles, 2024)
        XCTAssertFalse(viewModel.isLoading)
    }

    func testViewModelShowsACachedScopeWithoutARequest() async {
        let repository = StatsPageRepositoryFake { scope in Self.page(for: scope) }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)
        viewModel.selectPeriod(.year(2025))
        await settle(viewModel)

        viewModel.selectPeriod(.allTime)

        XCTAssertFalse(viewModel.isLoading)
        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .allTime, mediaType: nil))
        await settle(viewModel)
        XCTAssertEqual(repository.scopes.count, 2)
    }

    func testPageCacheDropsTheLeastRecentlyUsedScope() {
        var cache = StatsPageCache(capacity: 2)
        let a = StatsScope(period: .allTime, mediaType: nil)
        let b = StatsScope(period: .year(2025), mediaType: nil)
        let c = StatsScope(period: .year(2025), mediaType: "movie")

        cache.insert(Self.page(for: a), for: a)
        cache.insert(Self.page(for: b), for: b)
        XCTAssertNotNil(cache.page(for: a)) // a is now the most recent
        cache.insert(Self.page(for: c), for: c)

        XCTAssertNil(cache.page(for: b))
        XCTAssertNotNil(cache.page(for: a))
        XCTAssertNotNil(cache.page(for: c))
        XCTAssertEqual(StatsViewModel.cacheCapacity, 12)
    }

    func testViewModelErrorKeepsThePageAndShowsABanner() async {
        let repository = StatsPageRepositoryFake { scope in
            if scope.period == .year(2023) { throw APIError.invalidResponse }
            return Self.page(for: scope)
        }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)

        viewModel.selectPeriod(.year(2023))
        await settle(viewModel)

        XCTAssertNotNil(viewModel.bannerMessage)
        XCTAssertNil(viewModel.fullErrorMessage)
        XCTAssertEqual(viewModel.errorScope, StatsScope(period: .year(2023), mediaType: nil))
        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .allTime, mediaType: nil), "The old page stays")
        XCTAssertFalse(viewModel.isDimmed, "It's undimmed once the load has failed")
        XCTAssertEqual(viewModel.selectedPeriod, .year(2023), "The pill stays where the user put it")
        XCTAssertEqual(viewModel.bannerTitle, "Couldn't load All Media · 2023")

        viewModel.selectPeriod(.year(2024))
        XCTAssertNil(viewModel.bannerMessage, "A new load clears the banner")
        await settle(viewModel)
        XCTAssertNil(viewModel.bannerMessage)
    }

    func testAFailedTabSwitchNamesTheTabInTheBanner() async {
        let repository = StatsPageRepositoryFake { scope in
            if scope.mediaType == "movie" { throw APIError.invalidResponse }
            return Self.page(for: scope)
        }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)

        viewModel.selectMediaType("movie")
        await settle(viewModel)

        XCTAssertEqual(viewModel.selectedMediaType, "movie")
        XCTAssertEqual(viewModel.pageScope, StatsScope(period: .allTime, mediaType: nil), "The old page stays")
        XCTAssertEqual(viewModel.bannerTitle, "Couldn't load Movies · All Time")
    }

    func testViewModelErrorWithNothingOnScreenIsTheWholeStateAndRetries() async {
        var didCallUnauthorized = false
        var fails = true
        let repository = StatsPageRepositoryFake { scope in
            if fails { throw APIError.unauthorized }
            return Self.page(for: scope)
        }
        let viewModel = StatsViewModel(
            profileRepository: repository,
            debounce: .zero,
            defaults: defaults,
            onUnauthorized: { didCallUnauthorized = true }
        )
        viewModel.appeared()
        await settle(viewModel)

        XCTAssertNotNil(viewModel.fullErrorMessage)
        XCTAssertNil(viewModel.bannerMessage)
        XCTAssertFalse(viewModel.isFirstLoad)
        XCTAssertTrue(didCallUnauthorized)

        fails = false
        viewModel.retry()
        await settle(viewModel)
        XCTAssertNil(viewModel.fullErrorMessage)
        XCTAssertNotNil(viewModel.page)
    }

    func testChangeNotificationsReloadOnceOnTheNextAppear() async {
        let repository = StatsPageRepositoryFake { scope in Self.page(for: scope) }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)
        viewModel.selectPeriod(.year(2025))
        await settle(viewModel)

        viewModel.markNeedsRefresh()
        viewModel.markNeedsRefresh()
        XCTAssertEqual(repository.scopes.count, 2, "Nothing reloads until the page appears")

        viewModel.appeared()
        XCTAssertFalse(viewModel.isDimmed, "A reload of the same scope stays undimmed")
        await settle(viewModel)
        viewModel.appeared()
        await settle(viewModel)

        XCTAssertEqual(repository.scopes.count, 3)
        XCTAssertEqual(repository.scopes.last, StatsScope(period: .year(2025), mediaType: nil))

        viewModel.selectPeriod(.allTime)
        await settle(viewModel)
        XCTAssertEqual(repository.scopes.count, 4, "The change also emptied the cache")
    }

    func testUserSwitchCountMovesOnlyOnTheUsersOwnSwitches() async {
        let repository = StatsPageRepositoryFake { scope in Self.page(for: scope) }
        let viewModel = makeViewModel(repository)
        viewModel.appeared()
        await settle(viewModel)
        await viewModel.refresh()
        viewModel.retry()
        await settle(viewModel)
        viewModel.selectPeriod(.allTime)
        viewModel.selectMediaType("")
        XCTAssertEqual(viewModel.userSwitchCount, 0)

        viewModel.selectMediaType("tv")
        viewModel.selectMediaType(nil)
        XCTAssertEqual(viewModel.userSwitchCount, 2)
    }

    func testViewModelCachesYearsAndTypesPerPerson() async {
        let repository = StatsPageRepositoryFake { scope in
            StatsPage(
                years: StatsYears(first: 2014, current: 2026),
                mediaTypes: [
                    StatsMediaTypeCount(mediaType: "movie", titles: 3, logs: 4),
                    StatsMediaTypeCount(mediaType: "book", titles: 1, logs: 1),
                ],
                header: StatsHeader(titles: 1, logs: 1, activeDays: 1, longestDailyStreak: 1, details: [])
            )
        }
        let now = { Self.date("2026-05-01") }
        let first = StatsViewModel(profileRepository: repository, username: "mika", debounce: .zero, defaults: defaults, now: now, onUnauthorized: {})
        XCTAssertEqual(first.mediaTypes, APIConstants.fallbackMediaTypes)
        first.appeared()
        await settle(first)
        XCTAssertEqual(first.periods.first, .allTime)
        XCTAssertEqual(first.periods.last, .year(2014))
        XCTAssertEqual(first.mediaTypes, ["movie", "book"])

        // Before its first response, the next visit shows the cached pills and types.
        let again = StatsViewModel(profileRepository: repository, username: "mika", defaults: defaults, now: now, onUnauthorized: {})
        XCTAssertEqual(again.periods.count, 1 + 13)
        XCTAssertEqual(again.periods.last, .year(2014))
        XCTAssertEqual(again.mediaTypes, ["movie", "book"])

        let someoneElse = StatsViewModel(profileRepository: repository, username: "zoe", defaults: defaults, now: now, onUnauthorized: {})
        XCTAssertEqual(
            someoneElse.periods,
            [.allTime] + StatsPeriod.recentYears(from: now()),
            "Nobody cached: the recent years"
        )
        XCTAssertEqual(someoneElse.mediaTypes, APIConstants.fallbackMediaTypes)
    }

    private var defaults: UserDefaults {
        if let suite = statsDefaults { return suite }
        let name = "StatsTests.\(UUID().uuidString)"
        let suite = UserDefaults(suiteName: name)!
        statsDefaults = suite
        statsDefaultsName = name
        return suite
    }

    private func makeViewModel(_ repository: ProfileRepository, debounce: Duration = .zero) -> StatsViewModel {
        StatsViewModel(profileRepository: repository, debounce: debounce, defaults: defaults, onUnauthorized: {})
    }

    /// Waits until the view model has no load running.
    private func settle(_ viewModel: StatsViewModel, file: StaticString = #filePath, line: UInt = #line) async {
        await waitUntil(file: file, line: line) { !viewModel.isLoading }
    }

    private func waitUntil(
        file: StaticString = #filePath,
        line: UInt = #line,
        _ condition: () -> Bool
    ) async {
        for _ in 0 ..< 400 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("Timed out waiting", file: file, line: line)
    }

    /// A page whose header titles name its scope: the year, or 1 for All Time.
    private static func page(for scope: StatsScope) -> StatsPage {
        let marker: Int
        switch scope.period {
        case .allTime: marker = 1
        case let .year(year): marker = year
        }
        return StatsPage(
            mediaType: scope.mediaType,
            header: StatsHeader(titles: marker, logs: marker, activeDays: 1, longestDailyStreak: 1, details: [])
        )
    }

    private static func mediaJSON(id: Int) -> String {
        """
        {"ref": {"item_id": \(id), "source": "tmdb", "media_type": "movie", "media_id": "\(id)", \
        "season_number": null, "episode_number": null}, "title": "Title \(id)", "poster_url": null}
        """
    }

    private func makeAPIRepository() -> APIProfileRepository {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StatsRequestCaptureURLProtocol.self]
        return APIProfileRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: .shared,
            session: URLSession(configuration: configuration)
        ))
    }

    private func queryDictionary(_ items: [URLQueryItem]) -> [String: String] {
        Dictionary(uniqueKeysWithValues: items.compactMap { item in
            item.value.map { (item.name, $0) }
        })
    }

    private static func mostLoggedJSON(id: Int, logCount: Int) -> String {
        """
        {"media": {"ref": {"item_id": \(id), "source": "tmdb", "media_type": "movie", "media_id": "\(id)", \
        "season_number": null, "episode_number": null}, "title": "Title \(id)", "poster_url": null}, \
        "log_count": \(logCount)}
        """
    }

    private static func mostLoggedPageData(ids: [Int], count: Int, next: String?) -> Data {
        let results = ids.map { mostLoggedJSON(id: $0, logCount: 2) }.joined(separator: ",")
        let nextValue = next.map { "\"\($0)\"" } ?? "null"
        return Data("{\"count\": \(count), \"next\": \(nextValue), \"previous\": null, \"results\": [\(results)]}".utf8)
    }

    private static func utcDate(year: Int, month: Int, day: Int) -> Date? {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return calendar.date(from: DateComponents(year: year, month: month, day: day))
    }

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

    private static let emptySummaryData = Data(
        """
        {
          "schema_version": 1,
          "range": {"start_date": null, "end_date": null, "timezone": "UTC", "is_all_time": true},
          "overview": {},
          "media_types": [],
          "activity": {"days": [], "months": []},
          "rating_distribution": [],
          "release_years": [],
          "top_genres": [],
          "top_languages": [],
          "metadata_coverage": {},
          "diary_top_rated": [],
          "most_logged": []
        }
        """.utf8
    )

    private static let fullSummaryData = Data(
        """
        {
          "schema_version": 1,
          "range": {
            "start_date": null,
            "end_date": null,
            "timezone": "America/Los_Angeles",
            "is_all_time": true
          },
          "overview": {
            "tracked_count": 42,
            "completed_count": 31,
            "diary_entry_count": 36,
            "unique_logged_count": 30,
            "review_count": 4,
            "repeat_count": 6,
            "rated_count": 28,
            "average_rating": "8.25",
            "liked_count": 12,
            "active_days": 20,
            "current_streak_days": 3,
            "longest_streak_days": 8
          },
          "media_types": [
            {
              "media_type": "movie",
              "tracked_count": 42,
              "completed_count": 31,
              "diary_entry_count": 36,
              "unique_logged_count": 30,
              "review_count": 4,
              "repeat_count": 6,
              "rated_count": 28,
              "average_rating": "8.5",
              "liked_count": 12,
              "statuses": {"completed": 31, "planning": 11},
              "rating_distribution": [{"rating": "8.0", "count": 4}],
              "top_rated": [
                {
                  "media": {
                    "ref": {"item_id": 42, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null},
                    "title": "Fight Club",
                    "poster_url": "https://example.com/fight-club.jpg"
                  },
                  "rating": "9.5"
                }
              ],
              "most_logged": [
                {
                  "media": {
                    "ref": {"item_id": 42, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null},
                    "title": "Fight Club",
                    "poster_url": "https://example.com/fight-club.jpg"
                  },
                  "log_count": 5
                }
              ],
              "release_years": [{"year": 1999, "count": 1}],
              "top_genres": [{"name": "Drama", "count": 18}],
              "top_languages": [{"name": "English", "count": 24}],
              "metadata_coverage": {"total_items": 31, "release_year_items": 28, "genre_items": 27, "language_items": 26}
            }
          ],
          "activity": {
            "days": [{"date": "2026-07-13", "count": 2}],
            "months": [{"month": "2026-07", "count": 8}],
            "active_days": 20,
            "current_streak_days": 3,
            "longest_streak_days": 8,
            "most_active_weekday": {"weekday": 6, "name": "Sunday", "active_day_count": 8, "percentage": 42.5}
          },
          "rating_distribution": [{"rating": "8.0", "count": 4}],
          "release_years": [{"year": 1999, "count": 1}],
          "top_genres": [{"name": "Drama", "count": 18}],
          "top_languages": [{"name": "English", "count": 24}],
          "metadata_coverage": {"total_items": 31, "release_year_items": 28, "genre_items": 30, "language_items": 29},
          "diary_top_rated": [
            {
              "media": {
                "ref": {"item_id": 42, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null},
                "title": "Fight Club",
                "poster_url": "https://example.com/fight-club.jpg"
              },
              "rating": "9.5"
            }
          ],
          "most_logged": [
            {
              "media": {
                "ref": {"item_id": 42, "source": "tmdb", "media_type": "movie", "media_id": "550", "season_number": null, "episode_number": null},
                "title": "Fight Club",
                "poster_url": "https://example.com/fight-club.jpg"
              },
              "log_count": 5
            }
          ]
        }
        """.utf8
    )
}

private struct StatsFakeRequest: Equatable {
    let username: String?
    let period: StatsPeriod
}

private final class StatsProfileRepositoryFake: ProfileRepository {
    typealias Handler = (String?, StatsPeriod) async throws -> StatsSummary

    private(set) var requests: [StatsFakeRequest] = []
    private let handler: Handler

    init(handler: @escaping Handler) {
        self.handler = handler
    }

    func statsSummary(username: String?, period: StatsPeriod) async throws -> StatsSummary {
        requests.append(StatsFakeRequest(username: username, period: period))
        return try await handler(username, period)
    }

    func me() async throws -> UserProfile { fatalError("Not used") }
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] { fatalError("Not used") }
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] { fatalError("Not used") }
}

private final class StatsPageRepositoryFake: ProfileRepository {
    typealias Handler = (StatsScope) async throws -> StatsPage

    private(set) var scopes: [StatsScope] = []
    private let handler: Handler

    init(handler: @escaping Handler) {
        self.handler = handler
    }

    func statsSummary(username: String?, period: StatsPeriod, mediaType: String?) async throws -> StatsPage {
        let scope = StatsScope(period: period, mediaType: mediaType)
        scopes.append(scope)
        return try await handler(scope)
    }

    func me() async throws -> UserProfile { fatalError("Not used") }
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] { fatalError("Not used") }
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] { fatalError("Not used") }
}

private final class StatsRequestCaptureURLProtocol: URLProtocol {
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
}
