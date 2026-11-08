import XCTest
@testable import Spine

/// The Trending panel's Open icon color, the faces row and the open stars: the pure parts and the source rules.
@MainActor
final class TrendingPanelTests: XCTestCase {
    private typealias RGB = MeterTint.RGB

    // MARK: The Open icon's color

    private func tint(_ colors: RGB...) -> MeterTint {
        MeterTint(ramp: colors, today: .white)
    }

    private let blue = RGB(red: 0, green: 0, blue: 0.7)
    private let gold = RGB(red: 0.9, green: 0.8, blue: 0.3)
    private let maroon = RGB(red: 0.5, green: 0, blue: 0)

    func testWhiteReadsOnTheDarkTileAndBlackDoesNot() {
        XCTAssertEqual(TrendingOpenTint.luminance(.white), 1, accuracy: 0.001)
        XCTAssertEqual(TrendingOpenTint.luminance(RGB(red: 0, green: 0, blue: 0)), 0, accuracy: 0.001)
        XCTAssertGreaterThan(TrendingOpenTint.contrast(.white), 10)
        XCTAssertLessThan(TrendingOpenTint.contrast(RGB(red: 0, green: 0, blue: 0)), 1.1)
    }

    func testThePostersFirstColorIsUsedWhenItReads() {
        XCTAssertEqual(TrendingOpenTint.pick(from: tint(gold, blue)), gold)
    }

    func testAFirstColorTooDimOnTheTileGivesWayToTheSecond() {
        XCTAssertLessThan(TrendingOpenTint.contrast(blue), TrendingOpenTint.minimumContrast)
        XCTAssertEqual(TrendingOpenTint.pick(from: tint(blue, gold)), gold)
        XCTAssertEqual(TrendingOpenTint.pick(from: tint(blue, maroon, gold)), gold, "Then the third")
    }

    func testNoColorThatReadsLeavesTheIconWhite() {
        XCTAssertNil(TrendingOpenTint.pick(from: tint(blue, maroon)))
        XCTAssertNil(TrendingOpenTint.pick(from: nil))
        XCTAssertNil(TrendingOpenTint.color(from: nil))
        XCTAssertNil(TrendingOpenTint.color(from: []))
    }

    func testIncreaseContrastAsksForMore() {
        let borderline = RGB(red: 0.46, green: 0.46, blue: 0.46)
        let ratio = TrendingOpenTint.contrast(borderline)
        XCTAssertGreaterThanOrEqual(ratio, TrendingOpenTint.minimumContrast)
        XCTAssertLessThan(ratio, TrendingOpenTint.highContrastMinimum, "The fixture sits between the two limits")
        XCTAssertEqual(TrendingOpenTint.pick(from: tint(borderline, gold)), borderline)
        XCTAssertEqual(TrendingOpenTint.pick(from: tint(borderline, gold), highContrast: true), gold)
    }

    func testAPostersSwatchesBecomeAColor() {
        let bright = [PosterSwatch(hex: "F2C94C", share: 0.6)]
        XCTAssertNotNil(TrendingOpenTint.color(from: bright))
        let onlyBlack = [PosterSwatch(hex: "050505", share: 0.9)]
        XCTAssertNil(TrendingOpenTint.color(from: onlyBlack), "Nothing but black is no color at all")
    }

    // MARK: The heart and the stars

    func testTheHeartOpensTheStarsOnlyOnAPageThatWantsItAndNeverOnTrending() {
        func opens(liked: Bool = false, offers: Bool = true, open: Bool = false, allowed: Bool = true) -> Bool {
            ActionRail.likeOpensRatingPicker(
                isLiked: liked, offersRatingAfterBaseAction: offers, pickerPresented: open, allowed: allowed
            )
        }
        XCTAssertTrue(opens(), "The media page: a first heart opens the stars")
        XCTAssertFalse(opens(allowed: false), "Trending: the heart never opens them")
        XCTAssertFalse(opens(liked: true), "Taking a heart back does not")
        XCTAssertFalse(opens(offers: false))
        XCTAssertFalse(opens(open: true), "Already open stays as it is")
    }

    func testDismissingTheStarsKeepsTheRating() {
        var picker = MediaRatingPickerState()
        picker.syncConfirmed(7)
        picker.open(markLocallyWatched: false)
        picker.draftHalfSteps = 3
        picker.dismiss()
        XCTAssertFalse(picker.isPresented)
        XCTAssertEqual(picker.draftHalfSteps, 7, "The draft goes back to the confirmed rating")
        XCTAssertEqual(picker.confirmedHalfSteps, 7)
    }

    // MARK: Source rules

    private static let tiles: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("Spine/Features/Home/Tiles", isDirectory: true)

    private func read(_ name: String) throws -> String {
        try String(contentsOf: Self.tiles.appendingPathComponent(name), encoding: .utf8)
    }

    func testThePanelShowsNeitherTheAverageNorTheWordsPopularThisWeek() throws {
        let panel = try read("TrendingPanel.swift")
        let code = panel.split(separator: "\n").filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }
            .joined(separator: "\n")
        XCTAssertFalse(code.contains("Popular this week"))
        XCTAssertFalse(code.contains("\"avg\""))
        XCTAssertFalse(code.contains("averageText"))
        XCTAssertFalse(code.contains("starGold"))
    }

    func testAFaceOpensTheProfileAndNotTheMediaPage() throws {
        let panel = try read("TrendingPanel.swift")
        XCTAssertTrue(panel.contains("UserProfileDestination(username: avatar.username)"))
        XCTAssertFalse(panel.contains("onOpenFaces"))
        XCTAssertFalse(panel.contains("trending.faces"))
    }

    func testTheRailGetsTheTintAndTheHeartDoesNotOpenTheStars() throws {
        let host = try read("TrendingRailHost.swift")
        XCTAssertTrue(host.contains("likeOpensRatingPicker: false"))
        XCTAssertTrue(host.contains("tint: openTint"))
    }
}
