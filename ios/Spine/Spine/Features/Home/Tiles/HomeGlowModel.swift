import CoreGraphics
import Foundation
import Observation

/// What Home's glow is showing: the artwork you are on now (`idle`) until a tile focuses another poster (`focused`),
/// and how far the page has scrolled. The glow view is the only reader of `scrollOffset`, so scrolling redraws the
/// glow's own small body and never the feed.
@MainActor
@Observable
final class HomeGlowModel {
    /// The first In progress item, else the newest feed item.
    var idle: MediaSummary?
    /// The poster a tile's carousel is on. Set by the tiles; nil puts the glow back on `idle`.
    var focused: MediaSummary?
    /// Points the page has scrolled down; negative while it is pulled past the top.
    var scrollOffset: CGFloat = 0

    var active: MediaSummary? { focused ?? idle }

    /// The image the glow shows for `active`: its backdrop, else its poster.
    var activeURL: URL? { HomeArtworkAtmosphereModel.url(for: active) }
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
}

/// The glow's two image layers as a pure state machine, so a quick swipe through a carousel never flashes.
///
/// A new image goes on the `upper` layer and only that layer fades in; the `lower` layer is never touched, because
/// fading both makes a dip in brightness. When the fade ends, the image is the `lower` layer and the upper clears.
/// A `show` during a fade is queued, the latest one wins, and it starts when the running fade ends.
nonisolated struct GlowLayers: Equatable {
    private(set) var lower: URL?
    private(set) var upper: URL?
    private var queued: URL?

    /// The image the layers will end on.
    var target: URL? { queued ?? upper ?? lower }
    var isFading: Bool { upper != nil }
    /// The layers on screen, bottom first. Never more than two.
    var alive: [URL] { [lower, upper].compactMap { $0 } }

    /// Shows `url`, or clears with nil. Returns true when a fade just started on the upper layer.
    @discardableResult
    mutating func show(_ url: URL?) -> Bool {
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
        upper = url
        return true
    }

    /// The upper layer's fade ended. Returns true when a queued image started its own fade.
    @discardableResult
    mutating func fadeCompleted(of url: URL) -> Bool {
        guard upper == url else { return false }
        lower = url
        upper = nil
        guard let next = queued else { return false }
        queued = nil
        guard next != lower else { return false }
        upper = next
        return true
    }
}
