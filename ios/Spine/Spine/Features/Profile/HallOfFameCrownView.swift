import SwiftUI

struct HallOfFameCrownView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let slots: [FavoriteSlot]
    var savingSlotIDs: Set<String> = []
    var collapseProgress: CGFloat = 0
    let onTap: (FavoriteSlot) -> Void
    var onEmptyTap: (FavoriteSlot) -> Void = { _ in }
    var onFilledLongPress: (FavoriteSlot) -> Void = { _ in }

    @State private var crownRevealed = false

    private let borderOpacity = 0.14
    private let crownWidth: CGFloat = 340

    var body: some View {
        let arrangement = HallOfFameCrownLayout.arrangement(for: slots)
        let cardSize = HallOfFameCrownLayout.cardSize(for: max(arrangement.below.count, 1))
        let belowPlacements = HallOfFameCrownLayout.placements(
            count: arrangement.below.count,
            avatarDiameter: HallOfFameCrownLayout.avatarDiameter,
            collapseProgress: collapseProgress,
            position: .belowAvatar
        )

        ZStack(alignment: .topLeading) {
            ForEach(arrangement.below.indices, id: \.self) { index in
                crownCard(
                    arrangement.below[index],
                    cardSize: cardSize,
                    placement: belowPlacements[index],
                    position: .belowAvatar
                )
            }

            if let music = arrangement.above {
                let musicSize = HallOfFameCrownLayout.visualSize(for: music, cardSize: cardSize)
                crownCard(
                    music,
                    cardSize: cardSize,
                    placement: HallOfFameCrownLayout.aboveMusicPlacement(
                        cardSize: musicSize,
                        lowerCount: arrangement.below.count,
                        avatarDiameter: HallOfFameCrownLayout.avatarDiameter,
                        collapseProgress: collapseProgress
                    ),
                    position: .aboveAvatar
                )
            }
        }
        .frame(width: crownWidth, height: HallOfFameCrownLayout.crownHeight)
        .allowsHitTesting(collapseProgress < 0.72)
        .onAppear {
            crownRevealed = true
        }
    }

    private func crownCard(
        _ slot: FavoriteSlot,
        cardSize: CGSize,
        placement: HallOfFameCrownPlacement,
        position: HallOfFameCrownPosition
    ) -> some View {
        let visualSize = HallOfFameCrownLayout.visualSize(for: slot, cardSize: cardSize)
        let revealIndex = slots.firstIndex(where: { $0.id == slot.id }) ?? placement.index

        return HallOfFameCrownCard(
            slot: slot,
            visualSize: visualSize,
            borderOpacity: borderOpacity,
            isSaving: savingSlotIDs.contains(slot.id),
            onTap: onTap,
            onEmptyTap: onEmptyTap,
            onFilledLongPress: onFilledLongPress
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

private struct HallOfFameCrownCard: View {
    let slot: FavoriteSlot
    let visualSize: CGSize
    let borderOpacity: Double
    let isSaving: Bool
    let onTap: (FavoriteSlot) -> Void
    let onEmptyTap: (FavoriteSlot) -> Void
    let onFilledLongPress: (FavoriteSlot) -> Void

    var body: some View {
        ZStack {
            if let item = slot.item {
                HallOfFameCrownFilledCard(item: item, visualSize: visualSize, borderOpacity: borderOpacity)
                    .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .onTapGesture {
                        onTap(slot)
                    }
                    .onLongPressGesture {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        onFilledLongPress(slot)
                    }
                    .accessibilityLabel("Hall of Fame, \(item.title)")
                    .accessibilityAddTraits(.isButton)
            } else {
                Button {
                    onEmptyTap(slot)
                } label: {
                    HallOfFameCrownEmptyShell(visualSize: visualSize)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Add Hall of Fame \(slot.title)")
            }

            Group {
                if isSaving {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(.black.opacity(0.52))
                        .frame(width: visualSize.width, height: visualSize.height)
                        .overlay {
                            ProgressView()
                                .tint(.white)
                        }
                }
            }
            .spineContentTransition(value: isSaving)
        }
        .frame(width: visualSize.width, height: visualSize.height)
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

private struct HallOfFameCrownEmptyShell: View {
    let visualSize: CGSize

    var body: some View {
        RoundedRectangle(cornerRadius: 8, style: .continuous)
            .fill(.white.opacity(0.04))
            .frame(width: visualSize.width, height: visualSize.height)
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(.white.opacity(0.10), lineWidth: 1)
            }
            .overlay {
                Image(systemName: "plus")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.26))
            }
            .opacity(0.88)
            .shadow(color: .black.opacity(0.16), radius: 8, y: 5)
    }
}

#Preview("0 slots") {
    HallOfFameCrownPreview(slots: [])
}

#Preview("8 empty shells") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"], filledIndexes: []))
}

#Preview("Music only below") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["music"], filledIndexes: [0]))
}

#Preview("Even count, music above") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "music"], filledIndexes: [0, 1]))
}

#Preview("Odd count, music centered below") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "music"], filledIndexes: [0, 1, 2]))
}

#Preview("2 filled, 6 empty") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"], filledIndexes: [0, 7]))
}

#Preview("5 filled") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(filledIndexes: [0, 1, 2, 3, 4]))
}

#Preview("8 slots, 3 filled") {
    HallOfFameCrownPreview(slots: HallOfFameCrownPreview.slots(keys: ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"], filledIndexes: [0, 2, 7]))
}

private struct HallOfFameCrownPreview: View {
    let slots: [FavoriteSlot]

    static func slots(keys: [String] = ["movie", "tv", "anime", "manga", "game"], filledIndexes: Set<Int>) -> [FavoriteSlot] {
        keys.enumerated().map { index, key in
            FavoriteSlot(id: key, title: title(for: key), item: filledIndexes.contains(index) ? media(index: index, mediaType: key) : nil)
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
