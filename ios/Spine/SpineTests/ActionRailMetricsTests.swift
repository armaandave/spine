import XCTest
@testable import Spine

/// The shared Liquid Glass rail's numbers, which the media page and Home's Trending panel both use. These pin that
/// moving the rail out of the media page left the page's rail exactly as it was, and that Open adds one slot.
@MainActor
final class ActionRailMetricsTests: XCTestCase {
    func testThePagesRailIsTwoHundredAndTwoWideAndHomesWithOpenIsTwoHundredAndFifty() {
        // The page: + eye star heart. Without the star (a book's rail before it has one) it is a slot narrower.
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 4, pickerPresented: false), 202)
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 3, pickerPresented: false), 154)
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 2, pickerPresented: false), 106)
        // Home: lead, eye, Open, star, heart.
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 5, pickerPresented: false), 250)
    }

    func testTheOpenPickerIsAtLeastOneHundredAndEightyThreeWide() {
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 2, pickerPresented: true), 183)
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 3, pickerPresented: true), 183)
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 4, pickerPresented: true), 202)
        XCTAssertEqual(ActionRail.surfaceWidth(buttonCount: 5, pickerPresented: true), 250)
    }

    func testTheCornersAreACapsuleAtFiftyEightPointsAndTwentySixWhenThePickerIsOpen() {
        XCTAssertEqual(ActionRail.cornerRadius(pickerPresented: false), 29)
        XCTAssertEqual(ActionRail.cornerRadius(pickerPresented: true), 26)
        XCTAssertEqual(ActionRail.buttonSize + 10, 58, "29 is half of the rail's height")
    }

    func testEachRailSlotIsFortyEightPoints() {
        XCTAssertEqual(ActionRail.buttonSize, 48)
    }
}

/// A tap Home's rail leaves for the page: each value runs exactly one of the page's four functions.
@MainActor
final class RailHandoffTests: XCTestCase {
    private func run(_ handoff: RailHandoff) -> [String] {
        var calls: [String] = []
        handoff.run(
            track: { calls.append("track") },
            eye: { calls.append("eye") },
            like: { calls.append("like") },
            rate: { calls.append("rate \($0)") }
        )
        return calls
    }

    func testEachHandoffRunsOneOfThePagesFunctions() {
        XCTAssertEqual(run(.track), ["track"])
        XCTAssertEqual(run(.eye), ["eye"])
        XCTAssertEqual(run(.like), ["like"])
        XCTAssertEqual(run(.rate(halfSteps: 7)), ["rate 7"])
        XCTAssertEqual(run(.rate(halfSteps: 0)), ["rate 0"], "Taking a rating away is a rate too")
    }
}
