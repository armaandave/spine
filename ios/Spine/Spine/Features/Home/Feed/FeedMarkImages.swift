import SwiftUI
import UIKit

/// The headline's heart, rewatch and star symbols with the soft glow of a lit tick in the In progress meter, drawn once
/// each into a bitmap and reused by every card that shows one, so a card that scrolls in draws an image and no blur of
/// its own. There are eight: the heart, the rewatch mark, the star and the half star, each on dark and on light art.
@MainActor
enum FeedMarkImages {
    /// One layer of the glow: the mark's own color at `opacity`, blurred by `radius`.
    struct GlowLayer: Equatable {
        let radius: CGFloat
        let opacity: Double
    }

    /// The layers as they were before the glow was turned down by `HomeDesign.FeedDepth.markGlowScale`.
    static func fullGlowLayers(for ink: ArtInk) -> [GlowLayer] {
        ink == .dark
            ? [GlowLayer(radius: 2.5, opacity: 0.65), GlowLayer(radius: 6, opacity: 0.40)]
            : [GlowLayer(radius: 1.5, opacity: 0.40), GlowLayer(radius: 3.5, opacity: 0.28)]
    }

    /// A tight layer under a wide one, as the meter's glow stacks four inflated shapes into a soft falloff. Dark art
    /// takes the stronger glow, as the meter's lit ticks do; on light art it is a quiet halo. Every mark shares them.
    /// The strength is turned down by `markGlowScale` and the reach shrinks with the mark (`markScale`), so the glow
    /// looks the same around the smaller symbol.
    /// `size` is the symbol's point size (the card's 12 pt unless a page draws it bigger): the reach grows with it.
    static func glowLayers(for ink: ArtInk, size: CGFloat = HomeDesign.FeedDepth.markSymbolSize) -> [GlowLayer] {
        let scale = HomeDesign.FeedDepth.markGlowScale
        let reach = CGFloat(HomeDesign.FeedDepth.markScale) * growth(for: size)
        return fullGlowLayers(for: ink).map {
            GlowLayer(radius: $0.radius * CGFloat(scale) * reach, opacity: $0.opacity * scale)
        }
    }

    /// How far the glow is allowed to reach past the symbol on every side, so it is never cut: 9 pt on dark art and
    /// 6 pt on light art at full size, shrunk with the mark.
    static func padding(for ink: ArtInk, size: CGFloat = HomeDesign.FeedDepth.markSymbolSize) -> CGFloat {
        (ink == .dark ? 9 : 6) * CGFloat(HomeDesign.FeedDepth.markScale) * growth(for: size)
    }

    /// How much bigger than the card's mark a symbol of `size` points is; 1 for the card's own.
    static func growth(for size: CGFloat) -> CGFloat {
        size / HomeDesign.FeedDepth.markSymbolSize
    }

    static let renderScale: CGFloat = 3

    /// The marks a card shows.
    static let warmSet: [FeedMark] = [.heart, .rewatch, .star, .halfStar]

    private static var cache: [String: UIImage] = [:]

    /// How many images are kept.
    static var cachedCount: Int { cache.count }

    /// Forgets the images (tests).
    static func reset() {
        cache.removeAll()
    }

    /// The image of a mark, its glow included, the symbol plus `padding(for: ink)` on every side at 3x; nil if rendering
    /// failed.
    /// A post page draws the same marks bigger with `size`; the glow and the room around the symbol grow with it. The
    /// card's size keeps its original key, so `warm()` still fills eight.
    static func image(_ mark: FeedMark, ink: ArtInk, size: CGFloat = HomeDesign.FeedDepth.markSymbolSize) -> UIImage? {
        var key = "\(mark.cacheName)|\(ink == .dark ? "dark" : "light")"
        if size != HomeDesign.FeedDepth.markSymbolSize { key += "|\(size)" }
        if let cached = cache[key] { return cached }
        let renderer = ImageRenderer(content: BakedGlowSymbol(mark: mark, ink: ink, size: size))
        renderer.scale = renderScale
        guard let image = renderer.uiImage else { return nil }
        cache[key] = image
        return image
    }

    /// Draws the eight images, so the first card to scroll in does not pay for them.
    static func warm() {
        for ink in [ArtInk.dark, ArtInk.light] {
            for mark in warmSet {
                _ = image(mark, ink: ink)
            }
        }
    }
}

/// The symbol as the headline draws it: 20 % smaller than the headline's text, bold, in the mark's color.
struct FeedMarkSymbol: View {
    let mark: FeedMark
    let ink: ArtInk
    /// Point size; the card's 12 pt by default.
    var size: CGFloat = HomeDesign.FeedDepth.markSymbolSize

    var body: some View {
        Image(systemName: mark.symbolName)
            .font(Font.system(size: size, weight: .bold))
            .foregroundStyle(mark.color(for: ink))
    }
}

/// What the bitmap is made of: the symbol under each glow layer. These are the only live shadows in the feed besides the
/// text lift, and they are drawn once, here.
private struct BakedGlowSymbol: View {
    let mark: FeedMark
    let ink: ArtInk
    let size: CGFloat

    var body: some View {
        let color = mark.color(for: ink)
        let layers = FeedMarkImages.glowLayers(for: ink, size: size)
        ZStack {
            ForEach(Array(layers.enumerated()), id: \.offset) { _, layer in
                FeedMarkSymbol(mark: mark, ink: ink, size: size)
                    .shadow(color: color.opacity(layer.opacity), radius: layer.radius)
            }
        }
        .padding(FeedMarkImages.padding(for: ink, size: size))
    }
}

/// A mark in a headline. Its layout box is exactly the symbol's; the baked glow reaches past that without taking any
/// room. Without an image it draws the plain symbol, so nothing is ever blank.
struct FeedMarkView: View {
    let mark: FeedMark
    let ink: ArtInk
    var size: CGFloat = HomeDesign.FeedDepth.markSymbolSize

    var body: some View {
        FeedMarkSymbol(mark: mark, ink: ink, size: size)
            .background {
                if let image = FeedMarkImages.image(mark, ink: ink, size: size) {
                    Image(uiImage: image)
                        .resizable()
                        .frame(width: image.size.width, height: image.size.height)
                }
            }
            .accessibilityHidden(true)
    }
}
