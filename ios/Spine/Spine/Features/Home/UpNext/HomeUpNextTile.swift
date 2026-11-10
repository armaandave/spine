import SwiftUI

/// Home's Up next tile, under In progress and Trending: full width and short, the tiles' glass over the first
/// title's blurred art, "Up next ›" and the count on the left and the next posters on the right, soonest first. The
/// whole tile opens the Up next page. It shows only once something is coming up.
struct HomeUpNextTile: View {
    let model: HomeUpNextModel
    // A closure the page rebuilds every time it runs: it says nothing new (`StableInput`).
    @StableInput var onOpen: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var taps = 0

    var body: some View {
        let items = model.items
        Group {
            if !items.isEmpty {
                Button {
                    taps += 1
                    onOpen()
                } label: {
                    UpNextTileFace(items: items)
                }
                .buttonStyle(UpNextTilePressStyle())
                .accessibilityLabel("Up next")
                .accessibilityValue(UpNextPresentation.tileValue(items, today: Date()))
                .accessibilityHint("Shows what's coming up")
                .accessibilityIdentifier("home.upNext")
                .padding(.top, HomeDesign.Metric.tileGap)
                .transition(.opacity)
            }
        }
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: items.isEmpty)
        .sensoryFeedback(.impact(weight: .light), trigger: taps)
        // Dark art whatever the phone is set to, as the tiles above.
        .environment(\.colorScheme, .dark)
    }
}

/// The numbers of the tile, from the mock.
private enum UpNextTileSpec {
    /// Unchanged since the posters lost their date band, so Home doesn't shift; the posters sit centered in it.
    static let height: CGFloat = 79
    static let corner: CGFloat = 26
    static let leading: CGFloat = 16
    static let trailing: CGFloat = 12
    static let posterGap: CGFloat = 6
    /// A whole 2:3 poster (27 x 40 made 15% bigger).
    static let poster = CGSize(width: 31, height: 46)
    static let posterCorner: CGFloat = 5
    static let maxPosters = 5
    static let titleFont = Font.system(size: 17, weight: .heavy)
    static let chevronFont = Font.system(size: 14, weight: .bold)
}

private struct UpNextTileFace: View {
    let items: [UpNextItem]

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: UpNextTileSpec.corner, style: .continuous)
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 4) {
                    Text("Up next")
                        .font(UpNextTileSpec.titleFont)
                        .tracking(-0.3)
                    Image(systemName: "chevron.right")
                        .font(UpNextTileSpec.chevronFont)
                }
                .foregroundStyle(.white)
                Text(UpNextPresentation.countLine(items.count))
                    .font(HomeDesign.Text.tileSubtitle)
                    .foregroundStyle(TileStyle.subtitle)
            }
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: UpNextTileSpec.posterGap) {
                ForEach(items.prefix(UpNextTileSpec.maxPosters)) { item in
                    UpNextTilePoster(item: item)
                }
            }
            .fixedSize()
        }
        .padding(.leading, UpNextTileSpec.leading)
        .padding(.trailing, UpNextTileSpec.trailing)
        .frame(maxWidth: .infinity)
        .frame(height: UpNextTileSpec.height)
        .background {
            // The feed card's sheen on the art, under the posters and the words, as the card has it.
            UpNextTileArt(url: items.first.flatMap { URL(string: $0.media.displayPosterURL ?? "") })
                .overlay { FeedCardSheen() }
        }
        .background(TileStyle.veil)
        .clipShape(shape)
        .overlay { TileOutline(shape: shape, corner: UpNextTileSpec.corner) }
        .contentShape(shape)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
    }
}

/// One small poster, whole, with the tiles' hairline and shadow round it.
private struct UpNextTilePoster: View {
    let item: UpNextItem

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: UpNextTileSpec.posterCorner, style: .continuous)
        let poster = UpNextTileSpec.poster
        SpineAsyncImage(
            url: URL(string: item.media.displayPosterURL ?? ""),
            thumbnailSize: poster
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                TileStyle.artBase
            }
        }
        .frame(width: poster.width, height: poster.height)
        .clipShape(shape)
        .overlay { shape.strokeBorder(TileStyle.posterHairline, lineWidth: TileStyle.strokeWidth) }
        .shadow(color: .black.opacity(0.35), radius: 6, y: 5)
        .accessibilityHidden(true)
    }
}

/// The first title's art, blurred, saturated and darkened in the image cache as the tiles' is.
private struct UpNextTileArt: View {
    let url: URL?

    var body: some View {
        let style = BlurredArt.Style.tile
        ZStack {
            TileStyle.artBase
            SpineAsyncImage(url: url, thumbnailSize: style.thumbnailSize, processors: [style.processor]) { phase in
                if case let .success(image) = phase {
                    image
                        .resizable()
                        .interpolation(.high)
                        .scaledToFill()
                } else {
                    Color.clear
                }
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Sinks a little while pressed; the push is the rest of the feedback.
private struct UpNextTilePressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .animation(.easeOut(duration: 0.18), value: configuration.isPressed)
    }
}
