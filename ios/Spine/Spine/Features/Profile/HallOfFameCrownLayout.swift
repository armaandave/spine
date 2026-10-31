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

    static func arrangement(for slots: [FavoriteSlot]) -> HallOfFameCrownArrangement {
        guard let musicIndex = slots.firstIndex(where: { $0.id == "music" }) else {
            return HallOfFameCrownArrangement(above: nil, below: slots)
        }

        let music = slots[musicIndex]
        var below = slots
        below.remove(at: musicIndex)

        if slots.count.isMultiple(of: 2) {
            return HallOfFameCrownArrangement(above: music, below: below)
        }

        below.insert(music, at: below.count / 2)
        return HallOfFameCrownArrangement(above: nil, below: below)
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
        let maxAngle = maxAngle(for: count)
        let angles: [Double]
        if count == 1 {
            angles = [0]
        } else {
            angles = (0..<count).map { index in
                let progress = Double(index) / Double(count - 1)
                return -maxAngle + progress * maxAngle * 2
            }
        }
        let radiusX = avatarDiameter * 1.12
        let radiusY = avatarDiameter * 0.95
        let baseY = avatarDiameter * 0.10

        return angles.enumerated().map { index, degrees in
            let radians = degrees * .pi / 180
            let distanceFromCenter = abs(Double(index) - Double(count - 1) / 2)
            let normalizedDistance = count == 1 ? 0 : distanceFromCenter / (Double(count - 1) / 2)
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
        let isSquare = slot.id == "music"
            || slot.item?.ref.mediaType == "music"
            || slot.item?.posterOrientation == .square
        return isSquare ? CGSize(width: cardSize.width, height: cardSize.width) : cardSize
    }

    /// Room kept above the avatar on every profile, sized for the largest music card
    /// (one card below), so the avatar sits in the same place whatever the favorites.
    static func avatarTopClearance(collapseProgress: CGFloat) -> CGFloat {
        let musicHeight = cardSize(for: 1).width
        return (musicHeight + musicAvatarGap(forLowerCount: 1))
            * (1 - clampedCollapseProgress(collapseProgress))
    }

    static func musicAvatarGap(forLowerCount count: Int) -> CGFloat {
        let cardSize = cardSize(for: count)
        let center = placements(count: count, avatarDiameter: avatarDiameter, position: .belowAvatar)[count / 2]
        return crownHeight / 2 + center.y - cardSize.height / 2 - avatarDiameter
    }

    static func cardSize(for count: Int) -> CGSize {
        switch max(count, 1) {
        case 1:
            CGSize(width: 74, height: 111)
        case 2:
            CGSize(width: 70, height: 105)
        case 3:
            CGSize(width: 66, height: 99)
        case 4:
            CGSize(width: 62, height: 93)
        case 5:
            CGSize(width: 58, height: 87)
        case 6, 7:
            CGSize(width: 54, height: 81)
        default:
            CGSize(width: 50, height: 75)
        }
    }

    private static func clampedCollapseProgress(_ collapseProgress: CGFloat) -> CGFloat {
        min(1, max(0, collapseProgress))
    }

    private static func maxAngle(for count: Int) -> Double {
        switch count {
        case 1:
            0
        case 2:
            35
        case 3:
            48
        case 4:
            58
        case 5:
            66
        case 6:
            76
        default:
            78
        }
    }
}
