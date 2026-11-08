import Foundation

/// Where the two buttons sit under a title's meter. `+N` ends on the meter's lit end (a 36 pt circle at least), and
/// `Update` takes the rest of the row, or shrinks to a 36 pt circle at the right when `+N` is long.
nonisolated enum PanelButtonGeometry {
    static let height = 36.0
    static let minQuick = 36.0
    static let gap = 10.0
    /// `quick > W - 86` would leave `Update` under 76 wide: it becomes an icon.
    static let updateRoomForIcon = 86.0
    /// `quick <= W - 46` keeps room for a 36 circle and the 10 gap.
    static let quickMaxInset = 46.0

    struct Result: Equatable {
        var quickWidth: Double
        var updateX: Double
        var updateWidth: Double
        var hasQuick: Bool
        var updateIsIcon: Bool
    }

    static func make(litEnd: Double, hasQuick: Bool, width W: Double) -> Result {
        guard hasQuick else {
            return Result(quickWidth: 0, updateX: 0, updateWidth: W, hasQuick: false, updateIsIcon: false)
        }
        let quick = min(max(litEnd, minQuick), max(minQuick, W - quickMaxInset))
        let icon = quick > W - updateRoomForIcon
        let x = icon ? W - height : quick + gap
        return Result(quickWidth: quick, updateX: x, updateWidth: W - x, hasQuick: true, updateIsIcon: icon)
    }

    /// How much of `Update`'s words and of its icon show at a width that is animating between the two: the words are
    /// gone by 50 pt, the icon is there by 36.
    static func textOpacity(updateWidth: Double) -> Double {
        min(max((updateWidth - 50) / 20, 0), 1)
    }

    static func iconOpacity(updateWidth: Double) -> Double {
        min(max((60 - updateWidth) / 24, 0), 1)
    }
}

/// Picks the longest of a plan's words that fits the `+N` button.
nonisolated enum PanelLabelTier {
    /// The index of the first tier with `measure(tier) + padding <= width`; the last when none fits.
    static func pick(
        tiers: [String],
        quickWidth: Double,
        padding: Double = InProgressPanelMetrics.labelPadding,
        measure: (String) -> Double
    ) -> Int {
        guard !tiers.isEmpty else { return 0 }
        for (index, tier) in tiers.enumerated() where measure(tier) + padding <= quickWidth {
            return index
        }
        return tiers.count - 1
    }
}
