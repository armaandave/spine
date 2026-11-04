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
        XCTAssertEqual(TVDataURLProtocol.requests.last?.url?.path, "/api/v1/tracking/mal/anime/16498/actions/mark_earlier/")
        let body = try XCTUnwrap(TVDataURLProtocol.bodies.last)
        XCTAssertEqual((body["from"] as? [String: Any])?["episode_number"] as? Int, 1)
        XCTAssertEqual((body["to"] as? [String: Any])?["episode_number"] as? Int, 4)
        XCTAssertEqual(body["offer_log"] as? Bool, true)
        XCTAssertEqual(body["mutation_id"] as? String, id.uuidString)

        TVDataURLProtocol.response = Data(#"{"anime":{"status":null},"rows":{"count":0,"episodes":[]}}"#.utf8)
        let ref = MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "21", seasonNumber: nil, episodeNumber: nil)
        _ = try await repository.animeRows(ref: ref)
        XCTAssertEqual(TVDataURLProtocol.requests.last?.url?.path, "/api/v1/tracking/mal/anime/21/rows/")

        TVDataURLProtocol.response = Data(#"{"count":0,"next":null,"previous":null,"results":[]}"#.utf8)
        _ = try await repository.continueWatchingAnime(page: nil)
        let url = try XCTUnwrap(TVDataURLProtocol.requests.last?.url)
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertEqual(query.first { $0.name == "media_type" }?.value, "anime")
        XCTAssertEqual(query.first { $0.name == "continue_watching" }?.value, "true", "AN-701 reads Continue Watching, as TV does")
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

    func testAN301ACancelledLogScreenChangesNothing() async throws {
        // Partway: the log screen opens holding the value, and nothing is sent until it saves.
        let state = Self.watching(upNext: 3, watched: 2, aired: 12, opinion: .log)
        let detail = Self.detail(anime: state)
        let page = model(detail)
        let log = logModel(detail, preselectedRatingSteps: 8, preselectedLiked: true)
        XCTAssertEqual(log.ratingSteps, 8, "It opens with the value set")
        XCTAssertTrue(log.liked)
        XCTAssertFalse(log.isSaving)
        // Cancelling is closing the screen: no save, no action, no state.
        XCTAssertTrue(diary.tvCreates.isEmpty)
        XCTAssertTrue(tracking.actions.isEmpty)
        XCTAssertNil(page.viewer?.rating, "The stars stay as they were")
        XCTAssertEqual(page.viewer?.hasLiked, false, "The heart stays as it was")
        XCTAssertEqual(page.viewer?.anime?.watchedCount, 2)
    }

    func testTV304ACancelledShowOrSeasonComposerChangesNothing() {
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
        XCTAssertEqual(metrics.stride, 100)
        XCTAssertEqual(metrics.offset(ofRow: 800), 80_000)

        // It builds what's on screen and a chunk or more either side, never the whole list.
        let window = metrics.window(openingOnRow: 800)
        XCTAssertTrue(window.contains(800))
        XCTAssertLessThan(window.count, 40)
        XCTAssertEqual(metrics.heightAbove(window), CGFloat(window.lowerBound) * 100)
        let total = metrics.heightAbove(window) + CGFloat(window.count) * 100 + metrics.heightBelow(window)
        XCTAssertEqual(total, 1_189 * 100, "The spacers and the rows always add up to the whole list")

        // Scrolling a little changes nothing: the rows are built in chunks, so most frames build nothing.
        var changes = 0
        var last = metrics.window(y: 0, height: 700)
        for y in stride(from: CGFloat(0), through: 8_000, by: 10) {
            let next = metrics.window(y: y, height: 700)
            if next != last { changes += 1 }
            last = next
            XCTAssertTrue(next.contains(Int(y / 100)), "The row at the top is always built")
            XCTAssertTrue(next.contains(min(Int((y + 700) / 100), 1_188)), "So is the one at the bottom")
        }
        XCTAssertLessThanOrEqual(changes, 10, "80 rows scrolled, about ten changes")

        // Every episode is reachable, the ends included.
        XCTAssertEqual(metrics.window(y: 0, height: 700).lowerBound, 0)
        XCTAssertEqual(metrics.window(y: metrics.offset(ofRow: 1_188), height: 700).upperBound, 1_189)
        XCTAssertEqual(metrics.window(y: -50, height: 700).lowerBound, 0, "An overscroll at the top")
        XCTAssertEqual(AnimeListMetrics(count: 0).window(y: 0, height: 700), 0..<0)

        // The room under the last row lets a row near the end reach the top.
        XCTAssertEqual(metrics.roomBelowRows(viewportHeight: 340, openingOnRow: 1_188), 440)
        XCTAssertEqual(metrics.roomBelowRows(viewportHeight: 340, openingOnRow: 100), 0)

        // The accessibility text sizes' rows have a fixed height too.
        XCTAssertEqual(AnimeListMetrics(isAccessibilitySize: true, count: 10).stride, AnimeListMetrics.accessibilityRowHeight + 16)
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
        ticks.onAnimeUpdate = { _, anime in cards.append(anime) }
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

        // Not now.
        follow.composerDismissed()
        sheet.finishComposerClosed()
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
        XCTAssertEqual(cards.count, cardCount, "It isn't the card's title")
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
            sheet.finishComposerClosed()
            XCTAssertEqual(sheet.ref.mediaId, "25777")
            XCTAssertEqual(sheet.rows?.episodes.filter(\.isWatched).count, 12, "Every tick filled")
        }
    }

    func testAN501ThePagesSheetNeverMovesOn() async throws {
        var finished = AnimeState(inLibrary: true, status: "Completed", finished: true)
        finished.nextTitle = AnimeNextTitle(mediaId: "35760", title: "Season 3", hasAired: true)
        let sheet = AnimeEpisodes(detail: Self.detail(anime: finished, rows: Self.rows(through: 12, watched: 12))) { [self] ref in
            model(MediaDetail(ref: ref, title: "Seed"))
        }
        XCTAssertFalse(sheet.offersOpenPage, "It's over the page already")
        sheet.finishComposerClosed()
        XCTAssertEqual(sheet.ref.mediaId, "16498")
    }

    func testAN101ThePagesSheetStartsFromTheRowsItHas() async throws {
        let rows = Self.rows(through: 1_189, watched: 800)
        let detail = Self.detail(anime: Self.watching(upNext: 801, watched: 800, aired: 1_189), rows: rows)
        let sheet = AnimeEpisodes(detail: detail) { [self] ref in model(MediaDetail(ref: ref, title: "Seed")) }
        XCTAssertEqual(sheet.startEpisode, 801)
        XCTAssertEqual(sheet.rows?.episodes.count, 1_189)
        await sheet.loadRowsIfNeeded()
        XCTAssertTrue(tracking.rowsRequests.isEmpty, "Nothing to load")
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
        let replaced = ContinueWatchingTicks.replacingAnime(Self.watching(upNext: 6, watched: 5, aired: 12), id: second.id, in: [first, second])
        XCTAssertEqual(replaced.map(\.media.ref.mediaId), ["1", "2"])
        XCTAssertEqual(replaced[1].tracking.anime?.upNext?.episodeNumber, 6)
        XCTAssertEqual(replaced[0].tracking.anime?.upNext?.episodeNumber, 2)
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

    private func model(_ detail: MediaDetail) -> MediaDetailViewModel {
        // Writes refresh their community stats; this holds them away from the fixtures.
        let page = MediaDetailViewModel(
            ref: detail.ref,
            mediaRepository: media,
            trackingRepository: tracking,
            diaryRepository: diary,
            onUnauthorized: {},
            writeRefreshDelay: .seconds(60),
            externalRefreshDelay: .seconds(60)
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

struct MediaLikeRequest: Equatable {
    let ref: MediaRef
    let liked: Bool
}

/// A tracking repository for anime's routes: it records what's sent and answers what a test scripted.
final class AnimeTrackingRepository: TrackingRepository {
    var actions: [(source: String, mediaId: String, action: TVAction, request: TVActionRequest)] = []
    var actionResponses: [TVActionResponse] = []
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
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
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
