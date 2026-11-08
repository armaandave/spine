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

/// Folds a closed tile's two back posters in behind the front one as the page scrolls: they straighten, slide in
/// under it, shrink and darken, one to one with how far the tile has scrolled off the top (`TileGeometry.foldProgress`),
/// and unfold as you scroll back. Only transforms and one brightness: nothing is laid out again, and no view is added.
///
/// A modifier of its own, so a scroll re-evaluates these few lines for each back poster and not the poster or the
/// tile. Its `gain` is 1 on a closed tile and 0 on an open one (and on the posters that do not fold), and animates
/// with the pose, so a tile that closes onto a scrolled page folds in as it lands. At 0 it reads no scroll.
struct PosterFold: ViewModifier, Animatable {
    var gain: CGFloat
    let glow: HomeGlowModel
    /// The back poster's tilt, which the fold takes out.
    let degrees: Double
    /// Where the back poster sits in the poster's frame (as a fraction of it), so it turns and shrinks about itself.
    let anchor: UnitPoint
    /// How far its center moves to sit behind the front poster.
    let slide: CGSize

    var animatableData: CGFloat {
        get { gain }
        set { gain = newValue }
    }

    func body(content: Content) -> some View {
        let progress = gain == 0
            ? 0
            : gain * TileGeometry.foldProgress(scroll: glow.scrollOffset, tilesBottom: glow.tilesBottom)
        content
            .brightness(Double(-TileGeometry.foldDim * progress))
            .scaleEffect(1 - (1 - TileGeometry.foldScale) * progress, anchor: anchor)
            .rotationEffect(.degrees(-degrees * progress), anchor: anchor)
            .offset(x: slide.width * progress, y: slide.height * progress)
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
    let glow: HomeGlowModel
    let onTap: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let isOpen = state.open == tile
        let focus = state.focusIndex ?? 0
        let rest = state.restIndex(tile)
        let role = TileGeometry.fanRole(index: index, rest: rest, count: count)
        let isFocused = isOpen && focus == index
        let pose = TileGeometry.pose(
            index: index, focus: focus, isOpen: isOpen, rest: rest, count: count, contentWidth: contentWidth
        )
        let openDelay = TileGeometry.openDelay(index: index, rest: rest)
        let media = poster.media
        // Where the poster sits in its 120 x 180 frame, as a fraction of it: the point its fold turns and shrinks about.
        let fanPose = TileGeometry.fanPose(index: index, rest: rest, count: count, contentWidth: contentWidth)
        let foldAnchor = UnitPoint(
            x: 0.5 + fanPose.offsetX / TileGeometry.posterSize.width,
            y: 0.5 + fanPose.offsetY / TileGeometry.posterSize.height
        )
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
        .frame(width: TileGeometry.posterSize.width, height: TileGeometry.posterSize.height)
        .modifier(PosterFold(
            gain: !isOpen && role.folds ? 1 : 0,
            glow: glow,
            degrees: fanPose.degrees,
            anchor: foldAnchor,
            slide: TileGeometry.foldSlide(role: role, contentWidth: contentWidth)
        ))
        // The innermost of these two that applies wins, so an open or a close (a flight) beats a change of focus
        // that happens in the same update. Opening is the spring after a stagger; closing is a zoom into the fan.
        .transaction(value: isOpen) { transaction in
            transaction.animation = isOpen
                ? TileMotion.flight(delay: openDelay, reduceMotion: reduceMotion)
                : TileMotion.close(role: role, reduceMotion: reduceMotion)
        }
        .transaction(value: isFocused) { transaction in
            transaction.animation = TileMotion.focus(reduceMotion: reduceMotion)
        }
        .opacity(pose.opacity)
        .animation(
            isOpen
                ? TileMotion.posterFade(delay: openDelay, reduceMotion: reduceMotion)
                : TileMotion.closeFade(reduceMotion: reduceMotion),
            value: pose.opacity
        )
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
    let glow: HomeGlowModel
    let contentWidth: CGFloat
    /// The focused poster was tapped: open its title.
    let onOpenTitle: (TilePoster) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var focusTicks = 0
    @State private var resting = RestingIndex()

    var body: some View {
        let isOpen = state.open == tile
        let count = posters.count
        let rest = state.restIndex(tile)
        ScrollViewReader { proxy in
            ScrollView(.horizontal) {
                HStack(spacing: TileGeometry.spacing) {
                    ForEach(Array(posters.enumerated()), id: \.element.id) { index, poster in
                        if TileGeometry.isMounted(
                            index: index, rest: rest, count: count, allMounted: state.isFullyMounted(tile)
                        ) {
                            TilePosterCell(
                                poster: poster,
                                index: index,
                                count: count,
                                tile: tile,
                                contentWidth: contentWidth,
                                state: state,
                                glow: glow,
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
            // The fractional offset, for the In progress panel to follow the finger. Written here and read nowhere
            // in this view, so a scroll frame redraws the panel and nothing else.
            .onScrollGeometryChange(for: Double.self) { geometry in
                let raw = TileGeometry.scrollPosition(
                    scrollX: geometry.contentOffset.x + geometry.contentInsets.leading,
                    count: count
                )
                // 1/512 of a poster: 0.26 pt, below one pixel.
                return (raw * 512).rounded() / 512
            } action: { _, position in
                guard state.open == tile else { return }
                var transaction = Transaction()
                transaction.disablesAnimations = true
                withTransaction(transaction) { state.scrollPosition = position }
            }
            .onAppear {
                if rest > 0 { placeAtRest(proxy: proxy) }
            }
            .onChange(of: isOpen) { _, _ in
                // The carousel stays on the title the tile rests on, open or closed: closing does not scroll it, so
                // the focused poster zooms out of where it is. A swipe still gliding when the tile closes is settled.
                placeAtRest(proxy: proxy)
            }
            .onChange(of: state.focusID) { _, _ in
                syncScroll(proxy: proxy)
            }
            .onChange(of: posters.map(\.id)) { _, _ in
                syncScroll(proxy: proxy)
                placeAtRest(proxy: proxy)
            }
            .onChange(of: rest) { _, _ in
                placeAtRest(proxy: proxy)
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

    /// A tile that is not open sits scrolled to the title it rests on (the fan is drawn from there), and a tile that is
    /// about to open starts there. At once, and not at all when it is already so.
    private func placeAtRest(proxy: ScrollViewProxy) {
        guard let id = state.restID(tile) else { return }
        // While open, the focus rules; the rest place only matters again once the tile has closed.
        if state.open == tile, state.focusID != id { return }
        resting.value = state.restIndex(tile)
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            proxy.scrollTo(id, anchor: .center)
        }
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
