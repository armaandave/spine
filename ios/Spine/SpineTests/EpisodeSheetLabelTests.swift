import XCTest
@testable import Spine

/// The words on an update sheet's header (R3SheetTV, R3SheetLong): the white button names up next the way the In
/// progress tile's button does, and the summary line always has a title to show.
@MainActor
final class EpisodeSheetLabelTests: XCTestCase {
    private typealias Model = EpisodeTickModel

    private func season(_ number: Int?, count: Int = 10, watched: Int, titles: Bool = true) -> Model {
        let episodes = (1...count).map { n in
            Model.Episode(number: n, state: n <= watched ? .watched : .unwatched, title: titles ? "Title \(n)" : nil)
        }
        return Model(
            episodes: episodes,
            upNext: watched < count ? watched + 1 : nil,
            todayCount: 0,
            seasonNumber: number
        )
    }

    // MARK: The white button

    func testASeasonOneShowNamesTheEpisodeAlone() {
        XCTAssertEqual(season(1, watched: 6).primaryLabel, "Watched E7")
        XCTAssertEqual(season(nil, watched: 6).primaryLabel, "Watched E7", "An anime has no season")
    }

    func testALaterSeasonNamesTheSeasonToo() {
        XCTAssertEqual(season(2, watched: 6).primaryLabel, "Watched S2 E7")
        XCTAssertEqual(season(0, count: 4, watched: 2).primaryLabel, "Watched S0 E3", "Specials")
    }

    func testTheLongSeriesButtonGroupsThousands() {
        let total = 1150
        let episodes = (1...total).map { Model.Episode(number: $0, state: $0 <= 1088 ? .watched : .unwatched) }
        let model = Model(episodes: episodes, upNext: 1089, seasonNumber: nil)
        XCTAssertEqual(model.primaryLabel, "Watched E1,089")
        XCTAssertEqual(model.primaryAccessibilityLabel, "Mark episode 1,089 watched")
    }

    func testTheButtonAndTheTileAgree() {
        for (season, episode) in [(1, 7), (2, 7), (3, 12), (0, 3)] as [(Int, Int)] {
            XCTAssertEqual(
                QuickActionPlan.tickUpNext(season: season, episode: episode).label,
                "Watched " + Model.upNextCode(season: season, episode: episode),
                "S\(season) E\(episode)"
            )
        }
        XCTAssertEqual(
            QuickActionPlan.tickUpNext(season: nil, episode: 1089).label,
            "Watched " + Model.upNextCode(season: nil, episode: 1089)
        )
    }

    func testVoiceOverNamesTheSeasonWhenTheLabelDoes() {
        XCTAssertEqual(season(1, watched: 6).primaryAccessibilityLabel, "Mark episode 7 watched")
        XCTAssertEqual(season(2, watched: 6).primaryAccessibilityLabel, "Mark season 2 episode 7 watched")
    }

    func testNothingLeftToTickKeepsItsWords() {
        XCTAssertEqual(season(2, watched: 10).primaryLabel, "Season finished")
        XCTAssertEqual(season(2, watched: 10).primaryAccessibilityLabel, "Season finished")
    }

    func testPagingTheWindowKeepsTheSeason() {
        let model = season(2, count: 10, watched: 4)
        XCTAssertEqual(model.withPagingOffset(1).seasonNumber, 2)
        XCTAssertEqual(model.paged(1).seasonNumber, 2)
    }

    // MARK: The summary line

    func testTheSummaryUsesTheRealTitle() {
        XCTAssertEqual(season(2, watched: 6).summaryLine, "Up to E6 · Title 6")
    }

    func testTheSummaryFallsBackToEpisodeAndItsNumber() {
        XCTAssertEqual(season(2, watched: 6, titles: false).summaryLine, "Up to E6 · Episode 6")
        let blank = Model(
            episodes: [.init(number: 1, state: .watched, title: "")],
            upNext: nil
        )
        XCTAssertEqual(blank.summaryLine, "Up to E1 · Episode 1")
    }

    func testTheSummaryGroupsThousandsInBothPlaces() {
        let episodes = (1...1150).map { Model.Episode(number: $0, state: $0 <= 1088 ? .watched : .unwatched) }
        XCTAssertEqual(Model(episodes: episodes, upNext: 1089).summaryLine, "Up to E1,088 · Episode 1,088")
    }

    func testNothingWatchedStaysNothingWatched() {
        XCTAssertEqual(season(1, watched: 0).summaryLine, "Nothing watched yet")
    }

    // MARK: Today

    func testTodayIsTheAccentLineOnlyWhenThereIsSome() {
        let none = Model(episodes: [.init(number: 1, state: .watched)], upNext: nil, todayCount: 0)
        XCTAssertEqual(none.todayLine, "")
        let two = Model(
            episodes: [.init(number: 1, state: .watched), .init(number: 2, state: .watched)],
            upNext: nil,
            todayCount: 2
        )
        XCTAssertEqual(two.todayLine, "+2 today")
    }

    // MARK: The sheet builds the model with its season

    func testASeasonSheetBuildsItsModelWithItsSeason() {
        let model = ContinueWatchingEpisodes.tickModel(
            episodes: [.init(number: 1, state: .watched), .init(number: 2, state: .unwatched)],
            todayBase: nil,
            baseline: nil,
            seasonNumber: 3
        )
        XCTAssertEqual(model.primaryLabel, "Watched S3 E2")
        let anime = ContinueWatchingEpisodes.tickModel(
            episodes: [.init(number: 1, state: .watched), .init(number: 2, state: .unwatched)],
            todayBase: nil,
            baseline: nil
        )
        XCTAssertEqual(anime.primaryLabel, "Watched E2")
    }
}
