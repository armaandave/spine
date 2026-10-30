import SwiftUI

/// Top (STAT-206 to STAT-208): the tagged list's name, its first ten titles in two rows of five,
/// and "See full list". Without a tagged list the owner sees an empty card that says how to tag
/// one, and other viewers see nothing. One view per row, for the page's lazy stack.
struct StatsTopSection: View {
    let top: StatsTop
    let onOpenTitle: (MediaSummary) -> Void
    var onOpenList: ((StatsTopList) -> Void)?

    enum Row: Hashable, Identifiable {
        case title
        /// The list's first ten, in its order; `hasNote` when a note follows it.
        case grid([StatsTopItem], hasNote: Bool)
        case note(String, systemImage: String)
        /// No tagged list: how to tag one, for the owner.
        case empty(String)

        var id: String {
            switch self {
            case .title: "top.title"
            case .grid: "top.grid"
            case let .note(text, _): "top.note.\(text)"
            case .empty: "top.empty"
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: top)) { row in
            switch row {
            case .title:
                StatsSectionTitle(Self.title(for: top)) {
                    if top.isFromList, let list = top.list, let onOpenList {
                        StatsShowAllButton(title: "See full list") { onOpenList(list) }
                            .accessibilityLabel("See full list, \(list.total.formatted()) titles")
                    }
                }
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
            case let .empty(message):
                Label(message, systemImage: "tag")
                    .font(.subheadline)
                    .foregroundStyle(StatsChartStyle.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
                    .statsCardSlice(isFirst: true, isLast: true)
            }
        }
    }

    // MARK: - Data shaping

    /// The tagged list's name; "Top 10" while there's no tagged list.
    static func title(for top: StatsTop) -> String {
        guard top.isFromList else { return "Top 10" }
        let name = top.list?.name.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return name.isEmpty ? "Top" : name
    }

    /// The owner's hint without a tagged list, as the server words it: "Tag a ranked movie list
    /// top2025" (STAT-208). Other viewers get no hint (STAT-004).
    static func hintText(for top: StatsTop) -> String? {
        guard !top.isFromList,
              let hint = top.tagHint?.trimmingCharacters(in: .whitespacesAndNewlines),
              !hint.isEmpty else { return nil }
        return hint
    }

    /// "Tag a ranked movie list top2025 to show it here."
    static func emptyMessage(for top: StatsTop) -> String? {
        hintText(for: top).map { "\($0) to show it here." }
    }

    static func rows(for top: StatsTop) -> [Row] {
        guard top.isFromList else {
            guard let message = emptyMessage(for: top) else { return [] }
            return [.title, .empty(message)]
        }

        let items = top.items.sorted { $0.rank < $1.rank }
        guard !items.isEmpty else { return [] }
        let note: Row? = top.hintPrivate ? .note("Only you can see this list.", systemImage: "lock") : nil
        var rows: [Row] = [.title, .grid(Array(items.prefix(StatsTopGridMetrics.capacity)), hasNote: note != nil)]
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
