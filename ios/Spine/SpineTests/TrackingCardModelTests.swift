import XCTest
@testable import Spine

/// The media page's Your Tracking card: Home's bar with today's part lit, and its one date.
@MainActor
final class TrackingCardModelTests: XCTestCase {
    private static var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        return calendar
    }

    private static let now = calendar.date(from: DateComponents(year: 2026, month: 11, day: 21, hour: 12))!

    private static func ref(_ type: String, season: Int? = nil) -> MediaRef {
        MediaRef(itemId: 1, source: "tmdb", mediaType: type, mediaId: "100", seasonNumber: season, episodeNumber: nil)
    }

    private static func show(watched: Int, aired: Int) -> TVShowState {
        TVShowState(inLibrary: true, status: "In progress", started: true, watchedCount: watched, airedCount: aired)
    }

    private static func model(
        _ type: String,
        season: Int? = nil,
        state: UserMediaState,
        today: TodayPart? = nil,
        baseline: Double? = nil
    ) -> TrackingCardModel {
        TrackingCardModel.make(
            detail: MediaDetail(ref: ref(type, season: season), title: "Fixture", userState: state),
            state: state,
            preferredMode: nil,
            today: today,
            baseline: baseline,
            now: now,
            calendar: calendar
        )
    }

    func testTodaysEpisodesAreTheLitPartOfTheShowsBar() {
        let state = UserMediaState(isTracked: true, status: "In progress", tv: Self.show(watched: 9, aired: 85))
        let card = Self.model("tv", state: state, today: TodayPart(unit: .episodes, amount: 2))

        XCTAssertEqual(card.meter?.from, 7)
        XCTAssertEqual(card.meter?.to, 9)
        XCTAssertEqual(card.meter?.total, 85)
    }

    func testProgressMadeOnThePageLightsBeforeTodaysRowsCatchUp() {
        let state = UserMediaState(isTracked: true, status: "In progress", tv: Self.show(watched: 11, aired: 85))
        let card = Self.model("tv", state: state, today: nil, baseline: 9)

        XCTAssertEqual(card.meter?.from, 9)
        XCTAssertEqual(card.meter?.to, 11)
    }

    func testASeasonLightsNoMoreThanItsOwnWatchedEpisodes() {
        var tv = Self.show(watched: 12, aired: 20)
        tv.season = TVSeasonState(
            seasonNumber: 2, state: .inProgress, watchedCount: 2, airedCount: 10, episodeCount: 10, availableActions: []
        )
        let state = UserMediaState(isTracked: true, status: "In progress", tv: tv)
        let card = Self.model("season", season: 2, state: state, today: TodayPart(unit: .episodes, amount: 5))

        XCTAssertEqual(card.meter?.from, 0)
        XCTAssertEqual(card.meter?.to, 2)
        XCTAssertEqual(card.meter?.total, 10)
    }

    func testTheDateIsOneLogsDayElseTheStartWithTheYearOnlyWhenItIsNotThisYear() {
        let logged = UserMediaState(isTracked: true, status: "Completed", diaryEntryId: 4, diaryCount: 1, diaryConsumedAt: "2026-11-18")
        XCTAssertEqual(Self.model("movie", state: logged).date, "Logged Nov 18")

        var reading = UserMediaState(isTracked: true, status: "In progress")
        reading.startDate = "2025-01-17"
        XCTAssertEqual(Self.model("book", state: reading).date, "Started Jan 17, 2025")

        let several = UserMediaState(isTracked: true, status: "Completed", diaryEntryId: 4, diaryCount: 3, diaryConsumedAt: "2026-10-31")
        let card = Self.model("movie", state: several)
        XCTAssertNil(card.date, "Several logs show their count, not one of their days")
        XCTAssertEqual(card.detail, "3 logs")
    }

    func testAFinishedBookEndsInTheCheckAndAMovieHasNoBar() {
        var book = UserMediaState(isTracked: true, status: "Completed")
        book.progress = ProgressState(kind: "pages", value: 300, max: 300, unit: "pages")
        let finished = Self.model("book", state: book)
        XCTAssertEqual(finished.meter?.endFraction, 1)
        XCTAssertTrue(finished.finished)

        let movie = UserMediaState(isTracked: true, status: "Completed", rating: "2.0")
        XCTAssertNil(Self.model("movie", state: movie).meter)
    }
}
