import XCTest
@testable import Spine

/// The follow-up after a TV write: the earlier-episodes question first (TV-107), then the season-finish composer
/// once (TV-403).
@MainActor
final class TVWriteFollowUpTests: XCTestCase {
    private var tracking: TVDataTrackingRepository!
    private var followUp: TVWriteFollowUp!

    override func setUp() async throws {
        try await super.setUp()
        tracking = TVDataTrackingRepository()
        followUp = TVWriteFollowUp(showRef: Self.showRef, trackingRepository: tracking)
    }

    func testTV107AsksBeforeOpeningTheComposerAndYesSendsMarkEarlier() async throws {
        // Ticking S3 E1 first finishes a one-episode Season 3 and leaves Seasons 1-2 unwatched.
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 3),
                                        earlierUnwatched: Self.question(offer: 3)))
        XCTAssertEqual(followUp.question?.question, "Also mark Seasons 1–2 watched?")
        XCTAssertNil(followUp.composer, "The question comes before the composer.")

        tracking.actionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(
            changed: true,
            finished: Self.finished(season: 1)
        ))]
        var folded: [TVActionResponse] = []
        followUp.onResponse = { folded.append($0) }
        await followUp.answer(yes: true)

        XCTAssertNil(followUp.question)
        let sent = try XCTUnwrap(tracking.actions.first)
        XCTAssertEqual(tracking.actions.count, 1)
        XCTAssertEqual(sent.source, "tmdb")
        XCTAssertEqual(sent.mediaId, "1399")
        XCTAssertEqual(sent.action, .markEarlier)
        XCTAssertNotNil(sent.request.mutationId)
        XCTAssertNil(sent.request.expectedVersion, "mark_earlier carries no expected_version")
        XCTAssertEqual(sent.request.from, TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1))
        XCTAssertEqual(sent.request.to, TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 10))
        XCTAssertEqual(sent.request.offerSeasonNumber, 3)
        XCTAssertEqual(folded.count, 1, "The page folds in the Yes response.")

        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 3, "The first response's season opens, not one Yes finished.")
        XCTAssertEqual(followUp.composer?.detail.ref.mediaType, "season")
        XCTAssertEqual(followUp.composer?.detail.ref.seasonNumber, 3)
    }

    func testTV107NoSendsNothingAndStillOffersTheFirstResponsesSeason() async {
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 3),
                                        earlierUnwatched: Self.question(offer: 3)))
        await followUp.answer(yes: false)
        XCTAssertTrue(tracking.actions.isEmpty)
        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 3)

        followUp.composerDismissed()
        XCTAssertNil(followUp.composer)
        await followUp.answer(yes: true)
        XCTAssertTrue(tracking.actions.isEmpty, "The question is used up once answered.")
        XCTAssertNil(followUp.composer, "The composer opens once.")
    }

    func testTV403YesOpensTheWritesOwnSeasonWhenYesFinishesIt() async {
        // A-55: ticking S1 E10 first with E1-E9 unwatched; Yes fills E1-E9 and opens the Season 1 composer.
        followUp.handle(TVWriteEnvelope(changed: true, earlierUnwatched: Self.question(offer: 1, seasons: nil)))
        tracking.actionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(
            changed: true,
            finished: Self.finished(season: 1)
        ))]
        await followUp.answer(yes: true)
        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 1)
        XCTAssertEqual(followUp.composer?.detail.ref.seasonNumber, 1)
    }

    func testTV107ASeasonOnlyTheYesFinishedGetsNoComposer() async {
        // Logging Season 3 first: the log isn't a tick or episode log, so there is no season of its own to offer.
        followUp.handle(TVWriteEnvelope(changed: true, earlierUnwatched: Self.question(offer: nil)))
        tracking.actionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(
            changed: true,
            finished: Self.finished(season: 2)
        ))]
        await followUp.answer(yes: true)
        XCTAssertEqual(tracking.actions.count, 1)
        XCTAssertNil(followUp.composer)

        // A Yes response naming another season than the write's own is ignored too.
        let other = TVWriteFollowUp(showRef: Self.showRef, trackingRepository: tracking)
        other.handle(TVWriteEnvelope(changed: true, earlierUnwatched: Self.question(offer: 3)))
        tracking.actionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(
            changed: true,
            finished: Self.finished(season: 2)
        ))]
        await other.answer(yes: true)
        XCTAssertNil(other.composer)
    }

    func testTV403AFinishedSeasonWithoutAQuestionOpensItsComposerInTheSameTurn() throws {
        // Nothing is loaded or awaited between the response and the composer.
        followUp.context = { Self.showDetail }
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 1)))
        XCTAssertNil(followUp.question)
        XCTAssertNil(followUp.loading, "Nothing is loaded first")
        let composer = try XCTUnwrap(followUp.composer)
        XCTAssertEqual(composer.finished.composer.initialTarget?.id, "season:1")
        XCTAssertEqual(composer.finished.composer.initialTarget?.date, "2026-10-24", "Dated with the finishing tick's day.")
        XCTAssertEqual(composer.detail.ref, Self.seasonRef(1))
        XCTAssertEqual(composer.detail.title, "Game of Thrones", "The header names the show")
        XCTAssertEqual(composer.detail.displayPosterURL, "https://img/s1.jpg", "with the season's poster")
        XCTAssertEqual(composer.detail.displayBackdropURL, "https://img/backdrop.jpg")
        XCTAssertTrue(followUp.isActive)
        followUp.composerDismissed()
        XCTAssertFalse(followUp.isActive)
    }

    func testTV403TheComposerHeaderComesFromWhatTheScreenAlreadyHas() {
        let finished = Self.finished(season: 2)
        // A season page finishing its own season opens on its own detail.
        let seasonPage = MediaDetail(ref: MediaRef(itemId: 9, source: "tmdb", mediaType: "season", mediaId: "1399",
                                                   seasonNumber: 2, episodeNumber: nil),
                                     title: "Game of Thrones", posterUrl: "https://img/s2-page.jpg")
        let fromSeason = TVWriteFollowUp.composerDetail(for: finished, showRef: Self.showRef, context: seasonPage)
        XCTAssertEqual(fromSeason.ref.itemId, 9)
        XCTAssertEqual(fromSeason.displayPosterURL, "https://img/s2-page.jpg")

        // A show with no poster for that season falls back to the show's own.
        let fromShow = TVWriteFollowUp.composerDetail(for: finished, showRef: Self.showRef, context: Self.showDetail)
        XCTAssertEqual(fromShow.ref, Self.seasonRef(2))
        XCTAssertEqual(fromShow.title, "Game of Thrones")
        XCTAssertEqual(fromShow.displayPosterURL, "https://img/show.jpg")

        // Continue Watching has the show's summary.
        let summary = MediaSummary(ref: Self.showRef, title: "Game of Thrones", posterUrl: "https://img/show.jpg",
                                   posterOrientation: .portrait)
        let fromHome = TVWriteFollowUp.composerDetail(for: finished, showRef: Self.showRef,
                                                      context: TVWriteFollowUp.context(show: summary))
        XCTAssertEqual(fromHome.ref, Self.seasonRef(2))
        XCTAssertEqual(fromHome.title, "Game of Thrones")
        XCTAssertEqual(fromHome.displayPosterURL, "https://img/show.jpg")

        // An episode page names its show in its details; its title is the episode's.
        let episodePage = MediaDetail(ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "episode", mediaId: "1399",
                                                    seasonNumber: 2, episodeNumber: 10),
                                      title: "Valar Morghulis", details: ["series_title": .string("Game of Thrones")])
        XCTAssertEqual(TVWriteFollowUp.composerDetail(for: finished, showRef: Self.showRef, context: episodePage).title,
                       "Game of Thrones")

        // With nothing known, the season's own name.
        let unknown = TVWriteFollowUp.composerDetail(for: finished, showRef: Self.showRef, context: nil)
        XCTAssertEqual(unknown.ref, Self.seasonRef(2))
        XCTAssertEqual(unknown.title, "Season 2")
    }

    func testNothingToFollowUpDoesNothing() async {
        followUp.handle(.empty)
        followUp.handle(TVWriteEnvelope(changed: true))
        await followUp.loading?.value
        XCTAssertNil(followUp.question)
        XCTAssertNil(followUp.composer)
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testAButtonAnswersOnceEvenWhenTheAlertAlsoDismisses() async {
        followUp.handle(TVWriteEnvelope(changed: true, earlierUnwatched: Self.question(offer: nil)))
        followUp.respond(yes: true)
        followUp.respond(yes: false)
        await followUp.loading?.value
        XCTAssertEqual(tracking.actions.map(\.action), [.markEarlier])
    }

    func testAFailedYesAsksAgainWithTheReasonAndTryAgainResendsTheSameMutation() async throws {
        // SW-700: a failed Yes isn't silent and doesn't use the question up.
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 3),
                                        earlierUnwatched: Self.question(offer: 3)))
        tracking.actionError = APIError.httpStatus(503, #"{"error":{"code":"provider_unavailable","reason":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#)
        await followUp.answer(yes: true)
        XCTAssertEqual(followUp.errorMessage, "TV details are unavailable right now")
        XCTAssertEqual(followUp.question?.question, "Also mark Seasons 1–2 watched?", "The question comes back")
        XCTAssertNil(followUp.composer, "The composer waits for the answer")
        XCTAssertTrue(followUp.isActive)

        tracking.actionError = nil
        await followUp.answer(yes: true)
        XCTAssertEqual(tracking.actions.count, 2)
        let first = try XCTUnwrap(tracking.actions.first?.request.mutationId)
        XCTAssertEqual(tracking.actions.last?.request.mutationId, first, "Try Again applies at most once (TV-705)")
        XCTAssertNil(followUp.errorMessage)
        XCTAssertNil(followUp.question)
        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 3)
    }

    func testAFailedYesThenNotNowStillOffersTheFirstResponsesSeason() async {
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 3),
                                        earlierUnwatched: Self.question(offer: 3)))
        tracking.actionError = APIError.network(URLError(.notConnectedToInternet))
        await followUp.answer(yes: true)
        XCTAssertNotNil(followUp.errorMessage)

        await followUp.answer(yes: false)
        XCTAssertEqual(tracking.actions.count, 1, "Not Now sends nothing")
        XCTAssertNil(followUp.errorMessage)
        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 3)
    }

    func testASeasonOrEpisodeRefActsOnTheShow() {
        let season = MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 2, episodeNumber: nil)
        let followUp = TVWriteFollowUp(showRef: season, trackingRepository: tracking)
        XCTAssertEqual(followUp.showRef, Self.showRef)
    }

    // MARK: Fixtures

    private static let showRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "tv", mediaId: "1399",
                                          seasonNumber: nil, episodeNumber: nil)

    private static func seasonRef(_ season: Int) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: season, episodeNumber: nil)
    }

    /// The show page: Season 1 has a poster of its own, Season 2 doesn't.
    private static let showDetail = MediaDetail(
        ref: MediaRef(itemId: 7, source: "tmdb", mediaType: "tv", mediaId: "1399", seasonNumber: nil, episodeNumber: nil),
        title: "Game of Thrones",
        posterUrl: "https://img/show.jpg",
        backdropUrl: "https://img/backdrop.jpg",
        seasons: [
            SeasonSummary(seasonNumber: 1, title: "Season 1", episodeCount: 10, imageUrl: "https://img/s1.jpg",
                          releaseDate: nil, completion: nil),
            SeasonSummary(seasonNumber: 2, title: "Season 2", episodeCount: 10, imageUrl: nil,
                          releaseDate: nil, completion: nil),
        ]
    )

    private static func finished(season: Int) -> TVFinished {
        TVFinished(seasonNumber: season, composer: TVComposer(defaultTarget: "season:\(season)", targets: [
            TVComposerTarget(id: "season:\(season)", level: .season, seasonNumber: season, title: "Season \(season)",
                             date: "2026-10-24"),
        ]))
    }

    private static func question(offer: Int?, seasons: [Int]? = [1, 2]) -> TVEarlierUnwatched {
        TVEarlierUnwatched(
            from: TVEpisodeCoordinate(seasonNumber: 1, episodeNumber: 1),
            to: TVEpisodeCoordinate(seasonNumber: 2, episodeNumber: 10),
            count: 19,
            seasons: seasons,
            question: seasons == nil ? "Also mark S1 E1–S2 E10 watched?" : "Also mark Seasons 1–2 watched?",
            offerSeasonNumber: offer
        )
    }
}
