import SwiftUI

/// Milestones (STAT-216): the 1st, 100th, 250th… log as poster tiles, each with its number and
/// day ("100th log", "12 Mar 2024"). Each grid row is its own item in the page's lazy stack.
struct StatsMilestonesSection: View {
    let milestones: [StatsMilestone]
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
                        accessibilityText: Self.accessibilityText(for: milestone),
                        action: { onOpenMilestone(milestone) }
                    ) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(Self.numberLabel(milestone.number))
                                .font(.caption.weight(.semibold))
                                .monospacedDigit()
                                .foregroundStyle(.white.opacity(0.85))
                            Text(StatsTitleFormat.day(milestone.day) ?? milestone.day)
                                .font(.caption2)
                                .foregroundStyle(.white.opacity(0.5))
                        }
                        .lineLimit(1)
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

    /// "1st log", "100th log", "1,000th log".
    static func numberLabel(_ number: Int) -> String {
        "\(StatsTitleFormat.ordinal(number)) log"
    }

    static func accessibilityText(for milestone: StatsMilestone, locale: Locale = .current) -> String {
        let day = StatsTitleFormat.day(milestone.day, locale: locale) ?? milestone.day
        return "\(numberLabel(milestone.number)), \(milestone.media.displayTitle), \(day)"
    }
}
