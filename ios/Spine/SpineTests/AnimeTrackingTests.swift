import SwiftUI
import XCTest
@testable import Spine

/// Anime on the app (docs/anime-tracking-contract.md): the payloads, the rating rule, the page's writes, Library's lines,
/// Home's sheet and long titles. Each test names the law it checks.
@MainActor
final class AnimeTrackingTests: XCTestCase {
    private var tracking: AnimeTrackingRepository!
    private var media: AnimeMediaRepository!
    private var diary: SurfacesDiaryRepository!
    private var heldGates: [RefreshGate] = []

    override func setUp() async throws {
        try await super.setUp()
        tracking = AnimeTrackingRepository()
        media = AnimeMediaRepository()
        diary = SurfacesDiaryRepository()
        TVDataURLProtocol.reset()
    }

    override func tearDown() async throws {
        for gate in heldGates { await gate.open() }
        heldGates = []
        try await super.tearDown()
    }

    // MARK: Decoding (docs/anime-tracking-coverage.md, App payload)

    func testDecodesAStateWithEveryKey() throws {
        let anime = try decode(AnimeState.self, Self.stateJSON)
        XCTAssertEqual(anime.version, 1_792_954_800_000)
        XCTAssertTrue(anime.inLibrary)
        XCTAssertEqual(anime.status, "In progress")
        XCTAssertEqual(anime.choice, "Paused")
        XCTAssertTrue(anime.started)
        XCTAssertFalse(anime.finished)
        XCTAssertFalse(anime.caughtUp)
        XCTAssertFalse(anime.single)
        XCTAssertEqual(anime.total, 12)
        XCTAssertEqual(anime.watchedCount, 1)
        XCTAssertEqual(anime.airedCount, 7)
        XCTAssertEqual(anime.firstGapEpisode, 4)
        XCTAssertEqual(anime.upNext, AnimeEpisodePointer(episodeNumber: 6, title: "A title", imageUrl: "https://img/6.jpg", airDate: "2026-10-22", isNew: true))
        XCTAssertEqual(anime.nextAiring, AnimeAiring(episodeNumber: 8, airDate: "2026-10-29"))
        XCTAssertEqual(anime.premiere, AnimeAiring(airDate: "2026-03-12"))
        XCTAssertEqual(anime.rewatch, AnimeRewatch(startedAt: "2026-10-01", passNumber: 2))
        XCTAssertEqual(anime.lastWatchedOn, "2026-10-25")
        XCTAssertEqual(anime.selectedAction, "watching")
        XCTAssertEqual(anime.removeCount, 1)
        XCTAssertEqual(anime.ratingSourceDiaryEntryId, 11)
        XCTAssertEqual(anime.likeSourceDiaryEntryId, 12)
        XCTAssertEqual(anime.likeIsIndependent, false)
        XCTAssertEqual(anime.eye, TVEyeState(filled: false, tap: .mark, count: 6))
        XCTAssertEqual(anime.availableActions, ["pause", "drop", "log", "remove", "rate"])
        XCTAssertEqual(
            anime.actionReasons,
            ["planning": "You've started this anime. Pause or drop it instead.", "stop_rewatch": "Not rewatching."],
            "Reasons keep the wire's action names (TV-005)"
        )
        XCTAssertEqual(anime.opinionAction, .log)
        XCTAssertEqual(anime.composer?.defaultTarget, "title")
        let target = try XCTUnwrap(anime.composer?.initialTarget)
        XCTAssertEqual(target.level, .title)
        XCTAssertEqual(target.title, "Attack on Titan")
        XCTAssertEqual(target.date, "2026-10-25")
        XCTAssertEqual(anime.nextTitle, AnimeNextTitle(mediaId: "35760", title: "Attack on Titan Season 3", imageUrl: "https://img/s3.jpg", hasAired: true))
        XCTAssertEqual(anime.nextTitle?.ref.id, "mal:anime:35760:_:_")
    }

    func testDecodesCompactRows() throws {
        let rows = try decode(AnimeRows.self, """
        {"count": 12, "up_next": 7, "first_unwatched": 1, "log_message": "Delete your log first.",
         "episodes": [
           {"n": 1, "t": "To You", "i": "https://img.example/1.jpg", "a": 1},
           {"n": 2, "a": 1},
           {"n": 5, "a": 1, "w": 1, "u": 4412},
           {"n": 6, "a": 1, "w": 1, "l": 1},
           {"n": 8, "d": "2026-10-29"},
           {"n": 9}
         ]}
        """)
        XCTAssertEqual(rows.count, 12)
        XCTAssertEqual(rows.upNext, 7)
        XCTAssertEqual(rows.firstUnwatched, 1)
        XCTAssertEqual(rows.logMessage, "Delete your log first.")
        XCTAssertEqual(rows.episodes.map(\.number), [1, 2, 5, 6, 8, 9])
        XCTAssertEqual(rows.episodes[0].title, "To You")
        XCTAssertEqual(rows.episodes[0].imageUrl, "https://img.example/1.jpg")
        XCTAssertTrue(rows.episodes[0].hasAired)
        XCTAssertFalse(rows.episodes[0].isWatched)
        XCTAssertEqual(rows.episodes[2].viewingId, 4412)
        XCTAssertTrue(rows.episodes[2].isWatched)
        XCTAssertTrue(rows.episodes[3].isLogged)
        XCTAssertEqual(rows.episodes[4].airDate, "2026-10-29")
        XCTAssertFalse(rows.episodes[4].hasAired)
        XCTAssertNil(rows.episodes[5].airDate)
    }

    func testDecodesAPageATrackingRowAndAMediaSummary() throws {
        let detail = try decode(MediaDetail.self, """
        {"ref": {"source": "mal", "media_type": "anime", "media_id": "16498"}, "title": "Shingeki no Kyojin",
         "user_state": {"is_tracked": true, "status": "In progress", "in_lists": [], "has_liked": false,
                        "anime": \(Self.stateJSON)},
         "anime_rows": {"count": 1, "up_next": 1, "first_unwatched": 1, "episodes": [{"n": 1, "a": 1}]}}
        """)
        XCTAssertEqual(detail.userState?.anime?.opinionAction, .log)
        XCTAssertEqual(detail.animeRows?.episodes.count, 1)
        XCTAssertTrue(detail.ref.isAnime)
        XCTAssertTrue(detail.ref.usesCalendarLogDate, "An anime log is dated by calendar day (AN-400)")

        // The list variant has the keys from `version` to `last_watched_on`, with neither `single` nor `total`.
        let item = try decode(Spine.LibraryItem.self, """
        {"media": {"ref": {"source": "mal", "media_type": "anime", "media_id": "16498"}, "title": "Attack on Titan",
                   "genres": [], "languages": [], "roles": [], "credit_roles": []},
         "tracking": {"tracking_id": 3, "status": "In progress",
                      "anime": {"version": 7, "in_library": true, "status": "In progress", "choice": null,
                                "started": true, "finished": false, "caught_up": false, "watched_count": 7,
                                "aired_count": 24, "first_gap_episode": null,
                                "up_next": {"episode_number": 8, "title": "Small Blade", "image_url": null,
                                            "air_date": null, "is_new": false},
                                "next_airing": null, "premiere": null, "rewatch": null, "last_watched_on": "2026-10-25"}}}
        """)
        XCTAssertEqual(item.tracking.anime?.upNext?.title, "Small Blade")
        XCTAssertNil(item.tracking.anime?.eye, "Lists have no eye, menu or composer")
        XCTAssertNil(item.tracking.anime?.composer)
        XCTAssertFalse(item.tracking.anime?.single ?? true)
    }

    func testAN101ARowThatCantBeReadLosesOnlyItself() throws {
        let rows = try decode(AnimeRows.self, """
        {"count": 4, "up_next": 4, "episodes": [{"n": 1, "a": 1}, {"a": 1}, {"n": 3, "a": true}, {"n": 4, "a": 1}]}
        """)
        XCTAssertEqual(rows.episodes.map(\.number), [1, 4])
        XCTAssertEqual(rows.startIndex, 1, "The list still opens on up next")

        let page = try decode(MediaDetail.self, """
        {"ref": {"source": "mal", "media_type": "anime", "media_id": "21"}, "title": "One Piece",
         "anime_rows": {"count": 2, "episodes": [{"t": "No number"}, {"n": 2, "a": 1}]}}
        """)
        XCTAssertEqual(page.title, "One Piece")
        XCTAssertEqual(page.animeRows?.episodes.map(\.number), [2])
    }

    func testABadAnimeStateDecodesAsAbsentAndCostsNothingElse() throws {
        let page = try decode(MediaDetail.self, """
        {"ref": {"source": "mal", "media_type": "anime", "media_id": "21"}, "title": "One Piece",
         "user_state": {"is_tracked": true, "status": "In progress", "has_liked": true, "anime": {"version": "not a number"}},
         "anime_rows": "not a list"}
        """)
        XCTAssertEqual(page.title, "One Piece")
        XCTAssertEqual(page.userState?.status, "In progress")
        XCTAssertEqual(page.userState?.hasLiked, true)
        XCTAssertNil(page.userState?.anime)
        XCTAssertNil(page.animeRows)

        let envelope = try decode(TVActionResponse.self, """
        {"tracking": null, "anime": {"up_next": {"episode_number": "x"}}, "row": {"a": 1}, "rows": 7, "changed": true}
        """)
        XCTAssertTrue(envelope.envelope.changed, "The write landed")
        XCTAssertNil(envelope.envelope.anime)
        XCTAssertNil(envelope.envelope.row)
        XCTAssertNil(envelope.envelope.rows)

        let like = try decode(MediaLikeResponse.self, #"{"liked": true, "anime": [], "rows": {"episodes": 3}}"#)
        XCTAssertTrue(like.liked)
        XCTAssertNil(like.anime)
        XCTAssertNil(like.rows)
    }

    func testABadListStateCostsHomesRailOnlyThatState() async throws {
        let repository = APITrackingRepository(client: TVTrackingModelsTests.client())
        func item(_ mediaId: String, anime: String) -> String {
            """
            {"media": {"ref": {"source": "mal", "media_type": "anime", "media_id": "\(mediaId)"}, "title": "T\(mediaId)"},
             "tracking": {"tracking_id": \(mediaId), "status": "In progress", "rating": null, "progress": null, "repeats": null,
                          "start_date": null, "end_date": null, "notes": null, "updated_at": null, "anime": \(anime)}}
            """
        }
        let good = #"{"version": 1, "in_library": true, "status": "In progress", "up_next": {"episode_number": 2}}"#
        let results = [item("1", anime: #"{"version": "bad"}"#), item("2", anime: good)].joined(separator: ",")
        TVDataURLProtocol.response = Data(#"{"count": 2, "next": null, "previous": null, "results": [\#(results)]}"#.utf8)
        let page = try await repository.continueWatchingAnime(page: nil)
        XCTAssertEqual(page.results.map(\.media.ref.mediaId), ["1", "2"], "Both stay on the rail")
        XCTAssertNil(page.results[0].tracking.anime)
        XCTAssertEqual(page.results[1].tracking.anime?.upNext?.episodeNumber, 2)
    }

    func testPayloadsWithoutAnimeKeysDecodeAsBefore() throws {
        let detail = try decode(MediaDetail.self, #"{"ref":{"source":"tmdb","media_type":"tv","media_id":"1"},"title":"A Show","user_state":{"is_tracked":false,"in_lists":[],"has_liked":false}}"#)
        XCTAssertNil(detail.animeRows)
        XCTAssertNil(detail.userState?.anime)
        let like = try decode(MediaLikeResponse.self, #"{"liked":true,"media":null}"#)
        XCTAssertNil(like.anime)
        XCTAssertNil(like.rows)
    }

    func testAWriteThatAsksTheEarlierEpisodesQuestion() throws {
        let response = try decode(TVActionResponse.self, """
        {"tracking": {"tracking_id": 3, "status": "In progress"},
         "anime": {"status": "In progress", "watched_count": 1},
         "row": {"n": 5, "a": 1, "w": 1, "u": 4412},
         "rows": null, "changed": true, "finished": null,
         "earlier_unwatched": {"from": {"episode_number": 1}, "to": {"episode_number": 4}, "count": 4,
                               "question": "Also mark E1–E4 watched?", "offer_log": true}}
        """)
        XCTAssertEqual(response.envelope.row?.viewingId, 4412)
        XCTAssertNil(response.envelope.rows)
        let earlier = try XCTUnwrap(response.earlierUnwatched)
        XCTAssertEqual(earlier.from.episodeNumber, 1)
        XCTAssertEqual(earlier.to.episodeNumber, 4)
        XCTAssertEqual(earlier.question, "Also mark E1–E4 watched?")
        XCTAssertEqual(earlier.offerLog, true)
    }

    func testAWriteThatSaysTheTitleJustFinished() throws {
        let response = try decode(TVActionResponse.self, """
        {"tracking": {"tracking_id": 3, "status": "Completed"},
         "anime": {"status": "Completed", "finished": true,
                   "next_title": {"source": "mal", "media_type": "anime", "media_id": "35760",
                                  "title": "Attack on Titan Season 3", "image_url": "https://img/s3.jpg", "has_aired": true}},
         "row": {"n": 2, "a": 1, "w": 1, "u": 4413}, "rows": null, "changed": true,
         "finished": {"level": "title",
                      "composer": {"default_target": "title",
                                   "targets": [{"id": "title", "level": "title", "title": "Attack on Titan Season 2",
                                                "date": "2026-10-25", "rating": null, "liked": false, "is_rewatch": false,
                                                "available": true, "reason": null}]}},
         "earlier_unwatched": null}
        """)
        let finished = try XCTUnwrap(response.finished)
        XCTAssertEqual(finished.level, .title)
        XCTAssertNil(finished.seasonNumber)
        XCTAssertEqual(finished.composer.initialTarget?.date, "2026-10-25")
        XCTAssertEqual(response.tracking?.status, "Completed")
        XCTAssertEqual(response.envelope.anime?.nextTitle?.hasAired, true)
    }

    func testADiaryEntryAddsAnimeRowsAndItsLog() throws {
        let entry = try decode(DiaryEntry.self, TVTrackingModelsTests.diaryEntryJSON(mediaType: "anime", extra: """
        ,"anime": {"status": "Completed", "finished": true},
         "rows": {"count": 1, "up_next": null, "first_unwatched": null, "episodes": [{"n": 1, "a": 1, "w": 1, "l": 1}]},
         "anime_log": {"unmarks_count": 7, "covers_count": 12, "state_version": 99}
        """))
        XCTAssertEqual(entry.anime?.status, "Completed")
        XCTAssertEqual(entry.rows?.episodes.first?.isLogged, true)
        XCTAssertEqual(entry.trackedLog?.unmarksCount, 7)
        XCTAssertEqual(entry.trackedLog?.coversCount, 12)
        XCTAssertEqual(entry.trackedLog?.stateVersion, 99)
        let envelope = TVWriteEnvelope(diaryEntry: entry)
        XCTAssertEqual(envelope.anime?.status, "Completed")
        XCTAssertEqual(envelope.rows?.count, 1)
    }

    // MARK: Routes

    func testActionsLikesAndRowsUseTheirRoutes() async throws {
        let repository = APITrackingRepository(client: TVTrackingModelsTests.client())
        TVDataURLProtocol.response = Data(#"{"tracking":null,"anime":{"status":null},"row":null,"rows":null,"changed":true,"finished":null,"earlier_unwatched":null}"#.utf8)
        let id = UUID()

        _ = try await repository.performAnimeAction(
            source: "mal",
            mediaId: "16498",
            action: .markEarlier,
            request: TVActionRequest(
                mutationId: id,
                from: AnimeRows.coordinate(1),
                to: AnimeRows.coordinate(4),
                offerLog: true
            )
        )
        XCTAssertEqual(TVDataURLProtocol.requests.last?.url?.path, "/api/v1/tracking/mal/anime/16498/actions/mark_earlier")
        let body = try XCTUnwrap(TVDataURLProtocol.bodies.last)
        XCTAssertEqual((body["from"] as? [String: Any])?["episode_number"] as? Int, 1)
        XCTAssertEqual((body["to"] as? [String: Any])?["episode_number"] as? Int, 4)
        XCTAssertEqual(body["offer_log"] as? Bool, true)
        XCTAssertEqual(body["mutation_id"] as? String, id.uuidString)

        TVDataURLProtocol.response = Data(#"{"anime":{"status":null},"rows":{"count":0,"episodes":[]}}"#.utf8)
        let ref = MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "21", seasonNumber: nil, episodeNumber: nil)
        _ = try await repository.animeRows(ref: ref)
        XCTAssertEqual(TVDataURLProtocol.requests.last?.url?.path, "/api/v1/tracking/mal/anime/21/rows")

        TVDataURLProtocol.response = Data(#"{"count":0,"next":null,"previous":null,"results":[]}"#.utf8)
        _ = try await repository.continueWatchingAnime(page: nil)
        let url = try XCTUnwrap(TVDataURLProtocol.requests.last?.url)
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertEqual(query.first { $0.name == "media_type" }?.value, "anime")
        XCTAssertEqual(query.first { $0.name == "continue_watching" }?.value, "true", "AN-701 reads Continue Watching, as TV does")
        XCTAssertEqual(query.first { $0.name == "status" }?.value, "In progress", "All a server older than this build reads")
    }

    func testAN701InProgressReadsContinueWatchingAndKeepsTheServersOrder() async throws {
        let older = Self.card(mediaId: "1", title: "Older", anime: Self.watching(upNext: 2, watched: 1, aired: 12), updatedAt: "2026-10-01T00:00:00Z")
        let newer = Self.card(mediaId: "2", title: "Newer", anime: Self.watching(upNext: 3, watched: 2, aired: 12), updatedAt: "2026-10-20T00:00:00Z")
        // The server's order is by the latest tick or log, and a title's other dates can say otherwise.
        tracking.continueWatchingAnimeResponse = PagedResponse(count: 2, next: nil, previous: nil, results: [older, newer])
        let items = try await InProgressLibraryLoader.load(mediaTypes: ["anime"], trackingRepository: tracking, limit: 10)
        XCTAssertEqual(items.map(\.media.ref.mediaId), ["1", "2"])
        XCTAssertEqual(tracking.continueWatchingAnimeRequests, 1)
        XCTAssertTrue(tracking.listRequests.isEmpty, "Never the plain In progress list")
    }

    // MARK: The rating rule (AN-301, TV-303, TV-304)

    func testAN301OpinionActionsSayWhetherTheLogScreenOpens() {
        XCTAssertFalse(OpinionAction.mark.opensLog(isGiving: true), "Mark is the direct set")
        XCTAssertFalse(OpinionAction.save.opensLog(isGiving: true), "Save is the direct set")
        XCTAssertTrue(OpinionAction.log.opensLog(isGiving: true), "Partway, giving opens the log screen")
        XCTAssertFalse(OpinionAction.log.opensLog(isGiving: false), "Clearing is only that, at any time")
        XCTAssertFalse(OpinionAction.unknown.opensLog(isGiving: true))
    }

    func testAN301PartwayStarsOpenTheLogHoldingThemAndLeaveTheScreenAsItWas() throws {
        // The rail has set the stars on screen by the time it calls back.
        let page = model(Self.detail(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log)))
        let opinion = OpinionFixture(page: page)
        opinion.pick(halfSteps: 8)
        XCTAssertEqual(opinion.box.picker.confirmedHalfSteps, 8)

        opinion.router.rate(halfSteps: 8)

        XCTAssertEqual(opinion.logs, [.init(ratingSteps: 8, liked: nil)], "The log screen holds the value")
        XCTAssertEqual(opinion.box.picker.confirmedHalfSteps, 0, "Cancelling it leaves the stars as they were, on screen too")
        XCTAssertEqual(opinion.box.picker.draftHalfSteps, 0)
        XCTAssertTrue(tracking.actions.isEmpty, "Nothing is sent until it saves")
        XCTAssertNil(page.viewer?.rating)
    }

    func testAN301MarkAndSaveSendTheStarsAndAFailurePutsThemBack() async throws {
        for action in [OpinionAction.mark, .save] {
            let page = model(Self.detail(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: action)))
            let opinion = OpinionFixture(page: page)
            tracking.actions = []
            tracking.actionError = nil
            tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .save), rating: "4.0")]
            opinion.pick(halfSteps: 8)
            opinion.router.rate(halfSteps: 8)
            XCTAssertTrue(opinion.logs.isEmpty, "No log screen")
            _ = await page.settled()
            XCTAssertEqual(tracking.actions.map(\.action), [.rate])
            XCTAssertEqual(page.viewer?.rating, "4.0")

            tracking.actionError = APIError.httpStatus(503, nil)
            opinion.pick(halfSteps: 10)
            opinion.router.rate(halfSteps: 10)
            let failed = await page.settled()
            XCTAssertFalse(failed)
            await Task.yield()
            await Task.yield()
            XCTAssertEqual(opinion.box.picker.confirmedHalfSteps, 8, "The stars go back to what the server has")
        }
    }

    func testAN301ClearingStarsIsOnlyThatEvenPartway() async throws {
        let page = model(Self.detail(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log)))
        let opinion = OpinionFixture(page: page)
        tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log))]
        opinion.router.rate(halfSteps: 0)
        XCTAssertTrue(opinion.logs.isEmpty)
        _ = await page.settled()
        XCTAssertEqual(tracking.actions.map(\.action), [.rate])
    }

    func testAN301TheHeartGoesWhereThePageSays() async throws {
        // Partway: the log screen holds it, and nothing is sent.
        let partway = model(Self.detail(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log)))
        let opinion = OpinionFixture(page: partway)
        opinion.router.like(try XCTUnwrap(partway.detail))
        XCTAssertEqual(opinion.logs, [.init(ratingSteps: nil, liked: true)])
        XCTAssertTrue(media.likeRequests.isEmpty)
        XCTAssertEqual(partway.viewer?.hasLiked, false, "It stays as it was, on screen too")

        // Not started, or finished: the heart itself.
        for action in [OpinionAction.mark, .save] {
            media.likeRequests = []
            let page = model(Self.detail(anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: action)))
            let direct = OpinionFixture(page: page)
            direct.router.like(try XCTUnwrap(page.detail))
            XCTAssertTrue(direct.logs.isEmpty)
            _ = await page.settled()
            XCTAssertEqual(media.likeRequests.map(\.liked), [true])
        }
    }

    func testAN301ATickOnItsWayDecidesWhereTheStarsGo() async throws {
        // Not started when the stars were given, and partway once the tick lands.
        let rows = Self.rows(through: 12)
        let page = model(Self.detail(anime: Self.watching(upNext: 1, watched: 0, aired: 12, opinion: .mark), rows: rows))
        let opinion = OpinionFixture(page: page)
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 2, watched: 1, aired: 12, opinion: .log),
            row: AnimeRow(number: 1, aired: 1, watched: 1, viewingId: 1)
        )]
        page.toggleTVEpisode(rows.state(of: rows.episodes[0]))
        opinion.pick(halfSteps: 8)
        opinion.router.rate(halfSteps: 8)
        XCTAssertTrue(opinion.logs.isEmpty, "It waits")
        await gate.open()
        _ = await page.settled()
        await Task.yield()
        await Task.yield()
        XCTAssertEqual(opinion.logs, [.init(ratingSteps: 8, liked: nil)])
        XCTAssertEqual(tracking.actions.map(\.action), [.tick], "No direct set")
        XCTAssertEqual(opinion.box.picker.confirmedHalfSteps, 0)

        // A tick that fails puts the stars back and opens nothing.
        let failing = model(Self.detail(anime: Self.watching(upNext: 1, watched: 0, aired: 12, opinion: .mark), rows: rows))
        let second = OpinionFixture(page: failing)
        let held = RefreshGate()
        heldGates.append(held)
        tracking.actionGate = held
        tracking.actionError = APIError.httpStatus(503, nil)
        failing.toggleTVEpisode(rows.state(of: rows.episodes[0]))
        second.pick(halfSteps: 8)
        second.router.rate(halfSteps: 8)
        await held.open()
        _ = await failing.settled()
        await Task.yield()
        await Task.yield()
        XCTAssertTrue(second.logs.isEmpty)
        XCTAssertEqual(second.box.picker.confirmedHalfSteps, 0, "Back to what the server has")
    }

    func testTV304AShowOrSeasonGoesWhereItsOwnRuleSays() async throws {
        // TV: the show page reads the show's, and a season page that season's, never the other's.
        let showRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "tv", mediaId: "1399", seasonNumber: nil, episodeNumber: nil)
        let seasonRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 2, episodeNumber: nil)
        let unstarted = TVSeasonState(seasonNumber: 2, opinionAction: .mark)
        let partwayShow = TVShowState(opinionAction: .log, season: unstarted)
        let show = model(MediaDetail(ref: showRef, title: "A Show", userState: UserMediaState(isTracked: true, tv: partwayShow)))
        let onShow = OpinionFixture(page: show)
        onShow.pick(halfSteps: 7)
        onShow.router.rate(halfSteps: 7)
        XCTAssertEqual(onShow.logs, [.init(ratingSteps: 7, liked: nil)], "Partway show: Whole show opens")
        XCTAssertEqual(onShow.box.picker.confirmedHalfSteps, 0)
        XCTAssertTrue(tracking.tvActions.isEmpty)

        let season = model(MediaDetail(ref: seasonRef, title: "A Show", userState: UserMediaState(isTracked: true, tv: partwayShow)))
        let onSeason = OpinionFixture(page: season)
        tracking.tvActionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(tv: partwayShow, changed: true))]
        onSeason.pick(halfSteps: 7)
        onSeason.router.rate(halfSteps: 7)
        _ = await season.settled()
        XCTAssertTrue(onSeason.logs.isEmpty, "An unstarted season marks, whatever the show is")
        XCTAssertEqual(tracking.tvActions.map(\.action), [.rate])
        XCTAssertEqual(tracking.tvActions.first?.request.seasonNumber, 2)

        // A heart on a partway season opens that season's composer.
        let partwaySeason = TVSeasonState(seasonNumber: 2, opinionAction: .log)
        let heartPage = model(MediaDetail(ref: seasonRef, title: "A Show", userState: UserMediaState(isTracked: true, tv: TVShowState(opinionAction: .mark, season: partwaySeason))))
        let onHeart = OpinionFixture(page: heartPage)
        onHeart.router.like(try XCTUnwrap(heartPage.detail))
        XCTAssertEqual(onHeart.logs, [.init(ratingSteps: nil, liked: true)])
        XCTAssertTrue(media.likeRequests.isEmpty)

        // An episode page and other media types keep their own rules.
        let episodeRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "episode", mediaId: "1399", seasonNumber: 2, episodeNumber: 1)
        XCTAssertNil(model(MediaDetail(ref: episodeRef, title: "E", userState: UserMediaState(isTracked: true, tv: partwayShow))).opinionAction)
        let movieRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        XCTAssertNil(model(MediaDetail(ref: movieRef, title: "M", userState: .untracked)).opinionAction)
    }

    func testTV304OpinionActionDecodesOnTheShowAndTheSeason() throws {
        let tv = try decode(TVShowState.self, """
        {"version": 1, "in_library": true, "opinion_action": "log",
         "season": {"season_number": 2, "opinion_action": "mark"}}
        """)
        XCTAssertEqual(tv.opinionAction, .log)
        XCTAssertEqual(tv.season?.opinionAction, .mark)
        let season = try decode(TVSeasonState.self, #"{"season_number": 1, "opinion_action": "save"}"#)
        XCTAssertEqual(season.opinionAction, .save)
        let unknown = try decode(TVSeasonState.self, #"{"season_number": 1, "opinion_action": "later"}"#)
        XCTAssertEqual(unknown.opinionAction, .unknown, "An unknown value never fails the page")
        let installed = try decode(TVShowState.self, #"{"version": 1}"#)
        XCTAssertNil(installed.opinionAction)
    }

    func testAN301MarkAndSaveAreTheDirectSetAndClearingIsDirect() async throws {
        let page = model(Self.detail(anime: Self.watching(upNext: 6, watched: 5, aired: 12, opinion: .save)))
        tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 6, watched: 5, aired: 12, opinion: .save), rating: "4.5")]
        let rated = await page.rateTV(steps: 9).value
        XCTAssertTrue(rated)
        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(sent.action, .rate)
        XCTAssertEqual(sent.mediaId, "16498")
        XCTAssertEqual(sent.request.rating, 9)
        XCTAssertTrue(sent.request.includesRating)
        XCTAssertNil(sent.request.seasonNumber)
        XCTAssertEqual(page.detail?.userState?.rating, "4.5")

        tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 6, watched: 5, aired: 12, opinion: .save))]
        _ = await page.rateTV(steps: 0).value
        let body = try JSONSerialization.jsonObject(with: JSONEncoder.api.encode(tracking.actions[1].request)) as? [String: Any]
        XCTAssertTrue(body?.keys.contains("rating") == true)
        XCTAssertTrue(body?["rating"] is NSNull, "Clearing the stars sends null")
    }

    func testAN301TheLogScreenOpensHoldingTheValue() async throws {
        let state = Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log)
        let log = logModel(Self.detail(anime: state), preselectedRatingSteps: 8, preselectedLiked: true)
        XCTAssertEqual(log.ratingSteps, 8, "It opens with the value set")
        XCTAssertTrue(log.liked)
        XCTAssertFalse(log.isSaving)
    }

    func testTV304AShowOrSeasonComposerOpensOnItsOwnLevelHoldingTheValue() {
        // The show page opens on Whole show, and a season page on that season, each holding the value.
        let show = MediaRef(itemId: nil, source: "tmdb", mediaType: "tv", mediaId: "1399", seasonNumber: nil, episodeNumber: nil)
        let composer = TVComposer(defaultTarget: "show", targets: [
            TVComposerTarget(id: "season:1", level: .season, seasonNumber: 1, title: "Season 1", date: "2026-10-25"),
            TVComposerTarget(id: "show", level: .show, title: "Whole show", date: "2026-10-25"),
        ])
        let tv = TVShowState(version: 3, inLibrary: true, status: "In progress", started: true, opinionAction: .log, composer: composer)
        let detail = MediaDetail(ref: show, title: "A Show", userState: UserMediaState(isTracked: true, status: "In progress", tv: tv))
        let log = logModel(detail, preselectedRatingSteps: 7, preselectedLiked: nil)
        XCTAssertEqual(log.selectedTVTargetId, "show", "Whole show on the show page")
        XCTAssertEqual(log.selectedRef.mediaType, "tv")
        XCTAssertEqual(log.ratingSteps, 7)
        XCTAssertFalse(log.liked)
        XCTAssertTrue(diary.tvCreates.isEmpty, "Nothing changes until it saves")
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    // MARK: The page's writes (AN-000, TV-100, TV-102, TV-705)

    func testTV100ATickGoesThroughTheQueueShowsAtOnceAndRollsBackOnFailure() async throws {
        let rows = Self.rows(through: 5)
        let page = model(Self.detail(anime: Self.watching(upNext: 1, watched: 0, aired: 5), rows: rows))
        let e1 = rows.state(of: rows.episodes[0])
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        tracking.actionError = APIError.httpStatus(503, #"{"error":{"code":"provider_unavailable","message":"Anime details are unavailable right now"}}"#)

        page.toggleTVEpisode(e1)
        XCTAssertTrue(page.viewer?.tvIsWatched(e1) == true, "It shows before the server answers")
        XCTAssertEqual(page.viewer?.anime?.watchedCount, 1)
        XCTAssertEqual(page.viewer?.status, "In progress")
        await gate.open()
        let failed = await page.settled()
        XCTAssertFalse(failed)
        XCTAssertFalse(page.viewer?.tvIsWatched(e1) == true, "A failure restores the last confirmed state")
        XCTAssertEqual(page.viewer?.anime?.watchedCount, 0)
        XCTAssertEqual(page.quickActionErrorMessage, "Anime details are unavailable right now")

        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(sent.action, .tick)
        XCTAssertEqual(sent.request.episodeNumber, 1)
        XCTAssertNil(sent.request.seasonNumber, "TV's route with no season")
        XCTAssertNotNil(sent.request.mutationId)

        // A retry keeps its mutation ID, and the answer's row replaces its row where it is.
        tracking.actionGate = nil
        tracking.actionError = nil
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 2, watched: 1, aired: 5),
            row: AnimeRow(number: 1, aired: 1, watched: 1, viewingId: 41)
        )]
        page.toggleTVEpisode(e1)
        let confirmed = await page.settled()
        XCTAssertTrue(confirmed)
        XCTAssertEqual(tracking.actions[1].request.mutationId, sent.request.mutationId)
        XCTAssertEqual(page.detail?.animeRows?.episodes[0].viewingId, 41)
        XCTAssertEqual(page.detail?.animeRows?.upNext, 2, "Up next follows the new state")
        XCTAssertEqual(page.detail?.animeRows?.firstUnwatched, 2)
        XCTAssertEqual(page.detail?.userState?.anime?.upNext?.episodeNumber, 2)
        XCTAssertEqual(page.detail?.userState?.status, "In progress")
    }

    func testTV102AnUntickSendsTheViewingTheRowNames() async throws {
        let rows = AnimeRows(upNext: 2, firstUnwatched: 2, logMessage: "Delete your log first.", episodes: [
            AnimeRow(number: 1, aired: 1, watched: 1, viewingId: 77),
            AnimeRow(number: 2, aired: 1),
        ])
        let page = model(Self.detail(anime: Self.watching(upNext: 2, watched: 1, aired: 2), rows: rows))
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 1, watched: 0, aired: 2),
            row: AnimeRow(number: 1, aired: 1)
        )]
        let e1 = rows.state(of: rows.episodes[0])

        page.toggleTVEpisode(e1)
        XCTAssertFalse(page.viewer?.tvIsWatched(e1) == true, "The untick shows at once")
        let settled = await page.settled()
        XCTAssertTrue(settled)
        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(sent.action, .untick)
        XCTAssertEqual(sent.request.episodeNumber, 1)
        XCTAssertEqual(sent.request.viewingId, 77)
        XCTAssertFalse(page.detail?.animeRows?.episodes[0].isWatched ?? true)
    }

    func testAN000AFilledTickWithALogSaysDeleteYourLogFirstAndSendsNothing() throws {
        let rows = AnimeRows(upNext: nil, firstUnwatched: nil, logMessage: "Delete your log first.", episodes: [
            AnimeRow(number: 1, aired: 1, watched: 1, logged: 1),
        ])
        let page = model(Self.detail(anime: AnimeState(inLibrary: true, status: "Completed", finished: true), rows: rows))
        page.toggleTVEpisode(rows.state(of: rows.episodes[0]))
        XCTAssertEqual(page.tvNotice, "Delete your log first.")
        XCTAssertTrue(tracking.actions.isEmpty)
        XCTAssertFalse(page.hasPendingWrites)
    }

    func testAN902ARowWithNoViewingToNameStillUnticks() async throws {
        let rows = AnimeRows(upNext: 2, firstUnwatched: 2, logMessage: "Delete your log first.", episodes: [
            AnimeRow(number: 1, aired: 1, watched: 1),
            AnimeRow(number: 2, aired: 1),
        ])
        let page = model(Self.detail(anime: Self.watching(upNext: 2, watched: 1, aired: 2), rows: rows))
        tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 1, watched: 0, aired: 2), row: AnimeRow(number: 1, aired: 1))]
        page.toggleTVEpisode(rows.state(of: rows.episodes[0]))
        let settled = await page.settled()
        XCTAssertTrue(settled)
        XCTAssertNil(page.tvNotice)
        XCTAssertNil(tracking.actions.first?.request.viewingId)
        XCTAssertEqual(tracking.actions.first?.action, .untick)
    }

    func testTV108MarkWatchedUpToHereAsksThenShowsAtOnce() async throws {
        let rows = Self.rows(through: 1_189)
        let page = model(Self.detail(anime: Self.watching(upNext: 1, watched: 0, aired: 1_189), rows: rows))
        let far = rows.state(of: rows.episodes[799])
        page.askMarkTVUpTo(far)
        let confirmation = try XCTUnwrap(page.tvConfirmation)
        XCTAssertEqual(confirmation.prompt, "Mark E1–E800 watched?")
        XCTAssertEqual(confirmation.kind, .markUpTo(AnimeRows.coordinate(800)))

        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        var answered = rows
        for index in 0..<800 { answered.episodes[index].watched = 1 }
        answered.upNext = 801
        answered.firstUnwatched = 801
        tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 801, watched: 800, aired: 1_189), rows: answered)]
        page.confirmTV(confirmation)
        XCTAssertTrue(page.viewer?.tvIsWatched(far) == true, "The rows show it at once")
        XCTAssertTrue(page.viewer?.tvIsWatched(rows.state(of: rows.episodes[0])) == true)
        XCTAssertFalse(page.viewer?.tvIsWatched(rows.state(of: rows.episodes[800])) == true, "Nothing past it")
        XCTAssertEqual(page.viewer?.tvPending.episodes.count, 0, "One number, however long the list")
        await gate.open()
        let settled = await page.settled()
        XCTAssertTrue(settled)
        XCTAssertEqual(tracking.actions.first?.action, .markUpTo)
        XCTAssertEqual(tracking.actions.first?.request.episodeNumber, 800)
        XCTAssertNil(tracking.actions.first?.request.seasonNumber)
        XCTAssertEqual(page.detail?.animeRows?.upNext, 801)
        XCTAssertEqual(page.detail?.animeRows?.episodes[799].isWatched, true)
    }

    func testAN300TheEyeFollowsTheServersTap() async throws {
        // Empty: it marks at once, with no question.
        var state = Self.watching(upNext: 3, watched: 2, aired: 12)
        state.eye = TVEyeState(filled: false, tap: .mark, count: 10)
        let page = model(Self.detail(anime: state, rows: Self.rows(through: 12)))
        var marked = AnimeState(version: 8, inLibrary: true, status: "Completed", finished: true, watchedCount: 12, airedCount: 12)
        marked.eye = TVEyeState(filled: true, tap: .unmarkMarks, count: 10)
        tracking.actionResponses = [Self.response(anime: marked, rows: Self.rows(through: 12, watched: 12))]
        page.tapTVEye()
        XCTAssertEqual(page.viewer?.anime?.status, "Completed", "It shows at once")
        XCTAssertEqual(page.viewer?.anime?.eye?.filled, true)
        let settled = await page.settled()
        XCTAssertTrue(settled)
        XCTAssertEqual(tracking.actions.last?.action, .markWatched)
        XCTAssertEqual(page.detail?.animeRows?.episodes.allSatisfy(\.isWatched), true)

        // Filled, with marks to remove: it undoes them at once.
        tracking.actionResponses = [Self.response(anime: Self.watching(upNext: 3, watched: 2, aired: 12), rows: Self.rows(through: 12, watched: 2))]
        page.tapTVEye()
        let undone = await page.settled()
        XCTAssertTrue(undone)
        XCTAssertEqual(tracking.actions.last?.action, .unmark)
        XCTAssertNil(tracking.actions.last?.request.confirm)
        XCTAssertEqual(page.detail?.animeRows?.episodes[2].isWatched, false)

        // Filled, finished by ticking: it asks, with the count and the version.
        var ticked = AnimeState(version: 9, inLibrary: true, status: "Completed", finished: true)
        ticked.eye = TVEyeState(filled: true, tap: .unmarkConfirm, count: 24, prompt: "Mark all 24 episodes unwatched?")
        let asks = model(Self.detail(anime: ticked, rows: Self.rows(through: 24, watched: 24)))
        asks.tapTVEye()
        let question = try XCTUnwrap(asks.tvConfirmation)
        XCTAssertEqual(question.prompt, "Mark all 24 episodes unwatched?")
        XCTAssertEqual(question.expectedVersion, 9)
        tracking.actionResponses = [Self.response(anime: AnimeState(), rows: Self.rows(through: 24), tracked: false)]
        asks.confirmTV(question)
        let confirmed = await asks.settled()
        XCTAssertTrue(confirmed)
        XCTAssertEqual(tracking.actions.last?.request.confirm, true)
        XCTAssertEqual(tracking.actions.last?.request.expectedVersion, 9)

        // A log blocks it, and nothing aired explains: neither writes.
        var blocked = AnimeState(inLibrary: true, status: "Completed")
        blocked.eye = TVEyeState(filled: true, tap: .blocked, message: "Delete your log first.")
        let blockedPage = model(Self.detail(anime: blocked))
        let count = tracking.actions.count
        blockedPage.tapTVEye()
        XCTAssertEqual(blockedPage.tvNotice, "Delete your log first.")
        var unaired = AnimeState()
        unaired.eye = TVEyeState(filled: false, tap: .nothingAired, message: "Premieres Mar 12")
        let unairedPage = model(Self.detail(anime: unaired))
        unairedPage.tapTVEye()
        XCTAssertEqual(unairedPage.tvNotice, "Premieres Mar 12")
        XCTAssertEqual(tracking.actions.count, count)
    }

    func testAN301AHeartFoldsTheStateAndRowsIn() async throws {
        let ref = Self.ref()
        let detail = Self.detail(anime: AnimeState(eye: TVEyeState(filled: false, tap: .mark, count: 12), opinionAction: .mark), rows: Self.rows(through: 12), tracked: false)
        let page = model(detail)
        var marked = AnimeState(version: 4, inLibrary: true, status: "Completed", finished: true, watchedCount: 12, airedCount: 12)
        marked.opinionAction = .save
        media.likeResponse = MediaLikeResponse(
            liked: true,
            media: MediaSummary(ref: ref, title: "Attack on Titan", userState: UserMediaState(isTracked: true, status: "Completed", hasLiked: true, anime: marked)),
            anime: marked,
            rows: Self.rows(through: 12, watched: 12)
        )
        let liked = await page.toggleMediaLike(for: detail).value
        XCTAssertTrue(liked)
        XCTAssertEqual(page.detail?.userState?.hasLiked, true)
        XCTAssertEqual(page.detail?.userState?.anime?.opinionAction, .save)
        XCTAssertEqual(page.detail?.userState?.status, "Completed")
        XCTAssertEqual(page.detail?.animeRows?.episodes.allSatisfy(\.isWatched), true)
        XCTAssertEqual(media.likeRequests, [MediaLikeRequest(ref: ref, liked: true)])
    }

    func testAN003ARefusedWriteSaysTheServersReason() async throws {
        let page = model(Self.detail(anime: Self.watching(upNext: 1, watched: 0, aired: 12), rows: Self.rows(through: 12)))
        tracking.actionError = APIError.httpStatus(400, #"{"error":{"code":"validation_error","message":"This anime has 25 episodes."}}"#)
        let ok = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 26).value
        XCTAssertFalse(ok)
        XCTAssertEqual(page.quickActionErrorMessage, "This anime has 25 episodes.")
    }

    func testAN203ALastTickOpensTheLogScreenAtOnceWithItsDefaults() async throws {
        let page = model(Self.detail(anime: Self.watching(upNext: 12, watched: 11, aired: 12), rows: Self.rows(through: 12, watched: 11)))
        let composer = TVComposer(defaultTarget: "title", targets: [
            TVComposerTarget(id: "title", level: .title, title: "Attack on Titan", date: "2026-10-25", isRewatch: true),
        ])
        var finished = AnimeState(version: 5, inLibrary: true, status: "Completed", finished: true, watchedCount: 12, airedCount: 12)
        finished.composer = composer
        tracking.actionResponses = [Self.response(
            anime: finished,
            row: AnimeRow(number: 12, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        let ok = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 12).value
        XCTAssertTrue(ok)
        let item = try XCTUnwrap(page.tvFollowUp.composer, "It opens as soon as the response arrives")
        XCTAssertEqual(item.detail.ref.id, page.ref.id, "On the page's own detail, loading nothing first")
        XCTAssertEqual(media.detailRequests.count, 0)
        let log = logModel(item.detail, tvComposer: item.finished.composer)
        XCTAssertEqual(log.selectedRef.mediaType, "anime")
        XCTAssertFalse(log.supportsTVTargetPicker, "One target, no picker")
        XCTAssertFalse(log.supportsProgress, "No Progress mode")
        XCTAssertEqual(TVTrackingPresentation.calendarString(log.consumedAt), "2026-10-25", "Dated with the tick's day")
        XCTAssertTrue(log.isRepeat, "During a rewatch it opens with Rewatch on")
        page.tvFollowUp.composerDismissed()
        XCTAssertNil(page.tvFollowUp.composer, "Not now saves nothing")
        XCTAssertTrue(diary.tvCreates.isEmpty)
    }

    func testTV107TheFirstTickAsksTheQuestionThenTheYesSendsItsRange() async throws {
        let page = model(Self.detail(anime: AnimeState(), rows: Self.rows(through: 5), tracked: false))
        let earlier = TVEarlierUnwatched(
            from: AnimeRows.coordinate(1),
            to: AnimeRows.coordinate(4),
            count: 4,
            question: "Also mark E1–E4 watched?",
            offerLog: true
        )
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "Title", date: "2026-10-25")])
        tracking.actionResponses = [
            Self.response(
                anime: Self.watching(upNext: 1, watched: 1, aired: 5),
                row: AnimeRow(number: 5, aired: 1, watched: 1, viewingId: 3),
                earlier: earlier
            ),
            // The Yes finishes the title, so its response names the log screen.
            Self.response(
                anime: AnimeState(inLibrary: true, status: "Completed", finished: true),
                rows: Self.rows(through: 5, watched: 5),
                finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
            ),
        ]
        let ok = await page.tickTVEpisode(seasonNumber: 1, episodeNumber: 5).value
        XCTAssertTrue(ok)
        XCTAssertEqual(page.tvFollowUp.question, earlier, "TV's dialog, with the server's question")
        XCTAssertNil(page.tvFollowUp.composer)

        await page.tvFollowUp.answer(yes: true)
        let yes = try XCTUnwrap(tracking.actions.last)
        XCTAssertEqual(yes.action, .markEarlier)
        XCTAssertEqual(yes.request.from?.episodeNumber, 1)
        XCTAssertEqual(yes.request.to?.episodeNumber, 4)
        XCTAssertEqual(yes.request.offerLog, true)
        XCTAssertNotNil(yes.request.mutationId, "A Yes needs a mutation ID")
        XCTAssertNotNil(page.tvFollowUp.composer, "The Yes finished it: the log screen follows")
        XCTAssertEqual(page.detail?.animeRows?.episodes.allSatisfy(\.isWatched), true)

        // No sends nothing.
        let other = model(Self.detail(anime: AnimeState(), rows: Self.rows(through: 5), tracked: false))
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 1, watched: 1, aired: 5),
            row: AnimeRow(number: 5, aired: 1, watched: 1, viewingId: 3),
            earlier: earlier
        )]
        let count = tracking.actions.count
        let ticked = await other.tickTVEpisode(seasonNumber: 1, episodeNumber: 5).value
        XCTAssertTrue(ticked)
        await other.tvFollowUp.answer(yes: false)
        XCTAssertEqual(tracking.actions.count, count + 1, "Only the tick")
    }

    func testAN400ASavedLogSendsADateAndAMutationIdAndFoldsItsStateIn() async throws {
        let composer = TVComposer(defaultTarget: "title", targets: [
            TVComposerTarget(id: "title", level: .title, title: "Attack on Titan", date: "2026-10-20", rating: "3.5", liked: true),
        ])
        var state = Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log)
        state.composer = composer
        let detail = Self.detail(anime: state, rows: Self.rows(through: 12, watched: 2))
        let page = model(detail)
        var saved: MediaLogSaveResult?
        let log = logModel(detail, preselectedRatingSteps: 8, onResult: { saved = $0 })
        XCTAssertEqual(log.ratingSteps, 8, "The value it opened with wins")
        XCTAssertTrue(log.liked, "The heart prefills from the title's own")
        XCTAssertEqual(TVTrackingPresentation.calendarString(log.consumedAt), "2026-10-20")
        XCTAssertEqual(log.primaryActionTitle, "Log Anime")

        var finished = AnimeState(version: 6, inLibrary: true, status: "Completed", finished: true, watchedCount: 12, airedCount: 12)
        finished.opinionAction = .save
        diary.createTVEnvelope = TVWriteEnvelope(changed: true, anime: finished, rows: Self.rows(through: 12, watched: 12))
        let ok = await log.save()
        XCTAssertTrue(ok)
        let request = try XCTUnwrap(diary.tvCreates.first)
        XCTAssertEqual(request.ref.mediaType, "anime")
        XCTAssertNotNil(request.mutationId)
        XCTAssertEqual(request.isRewatch, false)
        let body = try XCTUnwrap(try JSONSerialization.jsonObject(with: JSONEncoder.api.encode(request)) as? [String: Any])
        XCTAssertEqual(body["consumed_at"] as? String, "2026-10-20", "A calendar date, never a datetime")
        XCTAssertNotNil(body["mutation_id"])

        page.applyLogSave(try XCTUnwrap(saved), followsUp: false)
        XCTAssertEqual(page.detail?.userState?.anime?.status, "Completed")
        XCTAssertEqual(page.detail?.userState?.status, "Completed")
        XCTAssertEqual(page.detail?.animeRows?.episodes.allSatisfy(\.isWatched), true)
    }

    func testAN400TheLogScreenReadsTheTitleThePageReads() {
        let state = UserMediaState(isTracked: true, anime: Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log))
        let detail = MediaDetail(ref: Self.ref(), title: "Shingeki no Kyojin", preferredTitle: "Attack on Titan", userState: state)
        XCTAssertEqual(logModel(detail).selectedTitle, "Attack on Titan")
    }

    func testAN400TheDiaryTabLogsATitleUnderTheSameRules() async throws {
        let viewModel = DiaryCreateViewModel(diaryRepository: diary, mediaRepository: media, trackingRepository: tracking, onUnauthorized: {})
        viewModel.select(MediaSummary(ref: Self.ref(), title: "Attack on Titan", posterUrl: nil, posterOrientation: .portrait))
        XCTAssertTrue(viewModel.usesCalendarDate)
        XCTAssertNil(viewModel.isRewatchForRequest, "Left to the server until the toggle is touched")

        _ = await viewModel.create()
        let request = try XCTUnwrap(diary.tvCreates.last)
        XCTAssertEqual(request.ref.mediaType, "anime")
        XCTAssertNotNil(request.mutationId)
        let body = try XCTUnwrap(try JSONSerialization.jsonObject(with: JSONEncoder.api.encode(request)) as? [String: Any])
        XCTAssertEqual((body["consumed_at"] as? String)?.count, 10, "A calendar date, never a datetime")
    }

    func testAN402EditAndDeleteMirrorTV() async throws {
        let entry = try decode(DiaryEntry.self, TVTrackingModelsTests.diaryEntryJSON(mediaType: "anime", extra: #","anime_log":{"unmarks_count":7,"covers_count":12,"state_version":44}"#))
        diary.detailResults = [entry]
        let viewModel = DiaryLogDetailViewModel(
            entryId: entry.id,
            diaryRepository: diary,
            mediaRepository: media,
            onUnauthorized: {}
        )
        await viewModel.load()
        XCTAssertEqual(viewModel.deletePrompt, "Delete this log? 7 episodes it marked watched will be unmarked.")
        let deleted = await viewModel.confirmDelete()
        XCTAssertTrue(deleted)
        let call = try XCTUnwrap(diary.deletes.first)
        XCTAssertEqual(call.expectedVersion, 44)
        XCTAssertNotNil(call.mutationId)
    }

    func testTV005TheMenu() {
        var anime = AnimeState(status: "In progress", selectedAction: "watching", removeCount: 13)
        anime.inLibrary = true
        anime.availableActions = ["pause", "drop", "log", "remove", "rate"]
        anime.actionReasons = ["planning": "You've started this anime. Pause or drop it instead."]
        var items = TVTrackingPresentation.animeMenu(for: anime)
        XCTAssertEqual(items.map(\.title), ["Watching", "Planning", "Pause", "Drop", "Log", "Remove from Library"])
        XCTAssertTrue(items[0].isSelected)
        XCTAssertFalse(items[1].isAvailable)
        XCTAssertEqual(items[1].reason, "You've started this anime. Pause or drop it instead.")
        XCTAssertEqual(items[3].command, .confirmDrop)
        XCTAssertEqual(items[5].command, .confirmRemove)
        XCTAssertEqual(AnimeTrackingPresentation.removePrompt(for: anime), "Remove this anime? Your 13 watched episodes will be unmarked.")
        XCTAssertEqual(AnimeTrackingPresentation.dropPrompt, "Drop this anime? Your progress will be kept.")

        // Paused: Resume in Watching's place, one row.
        anime.status = "Paused"
        anime.selectedAction = "pause"
        anime.availableActions = ["resume", "drop", "log", "remove", "rate"]
        items = TVTrackingPresentation.animeMenu(for: anime)
        XCTAssertEqual(items.map(\.title), ["Resume", "Planning", "Pause", "Drop", "Log", "Remove from Library"])
        XCTAssertEqual(items[0].command, .action(.resume))

        // A finished title offers Rewatch; during one, Stop Rewatching takes its place.
        anime.status = "Completed"
        anime.selectedAction = nil
        anime.availableActions = ["pause", "drop", "rewatch", "log", "rate"]
        anime.actionReasons = ["watching": "You've watched everything that has aired.", "remove": "Delete this anime's logs first."]
        items = TVTrackingPresentation.animeMenu(for: anime)
        XCTAssertEqual(items.map(\.title), ["Watching", "Planning", "Pause", "Drop", "Rewatch", "Log", "Remove from Library"])
        XCTAssertEqual(items.last?.reason, "Delete this anime's logs first.")
        XCTAssertEqual(items[0].reason, "You've watched everything that has aired.")
        anime.availableActions = ["pause", "drop", "stop_rewatch", "log", "rate"]
        anime.actionReasons = ["rewatch": "You're already rewatching this."]
        XCTAssertEqual(TVTrackingPresentation.animeMenu(for: anime).map(\.title), ["Watching", "Planning", "Pause", "Drop", "Stop Rewatching", "Log", "Remove from Library"])

        // Not in the library: nothing to remove.
        XCTAssertFalse(TVTrackingPresentation.animeMenu(for: AnimeState()).map(\.title).contains("Remove from Library"))
    }

    func testAN002ASingleHasPlanningLogAndRemoveAndNoNumberAnywhere() {
        var single = AnimeState(inLibrary: true, status: "Completed", finished: true, single: true, watchedCount: 1, airedCount: 1, removeCount: 1)
        single.availableActions = ["planning", "log", "remove", "rate"]
        XCTAssertEqual(TVTrackingPresentation.animeMenu(for: single).map(\.title), ["Planning", "Log", "Remove from Library"])
        XCTAssertEqual(AnimeTrackingPresentation.pageLines(for: single), [])
        XCTAssertNil(AnimeTrackingPresentation.gridBadge(for: single))
        XCTAssertNil(AnimeTrackingPresentation.cardLine(for: single))
        XCTAssertEqual(AnimeTrackingPresentation.removePrompt(for: single), "Remove this anime?")

        // A single imported as In progress stays on that shelf with no number. Lists don't say it's a single.
        var imported = AnimeState(inLibrary: true, status: "In progress", watchedCount: 0, airedCount: 1)
        imported.single = false
        XCTAssertNil(AnimeTrackingPresentation.cardLine(for: imported))
        XCTAssertEqual(AnimeLibraryCard.line(for: Self.card(mediaId: "9", title: "Movie", anime: imported)), "Watching")
        XCTAssertNil(AnimeTrackingPresentation.gridBadge(for: imported))
    }

    // MARK: Rows (AN-101, TV-108)

    func testAN101RowsReadAsTheContractSays() {
        let rows = AnimeRows(upNext: 2, firstUnwatched: 2, logMessage: "Delete your log first.", episodes: [
            AnimeRow(number: 1, title: " To You ", imageUrl: "https://img/1.jpg", aired: 1, watched: 1, viewingId: 5),
            AnimeRow(number: 2, aired: 1),
            AnimeRow(number: 3, aired: 1, watched: 1, logged: 1),
            AnimeRow(number: 4, aired: 1),
            AnimeRow(number: 5, airDate: "2026-10-29"),
            AnimeRow(number: 6),
        ])
        let models = rows.episodes.indices.map { rows.rowModel(at: $0) }
        XCTAssertEqual(models[0].title, "To You")
        XCTAssertEqual(models[0].summary?.imageUrl, "https://img/1.jpg")
        XCTAssertEqual(models[1].title, "Episode 2", "Rows with no title read Episode 5")

        let trailing = models.map { TVEpisodeTrailing.resolve($0.tv, isWatched: $0.tv?.watched ?? false, calendar: Self.calendar) }
        XCTAssertEqual(trailing[0], .tick(watched: true))
        XCTAssertEqual(trailing[1], .tick(watched: false))
        XCTAssertEqual(trailing[3], .tick(watched: false))
        XCTAssertEqual(trailing[4], .airDate("Oct 29"), "An unaired row shows its date in place of a tick")
        XCTAssertEqual(trailing[5], .airDate("TBA"))

        XCTAssertEqual(models[0].tv?.untickViewingId, 5)
        XCTAssertNil(models[2].tv?.untickViewingId)
        XCTAssertEqual(models[2].tv?.untickMessage, "Delete your log first.")
        XCTAssertEqual(models[1].tv?.isUpNext, true)

        // Touch and hold names the range from the first unwatched; an unaired row has none, and neither has a row with
        // nothing unwatched up to it.
        XCTAssertEqual(models[3].tv?.markUpToPrompt, "Mark E2–E4 watched?")
        XCTAssertEqual(models[1].tv?.markUpToPrompt, "Mark E2 watched?")
        XCTAssertNil(models[0].tv?.markUpToPrompt)
        XCTAssertNil(models[4].tv?.markUpToPrompt)
    }

    func testAN101AWriteThatNamesOneRowMovesUpNextAndTheFirstUnwatched() {
        var rows = Self.rows(through: 5)
        rows.apply(AnimeRow(number: 1, aired: 1, watched: 1, viewingId: 9), state: Self.watching(upNext: 2, watched: 1, aired: 5))
        XCTAssertEqual(rows.upNext, 2)
        XCTAssertEqual(rows.firstUnwatched, 2)
        XCTAssertEqual(rows.episodes.map(\.isWatched), [true, false, false, false, false])
        // With everything watched there's no up next.
        var last = Self.rows(through: 1, watched: 0)
        last.apply(AnimeRow(number: 1, aired: 1, watched: 1, viewingId: 9), state: AnimeState(status: "Completed", finished: true))
        XCTAssertNil(last.upNext)
        XCTAssertNil(last.firstUnwatched)
    }

    func testAN101AListOpensOnUpNextOrWithNoneOnTheLastAiredRow() {
        var rows = Self.rows(through: 12, watched: 7)
        XCTAssertEqual(rows.startEpisode, 8)
        rows.upNext = nil
        rows.episodes.append(AnimeRow(number: 13, airDate: "2030-01-01"))
        XCTAssertEqual(rows.startEpisode, 12, "Caught up: the last aired row")
        XCTAssertNil(AnimeRows().startEpisode)
    }

    func testAN101APageListsAShortTitleWholeAndALongOneFromUpNext() {
        let short = Self.rows(through: 24)
        XCTAssertFalse(short.isLong)
        XCTAssertEqual(short.pageRange(start: 7), 0..<24)
        let long = Self.rows(through: 1_189)
        XCTAssertTrue(long.isLong)
        XCTAssertEqual(long.pageRange(start: 800), 800..<810)
        XCTAssertEqual(long.pageRange(start: 1_188), 1_179..<1_189, "Near the end it keeps its rows")
    }

    func testAN101TheListWorksOutEachRowsPlace() {
        let metrics = AnimeListMetrics(count: 1_189)
        let rowHeight = TVEpisodeRows.rowHeight
        let spacing = TVEpisodeRows.spacing
        XCTAssertEqual(metrics.stride, rowHeight + spacing)
        XCTAssertEqual(metrics.offset(ofRow: 800), 800 * (rowHeight + spacing))

        // It builds what's on screen and a chunk or more either side, never the whole list.
        let opening = metrics.built(metrics.window(openingOnRow: 800))
        XCTAssertTrue(opening.contains(800))
        XCTAssertLessThan(opening.count, 40)

        // Laid out as the list lays them out, a spacer, the rows with their spacing, a spacer: wherever the window is, every
        // row sits where it would in the whole list, and the list is as long as all its rows.
        let wholeList = 1_189 * rowHeight + 1_188 * spacing
        for y in stride(from: CGFloat(0), through: wholeList, by: 777) {
            let window = metrics.built(metrics.window(y: y, height: 700))
            let builtRows = CGFloat(window.count) * rowHeight + CGFloat(window.count - 1) * spacing
            XCTAssertEqual(metrics.heightAbove(window) + builtRows + metrics.heightBelow(window), wholeList)
            for row in window {
                let top = metrics.heightAbove(window) + CGFloat(row - window.lowerBound) * (rowHeight + spacing)
                XCTAssertEqual(top, metrics.offset(ofRow: row))
            }
        }

        // Scrolling a little changes nothing: the rows are built in chunks, so most frames build nothing.
        var changes = 0
        var last = metrics.built(metrics.window(y: 0, height: 700))
        for y in stride(from: CGFloat(0), through: 8_000, by: 10) {
            let next = metrics.built(metrics.window(y: y, height: 700))
            if next != last { changes += 1 }
            last = next
            XCTAssertTrue(next.contains(Int(y / metrics.stride)), "The row at the top is always built")
            XCTAssertTrue(next.contains(min(Int((y + 700) / metrics.stride), 1_188)), "So is the one at the bottom")
        }
        XCTAssertLessThanOrEqual(changes, 10, "80 rows scrolled, about ten changes")

        // Every episode is reachable, the ends included.
        XCTAssertEqual(metrics.window(y: 0, height: 700).lowerBound, 0)
        XCTAssertEqual(metrics.built(metrics.window(y: metrics.offset(ofRow: 1_188), height: 700)).upperBound, 1_189)
        XCTAssertEqual(metrics.window(y: -50, height: 700).lowerBound, 0, "An overscroll at the top")
        let empty = AnimeListMetrics(count: 0)
        XCTAssertEqual(empty.built(empty.window(y: 0, height: 700)), 0..<0)

        // A row a tick adds shows at once: the window doesn't stop at the old end (N-18).
        let one = AnimeListMetrics(count: 1)
        let two = AnimeListMetrics(count: 2)
        XCTAssertEqual(two.built(one.window(y: 0, height: 700)), 0..<2)

        // The room under the last row lets a row near the end reach the top.
        XCTAssertEqual(metrics.roomBelowRows(viewportHeight: 340, openingOnRow: 1_188), 440)
        XCTAssertEqual(metrics.roomBelowRows(viewportHeight: 340, openingOnRow: 100), 0)

        // The accessibility text sizes' rows have a fixed height too.
        XCTAssertEqual(AnimeListMetrics(isAccessibilitySize: true, count: 10).stride, AnimeListMetrics.accessibilityRowHeight + spacing)
    }

    func testAN701TheListNamesUpNextsRowToScrollTo() async throws {
        // It scrolls to a row, whatever an OS version counts as the top of the list.
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        let ticks = continueWatching()
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 8, watched: 7, aired: 24), rows: Self.rows(through: 24, watched: 7))
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        let beforeRows = sheet.startRowID
        XCTAssertEqual(sheet.startIndex, 0, "Up next's row alone")
        await sheet.loadRowsIfNeeded()
        XCTAssertEqual(sheet.startRowID, beforeRows, "The same row before and after the rows load")
        let rows = try XCTUnwrap(sheet.rows)
        XCTAssertEqual(sheet.startRowID, rows.rowModel(at: 7).id)
        XCTAssertEqual(sheet.startIndex, 7)
    }

    // MARK: Library (AN-700)

    func testAN700TheFiveShelves() {
        XCTAssertEqual(LibraryShelf.available(for: "anime"), [.currentlyReading, .planning, .paused, .didNotFinish, .read])
        XCTAssertEqual(LibraryShelf.available(for: "anime").map { $0.title(mediaType: "anime") }, ["Watching", "Planning", "Paused", "Dropped", "Completed"])
        XCTAssertTrue(LibraryShelf.usesStatusShelves("anime"))
        XCTAssertEqual(LibraryShelf.resolved(.tracked, for: "anime"), .currentlyReading)
        XCTAssertEqual(LibraryShelf.animeEmptyTitle(.didNotFinish), "No dropped anime")
        XCTAssertEqual(LibraryShelf.animeEmptyMessage(.currentlyReading), "Anime you're watching will appear here.")
    }

    func testAN700CardLinesSayWhereYouAre() {
        let today = Self.date("2026-10-26") // A Monday.
        func line(_ anime: AnimeState) -> String? {
            AnimeTrackingPresentation.cardLine(for: anime, today: today, calendar: Self.calendar)
        }
        XCTAssertEqual(line(Self.watching(upNext: 8, watched: 7, aired: 24)), "E8 next · 7 of 24")

        var noCount = Self.watching(upNext: 8, watched: 7, aired: 24)
        noCount.airedCount = nil
        XCTAssertEqual(line(noCount), "E8 next · 7 episodes", "AN-102: when Spine doesn't know how many have aired")

        var gap = Self.watching(upNext: 4, watched: 11, aired: 12)
        gap.firstGapEpisode = 4
        XCTAssertEqual(line(gap), "11 of 12 · E4 not watched", "AN-200")

        var caughtUp = AnimeState(status: "In progress", caughtUp: true, watchedCount: 7, airedCount: 7)
        caughtUp.nextAiring = AnimeAiring(episodeNumber: 8, airDate: "2026-10-29")
        XCTAssertEqual(line(caughtUp), "Caught up · E8 on Thu", "Within six days, the weekday")
        caughtUp.nextAiring = AnimeAiring(episodeNumber: 8, airDate: "2026-11-05")
        XCTAssertEqual(line(caughtUp), "Caught up · E8 on Nov 5", "After that, the date")
        caughtUp.nextAiring = AnimeAiring(episodeNumber: 8, airDate: nil)
        XCTAssertEqual(line(caughtUp), "Caught up", "With no date")
        caughtUp.nextAiring = nil
        XCTAssertEqual(line(caughtUp), "Caught up")

        var rewatching = Self.watching(upNext: 3, watched: 2, aired: 24)
        rewatching.rewatch = AnimeRewatch(startedAt: "2026-10-01", passNumber: 2)
        XCTAssertEqual(line(rewatching), "Rewatching · E3 next")

        var planning = AnimeState(status: "Planning")
        XCTAssertNil(line(planning), "Nothing to say with no premiere")
        planning.premiere = AnimeAiring(airDate: "2027-03-12")
        XCTAssertEqual(line(planning), "Premieres Mar 12")

        var paused = Self.watching(upNext: 8, watched: 7, aired: 24)
        paused.status = "Paused"
        XCTAssertEqual(line(paused), "Paused at E8")
        paused.watchedCount = 0
        XCTAssertEqual(line(paused), "Paused", "With nothing watched")

        var dropped = Self.watching(upNext: 8, watched: 7, aired: 24)
        dropped.status = "Dropped"
        XCTAssertEqual(line(dropped), "Stopped at E8")
        dropped.watchedCount = 0
        XCTAssertEqual(line(dropped), "Dropped")

        XCTAssertNil(line(AnimeState(status: "Completed", finished: true)), "Completed: nothing extra")
        XCTAssertEqual(AnimeLibraryCard.line(for: Self.card(mediaId: "1", title: "T", anime: AnimeState(status: "Completed", finished: true))), "Completed")
    }

    func testAN700TheBadgeNamesUpNextOnceSomethingIsWatched() {
        func badge(_ anime: AnimeState) -> String? {
            LibraryPosterBadge.gridText(for: Self.card(mediaId: "1", title: "T", anime: anime))
        }
        XCTAssertEqual(badge(Self.watching(upNext: 8, watched: 7, aired: 24)), "E8")
        XCTAssertNil(badge(Self.watching(upNext: 1, watched: 0, aired: 24)), "Nothing watched in this pass")
        var paused = Self.watching(upNext: 8, watched: 7, aired: 24)
        paused.status = "Paused"
        XCTAssertEqual(badge(paused), "E8")
        paused.status = "Dropped"
        XCTAssertEqual(badge(paused), "E8")
        paused.status = "Planning"
        XCTAssertNil(badge(paused))
        paused.status = "Completed"
        XCTAssertNil(badge(paused))
        var caughtUp = Self.watching(upNext: 8, watched: 7, aired: 24)
        caughtUp.caughtUp = true
        XCTAssertNil(badge(caughtUp))
        var rewatching = Self.watching(upNext: 3, watched: 2, aired: 24)
        rewatching.rewatch = AnimeRewatch()
        XCTAssertEqual(badge(rewatching), "E3")
    }

    func testAN701TheCardReadsAsTVsDoes() {
        let card = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        XCTAssertEqual(InProgressCardPresentation.accessibilityLabel(for: card), "Attack on Titan, update progress, E8 next, 7 of 24")
        XCTAssertEqual(InProgressCardPresentation.progressFraction(for: card) ?? 0, 7.0 / 24.0, accuracy: 0.0001)
        var noCount = Self.watching(upNext: 8, watched: 7, aired: 24)
        noCount.airedCount = nil
        XCTAssertNil(InProgressCardPresentation.progressFraction(for: Self.card(mediaId: "1", title: "T", anime: noCount)), "The bar needs a known aired count")
        XCTAssertNil(InProgressCardPresentation.progressFraction(for: Self.card(mediaId: "1", title: "T", anime: Self.watching(upNext: 1, watched: 0, aired: 24))))
    }

    func testAN702ThePagesLines() {
        XCTAssertEqual(AnimeTrackingPresentation.pageStatus(for: Self.watching(upNext: 8, watched: 7, aired: 24)), "Watching")
        XCTAssertEqual(AnimeTrackingPresentation.pageLines(for: Self.watching(upNext: 8, watched: 7, aired: 24)), ["7 of 24 watched"])
        var gap = Self.watching(upNext: 4, watched: 11, aired: 12)
        gap.firstGapEpisode = 4
        XCTAssertEqual(AnimeTrackingPresentation.pageLines(for: gap), ["11 of 12 · E4 not watched"])
        var rewatching = Self.watching(upNext: 3, watched: 2, aired: 24)
        rewatching.rewatch = AnimeRewatch()
        XCTAssertEqual(AnimeTrackingPresentation.pageLines(for: rewatching), ["Rewatching · E3 next", "2 of 24 watched"])
        XCTAssertEqual(AnimeTrackingPresentation.pageLines(for: AnimeState(status: "Completed", started: true, finished: true, watchedCount: 12, airedCount: 12)), [], "Not in the library")
        XCTAssertNil(AnimeTrackingPresentation.pageStatus(for: AnimeState()), "Not in the library")
        XCTAssertEqual(AnimeTrackingPresentation.upNextLabel(for: TVEpisodePointer(seasonNumber: 1, episodeNumber: 2)), "Up next")
        XCTAssertEqual(AnimeTrackingPresentation.upNextLabel(for: TVEpisodePointer(seasonNumber: 1, episodeNumber: 2, isNew: true)), "New episode")
    }

    func testAN702TheTrackingSectionReadsTheTitlesState() {
        let state = UserMediaState(
            isTracked: true,
            status: "In progress",
            progress: ProgressState(kind: "episodes", value: 7, max: 24, unit: "episode"),
            anime: Self.watching(upNext: 8, watched: 7, aired: 24)
        )
        let detail = MediaDetail(ref: Self.ref(), title: "Attack on Titan", userState: state)
        XCTAssertEqual(TrackingSummarySection.lines(detail: detail, state: state, preferredMode: nil), ["7 of 24 watched"], "Not the row's stored number")
        XCTAssertEqual(TrackingSummarySection.tvUpNext(detail: detail, state: state)?.episodeNumber, 8)
        XCTAssertFalse(TrackingSummarySection.showsUpdateProgress(ref: detail.ref, status: "In progress", state: state), "The rows are its progress UI")

        // Not in the library yet: the list has every row, and there's no Up next row above it.
        let untracked = UserMediaState(isTracked: false, anime: AnimeState(upNext: AnimeEpisodePointer(episodeNumber: 1)))
        XCTAssertNil(TrackingSummarySection.tvUpNext(detail: detail, state: untracked))
        XCTAssertEqual(TrackingSummarySection.lines(detail: detail, state: untracked, preferredMode: nil), [])

        // A single has no number anywhere (AN-002).
        let single = UserMediaState(
            isTracked: true,
            status: "Completed",
            progress: ProgressState(kind: "episodes", value: 1, max: 1, unit: "episode"),
            anime: AnimeState(inLibrary: true, status: "Completed", started: true, finished: true, single: true, watchedCount: 1, airedCount: 1)
        )
        XCTAssertEqual(TrackingSummarySection.lines(detail: detail, state: single, preferredMode: nil), [])
    }

    // MARK: Home's sheet (AN-701, TV-701)

    func testAN701TheLineOpensTheSheetOnUpNextsRowAtOnceThenEveryRow() async throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24, upNextTitle: "Small Blade", upNextImage: "https://img/8.jpg"))
        XCTAssertEqual(InProgressUpdates.route(for: item), .episodes)
        let ticks = continueWatching()
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.rowsGate = gate
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 8, watched: 7, aired: 24), rows: Self.rows(through: 24, watched: 7))

        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        XCTAssertNil(ticks.episodes, "Not a show's sheet")
        XCTAssertEqual(sheet.title, "Attack on Titan", "Titled with the title")
        XCTAssertEqual(sheet.upNext?.episodeNumber, 8)
        XCTAssertEqual(sheet.upNext?.title, "Small Blade", "Up next's row shows at once, from the card")
        XCTAssertEqual(sheet.upNext?.imageUrl, "https://img/8.jpg")
        XCTAssertNil(sheet.rows)
        XCTAssertEqual(sheet.startEpisode, 8, "The list opens on up next")
        XCTAssertEqual(sheet.listID, "mal:anime:16498:_:_-up-next")
        XCTAssertFalse(sheet.canLog, "Log… waits for the rows' composer")
        XCTAssertEqual(sheet.page.changeOrigin, ticks.changeOrigin, "Its writes post as Home's own, so the card stays put")
        XCTAssertTrue(sheet.offersOpenPage)

        let load = Task { await sheet.loadRowsIfNeeded() }
        await gate.open()
        await load.value
        XCTAssertEqual(tracking.rowsRequests, [item.media.ref], "Its own route, not the page")
        XCTAssertTrue(media.detailRequests.isEmpty)
        XCTAssertEqual(sheet.rows?.episodes.count, 24)
        XCTAssertEqual(sheet.listID, "mal:anime:16498:_:_", "A new list: the sheet asks for up next's place again")
        XCTAssertEqual(sheet.startEpisode, 8)

        // A reopen is a new sheet, on up next again.
        ticks.animeEpisodes = nil
        _ = ticks.animeSheetDidClose()
        ticks.openEpisodes(for: item)
        let reopened = try XCTUnwrap(ticks.animeEpisodes)
        XCTAssertFalse(reopened === sheet)
        XCTAssertEqual(reopened.startEpisode, 8)
    }

    func testAN701OnlyATitleWithAnUpNextOpensTheSheet() {
        let ticks = continueWatching()
        let caughtUp = Self.card(mediaId: "1", title: "Caught Up", anime: AnimeState(status: "In progress", caughtUp: true))
        XCTAssertEqual(InProgressUpdates.route(for: caughtUp), .page)
        ticks.openEpisodes(for: caughtUp)
        XCTAssertNil(ticks.animeEpisodes)
    }

    func testAN701ATickBeforeTheRowsLoadStaysAndTheListStaysPut() async throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        let ticks = continueWatching()
        var cards: [AnimeState] = []
        ticks.onAnimeUpdate = { _, anime in cards.append(anime) }
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        let upNext = try XCTUnwrap(sheet.upNext)
        let row = TVEpisodeRowModel.upNext(upNext)
        let state = try XCTUnwrap(row.tv)

        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 9, watched: 8, aired: 24),
            row: AnimeRow(number: 8, aired: 1, watched: 1, viewingId: 70)
        )]
        sheet.toggle(state)
        XCTAssertTrue(sheet.page.tvIsWatched(state), "It shows at once")
        let settled = await sheet.page.settled()
        XCTAssertTrue(settled)
        XCTAssertTrue(sheet.page.tvIsWatched(state), "The answer stays until the rows arrive")
        XCTAssertEqual(tracking.actions.first?.request.episodeNumber, 8)
        XCTAssertEqual(cards.last?.upNext?.episodeNumber, 9, "The card changes where it is")

        var answered = Self.rows(through: 24, watched: 8)
        answered.upNext = 9
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 9, watched: 8, aired: 24), rows: answered)
        await sheet.loadRowsIfNeeded()
        XCTAssertEqual(sheet.startEpisode, 8, "The list opens where it showed up next: a tick doesn't move it")
        XCTAssertEqual(sheet.page.tvConfirmed, TVConfirmedWrites(), "The rows take over")
        XCTAssertEqual(sheet.rows?.episodes[7].viewingId, 8, "Every row now")
    }

    func testAN701RowsThatMovedElsewhereOpenOnTheServersUpNext() async throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        let ticks = continueWatching()
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 10, watched: 9, aired: 24), rows: Self.rows(through: 24, watched: 9))
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        await sheet.loadRowsIfNeeded()
        XCTAssertEqual(sheet.startEpisode, 10)
    }

    func testAN501TheLastTickOpensTheLogScreenThenTheSheetMovesOnWithNothingAdded() async throws {
        let season2 = Self.card(mediaId: "25777", title: "Attack on Titan Season 2", anime: Self.watching(upNext: 12, watched: 11, aired: 12))
        let ticks = continueWatching()
        var cards: [AnimeState] = []
        var written: [MediaRef] = []
        ticks.onAnimeUpdate = { ref, anime in
            written.append(ref)
            cards.append(anime)
        }
        tracking.rowsResponses[season2.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 12, watched: 11, aired: 12), rows: Self.rows(through: 12, watched: 11))
        ticks.openEpisodes(for: season2)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        await sheet.loadRowsIfNeeded()
        let last = try XCTUnwrap(sheet.rows?.episodes.last)

        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "Season 2", date: "2026-10-25")])
        var finished = AnimeState(version: 5, inLibrary: true, status: "Completed", finished: true, watchedCount: 12, airedCount: 12)
        finished.nextTitle = AnimeNextTitle(mediaId: "35760", title: "Attack on Titan Season 3", imageUrl: "https://img/s3.jpg", hasAired: true)
        tracking.actionResponses = [Self.response(
            anime: finished,
            row: AnimeRow(number: 12, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: last))
        let settled = await sheet.page.settled()
        XCTAssertTrue(settled)
        let follow = sheet.page.tvFollowUp
        XCTAssertNotNil(follow.composer, "The log screen opens over the sheet at once")
        XCTAssertEqual(cards.last?.status, "Completed", "The card changes where it is")

        // Not now: the log screen's cover closes, and that alone moves the sheet on.
        follow.composerDismissed()
        XCTAssertEqual(sheet.ref.mediaId, "35760", "The sheet shows the next title")
        XCTAssertEqual(sheet.title, "Attack on Titan Season 3")
        XCTAssertNil(sheet.rows, "Its rows load next")
        XCTAssertEqual(sheet.listID, "mal:anime:35760:_:_-up-next")
        XCTAssertEqual(sheet.page.ref.mediaId, "35760")
        XCTAssertEqual(sheet.page.changeOrigin, ticks.changeOrigin)
        XCTAssertEqual(tracking.actions.count, 1, "Moving on writes nothing")

        tracking.rowsResponses[sheet.ref.id] = AnimeRowsResponse(anime: AnimeState(single: false, watchedCount: 0, airedCount: 10, upNext: AnimeEpisodePointer(episodeNumber: 1)), rows: Self.rows(through: 10))
        await sheet.loadRowsIfNeeded()
        XCTAssertEqual(tracking.rowsRequests.last, sheet.ref)
        XCTAssertEqual(tracking.actions.count, 1, "Loading its rows adds nothing to the library")
        XCTAssertEqual(sheet.page.detail?.userState?.isTracked, false)
        XCTAssertEqual(sheet.startEpisode, 1, "Up next at the top")

        // The first tick there is an ordinary first tick, and the card it didn't open from stays as it was.
        let cardCount = cards.count
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 2, watched: 1, aired: 10),
            row: AnimeRow(number: 1, aired: 1, watched: 1, viewingId: 10)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.first)))
        let ticked = await sheet.page.settled()
        XCTAssertTrue(ticked)
        XCTAssertEqual(tracking.actions.last?.mediaId, "35760")
        XCTAssertEqual(tracking.actions.last?.action, .tick)
        XCTAssertEqual(cards.count, cardCount + 1, "Its card, if the rail has one, changes where it is (TV-701)")
        XCTAssertEqual(written.last?.mediaId, "35760", "The title that was written, not the one the sheet opened on")
        XCTAssertTrue(ticks.needsReload, "The rail picks it up on its next visit")
    }

    func testAN501TheSheetStaysPutWithoutANextTitleThatHasAired() async throws {
        for next in [nil, AnimeNextTitle(mediaId: "35760", title: "Season 3", hasAired: false)] {
            let item = Self.card(mediaId: "25777", title: "Season 2", anime: Self.watching(upNext: 12, watched: 11, aired: 12))
            let ticks = continueWatching()
            tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 12, watched: 11, aired: 12), rows: Self.rows(through: 12, watched: 11))
            ticks.openEpisodes(for: item)
            let sheet = try XCTUnwrap(ticks.animeEpisodes)
            await sheet.loadRowsIfNeeded()
            let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "Season 2", date: "2026-10-25")])
            var finished = AnimeState(inLibrary: true, status: "Completed", finished: true)
            finished.nextTitle = next
            tracking.actionResponses = [Self.response(
                anime: finished,
                row: AnimeRow(number: 12, aired: 1, watched: 1, viewingId: 9),
                finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
            )]
            sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last)))
            let settled = await sheet.page.settled()
            XCTAssertTrue(settled)
            sheet.page.tvFollowUp.composerDismissed()
            XCTAssertEqual(sheet.ref.mediaId, "25777")
            XCTAssertEqual(sheet.rows?.episodes.filter(\.isWatched).count, 12, "Every tick filled")
        }
    }

    func testAN501TheSheetStaysPutOnANextTitleThatIsASingle() async throws {
        // A season whose sequel is a movie: there are no rows to move on to, and the page's Next: row still shows it.
        let item = Self.card(mediaId: "38000", title: "Season 1", anime: Self.watching(upNext: 26, watched: 25, aired: 26))
        let ticks = continueWatching()
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 26, watched: 25, aired: 26), rows: Self.rows(through: 26, watched: 25))
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        await sheet.loadRowsIfNeeded()
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "Season 1", date: "2026-10-25")])
        var finished = AnimeState(inLibrary: true, status: "Completed", finished: true)
        finished.nextTitle = AnimeNextTitle(mediaId: "40456", title: "The Movie", hasAired: true, single: true)
        tracking.actionResponses = [Self.response(
            anime: finished,
            row: AnimeRow(number: 26, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last)))
        _ = await sheet.page.settled()
        sheet.page.tvFollowUp.composerDismissed()
        XCTAssertEqual(sheet.ref.mediaId, "38000", "It stays on the finished title")
        XCTAssertEqual(sheet.title, "Season 1")
        XCTAssertEqual(sheet.rows?.episodes.filter(\.isWatched).count, 26, "Every tick filled")
        XCTAssertEqual(sheet.page.viewer?.anime?.nextTitle?.title, "The Movie", "The page's Next: row reads this")
    }

    func testAN501ThePagesSheetNeverMovesOn() async throws {
        // The last tick in the page's own sheet opens the log screen, and closing it leaves the sheet where it is.
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "T", date: "2026-10-25")])
        var finished = AnimeState(inLibrary: true, status: "Completed", finished: true)
        finished.nextTitle = AnimeNextTitle(mediaId: "35760", title: "Season 3", hasAired: true)
        let page = model(Self.detail(anime: Self.watching(upNext: 60, watched: 59, aired: 60), rows: Self.rows(through: 60, watched: 59)))
        let sheet = AnimeEpisodes(page: page)
        XCTAssertFalse(sheet.offersOpenPage, "It's over the page already")
        tracking.actionResponses = [Self.response(
            anime: finished,
            row: AnimeRow(number: 60, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last)))
        _ = await page.settled()
        XCTAssertNotNil(page.tvFollowUp.composer)
        page.tvFollowUp.composerDismissed()
        XCTAssertEqual(sheet.ref.mediaId, "16498")
        XCTAssertTrue(sheet.page === page)
    }

    func testAN101ThePagesSheetStartsFromTheRowsItHas() async throws {
        let rows = Self.rows(through: 1_189, watched: 800)
        let page = model(Self.detail(anime: Self.watching(upNext: 801, watched: 800, aired: 1_189), rows: rows))
        let sheet = AnimeEpisodes(page: page)
        XCTAssertEqual(sheet.startEpisode, 801)
        XCTAssertEqual(sheet.rows?.episodes.count, 1_189)
        await sheet.loadRowsIfNeeded()
        XCTAssertTrue(tracking.rowsRequests.isEmpty, "Nothing to load")
    }

    func testTV701ALastTickInThePagesSheetStillOpensItsLogScreenAfterTheSheetCloses() async throws {
        // Tick the last row and tap the sheet's close button before the answer. The server offers the log screen once.
        let rows = Self.rows(through: 60, watched: 59)
        let page = model(Self.detail(anime: Self.watching(upNext: 60, watched: 59, aired: 60), rows: rows))
        let sheet = AnimeEpisodes(page: page)
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "T", date: "2026-10-25")])
        tracking.actionResponses = [Self.response(
            anime: AnimeState(inLibrary: true, status: "Completed", finished: true),
            row: AnimeRow(number: 60, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last)))
        XCTAssertTrue(sheet.page === page, "A write in the sheet is the page's own")
        XCTAssertTrue(page.hasPendingWrites)

        await gate.open()
        _ = await page.settled()
        XCTAssertNotNil(page.tvFollowUp.composer, "The page's screen presents it once the sheet has gone")
        XCTAssertEqual(page.detail?.animeRows?.episodes.last?.isWatched, true, "The page's row is up to date, with no refresh")
        XCTAssertEqual(page.viewer?.status, "Completed")
        XCTAssertTrue(media.detailRequests.isEmpty)
    }

    func testTV705AFailedTickInThePagesSheetSaysSoOnThePage() async throws {
        let page = model(Self.detail(anime: Self.watching(upNext: 60, watched: 59, aired: 60), rows: Self.rows(through: 60, watched: 59)))
        let sheet = AnimeEpisodes(page: page)
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        tracking.actionError = APIError.httpStatus(503, #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"Anime details are unavailable right now"}}"#)
        let last = try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last))
        sheet.toggle(last)
        XCTAssertEqual(page.viewer?.tvIsWatched(last), true, "It shows at once")
        await gate.open()
        let succeeded = await page.settled()
        XCTAssertFalse(succeeded)
        XCTAssertEqual(page.quickActionErrorMessage, "Anime details are unavailable right now", "The page's alert has it")
        XCTAssertEqual(page.viewer?.tvIsWatched(last), false, "The row went back, on the page too")
    }

    func testTV705AllEpisodesOpensWithATickStillOnItsWay() async throws {
        // Tick E801 in the page's list, then tap All Episodes before the answer.
        let rows = Self.rows(through: 1_189, watched: 800)
        let page = model(Self.detail(anime: Self.watching(upNext: 801, watched: 800, aired: 1_189), rows: rows))
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 802, watched: 801, aired: 1_189),
            row: AnimeRow(number: 801, aired: 1, watched: 1, viewingId: 801)
        )]
        let e801 = rows.state(of: rows.episodes[800])
        page.toggleTVEpisode(e801)

        let sheet = AnimeEpisodes(page: page)
        XCTAssertEqual(sheet.page.viewer?.tvIsWatched(e801), true, "It opens showing the tick")
        XCTAssertEqual(sheet.startEpisode, 801, "On the row it opened for")

        await gate.open()
        _ = await page.settled()
        XCTAssertEqual(sheet.rows?.episodes[800].isWatched, true, "And then the answer, with nothing to reload")
        XCTAssertEqual(sheet.page.viewer?.anime?.upNext?.episodeNumber, 802)
        XCTAssertTrue(tracking.rowsRequests.isEmpty)
    }

    func testAN203LogWaitsAndOpensOneScreenOnly() async throws {
        // Tick the last row, then choose Log before the answer: the last tick's log screen opens, and Log stays closed.
        let rows = Self.rows(through: 12, watched: 11)
        let page = model(Self.detail(anime: Self.watching(upNext: 12, watched: 11, aired: 12), rows: rows))
        let sheet = AnimeEpisodes(page: page)
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "T", date: "2026-10-25")])
        tracking.actionResponses = [Self.response(
            anime: AnimeState(inLibrary: true, status: "Completed", finished: true),
            row: AnimeRow(number: 12, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(rows.state(of: rows.episodes[11]))
        let log = Task { await sheet.readyForLog() }
        await gate.open()
        let opens = await log.value
        XCTAssertFalse(opens, "One screen only")
        XCTAssertNotNil(page.tvFollowUp.composer, "The one the last tick asked for")

        // With nothing on its way, Log opens.
        page.tvFollowUp.composerDismissed()
        let opensNow = await sheet.readyForLog()
        XCTAssertTrue(opensNow)

        // A write that fails says so, and Log stays closed: the tap came from a state that never landed.
        tracking.actionError = APIError.httpStatus(503, nil)
        page.rateTV(steps: 8)
        let afterFailure = await sheet.readyForLog()
        XCTAssertFalse(afterFailure)
    }

    func testTV705AFailedTickInTheSheetRestoresTheRowAndSaysSo() async throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        let ticks = continueWatching()
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 8, watched: 7, aired: 24), rows: Self.rows(through: 24, watched: 7))
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        await sheet.loadRowsIfNeeded()
        let rows = try XCTUnwrap(sheet.rows)
        let e8 = rows.state(of: rows.episodes[7])
        tracking.actionError = APIError.httpStatus(503, #"{"error":{"message":"Anime details are unavailable right now"}}"#)
        sheet.toggle(e8)
        XCTAssertTrue(sheet.page.tvIsWatched(e8))
        let settled = await sheet.page.settled()
        XCTAssertFalse(settled)
        XCTAssertFalse(sheet.page.tvIsWatched(e8))
        XCTAssertEqual(sheet.failureTitle, ContinueWatchingEpisodes.tickFailureTitle)
        XCTAssertEqual(sheet.page.quickActionErrorMessage, "Anime details are unavailable right now")
    }

    func testAN701ASheetClosedWhileItsTickIsOnItsWayStillOpensItsLogScreen() async throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 12, watched: 11, aired: 12))
        let ticks = continueWatching()
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 12, watched: 11, aired: 12), rows: Self.rows(through: 12, watched: 11))
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        await sheet.loadRowsIfNeeded()
        let gate = RefreshGate()
        heldGates.append(gate)
        tracking.actionGate = gate
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "T", date: "2026-10-25")])
        tracking.actionResponses = [Self.response(
            anime: AnimeState(inLibrary: true, status: "Completed", finished: true),
            row: AnimeRow(number: 12, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last)))
        ticks.animeEpisodes = nil
        XCTAssertNil(ticks.animeSheetDidClose())
        XCTAssertTrue(ticks.closedAnimeEpisodes === sheet)
        await gate.open()
        let settled = await sheet.page.settled()
        XCTAssertTrue(settled)
        XCTAssertNotNil(sheet.page.tvFollowUp.composer, "It shows over the screen")
    }

    func testAN701OpenAnimeClosesTheSheetFirst() throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        let ticks = continueWatching()
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        sheet.openPage()
        XCTAssertNil(ticks.animeEpisodes, "Its sheet closes")
        let selection = try XCTUnwrap(ticks.animeSheetDidClose(), "Then the page opens")
        XCTAssertEqual(selection.ref.id, item.media.ref.id)
    }

    func testAN701TheRailKeepsACardsPlaceAfterAWrite() {
        let first = Self.card(mediaId: "1", title: "One", anime: Self.watching(upNext: 2, watched: 1, aired: 12))
        let second = Self.card(mediaId: "2", title: "Two", anime: Self.watching(upNext: 5, watched: 4, aired: 12))
        let replaced = ContinueWatchingTicks.replacingAnime(Self.watching(upNext: 6, watched: 5, aired: 12), ref: second.media.ref, in: [first, second])
        XCTAssertEqual(replaced.map(\.media.ref.mediaId), ["1", "2"])
        XCTAssertEqual(replaced[1].tracking.anime?.upNext?.episodeNumber, 6)
        XCTAssertEqual(replaced[0].tracking.anime?.upNext?.episodeNumber, 2)

        let noCard = ContinueWatchingTicks.replacingAnime(Self.watching(upNext: 9, watched: 8, aired: 12), ref: Self.ref("3"), in: [first, second])
        XCTAssertEqual(noCard.map { $0.tracking.anime?.upNext?.episodeNumber }, [2, 5], "A title with no card changes nothing")
    }

    func testTV701ASecondTitleOnTheRailMovesWhenTheSheetWritesIt() async throws {
        // The rail has Season 1 and Season 2. Season 1's sheet moves on to Season 2, and a tick there shows on its card.
        let season1 = Self.card(mediaId: "16498", title: "Season 1", anime: Self.watching(upNext: 12, watched: 11, aired: 12))
        let season2 = Self.card(mediaId: "25777", title: "Season 2", anime: Self.watching(upNext: 3, watched: 2, aired: 12))
        var rail = [season1, season2]
        let ticks = continueWatching()
        ticks.onAnimeUpdate = { ref, anime in rail = ContinueWatchingTicks.replacingAnime(anime, ref: ref, in: rail) }
        tracking.rowsResponses[season1.media.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 12, watched: 11, aired: 12), rows: Self.rows(through: 12, watched: 11))
        ticks.openEpisodes(for: season1)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        await sheet.loadRowsIfNeeded()
        var finished = AnimeState(version: 5, inLibrary: true, status: "Completed", finished: true, watchedCount: 12, airedCount: 12)
        finished.nextTitle = AnimeNextTitle(mediaId: "25777", title: "Season 2", hasAired: true)
        let composer = TVComposer(defaultTarget: "title", targets: [TVComposerTarget(id: "title", level: .title, title: "Season 1", date: "2026-10-25")])
        tracking.actionResponses = [Self.response(
            anime: finished,
            row: AnimeRow(number: 12, aired: 1, watched: 1, viewingId: 9),
            finished: TVFinished(level: .title, seasonNumber: nil, composer: composer)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes.last)))
        _ = await sheet.page.settled()
        XCTAssertEqual(rail[0].tracking.anime?.status, "Completed", "Its own card")

        sheet.page.tvFollowUp.composerDismissed()
        XCTAssertEqual(sheet.ref.mediaId, "25777")
        tracking.rowsResponses[sheet.ref.id] = AnimeRowsResponse(anime: Self.watching(upNext: 3, watched: 2, aired: 12), rows: Self.rows(through: 12, watched: 2))
        await sheet.loadRowsIfNeeded()
        tracking.actionResponses = [Self.response(
            anime: Self.watching(upNext: 4, watched: 3, aired: 12),
            row: AnimeRow(number: 3, aired: 1, watched: 1, viewingId: 10)
        )]
        sheet.toggle(try XCTUnwrap(sheet.rows).state(of: try XCTUnwrap(sheet.rows?.episodes[2])))
        _ = await sheet.page.settled()
        XCTAssertEqual(rail[1].tracking.anime?.upNext?.episodeNumber, 4, "Season 2's bar, badge and VoiceOver line")
        XCTAssertEqual(rail[1].tracking.anime?.watchedCount, 3)
        XCTAssertEqual(InProgressCardPresentation.progressFraction(for: rail[1]), 3.0 / 12.0)
    }

    // MARK: Projections

    func testProjectionsShowAtOnceAndNeverReachConfirmedState() {
        let watching = UserMediaState(isTracked: true, status: "In progress", anime: Self.watching(upNext: 3, watched: 2, aired: 12))

        var dropped = watching
        TrackingProjection.animeChoice(status: "Dropped")(&dropped)
        XCTAssertEqual(dropped.status, "Dropped")
        XCTAssertEqual(dropped.anime?.selectedAction, "drop")
        XCTAssertEqual(watching.status, "In progress")

        var resumed = dropped
        TrackingProjection.animeTick(episode: 3)(&resumed)
        XCTAssertEqual(resumed.status, "In progress", "A tick ends a choice")
        XCTAssertEqual(resumed.anime?.selectedAction, "watching")
        XCTAssertEqual(resumed.anime?.watchedCount, 3)

        var finishedDropped = dropped
        finishedDropped.anime?.finished = true
        TrackingProjection.animeChoice(status: "In progress")(&finishedDropped)
        XCTAssertEqual(finishedDropped.status, "Completed", "Watching on a finished title reads Completed")

        var marked = UserMediaState(isTracked: false, anime: AnimeState(watchedCount: 0, airedCount: 12, upNext: AnimeEpisodePointer(episodeNumber: 1), eye: TVEyeState(filled: false, tap: .mark, count: 12)))
        TrackingProjection.animeMarked(&marked)
        XCTAssertTrue(marked.isTracked)
        XCTAssertEqual(marked.status, "Completed")
        XCTAssertEqual(marked.anime?.watchedCount, 12)
        XCTAssertNil(marked.anime?.upNext)
        XCTAssertEqual(marked.anime?.eye?.filled, true)
        XCTAssertTrue(marked.tvIsWatched(TVEpisodeState(seasonNumber: AnimeRows.seasonNumber, episodeNumber: 7)))

        var airing = UserMediaState(isTracked: false, anime: AnimeState(nextAiring: AnimeAiring(episodeNumber: 8, airDate: "2026-10-29")))
        TrackingProjection.animeMarked(&airing)
        XCTAssertEqual(airing.status, "In progress", "Watching as caught up")
        XCTAssertEqual(airing.anime?.caughtUp, true)
    }

    func testAN301TheEyeUndoingAHeartsMarkTakesTheHeartOffTheScreen() async throws {
        // Planning, nothing watched. The heart marks it; the filled eye undoes the mark and the choice comes back.
        var planning = AnimeState(version: 1, inLibrary: true, status: "Planning", choice: "Planning", watchedCount: 0, airedCount: 12)
        planning.opinionAction = .mark
        let page = model(Self.detail(anime: planning))
        var completed = AnimeState(version: 2, inLibrary: true, status: "Completed", started: true, finished: true, watchedCount: 12, airedCount: 12)
        completed.liked = true
        completed.eye = TVEyeState(filled: true, tap: .unmarkMarks)
        media.likeResponse = MediaLikeResponse(liked: true, media: nil, anime: completed)
        _ = await page.toggleMediaLike(for: try XCTUnwrap(page.detail)).value
        XCTAssertEqual(page.viewer?.hasLiked, true)
        XCTAssertEqual(page.viewer?.status, "Completed")

        var backToPlanning = planning
        backToPlanning.version = 3
        backToPlanning.liked = false
        tracking.actionResponses = [Self.response(anime: backToPlanning)]
        page.tapTVEye()
        _ = await page.settled()
        XCTAssertEqual(page.viewer?.status, "Planning")
        XCTAssertNil(page.viewer?.rating)
        XCTAssertEqual(page.viewer?.hasLiked, false, "The heart went with it, on screen too")

        // So the next tap gives the heart rather than taking it away.
        media.likeResponse = nil
        _ = await page.toggleMediaLike(for: try XCTUnwrap(page.detail)).value
        XCTAssertEqual(media.likeRequests.last?.liked, true)
    }

    func testAN301TheRowsRouteBringsTheHeartHomesSheetCantKnow() async throws {
        let item = Self.card(mediaId: "16498", title: "Attack on Titan", anime: Self.watching(upNext: 8, watched: 7, aired: 24))
        let ticks = continueWatching()
        var loaded = Self.watching(upNext: 8, watched: 7, aired: 24)
        loaded.liked = true
        tracking.rowsResponses[item.media.ref.id] = AnimeRowsResponse(anime: loaded, rows: Self.rows(through: 24, watched: 7))
        ticks.openEpisodes(for: item)
        let sheet = try XCTUnwrap(ticks.animeEpisodes)
        XCTAssertEqual(sheet.page.viewer?.hasLiked, false)
        await sheet.loadRowsIfNeeded()
        XCTAssertEqual(sheet.page.viewer?.hasLiked, true)
    }

    func testAStartReadsTheDayOnTheUsersCalendar() {
        // A tick at 8:30 p.m. New York on Nov 3 is Nov 4 in UTC.
        XCTAssertEqual(TVTrackingPresentation.startDay("2026-11-04T01:30:00Z", calendar: Self.calendar), "2026-11-03")
        XCTAssertEqual(TVTrackingPresentation.startDay("2026-11-04T01:30:00.123456Z", calendar: Self.calendar), "2026-11-03")
        XCTAssertEqual(TVTrackingPresentation.startDay("2026-11-03T20:30:00-05:00", calendar: Self.calendar), "2026-11-03")
        // A log's day stays its day, at UTC midnight or plain, however a server in another zone writes it.
        XCTAssertEqual(TVTrackingPresentation.startDay("2026-11-04T00:00:00Z", calendar: Self.calendar), "2026-11-04")
        XCTAssertEqual(TVTrackingPresentation.startDay("2026-11-03T19:00:00-05:00", calendar: Self.calendar), "2026-11-04")
        XCTAssertEqual(TVTrackingPresentation.startDay("2026-11-04", calendar: Self.calendar), "2026-11-04")

        let state = UserMediaState(isTracked: true, status: "In progress", startDate: "2026-11-04T01:30:00Z", anime: Self.watching(upNext: 2, watched: 1, aired: 25))
        let anime = MediaDetail(ref: Self.ref(), title: "Attack on Titan", userState: state)
        XCTAssertTrue(TrackingSummarySection.lines(detail: anime, state: state, preferredMode: nil, calendar: Self.calendar).contains("Started November 3, 2026"))

        // TV reads through the same line.
        let showRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "tv", mediaId: "1399", seasonNumber: nil, episodeNumber: nil)
        let showState = UserMediaState(isTracked: true, status: "In progress", startDate: "2026-11-04T01:30:00Z", tv: TVShowState(inLibrary: true, status: "In progress"))
        let show = MediaDetail(ref: showRef, title: "A Show", userState: showState)
        XCTAssertTrue(TrackingSummarySection.lines(detail: show, state: showState, preferredMode: nil, calendar: Self.calendar).contains("Started November 3, 2026"))

        // A book's start is the date it was given.
        let bookRef = MediaRef(itemId: nil, source: "openlibrary", mediaType: "book", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        let bookState = UserMediaState(isTracked: true, status: "In progress", startDate: "2026-11-04T01:30:00Z")
        let book = MediaDetail(ref: bookRef, title: "A Book", userState: bookState)
        XCTAssertTrue(TrackingSummarySection.lines(detail: book, state: bookState, preferredMode: nil, calendar: Self.calendar).contains("Started November 4, 2026"))
    }

    func testAN500AWriteOnTheNextTitleRefreshesThisPage() async throws {
        var finished = AnimeState(version: 1, inLibrary: true, status: "Completed", finished: true)
        finished.nextTitle = AnimeNextTitle(mediaId: "25777", title: "Season 2", hasAired: true)
        let page = model(Self.detail(anime: finished), externalRefreshDelay: .zero)

        page.handleMediaStateChange(["ref": Self.ref("99999"), "origin": UUID()])
        await page.refreshFinished()
        XCTAssertTrue(media.detailRequests.isEmpty, "Another title's write says nothing about this page")

        page.handleMediaStateChange(["ref": Self.ref("25777"), "origin": UUID()])
        await page.refreshFinished()
        XCTAssertEqual(media.detailRequests.map(\.mediaId), ["16498"], "The page reads itself again, and Next: goes when that one's finished")
    }

    func testAServerOlderThanThisBuildLeavesThePageAsItWas() async throws {
        // No anime state: the page isn't tracked by it, its stars and heart take the plain routes, and its lines read the row.
        let state = UserMediaState(
            isTracked: true,
            status: "In progress",
            progress: ProgressState(kind: "episodes", value: 3, max: 12, unit: "episode")
        )
        let detail = MediaDetail(ref: Self.ref(), title: "Attack on Titan", userState: state)
        let page = model(detail)
        XCTAssertFalse(page.tracksAnime)
        XCTAssertNil(page.opinionAction)
        XCTAssertEqual(
            TrackingSummarySection.lines(detail: detail, state: state, preferredMode: nil),
            [try XCTUnwrap(state.progress?.detailDisplayText(preferredMode: nil))],
            "The row's progress, as before"
        )

        media.likeResponse = MediaLikeResponse(liked: true, media: nil)
        _ = await page.toggleMediaLike(for: detail).value
        XCTAssertEqual(media.likeRequests.count, 1)
        XCTAssertEqual(page.viewer?.hasLiked, true)
        tracking.updateResponse = TrackingState(trackingId: 3, status: "In progress", rating: "4.0", progress: nil, repeats: nil, startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
        _ = await page.setCurrentRating(for: detail, halfSteps: 8).value
        XCTAssertTrue(tracking.actions.isEmpty, "No anime route, which that server doesn't have")
        XCTAssertEqual(tracking.updates.count, 1)
        XCTAssertEqual(page.viewer?.rating, "4.0")

        // Home's card opens its page, and its Library line reads the status.
        let card = Spine.LibraryItem(
            media: MediaSummary(ref: Self.ref(), title: "Attack on Titan"),
            tracking: TrackingState(trackingId: 3, status: "In progress", rating: nil, progress: nil, repeats: nil, startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
        )
        XCTAssertEqual(InProgressUpdates.route(for: card), .page)
        XCTAssertEqual(AnimeLibraryCard.line(for: card), TVLibraryCard.statusTitle("In progress"))

        // With the state it's tracked by it again.
        XCTAssertTrue(model(Self.detail(anime: Self.watching(upNext: 2, watched: 1, aired: 12))).tracksAnime)
    }

    func testFoldingAWriteKeepsTheHeartAndLeavingTheLibraryClearsIt() {
        let ref = Self.ref()
        var current = UserMediaState(isTracked: true, status: "In progress", rating: "4.0", hasLiked: true, anime: Self.watching(upNext: 3, watched: 2, aired: 12))
        let tracking = TrackingState(trackingId: 3, status: "Completed", rating: "4.0", progress: nil, repeats: nil, startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
        current = MediaDetailViewModel.foldTV(
            TVActionResponse(tracking: tracking, envelope: TVWriteEnvelope(anime: AnimeState(status: "Completed"))),
            into: current,
            page: ref
        )
        XCTAssertEqual(current.status, "Completed")
        XCTAssertEqual(current.anime?.status, "Completed", "The envelope's state, not the row's list variant")
        XCTAssertTrue(current.hasLiked)

        let removed = MediaDetailViewModel.foldTV(
            TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(anime: AnimeState(inLibrary: false))),
            into: current,
            page: ref
        )
        XCTAssertFalse(removed.isTracked)
        XCTAssertNil(removed.status)
        XCTAssertNil(removed.rating, "Leaving the library clears stars and the heart (TV-208)")
        XCTAssertFalse(removed.hasLiked)
        XCTAssertEqual(removed.anime?.inLibrary, false, "It still has its state, for the page's menu")
    }

    // MARK: Fixtures

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private func model(_ detail: MediaDetail, externalRefreshDelay: Duration = .seconds(60)) -> MediaDetailViewModel {
        // Writes refresh their community stats; this holds them away from the fixtures.
        let page = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: media,
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            writeRefreshDelay: .seconds(60),
            externalRefreshDelay: externalRefreshDelay
        )
        page.detail = detail
        page.isLoading = false
        return page
    }

    private func continueWatching() -> ContinueWatchingTicks {
        ContinueWatchingTicks(
            mediaRepository: media,
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {}
        )
    }

    private func logModel(
        _ detail: MediaDetail,
        preselectedRatingSteps: Int? = nil,
        preselectedLiked: Bool? = nil,
        tvComposer: TVComposer? = nil,
        onResult: ((MediaLogSaveResult) -> Void)? = nil
    ) -> MediaLogViewModel {
        MediaLogViewModel(
            detail: detail,
            trackingRepository: tracking,
            diaryRepository: diary,
            preselectedLiked: preselectedLiked,
            preselectedRatingSteps: preselectedRatingSteps,
            onResult: onResult,
            onUnauthorized: {},
            onSaved: {},
            tvComposer: tvComposer
        )
    }

    private static var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        return calendar
    }

    private static func date(_ value: String) -> Date {
        TVTrackingPresentation.calendarDate(value, calendar: calendar)!
    }

    private static func ref(_ mediaId: String = "16498") -> MediaRef {
        MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: mediaId, seasonNumber: nil, episodeNumber: nil)
    }

    private static func watching(
        upNext: Int,
        watched: Int,
        aired: Int,
        opinion: OpinionAction? = nil,
        upNextTitle: String? = nil,
        upNextImage: String? = nil
    ) -> AnimeState {
        AnimeState(
            version: 1,
            inLibrary: true,
            status: "In progress",
            started: watched > 0,
            watchedCount: watched,
            airedCount: aired,
            upNext: AnimeEpisodePointer(episodeNumber: upNext, title: upNextTitle, imageUrl: upNextImage),
            selectedAction: "watching",
            opinionAction: opinion
        )
    }

    /// `watched` rows from the first are watched, each with a viewing named for its number.
    private static func rows(through count: Int, watched: Int = 0) -> AnimeRows {
        AnimeRows(
            upNext: watched < count ? watched + 1 : nil,
            firstUnwatched: watched < count ? watched + 1 : nil,
            logMessage: "Delete your log first.",
            episodes: (1...count).map { number in
                number <= watched
                    ? AnimeRow(number: number, aired: 1, watched: 1, viewingId: number)
                    : AnimeRow(number: number, aired: 1)
            }
        )
    }

    private static func detail(anime: AnimeState, rows: AnimeRows? = nil, tracked: Bool = true) -> MediaDetail {
        MediaDetail(
            ref: ref(),
            title: "Attack on Titan",
            userState: UserMediaState(isTracked: tracked, status: anime.status, anime: anime),
            animeRows: rows
        )
    }

    private static func card(mediaId: String, title: String, anime: AnimeState, updatedAt: String? = nil) -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(ref: ref(mediaId), title: title, imageUrl: "https://img/poster.jpg"),
            tracking: TrackingState(
                trackingId: 3,
                status: anime.status,
                rating: nil,
                progress: nil,
                repeats: nil,
                startDate: nil,
                endDate: nil,
                notes: nil,
                updatedAt: updatedAt,
                anime: anime
            )
        )
    }

    private static func response(
        anime: AnimeState,
        row: AnimeRow? = nil,
        rows: AnimeRows? = nil,
        finished: TVFinished? = nil,
        earlier: TVEarlierUnwatched? = nil,
        tracked: Bool = true,
        rating: String? = nil
    ) -> TVActionResponse {
        TVActionResponse(
            tracking: tracked
                ? TrackingState(trackingId: 3, status: anime.status, rating: rating, progress: nil, repeats: nil, startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
                : nil,
            envelope: TVWriteEnvelope(
                changed: true,
                finished: finished,
                earlierUnwatched: earlier,
                anime: anime,
                row: row,
                rows: rows
            )
        )
    }

    private static let stateJSON = """
    {"version": 1792954800000, "in_library": true, "status": "In progress", "choice": "Paused", "started": true,
     "finished": false, "caught_up": false, "single": false, "total": 12, "watched_count": 1, "aired_count": 7,
     "first_gap_episode": 4,
     "up_next": {"episode_number": 6, "title": "A title", "image_url": "https://img/6.jpg", "air_date": "2026-10-22", "is_new": true},
     "next_airing": {"episode_number": 8, "air_date": "2026-10-29"},
     "premiere": {"air_date": "2026-03-12"},
     "rewatch": {"started_at": "2026-10-01", "pass_number": 2},
     "last_watched_on": "2026-10-25", "selected_action": "watching", "remove_count": 1,
     "rating_source_diary_entry_id": 11, "like_source_diary_entry_id": 12, "like_is_independent": false,
     "eye": {"filled": false, "tap": "mark", "count": 6, "prompt": null, "message": null},
     "available_actions": ["pause", "drop", "log", "remove", "rate"],
     "action_reasons": {"planning": "You've started this anime. Pause or drop it instead.", "stop_rewatch": "Not rewatching."},
     "opinion_action": "log",
     "composer": {"default_target": "title",
                  "targets": [{"id": "title", "level": "title", "title": "Attack on Titan", "date": "2026-10-25",
                               "rating": null, "liked": false, "is_rewatch": false, "available": true, "reason": null}]},
     "next_title": {"source": "mal", "media_type": "anime", "media_id": "35760", "title": "Attack on Titan Season 3",
                    "image_url": "https://img/s3.jpg", "has_aired": true}}
    """
}

/// The stars on screen and the router a page's rail calls, recording what it opens.
@MainActor
final class OpinionFixture {
    struct LogRequest: Equatable {
        let ratingSteps: Int?
        let liked: Bool?
    }

    final class PickerBox {
        var picker = MediaRatingPickerState()
    }

    let box = PickerBox()
    var logs: [LogRequest] = []
    let page: MediaDetailViewModel

    init(page: MediaDetailViewModel) {
        self.page = page
        box.picker.syncConfirmed(page.viewer?.rating?.ratingHalfSteps ?? 0)
    }

    lazy var router = MediaOpinionRouter(
        page: page,
        picker: Binding(get: { [box] in box.picker }, set: { [box] in box.picker = $0 })
    ) { [unowned self] ratingSteps, liked in
        logs.append(LogRequest(ratingSteps: ratingSteps, liked: liked))
    }

    /// What the rail has done by the time it calls back: the stars are set on screen.
    func pick(halfSteps: Int) {
        box.picker.open()
        box.picker.draftHalfSteps = halfSteps
        box.picker.confirm()
    }
}

struct MediaLikeRequest: Equatable {
    let ref: MediaRef
    let liked: Bool
}

/// A tracking repository for anime's routes: it records what's sent and answers what a test scripted.
final class AnimeTrackingRepository: TrackingRepository {
    var actions: [(source: String, mediaId: String, action: TVAction, request: TVActionRequest)] = []
    var actionResponses: [TVActionResponse] = []
    var tvActions: [(source: String, mediaId: String, action: TVAction, request: TVActionRequest)] = []
    var tvActionResponses: [TVActionResponse] = []
    var actionError: Error?
    /// Holds each action's answer until it opens.
    var actionGate: RefreshGate?
    var rowsRequests: [MediaRef] = []
    var rowsResponses: [String: AnimeRowsResponse] = [:]
    /// Holds the rows route until it opens.
    var rowsGate: RefreshGate?
    var continueWatchingAnimeResponse: PagedResponse<Spine.LibraryItem>?
    var continueWatchingAnimeRequests = 0
    var listRequests: [String] = []
    /// The plain tracking route, which a server older than this build answers for anime.
    var updates: [TrackingWriteRequest] = []
    var updateResponse: TrackingState?

    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        tvActions.append((source, mediaId, action, request))
        return tvActionResponses.isEmpty ? TVActionResponse(tracking: nil, envelope: .empty) : tvActionResponses.removeFirst()
    }

    func performAnimeAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        actions.append((source, mediaId, action, request))
        await actionGate?.wait()
        if let actionError { throw actionError }
        return actionResponses.isEmpty ? TVActionResponse(tracking: nil, envelope: .empty) : actionResponses.removeFirst()
    }

    func animeRows(ref: MediaRef) async throws -> AnimeRowsResponse {
        rowsRequests.append(ref)
        await rowsGate?.wait()
        guard let response = rowsResponses[ref.id] else { throw APIError.httpStatus(503, nil) }
        return response
    }

    func continueWatchingAnime(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        continueWatchingAnimeRequests += 1
        return continueWatchingAnimeResponse ?? PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listRequests.append(mediaType)
        return PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func detail(ref: MediaRef) async throws -> TrackingState { throw APIError.httpStatus(404, nil) }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        updates.append(request)
        guard let updateResponse else { throw APIError.httpStatus(404, nil) }
        return updateResponse
    }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

/// A media repository whose hearts answer what a test scripted.
final class AnimeMediaRepository: MediaRepository {
    var detailRequests: [MediaRef] = []
    var likeRequests: [MediaLikeRequest] = []
    var likeResponse: MediaLikeResponse?

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailRequests.append(ref)
        return MediaDetail(ref: ref, title: "Attack on Titan")
    }

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        likeRequests.append(MediaLikeRequest(ref: ref, liked: liked))
        return likeResponse ?? MediaLikeResponse(liked: liked, media: nil)
    }

    func reviewPage(ref: MediaRef, page: String?) async throws -> PagedResponse<MediaReview> {
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }

    func meta() async throws -> MetaResponse { throw URLError(.notConnectedToInternet) }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}
