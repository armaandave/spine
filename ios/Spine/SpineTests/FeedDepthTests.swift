import SwiftUI
import UIKit
import XCTest
@testable import Spine

/// The feed card's depth (direction B): its tokens, the marks' geometry and palettes, the baked images, the pills.
@MainActor
final class FeedDepthTests: XCTestCase {
    private typealias D = HomeDesign.FeedDepth

    // MARK: Tokens

    func testTheTokensAreTheDesignsNumbers() {
        XCTAssertEqual(D.countsPillHeight, 22)
        XCTAssertEqual(D.countsPillHPad, 9)
        XCTAssertEqual(D.countsPillGap, 8)
        XCTAssertEqual(D.countsPairGap, 4)
        XCTAssertEqual(D.countsIcon, 13)
        XCTAssertEqual(D.countsTrailing, 10)
        XCTAssertEqual(D.countsBottom, 8)
        XCTAssertEqual(D.countsReserveBase, 64)
        XCTAssertEqual(D.countsReservePerDigit, 7)

        XCTAssertEqual(D.statusPillHeight, 19)
        XCTAssertEqual(D.statusLeading, 6)
        XCTAssertEqual(D.statusTrailing, 8)
        XCTAssertEqual(D.statusGap, 4)
        XCTAssertEqual(D.statusDot, 5)
        XCTAssertEqual(D.statusDotMargin, 1)

        XCTAssertEqual(D.heartSize, 18)
        XCTAssertEqual(D.rewatchSize, 22)
        XCTAssertEqual(D.markMargin, -1)

        XCTAssertEqual(D.posterShadowBlur, 9)
        XCTAssertEqual(D.posterShadowOffsetY, 5)
        XCTAssertEqual(D.posterShadowInset, 4)
        XCTAssertEqual(D.posterShadowOpacity, 0.5)
        XCTAssertEqual(D.posterContactBlur, 3)
        XCTAssertEqual(D.posterContactOffsetY, 1)
        XCTAssertEqual(D.posterContactOpacity, 0.4)
        XCTAssertEqual(D.posterGlossTop, 0.14)
        XCTAssertEqual(D.posterInnerRim, 0.18)
        XCTAssertEqual(D.posterOuterRim, 0.30)

        XCTAssertEqual(D.darkLiftOpacity, 0.32)
        XCTAssertEqual(D.lightHaloOpacity, 0.30)
        XCTAssertEqual(D.pillHighlightDark, 0.30)
        XCTAssertEqual(D.pillHighlightLight, 0.75)
    }

    func testThePosterShadowStaysInsideTheCard() {
        // Its lowest edge and its left edge, as the card's poster sits at (12, 12), 60 x 90.
        let lowest = 12 + 90 - D.posterShadowInset + D.posterShadowOffsetY + D.posterShadowBlur
        XCTAssertLessThan(lowest, HomeDesign.Metric.cardHeight)
        let leftmost = 12 + D.posterShadowInset - D.posterShadowBlur
        XCTAssertGreaterThan(leftmost, 0)
    }

    func testTheCountsReserveIs64AndSevenADigit() {
        XCTAssertEqual(FeedCountsPill.reserve(for: nil), 0)
        for (likes, comments) in [(0, 0), (12, 3), (1204, 38), (999_999, 999_999)] {
            let engagement = PostEngagement(hasLiked: false, likeCount: likes, commentCount: comments)
            let digits = likes.formatted().count + comments.formatted().count
            XCTAssertEqual(FeedCountsPill.reserve(for: engagement), 64 + 7 * CGFloat(digits))
            XCTAssertEqual(FeedCardView.countsReserve(for: engagement), FeedCountsPill.reserve(for: engagement))
        }
    }

    // MARK: Marks

    func testTheHeartFillsItsBoxAsDesigned() {
        let box = HeartShape().path(in: CGRect(x: 0, y: 0, width: 24, height: 24)).boundingRect
        XCTAssertEqual(box.minX, 3, accuracy: 0.01)
        XCTAssertEqual(box.minY, 4.4, accuracy: 0.01)
        XCTAssertEqual(box.width, 18, accuracy: 0.01)
        XCTAssertEqual(box.height, 16, accuracy: 0.01)
    }

    func testTheRewatchMarkStaysInsideItsBox() {
        let box = RewatchShape().path(in: CGRect(x: 0, y: 0, width: 24, height: 24)).boundingRect
        XCTAssertGreaterThanOrEqual(box.minX, 0)
        XCTAssertGreaterThanOrEqual(box.minY, 0)
        XCTAssertLessThanOrEqual(box.maxX, 24)
        XCTAssertLessThanOrEqual(box.maxY, 24)
        XCTAssertGreaterThan(box.width, 12)
    }

    func testTheMarksAreNotEmptyAndScaleLinearly() {
        for mark in [FeedMark.heart, FeedMark.rewatch] {
            let small = mark.path(in: CGRect(x: 0, y: 0, width: 12, height: 12)).boundingRect
            let large = mark.path(in: CGRect(x: 0, y: 0, width: 24, height: 24)).boundingRect
            XCTAssertFalse(small.isEmpty)
            XCTAssertEqual(large.width, small.width * 2, accuracy: 0.01)
            XCTAssertEqual(large.height, small.height * 2, accuracy: 0.01)
            XCTAssertEqual(large.minX, small.minX * 2, accuracy: 0.01)
            XCTAssertEqual(large.minY, small.minY * 2, accuracy: 0.01)
        }
    }

    func testTheHeartsGradientPointsAreTheBoardsSvgDefaults() {
        let units = MarkGradientUnits.units(for: .heart)
        XCTAssertEqual(units.center.x, 0.5, accuracy: 0.01)
        XCTAssertEqual(units.center.y, 0.437, accuracy: 0.01)
        XCTAssertEqual(units.radiusFraction, 0.465, accuracy: 0.01)
        XCTAssertEqual(units.lineStart.x, 0.275, accuracy: 0.01)
        XCTAssertEqual(units.lineStart.y, 0.183, accuracy: 0.01)
        XCTAssertEqual(units.lineEnd.x, 0.65, accuracy: 0.01)
        XCTAssertEqual(units.lineEnd.y, 0.85, accuracy: 0.01)
    }

    func testThePalettesAreTheDesignsFourRows() {
        func close(_ rgb: ArtHex.RGB, _ hex: UInt32, _ label: String, line: UInt = #line) {
            let tolerance = 1.0 / 255
            XCTAssertEqual(rgb.red, Double((hex >> 16) & 0xFF) / 255, accuracy: tolerance, label, line: line)
            XCTAssertEqual(rgb.green, Double((hex >> 8) & 0xFF) / 255, accuracy: tolerance, label, line: line)
            XCTAssertEqual(rgb.blue, Double(hex & 0xFF) / 255, accuracy: tolerance, label, line: line)
        }
        let heartDark = MarkPalette.palette(for: .heart, ink: .dark)
        close(heartDark.hi, 0xFFA3B8, "heart dark hi")
        close(heartDark.mid, 0xFF4F7F, "heart dark mid")
        close(heartDark.lo, 0xE01E56, "heart dark lo")
        close(heartDark.glow, 0xFF4C78, "heart dark glow")
        close(heartDark.deep, 0x6E0028, "heart dark deep")
        XCTAssertEqual(heartDark.bodyCenterAlpha, 0.52)
        XCTAssertEqual(heartDark.bodyRimAlpha, 0.16)

        let heartLight = MarkPalette.palette(for: .heart, ink: .light)
        close(heartLight.hi, 0xFF5F95, "heart light hi")
        close(heartLight.mid, 0xE0145E, "heart light mid")
        close(heartLight.lo, 0xA3093F, "heart light lo")
        close(heartLight.glow, 0xC81450, "heart light glow")
        close(heartLight.deep, 0x5A001E, "heart light deep")
        XCTAssertEqual(heartLight.bodyCenterAlpha, 0.62)
        XCTAssertEqual(heartLight.bodyRimAlpha, 0.30)

        let rewatchDark = MarkPalette.palette(for: .rewatch, ink: .dark)
        close(rewatchDark.hi, 0xB8FBD0, "rewatch dark hi")
        close(rewatchDark.mid, 0x34D07A, "rewatch dark mid")
        close(rewatchDark.lo, 0x0E9E4B, "rewatch dark lo")
        close(rewatchDark.glow, 0x34D17A, "rewatch dark glow")
        close(rewatchDark.deep, 0x00461E, "rewatch dark deep")

        let rewatchLight = MarkPalette.palette(for: .rewatch, ink: .light)
        close(rewatchLight.hi, 0x2DBE72, "rewatch light hi")
        close(rewatchLight.mid, 0x0C8F47, "rewatch light mid")
        close(rewatchLight.lo, 0x055E2D, "rewatch light lo")
        close(rewatchLight.glow, 0x0A823C, "rewatch light glow")
        close(rewatchLight.deep, 0x003214, "rewatch light deep")
    }

    // MARK: Baked marks

    func testABakedMarkIsAnImageOfItsSizeAndGlowAndIsRememberedOnce() throws {
        FeedMarkImages.reset()
        let first = try XCTUnwrap(FeedMarkImages.image(.heart, ink: .dark, size: 18))
        // 18 pt and 8 pt of room on each side, at 3x.
        XCTAssertEqual(first.cgImage?.width, (18 + 16) * 3)
        XCTAssertEqual(first.cgImage?.height, (18 + 16) * 3)
        XCTAssertTrue(first === FeedMarkImages.image(.heart, ink: .dark, size: 18))

        let light = try XCTUnwrap(FeedMarkImages.image(.heart, ink: .light, size: 18))
        XCTAssertEqual(light.cgImage?.width, (18 + 6) * 3, "The light ink's halo is tighter")
        XCTAssertNotEqual(first.pngData(), light.pngData())
    }

    func testWarmingMakesExactlySixImages() {
        FeedMarkImages.reset()
        XCTAssertEqual(FeedMarkImages.cachedCount, 0)
        FeedMarkImages.warm()
        XCTAssertEqual(FeedMarkImages.cachedCount, 6)
        FeedMarkImages.warm()
        XCTAssertEqual(FeedMarkImages.cachedCount, 6)
    }

    func testAMarkViewTakesExactlyItsSize() throws {
        for (mark, size) in [(FeedMark.heart, 18.0), (.heart, 13), (.rewatch, 22)] {
            for ink in [ArtInk.dark, ArtInk.light] {
                // The baked glow reaches past the box, but the layout box is the mark's own size.
                let image = try XCTUnwrap(ImageRenderer(content: FeedMarkView(mark: mark, ink: ink, size: size)).uiImage)
                XCTAssertEqual(image.size.width, size, accuracy: 0.01)
                XCTAssertEqual(image.size.height, size, accuracy: 0.01)
            }
        }
    }

    // MARK: Status pill

    func testTheStatusWordsMapToTheirKinds() {
        XCTAssertEqual(FeedStatusPill.Kind(word: "Paused"), .paused)
        XCTAssertEqual(FeedStatusPill.Kind(word: "Picked back up"), .pickedBackUp)
        XCTAssertEqual(FeedStatusPill.Kind(word: "Stopped"), .stopped)
        XCTAssertEqual(FeedStatusPill.Kind(word: "Rewatching"), .other)
    }

    func testEachStatusKindHasItsOwnDotAndAGlowExceptTheNeutralOne() {
        func components(_ color: Color) -> [Int] {
            var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
            UIColor(color).getRed(&red, green: &green, blue: &blue, alpha: &alpha)
            return [red, green, blue].map { Int(($0 * 255).rounded()) }
        }
        XCTAssertEqual(components(FeedStatusPill.Kind.paused.dot(ink: .dark).color), [0xFF, 0xC2, 0x4D])
        XCTAssertEqual(components(FeedStatusPill.Kind.paused.dot(ink: .light).color), [0xD9, 0x8A, 0x00])
        XCTAssertEqual(components(FeedStatusPill.Kind.pickedBackUp.dot(ink: .dark).color), [0x34, 0xD0, 0x7A])
        XCTAssertEqual(components(FeedStatusPill.Kind.pickedBackUp.dot(ink: .light).color), [0x0C, 0x8F, 0x47])
        XCTAssertEqual(components(FeedStatusPill.Kind.stopped.dot(ink: .dark).color), [0xFF, 0x6B, 0x6B])
        XCTAssertEqual(components(FeedStatusPill.Kind.stopped.dot(ink: .light).color), [0xC0, 0x39, 0x2B])
        XCTAssertEqual(FeedStatusPill.Kind.paused.dot(ink: .dark).glow?.alpha, 0.85)
        XCTAssertEqual(FeedStatusPill.Kind.paused.dot(ink: .light).glow?.alpha, 0.55)
        XCTAssertEqual(FeedStatusPill.Kind.stopped.dot(ink: .dark).glow?.alpha, 0.7)
        XCTAssertNil(FeedStatusPill.Kind.other.dot(ink: .dark).glow)
        XCTAssertNil(FeedStatusPill.Kind.other.dot(ink: .light).glow)
    }

    func testTheStatusPillIsNineteenHighAndLongEnoughForItsLongestWord() throws {
        for ink in [ArtInk.dark, ArtInk.light] {
            let image = try XCTUnwrap(ImageRenderer(content: FeedStatusPill(word: "Picked back up", ink: ink)).uiImage)
            XCTAssertEqual(image.size.height, 19)
            // 6 + 1 + 5 + 1 + 4 before the word, 8 after it; the word is about 78 pt at 11 bold.
            XCTAssertGreaterThan(image.size.width, 100)
            XCTAssertLessThan(image.size.width, 125)
        }
    }

    // MARK: Counts pill

    func testALikedHeartDrawsDifferentlyFromAnUnlikedOne() throws {
        for ink in [ArtInk.dark, ArtInk.light] {
            let liked = FeedCountsPill(engagement: PostEngagement(hasLiked: true, likeCount: 5, commentCount: 2), ink: ink)
            let unliked = FeedCountsPill(engagement: PostEngagement(hasLiked: false, likeCount: 5, commentCount: 2), ink: ink)
            let likedImage = try XCTUnwrap(ImageRenderer(content: liked).uiImage)
            let unlikedImage = try XCTUnwrap(ImageRenderer(content: unliked).uiImage)
            XCTAssertEqual(likedImage.size, unlikedImage.size, "The pill is as wide either way")
            XCTAssertNotEqual(likedImage.pngData(), unlikedImage.pngData())
            XCTAssertEqual(likedImage.size.height, 22)
        }
    }

    func testAZeroCountStillReadsItsNumber() {
        XCTAssertEqual(FeedCountsPill.countText(0), "0")
        XCTAssertEqual(FeedCountsPill.countText(1204), "1,204")
    }

    func testThePillStaysClearOfTheMeterForTwoToSixDigits() throws {
        // The meter ends `cardTrailing + reserve` from the card's right edge; the pill starts `countsTrailing + width`.
        for (likes, comments) in [(1, 1), (12, 3), (12, 34), (123, 45), (123, 456), (1204, 456)] {
            let engagement = PostEngagement(hasLiked: true, likeCount: likes, commentCount: comments)
            let digits = likes.formatted().count + comments.formatted().count
            let pill = try XCTUnwrap(ImageRenderer(content: FeedCountsPill(engagement: engagement, ink: .dark)).uiImage)
            let meterRight = HomeDesign.Metric.cardTrailing + FeedCountsPill.reserve(for: engagement)
            let pillLeft = D.countsTrailing + pill.size.width
            XCTAssertGreaterThanOrEqual(meterRight - pillLeft, 6 - 0.5, "\(digits) digits leave \(meterRight - pillLeft) pt")
        }
    }

    // MARK: Baked shadows

    func testTheRectangleShadowIsStillTheOldImage() {
        let image = BakedShadow.image(
            size: CGSize(width: 60, height: 90), cornerRadius: 6, blur: 28, opacity: 0.5, offsetY: 14, scale: 2
        )
        XCTAssertEqual(image.size, CGSize(width: 60 + 112, height: 90 + 112))
        XCTAssertEqual(image.scale, 2)
        XCTAssertEqual(alpha(of: image, atX: 0, y: 0), 0, accuracy: 0.01, "The corner is clear")
        let center = alpha(of: image, atX: image.size.width / 2, y: image.size.height / 2 + 14)
        XCTAssertGreaterThan(center, 0.2)
        XCTAssertLessThanOrEqual(center, 0.5 + 0.01)
        XCTAssertTrue(image === BakedShadow.image(
            size: CGSize(width: 60, height: 90), cornerRadius: 6, blur: 28, opacity: 0.5, offsetY: 14, scale: 2
        ))
    }

    func testACapsuleShadowKeepsItsEndsAndIsRememberedOnce() {
        let shadow = BakedShadow.capsule(height: 22, blur: 3, offsetY: 1, scale: 2)
        XCTAssertEqual(shadow.image.size, CGSize(width: 66 + 12, height: 22 + 12))
        XCTAssertEqual(shadow.capInsets.left, 6 + 11)
        XCTAssertEqual(shadow.capInsets.right, 6 + 11)
        XCTAssertEqual(shadow.capInsets.top, 0)
        XCTAssertEqual(shadow.capInsets.bottom, 0)
        XCTAssertTrue(shadow.image === BakedShadow.capsule(height: 22, blur: 3, offsetY: 1, scale: 2).image)
        XCTAssertEqual(alpha(of: shadow.image, atX: shadow.image.size.width / 2, y: shadow.image.size.height / 2 + 1), 1, accuracy: 0.05)
        XCTAssertEqual(alpha(of: shadow.image, atX: 0, y: 0), 0, accuracy: 0.01)
    }

    private func alpha(of image: UIImage, atX x: CGFloat, y: CGFloat) -> Double {
        guard let cgImage = image.cgImage else { return -1 }
        let px = min(max(Int(x * image.scale), 0), cgImage.width - 1)
        let py = min(max(Int(y * image.scale), 0), cgImage.height - 1)
        var pixel = [UInt8](repeating: 0, count: 4)
        let context = CGContext(
            data: &pixel, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )
        context?.draw(cgImage, in: CGRect(x: -px, y: -(cgImage.height - 1 - py), width: cgImage.width, height: cgImage.height))
        return Double(pixel[3]) / 255
    }
}
