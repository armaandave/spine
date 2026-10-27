import SwiftUI

/// The header (STAT-200): four big numbers in one card, then a card of label/value details.
/// Its body is one view per row, so each row is its own item in the page's lazy stack.
struct StatsHeaderSection: View {
    let header: StatsHeader
    /// The tab: nil on Global.
    let mediaType: String?
    /// Opens the liked logs (STAT-401); nil on someone else's stats, where nothing opens.
    var onOpenLikes: (() -> Void)?

    struct Count: Hashable, Identifiable {
        let label: String
        let value: Int
        /// "days" beside the number, or nil.
        let unit: String?

        var id: String { label }
    }

    enum Row: Hashable, Identifiable {
        case counts
        case detail(StatsHeaderDetail, isFirst: Bool, isLast: Bool)

        var id: String {
            switch self {
            case .counts: "header.counts"
            case let .detail(detail, _, _): "header.detail.\(detail.key)"
            }
        }
    }

    var body: some View {
        ForEach(Self.rows(for: header)) { row in
            switch row {
            case .counts:
                countsCard
                    .padding(.bottom, Self.shownDetails(header).isEmpty ? StatsSectionLayout.sectionGap : StatsSectionLayout.cardGap)
            case let .detail(detail, isFirst, isLast):
                if let onOpenLikes, Self.opensLikes(detail) {
                    Button(action: onOpenLikes) {
                        StatsLabelValueRow(
                            label: detail.label,
                            value: Self.detailValue(detail),
                            showsChevron: true,
                            showsSeparator: !isLast
                        )
                    }
                    .buttonStyle(DiaryRowButtonStyle())
                    .accessibilityAddTraits(.isButton)
                    .statsCardSlice(isFirst: isFirst, isLast: isLast)
                } else {
                    StatsLabelValueRow(
                        label: detail.label,
                        value: Self.detailValue(detail),
                        showsSeparator: !isLast
                    )
                    .statsCardSlice(isFirst: isFirst, isLast: isLast)
                }
            }
        }
    }

    private var countsCard: some View {
        let counts = Self.counts(for: header, mediaType: mediaType)
        return Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 18) {
            ForEach(Array(stride(from: 0, to: counts.count, by: 2)), id: \.self) { start in
                GridRow {
                    ForEach(counts[start ..< min(start + 2, counts.count)]) { count in
                        countCell(count)
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .diaryCard()
    }

    private func countCell(_ count: Count) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(count.value.formatted())
                    .font(.title.weight(.bold))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                    .contentTransition(.numericText())
                if let unit = count.unit {
                    Text(unit)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.6))
                }
            }
            .lineLimit(1)
            .minimumScaleFactor(0.7)

            Text(count.label)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.6))
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Data shaping

    /// Current streaks that are 0 say nothing ("0 days"), so they're left out; a streak that's
    /// running shows.
    static let hiddenWhenZero: Set<String> = ["current_daily_streak", "current_weekly_streak"]

    static func shownDetails(_ header: StatsHeader) -> [StatsHeaderDetail] {
        header.details.filter { !(hiddenWhenZero.contains($0.key) && $0.value == 0) }
    }

    static func rows(for header: StatsHeader) -> [Row] {
        let details = shownDetails(header)
        return [.counts] + details.enumerated().map { index, detail in
            .detail(detail, isFirst: index == 0, isLast: index == details.count - 1)
        }
    }

    /// Whether a detail row opens the liked logs: the "likes" row, when it counts any.
    static func opensLikes(_ detail: StatsHeaderDetail) -> Bool {
        detail.key == "likes" && detail.value > 0
    }

    /// Titles, logs, active days and the longest daily streak. "Titles" names the tab's kind.
    static func counts(for header: StatsHeader, mediaType: String?) -> [Count] {
        [
            Count(label: titlesLabel(for: mediaType), value: header.titles, unit: nil),
            Count(label: "Logs", value: header.logs, unit: nil),
            Count(label: "Active days", value: header.activeDays, unit: nil),
            Count(label: "Longest streak", value: header.longestDailyStreak, unit: unitText("day", value: header.longestDailyStreak)),
        ]
    }

    static func titlesLabel(for mediaType: String?) -> String {
        switch mediaType {
        case "movie": "Movies"
        case "tv": "Shows"
        case "anime": "Anime"
        case "manga": "Manga"
        case "game": "Games"
        case "book": "Books"
        case "comic": "Comics"
        case "music": "Albums"
        default: "Titles"
        }
    }

    /// "≈ 3 days", "21 weeks", "17". The server sends the label ready to show and the unit
    /// singular; the app pluralizes it.
    static func detailValue(_ detail: StatsHeaderDetail) -> String {
        var text = detail.value.formatted()
        if let unit = unitText(detail.unit, value: detail.value) {
            text += " \(unit)"
        }
        return detail.approximate ? "≈ \(text)" : text
    }

    static func unitText(_ unit: String?, value: Int) -> String? {
        guard let unit = unit?.trimmingCharacters(in: .whitespaces), !unit.isEmpty else { return nil }
        return value == 1 ? unit : unit + "s"
    }
}
