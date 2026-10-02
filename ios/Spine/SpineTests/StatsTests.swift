import Foundation
import SwiftUI
import XCTest
@testable import Spine

@MainActor
final class StatsTests: XCTestCase {
    override func tearDown() {
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

    func testStatsRatingChartNormalizesSparseHalfSteps() {
        let points = SWStatsRatingChart.normalizedPoints(from: [
            SWStatsRatingPoint(rating: 3.5, count: 1),
            SWStatsRatingPoint(rating: 8, count: 2),
            SWStatsRatingPoint(rating: 8, count: 1),
            SWStatsRatingPoint(rating: 4.2, count: 99),
            SWStatsRatingPoint(rating: .nan, count: 99),
            SWStatsRatingPoint(rating: .infinity, count: 99),
            SWStatsRatingPoint(rating: 5, count: -1),
        ])

        XCTAssertEqual(points.count, 21)
        XCTAssertEqual(points[7].rating, 3.5)
        XCTAssertEqual(points[7].count, 1)
        XCTAssertEqual(points[16].rating, 8)
        XCTAssertEqual(points[16].count, 3)
        XCTAssertEqual(points.reduce(0) { $0 + $1.count }, 4)
    }

    func testStatsRatingChartCombinesNativeRatingScalesOnTenPointAxis() {
        let points = SWStatsRatingChart.normalizedPoints(from: [
            StatsMediaTypeSummary(
                mediaType: "book",
                ratingDistribution: [StatsRatingBucket(rating: "3.5", count: 1)]
            ),
            StatsMediaTypeSummary(
                mediaType: "tv",
                ratingDistribution: [StatsRatingBucket(rating: "8.0", count: 1)]
            ),
        ])

        XCTAssertEqual(points[14].count, 1)
        XCTAssertEqual(points[16].count, 1)
        XCTAssertEqual(SWStatsRatingChart.averageRating(from: points), 7.5)
    }

    func testStatsYearChartFillsGapsIntoContinuousTimeline() {
        let points = SWStatsYearChart.filledPoints(from: [
            SWStatsYearPoint(year: 2022, count: 3),
            SWStatsYearPoint(year: 2019, count: 1),
            SWStatsYearPoint(year: 2022, count: 2),
            SWStatsYearPoint(year: 2020, count: -4),
        ])

        XCTAssertEqual(points.map(\.year), [2019, 2020, 2021, 2022])
        XCTAssertEqual(points.map(\.count), [1, 0, 0, 5])
    }

    func testStatsYearChartKeepsSingleYearAsOnePoint() {
        let points = SWStatsYearChart.filledPoints(from: [SWStatsYearPoint(year: 2024, count: 4)])

        XCTAssertEqual(points, [SWStatsYearPoint(year: 2024, count: 4)])
        XCTAssertTrue(SWStatsYearChart.filledPoints(from: []).isEmpty)
    }

    func testStatsYearChartFindsPeakDecade() {
        let decade = SWStatsYearChart.peakDecade(from: [
            SWStatsYearPoint(year: 1974, count: 1),
            SWStatsYearPoint(year: 1999, count: 4),
            SWStatsYearPoint(year: 2012, count: 3),
            SWStatsYearPoint(year: 2018, count: 3),
        ])

        XCTAssertEqual(decade, StatsDecade(decade: 2010, count: 6))
        XCTAssertEqual(decade?.title, "2010s")
        XCTAssertNil(SWStatsYearChart.peakDecade(from: [SWStatsYearPoint(year: 2000, count: 0)]))
    }

    func testStatsReleaseSeriesGroupsLongHistoriesByDecade() {
        let series = StatsReleaseSeries.make(points: [
            SWStatsYearPoint(year: 1946, count: 1),
            SWStatsYearPoint(year: 1988, count: 5),
            SWStatsYearPoint(year: 2012, count: 3),
            SWStatsYearPoint(year: 2018, count: 4),
            SWStatsYearPoint(year: 2025, count: 6),
        ])

        XCTAssertEqual(series.granularity, .decade)
        XCTAssertEqual(series.buckets.map(\.id), ["1940s", "1950s", "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"])
        XCTAssertEqual(series.buckets.map(\.count), [1, 0, 0, 0, 5, 0, 0, 7, 6])
        XCTAssertEqual(series.buckets.first?.axisLabel, "’40s")
        XCTAssertEqual(series.buckets.last?.axisLabel, "’20s")
        XCTAssertEqual(series.peak?.id, "2010s")
    }

    func testStatsReleaseSeriesKeepsShortHistoriesYearByYear() {
        let series = StatsReleaseSeries.make(points: [
            SWStatsYearPoint(year: 2019, count: 2),
            SWStatsYearPoint(year: 2025, count: 5),
            SWStatsYearPoint(year: 2022, count: 5),
        ])

        XCTAssertEqual(series.granularity, .year)
        XCTAssertEqual(series.buckets.map(\.id), ["2019", "2020", "2021", "2022", "2023", "2024", "2025"])
        XCTAssertEqual(series.peak?.id, "2025")
        XCTAssertNil(StatsReleaseSeries.make(points: []).peak)
    }

    func testStatsRatingStarBucketsFoldTenPointScaleOntoHalfStars() {
        let points = SWStatsRatingChart.normalizedPoints(from: [
            SWStatsRatingPoint(rating: 7.5, count: 2),
            SWStatsRatingPoint(rating: 8, count: 1),
            SWStatsRatingPoint(rating: 0.5, count: 1),
            SWStatsRatingPoint(rating: 0, count: 1),
            SWStatsRatingPoint(rating: 10, count: 3),
        ])

        let buckets = SWStatsRatingChart.starBuckets(from: points)

        XCTAssertEqual(buckets.map(\.step), Array(1 ... 10))
        XCTAssertEqual(buckets[0].count, 2)
        XCTAssertEqual(buckets[7].count, 3)
        XCTAssertEqual(buckets[7].stars, 4)
        XCTAssertEqual(buckets[9].count, 3)
        XCTAssertEqual(buckets.reduce(0) { $0 + $1.count }, 8)
    }

    func testStatsActivitySeriesChartsEveryMonthOfASelectedYear() {
        let range = StatsRange(startDate: "2025-01-01", endDate: "2025-12-31", timezone: "UTC", isAllTime: false)
        let series = StatsActivitySeries.make(
            months: [
                StatsActivityMonth(month: "2025-03", count: 4),
                StatsActivityMonth(month: "2025-11", count: 9),
                StatsActivityMonth(month: "2024-11", count: 50),
            ],
            range: range,
            now: Self.date("2026-10-01"),
            calendar: Self.utcCalendar
        )

        XCTAssertEqual(series.granularity, .month)
        XCTAssertEqual(series.buckets.count, 12)
        XCTAssertEqual(series.buckets.first?.id, "2025-01")
        XCTAssertEqual(series.buckets.last?.id, "2025-12")
        XCTAssertEqual(series.buckets[2].count, 4)
        XCTAssertEqual(series.total, 13)
        XCTAssertEqual(series.busiest?.id, "2025-11")
        XCTAssertTrue(series.buckets.allSatisfy { $0.axisLabel != nil })
    }

    func testStatsActivitySeriesChartsAllTimeByYearThroughCurrentYear() {
        let series = StatsActivitySeries.make(
            months: [
                StatsActivityMonth(month: "2019-02", count: 2),
                StatsActivityMonth(month: "2019-07", count: 3),
                StatsActivityMonth(month: "2023-01", count: 7),
            ],
            range: .empty,
            now: Self.date("2026-10-01"),
            calendar: Self.utcCalendar
        )

        XCTAssertEqual(series.granularity, .year)
        XCTAssertEqual(series.buckets.map(\.id), ["2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"])
        XCTAssertEqual(series.buckets.map(\.count), [5, 0, 0, 0, 7, 0, 0, 0])
        XCTAssertEqual(series.buckets.first?.axisLabel, "2019")
        XCTAssertEqual(series.buckets.last?.axisLabel, "2026")
        XCTAssertEqual(series.busiest?.id, "2023")
    }

    func testStatsActivitySeriesUsesTrailingMonthsForShortAllTimeHistory() {
        let series = StatsActivitySeries.make(
            months: [
                StatsActivityMonth(month: "2025-12", count: 2),
                StatsActivityMonth(month: "2026-10", count: 6),
            ],
            range: .empty,
            now: Self.date("2026-10-01"),
            calendar: Self.utcCalendar
        )

        XCTAssertEqual(series.granularity, .month)
        XCTAssertEqual(series.buckets.count, 12)
        XCTAssertEqual(series.buckets.first?.id, "2025-11")
        XCTAssertEqual(series.buckets.last?.id, "2026-10")
        XCTAssertEqual(series.buckets[1].count, 2)
        XCTAssertEqual(series.total, 8)
        XCTAssertTrue(StatsActivitySeries.make(months: [], range: .empty).buckets.isEmpty)
    }

    func testStatsActivityAxisLabelsKeepEndsAndAvoidCrowding() {
        XCTAssertEqual(StatsActivitySeries.axisLabelIndices(count: 4, maximumLabels: 5), [0, 1, 2, 3])
        XCTAssertEqual(StatsActivitySeries.axisLabelIndices(count: 16, maximumLabels: 5), [0, 4, 8, 12, 15])
        XCTAssertEqual(StatsActivitySeries.axisLabelIndices(count: 8, maximumLabels: 5), [0, 2, 4, 7])
        XCTAssertTrue(StatsActivitySeries.axisLabelIndices(count: 0, maximumLabels: 5).isEmpty)
    }

    func testStatsWeekdayDistributionCountsActiveDaysMondayFirst() {
        let counts = StatsWeekdayDistribution.activeDays(from: [
            StatsActivityDay(date: "2026-09-28", count: 3),
            StatsActivityDay(date: "2026-10-04", count: 1),
            StatsActivityDay(date: "2026-10-05", count: 2),
            StatsActivityDay(date: "2026-10-02", count: 0),
            StatsActivityDay(date: "not-a-date", count: 4),
        ])

        XCTAssertEqual(counts, [2, 0, 0, 0, 0, 0, 1])
    }

    func testStatsMixBarKeepsTinySlicesVisibleWithinWidth() {
        let widths = StatsMixBar.widths(for: [761, 2, 216], totalWidth: 306, spacing: 3, minimum: 5)

        XCTAssertEqual(widths[1], 5)
        XCTAssertEqual(widths.reduce(0, +), 300, accuracy: 0.001)
        XCTAssertGreaterThan(widths[0], widths[2])
        XCTAssertEqual(StatsMixBar.widths(for: [0, 0], totalWidth: 100, spacing: 3, minimum: 5), [0, 0])
    }

    func testStatsChartSelectionRoundsAndClampsPlotIndices() {
        XCTAssertEqual(SWStatsChartSelection.index(for: -2, count: 10), 0)
        XCTAssertEqual(SWStatsChartSelection.index(for: 4.6, count: 10), 5)
        XCTAssertEqual(SWStatsChartSelection.index(for: 20, count: 10), 9)
        XCTAssertNil(SWStatsChartSelection.index(for: .nan, count: 10))
        XCTAssertNil(SWStatsChartSelection.index(for: 3, count: 0))
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

    func testStatsViewModelLoadsContentAndTracksRequest() async {
        let expected = Self.summary(completedCount: 3)
        let repository = StatsProfileRepositoryFake { _, _ in expected }
        let viewModel = StatsViewModel(
            profileRepository: repository,
            username: "mika",
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertEqual(viewModel.state, .loaded(expected))
        XCTAssertEqual(viewModel.summary, expected)
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertEqual(repository.requests, [StatsFakeRequest(username: "mika", period: .allTime)])
    }

    func testStatsViewModelSurfacesEmptyState() async {
        let repository = StatsProfileRepositoryFake { _, _ in StatsSummary() }
        let viewModel = StatsViewModel(profileRepository: repository, onUnauthorized: {})

        await viewModel.load()

        XCTAssertEqual(viewModel.state, .empty)
        XCTAssertNil(viewModel.summary)
    }

    func testStatsViewModelSurfacesErrorAndUnauthorizedCallback() async {
        var didCallUnauthorized = false
        let repository = StatsProfileRepositoryFake { _, _ in throw APIError.unauthorized }
        let viewModel = StatsViewModel(
            profileRepository: repository,
            onUnauthorized: { didCallUnauthorized = true }
        )

        await viewModel.load()

        guard case let .error(message) = viewModel.state else {
            return XCTFail("Expected an error state")
        }
        XCTAssertFalse(message.isEmpty)
        XCTAssertEqual(viewModel.errorMessage, message)
        XCTAssertTrue(didCallUnauthorized)
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

    func testSpineImageRetriesOnlyCancelledLoadsUpToTheCap() {
        let cancelled = AsyncImagePhase.failure(URLError(.cancelled))
        let missing = AsyncImagePhase.failure(URLError(.fileDoesNotExist))

        XCTAssertTrue(SpineImageRetryPolicy.shouldRetry(phase: cancelled, retries: 0))
        XCTAssertFalse(SpineImageRetryPolicy.shouldRetry(
            phase: cancelled,
            retries: SpineImageRetryPolicy.maxCancellationRetries
        ))
        XCTAssertFalse(SpineImageRetryPolicy.shouldRetry(phase: missing, retries: 0))
        XCTAssertFalse(SpineImageRetryPolicy.shouldRetry(phase: .empty, retries: 0))
    }

    func testStatsMostLoggedViewModelPagesAndPrefetchesNearTheEnd() async throws {
        let firstPage = try Self.mostLoggedPage(
            ids: Array(1 ... 48),
            count: 50,
            next: "https://example.com/api/v1/stats/me/most-logged/?page=2&page_size=48"
        )
        let secondPage = try Self.mostLoggedPage(ids: [48, 49, 50], count: 50, next: nil)
        let repository = StatsMostLoggedRepositoryFake { request in
            request.page == nil ? firstPage : secondPage
        }
        let viewModel = StatsMostLoggedViewModel(
            profileRepository: repository,
            username: nil,
            period: .year(2025),
            mediaType: "movie",
            expectedTotal: 12,
            onUnauthorized: {}
        )

        XCTAssertEqual(viewModel.totalCount, 12)
        await viewModel.load()
        XCTAssertEqual(viewModel.items.count, 48)
        XCTAssertEqual(viewModel.totalCount, 50)
        XCTAssertTrue(viewModel.hasMorePages)

        await viewModel.loadNextPageIfNeeded(currentItemID: viewModel.items[10].id)
        XCTAssertEqual(repository.requests.count, 1)

        await viewModel.loadNextPageIfNeeded(currentItemID: viewModel.items[44].id)
        XCTAssertEqual(repository.requests.map(\.page), [nil, "2"])
        XCTAssertEqual(repository.requests.map(\.mediaType), ["movie", "movie"])
        XCTAssertEqual(repository.requests.map(\.pageSize), [48, 48])
        XCTAssertEqual(repository.requests.first?.period, .year(2025))
        XCTAssertEqual(viewModel.items.count, 50)
        XCTAssertFalse(viewModel.hasMorePages)
    }

    func testStatsMostLoggedViewModelSurfacesErrorsAndUnauthorized() async {
        var didCallUnauthorized = false
        let repository = StatsMostLoggedRepositoryFake { _ in throw APIError.unauthorized }
        let viewModel = StatsMostLoggedViewModel(
            profileRepository: repository,
            username: "mika",
            period: .allTime,
            mediaType: nil,
            onUnauthorized: { didCallUnauthorized = true }
        )

        await viewModel.load()

        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertTrue(viewModel.items.isEmpty)
        XCTAssertFalse(viewModel.isLoadingInitial)
        XCTAssertTrue(didCallUnauthorized)
        XCTAssertEqual(repository.requests.first?.username, "mika")
    }

    func testStatsViewModelIgnoresStaleRequestAfterPeriodChange() async {
        let firstRequestStarted = expectation(description: "All-time request started")
        let repository = StatsProfileRepositoryFake { _, period in
            switch period {
            case .allTime:
                firstRequestStarted.fulfill()
                try await Task<Never, Never>.sleep(nanoseconds: 80_000_000)
                return Self.summary(completedCount: 1)
            case .year:
                return Self.summary(completedCount: 2)
            }
        }
        let viewModel = StatsViewModel(profileRepository: repository, onUnauthorized: {})

        let firstLoad = Task { await viewModel.load() }
        await fulfillment(of: [firstRequestStarted], timeout: 1)
        await viewModel.selectPeriod(.year(2026))
        await firstLoad.value

        XCTAssertEqual(viewModel.selectedPeriod, .year(2026))
        XCTAssertEqual(viewModel.summary?.overview.completedCount, 2)
        XCTAssertEqual(repository.requests, [
            StatsFakeRequest(username: nil, period: .allTime),
            StatsFakeRequest(username: nil, period: .year(2026)),
        ])
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

    private static func mostLoggedPage(ids: [Int], count: Int, next: String?) throws -> PagedResponse<StatsMostLoggedItem> {
        try JSONDecoder.api.decode(
            PagedResponse<StatsMostLoggedItem>.self,
            from: mostLoggedPageData(ids: ids, count: count, next: next)
        )
    }

    private static func summary(completedCount: Int) -> StatsSummary {
        StatsSummary(overview: StatsOverview(completedCount: completedCount))
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

private struct StatsMostLoggedFakeRequest: Equatable {
    let username: String?
    let period: StatsPeriod
    let mediaType: String?
    let page: String?
    let pageSize: Int
}

private final class StatsMostLoggedRepositoryFake: ProfileRepository {
    typealias Handler = (StatsMostLoggedFakeRequest) async throws -> PagedResponse<StatsMostLoggedItem>

    private(set) var requests: [StatsMostLoggedFakeRequest] = []
    private let handler: Handler

    init(handler: @escaping Handler) {
        self.handler = handler
    }

    func statsMostLogged(
        username: String?,
        period: StatsPeriod,
        mediaType: String?,
        page: String?,
        pageSize: Int
    ) async throws -> PagedResponse<StatsMostLoggedItem> {
        let request = StatsMostLoggedFakeRequest(
            username: username,
            period: period,
            mediaType: mediaType,
            page: page,
            pageSize: pageSize
        )
        requests.append(request)
        return try await handler(request)
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
