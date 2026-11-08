import CoreGraphics
import Foundation
import Observation

/// What Home's glow is showing while a tile's carousel is open: the poster it is on (`focused`) across the whole glow.
/// At rest the glow is behind Home's picture, on the first In progress title's art, ready to be shown. Also how far
/// the page has scrolled. The glow itself does not read `scrollOffset` (it moves with the content); only the status
/// bar fade and the back posters' fold do, so scrolling redraws their small bodies and never the feed.
@MainActor
@Observable
final class HomeGlowModel {
    /// The first In progress item, else the newest feed item: what the glow rests on.
    var idle: MediaSummary?
    /// The poster in front of the In progress tile's fan, set by the tiles: it takes over from `idle` while there is one.
    var restLeft: MediaSummary?
    /// The poster a tile's carousel is on. Set by the tiles; nil puts the glow back to rest.
    var focused: MediaSummary?
    /// Points the page has scrolled down; negative while it is pulled past the top.
    var scrollOffset: CGFloat = 0
    /// Whether today's picture covers the top of the page at rest. Without one the page is plain black there and the
    /// glow shows only while a tile is open.
    var hasRestPicture = false
    /// The screen's width, measured: the picture and the header's place scale with it.
    var screenWidth: CGFloat = HomeTopArtSpec.referenceWidth

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

    /// How tall the glow is: `GlowSpec.restHeight` at rest; while a tile is open, the full
    /// `HomeDesign.Metric.glowHeight`, so the open tile's posters sit in it.
    var height: CGFloat { GlowSpec.height(isOpen: focused != nil) }

    /// Whether the glow is seen: while a tile is open, and at rest only when a picture covers it, so it is already
    /// there as the picture fades out.
    var showsGlow: Bool { focused != nil || hasRestPicture }

    /// What the glow draws: the focused poster, else the poster it rests on.
    var art: GlowArt? { activeURL.map(GlowArt.init) }
}

/// One state of the glow: an image across it.
nonisolated struct GlowArt: Hashable {
    let url: URL
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

    /// Where the status bar's inset is until it has been measured.
    static let estimatedTopInset: CGFloat = 62

    /// How far to move the glow from its layout place, which is the top of the page's content (`layoutTop`, on
    /// screen). Moved by the inset and the design's offset it sits at the screen's top at rest and rides up one to
    /// one with the content, because the content carries it. Pulled past the top (`layoutTop` beyond `topInset`)
    /// it is lifted by the pull and stays at the top, so no gap opens above it. There is no opacity tied to scroll:
    /// nothing to fade, so nothing to flatten. Off screen once the page has scrolled the glow's height.
    /// Pure and nonisolated: the effect that calls it runs off the main actor, so the design's offset
    /// (`BackdropLayout.topOffset`) is passed in.
    nonisolated static func offset(layoutTop: CGFloat, topInset: CGFloat, topOffset: CGFloat) -> CGFloat {
        min(topOffset - topInset, topOffset - layoutTop)
    }

    /// Where the closed tiles end on a 393-402 pt phone, until they have been measured.
    static let estimatedTilesBottom: CGFloat = 322
    /// How tall the glow is at rest, behind the picture: down to where the closed tiles start.
    static let restHeight: CGFloat = 318

    /// The glow's height: while a tile is open, `HomeDesign.Metric.glowHeight`; at rest, `restHeight`.
    static func height(isOpen: Bool) -> CGFloat {
        isOpen ? HomeDesign.Metric.glowHeight : restHeight
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
