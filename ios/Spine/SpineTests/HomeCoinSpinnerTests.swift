import XCTest
@testable import Spine

final class HomeCoinSpinnerTests: XCTestCase {
    private let turn = 2 * CGFloat.pi

    func testThePullTurnsTheCoinOnceByTheTimeLettingGoReloads() {
        XCTAssertEqual(HomeCoinChoreography.angle(pull: 0), 0)
        XCTAssertEqual(HomeCoinChoreography.angle(pull: HomeRefreshModel.threshold), turn, accuracy: 0.0001)
        XCTAssertEqual(HomeCoinChoreography.angle(pull: HomeRefreshModel.threshold + 60), turn, accuracy: 0.0001)
        let halfway = HomeCoinChoreography.angle(pull: HomeRefreshModel.threshold / 2)
        XCTAssertEqual(halfway, .pi, accuracy: 0.0001)
    }

    func testItLandsFaceUpAheadOfWhereItIs() {
        for angle in stride(from: CGFloat(0), through: 40, by: 0.37) {
            let target = HomeCoinChoreography.landingAngle(from: angle)
            XCTAssertEqual((target / turn).rounded(), target / turn, accuracy: 0.0001, "face up at \(angle)")
            XCTAssertGreaterThanOrEqual(target, angle + HomeCoinChoreography.spinVelocity * HomeCoinChoreography.carry - 0.0001)
            XCTAssertLessThan(target, angle + HomeCoinChoreography.spinVelocity * HomeCoinChoreography.carry + turn)
        }
    }

    func testTheGlowFlashesWhenAFaceTurnsToTheScreen() {
        let faceOn = HomeCoinChoreography.glow(angle: 0)
        XCTAssertEqual(HomeCoinChoreography.glow(angle: .pi), faceOn, accuracy: 0.0001)
        XCTAssertLessThan(HomeCoinChoreography.glow(angle: .pi / 2), faceOn)
        XCTAssertEqual(HomeCoinChoreography.glint(angle: 0).opacity, 0, accuracy: 0.0001)
        XCTAssertGreaterThan(HomeCoinChoreography.glint(angle: .pi / 2).opacity, 0.5)
    }

    @MainActor
    func testTheSpinnerLandsBeforeThePageClosesTheRoom() async {
        let model = HomeRefreshModel()
        let run = Task { await model.run { XCTAssertEqual(model.phase, .spinning) } }
        try? await Task.sleep(for: .milliseconds(60))
        XCTAssertEqual(model.phase, .landing)
        XCTAssertTrue(model.isRefreshing)
        await run.value
        XCTAssertEqual(model.phase, .pulling)
        XCTAssertFalse(model.isRefreshing)
    }
}
