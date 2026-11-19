import XCTest
@testable import Spine

/// The launch animation must be seamless at both ends: it starts on the still launch screen and ends on the header's
/// own logo and word, which it hands over to. The logo only sets off after a whole beat of the glow, at rest.
@MainActor
final class LaunchChoreographyTests: XCTestCase {
    private let start = CGPoint(x: 215, y: 466)
    private let end = CGPoint(x: 28.4, y: 268)

    func testItStartsOnTheLaunchScreenAndEndsOnTheHeader() {
        let plan = LaunchChoreography(start: start, end: end, endScale: 0.165)

        let first = plan.frame(at: 0)
        XCTAssertEqual(first.center, start)
        XCTAssertEqual(first.scale, 1, accuracy: 1e-9)
        XCTAssertEqual(first.plaster, 1, accuracy: 1e-9)
        XCTAssertEqual(first.black, 0, accuracy: 1e-9)
        XCTAssertEqual(first.word, 0, accuracy: 1e-9)

        let last = plan.frame(at: 1)
        XCTAssertEqual(last.center.x, end.x, accuracy: 1e-9)
        XCTAssertEqual(last.center.y, end.y, accuracy: 1e-9)
        XCTAssertEqual(last.scale, 0.165, accuracy: 1e-9)
        XCTAssertEqual(last.plaster, 0, accuracy: 1e-9)
        XCTAssertEqual(last.black, 0, accuracy: 1e-9)
        XCTAssertEqual(last.word, 1, accuracy: 1e-9)
    }

    func testTheLogoSetsOffAfterAWholeBeatWithTheGlowAtRest() {
        let firstBeat = 100.0
        let oneBeat = LaunchChoreography.swell + LaunchChoreography.settle
        // Ready at once: it still waits for the first beat.
        XCTAssertEqual(LaunchChoreography.departure(ready: firstBeat, firstBeat: firstBeat), firstBeat + oneBeat, accuracy: 1e-9)
        // Ready while the glow rests between beats: it goes now.
        let resting = firstBeat + oneBeat + 0.2
        XCTAssertEqual(LaunchChoreography.departure(ready: resting, firstBeat: firstBeat), resting, accuracy: 1e-9)
        // Ready during the second beat: it waits for that beat to settle.
        let duringSecond = firstBeat + LaunchChoreography.beatPeriod + 0.1
        let leaves = LaunchChoreography.departure(ready: duringSecond, firstBeat: firstBeat)
        XCTAssertEqual(leaves, firstBeat + LaunchChoreography.beatPeriod + oneBeat, accuracy: 1e-9)
        XCTAssertEqual(LaunchChoreography.beat(at: leaves - firstBeat), 0, accuracy: 1e-9)
    }
}
