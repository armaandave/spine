import SwiftUI

struct HallOfFameCrownView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let slots: [FavoriteSlot]
    var collapseProgress: CGFloat = 0
    let onTap: (FavoriteSlot) -> Void
    /// Your own profile's long-press menu: nil leaves the cards without one.
    var menu: HallOfFameCrownMenu?

    @State private var crownRevealed = false

    private let borderOpacity = 0.14
    private let crownWidth: CGFloat = 340

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(cards, id: \.slot.id) { card in
                crownCard(card)
            }
        }
        .frame(width: crownWidth, height: HallOfFameCrownLayout.crownHeight)
        .allowsHitTesting(collapseProgress < 0.72)
        // A reorder moves each card to its new place instead of swapping artwork in place.
        .animation(reduceMotion ? nil : .spring(response: 0.38, dampingFraction: 0.84), value: slots.map(\.id))
        .onAppear {
            crownRevealed = true
        }
    }

    /// Every card with its place, in one list, so a card moving between the row and the top keeps its identity.
    private var cards: [HallOfFameCrownCardPlacement] {
        let arrangement = HallOfFameCrownLayout.arrangement(for: slots)
        let belowPlacements = HallOfFameCrownLayout.placements(
            count: arrangement.below.count,
            avatarDiameter: HallOfFameCrownLayout.avatarDiameter,
            collapseProgress: collapseProgress,
            position: .belowAvatar
        )
        var cards = zip(arrangement.below, belowPlacements).map { slot, placement in
            HallOfFameCrownCardPlacement(slot: slot, placement: placement, position: .belowAvatar)
        }
        if let above = arrangement.above {
            cards.append(HallOfFameCrownCardPlacement(
                slot: above,
                placement: HallOfFameCrownLayout.aboveMusicPlacement(
                    cardSize: HallOfFameCrownLayout.visualSize(for: above, cardSize: HallOfFameCrownLayout.cardSize),
                    lowerCount: arrangement.below.count,
                    avatarDiameter: HallOfFameCrownLayout.avatarDiameter,
                    collapseProgress: collapseProgress
                ),
                position: .aboveAvatar
            ))
        }
        return cards
    }

    private func crownCard(_ card: HallOfFameCrownCardPlacement) -> some View {
        let slot = card.slot
        let placement = card.placement
        let position = card.position
        let visualSize = HallOfFameCrownLayout.visualSize(for: slot, cardSize: HallOfFameCrownLayout.cardSize)
        let revealIndex = slots.firstIndex(where: { $0.id == slot.id }) ?? placement.index

        return HallOfFameCrownCard(
            slot: slot,
            visualSize: visualSize,
            borderOpacity: borderOpacity,
            onTap: onTap,
            menu: menu
        )
        .scaleEffect(placement.scale, anchor: position.transformAnchor)
        .rotationEffect(crownRevealed ? placement.rotation : .zero, anchor: position.transformAnchor)
        .position(
            x: crownWidth / 2,
            y: position == .belowAvatar
                ? HallOfFameCrownLayout.crownHeight / 2
                : HallOfFameCrownLayout.avatarDiameter / 2
        )
        .offset(
            x: placement.x,
            y: crownRevealed ? placement.y : placement.y + position.revealYOffset
        )
        .opacity(crownRevealed ? 1 - collapseProgress * 0.45 : 0)
        .zIndex(placement.zIndex)
        .animation(
            reduceMotion ? nil : .spring(response: 0.45, dampingFraction: 0.78)
                .delay(Double(revealIndex) * 0.04),
            value: crownRevealed
        )
    }
}

/// What a long press on one of your own crown's cards offers.
struct HallOfFameCrownMenu {
    let onEdit: () -> Void
    let onRemove: (FavoriteSlot) -> Void
}

private struct HallOfFameCrownCardPlacement {
    let slot: FavoriteSlot
    let placement: HallOfFameCrownPlacement
    let position: HallOfFameCrownPosition
}

private struct HallOfFameCrownCard: View {
    let slot: FavoriteSlot
    let visualSize: CGSize
    let borderOpacity: Double
    let onTap: (FavoriteSlot) -> Void
    let menu: HallOfFameCrownMenu?

    var body: some View {
        if let item = slot.item {
            card(item)
                .frame(width: visualSize.width, height: visualSize.height)
        }
    }

    @ViewBuilder
    private func card(_ item: MediaSummary) -> some View {
        let filled = HallOfFameCrownFilledCard(item: item, visualSize: visualSize, borderOpacity: borderOpacity)
            .contentShape(.contextMenuPreview, RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .onTapGesture {
                onTap(slot)
            }
            .accessibilityLabel("Hall of Fame, \(item.title)")
            .accessibilityAddTraits(.isButton)

        if let menu {
            filled.contextMenu {
                Button("Edit Hall of Fame", systemImage: "pencil") {
                    menu.onEdit()
                }
                Button("Remove from Hall of Fame", systemImage: "trash", role: .destructive) {
                    menu.onRemove(slot)
                }
            }
        } else {
            filled
        }
    }
}

private struct HallOfFameCrownFilledCard: View {
    let item: MediaSummary
    let visualSize: CGSize
    let borderOpacity: Double

    var body: some View {
        MediaArtwork(
            url: item.displayPosterURL,
            title: item.title,
            slot: .hofCrown,
            mediaType: item.ref.mediaType,
            orientation: item.posterOrientation
        )
        .scaleEffect(visualSize.width / PosterSlot.hofCrown.size.width)
        .frame(width: visualSize.width, height: visualSize.height)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(.white.opacity(borderOpacity), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.42), radius: 12, y: 8)
    }
}

#Preview("1 pick") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["music"]))
}

#Preview("Even count, middle pick above") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "book", "music", "tv"]))
}

#Preview("Odd count, all in the row") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "music"]))
}

#Preview("Full crown") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "music", "game", "book", "comic"]))
}

private struct HallOfFameCrownPreview: View {
    let slots: [FavoriteSlot]

    static func slots(keys: [String]) -> [FavoriteSlot] {
        keys.enumerated().map { index, key in
            FavoriteSlot(id: "\(key)-\(index)", title: title(for: key), item: media(index: index, mediaType: key))
        }
    }

    private static func title(for key: String) -> String {
        key.replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .split(separator: " ")
            .map { $0.capitalized }
            .joined(separator: " ")
    }

    private static func media(index: Int, mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: index,
                source: "preview",
                mediaType: mediaType,
                mediaId: "\(index)",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Favorite \(index + 1)",
            posterUrl: nil,
            posterOrientation: mediaType == "music" ? .square : .portrait
        )
    }

    var body: some View {
        let clearance = HallOfFameCrownLayout.avatarTopClearance(collapseProgress: 0)

        ZStack {
            SpinePageBackground()

            ZStack(alignment: .top) {
                HallOfFameCrownView(slots: slots) { _ in }

                Circle()
                    .fill(.black.opacity(0.82))
                    .frame(width: 128, height: 128)
                    .overlay {
                        Circle()
                            .stroke(.white.opacity(0.18), lineWidth: 1)
                    }
                    .shadow(color: .black.opacity(0.44), radius: 22, y: 12)
            }
            .padding(.top, clearance)
        }
        .frame(width: 390, height: 390)
    }
}
