import XCTest
@testable import Spine

/// The In progress tile shows the poster of the season a show is on (the up-next episode's season), not the show's.
@MainActor
final class InProgressSeasonPosterTests: XCTestCase {
    // MARK: Fixtures

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private func media(_ id: String, type: String = "tv") -> MediaSummary {
        MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: type, mediaId: "season-poster-\(id)",
                          seasonNumber: nil, episodeNumber: nil),
            title: "Show \(id)",
            posterUrl: "https://example.com/show-\(id).jpg",
            posterOrientation: .portrait,
            posterAccentColor: "#111111",
            userState: nil
        )
    }

    private func item(
        _ id: String,
        type: String = "tv",
        upNext: TVEpisodePointer? = nil,
        palette: [PosterSwatch]? = nil
    ) -> Spine.LibraryItem {
        var shown = media(id, type: type)
        shown.posterPalette = palette
        return Spine.LibraryItem(
            media: shown,
            tracking: TrackingState(
                trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil, startDate: nil,
                endDate: nil, notes: nil, updatedAt: nil,
                tv: TVShowState(status: "In progress", started: true, upNext: upNext)
            )
        )
    }

    // MARK: Decoding

    func testDecodesTheSeasonPosterAndItsAccent() throws {
        let pointer = try decode(TVEpisodePointer.self, """
        {"season_number":2,"episode_number":4,"title":"Fly","is_new":false,
         "season_poster_url":"https://example.com/s2.jpg","season_poster_accent_color":"#aa3300"}
        """)
        XCTAssertEqual(pointer.seasonPosterUrl, "https://example.com/s2.jpg")
        XCTAssertEqual(pointer.seasonPosterAccentColor, "#aa3300")
    }

    func testDecodesWithoutTheKeysFromAnOlderServerAndWithNulls() throws {
        let old = try decode(TVEpisodePointer.self, #"{"season_number":2,"episode_number":4}"#)
        XCTAssertNil(old.seasonPosterUrl)
        XCTAssertNil(old.seasonPosterAccentColor)
        let nulls = try decode(TVEpisodePointer.self, """
        {"season_number":2,"episode_number":4,"season_poster_url":null,"season_poster_accent_color":null}
        """)
        XCTAssertNil(nulls.seasonPosterUrl)
        XCTAssertNil(nulls.seasonPosterAccentColor)
    }

    // MARK: The poster an In progress row shows

    func testTheSeasonPosterWinsAndOnlyThePosterChanges() {
        let show = item("a", upNext: TVEpisodePointer(
            seasonNumber: 2, episodeNumber: 1, seasonPosterUrl: "https://example.com/s2.jpg",
            seasonPosterAccentColor: "#aa3300"
        ))
        let shown = show.inProgressMedia
        XCTAssertEqual(shown.displayPosterURL, "https://example.com/s2.jpg")
        XCTAssertEqual(shown.posterAccentColor, "#aa3300")
        XCTAssertEqual(shown.ref, show.media.ref)
        XCTAssertEqual(shown.title, show.media.title)
        XCTAssertEqual(shown.id, show.media.id)
        XCTAssertEqual(shown.posterOrientation, .portrait)
    }

    func testWithoutASeasonPosterTheShowsOwnIsShown() {
        let plain = item("b", upNext: TVEpisodePointer(seasonNumber: 1, episodeNumber: 1))
        XCTAssertEqual(plain.inProgressMedia, plain.media)
        XCTAssertEqual(plain.inProgressMedia.displayPosterURL, "https://example.com/show-b.jpg")
        let noUpNext = item("c")
        XCTAssertEqual(noUpNext.inProgressMedia, noUpNext.media)
        let empty = item("d", upNext: TVEpisodePointer(seasonNumber: 1, episodeNumber: 1, seasonPosterUrl: ""))
        XCTAssertEqual(empty.inProgressMedia, empty.media)
    }

    func testASeasonPosterWithoutAnAccentNeverUsesTheShowsAccentOrPalette() throws {
        let swatch = try decode(PosterSwatch.self, ##"{"hex":"#112233","share":0.6}"##)
        let show = item("e", upNext: TVEpisodePointer(
            seasonNumber: 2, episodeNumber: 1, seasonPosterUrl: "https://example.com/s2.jpg"
        ), palette: [swatch])
        XCTAssertEqual(show.media.posterAccentColor, "#111111")
        XCTAssertNil(show.inProgressMedia.posterAccentColor)
        XCTAssertNil(show.inProgressMedia.posterPalette)
        XCTAssertNil(InProgressTilePresenter.detail(for: show, today: nil, baseline: nil).accentHex)
    }

    func testThePanelsAccentFollowsThePosterShown() {
        let withAccent = item("f", upNext: TVEpisodePointer(
            seasonNumber: 3, episodeNumber: 2, seasonPosterUrl: "https://example.com/s3.jpg",
            seasonPosterAccentColor: "#00aa55"
        ))
        XCTAssertEqual(InProgressTilePresenter.detail(for: withAccent, today: nil, baseline: nil).accentHex, "#00aa55")
        let showOnly = item("g", upNext: TVEpisodePointer(seasonNumber: 3, episodeNumber: 2))
        XCTAssertEqual(InProgressTilePresenter.detail(for: showOnly, today: nil, baseline: nil).accentHex, "#111111")
    }

    func testOtherTypesKeepTheirPosterEvenIfSomethingCarriesASeasonPoster() {
        let anime = item("h", type: "anime", upNext: TVEpisodePointer(
            seasonNumber: 1, episodeNumber: 1, seasonPosterUrl: "https://example.com/s1.jpg"
        ))
        XCTAssertEqual(anime.inProgressMedia, anime.media)
        let movie = item("i", type: "movie")
        XCTAssertEqual(movie.inProgressMedia, movie.media)
    }

    func testTheTilesPostersUseItButTrendingDoesNot() {
        let model = HomeTilesModel()
        let show = item("j", upNext: TVEpisodePointer(
            seasonNumber: 2, episodeNumber: 1, seasonPosterUrl: "https://example.com/s2.jpg"
        ))
        model.inProgress = [show]
        let poster = model.inProgressPosters[0]
        XCTAssertEqual(poster.id, show.id)
        XCTAssertEqual(poster.media.displayPosterURL, "https://example.com/s2.jpg")
        XCTAssertEqual(poster.media.ref, show.media.ref, "Tapping still opens the show")
        XCTAssertEqual(model.media(for: .inProgress, id: show.id)?.displayPosterURL, "https://example.com/s2.jpg")
        XCTAssertEqual(model.item(id: show.id)?.media.displayPosterURL, "https://example.com/show-j.jpg")

        let trending = TrendingTitle(
            key: FeedEntries.titleKey(for: show.media.ref), media: show.media, people: [], includesYou: false,
            averageRating: nil, lastDay: nil
        )
        model.trending = [trending]
        XCTAssertEqual(model.trendingPosters[0].media.displayPosterURL, "https://example.com/show-j.jpg")
    }

    // MARK: A tick keeps the poster

    func testAWriteAnswerInTheSameSeasonKeepsTheSeasonPoster() {
        let old = TVEpisodePointer(
            seasonNumber: 2, episodeNumber: 3, seasonPosterUrl: "https://example.com/s2.jpg",
            seasonPosterAccentColor: "#aa3300"
        )
        let kept = TVEpisodePointer(seasonNumber: 2, episodeNumber: 4).keepingSeasonPoster(from: old)
        XCTAssertEqual(kept.episodeNumber, 4)
        XCTAssertEqual(kept.seasonPosterUrl, "https://example.com/s2.jpg")
        XCTAssertEqual(kept.seasonPosterAccentColor, "#aa3300")

        let nextSeason = TVEpisodePointer(seasonNumber: 3, episodeNumber: 1).keepingSeasonPoster(from: old)
        XCTAssertEqual(nextSeason.seasonPosterUrl, "https://example.com/s2.jpg", "Kept until the rail reloads")
        XCTAssertEqual(nextSeason.seasonPosterAccentColor, "#aa3300", "The accent stays with its poster")
        XCTAssertNil(TVEpisodePointer(seasonNumber: 2, episodeNumber: 4).keepingSeasonPoster(from: nil).seasonPosterUrl)

        let own = TVEpisodePointer(seasonNumber: 2, episodeNumber: 4, seasonPosterUrl: "https://example.com/new.jpg")
        XCTAssertEqual(own.keepingSeasonPoster(from: old).seasonPosterUrl, "https://example.com/new.jpg")
    }

    func testTheRailKeepsTheSeasonPosterThroughATick() {
        let show = item("k", upNext: TVEpisodePointer(
            seasonNumber: 2, episodeNumber: 3, seasonPosterUrl: "https://example.com/s2.jpg"
        ))
        let answer = TVShowState(status: "In progress", started: true, upNext: TVEpisodePointer(seasonNumber: 2, episodeNumber: 4))
        let ticked = ContinueWatchingTicks.replacingTV(answer, id: show.id, in: [show])
        XCTAssertEqual(ticked[0].tracking.tv?.upNext?.episodeNumber, 4)
        XCTAssertEqual(ticked[0].inProgressMedia.displayPosterURL, "https://example.com/s2.jpg")
    }
}
