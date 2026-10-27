import XCTest
@testable import Spine

/// Data shaping for the Stats chart and list sections: Activity, Ratings, Release years, Genres,
/// Languages, List progress and Collections.
@MainActor
final class StatsSectionsChartTests: XCTestCase {
    // MARK: - Release years (STAT-209)

    func testYearSeriesFillsEveryYearFromTheFirstToThisYear() {
        let rows = [
            year(2019, titles: 2, logs: 3),
            year(2022, titles: 5, logs: 5),
        ]

        let points = StatsReleaseYearsChartData.yearSeries(rows, currentYear: 2024)

        XCTAssertEqual(points.map(\.start), [2019, 2020, 2021, 2022, 2023, 2024])
        XCTAssertEqual(points.map(\.titles), [2, 0, 0, 5, 0, 0])
        XCTAssertEqual(points.map(\.label), ["2019", "2020", "2021", "2022", "2023", "2024"])
        XCTAssertNil(points[1].averageStars)
    }

    func testYearSeriesRunsPastThisYearWhenARowIsLater() {
        let points = StatsReleaseYearsChartData.yearSeries([year(2025), year(2027)], currentYear: 2026)

        XCTAssertEqual(points.map(\.start), [2025, 2026, 2027])
    }

    func testYearSeriesIsEmptyWithoutRows() {
        XCTAssertTrue(StatsReleaseYearsChartData.yearSeries([], currentYear: 2026).isEmpty)
        XCTAssertTrue(StatsReleaseYearsChartData.decadeSeries([], currentYear: 2026).isEmpty)
    }

    func testDecadesAddUpTheirYearsAndWeightTheAverageByRatedLogs() {
        let rows = [
            year(1987, titles: 2, logs: 2, rated: 2, average: "4.0", byType: ["movie": 2]),
            year(1991, titles: 3, logs: 4, rated: 3, average: "4.0", byType: ["movie": 2, "tv": 1]),
            year(1995, titles: 1, logs: 1, rated: 1, average: "2.0", byType: ["tv": 1]),
        ]

        let decades = StatsReleaseYearsChartData.decadeSeries(rows, currentYear: 2003)

        XCTAssertEqual(decades.map(\.label), ["1980s", "1990s", "2000s"])
        XCTAssertEqual(decades.map(\.start), [1980, 1990, 2000])
        XCTAssertEqual(decades.map(\.end), [1989, 1999, 2009])
        XCTAssertEqual(decades[1].titles, 4)
        XCTAssertEqual(decades[1].logs, 5)
        XCTAssertEqual(decades[1].rated, 4)
        XCTAssertEqual(decades[1].byType, ["movie": 2, "tv": 2])
        XCTAssertEqual(decades[1].averageStars ?? 0, 3.5, accuracy: 0.0001)
        XCTAssertEqual(decades[1].averageText, "3.5")
        XCTAssertEqual(decades[0].averageText, "4.0")
        XCTAssertEqual(decades[2].titles, 0)
        XCTAssertNil(decades[2].averageStars)
    }

    func testDecadeAverageKeepsTwoDecimals() {
        let rows = [
            year(2001, rated: 1, average: "3.3"),
            year(2002, rated: 1, average: "4.0"),
        ]

        let decade = StatsReleaseYearsChartData.decadeSeries(rows, currentYear: 2005)[0]

        XCTAssertEqual(decade.averageText, "3.7")
    }

    func testLongSpansFoldOlderYearsIntoOneBeforeBar() {
        let rows = [
            year(1488, titles: 1, logs: 1, rated: 1, average: "5.0", byType: ["book": 1]),
            year(1813, titles: 2, logs: 3, rated: 0, byType: ["book": 2]),
            year(2020, titles: 4, logs: 4),
        ]

        let years = StatsReleaseYearsChartData.yearSeries(rows, currentYear: 2025)
        XCTAssertEqual(years.count, StatsReleaseYearsChartData.maximumBars + 1)
        let before = years[0]
        XCTAssertTrue(before.isEarlier)
        XCTAssertEqual(before.label, "Before 1876")
        XCTAssertEqual([before.titles, before.logs, before.rated], [3, 4, 1])
        XCTAssertEqual(before.byType, ["book": 3])
        XCTAssertEqual(before.averageText, "5.0")
        XCTAssertEqual(years[1].start, 1876)
        XCTAssertEqual(years.last?.start, 2025)
        XCTAssertNil(StatsReleaseYearsChartData.drillDown(for: before), "The Before bar has no single range")
        XCTAssertFalse(StatsReleaseYearsChartData.axisLabels(years).contains("Before 1876"))
        XCTAssertLessThanOrEqual(StatsReleaseYearsChartData.axisLabels(years).count, 7)

        let ancient = [year(-750, titles: 1), year(2020, titles: 1)]
        let decades = StatsReleaseYearsChartData.decadeSeries(ancient, currentYear: 2025)
        XCTAssertEqual(decades.count, StatsReleaseYearsChartData.maximumBars + 1)
        XCTAssertEqual(decades[0].label, "Before 530")
        XCTAssertEqual(decades[1].label, "530s")
        XCTAssertEqual(decades.last?.label, "2020s")

        let short = StatsReleaseYearsChartData.yearSeries([year(1990), year(2019)], currentYear: 2019)
        XCTAssertFalse(short.contains { $0.isEarlier }, "A span that fits has no Before bar")
    }

    func testRatingsModeLeavesYearsWithoutRatingsEmpty() {
        let points = StatsReleaseYearsChartData.yearSeries(
            [year(2020, titles: 3, rated: 0), year(2021, titles: 1, rated: 1, average: "4.5")],
            currentYear: 2021
        )

        XCTAssertNil(points[0].value(for: .ratings))
        XCTAssertEqual(points[1].value(for: .ratings), 4.5)
        XCTAssertEqual(points[0].value(for: .titles), 3)

        let ratings = StatsReleaseYearsChartData.parts(points, mode: .ratings, stacked: true)
        XCTAssertEqual(ratings.map(\.label), ["2021"], "Only years with ratings get a bar")
        XCTAssertEqual(ratings.map(\.mediaType), [nil], "Ratings never stack")
        XCTAssertEqual(ratings.map(\.value), [4.5])
    }

    func testTitlesStackByTypeOnGlobalOnly() {
        let points = StatsReleaseYearsChartData.yearSeries(
            [year(2024, titles: 3, logs: 4, byType: ["tv": 1, "movie": 2])],
            currentYear: 2024
        )

        let stacked = StatsReleaseYearsChartData.parts(points, mode: .titles, stacked: true)
        XCTAssertEqual(stacked.map(\.mediaType), ["movie", "tv"])
        XCTAssertEqual(stacked.map(\.value), [2, 1])

        let logs = StatsReleaseYearsChartData.parts(points, mode: .logs, stacked: true)
        XCTAssertEqual(logs.map(\.mediaType), [nil])
        XCTAssertEqual(logs.map(\.value), [4])

        let typeTab = StatsReleaseYearsChartData.parts(points, mode: .titles, stacked: false)
        XCTAssertEqual(typeTab.map(\.mediaType), [nil])
        XCTAssertEqual(typeTab.map(\.value), [3])
    }

    func testEachModeNamesItsOwnPeak() {
        let points = StatsReleaseYearsChartData.yearSeries(
            [
                year(2010, titles: 5, logs: 5, rated: 4, average: "3.0"),
                year(2015, titles: 2, logs: 9, rated: 3, average: "4.5"),
                year(2018, titles: 1, logs: 1),
            ],
            currentYear: 2018
        )

        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(points, mode: .titles), "Most titles from 2010")
        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(points, mode: .logs), "Most logs from 2015")
        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(points, mode: .ratings), "Highest rated: 2015")

        let decades = StatsReleaseYearsChartData.decadeSeries(
            [year(2012, titles: 2, logs: 9), year(1995, titles: 4, logs: 4)],
            currentYear: 2018
        )
        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(decades, mode: .logs), "Most logs from the 2010s")
        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(decades, mode: .titles), "Most titles from the 1990s")

        let unrated = StatsReleaseYearsChartData.yearSeries([year(2018, titles: 2)], currentYear: 2018)
        XCTAssertNil(StatsReleaseYearsChartData.summaryTitle(unrated, mode: .ratings))
        XCTAssertEqual(StatsReleaseYearsChartData.emptyTitle(mode: .ratings), "Nothing rated yet")
        XCTAssertEqual(StatsReleaseYearsChartData.emptyTitle(mode: .titles), "No release years yet")
        XCTAssertEqual(StatsReleaseMode.allCases.map(\.title), ["Titles", "Ratings", "Logs"])
    }

    func testHighestRatedYearNeedsThreeRatings() {
        // One 5-star log of a 1942 film doesn't make 1942 the highest rated year.
        let points = StatsReleaseYearsChartData.yearSeries(
            [
                year(1942, titles: 1, logs: 1, rated: 1, average: "5.0"),
                year(2001, titles: 3, logs: 3, rated: 3, average: "3.5"),
                year(2010, titles: 4, logs: 4, rated: 4, average: "4.0"),
            ],
            currentYear: 2010
        )
        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(points, mode: .ratings), "Highest rated: 2010")

        let fewRatings = StatsReleaseYearsChartData.yearSeries(
            [year(1942, titles: 1, rated: 1, average: "5.0"), year(2001, titles: 2, rated: 2, average: "3.5")],
            currentYear: 2010
        )
        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(fewRatings, mode: .ratings), "Average stars by release year")
        XCTAssertEqual(
            StatsReleaseYearsChartData.parts(fewRatings, mode: .ratings, stacked: false).map(\.label),
            ["1942", "2001"],
            "Every rated year still gets its bar"
        )
    }

    func testAxisLabelsStepOnRoundYears() {
        let rows = [year(1990), year(2019)]
        let points = StatsReleaseYearsChartData.yearSeries(rows, currentYear: 2019)

        XCTAssertEqual(StatsReleaseYearsChartData.axisLabels(points), ["1990", "1995", "2000", "2005", "2010", "2015"])
    }

    func testPeakPrefersTheMostRecentOnATie() {
        let points = StatsReleaseYearsChartData.yearSeries(
            [year(2010, titles: 4), year(2015, titles: 4), year(2012, titles: 1)],
            currentYear: 2016
        )

        XCTAssertEqual(StatsReleaseYearsChartData.summaryTitle(points), "Most titles from 2015")
    }

    func testReleaseDrillDownsAreAYearOrADecade() {
        let years = StatsReleaseYearsChartData.yearSeries([year(1999)], currentYear: 1999)
        let decades = StatsReleaseYearsChartData.decadeSeries([year(1999)], currentYear: 1999)

        XCTAssertEqual(StatsReleaseYearsChartData.drillDown(for: years[0]), .releaseYear(1999))
        XCTAssertEqual(StatsReleaseYearsChartData.drillDown(for: decades[0]), .releaseDecade(1990))
    }

    func testReleaseDetailAndNewVsOlder() {
        let point = StatsReleaseYearsChartData.yearSeries(
            [year(1999, titles: 4, logs: 5, rated: 3, average: "4.0")],
            currentYear: 1999
        )[0]

        XCTAssertEqual(StatsReleaseYearsChartData.detail(point), "4 titles · 5 logs · 4.0 average")
        XCTAssertTrue(StatsReleaseYearsChartData.newVsOlderItems(StatsNewVsOlder(new: 0, recent: 0, older: 0)).isEmpty)
        XCTAssertEqual(
            StatsReleaseYearsChartData.newVsOlderItems(StatsNewVsOlder(new: 12, recent: 40, older: 58)).map(\.value),
            [12, 40, 58]
        )
    }

    // MARK: - Captions

    func testCoverageNoteOnlyWhenSomeTitlesAreMissing() {
        XCTAssertEqual(
            StatsChartCopy.coverageNote(StatsCoverage(known: 110, total: 120), qualifier: "with a release year"),
            "Based on 110 of 120 titles with a release year"
        )
        XCTAssertNil(StatsChartCopy.coverageNote(StatsCoverage(known: 120, total: 120), qualifier: "with a release year"))
        XCTAssertNil(StatsChartCopy.coverageNote(StatsCoverage(known: 0, total: 0), qualifier: ""))
        XCTAssertNil(StatsChartCopy.coverageNote(nil, qualifier: ""))
    }

    func testLanguagesNoteNamesTheTypesOnGlobalOnly() {
        let languages = StatsLanguages(
            rows: [language("English", titles: 140)],
            total: 14,
            coverage: StatsCoverage(known: 150, total: 158),
            mediaTypes: ["movie", "tv"]
        )

        XCTAssertEqual(
            StatsLanguagesSection.footnote(languages, mediaType: nil),
            "From movies and TV · Based on 150 of 158 titles with a language"
        )
        XCTAssertEqual(
            StatsLanguagesSection.footnote(languages, mediaType: "movie"),
            "Based on 150 of 158 titles with a language"
        )

        let complete = StatsLanguages(rows: [], total: 0, coverage: StatsCoverage(known: 3, total: 3), mediaTypes: ["movie"])
        XCTAssertNil(StatsLanguagesSection.footnote(complete, mediaType: "movie"))
        XCTAssertEqual(StatsLanguagesSection.footnote(complete, mediaType: nil), "From movies")
    }

    func testTypeNounsAndJoining() {
        XCTAssertEqual(StatsChartCopy.typesNote(["movie", "tv"]), "From movies and TV")
        XCTAssertEqual(StatsChartCopy.typesNote(["movie", "tv", "book"]), "From movies, TV and books")
        XCTAssertNil(StatsChartCopy.typesNote([]))
        XCTAssertEqual(StatsChartCopy.count(1, "title", "titles"), "1 title")
        XCTAssertEqual(StatsChartCopy.count(1200, "title", "titles"), "\(1200.formatted()) titles")
    }

    func testSpokenStars() {
        XCTAssertEqual(StatsChartCopy.spokenStars(0.5), "½ star")
        XCTAssertEqual(StatsChartCopy.spokenStars(1), "1 star")
        XCTAssertEqual(StatsChartCopy.spokenStars(3.5), "3½ stars")
        XCTAssertEqual(StatsChartCopy.spokenStars(5), "5 stars")
    }

    // MARK: - Ratings (STAT-204)

    func testRatingsAlwaysHaveTenBucketsInStarOrder() {
        let sent = [
            bucket("5.0", count: 12, min: "9.5", max: "10.0", byType: ["movie": 12]),
            bucket("3.5", count: 37, min: "6.5", max: "7.4", byType: ["movie": 30, "tv": 7]),
            bucket("0.5", count: 0, min: "0.0", max: "1.4"),
        ]

        let buckets = StatsRatingsChartData.buckets(sent)

        XCTAssertEqual(
            buckets.map(\.stars.text),
            ["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0"]
        )
        XCTAssertEqual(buckets[6].count, 37)
        XCTAssertEqual(buckets[9].count, 12)
        XCTAssertEqual(buckets[7].count, 0)
        // A missing bucket gets the contract's bounds; a sent one keeps the server's spelling.
        XCTAssertEqual(buckets[7].ratingMin.text, "7.5")
        XCTAssertEqual(buckets[7].ratingMax.text, "8.4")
        XCTAssertEqual(buckets[6].ratingMin.text, "6.5")
        XCTAssertEqual(buckets[9].ratingMax.text, "10.0")
    }

    func testRatingsBucketsTakeTheFirstOfADuplicateAndDropOffScaleStars() {
        let buckets = StatsRatingsChartData.buckets([
            bucket("4.0", count: 3, min: "7.5", max: "8.4"),
            bucket("4.0", count: 99, min: "7.5", max: "8.4"),
            bucket("6.0", count: 5, min: "11.5", max: "12.4"),
        ])

        XCTAssertEqual(buckets.count, 10)
        XCTAssertEqual(buckets[7].count, 3)
        XCTAssertEqual(buckets.map(\.count).reduce(0, +), 3)
    }

    func testRatingsBarsStackByTypeOnGlobal() {
        let buckets = StatsRatingsChartData.buckets([
            bucket("3.5", count: 37, min: "6.5", max: "7.4", byType: ["tv": 7, "movie": 30]),
        ])

        let stacked = StatsRatingsChartData.parts(buckets, stacked: true)
        XCTAssertEqual(stacked.map(\.mediaType), ["movie", "tv"])
        XCTAssertEqual(stacked.map(\.count), [30, 7])

        let single = StatsRatingsChartData.parts(buckets, stacked: false)
        XCTAssertEqual(single.map(\.count), [37])
        XCTAssertEqual(single.map(\.mediaType), [nil])
    }

    func testRatingsAxisShowsWholeStarsOnly() {
        XCTAssertNil(StatsRatingsChartData.axisLabel(forStars: "0.5"))
        XCTAssertEqual(StatsRatingsChartData.axisLabel(forStars: "1.0"), "1★")
        XCTAssertNil(StatsRatingsChartData.axisLabel(forStars: "3.5"))
        XCTAssertEqual(StatsRatingsChartData.axisLabel(forStars: "5.0"), "5★")
    }

    func testRatingsSummary() {
        let ratings = StatsRatings(averageStars: StatsDecimal("3.65"), rated: 140, distribution: [])
        XCTAssertEqual(StatsRatingsChartData.summary(ratings), "3.7 average · 140 ratings")
        XCTAssertEqual(
            StatsRatingsChartData.summary(StatsRatings(averageStars: nil, rated: 0, distribution: [])),
            "0 ratings"
        )
    }

    func testATapFindsTheStackedPartUnderIt() {
        let parts = [StatsTypeCount(mediaType: "movie", count: 30), StatsTypeCount(mediaType: "tv", count: 7)]

        XCTAssertEqual(statsStackedPart(at: 12, parts: parts), "movie")
        XCTAssertEqual(statsStackedPart(at: 30, parts: parts), "movie")
        XCTAssertEqual(statsStackedPart(at: 33, parts: parts), "tv")
        XCTAssertNil(statsStackedPart(at: 40, parts: parts), "Above the bar is the whole bar")
    }

    func testTypesStackInTheAppsTypeOrder() {
        let ordered = StatsChartStyle.orderedTypes(["game": 1, "movie": 3, "tv": 0, "anime": 2])

        XCTAssertFalse(ordered.map(\.mediaType).contains("tv"), "Types with nothing are left out")
        XCTAssertEqual(
            ordered.map(\.mediaType),
            StatsChartStyle.typeOrder.filter { ["movie", "anime", "game"].contains($0) }
        )
        XCTAssertEqual(StatsChartStyle.orderedTypes(["zzz": 1, "movie": 1]).map(\.mediaType), ["movie", "zzz"])
    }

    func testCountTicksAreWholeNumbers() {
        XCTAssertEqual(StatsChartStyle.countTicks(maximum: 0), [0, 1])
        XCTAssertEqual(StatsChartStyle.countTicks(maximum: 2), [0, 1, 2])
        XCTAssertEqual(StatsChartStyle.countTicks(maximum: 7), [0, 5, 10])
        XCTAssertEqual(StatsChartStyle.countTicks(maximum: 31), [0, 20, 40])
    }

    func testStackedBarWidthsShareTheBarByCount() {
        let widths = StatsStackedBar.widths(for: [3, 1], fraction: 0.5, totalWidth: 200)

        XCTAssertEqual(widths, [75, 25])
        XCTAssertEqual(StatsStackedBar.widths(for: [0, 0], fraction: 1, totalWidth: 200), [0, 0])
        XCTAssertEqual(StatsStackedBar.widths(for: [1], fraction: 2, totalWidth: 100), [100])
    }

    // MARK: - Activity (STAT-203)

    func testWeekdaysReorderForASundayWeek() {
        let counts = [31, 18, 22, 19, 25, 40, 44]

        let monday = StatsActivityChartData.weekdays(counts, weekStartsOn: "monday")
        XCTAssertEqual(monday.first?.name, "Monday")
        XCTAssertEqual(monday.map(\.count), counts)

        let sunday = StatsActivityChartData.weekdays(counts, weekStartsOn: "sunday")
        XCTAssertEqual(sunday.map(\.name).first, "Sunday")
        XCTAssertEqual(sunday.map(\.count), [44, 31, 18, 22, 19, 25, 40])

        XCTAssertTrue(StatsActivityChartData.weekdays([1, 2], weekStartsOn: "monday").isEmpty)
    }

    func testBusiestWeekday() {
        let days = StatsActivityChartData.weekdays([1, 5, 5, 0, 0, 0, 0], weekStartsOn: "monday")
        XCTAssertEqual(StatsActivityChartData.busiestWeekday(days)?.name, "Tuesday")

        let none = StatsActivityChartData.weekdays([0, 0, 0, 0, 0, 0, 0], weekStartsOn: "monday")
        XCTAssertNil(StatsActivityChartData.busiestWeekday(none))
    }

    func testMonthBarsStackOnGlobalAndSkipEmptyMonths() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let months = [
            StatsEventMonth(month: "2025-01", count: 14, byType: ["tv": 5, "movie": 9]),
            StatsEventMonth(month: "2025-02", count: 0, byType: [:]),
            StatsEventMonth(month: "2025-03", count: 2, byType: [:]),
        ]

        let stacked = StatsActivityChartData.parts(months, stacked: true, calendar: calendar)
        XCTAssertEqual(stacked.map(\.month), ["2025-01", "2025-01", "2025-03"])
        XCTAssertEqual(stacked.map(\.mediaType), ["movie", "tv", nil])

        let single = StatsActivityChartData.parts(months, stacked: false, calendar: calendar)
        XCTAssertEqual(single.map(\.count), [14, 2])
    }

    func testActivityLabels() {
        let locale = Locale(identifier: "en_US")

        XCTAssertEqual(StatsActivityChartData.monthTitle("2025-08", locale: locale), "August 2025")
        XCTAssertEqual(StatsActivityChartData.monthTitle("nonsense", locale: locale), "nonsense")
        XCTAssertEqual(
            StatsActivityChartData.busiestMonthText(StatsBusiestMonth(month: "2025-08", count: 31), locale: locale),
            "August 2025",
            "The month's count adds up title-days, so it isn't shown as titles"
        )
        XCTAssertEqual(
            StatsActivityChartData.busiestDayText(StatsBusiestDay(date: "2025-08-02", count: 1), locale: locale),
            "Sat, Aug 2, 2025 · 1 title"
        )
    }

    func testMonthAxisSwitchesToYearsForLongRanges() {
        XCTAssertNil(StatsActivityChartData.yearStride(monthCount: 12))
        XCTAssertEqual(StatsActivityChartData.yearStride(monthCount: 36), 1)
        XCTAssertEqual(StatsActivityChartData.yearStride(monthCount: 150), 3)

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let end = calendar.date(from: DateComponents(year: 2026, month: 11, day: 1))!
        let lastMark = calendar.date(from: DateComponents(year: 2026, month: 1, day: 1))!
        let earlierMark = calendar.date(from: DateComponents(year: 2023, month: 1, day: 1))!
        XCTAssertFalse(
            StatsActivityChartData.yearLabelFits(lastMark, end: end, stride: 3, calendar: calendar),
            "Ten months is too little room for a label every three years"
        )
        XCTAssertTrue(StatsActivityChartData.yearLabelFits(earlierMark, end: end, stride: 3, calendar: calendar))
        XCTAssertTrue(StatsActivityChartData.yearLabelFits(lastMark, end: end, stride: 1, calendar: calendar))
    }

    // MARK: - Genres and languages (STAT-210 to STAT-212)

    func testMostLoggedOrderIsTitlesThenName() {
        let rows = [
            ranked("Drama", titles: 5),
            ranked("Comedy", titles: 9),
            ranked("Action", titles: 5),
        ]

        XCTAssertEqual(StatsRankedRowsData.sorted(rows, by: .mostLogged).map(\.name), ["Comedy", "Action", "Drama"])
    }

    func testHighestRatedRanksOnlyRowsWithThreeRatedLogs() {
        let rows = [
            ranked("Drama", titles: 10, rated: 8, average: "3.6"),
            ranked("Horror", titles: 2, rated: 2, average: "4.9"),
            ranked("Comedy", titles: 4, rated: 3, average: "4.1"),
            ranked("Mystery", titles: 6, rated: 5, average: "3.6"),
            ranked("Romance", titles: 6, rated: 5, average: nil),
        ]

        XCTAssertEqual(
            StatsRankedRowsData.sorted(rows, by: .highestRated).map(\.name),
            ["Comedy", "Drama", "Mystery"]
        )
        XCTAssertTrue(StatsRankedRowsData.canRankByRating(rows))
        XCTAssertFalse(StatsRankedRowsData.canRankByRating([rows[1], rows[4]]))
    }

    func testRankedRowsShowEightUntilShowAll() {
        let rows = (1 ... 12).map { ranked("Genre \($0)", titles: 20 - $0) }

        XCTAssertEqual(StatsRankedRowsData.visible(rows, showsAll: false).count, 8)
        XCTAssertEqual(StatsRankedRowsData.visible(rows, showsAll: true).count, 12)
    }

    func testRankedBarFractions() {
        let row = ranked("Drama", titles: 5, rated: 4, average: "4.0")

        XCTAssertEqual(StatsRankedRowsData.fraction(row, sort: .mostLogged, maximumTitles: 10), 0.5)
        XCTAssertEqual(StatsRankedRowsData.fraction(row, sort: .highestRated, maximumTitles: 10), 0.8)
        XCTAssertEqual(StatsRankedRowsData.fraction(row, sort: .mostLogged, maximumTitles: 0), 0)
    }

    func testHighestRatedBarsSpreadAcrossTheRankedAverages() {
        let rows = [
            ranked("Drama", titles: 5, rated: 4, average: "4.0"),
            ranked("Comedy", titles: 5, rated: 3, average: "3.7"),
            ranked("Horror", titles: 5, rated: 1, average: "1.0"), // too few ratings to rank
        ]
        let range = StatsRankedRowsData.starRange(rows)
        XCTAssertEqual(range, 3.0 ... 4.0)
        let drama = StatsRankedRowsData.fraction(rows[0], sort: .highestRated, maximumTitles: 5, starRange: range)
        let comedy = StatsRankedRowsData.fraction(rows[1], sort: .highestRated, maximumTitles: 5, starRange: range)
        XCTAssertEqual(drama, 1)
        XCTAssertEqual(comedy, 0.7, accuracy: 0.001, "3.7 against 4.0 reads as a clear difference, not 74% against 80%")

        XCTAssertEqual(StatsRankedRowsData.starRange([rows[0]]), 3.5 ... 4.0, "One average still gets a bar")
        XCTAssertNil(StatsRankedRowsData.starRange([rows[2]]))
    }

    func testGenreRowsDrillDownByGroupOnGlobalAndBySpellingsOnATypeTab() {
        let global = StatsGenres(
            rows: [
                StatsGenreRow(
                    key: "scifi_fantasy", name: "Sci-Fi & Fantasy", values: nil, titles: 41, rated: 33,
                    averageStars: StatsDecimal("3.8"), byType: ["movie": 20, "tv": 6]
                ),
            ],
            total: 13, coverage: nil, music: []
        )
        let typeTab = StatsGenres(
            rows: [
                StatsGenreRow(
                    key: nil, name: "Sci-Fi", values: ["Sci-Fi", "Sci-fi"], titles: 22, rated: 25,
                    averageStars: StatsDecimal("3.9"), byType: ["manga": 22]
                ),
            ],
            total: 19, coverage: nil, music: []
        )

        XCTAssertEqual(
            StatsRankedRowsData.rows(global).first?.drillDown,
            .genreGroup(key: "scifi_fantasy", name: "Sci-Fi & Fantasy")
        )
        XCTAssertEqual(
            StatsRankedRowsData.rows(typeTab).first?.drillDown,
            .genre(name: "Sci-Fi", values: ["Sci-Fi", "Sci-fi"])
        )
    }

    func testLanguageRowsDrillDownByOriginalLanguage() {
        let languages = StatsLanguages(rows: [language("Japanese", titles: 9)], total: 1, coverage: nil, mediaTypes: ["movie", "tv"])

        XCTAssertEqual(StatsRankedRowsData.rows(languages).first?.drillDown, .language(name: "Japanese"))
        XCTAssertEqual(StatsRankedRowsData.rows(languages).first?.id, "language.Japanese")
    }

    func testSortLabelsFollowTheTab() {
        XCTAssertEqual(StatsRankSort.mostTitle(for: "movie"), "Most watched")
        XCTAssertEqual(StatsRankSort.mostTitle(for: "book"), "Most read")
        XCTAssertEqual(StatsRankSort.mostTitle(for: "game"), "Most played")
        XCTAssertEqual(StatsRankSort.mostTitle(for: "music"), "Most listened")
        XCTAssertEqual(StatsRankSort.mostTitle(for: nil), "Most titles")
        XCTAssertEqual(StatsRankSort.highestRated.title(mostTitle: "Most read"), "Highest rated")
    }

    func testRankedRowAccessibilityLabel() {
        XCTAssertEqual(
            StatsRankedRowsData.accessibilityLabel(ranked("Drama", titles: 5, rated: 4, average: "3.75")),
            "Drama, 5 titles, 3.8 average from 4 ratings"
        )
        XCTAssertEqual(StatsRankedRowsData.accessibilityLabel(ranked("Drama", titles: 1)), "Drama, 1 title")
    }

    // MARK: - List progress (STAT-213)

    func testRingsSortByPercentThenSeenThenName() {
        let rings = [
            ring(1, "B List", seen: 10, total: 100),
            ring(2, "Booker", seen: 9, total: 57),
            ring(3, "A List", seen: 10, total: 100),
            ring(4, "Big", seen: 20, total: 200),
            ring(5, "Top 500", seen: 155, total: 250),
        ]

        XCTAssertEqual(StatsListProgressData.sorted(rings).map(\.id), [5, 2, 4, 3, 1])
    }

    func testRingCaptions() {
        XCTAssertEqual(StatsListProgressData.caption(ring(1, "Top", seen: 155, total: 250)), "62% · 155 of 250")
        XCTAssertEqual(StatsListProgressData.caption(ring(1, "Top", seen: 250, total: 250)), "100% · 250 of 250")
        XCTAssertEqual(StatsListProgressData.percent(ring(1, "Top", seen: 499, total: 500)), 99, "Not 100% until every item is seen")
        XCTAssertEqual(StatsListProgressData.percent(ring(1, "Top", seen: 2, total: 1000)), 1, "Not 0% once one is seen")
        XCTAssertEqual(StatsListProgressData.percent(ring(1, "Top", seen: 0, total: 0)), 0)
        XCTAssertEqual(
            StatsListProgressData.accessibilityLabel(ring(1, "Booker Prize Winners", seen: 9, total: 57)),
            "Booker Prize Winners, 16 percent, 9 of 57 seen"
        )
    }

    // MARK: - Collections (STAT-214)

    func testCollectionCaptionsAndGroups() throws {
        let complete = try series(id: "119", name: "The Lord of the Rings Collection", completed: 3, total: 3)
        let almost = try series(id: "10", name: "Star Wars", completed: 18, total: 20)
        let collections = StatsCollections(completeCount: 33, almostCount: 0, complete: [complete], almost: [])

        XCTAssertEqual(StatsCollectionsData.caption(complete), "3 of 3")
        XCTAssertEqual(StatsCollectionsData.caption(almost), "18 of 20")
        XCTAssertEqual(StatsCollectionsData.groups(collections).map(\.group), [.complete], "An empty group is left out")
        XCTAssertEqual(StatsCollectionsData.groupTitle(.complete, count: 33), "Complete (33)")
        XCTAssertEqual(StatsCollectionsData.groupTitle(.almost, count: 7), "Almost complete (7)")
        XCTAssertTrue(StatsCollectionsData.hasMore(count: 33, shown: 4))
        XCTAssertFalse(StatsCollectionsData.hasMore(count: 4, shown: 4))
        XCTAssertEqual(
            StatsCollectionsData.accessibilityLabel(complete),
            "The Lord of the Rings Collection, 3 of 3 seen"
        )
    }

    func testCollectionTilesUseThreePostersOrAPlaceholder() throws {
        let many = try series(id: "1", name: "Many", completed: 1, total: 5, posters: ["a", "b", "c", "d"])
        let none = try series(id: "2", name: "None", completed: 1, total: 2, posters: [])

        XCTAssertEqual(StatsCollectionTile.posters(many), ["a", "b", "c"])
        XCTAssertEqual(StatsCollectionTile.posters(none), [nil])
    }

    // MARK: - Fixtures

    private func year(
        _ year: Int,
        titles: Int = 1,
        logs: Int = 1,
        rated: Int = 0,
        average: String? = nil,
        byType: [String: Int] = [:]
    ) -> StatsReleaseYearRow {
        StatsReleaseYearRow(
            year: year,
            titles: titles,
            logs: logs,
            rated: rated,
            averageStars: average.flatMap(StatsDecimal.init),
            byType: byType
        )
    }

    private func bucket(
        _ stars: String,
        count: Int,
        min: String,
        max: String,
        byType: [String: Int] = [:]
    ) -> StatsRatingsBucket {
        StatsRatingsBucket(
            stars: StatsDecimal(stars)!,
            count: count,
            ratingMin: StatsDecimal(min)!,
            ratingMax: StatsDecimal(max)!,
            byType: byType
        )
    }

    private func ranked(_ name: String, titles: Int, rated: Int = 0, average: String? = nil) -> StatsRankedRow {
        StatsRankedRow(
            id: name,
            name: name,
            titles: titles,
            rated: rated,
            averageStars: average.flatMap(StatsDecimal.init),
            byType: [:],
            drillDown: .language(name: name)
        )
    }

    private func language(_ name: String, titles: Int) -> StatsLanguageRow {
        StatsLanguageRow(name: name, titles: titles, rated: 0, averageStars: nil, byType: ["movie": titles])
    }

    private func ring(_ id: Int, _ name: String, seen: Int, total: Int) -> StatsListRing {
        StatsListRing(id: id, name: name, mediaType: "movie", kind: "community", seen: seen, total: total)
    }

    private func series(
        id: String,
        name: String,
        completed: Int,
        total: Int,
        posters: [String] = ["https://example.com/a.jpg"]
    ) throws -> MediaSeriesSummary {
        let json: [String: Any] = [
            "id": id,
            "source": "tmdb",
            "media_type": "movie",
            "name": name,
            "item_count": total,
            "poster_urls": posters,
            "completion": ["completed_count": completed, "total_count": total],
        ]
        let data = try JSONSerialization.data(withJSONObject: json)
        return try JSONDecoder.api.decode(MediaSeriesSummary.self, from: data)
    }
}
