import XCTest
@testable import Spine

@MainActor
final class StatsDrillDownTests: XCTestCase {
    private let ownGlobal2025 = StatsDrillDownContext(period: .year(2025), mediaType: nil, isOwnStats: true)
    private let ownMovies2025 = StatsDrillDownContext(period: .year(2025), mediaType: "movie", isOwnStats: true)
    private let ownTVAllTime = StatsDrillDownContext(period: .allTime, mediaType: "tv", isOwnStats: true)
    private let theirsGlobal2025 = StatsDrillDownContext(period: .year(2025), mediaType: nil, isOwnStats: false)

    // MARK: - Query building (STAT-401)

    func testEveryDrillDownSendsSkipBackfillRollupAndTheYearsDates() {
        let query = pairs(StatsDrillDown.liked.diaryFilter(in: ownGlobal2025))

        XCTAssertTrue(query.contains(["skip_backfill", "true"]))
        XCTAssertTrue(query.contains(["tv_rollup", "true"]))
        XCTAssertTrue(query.contains(["watched_from", "2025-01-01"]))
        XCTAssertTrue(query.contains(["watched_to", "2025-12-31"]))
        XCTAssertTrue(query.contains(["liked", "true"]))
        XCTAssertFalse(names(query).contains("media_type"), "Global sends no media type")
    }

    func testAllTimeSendsNoDates() {
        let query = names(pairs(StatsDrillDown.liked.diaryFilter(in: ownTVAllTime)))

        XCTAssertFalse(query.contains("watched_from"))
        XCTAssertFalse(query.contains("watched_to"))
        XCTAssertTrue(query.contains("skip_backfill"))
    }

    func testTypeTabGenreSendsEverySpellingAndTheTabsType() {
        let row = StatsGenreRow(
            key: nil, name: "Sci-Fi", values: ["Sci-Fi", "Sci-fi"], titles: 22, rated: 25,
            averageStars: StatsDecimal("3.9"), byType: ["movie": 22]
        )
        let query = pairs(StatsDrillDown.forGenre(row).diaryFilter(in: ownMovies2025))

        XCTAssertEqual(values("genre", in: query), ["Sci-Fi", "Sci-fi"])
        XCTAssertEqual(values("media_type", in: query), ["movie"])
        XCTAssertTrue(values("genre_group", in: query).isEmpty)
    }

    func testTypeTabGenreWithoutValuesFallsBackToItsName() {
        let row = StatsGenreRow(
            key: nil, name: "Drama", values: nil, titles: 3, rated: 0,
            averageStars: nil, byType: [:]
        )
        let query = pairs(StatsDrillDown.forGenre(row).diaryFilter(in: ownMovies2025))

        XCTAssertEqual(values("genre", in: query), ["Drama"])
    }

    func testMusicGenreDecodesEverySpellingAndSendsThemAll() throws {
        let json = #"{"rows": [], "total": 0, "coverage": null, "music": [{"name": "jazz", "values": ["jazz", "Jazz"], "titles": 26}]}"#
        let genres = try JSONDecoder.api.decode(StatsGenres.self, from: Data(json.utf8))
        XCTAssertEqual(genres.music.first?.values, ["jazz", "Jazz"])

        let row = try XCTUnwrap(StatsRankedRowsData.rows(music: genres.music).first)
        let query = pairs(row.drillDown.diaryFilter(in: ownGlobal2025))

        XCTAssertEqual(values("genre", in: query), ["jazz", "Jazz"])
        XCTAssertEqual(values("media_type", in: query), ["music"])
    }

    func testGlobalGenreGroupSendsItsKeyAndOnlyThePartsTypes() {
        let row = StatsGenreRow(
            key: "scifi_fantasy", name: "Sci-Fi & Fantasy", values: nil, titles: 41, rated: 33,
            averageStars: StatsDecimal("3.8"), byType: ["movie": 20, "tv": 6]
        )

        let whole = pairs(StatsDrillDown.forGenre(row).diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("genre_group", in: whole), ["scifi_fantasy"])
        XCTAssertTrue(values("media_type", in: whole).isEmpty)
        XCTAssertTrue(values("genre", in: whole).isEmpty)

        let tvPart = pairs(StatsDrillDown.forGenre(row, mediaType: "tv").diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("genre_group", in: tvPart), ["scifi_fantasy"])
        XCTAssertEqual(values("media_type", in: tvPart), ["tv", "season", "episode"])
    }

    func testLanguageSendsTheOriginalLanguageNotTheLanguageFacet() {
        let query = pairs(StatsDrillDown.language(name: "Japanese", mediaType: "movie").diaryFilter(in: ownGlobal2025))

        XCTAssertEqual(values("original_language", in: query), ["Japanese"])
        XCTAssertTrue(values("language", in: query).isEmpty)
        XCTAssertEqual(values("media_type", in: query), ["movie"])
    }

    func testReleaseYearAndDecade() {
        let year = pairs(StatsDrillDown.releaseYear(1999).diaryFilter(in: ownMovies2025))
        XCTAssertEqual(values("year", in: year), ["1999"])
        XCTAssertEqual(values("media_type", in: year), ["movie"])

        let decade = pairs(StatsDrillDown.releaseDecade(1990).diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("year_min", in: decade), ["1990"])
        XCTAssertEqual(values("year_max", in: decade), ["1999"])
        XCTAssertTrue(values("year", in: decade).isEmpty)
    }

    func testRatingBarSendsThePayloadsStoredBoundsAsTheyAre() throws {
        let bucket = StatsRatingsBucket(
            stars: try XCTUnwrap(StatsDecimal("3.5")), count: 37,
            ratingMin: try XCTUnwrap(StatsDecimal("6.5")), ratingMax: try XCTUnwrap(StatsDecimal("7.4")),
            byType: ["movie": 30, "tv": 7]
        )

        let whole = pairs(StatsDrillDown.rating(bucket).diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("rating_min", in: whole), ["6.5"])
        XCTAssertEqual(values("rating_max", in: whole), ["7.4"])

        let tvPart = pairs(StatsDrillDown.rating(bucket, mediaType: "tv").diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("media_type", in: tvPart), ["tv", "season", "episode"])
    }

    func testTopRatingBucketKeepsItsUpperBound() throws {
        let bucket = StatsRatingsBucket(
            stars: try XCTUnwrap(StatsDecimal("5.0")), count: 12,
            ratingMin: try XCTUnwrap(StatsDecimal("9.5")), ratingMax: try XCTUnwrap(StatsDecimal("10.0")),
            byType: [:]
        )
        let query = pairs(StatsDrillDown.rating(bucket).diaryFilter(in: ownGlobal2025))

        XCTAssertEqual(values("rating_min", in: query), ["9.5"])
        XCTAssertEqual(try XCTUnwrap(values("rating_max", in: query).first.flatMap(Double.init)), 10)
    }

    func testTitleSendsItsItemAndRollsUpShows() {
        let show = media(itemId: 812, mediaType: "tv")
        let query = pairs(StatsDrillDown.title(show).diaryFilter(in: ownTVAllTime))

        XCTAssertEqual(values("item_id", in: query), ["812"])
        XCTAssertTrue(query.contains(["tv_rollup", "true"]))
        XCTAssertEqual(values("media_type", in: query), ["tv", "season", "episode"])
    }

    func testMonthReplacesTheRangeWithItsOwnDays() {
        let february = pairs(StatsDrillDown.month("2024-02").diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("watched_from", in: february), ["2024-02-01"])
        XCTAssertEqual(values("watched_to", in: february), ["2024-02-29"])

        let april = pairs(StatsDrillDown.month("2025-04").diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("watched_to", in: april), ["2025-04-30"])

        let feb2025 = StatsDrillDown.dayRange(forMonth: "2025-02")
        XCTAssertEqual(feb2025?.to, "2025-02-28")
        XCTAssertNil(StatsDrillDown.dayRange(forMonth: "2025-13"))
    }

    func testBusiestDayOpensThatDaysLogs() {
        let day = StatsDrillDown.day("2024-02-29")
        let query = pairs(day.diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("watched_from", in: query), ["2024-02-29"])
        XCTAssertEqual(values("watched_to", in: query), ["2024-02-29"])
        XCTAssertFalse(day.diaryTitle(in: ownGlobal2025).contains("2025"), "The day names its own year")
    }

    func testTagAndLiked() {
        let tag = pairs(StatsDrillDown.tag("comfort").diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("tag", in: tag), ["comfort"])

        let liked = pairs(StatsDrillDown.liked.diaryFilter(in: ownMovies2025))
        XCTAssertEqual(values("liked", in: liked), ["true"])
        XCTAssertEqual(values("media_type", in: liked), ["movie"])
    }

    // MARK: - Header drills (STAT-200)

    func testAHeaderDrillGoesOnTopOfTheScopesFilter() {
        let repeats = pairs(StatsDrillDown.stat(label: "Repeats", drill: ["repeat": "true"]).diaryFilter(in: ownMovies2025))
        XCTAssertEqual(values("repeat", in: repeats), ["true"])
        XCTAssertEqual(values("watched_from", in: repeats), ["2025-01-01"], "The scope's own params stay")
        XCTAssertEqual(values("media_type", in: repeats), ["movie"])
        XCTAssertTrue(repeats.contains(["skip_backfill", "true"]))
        XCTAssertTrue(repeats.contains(["tv_rollup", "true"]))

        let multi = pairs(StatsDrillDown.stat(label: "Days with 2+ titles", drill: ["multi_title_days": "true"]).diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("multi_title_days", in: multi), ["true"])

        let reviews = pairs(StatsDrillDown.stat(label: "Reviews", drill: ["has_review": "true"]).diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("has_review", in: reviews), ["true"])
        let likes = pairs(StatsDrillDown.stat(label: "Likes", drill: ["liked": "true"]).diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("liked", in: likes), ["true"])
    }

    func testADrillKeyReplacesTheScopesParamOfTheSameName() {
        let streak = StatsDrillDown.stat(
            label: "Current daily streak",
            drill: ["watched_from": "2025-12-27", "watched_to": "2025-12-31"]
        )
        let query = pairs(streak.diaryFilter(in: ownGlobal2025))
        XCTAssertEqual(values("watched_from", in: query), ["2025-12-27"])
        XCTAssertEqual(values("watched_to", in: query), ["2025-12-31"])

        let tvOnly = pairs(StatsDrillDown.stat(label: "X", drill: ["media_type": "episode"]).diaryFilter(in: ownTVAllTime))
        XCTAssertEqual(values("media_type", in: tvOnly), ["episode"])

        let noRollup = pairs(StatsDrillDown.stat(label: "X", drill: ["tv_rollup": "false"]).diaryFilter(in: ownTVAllTime))
        XCTAssertFalse(names(noRollup).contains("tv_rollup"))
    }

    func testAnEmptyDrillIsTheScopesOwnLogs() {
        let scope = StatsDrillDown.stat(label: "Logs").diaryFilter(in: ownMovies2025)
        var expected = MediaFilterState()
        expected.skipBackfill = true
        expected.tvRollup = true
        expected.watchedFrom = MediaFilterState.date(fromDay: "2025-01-01")
        expected.watchedTo = MediaFilterState.date(fromDay: "2025-12-31")
        expected.mediaTypes = ["movie"]
        XCTAssertEqual(scope, expected)
        XCTAssertEqual(StatsDrillDown.stat(label: "Logs").diaryTitle(in: ownMovies2025), "Logs · 2025")
        XCTAssertEqual(StatsDrillDown.stat(label: "Repeats", drill: ["repeat": "true"]).diaryTitle(in: ownGlobal2025), "Repeats · 2025")
    }

    func testDrillsTheAppCantSendOpenNothing() {
        XCTAssertNil(MediaFilterState().applying(drill: ["mystery": "true"]))
        XCTAssertNil(MediaFilterState().applying(drill: ["watched_from": "last week"]))
        XCTAssertNil(MediaFilterState().applying(drill: ["repeat": "yes"]))
        XCTAssertNil(MediaFilterState().applying(drill: ["media_type": ""]))
        XCTAssertNotNil(MediaFilterState().applying(drill: [:]))
        XCTAssertNotNil(MediaFilterState().applying(drill: ["liked": "TRUE"]))

        XCTAssertNil(StatsDrillDown.headerStat(label: "Reads", drill: ["mystery": "1"]))
        XCTAssertNil(StatsDrillDown.stat(label: "Reads", drill: ["mystery": "1"]).route(in: ownGlobal2025))
        XCTAssertEqual(
            StatsDrillDown.stat(label: "Repeats", drill: ["repeat": "true"]).route(in: ownGlobal2025),
            .diary(.stat(label: "Repeats", drill: ["repeat": "true"]), ownGlobal2025)
        )
        XCTAssertNil(StatsDrillDown.stat(label: "Repeats", drill: ["repeat": "true"]).route(in: theirsGlobal2025), "Owner only")
    }

    func testRepeatAndMultiTitleDaysCountAsFilters() {
        var filter = MediaFilterState()
        filter.repeat = true
        XCTAssertEqual(filter.activeCount, 1)
        filter.multiTitleDays = true
        XCTAssertEqual(filter.activeCount, 2)
        XCTAssertEqual(values("repeat", in: pairs(filter)), ["true"])
        XCTAssertEqual(values("multi_title_days", in: pairs(filter)), ["true"])
        XCTAssertFalse(names(pairs(MediaFilterState())).contains("repeat"), "Off sends nothing")
    }

    func testDrillDownFiltersDontChangeTheFilterBadgeForRollupAndBackfill() {
        var filter = MediaFilterState()
        filter.skipBackfill = true
        filter.tvRollup = true
        XCTAssertEqual(filter.activeCount, 0)

        filter.genreGroups = ["drama"]
        filter.originalLanguages = ["French"]
        XCTAssertEqual(filter.activeCount, 2)
    }

    func testDayStringsRoundTripThroughTheFilter() {
        var filter = MediaFilterState()
        filter.watchedFrom = MediaFilterState.date(fromDay: "2025-01-01")
        filter.watchedTo = MediaFilterState.date(fromDay: "2025-12-31")

        let query = pairs(filter)
        XCTAssertEqual(values("watched_from", in: query), ["2025-01-01"])
        XCTAssertEqual(values("watched_to", in: query), ["2025-12-31"])
    }

    func testDiaryViewModelSendsTheDrillDownFilter() async throws {
        let repository = RecordingDiaryRepository()
        let filter = StatsDrillDown.language(name: "Korean").diaryFilter(in: ownMovies2025)
        let viewModel = DiaryViewModel(diaryRepository: repository, mediaFilter: filter, onUnauthorized: {})

        await viewModel.loadIfNeeded()

        let sent = try XCTUnwrap(repository.filters.first)
        XCTAssertEqual(sent, filter)
        XCTAssertEqual(values("original_language", in: pairs(sent)), ["Korean"])
    }

    // MARK: - Titles

    func testDiaryTitles() throws {
        XCTAssertEqual(
            StatsDrillDown.genreGroup(key: "scifi_fantasy", name: "Sci-Fi & Fantasy").diaryTitle(in: ownGlobal2025),
            "Sci-Fi & Fantasy · 2025"
        )
        XCTAssertEqual(
            StatsDrillDown.genreGroup(key: "drama", name: "Drama", mediaType: "movie").diaryTitle(in: ownGlobal2025),
            "Drama · Movies · 2025"
        )
        XCTAssertEqual(StatsDrillDown.genre(name: "Sci-Fi", values: ["Sci-Fi"]).diaryTitle(in: ownTVAllTime), "Sci-Fi · All Time")
        XCTAssertEqual(StatsDrillDown.releaseYear(1999).diaryTitle(in: ownMovies2025), "Released in 1999 · 2025")
        XCTAssertEqual(StatsDrillDown.releaseDecade(1990).diaryTitle(in: ownMovies2025), "Released in the 1990s · 2025")
        XCTAssertEqual(StatsDrillDown.month("2025-03").diaryTitle(in: ownGlobal2025), "March 2025")
        XCTAssertEqual(StatsDrillDown.liked.diaryTitle(in: ownGlobal2025), "Likes · 2025")

        let bucket = StatsRatingsBucket(
            stars: try XCTUnwrap(StatsDecimal("3.5")), count: 1,
            ratingMin: try XCTUnwrap(StatsDecimal("6.5")), ratingMax: try XCTUnwrap(StatsDecimal("7.4")),
            byType: [:]
        )
        XCTAssertEqual(StatsDrillDown.rating(bucket).diaryTitle(in: ownGlobal2025), "3.5 stars · 2025")
    }

    func testStarsLabels() throws {
        XCTAssertEqual(StatsDrillDown.starsLabel(try XCTUnwrap(StatsDecimal("4.0"))), "4 stars")
        XCTAssertEqual(StatsDrillDown.starsLabel(try XCTUnwrap(StatsDecimal("1.0"))), "1 star")
        XCTAssertEqual(StatsDrillDown.starsLabel(try XCTUnwrap(StatsDecimal("0.5"))), "0.5 stars")
    }

    func testSeasonTitleUsesTheSeasonLabel() {
        let season = MediaSummary(
            ref: MediaRef(itemId: 9, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 2, episodeNumber: nil),
            title: "Show",
            posterOrientation: .portrait
        )
        XCTAssertEqual(StatsDrillDown.title(season).diaryTitle(in: ownTVAllTime), "Show S2 · All Time")
    }

    // MARK: - Routes: drill-downs only on your own stats

    func testOwnStatsOpenTheFilteredDiary() {
        XCTAssertEqual(StatsDrillDown.liked.route(in: ownGlobal2025), .diary(.liked, ownGlobal2025))
        let movie = media(itemId: 5, mediaType: "movie")
        XCTAssertEqual(StatsDrillDown.title(movie).route(in: ownGlobal2025), .diary(.title(movie), ownGlobal2025))
    }

    func testSomeoneElsesChartsArentTappable() throws {
        let bucket = StatsRatingsBucket(
            stars: try XCTUnwrap(StatsDecimal("3.5")), count: 1,
            ratingMin: try XCTUnwrap(StatsDecimal("6.5")), ratingMax: try XCTUnwrap(StatsDecimal("7.4")),
            byType: [:]
        )
        let charts: [StatsDrillDown] = [
            .genre(name: "Drama", values: ["Drama"]),
            .genreGroup(key: "drama", name: "Drama"),
            .language(name: "English"),
            .releaseYear(1999),
            .releaseDecade(1990),
            .rating(bucket),
            .month("2025-03"),
            .liked,
            .tag("comfort"),
        ]
        for chart in charts {
            XCTAssertNil(chart.route(in: theirsGlobal2025), "\(chart)")
        }
    }

    func testSomeoneElsesTitlesOpenTheTitlePage() {
        let movie = media(itemId: 5, mediaType: "movie")
        let other = media(itemId: 6, mediaType: "movie")
        let route = StatsDrillDown.title(movie, within: [movie.ref, other.ref]).route(in: theirsGlobal2025)

        XCTAssertEqual(route, .title(MediaBrowsingSelection(ref: movie.ref, within: [movie.ref, other.ref])))
        XCTAssertEqual(route?.presentsFullScreen, true)
    }

    func testATitleWithoutAnItemOpensTheTitlePageEvenOnYourOwnStats() {
        let movie = media(itemId: nil, mediaType: "movie")
        XCTAssertEqual(
            StatsDrillDown.title(movie).route(in: ownGlobal2025),
            .title(MediaBrowsingSelection(ref: movie.ref, within: [movie.ref]))
        )
    }

    func testMilestoneOpensTheLogOnlyOnYourOwnStats() {
        let milestone = StatsMilestone(number: 100, day: "2024-03-12", entryId: 9312, mediaType: "movie", media: media(itemId: 5, mediaType: "movie"))

        XCTAssertEqual(StatsDrillDown.milestone(milestone).route(in: ownGlobal2025), .log(entryId: 9312))
        XCTAssertEqual(
            StatsDrillDown.milestone(milestone).route(in: theirsGlobal2025),
            .title(MediaBrowsingSelection(ref: milestone.media.ref, within: [milestone.media.ref]))
        )
    }

    func testListsAndCollectionsOpenForEveryone() throws {
        XCTAssertEqual(StatsDrillDown.list(id: 81).route(in: theirsGlobal2025), .list(id: 81))
        XCTAssertEqual(StatsDrillDown.list(id: 81).route(in: ownGlobal2025)?.presentsFullScreen, false)

        let series = try JSONDecoder.api.decode(MediaSeriesSummary.self, from: Data("""
        {"id": "119", "source": "tmdb", "media_type": "movie", "name": "The Lord of the Rings Collection",
         "item_count": 3, "poster_urls": [], "completion": {"completed_count": 3, "total_count": 3}}
        """.utf8))
        let route = StatsDrillDown.collection(series).route(in: theirsGlobal2025)
        XCTAssertEqual(route, .series(SeriesRef(source: "tmdb", id: "119", mediaType: "movie")))
        XCTAssertEqual(route?.presentsFullScreen, true)
    }

    // MARK: - Helpers

    private func media(itemId: Int?, mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: itemId, source: "tmdb", mediaType: mediaType, mediaId: "\(itemId ?? 0)", seasonNumber: nil, episodeNumber: nil),
            title: "Title \(itemId ?? 0)",
            posterOrientation: .portrait
        )
    }

    private func pairs(_ filter: MediaFilterState) -> [[String]] {
        filter.queryItems().map { [$0.name, $0.value ?? ""] }
    }

    private func names(_ pairs: [[String]]) -> [String] {
        pairs.map { $0[0] }
    }

    private func values(_ name: String, in pairs: [[String]]) -> [String] {
        pairs.filter { $0[0] == name }.map { $0[1] }
    }
}

@MainActor
private final class RecordingDiaryRepository: DiaryRepository {
    private(set) var filters: [MediaFilterState] = []

    func page(filter: MediaFilterState, page: String?) async throws -> PagedResponse<DiaryEntry> {
        filters.append(filter)
        return PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}
