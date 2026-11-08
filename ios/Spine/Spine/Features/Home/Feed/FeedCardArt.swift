import Nuke
import SwiftUI

// MARK: Requests

/// Everything a feed card asks the image pipeline for, in one place, so the card's views, the prefetcher and the
/// tests all ask for the same URL, thumbnail size and processors, and a prefetched row finds its images already in
/// Nuke's memory cache.
enum FeedCardArt {
    /// How many cards past the one that just appeared are prefetched: 11 of the 125 pt rows reach about as far down
    /// the feed (1,375 pt) as 8 of the old 174 pt rows did.
    static let lookahead = 11
    /// Avatars are drawn at 20 pt and decoded at that size, never at full size.
    static let avatarSize = CGSize(width: 20, height: 20)
    /// The card is glass: its art and base are partly see-through, so the page (and the glow near the top) shows
    /// through and the card does not read as a solid block.
    static let artOpacity: Double = 0.74
    static let baseOpacity: Double = 0.45

    /// How bright the card looks for art measured at `luma`: the art is laid at `artOpacity` over the black page.
    /// The ink and the tone are chosen from this, not from the bitmap alone.
    static func shownLuma(_ luma: Double) -> Double {
        luma * artOpacity
    }

    /// The URL an image view loads: a missing or blank string has none.
    static func url(from string: String?) -> URL? {
        guard let string, !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return URL(string: string)
    }

    static func orientation(isSquare: Bool) -> PosterOrientation {
        isSquare ? .square : .portrait
    }

    /// The decoded size of a card poster: 60 x 90, or 90 x 90 for square art.
    static func posterSize(isSquare: Bool) -> CGSize {
        PosterSlot.feedCard.artworkSize(mediaType: nil, orientation: orientation(isSquare: isSquare))
    }

    /// The poster, or an episode's still aspect-filled into the same slot.
    static func posterRequest(urlString: String?, isSquare: Bool) -> ImageRequest? {
        MediaArtwork.imageRequest(
            url: urlString,
            slot: .feedCard,
            mediaType: nil,
            orientation: orientation(isSquare: isSquare)
        )
    }

    static func posterRequest(for art: FeedCardArtSpec) -> ImageRequest? {
        posterRequest(urlString: art.posterURL, isSquare: art.isSquare)
    }

    /// The baked blurred art behind the card.
    static func backgroundRequest(for art: FeedCardArtSpec) -> ImageRequest? {
        BlurredArt.request(url: url(from: art.backgroundURL), style: .card)
    }

    static func avatarRequest(urlString: String?) -> ImageRequest? {
        SpineImagePipeline.request(url: url(from: urlString), thumbnailSize: avatarSize)
    }

    /// The brightness measured for this baked art before, so a card scrolled back in has its ink and tone on its
    /// first frame.
    static func rememberedLuma(backgroundURL: String?) -> Double? {
        guard let url = url(from: backgroundURL) else { return nil }
        return ArtLuma.remembered(key: url.absoluteString).map(shownLuma)
    }

    /// Every image the card shows: its poster (or a Planning card's fan), the baked art and the avatar.
    static func prefetchRequests(for model: FeedCardModel) -> [ImageRequest] {
        let posters: [ImageRequest?]
        if model.art.planningPosterURLs.isEmpty {
            posters = [posterRequest(for: model.art)]
        } else {
            posters = model.art.planningPosterURLs.prefix(FeedCardStack.maxPosters).map {
                posterRequest(urlString: $0, isSquare: model.art.isSquare)
            }
        }
        return (posters + [backgroundRequest(for: model.art), avatarRequest(urlString: model.avatarURL)])
            .compactMap { $0 }
    }

    /// What the card's art is made of. A card whose key is unchanged needs no new prefetch.
    static func prefetchKey(for model: FeedCardModel) -> String {
        let art = model.art
        return [
            art.posterURL ?? "",
            art.backgroundURL ?? "",
            art.isSquare ? "square" : "portrait",
            model.avatarURL ?? "",
            art.planningPosterURLs.joined(separator: ","),
        ].joined(separator: "|")
    }
}

// MARK: Prefetching

/// Looks ahead of the card that just appeared: it prefetches the next `lookahead` cards that nothing has asked for
/// yet, so a fast fling keeps extending the window and no card is requested twice. Day headers are skipped, not
/// counted. A card whose art changed (a Planning day's posters arriving) is asked for again.
@MainActor
final class FeedCardPrefetcher {
    static let shared = FeedCardPrefetcher { ArtworkPrefetch.prefetch($0) }

    /// Bookkeeping is dropped past this many cards; Nuke dedupes whatever is asked for again.
    private static let rememberedLimit = 600

    private let lookahead: Int
    private let sink: ([ImageRequest]) -> Void
    /// Card id to the art key last prefetched for it.
    private var prefetched: [Int: String] = [:]

    init(lookahead: Int = FeedCardArt.lookahead, sink: @escaping ([ImageRequest]) -> Void) {
        self.lookahead = lookahead
        self.sink = sink
    }

    /// The cards a card appearing at `index` would prefetch, nearest first, without prefetching them.
    func cardsToPrefetch(appearing index: Int, in entries: [FeedListEntry]) -> [FeedCardModel] {
        guard index >= 0 else { return [] }
        var cards: [FeedCardModel] = []
        var seen = 0
        var next = index + 1
        while next < entries.count, seen < lookahead {
            if case let .card(model) = entries[next] {
                // A card already asked for still uses one of the window's slots.
                seen += 1
                if prefetched[model.id] != FeedCardArt.prefetchKey(for: model) { cards.append(model) }
            }
            next += 1
        }
        return cards
    }

    /// A card (or a header) at `index` came on screen.
    func entryAppeared(at index: Int, in entries: [FeedListEntry]) {
        let cards = cardsToPrefetch(appearing: index, in: entries)
        guard !cards.isEmpty else { return }
        if prefetched.count > Self.rememberedLimit { prefetched.removeAll() }
        var requests: [ImageRequest] = []
        for card in cards {
            prefetched[card.id] = FeedCardArt.prefetchKey(for: card)
            requests.append(contentsOf: FeedCardArt.prefetchRequests(for: card))
        }
        if !requests.isEmpty { sink(requests) }
    }
}

extension View {
    /// When this row appears, prefetches the art of the next 8 cards that have not been prefetched. Use it on every
    /// row of the feed's flat list.
    func prefetchingFeedCards(at index: Int, in entries: [FeedListEntry]) -> some View {
        onAppear {
            FeedCardPrefetcher.shared.entryAppeared(at: index, in: entries)
        }
    }
}

// MARK: Planning fan

/// Where each poster of a Planning card's fan sits: the i-th is 10 further right and 8 further down than the one
/// before it, 6 narrower and 9 shorter (still 2:3), and dimmed by a quarter more.
nonisolated struct FeedStackSlot: Equatable {
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat
    /// Black laid over the poster, 0 to 1: the mock's `brightness(1 - 0.25 i)`.
    let dimming: Double
}

nonisolated enum FeedCardStack {
    static let maxPosters = 3

    static func slot(index: Int, isSquare: Bool) -> FeedStackSlot {
        let step = CGFloat(index)
        let inset = HomeDesign.Metric.cardInset
        if isSquare {
            // Square art is 90 wide and the text starts at 116, so the fan steps right by less.
            return FeedStackSlot(
                x: inset + 7 * step,
                y: inset + 8 * step,
                width: HomeDesign.Metric.cardSquare.width - 9 * step,
                height: HomeDesign.Metric.cardSquare.height - 9 * step,
                dimming: 0.25 * Double(index)
            )
        }
        return FeedStackSlot(
            x: inset + 10 * step,
            y: inset + 8 * step,
            width: HomeDesign.Metric.cardPoster.width - 6 * step,
            height: HomeDesign.Metric.cardPoster.height - 9 * step,
            dimming: 0.25 * Double(index)
        )
    }
}

// MARK: Views

/// A card's poster at (12, 12): one poster, or up to three stacked posters for a Planning day once its titles
/// have loaded. No shadow; a hairline keeps a dark cover's edge.
struct FeedCardPoster: View {
    let art: FeedCardArtSpec
    let title: String

    private static let radius = PosterSlot.feedCard.cornerRadius

    var body: some View {
        if art.planningPosterURLs.isEmpty {
            MediaArtwork(
                url: art.posterURL,
                title: title,
                slot: .feedCard,
                mediaType: nil,
                orientation: FeedCardArt.orientation(isSquare: art.isSquare)
            )
            .overlay { Self.hairline }
            .padding(.leading, HomeDesign.Metric.cardInset)
            .padding(.top, HomeDesign.Metric.cardInset)
        } else {
            fan
        }
    }

    /// Back to front: the last poster is drawn first, so the first is on top.
    private var fan: some View {
        let urls = Array(art.planningPosterURLs.prefix(FeedCardStack.maxPosters))
        return ZStack(alignment: .topLeading) {
            ForEach(Array(urls.enumerated()).reversed(), id: \.offset) { index, url in
                let slot = FeedCardStack.slot(index: index, isSquare: art.isSquare)
                SpineAsyncImage(
                    url: FeedCardArt.url(from: url),
                    thumbnailSize: FeedCardArt.posterSize(isSquare: art.isSquare)
                ) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    } else {
                        Color.white.opacity(0.12)
                    }
                }
                .frame(width: slot.width, height: slot.height)
                .overlay { Color.black.opacity(slot.dimming) }
                .clipShape(RoundedRectangle(cornerRadius: Self.radius, style: .continuous))
                .overlay { Self.hairline }
                .padding(.leading, slot.x)
                .padding(.top, slot.y)
            }
        }
    }

    private static var hairline: some View {
        RoundedRectangle(cornerRadius: radius, style: .continuous)
            .strokeBorder(Color.white.opacity(0.10), lineWidth: 0.5)
    }
}

/// The card's background: baked blurred art over a base, both partly see-through, a bounded tone overlay for the
/// ink, a sheen, and the glass
/// edge, all clipped to the card's rounded shape. This is the only clipped layer of a card.
///
/// It owns the art's measured brightness, which only the tone overlay needs, and tells the card the ink the
/// measurement settled when that is not what the last one settled, so the card's text is not drawn again for a
/// measurement that changes nothing about it.
struct FeedCardBackground: View {
    let art: FeedCardArtSpec
    /// The ink the art's brightness settles (nil inside the dead band), told only when it differs from before.
    let onSettled: @MainActor (ArtInk?) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var luma: Double?

    init(art: FeedCardArtSpec, onSettled: @escaping @MainActor (ArtInk?) -> Void) {
        self.art = art
        self.onSettled = onSettled
        _luma = State(initialValue: FeedCardArt.rememberedLuma(backgroundURL: art.backgroundURL))
    }

    var body: some View {
        let ink = ArtInk.resolve(luma: luma, guess: art.guessInk)
        BlurredArtBackground(
            url: FeedCardArt.url(from: art.backgroundURL),
            accentHex: art.accentHex,
            style: .card,
            onMeasured: measured,
            artOpacity: FeedCardArt.artOpacity,
            baseOpacity: FeedCardArt.baseOpacity
        )
        .overlay {
            ArtTone.overlayColor(ink: ink).opacity(ArtTone.overlayAlpha(ink: ink, luma: luma))
        }
        .overlay { FeedCardSheen() }
        .overlay { FeedCardEdge() }
        .clipShape(RoundedRectangle(cornerRadius: HomeDesign.Metric.cardCorner, style: .continuous))
        // The tone settles over 0.18 s when the art is measured, instead of jumping.
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: luma)
        .onChange(of: art.backgroundURL) { _, url in
            luma = FeedCardArt.rememberedLuma(backgroundURL: url)
        }
    }

    private func measured(_ raw: Double) {
        let value = FeedCardArt.shownLuma(raw)
        guard luma != value else { return }
        let before = ArtInk.conclusive(luma: luma)
        luma = value
        let after = ArtInk.conclusive(luma: value)
        if after != before { onSettled(after) }
    }
}

/// The glass rim: a half-point line round the edge and a one-point highlight along the top, as two shapes, a little
/// brighter than the mock's `0.22` / `0.16` so the see-through card keeps a clear edge.
struct FeedCardEdge: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: HomeDesign.Metric.cardCorner, style: .continuous)
                .strokeBorder(Color.white.opacity(0.28), lineWidth: 0.5)
            FeedCardTopHighlight()
                .fill(Color.white.opacity(0.28))
        }
        .allowsHitTesting(false)
    }
}

/// Light on the glass: a soft white sheen down from the top edge that is gone by the middle, and a faint lift at the
/// bottom. One gradient layer, drawn by the GPU.
struct FeedCardSheen: View {
    var body: some View {
        LinearGradient(
            stops: [
                .init(color: .white.opacity(0.12), location: 0),
                .init(color: .white.opacity(0.03), location: 0.4),
                .init(color: .white.opacity(0), location: 0.7),
                .init(color: .white.opacity(0.03), location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
        .allowsHitTesting(false)
    }
}

/// The part of the card's outline a copy of itself moved down by one point leaves uncovered: a crescent that is
/// one point thick at the top and thins away round the corners.
struct FeedCardTopHighlight: Shape {
    func path(in rect: CGRect) -> Path {
        let outline = RoundedRectangle(cornerRadius: HomeDesign.Metric.cardCorner, style: .continuous).path(in: rect)
        return outline.subtracting(outline.offsetBy(dx: 0, dy: 1))
    }
}
