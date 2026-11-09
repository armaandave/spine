import XCTest
@testable import Spine

@MainActor
final class EpisodeTickModelTests: XCTestCase {
    private typealias Model = EpisodeTickModel

    // MARK: Fixtures

    /// A season: episodes 1...count, the first `watched` watched, the next `unaired` after the aired ones unaired.
    private func season(
        count: Int,
        watched: Int,
        unaired: Int = 0,
        todayCount: Int = 0,
        titles: Bool = true
    ) -> Model {
        let episodes = (1...count).map { number -> Model.Episode in
            let state: Model.EpisodeState
            if number <= watched {
                state = .watched
            } else if number > count - unaired {
                state = .unaired
            } else {
                state = .unwatched
            }
            return Model.Episode(number: number, state: state, title: titles ? "Title \(number)" : nil)
        }
        let upNext = watched + 1 <= count - unaired ? watched + 1 : nil
        return Model(episodes: episodes, upNext: upNext, todayCount: todayCount)
    }

    /// One Piece: 1,150 episodes, the first `watched` watched.
    private func longSeries(watched: Int, todayCount: Int = 0, pagingOffset: Int = 0) -> Model {
        let total = 1150
        let episodes = (1...total).map {
            Model.Episode(number: $0, state: $0 <= watched ? .watched : .unwatched)
        }
        return Model(
            episodes: episodes,
            upNext: watched < total ? watched + 1 : nil,
            todayCount: todayCount,
            pagingOffset: pagingOffset
        )
    }

    // MARK: Tick states

    func testTodayCountLightsTheLastWatchedEpisodes() {
        let model = season(count: 10, watched: 8, todayCount: 1)
        let states = (1...10).map { model.tickState(of: $0) }
        XCTAssertEqual(states, [.before, .before, .before, .before, .before, .before, .before, .today, .rest, .rest])

        let three = season(count: 10, watched: 8, todayCount: 3)
        XCTAssertEqual((1...10).map { three.tickState(of: $0) }.filter { $0 == .today }.count, 3)
        XCTAssertEqual(three.tickState(of: 5), .before)
        XCTAssertEqual(three.tickState(of: 6), .today)
        XCTAssertEqual(three.tickState(of: 8), .today)
    }

    func testNothingLitWithNoTodayCount() {
        let model = season(count: 10, watched: 8)
        XCTAssertFalse((1...10).contains { model.tickState(of: $0) == .today })
    }

    func testTodayCountLargerThanWatchedLightsAllWatchedOnly() {
        let model = season(count: 10, watched: 3, todayCount: 9)
        XCTAssertEqual((1...10).map { model.tickState(of: $0) }.filter { $0 == .today }.count, 3)
        XCTAssertEqual(model.tickState(of: 4), .rest)
    }

    func testTodayFollowsWatchedEpisodesWhenTheyHaveGaps() {
        let episodes: [Model.Episode] = [
            .init(number: 1, state: .watched), .init(number: 2, state: .unwatched), .init(number: 3, state: .watched),
            .init(number: 4, state: .watched), .init(number: 5, state: .unwatched),
        ]
        let model = Model(episodes: episodes, upNext: 2, todayCount: 2)
        XCTAssertEqual((1...5).map { model.tickState(of: $0) }, [.before, .rest, .today, .today, .rest])
    }

    func testNegativeTodayCountIsZero() {
        let model = Model(episodes: [.init(number: 1, state: .watched)], upNext: nil, todayCount: -4)
        XCTAssertEqual(model.todayCount, 0)
        XCTAssertEqual(model.tickState(of: 1), .before)
    }

    func testEpisodesAreSortedAndNumbersAreUnique() {
        let model = Model(
            episodes: [.init(number: 3, state: .watched), .init(number: 1, state: .watched), .init(number: 3, state: .unwatched)],
            upNext: nil
        )
        XCTAssertEqual(model.episodes.map(\.number), [1, 3])
    }

    // MARK: Words

    func testSummaryLineNamesTheLastWatchedEpisode() {
        let model = season(count: 10, watched: 7, todayCount: 3)
        XCTAssertEqual(model.summaryLine, "Up to E7 · Title 7")
        XCTAssertEqual(model.todayLine, "+3 today")
    }

    func testSummaryLineWithoutATitleOrAnyProgress() {
        XCTAssertEqual(season(count: 10, watched: 7, titles: false).summaryLine, "Up to E7 · Episode 7")
        XCTAssertEqual(season(count: 10, watched: 0).summaryLine, "Nothing watched yet")
        XCTAssertEqual(season(count: 10, watched: 7).todayLine, "")
    }

    func testPrimaryLabels() {
        XCTAssertEqual(season(count: 10, watched: 7).primaryLabel, "Watched E8")
        XCTAssertTrue(season(count: 10, watched: 7).primaryEnabled)
        let finished = season(count: 10, watched: 10)
        XCTAssertEqual(finished.primaryLabel, "Season finished")
        XCTAssertFalse(finished.primaryEnabled)
        // Caught up with episodes still to air is not a finished season.
        XCTAssertEqual(season(count: 10, watched: 8, unaired: 2).primaryLabel, "All caught up")
        XCTAssertEqual(season(count: 10, watched: 7).primaryAccessibilityLabel, "Mark episode 8 watched")
    }

    func testLongSeriesPrimaryLabels() {
        XCTAssertEqual(longSeries(watched: 1089).primaryLabel, "Watched E1,090")
        let done = longSeries(watched: 1150)
        XCTAssertEqual(done.primaryLabel, "All caught up")
        XCTAssertFalse(done.primaryEnabled)
    }

    func testGroupedDoesNotDependOnTheLocale() {
        XCTAssertEqual(Model.grouped(0), "0")
        XCTAssertEqual(Model.grouped(999), "999")
        XCTAssertEqual(Model.grouped(1000), "1,000")
        XCTAssertEqual(Model.grouped(1150), "1,150")
        XCTAssertEqual(Model.grouped(1_234_567), "1,234,567")
    }

    // MARK: Strip or window

    func testTwelveAndFourteenEpisodesUseTheStrip() {
        XCTAssertFalse(season(count: 12, watched: 3).usesWindow)
        XCTAssertFalse(season(count: 14, watched: 3).usesWindow)
        XCTAssertEqual(season(count: 14, watched: 3).stripTicks.count, 14)
    }

    func testFifteenEpisodesUseTheWindow() {
        let model = season(count: 15, watched: 3)
        XCTAssertTrue(model.usesWindow)
        XCTAssertEqual(model.windowFirst, 1)
        XCTAssertEqual(model.windowTicks.count, 15)
        XCTAssertTrue(longSeries(watched: 3).usesWindow)
    }

    func testSeriesTotalCanBeLongerThanTheLoadedEpisodes() {
        let model = Model(episodes: [.init(number: 1, state: .watched)], upNext: 2, seriesTotal: 1150)
        XCTAssertEqual(model.total, 1150)
        XCTAssertTrue(model.usesWindow)
    }

    // MARK: Tap rules

    func testTapRulesForASeasonInProgress() {
        // 1-4 watched, 5 up next, 6-8 aired, 9 unaired, 10 unlisted.
        var episodes = (1...4).map { Model.Episode(number: $0, state: .watched) }
        episodes += (5...8).map { Model.Episode(number: $0, state: .unwatched) }
        episodes.append(.init(number: 9, state: .unaired))
        episodes.append(.init(number: 10, state: .unlisted))
        let model = Model(episodes: episodes, upNext: 5)

        XCTAssertEqual(model.action(for: 5), .tick, "up next")
        XCTAssertEqual(model.action(for: 6), .markUpTo, "beyond up next")
        XCTAssertEqual(model.action(for: 8), .markUpTo)
        XCTAssertEqual(model.action(for: 4), .untick, "the last watched")
        XCTAssertEqual(model.action(for: 2), .untick, "an earlier watched one")
        XCTAssertEqual(model.action(for: 9), .none, "unaired")
        XCTAssertEqual(model.action(for: 10), .none, "unlisted")
        XCTAssertEqual(model.action(for: 99), .none, "not in the run at all")
    }

    func testTapRulesForAFinishedSeason() {
        let model = season(count: 10, watched: 10)
        for number in 1...10 {
            XCTAssertEqual(model.action(for: number), .untick, "episode \(number)")
        }
        XCTAssertEqual(model.action(for: 11), .none)
    }

    func testTapRulesWhenCaughtUpWithEpisodesToAir() {
        let model = season(count: 10, watched: 8, unaired: 2)
        XCTAssertNil(model.upNext)
        XCTAssertEqual(model.action(for: 8), .untick)
        XCTAssertEqual(model.action(for: 9), .none)
        XCTAssertEqual(model.action(for: 10), .none)
    }

    func testAnUnwatchedEpisodeBeforeUpNextIsJustTicked() {
        // The host named episode 5 up next although 2 is a skipped gap: tapping 2 marks only 2.
        let episodes: [Model.Episode] = [
            .init(number: 1, state: .watched), .init(number: 2, state: .unwatched), .init(number: 3, state: .watched),
            .init(number: 4, state: .watched), .init(number: 5, state: .unwatched),
        ]
        let model = Model(episodes: episodes, upNext: 5)
        XCTAssertEqual(model.action(for: 2), .tick)
        XCTAssertEqual(model.action(for: 5), .tick)
    }

    func testTicksCarryTheirActionAndSpokenWords() {
        let model = season(count: 10, watched: 4)
        let ticks = model.stripTicks
        XCTAssertEqual(ticks[0].accessibilityLabel, "Watched up to episode 1, Title 1")
        XCTAssertEqual(ticks[0].accessibilityValue, "Watched")
        XCTAssertEqual(ticks[4].action, .tick)
        XCTAssertEqual(ticks[4].accessibilityValue, "Not watched")
        XCTAssertEqual(ticks[5].action, .markUpTo)
        let today = season(count: 10, watched: 4, todayCount: 1).stripTicks[3]
        XCTAssertEqual(today.accessibilityValue, "Watched today")
        let untitled = season(count: 10, watched: 4, titles: false).stripTicks[0]
        XCTAssertEqual(untitled.accessibilityLabel, "Watched up to episode 1")
    }

    func testHitHeightIsAtLeast44() {
        XCTAssertGreaterThanOrEqual(EpisodeTickStyle.minHit, 44)
        XCTAssertGreaterThanOrEqual(EpisodeTickStyle.stripTickHeight, 44)
        XCTAssertGreaterThanOrEqual(EpisodeTickStyle.windowTickRowHeight, 44)
        XCTAssertEqual(EpisodeTickStyle.primaryHeight, 52)
    }

    // MARK: Window math

    func testWindowForOnePieceAt1089StartsAt1074() {
        let model = longSeries(watched: 1089, todayCount: 1)
        XCTAssertEqual(model.windowFirst, 1074)
        XCTAssertEqual(model.windowLast, 1093)
        XCTAssertEqual(model.windowLabel, "E1,074 – E1,093")
        let ticks = model.windowTicks
        XCTAssertEqual(ticks.count, 20)
        XCTAssertEqual(ticks.first?.number, 1074)
        XCTAssertEqual(ticks.last?.number, 1093)
    }

    func testWindowAtBothEnds() {
        XCTAssertEqual(longSeries(watched: 1).windowFirst, 1)
        XCTAssertEqual(longSeries(watched: 0).windowFirst, 1)
        XCTAssertEqual(longSeries(watched: 1150).windowFirst, 1131)
        XCTAssertEqual(longSeries(watched: 1150).windowLast, 1150)
        XCTAssertEqual(longSeries(watched: 1150).windowLabel, "E1,131 – E1,150")
    }

    func testWindowTickStatesAndHeights() {
        let model = longSeries(watched: 1089, todayCount: 2)
        let byNumber = Dictionary(uniqueKeysWithValues: model.windowTicks.map { ($0.number, $0.state) })
        XCTAssertEqual(byNumber[1087], .before)
        XCTAssertEqual(byNumber[1088], .today)
        XCTAssertEqual(byNumber[1089], .today)
        XCTAssertEqual(byNumber[1090], .rest)
        XCTAssertEqual(Model.windowTickHeight(.today), 60)
        XCTAssertEqual(Model.windowTickHeight(.before), 48)
        XCTAssertEqual(Model.windowTickHeight(.rest), 40)
        XCTAssertEqual(model.windowTicks.first?.accessibilityLabel, "Watched up to episode 1,074")
    }

    func testPagingMovesByTwentyAndStopsAtBothEnds() {
        let model = longSeries(watched: 1089)
        XCTAssertEqual(model.paged(-1).windowFirst, 1054)
        XCTAssertEqual(model.paged(1).windowFirst, 1094)
        XCTAssertEqual(model.paged(-1).paged(-1).windowFirst, 1034)
        XCTAssertEqual(model.paged(-1).paged(1).windowFirst, 1074)
        // The end: 1131 is the last start; paging later stays and paging back leaves at once.
        var end = model
        for _ in 0..<5 { end = end.paged(1) }
        XCTAssertEqual(end.windowFirst, 1131)
        XCTAssertEqual(end.paged(1).windowFirst, 1131)
        XCTAssertEqual(end.paged(-1).windowFirst, 1111)
        // The start: the same at the other end.
        var start = model
        for _ in 0..<80 { start = start.paged(-1) }
        XCTAssertEqual(start.windowFirst, 1)
        XCTAssertEqual(start.paged(-1).windowFirst, 1)
        XCTAssertEqual(start.paged(1).windowFirst, 21)
    }

    func testPagingKeepsTheEpisodeTheSameAndResetsWithZeroOffset() {
        let paged = longSeries(watched: 1089).paged(-1)
        XCTAssertEqual(paged.upTo, 1089)
        XCTAssertEqual(paged.withPagingOffset(0).windowFirst, 1074)
        XCTAssertEqual(longSeries(watched: 1089, pagingOffset: 20).windowFirst, 1094)
    }

    func testSeriesLineFractions() {
        let model = longSeries(watched: 1089)
        XCTAssertEqual(model.seriesFill, 1089.0 / 1150, accuracy: 1e-9)
        XCTAssertEqual(model.seriesWindowStart, 1073.0 / 1150, accuracy: 1e-9)
        XCTAssertEqual(model.seriesWindowWidth, 20.0 / 1150, accuracy: 1e-9)
        XCTAssertEqual(model.seriesAccessibilityLabel, "Episode 1,089 of 1,150")
        // A short window on a very long series keeps a visible box.
        let huge = Model(episodes: [.init(number: 1, state: .watched)], upNext: 2, seriesTotal: 5000)
        XCTAssertEqual(huge.seriesWindowWidth, EpisodeTickStyle.lineBoxMinFraction, accuracy: 1e-9)
    }

    // MARK: Typing

    func testTypingKeepsDigitsOnly() {
        let model = longSeries(watched: 1089)
        XCTAssertEqual(model.sanitizedTyped("12ab3", previous: ""), "123")
        XCTAssertEqual(model.sanitizedTyped("１２", previous: "9"), "", "full-width digits are not digits")
        XCTAssertEqual(model.sanitizedTyped("  7 ", previous: ""), "7")
        XCTAssertEqual(model.sanitizedTyped("abc", previous: "5"), "")
    }

    func testTypingIgnoresOutOfRangeValues() {
        let model = longSeries(watched: 1089)
        XCTAssertEqual(model.sanitizedTyped("1150", previous: "115"), "1150")
        XCTAssertEqual(model.sanitizedTyped("1151", previous: "115"), "115")
        XCTAssertEqual(model.sanitizedTyped("99999", previous: "9999"), "9999")
        XCTAssertEqual(model.sanitizedTyped("2000", previous: "200"), "200")
    }

    func testTypingDropsLeadingZeros() {
        let model = longSeries(watched: 1089)
        XCTAssertEqual(model.sanitizedTyped("0", previous: ""), "")
        XCTAssertEqual(model.sanitizedTyped("007", previous: ""), "7")
    }

    func testTypedEpisodeMustBeInRange() {
        let model = longSeries(watched: 1089)
        XCTAssertEqual(model.typedEpisode("1"), 1)
        XCTAssertEqual(model.typedEpisode("1150"), 1150)
        XCTAssertNil(model.typedEpisode(""))
        XCTAssertNil(model.typedEpisode("0"))
        XCTAssertNil(model.typedEpisode("1151"))
        XCTAssertNil(model.typedEpisode("12x"))
    }

    func testWindowTodayLineFollowsTheTypedNumber() {
        let model = longSeries(watched: 1089, todayCount: 1)
        XCTAssertEqual(model.windowTodayLine(typed: nil), "+1 today")
        XCTAssertEqual(model.windowTodayLine(typed: 1089), "+1 today")
        XCTAssertEqual(model.windowTodayLine(typed: 1092), "+4 today")
        XCTAssertEqual(model.windowTodayLine(typed: 1000), "moved back")
        XCTAssertEqual(longSeries(watched: 1089).windowTodayLine(typed: nil), "")
    }
}
