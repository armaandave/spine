import SwiftUI
import XCTest
@testable import Spine

@MainActor
final class MusicContractTests: XCTestCase {
    override func tearDown() {
        MusicContractURLProtocol.handler = nil
        MusicContractURLProtocol.lastRequest = nil
        super.tearDown()
    }

    func testMusicSearchFixtureDecodesMediaSummary() throws {
        let responses = try JSONDecoder.api.decode(
            Phase9Responses.self,
            from: Self.contractData("phase-9-api-responses.example.json")
        )

        let result = try XCTUnwrap(responses.musicSearch.results.first)
        XCTAssertEqual(responses.musicSearch.count, 1)
        XCTAssertEqual(result.title, "Year Zero")
        XCTAssertEqual(result.ref.mediaType, "music")
        XCTAssertEqual(result.ref.source, "musicbrainz")
        XCTAssertEqual(result.posterOrientation, .square)
        XCTAssertEqual(result.posterAspectRatio, 1)
    }

    func testAlbumDetailFixtureDecodesOrderedTracks() throws {
        let detail = try Self.albumDetail()
        let release = try XCTUnwrap(detail.music?.representativeRelease)
        let tracks = release.media.flatMap(\.tracks)

        XCTAssertEqual(detail.title, "Year Zero")
        XCTAssertEqual(release.discCount, 1)
        XCTAssertEqual(tracks.count, 16)
        XCTAssertEqual(tracks.first?.position, 1)
        XCTAssertEqual(tracks.first?.title, "HYPERPOWER!")
        XCTAssertEqual(tracks.first?.recording.recordingMbid, "35518724-a25a-4627-a2cc-0786dd1d2272")
        XCTAssertEqual(tracks.last?.position, 16)
        XCTAssertEqual(tracks.last?.title, "Zero‐Sum")
        XCTAssertEqual(tracks[1].recording.isrcs, [])
    }

    func testAlbumDetailWithoutTracksDecodes() throws {
        var root = try Self.albumObject()
        var music = try XCTUnwrap(root["music"] as? [String: Any])
        music["representative_release"] = NSNull()
        music.removeValue(forKey: "secondary_types")
        music.removeValue(forKey: "artist_credit")
        root["music"] = music

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Self.data(from: root))

        XCTAssertNil(detail.music?.representativeRelease)
        XCTAssertEqual(detail.music?.secondaryTypes, [])
        XCTAssertTrue(detail.music?.artistCredit.isEmpty == true)
    }

    func testMultiDiscTracklistPreservesDiscAndPrintedNumbers() throws {
        var root = try Self.albumObject()
        var music = try XCTUnwrap(root["music"] as? [String: Any])
        var release = try XCTUnwrap(music["representative_release"] as? [String: Any])
        let originalMedia = try XCTUnwrap(release["media"] as? [[String: Any]])
        let originalMedium = try XCTUnwrap(originalMedia.first)
        let originalTracks = try XCTUnwrap(originalMedium["tracks"] as? [[String: Any]])
        let originalTrack = try XCTUnwrap(originalTracks.first)

        var discOne = originalMedium
        var discOneTrack = originalTrack
        discOne["position"] = 1
        discOne["track_count"] = 1
        discOneTrack["disc_number"] = 1
        discOneTrack["position"] = 1
        discOneTrack["number"] = "A1"
        discOne["tracks"] = [discOneTrack]

        var discTwo = originalMedium
        var discTwoTrack = originalTrack
        var discTwoRecording = try XCTUnwrap(discTwoTrack["recording"] as? [String: Any])
        discTwo["position"] = 2
        discTwo["track_count"] = 1
        discTwoTrack["track_mbid"] = "track-disc-two"
        discTwoTrack["disc_number"] = 2
        discTwoTrack["position"] = 1
        discTwoTrack["number"] = "2-01"
        discTwoRecording["recording_mbid"] = "recording-disc-two"
        discTwoTrack["recording"] = discTwoRecording
        discTwo["tracks"] = [discTwoTrack]

        release["disc_count"] = 2
        release["track_count"] = 2
        release["media"] = [discOne, discTwo]
        music["representative_release"] = release
        root["music"] = music

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Self.data(from: root))
        let media = try XCTUnwrap(detail.music?.representativeRelease?.media)

        XCTAssertEqual(media.map(\.position), [1, 2])
        XCTAssertEqual(media[0].tracks[0].discNumber, 1)
        XCTAssertEqual(media[0].tracks[0].number, "A1")
        XCTAssertEqual(media[1].tracks[0].discNumber, 2)
        XCTAssertEqual(media[1].tracks[0].number, "2-01")
    }

    func testAlbumPresentationFormatsMusicFactsAndTrackRows() throws {
        let detail = try Self.albumDetail()
        let music = try XCTUnwrap(detail.music)
        let release = try XCTUnwrap(music.representativeRelease)
        let track = try XCTUnwrap(release.media.first?.tracks.first)

        XCTAssertEqual(MusicAlbumPresentation.artistCreditText(music.artistCredit), "Nine Inch Nails")
        XCTAssertEqual(MusicAlbumPresentation.releaseType(music), "Album")
        XCTAssertEqual(MusicAlbumPresentation.songCount(release), "16 songs")
        XCTAssertEqual(MusicAlbumPresentation.totalLength(release), "64 min")
        XCTAssertEqual(MusicAlbumPresentation.duration(track.lengthMs), "1:41")
        XCTAssertEqual(MusicAlbumPresentation.countryName(release.country), "Worldwide")
        XCTAssertEqual(MusicAlbumPresentation.format(release), "Digital Media")
        XCTAssertNil(MusicAlbumPresentation.differingArtistCredit(track: track, albumCredits: music.artistCredit))
        XCTAssertEqual(
            MusicAlbumPresentation.musicBrainzURL(releaseGroupMbid: music.releaseGroupMbid).absoluteString,
            "https://musicbrainz.org/release-group/3bd76d40-7f0e-36b7-9348-91a33afee20e"
        )
        XCTAssertEqual(
            MusicAlbumPresentation.trackAccessibilityLabel(track: track, albumCredits: music.artistCredit),
            "Disc 1, track 1, HYPERPOWER!, by Nine Inch Nails, 1:41"
        )
    }

    func testAlbumPillsSkipVideosAndNeedEverySongLength() throws {
        let music = try XCTUnwrap(Self.albumObject()["music"] as? [String: Any])
        let release = try XCTUnwrap(music["representative_release"] as? [String: Any])
        let media = try XCTUnwrap(release["media"] as? [[String: Any]])
        var tracks = try XCTUnwrap(media.first?["tracks"] as? [[String: Any]])
        var video = try XCTUnwrap(tracks[0]["recording"] as? [String: Any])
        video["is_video"] = true
        tracks[0]["recording"] = video
        tracks[3]["length_ms"] = NSNull()

        let withRecordingLength = try Self.release(tracks: tracks)
        XCTAssertEqual(MusicAlbumPresentation.songCount(withRecordingLength), "15 songs")
        XCTAssertEqual(MusicAlbumPresentation.totalLength(withRecordingLength), "62 min")

        var recording = try XCTUnwrap(tracks[3]["recording"] as? [String: Any])
        recording["length_ms"] = NSNull()
        tracks[3]["recording"] = recording

        let withoutLength = try Self.release(tracks: tracks)
        XCTAssertNil(MusicAlbumPresentation.totalLength(withoutLength))
        XCTAssertEqual(MusicAlbumPresentation.songCount(withoutLength), "15 songs")
    }

    func testAlbumPresentationShowsDifferentTrackArtist() throws {
        var root = try Self.albumObject()
        var music = try XCTUnwrap(root["music"] as? [String: Any])
        var release = try XCTUnwrap(music["representative_release"] as? [String: Any])
        var media = try XCTUnwrap(release["media"] as? [[String: Any]])
        var tracks = try XCTUnwrap(media[0]["tracks"] as? [[String: Any]])
        tracks[0]["artist_credit"] = [[
            "artist_mbid": "different-artist",
            "name": "Guest Artist",
            "join_phrase": "",
        ]]
        media[0]["tracks"] = tracks
        release["media"] = media
        music["representative_release"] = release
        root["music"] = music

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Self.data(from: root))
        let decodedMusic = try XCTUnwrap(detail.music)
        let track = try XCTUnwrap(decodedMusic.representativeRelease?.media.first?.tracks.first)

        XCTAssertEqual(
            MusicAlbumPresentation.differingArtistCredit(track: track, albumCredits: decodedMusic.artistCredit),
            "Guest Artist"
        )
    }

    func testMusicCreditsProduceMusicBrainzPersonRefs() throws {
        let albumCredits = try JSONDecoder.api.decode(
            [MusicArtistCredit].self,
            from: """
            [
              {"artist_mbid":"artist-1","name":"Artist","join_phrase":""},
              {"artist_mbid":"artist-1","name":"Artist Duplicate","join_phrase":" feat. "},
              {"artist_mbid":"artist-2","name":"Guest","join_phrase":""},
              {"artist_mbid":"  ","name":"Unknown","join_phrase":""}
            ]
            """.data(using: .utf8)!
        )
        let recordingCredits = try JSONDecoder.api.decode(
            [MusicRecordingCredit].self,
            from: """
            [
              {"artist_mbid":"writer-1","name":"Writer","roles":["writer"]},
              {"artist_mbid":null,"name":"Unknown Writer","roles":["composer"]}
            ]
            """.data(using: .utf8)!
        )

        XCTAssertEqual(albumCredits[0].personRef, PersonRef(source: "musicbrainz", id: "artist-1"))
        XCTAssertNil(albumCredits[3].personRef)
        XCTAssertEqual(recordingCredits[0].personRef, PersonRef(source: "musicbrainz", id: "writer-1"))
        XCTAssertNil(recordingCredits[1].personRef)

        let presentation = try XCTUnwrap(MediaCreditPresentation.musicArtists(albumCredits))
        XCTAssertEqual(presentation.label, "Artists")
        XCTAssertEqual(presentation.people.map(\.name), ["Artist", "Guest", "Unknown"])
        XCTAssertEqual(
            presentation.people.compactMap(\.personRef),
            [
                PersonRef(source: "musicbrainz", id: "artist-1"),
                PersonRef(source: "musicbrainz", id: "artist-2"),
            ]
        )
    }

    func testAlbumCreditPresentationUsesMusicBrainzArtists() throws {
        let detail = try Self.albumDetail()

        let presentation = try XCTUnwrap(MediaCreditPresentation.make(for: detail))

        XCTAssertEqual(presentation.label, "Artist")
        XCTAssertEqual(presentation.people.map(\.name), ["Nine Inch Nails"])
        XCTAssertEqual(
            presentation.people.first?.personRef,
            PersonRef(source: "musicbrainz", id: "b7ffd2af-418f-4be2-bdd1-22f8b48613da")
        )
    }

    func testAlbumPresentationAllowsOnlyRecognizedHTTPStreamingLinks() {
        let links = [
            MusicStreamingLink(service: "music.apple.com", url: "https://music.apple.com/album/example"),
            MusicStreamingLink(service: "open.spotify.com", url: "http://open.spotify.com/album/example"),
            MusicStreamingLink(service: "qobuz.com", url: "https://open.qobuz.com/album/example"),
            MusicStreamingLink(service: "unknown.example", url: "https://unknown.example/album/example"),
            MusicStreamingLink(service: "music.apple.com", url: "javascript:alert(1)"),
        ]

        let destinations = MusicAlbumPresentation.streamingDestinations(links)

        XCTAssertEqual(destinations.map(\.label), ["Apple Music", "Spotify", "Qobuz"])
    }

    func testRecordingDetailFixtureDecodesRelationshipsAndContext() throws {
        let detail = try Self.recordingDetail()

        XCTAssertEqual(detail.title, "HYPERPOWER!")
        XCTAssertEqual(detail.rating?.value, 4.5)
        XCTAssertEqual(detail.works.first?.relationshipType, "performance")
        XCTAssertEqual(detail.works.first?.credits.first?.name, "Trent Reznor")
        XCTAssertEqual(detail.works.first?.credits.first?.roles, ["composer"])
        XCTAssertEqual(detail.releases.first?.country, "XW")
        XCTAssertEqual(detail.contextRelease.track.number, "1")
        XCTAssertEqual(detail.parentAlbum.title, "Year Zero")
        XCTAssertEqual(detail.albums.first?.ref, detail.parentAlbum.ref)
        XCTAssertEqual(detail.externalLinks["MusicBrainz"], detail.sourceUrl)
        XCTAssertEqual(detail.capabilities["trackable"], false)
        XCTAssertTrue(detail.alternativeRecordings.isEmpty)
    }

    func testRecordingDetailDefaultsOmittedCollectionsAndNullableFields() throws {
        var root = try Self.recordingObject()
        for key in [
            "artist_credit", "isrcs", "genres", "works", "alternative_recordings",
            "external_links", "albums", "releases", "capabilities",
        ] {
            root.removeValue(forKey: key)
        }
        for key in [
            "length_ms", "disambiguation", "first_release_date", "rating", "annotation",
            "source_url", "image_url",
        ] {
            root[key] = NSNull()
        }

        let detail = try JSONDecoder.api.decode(MusicRecordingDetail.self, from: Self.data(from: root))

        XCTAssertTrue(detail.artistCredit.isEmpty)
        XCTAssertEqual(detail.isrcs, [])
        XCTAssertEqual(detail.genres, [])
        XCTAssertTrue(detail.works.isEmpty)
        XCTAssertTrue(detail.alternativeRecordings.isEmpty)
        XCTAssertEqual(detail.externalLinks, [:])
        XCTAssertTrue(detail.albums.isEmpty)
        XCTAssertTrue(detail.releases.isEmpty)
        XCTAssertEqual(detail.capabilities, [:])
        XCTAssertNil(detail.lengthMs)
        XCTAssertNil(detail.rating)
        XCTAssertNil(detail.annotation)
        XCTAssertNil(detail.sourceUrl)
        XCTAssertNil(detail.imageUrl)
    }

    func testAlbumTrackSelectionUsesExactRecordingAndStableTrackIdentity() throws {
        let detail = try Self.albumDetail()
        let tracks = try XCTUnwrap(detail.music?.representativeRelease).media.flatMap(\.tracks)
        let track = try XCTUnwrap(tracks.first)
        let selection = MusicSongSelection(album: detail.ref, track: track, artworkURL: detail.displayPosterURL)

        XCTAssertEqual(track.id, track.trackMbid)
        XCTAssertEqual(selection.album, detail.ref)
        XCTAssertEqual(selection.recordingMbid, track.recording.recordingMbid)
        XCTAssertEqual(selection.artworkURL, detail.displayPosterURL)
        XCTAssertEqual(selection.id, "\(detail.ref.id):\(track.recording.recordingMbid)")
        XCTAssertEqual(Set(tracks.map(\.id)).count, tracks.count)
    }

    func testStudioAndLiveRecordingsWithSameTitleDoNotCollide() throws {
        var root = try Self.albumObject()
        var music = try XCTUnwrap(root["music"] as? [String: Any])
        var release = try XCTUnwrap(music["representative_release"] as? [String: Any])
        var media = try XCTUnwrap(release["media"] as? [[String: Any]])
        var tracks = try XCTUnwrap(media[0]["tracks"] as? [[String: Any]])
        tracks[0]["title"] = "The Song"
        tracks[1]["title"] = "The Song"
        var studio = try XCTUnwrap(tracks[0]["recording"] as? [String: Any])
        var live = try XCTUnwrap(tracks[1]["recording"] as? [String: Any])
        studio["title"] = "The Song"
        studio["recording_mbid"] = "studio-recording"
        live["title"] = "The Song"
        live["recording_mbid"] = "live-recording"
        tracks[0]["recording"] = studio
        tracks[1]["recording"] = live
        media[0]["tracks"] = tracks
        release["media"] = media
        music["representative_release"] = release
        root["music"] = music
        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Self.data(from: root))
        let decodedTracks = try XCTUnwrap(detail.music?.representativeRelease?.media[0].tracks)

        let studioSelection = MusicSongSelection(album: detail.ref, track: decodedTracks[0], artworkURL: nil)
        let liveSelection = MusicSongSelection(album: detail.ref, track: decodedTracks[1], artworkURL: nil)

        XCTAssertEqual(decodedTracks[0].title, decodedTracks[1].title)
        XCTAssertNotEqual(decodedTracks[0].id, decodedTracks[1].id)
        XCTAssertNotEqual(studioSelection.recordingMbid, liveSelection.recordingMbid)
        XCTAssertNotEqual(studioSelection.id, liveSelection.id)
    }

    func testSongDetailHandlesMissingWorksArtworkAndMultipleAppearances() throws {
        var root = try Self.recordingObject()
        root["works"] = []
        root["image_url"] = NSNull()
        var parent = try XCTUnwrap(root["parent_album"] as? [String: Any])
        parent["image_url"] = NSNull()
        parent["poster_url"] = NSNull()
        root["parent_album"] = parent
        var secondAlbum = parent
        var secondRef = try XCTUnwrap(secondAlbum["ref"] as? [String: Any])
        secondRef["media_id"] = "second-album"
        secondAlbum["ref"] = secondRef
        secondAlbum["title"] = "Second Album"
        root["albums"] = [parent, secondAlbum]
        var releases = try XCTUnwrap(root["releases"] as? [[String: Any]])
        var secondRelease = try XCTUnwrap(releases.first)
        secondRelease["release_mbid"] = "second-release"
        secondRelease["title"] = "Second Edition"
        releases.append(secondRelease)
        root["releases"] = releases

        let detail = try JSONDecoder.api.decode(MusicRecordingDetail.self, from: Self.data(from: root))

        XCTAssertTrue(detail.works.isEmpty)
        XCTAssertNil(detail.imageUrl)
        XCTAssertNil(detail.parentAlbum.displayPosterURL)
        XCTAssertEqual(detail.albums.map(\.title), ["Year Zero", "Second Album"])
        XCTAssertEqual(detail.releases.map(\.releaseMbid), ["2d0bad69-f735-484b-bc0b-2ea54c76225e", "second-release"])
    }

    func testSongPresentationFormatsCredits() throws {
        let detail = try Self.recordingDetail()
        let credit = try XCTUnwrap(detail.works.first?.credits.first)

        XCTAssertEqual(MusicSongPresentation.albumContext(detail), "Track 1 on Year Zero")
        XCTAssertEqual(MusicSongPresentation.credit(credit), "Trent Reznor · Composer")
    }

    func testSongDetailFailedFetchCanRetry() async throws {
        let detail = try Self.recordingDetail()
        let repository = SequencedMusicRepository(results: [
            .failure(APIError.httpStatus(503, nil)),
            .success(detail),
        ])
        let selection = try Self.songSelection()
        let viewModel = SongDetailViewModel(
            selection: selection,
            musicRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.load()
        XCTAssertNil(viewModel.detail)
        XCTAssertNotNil(viewModel.errorMessage)

        await viewModel.load()
        XCTAssertEqual(viewModel.detail?.recordingMbid, selection.recordingMbid)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertEqual(repository.requests.map(\.recordingMbid), [selection.recordingMbid, selection.recordingMbid])
    }

    func testSongDetailCancellationDoesNotSurfaceAsError() async throws {
        let repository = SequencedMusicRepository(results: [.failure(CancellationError())])
        let viewModel = SongDetailViewModel(
            selection: try Self.songSelection(),
            musicRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.load()

        XCTAssertNil(viewModel.detail)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    func testUnknownFutureFieldsAreIgnoredAtEveryMusicLevel() throws {
        var root = try Self.albumObject()
        root["future_response"] = ["version": 2]
        var music = try XCTUnwrap(root["music"] as? [String: Any])
        music["future_music"] = true
        var release = try XCTUnwrap(music["representative_release"] as? [String: Any])
        var media = try XCTUnwrap(release["media"] as? [[String: Any]])
        var tracks = try XCTUnwrap(media[0]["tracks"] as? [[String: Any]])
        tracks[0]["future_track"] = "ignored"
        var recording = try XCTUnwrap(tracks[0]["recording"] as? [String: Any])
        recording["future_recording"] = 42
        tracks[0]["recording"] = recording
        media[0]["tracks"] = tracks
        release["media"] = media
        music["representative_release"] = release
        root["music"] = music

        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Self.data(from: root))

        XCTAssertEqual(detail.music?.representativeRelease?.media[0].tracks[0].title, "HYPERPOWER!")
    }

    func testAPIRepositoryRequestsExactRecordingPathAndDecodesDetail() async throws {
        let responseData = try Self.contractData("song-detail.example.json")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MusicContractURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        MusicContractURLProtocol.handler = { request in
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, responseData)
        }
        let album = MediaRef(
            itemId: nil,
            source: "musicbrainz",
            mediaType: "music",
            mediaId: "3bd76d40-7f0e-36b7-9348-91a33afee20e",
            seasonNumber: nil,
            episodeNumber: nil
        )
        let repository = APIMusicRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            session: session
        ))

        let detail = try await repository.recordingDetail(
            album: album,
            recordingMbid: "35518724-a25a-4627-a2cc-0786dd1d2272"
        )

        XCTAssertEqual(MusicContractURLProtocol.lastRequest?.httpMethod, "GET")
        XCTAssertEqual(
            MusicContractURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/media/musicbrainz/music/3bd76d40-7f0e-36b7-9348-91a33afee20e/recordings/35518724-a25a-4627-a2cc-0786dd1d2272/"
        )
        XCTAssertEqual(detail.recordingMbid, "35518724-a25a-4627-a2cc-0786dd1d2272")
    }

    func testMusicFallbackThemeAndHumanLabels() {
        let theme = MediaTypeTheme.theme(for: "music")
        let teal = Color(red: 0.20, green: 0.78, blue: 0.74)
        let ref = MediaRef(
            itemId: nil,
            source: "musicbrainz",
            mediaType: "music",
            mediaId: "album",
            seasonNumber: nil,
            episodeNumber: nil
        )

        XCTAssertEqual(Array(APIConstants.fallbackMediaTypes.suffix(3)), ["book", "comic", "music"])
        XCTAssertEqual(theme.displayName, "Music")
        XCTAssertEqual(theme.symbolName, "music.note.list")
        XCTAssertEqual(theme.artworkOrientation, .square)
        XCTAssertEqual(theme.accentColor, teal)
        XCTAssertEqual(theme.statsColor, teal)
        XCTAssertEqual(ref.trackingStatusLabel("In progress"), "Listening")
        XCTAssertEqual(ref.trackingStatusLabel("Completed"), "Listened")
        XCTAssertEqual(ref.repeatLabel, "Relisten")
        XCTAssertEqual(ref.consumedDateLabel, "Date listened")
    }

    func testSuccessfulMetaResponseOverridesFallbackMediaTypes() async throws {
        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: Data("""
        {
          "version": "1",
          "media_types": ["movie", "music"],
          "sources": {},
          "status_choices": [],
          "source_choices": []
        }
        """.utf8))
        let viewModel = SearchViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            onUnauthorized: {}
        )

        XCTAssertEqual(viewModel.mediaTypes, APIConstants.fallbackMediaTypes)
        await viewModel.loadMeta()
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "music"])
    }

    func testMetaResponseHidesMusicWhenServerDisablesIt() async throws {
        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: Data("""
        {
          "version": "1",
          "media_types": ["movie", "book"],
          "sources": {},
          "status_choices": [],
          "source_choices": []
        }
        """.utf8))
        let viewModel = SearchViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            onUnauthorized: {}
        )

        await viewModel.loadMeta()

        XCTAssertEqual(viewModel.mediaTypes, ["movie", "book"])
        XCTAssertFalse(viewModel.mediaTypes.contains("music"))
    }

    func testSearchUsesAuthenticatedEnabledMediaTypesWhenAvailable() async throws {
        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: Data("""
        {
          "version": "1",
          "media_types": ["movie", "book", "music"],
          "enabled_media_types": ["book"],
          "sources": {},
          "status_choices": [],
          "source_choices": []
        }
        """.utf8))
        let viewModel = SearchViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            onUnauthorized: {}
        )

        await viewModel.loadMeta()

        XCTAssertEqual(viewModel.mediaTypes, ["book"])
    }

    func testEmptyAuthenticatedEnabledMediaTypesDoNotRestoreFallbackTypes() async throws {
        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: Data("""
        {
          "version": "1",
          "media_types": ["movie", "book"],
          "enabled_media_types": [],
          "sources": {},
          "status_choices": [],
          "source_choices": []
        }
        """.utf8))
        let viewModel = SearchViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            onUnauthorized: {}
        )

        await viewModel.loadMeta()

        XCTAssertTrue(viewModel.mediaTypes.isEmpty)
    }

    func testAllSearchScopesPutAllBeforeEnabledPrimaryTypes() {
        XCTAssertEqual(
            SearchViewModel.allSearchScopes(from: ["movie", "season", "book", "episode"]),
            [APIConstants.allMedia, "movie", "book"]
        )
    }

    func testAllSearchWarningFormatsUnavailableMediaNames() {
        XCTAssertEqual(
            MediaSearchWarning.message(for: ["book", "music"]),
            "Books and Music couldn’t be searched. Other results are shown."
        )
    }

    func testMusicLensPersistsAndUnknownLensRecovers() {
        let defaults = UserDefaults(suiteName: "MusicLensPhase11Tests")!
        defer { defaults.removePersistentDomain(forName: "MusicLensPhase11Tests") }

        defaults.set("music", forKey: MediaLensStore.persistenceKey)
        let musicStore = MediaLensStore(defaults: defaults)
        XCTAssertEqual(musicStore.validateSelection(in: ["movie", "music"]), "music")
        XCTAssertEqual(defaults.string(forKey: MediaLensStore.persistenceKey), "music")

        XCTAssertEqual(musicStore.validateSelection(in: ["movie", "book"]), "movie")
        XCTAssertEqual(defaults.string(forKey: MediaLensStore.persistenceKey), "movie")

        defaults.set("future-media", forKey: MediaLensStore.persistenceKey)
        let recoveredStore = MediaLensStore(defaults: defaults)
        XCTAssertEqual(recoveredStore.validateSelection(in: ["movie", "music"]), "movie")
        XCTAssertEqual(defaults.string(forKey: MediaLensStore.persistenceKey), "movie")
    }

    func testAllMediaThemeUsesGlobeWithoutBecomingAStoredMediaType() {
        let defaults = UserDefaults(suiteName: "AllMediaLensTests")!
        defer { defaults.removePersistentDomain(forName: "AllMediaLensTests") }
        defaults.removePersistentDomain(forName: "AllMediaLensTests")
        let store = MediaLensStore(defaults: defaults)

        let theme = MediaTypeTheme.theme(for: APIConstants.allMedia)
        _ = store.theme(for: APIConstants.allMedia)

        XCTAssertEqual(theme.displayName, "All Media")
        XCTAssertEqual(theme.symbolName, "globe")
        XCTAssertEqual(store.selectedMediaType, "movie")
        XCTAssertNil(defaults.string(forKey: MediaLensStore.persistenceKey))

        store.setMediaType("book")
        XCTAssertEqual(defaults.string(forKey: MediaLensStore.persistenceKey), "book")
    }

    func testMusicSearchUsesBasicThenEnrichedDetailPaths() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MusicContractURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let repository = APIMediaRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            session: session
        ))

        MusicContractURLProtocol.handler = { request in
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, try Self.responseData(
                key: "music_search",
                fileName: "phase-9-api-responses.example.json"
            ))
        }

        let results = try await repository.search(query: "year zero", mediaType: "music")
        let searchURL = try XCTUnwrap(MusicContractURLProtocol.lastRequest?.url)
        let searchComponents = try XCTUnwrap(URLComponents(url: searchURL, resolvingAgainstBaseURL: false))
        let searchQuery = Dictionary(uniqueKeysWithValues: (searchComponents.queryItems ?? []).map { ($0.name, $0.value) })

        XCTAssertEqual(searchComponents.path, "/api/v1/media/search/")
        XCTAssertEqual(searchQuery["q"]!, "year zero")
        XCTAssertEqual(searchQuery["media_type"]!, "music")
        XCTAssertEqual(results.first?.ref.mediaType, "music")

        MusicContractURLProtocol.handler = { request in
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, try Self.contractData("album-detail.example.json"))
        }

        let detail = try await repository.detail(ref: try XCTUnwrap(results.first?.ref))

        XCTAssertEqual(
            MusicContractURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/media/musicbrainz/music/3bd76d40-7f0e-36b7-9348-91a33afee20e/basic/"
        )
        XCTAssertEqual(detail.ref.mediaType, "music")

        let enriched = try await repository.enrichedMusicDetail(ref: detail.ref)

        XCTAssertEqual(
            MusicContractURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/media/musicbrainz/music/3bd76d40-7f0e-36b7-9348-91a33afee20e/enrichment/"
        )
        XCTAssertNotNil(enriched.music?.representativeRelease)
    }

    func testAllMediaSearchUsesScopeAndDecodesUnavailableTypes() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MusicContractURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let repository = APIMediaRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            session: session
        ))

        MusicContractURLProtocol.handler = { request in
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, Data("""
            {
              "count": 0,
              "next": null,
              "previous": null,
              "results": [],
              "unavailable_media_types": ["book", "music"]
            }
            """.utf8))
        }

        let response = try await repository.searchAll(query: "deathly hallows")
        let url = try XCTUnwrap(MusicContractURLProtocol.lastRequest?.url)
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })

        XCTAssertEqual(components.path, "/api/v1/media/search/")
        XCTAssertEqual(query["q"]!, "deathly hallows")
        XCTAssertEqual(query["scope"]!, APIConstants.allMedia)
        XCTAssertEqual(MusicContractURLProtocol.lastRequest?.timeoutInterval, 12)
        XCTAssertEqual(response.unavailableMediaTypes, ["book", "music"])
    }

    func testAllMediaSearchPublishesPartialFailureTypes() async {
        let viewModel = SearchViewModel(mediaRepository: AllSearchMediaRepository(), onUnauthorized: {})

        await viewModel.search("dune", mediaType: APIConstants.allMedia)

        XCTAssertEqual(viewModel.results.map(\.title), ["Dune"])
        XCTAssertEqual(viewModel.unavailableMediaTypes, ["music"])
        XCTAssertNil(viewModel.errorMessage)
    }

    func testMusicArtworkUsesSquareLayoutAndFallback() {
        XCTAssertEqual(
            PosterSlot.searchRow.artworkSize(mediaType: "music", orientation: .square),
            CGSize(width: 54, height: 54)
        )
        XCTAssertEqual(
            PosterSlot.libraryRow.artworkSize(mediaType: "music", orientation: .unknown),
            CGSize(width: 56, height: 56)
        )
        XCTAssertEqual(
            PosterSlot.libraryRow.artworkSize(mediaType: "music", orientation: nil),
            CGSize(width: 56, height: 56)
        )
        XCTAssertEqual(
            PosterSlot.libraryRow.artworkSize(mediaType: "movie", orientation: nil),
            CGSize(width: 56, height: 84)
        )
    }

    func testMusicSearchSubtitleDoesNotDuplicateReleaseDate() {
        let music = MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "musicbrainz",
                mediaType: "music",
                mediaId: "album",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Year Zero",
            subtitle: "Nine Inch Nails · 2007 · Album",
            releaseDate: "2007-04-13"
        )
        let movie = MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "tmdb",
                mediaType: "movie",
                mediaId: "movie",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Movie",
            subtitle: "Drama",
            releaseDate: "2007-04-13"
        )

        XCTAssertEqual(music.searchResultSubtitle, "Nine Inch Nails · 2007 · Album")
        XCTAssertEqual(movie.searchResultSubtitle, "Drama · April 13, 2007")
    }

    func testProviderErrorEnvelopeSurfacesMessageAndRequestIDWithoutExposingUnknownServerBodies() {
        let provider = APIError.httpStatus(
            503,
            #"{"error":{"code":"provider_unavailable","message":"There was an error contacting the MusicBrainz API.","fields":null,"request_id":"phase16"}}"#
        )
        let invalidLogin = APIError.httpStatus(
            400,
            #"{"error":{"code":"validation_error","message":"One or more fields are invalid.","fields":{"non_field_errors":["Invalid username/email or password."]},"request_id":null}}"#
        )
        let unknown = APIError.httpStatus(500, #"{"debug":"internal stack detail"}"#)

        XCTAssertEqual(
            provider.localizedDescription,
            "There was an error contacting the MusicBrainz API. Request ID: phase16"
        )
        XCTAssertEqual(invalidLogin.localizedDescription, "Invalid username/email or password.")
        XCTAssertFalse(unknown.localizedDescription.contains("internal stack detail"))
        XCTAssertTrue(unknown.localizedDescription.contains("HTTP 500"))
    }

    func testRecentMediaFiltersOnceForSupportedTypes() throws {
        let music = try Self.responseData(
            key: "music_search",
            fileName: "phase-9-api-responses.example.json"
        )
        let response = try JSONDecoder.api.decode(PagedResponse<MediaSummary>.self, from: music)
        let album = try XCTUnwrap(response.results.first)
        let movie = MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "tmdb",
                mediaType: "movie",
                mediaId: "1",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Movie"
        )
        let data = try JSONEncoder().encode([album, movie])
        let encoded = try XCTUnwrap(String(data: data, encoding: .utf8))

        XCTAssertEqual(
            RecentMedia.decodeList(from: encoded, supportedMediaTypes: ["music"]).map(\.ref.mediaType),
            ["music"]
        )
    }

    func testChangingSearchCancelsOldRequestWithoutSurfacingAnError() async throws {
        let repository = DelayedSearchMediaRepository()
        let viewModel = SearchViewModel(mediaRepository: repository, onUnauthorized: {})

        let oldSearch = Task { await viewModel.search("slow", mediaType: "music") }
        try await Task.sleep(for: .milliseconds(10))
        oldSearch.cancel()
        await viewModel.search("new", mediaType: "music")
        await oldSearch.value

        XCTAssertEqual(viewModel.query, "new")
        XCTAssertEqual(viewModel.results.map(\.title), ["new"])
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    func testChangingAllSearchIgnoresOldResponse() async throws {
        let repository = DelayedSearchMediaRepository()
        let viewModel = SearchViewModel(mediaRepository: repository, onUnauthorized: {})

        let oldSearch = Task { await viewModel.search("slow", mediaType: APIConstants.allMedia) }
        try await Task.sleep(for: .milliseconds(10))
        await viewModel.search("new", mediaType: APIConstants.allMedia)
        await oldSearch.value

        XCTAssertEqual(viewModel.query, "new")
        XCTAssertEqual(viewModel.results.map(\.title), ["new"])
        XCTAssertTrue(viewModel.unavailableMediaTypes.isEmpty)
    }

    func testChangingDiaryAllSearchIgnoresOldResultsAndWarnings() async throws {
        let viewModel = DiaryCreateViewModel(
            diaryRepository: UnusedDiaryRepository(),
            mediaRepository: DelayedSearchMediaRepository(),
            onUnauthorized: {}
        )
        viewModel.query = "slow"
        let oldSearch = Task { await viewModel.search() }
        try await Task.sleep(for: .milliseconds(10))

        viewModel.query = "new"
        await viewModel.search()
        await oldSearch.value

        XCTAssertEqual(viewModel.results.map(\.title), ["new"])
        XCTAssertTrue(viewModel.unavailableMediaTypes.isEmpty)
        XCTAssertFalse(viewModel.isSearching)
    }

    func testDiaryPreferenceUpdateRecoversDisabledScopeToAll() {
        let viewModel = DiaryCreateViewModel(
            diaryRepository: UnusedDiaryRepository(),
            mediaRepository: DelayedSearchMediaRepository(),
            onUnauthorized: {}
        )
        viewModel.mediaType = "book"

        viewModel.setEnabledMediaTypes(["movie", "season", "episode"])

        XCTAssertEqual(viewModel.mediaTypes, ["movie"])
        XCTAssertEqual(viewModel.mediaType, APIConstants.allMedia)
    }

    // MARK: Apple Music catalog

    func testAppleAlbumFixtureDecodesTheWholePage() throws {
        let detail = try Self.appleAlbumDetail()
        let music = try XCTUnwrap(detail.music)
        let release = try XCTUnwrap(music.representativeRelease)
        let tracks = release.media.flatMap(\.tracks)

        XCTAssertEqual(detail.ref.source, "apple_music")
        XCTAssertEqual(detail.ref.mediaId, "1657869377")
        XCTAssertEqual(detail.title, "SOS")
        XCTAssertEqual(music.contentRating, "explicit")
        XCTAssertEqual(music.coverArt.source, "apple_music")
        XCTAssertEqual(release.selectionBasis, "apple_music")
        XCTAssertEqual(tracks.count, 23)
        XCTAssertEqual(tracks[0].number, "1")
        XCTAssertEqual(tracks[0].contentRating, "explicit")
        XCTAssertNil(tracks[1].contentRating)
        XCTAssertEqual(tracks[1].title, "Kill Bill")
        XCTAssertEqual(tracks[0].artistCredit.first?.artistSource, "apple_music")
        XCTAssertEqual(release.streamingLinks.map(\.kind), ["album", "search"])
        XCTAssertEqual(MusicAlbumPresentation.songCount(release), "23 songs")
    }

    func testAppleAlbumRelatedSectionsKeepTheServersTitlesAndBadges() throws {
        let sections = try XCTUnwrap(Self.appleAlbumDetail().relatedSections)

        XCTAssertEqual(sections.map(\.id), ["other_versions", "more_by_artist", "related_albums"])
        XCTAssertEqual(sections.map(\.title), ["Other Versions", "More by SZA", "You Might Also Like"])
        XCTAssertEqual(sections.map(\.items.count), [1, 2, 10])
        let deluxe = try XCTUnwrap(sections[0].items.first)
        XCTAssertEqual(deluxe.title, "SOS Deluxe: LANA")
        XCTAssertEqual(deluxe.relation, "Deluxe")
        XCTAssertEqual(deluxe.contentRating, "explicit")
        XCTAssertEqual(deluxe.ref.source, "apple_music")
        XCTAssertNil(sections[1].items[0].relation)
    }

    func testAppleSongFixtureDecodesWithAppleArtistAndAlbum() throws {
        let detail = try JSONDecoder.api.decode(
            MusicRecordingDetail.self,
            from: Self.contractData("apple-song-detail.example.json")
        )

        XCTAssertEqual(detail.title, "Kill Bill")
        XCTAssertEqual(detail.artistCredit.first?.personRef, PersonRef(source: "apple_music", id: "605800394"))
        XCTAssertEqual(detail.parentAlbum.ref.source, "apple_music")
        XCTAssertEqual(detail.parentAlbum.ref.mediaId, "1657869377")
        XCTAssertEqual(detail.contextRelease.track.number, "2")
        XCTAssertNil(detail.contextRelease.track.contentRating)
        XCTAssertEqual(detail.externalLinks["Apple Music"], detail.sourceUrl)
        XCTAssertTrue(detail.works.isEmpty)
        XCTAssertEqual(MusicSongPresentation.albumContext(detail), "Track 2 on SOS")
    }

    func testMusicBrainzFixturesLeaveTheAppleFieldsEmpty() throws {
        let album = try Self.albumDetail()
        let music = try XCTUnwrap(album.music)
        let release = try XCTUnwrap(music.representativeRelease)
        let song = try Self.recordingDetail()

        XCTAssertNil(music.contentRating)
        XCTAssertTrue(release.media.flatMap(\.tracks).allSatisfy { $0.contentRating == nil })
        XCTAssertEqual(release.streamingLinks.map(\.kind), [nil])
        XCTAssertNil(music.artistCredit.first?.artistSource)
        XCTAssertEqual(song.artistCredit.first?.personRef?.source, "musicbrainz")
        XCTAssertEqual(song.works.first?.credits.first?.personRef?.source, "musicbrainz")
    }

    func testMusicCreditsOpenTheArtistPageOfTheirOwnSource() throws {
        let albumCredits = try JSONDecoder.api.decode(
            [MusicArtistCredit].self,
            from: Data("""
            [
              {"artist_mbid":null,"name":"SZA","join_phrase":"","artist_source":"apple_music","artist_id":"605800394"},
              {"artist_mbid":"artist-1","name":"Artist","join_phrase":""},
              {"artist_mbid":"artist-2","name":"Half","join_phrase":"","artist_source":"apple_music","artist_id":" "},
              {"artist_mbid":null,"name":"Unknown","join_phrase":"","artist_source":null,"artist_id":"605800394"}
            ]
            """.utf8)
        )
        let recordingCredit = try JSONDecoder.api.decode(
            MusicRecordingCredit.self,
            from: Data(#"{"artist_mbid":null,"name":"SZA","roles":["composer"],"artist_source":"apple_music","artist_id":"605800394"}"#.utf8)
        )

        XCTAssertEqual(albumCredits.map(\.personRef), [
            PersonRef(source: "apple_music", id: "605800394"),
            PersonRef(source: "musicbrainz", id: "artist-1"),
            PersonRef(source: "musicbrainz", id: "artist-2"),
            nil,
        ])
        XCTAssertEqual(recordingCredit.personRef, PersonRef(source: "apple_music", id: "605800394"))
        XCTAssertEqual(PersonRef(source: "apple_music", id: "605800394").sourceDisplayName, "Apple Music")

        let presentation = try XCTUnwrap(MediaCreditPresentation.make(for: Self.appleAlbumDetail()))
        XCTAssertEqual(presentation.people.map(\.name), ["SZA"])
        XCTAssertEqual(presentation.people.first?.personRef, PersonRef(source: "apple_music", id: "605800394"))
    }

    func testListeningButtonsPutAppleMusicFirstAndShowSpotifyOnlyForTheAlbumItself() throws {
        let links = [
            MusicStreamingLink(service: "open.spotify.com", url: "https://open.spotify.com/search/SZA%20SOS/albums", kind: "search"),
            MusicStreamingLink(service: "open.spotify.com", url: "https://open.spotify.com/album/abc", kind: "album"),
            MusicStreamingLink(service: "music.apple.com", url: "https://music.apple.com/us/album/sos/1", kind: "album"),
            MusicStreamingLink(service: "qobuz.com", url: "https://open.qobuz.com/album/example"),
        ]
        let destinations = MusicAlbumPresentation.listeningDestinations(links)
        XCTAssertEqual(destinations.map(\.label), ["Apple Music", "Spotify"])
        XCTAssertEqual(destinations[1].url.absoluteString, "https://open.spotify.com/album/abc", "The album, never the search")

        let search = MusicAlbumPresentation.listeningDestinations([
            MusicStreamingLink(service: "open.spotify.com", url: "https://open.spotify.com/search/SZA%20SOS/albums", kind: "search"),
        ])
        XCTAssertTrue(search.isEmpty, "A Spotify search link gets no button.")

        let musicBrainz = MusicAlbumPresentation.listeningDestinations([
            MusicStreamingLink(service: "open.spotify.com", url: "https://open.spotify.com/album/abc"),
        ])
        XCTAssertEqual(musicBrainz.map(\.label), ["Spotify"], "A link without a kind opens the album.")

        let apple = try XCTUnwrap(Self.appleAlbumDetail().music?.representativeRelease?.streamingLinks)
        XCTAssertTrue(apple.contains { $0.kind == "search" }, "The Apple album's Spotify link is a search")
        XCTAssertEqual(MusicAlbumPresentation.listeningDestinations(apple).map(\.label), ["Apple Music"])
        let year = try XCTUnwrap(Self.albumDetail().music?.representativeRelease?.streamingLinks)
        XCTAssertEqual(MusicAlbumPresentation.listeningDestinations(year).map(\.label), ["Apple Music"])
    }

    func testExplicitBadgeShowsOnlyForExplicitContent() throws {
        XCTAssertTrue(MusicContentRating.isExplicit("explicit"))
        XCTAssertTrue(MusicContentRating.isExplicit("Explicit"))
        XCTAssertFalse(MusicContentRating.isExplicit("clean"))
        XCTAssertFalse(MusicContentRating.isExplicit(""))
        XCTAssertFalse(MusicContentRating.isExplicit(nil))

        let detail = try Self.appleAlbumDetail()
        let music = try XCTUnwrap(detail.music)
        let tracks = try XCTUnwrap(music.representativeRelease).media.flatMap(\.tracks)
        XCTAssertEqual(
            MusicAlbumPresentation.trackAccessibilityLabel(track: tracks[0], albumCredits: music.artistCredit),
            "Disc 1, track 1, SOS, Explicit, by SZA, 1:57"
        )
        XCTAssertEqual(
            MusicAlbumPresentation.trackAccessibilityLabel(track: tracks[1], albumCredits: music.artistCredit),
            "Disc 1, track 2, Kill Bill, by SZA, 2:33"
        )

        let summary = try JSONDecoder.api.decode(MediaSummary.self, from: Data("""
        {"ref":{"item_id":null,"source":"apple_music","media_type":"music","media_id":"1","season_number":null,
         "episode_number":null},"title":"SOS","subtitle":"SZA · 2022 · Album","content_rating":"explicit"}
        """.utf8))
        XCTAssertTrue(MusicContentRating.isExplicit(summary.contentRating))
        let recent = try JSONDecoder().decode(MediaSummary.self, from: JSONEncoder().encode(summary))
        XCTAssertEqual(recent.contentRating, "explicit", "Recent searches keep the rating.")
    }

    func testAlbumCreditNamesItsSource() throws {
        XCTAssertEqual(MusicAlbumPresentation.providerAttribution(try Self.appleAlbumDetail()), "Data from Apple Music")
        XCTAssertEqual(
            MusicAlbumPresentation.providerAttribution(try Self.albumDetail()),
            "Metadata by MusicBrainz · Cover art by Cover Art Archive"
        )
    }

    func testAppleAlbumOpensWithOneGenericDetailRequest() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MusicContractURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let repository = APIMediaRepository(client: APIClient(
            baseURL: URL(string: "https://example.com")!,
            session: session
        ))
        MusicContractURLProtocol.handler = { request in
            let response = try XCTUnwrap(HTTPURLResponse(
                url: try XCTUnwrap(request.url),
                statusCode: 200,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            ))
            return (response, try Self.contractData("apple-album-detail.example.json"))
        }
        let ref = MediaRef(
            itemId: nil,
            source: "apple_music",
            mediaType: "music",
            mediaId: "1657869377",
            seasonNumber: nil,
            episodeNumber: nil
        )

        let detail = try await repository.detail(ref: ref)

        XCTAssertEqual(
            MusicContractURLProtocol.lastRequest?.url?.absoluteString,
            "https://example.com/api/v1/media/apple_music/music/1657869377/"
        )
        XCTAssertEqual(detail.music?.representativeRelease?.trackCount, 23)
    }

    func testAppleMusicButtonOpensTheListenersStorefront() throws {
        // A MusicBrainz release's links, in the server's order: sorted by URL, so /gb/ comes first.
        let links = [
            MusicStreamingLink(service: "music.apple.com", url: "https://music.apple.com/gb/album/1794644675"),
            MusicStreamingLink(service: "music.apple.com", url: "https://music.apple.com/us/album/1794644675"),
            MusicStreamingLink(service: "open.spotify.com", url: "https://open.spotify.com/album/abc"),
        ]
        func appleURL(_ links: [MusicStreamingLink], region: String?) -> String? {
            MusicAlbumPresentation.listeningDestinations(links, region: region).first?.url.absoluteString
        }

        XCTAssertEqual(appleURL(links, region: "US"), "https://music.apple.com/us/album/1794644675")
        XCTAssertEqual(appleURL(links, region: "GB"), "https://music.apple.com/gb/album/1794644675")
        XCTAssertEqual(appleURL(links, region: "DE"), "https://music.apple.com/us/album/1794644675", "Else the US store")
        XCTAssertEqual(appleURL(links, region: nil), "https://music.apple.com/us/album/1794644675")
        XCTAssertEqual(
            MusicAlbumPresentation.listeningDestinations(links, region: "GB").map(\.label),
            ["Apple Music", "Spotify"]
        )

        let anyStore = [links[0], MusicStreamingLink(service: "music.apple.com", url: "https://music.apple.com/album/year-zero/1440766015")]
        XCTAssertEqual(
            appleURL(anyStore, region: "DE"),
            "https://music.apple.com/album/year-zero/1440766015",
            "A link without a storefront opens the listener's own."
        )
        XCTAssertEqual(appleURL(Array(links.prefix(1)), region: "US"), "https://music.apple.com/gb/album/1794644675")

        let apple = try XCTUnwrap(Self.appleAlbumDetail().music?.representativeRelease?.streamingLinks)
        XCTAssertEqual(appleURL(apple, region: "GB"), "https://music.apple.com/us/album/sos/1657869377")
    }

    func testRelatedCardsAreAsTallAsTheirArtworkAndReadTheirRelation() throws {
        let sections = try XCTUnwrap(Self.appleAlbumDetail().relatedSections)
        XCTAssertEqual(sections.map { RelatedCardLayout.height(for: $0.items) }, [140, 140, 140], "Square covers")

        let movie = MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "550", seasonNumber: nil, episodeNumber: nil),
            title: "Fight Club"
        )
        XCTAssertEqual(RelatedCardLayout.height(for: [movie]), 190, "2:3 posters keep their cards")
        XCTAssertEqual(RelatedCardLayout.height(for: sections[0].items + [movie]), 190)

        XCTAssertEqual(RelatedCardLayout.accessibilityLabel(sections[0].items[0]), "Open SOS Deluxe: LANA, Deluxe")
        XCTAssertEqual(RelatedCardLayout.accessibilityLabel(sections[1].items[0]), "Open \(sections[1].items[0].displayTitle)")

        XCTAssertNil(RelatedCardLayout.badge(sections[0].items[0]), "Album cards show no Deluxe badge")
        let sequel = MediaSummary(
            ref: MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "5", seasonNumber: nil, episodeNumber: nil),
            title: "Shingeki no Kyojin: The Final Season",
            relation: "Sequel"
        )
        XCTAssertEqual(RelatedCardLayout.badge(sequel), "Sequel", "Anime and manga cards keep theirs")
    }

    func testExplicitSongPageShowsTheE() throws {
        var root = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Self.contractData("apple-song-detail.example.json")) as? [String: Any]
        )
        func song() throws -> MusicRecordingDetail {
            try JSONDecoder.api.decode(MusicRecordingDetail.self, from: Self.data(from: root))
        }
        XCTAssertFalse(MusicSongPresentation.isExplicit(try song()))

        root["content_rating"] = "explicit"
        XCTAssertEqual(try song().contentRating, "explicit")
        XCTAssertTrue(MusicSongPresentation.isExplicit(try song()))

        root["content_rating"] = nil
        var context = try XCTUnwrap(root["context_release"] as? [String: Any])
        var track = try XCTUnwrap(context["track"] as? [String: Any])
        track["content_rating"] = "explicit"
        context["track"] = track
        root["context_release"] = context
        XCTAssertTrue(MusicSongPresentation.isExplicit(try song()), "Else its track's rating, as the tracklist showed")

        XCTAssertFalse(MusicSongPresentation.isExplicit(try Self.recordingDetail()))
    }

    private static func appleAlbumDetail() throws -> MediaDetail {
        try JSONDecoder.api.decode(MediaDetail.self, from: contractData("apple-album-detail.example.json"))
    }

    private static func albumDetail() throws -> MediaDetail {
        try JSONDecoder.api.decode(MediaDetail.self, from: contractData("album-detail.example.json"))
    }

    private static func recordingDetail() throws -> MusicRecordingDetail {
        try JSONDecoder.api.decode(
            MusicRecordingDetail.self,
            from: contractData("song-detail.example.json")
        )
    }

    private static func songSelection() throws -> MusicSongSelection {
        let detail = try albumDetail()
        let track = try XCTUnwrap(detail.music?.representativeRelease?.media.first?.tracks.first)
        return MusicSongSelection(album: detail.ref, track: track, artworkURL: detail.displayPosterURL)
    }

    private static func albumObject() throws -> [String: Any] {
        try XCTUnwrap(
            JSONSerialization.jsonObject(with: contractData("album-detail.example.json")) as? [String: Any]
        )
    }

    /// The album fixture's release with its first disc's tracks replaced.
    private static func release(tracks: [[String: Any]]) throws -> MusicRepresentativeRelease {
        var root = try albumObject()
        var music = try XCTUnwrap(root["music"] as? [String: Any])
        var release = try XCTUnwrap(music["representative_release"] as? [String: Any])
        var media = try XCTUnwrap(release["media"] as? [[String: Any]])
        media[0]["tracks"] = tracks
        release["media"] = media
        music["representative_release"] = release
        root["music"] = music
        let detail = try JSONDecoder.api.decode(MediaDetail.self, from: data(from: root))
        return try XCTUnwrap(detail.music?.representativeRelease)
    }

    private static func recordingObject() throws -> [String: Any] {
        try XCTUnwrap(
            JSONSerialization.jsonObject(with: contractData("song-detail.example.json")) as? [String: Any]
        )
    }

    private static func data(from object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }

    private static func responseData(key: String, fileName: String) throws -> Data {
        let root = try XCTUnwrap(
            JSONSerialization.jsonObject(with: contractData(fileName)) as? [String: Any]
        )
        return try JSONSerialization.data(withJSONObject: try XCTUnwrap(root[key]), options: [.sortedKeys])
    }

    private static func contractData(_ fileName: String) throws -> Data {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try Data(contentsOf: repositoryRoot
            .appendingPathComponent("docs/music/contracts", isDirectory: true)
            .appendingPathComponent(fileName))
    }
}

@MainActor
private final class SequencedMusicRepository: MusicRepository {
    struct Request {
        let album: MediaRef
        let recordingMbid: String
    }

    private var results: [Result<MusicRecordingDetail, Error>]
    private(set) var requests: [Request] = []

    init(results: [Result<MusicRecordingDetail, Error>]) {
        self.results = results
    }

    func recordingDetail(album: MediaRef, recordingMbid: String) async throws -> MusicRecordingDetail {
        requests.append(Request(album: album, recordingMbid: recordingMbid))
        return try results.removeFirst().get()
    }
}

private struct Phase9Responses: Decodable {
    let musicSearch: PagedResponse<MediaSummary>
}

struct MetaOnlyMediaRepository: MediaRepository {
    let meta: MetaResponse

    func meta() async throws -> MetaResponse { meta }
    func search(query _: String, mediaType _: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref _: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref _: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref _: MediaRef, posterURL _: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref _: MediaRef, backdropURL _: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

private struct AllSearchMediaRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query _: String, mediaType _: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func searchAll(query _: String) async throws -> MediaSearchResponse {
        MediaSearchResponse(
            results: [MediaSummary(
                ref: MediaRef(
                    itemId: nil,
                    source: "tmdb",
                    mediaType: "movie",
                    mediaId: "dune",
                    seasonNumber: nil,
                    episodeNumber: nil
                ),
                title: "Dune"
            )],
            unavailableMediaTypes: ["music"]
        )
    }
    func detail(ref _: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref _: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref _: MediaRef, posterURL _: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref _: MediaRef, backdropURL _: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

@MainActor
private struct DelayedSearchMediaRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }

    func search(query: String, mediaType: String) async throws -> [MediaSummary] {
        try await Task.sleep(for: .milliseconds(query == "slow" ? 100 : 1))
        return [MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: "musicbrainz",
                mediaType: mediaType,
                mediaId: query,
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: query
        )]
    }

    func searchAll(query: String) async throws -> MediaSearchResponse {
        MediaSearchResponse(
            results: try await search(query: query, mediaType: APIConstants.allMedia),
            unavailableMediaTypes: query == "slow" ? ["music"] : []
        )
    }

    func detail(ref _: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref _: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref _: MediaRef, posterURL _: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref _: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref _: MediaRef, backdropURL _: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref _: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref _: MediaRef, logoURL _: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

private struct UnusedDiaryRepository: DiaryRepository {
    func list(tag _: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id _: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId _: Int, liked _: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query _: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

private final class MusicContractURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    nonisolated(unsafe) static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequest = request
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: APIError.invalidResponse)
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
