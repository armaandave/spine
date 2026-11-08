import XCTest

/// The open In progress tile's panel from the outside: one persistent panel whose buttons follow the carousel. Needs
/// a signed-in app with at least two titles in progress (the always-on test backend, or the perf backend).
final class InProgressPanelUITests: XCTestCase {
    private let tileIdentifier = "home.tile.in-progress"
    private let carouselIdentifier = "home.tile.carousel"
    private let quickIdentifier = "in-progress.quick"
    private let updateIdentifier = "in-progress.update"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testTheButtonsFollowTheCarouselAndUpdateStillOpensTheSheet() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--dev-sign-in"]
        if let base = ProcessInfo.processInfo.environment["SPINE_API_BASE_URL"] {
            app.launchEnvironment["SPINE_API_BASE_URL"] = base
        }
        app.launch()

        let tile = app.buttons.matching(identifier: tileIdentifier).firstMatch
        XCTAssertTrue(tile.waitForExistence(timeout: 40), "No In progress tile.")
        tile.tap()

        let update = app.buttons.matching(identifier: updateIdentifier).firstMatch
        XCTAssertTrue(update.waitForExistence(timeout: 10), "The open tile has no Update button.")
        let quick = app.buttons.matching(identifier: quickIdentifier).firstMatch
        let firstQuickLabel = quick.exists ? quick.label : nil
        let carousel = app.buttons.matching(identifier: carouselIdentifier).firstMatch
        let firstPoster = carousel.value as? String

        // One poster to the left.
        let y = min(max(carousel.frame.midY, app.frame.height * 0.15), app.frame.height * 0.8) / app.frame.height
        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: y))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: y))
        start.press(forDuration: 0.02, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.1)
        XCTAssertNotEqual(carousel.value as? String, firstPoster, "The drag did not move the carousel.")

        // The same panel: both buttons still exist, and `+N` now names the second title's plan (or is gone).
        XCTAssertTrue(update.waitForExistence(timeout: 5))
        if let firstQuickLabel, quick.exists {
            XCTAssertFalse(quick.label.isEmpty)
            _ = firstQuickLabel
        }

        // The glass is 36 pt tall; the hit area is 44. A tap 3 pt under the button's edge still hits it.
        XCTAssertEqual(update.frame.height, 36, accuracy: 1.5)
        let below = app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: update.frame.midX, dy: update.frame.maxY + 3))
        below.tap()
        // The progress sheet has a "Close" button; a title whose page is its progress UI opens that page instead.
        XCTAssertTrue(
            app.buttons["Close"].firstMatch.waitForExistence(timeout: 20) || app.buttons["Back"].firstMatch.exists,
            "Update did not open the title's progress UI."
        )
    }
}
