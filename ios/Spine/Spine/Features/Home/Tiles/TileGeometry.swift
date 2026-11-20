import CoreGraphics
import Foundation
import SwiftUI

/// How one poster of a tile looks and where it sits, relative to its natural place in the carousel's scroll content.
/// The same poster view serves the fan on a closed tile and the carousel on an open one: only this pose changes.
nonisolated struct PosterPose: Equatable {
    /// Moves the poster's center from its natural center (scroll offset at rest).
    var offsetX: CGFloat
    var offsetY: CGFloat
    /// Of the poster's fixed 120 x 180 frame.
    var scale: CGFloat
    var degrees: Double
    /// 1 is untouched. The poster is dimmed by a black overlay of `dim`, not by a filter.
    var brightness: Double
    var opacity: Double
    var z: Double
    /// The corner radius it reads as on screen, whatever its scale.
    var radius: CGFloat = HomeDesign.Metric.posterRadius

    var dim: Double { 1 - brightness }
}

/// A tile's height inside the section, and how it leaves when its sibling opens. Both tiles are as wide as the section
/// and start at its left: the closed row is one strip across the page, split at the seam.
nonisolated struct TileFrame: Equatable {
    var height: CGFloat
    /// The slide-out of the tile that is not open: a shift away from the seam.
    var slide: CGFloat
    var opacity: Double
    var z: Double
    var isOpen: Bool
    var hitTestable: Bool
}

/// The Home tiles' numbers as pure functions of the content width, so they can be tested without a screen. The
/// mock's script is the spec: `fanPose` and `openPose` are its closed and open poses turned into transforms of one
/// natural frame per poster. Coordinates are the section's: the page's content width wide, the 16 pt margins left out,
/// so the screen's middle (where the seam is) is half the content width.
nonisolated enum TileGeometry {
    /// The width the mock is drawn at (a 390 pt phone less two 16 pt margins), used until the section has measured.
    static let referenceContentWidth: CGFloat = 358

    static let posterSize = HomeDesign.Metric.carouselPoster
    /// From one poster's leading edge to the next: the poster and a 14 pt gap.
    static let step = HomeDesign.Metric.carouselStep
    static let spacing = HomeDesign.Metric.carouselStep - HomeDesign.Metric.carouselPoster.width
    static let top = HomeDesign.Metric.carouselTop

    /// Posters mounted while a tile is closed: the three the strip shows.
    static let fanMountedCount = 3
    /// The most posters a tile holds.
    static let maxPosters = 10

    // MARK: Section

    /// The closed row's height: the labels, the strip of posters and the seam.
    static let closedHeight = HomeDesign.Metric.tileClosedHeight

    /// The section's height: the row closed, 410 open.
    static func containerHeight(isOpen: Bool) -> CGFloat {
        isOpen ? HomeDesign.Metric.tileOpenHeight : closedHeight
    }

    /// How far the tile that is not open slides away from the seam as it fades out.
    static let otherSlide: CGFloat = 130

    /// Which side of the seam tile `tile` (0 In progress, 1 Trending) sits on: -1 left, 1 right.
    static func side(tile: Int) -> CGFloat {
        tile == 0 ? -1 : 1
    }

    /// Tile `tile` (0 left, 1 right) while tile `open` is open (nil when none is). The open tile is 410 tall; the
    /// other keeps its place, slides away from the seam and fades out.
    static func frame(tile: Int, open: Int?) -> TileFrame {
        guard let open else {
            return TileFrame(height: closedHeight, slide: 0, opacity: 1, z: 1, isOpen: false, hitTestable: true)
        }
        if open == tile {
            return TileFrame(
                height: HomeDesign.Metric.tileOpenHeight, slide: 0, opacity: 1, z: 3, isOpen: true, hitTestable: true
            )
        }
        return TileFrame(
            height: closedHeight, slide: side(tile: tile) * otherSlide, opacity: 0, z: 1, isOpen: false,
            hitTestable: false
        )
    }

    // MARK: Carousel layout

    /// The scroll content's leading and trailing margin: the first poster starts here, so it is centered.
    static func carouselPadding(contentWidth: CGFloat) -> CGFloat {
        (contentWidth - posterSize.width) / 2
    }

    /// The center of poster `index` in the tile's coordinates when the carousel rests on the first poster.
    static func naturalCenter(index: Int, contentWidth: CGFloat) -> CGPoint {
        CGPoint(
            x: carouselPadding(contentWidth: contentWidth) + posterSize.width / 2 + CGFloat(index) * step,
            y: top + posterSize.height / 2
        )
    }

    // MARK: Fan

    /// Which place of the strip a poster has on a closed tile, counted from the seam outward.
    nonisolated enum FanRole: Equatable {
        /// Beside the seam.
        case front
        case second
        case third
        /// Waiting at the third place, invisible.
        case hidden

        /// The place in `HomeDesign.Metric.fan`.
        var slot: Int {
            switch self {
            case .front: 0
            case .second: 1
            case .third: 2
            case .hidden: 3
            }
        }

        /// Whether the poster folds in behind the front one as the page scrolls.
        var folds: Bool { self == .second || self == .third }
    }

    /// The posters a closed tile shows when it rests on `rest`: that title beside the seam, then the one before it,
    /// then the one after it. At the first title the other two are the second and third; at the last, the two before
    /// it (the mirror image).
    static func fanSlots(rest: Int, count: Int) -> (front: Int, second: Int?, third: Int?) {
        guard count > 0 else { return (0, nil, nil) }
        let front = min(max(rest, 0), count - 1)
        let back: [Int]
        if front == 0 {
            back = [1, 2].filter { $0 < count }
        } else if front == count - 1 {
            back = [count - 3, count - 2].filter { $0 >= 0 }
        } else {
            back = [front - 1, front + 1]
        }
        return (front, back.first, back.count > 1 ? back[1] : nil)
    }

    static func fanRole(index: Int, rest: Int, count: Int) -> FanRole {
        let slots = fanSlots(rest: rest, count: count)
        if index == slots.front { return .front }
        if index == slots.second { return .second }
        if index == slots.third { return .third }
        return .hidden
    }

    /// Poster `index` in the strip of tile `tile` resting on `rest`: the `HomeDesign.Metric.fan` frame as a scale, a
    /// rotation and an offset from the natural frame. A closed carousel sits scrolled to `rest`, so the natural frame
    /// is that far to the left.
    static func fanPose(tile: Int, index: Int, rest: Int, count: Int, contentWidth: CGFloat) -> PosterPose {
        let role = fanRole(index: index, rest: rest, count: count)
        let fan = HomeDesign.Metric.fan(index: role.slot, side: side(tile: tile), contentWidth: contentWidth)
        let natural = naturalCenter(index: index, contentWidth: contentWidth)
        return PosterPose(
            offsetX: fan.x + fan.width / 2 - natural.x + scrollX(forIndex: rest),
            offsetY: fan.y + fan.height / 2 - natural.y,
            scale: fan.width / posterSize.width,
            degrees: fan.degrees,
            brightness: fan.brightness,
            opacity: fan.opacity,
            z: fan.z,
            radius: HomeDesign.Metric.stripCorner
        )
    }

    /// The strip resting on the first title.
    static func fanPose(tile: Int, index: Int, contentWidth: CGFloat) -> PosterPose {
        fanPose(tile: tile, index: index, rest: 0, count: maxPosters, contentWidth: contentWidth)
    }

    /// The places from the seam outward that no title fills, which hold a hairline outline: the second and third of
    /// a tile with fewer than three titles (and, with none, the front place holds the offer's glyph).
    static func emptyRoles(count: Int) -> [Int] {
        (1 ... 2).filter { $0 >= count }
    }

    // MARK: Poses

    /// Poster `index` in the open carousel with poster `focus` in the middle: no offset, full size and light for the
    /// focused one, 0.84 and half dark for the rest, the focused one on top.
    static func openPose(index: Int, focus: Int) -> PosterPose {
        let focused = index == focus
        return PosterPose(
            offsetX: 0,
            offsetY: 0,
            scale: focused ? 1 : HomeDesign.Metric.carouselRestScale,
            degrees: 0,
            brightness: focused ? 1 : HomeDesign.Metric.carouselRestBrightness,
            opacity: 1,
            z: 20 - Double(abs(index - focus))
        )
    }

    static func pose(
        tile: Int, index: Int, focus: Int, isOpen: Bool, rest: Int = 0, count: Int = maxPosters, contentWidth: CGFloat
    ) -> PosterPose {
        isOpen
            ? openPose(index: index, focus: focus)
            : fanPose(tile: tile, index: index, rest: rest, count: count, contentWidth: contentWidth)
    }

    /// The radius that reads as `radius` pt once the poster is drawn at `scale`: the mock keeps it while it resizes.
    static func cornerRadius(_ radius: CGFloat, forScale scale: CGFloat) -> CGFloat {
        radius / max(scale, 0.01)
    }

    /// Which poster the carousel is on for a horizontal scroll distance from rest (0 on the first poster, 134 on
    /// the second): the nearest one, kept inside the list.
    static func focusIndex(scrollX: CGFloat, count: Int) -> Int {
        guard count > 0 else { return 0 }
        let nearest = Int((scrollX / step).rounded())
        return min(max(nearest, 0), count - 1)
    }

    /// The same distance in poster units and not rounded: 1.5 is halfway from the second poster to the third. Kept
    /// inside the list, so a rubber-banding scroll reads as the first or last poster.
    static func scrollPosition(scrollX: CGFloat, count: Int) -> Double {
        guard count > 0 else { return 0 }
        return min(max(Double(scrollX / step), 0), Double(count - 1))
    }

    /// The scroll distance that puts poster `index` in the middle.
    static func scrollX(forIndex index: Int) -> CGFloat {
        CGFloat(index) * step
    }

    // MARK: Motion

    /// The opacity a poster is drawn with. A poster that is not part of the fan fades in as the tile opens, so one
    /// that is drawn for the first time with its tile already open (mounted too late to have a closed pose to leave)
    /// starts clear and not at its carousel place at full strength.
    static func cellOpacity(pose: PosterPose, role: FanRole, isOpen: Bool, entered: Bool) -> Double {
        isOpen && role == .hidden && !entered ? 0 : pose.opacity
    }

    // MARK: Mounting

    /// A closed tile mounts only the fan's three posters; a tile that is opening, open or closing mounts all.
    static func isMounted(index: Int, rest: Int, count: Int, allMounted: Bool) -> Bool {
        allMounted || fanRole(index: index, rest: rest, count: count) != .hidden
    }

    /// The posters whose images to warm when a tile starts to open: the three nearest to the one it rests on that the
    /// fan does not show, in list order.
    static func openPrefetchIndexes(rest: Int, count: Int) -> [Int] {
        let hidden = (0 ..< max(count, 0)).filter { fanRole(index: $0, rest: rest, count: count) == .hidden }
        let nearest = hidden.sorted { (abs($0 - rest), $0) < (abs($1 - rest), $1) }
        return Array(nearest.prefix(3)).sorted()
    }

    // MARK: Fold

    /// How far the two back posters of a closed tile have folded behind the front one: 0 with the page at rest, 1
    /// once the tile has left the top of the screen, one to one with the scroll in between.
    static func foldProgress(scroll: CGFloat, tilesBottom: CGFloat?) -> CGFloat {
        let end = tilesBottom ?? GlowSpec.estimatedTilesBottom
        guard end > 0 else { return 0 }
        return min(max(scroll / end, 0), 1)
    }

    /// How far the open carousel's posters have folded behind the focused one: 0 with the page at rest, 1 once the
    /// poster row's bottom edge (`rowBottom`, on screen with the page at rest) has reached the top of the screen.
    static func openFoldProgress(scroll: CGFloat, rowBottom: CGFloat) -> CGFloat {
        foldProgress(scroll: scroll, tilesBottom: rowBottom)
    }

    /// How much of the fold a poster takes, by how far it sits from the focused one in poster steps (fractional while
    /// a swipe is under way): none for the focused poster, all of it from one step out.
    static func openFoldWeight(slotsFromFocus: CGFloat) -> CGFloat {
        min(abs(slotsFromFocus), 1)
    }

    /// The horizontal move, in points, that takes a poster `slotsFromFocus` steps from the focused one toward it:
    /// back by its whole distance at progress 1. Zero for the focused poster, and for a side with no posters.
    static func openFoldOffset(slotsFromFocus: CGFloat, progress: CGFloat) -> CGFloat {
        -slotsFromFocus * step * progress
    }

    /// How much of a poster still shows as the open carousel folds: the focused poster and its two neighbors stay, as
    /// the closed fan has one poster and two behind it; one more step out it fades with the fold, so the posters that
    /// sat off screen don't fly in and crowd behind the focused one.
    static func openFoldOpacity(slotsFromFocus: CGFloat, progress: CGFloat) -> Double {
        let beyondNeighbor = min(max(abs(slotsFromFocus) - 1, 0), 1)
        return Double(1 - beyondNeighbor * progress)
    }

    /// A folded back poster is this much of its size, and this much darker.
    static let foldScale: CGFloat = 0.82
    static let foldDim: CGFloat = 0.45

    /// Where a back poster's center has to move, in points, to sit centered behind the front poster.
    static func foldSlide(tile: Int, role: FanRole, contentWidth: CGFloat) -> CGSize {
        let side = side(tile: tile)
        let front = HomeDesign.Metric.fan(index: FanRole.front.slot, side: side, contentWidth: contentWidth)
        let back = HomeDesign.Metric.fan(index: role.slot, side: side, contentWidth: contentWidth)
        return CGSize(
            width: front.x + front.width / 2 - (back.x + back.width / 2),
            height: front.y + front.height / 2 - (back.y + back.height / 2)
        )
    }

    // MARK: Label

    /// The open label's left, and the width of the box each label is laid out in.
    static let labelLeading: CGFloat = 16
    static let labelWidth: CGFloat = 150
    /// How far the closed labels stand from the middle of the page: In progress ends 13.5 pt before it and Trending
    /// starts 14 pt after it.
    static let labelInnerLeft: CGFloat = 13.5
    static let labelInnerRight: CGFloat = 14
    static let titleClosedTop: CGFloat = 156
    static let titleOpenTop: CGFloat = 18
    /// 24 over 17: the title is drawn at its closed size and scaled up, anchored at its top-leading corner.
    static let titleOpenScale: CGFloat = 24.0 / 17.0
    static let titleLineHeight: CGFloat = 1.15

    static func titleTop(isOpen: Bool) -> CGFloat {
        isOpen ? titleOpenTop : titleClosedTop
    }

    /// The subtitle sits under the title's line box, which grows with the title's size.
    static func subtitleTop(isOpen: Bool) -> CGFloat {
        titleTop(isOpen: isOpen) + (isOpen ? 24 : 17) * titleLineHeight + (isOpen ? 2 : 1)
    }

    /// The left of a label's box: open, at the page's margin; closed, In progress's ends before the seam (its text is
    /// right-aligned in the box) and Trending's starts after it.
    static func labelLeft(tile: Int, isOpen: Bool, contentWidth: CGFloat) -> CGFloat {
        if isOpen { return labelLeading }
        return tile == 0
            ? contentWidth / 2 - labelInnerLeft - labelWidth
            : contentWidth / 2 + labelInnerRight
    }

    // MARK: Seam

    /// The seam is one line down the page's middle. Closed it runs from 8 pt below the row's top to its bottom, 188 pt; while a tile is open
    /// it glides to the edge the tile's posters came from (In progress's to the right, Trending's to the left) and
    /// runs from 24 to 384 pt, softer.
    static let seamRestTop: CGFloat = 8
    static let seamRestHeight: CGFloat = 188
    static let seamOpenTop: CGFloat = 24
    static let seamOpenHeight: CGFloat = 360
    static let seamOpenOpacity: Double = 0.55
    /// How far the open seam is from the screen's edge.
    static let seamOpenInset: CGFloat = 8
    static let seamWidth: CGFloat = 1

    /// The seam's left edge. Open on In progress it is 9 pt from the screen's right edge, on Trending 8 pt from the
    /// left (the screen is the content plus a margin on each side).
    static func seamLeft(open: Int?, contentWidth: CGFloat) -> CGFloat {
        let margin = HomeDesign.Metric.pageMargin
        switch open {
        case nil: return contentWidth / 2 - seamWidth / 2
        case 0: return contentWidth + 2 * margin - (seamOpenInset + seamWidth) - margin
        default: return seamOpenInset - margin
        }
    }

    // MARK: Panel

    static let panelTop: CGFloat = 268
    static let panelInset: CGFloat = 16
    /// Height of the block under the panel's title row, whether it holds ticks, a bar or the friends line: the
    /// buttons stay put as you swipe between titles.
    static let panelMeterBlock: CGFloat = 34
    static let buttonHeight: CGFloat = 44
    static let closeSize: CGFloat = 44
    static let closeInset: CGFloat = 12

    /// A long series' window sits as on the tile panel (15 episodes behind the current one), not as on a feed card.
    static func panelMeter(_ model: ProgressMeterModel?) -> ProgressMeterModel? {
        guard let model, case .window = model.kind else { return model }
        return ProgressMeterModel.make(
            unit: model.unit,
            from: model.from,
            to: model.to,
            total: model.total,
            windowFirst: EpisodeWindow.panelFirst(value: model.to, total: model.total)
        )
    }

    /// How much of tick `index` of `count` shows when the left of a window fades in: the mock's mask runs from
    /// clear at the left edge to solid at 24 percent of the width, sampled at each tick's center.
    static func windowFade(index: Int, count: Int) -> Double {
        guard count > 0 else { return 1 }
        let center = (Double(index) + 0.5) / Double(count)
        return min(max(center / Double(ProgressMeterMetrics.fadeFraction), 0), 1)
    }
}

/// The colors and strokes of a tile that the mock draws and `HomeDesign` has no token for. Everything else a tile
/// uses comes from `HomeDesign`.
nonisolated enum TileStyle {
    /// The border of Up next's tile (the ring and the top line, `TileOutline`) at this share of the mock's: half, so
    /// the edges are quiet.
    static let outlineStrength = 0.5
    /// rgba(18, 18, 18, 0.3) under the baked art.
    static let veil = Color(red: 18.0 / 255, green: 18.0 / 255, blue: 18.0 / 255).opacity(0.3)
    /// What shows while a tile's art loads.
    static let artBase = Color(white: 0.12)
    static let stroke = Color.white.opacity(0.26)
    static let strokeWidth: CGFloat = 0.5
    static let highlight = Color.white.opacity(0.20)
    static let highlightWidth: CGFloat = 1
    /// The poster's hairline: 0 0 0 0.5px rgba(255, 255, 255, 0.16).
    static let posterHairline = Color.white.opacity(0.16)
    static let subtitle = Color.white.opacity(0.84)
    /// The closed row's subtitles, under the titles' glow.
    static let subtitleRest = Color.white.opacity(0.72)
    /// The hairline that holds an empty place in the closed row.
    static let emptySlot = Color.white.opacity(0.15)
    static let panelSecondary = Color.white.opacity(0.88)

    // The tile panel's meter: the mock's own greys, not the card's.
    static let meterBefore = Color.white.opacity(0.55)
    static let meterRest = Color.white.opacity(0.16)
    static let tickHeight: CGFloat = 10
    static let tickRadius: CGFloat = 3
    static let tickGap: CGFloat = 3

    static let primaryFill = Color.white
    static let primaryInk = Color.black
    static let secondaryFill = Color.white.opacity(0.16)
    static let secondaryInk = Color.white
    static let buttonFont = Font.system(size: 15, weight: .bold)

    static let panelAside = Font.system(size: 13, weight: .bold)

    static func meterPalette(accent: Color) -> MeterPalette {
        MeterPalette(before: meterBefore, rest: meterRest, accent: accent)
    }
}

/// Every animation a tile uses, with Reduce Motion turning each into the short fade.
enum TileMotion {
    /// The mock's plain `ease`.
    private static func ease(_ duration: Double) -> Animation {
        .timingCurve(0.25, 0.1, 0.25, 1.0, duration: duration)
    }

    /// The section's height, the tile's frame and its art: 0.62 s.
    static func frame(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.tileHeight(reduceMotion: reduceMotion)
    }

    /// The tile's frame opening or closing: the same length and curve both ways, so the open is the close played
    /// backwards.
    static func frame(closing _: Bool, reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeFrameDuration, reduceMotion: reduceMotion)
    }

    /// The other tile's slide and shrink: 0.5 s.
    static func slide(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.soft(duration: 0.5, reduceMotion: reduceMotion)
    }

    /// The other tile's slide and shrink, opening or closing: at the pace of the tile, the same both ways.
    static func slide(closing _: Bool, reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeSlideDuration, reduceMotion: reduceMotion)
    }

    /// The other tile's fade: 0.32 s.
    static func fade(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(0.32)
    }

    /// The other tile's fade, opening or closing: the same both ways.
    static func fade(closing _: Bool, reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(HomeDesign.Motion.closeTileFadeDuration)
    }

    /// The baked art's fade: 0.5 s.
    static func art(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(0.5)
    }

    /// How a poster moves as its tile opens, by the place it starts in: the close played backwards in time. The front
    /// poster zooms out of its fan spot and the two beside it fly from their places in the row, together; the posters
    /// that were hidden behind them start late and end with them.
    static func open(role: TileGeometry.FanRole, reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        switch role {
        case .front:
            return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeFrameDuration, reduceMotion: false)
                .delay(HomeDesign.Motion.openFrontDelay)
        case .second, .third:
            return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeNeighborDuration, reduceMotion: false)
        case .hidden:
            return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeRestDuration, reduceMotion: false)
                .delay(HomeDesign.Motion.openRestDelay)
        }
    }

    /// A poster's opacity as the tile opens: the posters from behind the fan appear once they have landed, off the
    /// screen's edges (the others do not change opacity). Nothing clips the row, so a fade that started in flight showed
    /// one of them crossing the right edge as In progress opened on a later title.
    static func openFade(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return .easeIn(duration: HomeDesign.Motion.closeFadeDuration).delay(HomeDesign.Motion.openFadeDelay)
    }

    /// The poster the tile closes on, zooming out into the fan's front place: the tile's own close animation, so it
    /// shrinks with the tile and lands as the tile does. The close curve never overshoots the place.
    static func closeFront(reduceMotion: Bool) -> Animation {
        frame(closing: true, reduceMotion: reduceMotion)
    }

    /// The posters beside it flying into their places in the row: the same curve and length as the front one and no
    /// stagger, so the three travel in step and each path can be followed.
    static func closeNeighbor(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeNeighborDuration, reduceMotion: false)
            .delay(HomeDesign.Motion.closeNeighborDelay)
    }

    /// The rest drawing in towards the fan while they fade.
    static func closeRest(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeRestDuration, reduceMotion: reduceMotion)
    }

    /// A poster's opacity while the tile closes: the rest are gone at once. They wait off screen in the carousel and
    /// the row is no longer clipped, so a slower fade would show them crossing the other half as dark shapes.
    static func closeFade(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : .easeOut(duration: 0.04)
    }

    /// The count and "Swipe to browse" trading places under the title: the line that leaves is gone in the first half,
    /// the one that arrives fades in the second, so the two are never printed on top of each other.
    static func labelSwap(appearing: Bool, reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return appearing ? .easeOut(duration: 0.06).delay(0.06) : .easeOut(duration: 0.06)
    }

    /// The label of the tile that is not open: it is gone before the open tile's label sets out across the row. It
    /// comes back at once, unanimated, as the tile closes: its tile is still invisible then, so the tile's own fade
    /// brings it in with its posters, already in its place, as Trending's label comes back.
    static func labelYield(yielding: Bool, reduceMotion: Bool) -> Animation? {
        guard yielding else { return nil }
        return reduceMotion ? HomeDesign.Motion.reduced : .easeOut(duration: 0.12)
    }

    /// How a poster moves as its tile closes, by the place it ends in.
    static func close(role: TileGeometry.FanRole, reduceMotion: Bool) -> Animation {
        switch role {
        case .front: closeFront(reduceMotion: reduceMotion)
        case .second, .third: closeNeighbor(reduceMotion: reduceMotion)
        case .hidden: closeRest(reduceMotion: reduceMotion)
        }
    }

    /// A tile's first posters and art fading in together, and Up next's place opening or closing: 0.4 s.
    static func reveal(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(0.4)
    }

    /// A poster growing or shrinking as the focus passes: 0.45 s.
    static func focus(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.transform(reduceMotion: reduceMotion)
    }

    /// A poster's dim: 0.35 s.
    static func dim(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(0.35)
    }

    /// The detail panel rising in: 0.319 s, after 0.191 s.
    static func panelRise(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return HomeDesign.Motion.panelRise(reduceMotion: false).delay(0.191)
    }

    /// The Close button popping in, after 0.191 s.
    static func closePop(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return HomeDesign.Motion.pop(reduceMotion: false).delay(0.191)
    }

    /// How long a closed tile keeps all its posters mounted so the flight home can finish: the longest close (0.548 s
    /// for every poster and the tile) and some slack.
    static let unmountDelay: Duration = .milliseconds(830)
    /// One frame or two, so posters mounted at the start of an open are on screen in the fan before they fly.
    static let mountLead: Duration = .milliseconds(24)
}
