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
    func testLocalBackendLoginAndLibrarySmoke() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let baseURL = environment["SPINE_API_BASE_URL"],
              let url = URL(string: baseURL),
              ["localhost", "127.0.0.1", "::1"].contains(url.host ?? ""),
              let username = environment["SPINE_TEST_USERNAME"],
              let password = environment["SPINE_TEST_PASSWORD"] else {
            throw XCTSkip("Set a local SPINE_API_BASE_URL and runtime test credentials to run live integration.")
        }
        let app = XCUIApplication()
        app.launchEnvironment["SPINE_API_BASE_URL"] = baseURL
        app.launch()
        let library = app.tabBars.buttons["Library"]
        if !library.waitForExistence(timeout: 5) {
            let signIn = app.buttons["onboarding.signIn"]
            if signIn.waitForExistence(timeout: 5) { signIn.tap() }
            let usernameField = app.textFields["auth.usernameOrEmail"]
            XCTAssertTrue(usernameField.waitForExistence(timeout: 10))
            usernameField.tap()
            usernameField.typeText(username)
            let passwordField = app.secureTextFields["auth.password"]
            passwordField.tap()
            passwordField.typeText(password)
            app.buttons["auth.submit"].tap()
        }
        XCTAssertTrue(library.waitForExistence(timeout: 20), "The real local API must authenticate the account.")
        library.tap()
        if !app.buttons["Games"].waitForExistence(timeout: 5) { library.tap() }
        XCTAssertTrue(app.buttons["Games"].waitForExistence(timeout: 10))
        app.buttons["Games"].tap()
        XCTAssertTrue(app.buttons["Playing"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["Paused"].exists)
        XCTAssertTrue(app.buttons["Dropped"].exists)
        app.tabBars.buttons["Profile"].tap()
        let lists = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Lists'")).firstMatch
        if !lists.waitForExistence(timeout: 5) { app.tabBars.buttons["Profile"].tap() }
        XCTAssertTrue(lists.waitForExistence(timeout: 10))
        lists.tap()
        let qaList = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Game Contract QA")).firstMatch
        XCTAssertTrue(qaList.waitForExistence(timeout: 10))
        qaList.tap()
        let fixtureTitle = environment["SPINE_TEST_GAME_TITLE"] ?? "10 UI Smoke"
        let fixture = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", fixtureTitle)).firstMatch
        for _ in 0..<8 where !fixture.isHittable { app.swipeUp() }
        XCTAssertTrue(fixture.waitForExistence(timeout: 10))
        fixture.tap()
        XCTAssertTrue(app.buttons["Track"].firstMatch.waitForExistence(timeout: 10))
        let track = try XCTUnwrap(app.buttons.matching(identifier: "Track").allElementsBoundByIndex.first { app.frame.contains($0.frame) })
        track.tap()
        let playing = app.buttons["Playing"]
        if playing.waitForExistence(timeout: 2) {
            playing.tap()
            XCTAssertTrue(app.buttons["Track"].firstMatch.waitForExistence(timeout: 10))
            try visibleTrackButton(app).tap()
        }
        try openProgressFromMenu(app)
        let hours = app.textFields["Total hours this playthrough"]
        XCTAssertTrue(hours.waitForExistence(timeout: 5))
        app.buttons["Clear time"].tap()
        app.buttons["Clear percentage"].tap()
        hours.tap()
        hours.typeText("0")
        let percentage = app.textFields["Percentage"]
        percentage.tap()
        percentage.typeText("0")
        app.navigationBars.buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["0% · 0h"].waitForExistence(timeout: 10))
        try visibleTrackButton(app).tap()
        try openProgressFromMenu(app)
        XCTAssertTrue(app.textFields["Total hours this playthrough"].waitForExistence(timeout: 5))
        app.buttons["Clear time"].tap()
        XCTAssertEqual(app.textFields["Total hours this playthrough"].value as? String, "Hours")
        XCTAssertEqual(app.textFields["Percentage"].value as? String, "0")
        app.navigationBars.buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["0%"].waitForExistence(timeout: 10))
        try visibleTrackButton(app).tap()
        try openProgressFromMenu(app)
        XCTAssertTrue(app.textFields["Total hours this playthrough"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.textFields["Total hours this playthrough"].value as? String, "Hours")
        XCTAssertEqual(app.textFields["Percentage"].value as? String, "0")
        app.navigationBars.buttons["Cancel"].tap()
        XCTAssertTrue(app.buttons["Rate"].firstMatch.waitForExistence(timeout: 5))
        let rate = try XCTUnwrap(app.buttons.matching(identifier: "Rate").allElementsBoundByIndex.last { app.frame.contains($0.frame) })
        rate.tap()
        let rating = app.sliders["media-detail.rating-picker"]
        XCTAssertTrue(rating.waitForExistence(timeout: 5))
        // SwiftUI's accessibility representation has no XCTest scrubber endpoints.
        // Tap the observed star control bounds to select the ninth half-star step.
        rating.coordinate(withNormalizedOffset: CGVector(dx: 0.85, dy: 0.5)).tap()
        XCTAssertEqual(rating.value as? String, "4.5 out of 5")
        let confirmRating = app.buttons["Confirm rating"]
        XCTAssertGreaterThanOrEqual(confirmRating.frame.width, 44)
        XCTAssertGreaterThanOrEqual(confirmRating.frame.height, 44)
        let ratingAttachment = XCTAttachment(screenshot: app.screenshot())
        ratingAttachment.name = "Local API rating before completion"
        ratingAttachment.lifetime = .keepAlways
        add(ratingAttachment)
        confirmRating.tap()
        XCTAssertTrue(app.sliders["media-log.rating"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.sliders["media-log.rating"].value as? String, "4.5/5")
        app.buttons["Clear percentage"].tap()
        let completionPercentage = app.textFields["Percentage"]
        completionPercentage.tap()
        completionPercentage.typeText("101")
        app.buttons["Done"].tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5))
        app.buttons["Log Completion"].tap()
        let saveError = app.staticTexts["media-log.save-error"]
        XCTAssertTrue(saveError.waitForExistence(timeout: 5))
        XCTAssertTrue(saveError.isHittable, "The fixed footer must show a save error without scrolling.")
        XCTAssertEqual(completionPercentage.value as? String, "101", "A failed save must preserve the draft.")
        let errorAttachment = XCTAttachment(screenshot: app.screenshot())
        errorAttachment.name = "Local API completion draft with visible validation error"
        errorAttachment.lifetime = .keepAlways
        add(errorAttachment)
        app.buttons["Close log"].tap()
        XCTAssertTrue(app.buttons["Rate"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons.matching(identifier: "Edit rating").allElementsBoundByIndex.contains { app.frame.contains($0.frame) })
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Local API game zero and independent clear"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @MainActor
    private func visibleTrackButton(_ app: XCUIApplication) throws -> XCUIElement {
        try XCTUnwrap(app.buttons.matching(identifier: "Track").allElementsBoundByIndex.last { app.frame.contains($0.frame) })
    }

    @MainActor
    private func openProgressFromMenu(_ app: XCUIApplication) throws {
        let grabber = app.buttons["Sheet Grabber"]
        XCTAssertTrue(grabber.waitForExistence(timeout: 5))
        grabber.swipeUp()
        XCTAssertTrue(app.buttons["Update Progress"].firstMatch.waitForExistence(timeout: 10))
        let update = try XCTUnwrap(app.buttons.matching(identifier: "Update Progress").allElementsBoundByIndex.last)
        update.tap()
    }

    @MainActor
    func testLaunchPerformance() throws {
        // This measures how long it takes to launch your application.
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            XCUIApplication().launch()
        }
    }
}
