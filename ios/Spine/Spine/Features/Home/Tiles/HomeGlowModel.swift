import CoreGraphics
import Foundation
import Observation

/// What Home's glow is showing: at rest, In progress's color on the left and Trending's on the right, meeting in the
/// middle; while a tile's carousel is open, the poster it is on (`focused`) across the whole glow. Also how far the
/// page has scrolled. The glow view is the only reader of `scrollOffset`, so scrolling redraws the glow's own small
/// body and never the feed.
@MainActor
@Observable
final class HomeGlowModel {
    /// The first In progress item, else the newest feed item: the left of the glow at rest.
    var idle: MediaSummary?
    /// The first Trending title: the right of the glow at rest.
    var trending: MediaSummary?
    /// The poster in front of each closed tile's fan, set by the tiles: they take over the glow's two sides at rest from
    /// `idle` and `trending`, which are what the page falls back on while a tile has none.
    var restLeft: MediaSummary?
    var restRight: MediaSummary?
    /// The poster a tile's carousel is on. Set by the tiles; nil puts the glow back to rest.
    var focused: MediaSummary?
    /// Points the page has scrolled down; negative while it is pulled past the top.
    var scrollOffset: CGFloat = 0

    var active: MediaSummary? { focused ?? restLeft ?? idle }

    /// The image the glow shows for `active`: its backdrop, else its poster.
    var activeURL: URL? { HomeArtworkAtmosphereModel.url(for: active) }

    /// The page's coordinate space: the scrolling content, so a place measured in it does not move as the page scrolls.
    static let contentSpace = "home.content"
    /// The scroll view's top inset (the status bar's height), measured: the content starts this far down the screen.
    var contentTopInset: CGFloat?
    /// Where the tiles start in the page's content (`contentSpace`), measured.
    var tilesTop: CGFloat?

    /// The closed tiles' bottom edge on screen with the page at rest, once both are measured.
    var tilesBottom: CGFloat? {
        guard let contentTopInset, let tilesTop else { return nil }
        return contentTopInset + tilesTop + TileGeometry.closedHeight
    }

    /// How tall the glow is: at rest it ends just above the closed tiles' bottom edge; while a tile is open, the full
    /// `HomeDesign.Metric.glowHeight`, so the open tile's posters sit in it.
    var height: CGFloat { GlowSpec.height(tilesBottom: tilesBottom, isOpen: focused != nil) }

    /// What the glow draws: the focused poster alone; at rest `idle` and `trending` side by side, or whichever of
    /// them there is when there is one, or both are the same image.
    var art: GlowArt? {
        let rightSide = restRight ?? trending
        guard let left = activeURL else {
            return HomeArtworkAtmosphereModel.url(for: rightSide).map { GlowArt(left: $0, right: nil) }
        }
        guard focused == nil, let right = HomeArtworkAtmosphereModel.url(for: rightSide), right != left else {
            return GlowArt(left: left, right: nil)
        }
        return GlowArt(left: left, right: right)
    }
}

/// One state of the glow: an image across it, or two images that meet in the middle.
nonisolated struct GlowArt: Hashable {
    let left: URL
    /// Drawn over the right side, faded out toward the middle; nil when `left` fills the glow.
    let right: URL?
}

/// The glow's numbers from the design: a 390 x 440 box holding each image at 590 x 720, offset -100 / -170, at
/// opacity 0.95, under a black gradient that meets the black page at the bottom.
enum GlowSpec {
    static let imageOpacity: Double = 0.95
    /// 590 / 390: the image is this many times as wide as the box and centered on it (-100 to 490 on 390).
    static let imageWidthRatio: CGFloat = 1.513
    static let imageHeight: CGFloat = 720
    /// Where the image's top sits relative to the box's top.
    static let imageTop: CGFloat = -170

    /// Black over the image: (opacity, location). The last stop is solid so the bottom meets the page.
    static let gradientStops: [(opacity: Double, location: CGFloat)] = [
        (0.22, 0),
        (0.08, 0.35),
        (0.70, 0.78),
        (1.0, 1.0),
    ]

    /// The whole box moves one to one with the content and is off screen once the page has scrolled the glow's
    /// height. There is no opacity tied to scroll: nothing to fade, so nothing to flatten.
    static func offset(scroll: CGFloat) -> CGFloat {
        BackdropLayout.topOffset - max(0, scroll)
    }

    /// At rest the glow is fully black this far above the closed tiles' bottom edge.
    static let restGap: CGFloat = 6
    /// Where the closed tiles end on a 393-402 pt phone, until they have been measured.
    static let estimatedTilesBottom: CGFloat = 322
    /// The shortest the glow gets, whatever is measured.
    static let minimumHeight: CGFloat = 200

    /// The glow's height: while a tile is open, `HomeDesign.Metric.glowHeight`; at rest, from its top (at
    /// `BackdropLayout.topOffset`) to `restGap` above the tiles' bottom edge.
    static func height(tilesBottom: CGFloat?, isOpen: Bool) -> CGFloat {
        let full = HomeDesign.Metric.glowHeight
        guard !isOpen else { return full }
        let bottom = tilesBottom ?? estimatedTilesBottom
        return min(full, max(minimumHeight, (bottom - BackdropLayout.topOffset - restGap).rounded()))
    }
}

/// The glow's two image layers as a pure state machine, so a quick swipe through a carousel never flashes.
///
/// A new image goes on the `upper` layer and only that layer fades in; the `lower` layer is never touched, because
/// fading both makes a dip in brightness. When the fade ends, the image is the `lower` layer and the upper clears.
/// A `show` during a fade is queued, the latest one wins, and it starts when the running fade ends.
nonisolated struct GlowLayers<Art: Hashable>: Equatable {
    /// One layer on screen. The view is keyed by `token`, not the art: art that comes back as a new upper layer
    /// (swiping away and back during a fade) gets a new view, so its fade-in runs and reports its end. Keyed by art,
    /// the old lower layer's view was reused, its fade never ran, `upper` never cleared, and every later swipe only
    /// queued: the glow stuck.
    nonisolated struct Layer: Hashable {
        let art: Art
        let token: Int
    }

    private(set) var lower: Art?
    private(set) var upper: Art?
    private var queued: Art?
    private var lowerToken = 0
    private(set) var upperToken = 0
    private var lastToken = 0

    /// The image the layers will end on.
    var target: Art? { queued ?? upper ?? lower }
    var isFading: Bool { upper != nil }
    /// The layers on screen, bottom first. Never more than two.
    var alive: [Art] { [lower, upper].compactMap { $0 } }
    /// The same layers with the identity their views are keyed by. An upper layer keeps its token when its fade
    /// ends and it becomes the lower one, so the hand-over redraws nothing.
    var aliveLayers: [Layer] {
        var layers: [Layer] = []
        if let lower { layers.append(Layer(art: lower, token: lowerToken)) }
        if let upper { layers.append(Layer(art: upper, token: upperToken)) }
        return layers
    }

    /// Shows `url`, or clears with nil. Returns true when a fade just started on the upper layer.
    @discardableResult
    mutating func show(_ url: Art?) -> Bool {
        guard url != target else { return false }
        guard let url else {
            lower = nil
            upper = nil
            queued = nil
            return false
        }
        if isFading {
            // The fade already running is on its way to `upper`, so asking for it again drops what was queued.
            queued = (url == upper) ? nil : url
            return false
        }
        startUpper(url)
        return true
    }

    /// The upper layer's fade ended. Returns true when a queued image started its own fade.
    @discardableResult
    mutating func fadeCompleted(of url: Art) -> Bool {
        guard upper == url else { return false }
        lower = url
        lowerToken = upperToken
        upper = nil
        guard let next = queued else { return false }
        queued = nil
        guard next != lower else { return false }
        startUpper(next)
        return true
    }

    private mutating func startUpper(_ url: Art) {
        lastToken += 1
        upper = url
        upperToken = lastToken
    }
}
