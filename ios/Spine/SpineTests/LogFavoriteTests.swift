import XCTest
@testable import Spine

/// A log's favorite song or episode: its wire shape, what a save sends, and picking it.
@MainActor
final class LogFavoriteTests: XCTestCase {
    private static let song = LogFavorite(kind: .song, title: "First", id: "recording-1", number: "A1")

    func testAFavoriteDecodesFromALogAndEncodesOnCreate() throws {
        let entry = try TVTrackingSurfacesTests.entry(mediaType: "tv", title: "Breaking Bad")
        XCTAssertNil(entry.favorite, "Logs without one, and older servers, read nil")

        let body = """
        {"kind": "episode", "season_number": 1, "episode_number": 4, "title": "Cancer Man"}
        """
        let episode = try JSONDecoder.api.decode(LogFavorite.self, from: Data(body.utf8))
        XCTAssertEqual(episode, LogFavorite(kind: .episode, title: "Cancer Man", seasonNumber: 1, episodeNumber: 4))
        XCTAssertEqual(episode.numberLabel, "S1 E4")
        XCTAssertEqual(Self.song.numberLabel, "A1")

        var request = DiaryEntryWriteRequest(
            ref: TVTrackingSurfacesTests.ref("music"), consumedAt: nil, rating: nil, review: "", reviewTitle: "",
            liked: false, isRewatch: false, autoMarkConsumed: true, containsSpoilers: false, visibility: "public", tags: []
        )
        XCTAssertNil(try TVTrackingSurfacesTests.object(request)["favorite"])
        request.favorite = Self.song
        let favorite = try XCTUnwrap(try TVTrackingSurfacesTests.object(request)["favorite"] as? [String: Any])
        XCTAssertEqual(favorite["kind"] as? String, "song")
        XCTAssertEqual(favorite["id"] as? String, "recording-1")
        XCTAssertEqual(favorite["number"] as? String, "A1")
        XCTAssertEqual(favorite["title"] as? String, "First")
    }

    func testAnEditSendsTheFavoriteOnlyWhenItChanges() throws {
        var request = DiaryEntryUpdateRequest(
            consumedAt: nil, rating: nil, review: nil, reviewTitle: nil, tags: nil, liked: nil, isRewatch: nil,
            containsSpoilers: nil, visibility: nil
        )
        XCTAssertNil(try TVTrackingSurfacesTests.object(request)["favorite"])

        request.includesFavorite = true
        XCTAssertTrue(try TVTrackingSurfacesTests.object(request)["favorite"] is NSNull, "A cleared favorite is sent as null")

        // An edit whose seasons can't be listed shows no favorite row, so it leaves the stored one alone.
        let entry = try TVTrackingSurfacesTests.entry(mediaType: "season", title: "Breaking Bad", season: 1)
        let edit = DiaryLogEditViewModel(entry: entry, diaryRepository: SurfacesDiaryRepository())
        XCTAssertNil(edit.favoriteKind)
        edit.favoriteDraft.favorite = Self.song
        XCTAssertNil(try TVTrackingSurfacesTests.object(try edit.request())["favorite"])
    }

    func testPickingFoldsTheListAwayAndPickingAgainClears() {
        let draft = LogFavoriteDraft()
        draft.isExpanded = true
        draft.toggle(Self.song)
        XCTAssertEqual(draft.favorite, Self.song)
        XCTAssertFalse(draft.isExpanded)

        draft.isExpanded = true
        draft.toggle(LogFavorite(kind: .song, title: "First (renamed)", id: "recording-1"))
        XCTAssertNil(draft.favorite, "The same song, whatever its title")
        XCTAssertTrue(draft.isExpanded)
    }

    func testFavoriteEpisodesFollowTheLoggedSeasons() {
        let pilot = EpisodeSummary(episodeNumber: 1, title: "Pilot", overview: nil, airDate: nil, runtime: nil,
                                   imageUrl: nil, rating: nil)
        let draft = LogFavoriteDraft(episodes: [2: [pilot]])
        let show = [LogFavoriteSeason(number: 1, title: "Season 1"), LogFavoriteSeason(number: 2, title: "Season 2")]

        // Without a repository, only seasons whose episodes it already has are offered.
        XCTAssertEqual(draft.seasons(for: TVTrackingSurfacesTests.ref("tv"), showSeasons: show).map(\.number), [2])
        XCTAssertEqual(draft.seasons(for: Self.seasonRef(2), showSeasons: show).map(\.number), [2])
        XCTAssertEqual(draft.seasons(for: Self.seasonRef(1), showSeasons: show), [])
        XCTAssertEqual(draft.seasons(for: TVTrackingSurfacesTests.ref("episode"), showSeasons: show), [])

        let loading = LogFavoriteDraft(loadSeason: { _ in [] })
        XCTAssertEqual(loading.seasons(for: TVTrackingSurfacesTests.ref("tv"), showSeasons: show).map(\.number), [1, 2])

        // Switching the log from the whole show to another season drops an episode from elsewhere.
        draft.favorite = .episode(pilot, seasonNumber: 2)
        draft.limit(to: [1, 2])
        XCTAssertEqual(draft.season, 2, "The list opens on the favorite's season")
        draft.limit(to: [1])
        XCTAssertNil(draft.favorite)
        XCTAssertEqual(draft.season, 1)
    }

    func testOnlyAlbumShowAndSeasonLogsOfferAFavorite() throws {
        let disc = try JSONDecoder.api.decode(MusicMedium.self, from: Data("""
        {"medium_mbid": null, "position": 1, "title": null, "format": null, "track_count": 1, "tracks": [{
          "track_mbid": "track-1", "disc_number": 1, "position": 1, "number": "A1", "title": "First", "length_ms": null,
          "artist_credit": [],
          "recording": {"recording_mbid": "recording-1", "title": "First", "length_ms": null, "disambiguation": null,
                        "first_release_date": null, "is_video": false, "isrcs": []}
        }]}
        """.utf8))
        XCTAssertEqual(LogFavorite.song(disc.tracks[0]), Self.song)

        let seasons = [LogFavoriteSeason(number: 1, title: "Season 1")]
        XCTAssertEqual(LogFavorite.Kind.of(TVTrackingSurfacesTests.ref("music"), discs: [disc], seasons: []), .song)
        XCTAssertNil(LogFavorite.Kind.of(TVTrackingSurfacesTests.ref("music"), discs: [], seasons: []), "No tracklist")
        XCTAssertEqual(LogFavorite.Kind.of(TVTrackingSurfacesTests.ref("tv"), discs: [], seasons: seasons), .episode)
        XCTAssertEqual(LogFavorite.Kind.of(Self.seasonRef(1), discs: [], seasons: seasons), .episode)
        XCTAssertNil(LogFavorite.Kind.of(TVTrackingSurfacesTests.ref("episode"), discs: [], seasons: seasons))
        XCTAssertNil(LogFavorite.Kind.of(TVTrackingSurfacesTests.ref("movie"), discs: [disc], seasons: seasons))
    }

    private static func seasonRef(_ number: Int) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1396", seasonNumber: number, episodeNumber: nil)
    }
}
