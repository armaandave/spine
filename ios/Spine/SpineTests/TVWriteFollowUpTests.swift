import XCTest
@testable import Spine

/// The follow-up after a TV write: the earlier-episodes question first (TV-107), then the season-finish composer
/// once (TV-403).
@MainActor
final class TVWriteFollowUpTests: XCTestCase {
    private var tracking: TVDataTrackingRepository!
    private var media: TVDataMediaRepository!
    private var followUp: TVWriteFollowUp!

    override func setUp() async throws {
        try await super.setUp()
        tracking = TVDataTrackingRepository()
        media = TVDataMediaRepository()
        followUp = TVWriteFollowUp(showRef: Self.showRef, trackingRepository: tracking, mediaRepository: media)
    }

    func testTV107AsksBeforeOpeningTheComposerAndYesSendsMarkEarlier() async throws {
        // Ticking S3 E1 first finishes a one-episode Season 3 and leaves Seasons 1-2 unwatched.
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 3),
                                        earlierUnwatched: Self.question(offer: 3)))
        XCTAssertEqual(followUp.question?.question, "Also mark Seasons 1–2 watched?")
        XCTAssertNil(followUp.composer, "The question comes before the composer.")
        XCTAssertTrue(media.detailRequests.isEmpty)

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
        XCTAssertEqual(media.detailRequests.map(\.mediaType), ["season"])
        XCTAssertEqual(media.detailRequests.map(\.seasonNumber), [3])
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
        XCTAssertEqual(media.detailRequests.count, 1, "The composer opens once.")
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
        XCTAssertEqual(media.detailRequests.map(\.seasonNumber), [1])
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
        XCTAssertTrue(media.detailRequests.isEmpty)

        // A Yes response naming another season than the write's own is ignored too.
        let other = TVWriteFollowUp(showRef: Self.showRef, trackingRepository: tracking, mediaRepository: media)
        other.handle(TVWriteEnvelope(changed: true, earlierUnwatched: Self.question(offer: 3)))
        tracking.actionResponses = [TVActionResponse(tracking: nil, envelope: TVWriteEnvelope(
            changed: true,
            finished: Self.finished(season: 2)
        ))]
        await other.answer(yes: true)
        XCTAssertNil(other.composer)
        XCTAssertTrue(media.detailRequests.isEmpty)
    }

    func testTV403AFinishedSeasonWithoutAQuestionOpensItsComposer() async throws {
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 1)))
        XCTAssertNil(followUp.question)
        await followUp.loading?.value
        let composer = try XCTUnwrap(followUp.composer)
        XCTAssertEqual(composer.finished.composer.initialTarget?.id, "season:1")
        XCTAssertEqual(composer.finished.composer.initialTarget?.date, "2026-10-24", "Dated with the finishing tick's day.")
        XCTAssertEqual(media.detailRequests, [MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399",
                                                       seasonNumber: 1, episodeNumber: nil)])
    }

    func testNothingToFollowUpDoesNothing() async {
        followUp.handle(.empty)
        followUp.handle(TVWriteEnvelope(changed: true))
        await followUp.loading?.value
        XCTAssertNil(followUp.question)
        XCTAssertNil(followUp.composer)
        XCTAssertTrue(media.detailRequests.isEmpty)
        XCTAssertTrue(tracking.actions.isEmpty)
    }

    func testAButtonAnswersOnceEvenWhenTheAlertAlsoDismisses() async {
        followUp.handle(TVWriteEnvelope(changed: true, earlierUnwatched: Self.question(offer: nil)))
        followUp.respond(yes: true)
        followUp.respond(yes: false)
        await followUp.loading?.value
        XCTAssertEqual(tracking.actions.map(\.action), [.markEarlier])
    }

    func testAFailedYesStillOffersTheFirstResponsesSeason() async {
        followUp.handle(TVWriteEnvelope(changed: true, finished: Self.finished(season: 3),
                                        earlierUnwatched: Self.question(offer: 3)))
        tracking.actionError = APIError.httpStatus(503, #"{"error":{"code":"tv_details_unavailable","message":"TV details are unavailable right now"}}"#)
        await followUp.answer(yes: true)
        XCTAssertEqual(followUp.errorMessage, "TV details are unavailable right now")
        XCTAssertEqual(followUp.composer?.finished.seasonNumber, 3)
    }

    func testASeasonOrEpisodeRefActsOnTheShow() {
        let season = MediaRef(itemId: nil, source: "tmdb", mediaType: "season", mediaId: "1399", seasonNumber: 2, episodeNumber: nil)
        let followUp = TVWriteFollowUp(showRef: season, trackingRepository: tracking, mediaRepository: media)
        XCTAssertEqual(followUp.showRef, Self.showRef)
    }

    // MARK: Fixtures

    private static let showRef = MediaRef(itemId: nil, source: "tmdb", mediaType: "tv", mediaId: "1399",
                                          seasonNumber: nil, episodeNumber: nil)

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

final class TVDataMediaRepository: MediaRepository {
    var detailRequests: [MediaRef] = []

    func detail(ref: MediaRef) async throws -> MediaDetail {
        detailRequests.append(ref)
        return MediaDetail(ref: ref, title: ref.seasonNumber.map { "Season \($0)" } ?? "A Show")
    }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}
