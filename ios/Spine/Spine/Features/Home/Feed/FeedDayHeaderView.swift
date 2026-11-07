import SwiftUI

/// The day above a run of cards: "TODAY", "YESTERDAY", "SATURDAY", "NOV 3". A fixed height, so the lazy stack only
/// ever shifts known sizes. Not pinned: it scrolls away with its cards.
struct FeedDayHeaderView: View, Equatable {
    /// The line of text is this tall; 20 above and 10 below it come from `HomeDesign.Metric`.
    static let textHeight: CGFloat = 15

    /// The header's whole height, margins included.
    static let height: CGFloat = HomeDesign.Metric.dayHeaderTop + textHeight + HomeDesign.Metric.dayHeaderBottom

    let title: String

    var body: some View {
        Text(title)
            .font(HomeDesign.Text.dayHeader)
            .tracking(HomeDesign.Text.dayHeaderTracking)
            .textCase(.uppercase)
            .foregroundStyle(Color.white.opacity(0.72))
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: Self.textHeight)
            .padding(.horizontal, HomeDesign.Metric.pageMargin)
            .padding(.top, HomeDesign.Metric.dayHeaderTop)
            .padding(.bottom, HomeDesign.Metric.dayHeaderBottom)
            .dynamicTypeSize(...HomeDesign.Text.maxCategory)
            .accessibilityAddTraits(.isHeader)
    }
}
