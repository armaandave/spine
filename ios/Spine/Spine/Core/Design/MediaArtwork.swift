import Nuke
import SwiftUI

struct MediaArtwork: View {
    let url: String?
    let title: String
    let slot: PosterSlot
    var mediaType: String?
    var orientation: PosterOrientation?
    var contentMode: PosterContentMode = .fill
    /// A flat fill in place of the media type's colored placeholder while the art loads.
    var placeholderFill: Color?

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

    @ViewBuilder
    private var placeholder: some View {
        if let placeholderFill {
            placeholderFill
                .frame(width: artworkSize.width, height: artworkSize.height)
        } else {
            let theme = MediaTypeTheme.theme(for: mediaType ?? "unknown")
            ZStack {
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

/// Up to three posters fanned out, the first on top: a series card on a person's page, and a day of Planning adds.
/// Sized by its slot; the tilt and spread were made for a series card's poster and scale with the slot's width.
struct PosterFan: View {
    let urls: [String?]
    let title: String
    let slot: PosterSlot
    var mediaType: String?

    var body: some View {
        ZStack {
            ForEach(Array(posters.enumerated()), id: \.offset) { index, url in
                MediaArtwork(url: url, title: title, slot: slot, mediaType: mediaType)
                    .scaleEffect(scale(index))
                    .rotationEffect(.degrees(rotation(index)))
                    .offset(x: offset(index) * spread)
                    .shadow(color: .black.opacity(0.32), radius: 8 * spread, y: 4 * spread)
                    .zIndex(Double(posters.count - index))
            }
        }
    }

    /// The series card's poster is 70 points wide; smaller slots fan out less.
    private var spread: CGFloat { slot.size.width / 70 }

    private func offset(_ index: Int) -> CGFloat {
        switch posters.count {
        case 2: index == 0 ? -6 : 6
        case 3: CGFloat(index - 1) * 10
        default: 0
        }
    }

    private func rotation(_ index: Int) -> Double {
        switch posters.count {
        case 2: index == 0 ? -3 : 3
        case 3: Double(index - 1) * 4
        default: 0
        }
    }

    private func scale(_ index: Int) -> CGFloat {
        posters.count == 3 && index == 1 ? 0.94 : 0.88
    }

    private var posters: [String?] {
        let urls = Array(urls.prefix(3))
        return urls.isEmpty ? [nil] : urls
    }
}
