import SwiftUI

/// Milestones (STAT-216): the 1st, 100th, 250th… log as poster tiles, each with its number and
/// day ("100th log", "12 Mar 2024"). In a year they count that year's logs: "100th log of 2025",
/// "12 Mar". Each grid row is its own item in the page's lazy stack.
struct StatsMilestonesSection: View {
    let milestones: [StatsMilestone]
    /// The range: a year numbers its own logs.
    var period: StatsPeriod = .allTime
    /// Opens the milestone's log (`entry_id`), or its title when there's none.
    let onOpenMilestone: (StatsMilestone) -> Void

    enum Row: Identifiable {
        case title
        case posters(StatsPosterGridRow<StatsMilestone>, isLast: Bool)

        var id: String {
            switch self {
            case .title: "milestones.title"
            case let .posters(row, _): row.id
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: milestones)) { row in
            switch row {
            case .title:
                StatsSectionTitle("Milestones")
            case let .posters(gridRow, isLast):
                StatsPosterGridRowView(row: gridRow) { milestone in
                    StatsPosterCell(
                        media: milestone.media,
                        accessibilityText: Self.accessibilityText(for: milestone, period: period),
                        action: { onOpenMilestone(milestone) }
                    ) {
                        VStack(alignment: .leading, spacing: 1) {
                            // A year's label takes two lines ("100th log", "of 2025"), so
                            // every tile in it breaks in the same place.
                            Text(Self.numberLines(milestone.number, period: period).joined(separator: "\n"))
                                .font(.caption.weight(.semibold))
                                .monospacedDigit()
                                .foregroundStyle(.white.opacity(0.85))
                                .lineLimit(2)
                            Text(Self.dayLabel(milestone.day, period: period))
                                .font(.caption2)
                                .foregroundStyle(.white.opacity(0.5))
                                .lineLimit(1)
                        }
                        .minimumScaleFactor(0.8)
                    }
                }
                .padding(.bottom, isLast ? StatsSectionLayout.sectionGap : StatsPosterGridLayout.rowSpacing)
            }
        }
    }

    // MARK: - Data shaping

    static func rows(for milestones: [StatsMilestone]) -> [Row] {
        let sorted = milestones.sorted { $0.number < $1.number }
        let gridRows = StatsPosterGridLayout.rows(sorted, idPrefix: "milestones")
        guard !gridRows.isEmpty else { return [] }
        return [.title] + gridRows.map { .posters($0, isLast: $0.index == gridRows.count - 1) }
    }

    /// "1st log", "100th log", "1,000th log"; in a year, "100th log of 2025".
    static func numberLabel(_ number: Int, period: StatsPeriod = .allTime) -> String {
        numberLines(number, period: period).joined(separator: " ")
    }

    /// The label as the tile shows it: one line in All Time, two in a year.
    static func numberLines(_ number: Int, period: StatsPeriod) -> [String] {
        let label = "\(StatsTitleFormat.ordinal(number)) log"
        return period == .allTime ? [label] : [label, "of \(period.title)"]
    }

    /// "12 Mar 2024"; in a year, which the label already names, "12 Mar".
    static func dayLabel(_ day: String, period: StatsPeriod, locale: Locale = .current) -> String {
        let text = period == .allTime
            ? StatsTitleFormat.day(day, locale: locale)
            : StatsTitleFormat.dayWithoutYear(day, locale: locale)
        return text ?? day
    }

    static func accessibilityText(for milestone: StatsMilestone, period: StatsPeriod = .allTime, locale: Locale = .current) -> String {
        let day = StatsTitleFormat.day(milestone.day, locale: locale) ?? milestone.day
        return "\(numberLabel(milestone.number, period: period)), \(milestone.media.displayTitle), \(day)"
    }
}
