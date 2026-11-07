import Nuke
import SwiftUI

/// The colored glow at the top of Home: the focused poster's art, already blurred and saturated in the image
/// cache, under a black gradient. It is `HomeDesign.Metric.glowHeight` tall, scrolls away with the page, and
/// cross-fades to whichever poster is focused.
///
/// Nothing here is live: no blur, saturation or opacity is applied on a frame, and no offscreen pass flattens the
/// layers. The only per-frame work is the offset in `GlowScrollOffset`.
struct HomeGlow: View {
    let model: HomeGlowModel
    @State private var layers = GlowLayers()

    var body: some View {
        let _ = PerfCounters.bump(.glowBody)
        Color.clear
            .frame(height: HomeDesign.Metric.glowHeight)
            .overlay(alignment: .top) {
                ZStack(alignment: .top) {
                    ForEach(layers.alive, id: \.self) { url in
                        GlowImageLayer(url: url, fades: url == layers.upper) { finished in
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
            .onChange(of: model.activeURL, initial: true) { _, url in
                layers.show(url)
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

/// One baked image at the design's 590 x 720 (1.513 times the box's width), centered on the box. The upper layer
/// fades in from nothing when it appears and tells its owner when it has finished; a lower layer is simply there.
/// The view is keyed by its URL, so an image that moves from upper to lower stays the same view and the
/// hand-over has no frame where it is redrawn.
private struct GlowImageLayer: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown: Bool

    let url: URL
    let onFinished: (URL) -> Void

    init(url: URL, fades: Bool, onFinished: @escaping (URL) -> Void) {
        self.url = url
        self.onFinished = onFinished
        _shown = State(initialValue: !fades)
    }

    var body: some View {
        Color.clear
            .frame(height: GlowSpec.imageHeight)
            .containerRelativeFrame(.horizontal) { width, _ in width * GlowSpec.imageWidthRatio }
            .overlay {
                SpineAsyncImage(
                    url: url,
                    thumbnailSize: BlurredArt.Style.glow.thumbnailSize,
                    processors: [BlurredArt.Style.glow.processor]
                ) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    } else {
                        Color.clear
                    }
                }
            }
            .clipped()
            .offset(y: GlowSpec.imageTop)
            .opacity(shown ? GlowSpec.imageOpacity : 0)
            .onAppear {
                guard !shown else { return }
                let url = url
                let onFinished = onFinished
                withAnimation(
                    HomeDesign.Motion.glowCrossfade(reduceMotion: reduceMotion),
                    completionCriteria: .logicallyComplete
                ) {
                    shown = true
                } completion: {
                    onFinished(url)
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
