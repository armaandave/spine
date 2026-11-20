import SwiftUI
import UIKit
import XCTest
@testable import Spine

@MainActor
final class HomeDesignTests: XCTestCase {
    // MARK: PosterSlot

    func testFeedCardSlotIsAPortraitOrASquare() {
        XCTAssertEqual(PosterSlot.feedCard.size, CGSize(width: 60, height: 90))
        XCTAssertEqual(PosterSlot.feedCard.cornerRadius, 6)
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "movie", orientation: .portrait), CGSize(width: 60, height: 90))
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "movie", orientation: nil), CGSize(width: 60, height: 90))
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "music", orientation: nil), CGSize(width: 90, height: 90))
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "movie", orientation: .square), CGSize(width: 90, height: 90))
        XCTAssertEqual(HomeDesign.Metric.cardPoster, PosterSlot.feedCard.size)
        XCTAssertEqual(HomeDesign.Metric.cardSquare, PosterSlot.feedCard.artworkSize(mediaType: "music", orientation: .square))
    }

    func testCarouselAndHeroSlots() {
        XCTAssertEqual(PosterSlot.homeCarousel.size, CGSize(width: 120, height: 180))
        XCTAssertEqual(PosterSlot.homeCarousel.cornerRadius, 12)
        XCTAssertEqual(PosterSlot.postHero.size, CGSize(width: 120, height: 180))
        XCTAssertEqual(PosterSlot.postHero.cornerRadius, 10)
        XCTAssertEqual(HomeDesign.Metric.carouselPoster, PosterSlot.homeCarousel.size)
        // Other slots keep width x width for square art.
        XCTAssertEqual(PosterSlot.homeCarousel.artworkSize(mediaType: "music", orientation: .square), CGSize(width: 120, height: 120))
        XCTAssertEqual(PosterSlot.postHero.artworkSize(mediaType: "music", orientation: .square), CGSize(width: 120, height: 120))
    }

    func testExistingSlotsAreUnchanged() {
        XCTAssertEqual(PosterSlot.activityPost.size, CGSize(width: 74, height: 111))
        XCTAssertEqual(PosterSlot.profileRail.size, CGSize(width: 70, height: 105))
        XCTAssertEqual(PosterSlot.carousel.size, CGSize(width: 100, height: 150))
        XCTAssertEqual(PosterSlot.hero.cornerRadius, 10)
        XCTAssertEqual(PosterSlot.diaryEntry.cornerRadius, 6)
        XCTAssertEqual(PosterSlot.episodeStill.cornerRadius, 4)
        XCTAssertEqual(PosterSlot.activityPost.artworkSize(mediaType: "music", orientation: .square), CGSize(width: 74, height: 74))
        XCTAssertEqual(PosterSlot.tagGrid.artworkSize(mediaType: "game", orientation: .landscape), CGSize(width: 80, height: 120))
    }

    // MARK: Metric

    func testMetricValues() {
        typealias M = HomeDesign.Metric
        XCTAssertEqual(M.pageMargin, 16)
        XCTAssertEqual(M.tileGap, 12)
        XCTAssertEqual(M.tileOpenHeight, 410)
        XCTAssertEqual(M.tileCorner, 30)
        XCTAssertEqual(M.tileClosedHeight, 196)
        XCTAssertEqual(M.cardHeight, 115)
        XCTAssertEqual(M.cardCorner, 20)
        XCTAssertEqual(M.cardInset, 12)
        XCTAssertEqual(M.cardTextLeading, 86)
        XCTAssertEqual(M.cardTextLeadingSquare, 116)
        XCTAssertEqual(M.cardTrailing, 14)
        XCTAssertEqual(M.cardGap, 10)
        XCTAssertEqual(M.cardTextTop, 9)
        XCTAssertEqual(M.meterBottom, 15)
        XCTAssertEqual(M.meterLineBottom, 17)
        XCTAssertEqual(M.glowHeight, 440)
        XCTAssertEqual(M.carouselStep, 134)
        XCTAssertEqual(M.carouselTop, 70)
        XCTAssertEqual(M.carouselRestScale, 0.84)
        XCTAssertEqual(M.carouselRestBrightness, 0.5)
        XCTAssertEqual(M.posterRadius, 12)
        XCTAssertEqual(M.stripPoster, CGSize(width: 84, height: 126))
        XCTAssertEqual(M.stripCenterY, 81)
        XCTAssertEqual(M.stripInner, 54)
        XCTAssertEqual(M.stripStep, 92)
        XCTAssertEqual(M.stripCorner, 10)
        XCTAssertEqual(M.stripDim, [0, 0.12, 0.34])
    }

    func testFanTable() {
        // A 430 pt phone: 398 of content, the seam at 199 in the section (215 on the screen).
        let width: CGFloat = 398
        func fan(_ index: Int, _ side: CGFloat) -> HomeDesign.Metric.Fan {
            HomeDesign.Metric.fan(index: index, side: side, contentWidth: width)
        }
        // In progress, left of the seam: 161, 69 and -23 on the screen, 16 less in the section.
        for (index, left, brightness, z) in [(0, 103.0, 1.0, 5.0), (1, 11.0, 0.88, 4.0), (2, -81.0, 0.66, 3.0)] {
            let poster = fan(index, -1)
            XCTAssertEqual(poster.x, left, accuracy: 0.0001, "index \(index)")
            XCTAssertEqual(poster.brightness, brightness, accuracy: 0.0001)
            XCTAssertEqual(poster.z, z)
            XCTAssertEqual([poster.y, poster.width, poster.height], [18, 84, 126])
            XCTAssertEqual(poster.degrees, 0)
            XCTAssertEqual(poster.opacity, 1)
        }
        // Trending is the mirror image: 269, 361 and 453.
        XCTAssertEqual([fan(0, 1).x, fan(1, 1).x, fan(2, 1).x], [211, 303, 395])
        // The two inner posters are 24 pt apart, the others 8.
        XCTAssertEqual(fan(0, 1).x - (fan(0, -1).x + 84), 24)
        XCTAssertEqual(fan(0, -1).x - (fan(1, -1).x + 84), 8)
        // The rest wait hidden at the third place.
        for index in [3, 4] {
            let hidden = fan(index, -1)
            XCTAssertEqual(hidden.x, fan(2, -1).x, "index \(index)")
            XCTAssertEqual(hidden.opacity, 0)
            XCTAssertEqual(hidden.z, 1)
            XCTAssertEqual(hidden.brightness, 0.66, accuracy: 0.0001)
        }
        // The strip follows the page's width.
        XCTAssertEqual(HomeDesign.Metric.fan(index: 0, side: -1, contentWidth: 358).x, 179 - 54 - 42)
    }

    // MARK: Palette and text

    func testPalette() {
        typealias P = HomeDesign.Palette
        XCTAssertEqual(components(P.starGold), [255, 212, 92])
        XCTAssertEqual(components(P.heart), [255, 107, 138])
        XCTAssertEqual(components(P.lightInk), [13, 26, 31])
        XCTAssertEqual(components(P.avatarInk), [27, 27, 27])
        XCTAssertEqual(components(P.pageBackground), [0, 0, 0])
        XCTAssertEqual(P.lightInkMinLuminance, 0.40)
    }

    func testTextTokens() {
        XCTAssertEqual(HomeDesign.Text.maxCategory, .xxLarge)
        XCTAssertEqual(HomeDesign.Text.day, Font.system(size: 12, weight: .medium))
        XCTAssertEqual(HomeDesign.Text.username, Font.system(size: 13.5, weight: .bold))
        XCTAssertEqual(HomeDesign.Text.tileTitleOpen, Font.system(size: 24, weight: .heavy))
    }

    // MARK: Motion

    func testMotionDurationsArePlainNumbers() {
        typealias M = HomeDesign.Motion
        XCTAssertEqual(M.tileHeightDuration, 0.62)
        XCTAssertEqual(M.tileWidthDuration, 0.62)
        XCTAssertEqual(M.transformDuration, 0.45)
        XCTAssertEqual(M.panelRiseDuration, 0.319)
        XCTAssertEqual(M.springDuration, 0.7)
        XCTAssertEqual(M.glowCrossfadeDuration, 0.3)
        XCTAssertEqual(M.reducedDuration, 0.1)
        XCTAssertEqual(M.closeFrameDuration, 0.548)
        XCTAssertEqual(M.closeSlideDuration, 0.446)
        XCTAssertEqual(M.closeTileFadeDuration, 0.289)
        XCTAssertEqual(M.closeNeighborDelay, 0)
        XCTAssertEqual(M.closeNeighborDuration, 0.548)
        XCTAssertEqual(M.closeRestDuration, 0.383)
        XCTAssertEqual(M.closeFadeDuration, 0.191)
    }

    func testOpeningMirrorsTheCloseSoEveryPosterEndsTogether() {
        typealias M = HomeDesign.Motion
        XCTAssertEqual(M.openFrontDelay + M.closeFrameDuration, M.closeNeighborDelay + M.closeNeighborDuration)
        XCTAssertEqual(M.openRestDelay + M.closeRestDuration, M.closeNeighborDuration, accuracy: 0.0001)
        XCTAssertEqual(M.openFadeDelay, M.closeNeighborDuration, accuracy: 0.0001)
    }

    func testOpeningAndClosingAreQuickerThanTheFirstCutAndNeverWhip() {
        typealias M = HomeDesign.Motion
        // The first cut ran the tile over 0.86 s and the other tile's slide over 0.7 s; then 25% faster, then 15%.
        XCTAssertEqual(M.closeFrameDuration, 0.86 * 0.75 * 0.85, accuracy: 0.001)
        XCTAssertEqual(M.closeSlideDuration, 0.7 * 0.75 * 0.85, accuracy: 0.001)
        // The three posters that stay visible start together and take as long as the tile.
        XCTAssertEqual(M.closeNeighborDelay, 0, "no stagger between the posters")
        XCTAssertEqual(M.closeNeighborDuration, M.closeFrameDuration)
        // No control point leaves 0...1, so the curve cannot overshoot the fan's places.
        let curve = M.closeCurve
        for value in [curve.c1x, curve.c1y, curve.c2x, curve.c2y] {
            XCTAssertTrue((0.0 ... 1.0).contains(value))
        }
        XCTAssertEqual(
            M.closing(duration: 0.86, reduceMotion: false),
            .timingCurve(curve.c1x, curve.c1y, curve.c2x, curve.c2y, duration: 0.86)
        )
        XCTAssertEqual(M.closing(duration: 0.86, reduceMotion: true), M.reduced)
    }

    func testMotionCurvesAreTheDesigns() {
        typealias M = HomeDesign.Motion
        XCTAssertEqual(
            M.soft(duration: 0.62, reduceMotion: false),
            .timingCurve(0.25, 1, 0.35, 1, duration: 0.62)
        )
        XCTAssertEqual(M.tileHeight(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.62))
        XCTAssertEqual(M.tileWidth(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.62))
        XCTAssertEqual(M.transform(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.45))
        XCTAssertEqual(M.panelRise(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.319))
        XCTAssertEqual(M.spring(reduceMotion: false), .timingCurve(0.3, 1.2, 0.4, 1, duration: 0.7))
        XCTAssertEqual(M.pop(reduceMotion: false), .spring(response: 0.4, dampingFraction: 0.6))
        XCTAssertEqual(M.glowCrossfade(reduceMotion: false), .easeOut(duration: 0.3))
    }

    func testReduceMotionIsAShortEase() {
        typealias M = HomeDesign.Motion
        let reduced = Animation.easeOut(duration: 0.1)
        XCTAssertEqual(M.reduced, reduced)
        XCTAssertEqual(M.soft(duration: 0.62, reduceMotion: true), reduced)
        XCTAssertEqual(M.tileHeight(reduceMotion: true), reduced)
        XCTAssertEqual(M.tileWidth(reduceMotion: true), reduced)
        XCTAssertEqual(M.transform(reduceMotion: true), reduced)
        XCTAssertEqual(M.panelRise(reduceMotion: true), reduced)
        XCTAssertEqual(M.spring(reduceMotion: true), reduced)
        XCTAssertEqual(M.pop(reduceMotion: true), reduced)
        XCTAssertEqual(M.glowCrossfade(reduceMotion: true), reduced)
    }

    // MARK: Helpers

    private func components(_ color: Color) -> [Int] {
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        UIColor(color).getRed(&red, green: &green, blue: &blue, alpha: &alpha)
        return [red, green, blue].map { Int(($0 * 255).rounded()) }
    }
}
