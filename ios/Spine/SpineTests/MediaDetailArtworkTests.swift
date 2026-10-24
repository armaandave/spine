import XCTest
@testable import Spine

@MainActor
final class MediaDetailArtworkTests: XCTestCase {
    func testMusicBrainzEnablesPosterCustomizationOnly() {
        XCTAssertTrue(MediaArtworkCustomization.supportsPoster(source: "musicbrainz", mediaType: "music"))
        XCTAssertFalse(MediaArtworkCustomization.supportsBackdrop(source: "musicbrainz", mediaType: "music"))
        XCTAssertFalse(MediaArtworkCustomization.supportsLogo(source: "musicbrainz", mediaType: "music"))
    }

    func testArtworkSavesUpdateArtworkAndPreserveLoadedDetail() throws {
        let original = makeDetail()
        let viewModel = makeViewModel(mediaRepository: ArtworkScriptedMediaRepository(results: []))
        viewModel.detail = original
        var changedRef: MediaRef?
        let observer = NotificationCenter.default.addObserver(
            forName: .mediaStateDidChange,
            object: nil,
            queue: nil
        ) { notification in
            changedRef = notification.userInfo?["ref"] as? MediaRef
        }
        defer { NotificationCenter.default.removeObserver(observer) }

        let selectedPosterURL = "https://example.com/selected-poster.jpg"
        viewModel.applyPosterSave(PosterSaveResponse(
            posterUrl: selectedPosterURL,
            customPosterUrl: selectedPosterURL,
            posterAccentColor: "#123456"
        ))

        let selectedBackdropURL = "https://example.com/selected-backdrop.jpg"
        viewModel.applyBackdropSave(BackdropSaveResponse(
            backdropUrl: selectedBackdropURL,
            customBackdropUrl: selectedBackdropURL
        ))

        let selectedLogoURL = "https://example.com/selected-logo.png"
        viewModel.applyLogoSave(LogoSaveResponse(
            logoUrl: selectedLogoURL,
            customLogoUrl: selectedLogoURL,
            logoWidth: 1_200,
            logoHeight: 400,
            logoAspectRatio: 3
        ))

        let updated = try XCTUnwrap(viewModel.detail)
        XCTAssertEqual(updated.posterUrl, selectedPosterURL)
        XCTAssertEqual(updated.customPosterUrl, selectedPosterURL)
        XCTAssertEqual(updated.displayPosterURL, selectedPosterURL)
        XCTAssertEqual(updated.posterAccentColor, "#123456")
        XCTAssertEqual(changedRef, original.ref)

        XCTAssertEqual(updated.backdropUrl, original.backdropUrl)
        XCTAssertEqual(updated.customBackdropUrl, selectedBackdropURL)
        XCTAssertEqual(updated.displayBackdropURL, selectedBackdropURL)

        XCTAssertEqual(updated.logoUrl, selectedLogoURL)
        XCTAssertEqual(updated.customLogoUrl, selectedLogoURL)
        XCTAssertEqual(updated.displayLogoURL, selectedLogoURL)
        XCTAssertEqual(updated.logoWidth, 1_200)
        XCTAssertEqual(updated.logoHeight, 400)
        XCTAssertEqual(updated.logoAspectRatio, 3)

        XCTAssertEqual(updated.ref, original.ref)
        XCTAssertEqual(updated.title, original.title)
        XCTAssertEqual(updated.subtitle, original.subtitle)
        XCTAssertEqual(updated.overview, original.overview)
        XCTAssertEqual(updated.synopsis, original.synopsis)
        XCTAssertEqual(updated.imageUrl, original.imageUrl)
        XCTAssertEqual(updated.posterOrientation, original.posterOrientation)
        XCTAssertEqual(updated.posterAspectRatio, original.posterAspectRatio)
        XCTAssertEqual(updated.posterWidth, original.posterWidth)
        XCTAssertEqual(updated.posterHeight, original.posterHeight)
        XCTAssertEqual(updated.releaseDate, original.releaseDate)
        XCTAssertEqual(updated.defaultSource, original.defaultSource)
        XCTAssertEqual(updated.userState, original.userState)
        XCTAssertEqual(updated.details, original.details)
    }

    func testFailedRefreshKeepsLastSuccessfullyLoadedDetail() async throws {
        let loadedDetail = makeDetail()
        let repository = ArtworkScriptedMediaRepository(results: [
            .success(loadedDetail),
            .failure(ArtworkRefreshError.failed),
        ])
        let viewModel = makeViewModel(mediaRepository: repository)

        await viewModel.load()

        XCTAssertEqual(viewModel.detail?.ref, loadedDetail.ref)
        XCTAssertEqual(viewModel.detail?.displayPosterURL, loadedDetail.displayPosterURL)
        XCTAssertNil(viewModel.errorMessage)

        await viewModel.load()

        let preserved = try XCTUnwrap(viewModel.detail)
        XCTAssertEqual(preserved.ref, loadedDetail.ref)
        XCTAssertEqual(preserved.title, loadedDetail.title)
        XCTAssertEqual(preserved.displayPosterURL, loadedDetail.displayPosterURL)
        XCTAssertEqual(preserved.displayBackdropURL, loadedDetail.displayBackdropURL)
        XCTAssertEqual(preserved.displayLogoURL, loadedDetail.displayLogoURL)
        XCTAssertEqual(viewModel.errorMessage, "Refresh failed.")
        XCTAssertFalse(viewModel.isLoading)
    }

    func testSuccessfulUntrackedRefreshClearsStaleTrackingState() async {
        let loadedDetail = makeDetail()
        let viewModel = makeViewModel(
            mediaRepository: ArtworkScriptedMediaRepository(results: [.success(loadedDetail)])
        )
        var tracked = loadedDetail
        tracked.userState = UserMediaState(isTracked: true, trackingId: 44, status: "In progress")
        viewModel.detail = tracked

        await viewModel.load()

        XCTAssertNotNil(viewModel.detail)
        XCTAssertEqual(viewModel.viewer?.isTracked, false)
        XCTAssertNil(viewModel.viewer?.status)
    }

    func testPendingExternalRatingsPollUntilReadyWithoutReloadingDetail() async throws {
        let ready = MediaExternalRatingsResponse(
            externalRatings: [
                ExternalRating(
                    source: "Steam",
                    value: "92%",
                    voteCount: 123_456,
                    maxValue: "100%",
                    url: "https://store.steampowered.com/app/1245620/"
                ),
            ],
            externalRatingsPreparation: .ready
        )
        let repository = ArtworkScriptedMediaRepository(
            results: [],
            ratingResults: [.success(ready)]
        )
        let viewModel = makeViewModel(
            mediaRepository: repository,
            pollInterval: .zero,
            maxPollAttempts: 2
        )
        viewModel.detail = makeDetail().replacingExternalRatings(with: MediaExternalRatingsResponse(
            externalRatings: [
                ExternalRating(
                    source: "IGDB",
                    value: "96",
                    voteCount: 5_000,
                    maxValue: "100",
                    url: "https://www.igdb.com/games/elden-ring"
                ),
                ExternalRating(
                    source: "Metacritic",
                    value: "94",
                    voteCount: nil,
                    maxValue: "100",
                    url: "https://www.metacritic.com/game/elden-ring/"
                ),
            ],
            externalRatingsPreparation: MediaExternalRatingsPreparation(
                state: .pending,
                retryAfterSeconds: 2
            )
        ))

        await viewModel.pollExternalRatingsIfNeeded()

        XCTAssertEqual(
            viewModel.detail?.externalRatings?.map(\.source),
            ["Steam", "IGDB", "Metacritic"]
        )
        XCTAssertEqual(viewModel.detail?.externalRatingsPreparation?.state, .ready)
        let readyRequestCount = await repository.ratingRequestCount
        XCTAssertEqual(readyRequestCount, 1)
    }

    func testExternalRatingPollingStopsAfterBoundedPendingResponses() async {
        let pending = MediaExternalRatingsResponse(
            externalRatings: [],
            externalRatingsPreparation: MediaExternalRatingsPreparation(
                state: .pending,
                retryAfterSeconds: 2
            )
        )
        let repository = ArtworkScriptedMediaRepository(
            results: [],
            ratingResults: [.success(pending)]
        )
        let viewModel = makeViewModel(
            mediaRepository: repository,
            pollInterval: .zero,
            maxPollAttempts: 1
        )
        viewModel.detail = makeDetail(preparation: pending.externalRatingsPreparation)

        await viewModel.pollExternalRatingsIfNeeded()

        let pendingRequestCount = await repository.ratingRequestCount
        XCTAssertEqual(pendingRequestCount, 1)
    }

    private func makeViewModel(
        mediaRepository: MediaRepository,
        pollInterval: Duration? = nil,
        maxPollAttempts: Int = 30
    ) -> MediaDetailViewModel {
        MediaDetailViewModel(
            ref: makeDetail().ref,
            mediaRepository: mediaRepository,
            trackingRepository: ArtworkUnusedTrackingRepository(),
            diaryRepository: ArtworkUnusedDiaryRepository(),
            onUnauthorized: {},
            externalRatingPollInterval: pollInterval,
            externalRatingMaxPollAttempts: maxPollAttempts
        )
    }

    private func makeDetail(
        preparation: MediaExternalRatingsPreparation? = nil
    ) -> MediaDetail {
        MediaDetail(
            ref: MediaRef(
                itemId: 101,
                source: "tmdb",
                mediaType: "movie",
                mediaId: "550",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Last Good Frame",
            subtitle: "A Motion Test",
            overview: "Loaded content should survive localized artwork changes.",
            synopsis: "The existing screen remains visible while refresh work happens.",
            imageUrl: "https://example.com/fallback.jpg",
            posterUrl: "https://example.com/default-poster.jpg",
            posterOrientation: .portrait,
            posterAspectRatio: 2.0 / 3.0,
            posterWidth: 1_000,
            posterHeight: 1_500,
            posterAccentColor: "#ABCDEF",
            logoUrl: "https://example.com/default-logo.png",
            logoWidth: 900,
            logoHeight: 300,
            logoAspectRatio: 3,
            releaseDate: "2026-07-14",
            defaultSource: "tmdb",
            userState: UserMediaState(isTracked: false, inLists: [4], hasLiked: true),
            backdropUrl: "https://example.com/default-backdrop.jpg",
            details: ["genre": .string("Drama")],
            externalRatingsPreparation: preparation,
            customPosterUrl: "https://example.com/old-custom-poster.jpg",
            customBackdropUrl: "https://example.com/old-custom-backdrop.jpg",
            customLogoUrl: "https://example.com/old-custom-logo.png"
        )
    }
}

private enum ArtworkRefreshError: LocalizedError {
    case failed

    var errorDescription: String? { "Refresh failed." }
}

private actor ArtworkScriptedMediaRepository: MediaRepository {
    private var results: [Result<MediaDetail, Error>]
    private var ratingResults: [Result<MediaExternalRatingsResponse, Error>]
    private(set) var ratingRequestCount = 0

    init(
        results: [Result<MediaDetail, Error>],
        ratingResults: [Result<MediaExternalRatingsResponse, Error>] = []
    ) {
        self.results = results
        self.ratingResults = ratingResults
    }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func discover(_ request: MediaDiscoverRequest) async throws -> PagedResponse<MediaSummary> { fatalError("Not used") }

    func detail(ref: MediaRef) async throws -> MediaDetail {
        guard !results.isEmpty else { fatalError("Unexpected detail request") }
        return try results.removeFirst().get()
    }

    func externalRatings(ref: MediaRef) async throws -> MediaExternalRatingsResponse {
        ratingRequestCount += 1
        guard !ratingResults.isEmpty else { fatalError("Unexpected external rating request") }
        return try ratingResults.removeFirst().get()
    }

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { [] }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

private struct ArtworkUnusedTrackingRepository: TrackingRepository {
    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        fatalError("Not used")
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }

    func updateBookProgress(
        source: String,
        mediaId: String,
        progressType: String,
        value: Decimal,
        notes: String
    ) async throws -> TrackingState {
        fatalError("Not used")
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        fatalError("Not used")
    }
}

private struct ArtworkUnusedDiaryRepository: DiaryRepository {
    func list(tag: String?) async throws -> [DiaryEntry] { fatalError("Not used") }
    func detail(id: Int) async throws -> DiaryEntry { fatalError("Not used") }
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry { fatalError("Not used") }
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState { fatalError("Not used") }
    func tags(query: String) async throws -> [DiaryTagSuggestion] { fatalError("Not used") }
}
