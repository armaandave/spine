import SwiftUI

/// Home's Up next tile, under In progress and Trending: full width and short, the tiles' glass over the first
/// title's blurred art, "Up next ›" and the count on the left and the next posters on the right, soonest first. The
/// whole tile opens the Up next page. It shows only once something is coming up.
struct HomeUpNextTile: View {
    let model: HomeUpNextModel
    /// The tiles above, and how far the page has scrolled: the posters fold in as the tiles' do.
    let tiles: HomeTilesState
    let glow: HomeGlowModel
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
                    UpNextTileFace(items: items, tiles: tiles, glow: glow)
                }
                .buttonStyle(UpNextTilePressStyle())
                .accessibilityLabel("Up next")
                .accessibilityValue(UpNextPresentation.tileValue(items, today: Date()))
                .accessibilityHint("Shows what's coming up")
                .accessibilityIdentifier("home.upNext")
                .padding(.top, HomeDesign.Metric.tileGap)
                .transition(.opacity)
            } else if model.holdsPlace {
                // Its place while the first read is on its way: the tile's glass and its title.
                UpNextTileFace(items: [], tiles: tiles, glow: glow)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Loading Up next")
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
    /// From one poster's leading edge to the next.
    static let posterStep = poster.width + posterGap
    static let maxPosters = 5
    static let titleFont = Font.system(size: 17, weight: .heavy)
    static let chevronFont = Font.system(size: 14, weight: .bold)
}

private struct UpNextTileFace: View {
    let items: [UpNextItem]
    let tiles: HomeTilesState
    let glow: HomeGlowModel

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: UpNextTileSpec.corner, style: .continuous)
        let shown = Array(items.prefix(UpNextTileSpec.maxPosters))
        // The poster the others fold in behind: the middle one, or the sooner of the two middle ones.
        let middle = (shown.count - 1) / 2
        // Not while a tile is open: this tile sits lower then. It folds or unfolds as the tile closes or opens.
        let foldGain: CGFloat = tiles.open == nil ? 1 : 0
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
                // Its line is kept while loading, so the title sits where it will.
                Text(UpNextPresentation.countLine(items.count))
                    .font(HomeDesign.Text.tileSubtitle)
                    .foregroundStyle(TileStyle.subtitle)
                    .opacity(items.isEmpty ? 0 : 1)
            }
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: UpNextTileSpec.posterGap) {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, item in
                    let slots = CGFloat(index - middle)
                    UpNextTilePoster(item: item)
                        .modifier(UpNextPosterFold(gain: foldGain, glow: glow, slotsFromMiddle: slots))
                        .zIndex(Double(-abs(slots)))
                }
            }
            .fixedSize()
        }
        .padding(.leading, UpNextTileSpec.leading)
        .padding(.trailing, UpNextTileSpec.trailing)
        .frame(maxWidth: .infinity)
        .frame(height: UpNextTileSpec.height)
        .background {
            // The feed card's sheen on the art, under the posters and the words, as the card has it. A tile still
            // loading is the glass alone: the sheen with no art under it reads as a glare.
            if !items.isEmpty {
                UpNextTileArt(url: items.first.flatMap { URL(string: $0.media.displayPosterURL ?? "") })
                    .overlay { FeedCardSheen() }
            }
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

/// Folds the posters in behind the middle one as the page scrolls, as the In progress and Trending tiles fold their two
/// back posters behind the front one: each slides toward the middle poster, shrinks and darkens, one to one with how
/// far this tile has scrolled off the top, and unfolds as you scroll back. The posters past the middle one's
/// neighbors fade as they go, as the open carousel's do, so it folds to one poster with two behind it like the fan.
///
/// Its `gain` is 1 while no tile is open and 0 while one is, and animates with the tile's open and close (they run
/// inside an animation); at 0 it reads no scroll.
private struct UpNextPosterFold: ViewModifier, Animatable {
    var gain: CGFloat
    let glow: HomeGlowModel
    /// Poster steps from the middle poster: negative to its left.
    let slotsFromMiddle: CGFloat

    var animatableData: CGFloat {
        get { gain }
        set { gain = newValue }
    }

    func body(content: Content) -> some View {
        // This tile's bottom edge on screen with the page at rest: it sits a gap under the closed tiles.
        let bottom = (glow.tilesBottom ?? GlowSpec.estimatedTilesBottom)
            + HomeDesign.Metric.tileGap + UpNextTileSpec.height
        let progress = gain == 0
            ? 0
            : gain * TileGeometry.foldProgress(scroll: glow.scrollOffset, tilesBottom: bottom)
        let weight = TileGeometry.openFoldWeight(slotsFromFocus: slotsFromMiddle) * progress
        content
            .brightness(Double(-TileGeometry.foldDim * weight))
            .scaleEffect(1 - (1 - TileGeometry.foldScale) * weight)
            .offset(x: -slotsFromMiddle * UpNextTileSpec.posterStep * progress)
            .opacity(TileGeometry.openFoldOpacity(slotsFromFocus: slotsFromMiddle, progress: progress))
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
