import SwiftUI
import XCTest
@testable import Spine

/// A post page says everything its feed card says. These run at the model level: the page's content is built from the
/// card's own presenter, so each kind of day on each kind of title is checked against the card made from the same row.
@MainActor
final class PostPageParityTests: XCTestCase {
    private func card(_ item: ActivityItem, standing: FeedStanding? = nil, newest: Bool = true) -> FeedCardModel {
        FeedCardPresenter.model(for: item, standing: standing, isNewestOfTitle: newest)
    }

    private func page(
        _ item: ActivityItem,
        card: FeedCardModel? = nil,
        standing: FeedStanding? = nil,
        newest: Bool? = true
    ) -> ProgressPageContent {
        ProgressPageContent(model: ProgressPageContent.model(
            for: item, card: card, standing: standing, isNewestOfTitle: newest
        ))
    }

    private func standing(_ unit: TodayUnit, current: Double, total: Double?, pagesTotal: Double? = nil) throws -> FeedStanding {
        try XCTUnwrap(FeedStandingMath.standing(
            position: TilePosition(unit: unit, current: current, total: total, pagesTotal: pagesTotal), told: nil
        ))
    }

    // MARK: Every media type's day

    private struct DayCase {
        let name: String
        let mediaType: String
        let source: String
        let season: Int?
        let episode: Int?
        let label: String
        let unit: String?
        let title: String
        let standing: () throws -> FeedStanding
        let hasMeter: Bool
    }

    private func dayCases() -> [DayCase] {
        [
            DayCase(
                name: "book pages", mediaType: "book", source: "manual", season: nil, episode: nil, label: "86 pages",
                unit: "pages", title: "Perf My Book",
                standing: { try self.standing(.pages, current: 212, total: 448, pagesTotal: 448) },
                hasMeter: true
            ),
            DayCase(
                name: "book percent", mediaType: "book", source: "manual", season: nil, episode: nil, label: "25%",
                unit: "percent", title: "Perf My Book",
                standing: { try self.standing(.percent, current: 63, total: 100) },
                hasMeter: true
            ),
            DayCase(
                name: "tv episode", mediaType: "episode", source: "tmdb", season: 2, episode: 7, label: "3 episodes",
                unit: "minutes", title: "Perf My Book",
                standing: { try XCTUnwrap(FeedStandingMath.seasonStanding(season: 2, watched: 7, total: 10, told: nil)) },
                hasMeter: true
            ),
            DayCase(
                name: "tv season", mediaType: "season", source: "tmdb", season: 2, episode: nil, label: "6 episodes",
                unit: "minutes", title: "Perf My Book",
                standing: { try XCTUnwrap(FeedStandingMath.seasonStanding(season: 2, watched: nil, total: 8, told: nil)) },
                hasMeter: true
            ),
            DayCase(
                name: "anime episode", mediaType: "episode", source: "mal", season: 1, episode: 7, label: "2 episodes",
                unit: "minutes", title: "Perf My Book",
                standing: { try self.standing(.episodes, current: 63, total: 70) },
                hasMeter: true
            ),
            DayCase(
                name: "anime season", mediaType: "season", source: "mal", season: 1, episode: nil, label: "12 episodes",
                unit: "minutes", title: "Perf My Book",
                standing: { try self.standing(.episodes, current: 12, total: nil) },
                hasMeter: false
            ),
            DayCase(
                name: "manga chapters", mediaType: "manga", source: "mal", season: nil, episode: nil, label: "4 chapters",
                unit: "chapters", title: "Perf My Book",
                standing: { try self.standing(.chapters, current: 41, total: 90) },
                hasMeter: true
            ),
            DayCase(
                name: "comic issues", mediaType: "comic", source: "comicvine", season: nil, episode: nil, label: "1 issue",
                unit: "issues", title: "Perf My Book",
                standing: { try self.standing(.issues, current: 5, total: nil) },
                hasMeter: false
            ),
            DayCase(
                name: "game time", mediaType: "game", source: "igdb", season: nil, episode: nil, label: "1h 30m",
                unit: "minutes", title: "Perf My Book",
                standing: { try self.standing(.minutes, current: 840, total: nil) },
                hasMeter: false
            ),
            DayCase(
                name: "game percent", mediaType: "game", source: "igdb", season: nil, episode: nil, label: "25%",
                unit: "minutes", title: "Perf My Book",
                standing: { try self.standing(.percent, current: 63, total: 100) },
                hasMeter: true
            ),
        ]
    }

    /// `labelled: false` is a status-only day: the server sends the moves and no amount.
    private func dayItem(_ day: DayCase, moves: [String]? = nil, labelled: Bool = true) throws -> ActivityItem {
        try FeedTestItems.day(
            label: labelled ? day.label : nil,
            moves: moves,
            unit: day.unit,
            mediaType: day.mediaType,
            source: day.source,
            season: day.season,
            episode: day.episode,
            title: day.title,
            mediaFields: "\"show_title\": \"Perf My Book\""
        )
    }

    func testEveryProgressDayShowsOnItsPageWhatItsCardShows() throws {
        for day in dayCases() {
            let item = try dayItem(day)
            let fed = try day.standing()
            let onCard = card(item, standing: fed)
            let onPage = page(item, standing: fed)

            XCTAssertEqual(onCard.kind, .progress, day.name)
            XCTAssertEqual(onPage.title, onCard.title, "\(day.name): title")
            XCTAssertEqual(onPage.tag, onCard.tag, "\(day.name): tag")
                        XCTAssertNotNil(onPage.amount, "\(day.name): amount")
            XCTAssertNotNil(onPage.standing, "\(day.name): where the person stands")
            XCTAssertEqual(onPage.meter, onCard.meter, "\(day.name): meter")
            XCTAssertEqual(onPage.meter != nil, day.hasMeter, "\(day.name): has a meter")
            XCTAssertEqual(onPage.art, onCard.art, "\(day.name): art")
            XCTAssertFalse(onPage.spokenHeadline.isEmpty, "\(day.name): spoken")
        }
    }

    func testThePageKeepsTheStandingBesideStatusPillsWhereTheCardDropsIt() throws {
        for day in dayCases() {
            let item = try dayItem(day, moves: ["paused"])
            let fed = try day.standing()
            let onCard = card(item, standing: fed)
            let onPage = page(item, standing: fed)
            guard case let .progress(_, tail, pills)? = onCard.headline else { return XCTFail("\(day.name): not a day") }
                        XCTAssertEqual(onPage.pills, pills, "\(day.name): pills")
            XCTAssertNotNil(onPage.amount, "\(day.name): amount")
            XCTAssertEqual(onPage.standing, tail, "\(day.name): the standing the card holds back")
            XCTAssertNotNil(onPage.standing, day.name)
        }
    }

    func testAStatusOnlyDayShowsItsPillsAndWhereOnThePage() throws {
        for word in ["paused", "stopped", "picked_back_up"] {
            for day in dayCases() {
                let item = try dayItem(day, moves: [word], labelled: false)
                let fed = try day.standing()
                let onCard = card(item, standing: fed)
                let onPage = page(item, standing: fed)
                XCTAssertEqual(onCard.kind, .status, "\(day.name) \(word)")
                XCTAssertNil(onPage.amount, "\(day.name) \(word)")
                XCTAssertEqual(onPage.pills.count, 1, "\(day.name) \(word)")
                XCTAssertNotNil(onPage.standing, "\(day.name) \(word)")
                XCTAssertEqual(onPage.meter, onCard.meter, "\(day.name) \(word)")
                XCTAssertEqual(onPage.title, onCard.title, "\(day.name) \(word)")
                XCTAssertEqual(onPage.tag, onCard.tag, "\(day.name) \(word)")
            }
        }
    }

    func testAStartAndAnOldServersDayShowOnThePage() throws {
        let start = try FeedTestItems.day(label: "Started")
        let started = page(start)
        XCTAssertTrue(started.isStart)
        XCTAssertNil(started.amount)
        XCTAssertNil(started.meter, "A start never has a bar")
        XCTAssertTrue(started.hasHeadline)

        let old = try FeedTestItems.day(summary: "watched 4 episodes", mediaType: "tv")
        let words = page(old)
        XCTAssertNotNil(words.words)
        XCTAssertTrue(words.hasHeadline)

        let none = page(try FeedTestItems.day())
        XCTAssertFalse(none.hasHeadline)
    }

    // MARK: Where the content comes from

    func testAPageOpenedFromACardDrawsThatCard() throws {
        let item = try FeedTestItems.day(label: "86 pages", unit: "pages")
        let fed = try standing(.pages, current: 212, total: 448, pagesTotal: 448)
        let onCard = card(item, standing: fed)
        let fromCard = page(item, card: onCard, standing: nil, newest: nil)
        XCTAssertEqual(fromCard.standing, ProgressPageContent(model: onCard).standing)
        XCTAssertNotNil(fromCard.standing)
        XCTAssertNotNil(fromCard.meter)
        XCTAssertTrue(ProgressPageContent.hasStanding(onCard))
        XCTAssertFalse(ProgressPageContent.hasStanding(card(item)))
        XCTAssertFalse(ProgressPageContent.hasStanding(nil))
    }

    func testAPageOpenedOnItsOwnLoadsTheStandingForTheNewestDay() throws {
        let item = try FeedTestItems.day(label: "86 pages", unit: "pages")
        let fed = try standing(.pages, current: 212, total: 448, pagesTotal: 448)

        let before = page(item, standing: nil, newest: nil)
        XCTAssertNotNil(before.amount)
        XCTAssertNil(before.standing)
        XCTAssertNil(before.meter)

        let after = page(item, standing: fed, newest: true)
        XCTAssertNotNil(after.standing)
        XCTAssertEqual(after.meter?.kind, .bar(total: 448))
        XCTAssertEqual(after, ProgressPageContent(model: card(item, standing: fed)))
    }

    func testAnOlderDayNeverGetsTheStandingNow() throws {
        let item = try FeedTestItems.day(label: "86 pages", unit: "pages")
        let fed = try standing(.pages, current: 212, total: 448, pagesTotal: 448)
        let older = page(item, standing: fed, newest: false)
        XCTAssertNotNil(older.amount)
        XCTAssertNil(older.standing)
        XCTAssertNil(older.meter)
    }

    func testTheNewestDayOfATitleIsFoundInTheirRecentActivity() throws {
        let newer = try FeedTestItems.day(id: 5, label: "10 pages", consumedAt: "2026-11-08")
        let older = try FeedTestItems.day(id: 4, label: "86 pages", consumedAt: "2026-11-07")
        let other = try FeedTestItems.day(id: 6, label: "2 chapters", mediaType: "manga", mediaID: "9")
        let recent = [other, newer, older]
        XCTAssertTrue(ProgressPageContent.isNewestOfTitle(newer, among: recent))
        XCTAssertFalse(ProgressPageContent.isNewestOfTitle(older, among: recent))
        XCTAssertTrue(ProgressPageContent.isNewestOfTitle(other, among: recent))
        XCTAssertFalse(ProgressPageContent.isNewestOfTitle(older, among: []), "Not among them: older than what was read")
    }

    func testAShowIsOneTitleWhateverItsSeasonOrEpisode() throws {
        let episode = try FeedTestItems.day(
            id: 8, label: "1 episode", mediaType: "episode", source: "tmdb", season: 2, episode: 7
        )
        let season = try FeedTestItems.day(
            id: 7, label: "5 episodes", mediaType: "season", source: "tmdb", season: 1
        )
        XCTAssertTrue(ProgressPageContent.isNewestOfTitle(episode, among: [episode, season]))
        XCTAssertFalse(ProgressPageContent.isNewestOfTitle(season, among: [episode, season]))
    }

    // MARK: Logs

    func testALogsMarksOnItsPageAreTheCardsMarksInTheCardsOrder() throws {
        let cases: [(String?, Bool, Bool)] = [
            ("4.5", false, false), ("3", true, false), ("0.5", true, true), (nil, true, false),
            (nil, false, true), ("5", true, true), ("2.5", false, true),
        ]
        for (rating, liked, rewatch) in cases {
            let item = try FeedTestItems.log(rating: rating, liked: liked, rewatch: rewatch)
            let onCard = card(item)
            var expected: [FeedMark] = []
            if case let .marks(symbols, cardLiked, cardRewatch, _, _)? = onCard.headline {
                expected = symbols.compactMap(FeedMark.init(starSymbol:))
                if cardLiked { expected.append(.heart) }
                if cardRewatch { expected.append(.rewatch) }
            }
            XCTAssertEqual(
                PostRatingLine.marks(rating: rating, liked: liked, rewatch: rewatch),
                expected,
                "\(String(describing: rating)) liked \(liked) rewatch \(rewatch)"
            )
        }
    }

    func testEveryMediaTypesLogShowsItsRepeatMarkOnThePage() throws {
        for type in ["movie", "tv", "anime", "music", "book", "manga", "comic", "game"] {
            let item = try FeedTestItems.log(rating: "4", rewatch: true, mediaType: type)
            guard case let .marks(_, _, rewatch, label, _)? = card(item).headline else { return XCTFail(type) }
            XCTAssertTrue(rewatch, type)
            XCTAssertTrue(PostRatingLine.marks(rating: "4", liked: false, rewatch: rewatch).contains(.rewatch), type)
            XCTAssertFalse(label.isEmpty, type)
        }
    }

    func testThePageDrawsMarksWithTheCardsGlowBakedAtTheirOwnSize() throws {
        FeedMarkImages.reset()
        for mark in FeedMarkImages.warmSet {
            let page = try XCTUnwrap(FeedMarkImages.image(mark, ink: .dark, size: PostRatingLine.starSize))
            let card = try XCTUnwrap(FeedMarkImages.image(mark, ink: .dark))
            let bare = try XCTUnwrap(ImageRenderer(
                content: FeedMarkSymbol(mark: mark, ink: .dark, size: PostRatingLine.starSize)
            ).uiImage)
            let padding = FeedMarkImages.padding(for: .dark, size: PostRatingLine.starSize)

            XCTAssertFalse(page === card, "\(mark): the page's image is its own, not the card's stretched")
            XCTAssertGreaterThan(page.size.width, bare.size.width, "\(mark): the glow reaches past the symbol")
            XCTAssertEqual(page.size.width, bare.size.width + padding * 2, accuracy: 1.5, "\(mark)")
            XCTAssertEqual(page.size.height, bare.size.height + padding * 2, accuracy: 1.5, "\(mark)")
            XCTAssertTrue(page === FeedMarkImages.image(mark, ink: .dark, size: PostRatingLine.starSize), "\(mark): cached")
        }
    }

    func testTheGlowAndRoomGrowWithTheSymbolAndTheCardsOwnSizeIsUnchanged() {
        let card = HomeDesign.FeedDepth.markSymbolSize
        XCTAssertEqual(FeedMarkImages.padding(for: .dark, size: card), FeedMarkImages.padding(for: .dark), accuracy: 0.0001)
        XCTAssertEqual(FeedMarkImages.glowLayers(for: .dark, size: card), FeedMarkImages.glowLayers(for: .dark))
        let big = PostRatingLine.starSize
        let growth = big / card
        XCTAssertEqual(FeedMarkImages.padding(for: .dark, size: big), FeedMarkImages.padding(for: .dark) * growth, accuracy: 0.0001)
        for (large, small) in zip(FeedMarkImages.glowLayers(for: .dark, size: big), FeedMarkImages.glowLayers(for: .dark)) {
            XCTAssertEqual(large.radius, small.radius * growth, accuracy: 0.0001)
            XCTAssertEqual(large.opacity, small.opacity, accuracy: 0.0001)
            XCTAssertLessThanOrEqual(large.radius * 2, FeedMarkImages.padding(for: .dark, size: big), "A glow is never cut")
        }
    }

    func testWarmingStillFillsEightWhateverSizesThePagesAskedFor() {
        FeedMarkImages.reset()
        _ = FeedMarkImages.image(.star, ink: .dark, size: PostRatingLine.starSize)
        _ = FeedMarkImages.image(.heart, ink: .dark, size: PostRatingLine.heartSize)
        FeedMarkImages.warm()
        XCTAssertEqual(FeedMarkImages.cachedCount, 10)
        FeedMarkImages.reset()
        FeedMarkImages.warm()
        XCTAssertEqual(FeedMarkImages.cachedCount, 8)
    }

    // MARK: Planning

    func testAPlanningDaysPageNamesWhatTheCardNames() throws {
        for type in ["tv", "movie", "book", "music", "anime"] {
            let row = try FeedTestItems.planningRow(count: 3, mediaType: type)
            let onCard = FeedCardPresenter.model(for: row, isNewestOfTitle: false)
            XCTAssertFalse(onCard.title.isEmpty, type)
            XCTAssertEqual(onCard.username, "perf-ava", type)
            XCTAssertEqual(row.actor.username, onCard.username, "\(type): the page keeps the person to show")
        }
    }
}
