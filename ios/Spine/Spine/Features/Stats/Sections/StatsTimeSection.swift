import SwiftUI

/// Time (STAT-201, STAT-202): one row per type in its own unit, "≈" when estimated. On Global
/// a row opens that type's tab, and "Total hours" adds up the types counted in minutes, with a
/// note naming the ones it leaves out. One view per row, for the page's lazy stack.
struct StatsTimeSection: View {
    let time: StatsTime
    /// The tab: nil on Global.
    let mediaType: String?
    /// Global only: a tapped row switches to its type.
    var onSelectMediaType: ((String) -> Void)?

    enum Row: Hashable, Identifiable {
        case title
        case type(StatsTimeRow, isFirst: Bool, isLast: Bool)
        case total(StatsHoursTotal, isLast: Bool)
        case note(String)

        var id: String {
            switch self {
            case .title: "time.title"
            case let .type(row, _, _): "time.type.\(row.mediaType)"
            case .total: "time.total"
            case .note: "time.note"
            }
        }
    }

    var body: some View {
        let rows = Self.rows(for: time, mediaType: mediaType)
        let hasNote = rows.contains { if case .note = $0 { true } else { false } }
        ForEach(rows) { row in
            switch row {
            case .title:
                StatsSectionTitle("Time")
            case let .type(timeRow, isFirst, isLast):
                typeRow(timeRow, isLast: isLast)
                    .statsCardSlice(isFirst: isFirst, isLast: isLast, gapAfter: gap(hasNote: hasNote))
            case let .total(total, isLast):
                StatsLabelValueRow(label: "Total hours", value: Self.totalValue(total))
                    .statsCardSlice(isFirst: false, isLast: isLast, gapAfter: gap(hasNote: hasNote))
            case let .note(note):
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.5))
                    .padding(.horizontal, 4)
                    .padding(.top, 8)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func gap(hasNote: Bool) -> CGFloat {
        hasNote ? 0 : StatsSectionLayout.sectionGap
    }

    @ViewBuilder
    private func typeRow(_ row: StatsTimeRow, isLast: Bool) -> some View {
        let label = MediaTypeTheme.theme(for: row.mediaType).displayName
        let value = Self.value(for: row)
        if mediaType == nil, let onSelectMediaType {
            Button {
                onSelectMediaType(row.mediaType)
            } label: {
                StatsLabelValueRow(label: label, value: value, showsChevron: true, showsSeparator: !isLast)
            }
            .buttonStyle(DiaryRowButtonStyle())
            .accessibilityHint("Shows \(label) stats")
        } else {
            StatsLabelValueRow(label: label, value: value, showsSeparator: !isLast)
        }
    }

    // MARK: - Data shaping

    /// The total shows on Global when at least two of the rows are counted in minutes;
    /// with one, it would only repeat that row.
    static func rows(for time: StatsTime, mediaType: String?) -> [Row] {
        guard !time.rows.isEmpty else { return [] }
        let total = mediaType == nil ? time.hoursTotal : nil
        let showsTotal = total != nil && time.rows.filter { $0.unit == "minutes" }.count >= 2

        var rows: [Row] = [.title]
        for (index, row) in time.rows.enumerated() {
            let isLast = index == time.rows.count - 1 && !showsTotal
            rows.append(.type(row, isFirst: index == 0, isLast: isLast))
        }
        if showsTotal, let total {
            rows.append(.total(total, isLast: true))
            if let note = leavesOutNote(total.leavesOut) {
                rows.append(.note(note))
            }
        }
        return rows
    }

    /// "212 hours", "≈ 340 hours", "45 minutes", "4,310 pages", "812 chapters", "64 issues".
    static func value(for row: StatsTimeRow) -> String {
        let text: String
        switch row.unit {
        case "minutes":
            text = duration(minutes: row.value)
        default:
            let unit = row.unit.hasSuffix("s") ? String(row.unit.dropLast()) : row.unit
            text = "\(row.value.formatted()) \(row.value == 1 ? unit : unit + "s")"
        }
        return row.approximate ? "≈ \(text)" : text
    }

    /// Whole hours from an hour up, minutes below.
    static func duration(minutes: Int) -> String {
        guard minutes >= 60 else {
            return "\(minutes.formatted()) \(minutes == 1 ? "minute" : "minutes")"
        }
        let hours = Int((Double(minutes) / 60).rounded())
        return "\(hours.formatted()) \(hours == 1 ? "hour" : "hours")"
    }

    /// The total's number of hours, "≈ 1,150" when any part is estimated.
    static func totalValue(_ total: StatsHoursTotal) -> String {
        let hours = Int((Double(total.minutes) / 60).rounded()).formatted()
        return total.approximate ? "≈ \(hours)" : hours
    }

    /// "Doesn't include books, manga or comics", in the order the server lists them.
    static func leavesOutNote(_ mediaTypes: [String]) -> String? {
        let nouns = mediaTypes.map { pluralNoun(for: $0) }
        guard let last = nouns.last else { return nil }
        let list = nouns.count == 1 ? last : nouns.dropLast().joined(separator: ", ") + " or " + last
        return "Doesn't include \(list)"
    }

    /// The type in a sentence: "movies", "TV", "anime", "books".
    static func pluralNoun(for mediaType: String) -> String {
        switch mediaType {
        case "movie": "movies"
        case "tv": "TV"
        case "anime": "anime"
        case "manga": "manga"
        case "game": "games"
        case "book": "books"
        case "comic": "comics"
        case "music": "music"
        default: MediaTypeTheme.theme(for: mediaType).displayName.lowercased()
        }
    }
}
