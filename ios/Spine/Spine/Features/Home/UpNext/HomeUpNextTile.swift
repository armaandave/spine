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
    static let height: CGFloat = 92
    static let corner: CGFloat = 26
    static let leading: CGFloat = 16
    static let trailing: CGFloat = 14
    static let posterGap: CGFloat = 6
    /// A whole 2:3 poster (27 x 40 made 15% bigger).
    static let poster = CGSize(width: 31, height: 46)
    /// The strip under the poster holding its date.
    static let band: CGFloat = 9
    static let posterCorner: CGFloat = 5
    static let dateFont = Font.system(size: 5.5, weight: .bold, design: .rounded)
    static let dateInk = Color.white.opacity(0.9)
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
        .background { UpNextTileArt(url: items.first.flatMap { URL(string: $0.media.displayPosterURL ?? "") }) }
        .background(TileStyle.veil)
        .clipShape(shape)
        .overlay { TileOutline(shape: shape, corner: UpNextTileSpec.corner) }
        .contentShape(shape)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
    }
}

/// One small poster, whole, with a band under it holding the day it airs: the poster runs on into the band and
/// fades into a soft see-through dark, so the date sits on the poster's own colors. The tiles' hairline and shadow go
/// round both.
private struct UpNextTilePoster: View {
    let item: UpNextItem

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: UpNextTileSpec.posterCorner, style: .continuous)
        let poster = UpNextTileSpec.poster
        let band = UpNextTileSpec.band
        SpineAsyncImage(
            url: URL(string: item.media.displayPosterURL ?? ""),
            thumbnailSize: poster
        ) { phase in
            VStack(spacing: 0) {
                if case let .success(image) = phase {
                    image
                        .resizable()
                        .scaledToFill()
                        .frame(width: poster.width, height: poster.height)
                        .clipped()
                    UpNextTileBand(image: image)
                } else {
                    TileStyle.artBase
                        .frame(width: poster.width, height: poster.height + band)
                }
            }
        }
        .frame(width: poster.width, height: poster.height + band)
        .overlay(alignment: .bottom) {
            Text(UpNextPresentation.shortDate(item.airing.airDate))
                .font(UpNextTileSpec.dateFont)
                .tracking(0.1)
                .foregroundStyle(UpNextTileSpec.dateInk)
                .lineLimit(1)
                .fixedSize()
                .frame(width: poster.width, height: band)
        }
        .clipShape(shape)
        .overlay { shape.strokeBorder(TileStyle.posterHairline, lineWidth: TileStyle.strokeWidth) }
        .shadow(color: .black.opacity(0.35), radius: 6, y: 5)
        .accessibilityHidden(true)
    }
}

/// The poster's last row carried down over the band, softened, then fading into a see-through dark.
private struct UpNextTileBand: View {
    let image: Image

    var body: some View {
        let poster = UpNextTileSpec.poster
        let band = UpNextTileSpec.band
        image
            .resizable()
            .scaledToFill()
            .frame(width: poster.width, height: poster.height)
            .clipped()
            // Only the last row, in a one-point strip, then the strip stretched down to the band's height.
            .frame(width: poster.width, height: 1, alignment: .bottom)
            .clipped()
            .scaleEffect(x: 1, y: band, anchor: .top)
            .frame(width: poster.width, height: band, alignment: .top)
            .blur(radius: 2)
            .overlay {
                LinearGradient(
                    colors: [.black.opacity(0.2), .black.opacity(0.55)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .clipped()
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
