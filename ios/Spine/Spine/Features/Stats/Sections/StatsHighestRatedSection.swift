import SwiftUI

/// Highest rated (STAT-205): a four-column poster grid in the server's order, gold stars under
/// each poster, and "Show all". When Top comes from a list, the server leaves Top's titles out of
/// it, so the two never repeat each other. Each grid row is its own item in the page's lazy stack.
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

    /// Whenever there's something rated; Top no longer stands in for it.
    static func isShown(_ highestRated: StatsHighestRated?) -> Bool {
        guard let highestRated else { return false }
        return !highestRated.isEmpty
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

/// A rated poster: the artwork and its gold stars, and "S2" or "Specials" under a season, which
/// is rated on its own and can share its show's artwork. Shared with the show-all page.
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
