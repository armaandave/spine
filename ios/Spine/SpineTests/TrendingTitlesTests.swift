import XCTest
@testable import Spine

/// The Trending tile: what you and the people you follow logged, ranked, with the TMDB list filling in behind.
@MainActor
final class TrendingTitlesTests: XCTestCase {
    private let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0) ?? .gmt
        return calendar
    }()

    private var now: Date {
        calendar.date(from: DateComponents(year: 2026, month: 11, day: 7, hour: 12)) ?? Date()
    }

    private let you = UserSummary(id: 1, username: "claude", displayName: "Claude", avatarUrl: nil)

    // MARK: Fixtures

    private func log(
        _ id: Int,
        _ media: String,
        by actor: Int = 2,
        rating: String? = nil,
        day: String = "2026-11-07",
        poster: Bool = true,
        type: String = "movie",
        source: String = "tmdb",
        season: Int? = nil,
        episode: Int? = nil,
        title: String? = nil,
        mediaFields: String = "",
        objectID: Int? = nil
    ) throws -> ActivityItem {
        try FeedTestItems.item(
            id: id,
            objectID: objectID ?? id * 10,
            objectFields: rating.map { "\"rating\": \"\($0)\"" } ?? "",
            actorID: actor,
            username: "user\(actor)",
            source: source,
            mediaType: type,
            mediaID: media,
            season: season,
            episode: episode,
            title: title ?? "Title \(media)",
            mediaFields: FeedTestItems.join([poster ? "\"poster_url\": \"https://p/\(media).jpg\"" : "", mediaFields]),
            consumedAt: day
        )
    }

    private func progress(
        _ id: Int,
        _ media: String,
        by actor: Int = 2,
        label: String? = "2 episodes",
        moves: String? = nil,
        day: String = "2026-11-07",
        type: String = "tv"
    ) throws -> ActivityItem {
        try FeedTestItems.item(
            id: id,
            type: "progress_updated",
            objectType: "progress_day",
            objectID: id * 10,
            objectFields: FeedTestItems.join([
                label.map { "\"label\": \"\($0)\"" } ?? "",
                moves.map { "\"moves\": [\"\($0)\"]" } ?? "",
            ]),
            actorID: actor,
            username: "user\(actor)",
            mediaType: type,
            mediaID: media,
            title: "Title \(media)",
            mediaFields: "\"poster_url\": \"https://p/\(media).jpg\"",
            consumedAt: day
        )
    }

    private func summary(_ id: String, title: String? = nil, poster: Bool = true) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: id, seasonNumber: nil, episodeNumber: nil),
            title: title ?? "Popular \(id)",
            posterUrl: poster ? "https://p/\(id).jpg" : nil,
            posterOrientation: .portrait
        )
    }

    private func result(
        feed: [ActivityItem],
        own: [ActivityItem] = [],
        you: UserSummary? = nil,
        fallback: [MediaSummary] = []
    ) -> TrendingResult {
        TrendingAggregator.titles(feed: feed, own: own, you: you, fallback: fallback, now: now, calendar: calendar)
    }

    private func ids(_ result: TrendingResult) -> [String] {
        result.titles.map(\.media.ref.mediaId)
    }

    // MARK: Ranking

    func testTwoPersonTitlesComeFirstThenTheNewestThenTheName() throws {
        let feed = [
            try log(1, "solo-old", by: 2, day: "2026-11-02"),
            try log(2, "solo-new", by: 3, day: "2026-11-06"),
            try log(3, "pair", by: 2, day: "2026-11-03"),
            try log(4, "pair", by: 3, day: "2026-11-04"),
            try log(5, "solo-tie-b", by: 4, day: "2026-11-05", title: "Bravo"),
            try log(6, "solo-tie-a", by: 5, day: "2026-11-05", title: "Alpha"),
        ]
        let titles = result(feed: feed)
        XCTAssertEqual(ids(titles), ["pair", "solo-new", "solo-tie-a", "solo-tie-b", "solo-old"])
        XCTAssertEqual(titles.titles[0].people.map(\.id), [3, 2], "Most recent person first")
        XCTAssertEqual(titles.titles[0].lastDay, "2026-11-04")
        XCTAssertEqual(titles.subtitle, "This week")
    }

    func testOnePersonLoggingTwiceIsOnePerson() throws {
        let feed = [
            try log(1, "a", by: 2),
            try progress(2, "a", by: 2, type: "movie"),
            try log(3, "b", by: 2),
            try log(4, "c", by: 2),
        ]
        let titles = result(feed: feed)
        XCTAssertEqual(titles.titles.first { $0.media.ref.mediaId == "a" }?.people.count, 1)
    }

    func testOnlyLogsProgressAndStartsCount() throws {
        let feed = [
            try log(1, "log"),
            try progress(2, "progress"),
            try progress(3, "start", label: "Started"),
            try progress(4, "status-only", label: nil, moves: "paused"),
            try log(5, "no-poster", poster: false),
            try FeedTestItems.planningRow(),
            try FeedTestItems.item(id: 7, type: "diary_deleted", objectID: 70, mediaID: "deleted",
                                   mediaFields: "\"poster_url\": \"https://p/d.jpg\""),
            try FeedTestItems.item(id: 8, type: "rating_updated", objectType: "rating", objectID: 80, mediaID: "rating-only",
                                   mediaFields: "\"poster_url\": \"https://p/r.jpg\""),
            try FeedTestItems.item(id: 9, objectType: "diary", objectID: 90, mediaID: "no-media", withMedia: false),
        ]
        XCTAssertEqual(Set(ids(result(feed: feed))), ["log", "progress", "start"])
    }

    func testALogEditedLaterIsOneRowNotTwo() throws {
        let feed = [
            try log(1, "a", by: 2, objectID: 500),
            try log(2, "a", by: 3, objectID: 500),
            try log(3, "b", by: 2), try log(4, "c", by: 2),
        ]
        XCTAssertEqual(result(feed: feed).titles.first { $0.media.ref.mediaId == "a" }?.people.count, 1)
    }

    func testEpisodesAndSeasonsFoldIntoTheirShow() throws {
        let feed = [
            try log(1, "100", by: 2, type: "episode", season: 1, episode: 3, title: "Pilot",
                    mediaFields: "\"show_title\": \"The Show\""),
            try log(2, "100", by: 3, type: "season", season: 1, title: "Season 1",
                    mediaFields: "\"show_title\": \"The Show\""),
            try progress(3, "100", by: 4, type: "tv"),
            try log(4, "200", by: 2), try log(5, "300", by: 2),
        ]
        let titles = result(feed: feed)
        let show = try XCTUnwrap(titles.titles.first { $0.key == "tmdb:tv:100" })
        XCTAssertEqual(show.people.count, 3)
        XCTAssertEqual(show.media.ref.mediaType, "tv", "The show, not its season or episode")
        XCTAssertNil(show.media.ref.seasonNumber)
        XCTAssertEqual(show.media.title, "Title 100", "The tv row's own title wins over the episode's")
        XCTAssertEqual(titles.titles.first?.key, "tmdb:tv:100")
    }

    func testAnEpisodeAloneNamesItsShow() throws {
        let feed = [
            try log(1, "100", type: "episode", season: 2, episode: 4, title: "Ep",
                    mediaFields: "\"show_title\": \"The Show\""),
        ]
        let show = try XCTUnwrap(result(feed: feed).titles.first)
        XCTAssertEqual(show.media.title, "The Show")
        XCTAssertEqual(show.media.ref.mediaType, "tv")
        XCTAssertEqual(show.media.ref.mediaId, "100")
    }

    func testAMalEpisodeFoldsIntoItsAnime() throws {
        let feed = [try log(1, "5114", type: "episode", source: "mal", season: 1, episode: 8, title: "Ep")]
        let anime = try XCTUnwrap(result(feed: feed).titles.first)
        XCTAssertEqual(anime.media.ref.mediaType, "anime")
        XCTAssertEqual(anime.key, "mal:anime:5114")
    }

    // MARK: Window and subtitle

    func testTheSevenDayWindowDropsOlderTitlesWhenThreeAreInsideIt() throws {
        let feed = [
            try log(1, "today", day: "2026-11-07"),
            try log(2, "six-days", day: "2026-11-01"),
            try log(3, "yesterday", day: "2026-11-06"),
            try log(4, "seven-days", day: "2026-10-31"),
            try log(5, "old", day: "2026-09-01"),
        ]
        let titles = result(feed: feed)
        XCTAssertEqual(Set(ids(titles)), ["today", "six-days", "yesterday"])
        XCTAssertEqual(titles.subtitle, "This week")
    }

    func testFewerThanThreeInTheWindowWidensToEverything() throws {
        let feed = [
            try log(1, "recent-a", day: "2026-11-07"),
            try log(2, "recent-b", day: "2026-11-06"),
            try log(3, "older-a", day: "2026-10-20"),
            try log(4, "older-b", day: "2026-10-10"),
        ]
        let titles = result(feed: feed)
        XCTAssertEqual(ids(titles), ["recent-a", "recent-b", "older-a", "older-b"])
        XCTAssertEqual(titles.subtitle, "Lately")
    }

    func testTooFewTitlesEvenWidenedIsJustPopular() throws {
        let titles = result(feed: [try log(1, "a", day: "2026-10-01"), try log(2, "b", day: "2026-10-02")])
        XCTAssertEqual(titles.subtitle, "Popular")
        XCTAssertEqual(result(feed: []).subtitle, "Popular")
        XCTAssertTrue(result(feed: []).titles.isEmpty)
    }

    func testTheCapIsTen() throws {
        let feed = try (1 ... 14).map { try log($0, "t\($0)", by: 2 + $0) }
        XCTAssertEqual(result(feed: feed).titles.count, 10)
    }

    // MARK: Average

    func testTheAverageNeedsTwoRatings() throws {
        let feed = [
            try log(1, "one-rating", by: 2, rating: "4.5"),
            try log(2, "one-rating", by: 3),
            try log(3, "two-ratings", by: 2, rating: "4"),
            try log(4, "two-ratings", by: 3, rating: "4.5"),
            try log(5, "progress-no-rating", by: 2),
            try progress(6, "one-rating", by: 4, type: "movie"),
        ]
        let titles = result(feed: feed).titles
        XCTAssertNil(titles.first { $0.media.ref.mediaId == "one-rating" }?.averageRating)
        XCTAssertEqual(try XCTUnwrap(titles.first { $0.media.ref.mediaId == "two-ratings" }?.averageRating), 4.25, accuracy: 0.0001)
        XCTAssertNil(titles.first { $0.media.ref.mediaId == "progress-no-rating" }?.averageRating)
    }

    // MARK: You and your friends

    func testYouCountAsAPersonOnTheTitlesYouLogged() throws {
        let feed = [try log(1, "a", by: 2), try log(2, "b", by: 3), try log(3, "c", by: 4)]
        let own = [try log(4, "a", by: 1), try log(5, "mine", by: 1)]
        let titles = result(feed: feed, own: own, you: you)
        let shared = try XCTUnwrap(titles.titles.first { $0.media.ref.mediaId == "a" })
        XCTAssertEqual(shared.people.count, 2)
        XCTAssertTrue(shared.includesYou)
        XCTAssertEqual(shared.people.first?.id, 1, "Your later log is the most recent")
        XCTAssertEqual(titles.titles.first?.media.ref.mediaId, "a")
        let mine = try XCTUnwrap(titles.titles.first { $0.media.ref.mediaId == "mine" })
        XCTAssertEqual(mine.people, [you])
        XCTAssertFalse(try XCTUnwrap(titles.titles.first { $0.media.ref.mediaId == "b" }).includesYou)
    }

    func testARowInBothListsCountsOnce() throws {
        let row = try log(1, "a", by: 1)
        let titles = result(feed: [row, try log(2, "b"), try log(3, "c")], own: [row], you: you)
        XCTAssertEqual(titles.titles.first { $0.media.ref.mediaId == "a" }?.people.count, 1)
    }

    // MARK: Fallback

    func testTheFallbackFillsToFiveWithoutDuplicatesOrPostlessTitles() throws {
        let feed = [try log(1, "p1"), try log(2, "p2")]
        let fallback = [
            summary("p1"), summary("no-poster", poster: false), summary("f1"), summary("f2"), summary("f3"), summary("f4"),
        ]
        let titles = result(feed: feed, fallback: fallback)
        XCTAssertEqual(ids(titles), ["p1", "p2", "f1", "f2", "f3"], "People titles first, then the list in its order")
        XCTAssertEqual(titles.titles.count, 5)
        XCTAssertTrue(titles.titles.suffix(3).allSatisfy { $0.people.isEmpty && $0.lastDay == nil && $0.averageRating == nil })
        XCTAssertEqual(titles.subtitle, "Popular")
    }

    func testAFullListNeedsNoFallback() throws {
        let five = try (1 ... 5).map { try log($0, "t\($0)", by: 2 + $0) }
        XCTAssertFalse(TrendingAggregator.needsFallback(feed: five, own: [], now: now, calendar: calendar))
        XCTAssertTrue(TrendingAggregator.needsFallback(feed: Array(five.prefix(4)), own: [], now: now, calendar: calendar))
        XCTAssertTrue(TrendingAggregator.needsFallback(feed: [], own: [], now: now, calendar: calendar))
        let withFallback = result(feed: five, fallback: [summary("f1")])
        XCTAssertEqual(withFallback.titles.count, 5, "Nothing is added to a full list")
    }

    func testThreePeopleTitlesInTheWindowStillSaysThisWeekWhenFilled() throws {
        let feed = [try log(1, "a"), try log(2, "b"), try log(3, "c")]
        let titles = result(feed: feed, fallback: [summary("f1"), summary("f2"), summary("f3")])
        XCTAssertEqual(titles.titles.count, 5)
        XCTAssertEqual(titles.subtitle, "This week")
    }

    // MARK: The panel

    func testThePanelForAPopularTitle() throws {
        let feed = [
            try log(1, "a", by: 2, rating: "4"), try log(2, "a", by: 3, rating: "4.5"),
            try log(3, "a", by: 4), try log(4, "a", by: 5), try log(5, "b"), try log(6, "c"),
        ]
        let title = try XCTUnwrap(result(feed: feed).titles.first)
        let detail = TrendingTileModel.detail(for: title, planned: false)
        XCTAssertEqual(detail.title, "Title a")
        XCTAssertEqual(detail.averageText, "\u{2605} 4.3")
        XCTAssertEqual(detail.avatars.count, 3, "Three avatars at most")
        XCTAssertEqual(detail.avatars.first?.initial, "U")
        XCTAssertEqual(detail.whoLine, "4 logged it")
        XCTAssertEqual(detail.primaryTitle, "Plan to watch")
        XCTAssertFalse(detail.isPlanned)
        XCTAssertEqual(detail.secondaryTitle, "Open")
        XCTAssertEqual(detail.accessibilityLabel, "Title a, average 4.3 stars, 4 logged it, Plan to watch")
    }

    func testAPlannedTitleSaysPlanned() throws {
        let title = try XCTUnwrap(result(feed: [try log(1, "a")]).titles.first)
        let detail = TrendingTileModel.detail(for: title, planned: true)
        XCTAssertEqual(detail.primaryTitle, "Planned")
        XCTAssertTrue(detail.isPlanned)
        XCTAssertNil(detail.averageText, "One log has no average")
        XCTAssertEqual(detail.whoLine, "1 logged it")
    }

    func testYouAloneAndNobodyRead() throws {
        let mine = try XCTUnwrap(result(feed: [], own: [try log(1, "a", by: 1)], you: you).titles.first)
        XCTAssertEqual(TrendingTileModel.detail(for: mine, planned: false).whoLine, "You logged it")
        let popular = try XCTUnwrap(result(feed: [], fallback: [summary("f1")]).titles.first)
        let detail = TrendingTileModel.detail(for: popular, planned: false)
        XCTAssertEqual(detail.whoLine, "Popular this week")
        XCTAssertTrue(detail.avatars.isEmpty)
        XCTAssertEqual(detail.accessibilityLabel, "Popular f1, Popular this week, Plan to watch")
    }

    func testPlanningWordsFollowTheType() throws {
        let book = TrendingTitle(
            key: "k",
            media: MediaSummary(
                ref: MediaRef(itemId: nil, source: "hardcover", mediaType: "book", mediaId: "1", seasonNumber: nil, episodeNumber: nil),
                title: "A Book", posterUrl: "https://p/b.jpg", posterOrientation: .portrait
            ),
            people: [], includesYou: false, averageRating: nil, lastDay: nil
        )
        XCTAssertEqual(TrendingTileModel.detail(for: book, planned: false).primaryTitle, "Plan to read")
    }
}
