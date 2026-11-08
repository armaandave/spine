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
    /// "Plan to watch" on a Trending title, or taking it back.
    var onPlanToWatch: (TrendingTitle) -> Void = { _ in }
    /// A poster in the Trending carousel was tapped, or its Open button: the title's page.
    var onOpenTrending: (TrendingTitle) -> Void = { _ in }
    /// The empty In progress tile's button.
    var onFindSomething: () -> Void = {}
    /// The empty Trending tile's button, shown when you follow nobody.
    var onFindPeople: () -> Void = {}
}

/// The panel at the foot of an open tile: the focused title's name and where it stands, its meter, and two buttons.
/// Trending reads the focused id here and nowhere above, so swiping redraws this panel and the posters, not the page.
struct TileDetailPanel: View {
    let tile: HomeTileID
    let model: HomeTilesModel
    let state: HomeTilesState
    let actions: HomeTilesActions

    var body: some View {
        switch tile {
        case .inProgress:
            // One panel for the whole tile: it follows the carousel's scroll and morphs between titles, so it has no
            // identity per title and never reads the focus here.
            InProgressMorphPanel(
                model: model,
                state: state,
                actions: actions,
                quickID: "in-progress.quick",
                updateID: "in-progress.update"
            )
        case .trending:
            if let id = state.focusID, let title = model.trendingTitle(id: id) {
                TrendingPanel(
                    title: title,
                    detail: model.trendingDetail(for: title),
                    busy: model.busyItemIDs.contains(title.key),
                    actions: actions
                )
                .id(title.id)
            }
        }
    }
}

// MARK: - Trending

private struct TrendingPanel: View {
    let title: TrendingTitle
    let detail: TrendingTileDetail
    let busy: Bool
    let actions: HomeTilesActions

    @State private var planTicks = 0
    @State private var openTicks = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TilePanelTitleRow(title: detail.title, accessibilityLabel: detail.accessibilityLabel) {
                Text(detail.averageText.map { "\($0) avg" } ?? "")
            }
            friends
                .padding(.top, 8)
                .frame(height: TileGeometry.panelMeterBlock, alignment: .top)
            HStack(spacing: 10) {
                TilePillButton(
                    title: detail.primaryTitle,
                    style: .primary,
                    identifier: "trending.plan",
                    isDisabled: busy,
                    checked: detail.isPlanned
                ) {
                    planTicks += 1
                    actions.onPlanToWatch(title)
                }
                TilePillButton(
                    title: detail.secondaryTitle,
                    style: .secondary,
                    identifier: "trending.open",
                    isDisabled: false
                ) {
                    openTicks += 1
                    actions.onOpenTrending(title)
                }
            }
            .padding(.top, 12)
        }
        .sensoryFeedback(.selection, trigger: planTicks)
        .sensoryFeedback(.impact, trigger: openTicks)
    }

    /// Up to three faces overlapping by 6 pt, and who they are: "2 logged it", "You logged it".
    private var friends: some View {
        HStack(spacing: 10) {
            HStack(spacing: -TileStyle.avatarOverlap) {
                ForEach(Array(detail.avatars.enumerated()), id: \.offset) { _, avatar in
                    TileAvatar(avatar: avatar)
                }
            }
            Text(detail.whoLine)
                .font(TileStyle.friendsFont)
                .foregroundStyle(TileStyle.panelSecondary)
                .lineLimit(1)
        }
        .frame(height: 26)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(detail.whoLine)
    }
}

/// A person's picture at 22 pt, decoded at that size, with a dark ring so overlapping faces read apart. Without a
/// picture, their initial on a pastel that stays the same for the same letter.
private struct TileAvatar: View {
    let avatar: TrendingTileDetail.Avatar

    var body: some View {
        SpineAsyncImage(
            url: avatar.url.flatMap { URL(string: $0) },
            thumbnailSize: CGSize(width: TileStyle.avatarSize, height: TileStyle.avatarSize)
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    Color(hue: Self.hue(for: avatar.initial), saturation: 0.36, brightness: 0.97)
                    Text(avatar.initial)
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(HomeDesign.Palette.avatarInk)
                }
            }
        }
        .frame(width: TileStyle.avatarSize, height: TileStyle.avatarSize)
        .clipShape(Circle())
        .background {
            Circle()
                .fill(TileStyle.avatarRing)
                .padding(-2)
        }
        .accessibilityHidden(true)
    }

    nonisolated static func hue(for initial: String) -> Double {
        let value = initial.unicodeScalars.first.map { Int($0.value) } ?? 0
        return Double((value &* 37) % 360) / 360
    }
}

// MARK: - Shared pieces

/// The panel's top row: the title at 19, and the right-hand words at 13, on one baseline. The title gives way first.
private struct TilePanelTitleRow<Aside: View>: View {
    let title: String
    let accessibilityLabel: String
    @ViewBuilder let aside: () -> Aside

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title)
                .font(HomeDesign.Text.panelTitle)
                .tracking(-0.3)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)
            aside()
                .font(TileStyle.panelAside)
                .foregroundStyle(TileStyle.panelSecondary)
                .lineLimit(1)
                .fixedSize()
                .layoutPriority(1)
        }
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityAddTraits(.isHeader)
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
    var checked = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title)
                if checked {
                    Image(systemName: "checkmark")
                        .font(.system(size: 13, weight: .heavy))
                }
            }
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
