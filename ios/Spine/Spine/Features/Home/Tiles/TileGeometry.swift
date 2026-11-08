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

    var dim: Double { 1 - brightness }
}

/// A tile's frame inside the section, in the section's own coordinates, and how it leaves when its sibling opens.
nonisolated struct TileFrame: Equatable {
    var left: CGFloat
    var width: CGFloat
    var height: CGFloat
    /// The slide-out of the tile that is not open: a shift from `left` and a scale about its own center.
    var slide: CGFloat
    var scale: CGFloat
    var opacity: Double
    var z: Double
    var isOpen: Bool
    var hitTestable: Bool
}

/// The Home tiles' numbers as pure functions of the content width, so they can be tested without a screen. The
/// mock's script (R3HomeLeft) is the spec: `fanPose` and `openPose` are its `fanPoster` and open poses turned into
/// transforms of one natural frame per poster.
nonisolated enum TileGeometry {
    /// The width the mock is drawn at (a 390 pt phone less two 16 pt margins), used until the section has measured.
    static let referenceContentWidth: CGFloat = 358

    static let posterSize = HomeDesign.Metric.carouselPoster
    /// From one poster's leading edge to the next: the poster and a 14 pt gap.
    static let step = HomeDesign.Metric.carouselStep
    static let spacing = HomeDesign.Metric.carouselStep - HomeDesign.Metric.carouselPoster.width
    static let top = HomeDesign.Metric.carouselTop

    /// Posters mounted while a tile is closed: the three the fan shows.
    static let fanMountedCount = 3
    /// The most posters a tile holds.
    static let maxPosters = 10

    // MARK: Section

    /// A closed tile's width: half the content less half the gap.
    static func tileSide(contentWidth: CGFloat) -> CGFloat {
        HomeDesign.Metric.tileSide(contentWidth: contentWidth)
    }

    /// A closed tile's height, whatever the width: the fan, the title and the count.
    static let closedHeight = HomeDesign.Metric.tileClosedHeight

    /// The section's height: one tile closed, 410 open.
    static func containerHeight(contentWidth: CGFloat, isOpen: Bool) -> CGFloat {
        isOpen ? HomeDesign.Metric.tileOpenHeight : closedHeight
    }

    /// Tile `tile` (0 left, 1 right) while tile `open` is open (nil when none is). The open tile fills the width;
    /// the other keeps its place, shrinks to 0.82, slides away (left by 80, right by 100) and fades out.
    static func frame(tile: Int, open: Int?, contentWidth: CGFloat) -> TileFrame {
        let side = tileSide(contentWidth: contentWidth)
        let closedLeft = CGFloat(tile) * (side + HomeDesign.Metric.tileGap)
        guard let open else {
            return TileFrame(
                left: closedLeft, width: side, height: closedHeight, slide: 0, scale: 1, opacity: 1, z: 1,
                isOpen: false, hitTestable: true
            )
        }
        if open == tile {
            return TileFrame(
                left: 0, width: contentWidth, height: HomeDesign.Metric.tileOpenHeight, slide: 0, scale: 1,
                opacity: 1, z: 3, isOpen: true, hitTestable: true
            )
        }
        return TileFrame(
            left: closedLeft, width: side, height: closedHeight, slide: tile == 0 ? -80 : 100, scale: 0.82, opacity: 0,
            z: 1, isOpen: false, hitTestable: false
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

    /// Which place of the fan a poster has on a closed tile.
    nonisolated enum FanRole: Equatable {
        case front
        case left
        case right
        /// Behind the front poster and invisible.
        case hidden

        /// The place in `HomeDesign.Metric.fan`.
        var slot: Int {
            switch self {
            case .front: 0
            case .left: 1
            case .right: 2
            case .hidden: 3
            }
        }

        /// Whether the poster folds in behind the front one as the page scrolls.
        var folds: Bool { self == .left || self == .right }
    }

    /// The posters a closed tile shows when it rests on `rest`: that title in front, the one before it in the left
    /// back slot and the one after it in the right. At the first title the back slots are the second and third; at the
    /// last, the two before it (the mirror image).
    static func fanSlots(rest: Int, count: Int) -> (front: Int, left: Int?, right: Int?) {
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
        if index == slots.left { return .left }
        if index == slots.right { return .right }
        return .hidden
    }

    /// Poster `index` in the fan on a closed tile resting on `rest`: the `HomeDesign.Metric.fan` frame as a scale, a
    /// rotation and an offset from the natural frame. A closed carousel sits scrolled to `rest`, so the natural frame
    /// is that far to the left.
    static func fanPose(index: Int, rest: Int, count: Int, contentWidth: CGFloat) -> PosterPose {
        let role = fanRole(index: index, rest: rest, count: count)
        let fan = HomeDesign.Metric.fan(index: role.slot, tileSide: tileSide(contentWidth: contentWidth))
        let natural = naturalCenter(index: index, contentWidth: contentWidth)
        return PosterPose(
            offsetX: fan.x + fan.width / 2 - natural.x + scrollX(forIndex: rest),
            offsetY: fan.y + fan.height / 2 - natural.y,
            scale: fan.width / posterSize.width,
            degrees: fan.degrees,
            brightness: fan.brightness,
            opacity: fan.opacity,
            z: fan.z
        )
    }

    /// The fan resting on the first title.
    static func fanPose(index: Int, contentWidth: CGFloat) -> PosterPose {
        fanPose(index: index, rest: 0, count: maxPosters, contentWidth: contentWidth)
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
        index: Int, focus: Int, isOpen: Bool, rest: Int = 0, count: Int = maxPosters, contentWidth: CGFloat
    ) -> PosterPose {
        isOpen
            ? openPose(index: index, focus: focus)
            : fanPose(index: index, rest: rest, count: count, contentWidth: contentWidth)
    }

    /// The radius that reads as 12 pt once the poster is drawn at `scale`: the mock keeps 12 while it resizes.
    static func cornerRadius(forScale scale: CGFloat) -> CGFloat {
        HomeDesign.Metric.posterRadius / max(scale, 0.01)
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

    /// A folded back poster is this much of its size, and this much darker.
    static let foldScale: CGFloat = 0.82
    static let foldDim: CGFloat = 0.45

    /// Where a back poster's center has to move, in points, to sit centered behind the front poster.
    static func foldSlide(role: FanRole, contentWidth: CGFloat) -> CGSize {
        let side = tileSide(contentWidth: contentWidth)
        let front = HomeDesign.Metric.fan(index: FanRole.front.slot, tileSide: side)
        let back = HomeDesign.Metric.fan(index: role.slot, tileSide: side)
        return CGSize(
            width: front.x + front.width / 2 - (back.x + back.width / 2),
            height: front.y + front.height / 2 - (back.y + back.height / 2)
        )
    }

    // MARK: Label

    static let labelLeading: CGFloat = 16
    static let titleClosedTop: CGFloat = 120
    static let titleOpenTop: CGFloat = 18
    /// 24 over 16: the title is drawn at its closed size and scaled up, anchored at its top-leading corner.
    static let titleOpenScale: CGFloat = 24.0 / 16.0
    static let titleLineHeight: CGFloat = 1.15
    static let subtitleGap: CGFloat = 2

    static func titleTop(isOpen: Bool) -> CGFloat {
        isOpen ? titleOpenTop : titleClosedTop
    }

    /// The subtitle sits under the title's line box, which grows with the title's size.
    static func subtitleTop(isOpen: Bool) -> CGFloat {
        titleTop(isOpen: isOpen) + (isOpen ? 24 : 16) * titleLineHeight + subtitleGap
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

    /// The baked art's fade, opening or closing: the same both ways.
    static func art(closing _: Bool, reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(HomeDesign.Motion.closeArtDuration)
    }

    /// How a poster moves as its tile opens, by the place it starts in: the close played backwards in time. The front
    /// poster zooms out of its fan spot and the two beside it fly from the back places, together; the posters that
    /// were hidden behind them start late and end with them.
    static func open(role: TileGeometry.FanRole, reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        switch role {
        case .front:
            return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeFrameDuration, reduceMotion: false)
                .delay(HomeDesign.Motion.openFrontDelay)
        case .left, .right:
            return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeNeighborDuration, reduceMotion: false)
        case .hidden:
            return HomeDesign.Motion.closing(duration: HomeDesign.Motion.closeRestDuration, reduceMotion: false)
                .delay(HomeDesign.Motion.openRestDelay)
        }
    }

    /// A poster's opacity as the tile opens: the posters from behind the fan appear in the last 0.3 s, when they are
    /// nearly in their places (the others do not change opacity).
    static func openFade(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return .easeIn(duration: HomeDesign.Motion.closeFadeDuration).delay(HomeDesign.Motion.openFadeDelay)
    }

    /// The poster the tile closes on, zooming out into the fan's front place: the tile's own close animation, so it
    /// shrinks with the tile and lands as the tile does. The close curve never overshoots the place.
    static func closeFront(reduceMotion: Bool) -> Animation {
        frame(closing: true, reduceMotion: reduceMotion)
    }

    /// The posters beside it flying into the back places: the same curve and length as the front one and no
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

    /// A poster's opacity while the tile closes: the rest are gone in 0.3 s, as they draw in.
    static func closeFade(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : .easeOut(duration: HomeDesign.Motion.closeFadeDuration)
    }

    /// How a poster moves as its tile closes, by the place it ends in.
    static func close(role: TileGeometry.FanRole, reduceMotion: Bool) -> Animation {
        switch role {
        case .front: closeFront(reduceMotion: reduceMotion)
        case .left, .right: closeNeighbor(reduceMotion: reduceMotion)
        case .hidden: closeRest(reduceMotion: reduceMotion)
        }
    }

    /// A poster growing or shrinking as the focus passes: 0.45 s.
    static func focus(reduceMotion: Bool) -> Animation {
        HomeDesign.Motion.transform(reduceMotion: reduceMotion)
    }

    /// A poster's dim: 0.35 s.
    static func dim(reduceMotion: Bool) -> Animation {
        reduceMotion ? HomeDesign.Motion.reduced : ease(0.35)
    }

    /// The detail panel rising in: 0.375 s, after 0.225 s.
    static func panelRise(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return HomeDesign.Motion.panelRise(reduceMotion: false).delay(0.225)
    }

    /// The Close button popping in, after 0.225 s.
    static func closePop(reduceMotion: Bool) -> Animation {
        guard !reduceMotion else { return HomeDesign.Motion.reduced }
        return HomeDesign.Motion.pop(reduceMotion: false).delay(0.225)
    }

    /// How long a closed tile keeps all its posters mounted so the flight home can finish: the longest close (0.645 s
    /// for every poster and the tile) and some slack.
    static let unmountDelay: Duration = .milliseconds(975)
    /// One frame or two, so posters mounted at the start of an open are on screen in the fan before they fly.
    static let mountLead: Duration = .milliseconds(24)
}
