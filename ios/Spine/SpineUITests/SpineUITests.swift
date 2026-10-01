//
//  SpineUITests.swift
//  SpineUITests
//
//  Created by Armaan Dave on 6/20/26.
//

import XCTest

final class SpineUITests: XCTestCase {

    override func setUpWithError() throws {
        // Put setup code here. This method is called before the invocation of each test method in the class.

        // In UI tests it is usually best to stop immediately when a failure occurs.
        continueAfterFailure = false

        // In UI tests it’s important to set the initial state - such as interface orientation - required for your tests before they run. The setUp method is a good place to do this.
    }

    override func tearDownWithError() throws {
        // Put teardown code here. This method is called after the invocation of each test method in the class.
    }

    @MainActor
    func testExample() throws {
        // UI tests must launch the application that they test.
        let app = XCUIApplication()
        app.launch()

        // Use XCTAssert and related functions to verify your tests produce the correct results.
        // XCUIAutomation Documentation
        // https://developer.apple.com/documentation/xcuiautomation
    }

    @MainActor
    func testLiveMediaDetailNavigation() throws {
        let environment = ProcessInfo.processInfo.environment
        guard environment["SPINE_RUN_LIVE_NAVIGATION_TEST"] == "1" else {
            throw XCTSkip("Opt in on a signed-in device to test read-only live media navigation.")
        }
        let app = XCUIApplication()
        app.launch()
        let search = app.tabBars.buttons["Search"]
        XCTAssertTrue(search.waitForExistence(timeout: 20), "Sign in before running this read-only test.")
        search.tap()

        for (type, title) in [
            ("Movies", "Inception"), ("TV", "Breaking Bad"),
            ("Anime", "Death Note"), ("Manga", "Berserk"),
            ("Games", "Hades"), ("Books", "The Hobbit"),
            ("Comics", "Watchmen"), ("Music", "Random Access Memories")
        ] {
            let clear = app.buttons["Clear search"]
            if clear.exists { clear.tap() }
            let lens = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Media type, ")).firstMatch
            XCTAssertTrue(lens.waitForExistence(timeout: 10))
            lens.tap()
            let typeButton = app.buttons[type]
            let rail = app.scrollViews["Media type picker"]
            for _ in 0..<5 where !typeButton.isHittable { rail.swipeLeft() }
            XCTAssertTrue(typeButton.isHittable, "Missing search type: \(type)")
            typeButton.tap()
            let field = app.textFields.firstMatch
            XCTAssertTrue(field.waitForExistence(timeout: 5))
            field.tap()
            field.typeText(title + "\n")
            let result = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", title)).firstMatch
            XCTAssertTrue(result.waitForExistence(timeout: 45), "No live \(type) result for \(title)")
            result.tap()
            let poster = app.buttons["media-detail.poster"].firstMatch
            XCTAssertTrue(poster.waitForExistence(timeout: 45), "\(type) detail failed to render")
            XCTAssertTrue(app.buttons["More"].firstMatch.exists)
            app.swipeUp()
            XCTAssertEqual(app.state, .runningForeground, "\(type) detail crashed while scrolling")
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = "Live \(type) detail"
            attachment.lifetime = .keepAlways
            add(attachment)
            app.buttons["Back"].firstMatch.tap()
            XCTAssertTrue(field.waitForExistence(timeout: 10), "\(type) detail failed to dismiss")
        }
    }


    @MainActor
    func testLaunchPerformance() throws {
        // This measures how long it takes to launch your application.
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            XCUIApplication().launch()
        }
    }
}
