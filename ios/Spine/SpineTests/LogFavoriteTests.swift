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
        let sameRecordingOnDiscTwo = LogFavorite(kind: .song, title: "First", id: "recording-1", number: "B1")
        draft.toggle(sameRecordingOnDiscTwo)
        XCTAssertEqual(draft.favorite, sameRecordingOnDiscTwo, "A deluxe album's second copy is its own pick")

        draft.isExpanded = true
        draft.toggle(LogFavorite(kind: .song, title: "First (renamed)", id: "recording-1", number: "B1"))
        XCTAssertNil(draft.favorite, "The same song, whatever its title")
        XCTAssertTrue(draft.isExpanded)
    }

    func testAFavoriteEpisodeWaitsBehindASpoilerLogsRevealAndASongDoesnt() {
        let episode = LogFavorite(kind: .episode, title: "Ozymandias", seasonNumber: 5, episodeNumber: 14)
        XCTAssertTrue(episode.isHidden(containsSpoilers: true, revealed: false))
        XCTAssertFalse(episode.isHidden(containsSpoilers: true, revealed: true))
        XCTAssertFalse(episode.isHidden(containsSpoilers: false, revealed: false))
        XCTAssertFalse(Self.song.isHidden(containsSpoilers: true, revealed: false))
    }

    func testOnlyAiredEpisodesAreOfferedAndUnnamedOnesGetAName() {
        func episode(airDate: String?, title: String = "Named") -> EpisodeSummary {
            EpisodeSummary(episodeNumber: 3, title: title, overview: nil, airDate: airDate, runtime: nil, imageUrl: nil, rating: nil)
        }
        XCTAssertTrue(episode(airDate: "2020-01-01").hasAired)
        XCTAssertFalse(episode(airDate: "2999-01-01").hasAired)
        XCTAssertFalse(episode(airDate: nil).hasAired, "To be announced")

        XCTAssertEqual(LogFavorite.episode(episode(airDate: nil, title: ""), seasonNumber: 1).title, "Episode 3")
    }

    /// A Form drops a row that scrolls away, which cancels that row's task. That isn't a failed load, and two rows
    /// asking at once make one request.
    func testALoadSurvivesItsAskerGoingAwayAndRunsOnce() async throws {
        let disc = try Self.disc()
        var songLoads = 0
        let songs = LogFavoriteDraft(loadDiscs: {
            songLoads += 1
            try await Task.sleep(for: .milliseconds(50))
            return [disc]
        })
        let songAsker = Task { await songs.loadSongs() }
        await Task.yield()
        songAsker.cancel()
        await songs.loadSongs()
        XCTAssertFalse(songs.discsFailed)
        XCTAssertEqual(songs.discs.map(\.position), [1])
        XCTAssertEqual(songLoads, 1)

        var seasonLoads = 0
        let seasons = LogFavoriteDraft(loadSeason: { _ in
            seasonLoads += 1
            try await Task.sleep(for: .milliseconds(50))
            return []
        })
        let seasonAsker = Task { await seasons.load(season: 1) }
        await Task.yield()
        seasonAsker.cancel()
        await seasons.load(season: 1)
        XCTAssertTrue(seasons.failedSeasons.isEmpty)
        XCTAssertEqual(seasons.episodes[1]?.count, 0)
        XCTAssertEqual(seasonLoads, 1)
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

        // The list opens on the picked season, else the favorite's, else the first it can show.
        draft.favorite = .episode(pilot, seasonNumber: 2)
        XCTAssertEqual(draft.listedSeason(in: show), 2)
        draft.season = 1
        XCTAssertEqual(draft.listedSeason(in: show), 1)
        draft.season = nil
        XCTAssertEqual(draft.listedSeason(in: [show[0]]), 1, "The favorite's season isn't listed")
        XCTAssertNotNil(draft.favorite, "Working out what to list never changes a saved favorite")

        // Pointing the log at another season drops an episode from elsewhere.
        draft.dropFavorite(outside: show)
        XCTAssertNotNil(draft.favorite)
        draft.dropFavorite(outside: [show[0]])
        XCTAssertNil(draft.favorite)
    }

    func testOnlyAlbumShowAndSeasonLogsOfferAFavorite() throws {
        let disc = try Self.disc()
        XCTAssertEqual(LogFavorite.song(disc.tracks[0]), Self.song)

        let seasons = [LogFavoriteSeason(number: 1, title: "Season 1")]
        let listed = LogFavoriteDraft(discs: [disc])
        XCTAssertEqual(listed.kind(for: TVTrackingSurfacesTests.ref("music"), seasons: []), .song)
        XCTAssertNil(LogFavoriteDraft().kind(for: TVTrackingSurfacesTests.ref("music"), seasons: []),
                     "No songs and nowhere to load them from")
        XCTAssertEqual(listed.kind(for: TVTrackingSurfacesTests.ref("tv"), seasons: seasons), .episode)
        XCTAssertEqual(listed.kind(for: Self.seasonRef(1), seasons: seasons), .episode)
        XCTAssertNil(listed.kind(for: TVTrackingSurfacesTests.ref("episode"), seasons: seasons))
        XCTAssertNil(listed.kind(for: TVTrackingSurfacesTests.ref("movie"), seasons: seasons))
    }

    /// A MusicBrainz album page opens without its songs (they come with its enrichment), and the log page only ever
    /// loads that first payload: the log and Edit Log sheets offer the favorite anyway and load the songs themselves.
    func testAnAlbumThatOpenedWithoutSongsStillOffersAFavoriteAndLoadsThem() async throws {
        let disc = try Self.disc()
        var loads = 0
        let draft = LogFavoriteDraft(loadDiscs: {
            loads += 1
            return [disc]
        })
        XCTAssertEqual(draft.kind(for: TVTrackingSurfacesTests.ref("music"), seasons: []), .song)
        XCTAssertTrue(draft.discs.isEmpty)

        await draft.loadSongs()
        await draft.loadSongs()
        XCTAssertEqual(draft.discs.map(\.position), [1])
        XCTAssertEqual(loads, 1, "Loaded once")

        struct Offline: Error {}
        let failing = LogFavoriteDraft(loadDiscs: { throw Offline() })
        await failing.loadSongs()
        XCTAssertTrue(failing.discsFailed)
        XCTAssertEqual(failing.kind(for: TVTrackingSurfacesTests.ref("music"), seasons: []), .song, "The row stays to retry")
    }

    private static func disc() throws -> MusicMedium {
        try JSONDecoder.api.decode(MusicMedium.self, from: Data("""
        {"medium_mbid": null, "position": 1, "title": null, "format": null, "track_count": 1, "tracks": [{
          "track_mbid": "track-1", "disc_number": 1, "position": 1, "number": "A1", "title": "First", "length_ms": null,
          "artist_credit": [],
          "recording": {"recording_mbid": "recording-1", "title": "First", "length_ms": null, "disambiguation": null,
                        "first_release_date": null, "is_video": false, "isrcs": []}
        }]}
        """.utf8))
    }

    private static func seasonRef(_ number: Int) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1396", seasonNumber: number, episodeNumber: nil)
    }
}
