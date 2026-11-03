import XCTest
@testable import Spine

/// A log opens on the day its movie, episode or album started, when that was before midnight.
@MainActor
final class LogStartDayTests: XCTestCase {
    private static let newYork: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        return calendar
    }()

    // MARK: The rule

    func testALogTakesTheDayBeforeOnlyWhenItsLengthReachesPastMidnight() {
        let cases: [(length: Int?, hour: Int, minute: Int, day: String, why: String)] = [
            (180, 0, 10, "2026-11-02", "A three-hour film at ten past midnight"),
            (81, 1, 0, "2026-11-02", "Started 11:39 PM"),
            (181, 2, 50, "2026-11-02", "Started 11:49 PM"),
            (45, 0, 20, "2026-11-02", "An album started 11:35 PM"),
            (14, 1, 0, "2026-11-03", "A short started after midnight"),
            (181, 3, 10, "2026-11-03", "Started 12:09 AM"),
            (45, 1, 0, "2026-11-03", "An album started 12:15 AM"),
            (60, 1, 0, "2026-11-03", "Started exactly at midnight: already the new day"),
            (432, 5, 59, "2026-11-02", "Still the small hours"),
            (432, 6, 0, "2026-11-03", "From 6 AM it's a morning log"),
            (180, 12, 0, "2026-11-03", "Midday"),
            (180, 23, 30, "2026-11-03", "Before midnight it's the same day"),
            (100_000, 1, 0, "2026-11-02", "Never further back than the day before"),
            (nil, 0, 10, "2026-11-03", "No length"),
            (0, 0, 10, "2026-11-03", "An unknown length of zero")
        ]
        for item in cases {
            let now = date(2026, 11, 3, item.hour, item.minute, calendar: Self.newYork)
            let day = LogStartDay.date(lengthMinutes: item.length, now: now, calendar: Self.newYork)
            XCTAssertEqual(CalendarDateCodec.string(from: day, calendar: Self.newYork), item.day, item.why)
        }
    }

    func testTheDayBeforeCrossesAYear() {
        let now = date(2027, 1, 1, 0, 10, calendar: Self.newYork)
        let day = LogStartDay.date(lengthMinutes: 120, now: now, calendar: Self.newYork)
        XCTAssertEqual(CalendarDateCodec.string(from: day, calendar: Self.newYork), "2026-12-31")
    }

    func testRuntimeTextBecomesMinutes() {
        let cases: [(String?, Int?)] = [
            ("2h 7m", 127), ("48m", 48), ("58 min", 58), ("1h", 60), ("1.5 hours", 90), ("120", 120),
            ("2 hrs 5 mins", 125), ("2H 7M", 127),
            (nil, nil), ("", nil), ("0m", nil), ("Unknown", nil), ("inf", nil), ("nan", nil)
        ]
        for (text, minutes) in cases {
            XCTAssertEqual(LogStartDay.minutes(runtime: text), minutes, text ?? "nil")
        }
    }

    // MARK: Episode composer

    func testAnEpisodeTargetMovesToTheDayItStarted() throws {
        let now = date(2026, 11, 3, 0, 20, calendar: Self.newYork)
        let composer = Self.composer([Self.episodeTarget(date: "2026-11-03")])
            .datedByStart(lengthMinutes: 58, now: now, calendar: Self.newYork)
        let draft = try XCTUnwrap(TVComposerDraft(composer: composer))
        XCTAssertEqual(draft.date, "2026-11-02")
        XCTAssertFalse(draft.editedFields.contains(.date), "Still a default, not the user's pick")
    }

    func testOtherComposerDatesStayTheServers() {
        let now = date(2026, 11, 3, 0, 20, calendar: Self.newYork)
        let targets = [
            Self.episodeTarget(date: "2026-11-03", claimsViewingId: 9),
            Self.episodeTarget(date: "2026-10-30"),
            TVComposerTarget(id: "season:1", level: .season, seasonNumber: 1, title: "Season 1", date: "2026-11-03"),
            TVComposerTarget(id: "show", level: .show, title: "Whole show", date: "2026-11-03")
        ]
        let dated = Self.composer(targets).datedByStart(lengthMinutes: 58, now: now, calendar: Self.newYork)
        XCTAssertEqual(dated.targets.map(\.date), ["2026-11-03", "2026-10-30", "2026-11-03", "2026-11-03"],
                       "A tick's own day, an older day, and seasons or shows, which aren't one sitting")

        let noLength = Self.composer([Self.episodeTarget(date: "2026-11-03")])
            .datedByStart(lengthMinutes: nil, now: now, calendar: Self.newYork)
        XCTAssertEqual(noLength.targets.first?.date, "2026-11-03")
    }

    // MARK: Log screen

    func testAMovieLogOpensAndSavesOnTheDayItStarted() async throws {
        let diary = SurfacesDiaryRepository()
        let composer = composer(try Self.movieDetail(runtime: "3h 1m"), diary: diary, now: now(hour: 1))
        XCTAssertEqual(CalendarDateCodec.string(from: composer.consumedAt), "2026-06-09")

        let saved = await composer.save()
        XCTAssertTrue(saved)
        let request = try XCTUnwrap(diary.creates.first)
        XCTAssertEqual(try TVTrackingSurfacesTests.object(request)["consumed_at"] as? String, "2026-06-09")
    }

    func testAMovieStaysTodayOutsideTheSmallHoursOrWithoutARuntime() throws {
        let afternoon = composer(try Self.movieDetail(runtime: "3h 1m"), now: now(hour: 14))
        XCTAssertEqual(CalendarDateCodec.string(from: afternoon.consumedAt), "2026-06-10")

        let noRuntime = composer(try Self.movieDetail(runtime: nil), now: now(hour: 1))
        XCTAssertEqual(CalendarDateCodec.string(from: noRuntime.consumedAt), "2026-06-10")
    }

    func testAnAlbumUsesItsSongLengths() throws {
        let album = try Self.albumDetail(lengths: [1_500_000, 1_200_000])
        let justAfterMidnight = composer(album, now: now(hour: 0, minute: 20))
        XCTAssertEqual(CalendarDateCodec.string(from: justAfterMidnight.consumedAt), "2026-06-09")

        let later = composer(album, now: now(hour: 1))
        XCTAssertEqual(CalendarDateCodec.string(from: later.consumedAt), "2026-06-10")

        let partial = composer(try Self.albumDetail(lengths: [1_500_000, nil]), now: now(hour: 0, minute: 5))
        XCTAssertEqual(CalendarDateCodec.string(from: partial.consumedAt), "2026-06-10", "A partial sum would understate it")

        let noSongs = composer(try Self.albumDetail(lengths: []), now: now(hour: 0, minute: 5))
        XCTAssertEqual(CalendarDateCodec.string(from: noSongs.consumedAt), "2026-06-10", "Songs that haven't loaded yet")
    }

    func testAnEpisodeLogOpensAndSavesOnTheDayItStarted() async throws {
        let diary = SurfacesDiaryRepository()
        let tvComposer = Self.composer([Self.episodeTarget(date: "2026-06-10")])
        let composer = composer(try Self.episodeDetail(), diary: diary, tvComposer: tvComposer, now: now(hour: 0, minute: 20))
        XCTAssertEqual(CalendarDateCodec.string(from: composer.consumedAt), "2026-06-09")

        let saved = await composer.save()
        XCTAssertTrue(saved)
        let request = try XCTUnwrap(diary.tvCreates.first)
        XCTAssertEqual(try TVTrackingSurfacesTests.object(request)["consumed_at"] as? String, "2026-06-09")
    }

    func testOtherMediaStayToday() throws {
        for mediaType in ["book", "game", "anime", "manga", "comic", "boardgame"] {
            let body = #"{"ref":{"source":"x","media_type":"\#(mediaType)","media_id":"1"},"title":"T","details":{"runtime":"3h"},"external_links":{}}"#
            let detail = try JSONDecoder.api.decode(MediaDetail.self, from: Data(body.utf8))
            let composer = composer(detail, now: now(hour: 1))
            XCTAssertEqual(CalendarDateCodec.string(from: composer.consumedAt), "2026-06-10", mediaType)
        }
    }

    // MARK: Helpers

    private func composer(
        _ detail: MediaDetail,
        diary: DiaryRepository = SurfacesDiaryRepository(),
        tvComposer: TVComposer? = nil,
        now: Date
    ) -> MediaLogViewModel {
        MediaLogViewModel(
            detail: detail,
            trackingRepository: AppRepositories.current().tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            onSaved: {},
            tvComposer: tvComposer,
            now: now
        )
    }

    /// A past day on the device's calendar, as a log screen reads it: a save refuses future dates.
    private func now(hour: Int, minute: Int = 0) -> Date {
        date(2026, 6, 10, hour, minute, calendar: .autoupdatingCurrent)
    }

    private func date(_ year: Int, _ month: Int, _ day: Int, _ hour: Int, _ minute: Int, calendar: Calendar) -> Date {
        calendar.date(from: DateComponents(year: year, month: month, day: day, hour: hour, minute: minute))!
    }

    private static func composer(_ targets: [TVComposerTarget]) -> TVComposer {
        TVComposer(defaultTarget: targets.first?.id ?? "", targets: targets)
    }

    private static func episodeTarget(date: String, claimsViewingId: Int? = nil) -> TVComposerTarget {
        TVComposerTarget(
            id: "episode:3:2", level: .episode, seasonNumber: 3, episodeNumber: 2, title: "Episode 2", date: date,
            claimsViewingId: claimsViewingId
        )
    }

    private static func movieDetail(runtime: String?) throws -> MediaDetail {
        let details = runtime.map { #""details":{"runtime":"\#($0)"},"# } ?? ""
        let body = #"{"ref":{"source":"tmdb","media_type":"movie","media_id":"1"},"title":"Film",\#(details)"external_links":{}}"#
        return try JSONDecoder.api.decode(MediaDetail.self, from: Data(body.utf8))
    }

    private static func episodeDetail() throws -> MediaDetail {
        let body = #"{"ref":{"source":"tmdb","media_type":"episode","media_id":"1399","season_number":3,"episode_number":2},"title":"Episode","details":{"runtime":"58 min"},"external_links":{}}"#
        return try JSONDecoder.api.decode(MediaDetail.self, from: Data(body.utf8))
    }

    private static func albumDetail(lengths: [Int?]) throws -> MediaDetail {
        let tracks = lengths.enumerated().map { index, length in
            let lengthField = length.map { #""length_ms":\#($0),"# } ?? ""
            return #"{"track_mbid":"t\#(index)","disc_number":1,"position":\#(index + 1),"number":"\#(index + 1)","title":"Song",\#(lengthField)"recording":{"recording_mbid":"r\#(index)","title":"Song","is_video":false}}"#
        }.joined(separator: ",")
        let body = #"""
        {"ref":{"source":"apple_music","media_type":"music","media_id":"1"},"title":"Album","external_links":{},
         "music":{"release_group_mbid":"rg","cover_art":{"source":"apple_music","release_group_mbid":"rg","fallback_used":false},
          "representative_release":{"release_mbid":"r","title":"Album","selection_basis":"apple_music","is_deluxe_or_remastered":false,
           "disc_count":1,"track_count":\#(lengths.count),"media":[{"position":1,"track_count":\#(lengths.count),"tracks":[\#(tracks)]}]}}}
        """#
        return try JSONDecoder.api.decode(MediaDetail.self, from: Data(body.utf8))
    }
}
