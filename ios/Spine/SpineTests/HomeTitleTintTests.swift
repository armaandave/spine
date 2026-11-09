import XCTest
@testable import Spine

/// Which color Home's title takes from the picture: the most common real color, never the picture's black.
final class HomeTitleTintTests: XCTestCase {
    private func swatch(_ hex: String, _ share: Double) -> PosterSwatch {
        PosterSwatch(hex: hex, share: share)
    }

    private func hex(_ rgb: MeterTint.RGB?) -> String? {
        rgb.map {
            String(format: "#%02X%02X%02X", Int(($0.red * 255).rounded()), Int(($0.green * 255).rounded()), Int(($0.blue * 255).rounded()))
        }
    }

    func testTheBiggestRealColorWins() {
        let tint = HomeTitleTint.color(from: [swatch("#D98A3C", 0.4), swatch("#3C8AD9", 0.3), swatch("#000000", 0.3)])
        XCTAssertEqual(hex(tint), "#D98A3C")
    }

    func testTheBiggestSwatchIsFoundEvenWhenTheListIsNotInOrder() {
        let tint = HomeTitleTint.color(from: [swatch("#3C8AD9", 0.2), swatch("#D98A3C", 0.5)])
        XCTAssertEqual(hex(tint), "#D98A3C")
    }

    func testABlackTopSwatchGivesWayToTheNext() {
        let tint = HomeTitleTint.color(from: [swatch("#050505", 0.6), swatch("#D98A3C", 0.25), swatch("#3C8AD9", 0.15)])
        XCTAssertEqual(hex(tint), "#D98A3C")
    }

    func testSeveralDarkSwatchesAreAllSkipped() {
        // Black, a near-black grey, a very dark blue (nearly black), then a real color.
        let tint = HomeTitleTint.color(from: [
            swatch("#000000", 0.4), swatch("#2A2A2A", 0.25), swatch("#020210", 0.15), swatch("#3C8AD9", 0.1),
        ])
        XCTAssertEqual(hex(tint), "#3C8AD9")
    }

    func testNothingButDarkGivesNoColor() {
        XCTAssertNil(HomeTitleTint.color(from: [swatch("#000000", 0.6), swatch("#1A1A1A", 0.3), swatch("#050508", 0.1)]))
        XCTAssertNil(HomeTitleTint.color(from: []))
    }

    func testADarkColorIsLiftedToReadableAndKeepsItsHue() throws {
        // A deep navy is dark but a real color: kept, brought up to a lightness that reads, same hue.
        let navy = try XCTUnwrap(MeterTint.RGB(hex: "#10206A"))
        let tint = try XCTUnwrap(HomeTitleTint.color(from: [swatch("#000000", 0.5), swatch("#10206A", 0.5)]))
        XCTAssertGreaterThanOrEqual(HomeTitleTint.lab(of: tint).lightness, HomeTitleTint.readableLightness - 0.5)
        XCTAssertEqual(tint.hsb.hue, navy.hsb.hue, accuracy: 0.01)
    }

    func testALightEnoughColorIsNotChanged() throws {
        let orange = try XCTUnwrap(MeterTint.RGB(hex: "#D98A3C"))
        XCTAssertEqual(hex(HomeTitleTint.readable(orange)), "#D98A3C")
    }
}
