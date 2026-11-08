import SwiftUI

extension HomeDesign {
    /// The numbers of the feed card's depth (direction B, "Duotone"): the counts pill, the status pill, the marks, the
    /// poster's lift, the text lift. They sit in their own file so the tiles' tokens in `HomeDesign.swift` stay theirs.
    /// Points unless noted.
    nonisolated enum FeedDepth {
        // MARK: Counts pill

        static let countsPillHeight: CGFloat = 22
        static let countsPillHPad: CGFloat = 9
        /// Between the heart pair and the bubble pair.
        static let countsPillGap: CGFloat = 8
        /// Icon to number.
        static let countsPairGap: CGFloat = 4
        static let countsIcon: CGFloat = 13
        /// Used with `.monospacedDigit()`.
        static let countsFont = Font.system(size: 12, weight: .bold)
        /// The pill's right edge to the card's right edge.
        static let countsTrailing: CGFloat = 10
        /// The pill's bottom edge to the card's bottom edge.
        static let countsBottom: CGFloat = 8
        /// Room kept left of the pill for detail rows and the meter: 64 + 7 per digit.
        static let countsReserveBase: CGFloat = 64
        static let countsReservePerDigit: CGFloat = 7

        // MARK: Status pill

        static let statusPillHeight: CGFloat = 19
        static let statusFont = Font.system(size: 11, weight: .bold)
        static let statusLeading: CGFloat = 6
        static let statusTrailing: CGFloat = 8
        static let statusGap: CGFloat = 4
        static let statusDot: CGFloat = 5
        static let statusDotMargin: CGFloat = 1

        // MARK: Marks (the box a mark is designed in)

        static let heartSize: CGFloat = 18
        static let rewatchSize: CGFloat = 22
        /// Horizontal, each side, in the headline row.
        static let markMargin: CGFloat = -1

        // MARK: Poster

        static let posterShadowBlur: CGFloat = 9
        static let posterShadowOffsetY: CGFloat = 5
        static let posterShadowInset: CGFloat = 4
        /// Of the poster's accent color.
        static let posterShadowOpacity: Double = 0.5
        static let posterContactBlur: CGFloat = 3
        static let posterContactOffsetY: CGFloat = 1
        static let posterContactOpacity: Double = 0.4
        /// White, fading to 0 at 40 % of the 160-degree line.
        static let posterGlossTop: Double = 0.14
        /// An inside hairline, 0.5 pt.
        static let posterInnerRim: Double = 0.18
        /// An outside hairline, 0.5 pt.
        static let posterOuterRim: Double = 0.30

        // MARK: Text lift

        /// Black, 1 pt down, no blur.
        static let darkLiftOpacity: Double = 0.32
        /// White, 3 pt blur.
        static let lightHaloOpacity: Double = 0.30

        // MARK: Pill highlights

        static let pillHighlightDark: Double = 0.30
        static let pillHighlightLight: Double = 0.75
    }
}
