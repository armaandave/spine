import SwiftUI

/// What an open tile's buttons do. The section is handed these by the page; the tiles write nothing themselves.
struct HomeTilesActions {
    /// A poster in the In progress carousel was tapped: open the title's page.
    var onOpenTitle: (MediaRef) -> Void = { _ in }
    /// "Update" (or "Open" for a title whose page is its progress UI): the title's own progress UI.
    var onUpdate: (LibraryItem) -> Void = { _ in }
    /// The main button's write (an episode, 20 pages, 5 percent). Returns whether it was saved, which picks the
    /// success or the error haptic. A plan that would reach the end of the title never gets here: the section opens
    /// the progress UI for it through `onUpdate`.
    var onQuickAction: (LibraryItem) async -> Bool = { _ in true }
    /// A poster in the Trending carousel was tapped, or the rail's Open or the faces: the title's page.
    var onOpenTrending: (TrendingTitle) -> Void = { _ in }
    /// A Trending rail tap that needs the title's own screen (a log composer, a sheet): the page opens and finishes it.
    var onRailHandoff: (TrendingTitle, RailHandoff) -> Void = { _, _ in }
    /// Something a Trending rail write has to say (a notice, a failure): Home's alert shows it.
    var onRailMessage: (String) -> Void = { _ in }
    /// A tile has closed: the page re-sorts what a Trending rail write changed.
    var onTileClosed: (HomeTileID) -> Void = { _ in }
    /// The empty In progress tile's button.
    var onFindSomething: () -> Void = {}
    /// The empty Trending tile's button, shown when you follow nobody.
    var onFindPeople: () -> Void = {}
}

/// The panel at the foot of an open tile: the focused title's name and where it stands, its meter, and two buttons.
/// It reads the focused id here and nowhere above, so swiping redraws this panel and the posters, not the page.
struct TileDetailPanel: View {
    let tile: HomeTileID
    let model: HomeTilesModel
    let state: HomeTilesState
    let actions: HomeTilesActions

    var body: some View {
        switch tile {
        case .inProgress:
            if let id = state.focusID, let item = model.item(id: id) {
                InProgressPanel(
                    item: item,
                    detail: model.detail(for: item),
                    busy: model.busyItemIDs.contains(item.id),
                    actions: actions
                )
                .id(item.id)
            }
        case .trending:
            if let id = state.focusID, let title = model.trendingTitle(id: id) {
                // No `.id` on the panel: the rail's glass stays put as you swipe; its own title block is keyed.
                TrendingPanel(
                    title: title,
                    detail: model.trendingDetail(for: title),
                    neighbors: model.trendingNeighbors(of: title),
                    store: model.railStore,
                    actions: actions
                )
            }
        }
    }
}

/// Where a tile's panel sits inside the tile. The In progress panel hangs under the posters, inset 16 pt; the
/// Trending panel is a full-width, full-height layer that places its title block and anchors its rail to the tile's
/// foot, so a different tile height only moves the rail.
struct TilePanelPlacement: ViewModifier {
    let tile: HomeTileID
    let contentWidth: CGFloat

    @ViewBuilder
    func body(content: Content) -> some View {
        switch tile {
        case .inProgress:
            content
                .frame(width: contentWidth - 2 * TileGeometry.panelInset)
                .padding(.leading, TileGeometry.panelInset)
                .padding(.top, TileGeometry.panelTop)
        case .trending:
            content
                .frame(width: contentWidth, height: HomeDesign.Metric.tileOpenHeight, alignment: .top)
        }
    }
}

// MARK: - In progress

private struct InProgressPanel: View {
    let item: LibraryItem
    let detail: TileDetailModel
    let busy: Bool
    let actions: HomeTilesActions

    @State private var updateTicks = 0
    @State private var successTicks = 0
    @State private var failureTicks = 0
    @State private var isRunning = false

    var body: some View {
        let accent = ArtAccent.pastel(fromHex: detail.accentHex)
        VStack(alignment: .leading, spacing: 0) {
            // Where the title stands now. Today's part is the meter's lit stretch, not a "+6 chapters" that reads as a
            // random number when the last update was days ago.
            TilePanelTitleRow(title: detail.title, accessibilityLabel: detail.accessibilityLabel) {
                if !detail.whereText.isEmpty {
                    Text(detail.whereText)
                }
            }
            meter(accent: accent)
                .padding(.top, 16)
                .frame(height: TileGeometry.panelMeterBlock, alignment: .top)
            buttons
                .padding(.top, 12)
        }
        .sensoryFeedback(.impact, trigger: updateTicks)
        .sensoryFeedback(.success, trigger: successTicks)
        .sensoryFeedback(.error, trigger: failureTicks)
    }

    // MARK: Meter

    @ViewBuilder
    private func meter(accent: Color) -> some View {
        if let model = TileGeometry.panelMeter(detail.meter) {
            let palette = TileStyle.meterPalette(accent: accent)
            switch model.kind {
            case .bar:
                ProgressMeterView.bar(model: model, palette: palette, height: TileStyle.tickHeight, radius: 4)
            case .ticks, .window:
                TilePanelTicks(model: model, palette: palette)
            }
        } else {
            Color.clear.frame(height: TileStyle.tickHeight)
        }
    }

    // MARK: Buttons

    /// A plan that only opens the progress UI and a secondary button with the same words would be two buttons for
    /// one thing: they become one.
    private var primaryIsRedundant: Bool {
        if case let .openSheet(label)? = detail.primary { return label == detail.secondaryTitle }
        return false
    }

    @ViewBuilder
    private var buttons: some View {
        HStack(spacing: 10) {
            if let primary = detail.primary {
                TilePillButton(
                    title: primary.label,
                    style: .primary,
                    identifier: primaryIsRedundant ? "in-progress.update" : "in-progress.quick",
                    isDisabled: busy || isRunning
                ) {
                    primaryTapped(primary)
                }
            }
            if !primaryIsRedundant {
                TilePillButton(
                    title: detail.secondaryTitle,
                    style: detail.primary == nil ? .primary : .secondary,
                    identifier: "in-progress.update",
                    isDisabled: false
                ) {
                    updateTicks += 1
                    actions.onUpdate(item)
                }
            }
        }
    }

    private func primaryTapped(_ plan: QuickActionPlan) {
        guard plan.writes else {
            updateTicks += 1
            actions.onUpdate(item)
            return
        }
        guard !isRunning else { return }
        isRunning = true
        let item = item
        let run = actions.onQuickAction
        Task {
            let saved = await run(item)
            if saved { successTicks += 1 } else { failureTicks += 1 }
            isRunning = false
        }
    }
}

// MARK: - Shared pieces

/// The panel's top row: the title at 19, and the other words at 13, on one baseline. The title gives way first. In
/// progress's has its title at the left and its words at the right; Trending's is `centered`, the title and its words
/// together in the middle.
private struct TilePanelTitleRow<Aside: View>: View {
    let title: String
    let accessibilityLabel: String
    var centered = false
    @ViewBuilder let aside: () -> Aside

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title)
                .font(HomeDesign.Text.panelTitle)
                .tracking(-0.3)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: centered ? nil : .infinity, alignment: .leading)
            aside()
                .font(TileStyle.panelAside)
                .foregroundStyle(TileStyle.panelSecondary)
                .lineLimit(1)
                .fixedSize()
                .layoutPriority(1)
        }
        .frame(maxWidth: .infinity)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityAddTraits(.isHeader)
    }
}

/// Ten-point ticks for a short run of episodes, chapters or issues, or a 20-tick window of a long series with its
/// left edge fading out. Display only: a tick colors when the number moves, over 0.3 s.
private struct TilePanelTicks: View, Equatable {
    let model: ProgressMeterModel
    let palette: MeterPalette

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static func == (lhs: TilePanelTicks, rhs: TilePanelTicks) -> Bool {
        lhs.model == rhs.model && lhs.palette == rhs.palette
    }

    var body: some View {
        let count = model.tickCount
        let fades = isWindow
        HStack(spacing: TileStyle.tickGap) {
            ForEach(0 ..< count, id: \.self) { index in
                RoundedRectangle(cornerRadius: TileStyle.tickRadius, style: .continuous)
                    .fill(color(for: model.tickState(at: index)))
                    .opacity(fades ? TileGeometry.windowFade(index: index, count: count) : 1)
            }
        }
        .frame(height: TileStyle.tickHeight)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.3), value: model)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(model.accessibilityLabel)
        .accessibilityAddTraits(.isImage)
    }

    private var isWindow: Bool {
        if case .window = model.kind { true } else { false }
    }

    private func color(for state: MeterTickState) -> Color {
        switch state {
        case .before: palette.before
        case .today: palette.accent
        case .rest: palette.rest
        }
    }
}

/// A 44 pt pill: white with black words for the main action, a quiet white tint for the other. A press shrinks it a
/// touch, and the caller adds the haptic.
private struct TilePillButton: View {
    enum Style {
        case primary
        case secondary
    }

    let title: String
    let style: Style
    let identifier: String
    let isDisabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
            .font(TileStyle.buttonFont)
            .foregroundStyle(style == .primary ? TileStyle.primaryInk : TileStyle.secondaryInk)
            .lineLimit(1)
            .padding(.horizontal, 18)
            .frame(maxWidth: style == .primary ? .infinity : nil)
            .frame(height: TileGeometry.buttonHeight)
            .background(Capsule().fill(style == .primary ? TileStyle.primaryFill : TileStyle.secondaryFill))
            .contentShape(Capsule())
        }
        .buttonStyle(TilePressStyle())
        .disabled(isDisabled)
        .opacity(isDisabled ? 0.5 : 1)
        .accessibilityIdentifier(identifier)
    }
}

private struct TilePressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
