import XCTest
@testable import Spine

final class ListPlateLightTests: XCTestCase {
    func testArtWithoutARealColorLeavesThePlateClear() {
        // A white and grey cover (GNX), and one whose only color is a speck.
        XCTAssertNil(ListPlateLight.opacity(for: [
            PosterSwatch(hex: "F2F2F2", share: 0.5),
            PosterSwatch(hex: "8A8A8A", share: 0.3),
            PosterSwatch(hex: "141414", share: 0.2),
        ]))
        XCTAssertNil(ListPlateLight.opacity(for: [
            PosterSwatch(hex: "FFFFFF", share: 0.95),
            PosterSwatch(hex: "E8501E", share: 0.05),
        ]))
        XCTAssertNil(ListPlateLight.opacity(for: []))
        XCTAssertNil(ListPlateLight.opacity(for: nil))
    }

    func testColorfulArtLightsThePlateAndLightArtLess() throws {
        // Dark art with a real color (an ember on black) lights the plate fully.
        XCTAssertEqual(ListPlateLight.opacity(for: [
            PosterSwatch(hex: "E8501E", share: 0.4),
            PosterSwatch(hex: "201008", share: 0.6),
        ]), 1)

        // Bright lime art lights it, dimmed, never below the faintest.
        let lime = try XCTUnwrap(ListPlateLight.opacity(for: [
            PosterSwatch(hex: "A6E22E", share: 0.9),
            PosterSwatch(hex: "FFFFFF", share: 0.1),
        ]))
        XCTAssertLessThan(lime, 1)
        XCTAssertGreaterThanOrEqual(lime, ListPlateLight.faintestOpacity)
    }
}
