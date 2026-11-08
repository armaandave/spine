import XCTest
@testable import Spine

/// The In progress tile's panel, its quick action and its lit part, for the titles the perf data backend seeds
/// (a book 212 of 400, a game at 14h, a manga 41 of 90, a show 4 of 10) and the edges around them.
@MainActor
final class TileModelsTests: XCTestCase {
    private var touchedRefs: [MediaRef] = []

    override func tearDown() {
        for ref in touchedRefs {
            ProgressDisplayPreferences.removeMode(for: ref)
            ProgressDisplayPreferences.setGameMode(nil, for: ref)
        }
        touchedRefs = []
        super.tearDown()
    }

    // MARK: Fixtures

    private func ref(_ mediaType: String, _ id: String, source: String = "tmdb") -> MediaRef {
        let ref = MediaRef(itemId: nil, source: source, mediaType: mediaType, mediaId: "tile-\(id)",
                           seasonNumber: nil, episodeNumber: nil)
        touchedRefs.append(ref)
        return ref
    }

    private func item(
        _ mediaType: String,
        _ id: String,
        status: String = "In progress",
        accent: String? = nil,
        progress: ProgressState? = nil,
        book: BookTrackingState? = nil,
        series: BookTrackingState? = nil,
        game: GameTrackingState? = nil,
        tv: TVShowState? = nil,
        anime: AnimeState? = nil
    ) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(
                ref: ref(mediaType, id), title: "Title \(id)", posterUrl: nil, posterOrientation: .portrait,
                posterAccentColor: accent
            ),
            tracking: TrackingState(
                trackingId: 1, status: status, rating: nil, progress: progress, repeats: nil, startDate: nil,
                endDate: nil, notes: nil, updatedAt: nil, book: book, game: game, tv: tv, series: series, anime: anime
            )
        )
    }

    private func pages(_ value: Int, of max: Int = 400) -> ProgressState {
        ProgressState(kind: "pages", value: Decimal(value), max: Decimal(max), unit: "page")
    }

    private func book(_ id: String, pages value: Int, of max: Int = 400, status: String = "In progress") -> Spine.LibraryItem {
        let progress = pages(value, of: max)
        return item("book", id, status: status, progress: progress, book: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: status, progress: progress)
        ))
    }

    private func bookPercent(_ id: String, _ value: Int) -> Spine.LibraryItem {
        let progress = ProgressState(kind: "percentage", value: Decimal(value), max: 100, unit: "percent")
        return item("book", id, progress: progress, book: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress)
        ))
    }

    private func series(_ type: String, _ id: String, _ value: Int, of total: Int = 90) -> Spine.LibraryItem {
        let unit = type == "comic" ? "issue" : "chapter"
        let progress = ProgressState(kind: "count", value: Decimal(value), max: Decimal(total), unit: unit)
        return item(type, id, progress: progress, series: BookTrackingState(
            currentJourney: BookJourneyState(id: 1, status: "In progress", progress: progress), total: total, unit: unit
        ))
    }

    private func game(_ id: String, minutes: Int?, percent: Int?, status: String = "In progress") -> Spine.LibraryItem {
        let playthrough = GamePlaythroughState(
            id: 31, status: status, origin: "live", startDate: "2026-10-26", endDate: nil, totalMinutes: minutes,
            percentage: percent, completionDiaryEntryId: nil, isReplay: false
        )
        return item("game", id, status: status, game: GameTrackingState(
            currentPlaythrough: playthrough, playHistory: [], undatedCompletion: nil, statusSource: nil,
            completionDates: [], completedPlaythroughCount: 0, lifetimeCompletionCount: 0, isReplaying: false,
            canRemoveTracking: false, availableActions: [], actionReasons: [:], importedLifetimeMinutes: nil,
            importedLifetimeSource: nil
        ))
    }

    private func show(
        _ id: String,
        watched: Int?,
        aired: Int?,
        upNext: (Int, Int)? = nil,
        status: String = "In progress"
    ) -> Spine.LibraryItem {
        item("tv", id, status: status, tv: TVTrackingSurfacesTests.showState(
            status: status, watched: watched, aired: aired, upNext: upNext
        ))
    }

    private func anime(_ id: String, watched: Int?, aired: Int?, upNext: Int?) -> Spine.LibraryItem {
        item("anime", id, anime: AnimeState(
            inLibrary: true, status: "In progress", started: true, watchedCount: watched, airedCount: aired,
            upNext: upNext.map { AnimeEpisodePointer(episodeNumber: $0) }
        ))
    }

    private func detail(
        _ item: Spine.LibraryItem,
        today: TodayPart? = nil,
        baseline: Double? = nil,
        season: TileSeason? = nil
    ) -> TileDetailModel {
        InProgressTilePresenter.detail(for: item, today: today, baseline: baseline, season: season)
    }

    // MARK: Panel text, meter and action per type

    func testABookInPages() throws {
        let model = detail(book("pages", pages: 212))
        XCTAssertEqual(model.title, "Title pages")
        XCTAssertEqual(model.whereText, "p. 212 of 400")
        XCTAssertNil(model.todayText)
        XCTAssertEqual(model.meter?.kind, .bar(total: 400))
        XCTAssertEqual(model.meter?.from, 212)
        XCTAssertEqual(model.meter?.to, 212)
        XCTAssertEqual(model.primary, .addPages(20))
        XCTAssertEqual(model.primary?.label, "+20 pages")
        XCTAssertEqual(model.secondaryTitle, "Update")
    }

    func testTodaysPagesLightTheEndOfTheBar() throws {
        // Twenty pages were told today by the server; the book has not moved since the rail loaded.
        let model = detail(book("pages-today", pages: 212), today: TodayPart(unit: .pages, amount: 20), baseline: 212)
        XCTAssertEqual(model.todayText, "+20 pages")
        XCTAssertEqual(model.meter?.from, 192)
        XCTAssertEqual(model.meter?.to, 212)
        // A save from the tile after the rail loaded adds to it: 212 -> 232 with the server's 20 is 40 today.
        let moved = detail(book("pages-today", pages: 232), today: TodayPart(unit: .pages, amount: 20), baseline: 212)
        XCTAssertEqual(moved.todayText, "+40 pages")
        XCTAssertEqual(moved.meter?.from, 192)
        XCTAssertEqual(moved.meter?.to, 232)
    }

    func testABookSavedInPercentAndOnePageToday() throws {
        let model = detail(bookPercent("percent", 47), today: TodayPart(unit: .percent, amount: 3), baseline: 47)
        XCTAssertEqual(model.whereText, "47%")
        XCTAssertEqual(model.todayText, "+3%")
        XCTAssertEqual(model.meter?.kind, .bar(total: 100))
        XCTAssertEqual(model.meter?.from, 44)
        XCTAssertEqual(model.primary, .addPercent(5))
        XCTAssertEqual(model.primary?.label, "+5%")
        let onePage = detail(book("one-page", pages: 11), today: TodayPart(unit: .pages, amount: 1), baseline: 11)
        XCTAssertEqual(onePage.todayText, "+1 page")
    }

    func testAPagesBookShownInPercentConvertsThroughItsLength() throws {
        let page = book("pref-percent", pages: 100)
        ProgressDisplayPreferences.setMode(.percentage, for: page.media.ref)
        // 40 pages told today are 10% of a 400 page book.
        let model = detail(page, today: TodayPart(unit: .pages, amount: 40), baseline: 25)
        XCTAssertEqual(model.whereText, "25%")
        XCTAssertEqual(model.todayText, "+10%")
        XCTAssertEqual(model.meter?.from, 15)
        XCTAssertEqual(model.primary, .addPercent(5))
    }

    func testAGameInHoursHasNoMeter() throws {
        let model = detail(game("hours", minutes: 840, percent: nil))
        XCTAssertEqual(model.whereText, "14h")
        XCTAssertNil(model.meter)
        XCTAssertNil(model.todayText)
        XCTAssertEqual(model.primary, .addHours(1))
        XCTAssertEqual(model.primary?.label, "+1h")
        XCTAssertEqual(model.secondaryTitle, "Update")
        let today = detail(game("hours-today", minutes: 930, percent: nil), today: TodayPart(unit: .minutes, amount: 90), baseline: 930)
        XCTAssertEqual(today.whereText, "15h 30m")
        XCTAssertEqual(today.todayText, "+1h 30m")
        XCTAssertNil(today.meter)
    }

    func testAGameInPercentHasABar() throws {
        let model = detail(game("percent", minutes: nil, percent: 47), today: TodayPart(unit: .percent, amount: 6), baseline: 47)
        XCTAssertEqual(model.whereText, "47%")
        XCTAssertEqual(model.todayText, "+6%")
        XCTAssertEqual(model.meter?.kind, .bar(total: 100))
        XCTAssertEqual(model.meter?.from, 41)
        XCTAssertEqual(model.primary, .addPercent(2))
        XCTAssertEqual(model.primary?.label, "+2%")
    }

    func testAGamesHoursNeverBecomePercent() throws {
        // The game is tracked by percent; the server told the day in hours.
        let model = detail(game("mixed", minutes: nil, percent: 47), today: TodayPart(unit: .minutes, amount: 120), baseline: 47)
        XCTAssertNil(model.todayText)
        XCTAssertEqual(model.meter?.from, 47)
    }

    func testAMangaGetsABarBecauseItIsLong() throws {
        let model = detail(series("manga", "manga", 41), today: TodayPart(unit: .chapters, amount: 3), baseline: 41)
        XCTAssertEqual(model.whereText, "41 of 90 chapters")
        XCTAssertEqual(model.todayText, "+3 chapters")
        XCTAssertEqual(model.meter?.kind, .bar(total: 90))
        XCTAssertEqual(model.meter?.from, 38)
        XCTAssertEqual(model.primary, .addChapters(1, unit: "chapter"))
        XCTAssertEqual(model.primary?.label, "+1 chapter")
    }

    func testAComicCountsIssuesAndAShortOneGetsTicks() throws {
        let model = detail(series("comic", "comic", 6, of: 12))
        XCTAssertEqual(model.whereText, "6 of 12 issues")
        XCTAssertEqual(model.meter?.kind, .ticks(count: 12))
        XCTAssertEqual(model.primary?.label, "+1 issue")
    }

    func testAShowIsCountedByTheSeasonUpNextIsIn() throws {
        // 16 of the show's 19 aired episodes, up next S2 E8: the panel counts season 2, one tick per episode.
        let item = show("show", watched: 16, aired: 19, upNext: (2, 8))
        let season = TileSeason(seasonNumber: 2, watched: 7, total: 10, showWatchedAtRead: 16)
        let model = detail(item, season: season)
        XCTAssertEqual(model.whereText, "S2 \u{00B7} 7 of 10 episodes")
        XCTAssertEqual(model.meter?.kind, .ticks(count: 10))
        XCTAssertEqual(model.meter?.from, 7)
        XCTAssertEqual(model.meter?.to, 7)
        XCTAssertEqual(model.primary, .tickUpNext(season: 2, episode: 8))
        XCTAssertEqual(model.accessibilityLabel, "Title show, Season 2, 7 of 10 episodes")

        // Today's episodes are lit inside the season, never more than the season has watched.
        let lit = detail(item, today: TodayPart(unit: .episodes, amount: 2), baseline: 16, season: season)
        XCTAssertEqual(lit.todayText, "+2 ep")
        XCTAssertEqual(lit.meter?.from, 5)
        XCTAssertEqual(lit.meter?.to, 7)
        let binge = detail(item, today: TodayPart(unit: .episodes, amount: 12), baseline: 16, season: season)
        XCTAssertEqual(binge.meter?.from, 0)
        XCTAssertEqual(binge.meter?.to, 7)
    }

    func testAShowsSeasonWaitsForItsReadAndNeverShowsTheWholeShow() throws {
        let item = show("waiting", watched: 16, aired: 19, upNext: (2, 8))
        let before = detail(item)
        XCTAssertEqual(before.whereText, "Season 2")
        XCTAssertNil(before.meter)
        XCTAssertEqual(before.accessibilityLabel, "Title waiting, Season 2")
        // A read of another season (up next has moved on) is not this one.
        let stale = detail(item, season: TileSeason(seasonNumber: 1, watched: 9, total: 9, showWatchedAtRead: 9))
        XCTAssertEqual(stale.whereText, "Season 2")
        XCTAssertNil(stale.meter)
    }

    func testEpisodesWatchedSinceTheReadMoveTheSeasonOnAtOnce() throws {
        let season = TileSeason(seasonNumber: 2, watched: 7, total: 10, showWatchedAtRead: 16)
        let moved = detail(show("moved", watched: 18, aired: 19, upNext: (2, 10)), season: season)
        XCTAssertEqual(moved.whereText, "S2 \u{00B7} 9 of 10 episodes")
        // Never past the season's end, never below nothing.
        let past = detail(show("past", watched: 30, aired: 30, upNext: (2, 10)), season: season)
        XCTAssertEqual(past.whereText, "S2 \u{00B7} 10 of 10 episodes")
        let back = detail(show("back", watched: 2, aired: 19, upNext: (2, 1)), season: season)
        XCTAssertEqual(back.whereText, "S2 \u{00B7} 0 of 10 episodes")
    }

    func testCaughtUpCountsTheSeasonAiringNextAndAShowWithNeitherIsCountedWhole() throws {
        var tv = TVTrackingSurfacesTests.showState(status: "In progress", watched: 15, aired: 15, caughtUp: true)
        tv.nextAiring = TVAiring(seasonNumber: 3, episodeNumber: 6, airDate: "2026-11-12")
        let airing = item("tv", "airing", status: "In progress", tv: tv)
        XCTAssertEqual(InProgressTilePresenter.currentSeasonNumber(for: airing), 3)
        let model = detail(airing, season: TileSeason(seasonNumber: 3, watched: 5, total: 10, showWatchedAtRead: 15))
        XCTAssertEqual(model.whereText, "S3 \u{00B7} 5 of 10 episodes")

        let whole = detail(show("long", watched: 9, aired: 62))
        XCTAssertNil(InProgressTilePresenter.currentSeasonNumber(for: show("long", watched: 9, aired: 62)))
        XCTAssertEqual(whole.whereText, "9 of 62 episodes")
        guard case .window = whole.meter?.kind else { return XCTFail("A show of 62 episodes draws a window") }
    }

    func testALongSeasonGetsAWindow() throws {
        let model = detail(
            show("one-piece", watched: 1_089, aired: 1_150, upNext: (21, 200)),
            season: TileSeason(seasonNumber: 21, watched: 199, total: 300, showWatchedAtRead: 1_089)
        )
        XCTAssertEqual(model.whereText, "S21 \u{00B7} 199 of 300 episodes")
        guard case .window = model.meter?.kind else { return XCTFail("A season of 300 episodes draws a window") }
        XCTAssertEqual(model.primary?.label, "Watched S21 E200")
    }

    func testTheSeasonReadIsBuiltFromTheSeasonsTracking() throws {
        let read = try XCTUnwrap(TileSeason(
            state: TVSeasonState(seasonNumber: 2, state: .inProgress, watchedCount: 7, airedCount: 6, episodeCount: 10),
            showWatched: 16
        ))
        XCTAssertEqual(read, TileSeason(seasonNumber: 2, watched: 7, total: 10, showWatchedAtRead: 16))
        let airedOnly = TileSeason(state: TVSeasonState(seasonNumber: 1, watchedCount: 3, airedCount: 8), showWatched: nil)
        XCTAssertEqual(airedOnly?.total, 8)
        XCTAssertEqual(airedOnly?.showWatchedAtRead, 0)
        XCTAssertNil(TileSeason(state: TVSeasonState(seasonNumber: 4), showWatched: 3), "Nothing to count")
    }

    func testAnAnimeReadsLikeAShowWithoutASeason() throws {
        let model = detail(anime("anime", watched: 7, aired: 24, upNext: 8))
        XCTAssertEqual(model.whereText, "7 of 24 episodes")
        XCTAssertEqual(model.meter?.kind, .ticks(count: 24))
        XCTAssertEqual(model.primary, .tickUpNext(season: nil, episode: 8))
        XCTAssertEqual(model.primary?.label, "Watched E8")
    }

    func testAnUnknownEndHasWordsButNoMeter() throws {
        let model = detail(anime("open-end", watched: 7, aired: nil, upNext: 8))
        XCTAssertEqual(model.whereText, "7 episodes")
        XCTAssertNil(model.meter)
        XCTAssertEqual(model.primary?.label, "Watched E8")
    }

    func testMoviesAndMusicHaveNoMeterAndNoMainAction() throws {
        for type in ["movie", "music"] {
            let model = detail(item(type, type))
            XCTAssertNil(model.meter, type)
            XCTAssertNil(model.primary, type)
            XCTAssertNil(model.todayText, type)
            XCTAssertEqual(model.whereText, "", type)
            XCTAssertEqual(model.secondaryTitle, "Open", type)
            XCTAssertEqual(model.accessibilityLabel, "Title \(type)")
        }
    }

    func testTheAccentIsThePostersAndTheSubtitleCountsTitles() throws {
        XCTAssertEqual(detail(item("movie", "accent", accent: "#9BDBFF")).accentHex, "#9BDBFF")
        XCTAssertNil(detail(item("movie", "no-accent")).accentHex)
        XCTAssertEqual(InProgressTilePresenter.subtitle(count: 5), "5 titles")
        XCTAssertEqual(InProgressTilePresenter.subtitle(count: 1), "1 title")
    }

    func testVoiceOverReadsWhereAndTodayWithoutSymbols() throws {
        let model = detail(book("voice", pages: 212), today: TodayPart(unit: .pages, amount: 20), baseline: 212)
        XCTAssertEqual(model.accessibilityLabel, "Title voice, page 212 of 400, 20 pages today")
        XCTAssertEqual(
            detail(
                show("voice-show", watched: 4, aired: 10, upNext: (1, 5)),
                season: TileSeason(seasonNumber: 1, watched: 4, total: 10, showWatchedAtRead: 4)
            ).accessibilityLabel,
            "Title voice-show, Season 1, 4 of 10 episodes"
        )
    }

    func testLargeCountsAreGrouped() throws {
        XCTAssertEqual(InProgressTilePresenter.grouped(1089), "1,089")
        XCTAssertEqual(InProgressTilePresenter.grouped(62), "62")
        XCTAssertEqual(InProgressTilePresenter.grouped(1_150_000), "1,150,000")
        XCTAssertEqual(InProgressTilePresenter.number(5), "5")
        XCTAssertEqual(InProgressTilePresenter.number(42.5), "42.5")
        XCTAssertEqual(InProgressTilePresenter.number(12.34), "12.34")
    }

    // MARK: Which action a title offers

    func testAPlanThatWouldReachTheEndOpensTheSheetInsteadOfWriting() throws {
        // Pages: 20 more would reach or pass 400.
        XCTAssertEqual(QuickActionPlan.make(for: book("p379", pages: 379)), .addPages(20))
        XCTAssertEqual(QuickActionPlan.make(for: book("p380", pages: 380)), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: book("p395", pages: 395)), .openSheet(label: "Update"))
        // Percent: a book moves 5, a game 2.
        XCTAssertEqual(QuickActionPlan.make(for: bookPercent("b94", 94)), .addPercent(5))
        XCTAssertEqual(QuickActionPlan.make(for: bookPercent("b95", 95)), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: game("g97", minutes: nil, percent: 97)), .addPercent(2))
        XCTAssertEqual(QuickActionPlan.make(for: game("g98", minutes: nil, percent: 98)), .openSheet(label: "Update"))
        // Chapters and issues.
        XCTAssertEqual(QuickActionPlan.make(for: series("manga", "m88", 88)), .addChapters(1, unit: "chapter"))
        XCTAssertEqual(QuickActionPlan.make(for: series("manga", "m89", 89)), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: series("comic", "c11", 11, of: 12)), .openSheet(label: "Update"))
        // Shows and anime: the last aired episode finishes or catches up the title.
        XCTAssertEqual(QuickActionPlan.make(for: show("s8", watched: 8, aired: 10, upNext: (1, 9))), .tickUpNext(season: 1, episode: 9))
        XCTAssertEqual(QuickActionPlan.make(for: show("s9", watched: 9, aired: 10, upNext: (1, 10))), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: anime("a23", watched: 23, aired: 24, upNext: 24)), .openSheet(label: "Update"))
    }

    func testAnythingNotInProgressOrWithoutAnUpNextOpensTheSheet() throws {
        XCTAssertEqual(QuickActionPlan.make(for: book("paused", pages: 100, status: "Paused")), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: game("paused-game", minutes: 60, percent: nil, status: "Paused")), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: show("paused-show", watched: 4, aired: 10, upNext: (1, 5), status: "Paused")),
                       .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: show("no-next", watched: 4, aired: nil)), .openSheet(label: "Update"))
        XCTAssertEqual(QuickActionPlan.make(for: anime("no-next-anime", watched: 4, aired: nil, upNext: nil)), .openSheet(label: "Update"))
        // Nothing stored for a book yet.
        XCTAssertEqual(QuickActionPlan.make(for: item("book", "empty")), .openSheet(label: "Update"))
        XCTAssertFalse(QuickActionPlan.openSheet(label: "Update").writes)
    }

    func testAnUnknownEndNeverStopsATick() throws {
        XCTAssertEqual(QuickActionPlan.make(for: show("unknown-end", watched: 4, aired: nil, upNext: (1, 5))),
                       .tickUpNext(season: 1, episode: 5))
        XCTAssertEqual(QuickActionPlan.make(for: anime("unknown-end-anime", watched: 4, aired: nil, upNext: 5)),
                       .tickUpNext(season: nil, episode: 5))
        let openBook = item("book", "open-book", progress: ProgressState(kind: "pages", value: 50, max: nil, unit: "page"))
        XCTAssertEqual(QuickActionPlan.make(for: openBook), .addPages(20))
        XCTAssertTrue(QuickActionPlan.addPages(20).writes)
    }

    func testMoviesAndMusicHaveNoPlan() throws {
        XCTAssertNil(QuickActionPlan.make(for: item("movie", "plan-movie")))
        XCTAssertNil(QuickActionPlan.make(for: item("music", "plan-music")))
    }

    func testEveryPlanSaysWhatItDoes() throws {
        XCTAssertEqual(QuickActionPlan.tickUpNext(season: 2, episode: 8).label, "Watched S2 E8")
        XCTAssertEqual(QuickActionPlan.tickUpNext(season: 1, episode: 8).label, "Watched E8")
        XCTAssertEqual(QuickActionPlan.tickUpNext(season: 0, episode: 3).label, "Watched S0 E3")
        XCTAssertEqual(QuickActionPlan.tickUpNext(season: nil, episode: 1090).label, "Watched E1,090")
        XCTAssertEqual(QuickActionPlan.tickUpNext(season: nil, episode: 8).label, "Watched E8")
        XCTAssertEqual(QuickActionPlan.addPages(20).label, "+20 pages")
        XCTAssertEqual(QuickActionPlan.addPercent(5).label, "+5%")
        XCTAssertEqual(QuickActionPlan.addChapters(1, unit: "chapter").label, "+1 chapter")
        XCTAssertEqual(QuickActionPlan.addChapters(1, unit: "issue").label, "+1 issue")
        XCTAssertEqual(QuickActionPlan.addHours(1).label, "+1h")
        XCTAssertEqual(QuickActionPlan.openSheet(label: "Update").label, "Update")
    }

    func testBaselinesComeFromTheSameNumbersThePanelShows() throws {
        XCTAssertEqual(InProgressTilePresenter.position(for: book("base", pages: 212)),
                       TilePosition(unit: .pages, current: 212, total: 400, pagesTotal: 400))
        XCTAssertEqual(InProgressTilePresenter.position(for: game("base-game", minutes: 840, percent: nil)),
                       TilePosition(unit: .minutes, current: 840, total: nil, pagesTotal: nil))
        XCTAssertEqual(InProgressTilePresenter.position(for: show("base-show", watched: 4, aired: 10)),
                       TilePosition(unit: .episodes, current: 4, total: 10, pagesTotal: nil))
        XCTAssertNil(InProgressTilePresenter.position(for: item("movie", "base-movie")))
    }

    // MARK: Today's math

    func testServerPlusLocalIsToday() {
        let today = TileTodayMath.today(
            server: TodayPart(unit: .episodes, amount: 2), baseline: 4, current: 5, unit: .episodes, total: 10
        )
        XCTAssertEqual(today, 3)
        XCTAssertEqual(TileTodayMath.today(server: nil, baseline: 4, current: 6, unit: .episodes, total: 10), 2)
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .pages, amount: 20), baseline: nil, current: 232, unit: .pages, total: 400
        ), 20, "Without a baseline only the server counts")
    }

    func testPagesAndPercentConvertThroughAKnownLength() {
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .pages, amount: 20), baseline: 10, current: 12, unit: .percent, total: 400
        ), 7, "20 pages of 400 are 5%, plus 2% local")
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .percent, amount: 5), baseline: 100, current: 130, unit: .pages, total: 400
        ), 50, "5% of 400 pages is 20, plus 30 local")
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .pages, amount: 1), baseline: nil, current: nil, unit: .percent, total: 300
        ), 0.33)
    }

    func testUnitsThatCannotConvertCountOnlyTheLocalChange() {
        // Pages against percent with no length known.
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .pages, amount: 20), baseline: 10, current: 12, unit: .percent, total: nil
        ), 2)
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .percent, amount: 5), baseline: 100, current: 130, unit: .pages, total: 0
        ), 30)
        // Games never convert minutes to percent, whatever the length.
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .minutes, amount: 120), baseline: 40, current: 42, unit: .percent, total: 100
        ), 2)
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .percent, amount: 10), baseline: 60, current: 120, unit: .minutes, total: 100
        ), 60)
        // A count of one thing is not another.
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .chapters, amount: 3), baseline: 1, current: 2, unit: .issues, total: 12
        ), 1)
    }

    func testAMovedBackValueGivesZero() {
        XCTAssertEqual(TileTodayMath.today(server: nil, baseline: 10, current: 7, unit: .pages, total: 400), 0)
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .pages, amount: 20), baseline: 10, current: 7, unit: .pages, total: 400
        ), 20, "Moving back loses no gain the server already counted")
        XCTAssertEqual(TileTodayMath.today(
            server: TodayPart(unit: .pages, amount: -5), baseline: nil, current: nil, unit: .pages, total: 400
        ), 0)
    }
}
