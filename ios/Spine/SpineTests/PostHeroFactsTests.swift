import XCTest
@testable import Spine

@MainActor
final class PostHeroFactsTests: XCTestCase {
    // MARK: The year line

    func testTheYearLineSaysTheYearOnceThenTheLength() {
        let movie = detail("movie", releaseDate: "2025-04-18", ["runtime": .string("2h 18m")])
        let lines = PostHeroFacts.lines(displayTitle: "Sinners (2025)", detail: movie)
        XCTAssertEqual(lines.lead, "2025")
        XCTAssertEqual(lines.length, .runtime("2h 18m"))
        XCTAssertEqual(PostHeroFacts.lines(displayTitle: "Sinners", detail: movie).lead, "2025")
        XCTAssertEqual(PostHeroFacts.lines(displayTitle: "Sinners (2025)", detail: nil), PostHeroFacts.Lines(lead: "2025"))
        XCTAssertEqual(PostHeroFacts.lines(displayTitle: "A Feast for Crows", detail: nil), PostHeroFacts.Lines())
    }

    func testADaysEpisodeTagTakesTheYearsPlaceAndDropsTheLength() {
        let show = detail("tv", ["seasons": .number(5), "creators": people("Vince Gilligan")])
        let lines = PostHeroFacts.lines(displayTitle: "Breaking Bad", detail: show, tag: "S2 E7")
        XCTAssertEqual(lines.lead, "S2 E7")
        XCTAssertNil(lines.length)
        XCTAssertEqual(lines.maker, PostHeroFacts.Maker(role: .created, name: "Vince Gilligan", more: 0))
    }

    // MARK: Who made it

    func testEachKindNamesWhoMadeIt() {
        func maker(_ type: String, _ details: [String: JSONValue]) -> PostHeroFacts.Maker? {
            PostHeroFacts.maker(for: detail(type, details))
        }
        XCTAssertEqual(maker("movie", ["directors": people("Celine Song")]), .init(role: .directed, name: "Celine Song", more: 0))
        XCTAssertEqual(maker("movie", ["director": .string("Celine Song")]), .init(role: .directed, name: "Celine Song", more: 0))
        XCTAssertEqual(maker("season", ["creators": people("Vince Gilligan")]), .init(role: .created, name: "Vince Gilligan", more: 0))
        XCTAssertEqual(maker("anime", ["studios": .array([.string("Madhouse")])]), .init(role: .animated, name: "Madhouse", more: 0))
        XCTAssertEqual(maker("manga", ["authors": .array([.string("Katsuhiro Otomo")])]), .init(role: .written, name: "Katsuhiro Otomo", more: 0))
        XCTAssertEqual(maker("book", ["authors": people("Sally Rooney")]), .init(role: .written, name: "Sally Rooney", more: 0))
        XCTAssertEqual(maker("comic", ["publisher": .string("DC Comics")]), .init(role: .published, name: "DC Comics", more: 0))
        // Games name nobody yet.
        XCTAssertNil(maker("game", ["companies": .array([.string("Valve")])]))
        XCTAssertNil(maker("movie", [:]))
        XCTAssertNil(PostHeroFacts.maker(for: nil))
    }

    func testTwoMakersAreBothNamedAndMoreAreCounted() {
        XCTAssertEqual(
            PostHeroFacts.maker(for: detail("manga", ["authors": .array([.string("Kanehito Yamada"), .string("Tsukasa Abe")])])),
            .init(role: .written, name: "Kanehito Yamada & Tsukasa Abe", more: 0)
        )
        XCTAssertEqual(
            PostHeroFacts.maker(for: detail("movie", ["directors": people("Joaquim Dos Santos", "Kemp Powers", " ", "Justin K. Thompson", "Kemp Powers")])),
            .init(role: .directed, name: "Joaquim Dos Santos", more: 2)
        )
    }

    func testMusicCreditsItsArtistsAsOneNameAndSaysWhatTheReleaseIs() throws {
        let duo = #"[{"name":"Future","join_phrase":" & "},{"name":"Metro Boomin","join_phrase":""}]"#
        XCTAssertEqual(
            PostHeroFacts.maker(for: try album(artists: duo)),
            .init(role: .album, name: "Future & Metro Boomin", more: 0)
        )
        XCTAssertEqual(PostHeroFacts.maker(for: try album(primaryType: "EP"))?.role, .ep)
        XCTAssertEqual(PostHeroFacts.maker(for: try album(primaryType: "Single"))?.role, .single)
    }

    // MARK: How long it is

    func testAShowGivesItsSeasonsOrItsEpisodesWhenItHasOnlyOne() {
        XCTAssertEqual(length("tv", ["seasons": .number(5), "episodes": .number(62), "status": .string("Ended")]), .count(5, .seasons, soFar: false))
        XCTAssertEqual(length("tv", ["seasons": .number(1), "episodes": .number(7)]), .count(7, .episodes, soFar: false))
        XCTAssertEqual(length("tv", ["seasons": .number(4), "status": .string("Returning Series")]), .count(4, .seasons, soFar: true))
        XCTAssertNil(length("tv", ["seasons": .number(1)]))
        XCTAssertEqual(length("season", ["episodes": .number(13)]), .count(13, .episodes, soFar: false))
    }

    func testAMangaStillComingOutCountsItsChaptersSoFar() {
        XCTAssertEqual(length("manga", ["number_of_chapters": .number(120), "status": .string("Finished")]), .count(120, .chapters, soFar: false))
        XCTAssertEqual(
            length("manga", ["status": .string("Publishing"), "latest_chapter_translated": .string("1160")]),
            .count(1160, .chapters, soFar: true)
        )
        XCTAssertNil(length("manga", ["status": .string("Finished"), "latest_chapter_translated": .string("1160")]))
    }

    func testAnAnimeGivesItsWholeRunAndAnAnimeFilmItsRuntime() {
        XCTAssertEqual(length("anime", ["episodes": .number(28), "status": .string("Airing")]), .count(28, .episodes, soFar: false))
        XCTAssertEqual(length("anime", ["episodes": .number(1), "runtime": .string("1h 47m")]), .runtime("1h 47m"))
    }

    func testBooksComicsAndGamesSayTheirLength() {
        XCTAssertEqual(length("book", ["number_of_pages": .number(448)]), .count(448, .pages, soFar: false))
        XCTAssertEqual(length("comic", ["issues_count": .string("12")]), .count(12, .issues, soFar: false))
        XCTAssertEqual(length("game", ["time_to_beat": .object(["normally": .number(32_400)])]), .timeToBeat(minutes: 540))
        XCTAssertNil(length("book", ["number_of_pages": .number(0)]))
        XCTAssertNil(length("game", [:]))
    }

    func testAnAlbumSaysItsMinutesOnlyWhenEverySongHasALength() throws {
        XCTAssertEqual(PostHeroFacts.length(for: try album(lengths: [240_000, 300_000])), .album(songs: 2, minutes: 9))
        XCTAssertEqual(PostHeroFacts.length(for: try album(lengths: [240_000, nil])), .album(songs: 2, minutes: nil))
    }

    // MARK: Fixtures

    private func detail(_ type: String, releaseDate: String? = nil, _ details: [String: JSONValue]) -> MediaDetail {
        let ref = MediaRef(itemId: 1, source: "tmdb", mediaType: type, mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        return MediaDetail(ref: ref, title: "Title", releaseDate: releaseDate, details: details)
    }

    private func length(_ type: String, _ details: [String: JSONValue]) -> PostHeroFacts.Length? {
        PostHeroFacts.length(for: detail(type, details))
    }

    private func people(_ names: String...) -> JSONValue {
        .array(names.map { .object(["name": .string($0)]) })
    }

    private func album(
        artists: String = #"[{"name":"Ecco2k","join_phrase":""}]"#,
        primaryType: String? = nil,
        lengths: [Int?] = [200_000]
    ) throws -> MediaDetail {
        let tracks = lengths.enumerated().map { index, length in
            let lengthField = length.map { #""length_ms":\#($0),"# } ?? ""
            return #"{"track_mbid":"t\#(index)","disc_number":1,"position":\#(index + 1),"number":"\#(index + 1)","title":"Song",\#(lengthField)"recording":{"recording_mbid":"r\#(index)","title":"Song","is_video":false}}"#
        }.joined(separator: ",")
        let type = primaryType.map { #""primary_type":"\#($0)","# } ?? ""
        let body = #"""
        {"ref":{"source":"apple_music","media_type":"music","media_id":"1"},"title":"Album","external_links":{},
         "music":{"release_group_mbid":"rg",\#(type)"artist_credit":\#(artists),
          "cover_art":{"source":"apple_music","release_group_mbid":"rg","fallback_used":false},
          "representative_release":{"release_mbid":"r","title":"Album","selection_basis":"apple_music","is_deluxe_or_remastered":false,
           "disc_count":1,"track_count":\#(lengths.count),"media":[{"position":1,"track_count":\#(lengths.count),"tracks":[\#(tracks)]}]}}}
        """#
        return try JSONDecoder.api.decode(MediaDetail.self, from: Data(body.utf8))
    }
}
