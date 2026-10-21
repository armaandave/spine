import XCTest
@testable import Spine

@MainActor
final class AppConfigTests: XCTestCase {
    private let production = URL(string: AppConfig.productionAPIBaseURL)!
    private let localBuild = ["SpineAPIBaseURL": "http://Armaans-MacBook-Pro.local:8001"]

    func testLaunchEnvironmentWinsOverTheInfoPlist() {
        let url = AppConfig.resolvedAPIBaseURL(
            environment: ["SPINE_API_BASE_URL": "http://127.0.0.1:8002"],
            infoDictionary: localBuild
        )

        XCTAssertEqual(url.absoluteString, "http://127.0.0.1:8002")
    }

    func testDebugBuildsUseTheInfoPlistURL() {
        let url = AppConfig.resolvedAPIBaseURL(environment: [:], infoDictionary: localBuild)

        XCTAssertEqual(url.absoluteString, "http://Armaans-MacBook-Pro.local:8001")
    }

    func testMissingOrEmptyValuesFallBackToProduction() {
        let cases: [([String: String], [String: Any]?)] = [
            ([:], nil),
            ([:], [:]),
            (["SPINE_API_BASE_URL": ""], ["SpineAPIBaseURL": ""]),
        ]
        for (environment, info) in cases {
            XCTAssertEqual(AppConfig.resolvedAPIBaseURL(environment: environment, infoDictionary: info), production)
        }
    }
}
