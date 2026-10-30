import SwiftUI

/// Top (STAT-206 to STAT-208): the tagged list's name, its first ten titles in two rows of five,
/// and "See full list". Without a tagged list it's "Highest rated", drawn like the Highest rated
/// section, and the owner sees how to tag one. One view per row, for the page's lazy stack.
struct StatsTopSection: View {
    let top: StatsTop
    /// Highest rated's total, for the fallback's "Show all".
    var highestRatedTotal = 0
    let onOpenTitle: (MediaSummary) -> Void
    var onOpenList: ((StatsTopList) -> Void)?
    /// The fallback's "Show all": Highest rated's show-all page.
    var onShowAllHighestRated: (() -> Void)?

    enum Row: Hashable, Identifiable {
        /// The title, with "Show all" for the fallback when Highest rated has more.
        case title(showsAll: Bool)
        /// The list's first ten, in its order; `hasNote` when a note follows it.
        case grid([StatsTopItem], hasNote: Bool)
        case note(String, systemImage: String)
        /// A row of the fallback's posters, as the Highest rated section draws them; `isLast` on
        /// the final row, `hasHint` when the owner's hint follows it.
        case ratedPosters(StatsPosterGridRow<StatsRatedTitle>, isLast: Bool, hasHint: Bool)
        /// The owner's hint under the fallback's posters.
        case hint(String)

        var id: String {
            switch self {
            case .title: "top.title"
            case .grid: "top.grid"
            case let .note(text, _): "top.note.\(text)"
            case let .ratedPosters(row, _, _): "top.\(row.id)"
            case .hint: "top.hint"
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: top, highestRatedTotal: highestRatedTotal, canShowAll: onShowAllHighestRated != nil)) { row in
            switch row {
            case let .title(showsAll):
                titleRow(showsAll: showsAll)
            case let .grid(items, hasNote):
                StatsTopGridView(items: items, onOpenTitle: onOpenTitle)
                    .padding(.bottom, hasNote ? 4 : StatsSectionLayout.sectionGap)
            case let .note(text, systemImage):
                Label(text, systemImage: systemImage)
                    .font(.footnote)
                    .foregroundStyle(StatsChartStyle.secondaryText)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4)
                    .padding(.top, 8)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
            case let .ratedPosters(gridRow, isLast, hasHint):
                StatsPosterGridRowView(row: gridRow) { title in
                    StatsRatedPosterCell(title: title) {
                        onOpenTitle(title.media)
                    }
                }
                .padding(.bottom, isLast ? (hasHint ? 4 : StatsSectionLayout.sectionGap) : StatsPosterGridLayout.rowSpacing)
            case let .hint(text):
                Label(text, systemImage: "tag")
                    .font(.footnote)
                    .foregroundStyle(StatsChartStyle.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4)
                    .padding(.top, 8)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
            }
        }
    }

    @ViewBuilder
    private func titleRow(showsAll: Bool) -> some View {
        if !top.isFromList {
            StatsSectionTitle(Self.title(for: top)) {
                if showsAll, let onShowAllHighestRated {
                    StatsShowAllButton(action: onShowAllHighestRated)
                }
            }
        } else {
            StatsSectionTitle(Self.title(for: top)) {
                if let list = top.list, let onOpenList {
                    StatsShowAllButton(title: "See full list") { onOpenList(list) }
                        .accessibilityLabel("See full list, \(list.total.formatted()) titles")
                }
            }
        }
    }

    // MARK: - Data shaping

    /// The tagged list's name, "Highest rated" for the fallback.
    static func title(for top: StatsTop) -> String {
        guard top.isFromList else { return "Highest rated" }
        let name = top.list?.name.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return name.isEmpty ? "Top" : name
    }

    /// The owner's hint in the fallback, as the server words it: "Tag a ranked movie list
    /// top2025" (STAT-208). Other viewers get no hint (STAT-004).
    static func hintText(for top: StatsTop) -> String? {
        guard !top.isFromList,
              let hint = top.tagHint?.trimmingCharacters(in: .whitespacesAndNewlines),
              !hint.isEmpty else { return nil }
        return hint
    }

    static func rows(for top: StatsTop, highestRatedTotal: Int = 0, canShowAll: Bool = true) -> [Row] {
        let items = top.items.sorted { $0.rank < $1.rank }
        guard !items.isEmpty else { return [] }

        // The fallback is Highest rated, drawn like the Highest rated section: its poster rows,
        // the owner's hint, and "Show all" by the title when Highest rated has more.
        guard top.isFromList else {
            let titles = items.map { StatsRatedTitle(media: $0.media, stars: $0.stars, day: nil) }
            let gridRows = StatsPosterGridLayout.rows(titles, idPrefix: "rated")
            let hint = hintText(for: top)
            var rows: [Row] = [.title(showsAll: canShowAll && highestRatedTotal > titles.count)]
            rows += gridRows.map { .ratedPosters($0, isLast: $0.index == gridRows.count - 1, hasHint: hint != nil) }
            if let hint {
                rows.append(.hint(hint))
            }
            return rows
        }

        let note: Row? = top.hintPrivate ? .note("Only you can see this list.", systemImage: "lock") : nil
        var rows: [Row] = [.title(showsAll: false), .grid(Array(items.prefix(StatsTopGridMetrics.capacity)), hasNote: note != nil)]
        if let note {
            rows.append(note)
        }
        return rows
    }

    static func accessibilityLabel(for item: StatsTopItem) -> String {
        var parts = ["Number \(item.rank)", item.media.displayTitle]
        if let year = StatsTitleFormat.year(of: item.media) {
            parts.append(year)
        }
        if let stars = item.stars?.value {
            parts.append(StatsStars.accessibilityLabel(for: stars))
        }
        return parts.joined(separator: ", ")
    }
}
