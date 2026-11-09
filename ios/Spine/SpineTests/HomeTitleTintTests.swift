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

    func testTheDateTakesTheNextRealColorAfterTheTitles() {
        let swatches = [swatch("#050505", 0.5), swatch("#D98A3C", 0.25), swatch("#2A2A2A", 0.15), swatch("#3C8AD9", 0.1)]
        XCTAssertEqual(hex(HomeTitleTint.color(from: swatches)), "#D98A3C")
        XCTAssertEqual(hex(HomeTitleTint.dateColor(from: swatches)), "#3C8AD9", "black and the near-black grey skipped")
    }

    func testTheDateSkipsColorsFromTheTitlesFamilyAndGreys() {
        // An orange title; a lighter orange and a grey come next, then a real blue further down.
        let swatches = [
            swatch("#D98A3C", 0.4), swatch("#F0B070", 0.25), swatch("#8A8A8A", 0.15), swatch("#3C8AD9", 0.05),
        ]
        XCTAssertEqual(hex(HomeTitleTint.dateColor(from: swatches)), "#3C8AD9")
    }

    func testWithNoDifferentColorTheDateIsALighterShadeOfTheTitle() throws {
        let swatches = [swatch("#000000", 0.6), swatch("#D98A3C", 0.3), swatch("#F0B070", 0.1)]
        let title = try XCTUnwrap(HomeTitleTint.color(from: swatches))
        let date = try XCTUnwrap(HomeTitleTint.dateColor(from: swatches))
        XCTAssertEqual(date, title.mixed(toward: .white, by: HomeTitleTint.dateFallbackMix))
        XCTAssertNil(HomeTitleTint.dateColor(from: [swatch("#000000", 1)]), "no title color, no date color")
    }

    func testHueGapGoesTheShortWayRoundTheWheel() {
        let red = (lightness: 50.0, a: 60.0, b: 1.0)
        let nearRed = (lightness: 50.0, a: 60.0, b: -1.0)
        XCTAssertLessThan(HomeTitleTint.hueGap(red, nearRed), 5, "across zero degrees")
        let green = (lightness: 50.0, a: -60.0, b: 0.0)
        XCTAssertEqual(HomeTitleTint.hueGap(red, green), 179, accuracy: 2)
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

    func testOnADarkGlowTheColorsAreLeftAsTheyAre() {
        let poster = [swatch("#D98A3C", 0.5), swatch("#3C8AD9", 0.2)]
        let glow = [swatch("#050505", 0.7), swatch("#1A2A30", 0.3)]
        XCTAssertEqual(HomeTitleTint.color(from: poster, against: glow), HomeTitleTint.color(from: poster))
        XCTAssertEqual(HomeTitleTint.dateColor(from: poster, against: glow), HomeTitleTint.dateColor(from: poster))
    }

    func testOnAGlowOfTheSameColorTheColorIsBrightenedUntilItReadsAndKeepsItsHue() throws {
        // A red poster on its own red glow: still red, lighter, never dropped to white.
        let poster = [swatch("#B42A1C", 0.7), swatch("#C0391D", 0.3)]
        let glow = try XCTUnwrap(HomeTitleTint.glowLuminance(of: poster))
        let title = try XCTUnwrap(HomeTitleTint.color(from: poster, against: poster))
        XCTAssertGreaterThanOrEqual(HomeTitleTint.contrast(title, glow), HomeTitleTint.backgroundContrast)
        XCTAssertGreaterThan(title.red, title.blue + 0.1, "still a red")
    }

    func testTheGlowIsItsColorsAveragedByShareUnderTheShade() throws {
        let dimmedWhite = MeterTint.RGB(red: 1, green: 1, blue: 1)
            .mixed(toward: MeterTint.RGB(red: 0, green: 0, blue: 0), by: 1 - HomeTitleTint.glowDimming)
        let glow = try XCTUnwrap(HomeTitleTint.glowLuminance(of: [swatch("#000000", 0.75), swatch("#FFFFFF", 0.25)]))
        XCTAssertEqual(glow, TrendingOpenTint.luminance(dimmedWhite) / 4, accuracy: 0.001)
    }

    func testOnAVeryBrightGlowAColorIsLightenedOnlyHalfwayToWhite() throws {
        let title = try XCTUnwrap(HomeTitleTint.color(from: [swatch("#D98A3C", 1)], against: [swatch("#FFFFFF", 1)]))
        XCTAssertLessThan(title.blue, 0.8, "still an orange, not white")
    }
}
