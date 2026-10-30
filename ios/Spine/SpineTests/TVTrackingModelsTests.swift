import XCTest
@testable import Spine

/// TV tracking payloads, requests, repositories, the Continue Watching loader and state folding (ios-data).
@MainActor
final class TVTrackingModelsTests: XCTestCase {
    override func setUp() {
        super.setUp()
        TVDataURLProtocol.reset()
    }

    // MARK: Decoding (plan section 2.3)

    func testTV900PayloadsWithoutTVKeysDecodeAsBefore() throws {
        let detail = try decode(MediaDetail.self, """
        {"ref":{"source":"tmdb","media_type":"tv","media_id":"1399"},"title":"A Show",
         "user_state":{"is_tracked":true,"status":"In progress","rating":"8.0","in_lists":[],"has_liked":false},
         "seasons":[{"season_number":1,"title":"Season 1","episode_count":10,"image_url":null,"release_date":null,
                     "completion":{"completed_count":3,"total_count":10}}],
         "episodes":[{"episode_number":1,"title":"Pilot","overview":null,"air_date":"2010-01-01","runtime":"47m",
                      "image_url":null,"rating":"8.1"}]}
        """)
        XCTAssertNil(detail.userState?.tv)
        XCTAssertEqual(detail.userState?.status, "In progress")
        XCTAssertNil(detail.seasons?.first?.tv)
        XCTAssertEqual(detail.seasons?.first?.completion?.countText, "3 of 10")
        XCTAssertNil(detail.episodes?.first?.tv)
        XCTAssertEqual(detail.episodes?.first?.rating, "8.1")
        XCTAssertNil(detail.tvUnlistedSeasons)
        XCTAssertNil(detail.tvUnlistedEpisodes)

        let tracking = try decode(TrackingState.self, #"{"tracking_id":7,"status":"In progress","progress":{"kind":"episodes","value":3,"max":10,"unit":"episode"}}"#)
        XCTAssertNil(tracking.tv)
        XCTAssertEqual(tracking.progress?.value, 3)

        let entry = try decode(DiaryEntry.self, Self.diaryEntryJSON(mediaType: "movie", extra: ""))
        XCTAssertNil(entry.tvLog)
        XCTAssertNil(entry.tv)
        XCTAssertNil(entry.finished)
        XCTAssertNil(entry.earlierUnwatched)
        XCTAssertNil(entry.media.showTitle)
        XCTAssertNil(entry.media.episodeTitle)
        XCTAssertEqual(TVWriteEnvelope(diaryEntry: entry), .empty)

        let like = try decode(MediaLikeResponse.self, #"{"liked":true,"media":null}"#)
        XCTAssertTrue(like.liked)
        XCTAssertNil(like.tv)
        XCTAssertNil(like.season)
        XCTAssertNil(like.episode)
        XCTAssertNil(like.earlierUnwatched)
    }

    func testDecodesTheShowDetailExample() throws {
        let tv = try decode(TVShowState.self, Self.showDetailJSON)
        XCTAssertEqual(tv.version, 1_793_051_512_345)
        XCTAssertTrue(tv.inLibrary)
        XCTAssertEqual(tv.status, "In progress")
        XCTAssertNil(tv.choice)
        XCTAssertEqual(tv.selectedAction, "watching")
        XCTAssertTrue(tv.started)
        XCTAssertFalse(tv.finished)
        XCTAssertFalse(tv.caughtUp)
        XCTAssertEqual(tv.watchedCount, 13)
        XCTAssertEqual(tv.airedCount, 20)
        XCTAssertEqual(tv.upNext, TVEpisodePointer(seasonNumber: 2, episodeNumber: 4, title: "Fly", airDate: "2010-05-23", isNew: false))
        XCTAssertEqual(tv.upNextCode, "S2 E4")
        XCTAssertNil(tv.nextAiring)
        XCTAssertNil(tv.premiere)
        XCTAssertNil(tv.rewatch)
        XCTAssertEqual(tv.lastWatchedOn, "2026-10-25")
        XCTAssertEqual(tv.removeCount, 13)
        XCTAssertEqual(tv.likeIsIndependent, true)
        XCTAssertEqual(tv.eye, TVEyeState(filled: false, tap: .markConfirm, count: 20, prompt: "Mark all 20 aired episodes watched?"))
        XCTAssertEqual(tv.availableActions, ["pause", "drop", "log", "remove", "rate"])
        XCTAssertEqual(tv.actionReasons, ["planning": "You've started this show. Pause or drop it instead."])
        XCTAssertEqual(tv.composer?.defaultTarget, "season:1")
        XCTAssertEqual(tv.composer?.targets.map(\.id), ["season:1", "season:2", "season:0", "show"])
        XCTAssertEqual(tv.composer?.targets.map(\.title), ["Season 1", "Season 2", "Specials", "Whole show"])
        let first = try XCTUnwrap(tv.composer?.initialTarget)
        XCTAssertEqual(first.level, .season)
        XCTAssertEqual(first.seasonNumber, 1)
        XCTAssertEqual(first.date, "2026-10-26")
        XCTAssertEqual(first.rating, "4.0")
        XCTAssertTrue(first.liked)
        XCTAssertFalse(first.isRewatch)
        XCTAssertTrue(first.available)
        XCTAssertEqual(tv.composer?.target(id: "show")?.level, .show)
        XCTAssertNil(tv.season)
        XCTAssertNil(tv.episode)
    }

    func testDecodesTheListVariantIntoTheSameType() throws {
        let tracking = try decode(TrackingState.self, """
        {"tracking_id":3,"status":"Completed","progress":{"kind":"episodes","value":20,"max":20,"unit":"episode"},
         "updated_at":"2026-10-20T21:00:00Z","tv":\(Self.showListJSON)}
        """)
        let tv = try XCTUnwrap(tracking.tv)
        XCTAssertEqual(tv.version, 1_793_051_500_000)
        XCTAssertEqual(tv.status, "Completed")
        XCTAssertTrue(tv.finished)
        XCTAssertTrue(tv.caughtUp)
        XCTAssertNil(tv.upNext)
        XCTAssertEqual(tv.nextAiring, TVAiring(seasonNumber: 3, episodeNumber: 1, airDate: "2027-03-12"))
        XCTAssertNil(tv.selectedAction)
        XCTAssertNil(tv.eye)
        XCTAssertNil(tv.composer)
        XCTAssertNil(tv.removeCount)
        XCTAssertEqual(tv.availableActions, [])
        XCTAssertEqual(tv.actionReasons, [:])

        let library = try decode(PagedResponse<Spine.LibraryItem>.self, """
        {"count":1,"next":null,"previous":null,"results":[{"media":{"ref":{"source":"tmdb","media_type":"tv","media_id":"1"},
         "title":"A Show"},"tracking":{"tracking_id":3,"status":"Completed","tv":\(Self.showListJSON)}}]}
        """)
        XCTAssertEqual(library.results.first?.tracking.tv?.nextAiring?.seasonNumber, 3)
    }

    func testActionReasonKeysKeepTheirSnakeCaseNames() throws {
        let tv = try decode(TVShowState.self, """
        {"version":1,"in_library":true,"started":true,"finished":false,"caught_up":false,
         "available_actions":["stop_rewatch","log"],
         "action_reasons":{"stop_rewatch":"Not now.","rewatch":"Finish or stop your rewatch first.","log_season":"Nothing has aired yet."}}
        """)
        XCTAssertEqual(tv.actionReasons["stop_rewatch"], "Not now.")
        XCTAssertEqual(tv.actionReasons["log_season"], "Nothing has aired yet.")
        XCTAssertNil(tv.actionReasons["stopRewatch"])
        XCTAssertEqual(tv.availableActions, ["stop_rewatch", "log"])

        let season = try decode(TVSeasonState.self, #"{"season_number":2,"state":"watched","action_reasons":{"rewatch_season":"Finish or stop your rewatch first."}}"#)
        XCTAssertEqual(season.actionReasons, ["rewatch_season": "Finish or stop your rewatch first."])
        let episode = try decode(TVEpisodeState.self, #"{"season_number":2,"episode_number":1,"action_reasons":{"log_episode":"This episode hasn't aired yet."}}"#)
        XCTAssertEqual(episode.actionReasons, ["log_episode": "This episode hasn't aired yet."])

        // Whichever way a decoder treats dictionary keys, they come back as the wire's action names.
        XCTAssertEqual(TVActionReasons.restoringSnakeCase(["stopRewatch": "a", "log": "b", "rewatch_season": "c"]),
                       ["stop_rewatch": "a", "log": "b", "rewatch_season": "c"])

        // Encoding and decoding again keeps the names.
        let roundTrip = try JSONDecoder.api.decode(TVShowState.self, from: JSONEncoder.api.encode(tv))
        XCTAssertEqual(roundTrip.actionReasons, tv.actionReasons)
        XCTAssertEqual(roundTrip, tv)
    }

    func testDecodesSeasonPageEpisodesAndUnlistedEpisodes() throws {
        let detail = try decode(MediaDetail.self, """
        {"ref":{"source":"tmdb","media_type":"season","media_id":"1399","season_number":3},"title":"Season 3",
         "episodes":[{"episode_number":4,"title":"Fly","overview":"...","air_date":"2010-05-23","runtime":"47m","image_url":null,
                      "image_role":"still","rating":"8.1",
                      "tv":{"season_number":3,"episode_number":4,"is_special":false,"listed":true,"aired":true,"air_date":"2010-05-23",
                            "watched":true,"viewing_count":1,"log_count":0,"can_tick":false,"untick_viewing_id":9912,"untick_message":null,
                            "can_mark_up_to":true,"mark_up_to_prompt":null,"user_rating":null,"liked":false,"is_up_next":false}}],
         "tv_unlisted_episodes":[{"episode_number":14,"title":"Episode 14",
                      "tv":{"season_number":3,"episode_number":14,"is_special":false,"listed":false,"aired":true,"air_date":null,
                            "watched":true,"viewing_count":1,"log_count":0,"can_tick":false,"untick_viewing_id":77,"untick_message":null,
                            "can_mark_up_to":false,"mark_up_to_prompt":null,"user_rating":"7.0","liked":true,"is_up_next":false}}],
         "completion":{"completed_count":4,"total_count":10},
         "user_state":{"is_tracked":true,"status":"In progress","rating":"6.0","in_lists":[],"has_liked":false,
            "tv":{"version":5,"in_library":true,"status":"In progress","started":true,"finished":false,"caught_up":false,
                  "season":{"season_number":3,"is_specials":false,"listed":true,"started":true,"status":"In progress","state":"in_progress",
                            "watched_count":4,"aired_count":10,"episode_count":10,"first_gap_episode":null,"next_air_date":null,
                            "rewatching":false,"rating":"6.0","liked":false,"rating_source_diary_entry_id":12,
                            "like_source_diary_entry_id":null,"like_is_independent":null,"log_count":1,
                            "eye":{"filled":false,"tap":"mark","count":6,"prompt":null,"message":null},
                            "available_actions":["log_season","rate"],"action_reasons":{}}}}}
        """)
        let episode = try XCTUnwrap(detail.episodes?.first?.tv)
        XCTAssertEqual(episode.coordinate, TVEpisodeCoordinate(seasonNumber: 3, episodeNumber: 4))
        XCTAssertTrue(episode.watched)
        XCTAssertFalse(episode.canTick)
        XCTAssertEqual(episode.untickViewingId, 9912)
        XCTAssertTrue(episode.canMarkUpTo)
        XCTAssertTrue(episode.countsTowardProgress)
        XCTAssertEqual(episode.availableActions, [], "Episode actions come only on the episode page.")
        XCTAssertNil(episode.composer)
        XCTAssertEqual(detail.episodes?.first?.rating, "8.1", "rating stays the provider's vote")

        let unlisted = try XCTUnwrap(detail.tvUnlistedEpisodes?.first)
        XCTAssertEqual(unlisted.id, 14)
        XCTAssertFalse(unlisted.tv.listed)
        XCTAssertFalse(unlisted.tv.canTick)
        XCTAssertFalse(unlisted.tv.countsTowardProgress)
        XCTAssertEqual(unlisted.tv.untickViewingId, 77)

        let season = try XCTUnwrap(detail.userState?.tv?.season)
        XCTAssertEqual(season.state, .inProgress)
        XCTAssertEqual(season.watchedCount, 4)
        XCTAssertEqual(season.logCount, 1)
        XCTAssertEqual(season.eye?.tap, .mark)
        XCTAssertEqual(season.availableActions, ["log_season", "rate"])
    }

    func testDecodesShowPageSeasonCardsAndUnlistedSeasons() throws {
        let detail = try decode(MediaDetail.self, """
        {"ref":{"source":"tmdb","media_type":"tv","media_id":"1399"},"title":"A Show",
         "seasons":[{"season_number":1,"title":"Season 1","episode_count":10,"image_url":null,"release_date":null,"completion":null,
                     "tv":{"season_number":1,"is_specials":false,"listed":true,"started":true,"status":"In progress","state":"in_progress",
                           "watched_count":9,"aired_count":10,"episode_count":10,"first_gap_episode":4,"next_air_date":null,
                           "rewatching":false,"rating":null,"liked":false}},
                    {"season_number":0,"title":"Specials","episode_count":3,"image_url":null,"release_date":null,"completion":null,
                     "tv":{"season_number":0,"is_specials":true,"listed":true,"started":false,"status":null,"state":"not_started",
                           "watched_count":0,"aired_count":3,"episode_count":3,"first_gap_episode":null,"next_air_date":null,
                           "rewatching":false,"rating":null,"liked":false}}],
         "tv_unlisted_seasons":[{"season_number":7,"title":"Season 7",
                     "tv":{"season_number":7,"is_specials":false,"listed":false,"started":true,"status":"In progress","state":"unknown",
                           "watched_count":null,"aired_count":null,"episode_count":null,"first_gap_episode":null,"next_air_date":null,
                           "rewatching":false,"rating":"9.0","liked":true}}]}
        """)
        XCTAssertEqual(detail.seasons?.map(\.seasonNumber), [1, 0])
        let card = try XCTUnwrap(detail.seasons?.first?.tv)
        XCTAssertEqual(card.firstGapEpisode, 4)
        XCTAssertNil(card.eye, "Cards carry no detail keys.")
        XCTAssertNil(card.logCount)
        XCTAssertTrue(try XCTUnwrap(detail.seasons?.last?.tv).isSpecials)
        let unlisted = try XCTUnwrap(detail.tvUnlistedSeasons?.first)
        XCTAssertEqual(unlisted.id, 7)
        XCTAssertFalse(unlisted.tv.listed)
        XCTAssertEqual(unlisted.tv.state, .unknown)
        XCTAssertEqual(unlisted.tv.rating, "9.0")

        // Rebuilding the detail (a poster save, say) keeps the unlisted lists.
        let rebuilt = detail.replacingHasLiked(true)
        XCTAssertEqual(rebuilt.tvUnlistedSeasons, detail.tvUnlistedSeasons)
    }

    func testUnknownEnumStringsFallBackInsteadOfFailing() throws {
        let eye = try decode(TVEyeState.self, #"{"filled":true,"tap":"launch_rocket"}"#)
        XCTAssertEqual(eye.tap, .unknown)
        let season = try decode(TVSeasonState.self, #"{"season_number":1,"state":"half_watched"}"#)
        XCTAssertEqual(season.state, .unknown)
        let target = try decode(TVComposerTarget.self, #"{"id":"arc:1","level":"arc","title":"Arc","date":"2026-10-26","liked":false,"is_rewatch":false,"available":true}"#)
        XCTAssertEqual(target.level, .unknown)
        let log = try decode(TVLogInfo.self, #"{"level":"saga","unmarks_count":2,"covers_count":3,"state_version":4}"#)
        XCTAssertEqual(log.level, .unknown)
        XCTAssertEqual(log.unmarksCount, 2)
    }

    func testDecodesTheWriteEnvelopeExamples() throws {
        let response = try decode(TVActionResponse.self, """
        {"tracking":null,"tv":\(Self.showListJSON),"season":null,"episode":null,"changed":true,
         "finished":{"level":"season","season_number":1,"show_finished":false,
                     "composer":{"default_target":"season:1","targets":[{"id":"season:1","level":"season","season_number":1,
                       "episode_number":null,"title":"Season 1","date":"2026-10-26","rating":null,"liked":false,"is_rewatch":false,
                       "claims_viewing_id":null,"available":true,"reason":null}]}},
         "earlier_unwatched":{"from":{"season_number":1,"episode_number":1},"to":{"season_number":2,"episode_number":10},
                              "count":19,"seasons":[1,2],"question":"Also mark Seasons 1–2 watched?","offer_season_number":3}}
        """)
        XCTAssertNil(response.tracking)
        XCTAssertTrue(response.envelope.changed)
        XCTAssertEqual(response.tv?.version, 1_793_051_500_000)
        let finished = try XCTUnwrap(response.finished)
        XCTAssertEqual(finished.level, .season)
        XCTAssertEqual(finished.seasonNumber, 1)
        XCTAssertFalse(finished.showFinished)
        XCTAssertEqual(finished.composer.initialTarget?.id, "season:1")
        XCTAssertEqual(response.earlierUnwatched, TVEarlierUnwatched(
            from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 10),
            count: 19,
            seasons: [1, 2],
            question: "Also mark Seasons 1–2 watched?",
            offerSeasonNumber: 3
        ))
        XCTAssertTrue(response.envelope.hasFollowUp)

        let plain = try decode(TVActionResponse.self, #"{"tracking":{"tracking_id":1,"status":"In progress"},"tv":null,"changed":false}"#)
        XCTAssertEqual(plain.tracking?.trackingId, 1)
        XCTAssertFalse(plain.envelope.hasFollowUp)
        XCTAssertEqual(TVWriteEnvelope.empty, TVWriteEnvelope(tv: nil, season: nil, episode: nil, changed: false, finished: nil, earlierUnwatched: nil))
    }

    func testDiaryEntryCarriesTVLogTitlesAndTheEnvelope() throws {
        let entry = try decode(DiaryEntry.self, Self.diaryEntryJSON(mediaType: "episode", extra: """
        ,"tv_log":{"level":"episode","season_number":1,"episode_number":4,"unmarks_count":1,"covers_count":1,"viewing_id":9912,"state_version":42},
         "tv":\(Self.showListJSON),
         "finished":{"level":"season","season_number":1,"show_finished":true,"composer":{"default_target":"season:1","targets":[]}},
         "earlier_unwatched":null
        """))
        XCTAssertEqual(entry.tvLog, TVLogInfo(level: .episode, seasonNumber: 1, episodeNumber: 4, unmarksCount: 1,
                                              coversCount: 1, viewingId: 9912, stateVersion: 42))
        XCTAssertEqual(entry.media.showTitle, "Breaking Bad")
        XCTAssertEqual(entry.media.episodeTitle, "Cancer Man")
        let envelope = TVWriteEnvelope(diaryEntry: entry)
        XCTAssertTrue(envelope.changed)
        XCTAssertEqual(envelope.tv?.version, 1_793_051_500_000)
        XCTAssertEqual(envelope.finished?.showFinished, true)
        XCTAssertNil(envelope.earlierUnwatched)
    }

    func testMediaLikeResponseCarriesTVKeysAsAnEnvelope() throws {
        let like = try decode(MediaLikeResponse.self, """
        {"liked":true,"media":{"ref":{"source":"tmdb","media_type":"tv","media_id":"1"},"title":"A Show",
                               "user_state":{"is_tracked":true,"status":"Completed","in_lists":[],"has_liked":true,"tv":\(Self.showDetailJSON)}},
         "tv":\(Self.showListJSON),"season":null,"episode":null,
         "earlier_unwatched":{"from":{"season_number":1,"episode_number":1},"to":{"season_number":1,"episode_number":9},
                              "count":9,"seasons":null,"question":"Also mark S1 E1–S1 E9 watched?","offer_season_number":null}}
        """)
        XCTAssertEqual(like.media?.userState?.tv?.eye?.tap, .markConfirm, "POST returns the detail variant in user_state")
        XCTAssertEqual(like.tv?.version, 1_793_051_500_000)
        XCTAssertEqual(like.earlierUnwatched?.count, 9)
        XCTAssertNil(like.earlierUnwatched?.seasons)
        let envelope = like.tvEnvelope
        XCTAssertEqual(envelope.tv?.version, 1_793_051_500_000)
        XCTAssertEqual(envelope.earlierUnwatched?.question, "Also mark S1 E1–S1 E9 watched?")
        XCTAssertNil(envelope.finished)
    }

    // MARK: Requests

    func testTVActionRequestEncodesSnakeCaseAndOmitsNilKeys() throws {
        let id = try XCTUnwrap(UUID(uuidString: "7F1C0000-0000-4000-8000-000000000001"))
        let full = try object(TVActionRequest(
            seasonNumber: 2, episodeNumber: 4, viewingId: 9912, mutationId: id, expectedVersion: 1_793_051_512_345,
            confirm: true, from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 10), offerSeasonNumber: 3, rating: 8
        ))
        XCTAssertEqual(full["season_number"] as? Int, 2)
        XCTAssertEqual(full["episode_number"] as? Int, 4)
        XCTAssertEqual(full["viewing_id"] as? Int, 9912)
        XCTAssertEqual(full["mutation_id"] as? String, id.uuidString)
        XCTAssertEqual((full["expected_version"] as? NSNumber)?.int64Value, 1_793_051_512_345)
        XCTAssertEqual(full["confirm"] as? Bool, true)
        XCTAssertEqual(full["from"] as? [String: Int], ["season_number": 1, "episode_number": 1])
        XCTAssertEqual(full["to"] as? [String: Int], ["season_number": 2, "episode_number": 10])
        XCTAssertEqual(full["offer_season_number"] as? Int, 3)
        XCTAssertEqual((full["rating"] as? NSNumber)?.doubleValue, 4, "Half steps go out as stars.")

        let tick = try object(TVActionRequest(seasonNumber: 1, episodeNumber: 2))
        XCTAssertEqual(Set(tick.keys), ["season_number", "episode_number"])

        let clear = try object(TVActionRequest(rating: nil, includesRating: true))
        XCTAssertTrue(clear["rating"] is NSNull, "Clearing stars sends rating: null")
        XCTAssertEqual(Set(clear.keys), ["rating"])

        XCTAssertEqual(try object(TVActionRequest()).count, 0)
        XCTAssertEqual(TVAction.stopRewatch.rawValue, "stop_rewatch")
        XCTAssertEqual(TVAction.markEarlier.rawValue, "mark_earlier")
        XCTAssertEqual(TVAction.markUpTo.rawValue, "mark_up_to")
        XCTAssertEqual(TVAction.markWatched.rawValue, "mark_watched")
    }

    func testTV407LogsEncodeACalendarDate() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        let date = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 10, day: 25, hour: 23, minute: 30)))
        for mediaType in ["tv", "season", "episode"] {
            let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: mediaType, mediaId: "1399",
                               seasonNumber: mediaType == "tv" ? nil : 1, episodeNumber: mediaType == "episode" ? 4 : nil)
            XCTAssertTrue(ref.usesCalendarLogDate)
            XCTAssertFalse(ref.usesCalendarConsumptionDate, "TV keeps its per-entry visibility picker.")
            let json = try object(DiaryEntryWriteRequest(
                ref: ref, consumedAt: date, rating: 8, review: "", reviewTitle: "", liked: false, isRewatch: false,
                autoMarkConsumed: true, containsSpoilers: false, visibility: "public", tags: [], mutationId: UUID()
            ))
            XCTAssertEqual(json["consumed_at"] as? String, "2026-10-25", mediaType)
        }
        let anime = MediaRef(itemId: nil, source: "mal", mediaType: "anime", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        XCTAssertFalse(anime.usesCalendarLogDate)
        let animeJSON = try object(DiaryEntryWriteRequest(
            ref: anime, consumedAt: date, rating: nil, review: "", reviewTitle: "", liked: false, isRewatch: false,
            autoMarkConsumed: true, containsSpoilers: false, visibility: "public", tags: []
        ))
        XCTAssertGreaterThan((animeJSON["consumed_at"] as? String)?.count ?? 0, 10, "Other types keep their datetime.")
        let movie = MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "1", seasonNumber: nil, episodeNumber: nil)
        XCTAssertTrue(movie.usesCalendarLogDate)
    }

    func testAPIErrorCodeReadsTheErrorEnvelope() {
        let conflict = APIError.httpStatus(409, #"{"error":{"code":"tv_state_changed","message":"Changed.","fields":{},"request_id":"r1","tv":{"state_version":9,"count":61}}}"#)
        XCTAssertEqual(conflict.apiErrorCode, "tv_state_changed")
        XCTAssertEqual(conflict.apiErrorMessage, "Changed.")
        // TV-103: the retryable 503 is provider_unavailable (which installed builds show) with a reason; it shows
        // the server's message, not "can't reach the server", and no request ID.
        let unavailable = APIError.httpStatus(503, #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"TV details are unavailable right now","request_id":"r9"}}"#)
        XCTAssertEqual(unavailable.apiErrorMessage, "TV details are unavailable right now")
        XCTAssertEqual(unavailable.localizedDescription, "TV details are unavailable right now")
        XCTAssertEqual(APIError.httpStatus(503, #"{"error":{"code":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#).localizedDescription,
                       "TV details are unavailable right now", "Servers from before the reason sent their own code")
        XCTAssertEqual(APIError.httpStatus(409, #"{"error":{"code":"tv_log_blocks","message":"Delete this show's logs first."}}"#).apiErrorCode, "tv_log_blocks")
        XCTAssertNil(APIError.httpStatus(500, "<html>").apiErrorCode)
        XCTAssertNil(APIError.httpStatus(404, nil).apiErrorCode)
        XCTAssertNil(APIError.invalidResponse.apiErrorCode)
    }

    // MARK: Repositories

    func testPerformTVActionPostsTheActionRoute() async throws {
        TVDataURLProtocol.response = Data("""
        {"tracking":{"tracking_id":5,"status":"In progress","tv":\(Self.showListJSON)},"tv":\(Self.showDetailJSON),
         "season":null,"episode":null,"changed":true,"finished":null,"earlier_unwatched":null}
        """.utf8)
        let id = UUID()
        let response = try await APITrackingRepository(client: Self.client()).performTVAction(
            source: "tmdb", mediaId: "1399", action: .tick,
            request: TVActionRequest(seasonNumber: 1, episodeNumber: 2, mutationId: id)
        )
        let request = try XCTUnwrap(TVDataURLProtocol.requests.last)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "https://example.com/api/v1/tracking/tmdb/tv/1399/actions/tick/")
        XCTAssertEqual(TVDataURLProtocol.bodies.last?["season_number"] as? Int, 1)
        XCTAssertEqual(TVDataURLProtocol.bodies.last?["episode_number"] as? Int, 2)
        XCTAssertEqual(TVDataURLProtocol.bodies.last?["mutation_id"] as? String, id.uuidString)
        XCTAssertEqual(response.tracking?.trackingId, 5)
        XCTAssertEqual(response.tracking?.tv?.status, "Completed")
        XCTAssertEqual(response.tv?.eye?.tap, .markConfirm)
        XCTAssertTrue(response.envelope.changed)
    }

    func testContinueWatchingRequestsShowsInTheServersOrder() async throws {
        TVDataURLProtocol.response = Data(#"{"count":0,"next":null,"previous":null,"results":[]}"#.utf8)
        _ = try await APITrackingRepository(client: Self.client()).continueWatching(page: "2")
        let url = try XCTUnwrap(TVDataURLProtocol.requests.last?.url)
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        XCTAssertTrue(url.absoluteString.hasPrefix("https://example.com/api/v1/tracking/?"))
        XCTAssertEqual(items, [
            URLQueryItem(name: "media_type", value: "tv"),
            URLQueryItem(name: "continue_watching", value: "true"),
            URLQueryItem(name: "page", value: "2"),
        ])
    }

    func testDiaryDeleteSendsTheVersionAndMutationID() async throws {
        TVDataURLProtocol.status = 204
        let id = try XCTUnwrap(UUID(uuidString: "7F1C0000-0000-4000-8000-000000000002"))
        try await APIDiaryRepository(client: Self.client()).delete(id: 9, expectedVersion: 1_793_051_512_345, mutationId: id)
        let request = try XCTUnwrap(TVDataURLProtocol.requests.last)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString,
                       "https://example.com/api/v1/diary/9/?expected_version=1793051512345&mutation_id=7f1c0000-0000-4000-8000-000000000002")

        try await APIDiaryRepository(client: Self.client()).delete(id: 10)
        XCTAssertEqual(TVDataURLProtocol.requests.last?.url?.absoluteString, "https://example.com/api/v1/diary/10/")
    }

    func testDiaryDeleteReportsAStaleVersion() async throws {
        TVDataURLProtocol.status = 409
        TVDataURLProtocol.response = Data(#"{"error":{"code":"tv_state_changed","message":"This show changed.","tv":{"state_version":9,"count":6}}}"#.utf8)
        do {
            try await APIDiaryRepository(client: Self.client()).delete(id: 9, expectedVersion: 1, mutationId: UUID())
            XCTFail("A stale version must reach the caller so it can ask again.")
        } catch let error as APIError {
            XCTAssertEqual(error.apiErrorCode, "tv_state_changed")
        }
    }

    func testCreateTVReturnsTheEntryAndItsEnvelope() async throws {
        TVDataURLProtocol.status = 201
        TVDataURLProtocol.response = Data(Self.diaryEntryJSON(mediaType: "episode", extra: """
        ,"tv":\(Self.showListJSON),
         "finished":{"level":"season","season_number":1,"show_finished":false,"composer":{"default_target":"season:1","targets":[]}},
         "earlier_unwatched":null
        """).utf8)
        let ref = MediaRef(itemId: nil, source: "tmdb", mediaType: "episode", mediaId: "1396", seasonNumber: 1, episodeNumber: 4)
        let (entry, envelope) = try await APIDiaryRepository(client: Self.client()).createTV(DiaryEntryWriteRequest(
            ref: ref, consumedAt: Date(), rating: nil, review: "", reviewTitle: "", liked: false, isRewatch: false,
            autoMarkConsumed: true, containsSpoilers: false, visibility: "public", tags: [], mutationId: UUID()
        ))
        XCTAssertEqual(TVDataURLProtocol.requests.last?.httpMethod, "POST")
        XCTAssertEqual(TVDataURLProtocol.requests.last?.url?.absoluteString, "https://example.com/api/v1/diary/")
        XCTAssertEqual((TVDataURLProtocol.bodies.last?["consumed_at"] as? String)?.count, 10)
        XCTAssertEqual(entry.id, 31)
        XCTAssertEqual(envelope.finished?.seasonNumber, 1)
        XCTAssertEqual(envelope.tv?.status, "Completed")
    }

    func testRepositoryDefaultsForwardToTheExistingCalls() async throws {
        let tracking = TVDataTrackingRepository()
        tracking.listResponse = PagedResponse(count: 1, next: nil, previous: nil, results: [Self.libraryItem("1", "Show", "2026-10-20T10:00:00Z")])
        let page = try await tracking.continueWatchingThroughDefault(page: "3")
        XCTAssertEqual(page.results.map(\.media.title), ["Show"])
        XCTAssertEqual(tracking.listRequests.map(\.mediaType), ["tv"])
        XCTAssertEqual(tracking.listRequests.map(\.status), ["In progress"])
        XCTAssertEqual(tracking.listRequests.map(\.page), ["3"])

        let diary = TVDataDiaryRepository()
        try await diary.delete(id: 4, expectedVersion: 9, mutationId: UUID())
        XCTAssertEqual(diary.deletedIds, [4])
        diary.createResponse = try decode(DiaryEntry.self, Self.diaryEntryJSON(mediaType: "season", extra: ""))
        let (entry, envelope) = try await diary.createTV(DiaryEntryWriteRequest(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1", seasonNumber: 1, episodeNumber: nil),
            consumedAt: nil, rating: nil, review: "", reviewTitle: "", liked: false, isRewatch: false,
            autoMarkConsumed: true, containsSpoilers: false, visibility: "public", tags: []
        ))
        XCTAssertEqual(diary.createdRequests.count, 1)
        XCTAssertEqual(entry.id, 31)
        XCTAssertEqual(envelope, .empty)
    }

    // MARK: Continue Watching loader (TV-701)

    func testTV701LoaderListsShowsInTheServersOrderAmongOtherTypes() async throws {
        let tracking = TVDataTrackingRepository()
        // The server orders by calendar day, then recorded time; `updated_at` can disagree, and the app keeps the
        // server's order.
        tracking.continueWatchingResponse = PagedResponse(count: 3, next: nil, previous: nil, results: [
            Self.libraryItem("10", "Show A", "2026-10-25T09:00:00Z", mediaType: "tv"),
            Self.libraryItem("11", "Show B", "2026-10-25T21:00:00Z", mediaType: "tv"),
            Self.libraryItem("12", "Show C", "2026-10-20T10:00:00Z", mediaType: "tv"),
        ])
        tracking.listResponses = [
            "movie": PagedResponse(count: 2, next: nil, previous: nil, results: [
                Self.libraryItem("1", "Old Movie", "2026-10-01T10:00:00Z"),
                Self.libraryItem("2", "New Movie", "2026-10-26T10:00:00Z"),
            ]),
            "book": PagedResponse(count: 1, next: nil, previous: nil, results: [
                Self.libraryItem("3", "Mid Book", "2026-10-22T10:00:00Z", mediaType: "book"),
            ]),
        ]
        let profile = Self.profile(enabled: ["movie", "tv", "season", "episode", "book"])
        XCTAssertEqual(InProgressLibraryLoader.mediaTypes(from: profile), ["movie", "tv", "book"])

        let items = try await InProgressLibraryLoader.load(profile: profile, trackingRepository: tracking, limit: 10)
        XCTAssertEqual(items.map(\.media.title), ["New Movie", "Show A", "Show B", "Mid Book", "Show C", "Old Movie"])
        XCTAssertEqual(tracking.continueWatchingPages, [nil])
        XCTAssertEqual(tracking.listRequests.map(\.mediaType), ["movie", "book"])
        XCTAssertEqual(tracking.listRequests.map(\.status), ["In progress", "In progress"])

        let limited = try await InProgressLibraryLoader.load(mediaTypes: ["tv", "movie"], trackingRepository: tracking, limit: 2)
        XCTAssertEqual(limited.map(\.media.title), ["New Movie", "Show A"])
    }

    // MARK: Folding writes into the page (TV-705)

    func testEnvelopeFoldOnTheShowPageAppliesTheShowsTracking() throws {
        let page = UserMediaState(isTracked: true, trackingId: 5, status: "Paused", rating: "6.0", inLists: [3], hasLiked: true,
                                  tv: try decode(TVShowState.self, Self.showDetailJSON))
        let tracking = TrackingState(trackingId: 5, status: "In progress", rating: "6.0", progress: nil, repeats: nil,
                                     startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
        var next = try decode(TVShowState.self, Self.showDetailJSON)
        next.watchedCount = 14
        let folded = page.applying(tvEnvelope: TVWriteEnvelope(tv: next, changed: true), tracking: tracking)
        XCTAssertEqual(folded.status, "In progress")
        XCTAssertEqual(folded.tv?.watchedCount, 14)
        XCTAssertEqual(folded.inLists, [3])
        XCTAssertTrue(folded.hasLiked)

        // The show left the library: tracking is null, opinions clear (TV-003, TV-208), and the envelope's state stays.
        var gone = next
        gone.inLibrary = false
        gone.status = nil
        let removed = page.applying(tvEnvelope: TVWriteEnvelope(tv: gone, changed: true), tracking: nil)
        XCTAssertFalse(removed.isTracked)
        XCTAssertNil(removed.status)
        XCTAssertNil(removed.rating)
        XCTAssertFalse(removed.hasLiked)
        XCTAssertEqual(removed.tv?.inLibrary, false)
        XCTAssertEqual(removed.inLists, [3])

        XCTAssertNil(page.removed().tv)

        // A tracking read copies `tv`.
        let read = TrackingState(trackingId: 5, status: "Completed", rating: nil, progress: nil, repeats: nil, startDate: nil,
                                 endDate: nil, notes: nil, updatedAt: nil, tv: gone)
        XCTAssertEqual(page.applying(read).tv, gone)
        XCTAssertEqual(page.applying(TrackingState(trackingId: 5, status: "Completed", rating: nil, progress: nil, repeats: nil,
                                                   startDate: nil, endDate: nil, notes: nil, updatedAt: nil)).tv,
                       page.tv, "A state without tv keeps the page's")
    }

    func testEnvelopeFoldOnASeasonPageUsesTheSeasonsOwnState() throws {
        var pageTV = try decode(TVShowState.self, Self.showDetailJSON)
        pageTV.season = TVSeasonState(seasonNumber: 2, started: true, status: "In progress", state: .inProgress,
                                      watchedCount: 3, airedCount: 10, rating: "6.0", liked: false)
        let seasonComposer = TVComposer(defaultTarget: "season:2", targets: [])
        pageTV.composer = seasonComposer
        let page = UserMediaState(isTracked: true, trackingId: 77, status: "In progress", rating: "6.0", tv: pageTV)

        var showAfter = try decode(TVShowState.self, Self.showDetailJSON)
        showAfter.watchedCount = 14
        let seasonAfter = TVSeasonState(seasonNumber: 2, started: true, status: "In progress", state: .inProgress,
                                        watchedCount: 4, airedCount: 10, rating: "6.0", liked: true, likeIsIndependent: true)
        let showTracking = TrackingState(trackingId: 5, status: "In progress", rating: "9.0", progress: nil, repeats: nil,
                                         startDate: nil, endDate: nil, notes: nil, updatedAt: nil)
        let folded = page.applying(tvEnvelope: TVWriteEnvelope(tv: showAfter, season: seasonAfter, changed: true), tracking: showTracking)
        XCTAssertEqual(folded.trackingId, 77, "The show's tracking never replaces a season page's.")
        XCTAssertEqual(folded.rating, "6.0", "The season keeps its own stars (TV-500).")
        XCTAssertTrue(folded.hasLiked)
        XCTAssertEqual(folded.tv?.watchedCount, 14)
        XCTAssertEqual(folded.tv?.season?.watchedCount, 4)
        XCTAssertEqual(folded.tv?.composer, seasonComposer, "The season page keeps its own composer.")
    }

    // MARK: Projections (TV-100, TV-705)

    func testTickProjectionShowsAtOnceAndConfirmationReplacesIt() throws {
        var tv = try decode(TVShowState.self, Self.showDetailJSON)
        tv.choice = "Paused"
        tv.status = "Paused"
        tv.selectedAction = "pause"
        let confirmed = UserMediaState(isTracked: true, status: "Paused", tv: tv)
        var shown = confirmed
        TrackingProjection.tvTick(season: 2, episode: 4)(&shown)
        let upNext = TVEpisodeState(seasonNumber: 2, episodeNumber: 4, watched: false)
        XCTAssertTrue(shown.tvIsWatched(upNext))
        XCTAssertEqual(shown.tv?.watchedCount, 14)
        XCTAssertEqual(shown.status, "In progress", "A tick ends a Paused choice (TV-203).")
        XCTAssertEqual(shown.tv?.selectedAction, "watching")
        XCTAssertFalse(confirmed.tvIsWatched(upNext), "A failed write drops the projection; confirmed state never changed.")

        var unticked = shown
        TrackingProjection.tvUntick(season: 2, episode: 4)(&unticked)
        XCTAssertFalse(unticked.tvIsWatched(TVEpisodeState(seasonNumber: 2, episodeNumber: 4, watched: true)))
    }

    func testChoiceAndMarkProjections() throws {
        var finished = try decode(TVShowState.self, Self.showListJSON)
        finished.choice = "Dropped"
        finished.status = "Dropped"
        var dropped = UserMediaState(isTracked: true, status: "Dropped", tv: finished)
        TrackingProjection.tvChoice(status: "In progress")(&dropped)
        XCTAssertEqual(dropped.status, "Completed", "Watching on a finished show reads Completed (A-54).")
        XCTAssertNil(dropped.tv?.selectedAction)

        var paused = UserMediaState(isTracked: true, status: "In progress", tv: try decode(TVShowState.self, Self.showDetailJSON))
        TrackingProjection.tvChoice(status: "Paused")(&paused)
        XCTAssertEqual(paused.status, "Paused")
        XCTAssertEqual(paused.tv?.selectedAction, "pause")

        var marked = UserMediaState(isTracked: false, tv: TVShowState(watchedCount: 0, airedCount: 62,
                                                                      eye: TVEyeState(filled: false, tap: .markConfirm, count: 62)))
        TrackingProjection.tvMarked(season: nil)(&marked)
        XCTAssertTrue(marked.isTracked)
        XCTAssertEqual(marked.status, "Completed")
        XCTAssertEqual(marked.tv?.watchedCount, 62)
        XCTAssertEqual(marked.tv?.eye?.filled, true)
        XCTAssertTrue(marked.tvIsWatched(TVEpisodeState(seasonNumber: 3, episodeNumber: 2)))
        XCTAssertFalse(marked.tvIsWatched(TVEpisodeState(seasonNumber: 0, episodeNumber: 1)), "The show eye leaves specials (TV-004).")

        var midSeason = UserMediaState(isTracked: false, tv: TVShowState(watchedCount: 0, airedCount: 6,
                                                                         nextAiring: TVAiring(seasonNumber: 1, episodeNumber: 7, airDate: "2026-10-29")))
        TrackingProjection.tvMarked(season: nil)(&midSeason)
        XCTAssertEqual(midSeason.status, "In progress", "Marked mid-season reads Watching, caught up (TV-202).")
        XCTAssertEqual(midSeason.tv?.caughtUp, true)

        var seasonMark = UserMediaState(isTracked: false, tv: TVShowState())
        TrackingProjection.tvMarked(season: 0)(&seasonMark)
        XCTAssertTrue(seasonMark.tvIsWatched(TVEpisodeState(seasonNumber: 0, episodeNumber: 2)))
        XCTAssertTrue(seasonMark.tvIsMarked(TVSeasonState(seasonNumber: 0)))
    }

    // MARK: Fixtures

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private func object<T: Encodable>(_ value: T) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder.api.encode(value)) as? [String: Any])
    }

    static func client() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [TVDataURLProtocol.self]
        return APIClient(baseURL: URL(string: "https://example.com")!, session: URLSession(configuration: configuration))
    }

    static func libraryItem(_ id: String, _ title: String, _ updatedAt: String?, mediaType: String = "movie") -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: MediaSummary(
                ref: MediaRef(itemId: nil, source: "tmdb", mediaType: mediaType, mediaId: id, seasonNumber: nil, episodeNumber: nil),
                title: title,
                posterUrl: nil,
                posterOrientation: .portrait
            ),
            tracking: TrackingState(trackingId: Int(id) ?? 1, status: "In progress", rating: nil, progress: nil, repeats: nil,
                                    startDate: nil, endDate: nil, notes: nil, updatedAt: updatedAt)
        )
    }

    static func profile(enabled: [String]) -> UserProfile {
        UserProfile(
            id: 1, username: "armaan", displayName: "Armaan", email: nil, bio: nil, pronouns: nil, location: nil,
            avatarUrl: nil, profileBackdropUrl: nil, profileBackdropItem: nil, isPrivate: false,
            viewerRelationship: ViewerRelationship(following: false, followedBy: false, requested: false, blocked: false),
            counts: ProfileCounts(followers: 0, following: 0, diaryEntries: 0, lists: 0),
            hof: [:],
            preferences: UserPreferences(enabledMediaTypes: enabled, dateFormat: nil, timeFormat: nil, weekStartDay: nil,
                                         quickWatchDate: nil, releaseNotificationsEnabled: false, dailyDigestEnabled: false)
        )
    }

    static func diaryEntryJSON(mediaType: String, extra: String) -> String {
        let numbers = mediaType == "episode" ? #","season_number":1,"episode_number":4"# : mediaType == "season" ? #","season_number":1"# : ""
        let titles = mediaType == "episode" ? #","show_title":"Breaking Bad","episode_title":"Cancer Man""# : ""
        return """
        {"id":31,"user":{"id":1,"username":"armaan","display_name":"Armaan","avatar_url":null},
         "media":{"ref":{"source":"tmdb","media_type":"\(mediaType)","media_id":"1396"\(numbers)},"title":"Breaking Bad"\(titles)},
         "consumed_at":"2026-10-25T00:00:00Z","rating":"8.0","review_title":null,"review":"","contains_spoilers":false,
         "liked":false,"is_rewatch":false,"is_true_reread":null,"book_journey_id":null,"tags":[],"visibility":"public",
         "like_count":0,"viewer_has_liked":false,"created_at":null,"updated_at":null\(extra)}
        """
    }

    static let showDetailJSON = """
    {"version":1793051512345,"in_library":true,"status":"In progress","choice":null,"selected_action":"watching","started":true,
     "finished":false,"caught_up":false,"watched_count":13,"aired_count":20,
     "up_next":{"season_number":2,"episode_number":4,"title":"Fly","air_date":"2010-05-23","is_new":false},
     "next_airing":null,"premiere":null,"rewatch":null,"last_watched_on":"2026-10-25","remove_count":13,
     "rating_source_diary_entry_id":null,"like_source_diary_entry_id":null,"like_is_independent":true,
     "eye":{"filled":false,"tap":"mark_confirm","count":20,"prompt":"Mark all 20 aired episodes watched?","message":null},
     "available_actions":["pause","drop","log","remove","rate"],
     "action_reasons":{"planning":"You've started this show. Pause or drop it instead."},
     "composer":{"default_target":"season:1","targets":[
       {"id":"season:1","level":"season","season_number":1,"episode_number":null,"title":"Season 1","date":"2026-10-26","rating":"4.0","liked":true,"is_rewatch":false,"claims_viewing_id":null,"available":true,"reason":null},
       {"id":"season:2","level":"season","season_number":2,"episode_number":null,"title":"Season 2","date":"2026-10-26","rating":null,"liked":false,"is_rewatch":false,"claims_viewing_id":null,"available":true,"reason":null},
       {"id":"season:0","level":"season","season_number":0,"episode_number":null,"title":"Specials","date":"2026-10-26","rating":null,"liked":false,"is_rewatch":false,"claims_viewing_id":null,"available":true,"reason":null},
       {"id":"show","level":"show","season_number":null,"episode_number":null,"title":"Whole show","date":"2026-10-26","rating":"4.5","liked":false,"is_rewatch":false,"claims_viewing_id":null,"available":true,"reason":null}]},
     "season":null,"episode":null}
    """

    static let showListJSON = """
    {"version":1793051500000,"in_library":true,"status":"Completed","choice":null,"started":true,"finished":true,"caught_up":true,
     "watched_count":20,"aired_count":20,"up_next":null,"next_airing":{"season_number":3,"episode_number":1,"air_date":"2027-03-12"},
     "premiere":null,"rewatch":null,"last_watched_on":"2026-10-20"}
    """
}

// MARK: Test doubles

final class TVDataTrackingRepository: TrackingRepository {
    struct ListRequest: Equatable {
        let mediaType: String
        let page: String?
        let status: String?
    }

    var listRequests: [ListRequest] = []
    var listResponse = PagedResponse<Spine.LibraryItem>(count: 0, next: nil, previous: nil, results: [])
    var listResponses: [String: PagedResponse<Spine.LibraryItem>] = [:]
    var continueWatchingResponse: PagedResponse<Spine.LibraryItem>?
    var continueWatchingPages: [String?] = []
    var actions: [(source: String, mediaId: String, action: TVAction, request: TVActionRequest)] = []
    var actionResponses: [TVActionResponse] = []
    var actionError: Error?

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        listRequests.append(ListRequest(mediaType: mediaType, page: page, status: status))
        return listResponses[mediaType] ?? listResponse
    }

    func continueWatching(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        guard let continueWatchingResponse else { return try await continueWatchingThroughDefault(page: page) }
        continueWatchingPages.append(page)
        return continueWatchingResponse
    }

    /// The protocol extension's default, which test doubles without their own `continueWatching` get.
    func continueWatchingThroughDefault(page: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        try await TVDataDefaultTrackingRepository(base: self).continueWatching(page: page)
    }

    func performTVAction(source: String, mediaId: String, action: TVAction, request: TVActionRequest) async throws -> TVActionResponse {
        actions.append((source, mediaId, action, request))
        if let actionError { throw actionError }
        return actionResponses.isEmpty ? TVActionResponse(tracking: nil, envelope: .empty) : actionResponses.removeFirst()
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

/// A tracking repository that implements only the required calls, like the existing test doubles, so it uses
/// every new protocol default.
private struct TVDataDefaultTrackingRepository: TrackingRepository {
    let base: TVDataTrackingRepository

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        try await base.list(mediaType: mediaType, page: page, status: status, query: query)
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState { fatalError("Not used") }
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
}

/// Implements only the required diary calls plus `delete(id:)`, so `delete(id:expectedVersion:mutationId:)` and
/// `createTV` use their protocol defaults.
final class TVDataDiaryRepository: DiaryRepository {
    var deletedIds: [Int] = []
    var createdRequests: [DiaryEntryWriteRequest] = []
    var createResponse: DiaryEntry?

    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry {
        createdRequests.append(request)
        guard let createResponse else { fatalError("No scripted entry") }
        return createResponse
    }

    func delete(id: Int) async throws {
        deletedIds.append(id)
    }

    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}

final class TVDataURLProtocol: URLProtocol {
    nonisolated(unsafe) static var requests: [URLRequest] = []
    nonisolated(unsafe) static var bodies: [[String: Any]] = []
    nonisolated(unsafe) static var status = 200
    nonisolated(unsafe) static var response = Data()

    static func reset() {
        requests = []
        bodies = []
        status = 200
        response = Data()
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
            body = data
        }
        if let body, let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] { Self.bodies.append(json) }
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.status, httpVersion: nil,
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.status == 204 ? Data() : Self.response)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
