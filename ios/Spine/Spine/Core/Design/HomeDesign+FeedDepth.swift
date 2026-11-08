import SwiftUI

extension HomeDesign {
    /// The numbers of the feed card's depth (direction B): the counts pill, the status pill, the poster's lift, the
    /// text lift. They sit in their own file so the tiles' tokens in `HomeDesign.swift` stay theirs.
    /// Points unless noted.
    nonisolated enum FeedDepth {
        // MARK: Counts pill

        static let countsPillHeight: CGFloat = 22
        static let countsPillHPad: CGFloat = 9
        /// Between the heart pair and the bubble pair.
        static let countsPillGap: CGFloat = 5
        /// Icon to number.
        static let countsPairGap: CGFloat = 3
        /// The box the heart and the bubble are each drawn to fill, rim included.
        static let countsIcon: CGFloat = 13
        /// The rim of both icons.
        static let countsRim: CGFloat = 1.1
        /// Used with `.monospacedDigit()`.
        static let countsFont = Font.system(size: 12, weight: .bold)
        /// The pill's right edge to the card's right edge.
        static let countsTrailing: CGFloat = 10
        /// The pill's bottom edge to the card's bottom edge.
        static let countsBottom: CGFloat = 8
        /// Room kept left of the pill for detail rows and the meter: 61 + 8 per character of the counts (a bold 12 pt
        /// monospaced digit measures 8, not the board's 7.2), which keeps at least 6 pt between them. The pill itself is
        /// 2 x 9 + 2 x (13 + 3) + 5 = 55 plus the characters.
        static let countsReserveBase: CGFloat = 61
        static let countsReservePerDigit: CGFloat = 8

        // MARK: Status pill

        static let statusPillHeight: CGFloat = 19
        static let statusFont = Font.system(size: 11, weight: .bold)
        static let statusLeading: CGFloat = 6
        static let statusTrailing: CGFloat = 8
        static let statusGap: CGFloat = 4
        static let statusDot: CGFloat = 5
        static let statusDotMargin: CGFloat = 1

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

        // MARK: Mark glow

        /// The baked glow behind the headline's heart, rewatch and stars, as a share of the strength it started with
        /// (opacity and reach both): 25 % softer.
        static let markGlowScale: Double = 0.75

        // MARK: Pill highlights

        static let pillHighlightDark: Double = 0.30
        static let pillHighlightLight: Double = 0.75
    }
}
