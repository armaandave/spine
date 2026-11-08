import Nuke
import SwiftUI

/// The colored glow at the top of Home while a tile is open, already blurred and saturated in the image cache, under
/// a black gradient: the focused poster's art, `HomeDesign.Metric.glowHeight` tall (`HomeGlowModel.height`). At rest
/// it is behind Home's picture (`HomeTopArtView`) and out of sight. It scrolls away with the page and cross-fades
/// between images.
///
/// It is the background of the scrolling content itself (`HomeView`), so the scroll view moves it with the content
/// natively, in the same frame, with no state in between. Nothing here is live: no blur, saturation, mask or opacity
/// is applied on a frame, and no offscreen pass flattens the layers. The only per-frame work is the one-line
/// `GlowPlacement` effect, which runs in the render pass and re-evaluates no body.
struct HomeGlow: View {
    let model: HomeGlowModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var layers = GlowLayers<GlowArt>()

    var body: some View {
        let _ = PerfCounters.bump(.glowBody)
        Color.clear
            .frame(height: model.height)
            // It grows to its full height as a tile opens and back as it closes, with the tile.
            .animation(
                TileMotion.frame(closing: model.focused == nil, reduceMotion: reduceMotion),
                value: model.focused != nil
            )
            .overlay(alignment: .top) {
                ZStack(alignment: .top) {
                    ForEach(layers.aliveLayers, id: \.token) { layer in
                        GlowImageLayer(
                            art: layer.art,
                            fades: layers.upper != nil && layer.token == layers.upperToken
                        ) { finished in
                            layers.fadeCompleted(of: finished)
                        }
                    }
                }
            }
            // A plain overlay, not a mask: the page behind is black and the bottom stop is solid black.
            .overlay {
                LinearGradient(
                    stops: GlowSpec.gradientStops.map {
                        Gradient.Stop(color: .black.opacity($0.opacity), location: $0.location)
                    },
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .clipped()
            // At rest Home's picture covers it (or, with no picture, the page is plain black), so it shows only while
            // a tile is open, and fades in and out with the picture's own cross-fade.
            .opacity(model.showsGlow ? 1 : 0)
            .animation(HomeDesign.Motion.glowCrossfade(reduceMotion: reduceMotion), value: model.showsGlow)
            .modifier(GlowPlacement(topInset: model.contentTopInset ?? GlowSpec.estimatedTopInset))
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .onChange(of: model.art, initial: true) { old, art in
                // With nothing to show it keeps its last image, so it does not pop out as the picture fades back.
                guard let art else { return }
                let first = layers.show(art)
                // Coming back into the page (the content was rebuilt) with an image already chosen: it is simply there,
                // not faded in from nothing. A first image that arrives later still fades in.
                if first, old == art { layers.fadeCompleted(of: art) }
            }
    }
}

/// Tells the glow where the tiles start in the page's content, so the closed tiles' posters know their bottom edge. The
/// place is read in the content's own space, which does not move as the page scrolls, so scrolling writes nothing.
struct HomeGlowTilesAnchor: ViewModifier {
    let glow: HomeGlowModel

    func body(content: Content) -> some View {
        content.onGeometryChange(for: CGFloat.self) { proxy in
            proxy.frame(in: .named(HomeGlowModel.contentSpace)).minY.rounded()
        } action: { top in
            // While a tile is open the header squeezes up and the tiles move with it: that is not where they rest, so it is kept apart (`openTilesTop`).
            if glow.focused == nil {
                if glow.tilesTop != top { glow.tilesTop = top }
            } else if glow.openTilesTop != top {
                glow.openTilesTop = top
            }
        }
    }
}

/// Puts the glow at the top of the screen. The glow is a background of the scrolling content, so its layout top is the
/// content's top and the scroll view moves it; this lifts it up under the status bar by the inset and the design's
/// offset, and while the page is pulled past the top, lifts it by the pull so it stays at the top with no gap. It reads
/// the glow's place in the render pass, so nothing is written to state and no body runs.
struct GlowPlacement: ViewModifier {
    let topInset: CGFloat
    /// Where the top sits on screen at rest; Home's picture sits at the very top (0).
    var topOffset: CGFloat = BackdropLayout.topOffset

    func body(content: Content) -> some View {
        let topInset = topInset
        let topOffset = topOffset
        content.visualEffect { content, proxy in
            content.offset(y: GlowSpec.offset(
                layoutTop: proxy.frame(in: .global).minY, topInset: topInset, topOffset: topOffset
            ))
        }
    }
}

/// One layer of the glow at the design's 590 x 720 (1.513 times the box's width), centered on the box: one baked
/// image. The upper layer fades in from nothing
/// when it appears and tells its owner when it has finished; a lower layer is simply there. The view is keyed by its
/// layer's token, so a layer that moves from upper to lower stays the same view and the hand-over has no frame where
/// it is redrawn.
private struct GlowImageLayer: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown: Bool

    let art: GlowArt
    let onFinished: (GlowArt) -> Void

    init(art: GlowArt, fades: Bool, onFinished: @escaping (GlowArt) -> Void) {
        self.art = art
        self.onFinished = onFinished
        _shown = State(initialValue: !fades)
    }

    var body: some View {
        Color.clear
            .frame(height: GlowSpec.imageHeight)
            .containerRelativeFrame(.horizontal) { width, _ in width * GlowSpec.imageWidthRatio }
            .overlay { Self.image(art.url, processors: [BlurredArt.Style.glow.processor]) }
            .clipped()
            .offset(y: GlowSpec.imageTop)
            // Without a compositing group SwiftUI sets this on each image, not on the pair, so nothing is flattened.
            .opacity(shown ? GlowSpec.imageOpacity : 0)
            .onAppear {
                guard !shown else { return }
                let art = art
                let onFinished = onFinished
                withAnimation(
                    HomeDesign.Motion.glowCrossfade(reduceMotion: reduceMotion),
                    completionCriteria: .logicallyComplete
                ) {
                    shown = true
                } completion: {
                    onFinished(art)
                }
                // A backstop in case the completion never arrives: the layers would stay "fading" and the glow would
                // stop following the carousel. A second call is harmless (fadeCompleted ignores a layer that isn't upper).
                DispatchQueue.main.asyncAfter(deadline: .now() + HomeDesign.Motion.glowCrossfadeDuration + 0.3) {
                    onFinished(art)
                }
            }
    }

    private static func image(_ url: URL, processors: [any ImageProcessing]) -> some View {
        SpineAsyncImage(url: url, thumbnailSize: BlurredArt.Style.glow.thumbnailSize, processors: processors) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Color.clear
            }
        }
    }
}

extension HomeGlow {
    /// The request the glow loads and the prefetcher warms, so both share one cache entry.
    static func request(for media: MediaSummary?) -> ImageRequest? {
        BlurredArt.request(url: HomeArtworkAtmosphereModel.url(for: media), style: .glow)
    }

    /// Warms the glow's thumbnails for `media` (the focused poster's neighbors), at low priority.
    static func prefetch(_ media: [MediaSummary]) {
        ArtworkPrefetch.prefetch(media.compactMap { request(for: $0) })
    }

    /// The titles within `radius` places of `index`, not including it.
    static func neighbors(of index: Int, in media: [MediaSummary], radius: Int = 2) -> [MediaSummary] {
        guard media.indices.contains(index) else { return [] }
        let range = max(0, index - radius) ... min(media.count - 1, index + radius)
        return range.filter { $0 != index }.map { media[$0] }
    }
}
