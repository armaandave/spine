import SwiftUI
import UIKit
import XCTest
@testable import Spine

/// The feed card's depth (direction B): its tokens, the marks' geometry and glow, the baked images, the pills.
@MainActor
final class FeedDepthTests: XCTestCase {
    private typealias D = HomeDesign.FeedDepth

    // MARK: Tokens

    func testTheTokensAreTheDesignsNumbers() {
        XCTAssertEqual(D.countsPillHeight, 22)
        XCTAssertEqual(D.countsPillHPad, 9)
        XCTAssertEqual(D.countsPillGap, 5)
        XCTAssertEqual(D.countsPairGap, 3)
        XCTAssertEqual(D.countsIcon, 13)
        XCTAssertEqual(D.countsRim, 1.1)
        XCTAssertEqual(D.countsTrailing, 10)
        XCTAssertEqual(D.countsBottom, 8)
        XCTAssertEqual(D.countsReserveBase, 61)
        XCTAssertEqual(D.countsReservePerDigit, 8)

        XCTAssertEqual(D.statusPillHeight, 19)
        XCTAssertEqual(D.statusLeading, 6)
        XCTAssertEqual(D.statusTrailing, 8)
        XCTAssertEqual(D.statusGap, 4)
        XCTAssertEqual(D.statusDot, 5)
        XCTAssertEqual(D.statusDotMargin, 1)

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

    func testTheCountsReserveIs61AndEightACharacter() {
        XCTAssertEqual(FeedCountsPill.reserve(for: nil), 0)
        for (likes, comments) in [(0, 0), (12, 3), (1204, 38), (999_999, 999_999)] {
            let engagement = PostEngagement(hasLiked: false, likeCount: likes, commentCount: comments)
            let digits = likes.formatted().count + comments.formatted().count
            XCTAssertEqual(FeedCountsPill.reserve(for: engagement), 61 + 8 * CGFloat(digits))
            XCTAssertEqual(FeedCardView.countsReserve(for: engagement), FeedCountsPill.reserve(for: engagement))
        }
    }

    // MARK: Marks

    func testTheHeartFillsTheWidthOfItsRectAndSitsInTheMiddle() {
        let box = HeartShape().path(in: CGRect(x: 0, y: 0, width: 24, height: 24)).boundingRect
        XCTAssertEqual(box.minX, 0, accuracy: 0.01)
        XCTAssertEqual(box.width, 24, accuracy: 0.01)
        XCTAssertEqual(box.height, 24 * 16 / 18, accuracy: 0.01, "The board's heart is 18 wide and 16 tall")
        XCTAssertEqual(box.midY, 12, accuracy: 0.01)
    }

    func testTheHeartScalesLinearly() {
        let small = HeartShape().path(in: CGRect(x: 0, y: 0, width: 12, height: 12)).boundingRect
        let large = HeartShape().path(in: CGRect(x: 0, y: 0, width: 24, height: 24)).boundingRect
        XCTAssertFalse(small.isEmpty)
        XCTAssertEqual(large.width, small.width * 2, accuracy: 0.01)
        XCTAssertEqual(large.height, small.height * 2, accuracy: 0.01)
        XCTAssertEqual(large.minY, small.minY * 2, accuracy: 0.01)
    }

    func testTheMarksAreTheSystemSymbolsTheyWereInTheirColors() {
        XCTAssertEqual(FeedMark.heart.symbolName, "heart.fill")
        XCTAssertEqual(FeedMark.rewatch.symbolName, "arrow.clockwise")
        for ink in [ArtInk.dark, ArtInk.light] {
            XCTAssertEqual(FeedMark.heart.color(for: ink), HomeDesign.Palette.heart)
            XCTAssertEqual(FeedMark.rewatch.color(for: ink), ink.rewatch)
        }
        XCTAssertEqual(FeedMark.rewatch.color(for: .dark), HomeDesign.Palette.rewatchGreen)
        XCTAssertEqual(FeedMark.rewatch.color(for: .light), HomeDesign.Palette.rewatchGreenDeep)
    }

    // MARK: Glow

    func testTheGlowIsStrongerOnDarkArtAndNeverReachesPastItsPadding() {
        for ink in [ArtInk.dark, ArtInk.light] {
            let layers = FeedMarkImages.glowLayers(for: ink)
            XCTAssertEqual(layers.count, 2)
            for layer in layers {
                XCTAssertLessThanOrEqual(layer.radius * 2, FeedMarkImages.padding(for: ink), "A glow is never cut")
            }
        }
        XCTAssertGreaterThan(
            FeedMarkImages.glowLayers(for: .dark).map(\.opacity).max() ?? 0,
            FeedMarkImages.glowLayers(for: .light).map(\.opacity).max() ?? 0
        )
    }

    func testABakedMarkIsTheSymbolPlusItsGlowRoomAndIsRememberedOnce() throws {
        FeedMarkImages.reset()
        let baked = try XCTUnwrap(FeedMarkImages.image(.heart, ink: .dark))
        let symbol = try XCTUnwrap(ImageRenderer(content: FeedMarkSymbol(mark: .heart, ink: .dark)).uiImage)
        let padding = FeedMarkImages.padding(for: .dark)
        XCTAssertEqual(baked.size.width, symbol.size.width + 2 * padding, accuracy: 1.5)
        XCTAssertEqual(baked.size.height, symbol.size.height + 2 * padding, accuracy: 1.5)
        XCTAssertTrue(baked === FeedMarkImages.image(.heart, ink: .dark))

        let light = try XCTUnwrap(FeedMarkImages.image(.heart, ink: .light))
        XCTAssertLessThan(light.size.width, baked.size.width, "The light ink's halo is tighter")
        XCTAssertNotEqual(baked.pngData(), light.pngData())
    }

    func testTheGlowFadesOutIntoNothingBeforeTheImageEdge() throws {
        FeedMarkImages.reset()
        for mark in FeedMarkImages.warmSet {
            let image = try XCTUnwrap(FeedMarkImages.image(mark, ink: .dark))
            let padding = FeedMarkImages.padding(for: .dark)
            XCTAssertEqual(alpha(of: image, atX: 0.3, y: 0.3), 0, accuracy: 0.01, "The corner is clear")
            XCTAssertGreaterThan(
                alpha(of: image, atX: padding - 1, y: image.size.height / 2), 0.01, "\(mark) glows just outside its box"
            )
        }
    }

    func testWarmingMakesExactlyFourImages() {
        FeedMarkImages.reset()
        XCTAssertEqual(FeedMarkImages.cachedCount, 0)
        FeedMarkImages.warm()
        XCTAssertEqual(FeedMarkImages.cachedCount, 4)
        FeedMarkImages.warm()
        XCTAssertEqual(FeedMarkImages.cachedCount, 4)
    }

    func testAMarkViewTakesExactlyTheSymbolsSize() throws {
        for mark in FeedMarkImages.warmSet {
            for ink in [ArtInk.dark, ArtInk.light] {
                // The baked glow reaches past the box, but the layout box is the symbol's own.
                let withGlow = try XCTUnwrap(ImageRenderer(content: FeedMarkView(mark: mark, ink: ink)).uiImage)
                let plain = try XCTUnwrap(ImageRenderer(content: FeedMarkSymbol(mark: mark, ink: ink)).uiImage)
                XCTAssertEqual(withGlow.size.width, plain.size.width, accuracy: 0.01)
                XCTAssertEqual(withGlow.size.height, plain.size.height, accuracy: 0.01)
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

    func testTheHeartAndTheBubbleAreDrawnTheSameSize() {
        let side = FeedCountsPill.outlineSide
        let rect = CGRect(x: 0, y: 0, width: side, height: side)
        let heart = HeartShape().path(in: rect).boundingRect
        let bubble = CommentBubbleShape().path(in: rect).boundingRect
        XCTAssertEqual(heart.width, side, accuracy: 0.2, "The heart fills the width of its box")
        XCTAssertEqual(bubble.width, side, accuracy: 0.2, "The bubble fills the width of its box")
        XCTAssertEqual(heart.width, bubble.width, accuracy: 0.2)
        XCTAssertEqual(heart.midX, bubble.midX, accuracy: 0.2)
        XCTAssertEqual(heart.midY, bubble.midY, accuracy: 0.2, "Centered on the same line")
        // A heart is wider than it is tall, so it is a little shorter than the round bubble, but not by much.
        XCTAssertEqual(heart.height, bubble.height, accuracy: 1.6)
        // Both are stroked with the same rim, which reaches half its width past the outline: the icon's box exactly.
        XCTAssertEqual(side + D.countsRim, D.countsIcon, accuracy: 0.001)
    }

    func testAHeartAndABubbleTakeTheSameBoxWhetherLikedOrNot() throws {
        for ink in [ArtInk.dark, ArtInk.light] {
            let pill = { (liked: Bool) in
                FeedCountsPill(engagement: PostEngagement(hasLiked: liked, likeCount: 5, commentCount: 2), ink: ink)
            }
            let liked = try XCTUnwrap(ImageRenderer(content: pill(true)).uiImage)
            let unliked = try XCTUnwrap(ImageRenderer(content: pill(false)).uiImage)
            XCTAssertEqual(liked.size, unliked.size)
        }
    }

    func testThePillIsTighterThanItWas() throws {
        // 2 x 9 of padding, two 13 pt icons, 3 pt from each icon to its number, 5 pt between the pairs, and two digits.
        let pill = try XCTUnwrap(ImageRenderer(content: FeedCountsPill(
            engagement: PostEngagement(hasLiked: false, likeCount: 5, commentCount: 2), ink: .dark
        )).uiImage)
        let fixed = 2 * D.countsPillHPad + 2 * (D.countsIcon + D.countsPairGap) + D.countsPillGap
        XCTAssertEqual(fixed, 55)
        XCTAssertGreaterThan(pill.size.width, fixed)
        XCTAssertLessThan(pill.size.width, fixed + 2 * 9, "Two digits take under 9 pt each")
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
