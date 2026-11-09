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
        // A closed tile is 168 tall whatever its width: no longer square, no band of nothing under its text.
        XCTAssertEqual(TileGeometry.containerHeight(contentWidth: 358, isOpen: false), 168)
        XCTAssertEqual(TileGeometry.containerHeight(contentWidth: 358, isOpen: true), 410)
        XCTAssertEqual(TileGeometry.containerHeight(contentWidth: 370, isOpen: false), 168)
        // The space under the count's letters matches the 16 above the front poster (the count's baseline is about
        // 11 below its line's top), and the front poster of the fan sits above the title.
        let countBaseline = TileGeometry.subtitleTop(isOpen: false) + 11
        XCTAssertEqual(TileGeometry.closedHeight - countBaseline, HomeDesign.Metric.fan(index: 0, tileSide: 173).y, accuracy: 2)
        XCTAssertLessThan(HomeDesign.Metric.fan(index: 0, tileSide: 173).y + 99, TileGeometry.titleClosedTop)
    }

    func testClosedFrames() {
        let left = TileGeometry.frame(tile: 0, open: nil, contentWidth: width)
        XCTAssertEqual(left, TileFrame(
            left: 0, width: 173, height: 168, slide: 0, scale: 1, opacity: 1, z: 1, isOpen: false, hitTestable: true
        ))
        let right = TileGeometry.frame(tile: 1, open: nil, contentWidth: width)
        XCTAssertEqual(right.left, 185, "173 and a 12 pt gap")
        XCTAssertEqual(right.width, 173)
        XCTAssertEqual(right.height, 168)
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

    func testAClosedTileIgnoresTheFocus() {
        // The fan is built from where the tile rests, not from where an open tile is looking.
        for focus in 0 ..< 10 {
            for index in 0 ..< 10 {
                XCTAssertEqual(
                    TileGeometry.pose(index: index, focus: focus, isOpen: false, rest: 4, count: 10, contentWidth: width),
                    TileGeometry.fanPose(index: index, rest: 4, count: 10, contentWidth: width)
                )
            }
        }
    }

    // MARK: Rest

    func testTheFanIsBuiltAroundTheTitleTheTileRestsOn() {
        // In the middle: the title in front, the one before it left, the one after it right.
        var slots = TileGeometry.fanSlots(rest: 4, count: 10)
        XCTAssertEqual([slots.front, slots.left, slots.right], [4, 3, 5])
        slots = TileGeometry.fanSlots(rest: 1, count: 3)
        XCTAssertEqual([slots.front, slots.left, slots.right], [1, 0, 2])
        // At the first title: today's fan, 1, 2 and 3 (indexes 0, 1, 2).
        slots = TileGeometry.fanSlots(rest: 0, count: 10)
        XCTAssertEqual([slots.front, slots.left, slots.right], [0, 1, 2])
        // At the last: the last in front and the two before it behind.
        slots = TileGeometry.fanSlots(rest: 9, count: 10)
        XCTAssertEqual([slots.front, slots.left, slots.right], [9, 7, 8])
        slots = TileGeometry.fanSlots(rest: 2, count: 3)
        XCTAssertEqual([slots.front, slots.left, slots.right], [2, 0, 1])
    }

    func testShortListsFillOnlyTheSlotsTheyHave() {
        var slots = TileGeometry.fanSlots(rest: 0, count: 1)
        XCTAssertEqual(slots.front, 0)
        XCTAssertNil(slots.left)
        XCTAssertNil(slots.right)
        slots = TileGeometry.fanSlots(rest: 0, count: 2)
        XCTAssertEqual([slots.front, slots.left], [0, 1])
        XCTAssertNil(slots.right)
        slots = TileGeometry.fanSlots(rest: 1, count: 2)
        XCTAssertEqual([slots.front, slots.left], [1, 0])
        XCTAssertNil(slots.right)
        slots = TileGeometry.fanSlots(rest: 0, count: 0)
        XCTAssertEqual(slots.front, 0)
        XCTAssertNil(slots.left)
    }

    func testARestPastTheEndsIsKeptInsideTheList() {
        XCTAssertEqual(TileGeometry.fanSlots(rest: 99, count: 5).front, 4)
        XCTAssertEqual(TileGeometry.fanSlots(rest: -3, count: 5).front, 0)
    }

    func testEachPosterHasOneRoleAndTheRestAreHidden() {
        let roles = (0 ..< 10).map { TileGeometry.fanRole(index: $0, rest: 5, count: 10) }
        XCTAssertEqual(roles[5], .front)
        XCTAssertEqual(roles[4], .left)
        XCTAssertEqual(roles[6], .right)
        XCTAssertEqual(roles.filter { $0 == .hidden }.count, 7)
        // The first title's fan is the original one.
        for index in 0 ..< 10 {
            XCTAssertEqual(
                TileGeometry.fanPose(index: index, rest: 0, count: 10, contentWidth: width),
                TileGeometry.fanPose(index: index, contentWidth: width)
            )
        }
    }

    func testTheFrontPosterLandsInTheFrontSlotWhateverTheRest() {
        // A closed carousel is scrolled to the rest title, so its natural place is that far to the left.
        for contentWidth: CGFloat in [343, 358, 370] {
            let side = TileGeometry.tileSide(contentWidth: contentWidth)
            for rest in 0 ..< 10 {
                for index in 0 ..< 10 {
                    let role = TileGeometry.fanRole(index: index, rest: rest, count: 10)
                    let fan = HomeDesign.Metric.fan(index: role.slot, tileSide: side)
                    let natural = TileGeometry.naturalCenter(index: index, contentWidth: contentWidth)
                    let pose = TileGeometry.fanPose(index: index, rest: rest, count: 10, contentWidth: contentWidth)
                    let onScreenX = natural.x - TileGeometry.scrollX(forIndex: rest) + pose.offsetX
                    XCTAssertEqual(onScreenX, fan.x + fan.width / 2, accuracy: accuracy, "rest \(rest) poster \(index)")
                    XCTAssertEqual(natural.y + pose.offsetY, fan.y + fan.height / 2, accuracy: accuracy)
                }
            }
        }
    }

    func testTheFrontPosterIsAlwaysOnTopOfTheFan() {
        for rest in 0 ..< 10 {
            let front = TileGeometry.fanPose(index: rest, rest: rest, count: 10, contentWidth: width)
            XCTAssertEqual(front.scale, 0.55, accuracy: accuracy)
            XCTAssertEqual(front.brightness, 1)
            for index in 0 ..< 10 where index != rest {
                let other = TileGeometry.fanPose(index: index, rest: rest, count: 10, contentWidth: width)
                XCTAssertGreaterThan(front.z, other.z, "poster \(index) at rest \(rest)")
            }
        }
    }

    // MARK: Fold

    func testTheFoldFollowsTheScrollOneToOne() {
        XCTAssertEqual(TileGeometry.foldProgress(scroll: 0, tilesBottom: 320), 0)
        XCTAssertEqual(TileGeometry.foldProgress(scroll: 160, tilesBottom: 320), 0.5, accuracy: accuracy)
        XCTAssertEqual(TileGeometry.foldProgress(scroll: 80, tilesBottom: 320), 0.25, accuracy: accuracy)
        // Closed all the way once the tile has left the top of the screen, and no further.
        XCTAssertEqual(TileGeometry.foldProgress(scroll: 320, tilesBottom: 320), 1)
        XCTAssertEqual(TileGeometry.foldProgress(scroll: 500, tilesBottom: 320), 1)
        // Pulled past the top: not folded.
        XCTAssertEqual(TileGeometry.foldProgress(scroll: -40, tilesBottom: 320), 0)
    }

    func testTheFoldEndsWhereTheGlowsTilesEndBeforeTheyAreMeasured() {
        XCTAssertEqual(
            TileGeometry.foldProgress(scroll: GlowSpec.estimatedTilesBottom, tilesBottom: nil), 1
        )
        XCTAssertEqual(TileGeometry.foldProgress(scroll: 100, tilesBottom: 0), 0, "nothing to divide by")
    }

    func testTheOpenFoldGoesFromRestToFullyFoldedAsTheRowLeavesTheTop() {
        XCTAssertEqual(TileGeometry.openFoldProgress(scroll: 0, rowBottom: 300), 0, "at rest")
        XCTAssertEqual(TileGeometry.openFoldProgress(scroll: -30, rowBottom: 300), 0, "pulled past the top")
        XCTAssertEqual(TileGeometry.openFoldProgress(scroll: 150, rowBottom: 300), 0.5, accuracy: accuracy)
        XCTAssertEqual(TileGeometry.openFoldProgress(scroll: 300, rowBottom: 300), 1, "row bottom at the top")
        XCTAssertEqual(TileGeometry.openFoldProgress(scroll: 450, rowBottom: 300), 1, "fully off")
    }

    func testTheOpenFoldMovesEveryPosterTowardTheFocusedOneAndNeverTheFocusedOne() {
        let count = 5
        for focus in 0 ..< count {
            for index in 0 ..< count {
                let slots = CGFloat(index - focus)
                let offset = TileGeometry.openFoldOffset(slotsFromFocus: slots, progress: 1)
                let weight = TileGeometry.openFoldWeight(slotsFromFocus: slots)
                if index == focus {
                    XCTAssertEqual(offset, 0, "the focused poster stays")
                    XCTAssertEqual(weight, 0, "and is neither shrunk nor darkened")
                } else {
                    // Fully folded, it sits where the focused poster does.
                    XCTAssertEqual(CGFloat(index - focus) * TileGeometry.step + offset, 0, accuracy: accuracy)
                    XCTAssertEqual(weight, 1)
                    XCTAssertEqual(offset > 0, index < focus, "left ones slide right, right ones slide left")
                }
                XCTAssertEqual(
                    TileGeometry.openFoldOffset(slotsFromFocus: slots, progress: 0), 0, "nothing moves at rest"
                )
            }
        }
    }

    func testOnlyTheSideWithNeighborsSlidesWhenTheFocusIsFirstOrLast() {
        let count = 4
        func moved(focus: Int) -> [Int] {
            (0 ..< count).filter {
                TileGeometry.openFoldOffset(slotsFromFocus: CGFloat($0 - focus), progress: 1) != 0
            }
        }
        XCTAssertEqual(moved(focus: 0), [1, 2, 3], "all to the right of the first")
        XCTAssertEqual(moved(focus: 3), [0, 1, 2], "all to the left of the last")
        XCTAssertEqual(moved(focus: 1), [0, 2, 3])
        // One neighbor: only that one.
        XCTAssertEqual((0 ..< 2).filter {
            TileGeometry.openFoldOffset(slotsFromFocus: CGFloat($0), progress: 1) != 0
        }, [1])
    }

    func testTheOpenFoldWeightGrowsToOneStepAndNoFurther() {
        XCTAssertEqual(TileGeometry.openFoldWeight(slotsFromFocus: 0.5), 0.5, accuracy: accuracy)
        XCTAssertEqual(TileGeometry.openFoldWeight(slotsFromFocus: -0.5), 0.5, accuracy: accuracy)
        XCTAssertEqual(TileGeometry.openFoldWeight(slotsFromFocus: -3), 1)
        XCTAssertEqual(TileGeometry.openFoldOffset(slotsFromFocus: 2, progress: 0.5), -TileGeometry.step, accuracy: accuracy)
    }

    func testABackPosterSlidesBehindTheFrontOne() {
        let side = TileGeometry.tileSide(contentWidth: width)
        let front = HomeDesign.Metric.fan(index: 0, tileSide: side)
        for role in [TileGeometry.FanRole.left, .right] {
            let back = HomeDesign.Metric.fan(index: role.slot, tileSide: side)
            let slide = TileGeometry.foldSlide(role: role, contentWidth: width)
            XCTAssertEqual(back.x + back.width / 2 + slide.width, front.x + front.width / 2, accuracy: accuracy)
            XCTAssertEqual(back.y + back.height / 2 + slide.height, front.y + front.height / 2, accuracy: accuracy)
        }
        XCTAssertGreaterThan(TileGeometry.foldSlide(role: .left, contentWidth: width).width, 0, "left moves right")
        XCTAssertLessThan(TileGeometry.foldSlide(role: .right, contentWidth: width).width, 0, "right moves left")
        XCTAssertTrue(TileGeometry.FanRole.left.folds)
        XCTAssertTrue(TileGeometry.FanRole.right.folds)
        XCTAssertFalse(TileGeometry.FanRole.front.folds)
        XCTAssertFalse(TileGeometry.FanRole.hidden.folds)
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

    // MARK: Opacity

    func testAPosterFromBehindTheFanStaysClearUntilItHasBeenDrawnOnce() {
        let width = TileGeometry.referenceContentWidth
        for rest in [0, 4, 9] {
            for index in 0 ..< 10 {
                let role = TileGeometry.fanRole(index: index, rest: rest, count: 10)
                let open = TileGeometry.openPose(index: index, focus: rest)
                let fan = TileGeometry.fanPose(index: index, rest: rest, count: 10, contentWidth: width)
                let fresh = TileGeometry.cellOpacity(pose: open, role: role, isOpen: true, entered: false)
                XCTAssertEqual(fresh, role == .hidden ? 0 : 1, "index \(index), rest \(rest)")
                XCTAssertEqual(TileGeometry.cellOpacity(pose: open, role: role, isOpen: true, entered: true), 1)
                XCTAssertEqual(
                    TileGeometry.cellOpacity(pose: fan, role: role, isOpen: false, entered: false), fan.opacity
                )
            }
        }
    }

    // MARK: Mounting

    func testAClosedTileMountsOnlyTheFan() {
        for index in 0 ..< 3 {
            XCTAssertTrue(TileGeometry.isMounted(index: index, rest: 0, count: 10, allMounted: false))
        }
        for index in 3 ..< 10 {
            XCTAssertFalse(TileGeometry.isMounted(index: index, rest: 0, count: 10, allMounted: false))
        }
        // Resting on the fifth: the fourth, fifth and sixth.
        let mounted = (0 ..< 10).filter { TileGeometry.isMounted(index: $0, rest: 4, count: 10, allMounted: false) }
        XCTAssertEqual(mounted, [3, 4, 5])
        XCTAssertEqual(mounted.count, TileGeometry.fanMountedCount)
    }

    func testAnOpeningTileMountsEveryPoster() {
        for index in 0 ..< 10 {
            XCTAssertTrue(TileGeometry.isMounted(index: index, rest: 4, count: 10, allMounted: true))
        }
    }

    func testOpeningWarmsThePostersAroundTheFanThatItDoesNotShow() {
        XCTAssertTrue(TileGeometry.openPrefetchIndexes(rest: 0, count: 2).isEmpty)
        XCTAssertTrue(TileGeometry.openPrefetchIndexes(rest: 0, count: 3).isEmpty)
        XCTAssertEqual(TileGeometry.openPrefetchIndexes(rest: 0, count: 5), [3, 4])
        XCTAssertEqual(TileGeometry.openPrefetchIndexes(rest: 0, count: 10), [3, 4, 5])
        XCTAssertEqual(TileGeometry.openPrefetchIndexes(rest: 5, count: 10), [2, 3, 7])
        XCTAssertEqual(TileGeometry.openPrefetchIndexes(rest: 9, count: 10), [4, 5, 6])
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
        for role in [TileGeometry.FanRole.front, .left, .right, .hidden] {
            XCTAssertEqual(TileMotion.open(role: role, reduceMotion: true), reduced)
        }
        XCTAssertEqual(TileMotion.focus(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.openFade(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.dim(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.panelRise(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.closePop(reduceMotion: true), reduced)
        for role in [TileGeometry.FanRole.front, .left, .right, .hidden] {
            XCTAssertEqual(TileMotion.close(role: role, reduceMotion: true), reduced)
        }
        XCTAssertEqual(TileMotion.closeFade(reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.frame(closing: true, reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.slide(closing: true, reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.fade(closing: true, reduceMotion: true), reduced)
        XCTAssertEqual(TileMotion.art(closing: true, reduceMotion: true), reduced)
    }

    @MainActor
    func testOpeningRunsTheCloseTimingsAndTheCloseIsOnTheGentlerCurve() {
        // Opening is the close played backwards: the same lengths and curve both ways.
        for closing in [false, true] {
            XCTAssertEqual(
                TileMotion.frame(closing: closing, reduceMotion: false),
                HomeDesign.Motion.closing(duration: 0.548, reduceMotion: false)
            )
            XCTAssertEqual(
                TileMotion.slide(closing: closing, reduceMotion: false),
                HomeDesign.Motion.closing(duration: 0.446, reduceMotion: false)
            )
            XCTAssertEqual(
                TileMotion.fade(closing: closing, reduceMotion: false),
                TileMotion.fade(closing: true, reduceMotion: false)
            )
            XCTAssertEqual(
                TileMotion.art(closing: closing, reduceMotion: false),
                TileMotion.art(closing: true, reduceMotion: false)
            )
        }

        let curve = HomeDesign.Motion.closeCurve
        let closeFrame = Animation.timingCurve(curve.c1x, curve.c1y, curve.c2x, curve.c2y, duration: 0.548)
        XCTAssertEqual(TileMotion.frame(closing: true, reduceMotion: false), closeFrame)
        // The poster the tile closes on lands with the tile, and its neighbors travel with it, with no delay.
        XCTAssertEqual(TileMotion.close(role: .front, reduceMotion: false), closeFrame)
        XCTAssertEqual(TileMotion.close(role: .left, reduceMotion: false), closeFrame.delay(0))
        XCTAssertEqual(TileMotion.close(role: .right, reduceMotion: false), closeFrame.delay(0))
        XCTAssertEqual(
            TileMotion.close(role: .hidden, reduceMotion: false),
            .timingCurve(curve.c1x, curve.c1y, curve.c2x, curve.c2y, duration: 0.383)
        )
        XCTAssertEqual(TileMotion.closeFade(reduceMotion: false), .easeOut(duration: 0.191))
        // The mounted posters are kept until the longest close is over.
        XCTAssertGreaterThan(TileMotion.unmountDelay, .seconds(0.548))
    }

    @MainActor
    func testTheTileAnimationsKeepTheMocksDurations() {
        XCTAssertEqual(TileMotion.frame(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.62))
        XCTAssertEqual(TileMotion.slide(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.5))
        XCTAssertEqual(TileMotion.focus(reduceMotion: false), .timingCurve(0.25, 1, 0.35, 1, duration: 0.45))
    }

    @MainActor
    func testOpeningIsTheClosePlayedBackwardsOnTheSameCurve() {
        let curve = HomeDesign.Motion.closeCurve
        func timing(_ duration: Double) -> Animation {
            .timingCurve(curve.c1x, curve.c1y, curve.c2x, curve.c2y, duration: duration)
        }
        // The front poster and its neighbors travel together over the close's whole length.
        XCTAssertEqual(TileMotion.open(role: .front, reduceMotion: false), timing(0.548).delay(0))
        XCTAssertEqual(TileMotion.open(role: .left, reduceMotion: false), timing(0.548))
        XCTAssertEqual(TileMotion.open(role: .right, reduceMotion: false), timing(0.548))
        // The rest start late, so they end with them, and appear only in the last 0.191 s.
        XCTAssertEqual(
            TileMotion.open(role: .hidden, reduceMotion: false),
            timing(0.383).delay(HomeDesign.Motion.openRestDelay)
        )
        XCTAssertEqual(HomeDesign.Motion.openRestDelay, 0.165, accuracy: 0.0001)
        XCTAssertEqual(
            TileMotion.openFade(reduceMotion: false),
            Animation.easeIn(duration: 0.191).delay(HomeDesign.Motion.openFadeDelay)
        )
        XCTAssertEqual(HomeDesign.Motion.openFadeDelay, 0.357, accuracy: 0.0001)
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
