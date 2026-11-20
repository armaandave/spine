import XCTest
@testable import Spine

/// When the launch overlay glides into Home's header and when it just fades.
@MainActor
final class LaunchHandoffTests: XCTestCase {
    private let logo = CGRect(x: 16, y: 261, width: 24.76, height: 28)
    private let word = CGRect(x: 46.76, y: 256, width: 82, height: 36)

    func testItGlidesOnceSignedInWithHomeReadyAndItsHeaderLaidOut() {
        let handoff = LaunchHandoff(reduceMotion: false, palette: nil)
        handoff.sessionChanged(.signedIn)
        handoff.homeReadinessChanged(true)
        XCTAssertEqual(handoff.phase, .covering)

        handoff.headerMoved(logo: logo)
        handoff.headerMoved(word: word)
        XCTAssertEqual(handoff.phase, .landing)
        XCTAssertTrue(handoff.hidesHeaderLogo)
        // With no picture's colors, the header logo's glow is held at milk's, the glow the overlay's lands with.
        XCTAssertEqual(handoff.heldGlowColor, LaunchLogo.milkGlow)

        handoff.finish()
        XCTAssertEqual(handoff.phase, .done)
        XCTAssertFalse(handoff.hidesHeaderLogo)
        XCTAssertNil(handoff.heldWordColor)
        XCTAssertNil(handoff.heldGlowColor)
    }

    func testSignedOutItFadesIntoOnboarding() {
        let handoff = LaunchHandoff(reduceMotion: false, palette: nil)
        handoff.sessionChanged(.signedOut)
        XCTAssertEqual(handoff.phase, .fading)
        XCTAssertFalse(handoff.hidesHeaderLogo)
    }

    func testWithReduceMotionItFadesInsteadOfGliding() {
        let handoff = LaunchHandoff(reduceMotion: true, palette: nil)
        XCTAssertFalse(handoff.hidesHeaderLogo)
        handoff.sessionChanged(.signedIn)
        handoff.headerMoved(logo: logo)
        handoff.headerMoved(word: word)
        handoff.homeReadinessChanged(true)
        XCTAssertEqual(handoff.phase, .fading)
    }
}

/// The launch animation must be seamless at both ends: it starts on the still launch screen and ends on the header's
/// own logo and word, which it hands over to. The logo only sets off once it has filled.
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
        XCTAssertEqual(first.outline, 1, accuracy: 1e-9)
        XCTAssertEqual(first.color, 1, accuracy: 1e-9)
        XCTAssertEqual(first.toHeader, 0, accuracy: 1e-9)

        let last = plan.frame(at: 1)
        XCTAssertEqual(last.center.x, end.x, accuracy: 1e-9)
        XCTAssertEqual(last.center.y, end.y, accuracy: 1e-9)
        XCTAssertEqual(last.scale, 0.165, accuracy: 1e-9)
        XCTAssertEqual(last.plaster, 0, accuracy: 1e-9)
        XCTAssertEqual(last.black, 0, accuracy: 1e-9)
        XCTAssertEqual(last.word, 1, accuracy: 1e-9)
        XCTAssertEqual(last.outline, 0, accuracy: 1e-9)
        XCTAssertEqual(last.color, 0, accuracy: 1e-9)
        XCTAssertEqual(last.toHeader, 1, accuracy: 1e-9)
    }

    func testTheLogoSetsOffOnceItIsFullAndHomeIsReady() {
        let firstFrame = 100.0
        XCTAssertEqual(LaunchChoreography.fill(at: 0), 0, accuracy: 1e-9)
        XCTAssertEqual(LaunchChoreography.fill(at: LaunchChoreography.fillDuration), 1, accuracy: 1e-9)
        // Home ready at once: it still waits until the logo is full.
        XCTAssertEqual(
            LaunchChoreography.departure(ready: firstFrame, firstFrame: firstFrame),
            firstFrame + LaunchChoreography.fillDuration,
            accuracy: 1e-9
        )
        // Home ready after that: it goes then.
        let late = firstFrame + LaunchChoreography.fillDuration + 0.7
        XCTAssertEqual(LaunchChoreography.departure(ready: late, firstFrame: firstFrame), late, accuracy: 1e-9)
    }
}

/// The launch's colors from Home's picture: the background its main color, the logo a clearly different one that
/// stands out on it, kept for the next launch.
@MainActor
final class LaunchPaletteTests: XCTestCase {
    private func swatch(_ hex: String, _ share: Double) -> PosterSwatch { PosterSwatch(hex: hex, share: share) }

    func testTheBackgroundIsThePicturesMainColorAndTheLogoAClearlyDifferentOne() throws {
        let palette = try XCTUnwrap(LaunchPalette(swatches: [
            swatch("#050505", 0.5),
            swatch("#1D4C66", 0.3),
            swatch("#2F6E8A", 0.1),
            swatch("#E09A6A", 0.1),
        ]))
        // The black shadow is left out; the second blue is too close to the first to count as different.
        XCTAssertEqual(palette.background, MeterTint.RGB(hex: "#1D4C66"))
        XCTAssertEqual(palette.logo, MeterTint.RGB(hex: "#E09A6A"))
    }

    func testALogoTooCloseToTheBackgroundIsLightenedUntilItStandsOut() throws {
        let palette = try XCTUnwrap(LaunchPalette(swatches: [swatch("#1D4C66", 0.6), swatch("#2F6E8A", 0.4)]))
        let contrast = HomeTitleTint.contrast(palette.logo, TrendingOpenTint.luminance(palette.background))
        XCTAssertGreaterThanOrEqual(contrast, LaunchPalette.logoContrast)
    }

    func testAPictureThatIsAllShadowHasNoPalette() {
        XCTAssertNil(LaunchPalette(swatches: [swatch("#000000", 0.9), swatch("#0A0A0A", 0.1)]))
    }

    func testThePaletteIsKeptForTheNextLaunchAndCanBeForgotten() throws {
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "LaunchPaletteTests"))
        defer { defaults.removePersistentDomain(forName: "LaunchPaletteTests") }
        let palette = LaunchPalette(
            background: try XCTUnwrap(MeterTint.RGB(hex: "#3B2A45")),
            logo: try XCTUnwrap(MeterTint.RGB(hex: "#F0A86E")),
            headerGlow: try XCTUnwrap(MeterTint.RGB(hex: "#F4C7A1"))
        )
        LaunchPalette.remember(palette, in: defaults)
        XCTAssertEqual(LaunchPalette.saved(in: defaults), palette)
        LaunchPalette.remember(nil, in: defaults)
        XCTAssertNil(LaunchPalette.saved(in: defaults))
    }
}
