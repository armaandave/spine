import SwiftUI

/// Top from a tagged list (STAT-206, STAT-207): the list's first ten titles in its own order,
/// two rows of five posters the same size, each with its rank. It's one row of the page's lazy
/// stack, a fixed-size block like the header's numbers card.
enum StatsTopGridMetrics {
    static let columns = 5
    static var capacity: Int { columns * 2 }
    static let slot: PosterSlot = .statsTopGrid
    static let rowGap: CGFloat = 12

    static func height(count: Int) -> CGFloat {
        let rows = count > columns ? 2 : 1
        return slot.size.height * CGFloat(rows) + rowGap * CGFloat(rows - 1)
    }

    /// The five columns' own width, with no gaps.
    static var postersWidth: CGFloat { slot.size.width * CGFloat(columns) }

    /// One of the four equal gaps across `width`.
    static func gap(width: CGFloat) -> CGFloat {
        max((width - postersWidth) / CGFloat(columns - 1), 0)
    }

    /// Each poster's top-leading corner, in reading order: #1 to #5, then #6 to #10.
    static func origins(width: CGFloat, count: Int) -> [CGPoint] {
        let gap = gap(width: width)
        return (0 ..< min(count, capacity)).map { index in
            CGPoint(
                x: CGFloat(index % columns) * (slot.size.width + gap),
                y: CGFloat(index / columns) * (slot.size.height + rowGap)
            )
        }
    }
}

/// Places the grid's posters at `StatsTopGridMetrics.origins`, each at the slot's fixed size.
struct StatsTopGridLayout: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        CGSize(
            width: proposal.width ?? StatsTopGridMetrics.postersWidth,
            height: StatsTopGridMetrics.height(count: subviews.count)
        )
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let origins = StatsTopGridMetrics.origins(width: bounds.width, count: subviews.count)
        for (subview, origin) in zip(subviews, origins) {
            subview.place(at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y), proposal: .unspecified)
        }
    }
}

/// The list's top ten: two rows of five posters.
struct StatsTopGridView: View {
    let items: [StatsTopItem]
    let onOpenTitle: (MediaSummary) -> Void

    var body: some View {
        StatsTopGridLayout {
            ForEach(items.prefix(StatsTopGridMetrics.capacity)) { item in
                StatsTopGridCell(item: item) { onOpenTitle(item.media) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A grid poster with its rank in the corner (and "S2" for a season, which can share its
/// show's artwork).
struct StatsTopGridCell: View {
    let item: StatsTopItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            MediaArtwork(
                url: item.media.displayPosterURL,
                title: item.media.displayTitle,
                slot: StatsTopGridMetrics.slot,
                mediaType: item.media.ref.mediaType,
                orientation: item.media.posterOrientation
            )
            .overlay(alignment: .topLeading) {
                StatsTopGridBadge(text: "\(item.rank)")
            }
            .overlay(alignment: .bottomLeading) {
                if let season = StatsRatedPosterCell.seasonTag(for: item.media.ref) {
                    StatsTopGridBadge(text: season)
                }
            }
            .shadow(color: .black.opacity(0.28), radius: 6, y: 3)
            .frame(width: StatsTopGridMetrics.slot.size.width, height: StatsTopGridMetrics.slot.size.height, alignment: .bottom)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(StatsTopSection.accessibilityLabel(for: item))
        .accessibilityAddTraits(.isButton)
    }
}

/// A rank or season label on a poster's corner: white on a dark rounded square, so it reads on
/// any artwork.
private struct StatsTopGridBadge: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .bold))
            .monospacedDigit()
            .foregroundStyle(.white)
            .lineLimit(1)
            .padding(.horizontal, 5)
            .frame(minWidth: 18, minHeight: 18)
            .background(.black.opacity(0.62), in: RoundedRectangle(cornerRadius: 5, style: .continuous))
            .padding(4)
            .accessibilityHidden(true)
    }
}
