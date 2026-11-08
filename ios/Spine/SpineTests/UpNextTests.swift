import XCTest
@testable import Spine

/// Home's Up next (docs/up-next-contract.md): the wire shape, the words and dates the tile and page show, the months
/// the page groups by, and how the model loads.
@MainActor
final class UpNextTests: XCTestCase {
    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        calendar.locale = Locale(identifier: "en_US")
        return calendar
    }

    /// Sunday, Nov 8, 2026, at noon in Los Angeles.
    private var today: Date {
        calendar.date(from: DateComponents(year: 2026, month: 11, day: 8, hour: 12))!
    }

    private func item(
        _ title: String,
        mediaType: String = "tv",
        kind: String = "episode",
        season: Int? = 2,
        episode: Int? = 8,
        airDate: String = "2026-11-13"
    ) throws -> UpNextItem {
        func number(_ value: Int?) -> String { value.map(String.init) ?? "null" }
        let json = """
        {
          "media": {"ref": {"source": "tmdb", "media_type": "\(mediaType)", "media_id": "\(title)"}, "title": "\(title)"},
          "airing": {"kind": "\(kind)", "season_number": \(number(season)), "episode_number": \(number(episode)),
                     "air_date": "\(airDate)"}
        }
        """
        return try JSONDecoder.api.decode(UpNextItem.self, from: Data(json.utf8))
    }

    // MARK: The wire

    func testResponseDecodesTheServersShape() throws {
        let json = """
        {"results": [
          {"media": {"ref": {"source": "tmdb", "media_type": "tv", "media_id": "95396"}, "title": "Severance",
                     "poster_url": "https://image.tmdb.org/t/p/w500/s.jpg"},
           "airing": {"kind": "season_premiere", "season_number": 3, "episode_number": null, "air_date": "2027-03-12"}}
        ]}
        """
        let response = try JSONDecoder.api.decode(UpNextResponse.self, from: Data(json.utf8))
        let first = try XCTUnwrap(response.results.first)
        XCTAssertEqual(first.media.title, "Severance")
        XCTAssertEqual(first.media.displayPosterURL, "https://image.tmdb.org/t/p/w500/s.jpg")
        XCTAssertTrue(first.airing.isSeasonPremiere)
        XCTAssertEqual(first.airing.seasonNumber, 3)
        XCTAssertNil(first.airing.episodeNumber)
        XCTAssertEqual(first.airing.airDate, "2027-03-12")
    }

    // MARK: Words

    func testLineSaysWhatAirs() throws {
        XCTAssertEqual(UpNextPresentation.line(for: try item("A")), "S2 E8")
        XCTAssertEqual(UpNextPresentation.line(for: try item("B", kind: "season_premiere", season: 3, episode: 1)), "Season 3 premiere")
        XCTAssertEqual(UpNextPresentation.line(for: try item("C", kind: "season_premiere", season: 3, episode: nil)), "Season 3 premiere")
        XCTAssertEqual(UpNextPresentation.line(for: try item("D", kind: "series_premiere", season: 1, episode: 1)), "Series premiere")
        XCTAssertEqual(
            UpNextPresentation.line(for: try item("E", mediaType: "anime", season: nil, episode: 1152)),
            "Episode \(1152.formatted()) · Anime"
        )
        XCTAssertEqual(
            UpNextPresentation.line(for: try item("F", mediaType: "anime", kind: "series_premiere", season: nil, episode: 1)),
            "Premiere · Anime"
        )
    }

    func testAnUnknownKindReadsAsAnEpisode() throws {
        XCTAssertEqual(UpNextPresentation.line(for: try item("A", kind: "something_new")), "S2 E8")
    }

    func testRelativeDistance() {
        XCTAssertEqual(UpNextPresentation.relative(days: 0), "Today")
        XCTAssertEqual(UpNextPresentation.relative(days: 1), "Tomorrow")
        XCTAssertEqual(UpNextPresentation.relative(days: 5), "in 5 days")
        XCTAssertEqual(UpNextPresentation.relative(days: 7), "in 1 week")
        XCTAssertEqual(UpNextPresentation.relative(days: 13), "in 1 week")
        XCTAssertEqual(UpNextPresentation.relative(days: 21), "in 3 weeks")
        XCTAssertEqual(UpNextPresentation.relative(days: 124), "in 4 months")
    }

    func testCountLine() {
        XCTAssertEqual(UpNextPresentation.countLine(1), "1 coming up")
        XCTAssertEqual(UpNextPresentation.countLine(5), "5 coming up")
    }

    // MARK: Dates

    func testDaysAwayCountsCalendarDays() {
        XCTAssertEqual(UpNextPresentation.daysAway("2026-11-08", today: today, calendar: calendar), 0)
        XCTAssertEqual(UpNextPresentation.daysAway("2026-11-13", today: today, calendar: calendar), 5)
        XCTAssertEqual(UpNextPresentation.daysAway("2027-03-12", today: today, calendar: calendar), 124)
        XCTAssertNil(UpNextPresentation.daysAway("soon", today: today, calendar: calendar))
    }

    func testBadgeIsTheWeekdayOverTheDay() {
        let badge = UpNextPresentation.badge(for: "2026-11-13", calendar: calendar)
        XCTAssertEqual(badge.weekday, "FRI")
        XCTAssertEqual(badge.day, "13")
    }

    func testShortDateUnderTheTilesPosters() {
        XCTAssertEqual(UpNextPresentation.shortDate("2026-11-08", calendar: calendar), "11/8")
        XCTAssertEqual(UpNextPresentation.shortDate("2027-01-16", calendar: calendar), "1/16")
        XCTAssertEqual(UpNextPresentation.shortDate("soon", calendar: calendar), "")
    }

    func testSpokenWhen() {
        XCTAssertEqual(UpNextPresentation.spokenWhen("2026-11-08", today: today, calendar: calendar), "today")
        XCTAssertEqual(UpNextPresentation.spokenWhen("2026-11-09", today: today, calendar: calendar), "tomorrow")
        XCTAssertEqual(UpNextPresentation.spokenWhen("2026-11-13", today: today, calendar: calendar), "Friday")
        XCTAssertEqual(UpNextPresentation.spokenWhen("2027-01-16", today: today, calendar: calendar), "January 16")
    }

    // MARK: Months

    func testMonthsKeepTheServersOrderAndNameTheYearOnlyOutsideThisOne() throws {
        let items = [
            try item("One Piece", mediaType: "anime", season: nil, episode: 1152, airDate: "2026-11-08"),
            try item("Pluribus", season: 1, airDate: "2026-11-13"),
            try item("Frieren", mediaType: "anime", kind: "series_premiere", season: nil, episode: 1, airDate: "2027-01-16"),
            try item("Severance", kind: "season_premiere", season: 3, episode: 1, airDate: "2027-03-12"),
        ]
        let sections = UpNextPresentation.monthSections(items, today: today, calendar: calendar)
        XCTAssertEqual(sections.map(\.title), ["November", "January 2027", "March 2027"])
        XCTAssertEqual(sections.map(\.id), ["2026-11", "2027-01", "2027-03"])
        XCTAssertEqual(sections[0].items.map(\.media.title), ["One Piece", "Pluribus"])
    }

    // MARK: The model

    func testLoadShowsWhatTheServerSends() async throws {
        let repository = UpNextRepository(result: .success([try item("Pluribus")]))
        let model = HomeUpNextModel(repository: repository, onUnauthorized: {})
        await model.load()
        XCTAssertEqual(model.items.map(\.media.title), ["Pluribus"])
        XCTAssertTrue(model.hasLoaded)
        XCTAssertNil(model.errorMessage)
        XCTAssertFalse(model.isLoading)
    }

    func testAFailedLoadKeepsWhatWasShown() async throws {
        let repository = UpNextRepository(result: .success([try item("Pluribus")]))
        let model = HomeUpNextModel(repository: repository, onUnauthorized: {})
        await model.load()
        repository.result = .failure(URLError(.notConnectedToInternet))
        await model.load()
        XCTAssertEqual(model.items.map(\.media.title), ["Pluribus"])
        XCTAssertNotNil(model.errorMessage)
    }

    func testAnOlderServerWithoutUpNextLeavesItEmpty() async {
        let repository = UpNextRepository(result: .failure(APIError.httpStatus(404, "Not found")))
        let model = HomeUpNextModel(repository: repository, onUnauthorized: {})
        await model.load()
        XCTAssertTrue(model.items.isEmpty)
        XCTAssertFalse(model.hasLoaded)
    }

    func testUnauthorizedSignsOut() async {
        var signedOut = false
        let repository = UpNextRepository(result: .failure(APIError.unauthorized))
        let model = HomeUpNextModel(repository: repository, onUnauthorized: { signedOut = true })
        await model.load()
        XCTAssertTrue(signedOut)
    }

    func testAHomeWithoutExtrasMakesNoRead() async {
        let repository = UpNextRepository(result: .success([]))
        let model = HomeUpNextModel(repository: nil, onUnauthorized: {})
        await model.load()
        XCTAssertEqual(repository.calls, 0)
        XCTAssertFalse(model.hasLoaded)
    }

    // MARK: The tile's fold

    func testFoldProgressIsZeroAtRestHalfWayAndOneOnceTheTileIsOffScreen() {
        XCTAssertEqual(UpNextFold.progress(scroll: 0, restBottom: 400), 0)
        XCTAssertEqual(UpNextFold.progress(scroll: 200, restBottom: 400), 0.5)
        XCTAssertEqual(UpNextFold.progress(scroll: 400, restBottom: 400), 1)
        XCTAssertEqual(UpNextFold.progress(scroll: 900, restBottom: 400), 1)
        XCTAssertEqual(UpNextFold.progress(scroll: -50, restBottom: 400), 0, "Pulled past the top it stays unfolded")
    }

    func testFoldOffsetsSlideEveryPosterButTheFirstBehindIt() {
        let step: CGFloat = 37
        func offsets(count: Int, progress: CGFloat) -> [CGFloat] {
            (0 ..< count).map { UpNextFold.offset(index: $0, progress: progress, step: step) }
        }
        XCTAssertEqual(offsets(count: 1, progress: 1), [0], "A lone poster does not move")
        XCTAssertEqual(offsets(count: 2, progress: 1), [0, -37], "With two only the second slides")
        XCTAssertEqual(offsets(count: 5, progress: 0), [0, 0, 0, 0, 0])
        XCTAssertEqual(offsets(count: 5, progress: 0.5), [0, -18.5, -37, -55.5, -74])
        XCTAssertEqual(offsets(count: 5, progress: 1), [0, -37, -74, -111, -148])
    }
}

private final class UpNextRepository: TrackingRepository {
    var result: Result<[UpNextItem], Error>
    private(set) var calls = 0

    init(result: Result<[UpNextItem], Error>) {
        self.result = result
    }

    func upNext() async throws -> [UpNextItem] {
        calls += 1
        return try result.get()
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        fatalError("Not used")
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }

    func updateBookProgress(
        source: String,
        mediaId: String,
        progressType: String,
        value: Decimal,
        notes: String
    ) async throws -> TrackingState {
        fatalError("Not used")
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        fatalError("Not used")
    }
}
