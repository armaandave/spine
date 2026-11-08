import Foundation

/// Where the two buttons sit under a title's meter. `+N` ends on the meter's lit end (a 36 pt circle at least), and
/// `Update` always fills the rest of the row: it starts one standard gap right of `+N` and ends on the panel's right
/// edge. It widens and shrinks with `+N`; wide enough it reads "Update", narrow it is the pencil, and the two
/// cross-fade on its width (`textOpacity`, `iconOpacity`). It is never parked at the right with a hole to its left.
nonisolated enum PanelButtonGeometry {
    static let height = 36.0
    static let minQuick = 36.0
    static let gap = 10.0
    /// `Update` narrower than this (`quick > W - 86`) reads as the icon.
    static let updateRoomForIcon = 86.0
    /// `quick <= W - 46` keeps room for a 36 pt `Update` and the 10 gap.
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
        let x = quick + gap
        let width = max(W - x, 0)
        return Result(
            quickWidth: quick, updateX: x, updateWidth: width, hasQuick: true,
            updateIsIcon: quick > W - updateRoomForIcon
        )
    }

    /// How much of `Update`'s words and of its icon show at a width that is animating between the two. They cross
    /// over between 48 and 70 pt: the words are gone by 52, the icon is gone by 64, so the middle never goes blank.
    static func textOpacity(updateWidth: Double) -> Double {
        min(max((updateWidth - 52) / 18, 0), 1)
    }

    static func iconOpacity(updateWidth: Double) -> Double {
        min(max((64 - updateWidth) / 16, 0), 1)
    }

    /// The pencil grows from a little smaller as it comes in, and the words from a little smaller too, so the swap
    /// reads as a morph and not as two labels fading over each other.
    static func iconScale(updateWidth: Double) -> Double {
        0.7 + 0.3 * iconOpacity(updateWidth: updateWidth)
    }

    static func textScale(updateWidth: Double) -> Double {
        0.88 + 0.12 * textOpacity(updateWidth: updateWidth)
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
