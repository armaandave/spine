import CoreGraphics
import Nuke
import SwiftUI
import UIKit
import XCTest
@testable import Spine

@MainActor
final class BlurredArtTests: XCTestCase {
    // MARK: ArtInk

    func testGuessTable() {
        let dark = ["3D2416", "6F8FFF", "#3D2416", "garbage", "", "zzzzzz", "FFF", "12345", "1234567", nil]
        for hex in dark {
            XCTAssertEqual(ArtInk.guess(accentHex: hex), .dark, "\(hex ?? "nil") should be dark")
        }
        for hex in ["C9D6DE", "9CC7CF", "D8C27A", "#D8C27A", "d8c27a"] {
            XCTAssertEqual(ArtInk.guess(accentHex: hex), .light, "\(hex) should be light")
        }
    }

    func testResolveKeepsTheGuessInsideTheDeadBand() {
        for guess in [ArtInk.light, .dark] {
            for luma in [0.461, 0.47, 0.50, 0.53, 0.539] {
                XCTAssertEqual(ArtInk.resolve(luma: luma, guess: guess), guess)
            }
            XCTAssertEqual(ArtInk.resolve(luma: nil, guess: guess), guess)
        }
    }

    func testResolveFlipsOutsideTheDeadBand() {
        XCTAssertEqual(ArtInk.resolve(luma: 0.54, guess: .dark), .light)
        XCTAssertEqual(ArtInk.resolve(luma: 0.9, guess: .dark), .light)
        XCTAssertEqual(ArtInk.resolve(luma: 0.46, guess: .light), .dark)
        XCTAssertEqual(ArtInk.resolve(luma: 0.1, guess: .light), .dark)
        XCTAssertEqual(ArtInk.resolve(luma: 0.9, guess: .light), .light)
        XCTAssertEqual(ArtInk.resolve(luma: 0.1, guess: .dark), .dark)
    }

    func testConclusiveIsNilWithoutAMeasurementAndInsideTheDeadBand() {
        XCTAssertNil(ArtInk.conclusive(luma: nil))
        for luma in [0.461, 0.47, 0.50, 0.53, 0.539] {
            XCTAssertNil(ArtInk.conclusive(luma: luma))
        }
    }

    func testConclusiveSettlesOutsideTheDeadBandAndResolveFollowsIt() {
        XCTAssertEqual(ArtInk.conclusive(luma: 0.54), .light)
        XCTAssertEqual(ArtInk.conclusive(luma: 0.9), .light)
        XCTAssertEqual(ArtInk.conclusive(luma: 0.46), .dark)
        XCTAssertEqual(ArtInk.conclusive(luma: 0.1), .dark)
        for guess in [ArtInk.light, .dark] {
            for luma in [nil, 0.1, 0.46, 0.5, 0.54, 0.9] as [Double?] {
                XCTAssertEqual(ArtInk.resolve(luma: luma, guess: guess), ArtInk.conclusive(luma: luma) ?? guess)
            }
        }
    }

    func testInkTokens() {
        XCTAssertEqual(rgba(ArtInk.dark.text), [1, 1, 1, 1])
        XCTAssertEqual(rgba(ArtInk.dark.muted)[3], 0.80, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.dark.before)[3], 0.45, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.dark.rest)[3], 0.16, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.light.muted)[3], 0.72, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.light.before)[3], 0.36, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.light.rest)[3], 0.12, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.dark.chipFill)[3], 0.26, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.light.chipFill)[3], 0.45, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.dark.chipLine)[3], 0.22, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.light.chipLine)[3], 0.60, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.dark.star), rgba(HomeDesign.Palette.starGold))
        XCTAssertEqual(rgba(ArtInk.light.star), rgba(HomeDesign.Palette.lightInk))
        XCTAssertEqual(rgba(ArtInk.dark.heart), rgba(ArtInk.light.heart))
        XCTAssertEqual(rgba(ArtInk.dark.rewatch), rgba(HomeDesign.Palette.rewatchGreen))
        XCTAssertEqual(rgba(ArtInk.light.rewatch), rgba(HomeDesign.Palette.rewatchGreenDeep))
        XCTAssertEqual(rgba(ArtInk.dark.day)[3], 0.62, accuracy: 0.005)
        XCTAssertEqual(rgba(ArtInk.light.day)[3], 0.58, accuracy: 0.005)
        XCTAssertLessThan(rgba(ArtInk.dark.day)[3], rgba(ArtInk.dark.muted)[3], "The day is lighter than muted text")
        XCTAssertLessThan(rgba(ArtInk.light.day)[3], rgba(ArtInk.light.muted)[3])
    }

    func testAccentColor() {
        XCTAssertEqual(rgba(ArtInk.dark.accent(posterAccentHex: nil)), [1, 1, 1, 1])
        XCTAssertEqual(rgba(ArtInk.dark.accent(posterAccentHex: "junk")), [1, 1, 1, 1])
        XCTAssertEqual(
            rgba(ArtInk.dark.accent(posterAccentHex: "6F8FFF")),
            rgba(ArtAccent.pastel(fromHex: "6F8FFF"))
        )
        XCTAssertEqual(
            rgba(ArtInk.light.accent(posterAccentHex: "D8C27A")),
            rgba(HomeDesign.Palette.lightInk)
        )
    }

    // MARK: ArtLuma

    func testMeasureSolidBitmaps() throws {
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(bitmap(width: 60, height: 36) { _, _ in (0, 0, 0) })), 0, accuracy: 0.01)
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(bitmap(width: 60, height: 36) { _, _ in (255, 255, 255) })), 1, accuracy: 0.01)
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(bitmap(width: 60, height: 36) { _, _ in (128, 128, 128) })), 0.5, accuracy: 0.01)
        // Rec.601 weights green most.
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(bitmap(width: 60, height: 36) { _, _ in (0, 255, 0) })), 0.587, accuracy: 0.01)
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(bitmap(width: 60, height: 36) { _, _ in (255, 0, 0) })), 0.299, accuracy: 0.01)
    }

    func testMeasureLooksOnlyAtTheRightTwoThirds() throws {
        // Left third black, the rest white: the text area is all white.
        let leftThirdBlack = bitmap(width: 60, height: 36) { x, _ in x < 20 ? (0, 0, 0) : (255, 255, 255) }
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(leftThirdBlack)), 1, accuracy: 0.02)
        // Left half black, right half white: a quarter of the sampled area is black.
        let halves = bitmap(width: 60, height: 36) { x, _ in x < 30 ? (0, 0, 0) : (255, 255, 255) }
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(halves)), 0.75, accuracy: 0.06)
        // Top half black, bottom white: half and half.
        let stacked = bitmap(width: 60, height: 36) { _, y in y < 18 ? (0, 0, 0) : (255, 255, 255) }
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(stacked)), 0.5, accuracy: 0.06)
    }

    func testMeasureMemoIsByKeyAndCapped() throws {
        ArtLuma.forgetAll()
        let white = bitmap(width: 30, height: 18) { _, _ in (255, 255, 255) }
        let black = bitmap(width: 30, height: 18) { _, _ in (0, 0, 0) }

        XCTAssertNil(ArtLuma.remembered(key: "https://example.com/a.jpg"))
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(white, key: "https://example.com/a.jpg")), 1, accuracy: 0.01)
        // The memo wins over a different bitmap for the same URL: a URL is measured once.
        XCTAssertEqual(try XCTUnwrap(ArtLuma.measure(black, key: "https://example.com/a.jpg")), 1, accuracy: 0.01)
        XCTAssertEqual(try XCTUnwrap(ArtLuma.remembered(key: "https://example.com/a.jpg")), 1, accuracy: 0.01)

        for index in 0 ..< (ArtLuma.memoLimit + 40) {
            _ = ArtLuma.measure(black, key: "https://example.com/many-\(index).jpg")
        }
        XCTAssertEqual(ArtLuma.memoCount, ArtLuma.memoLimit)
        XCTAssertNil(ArtLuma.remembered(key: "https://example.com/many-0.jpg"))
        XCTAssertNotNil(ArtLuma.remembered(key: "https://example.com/many-\(ArtLuma.memoLimit + 39).jpg"))
        ArtLuma.forgetAll()
    }

    // MARK: ArtTone

    func testToneStaysInsideItsBounds() {
        for step in 0 ... 100 {
            let luma = Double(step) / 100
            let dark = ArtTone.overlayAlpha(ink: .dark, luma: luma)
            XCTAssertGreaterThanOrEqual(dark, 0.06)
            XCTAssertLessThanOrEqual(dark, 0.34)
            let light = ArtTone.overlayAlpha(ink: .light, luma: luma)
            XCTAssertGreaterThanOrEqual(light, 0)
            XCTAssertLessThanOrEqual(light, 0.22)
        }
        XCTAssertEqual(ArtTone.overlayAlpha(ink: .dark, luma: nil), 0.20)
        XCTAssertEqual(ArtTone.overlayAlpha(ink: .light, luma: nil), 0.04)
    }

    func testToneNudgesMidToneArtGently() {
        XCTAssertLessThanOrEqual(ArtTone.overlayAlpha(ink: .dark, luma: 0.5), 0.34)
        XCTAssertLessThanOrEqual(ArtTone.overlayAlpha(ink: .light, luma: 0.5), 0.22)
        // Dark art already dark enough gets the floor; bright art under white text gets the most.
        XCTAssertEqual(ArtTone.overlayAlpha(ink: .dark, luma: 0.1), 0.06)
        XCTAssertEqual(ArtTone.overlayAlpha(ink: .dark, luma: 0.9), 0.34)
        XCTAssertEqual(ArtTone.overlayAlpha(ink: .light, luma: 0.9), 0)
        XCTAssertEqual(ArtTone.overlayAlpha(ink: .light, luma: 0.0), 0.22)
        XCTAssertEqual(rgba(ArtTone.overlayColor(ink: .dark)), [0, 0, 0, 1])
        XCTAssertEqual(rgba(ArtTone.overlayColor(ink: .light)), [1, 1, 1, 1])
    }

    // MARK: ArtAccent

    func testPastelKeepsHueAndSetsBrightness() {
        for hex in ["6F8FFF", "3D2416", "D8C27A", "E5484D", "30A46C", "8E4EC6", "#F5CB84"] {
            let original = hsb(hex)
            let pastel = hsb(of: ArtAccent.pastel(fromHex: hex))
            XCTAssertEqual(pastel.brightness, 0.97, accuracy: 0.01, hex)
            XCTAssertGreaterThanOrEqual(pastel.saturation, 0.12 - 0.01, hex)
            XCTAssertLessThanOrEqual(pastel.saturation, 0.46 + 0.01, hex)
            XCTAssertLessThanOrEqual(hueDistanceDegrees(original.hue, pastel.hue), 2, hex)
        }
        // 0.6 of 0.56 saturation is 0.336, inside the clamp.
        XCTAssertEqual(ArtAccent.pastelHSB(fromHex: "6F8FFF").saturation, hsb("6F8FFF").saturation * 0.6, accuracy: 0.001)
        // Very saturated colors cap at 0.46; barely tinted ones rise to 0.12.
        XCTAssertEqual(ArtAccent.pastelHSB(fromHex: "FF0000").saturation, 0.46, accuracy: 0.0001)
        XCTAssertEqual(ArtAccent.pastelHSB(fromHex: "F0E8E8").saturation, 0.12, accuracy: 0.0001)
    }

    func testGreyAndMissingAccentBecomeNearWhite() {
        for hex in [nil, "808080", "FFFFFF", "000000", "junk"] as [String?] {
            let pastel = hsb(of: ArtAccent.pastel(fromHex: hex))
            XCTAssertEqual(pastel.brightness, 0.97, accuracy: 0.01, "\(hex ?? "nil")")
            XCTAssertLessThan(pastel.saturation, 0.03, "\(hex ?? "nil")")
        }
    }

    // MARK: Processors

    func testProcessorsKeepSizeAndOpaqueEdges() throws {
        for style in BlurredArt.Style.allCases where style.bakedAspect == nil {
            let source = UIImage(cgImage: bitmap(width: 8, height: 8) { _, _ in (200, 120, 60) }, scale: 3, orientation: .up)
            let output = try XCTUnwrap(style.processor.process(source), "\(style)")
            XCTAssertEqual(output.size, source.size, "\(style)")
            XCTAssertEqual(output.scale, source.scale, "\(style)")
            let cg = try XCTUnwrap(output.cgImage)
            XCTAssertEqual(cg.width, 8)
            XCTAssertEqual(cg.height, 8)
            let pixels = rgbaPixels(cg)
            for (x, y) in [(0, 0), (7, 0), (0, 7), (7, 7), (3, 0), (0, 4)] {
                XCTAssertEqual(pixels.alpha(x, y), 255, "\(style) edge \(x),\(y)")
            }
        }
    }

    func testTheCardBakesTheWholePosterSqueezedToTheCardsShape() throws {
        XCTAssertEqual(BlurredArt.Style.card.bakedAspect, 3.1)
        // A 2:3 poster as the card thumbnail decodes it (120x180 at 3x): red on top, blue at the bottom.
        let source = UIImage(
            cgImage: bitmap(width: 120, height: 180) { _, y in y < 90 ? (255, 0, 0) : (0, 0, 255) },
            scale: 3,
            orientation: .up
        )
        let output = try XCTUnwrap(BlurredArt.Style.card.processor.process(source))
        XCTAssertEqual(output.scale, 3)
        let cg = try XCTUnwrap(output.cgImage)
        XCTAssertEqual(cg.width, 120)
        XCTAssertEqual(cg.height, 39)
        let pixels = rgbaPixels(cg)
        // Both halves of the poster are on the card, top above bottom, and every edge stays opaque. The card's art is
        // darkened to 70%, so a full red or blue comes out near 178.
        XCTAssertGreaterThan(pixels.red(60, 2), 160, "the poster's top is the card's top")
        XCTAssertLessThan(pixels.blue(60, 2), 60)
        XCTAssertGreaterThan(pixels.blue(60, 36), 160, "the poster's bottom is the card's bottom")
        XCTAssertLessThan(pixels.red(60, 36), 60)
        for (x, y) in [(0, 0), (119, 0), (0, 38), (119, 38)] {
            XCTAssertEqual(pixels.alpha(x, y), 255, "edge \(x),\(y)")
        }
    }

    func testProcessorsBakeTheBrightnessAndSaturation() throws {
        let source = UIImage(cgImage: bitmap(width: 8, height: 8) { _, _ in (128, 128, 128) }, scale: 3, orientation: .up)
        let card = try XCTUnwrap(BlurredArt.Style.card.processor.process(source)?.cgImage)
        let tile = try XCTUnwrap(BlurredArt.Style.tile.processor.process(source)?.cgImage)
        let page = try XCTUnwrap(BlurredArt.Style.page.processor.process(source)?.cgImage)
        let glow = try XCTUnwrap(BlurredArt.Style.glow.processor.process(source)?.cgImage)
        // Grey stays grey under saturation; brightness multiplies: card x0.7, tile x0.6, glow x0.82, page x0.6.
        XCTAssertEqual(Double(rgbaPixels(card).red(4, 2)), 128 * 0.7, accuracy: 3)
        XCTAssertEqual(Double(rgbaPixels(tile).red(4, 4)), 128 * 0.6, accuracy: 3)
        XCTAssertEqual(Double(rgbaPixels(glow).red(4, 4)), 128 * 0.82, accuracy: 3)
        XCTAssertEqual(Double(rgbaPixels(page).red(4, 4)), 128 * 0.6, accuracy: 3)
    }

    func testTheCardIsCalmerThanTheTiles() {
        XCTAssertEqual(BlurredArt.Style.card.saturation, 1.1)
        XCTAssertEqual(BlurredArt.Style.card.brightness, 0.7)
        XCTAssertEqual(BlurredArt.Style.tile.saturation, 1.7, "The tiles keep their boost")
    }

    func testBlurSpreadsAnEdgeButKeepsTheBorderSolid() throws {
        // A bright left half and dark right half, 120 wide like the card thumbnail at 3x; the card squeezes it to 120x39.
        let source = UIImage(
            cgImage: bitmap(width: 120, height: 72) { x, _ in x < 60 ? (255, 255, 255) : (0, 0, 0) },
            scale: 3,
            orientation: .up
        )
        let output = try XCTUnwrap(BlurredArt.Style.card.processor.process(source)?.cgImage)
        let pixels = rgbaPixels(output)
        XCTAssertGreaterThan(pixels.red(66, 20), 10, "the dark side picks up light across the edge")
        XCTAssertLessThan(pixels.red(54, 20), 245, "the bright side picks up dark across the edge")
        XCTAssertEqual(pixels.alpha(0, 0), 255)
        XCTAssertEqual(pixels.alpha(119, 38), 255)
        // The card's art is darkened to 70%, so white comes out near 178.
        XCTAssertGreaterThan(pixels.red(0, 20), 168, "clamped edges do not fade toward transparent black")
        XCTAssertLessThan(pixels.red(119, 20), 15)
    }

    func testProcessorsShareOneContext() throws {
        XCTAssertTrue(BlurredArt.context === BlurredArt.context)
        let art = try source(of: "BlurredArt.swift")
        XCTAssertEqual(art.components(separatedBy: "CIContext(").count - 1, 1)
        for file in ["ArtInk.swift", "BlurredArtBackground.swift", "BakedShadow.swift"] {
            XCTAssertFalse(try source(of: file).contains("CIContext("), file)
        }
    }

    func testArtFolderHasNoLiveBlurOrMaterial() throws {
        for file in ["ArtInk.swift", "BlurredArt.swift", "BlurredArtBackground.swift", "BakedShadow.swift"] {
            let text = try source(of: file)
            XCTAssertFalse(text.contains(".blur("), file)
            XCTAssertFalse(text.contains("Material"), file)
            XCTAssertFalse(text.contains("GeometryReader"), file)
            XCTAssertFalse(text.contains("AsyncImage("), file)
        }
    }

    // MARK: Requests

    func testRequestHasTheStylesThumbnailAndProcessor() throws {
        let url = URL(string: "https://image.tmdb.org/t/p/w500/poster.jpg")
        for style in BlurredArt.Style.allCases {
            let request = try XCTUnwrap(BlurredArt.request(url: url, style: style))
            XCTAssertEqual(
                request.thumbnail,
                ImageRequest.ThumbnailOptions(size: style.thumbnailSize, unit: .points, contentMode: .aspectFill),
                "\(style)"
            )
            XCTAssertEqual(request.processors.map(\.identifier), [style.processor.identifier], "\(style)")
        }
        XCTAssertEqual(BlurredArt.Style.card.thumbnailSize, CGSize(width: 40, height: 24))
        XCTAssertEqual(BlurredArt.Style.tile.thumbnailSize, CGSize(width: 40, height: 40))
        XCTAssertEqual(BlurredArt.Style.glow.thumbnailSize, CGSize(width: 64, height: 77))
        XCTAssertEqual(BlurredArt.Style.page.thumbnailSize, CGSize(width: 54, height: 93))
        XCTAssertNil(BlurredArt.request(url: nil, style: .card))
    }

    func testProcessorIdentifiersDifferPerStyle() {
        let identifiers = BlurredArt.Style.allCases.map(\.processor.identifier)
        XCTAssertEqual(Set(identifiers).count, BlurredArt.Style.allCases.count)
    }

    func testTwoCallsBuildTheSameCacheEntry() throws {
        let url = URL(string: "https://image.tmdb.org/t/p/w500/poster.jpg")
        let cache = ImagePipeline.shared.cache
        for style in BlurredArt.Style.allCases {
            let view = try XCTUnwrap(BlurredArt.request(url: url, style: style))
            let prefetch = try XCTUnwrap(BlurredArt.request(url: url, style: style))
            XCTAssertEqual(cache.makeImageCacheKey(for: view), cache.makeImageCacheKey(for: prefetch), "\(style)")
        }
        let card = try XCTUnwrap(BlurredArt.request(url: url, style: .card))
        let tile = try XCTUnwrap(BlurredArt.request(url: url, style: .tile))
        XCTAssertNotEqual(cache.makeImageCacheKey(for: card), cache.makeImageCacheKey(for: tile))
    }

    func testBaseColorIsDarkenedOrLightened() {
        XCTAssertEqual(rgba(BlurredArtBackground.baseColor(accentHex: nil)), [0.16, 0.16, 0.16, 1])
        let dark = rgba(BlurredArtBackground.baseColor(accentHex: "6F8FFF"))
        XCTAssertLessThan(dark[2], 0.5)
        let light = rgba(BlurredArtBackground.baseColor(accentHex: "C9D6DE"))
        XCTAssertGreaterThan(light[0], 0.7)
    }

    // MARK: BakedShadow

    func testBakedShadowIsLargerThanTheRectangleAndFadesOut() throws {
        let size = CGSize(width: 66, height: 99)
        let image = BakedShadow.image(size: size, cornerRadius: 12, blur: 28, opacity: 0.5, offsetY: 14, scale: 2)
        XCTAssertEqual(image.size, BakedShadow.imageSize(size: size, blur: 28))
        XCTAssertEqual(image.size.width, size.width + 4 * 28)
        XCTAssertEqual(image.size.height, size.height + 4 * 28)
        XCTAssertEqual(image.scale, 2)

        let cg = try XCTUnwrap(image.cgImage)
        let pixels = rgbaPixels(cg)
        let pad = Int(BakedShadow.padding(blur: 28) * 2)
        let centerX = cg.width / 2
        let rectTop = pad
        let rectBottom = cg.height - pad

        XCTAssertGreaterThan(Double(pixels.alpha(centerX, cg.height / 2)) / 255, 0.4, "under the poster")
        XCTAssertEqual(pixels.alpha(0, 0), 0, "the corner of the bitmap is clear")
        XCTAssertEqual(pixels.alpha(cg.width - 1, cg.height - 1), 0)
        XCTAssertEqual(pixels.alpha(0, cg.height / 2), 0)
        // Outside the rectangle the shadow is still there, and the offset puts more of it below than above.
        let above = pixels.alpha(centerX, rectTop - 16)
        let below = pixels.alpha(centerX, rectBottom + 16)
        XCTAssertGreaterThan(below, 0)
        XCTAssertGreaterThan(below, above)
        // No shape is drawn: the shadow's own rectangle is not a hard-edged opaque block.
        XCTAssertLessThan(Double(pixels.alpha(centerX, cg.height / 2)) / 255, 0.55)
    }

    func testBakedShadowIsCachedByItsParameters() {
        let size = CGSize(width: 56, height: 84)
        let first = BakedShadow.image(size: size, cornerRadius: 12, blur: 28, opacity: 0.5, offsetY: 14, scale: 2)
        let again = BakedShadow.image(size: size, cornerRadius: 12, blur: 28, opacity: 0.5, offsetY: 14, scale: 2)
        let other = BakedShadow.image(size: size, cornerRadius: 12, blur: 28, opacity: 0.4, offsetY: 14, scale: 2)
        XCTAssertTrue(first === again)
        XCTAssertFalse(first === other)
    }

    // MARK: Helpers

    private func source(of file: String) throws -> String {
        let folder = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Core/Design/Art")
        return try String(contentsOf: folder.appendingPathComponent(file), encoding: .utf8)
    }

    private func rgba(_ color: Color) -> [Double] {
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        UIColor(color).getRed(&red, green: &green, blue: &blue, alpha: &alpha)
        return [Double(red), Double(green), Double(blue), Double(alpha)].map { ($0 * 1000).rounded() / 1000 }
    }

    private func hsb(_ hex: String) -> (hue: Double, saturation: Double, brightness: Double) {
        let rgb = ArtHex.rgb(hex)!
        return ArtHex.hsb(rgb)
    }

    private func hsb(of color: Color) -> (hue: Double, saturation: Double, brightness: Double) {
        var hue: CGFloat = 0, saturation: CGFloat = 0, brightness: CGFloat = 0, alpha: CGFloat = 0
        UIColor(color).getHue(&hue, saturation: &saturation, brightness: &brightness, alpha: &alpha)
        return (Double(hue), Double(saturation), Double(brightness))
    }

    private func hueDistanceDegrees(_ a: Double, _ b: Double) -> Double {
        let difference = abs(a - b)
        return min(difference, 1 - difference) * 360
    }

    private func bitmap(width: Int, height: Int, pixel: (Int, Int) -> (UInt8, UInt8, UInt8)) -> CGImage {
        var data = [UInt8](repeating: 255, count: width * height * 4)
        for y in 0 ..< height {
            for x in 0 ..< width {
                let (r, g, b) = pixel(x, y)
                let offset = (y * width + x) * 4
                data[offset] = r
                data[offset + 1] = g
                data[offset + 2] = b
            }
        }
        let provider = CGDataProvider(data: Data(data) as CFData)!
        return CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
            provider: provider,
            decode: nil,
            shouldInterpolate: false,
            intent: .defaultIntent
        )!
    }

    // MARK: Home's picture

    /// Rows of two white pixels then two black ones, top to bottom: sharp has full contrast, a blur has almost none.
    private func stripedImage(width: Int, height: Int) -> CGImage {
        let context = CGContext(
            data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        for row in 0 ..< height {
            let white = (row / 2) % 2 == 0
            context.setFillColor(red: white ? 1 : 0, green: white ? 1 : 0, blue: white ? 1 : 0, alpha: 1)
            context.fill(CGRect(x: 0, y: height - 1 - row, width: width, height: 1))
        }
        return context.makeImage()!
    }

    /// The largest difference in red between rows `rows` of column `x`.
    private func contrast(_ pixels: Pixels, rows: ClosedRange<Int>, x: Int = 10) -> Int {
        let values = rows.map { Int(pixels.red(x, $0)) }
        return (values.max() ?? 0) - (values.min() ?? 0)
    }

    func testThePictureIsBakedAsOneOpaqueImageInTheFramesShape() throws {
        let source = UIImage(cgImage: stripedImage(width: 160, height: 90), scale: 3, orientation: .up)
        let output = try XCTUnwrap(HomeBackdropMeltProcessor().process(source)?.cgImage)
        XCTAssertEqual(CGFloat(output.width) / CGFloat(output.height), HomeBackdropArt.aspect, accuracy: 0.01)
        XCTAssertEqual(output.width, 135, "a 16:9 backdrop is cropped to the media page's 3:2, keeping its height")
        let pixels = rgbaPixels(output)
        for (x, y) in [(0, 0), (output.width - 1, 0), (10, 45), (output.width - 1, output.height - 1), (5, output.height - 1)] {
            XCTAssertEqual(pixels.alpha(x, y), 255, "opaque at \(x), \(y)")
        }
    }

    func testThePictureIsTheMediaPagesStripSharpThenAWashOfItsColors() throws {
        let source = UIImage(cgImage: stripedImage(width: 160, height: 90), scale: 3, orientation: .up)
        let output = try XCTUnwrap(HomeBackdropMeltProcessor().process(source)?.cgImage)
        let pixels = rgbaPixels(output)
        // The strip is the top 90 rows; above its last 40% (row 54) the rows are the source's own, never blurred.
        for row in 0 ... 50 {
            let white = (row / 2) % 2 == 0
            XCTAssertEqual(Int(pixels.red(10, row)), white ? 255 : 0, accuracy: 3, "row \(row) is sharp")
        }
        // Below the strip is a smooth field of the picture's colors: the stripes' mid grey, not black and with no
        // stripes left in it.
        XCTAssertLessThan(contrast(pixels, rows: 96 ... (output.height - 1)), 24)
        for row in [100, output.height - 1] {
            XCTAssertGreaterThan(Int(pixels.red(10, row)), 60, "row \(row) has the picture's color, not black")
            XCTAssertLessThan(Int(pixels.red(10, row)), 220, "row \(row) is a mix of the picture's colors")
        }
    }

    func testThePictureProcessorHasAStableIdentifierThatNamesItsNumbers() {
        let identifier = HomeBackdropMeltProcessor().identifier
        XCTAssertEqual(identifier, HomeBackdropMeltProcessor().identifier)
        XCTAssertTrue(identifier.hasPrefix("com.armaan.spine.homebackdrop?"))
        for part in ["aspect=", "strip=1.5", "fade=0.4", "wash=0.22", "washsat="] {
            XCTAssertTrue(identifier.contains(part), "\(identifier) lacks \(part)")
        }
        XCTAssertNotEqual(identifier, BlurredArt.Style.glow.processor.identifier)
    }

    func testThePicturesRequestIsWhatTheViewLoadsAndNeedsNoBackdropToBeNil() throws {
        let url = try XCTUnwrap(URL(string: "https://example.com/backdrop.jpg"))
        let request = try XCTUnwrap(HomeBackdropArt.request(url: url))
        let viewRequest = try XCTUnwrap(SpineImagePipeline.request(
            url: url, thumbnailSize: HomeBackdropArt.thumbnailSize, processors: [HomeBackdropMeltProcessor()]
        ))
        let cache = ImagePipeline.shared.cache
        XCTAssertEqual(cache.makeImageCacheKey(for: request), cache.makeImageCacheKey(for: viewRequest))
        XCTAssertNotNil(request.thumbnail)
        XCTAssertEqual(request.processors.count, 1)
        XCTAssertNil(HomeBackdropArt.request(url: nil))
    }

    private struct Pixels {
        let width: Int
        let data: [UInt8]

        func red(_ x: Int, _ y: Int) -> UInt8 { data[(y * width + x) * 4] }
        func blue(_ x: Int, _ y: Int) -> UInt8 { data[(y * width + x) * 4 + 2] }
        func alpha(_ x: Int, _ y: Int) -> UInt8 { data[(y * width + x) * 4 + 3] }
    }

    /// Straight (unpremultiplied would lose precision at low alpha) RGBA read of an image: row 0 is the top.
    private func rgbaPixels(_ image: CGImage) -> Pixels {
        var data = [UInt8](repeating: 0, count: image.width * image.height * 4)
        data.withUnsafeMutableBytes { buffer in
            let context = CGContext(
                data: buffer.baseAddress,
                width: image.width,
                height: image.height,
                bitsPerComponent: 8,
                bytesPerRow: image.width * 4,
                space: CGColorSpace(name: CGColorSpace.sRGB)!,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )!
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        }
        return Pixels(width: image.width, data: data)
    }
}
