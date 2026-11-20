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
        let rotation = crownRevealed ? placement.rotation : .zero

        return HallOfFameCrownCard(
            slot: slot,
            visualSize: visualSize,
            borderOpacity: borderOpacity,
            onTap: onTap
        )
        .scaleEffect(placement.scale, anchor: position.transformAnchor)
        .rotationEffect(rotation, anchor: position.transformAnchor)
        // Held, the card lifts as it sits, tilted and scaled, instead of straightening.
        .modifier(HallOfFameCardHold(
            slot: slot,
            lift: HallOfFameCardLift(
                size: visualSize,
                scale: placement.scale,
                rotation: rotation,
                anchor: position.transformAnchor
            ),
            menu: menu
        ))
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

/// What a long press on one of your own crown's cards adds to the card's hold menu.
struct HallOfFameCrownMenu {
    let onEdit: () -> Void
    let onRemove: (FavoriteSlot) -> Void
}

/// A crown card's hold menu (`posterHoldMenu`), with your own crown's Edit and Remove. The menu takes the room the
/// tilted card covers and the card's exact outline, so the card lifts as it sits.
private struct HallOfFameCardHold: ViewModifier {
    let slot: FavoriteSlot
    let lift: HallOfFameCardLift
    let menu: HallOfFameCrownMenu?

    func body(content: Content) -> some View {
        if let item = slot.item {
            content
                .padding(lift.margins)
                .contentShape([.interaction, .contextMenuPreview], lift.outline)
                .posterHoldMenu(item, viewer: item.userState) {
                    if let menu {
                        Button("Edit Hall of Fame", systemImage: "pencil") {
                            menu.onEdit()
                        }
                        Button("Remove from Hall of Fame", systemImage: "trash", role: .destructive) {
                            menu.onRemove(slot)
                        }
                    }
                }
                .padding(lift.margins.negated)
        } else {
            content
        }
    }
}

/// Where a crown card is drawn once it's scaled and turned about its anchor: how far it reaches past its frame, and its
/// outline in that grown frame.
struct HallOfFameCardLift: Equatable {
    let size: CGSize
    let scale: CGFloat
    let rotation: Angle
    let anchor: UnitPoint

    /// The card's drawing in its own frame: scaled, then turned, both about the anchor (as `scaleEffect` then
    /// `rotationEffect` draw it).
    var transform: CGAffineTransform {
        let pivot = CGPoint(x: anchor.x * size.width, y: anchor.y * size.height)
        return CGAffineTransform(translationX: pivot.x, y: pivot.y)
            .rotated(by: rotation.radians)
            .scaledBy(x: scale, y: scale)
            .translatedBy(x: -pivot.x, y: -pivot.y)
    }

    /// How far the drawn card reaches past its frame on each side.
    var margins: EdgeInsets {
        let corners = [
            CGPoint.zero,
            CGPoint(x: size.width, y: 0),
            CGPoint(x: 0, y: size.height),
            CGPoint(x: size.width, y: size.height),
        ].map { $0.applying(transform) }
        let xs = corners.map(\.x)
        let ys = corners.map(\.y)
        return EdgeInsets(
            top: max(0, -(ys.min() ?? 0)),
            leading: max(0, -(xs.min() ?? 0)),
            bottom: max(0, (ys.max() ?? 0) - size.height),
            trailing: max(0, (xs.max() ?? 0) - size.width)
        )
    }

    /// The drawn card's rounded outline, in its frame grown by `margins`.
    var outline: HallOfFameCardOutline {
        let margins = margins
        return HallOfFameCardOutline(
            size: size,
            transform: transform.concatenating(CGAffineTransform(translationX: margins.leading, y: margins.top))
        )
    }
}

struct HallOfFameCardOutline: Shape {
    let size: CGSize
    let transform: CGAffineTransform

    func path(in rect: CGRect) -> Path {
        Path(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: 8, style: .continuous)
            .applying(transform)
    }
}

private extension EdgeInsets {
    var negated: EdgeInsets {
        EdgeInsets(top: -top, leading: -leading, bottom: -bottom, trailing: -trailing)
    }
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

    var body: some View {
        if let item = slot.item {
            HallOfFameCrownFilledCard(item: item, visualSize: visualSize, borderOpacity: borderOpacity)
                .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .onTapGesture {
                    onTap(slot)
                }
                .accessibilityLabel("Hall of Fame, \(item.title)")
                .accessibilityAddTraits(.isButton)
                .frame(width: visualSize.width, height: visualSize.height)
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
