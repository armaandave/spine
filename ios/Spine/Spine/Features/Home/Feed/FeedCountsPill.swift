import SwiftUI

/// The like and comment counts at the bottom right of a card: the heart and the bubble close together in one soft
/// glossy pill. A liked heart is the filled duotone mark and an unliked one only its rim; the bubble is its rim. Read-only:
/// a tap anywhere on the card, the pill included, opens the post. A zero count still reads "0".
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
                    .frame(width: Depth.countsIcon, height: Depth.countsIcon)
                Text(Self.countText(engagement.likeCount))
            }
            HStack(spacing: Depth.countsPairGap) {
                CommentBubbleShape()
                    .stroke(rimColor, style: StrokeStyle(lineWidth: 1.1, lineJoin: .round))
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

    @ViewBuilder
    private var heart: some View {
        if engagement.hasLiked {
            FeedMarkView(mark: .heart, ink: ink, size: Depth.countsIcon)
        } else {
            HeartShape()
                .stroke(rimColor, style: StrokeStyle(lineWidth: 2 * Depth.countsIcon / 24, lineJoin: .round))
        }
    }
}
