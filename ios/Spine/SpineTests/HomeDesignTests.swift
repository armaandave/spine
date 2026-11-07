import SwiftUI
import UIKit
import XCTest
@testable import Spine

@MainActor
final class HomeDesignTests: XCTestCase {
    // MARK: PosterSlot

    func testFeedCardSlotIsAPortraitOrASquare() {
        XCTAssertEqual(PosterSlot.feedCard.size, CGSize(width: 88, height: 132))
        XCTAssertEqual(PosterSlot.feedCard.cornerRadius, 8)
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "movie", orientation: .portrait), CGSize(width: 88, height: 132))
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "movie", orientation: nil), CGSize(width: 88, height: 132))
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "music", orientation: nil), CGSize(width: 132, height: 132))
        XCTAssertEqual(PosterSlot.feedCard.artworkSize(mediaType: "movie", orientation: .square), CGSize(width: 132, height: 132))
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

    func testTileSideSplitsTheContentWidth() {
        XCTAssertEqual(HomeDesign.Metric.tileSide(contentWidth: 358), 173)
        XCTAssertEqual(HomeDesign.Metric.tileSide(contentWidth: 370), 179)
        XCTAssertEqual(HomeDesign.Metric.tileSide(contentWidth: 359), 173)
        XCTAssertEqual(HomeDesign.Metric.tileSide(contentWidth: 361), 174)
    }

    func testMetricValues() {
        typealias M = HomeDesign.Metric
        XCTAssertEqual(M.pageMargin, 16)
        XCTAssertEqual(M.tileGap, 12)
        XCTAssertEqual(M.tileOpenHeight, 410)
        XCTAssertEqual(M.tileCorner, 30)
        XCTAssertEqual(M.cardHeight, 164)
        XCTAssertEqual(M.cardCorner, 24)
        XCTAssertEqual(M.cardInset, 14)
        XCTAssertEqual(M.cardTextLeading, 116)
        XCTAssertEqual(M.cardTextLeadingSquare, 160)
        XCTAssertEqual(M.cardTrailing, 14)
        XCTAssertEqual(M.cardGap, 10)
        XCTAssertEqual(M.cardTextTop, 12)
        XCTAssertEqual(M.meterBottom, 40)
        XCTAssertEqual(M.meterLineBottom, 33)
        XCTAssertEqual(M.countsBottom, 12)
        XCTAssertEqual(M.dayHeaderTop, 20)
        XCTAssertEqual(M.dayHeaderBottom, 10)
        XCTAssertEqual(M.glowHeight, 440)
        XCTAssertEqual(M.carouselStep, 134)
        XCTAssertEqual(M.carouselTop, 70)
        XCTAssertEqual(M.carouselRestScale, 0.84)
        XCTAssertEqual(M.carouselRestBrightness, 0.5)
        XCTAssertEqual(M.posterRadius, 12)
    }

    func testFanTable() {
        typealias Fan = HomeDesign.Metric.Fan
        let side = HomeDesign.Metric.tileSide(contentWidth: 358)
        let cx = side / 2
        XCTAssertEqual(side, 173)
        XCTAssertEqual(HomeDesign.Metric.fan(index: 0, tileSide: side), Fan(x: cx - 33, y: 16, width: 66, height: 99, degrees: 0, brightness: 1, z: 5, opacity: 1))
        XCTAssertEqual(HomeDesign.Metric.fan(index: 1, tileSide: side), Fan(x: cx - 64, y: 24, width: 56, height: 84, degrees: -11, brightness: 0.62, z: 4, opacity: 1))
        XCTAssertEqual(HomeDesign.Metric.fan(index: 2, tileSide: side), Fan(x: cx + 8, y: 24, width: 56, height: 84, degrees: 11, brightness: 0.62, z: 3, opacity: 1))
        for index in [3, 4] {
            XCTAssertEqual(
                HomeDesign.Metric.fan(index: index, tileSide: side),
                Fan(x: cx - 28, y: 22, width: 56, height: 84, degrees: 0, brightness: 0.5, z: 1, opacity: 0),
                "index \(index)"
            )
        }
        // The fan follows the tile's width.
        XCTAssertEqual(HomeDesign.Metric.fan(index: 0, tileSide: 179).x, 89.5 - 33)
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
        XCTAssertEqual(HomeDesign.Text.dayHeaderTrackingEm, 0.08)
        XCTAssertEqual(HomeDesign.Text.dayHeaderTracking, 0.96, accuracy: 0.0001)
        XCTAssertEqual(HomeDesign.Text.username, Font.system(size: 13.5, weight: .bold))
        XCTAssertEqual(HomeDesign.Text.tileTitleOpen, Font.system(size: 24, weight: .heavy))
    }

    // MARK: Motion

    func testMotionDurationsArePlainNumbers() {
        typealias M = HomeDesign.Motion
        XCTAssertEqual(M.tileHeightDuration, 0.62)
        XCTAssertEqual(M.tileWidthDuration, 0.62)
        XCTAssertEqual(M.transformDuration, 0.45)
        XCTAssertEqual(M.panelRiseDuration, 0.5)
        XCTAssertEqual(M.springDuration, 0.7)
        XCTAssertEqual(M.glowCrossfadeDuration, 0.7)
        XCTAssertEqual(M.reducedDuration, 0.1)
        XCTAssertEqual(M.openStaggerMs, 45)
        XCTAssertEqual(M.closeStaggerMs, 22)
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
        XCTAssertEqual(M.panelRise(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.5))
        XCTAssertEqual(M.spring(reduceMotion: false), .timingCurve(0.3, 1.2, 0.4, 1, duration: 0.7))
        XCTAssertEqual(M.pop(reduceMotion: false), .spring(response: 0.4, dampingFraction: 0.6))
        XCTAssertEqual(M.glowCrossfade(reduceMotion: false), .easeInOut(duration: 0.7))
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

    func testPosterStagger() {
        typealias M = HomeDesign.Motion
        XCTAssertEqual(M.openStagger(index: 0), 0)
        XCTAssertEqual(M.openStagger(index: 3), 0.135, accuracy: 0.0001)
        XCTAssertEqual(M.closeStagger(index: 4, count: 5), 0)
        XCTAssertEqual(M.closeStagger(index: 0, count: 5), 0.088, accuracy: 0.0001)
        XCTAssertEqual(M.closeStagger(index: 9, count: 5), 0)
    }

    // MARK: Helpers

    private func components(_ color: Color) -> [Int] {
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        UIColor(color).getRed(&red, green: &green, blue: &blue, alpha: &alpha)
        return [red, green, blue].map { Int(($0 * 255).rounded()) }
    }
}
