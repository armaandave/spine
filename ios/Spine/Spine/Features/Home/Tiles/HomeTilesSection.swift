import SwiftUI

/// Home's two tiles, In progress and Trending, 168 pt tall when closed. Tap one and it springs open to the full width and 410 pt: the
/// other slides away, the tile's posters fly out of their fan into a row you swipe through, the glow behind the page
/// follows the poster you are on, and a panel at the bottom says where that title stands and offers its buttons.
///
/// Give it the page's content width (the screen less the 16 pt margins). It reads the model and the state only
/// inside its own views, so a swipe, a quick action or a reload redraws the tiles and never the page that holds them.
struct HomeTilesSection: View {
    let model: HomeTilesModel
    let state: HomeTilesState
    let glow: HomeGlowModel
    // Closures the page rebuilds every time it runs: they say nothing new (`StableInput`).
    @StableInput var actions: HomeTilesActions

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var contentWidth = TileGeometry.referenceContentWidth
    @State private var openTicks = 0

    var body: some View {
        let _ = PerfCounters.bump(.tilesBody)
        let height = TileGeometry.containerHeight(contentWidth: contentWidth, isOpen: state.open != nil)
        ZStack(alignment: .topLeading) {
            ForEach(HomeTileID.allCases, id: \.self) { tile in
                HomeTile(
                    tile: tile,
                    contentWidth: contentWidth,
                    model: model,
                    state: state,
                    glow: glow,
                    actions: actions,
                    onOpen: { openTile(tile) },
                    onClose: closeTile
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(height: height, alignment: .top)
        .animation(TileMotion.frame(closing: state.open == nil, reduceMotion: reduceMotion), value: height)
        .onGeometryChange(for: CGFloat.self) { proxy in
            proxy.size.width
        } action: { width in
            if width > 0 { contentWidth = width }
        }
        .modifier(HomeGlowTilesAnchor(glow: glow))
        .background {
            TilesSync(model: model, state: state, glow: glow)
        }
        // The tiles are dark art whatever the phone is set to.
        .environment(\.colorScheme, .dark)
        .sensoryFeedback(.impact(weight: .light), trigger: openTicks)
        .onChange(of: state.open) { old, new in
            if new == nil, let old {
                model.railStore?.tileClosed()
                actions.onTileClosed(old)
            }
        }
        .task(id: state.open) {
            // A tile that has closed keeps all its posters mounted until they have flown home.
            guard state.open == nil else { return }
            try? await Task.sleep(for: TileMotion.unmountDelay)
            if !Task.isCancelled { state.finishClosing() }
        }
    }

    // MARK: Open and close

    private func openTile(_ tile: HomeTileID) {
        guard state.open == nil, model.face(for: tile) == .items else { return }
        openTicks += 1
        let posters = model.posters(for: tile)
        let rest = state.restIndex(tile)
        HomeGlow.prefetch(HomeGlow.neighbors(of: rest, in: posters.map(\.media)))
        if tile == .trending, rest < posters.count, let first = model.trendingTitle(id: posters[rest].id) {
            // The rail's state for the title the tile opens on and the next, fetched while the tile opens.
            model.railStore?.focus(first, neighbors: model.trendingNeighbors(of: first), neighborDelay: .zero)
        }
        if posters.count > TileGeometry.fanMountedCount {
            TilePosterArt.prefetch(posters, at: TileGeometry.openPrefetchIndexes(rest: rest, count: posters.count))
        }
        state.requestOpen(
            tile,
            posterCount: posters.count,
            animation: TileMotion.frame(closing: false, reduceMotion: reduceMotion),
            defersMount: !reduceMotion
        )
    }

    private func closeTile() {
        state.requestClose(animation: TileMotion.frame(closing: true, reduceMotion: reduceMotion))
    }
}

/// Keeps `HomeTilesState` and Home's glow in step with the model, from a view of its own so that the section's body
/// does not depend on any of it.
private struct TilesSync: View {
    let model: HomeTilesModel
    let state: HomeTilesState
    let glow: HomeGlowModel

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let inProgressIDs = model.ids(for: .inProgress)
        let trendingIDs = model.ids(for: .trending)
        // What the glow is showing: the focused poster, else the front poster of the In progress fan.
        let glowTargets = [
            state.focusedMedia(model: model),
            state.frontMedia(.inProgress, model: model),
        ]
        Color.clear
            .frame(width: 0, height: 0)
            .onChange(of: inProgressIDs, initial: true) { _, ids in
                sync(ids, for: .inProgress)
            }
            .onChange(of: trendingIDs, initial: true) { _, ids in
                sync(ids, for: .trending)
            }
            .onChange(of: glowTargets, initial: true) { _, _ in
                state.syncGlow(glow, model: model)
            }
            .accessibilityHidden(true)
    }

    /// A list that empties under an open tile collapses it, which moves the page, so that runs animated too.
    private func sync(_ ids: [String], for tile: HomeTileID) {
        if ids.isEmpty, state.open == tile {
            withAnimation(TileMotion.frame(closing: true, reduceMotion: reduceMotion)) { state.sync(ids, for: tile) }
        } else {
            state.sync(ids, for: tile)
        }
    }
}

// MARK: - One tile

private struct HomeTile: View {
    let tile: HomeTileID
    let contentWidth: CGFloat
    let model: HomeTilesModel
    let state: HomeTilesState
    let glow: HomeGlowModel
    let actions: HomeTilesActions
    let onOpen: () -> Void
    let onClose: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let isOpen = state.open == tile
        // Nothing open: the tiles are closing (or at rest), and everything that moves does so at the close's pace.
        let closing = state.open == nil
        let frame = TileGeometry.frame(tile: tile.index, open: state.open?.index, contentWidth: contentWidth)
        let face = model.face(for: tile)
        let posters = model.posters(for: tile)
        let shape = RoundedRectangle(cornerRadius: HomeDesign.Metric.tileCorner, style: .continuous)

        // The content is laid out once, at the open size, and clipped by a container whose frame animates; so the
        // posters, the label and the panel are never re-laid-out as the tile grows.
        content(isOpen: isOpen, face: face, posters: posters)
            .frame(width: contentWidth, height: HomeDesign.Metric.tileOpenHeight, alignment: .topLeading)
            .overlay(alignment: .topTrailing) {
                if isOpen {
                    TileCloseButton(label: "Collapse \(tile.title)", action: onClose)
                        .padding(TileGeometry.closeInset)
                        .transition(.asymmetric(
                            insertion: .scale(scale: 0.5).combined(with: .opacity)
                                .animation(TileMotion.closePop(reduceMotion: reduceMotion)),
                            removal: .opacity.animation(.easeOut(duration: 0.077))
                        ))
                }
            }
            .frame(width: frame.width, height: frame.height, alignment: .topLeading)
            .background(TileStyle.veil)
            .clipShape(shape)
            .overlay { TileOutline(shape: shape) }
            .overlay { tapTarget(face: face, shape: shape) }
            .animation(TileMotion.frame(closing: closing, reduceMotion: reduceMotion), value: [frame.width, frame.height])
            .scaleEffect(frame.scale)
            .offset(x: frame.slide)
            .animation(TileMotion.slide(closing: closing, reduceMotion: reduceMotion), value: frame.scale)
            .opacity(frame.opacity)
            .animation(TileMotion.fade(closing: closing, reduceMotion: reduceMotion), value: frame.opacity)
            .offset(x: frame.left)
            .animation(TileMotion.frame(closing: closing, reduceMotion: reduceMotion), value: frame.left)
            .zIndex(frame.z)
            .allowsHitTesting(frame.hitTestable)
            // The escape action goes on a container. On a plain view it stretches its child buttons' accessibility
            // frames (the closed tile's button reads 620 x 294, so a UI test tapping its center taps the other tile).
            .accessibilityElement(children: .contain)
            .accessibilityAction(.escape) {
                if isOpen { onClose() }
            }
    }

    // MARK: Layers

    @ViewBuilder
    private func content(isOpen: Bool, face: TileFace, posters: [TilePoster]) -> some View {
        let side = TileGeometry.tileSide(contentWidth: contentWidth)
        ZStack(alignment: .topLeading) {
            switch face {
            case .items:
                TileArt(
                    // The blurred art behind the fan follows its front poster.
                    url: front(of: posters).flatMap { TileURL.make($0.media.displayPosterURL) },
                    side: side,
                    height: TileGeometry.closedHeight,
                    contentWidth: contentWidth,
                    isOpen: isOpen
                )
                TileCarousel(
                    tile: tile,
                    posters: posters,
                    state: state,
                    glow: glow,
                    contentWidth: contentWidth,
                    onOpenTitle: openTitle
                )
            case .loading:
                TileSkeletonFace(contentWidth: contentWidth)
            case .offer:
                TileOfferGlyph(symbol: tile == .inProgress ? "plus" : "person.badge.plus", side: side)
            case .message:
                EmptyView()
            }
            TileLabel(
                title: tile.title,
                subtitle: isOpen && face == .items ? "Swipe to browse" : model.subtitle(for: tile),
                isOpen: isOpen,
                closedWidth: side - 2 * TileGeometry.labelLeading
            )
            if isOpen {
                TileDetailPanel(tile: tile, model: model, state: state, actions: actions)
                    .modifier(TilePanelPlacement(tile: tile, contentWidth: contentWidth))
                    .transition(.asymmetric(
                        insertion: .offset(y: 12).combined(with: .opacity)
                            .animation(TileMotion.panelRise(reduceMotion: reduceMotion)),
                        removal: .opacity.animation(.easeOut(duration: 0.077))
                    ))
            }
        }
    }

    /// The poster in front of the fan: the title the tile rests on.
    private func front(of posters: [TilePoster]) -> TilePoster? {
        let index = state.restIndex(tile)
        return posters.indices.contains(index) ? posters[index] : nil
    }

    /// The whole closed tile is one button: it opens, or (for an empty tile) offers what to do about it.
    @ViewBuilder
    private func tapTarget(face: TileFace, shape: RoundedRectangle) -> some View {
        switch face {
        case .items:
            Button(action: onOpen) {
                TileTapArea(shape: shape)
            }
            .buttonStyle(TileTapStyle())
            .allowsHitTesting(state.open == nil)
            .accessibilityHidden(state.open != nil)
            .accessibilityLabel(model.openLabel(for: tile))
            .accessibilityIdentifier(tile.identifier)
        case let .offer(words):
            Button(action: offerTapped) {
                TileTapArea(shape: shape)
            }
            .buttonStyle(TileTapStyle())
            .accessibilityHidden(state.open != nil)
            .accessibilityLabel("\(tile.title), \(words)")
            .accessibilityIdentifier(tile.identifier)
        case .loading:
            Color.clear
                .accessibilityElement(children: .ignore)
                .accessibilityHidden(state.open != nil)
                .accessibilityLabel("Loading \(tile.title)")
                .accessibilityIdentifier(tile.identifier)
                .allowsHitTesting(false)
        case let .message(words):
            Color.clear
                .accessibilityElement(children: .ignore)
                .accessibilityHidden(state.open != nil)
                .accessibilityLabel("\(tile.title), \(words)")
                .accessibilityIdentifier(tile.identifier)
                .allowsHitTesting(false)
        }
    }

    private func openTitle(_ poster: TilePoster) {
        switch tile {
        case .inProgress:
            actions.onOpenTitle(poster.media.ref)
        case .trending:
            if let title = model.trendingTitle(id: poster.id) { actions.onOpenTrending(title) }
        }
    }

    private func offerTapped() {
        switch tile {
        case .inProgress: actions.onFindSomething()
        case .trending: actions.onFindPeople()
        }
    }
}

// MARK: - Pieces

/// A URL for a string that may be missing or blank.
private enum TileURL {
    static func make(_ string: String?) -> URL? {
        guard let string, !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return URL(string: string)
    }
}

/// The mock's `inset 0 0 0 0.5px rgba(255,255,255,0.26), inset 0 1px 0 rgba(255,255,255,0.2)`: a half-point line round
/// the edge and a one-point highlight along the top, as two solid shapes. Both are solid colors, because a gradient
/// stroke is rasterized by Core Graphics on the main thread every frame the tile's size changes (the open and the
/// close), which was the biggest single cost of both.
struct TileOutline: View {
    let shape: RoundedRectangle
    /// The shape's corner radius, for the highlight's crescent (Up next's tile is rounder than the tiles').
    var corner: CGFloat = HomeDesign.Metric.tileCorner

    var body: some View {
        ZStack(alignment: .top) {
            shape.strokeBorder(TileStyle.stroke, lineWidth: TileStyle.strokeWidth)
            TileTopHighlight(corner: corner)
                .fill(TileStyle.highlight)
                // The crescent lives in the corner's height; a strip that tall is all that is ever redrawn.
                .frame(height: corner + TileStyle.highlightWidth)
        }
        .opacity(TileStyle.outlineStrength)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The part of the tile's outline a copy of itself moved down by the highlight's width leaves uncovered: a crescent
/// one point thick along the top that thins away round the corners. It is drawn in a strip as tall as the corner, so
/// the outline is taken from a rectangle taller than the strip; only the top corners matter and they do not depend on
/// the height.
private struct TileTopHighlight: Shape {
    var corner: CGFloat = HomeDesign.Metric.tileCorner

    func path(in rect: CGRect) -> Path {
        let full = CGRect(
            x: rect.minX,
            y: rect.minY,
            width: rect.width,
            height: rect.height + 2 * corner
        )
        let outline = RoundedRectangle(cornerRadius: corner, style: .continuous).path(in: full)
        return outline.subtracting(outline.offsetBy(dx: 0, dy: TileStyle.highlightWidth))
    }
}

/// The first title's art, already blurred, saturated and darkened in the image cache, filling the closed tile. Opening
/// stretches it with the tile and fades it out, so the glow behind shows through: it is only ever scaled and faded.
private struct TileArt: View {
    let url: URL?
    /// The closed tile's width and height.
    let side: CGFloat
    let height: CGFloat
    let contentWidth: CGFloat
    let isOpen: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let style = BlurredArt.Style.tile
        ZStack {
            TileStyle.artBase
            SpineAsyncImage(
                url: url,
                thumbnailSize: style.thumbnailSize,
                processors: [style.processor]
            ) { phase in
                if case let .success(image) = phase {
                    image
                        .resizable()
                        .interpolation(.high)
                        .scaledToFill()
                } else {
                    Color.clear
                }
            }
            // The feed card's sheen on the art, as the card has it: under the posters and the words, so only the glass
            // catches the light. It stretches and fades with the art as the tile opens.
            FeedCardSheen()
        }
        .frame(width: side, height: height)
        .clipped()
        .scaleEffect(
            x: isOpen ? contentWidth / side : 1,
            y: isOpen ? HomeDesign.Metric.tileOpenHeight / height : 1,
            anchor: .topLeading
        )
        .animation(TileMotion.frame(closing: !isOpen, reduceMotion: reduceMotion), value: isOpen)
        .opacity(isOpen ? 0 : 1)
        .animation(TileMotion.art(closing: !isOpen, reduceMotion: reduceMotion), value: isOpen)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The title and the line under it. The title is drawn at 16 and scaled to 24 about its top-leading corner as the tile
/// opens; both only move.
private struct TileLabel: View {
    let title: String
    let subtitle: String
    let isOpen: Bool
    let closedWidth: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack(alignment: .topLeading) {
            Text(title)
                .font(HomeDesign.Text.tileTitle)
                .tracking(-0.3)
                .foregroundStyle(.primary)
                .lineLimit(1)
                .frame(maxWidth: closedWidth, alignment: .leading)
                .scaleEffect(isOpen ? TileGeometry.titleOpenScale : 1, anchor: .topLeading)
                .offset(x: TileGeometry.labelLeading, y: TileGeometry.titleTop(isOpen: isOpen))
                .accessibilityAddTraits(.isHeader)
            Text(subtitle)
                .font(HomeDesign.Text.tileSubtitle)
                .foregroundStyle(TileStyle.subtitle)
                .lineLimit(1)
                .contentTransition(.identity)
                .frame(maxWidth: closedWidth, alignment: .leading)
                .offset(x: TileGeometry.labelLeading, y: TileGeometry.subtitleTop(isOpen: isOpen))
        }
        .animation(TileMotion.frame(closing: !isOpen, reduceMotion: reduceMotion), value: isOpen)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .allowsHitTesting(false)
        .accessibilityHidden(!isOpen)
    }
}

/// The empty tile's glyph, where the fan would be.
private struct TileOfferGlyph: View {
    let symbol: String
    let side: CGFloat

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 22, weight: .bold))
            .foregroundStyle(.primary)
            .frame(width: 56, height: 56)
            .background(Circle().fill(Color.white.opacity(0.12)))
            .offset(x: (side - 56) / 2, y: 36)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// The glass Close button. The glass is on the button itself, with a circle to tap, and the label says Collapse so
/// the progress sheet's own Close stays the only Close.
private struct TileCloseButton: View {
    let label: String
    let action: () -> Void

    @State private var ticks = 0

    var body: some View {
        Button {
            ticks += 1
            action()
        } label: {
            Image(systemName: "xmark")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.primary)
                .frame(width: TileGeometry.closeSize, height: TileGeometry.closeSize)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .circle)
        .accessibilityLabel(label)
        .accessibilityIdentifier("home.tile.close")
        .sensoryFeedback(.impact(weight: .light), trigger: ticks)
    }
}

/// What a tap on a closed tile lands on. The shape is the label's own content shape: a clear fill takes no touches,
/// and a content shape put on the Button from outside does not reach through a custom button style.
private struct TileTapArea: View {
    let shape: RoundedRectangle

    var body: some View {
        Color.clear.contentShape(shape)
    }
}

/// A closed tile's tap target adds no highlight; the tile itself is the feedback, by springing open.
private struct TileTapStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
    }
}
