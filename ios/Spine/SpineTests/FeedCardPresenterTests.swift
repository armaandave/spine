import XCTest
@testable import Spine

/// Feed items decoded from inline JSON the way the API sends them. Shared by the feed card tests.
@MainActor
enum FeedTestItems {
    static func join(_ parts: [String]) -> String {
        parts.filter { !$0.isEmpty }.joined(separator: ", ")
    }

    static func quoted(_ value: String?) -> String {
        value.map { "\"\($0)\"" } ?? "null"
    }

    /// - Parameters:
    ///   - objectFields: more JSON members for `object`, e.g. `"label": "3 episodes"`.
    ///   - mediaFields: more JSON members for `media`, e.g. `"poster_url": "https://p/1.jpg"`.
    ///   - topFields: more JSON members for the item itself, e.g. `"review": "Great"`.
    static func item(
        id: Int = 1,
        type: String = "diary_created",
        objectType: String = "diary",
        objectID: Int = 100,
        objectFields: String = "",
        actorID: Int = 3,
        username: String = "perf-ava",
        avatar: String? = nil,
        source: String = "tmdb",
        mediaType: String = "movie",
        mediaID: String = "1",
        season: Int? = nil,
        episode: Int? = nil,
        title: String = "Perf Movie",
        mediaFields: String = "",
        withMedia: Bool = true,
        topFields: String = "",
        consumedAt: String? = "2026-11-07",
        createdAt: String? = "2026-11-07T16:00:00Z",
        likes: Int = 3,
        comments: Int = 2
    ) throws -> ActivityItem {
        let media = withMedia
            ? """
            {\(join([
                "\"ref\": {\"source\": \"\(source)\", \"media_type\": \"\(mediaType)\", \"media_id\": \"\(mediaID)\", \"item_id\": 7, \"season_number\": \(season.map(String.init) ?? "null"), \"episode_number\": \(episode.map(String.init) ?? "null")}",
                "\"title\": \"\(title)\"",
                mediaFields,
            ]))}
            """
            : "null"
        let json = """
        {\(join([
            "\"id\": \(id)",
            "\"type\": \"\(type)\"",
            "\"created_at\": \(quoted(createdAt))",
            "\"consumed_at\": \(quoted(consumedAt))",
            "\"actor\": {\"id\": \(actorID), \"username\": \"\(username)\", \"display_name\": \"\(username)\", \"avatar_url\": \(quoted(avatar))}",
            "\"media\": \(media)",
            "\"person\": null",
            "\"object\": {\(join(["\"type\": \"\(objectType)\"", "\"id\": \(objectID)", objectFields]))}",
            "\"viewer\": {\"can_view\": true, \"has_liked\": false}",
            "\"like_count\": \(likes)",
            "\"comment_count\": \(comments)",
            topFields,
        ]))}
        """
        return try JSONDecoder.api.decode(ActivityItem.self, from: Data(json.utf8))
    }

    /// A diary log. `rating` is in stars.
    static func log(
        id: Int = 1,
        rating: String? = nil,
        liked: Bool = false,
        rewatch: Bool = false,
        review: String? = nil,
        reviewTitle: String? = nil,
        spoilers: Bool? = nil,
        mediaType: String = "movie",
        source: String = "tmdb",
        season: Int? = nil,
        episode: Int? = nil,
        title: String = "Perf Movie",
        mediaFields: String = "",
        consumedAt: String? = "2026-11-07",
        createdAt: String? = "2026-11-07T16:00:00Z",
        username: String = "perf-ava",
        actorID: Int = 3
    ) throws -> ActivityItem {
        try item(
            id: id,
            objectID: id * 10,
            objectFields: join([
                rating.map { "\"rating\": \"\($0)\"" } ?? "",
                "\"liked\": \(liked)",
                "\"is_rewatch\": \(rewatch)",
            ]),
            actorID: actorID,
            username: username,
            source: source,
            mediaType: mediaType,
            season: season,
            episode: episode,
            title: title,
            mediaFields: mediaFields,
            topFields: join([
                review.map { "\"review\": \"\($0)\"" } ?? "",
                reviewTitle.map { "\"review_title\": \"\($0)\"" } ?? "",
                spoilers.map { "\"contains_spoilers\": \($0)" } ?? "",
            ]),
            consumedAt: consumedAt,
            createdAt: createdAt
        )
    }

    /// A progress day. `label` nil sends none (a status row or an older server).
    static func day(
        id: Int = 1,
        label: String? = nil,
        summary: String? = nil,
        moves: [String]? = nil,
        unit: String? = nil,
        timeLabel: String? = nil,
        mediaType: String = "book",
        source: String = "manual",
        mediaID: String = "1",
        season: Int? = nil,
        episode: Int? = nil,
        title: String = "Perf My Book",
        mediaFields: String = "",
        consumedAt: String? = "2026-11-07",
        createdAt: String? = "2026-11-07T16:00:00Z",
        username: String = "perf-ava",
        actorID: Int = 3
    ) throws -> ActivityItem {
        try item(
            id: id,
            type: "progress_updated",
            objectType: "progress_day",
            objectID: id * 10,
            objectFields: join([
                label.map { "\"label\": \"\($0)\"" } ?? "",
                summary.map { "\"summary\": \"\($0)\"" } ?? "",
                moves.map { "\"moves\": [\($0.map { "\"\($0)\"" }.joined(separator: ", "))]" } ?? "",
                unit.map { "\"unit\": \"\($0)\"" } ?? "",
                timeLabel.map { "\"time_label\": \"\($0)\"" } ?? "",
            ]),
            actorID: actorID,
            username: username,
            source: source,
            mediaType: mediaType,
            mediaID: mediaID,
            season: season,
            episode: episode,
            title: title,
            mediaFields: mediaFields,
            consumedAt: consumedAt,
            createdAt: createdAt
        )
    }

    static func planningRow(
        id: Int = -17_592_186_044_420,
        count: Int? = 6,
        mediaType: String? = "movie",
        faceFields: String = "\"poster_url\": \"https://p/face.jpg\""
    ) throws -> ActivityItem {
        try item(
            id: id,
            type: "planning_added",
            objectType: "planning",
            objectID: abs(id),
            objectFields: join([
                "\"summary\": \"added titles to Planning\"",
                "\"count\": \(count.map(String.init) ?? "null")",
                "\"media_type\": \(quoted(mediaType))",
            ]),
            mediaType: mediaType ?? "movie",
            title: "Face Title",
            mediaFields: faceFields
        )
    }

    static func planningDay(titles: [(String, String?)], count: Int? = nil) throws -> PlanningDay {
        let results = titles.enumerated().map { index, entry in
            """
            {"ref": {"source": "tmdb", "media_type": "movie", "media_id": "\(index + 1)", "item_id": null, \
            "season_number": null, "episode_number": null}, "title": "\(entry.0)"\(entry.1.map { ", \"poster_url\": \"\($0)\"" } ?? "")}
            """
        }
        let json = """
        {"count": \(count.map(String.init) ?? "null"), "results": [\(results.joined(separator: ", "))]}
        """
        return try JSONDecoder.api.decode(PlanningDay.self, from: Data(json.utf8))
    }
}

@MainActor
final class FeedCardPresenterTests: XCTestCase {
    private func model(
        _ item: ActivityItem,
        standing: FeedStanding? = nil,
        newest: Bool = true,
        planning: PlanningDay? = nil
    ) -> FeedCardModel {
        FeedCardPresenter.model(for: item, standing: standing, isNewestOfTitle: newest, planning: planning)
    }

    private let fullStars = ["star.fill", "star.fill", "star.fill", "star.fill"]

    // MARK: Logs

    func testAMovieLogShowsOnlyItsMarks() throws {
        let item = try FeedTestItems.log(
            rating: "4.5", liked: true, rewatch: true, review: "the hills chase alone is worth the ticket",
            title: "One Battle After Another"
        )
        let card = model(item)
        XCTAssertEqual(card.kind, .log)
        XCTAssertEqual(card.username, "perf-ava")
        XCTAssertEqual(card.title, "One Battle After Another")
        XCTAssertNil(card.tag)
        XCTAssertEqual(
            card.headline,
            .marks(starSymbols: fullStars + ["star.leadinghalf.filled"], liked: true, rewatch: true, repeatLabel: "Rewatch")
        )
        // Under a headline the 115 pt card has room for one line.
        XCTAssertEqual(card.details, [.review(text: "the hills chase alone is worth the ticket", lines: 1)])
        XCTAssertNil(card.meter)
        XCTAssertEqual(card.engagement, PostEngagement(hasLiked: false, likeCount: 3, commentCount: 2))
        XCTAssertEqual(card.destination, .diary(10))
        XCTAssertEqual(card.accessibilityHint, "Opens log details")
    }

    func testALogWithNoMarksHasNoHeadlineRow() throws {
        let card = model(try FeedTestItems.log(review: "nothing happens and it is the best thing i have seen"))
        XCTAssertNil(card.headline)
        XCTAssertEqual(card.details.count, 1)
        // With no headline the rows start higher, so a card with only a review still fits.
        XCTAssertLessThanOrEqual(FeedDetailPlanner.bottom(of: card.details, hasHeadline: false), 103)
    }

    func testARewatchAloneIsAHeadline() throws {
        let card = model(try FeedTestItems.log(rewatch: true, mediaType: "book", source: "hardcover"))
        XCTAssertEqual(card.headline, .marks(starSymbols: [], liked: false, rewatch: true, repeatLabel: "Reread"))
    }

    func testASpoilerLogShowsOnlyTheChip() throws {
        let card = model(try FeedTestItems.log(rating: "3.5", liked: true, review: "he was dead the whole time", spoilers: true))
        XCTAssertEqual(card.details, [.spoilers])
        XCTAssertFalse(card.accessibilityLabel.contains("he was dead"), "The label must not give the spoiler away")
        XCTAssertTrue(card.accessibilityLabel.contains("Contains spoilers"))
    }

    func testAReviewTitleStandsInForAMissingReview() throws {
        let card = model(try FeedTestItems.log(reviewTitle: "Underrated", spoilers: false))
        XCTAssertEqual(card.details, [.review(text: "Underrated", lines: 2)])
        let hidden = model(try FeedTestItems.log(reviewTitle: "Underrated", spoilers: true))
        XCTAssertEqual(hidden.details, [.spoilers])
    }

    func testASpoilerFlagWithNoTextShowsNoChip() throws {
        let flagged = model(try FeedTestItems.log(rating: "4", spoilers: true))
        XCTAssertEqual(flagged.details, [])
        let blank = model(try FeedTestItems.log(rating: "4", review: "   ", reviewTitle: "", spoilers: true))
        XCTAssertEqual(blank.details, [])
    }

    func testAnEpisodeLogShowsTheShowTitleWithAnEpisodeCodeAndItsStill() throws {
        let card = model(try FeedTestItems.log(
            rating: "5", mediaType: "episode", season: 2, episode: 7, title: "Chikhai Bardo",
            mediaFields: "\"show_title\": \"Severance\", \"backdrop_url\": \"https://p/still.jpg\", \"poster_accent_color\": \"#102030\""
        ))
        XCTAssertEqual(card.title, "Severance")
        XCTAssertEqual(card.tag, "S2 E7")
        XCTAssertEqual(card.art.posterURL, "https://p/still.jpg")
        XCTAssertEqual(card.art.backgroundURL, "https://p/still.jpg")
        XCTAssertEqual(card.art.accentHex, "#102030")
        XCTAssertFalse(card.art.isSquare)
    }

    func testAnEpisodeWithNoStillFallsBackToItsImage() throws {
        let card = model(try FeedTestItems.log(
            mediaType: "episode", season: 1, episode: 1, mediaFields: "\"image_url\": \"https://p/image.jpg\""
        ))
        XCTAssertEqual(card.art.posterURL, "https://p/image.jpg")
    }

    func testASeasonLogNeverUsesTheTitleThatAppendsTheSeason() throws {
        let season = model(try FeedTestItems.log(
            mediaType: "season", season: 2, title: "Severance", mediaFields: "\"show_title\": \"Severance\""
        ))
        XCTAssertEqual(season.title, "Severance")
        XCTAssertEqual(season.tag, "Season 2")
        let specials = model(try FeedTestItems.log(mediaType: "season", season: 0, title: "Severance"))
        XCTAssertEqual(specials.title, "Severance")
        XCTAssertEqual(specials.tag, "Specials")
    }

    func testAnAnimeEpisodeTagHasNoSeason() throws {
        let card = model(try FeedTestItems.log(
            mediaType: "episode", source: "mal", season: 1, episode: 7, title: "Frieren",
            mediaFields: "\"show_title\": \"Frieren\""
        ))
        XCTAssertEqual(card.tag, "E7")
        XCTAssertEqual(card.title, "Frieren")
    }

    func testAnimeShowsItsEnglishTitle() throws {
        let card = model(try FeedTestItems.log(
            mediaType: "anime", source: "mal", title: "Sousou no Frieren", mediaFields: "\"display_title\": \"Frieren: Beyond Journey's End\""
        ))
        XCTAssertEqual(card.title, "Frieren: Beyond Journey's End")
        XCTAssertNil(card.tag)
    }

    func testAnAlbumIsSquare() throws {
        let album = model(try FeedTestItems.log(rating: "4.5", mediaType: "music", title: "SOS"))
        XCTAssertTrue(album.art.isSquare)
        let squareCover = model(try FeedTestItems.log(mediaFields: "\"poster_orientation\": \"square\""))
        XCTAssertTrue(squareCover.art.isSquare)
        let portrait = model(try FeedTestItems.log(mediaFields: "\"poster_orientation\": \"portrait\""))
        XCTAssertFalse(portrait.art.isSquare)
    }

    func testTheInkGuessComesFromTheAccentColor() throws {
        let light = model(try FeedTestItems.log(mediaFields: "\"poster_accent_color\": \"#F5F0D0\""))
        XCTAssertEqual(light.art.guessInk, .light)
        let dark = model(try FeedTestItems.log(mediaFields: "\"poster_accent_color\": \"#102030\""))
        XCTAssertEqual(dark.art.guessInk, .dark)
        XCTAssertEqual(model(try FeedTestItems.log()).art.guessInk, .dark, "No accent means dark")
    }

    // MARK: Progress days

    func testATVDayShowsItsLabelAndNoRuntimeTail() throws {
        let card = model(try FeedTestItems.day(
            label: "3 episodes", unit: "minutes", timeLabel: "2h 12m", mediaType: "tv", source: "tmdb", title: "Perf My Show"
        ))
        XCTAssertEqual(card.kind, .progress)
        XCTAssertEqual(card.headline, .progress(amount: "+3 episodes", tail: nil, pills: []))
        XCTAssertNil(card.meter)
        XCTAssertFalse(card.accessibilityLabel.contains("2h 12m"), "A runtime reads as a position")
        XCTAssertEqual(card.destination, .progress(10))
        XCTAssertEqual(card.accessibilityHint, "Opens this progress")
    }

    func testBookMangaAndGameDaysTakeTheirLabelsWhateverTheUnit() throws {
        let cases: [(String, String, String?, String)] = [
            ("book", "86 pages", "pages", "+86 pages"),
            ("manga", "4 chapters", "chapters", "+4 chapters"),
            ("game", "2h 30m", "minutes", "+2h 30m"),
            ("game", "15%", "minutes", "+15%"),
        ]
        for (type, label, unit, expected) in cases {
            let card = model(try FeedTestItems.day(label: label, unit: unit, mediaType: type))
            XCTAssertEqual(card.headline, .progress(amount: expected, tail: nil, pills: []), "\(type) \(label)")
        }
    }

    func testPausedWithAnAmountKeepsTheAmountAndAddsAPill() throws {
        let card = model(try FeedTestItems.day(label: "24 episodes", moves: ["paused"], mediaType: "tv"))
        XCTAssertEqual(card.headline, .progress(amount: "+24 episodes", tail: nil, pills: ["Paused"]))
        let several = model(try FeedTestItems.day(label: "86 pages", moves: ["paused", "stopped", "paused"]))
        XCTAssertEqual(several.headline, .progress(amount: "+86 pages", tail: nil, pills: ["Paused", "Stopped"]))
    }

    func testAStatusOnlyDayHasNoAmountAndNothingLit() throws {
        let card = model(try FeedTestItems.day(moves: ["picked_back_up"], mediaType: "tv"))
        XCTAssertEqual(card.kind, .status)
        XCTAssertEqual(card.headline, .status(words: "Picked back up", tail: nil))
        XCTAssertNil(card.meter)
        let stopped = model(try FeedTestItems.day(moves: ["stopped"]))
        XCTAssertEqual(stopped.headline, .status(words: "Stopped", tail: nil))
    }

    func testAStartSaysStartedAndANoteOnPickingBackUpIsDropped() throws {
        let start = model(try FeedTestItems.day(label: "Started"))
        XCTAssertEqual(start.kind, .start)
        XCTAssertEqual(start.headline, .started)
        let both = model(try FeedTestItems.day(label: "Started", moves: ["picked_back_up"]))
        XCTAssertEqual(both.headline, .started)
        XCTAssertNil(both.meter)
    }

    func testAnOldServerDayFallsBackToItsSummary() throws {
        let card = model(try FeedTestItems.day(summary: "watched 4 episodes", mediaType: "tv"))
        XCTAssertEqual(card.headline, .status(words: "Watched 4 episodes", tail: nil))
        let start = model(try FeedTestItems.day(summary: "started reading"))
        XCTAssertEqual(start.headline, .started)
        let nothing = model(try FeedTestItems.day())
        XCTAssertNil(nothing.headline)
    }

    func testUnknownTypesBecomeAnOtherCard() throws {
        let card = model(try FeedTestItems.item(type: "list_created", objectType: "list", objectFields: "\"name\": \"Best of 2026\"", withMedia: false))
        XCTAssertEqual(card.kind, .other)
        XCTAssertEqual(card.headline, .status(words: "Created a list", tail: nil))
        XCTAssertEqual(card.title, "Best of 2026")
        XCTAssertEqual(card.destination, .none)
    }

    // MARK: Standing

    func testAStandingMakesTicksAWindowOrABar() throws {
        let day = try FeedTestItems.day(label: "3 episodes", mediaType: "tv")
        let ticks = model(day, standing: FeedStanding(unit: .episodes, from: 4, to: 7, total: 10, whereText: "7 of 10 episodes"))
        XCTAssertEqual(ticks.meter?.kind, .ticks(count: 10))
        XCTAssertEqual(ticks.meter?.from, 4)
        XCTAssertEqual(ticks.meter?.to, 7)
        XCTAssertEqual(ticks.headline, .progress(amount: "+3 episodes", tail: "7 of 10 episodes", pills: []))

        let long = model(day, standing: FeedStanding(unit: .episodes, from: 1_065, to: 1_089, total: 1_150, whereText: nil))
        guard case .window(_, let span, let seriesTotal)? = long.meter?.kind else {
            return XCTFail("Expected a window, got \(String(describing: long.meter))")
        }
        XCTAssertEqual(span, 20)
        XCTAssertEqual(seriesTotal, 1_150)

        let book = try FeedTestItems.day(label: "86 pages")
        let bar = model(book, standing: FeedStanding(unit: .pages, from: 126, to: 212, total: 448, whereText: "p. 212"))
        XCTAssertEqual(bar.meter?.kind, .bar(total: 448))
        XCTAssertEqual(bar.headline, .progress(amount: "+86 pages", tail: "p. 212", pills: []))
    }

    func testNoMeterWithoutAStandingATotalOrTheNewestCard() throws {
        let book = try FeedTestItems.day(label: "86 pages")
        XCTAssertNil(model(book).meter)
        XCTAssertNil(model(book, standing: FeedStanding(unit: .pages, from: 1, to: 2, total: nil, whereText: "p. 2")).meter)
        let older = model(book, standing: FeedStanding(unit: .pages, from: 1, to: 2, total: 10, whereText: "p. 2"), newest: false)
        XCTAssertNil(older.meter, "Only the newest card of a title gets a meter")
        XCTAssertEqual(older.headline, .progress(amount: "+86 pages", tail: nil, pills: []), "Nor a where")
        let game = try FeedTestItems.day(label: "2h 30m")
        XCTAssertNil(model(game, standing: FeedStanding(unit: .minutes, from: 60, to: 150, total: 600, whereText: nil)).meter)
    }

    func testAStatusRowLightsNothing() throws {
        let day = try FeedTestItems.day(moves: ["paused"], mediaType: "tv")
        let card = model(day, standing: FeedStanding(unit: .episodes, from: 3, to: 5, total: 9, whereText: "after E5"))
        XCTAssertEqual(card.meter?.from, 5)
        XCTAssertEqual(card.meter?.to, 5)
        XCTAssertEqual(card.headline, .status(words: "Paused", tail: "after E5"))
    }

    func testLogsAndPlanningNeverGetAMeter() throws {
        let standing = FeedStanding(unit: .pages, from: 1, to: 2, total: 10, whereText: "p. 2")
        XCTAssertNil(model(try FeedTestItems.log(rating: "4"), standing: standing).meter)
        XCTAssertNil(model(try FeedTestItems.planningRow(), standing: standing).meter)
    }

    func testAMeterUnderAHeadlineLeavesNoDetailRow() throws {
        let day = try FeedTestItems.item(
            type: "progress_updated", objectType: "progress_day",
            objectFields: "\"label\": \"86 pages\"",
            mediaType: "book", topFields: "\"review\": \"a long review that would take two lines on its own, about this book\""
        )
        let card = model(day, standing: FeedStanding(unit: .pages, from: 1, to: 87, total: 400, whereText: nil))
        XCTAssertNotNil(card.meter)
        // The 115 pt card's bottom band holds the meter and the counts; the review stays on the post page.
        XCTAssertEqual(card.details, [])
        XCTAssertLessThanOrEqual(FeedDetailPlanner.bottom(of: card.details, hasHeadline: true), 81)
    }

    // MARK: Planning

    func testAPlanningCardBeforeItsTitlesLoad() throws {
        let card = model(try FeedTestItems.planningRow(count: 6, mediaType: "movie"))
        XCTAssertEqual(card.kind, .planning)
        XCTAssertEqual(card.title, "6 movies")
        XCTAssertEqual(card.tag, "Planning")
        XCTAssertNil(card.headline)
        XCTAssertNil(card.engagement, "Planning has no likes or comments")
        XCTAssertNil(card.meter)
        XCTAssertEqual(card.details, [])
        XCTAssertEqual(card.art.posterURL, "https://p/face.jpg")
        XCTAssertEqual(card.art.planningPosterURLs, [])
        XCTAssertEqual(card.destination, .planning(PlanningRef(id: 17_592_186_044_420, mediaType: "movie")))
        XCTAssertEqual(card.accessibilityHint, "Opens the titles added")
        XCTAssertFalse(card.accessibilityLabel.contains("comment"))
    }

    func testAPlanningCardAfterItsTitlesLoad() throws {
        let day = try FeedTestItems.planningDay(titles: [
            ("Frankenstein", "https://p/1.jpg"), ("Bugonia", "https://p/2.jpg"), ("Marty Supreme", "https://p/3.jpg"),
            ("Hamnet", "https://p/4.jpg"),
        ])
        let card = model(try FeedTestItems.planningRow(count: 4), planning: day)
        XCTAssertEqual(card.art.planningPosterURLs, ["https://p/1.jpg", "https://p/2.jpg", "https://p/3.jpg"])
        XCTAssertEqual(card.art.posterURL, "https://p/1.jpg")
        XCTAssertEqual(card.details, [.quiet(text: "Frankenstein, Bugonia, Marty Supreme and Hamnet", lines: 2)])
        XCTAssertNil(card.headline)
        XCTAssertEqual(
            card.accessibilityLabel,
            "perf-ava, 4 movies, Planning, Frankenstein, Bugonia, Marty Supreme and Hamnet"
        )
    }

    func testPlanningNounsAndNames() throws {
        let nouns: [(String?, Int, String)] = [
            ("movie", 1, "movie"), ("movie", 3, "movies"), ("tv", 2, "shows"), ("book", 1, "book"),
            ("game", 2, "games"), ("music", 2, "albums"), ("music", 1, "album"), ("comic", 5, "comics"),
            ("anime", 5, "anime"), ("manga", 1, "manga"), (nil, 6, "titles"), (nil, 1, "title"),
        ]
        for (type, count, noun) in nouns {
            XCTAssertEqual(FeedCardPresenter.planningNoun(for: type, count: count), noun, "\(type ?? "nil") \(count)")
        }
        let one = try FeedTestItems.planningDay(titles: [("A", nil)])
        XCTAssertEqual(FeedCardPresenter.planningNames(one.results), "A")
        let two = try FeedTestItems.planningDay(titles: [("A", nil), ("B", nil)])
        XCTAssertEqual(FeedCardPresenter.planningNames(two.results), "A and B")
        XCTAssertNil(FeedCardPresenter.planningNames([]))
        XCTAssertEqual(model(try FeedTestItems.planningRow(count: 1, mediaType: "book")).title, "1 book")
        XCTAssertEqual(model(try FeedTestItems.planningRow(count: nil, mediaType: nil)).title, "Titles")
    }

    func testAnAlbumPlanningDayIsSquare() throws {
        XCTAssertTrue(model(try FeedTestItems.planningRow(count: 3, mediaType: "music")).art.isSquare)
    }

    // MARK: Words and equality

    func testNoLogSaysAVerb() throws {
        let logs = [
            try FeedTestItems.log(id: 1, rating: "4.5", liked: true, rewatch: true, review: "so good", title: "Perf Movie"),
            try FeedTestItems.log(id: 2, liked: true, review: "great"),
            try FeedTestItems.log(id: 3, rating: "3", mediaType: "music", title: "Perf Album"),
            try FeedTestItems.log(id: 4, rating: "5", mediaType: "episode", season: 2, episode: 7, mediaFields: "\"show_title\": \"Perf Show\""),
            try FeedTestItems.log(id: 5, mediaType: "season", season: 2, title: "Perf Show"),
            try FeedTestItems.log(id: 6, rewatch: true, mediaType: "book"),
            try FeedTestItems.log(id: 7, rating: "2", mediaType: "game"),
            try FeedTestItems.log(id: 8, spoilers: true),
            try FeedTestItems.log(id: 9),
        ]
        for item in logs {
            let card = model(item)
            var strings = [card.username, card.title, card.accessibilityLabel, card.accessibilityHint]
            strings += [card.tag].compactMap { $0 }
            for row in card.details { strings.append(row.accessibilityText) }
            for word in ["finished", "watched", "listened", "logged"] {
                for string in strings {
                    XCTAssertFalse(string.lowercased().contains(word), "'\(string)' contains \(word) (log \(item.id))")
                }
            }
            if case let .marks(symbols, _, _, repeatLabel)? = card.headline {
                XCTAssertFalse(symbols.contains { $0.lowercased().contains("watched") })
                XCTAssertFalse(repeatLabel.lowercased().contains("watched"))
            }
        }
    }

    func testTheAccessibilityLabelCarriesUserTitleAndCounts() throws {
        let card = model(try FeedTestItems.log(
            rating: "4.5", liked: true, rewatch: true, review: "so good", title: "Perf Movie", username: "perf-ben"
        ))
        XCTAssertEqual(
            card.accessibilityLabel,
            "perf-ben, Perf Movie, Rated 4.5 out of 5 stars, Liked, Rewatch, so good, 3 likes, 2 comments"
        )
        XCTAssertTrue(card.accessibilityLabel.contains("comment"))
        let day = model(try FeedTestItems.day(label: "3 episodes", moves: ["paused"], mediaType: "tv", title: "Perf My Show"))
        XCTAssertEqual(day.accessibilityLabel, "perf-ava, Perf My Show, +3 episodes, Paused, 3 likes, 2 comments")
    }

    func testModelsAreEqualWhenNothingVisibleChangedAndDifferWhenEngagementDoes() throws {
        let item = try FeedTestItems.log(rating: "4", liked: true, review: "good")
        XCTAssertEqual(model(item), model(item))
        var liked = item
        liked.engagement = item.engagement.toggledLike
        XCTAssertNotEqual(model(item), model(liked))
        var commented = item
        commented.engagement = PostEngagement(hasLiked: false, likeCount: 3, commentCount: 9)
        XCTAssertNotEqual(model(item), model(commented))
        // A field nothing draws does not change the model.
        let sameFace = try FeedTestItems.log(id: 1, rating: "4", liked: true, review: "good", createdAt: "2026-11-07T20:00:00Z")
        XCTAssertEqual(model(item), model(sameFace))
    }
}
