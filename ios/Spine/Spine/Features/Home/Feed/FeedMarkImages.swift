import SwiftUI
import UIKit

/// The headline's heart and rewatch symbol with the soft glow of a lit tick in the In progress meter, drawn once each
/// into a bitmap and reused by every card that shows one, so a card that scrolls in draws an image and no blur of its
/// own. There are four: the heart and the rewatch mark, each on dark and on light art.
@MainActor
enum FeedMarkImages {
    /// One layer of the glow: the mark's own color at `opacity`, blurred by `radius`.
    struct GlowLayer: Equatable {
        let radius: CGFloat
        let opacity: Double
    }

    /// A tight layer under a wide one, as the meter's glow stacks four inflated shapes into a soft falloff. Dark art
    /// takes the stronger glow, as the meter's lit ticks do; on light art it is a quiet halo.
    static func glowLayers(for ink: ArtInk) -> [GlowLayer] {
        ink == .dark
            ? [GlowLayer(radius: 2.5, opacity: 0.65), GlowLayer(radius: 6, opacity: 0.40)]
            : [GlowLayer(radius: 1.5, opacity: 0.40), GlowLayer(radius: 3.5, opacity: 0.28)]
    }

    /// How far the glow is allowed to reach past the symbol on every side, so it is never cut.
    static func padding(for ink: ArtInk) -> CGFloat {
        ink == .dark ? 12 : 8
    }

    static let renderScale: CGFloat = 3

    /// The four marks a card shows.
    static let warmSet: [FeedMark] = [.heart, .rewatch]

    private static var cache: [String: UIImage] = [:]

    /// How many images are kept.
    static var cachedCount: Int { cache.count }

    /// Forgets the images (tests).
    static func reset() {
        cache.removeAll()
    }

    /// The image of a mark, its glow included, the symbol plus `padding(for: ink)` on every side at 3x; nil if rendering
    /// failed.
    static func image(_ mark: FeedMark, ink: ArtInk) -> UIImage? {
        let key = "\(mark.cacheName)|\(ink == .dark ? "dark" : "light")"
        if let cached = cache[key] { return cached }
        let renderer = ImageRenderer(content: BakedGlowSymbol(mark: mark, ink: ink))
        renderer.scale = renderScale
        guard let image = renderer.uiImage else { return nil }
        cache[key] = image
        return image
    }

    /// Draws the four images, so the first card to scroll in does not pay for them.
    static func warm() {
        for ink in [ArtInk.dark, ArtInk.light] {
            for mark in warmSet {
                _ = image(mark, ink: ink)
            }
        }
    }
}

/// The symbol as the headline draws it: the headline's size, bold, in the mark's color.
struct FeedMarkSymbol: View {
    let mark: FeedMark
    let ink: ArtInk

    var body: some View {
        Image(systemName: mark.symbolName)
            .font(HomeDesign.Text.headline.weight(.bold))
            .foregroundStyle(mark.color(for: ink))
    }
}

/// What the bitmap is made of: the symbol under each glow layer. These are the only live shadows in the feed besides the
/// text lift, and they are drawn once, here.
private struct BakedGlowSymbol: View {
    let mark: FeedMark
    let ink: ArtInk

    var body: some View {
        let color = mark.color(for: ink)
        let layers = FeedMarkImages.glowLayers(for: ink)
        ZStack {
            ForEach(Array(layers.enumerated()), id: \.offset) { _, layer in
                FeedMarkSymbol(mark: mark, ink: ink)
                    .shadow(color: color.opacity(layer.opacity), radius: layer.radius)
            }
        }
        .padding(FeedMarkImages.padding(for: ink))
    }
}

/// A mark in a headline. Its layout box is exactly the symbol's; the baked glow reaches past that without taking any
/// room. Without an image it draws the plain symbol, so nothing is ever blank.
struct FeedMarkView: View {
    let mark: FeedMark
    let ink: ArtInk

    var body: some View {
        FeedMarkSymbol(mark: mark, ink: ink)
            .background {
                if let image = FeedMarkImages.image(mark, ink: ink) {
                    Image(uiImage: image)
                        .resizable()
                        .frame(width: image.size.width, height: image.size.height)
                }
            }
            .accessibilityHidden(true)
    }
}
