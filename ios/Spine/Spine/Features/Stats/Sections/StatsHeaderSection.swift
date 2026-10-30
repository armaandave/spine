import SwiftUI

/// The header (STAT-200): four big numbers in one card, then a card of label/value details.
/// On a type tab the third number is the tab's own measure (hours, pages, chapters or issues)
/// and Active days opens the details card. Its body is one view per row, so each row is its own
/// item in the page's lazy stack.
struct StatsHeaderSection: View {
    let header: StatsHeader
    /// The tab: nil on Global.
    let mediaType: String?
    /// The page's Time block, for a type tab's measure.
    var time: StatsTime?
    /// Opens a number's or a row's logs (STAT-401); nil on someone else's stats, where nothing
    /// opens.
    var onOpen: ((StatsDrillDown) -> Void)?

    struct Count: Hashable, Identifiable {
        let label: String
        let value: Int
        /// "days" beside the number, or nil.
        let unit: String?
        /// The number reads "≈ 340".
        var approximate = false
        /// What a tap opens on your own stats; nil when it opens nothing.
        var drillDown: StatsDrillDown?

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
        let details = Self.shownDetails(header, mediaType: mediaType, time: time)
        ForEach(Self.rows(for: header, mediaType: mediaType, time: time)) { row in
            switch row {
            case .counts:
                countsCard
                    .padding(.bottom, details.isEmpty ? StatsSectionLayout.sectionGap : StatsSectionLayout.cardGap)
            case let .detail(detail, isFirst, isLast):
                if let onOpen, let drillDown = Self.drillDown(for: detail) {
                    Button {
                        onOpen(drillDown)
                    } label: {
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
        let counts = Self.counts(for: header, mediaType: mediaType, time: time)
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

    @ViewBuilder
    private func countCell(_ count: Count) -> some View {
        if let onOpen, let drillDown = count.drillDown {
            Button {
                onOpen(drillDown)
            } label: {
                countContent(count, showsChevron: true)
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(.isButton)
        } else {
            countContent(count, showsChevron: false)
        }
    }

    private func countContent(_ count: Count, showsChevron: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(Self.countValue(count))
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

            HStack(spacing: 3) {
                Text(count.label)
                    .lineLimit(1)
                if showsChevron {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.35))
                        .accessibilityHidden(true)
                }
            }
            .font(.subheadline)
            .foregroundStyle(.white.opacity(0.6))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    // MARK: - Data shaping

    /// Current streaks that are 0 say nothing ("0 days"), so they're left out; a streak that's
    /// running shows.
    static let hiddenWhenZero: Set<String> = ["current_daily_streak", "current_weekly_streak"]

    /// The details card's rows: Active days first when the tab's measure took its number, then
    /// the server's rows without the zero current streaks.
    static func shownDetails(_ header: StatsHeader, mediaType: String? = nil, time: StatsTime? = nil) -> [StatsHeaderDetail] {
        let details = header.details.filter { !(hiddenWhenZero.contains($0.key) && $0.value == 0) }
        guard primaryMeasure(time: time, mediaType: mediaType) != nil else { return details }
        let activeDays = StatsHeaderDetail(
            key: "active_days",
            label: "Active days",
            value: header.activeDays,
            unit: nil,
            approximate: false,
            drill: [:]
        )
        return [activeDays] + details.filter { $0.key != activeDays.key }
    }

    static func rows(for header: StatsHeader, mediaType: String? = nil, time: StatsTime? = nil) -> [Row] {
        let details = shownDetails(header, mediaType: mediaType, time: time)
        return [.counts] + details.enumerated().map { index, detail in
            .detail(detail, isFirst: index == 0, isLast: index == details.count - 1)
        }
    }

    /// What a detail row opens: its `drill` on top of the scope's logs (an empty one is the
    /// scope's own logs), or, from a server that sends no drills, the liked logs for "likes".
    /// A row that counts nothing opens nothing.
    static func drillDown(for detail: StatsHeaderDetail) -> StatsDrillDown? {
        guard detail.value > 0 else { return nil }
        if let drill = detail.drill {
            return StatsDrillDown.headerStat(label: detail.label, drill: drill)
        }
        return detail.key == "likes" ? .liked : nil
    }

    /// Titles, logs, the third number and the longest daily streak. "Titles" names the tab's
    /// kind; the third number is the tab's measure on a type tab, else active days. Each opens
    /// the scope's logs, and the streak opens its own days when the server sends them.
    static func counts(for header: StatsHeader, mediaType: String?, time: StatsTime? = nil) -> [Count] {
        var counts = [
            Count(label: titlesLabel(for: mediaType), value: header.titles, unit: nil),
            Count(label: "Logs", value: header.logs, unit: nil),
            primaryMeasure(time: time, mediaType: mediaType)
                ?? Count(label: "Active days", value: header.activeDays, unit: nil),
        ]
        for index in counts.indices where counts[index].value > 0 {
            counts[index].drillDown = .stat(label: counts[index].label)
        }

        let streakLabel = "Longest streak"
        var streak = Count(label: streakLabel, value: header.longestDailyStreak, unit: unitText("day", value: header.longestDailyStreak))
        if header.longestDailyStreak > 0, let range = header.longestDailyStreakRange {
            streak.drillDown = StatsDrillDown.headerStat(
                label: streakLabel,
                drill: ["watched_from": range.start, "watched_to": range.end]
            )
        }
        return counts + [streak]
    }

    /// A type tab's own measure from its Time row: hours (minutes under an hour), pages,
    /// chapters or issues. Nil on Global, and when the tab has no Time row or it counts nothing,
    /// so Active days keeps its place.
    static func primaryMeasure(time: StatsTime?, mediaType: String?) -> Count? {
        guard let mediaType,
              let row = time?.rows.first(where: { $0.mediaType == mediaType }),
              row.value > 0 else { return nil }
        let value: Int
        let label: String
        switch row.unit {
        case "minutes" where row.value >= 60:
            value = Int((Double(row.value) / 60).rounded())
            label = value == 1 ? "Hour" : "Hours"
        case "minutes":
            value = row.value
            label = value == 1 ? "Minute" : "Minutes"
        default:
            let singular = row.unit.hasSuffix("s") ? String(row.unit.dropLast()) : row.unit
            value = row.value
            label = (value == 1 ? singular : singular + "s").capitalized
        }
        return Count(label: label, value: value, unit: nil, approximate: row.approximate)
    }

    /// "1,204", or "≈ 340" for an estimate.
    static func countValue(_ count: Count) -> String {
        count.approximate ? "≈ \(count.value.formatted())" : count.value.formatted()
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
