import SwiftUI

struct HallOfFameCrownPlacement: Equatable {
    let index: Int
    let x: CGFloat
    let y: CGFloat
    let rotation: Angle
    let scale: CGFloat
    let zIndex: Double
}

struct HallOfFameCrownArrangement {
    let above: FavoriteSlot?
    let below: [FavoriteSlot]
}

enum HallOfFameCrownPosition {
    case aboveAvatar
    case belowAvatar

    var transformAnchor: UnitPoint {
        switch self {
        case .aboveAvatar: .bottom
        case .belowAvatar: .top
        }
    }

    var revealYOffset: CGFloat {
        switch self {
        case .aboveAvatar: 20
        case .belowAvatar: -20
        }
    }
}

struct HallOfFameCrownLayout {
    static let avatarDiameter: CGFloat = 128
    static let crownHeight: CGFloat = 210
    /// Every crown is drawn as a slice of the full one (seven cards below the avatar):
    /// the same card size and the same angle between neighbors, centered, whatever the count.
    static let cardSize = CGSize(width: 54, height: 81)
    private static let fullCrownCount = 7
    private static let angleStep: Double = 26
    /// Three cards or fewer sit this much closer to the avatar than a full crown's middle card;
    /// four to six step back down to where the full crown sits.
    private static let smallCrownLift: CGFloat = 29

    static func avatarLift(forCount count: Int) -> CGFloat {
        let count = min(max(count, 3), fullCrownCount)
        return smallCrownLift * CGFloat(fullCrownCount - count) / CGFloat(fullCrownCount - 3)
    }

    /// The row under the avatar always has an odd number of cards, so it has a middle one: an odd count is all
    /// row, left to right, and an even count lifts the card at `count / 2` above the avatar.
    static func arrangement(for slots: [FavoriteSlot]) -> HallOfFameCrownArrangement {
        guard !slots.isEmpty, slots.count.isMultiple(of: 2) else {
            return HallOfFameCrownArrangement(above: nil, below: slots)
        }

        var below = slots
        let above = below.remove(at: slots.count / 2)
        return HallOfFameCrownArrangement(above: above, below: below)
    }

    static func placements(
        count: Int,
        avatarDiameter: CGFloat,
        collapseProgress: CGFloat = 0,
        position: HallOfFameCrownPosition = .aboveAvatar
    ) -> [HallOfFameCrownPlacement] {
        let count = max(count, 0)
        guard count > 0 else { return [] }

        let collapseProgress = clampedCollapseProgress(collapseProgress)
        let remaining = 1 - collapseProgress
        let collapsedScale: CGFloat = 0.28
        let radiusX = avatarDiameter * 1.12
        let radiusY = avatarDiameter * 0.95
        let baseY = avatarDiameter * 0.10 + avatarLift(forCount: count)

        return (0..<count).map { index in
            let offsetFromCenter = Double(index) - Double(count - 1) / 2
            let degrees = offsetFromCenter * angleStep
            let radians = degrees * .pi / 180
            let normalizedDistance = abs(offsetFromCenter) / (Double(fullCrownCount - 1) / 2)
            let scale = 1.04 - CGFloat(normalizedDistance) * 0.10
            let x = sin(radians) * radiusX
            let aboveY = baseY - cos(radians) * radiusY
            let y = position == .aboveAvatar ? aboveY : -aboveY
            let rotationDirection: Double = position == .aboveAvatar ? 1 : -1
            let zIndex = 10 - normalizedDistance

            return HallOfFameCrownPlacement(
                index: index,
                x: x * remaining,
                y: y * remaining,
                rotation: .degrees(degrees * 0.58 * rotationDirection * Double(remaining)),
                scale: scale + (collapsedScale - scale) * collapseProgress,
                zIndex: zIndex * Double(remaining)
            )
        }
    }

    static func aboveMusicPlacement(
        cardSize: CGSize,
        lowerCount: Int,
        avatarDiameter: CGFloat,
        collapseProgress: CGFloat = 0
    ) -> HallOfFameCrownPlacement {
        let collapseProgress = clampedCollapseProgress(collapseProgress)
        let expandedScale: CGFloat = 1.04
        let collapsedScale: CGFloat = 0.28
        let expandedY = -(avatarDiameter / 2 + musicAvatarGap(forLowerCount: lowerCount) + cardSize.height / 2)

        return HallOfFameCrownPlacement(
            index: 0,
            x: 0,
            y: expandedY * (1 - collapseProgress),
            rotation: .zero,
            scale: expandedScale + (collapsedScale - expandedScale) * collapseProgress,
            zIndex: 10 * Double(1 - collapseProgress)
        )
    }

    static func visualSize(for slot: FavoriteSlot, cardSize: CGSize) -> CGSize {
        let isSquare = slot.item?.ref.mediaType == "music"
            || slot.item?.posterOrientation == .square
        return isSquare ? CGSize(width: cardSize.width, height: cardSize.width) : cardSize
    }

    /// Room kept above the avatar on every profile for the music card at its highest
    /// (a full crown below), so the avatar sits in the same place whatever the favorites.
    static func avatarTopClearance(collapseProgress: CGFloat) -> CGFloat {
        (cardSize.width + musicAvatarGap(forLowerCount: fullCrownCount))
            * (1 - clampedCollapseProgress(collapseProgress))
    }

    /// Room above the avatar for the tallest card that can sit there: a poster at a full crown's gap, scaled up
    /// from its bottom edge. `avatarTopClearance` only fits a square album card.
    static var posterTopClearance: CGFloat {
        cardSize.height * 1.04 + musicAvatarGap(forLowerCount: fullCrownCount)
    }

    /// How far below the crown's top the lowest card reaches, counting its scale and tilt.
    /// Cards are placed without taking up layout space, so the header has to ask.
    static func lowestCardEdge(for slots: [FavoriteSlot]) -> CGFloat {
        let below = arrangement(for: slots).below
        let placements = placements(count: below.count, avatarDiameter: avatarDiameter, position: .belowAvatar)

        return zip(below, placements).map { slot, placement in
            let size = visualSize(for: slot, cardSize: cardSize)
            let top = crownHeight / 2 + placement.y - size.height / 2
            let tilt = abs(placement.rotation.radians)
            return top + placement.scale * (size.height * cos(tilt) + size.width / 2 * sin(tilt))
        }.max() ?? 0
    }

    /// How much further down the name sits than it does under a full crown (seven cards below),
    /// so it keeps the same distance from the lowest card on every profile. Smaller crowns sit
    /// closer to the avatar, so their names move up.
    static func nameOffset(for slots: [FavoriteSlot], collapseProgress: CGFloat) -> CGFloat {
        (lowestCardEdge(for: slots) - fullCrownCardEdge) * (1 - clampedCollapseProgress(collapseProgress))
    }

    private static let fullCrownCardEdge = lowestCardEdge(
        for: Array(repeating: FavoriteSlot(id: "movie", title: "", item: nil), count: fullCrownCount)
    )

    /// Gap between the avatar and the upright middle card below it, kept for the music card above.
    static func musicAvatarGap(forLowerCount count: Int) -> CGFloat {
        let center = placements(count: count, avatarDiameter: avatarDiameter, position: .belowAvatar)[count / 2]
        return crownHeight / 2 + center.y - cardSize.height / 2 - avatarDiameter
    }

    private static func clampedCollapseProgress(_ collapseProgress: CGFloat) -> CGFloat {
        min(1, max(0, collapseProgress))
    }
}
