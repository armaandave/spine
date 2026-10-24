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

    func testDebugBuildsFallBackToTheLocalServerNotProduction() {
        let local = URL(string: AppConfig.localAPIBaseURL)!
        let cases: [([String: String], [String: Any]?)] = [
            ([:], nil),
            ([:], [:]),
            (["SPINE_API_BASE_URL": ""], ["SpineAPIBaseURL": ""]),
        ]
        for (environment, info) in cases {
            XCTAssertEqual(AppConfig.resolvedAPIBaseURL(environment: environment, infoDictionary: info), local)
        }
    }

    func testDebugBuildsIgnoreProductionOverrides() {
        let url = AppConfig.resolvedAPIBaseURL(
            environment: ["SPINE_API_BASE_URL": production.absoluteString],
            infoDictionary: ["SpineAPIBaseURL": production.absoluteString]
        )

        XCTAssertEqual(url.absoluteString, AppConfig.localAPIBaseURL)
        XCTAssertEqual(
            AppConfig.resolvedAPIBaseURL(environment: ["SPINE_API_BASE_URL": production.absoluteString], infoDictionary: localBuild)
                .absoluteString,
            "http://Armaans-MacBook-Pro.local:8001"
        )
        for value in ["https://API.Spine-API.com", "http://127.0.0.1:8000", "http://localhost:8000/"] {
            XCTAssertEqual(
                AppConfig.resolvedAPIBaseURL(environment: ["SPINE_API_BASE_URL": value], infoDictionary: nil).absoluteString,
                AppConfig.localAPIBaseURL,
                value
            )
        }
    }
}
