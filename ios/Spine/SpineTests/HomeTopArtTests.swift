import XCTest
@testable import Spine

/// Which title is the picture at the top of Home today, the fallbacks, and the label that names it.
@MainActor
final class HomeTopArtTests: XCTestCase {
    private var defaults: UserDefaults!
    private var media: RailMediaRepository!
    private var calendar: Calendar!
    private var date = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "HomeTopArtTests.\(UUID().uuidString)")
        media = RailMediaRepository()
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        self.calendar = calendar
    }

    // MARK: Fixtures

    private func ref(_ type: String, _ id: String) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: type, mediaId: id, seasonNumber: nil, episodeNumber: nil)
    }

    private func summary(_ type: String, _ id: String, releaseDate: String? = nil) -> MediaSummary {
        MediaSummary(ref: ref(type, id), title: "Title \(id)", releaseDate: releaseDate)
    }

    private func makeModel() -> HomeTopArtModel {
        HomeTopArtModel(mediaRepository: media, defaults: defaults, calendar: calendar, now: { self.date })
    }

    private func settle() async {
        try? await Task.sleep(for: .milliseconds(150))
    }

    private func detail(
        _ type: String, _ id: String, backdrop: String? = "https://example.com/b.jpg", custom: String? = nil,
        releaseDate: String? = nil, details: [String: JSONValue]? = nil
    ) {
        media.details[ref(type, id).id] = MediaDetail(
            ref: ref(type, id), title: "Detail \(id)", releaseDate: releaseDate, backdropUrl: backdrop,
            details: details, customBackdropUrl: custom
        )
    }

    // MARK: Which titles, which day

    func testOnlyMoviesShowsAnimeAndGamesCanBeThePictureInTheRailsOrderEachOnce() {
        let list = [
            summary("book", "1"), summary("tv", "2"), summary("music", "3"), summary("movie", "4"), summary("tv", "2"),
            summary("manga", "5"), summary("game", "6"), summary("anime", "7"),
        ]
        XCTAssertEqual(HomeTopArtPick.candidates(list).map(\.ref.mediaId), ["2", "4", "6", "7"])
        XCTAssertTrue(HomeTopArtPick.candidates([summary("book", "1")]).isEmpty)
    }

    func testTheDayPicksAPlaceInTheListAndWrapsAround() {
        let list = (0 ..< 4).map { summary("movie", "\($0)") }
        XCTAssertEqual(HomeTopArtPick.rotated(list, dayKey: 0).map(\.ref.mediaId), ["0", "1", "2", "3"])
        XCTAssertEqual(HomeTopArtPick.rotated(list, dayKey: 6).map(\.ref.mediaId), ["2", "3", "0", "1"])
        XCTAssertEqual(HomeTopArtPick.rotated(list, dayKey: 4).first?.ref.mediaId, "0")
        XCTAssertTrue(HomeTopArtPick.rotated([], dayKey: 3).isEmpty)
    }

    func testTheDayKeyIsTheSameAllDayAndOneHigherTomorrow() {
        let morning = date
        let evening = morning.addingTimeInterval(60 * 60 * 6)
        let key = HomeTopArtPick.dayKey(morning, calendar: calendar)
        XCTAssertEqual(HomeTopArtPick.dayKey(evening, calendar: calendar), key)
        XCTAssertEqual(HomeTopArtPick.dayKey(morning.addingTimeInterval(86_400), calendar: calendar), key + 1)
    }

    // MARK: The label

    func testTheLabelIsTitleDotYearOrJustTheTitle() throws {
        let url = try XCTUnwrap(URL(string: "https://example.com/b.jpg"))
        let withYear = HomeTopArt(ref: ref("movie", "1"), title: "Heat", year: "1995", backdrop: url)
        let without = HomeTopArt(ref: ref("movie", "1"), title: "Heat", year: nil, backdrop: url)
        XCTAssertEqual(withYear.label, "Heat · 1995")
        XCTAssertEqual(without.label, "Heat")
    }

    // MARK: The model

    func testItTakesTheUsersOwnBackdropBeforeTheDefaultAndTheShowsFirstAirYear() async throws {
        detail(
            "tv", "1", backdrop: "https://example.com/default.jpg", custom: "https://example.com/mine.jpg",
            releaseDate: "2024-01-01", details: ["first_air_date": .string("2019-05-04")]
        )
        let model = makeModel()
        model.update(inProgress: [summary("tv", "1")], trending: [])
        await settle()
        let art = try XCTUnwrap(model.art)
        XCTAssertEqual(art.backdrop, URL(string: "https://example.com/mine.jpg"))
        XCTAssertEqual(art.year, "2019")
        XCTAssertEqual(art.title, "Detail 1")
        XCTAssertEqual(art.label, "Detail 1 · 2019")
        XCTAssertEqual(art.ref, ref("tv", "1"))
    }

    func testAMoviesYearIsItsReleaseYearAndAnUnknownYearLeavesJustTheTitle() async throws {
        detail("movie", "1", releaseDate: "1995-12-15")
        let first = makeModel()
        first.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertEqual(first.art?.label, "Detail 1 · 1995")

        defaults.removeObject(forKey: HomeTopArtModel.defaultsKey)
        detail("game", "2")
        let second = makeModel()
        second.update(inProgress: [summary("game", "2")], trending: [])
        await settle()
        XCTAssertNil(second.art?.year)
        XCTAssertEqual(second.art?.label, "Detail 2")
    }

    func testItAsksOneTitlePageOnceAndKeepsTheDaysPickWhateverTheRailDoes() async {
        detail("movie", "1")
        detail("movie", "2")
        let model = makeModel()
        model.update(inProgress: [summary("movie", "1"), summary("movie", "2")], trending: [])
        await settle()
        let day = HomeTopArtPick.dayKey(date, calendar: calendar)
        let expected = day % 2 == 0 ? "1" : "2"
        XCTAssertEqual(media.detailRequests.map(\.mediaId), [expected], "one request for the day's pick")
        XCTAssertEqual(model.art?.ref.mediaId, expected)

        model.update(inProgress: [summary("movie", "2"), summary("movie", "1")], trending: [summary("movie", "9")])
        await settle()
        XCTAssertEqual(media.detailRequests.count, 1, "no second request")
        XCTAssertEqual(model.art?.ref.mediaId, expected, "the rail changing does not change today's picture")
    }

    func testBeforeTheRailHasLoadedNothingIsChosen() async {
        detail("movie", "9")
        let model = makeModel()
        model.update(inProgress: nil, trending: [summary("movie", "9")])
        await settle()
        XCTAssertNil(model.art)
        XCTAssertTrue(media.detailRequests.isEmpty, "Trending cannot win a race with the rail")
    }

    func testAnEmptyRailFallsBackToTrending() async {
        detail("movie", "9")
        let model = makeModel()
        model.update(inProgress: [], trending: [summary("book", "1"), summary("movie", "9")])
        await settle()
        XCTAssertEqual(model.art?.ref.mediaId, "9")
        XCTAssertEqual(media.detailRequests.map(\.mediaId), ["9"])
    }

    func testBooksAloneLeaveNoPictureAndAskNothing() async {
        let model = makeModel()
        model.update(inProgress: [summary("book", "1")], trending: [summary("manga", "2")])
        await settle()
        XCTAssertNil(model.art)
        XCTAssertTrue(media.detailRequests.isEmpty)
    }

    func testATitleWithoutABackdropPassesToTheNextAndNoMoreThanThreeAreAsked() async {
        for id in ["1", "2", "3", "4"] { detail("movie", id, backdrop: nil) }
        let model = makeModel()
        let list = ["1", "2", "3", "4"].map { summary("movie", $0) }
        model.update(inProgress: list, trending: [])
        await settle()
        XCTAssertNil(model.art)
        XCTAssertEqual(media.detailRequests.count, HomeTopArtModel.maxTries)

        // The three that had none are not asked again; the fourth is.
        model.update(inProgress: list, trending: [])
        await settle()
        XCTAssertEqual(media.detailRequests.count, HomeTopArtModel.maxTries + 1)
        XCTAssertNil(model.art)

        detail("movie", "2", backdrop: "https://example.com/two.jpg")
        let other = self.makeModel()
        other.update(inProgress: [summary("movie", "1"), summary("movie", "2")], trending: [])
        await settle()
        XCTAssertEqual(other.art?.ref.mediaId, "2", "the first without a backdrop is skipped")
    }

    func testAFailedRequestChoosesNothingAndIsTriedAgainOnTheNextUpdate() async {
        detail("movie", "1")
        media.error = URLError(.notConnectedToInternet)
        let model = makeModel()
        model.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertNil(model.art)

        media.error = nil
        model.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertNotNil(model.art)
    }

    func testTodaysPickIsRememberedAndNextDaysIsNot() async {
        detail("movie", "1", releaseDate: "2001-01-01")
        let first = makeModel()
        first.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        let chosen = first.art
        XCTAssertNotNil(chosen)

        let sameDay = makeModel()
        XCTAssertEqual(sameDay.art, chosen, "restored on the first frame")
        sameDay.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertEqual(media.detailRequests.count, 1, "restored, so not asked again")

        date = date.addingTimeInterval(86_400)
        let nextDay = makeModel()
        XCTAssertNil(nextDay.art, "yesterday's pick is not today's")
    }

    func testTheModelTellsTheGlowWhenThePictureArrives() async {
        detail("movie", "1")
        let model = makeModel()
        var seen: [Bool] = []
        model.onChange = { seen.append($0 != nil) }
        model.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertEqual(seen, [true])
    }

    func testTheYearHelperKeepsItsBehavior() {
        XCTAssertEqual(TrendingTileModel.year(from: "2025-03-14"), "2025")
        XCTAssertEqual(TrendingTileModel.year(from: "2025"), "2025")
        XCTAssertNil(TrendingTileModel.year(from: "abc"))
        XCTAssertNil(TrendingTileModel.year(from: nil))
        let show = MediaDetail(
            ref: ref("tv", "1"), title: "S", releaseDate: "2024-01-01", details: ["first_air_date": .string("2019-05-04")]
        )
        XCTAssertEqual(TrendingTileModel.year(of: show), "2019")
    }
}
