import Nuke
import SwiftUI
import XCTest
@testable import Spine

/// The tiles' numbers from the mock (R3HomeLeft), for the content width it is drawn at (358) and another phone's.
final class TileGeometryTests: XCTestCase {
    private let width: CGFloat = 358
    private let accuracy: CGFloat = 0.0001

    // MARK: Section

    func testTileSideAndContainerHeight() {
        XCTAssertEqual(TileGeometry.tileSide(contentWidth: 358), 173)
        XCTAssertEqual(TileGeometry.containerHeight(contentWidth: 358, isOpen: false), 173)
        XCTAssertEqual(TileGeometry.containerHeight(contentWidth: 358, isOpen: true), 410)
        XCTAssertEqual(TileGeometry.containerHeight(contentWidth: 370, isOpen: false), 179)
    }

    func testClosedFrames() {
        let left = TileGeometry.frame(tile: 0, open: nil, contentWidth: width)
        XCTAssertEqual(left, TileFrame(
            left: 0, width: 173, height: 173, slide: 0, scale: 1, opacity: 1, z: 1, isOpen: false, hitTestable: true
        ))
        let right = TileGeometry.frame(tile: 1, open: nil, contentWidth: width)
        XCTAssertEqual(right.left, 185, "173 and a 12 pt gap")
        XCTAssertEqual(right.width, 173)
        XCTAssertEqual(right.height, 173)
        XCTAssertTrue(right.hitTestable)
        XCTAssertEqual(TileGeometry.frame(tile: 1, open: nil, contentWidth: 370).left, 191)
    }

    func testTheOpenTileFillsTheWidthAt410() {
        for tile in 0 ... 1 {
            let frame = TileGeometry.frame(tile: tile, open: tile, contentWidth: width)
            XCTAssertEqual(frame, TileFrame(
                left: 0, width: 358, height: 410, slide: 0, scale: 1, opacity: 1, z: 3, isOpen: true, hitTestable: true
            ))
        }
        XCTAssertEqual(TileGeometry.frame(tile: 0, open: 0, contentWidth: 370).width, 370)
    }

    func testTheOtherTileSlidesAwayShrinksAndFades() {
        let leftWhenRightOpens = TileGeometry.frame(tile: 0, open: 1, contentWidth: width)
        XCTAssertEqual(leftWhenRightOpens.left, 0)
        XCTAssertEqual(leftWhenRightOpens.width, 173)
        XCTAssertEqual(leftWhenRightOpens.slide, -80)
        XCTAssertEqual(leftWhenRightOpens.scale, 0.82)
        XCTAssertEqual(leftWhenRightOpens.opacity, 0)
        XCTAssertFalse(leftWhenRightOpens.hitTestable)
        XCTAssertFalse(leftWhenRightOpens.isOpen)

        let rightWhenLeftOpens = TileGeometry.frame(tile: 1, open: 0, contentWidth: width)
        XCTAssertEqual(rightWhenLeftOpens.left, 185)
        XCTAssertEqual(rightWhenLeftOpens.slide, 100)
        XCTAssertEqual(rightWhenLeftOpens.scale, 0.82)
        XCTAssertEqual(rightWhenLeftOpens.opacity, 0)
        XCTAssertFalse(rightWhenLeftOpens.hitTestable)
    }

    // MARK: Carousel layout

    func testCarouselPaddingCentersTheFirstPoster() {
        XCTAssertEqual(TileGeometry.carouselPadding(contentWidth: 358), 119)
        XCTAssertEqual(TileGeometry.carouselPadding(contentWidth: 370), 125)
        XCTAssertEqual(TileGeometry.step, 134)
        XCTAssertEqual(TileGeometry.spacing, 14)
        XCTAssertEqual(TileGeometry.top, 70)
    }

    func testNaturalCentersStepByOnePosterAndAGap() {
        for index in 0 ..< 10 {
            let center = TileGeometry.naturalCenter(index: index, contentWidth: width)
            XCTAssertEqual(center.x, 179 + CGFloat(index) * 134, accuracy: accuracy)
            XCTAssertEqual(center.y, 160, accuracy: accuracy)
        }
    }

    // MARK: Fan

    func testTheFanPosesAreTheMocksFanTurnedIntoTransforms() {
        let front = TileGeometry.fanPose(index: 0, contentWidth: width)
        XCTAssertEqual(front.scale, 0.55, accuracy: accuracy, "66 over 120")
        XCTAssertEqual(front.offsetX, -92.5, accuracy: accuracy)
        XCTAssertEqual(front.offsetY, -94.5, accuracy: accuracy)
        XCTAssertEqual(front.degrees, 0)
        XCTAssertEqual(front.brightness, 1)
        XCTAssertEqual(front.opacity, 1)
        XCTAssertEqual(front.z, 5)

        let leftWing = TileGeometry.fanPose(index: 1, contentWidth: width)
        XCTAssertEqual(leftWing.scale, 56.0 / 120.0, accuracy: accuracy)
        XCTAssertEqual(leftWing.scale, 0.467, accuracy: 0.001)
        XCTAssertEqual(leftWing.offsetX, -262.5, accuracy: accuracy)
        XCTAssertEqual(leftWing.offsetY, -94, accuracy: accuracy)
        XCTAssertEqual(leftWing.degrees, -11)
        XCTAssertEqual(leftWing.brightness, 0.62)
        XCTAssertEqual(leftWing.opacity, 1)
        XCTAssertEqual(leftWing.z, 4)

        let rightWing = TileGeometry.fanPose(index: 2, contentWidth: width)
        XCTAssertEqual(rightWing.scale, 56.0 / 120.0, accuracy: accuracy)
        XCTAssertEqual(rightWing.offsetX, -324.5, accuracy: accuracy)
        XCTAssertEqual(rightWing.offsetY, -94, accuracy: accuracy)
        XCTAssertEqual(rightWing.degrees, 11)
        XCTAssertEqual(rightWing.brightness, 0.62)
        XCTAssertEqual(rightWing.z, 3)
    }

    func testPostersPastTheFanAreHiddenBehindIt() {
        for index in 3 ..< 10 {
            let pose = TileGeometry.fanPose(index: index, contentWidth: width)
            XCTAssertEqual(pose.opacity, 0, "poster \(index)")
            XCTAssertEqual(pose.brightness, 0.5)
            XCTAssertEqual(pose.degrees, 0)
            XCTAssertEqual(pose.z, 1)
            XCTAssertEqual(pose.scale, 56.0 / 120.0, accuracy: accuracy)
        }
        XCTAssertEqual(TileGeometry.fanPose(index: 3, contentWidth: width).offsetX, -494.5, accuracy: accuracy)
        XCTAssertEqual(TileGeometry.fanPose(index: 4, contentWidth: width).offsetX, -628.5, accuracy: accuracy)
        XCTAssertEqual(TileGeometry.fanPose(index: 3, contentWidth: width).offsetY, -96, accuracy: accuracy)
    }

    func testEveryFanPosterLandsOnTheFansCenterForEveryCount() {
        // Whatever the count, a poster's natural center plus its offset is where the fan wants it.
        for contentWidth: CGFloat in [343, 358, 370] {
            let side = TileGeometry.tileSide(contentWidth: contentWidth)
            for index in 0 ..< 10 {
                let fan = HomeDesign.Metric.fan(index: index, tileSide: side)
                let natural = TileGeometry.naturalCenter(index: index, contentWidth: contentWidth)
                let pose = TileGeometry.fanPose(index: index, contentWidth: contentWidth)
                XCTAssertEqual(natural.x + pose.offsetX, fan.x + fan.width / 2, accuracy: accuracy)
                XCTAssertEqual(natural.y + pose.offsetY, fan.y + fan.height / 2, accuracy: accuracy)
                XCTAssertEqual(pose.scale * TileGeometry.posterSize.width, fan.width, accuracy: accuracy)
            }
        }
    }

    // MARK: Open

    func testTheOpenPoseAtTheFirstPoster() {
        // Scroll 0: poster 0 is in the middle.
        let focus = TileGeometry.focusIndex(scrollX: 0, count: 5)
        XCTAssertEqual(focus, 0)
        let first = TileGeometry.openPose(index: 0, focus: focus)
        XCTAssertEqual(first, PosterPose(
            offsetX: 0, offsetY: 0, scale: 1, degrees: 0, brightness: 1, opacity: 1, z: 20
        ))
        let second = TileGeometry.openPose(index: 1, focus: focus)
        XCTAssertEqual(second.scale, 0.84)
        XCTAssertEqual(second.brightness, 0.5)
        XCTAssertEqual(second.dim, 0.5)
        XCTAssertEqual(second.z, 19)
        XCTAssertEqual(second.offsetX, 0)
        XCTAssertEqual(TileGeometry.openPose(index: 4, focus: focus).z, 16)
    }

    func testTheOpenPoseAtScroll268() {
        // 268 is two steps: poster 2 is in the middle.
        let focus = TileGeometry.focusIndex(scrollX: 268, count: 5)
        XCTAssertEqual(focus, 2)
        XCTAssertEqual(TileGeometry.openPose(index: 2, focus: focus).scale, 1)
        XCTAssertEqual(TileGeometry.openPose(index: 2, focus: focus).z, 20)
        XCTAssertEqual(TileGeometry.openPose(index: 2, focus: focus).dim, 0)
        XCTAssertEqual(TileGeometry.openPose(index: 0, focus: focus).scale, 0.84)
        XCTAssertEqual(TileGeometry.openPose(index: 0, focus: focus).z, 18)
        XCTAssertEqual(TileGeometry.openPose(index: 1, focus: focus).z, 19)
        XCTAssertEqual(TileGeometry.openPose(index: 3, focus: focus).z, 19)
        XCTAssertEqual(TileGeometry.openPose(index: 4, focus: focus).z, 18)
    }

    func testPoseChoosesTheFanOrTheOpenCarousel() {
        XCTAssertEqual(
            TileGeometry.pose(index: 1, focus: 0, isOpen: false, contentWidth: width),
            TileGeometry.fanPose(index: 1, contentWidth: width)
        )
        XCTAssertEqual(
            TileGeometry.pose(index: 1, focus: 0, isOpen: true, contentWidth: width),
            TileGeometry.openPose(index: 1, focus: 0)
        )
    }

    func testAClosedTileShowsItsFirstTitleInFrontWhateverTheFocusWas() {
        // The fan is by position: after an open, a swipe and a close the front poster is the first title again.
        for focus in 0 ..< 10 {
            let front = TileGeometry.pose(index: 0, focus: focus, isOpen: false, contentWidth: width)
            XCTAssertEqual(front, TileGeometry.fanPose(index: 0, contentWidth: width), "focus \(focus)")
            for index in 1 ..< 10 {
                let other = TileGeometry.pose(index: index, focus: focus, isOpen: false, contentWidth: width)
                XCTAssertGreaterThan(front.z, other.z, "index \(index) at focus \(focus)")
            }
        }
    }

    // MARK: Focus

    func testFocusIndexRoundsToTheNearestPoster() {
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 0, count: 5), 0)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 66.9, count: 5), 0)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 67, count: 5), 1)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 134, count: 5), 1)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 200, count: 5), 1)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 201, count: 5), 2)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 268, count: 5), 2)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 536, count: 5), 4)
    }

    func testFocusIndexIsKeptInsideTheList() {
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: -300, count: 5), 0, "pulled past the start")
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 5000, count: 5), 4, "pulled past the end")
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 5000, count: 2), 1)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 5000, count: 1), 0)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 5000, count: 10), 9)
        XCTAssertEqual(TileGeometry.focusIndex(scrollX: 100, count: 0), 0)
    }

    func testScrollXForIndexRoundTrips() {
        for count in [2, 5, 10] {
            for index in 0 ..< count {
                XCTAssertEqual(TileGeometry.focusIndex(scrollX: TileGeometry.scrollX(forIndex: index), count: count), index)
            }
        }
    }

    // MARK: Corner radius

    func testTheCornerRadiusReadsTwelveAtEverySize() {
        for scale: CGFloat in [0.467, 0.55, 0.84, 1] {
            let radius = TileGeometry.cornerRadius(forScale: scale)
            XCTAssertEqual(radius * scale, 12, accuracy: accuracy, "scale \(scale)")
        }
        XCTAssertEqual(TileGeometry.cornerRadius(forScale: 1), 12)
        XCTAssertEqual(TileGeometry.cornerRadius(forScale: 0), 1200, "a zero scale cannot divide by zero")
    }

    // MARK: Stagger

    func testOpeningStaggersFortyFiveMillisecondsApart() {
        for count in [2, 5, 10] {
            for index in 0 ..< count {
                XCTAssertEqual(
                    TileGeometry.staggerDelay(index: index, count: count, opening: true),
                    Double(index) * 0.045,
                    accuracy: 0.0000001
                )
            }
        }
    }

    func testClosingStaggersTheLastPosterFirst() {
        for count in [2, 5, 10] {
            XCTAssertEqual(TileGeometry.staggerDelay(index: count - 1, count: count, opening: false), 0)
            XCTAssertEqual(
                TileGeometry.staggerDelay(index: 0, count: count, opening: false),
                Double(count - 1) * 0.022,
                accuracy: 0.0000001
            )
        }
        XCTAssertEqual(TileGeometry.staggerDelay(index: 3, count: 5, opening: false), 0.022, accuracy: 0.0000001)
        XCTAssertEqual(TileGeometry.staggerDelay(index: 2, count: 5, opening: false), 0.044, accuracy: 0.0000001)
    }

    // MARK: Mounting

    func testAClosedTileMountsOnlyTheFan() {
        for index in 0 ..< 3 { XCTAssertTrue(TileGeometry.isMounted(index: index, allMounted: false)) }
        for index in 3 ..< 10 { XCTAssertFalse(TileGeometry.isMounted(index: index, allMounted: false)) }
    }

    func testAnOpeningTileMountsEveryPoster() {
        for index in 0 ..< 10 { XCTAssertTrue(TileGeometry.isMounted(index: index, allMounted: true)) }
    }

    func testOpeningWarmsPostersThreeToFive() {
        XCTAssertTrue(TileGeometry.openPrefetchIndexes(count: 2).isEmpty)
        XCTAssertTrue(TileGeometry.openPrefetchIndexes(count: 3).isEmpty)
        XCTAssertEqual(Array(TileGeometry.openPrefetchIndexes(count: 5)), [3, 4])
        XCTAssertEqual(Array(TileGeometry.openPrefetchIndexes(count: 10)), [3, 4, 5])
    }

    // MARK: Label

    func testTheLabelGrowsAndRises() {
        XCTAssertEqual(TileGeometry.titleTop(isOpen: false), 120)
        XCTAssertEqual(TileGeometry.titleTop(isOpen: true), 18)
        XCTAssertEqual(TileGeometry.titleOpenScale, 1.5)
        XCTAssertEqual(TileGeometry.subtitleTop(isOpen: false), 140.4, accuracy: 0.0001)
        XCTAssertEqual(TileGeometry.subtitleTop(isOpen: true), 47.6, accuracy: 0.0001)
    }

    // MARK: Panel

    func testALongSeriesWindowSitsAsOnThePanel() throws {
        // Episode 40 of 62: a feed card puts the window at 23, the panel (15 behind) at 25.
        let card = try XCTUnwrap(ProgressMeterModel.make(unit: .episodes, from: 38, to: 40, total: 62))
        guard case let .window(cardFirst, _, _) = card.kind else { return XCTFail("a window") }
        XCTAssertEqual(cardFirst, 23)

        let panel = try XCTUnwrap(TileGeometry.panelMeter(card))
        guard case let .window(first, span, total) = panel.kind else { return XCTFail("still a window") }
        XCTAssertEqual(first, 25)
        XCTAssertEqual(span, 20)
        XCTAssertEqual(total, 62)
        XCTAssertEqual(panel.from, 38)
        XCTAssertEqual(panel.to, 40)
    }

    func testPanelMeterLeavesOtherMetersAlone() throws {
        let ticks = ProgressMeterModel.make(unit: .episodes, from: 4, to: 5, total: 10)
        XCTAssertEqual(TileGeometry.panelMeter(ticks), ticks)
        let bar = ProgressMeterModel.make(unit: .pages, from: 100, to: 212, total: 400)
        XCTAssertEqual(TileGeometry.panelMeter(bar), bar)
        XCTAssertNil(TileGeometry.panelMeter(nil))
    }

    func testTheWindowFadesInOverTheFirstQuarter() {
        XCTAssertEqual(TileGeometry.windowFade(index: 0, count: 20), 0.025 / 0.24, accuracy: 0.0001)
        XCTAssertEqual(TileGeometry.windowFade(index: 4, count: 20), 0.225 / 0.24, accuracy: 0.0001)
        XCTAssertEqual(TileGeometry.windowFade(index: 5, count: 20), 1)
        XCTAssertEqual(TileGeometry.windowFade(index: 19, count: 20), 1)
        XCTAssertEqual(TileGeometry.windowFade(index: 0, count: 0), 1)
        for index in 1 ..< 20 {
            XCTAssertGreaterThanOrEqual(
                TileGeometry.windowFade(index: index, count: 20),
                TileGeometry.windowFade(index: index - 1, count: 20)
            )
        }
    }

    func testPanelNumbersFromTheMock() {
        XCTAssertEqual(TileGeometry.panelTop, 268)
        XCTAssertEqual(TileGeometry.panelInset, 16)
        XCTAssertEqual(TileGeometry.buttonHeight, 44)
        XCTAssertEqual(TileGeometry.closeSize, 44)
        XCTAssertEqual(TileGeometry.closeInset, 12)
        // The panel's last pixel stays inside the tile: top, a 23 pt title row, the 34 pt block, 12 and a button.
        XCTAssertLessThan(268 + 23 + TileGeometry.panelMeterBlock + 12 + TileGeometry.buttonHeight, 410)
    }

    // MARK: Motion and art

    @MainActor
    func testReduceMotionMakesEveryTileAnimationTheShortFade() {
        let reduced = HomeDesign.Motion.reduced
        XCTAssertEqual(TileMotion.frame(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.slide(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.fade(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.art(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.flight(delay: 0.2, reduceMotion: true), reduced, "no flying, no stagger")
        XCTAssertEqual(TileMotion.focus(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.posterFade(delay: 0.2, reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.dim(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.panelRise(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.closePop(reduceMotion: true), reduced)
    }

    @MainActor
    func testTheTileAnimationsKeepTheMocksDurations() {
        XCTAssertEqual(TileMotion.frame(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.62))
        XCTAssertEqual(TileMotion.slide(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.5))
        XCTAssertEqual(TileMotion.focus(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.45))
        XCTAssertEqual(
            TileMotion.flight(delay: 0.135, reduceMotion: false),
            Animation.timingCurve(0.3, 1.2, 0.4, 1, duration: 0.7).delay(0.135)
        )
    }

    @MainActor
    func testPostersAreDecodedAtTheCarouselSizeAndAlbumsAreSquare() {
        func media(_ type: String, _ orientation: PosterOrientation) -> MediaSummary {
            MediaSummary(
                ref: MediaRef(itemId: nil, source: "tmdb", mediaType: type, mediaId: "art-\(type)", seasonNumber: nil, episodeNumber: nil),
                title: "Art", posterUrl: "https://example.com/art.jpg", posterOrientation: orientation
            )
        }
        XCTAssertEqual(TilePosterArt.size(for: media("movie", .portrait)), CGSize(width: 120, height: 180))
        XCTAssertEqual(TilePosterArt.size(for: media("music", .square)), CGSize(width: 120, height: 120))
        let request = TilePosterArt.request(for: media("movie", .portrait))
        XCTAssertEqual(request?.url?.absoluteString, "https://example.com/art.jpg")
        XCTAssertNotNil(request?.thumbnail, "decoded at its display size, never at full size")
        XCTAssertNil(TilePosterArt.request(for: MediaSummary(
            ref: MediaRef(itemId: nil, source: "tmdb", mediaType: "movie", mediaId: "none", seasonNumber: nil, episodeNumber: nil),
            title: "None"
        )))
    }
}
