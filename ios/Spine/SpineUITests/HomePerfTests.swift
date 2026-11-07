//
//  HomePerfTests.swift
//  SpineUITests
//
//  Performance harness for the Home tab and feed. Run through scripts/ios/perf-harness.sh,
//  which serves the repeatable data set (scripts/ios/perf/seed_perf.py) from a private local
//  backend. The tests read nothing from the app but accessibility labels, so they run
//  unchanged before and after the Home redesign. What they rely on:
//    - a feed card is a button whose label names a seeded title ("Perf ...") and its comment count
//      (today: "Perf Show 5, Watched 1 episode, 0 likes, 0 comments"); the author button beside it is not the card;
//    - the In progress "Update" control has the accessibility identifier "in-progress.update";
//    - an open post page has the like button "post.like" and a "Back" button;
//    - the progress sheet has a "Close" button.
//  If the redesign renames any of those, change the constants below, not the scenarios.
//

import XCTest

final class HomePerfTests: XCTestCase {
    // MARK: - What the scenarios look for

    private let cardLabel = NSPredicate(format: "label CONTAINS 'Perf ' AND label CONTAINS 'comment'")
    private let updateIdentifier = "in-progress.update"
    private let likeIdentifier = "post.like"
    private let backLabel = "Back"
    private let sheetCloseLabel = "Close"

    // MARK: - Iterations

    /// Every scenario measures this many runs (the task asks for at least 5).
    private var iterations: Int { Int(ProcessInfo.processInfo.environment["PERF_ITERATIONS"] ?? "") ?? 8 }
    /// Fast and default swipes through the feed in the scroll scenario.
    private let scrollSwipes: [XCUIGestureVelocity] = [.fast, .fast, .fast, .default, .default, .default]

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    // MARK: - Helpers

    /// `coldImages` empties the app's memory and disk image caches at launch (SPINE_PERF_COLD_IMAGES, Debug builds);
    /// PERF_COLD_IMAGES=1 in the run's environment (perf-harness.sh) does the same for any scenario.
    private func makeApp(coldImages: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        let base = ProcessInfo.processInfo.environment["SPINE_API_BASE_URL"] ?? "http://127.0.0.1:8021"
        app.launchEnvironment["SPINE_API_BASE_URL"] = base
        app.launchArguments = ["--dev-sign-in"]
        // The app's frame/memory/CPU sampler (Core/Environment/PerfProbe.swift) writes here.
        if let probe = ProcessInfo.processInfo.environment["PERF_PROBE_FILE"] {
            app.launchEnvironment["SPINE_PERF_PROBE_FILE"] = probe
        }
        if coldImages || ProcessInfo.processInfo.environment["PERF_COLD_IMAGES"] == "1" {
            app.launchEnvironment["SPINE_PERF_COLD_IMAGES"] = "1"
        }
        return app
    }

    private func firstCard(in app: XCUIApplication) -> XCUIElement {
        let buttons = app.buttons.matching(cardLabel)
        if buttons.count > 0 { return buttons.firstMatch }
        return app.descendants(matching: .any).matching(cardLabel).firstMatch
    }

    private func waitForFirstCard(_ app: XCUIApplication, timeout: TimeInterval = 40, file: StaticString = #filePath, line: UInt = #line) {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let card = app.buttons.matching(cardLabel).firstMatch
            if card.exists { return }
            if app.descendants(matching: .any).matching(cardLabel).firstMatch.exists { return }
            Thread.sleep(forTimeInterval: 0.05)
        }
        XCTFail("No feed card (label with 'Perf ' and 'comment') appeared. Is the backend seeded and running?", file: file, line: line)
    }

    private func launchToHome() -> XCUIApplication {
        let app = makeApp()
        app.launch()
        waitForFirstCard(app)
        return app
    }

    private func scrollToTop(_ app: XCUIApplication) {
        // The status bar tap is the system's scroll to top; swipes finish the job if it didn't reach.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.012)).tap()
        Thread.sleep(forTimeInterval: 1.2)
        var tries = 0
        while !app.staticTexts["Home"].isHittable, tries < 14 {
            app.swipeDown(velocity: .fast)
            tries += 1
        }
        Thread.sleep(forTimeInterval: 0.6)
    }

    private func settle(_ seconds: TimeInterval = 0.7) {
        Thread.sleep(forTimeInterval: seconds)
    }

    private func options(manual: Bool = true) -> XCTMeasureOptions {
        let options = XCTMeasureOptions()
        options.iterationCount = iterations
        if manual { options.invocationOptions = [.manuallyStart, .manuallyStop] }
        return options
    }

    private func animationMetrics(_ app: XCUIApplication) -> [XCTMetric] {
        [XCTClockMetric()]
    }


    /// Measured windows are also written to the log as "PERFMARK <test> start|stop <epoch seconds>", so
    /// scripts/ios/perf/report.py can line frame and memory samples up with them.
    private func begin() {
        NSLog("PERFMARK %@ start %.3f", name, Date().timeIntervalSince1970)
        startMeasuring()
    }

    private func end() {
        stopMeasuring()
        NSLog("PERFMARK %@ stop %.3f", name, Date().timeIntervalSince1970)
    }

    // MARK: - a) Cold launch to the first feed card

    @MainActor
    func testA_ColdLaunchToFirstCard() throws {
        let app = makeApp()
        // Prime once so sign-in, the keychain and the first image fetches are not the first-ever run.
        app.launch()
        waitForFirstCard(app)
        measure(metrics: [XCTClockMetric()], options: options()) {
            app.terminate()
            settle(1.0)
            begin()
            app.launch()
            waitForFirstCard(app)
            end()
        }
    }

    // MARK: - b) Feed scroll

    @MainActor
    func testB_FeedScroll() throws {
        let app = launchToHome()
        let metrics: [XCTMetric] = [XCTClockMetric()]
        measure(metrics: metrics, options: options()) {
            scrollToTop(app)
            begin()
            for velocity in scrollSwipes { app.swipeUp(velocity: velocity) }
            end()
        }
    }

    // MARK: - b2) Feed scroll with empty image caches

    /// A fresh process per iteration with both image caches emptied at launch, so every poster, backdrop and avatar the
    /// scroll shows is fetched and decoded during the window. Same swipes as testB_FeedScroll. Works on any build that
    /// has the Debug hook (the frozen baseline gets it with the shared PerfProbe and SpineApp files).
    @MainActor
    func testB2_FeedScrollColdImages() throws {
        let app = makeApp(coldImages: true)
        measure(metrics: [XCTClockMetric()], options: options()) {
            app.launch()
            waitForFirstCard(app)
            begin()
            for velocity in scrollSwipes { app.swipeUp(velocity: velocity) }
            end()
            app.terminate()
            settle(1.0)
        }
    }

    // MARK: - c) Open a post page from a card, and come back

    @MainActor
    func testC1_OpenPost() throws {
        let app = launchToHome()
        measure(metrics: animationMetrics(app), options: options()) {
            let card = firstCard(in: app)
            XCTAssertTrue(card.waitForExistence(timeout: 10))
            begin()
            card.tap()
            XCTAssertTrue(app.buttons[likeIdentifier].waitForExistence(timeout: 20), "The post page never showed its like button.")
            end()
            settle()
            app.buttons[backLabel].firstMatch.tap()
            XCTAssertTrue(app.buttons[backLabel].firstMatch.waitForNonExistence(timeout: 10))
            settle()
        }
    }

    @MainActor
    func testC2_ClosePost() throws {
        let app = launchToHome()
        measure(metrics: animationMetrics(app), options: options()) {
            let card = firstCard(in: app)
            XCTAssertTrue(card.waitForExistence(timeout: 10))
            card.tap()
            XCTAssertTrue(app.buttons[likeIdentifier].waitForExistence(timeout: 20))
            settle(1.0)
            let back = app.buttons[backLabel].firstMatch
            begin()
            back.tap()
            XCTAssertTrue(back.waitForNonExistence(timeout: 10))
            XCTAssertTrue(firstCard(in: app).waitForExistence(timeout: 10))
            end()
            settle()
        }
    }

    // MARK: - d) Open the In progress update sheet from Home

    @MainActor
    func testD_OpenUpdateSheet() throws {
        let app = launchToHome()
        let update = app.buttons.matching(identifier: updateIdentifier).firstMatch
        XCTAssertTrue(update.waitForExistence(timeout: 20), "No In progress update control (\(updateIdentifier)).")
        measure(metrics: animationMetrics(app), options: options()) {
            let update = app.buttons.matching(identifier: updateIdentifier).firstMatch
            XCTAssertTrue(update.waitForExistence(timeout: 10))
            begin()
            update.tap()
            XCTAssertTrue(app.buttons[sheetCloseLabel].firstMatch.waitForExistence(timeout: 20), "The update sheet never opened.")
            end()
            settle()
            app.buttons[sheetCloseLabel].firstMatch.tap()
            XCTAssertTrue(app.buttons[sheetCloseLabel].firstMatch.waitForNonExistence(timeout: 10))
            settle()
        }
    }

    // MARK: - e) Memory after scrolling the feed

    /// A fresh process per iteration, so each run's peak is the cost of launching and scrolling the whole feed.
    @MainActor
    func testE_MemoryAfterScrollingFeed() throws {
        let app = makeApp()
        app.launch()
        waitForFirstCard(app)
        measure(metrics: [XCTClockMetric()], options: options()) {
            app.terminate()
            settle(1.0)
            app.launch()
            waitForFirstCard(app)
            settle(1.5)
            begin()
            for _ in 0..<10 { app.swipeUp(velocity: .fast) }
            settle(1.0)
            end()
        }
    }

    // MARK: - Not a scenario: dump the accessibility tree where the scenarios look

    /// Run it by name when a redesign breaks a scenario's lookups: it prints the tree of Home and of an opened post, so
    /// the constants at the top can be pointed at the new labels.
    @MainActor
    func testZ_DumpAccessibilityTrees() throws {
        let app = launchToHome()
        print("DUMP-HOME\n" + app.debugDescription)
        firstCard(in: app).tap()
        Thread.sleep(forTimeInterval: 3)
        print("DUMP-POST\n" + app.debugDescription)
    }
}
