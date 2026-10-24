import UIKit
import XCTest
@testable import Spine

final class AlbumBackdropColorTests: XCTestCase {
    func testMostlyBlueCoverGivesMutedDarkBlue() throws {
        let cover = makeCover([
            (UIColor(red: 0.16, green: 0.2, blue: 0.85, alpha: 1), 0.7),
            (UIColor(red: 0.95, green: 0.85, blue: 0.75, alpha: 1), 0.3),
        ])

        let dominant = try XCTUnwrap(AlbumBackdropColor.dominant(in: cover))
        XCTAssertEqual(dominant.hue * 360, 236.5, accuracy: 4)

        let tint = AlbumBackdropColor.tint(for: dominant)
        XCTAssertEqual(tint.hue, dominant.hue, accuracy: 0.001)
        XCTAssertEqual(tint.saturation, 0.62, accuracy: 0.001, "Saturation is capped below Apple Music's")
        XCTAssertEqual(tint.brightness, 0.24, accuracy: 0.001)
    }

    func testColorBeatsALittleMoreBlackAndWhite() throws {
        let cover = makeCover([
            (UIColor(red: 0.85, green: 0.12, blue: 0.1, alpha: 1), 0.45),
            (.black, 0.3),
            (.white, 0.25),
        ])

        let dominant = try XCTUnwrap(AlbumBackdropColor.dominant(in: cover))
        XCTAssertGreaterThan(dominant.saturation, 0.6)
        XCTAssertLessThan(min(dominant.hue, 1 - dominant.hue) * 360, 8)
    }

    func testRedsEitherSideOfZeroDegreesCountTogether() throws {
        // 20% crimson (hue ~355°) and 20% scarlet (hue ~5°) outweigh 30% green only if they share a bucket.
        let cover = makeCover([
            (UIColor(hue: 355 / 360, saturation: 0.8, brightness: 0.8, alpha: 1), 0.2),
            (UIColor(hue: 5 / 360, saturation: 0.8, brightness: 0.8, alpha: 1), 0.2),
            (UIColor(hue: 120 / 360, saturation: 0.8, brightness: 0.8, alpha: 1), 0.3),
            (.black, 0.3),
        ])

        let dominant = try XCTUnwrap(AlbumBackdropColor.dominant(in: cover))
        XCTAssertLessThan(min(dominant.hue, 1 - dominant.hue) * 360, 10)
    }

    func testNeighbouringBluesOutweighALargerSingleCyan() throws {
        // Like Melt My Eyez See Your Future: cyan is the biggest single hue, but the cover reads as blue.
        let cover = makeCover([
            (UIColor(hue: 180 / 360, saturation: 0.8, brightness: 0.8, alpha: 1), 0.3),
            (UIColor(hue: 210 / 360, saturation: 0.8, brightness: 0.8, alpha: 1), 0.2),
            (UIColor(hue: 240 / 360, saturation: 0.8, brightness: 0.8, alpha: 1), 0.25),
            (.black, 0.25),
        ])

        let dominant = try XCTUnwrap(AlbumBackdropColor.dominant(in: cover))
        XCTAssertGreaterThan(dominant.hue * 360, 195)
        XCTAssertLessThan(dominant.hue * 360, 245)
    }

    func testBlackAndWhiteCoverStaysNearBlack() throws {
        let cover = makeCover([
            (.black, 0.6),
            (.white, 0.35),
            (UIColor(red: 0.9, green: 0.1, blue: 0.1, alpha: 1), 0.05),
        ])

        let tint = AlbumBackdropColor.tint(for: try XCTUnwrap(AlbumBackdropColor.dominant(in: cover)))
        XCTAssertLessThanOrEqual(tint.saturation, 0.12)
        XCTAssertEqual(tint.brightness, 0.12, accuracy: 0.001)
    }

    func testDarkenedYellowLeansOrangeSoItReadsBrownNotOlive() {
        let tint = AlbumBackdropColor.tint(for: .init(hue: 42 / 360, saturation: 0.7, brightness: 0.95))

        XCTAssertEqual(tint.hue * 360, 33.6, accuracy: 0.1)
        XCTAssertEqual(tint.brightness, 0.24, accuracy: 0.001)
    }

    func testYellowShiftHasNoJumpAtEitherEnd() {
        func tintedDegrees(_ degrees: Double) -> Double {
            AlbumBackdropColor.tint(for: .init(hue: degrees / 360, saturation: 0.7, brightness: 0.9)).hue * 360
        }

        XCTAssertEqual(tintedDegrees(29.9), tintedDegrees(30.1), accuracy: 0.5)
        XCTAssertEqual(tintedDegrees(69.9), tintedDegrees(70.1), accuracy: 0.5)
    }

    func testVeryDarkCoverIsNotBrightenedPastItsOwnColor() {
        let tint = AlbumBackdropColor.tint(for: .init(hue: 230 / 360, saturation: 0.8, brightness: 0.16))

        XCTAssertEqual(tint.brightness, 0.16, accuracy: 0.001)
    }

    func testBlueHueIsNotShifted() {
        let tint = AlbumBackdropColor.tint(for: .init(hue: 0.65, saturation: 0.5, brightness: 0.6))

        XCTAssertEqual(tint.hue, 0.65, accuracy: 0.001)
        XCTAssertEqual(tint.saturation, 0.5, accuracy: 0.001)
    }

    /// Horizontal bands of color, each taking the given share of the cover's height.
    private func makeCover(_ bands: [(UIColor, CGFloat)]) -> CGImage {
        let side: CGFloat = 300
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        format.preferredRange = .standard
        let image = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format).image { context in
            var top: CGFloat = 0
            for (color, share) in bands {
                color.setFill()
                context.fill(CGRect(x: 0, y: top, width: side, height: side * share))
                top += side * share
            }
        }
        return image.cgImage!
    }
}
