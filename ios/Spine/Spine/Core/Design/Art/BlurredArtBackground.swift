import Nuke
import NukeUI
import SwiftUI

/// Baked blurred art filling whatever it is the background of. It draws over a solid base so the first frame is
/// never empty, shows an image already in Nuke's memory cache on the first frame with no fade, and otherwise
/// fades in over 0.18 s. A `.card` also measures the delivered bitmap once and reports its mean luma, so the
/// card can pick readable text.
struct BlurredArtBackground: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let url: URL?
    let accentHex: String?
    let style: BlurredArt.Style
    var onMeasured: (@MainActor (Double) -> Void)?
    /// Below 1 the art and the base let what is behind show through (a feed card's glass). Each is set on its own
    /// layer, so nothing is composited offscreen.
    var artOpacity: Double = 1
    var baseOpacity: Double = 1

    var body: some View {
        Color.clear
            .background(Self.baseColor(accentHex: accentHex).opacity(baseOpacity))
            .overlay {
                LazyImage(
                    request: BlurredArt.request(url: url, style: style),
                    transaction: Transaction(animation: SpineMotion.animation(reduceMotion: reduceMotion))
                ) { state in
                    if let image = state.image {
                        if style.bakedAspect == nil {
                            image
                                .resizable()
                                .interpolation(.high)
                                .scaledToFill()
                                .opacity(artOpacity)
                                .transition(.opacity)
                        } else {
                            // Baked to the frame's shape already: filling would crop its top and bottom.
                            image
                                .resizable()
                                .interpolation(.high)
                                .opacity(artOpacity)
                                .transition(.opacity)
                        }
                    }
                }
                .onCompletion { result in
                    report(result)
                }
            }
            .clipped()
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private func report(_ result: Result<ImageResponse, Error>) {
        guard style == .card, let onMeasured, let key = url?.absoluteString else { return }
        if let known = ArtLuma.remembered(key: key) {
            onMeasured(known)
            return
        }
        guard case let .success(response) = result, let bitmap = response.image.cgImage else { return }
        Task.detached(priority: .utility) {
            let luma = ArtLuma.measure(bitmap, key: key)
            await MainActor.run {
                if let luma { onMeasured(luma) }
            }
        }
    }

    /// The poster accent darkened for art that takes white text and lightened for art that takes dark text;
    /// a neutral dark grey when the server sent none.
    nonisolated static func baseColor(accentHex: String?) -> Color {
        guard let rgb = ArtHex.rgb(accentHex) else { return Color(white: 0.16) }
        switch ArtInk.guess(accentHex: accentHex) {
        case .dark:
            return Color(red: rgb.red * 0.45, green: rgb.green * 0.45, blue: rgb.blue * 0.45)
        case .light:
            return Color(
                red: rgb.red * 0.4 + 0.6,
                green: rgb.green * 0.4 + 0.6,
                blue: rgb.blue * 0.4 + 0.6
            )
        }
    }
}
