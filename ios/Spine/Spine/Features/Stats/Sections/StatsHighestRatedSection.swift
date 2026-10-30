import SwiftUI

/// Highest rated (STAT-205): a compact top 10 in the server's order, two rows of five posters
/// the same size, each with its rank, and "Show all". The page shows it only
/// when Top comes from a list (`isShown(top:highestRated:)`), so the two never repeat each
/// other; Top's own fallback uses the same bento.
struct StatsHighestRatedSection: View {
    let highestRated: StatsHighestRated
    let onOpenTitle: (MediaSummary) -> Void
    var onShowAll: (() -> Void)?

    enum Row: Identifiable {
        case title
        case bento(StatsBento)

        var id: String {
            switch self {
            case .title: "highest.title"
            case let .bento(bento): bento.id
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: highestRated)) { row in
            switch row {
            case .title:
                StatsSectionTitle("Highest rated") {
                    if let onShowAll, Self.hasMore(highestRated) {
                        StatsShowAllButton(action: onShowAll)
                    }
                }
            case let .bento(bento):
                StatsBentoView(bento: bento, onOpenTitle: onOpenTitle)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
            }
        }
    }

    // MARK: - Data shaping

    /// Only when Top comes from a list; otherwise Top already shows these titles.
    static func isShown(top: StatsTop?, highestRated: StatsHighestRated?) -> Bool {
        guard let highestRated, !highestRated.isEmpty else { return false }
        return top?.isFromList == true
    }

    /// More than the bento shows, counting the rows the payload sent but the bento leaves out.
    static func hasMore(_ highestRated: StatsHighestRated) -> Bool {
        highestRated.total > min(highestRated.items.count, StatsBento.capacity)
    }

    static func rows(for highestRated: StatsHighestRated) -> [Row] {
        let entries = highestRated.items.enumerated().map { index, title in
            StatsBentoEntry(rank: index + 1, media: title.media, stars: title.stars?.value)
        }
        guard let bento = StatsBento(entries, id: "highest.bento") else { return [] }
        return [.title, .bento(bento)]
    }
}

// MARK: - Bento

/// One poster of the bento.
struct StatsBentoEntry: Identifiable, Hashable {
    let rank: Int
    let media: MediaSummary
    let stars: Double?

    var id: String { media.id }
}

/// The first ten titles as a bento: two rows of five, all the same size. It's one row of the
/// page's lazy stack, a fixed-size block like the header's numbers card.
struct StatsBento: Identifiable, Hashable {
    static let columns = 5
    static var capacity: Int { columns * 2 }

    let id: String
    let entries: [StatsBentoEntry]

    /// Nil without any entries; entries past the tenth are left out.
    init?(_ entries: [StatsBentoEntry], id: String) {
        guard !entries.isEmpty else { return nil }
        self.id = id
        self.entries = Array(entries.prefix(Self.capacity))
    }
}

/// The bento's measurements. Every poster keeps the slot's fixed size, so artwork decodes once
/// at the size it shows; the width left over is shared by the four gaps across, so the columns
/// sit evenly on every phone.
enum StatsBentoMetrics {
    static let slot: PosterSlot = .statsBentoTile
    static let rowGap: CGFloat = 12

    static var height: CGFloat { slot.size.height * 2 + rowGap }
    /// The five columns' own width, with no gaps.
    static var postersWidth: CGFloat { slot.size.width * CGFloat(StatsBento.columns) }

    /// One of the four equal gaps across `width`.
    static func gap(width: CGFloat) -> CGFloat {
        max((width - postersWidth) / CGFloat(StatsBento.columns - 1), 0)
    }

    /// Each poster's top-leading corner, in reading order: #1 to #5, then #6 to #10.
    static func origins(width: CGFloat, count: Int) -> [CGPoint] {
        let gap = gap(width: width)
        return (0 ..< min(count, StatsBento.capacity)).map { index in
            CGPoint(
                x: CGFloat(index % StatsBento.columns) * (slot.size.width + gap),
                y: CGFloat(index / StatsBento.columns) * (slot.size.height + rowGap)
            )
        }
    }
}

/// Places the bento's posters at `StatsBentoMetrics.origins`, each at the slot's fixed size.
struct StatsBentoLayout: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        CGSize(width: proposal.width ?? StatsBentoMetrics.postersWidth, height: StatsBentoMetrics.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let origins = StatsBentoMetrics.origins(width: bounds.width, count: subviews.count)
        for (subview, origin) in zip(subviews, origins) {
            subview.place(at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y), proposal: .unspecified)
        }
    }
}

/// The bento: two rows of five posters.
struct StatsBentoView: View {
    let bento: StatsBento
    let onOpenTitle: (MediaSummary) -> Void

    var body: some View {
        StatsBentoLayout {
            ForEach(bento.entries) { entry in
                StatsBentoCell(entry: entry) { onOpenTitle(entry.media) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A bento poster with its rank in the corner (and "S2" for a season, which can share its
/// show's artwork).
struct StatsBentoCell: View {
    let entry: StatsBentoEntry
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            MediaArtwork(
                url: entry.media.displayPosterURL,
                title: entry.media.displayTitle,
                slot: StatsBentoMetrics.slot,
                mediaType: entry.media.ref.mediaType,
                orientation: entry.media.posterOrientation
            )
            .overlay(alignment: .topLeading) {
                StatsBentoBadge(text: "\(entry.rank)")
            }
            .overlay(alignment: .bottomLeading) {
                if let season = StatsRatedPosterCell.seasonTag(for: entry.media.ref) {
                    StatsBentoBadge(text: season)
                }
            }
            .shadow(color: .black.opacity(0.28), radius: 6, y: 3)
            .frame(width: StatsBentoMetrics.slot.size.width, height: StatsBentoMetrics.slot.size.height, alignment: .bottom)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityLabel(for: entry))
        .accessibilityAddTraits(.isButton)
    }

    /// "Number 2, Severance S2, Rated 4.5 out of 5 stars".
    static func accessibilityLabel(for entry: StatsBentoEntry) -> String {
        var parts = ["Number \(entry.rank)", entry.media.displayTitle]
        if let stars = entry.stars {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}

/// A rank or season label on a poster's corner: white on a dark rounded square, so it reads on
/// any artwork.
private struct StatsBentoBadge: View {
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

/// A rated poster: the artwork and its gold stars, and "S2" or "Specials" under a season, which
/// is rated on its own and can share its show's artwork. Highest rated's show-all page uses it.
struct StatsRatedPosterCell: View {
    let title: StatsRatedTitle
    let action: () -> Void

    var body: some View {
        StatsPosterCell(media: title.media, accessibilityText: Self.accessibilityText(for: title), action: action) {
            VStack(alignment: .leading, spacing: 3) {
                if let stars = title.stars?.value {
                    StatsStars(stars: stars, size: 10)
                } else {
                    Color.clear.frame(height: 12)
                }
                if let season = Self.seasonTag(for: title.media.ref) {
                    StatsPosterCaption(text: season)
                }
            }
        }
    }

    /// "S2" for a season, "Specials" for season 0, nil for anything else.
    static func seasonTag(for ref: MediaRef) -> String? {
        guard ref.mediaType == "season", let number = ref.seasonNumber else { return nil }
        return number == 0 ? "Specials" : "S\(number)"
    }

    static func accessibilityText(for title: StatsRatedTitle) -> String {
        var parts = [title.media.displayTitle]
        if let stars = title.stars?.value {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}
