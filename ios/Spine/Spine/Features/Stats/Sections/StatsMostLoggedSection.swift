import SwiftUI

/// Most logged (STAT-215): titles logged at least twice, as a four-column poster grid with
/// "5 times" or "Twice" under each poster, titled per type ("Most watched", "Most read").
/// Each grid row is its own item in the page's lazy stack.
struct StatsMostLoggedSection: View {
    let mostLogged: StatsMostLogged
    /// The tab, for the title when the payload has none: nil on Global.
    let mediaType: String?
    let onOpenTitle: (MediaSummary) -> Void
    var onShowAll: (() -> Void)?

    enum Row: Identifiable {
        case title
        case posters(StatsPosterGridRow<StatsMostLoggedTitle>, isLast: Bool)

        var id: String {
            switch self {
            case .title: "mostLogged.title"
            case let .posters(row, _): row.id
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: mostLogged)) { row in
            switch row {
            case .title:
                StatsSectionTitle(Self.title(for: mostLogged, mediaType: mediaType)) {
                    if let onShowAll, mostLogged.total > mostLogged.items.count {
                        StatsShowAllButton(action: onShowAll)
                    }
                }
            case let .posters(gridRow, isLast):
                StatsPosterGridRowView(row: gridRow) { title in
                    StatsMostLoggedPosterCell(title: title) {
                        onOpenTitle(title.media)
                    }
                }
                .padding(.bottom, isLast ? StatsSectionLayout.sectionGap : StatsPosterGridLayout.rowSpacing)
            }
        }
    }

    // MARK: - Data shaping

    static func rows(for mostLogged: StatsMostLogged) -> [Row] {
        let gridRows = StatsPosterGridLayout.rows(mostLogged.items, idPrefix: "mostLogged")
        guard !gridRows.isEmpty else { return [] }
        return [.title] + gridRows.map { .posters($0, isLast: $0.index == gridRows.count - 1) }
    }

    /// The payload's label, or the same words worked out from the tab.
    static func title(for mostLogged: StatsMostLogged, mediaType: String?) -> String {
        let text = mostLogged.label.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? label(for: mediaType) : text
    }

    /// "Most watched" for movies, TV and anime, "Most read" for books, manga and comics,
    /// "Most played" for games, "Most listened" for music, "Most logged" on Global.
    static func label(for mediaType: String?) -> String {
        switch mediaType {
        case "movie", "tv", "anime": "Most watched"
        case "book", "manga", "comic": "Most read"
        case "game": "Most played"
        case "music": "Most listened"
        default: "Most logged"
        }
    }

    /// "Twice", "5 times"; "Once" only if a row ever says 1.
    static func timesCaption(_ times: Int) -> String {
        switch times {
        case 1: "Once"
        case 2: "Twice"
        default: "\(times.formatted()) times"
        }
    }
}

/// A most-logged poster with its "5 times" caption. Shared with the show-all page.
struct StatsMostLoggedPosterCell: View {
    let title: StatsMostLoggedTitle
    let action: () -> Void

    var body: some View {
        StatsPosterCell(
            media: title.media,
            accessibilityText: "\(title.media.displayTitle), \(StatsMostLoggedSection.timesCaption(title.times))",
            action: action
        ) {
            StatsPosterCaption(text: StatsMostLoggedSection.timesCaption(title.times))
        }
    }
}
