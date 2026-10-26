import SwiftUI

/// Highest rated (STAT-205): a four-column poster grid in the server's order, gold stars under
/// each poster, and "Show all". The page shows it only when Top comes from a list
/// (`isShown(top:highestRated:)`), so the two never repeat each other. Each grid row is its own
/// item in the page's lazy stack.
struct StatsHighestRatedSection: View {
    let highestRated: StatsHighestRated
    let onOpenTitle: (MediaSummary) -> Void
    var onShowAll: (() -> Void)?

    enum Row: Identifiable {
        case title
        case posters(StatsPosterGridRow<StatsRatedTitle>, isLast: Bool)

        var id: String {
            switch self {
            case .title: "highest.title"
            case let .posters(row, _): row.id
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
            case let .posters(gridRow, isLast):
                StatsPosterGridRowView(row: gridRow) { title in
                    StatsRatedPosterCell(title: title) {
                        onOpenTitle(title.media)
                    }
                }
                .padding(.bottom, isLast ? StatsSectionLayout.sectionGap : StatsPosterGridLayout.rowSpacing)
            }
        }
    }

    // MARK: - Data shaping

    /// Only when Top comes from a list; otherwise Top already shows these titles.
    static func isShown(top: StatsTop?, highestRated: StatsHighestRated?) -> Bool {
        guard let highestRated, !highestRated.isEmpty else { return false }
        return top?.isFromList == true
    }

    static func hasMore(_ highestRated: StatsHighestRated) -> Bool {
        highestRated.total > highestRated.items.count
    }

    static func rows(for highestRated: StatsHighestRated) -> [Row] {
        let gridRows = StatsPosterGridLayout.rows(highestRated.items, idPrefix: "highest")
        guard !gridRows.isEmpty else { return [] }
        return [.title] + gridRows.map { .posters($0, isLast: $0.index == gridRows.count - 1) }
    }
}

/// A rated poster: the artwork and its gold stars. Shared with the show-all page.
struct StatsRatedPosterCell: View {
    let title: StatsRatedTitle
    let action: () -> Void

    var body: some View {
        StatsPosterCell(media: title.media, accessibilityText: Self.accessibilityText(for: title), action: action) {
            if let stars = title.stars?.value {
                StatsStars(stars: stars, size: 10)
            } else {
                Color.clear.frame(height: 12)
            }
        }
    }

    static func accessibilityText(for title: StatsRatedTitle) -> String {
        var parts = [title.media.displayTitle]
        if let stars = title.stars?.value {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}
