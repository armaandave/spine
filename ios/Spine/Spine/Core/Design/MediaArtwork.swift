import Nuke
import SwiftUI

struct MediaArtwork: View {
    let url: String?
    let title: String
    let slot: PosterSlot
    var mediaType: String?
    var orientation: PosterOrientation?
    var contentMode: PosterContentMode = .fill

    var body: some View {
        SpineAsyncImage(url: imageURL, thumbnailSize: artworkSize) { phase in
            artwork(for: phase)
        }
        .frame(width: artworkSize.width, height: artworkSize.height)
        .clipShape(RoundedRectangle(cornerRadius: slot.cornerRadius, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: slot.cornerRadius, style: .continuous))
        .accessibilityLabel(title)
    }

    @ViewBuilder
    private func artwork(for phase: AsyncImagePhase) -> some View {
        switch phase {
        case let .success(image):
            image
                .resizable()
                .modifier(ArtworkScaleModifier(contentMode: contentMode))
                .frame(width: artworkSize.width, height: artworkSize.height)
                .clipped()
        case .empty:
            placeholder
        default:
            placeholder
        }
    }

    /// The request an artwork with these values loads, so a grid can prefetch exactly what its cells will ask for.
    static func imageRequest(
        url: String?,
        slot: PosterSlot,
        mediaType: String? = nil,
        orientation: PosterOrientation? = nil
    ) -> ImageRequest? {
        SpineImagePipeline.request(
            url: imageURL(from: url),
            thumbnailSize: slot.artworkSize(mediaType: mediaType, orientation: orientation)
        )
    }

    private static func imageURL(from url: String?) -> URL? {
        guard let url, !url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return URL(string: url)
    }

    private var imageURL: URL? {
        Self.imageURL(from: url)
    }

    private var artworkSize: CGSize {
        slot.artworkSize(mediaType: mediaType, orientation: orientation)
    }

    private var placeholder: some View {
        let theme = MediaTypeTheme.theme(for: mediaType ?? "unknown")
        return ZStack {
            LinearGradient(
                colors: theme.gradientColors.map { $0.opacity(0.8) },
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .overlay(.quaternary.opacity(0.28))

            MediaTypeGlyph(theme: theme, size: min(artworkSize.width, artworkSize.height) * 0.36)
                .opacity(0.8)
        }
        .frame(width: artworkSize.width, height: artworkSize.height)
    }
}

private struct ArtworkScaleModifier: ViewModifier {
    let contentMode: PosterContentMode

    @ViewBuilder
    func body(content: Content) -> some View {
        switch contentMode {
        case .fill:
            content.scaledToFill()
        case .fit:
            content.scaledToFit()
        }
    }
}

#Preview("Media Artwork") {
    HStack(alignment: .top, spacing: 14) {
        MediaArtwork(
            url: "https://image.tmdb.org/t/p/w500/8kOWDBK6XlPUzckuHDo3wwVRFwt.jpg",
            title: "Portrait",
            slot: .carousel,
            mediaType: "movie",
            orientation: .portrait
        )
        MediaArtwork(
            url: "https://image.tmdb.org/t/p/original/9xxLWtnFxkpJ2h1uthpvCRK6vta.jpg",
            title: "Landscape",
            slot: .carousel,
            mediaType: "tv",
            orientation: .landscape
        )
        MediaArtwork(url: nil, title: "Missing", slot: .carousel, mediaType: "book")
    }
    .padding()
    .background(Color.black)
}
