import Nuke
import SwiftUI

/// The colored glow at the top of Home, already blurred and saturated in the image cache, under a black gradient: at
/// rest In progress's art on the left and Trending's on the right, meeting in the middle; the focused poster's art
/// while a carousel is open. It is `HomeDesign.Metric.glowHeight` tall, scrolls away with the page, and cross-fades
/// between them.
///
/// Nothing here is live: no blur, saturation, mask or opacity is applied on a frame, and no offscreen pass flattens
/// the layers. The only per-frame work is the offset in `GlowScrollOffset`.
struct HomeGlow: View {
    let model: HomeGlowModel
    @State private var layers = GlowLayers<GlowArt>()

    var body: some View {
        let _ = PerfCounters.bump(.glowBody)
        Color.clear
            .frame(height: HomeDesign.Metric.glowHeight)
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
            .ignoresSafeArea(edges: .top)
            .modifier(GlowScrollOffset(model: model))
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .onChange(of: model.art, initial: true) { _, art in
                layers.show(art)
            }
    }
}

/// Moves the glow with the page. A modifier of its own so that reading the scroll offset re-evaluates only this
/// one line, not the glow's layers.
private struct GlowScrollOffset: ViewModifier {
    let model: HomeGlowModel

    func body(content: Content) -> some View {
        content.offset(y: GlowSpec.offset(scroll: model.scrollOffset))
    }
}

/// One layer of the glow at the design's 590 x 720 (1.513 times the box's width), centered on the box: one baked
/// image, or two where the right one has its fade toward the middle baked in. The upper layer fades in from nothing
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
            .overlay { Self.image(art.left, processors: [BlurredArt.Style.glow.processor]) }
            .overlay {
                if let right = art.right { Self.image(right, processors: HomeGlow.meetingProcessors) }
            }
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

    /// The right-hand image of the glow at rest: the same bake, faded out toward the middle.
    static var meetingProcessors: [any ImageProcessing] {
        [BlurredArt.Style.glow.processor, GlowMeetProcessor()]
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
