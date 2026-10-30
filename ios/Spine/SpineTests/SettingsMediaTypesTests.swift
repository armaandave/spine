import XCTest
@testable import Spine

@MainActor
final class SettingsMediaTypesTests: XCTestCase {
    func testSeasonsAndEpisodesGetNoToggleOfTheirOwn() async throws {
        let meta = try JSONDecoder.api.decode(MetaResponse.self, from: Data("""
        {
          "version": "1",
          "media_types": ["tv", "season", "episode", "movie"],
          "sources": {},
          "status_choices": [],
          "source_choices": []
        }
        """.utf8))
        let viewModel = ProfileSettingsViewModel(
            profileRepository: APIProfileRepository(client: APIClient(
                baseURL: URL(string: "https://example.com")!,
                tokenProvider: .shared,
                session: URLSession(configuration: .ephemeral)
            )),
            mediaRepository: MetaOnlyMediaRepository(meta: meta),
            onUnauthorized: {}
        )

        await viewModel.loadOptions()

        XCTAssertEqual(viewModel.mediaTypes, ["tv", "movie"])
    }
}
