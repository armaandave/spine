//
//  HomePerfTests.swift
//  SpineUITests
//
//  Performance harness for the Home tab and feed. Run through scripts/ios/perf-harness.sh,
//  which serves the repeatable data set (scripts/ios/perf/seed_perf.py) from a private local
//  backend. The tests read nothing from the app but accessibility labels, so the same file runs on the frozen
//  baseline (the app before the Home redesign) and on the redesigned Home. What they rely on:
//    - a feed card is a button whose label names a seeded title ("Perf ...") and its comment count
//      (before: "Perf Show 5, Watched 1 episode, 0 likes, 0 comments"; now: "perf-dee, Perf Long Anime, +24 episodes,
//      1 like, 0 comments"); the author button beside it is not the card;
//    - the In progress "Update" control has the accessibility identifier "in-progress.update". Before the redesign
//      it sat on Home; now it sits in the open In progress tile (button "home.tile.in-progress", closed by the glass
//      button "home.tile.close", posters in the carousel "home.tile.carousel"). The scenarios open the tile only
//      when the app has one, so the frozen baseline runs the same code;
//    - an open post page has the like button "post.like" and a "Back" button;
//    - the progress sheet has a "Close" button.
//  If the app renames any of those, change the constants below, not the scenarios. Scenarios f, f2, g and g2 exist
//  only for the redesign (the baseline has no tiles), so perf-ab.sh runs them with PERF_AB_ONLY=B.
//

import XCTest

final class HomePerfTests: XCTestCase {
    // MARK: - What the scenarios look for

    private let cardLabel = NSPredicate(format: "label CONTAINS 'Perf ' AND label CONTAINS 'comment'")
    private let updateIdentifier = "in-progress.update"
    private let tileIdentifier = "home.tile.in-progress"
    private let tileCloseIdentifier = "home.tile.close"
    private let carouselIdentifier = "home.tile.carousel"
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

    // MARK: - The In progress tile (the redesigned Home only)

    /// The closed In progress tile: one button, present from the first loaded frame of the rail.
    private func tileButton(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(identifier: tileIdentifier).firstMatch
    }

    /// The glass button that exists only while a tile is open.
    private func tileClose(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(identifier: tileCloseIdentifier).firstMatch
    }

    private func openTile(_ app: XCUIApplication, file: StaticString = #filePath, line: UInt = #line) {
        let tile = tileButton(app)
        XCTAssertTrue(tile.waitForExistence(timeout: 20), "No In progress tile (\(tileIdentifier)).", file: file, line: line)
        tile.tap()
        XCTAssertTrue(tileClose(app).waitForExistence(timeout: 10), "The tile never opened (\(tileCloseIdentifier)).", file: file, line: line)
    }

    /// Taps the glass Close button, first scrolling the page up if a scroll left the tile off screen.
    private func collapseTile(_ app: XCUIApplication, file: StaticString = #filePath, line: UInt = #line) {
        let close = tileClose(app)
        var tries = 0
        while !close.isHittable, tries < 6 {
            app.swipeDown(velocity: .fast)
            tries += 1
        }
        close.tap()
        XCTAssertTrue(close.waitForNonExistence(timeout: 10), "The tile never closed.", file: file, line: line)
    }

    /// Three horizontal drags across the open tile's posters, in the direction that has room. The carousel element is
    /// as wide as its scroll content (wider than the screen), so the drags run between points on the screen, at the
    /// height of the carousel. Its accessibility value reads "Poster 2 of 8, Title".
    private func swipeCarousel(_ app: XCUIApplication, times: Int = 3) {
        let carousel = app.buttons.matching(identifier: carouselIdentifier).firstMatch
        let position = Self.posterPosition(carousel.value as? String)
        let forward = position.map { $0.index + times <= $0.count } ?? true
        let screen = app.frame
        let y = min(max(carousel.frame.midY, screen.height * 0.15), screen.height * 0.8) / screen.height
        let start = app.coordinate(withNormalizedOffset: CGVector(dx: forward ? 0.8 : 0.2, dy: y))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: forward ? 0.2 : 0.8, dy: y))
        for _ in 0..<times {
            start.press(forDuration: 0.02, thenDragTo: end, withVelocity: .default, thenHoldForDuration: 0.1)
        }
    }

    /// (place, count) from "Poster 2 of 8, Title".
    private static func posterPosition(_ value: String?) -> (index: Int, count: Int)? {
        guard let value, value.hasPrefix("Poster ") else { return nil }
        let words = value.dropFirst("Poster ".count).split(whereSeparator: { $0 == " " || $0 == "," })
        guard words.count >= 3, let index = Int(words[0]), let count = Int(words[2]) else { return nil }
        return (index, count)
    }

    /// The expand-then-fast-scroll sequence of scenario f2 (also the warm-up of g): tap the tile and at once fling the
    /// page up twice and back down twice, then close the tile.
    private func expandFlingCollapse(_ app: XCUIApplication) {
        let tile = tileButton(app)
        tile.tap()
        app.swipeUp(velocity: .fast)
        app.swipeUp(velocity: .fast)
        app.swipeDown(velocity: .fast)
        app.swipeDown(velocity: .fast)
        collapseTile(app)
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

    // MARK: - d) Open the In progress update sheet

    /// From the Update tap to the sheet's Close button. The redesigned Home keeps Update inside the open In progress
    /// tile, so each iteration opens the tile first (not measured) and collapses it at the end; the frozen baseline has
    /// Update on Home and no tile.
    @MainActor
    func testD_OpenUpdateSheet() throws {
        let app = launchToHome()
        let hasTile = tileButton(app).waitForExistence(timeout: 5)
        if !hasTile {
            let update = app.buttons.matching(identifier: updateIdentifier).firstMatch
            XCTAssertTrue(update.waitForExistence(timeout: 20), "No In progress update control (\(updateIdentifier)).")
        }
        measure(metrics: animationMetrics(app), options: options()) {
            if hasTile {
                openTile(app)
                settle(1.0)
            }
            let update = app.buttons.matching(identifier: updateIdentifier).firstMatch
            XCTAssertTrue(update.waitForExistence(timeout: 10), "No In progress update control (\(updateIdentifier)).")
            begin()
            update.tap()
            XCTAssertTrue(app.buttons[sheetCloseLabel].firstMatch.waitForExistence(timeout: 20), "The update sheet never opened.")
            end()
            settle()
            app.buttons[sheetCloseLabel].firstMatch.tap()
            XCTAssertTrue(app.buttons[sheetCloseLabel].firstMatch.waitForNonExistence(timeout: 10))
            settle()
            if hasTile {
                collapseTile(app)
                settle()
            }
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

    // MARK: - f) Tile: expand, swipe the carousel, collapse (redesign only)

    @MainActor
    func testF_TileExpandSwipeCollapse() throws {
        let app = launchToHome()
        XCTAssertTrue(tileButton(app).waitForExistence(timeout: 20), "No In progress tile (\(tileIdentifier)).")
        var run = 0
        measure(metrics: [XCTClockMetric()], options: options()) {
            run += 1
            scrollToTop(app)
            begin()
            tileButton(app).tap()
            XCTAssertTrue(tileClose(app).waitForExistence(timeout: 10), "The tile never opened (\(tileCloseIdentifier)).")
            // The warm-up run (its window is dropped by report.py) proves the drags really move the carousel.
            let carousel = app.buttons.matching(identifier: carouselIdentifier).firstMatch
            let before = run == 1 ? carousel.value as? String : nil
            swipeCarousel(app)
            if run == 1 {
                XCTAssertNotEqual(carousel.value as? String, before, "Three drags did not move the carousel off \(before ?? "nil").")
            }
            tileClose(app).tap()
            XCTAssertTrue(tileClose(app).waitForNonExistence(timeout: 10), "The tile never closed.")
            end()
            settle()
        }
    }

    // MARK: - f3) Tile: swipe the carousel only, 12 times (redesign only)

    /// The morphing In progress panel follows every frame of a swipe. The tile is opened before the window and closed
    /// after it, so the counters see swipes alone: the page, the cards and the tiles must not redraw (their deltas are
    /// 0, report.py asserts it) while the panel does (`panelBody` > 0).
    @MainActor
    func testF3_TileSwipeOnly() throws {
        let app = launchToHome()
        XCTAssertTrue(tileButton(app).waitForExistence(timeout: 20), "No In progress tile (\(tileIdentifier)).")
        measure(metrics: [XCTClockMetric()], options: options()) {
            scrollToTop(app)
            openTile(app)
            settle(1.0)
            begin()
            // Twelve full-width drags, three at a time, each run in the direction that has room.
            for _ in 0..<4 { swipeCarousel(app, times: 3) }
            end()
            collapseTile(app)
            settle()
        }
    }

    // MARK: - f2) Tile: expand, then immediately fast-scroll the page (redesign only)

    @MainActor
    func testF2_ExpandThenFastScroll() throws {
        let app = launchToHome()
        XCTAssertTrue(tileButton(app).waitForExistence(timeout: 20), "No In progress tile (\(tileIdentifier)).")
        measure(metrics: [XCTClockMetric()], options: options()) {
            scrollToTop(app)
            begin()
            expandFlingCollapse(app)
            end()
            settle()
        }
    }

    // MARK: - g) Idle for 10 s after tiles and fast scrolls (redesign only)

    @MainActor
    func testG_IdleAfterTiles() throws {
        let app = launchToHome()
        XCTAssertTrue(tileButton(app).waitForExistence(timeout: 20), "No In progress tile (\(tileIdentifier)).")
        measure(metrics: [XCTClockMetric()], options: options()) {
            scrollToTop(app)
            expandFlingCollapse(app)
            app.swipeUp(velocity: .fast)
            app.swipeDown(velocity: .fast)
            settle(1.5)
            begin()
            settle(10)
            end()
        }
    }

    // MARK: - g2) Idle for 10 s with a tile left open (redesign only)

    @MainActor
    func testG2_IdleWithOpenTile() throws {
        let app = launchToHome()
        XCTAssertTrue(tileButton(app).waitForExistence(timeout: 20), "No In progress tile (\(tileIdentifier)).")
        measure(metrics: [XCTClockMetric()], options: options()) {
            scrollToTop(app)
            openTile(app)
            app.swipeUp(velocity: .fast)
            app.swipeDown(velocity: .fast)
            scrollToTop(app)
            settle(1.0)
            begin()
            settle(10)
            end()
            collapseTile(app)
            settle()
        }
    }

    // MARK: - Not a scenario: dump the accessibility tree where the scenarios look

    /// Run it by name when a redesign breaks a scenario's lookups: it prints the tree of Home and of an opened post, so
    /// the constants at the top can be pointed at the new labels. With a tile it also prints the open tile's tree, and
    /// it saves screenshots of the settled Home, the open tile and the post into PERF_SHOT_DIR (the result directory).
    @MainActor
    func testZ_DumpAccessibilityTrees() throws {
        let app = launchToHome()
        settle(2.0)
        saveScreenshot(app, "home")
        print("DUMP-HOME\n" + app.debugDescription)
        if tileButton(app).exists {
            openTile(app)
            settle(1.5)
            saveScreenshot(app, "tile-open")
            print("DUMP-TILE\n" + app.debugDescription)
            collapseTile(app)
            settle(1.0)
        }
        firstCard(in: app).tap()
        Thread.sleep(forTimeInterval: 3)
        saveScreenshot(app, "post")
        print("DUMP-POST\n" + app.debugDescription)
    }

    private func saveScreenshot(_ app: XCUIApplication, _ name: String) {
        guard let directory = ProcessInfo.processInfo.environment["PERF_SHOT_DIR"] else { return }
        let url = URL(fileURLWithPath: directory).appendingPathComponent("\(name).png")
        try? app.screenshot().pngRepresentation.write(to: url)
    }
}
