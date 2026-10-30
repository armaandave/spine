import XCTest
@testable import Spine

@MainActor
final class SettingsMediaTypesTests: XCTestCase {
    func testSeasonsAndEpisodesGetNoToggleOfTheirOwn() async throws {
        let viewModel = ProfileSettingsViewModel(
            profileRepository: APIProfileRepository(client: Self.unusedClient()),
            mediaRepository: MetaOnlyMediaRepository(meta: try Self.meta(mediaTypes: ["tv", "season", "episode", "movie"])),
            onUnauthorized: {}
        )

        await viewModel.loadOptions()

        XCTAssertEqual(viewModel.mediaTypes, ["tv", "movie"])
    }

    func testLibraryOffersOnlyTheTypesTurnedOnAndGetsThemBack() async throws {
        let meta = try Self.meta(
            mediaTypes: ["tv", "season", "episode", "movie", "anime", "manga", "book"],
            enabledMediaTypes: ["movie", "tv", "book"]
        )
        let viewModel = LibraryViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            trackingRepository: APITrackingRepository(client: Self.unusedClient()),
            onUnauthorized: {}
        )

        await viewModel.loadMeta()
        XCTAssertEqual(viewModel.mediaTypes, ["tv", "movie", "book"], "The Library's own order, less anime and manga")

        viewModel.setEnabledMediaTypes(["movie", "tv", "anime", "book"])
        XCTAssertEqual(viewModel.mediaTypes, ["tv", "movie", "anime", "book"], "Turning anime back on brings it back")
    }

    func testDiaryOffersOnlyTheTypesTurnedOnAndLeavesOneTurnedOff() {
        let viewModel = DiaryViewModel(diaryRepository: APIDiaryRepository(client: Self.unusedClient()), onUnauthorized: {})
        XCTAssertEqual(viewModel.mediaTypes, APIConstants.fallbackMediaTypes)

        viewModel.filter.selectMediaType("movie")
        XCTAssertFalse(viewModel.setEnabledMediaTypes(["movie", "tv", "anime"]))
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv", "anime"])
        XCTAssertEqual(viewModel.filter.mediaType, "movie")

        viewModel.filter.selectMediaType("anime")
        XCTAssertTrue(viewModel.setEnabledMediaTypes(["movie", "tv"]), "A type just turned off reloads the diary")
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv"])
        XCTAssertNil(viewModel.filter.mediaType, "Back to All")
    }

    private static func meta(mediaTypes: [String], enabledMediaTypes: [String]? = nil) throws -> MetaResponse {
        var json: [String: Any] = [
            "version": "1",
            "media_types": mediaTypes,
            "sources": [String: Any](),
            "status_choices": [String](),
            "source_choices": [String](),
        ]
        json["enabled_media_types"] = enabledMediaTypes
        return try JSONDecoder.api.decode(MetaResponse.self, from: JSONSerialization.data(withJSONObject: json))
    }

    /// A client for a repository the test never calls.
    private static func unusedClient() -> APIClient {
        APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: .shared,
            session: URLSession(configuration: .ephemeral)
        )
    }
}
