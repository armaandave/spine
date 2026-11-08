import SwiftUI

/// A feed card's place while the feed loads: the card's size and shape with faint bars where the poster and the
/// text will be. Still, so waiting costs nothing to draw. Home shows three.
struct FeedCardSkeleton: View {
    private static let fill = Color.white.opacity(0.07)
    private static let bar = Color.white.opacity(0.09)

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: HomeDesign.Metric.cardCorner, style: .continuous)
                .fill(Self.fill)

            RoundedRectangle(cornerRadius: PosterSlot.feedCard.cornerRadius, style: .continuous)
                .fill(Self.bar)
                .frame(width: HomeDesign.Metric.cardPoster.width, height: HomeDesign.Metric.cardPoster.height)
                .padding(.leading, HomeDesign.Metric.cardInset)
                .padding(.top, HomeDesign.Metric.cardInset)

            VStack(alignment: .leading, spacing: 0) {
                skeletonBar(width: 84, height: 10)
                    .frame(height: FeedAuthorLink.height, alignment: .leading)
                skeletonBar(width: 150, height: 14)
                    .frame(height: FeedCardView.titleRowHeight, alignment: .leading)
                    .padding(.top, FeedCardView.titleTop)
                skeletonBar(width: 96, height: 10)
                    .frame(height: FeedCardView.headlineHeight, alignment: .leading)
                    .padding(.top, FeedCardView.headlineTop)
                skeletonBar(width: 150, height: 9)
                    .frame(height: 19, alignment: .leading)
                    .padding(.top, 4)
            }
            .padding(.top, HomeDesign.Metric.cardTextTop)
            .padding(.leading, HomeDesign.Metric.cardTextLeading)
            .padding(.trailing, HomeDesign.Metric.cardTrailing)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .frame(maxWidth: .infinity)
        .frame(height: HomeDesign.Metric.cardHeight)
        .accessibilityHidden(true)
    }

    private func skeletonBar(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: height / 2, style: .continuous)
            .fill(Self.bar)
            .frame(width: width, height: height)
    }
}
