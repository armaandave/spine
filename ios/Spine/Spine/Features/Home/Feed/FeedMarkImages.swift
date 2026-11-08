import SwiftUI
import UIKit

/// The feed's duotone marks, drawn once each into a bitmap and reused by every card that shows one, so a card that
/// scrolls in draws an image and no gradient, stroke or shadow of its own. There are six: the heart at 18 pt (the
/// headline) and 13 pt (the counts pill) and the rewatch mark at 22 pt, each on dark and on light art.
@MainActor
enum FeedMarkImages {
    /// How far the glow is allowed to reach past the mark on every side, so it is never cut: the dark ink's glow is
    /// the wider.
    static func padding(for ink: ArtInk) -> CGFloat {
        ink == .dark ? 8 : 3
    }

    static let renderScale: CGFloat = 3

    /// The six marks a card shows.
    static let warmSet: [(mark: FeedMark, size: CGFloat)] = [
        (.heart, HomeDesign.FeedDepth.heartSize),
        (.heart, HomeDesign.FeedDepth.countsIcon),
        (.rewatch, HomeDesign.FeedDepth.rewatchSize),
    ]

    private static var cache: [String: UIImage] = [:]

    /// How many images are kept.
    static var cachedCount: Int { cache.count }

    /// Forgets the images (tests).
    static func reset() {
        cache.removeAll()
    }

    /// The image of a mark, its glow included, `size + 2 * padding` points square at 3x; nil if rendering failed.
    static func image(_ mark: FeedMark, ink: ArtInk, size: CGFloat) -> UIImage? {
        let key = "\(mark.cacheName)|\(ink == .dark ? "dark" : "light")|\(size)"
        if let cached = cache[key] { return cached }
        let renderer = ImageRenderer(content: BakedDuotone(mark: mark, ink: ink, size: size))
        renderer.scale = renderScale
        guard let image = renderer.uiImage else { return nil }
        cache[key] = image
        return image
    }

    /// Draws the six images, so the first card to scroll in does not pay for them.
    static func warm() {
        for ink in [ArtInk.dark, ArtInk.light] {
            for (mark, size) in warmSet {
                _ = image(mark, ink: ink, size: size)
            }
        }
    }
}

/// What the bitmap is made of: the live mark plus its glow (dark art) or its halo and contact shadow (light art).
/// These are the only shadows in the feed that are not a text lift, and they are drawn once, here.
private struct BakedDuotone: View {
    let mark: FeedMark
    let ink: ArtInk
    let size: CGFloat

    var body: some View {
        let palette = MarkPalette.palette(for: mark, ink: ink)
        let pad = FeedMarkImages.padding(for: ink)
        let scale = size / 18
        Group {
            if ink == .dark {
                DuotoneMark(mark: mark, ink: ink, size: size)
                    .shadow(color: MarkPalette.color(palette.glow, opacity: 0.40), radius: 3.5 * scale)
            } else {
                DuotoneMark(mark: mark, ink: ink, size: size)
                    .shadow(color: .white.opacity(0.85), radius: 0.6)
                    .shadow(color: MarkPalette.color(palette.deep, opacity: 0.30), radius: 1 * scale, x: 0, y: 0.5 * scale)
            }
        }
        .frame(width: size + 2 * pad, height: size + 2 * pad)
    }
}

/// A mark in a card. Its layout box is exactly `size`; the baked image (and its glow) reaches past that without
/// taking any room. Without an image it draws the mark live, so nothing is ever blank.
struct FeedMarkView: View {
    let mark: FeedMark
    let ink: ArtInk
    let size: CGFloat

    var body: some View {
        if let image = FeedMarkImages.image(mark, ink: ink, size: size) {
            let pad = FeedMarkImages.padding(for: ink)
            Image(uiImage: image)
                .resizable()
                .frame(width: size + 2 * pad, height: size + 2 * pad)
                .padding(-pad)
                .accessibilityHidden(true)
        } else {
            DuotoneMark(mark: mark, ink: ink, size: size)
                .accessibilityHidden(true)
        }
    }
}
