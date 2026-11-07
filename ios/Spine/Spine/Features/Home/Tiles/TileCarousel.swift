import Nuke
import SwiftUI

/// What a tile poster asks the image pipeline for. The fan and the carousel are the same views, so the request is
/// the same: one decode at 120 x 180 (a square album at 120 x 120), shared by both.
enum TilePosterArt {
    static func size(for media: MediaSummary) -> CGSize {
        PosterSlot.homeCarousel.artworkSize(mediaType: media.ref.mediaType, orientation: media.posterOrientation)
    }

    static func request(for media: MediaSummary) -> ImageRequest? {
        media.artworkRequest(slot: .homeCarousel)
    }

    /// Warms the posters at `indexes` of `posters`, at low priority.
    static func prefetch(_ posters: [TilePoster], at indexes: some Sequence<Int>) {
        let requests = indexes.compactMap { index -> ImageRequest? in
            posters.indices.contains(index) ? request(for: posters[index].media) : nil
        }
        ArtworkPrefetch.prefetch(requests)
    }
}

/// A poster's place in the fan or the carousel as one animatable value. The poster view never changes its frame: it
/// moves by offset, scale and rotation, and the corner radius is worked out from the scale on every frame so the
/// poster reads 12 pt at every size. The hairline and the baked shadow come with it, so nothing is a live effect.
struct PosterPoseEffect: ViewModifier, Animatable {
    var scale: CGFloat
    var offsetX: CGFloat
    var offsetY: CGFloat
    var degrees: Double
    /// The poster's art, which may be smaller than its 120 x 180 slot (a square album).
    let artSize: CGSize

    var animatableData: AnimatablePair<AnimatablePair<CGFloat, CGFloat>, AnimatablePair<CGFloat, Double>> {
        get { AnimatablePair(AnimatablePair(scale, offsetX), AnimatablePair(offsetY, degrees)) }
        set {
            scale = newValue.first.first
            offsetX = newValue.first.second
            offsetY = newValue.second.first
            degrees = newValue.second.second
        }
    }

    func body(content: Content) -> some View {
        let radius = TileGeometry.cornerRadius(forScale: scale)
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        content
            .clipShape(shape)
            .overlay {
                shape.strokeBorder(TileStyle.posterHairline, lineWidth: 0.5 / max(scale, 0.01))
            }
            .background {
                BakedShadowView(size: artSize)
            }
            .scaleEffect(scale)
            .rotationEffect(.degrees(degrees))
            .offset(x: offsetX, y: offsetY)
    }
}

/// A button that adds no highlight of its own: a poster dims by pose, never by being pressed.
private struct PosterButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
    }
}

/// One poster. Which pose it takes, and how it gets there, depends on whether its tile is opening, closing or only
/// moving its focus: a flight is the spring after a stagger, a change of focus is the soft 0.45 s, a dim is 0.35 s.
private struct TilePosterCell: View {
    let poster: TilePoster
    let index: Int
    let count: Int
    let tile: HomeTileID
    let contentWidth: CGFloat
    let state: HomeTilesState
    let onTap: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let isOpen = state.open == tile
        let focus = state.focusIndex ?? 0
        let isFocused = isOpen && focus == index
        let pose = TileGeometry.pose(index: index, focus: focus, isOpen: isOpen, contentWidth: contentWidth)
        let delay = TileGeometry.staggerDelay(index: index, count: count, opening: isOpen)
        let media = poster.media
        Button(action: onTap) {
            MediaArtwork(
                url: media.displayPosterURL,
                title: media.displayTitle,
                slot: .homeCarousel,
                mediaType: media.ref.mediaType,
                orientation: media.posterOrientation
            )
            .overlay {
                Color.black.opacity(pose.dim)
                    .allowsHitTesting(false)
            }
            .animation(TileMotion.dim(reduceMotion: reduceMotion), value: pose.dim)
        }
        .buttonStyle(PosterButtonStyle())
        .modifier(PosterPoseEffect(
            scale: pose.scale,
            offsetX: pose.offsetX,
            offsetY: pose.offsetY,
            degrees: pose.degrees,
            artSize: TilePosterArt.size(for: media)
        ))
        // The innermost of these two that applies wins, so an open or a close (a flight) beats a change of focus
        // that happens in the same update.
        .transaction(value: isOpen) { transaction in
            transaction.animation = TileMotion.flight(delay: delay, reduceMotion: reduceMotion)
        }
        .transaction(value: isFocused) { transaction in
            transaction.animation = TileMotion.focus(reduceMotion: reduceMotion)
        }
        .opacity(pose.opacity)
        .animation(TileMotion.posterFade(delay: delay, reduceMotion: reduceMotion), value: pose.opacity)
        .frame(width: TileGeometry.posterSize.width, height: TileGeometry.posterSize.height)
        .zIndex(pose.z)
        .accessibilityHidden(true)
    }
}

/// The poster number the carousel last reported. A plain box: changing it redraws nothing, so a scroll never
/// touches observable state.
private final class RestingIndex {
    var value = 0
}

/// A tile's posters in one horizontal scroll view, so they follow the finger exactly. Closed, the same posters sit in
/// the fan and the view cannot be scrolled or touched; open, they spring into a row that snaps one poster at a time.
///
/// The tile reports only a whole poster number: `onScrollGeometryChange` turns the offset into the nearest poster's
/// index and acts when that number changes. No offset is stored anywhere.
struct TileCarousel: View {
    let tile: HomeTileID
    let posters: [TilePoster]
    let state: HomeTilesState
    let contentWidth: CGFloat
    /// The focused poster was tapped: open its title.
    let onOpenTitle: (TilePoster) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var focusTicks = 0
    @State private var resting = RestingIndex()

    var body: some View {
        let isOpen = state.open == tile
        let count = posters.count
        ScrollViewReader { proxy in
            ScrollView(.horizontal) {
                HStack(spacing: TileGeometry.spacing) {
                    ForEach(Array(posters.enumerated()), id: \.element.id) { index, poster in
                        if TileGeometry.isMounted(index: index, allMounted: state.isFullyMounted(tile)) {
                            TilePosterCell(
                                poster: poster,
                                index: index,
                                count: count,
                                tile: tile,
                                contentWidth: contentWidth,
                                state: state,
                                onTap: { tapped(poster, proxy: proxy) }
                            )
                        } else {
                            // The same size as a poster, so the ones that are mounted keep their natural places.
                            Color.clear
                                .frame(width: TileGeometry.posterSize.width, height: TileGeometry.posterSize.height)
                        }
                    }
                }
                .scrollTargetLayout()
                .padding(.top, TileGeometry.top)
            }
            .contentMargins(.horizontal, TileGeometry.carouselPadding(contentWidth: contentWidth), for: .scrollContent)
            .scrollTargetBehavior(.viewAligned)
            .scrollIndicators(.hidden)
            .scrollClipDisabled()
            .scrollDisabled(!isOpen)
            .onScrollGeometryChange(for: Int.self) { geometry in
                TileGeometry.focusIndex(
                    scrollX: geometry.contentOffset.x + geometry.contentInsets.leading,
                    count: count
                )
            } action: { _, index in
                focusChanged(to: index)
            }
            .onChange(of: isOpen) { _, nowOpen in
                guard let first = posters.first else { return }
                // Opening starts on the first poster; closing sends the carousel back to it.
                resting.value = 0
                if nowOpen {
                    proxy.scrollTo(first.id, anchor: .center)
                } else if reduceMotion {
                    proxy.scrollTo(first.id, anchor: .center)
                } else {
                    // Back to the first poster while the others fly into the fan.
                    withAnimation(.easeOut(duration: 0.25)) {
                        proxy.scrollTo(first.id, anchor: .center)
                    }
                }
            }
            .onChange(of: state.focusID) { _, _ in
                syncScroll(proxy: proxy)
            }
            .onChange(of: posters.map(\.id)) { _, _ in
                syncScroll(proxy: proxy)
            }
            .frame(
                width: contentWidth,
                height: HomeDesign.Metric.tileOpenHeight,
                alignment: .topLeading
            )
            .allowsHitTesting(isOpen)
            .sensoryFeedback(.selection, trigger: focusTicks)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(tile.title)
            .accessibilityValue(accessibilityValue)
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: step(by: 1, proxy: proxy)
                case .decrement: step(by: -1, proxy: proxy)
                @unknown default: break
                }
            }
            .accessibilityAction {
                if let focused = focusedPoster { onOpenTitle(focused) }
            }
            .accessibilityIdentifier("home.tile.carousel")
            .accessibilityHidden(!isOpen)
        }
    }

    // MARK: Focus

    private var focusedPoster: TilePoster? {
        guard let focusID = state.focusID else { return nil }
        return posters.first { $0.id == focusID }
    }

    /// "Poster 2 of 5, Severance": what VoiceOver reads for the carousel as it adjusts.
    private var accessibilityValue: String {
        guard let index = state.focusIndex, posters.indices.contains(index) else { return "" }
        return "Poster \(index + 1) of \(posters.count), \(posters[index].media.displayTitle)"
    }

    /// A reload moved the focused title to another place, or moved the focus to a neighbor: put the carousel on it
    /// again, at once. A scroll the person made has already told `resting`, so it does nothing then.
    private func syncScroll(proxy: ScrollViewProxy) {
        guard state.open == tile, let focusID = state.focusID, let index = state.focusIndex,
              index != resting.value else { return }
        resting.value = index
        proxy.scrollTo(focusID, anchor: .center)
    }

    private func focusChanged(to index: Int) {
        guard state.open == tile, posters.indices.contains(index) else { return }
        resting.value = index
        guard state.focus(posters[index].id) else { return }
        focusTicks += 1
        // The glow and the posters on either side are about to be asked for.
        HomeGlow.prefetch(HomeGlow.neighbors(of: index, in: posters.map(\.media)))
        TilePosterArt.prefetch(posters, at: max(0, index - 2) ... min(posters.count - 1, index + 2))
    }

    private func tapped(_ poster: TilePoster, proxy: ScrollViewProxy) {
        guard state.open == tile else { return }
        if poster.id == state.focusID {
            onOpenTitle(poster)
        } else {
            withAnimation(HomeDesign.Motion.transform(reduceMotion: reduceMotion)) {
                proxy.scrollTo(poster.id, anchor: .center)
            }
        }
    }

    private func step(by delta: Int, proxy: ScrollViewProxy) {
        guard let index = state.focusIndex else { return }
        let target = min(max(index + delta, 0), posters.count - 1)
        guard target != index else { return }
        withAnimation(HomeDesign.Motion.transform(reduceMotion: reduceMotion)) {
            proxy.scrollTo(posters[target].id, anchor: .center)
        }
    }
}
