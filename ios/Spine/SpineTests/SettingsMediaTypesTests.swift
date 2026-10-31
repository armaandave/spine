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

    func testPickerTypesKeepOneOrderWhateverOrderTheyArriveIn() {
        // The profile sends TV first, /meta and Stats send movies first: every picker shows the same.
        XCTAssertEqual(APIConstants.pickerMediaTypes(enabled: ["tv", "book", "movie"]), ["movie", "tv", "book"])
        XCTAssertEqual(APIConstants.pickerMediaTypes(enabled: []), APIConstants.fallbackMediaTypes)
        XCTAssertEqual(
            APIConstants.pickerMediaTypes(enabled: ["boardgame", "season", "tv"]),
            ["tv", "boardgame"],
            "A type the app doesn't list yet still shows, last"
        )
    }

    func testLibraryOpensOnTheRememberedTypesAndFollowsSettings() async throws {
        let meta = try Self.meta(
            mediaTypes: ["tv", "season", "episode", "movie", "anime", "manga", "book"],
            enabledMediaTypes: ["movie", "tv", "book"]
        )
        let viewModel = LibraryViewModel(
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            trackingRepository: APITrackingRepository(client: Self.unusedClient()),
            filterOptionsRepository: APIFilterOptionsRepository(client: Self.unusedClient()),
            enabledMediaTypes: ["tv", "book", "movie"],
            onUnauthorized: {}
        )
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv", "book"], "Right before anything loads")

        await viewModel.loadMeta()
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv", "book"], "The server agrees, so nothing moves")

        viewModel.setEnabledMediaTypes(["movie", "tv", "anime", "book"])
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv", "anime", "book"], "Turning anime back on brings it back")
    }

    func testDiaryOpensOnTheRememberedTypesAndLeavesOneTurnedOff() {
        let viewModel = DiaryViewModel(
            diaryRepository: APIDiaryRepository(client: Self.unusedClient()),
            filterOptionsRepository: APIFilterOptionsRepository(client: Self.unusedClient()),
            enabledMediaTypes: ["tv", "movie", "anime"],
            onUnauthorized: {}
        )
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv", "anime"], "Right before anything loads")

        viewModel.filter.selectMediaType("movie")
        XCTAssertFalse(viewModel.setEnabledMediaTypes(["movie", "tv", "anime"]))
        XCTAssertEqual(viewModel.filter.mediaType, "movie")

        viewModel.filter.selectMediaType("anime")
        XCTAssertTrue(viewModel.setEnabledMediaTypes(["movie", "tv"]), "A type just turned off reloads the diary")
        XCTAssertEqual(viewModel.mediaTypes, ["movie", "tv"])
        XCTAssertNil(viewModel.filter.mediaType, "Back to All")
    }

    func testStatsOpensOnTheOwnersTypesOverTheLastPagesOnes() throws {
        let suite = "spine.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        StatsPickerCache(defaults: defaults).setMediaTypes(APIConstants.fallbackMediaTypes, for: nil)
        let repository = APIProfileRepository(client: Self.unusedClient())

        let own = StatsViewModel(profileRepository: repository, enabledMediaTypes: ["tv", "movie"], defaults: defaults, onUnauthorized: {})
        XCTAssertEqual(own.mediaTypes, ["movie", "tv"], "Settings changed since the last page: the owner's types win")

        let unknown = StatsViewModel(profileRepository: repository, defaults: defaults, onUnauthorized: {})
        XCTAssertEqual(unknown.mediaTypes, APIConstants.fallbackMediaTypes, "Without them, the last page's types")
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
