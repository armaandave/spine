import SwiftUI

/// The like and comment counts at the bottom right of a card: the heart and the bubble close together in one soft
/// glossy pill. The two icons are drawn to the same size, rim included. A liked heart is filled and an unliked one only
/// its rim; the bubble is its rim. Read-only: a tap anywhere on the card, the pill included, opens the post. A zero
/// count still reads "0".
struct FeedCountsPill: View {
    let engagement: PostEngagement
    let ink: ArtInk

    typealias Depth = HomeDesign.FeedDepth

    /// The icons and the numbers.
    private var textColor: Color {
        ink == .dark ? Color.white.opacity(0.90) : HomeDesign.Palette.lightInk.opacity(0.82)
    }

    /// The rim-only heart and the bubble.
    private var rimColor: Color { ink.muted }

    /// The side of the box each icon's outline is drawn in: the icon's box less the rim, which reaches half its width
    /// past the outline on every side. The heart fills the width of its box and the bubble the whole of its own.
    static var outlineSide: CGFloat { Depth.countsIcon - Depth.countsRim }

    /// A count's words: always the number.
    static func countText(_ count: Int) -> String {
        count.formatted()
    }

    /// Room kept at the right of the detail rows and the meter for the pill, from the digits rather than measured. A
    /// card without counts (a Planning day) keeps none.
    static func reserve(for engagement: PostEngagement?) -> CGFloat {
        guard let engagement else { return 0 }
        let digits = countText(engagement.likeCount).count + countText(engagement.commentCount).count
        return Depth.countsReserveBase + Depth.countsReservePerDigit * CGFloat(digits)
    }

    var body: some View {
        HStack(spacing: Depth.countsPillGap) {
            HStack(spacing: Depth.countsPairGap) {
                heart
                    .padding(Depth.countsRim / 2)
                    .frame(width: Depth.countsIcon, height: Depth.countsIcon)
                Text(Self.countText(engagement.likeCount))
            }
            HStack(spacing: Depth.countsPairGap) {
                CommentBubbleShape()
                    .stroke(rimColor, style: Self.rim)
                    .padding(Depth.countsRim / 2)
                    .frame(width: Depth.countsIcon, height: Depth.countsIcon)
                Text(Self.countText(engagement.commentCount))
            }
        }
        .font(Depth.countsFont)
        .monospacedDigit()
        .foregroundStyle(textColor)
        .padding(.horizontal, Depth.countsPillHPad)
        .frame(height: Depth.countsPillHeight)
        .feedPillSurface(.counts(ink: ink), height: Depth.countsPillHeight)
    }

    private static let rim = StrokeStyle(lineWidth: Depth.countsRim, lineJoin: .round)

    /// The filled heart is the same outline with a fill under its rim, so it is no smaller than the empty one.
    private var heart: some View {
        ZStack {
            if engagement.hasLiked {
                HeartShape().fill(ink.heart)
            }
            HeartShape().stroke(engagement.hasLiked ? ink.heart : rimColor, style: Self.rim)
        }
    }
}
