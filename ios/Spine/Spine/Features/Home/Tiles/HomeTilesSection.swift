import SwiftUI

/// Home's two tiles, In progress and Trending, as one row 196 pt tall when closed: a strip of posters across the whole
/// screen, split by the seam, each side with its title over it. Tap a half and it opens to 410 pt: the other half slides
/// away, the tile's posters fly out of the strip into a row you swipe through, the seam glides to the edge, the glow
/// behind the page follows the poster you are on, and a panel at the bottom says where that title stands and offers its
/// buttons.
///
/// Give it the page's content width (the screen less the 16 pt margins). The row spans the screen, so it draws 16 pt
/// past the section on each side. It reads the model and the state only inside its own views, so a swipe, a quick
/// action or a reload redraws the tiles and never the page that holds them.
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
        let height = TileGeometry.containerHeight(isOpen: state.open != nil)
        // The section takes the width the page offers and measures that; the tiles draw in an overlay that never
        // sizes it. Each tile is as wide as the measured width, so if they sized the section, a phone narrower than
        // the stand-in width would keep the stand-in for good.
        Color.clear
            .frame(maxWidth: .infinity)
            .frame(height: height, alignment: .top)
            .overlay(alignment: .topLeading) {
                ZStack(alignment: .topLeading) {
                    // Under the tiles: a poster in flight covers the line and never has it drawn across its face.
                    HomeSeam(state: state, contentWidth: contentWidth)
                        .zIndex(0)
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
            }
        .animation(TileMotion.frame(closing: state.open == nil, reduceMotion: reduceMotion), value: height)
        .onGeometryChange(for: CGFloat.self) { proxy in
            proxy.size.width
        } action: { width in
            guard width > 0 else { return }
            // Taken at once: the tiles' frames animate, and they would grow from the stand-in width to the page's.
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) { contentWidth = width }
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
        guard state.open == nil, model.shownFace(for: tile) == .items else { return }
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
        let frame = TileGeometry.frame(tile: tile.index, open: state.open?.index)
        // What the data says, and what the tile shows: titles stay behind the loading look until revealed.
        let dataFace = model.face(for: tile)
        let face = model.shownFace(for: tile)
        let posters = model.posters(for: tile)

        // The content is laid out once, at the open size, in a frame whose height animates; so the posters, the label
        // and the panel are never re-laid-out as the row grows. Nothing clips it: the strip runs off the screen's edges.
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
            .frame(width: contentWidth, height: frame.height, alignment: .topLeading)
            .overlay(alignment: .topLeading) { tapTarget(face: face) }
            .animation(TileMotion.frame(closing: closing, reduceMotion: reduceMotion), value: frame.height)
            .offset(x: frame.slide)
            .animation(TileMotion.slide(closing: closing, reduceMotion: reduceMotion), value: frame.slide)
            .opacity(frame.opacity)
            .animation(TileMotion.fade(closing: closing, reduceMotion: reduceMotion), value: frame.opacity)
            .zIndex(frame.z)
            .allowsHitTesting(frame.hitTestable)
            // The escape action goes on a container. On a plain view it stretches its child buttons' accessibility
            // frames (the closed tile's button reads 620 x 294, so a UI test tapping its center taps the other tile).
            .accessibilityElement(children: .contain)
            .accessibilityAction(.escape) {
                if isOpen { onClose() }
            }
            .task(id: dataFace) { await reveal(dataFace) }
    }

    /// Titles that have just arrived show once the strip's posters are in the image cache (a second at most), all
    /// fading in together instead of one by one.
    private func reveal(_ face: TileFace) async {
        guard face == .items, !model.revealed.contains(tile) else { return }
        await TileReveal.warm(TileReveal.requests(for: model.posters(for: tile), rest: state.restIndex(tile)))
        guard !Task.isCancelled else { return }
        withAnimation(TileMotion.reveal(reduceMotion: reduceMotion)) {
            _ = model.revealed.insert(tile)
        }
    }

    // MARK: Layers

    @ViewBuilder
    private func content(isOpen: Bool, face: TileFace, posters: [TilePoster]) -> some View {
        ZStack(alignment: .topLeading) {
            switch face {
            case .items:
                TileCarousel(
                    tile: tile,
                    posters: posters,
                    state: state,
                    glow: glow,
                    contentWidth: contentWidth,
                    onOpenTitle: openTitle
                )
                TileEmptySlots(tile: tile, count: posters.count, contentWidth: contentWidth, isOpen: isOpen)
            case .loading:
                // The labels and the seam only: nothing stands in for the posters.
                EmptyView()
            case .offer:
                TileOfferGlyph(symbol: tile == .inProgress ? "plus" : "person.badge.plus", tile: tile, contentWidth: contentWidth)
                TileEmptySlots(tile: tile, count: 0, contentWidth: contentWidth, isOpen: isOpen)
            case .message:
                EmptyView()
            }
            // Above the posters: closed it stands under them, and it flies up across the row as the tile opens.
            TileLabel(
                tile: tile,
                title: tile.title,
                subtitle: face == .loading ? "" : model.subtitle(for: tile),
                showsBrowseHint: face == .items,
                isOpen: isOpen,
                yielding: state.open == .trending && tile == .inProgress,
                contentWidth: contentWidth
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

    /// The closed row's half on this tile's side of the seam is one button, from the screen's edge to the seam: it
    /// opens, or (for an empty tile) offers what to do about it.
    @ViewBuilder
    private func tapTarget(face: TileFace) -> some View {
        switch face {
        case .items:
            Button(action: onOpen) {
                TileTapArea()
            }
            .buttonStyle(TileTapStyle())
            .modifier(TileHalf(tile: tile, contentWidth: contentWidth))
            .allowsHitTesting(state.open == nil)
            .accessibilityHidden(state.open != nil)
            .accessibilityLabel(model.openLabel(for: tile))
            .accessibilityIdentifier(tile.identifier)
        case let .offer(words):
            Button(action: offerTapped) {
                TileTapArea()
            }
            .buttonStyle(TileTapStyle())
            .modifier(TileHalf(tile: tile, contentWidth: contentWidth))
            .accessibilityHidden(state.open != nil)
            .accessibilityLabel("\(tile.title), \(words)")
            .accessibilityIdentifier(tile.identifier)
        case .loading:
            Color.clear
                .modifier(TileHalf(tile: tile, contentWidth: contentWidth))
                .accessibilityElement(children: .ignore)
                .accessibilityHidden(state.open != nil)
                .accessibilityLabel("Loading \(tile.title)")
                .accessibilityIdentifier(tile.identifier)
                .allowsHitTesting(false)
        case let .message(words):
            Color.clear
                .modifier(TileHalf(tile: tile, contentWidth: contentWidth))
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
enum TileURL {
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

/// The title and the line under it. Closed, In progress's pair is right-aligned and ends at the seam, Trending's
/// starts there, both with a white glow; the title is drawn at 17 and scaled to 24 about its top-leading corner as the
/// tile opens, when the pair moves to the page's margin and the glow goes. Everything only moves.
private struct TileLabel: View {
    let tile: HomeTileID
    let title: String
    /// The count under the title, shown while the tile is closed.
    let subtitle: String
    /// Whether the open tile says "Swipe to browse" under the title instead of the count.
    let showsBrowseHint: Bool
    let isOpen: Bool
    /// In progress while Trending is open: Trending's label travels left across this one, so this one stays out of the way.
    let yielding: Bool
    let contentWidth: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var widths = (title: CGFloat(0), subtitle: CGFloat(0), browse: CGFloat(0))
    /// `swapped` as the two lines' opacities see it. It changes under its own explicit animation, so the swap's short
    /// fade can never be taken up by the lines' travel (an implicit animation on their opacity did exactly that: the
    /// count and the hint jumped to the open spot in the first frames while the title was still on its way).
    @State private var countHidden = false
    @State private var hintShown = false

    private func setWidth(_ width: CGFloat, _ key: WritableKeyPath<(title: CGFloat, subtitle: CGFloat, browse: CGFloat), CGFloat>) {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) { widths[keyPath: key] = width }
    }

    var body: some View {
        // In progress's pair ends at the seam while closed; its x is worked out from the measured text width, so it
        // glides to the open spot instead of snapping with a changed frame alignment.
        let rightAligned = tile == .inProgress && !isOpen
        let left = TileGeometry.labelLeft(tile: tile.index, isOpen: isOpen, contentWidth: contentWidth)
        let swapped = isOpen && showsBrowseHint
        ZStack(alignment: .topLeading) {
            Text(title)
                .font(HomeDesign.Text.tileTitle)
                .tracking(-0.3)
                .foregroundStyle(.primary)
                .shadow(color: .white.opacity(isOpen ? 0 : 0.30), radius: 6)
                .shadow(color: .white.opacity(isOpen ? 0 : 0.16), radius: 16)
                .lineLimit(1)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { setWidth($0, \.title) }
                .scaleEffect(isOpen ? TileGeometry.titleOpenScale : 1, anchor: .topLeading)
                .offset(x: rightAligned ? left + TileGeometry.labelWidth - widths.title : left, y: TileGeometry.titleTop(isOpen: isOpen))
                .accessibilityAddTraits(.isHeader)
            // The count and the hint are two texts, each placed by its own width, so the hint (wider than the count)
            // never runs past the seam as the tile opens: it is right-aligned like the count, and both only fade.
            Text(subtitle)
                .font(HomeDesign.Text.tileSubtitle)
                .foregroundStyle(isOpen ? TileStyle.subtitle : TileStyle.subtitleRest)
                .lineLimit(1)
                .contentTransition(.identity)
                // A count that arrives with the posters fades in with them.
                .opacity(subtitle.isEmpty || countHidden ? 0 : 1)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { setWidth($0, \.subtitle) }
                .offset(x: rightAligned ? left + TileGeometry.labelWidth - widths.subtitle : left, y: TileGeometry.subtitleTop(isOpen: isOpen))
            Text("Swipe to browse")
                .font(HomeDesign.Text.tileSubtitle)
                .foregroundStyle(TileStyle.subtitle)
                .lineLimit(1)
                .opacity(hintShown ? 1 : 0)
                .fixedSize()
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { setWidth($0, \.browse) }
                .offset(x: rightAligned ? left + TileGeometry.labelWidth - widths.browse : left, y: TileGeometry.subtitleTop(isOpen: isOpen))
                .accessibilityHidden(!swapped)
        }
        .animation(TileMotion.frame(closing: !isOpen, reduceMotion: reduceMotion), value: isOpen)
        .onAppear {
            countHidden = swapped
            hintShown = swapped
        }
        .onChange(of: swapped) { _, now in
            // Each line has its own animation: the one that leaves goes first, the one that arrives follows.
            withAnimation(TileMotion.labelSwap(appearing: !now, reduceMotion: reduceMotion)) { countHidden = now }
            withAnimation(TileMotion.labelSwap(appearing: now, reduceMotion: reduceMotion)) { hintShown = now }
        }
        .opacity(yielding ? 0 : 1)
        .animation(TileMotion.labelYield(yielding: yielding, reduceMotion: reduceMotion), value: yielding)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .allowsHitTesting(false)
        .accessibilityHidden(!isOpen)
    }
}

/// The hairline outlines that hold the places no title fills, so the strip keeps its rhythm. They fade out as the
/// tile opens.
private struct TileEmptySlots: View {
    let tile: HomeTileID
    let count: Int
    let contentWidth: CGFloat
    let isOpen: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(TileGeometry.emptyRoles(count: count), id: \.self) { role in
                let fan = HomeDesign.Metric.fan(
                    index: role, side: TileGeometry.side(tile: tile.index), contentWidth: contentWidth
                )
                RoundedRectangle(cornerRadius: HomeDesign.Metric.stripCorner, style: .continuous)
                    .strokeBorder(TileStyle.emptySlot, lineWidth: 1)
                    .frame(width: fan.width, height: fan.height)
                    .offset(x: fan.x, y: fan.y)
            }
        }
        .opacity(isOpen ? 0 : 1)
        .animation(HomeDesign.Motion.glowCrossfade(reduceMotion: reduceMotion), value: isOpen)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The empty tile's glyph, centered in the place beside the seam where the first poster would be.
private struct TileOfferGlyph: View {
    let symbol: String
    let tile: HomeTileID
    let contentWidth: CGFloat

    var body: some View {
        let fan = HomeDesign.Metric.fan(
            index: 0, side: TileGeometry.side(tile: tile.index), contentWidth: contentWidth
        )
        Image(systemName: symbol)
            .font(.system(size: 22, weight: .bold))
            .foregroundStyle(.primary)
            .frame(width: 56, height: 56)
            .background(Circle().fill(Color.white.opacity(0.12)))
            .offset(x: fan.x + (fan.width - 56) / 2, y: fan.y + (fan.height - 56) / 2)
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

/// What a tap on a closed row lands on: a clear fill takes no touches, so it has a content shape of its own.
private struct TileTapArea: View {
    var body: some View {
        Color.clear.contentShape(Rectangle())
    }
}

/// Puts a tile's tap target on its side of the seam: the row's height, from the screen's edge to the middle of the
/// page (the screen reaches a page margin past the section on each side).
private struct TileHalf: ViewModifier {
    let tile: HomeTileID
    let contentWidth: CGFloat

    func body(content: Content) -> some View {
        let margin = HomeDesign.Metric.pageMargin
        content
            .frame(width: contentWidth / 2 + margin, height: TileGeometry.closedHeight)
            .offset(x: tile == .inProgress ? -margin : contentWidth / 2)
    }
}

/// A closed tile's tap target adds no highlight; the row itself is the feedback, by opening.
private struct TileTapStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
    }
}
