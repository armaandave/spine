import Charts
import SwiftUI

// Activity (STAT-203): events by month and by weekday, then the busiest day and month (each
// opens its logs on your own stats). No heatmap. Each chart is one fixed-height row of the
// page's lazy stack; dragging across one picks a bar.

/// The Activity section's rows. `onDrillDown` is nil on someone else's stats, where nothing
/// opens (STAT-401). A picked month's "Show logs" opens that month's logs; on Global it can open
/// one type's.
struct StatsActivityChartSection: View {
    let activity: StatsEventActivity
    /// The tab: nil on Global, where months stack by type.
    let mediaType: String?
    var onDrillDown: ((StatsDrillDown) -> Void)?

    var body: some View {
        StatsSectionTitle("Activity")

        if activity.months.contains(where: { $0.count > 0 }) {
            StatsMonthsChartCard(months: activity.months, mediaType: mediaType, onDrillDown: onDrillDown)
                .padding(.bottom, StatsChartStyle.cardSpacing)
        }

        if activity.weekdays.count == 7 {
            StatsWeekdaysChartCard(
                days: StatsActivityChartData.weekdays(activity.weekdays, weekStartsOn: activity.weekStartsOn),
                mediaType: mediaType
            )
            .padding(.bottom, StatsChartStyle.cardSpacing)
        }

        if let day = activity.busiestDay {
            StatsActivityFactRow(
                label: "Busiest day",
                value: StatsActivityChartData.busiestDayText(day),
                roundsTop: true,
                roundsBottom: activity.busiestMonth == nil,
                action: onDrillDown.map { onDrillDown in
                    { onDrillDown(.day(day.date)) }
                }
            )
        }

        if let month = activity.busiestMonth {
            StatsActivityFactRow(
                label: "Busiest month",
                value: StatsActivityChartData.busiestMonthText(month),
                roundsTop: activity.busiestDay == nil,
                roundsBottom: true,
                action: onDrillDown.map { onDrillDown in
                    { onDrillDown(.month(month.month)) }
                }
            )
        }

        StatsSectionEnd()
    }
}

// MARK: - Data

/// One stacked part of a month's bar.
struct StatsMonthBarPart: Identifiable, Hashable {
    /// "YYYY-MM", the bar's category.
    let month: String
    /// Nil when the month has no per-type split.
    let mediaType: String?
    let count: Int

    var id: String { "\(month).\(mediaType ?? "all")" }
    /// The chart series: the type, or "all" for an unsplit bar.
    var seriesName: String { mediaType ?? "all" }
}

struct StatsWeekdayCount: Identifiable, Hashable {
    /// "Monday".
    let name: String
    let count: Int

    var id: String { name }
    var initial: String { String(name.prefix(1)) }
}

enum StatsActivityChartData {
    static let weekdayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    /// The seven counts in the order the owner's week runs. The payload is always Monday first.
    static func weekdays(_ counts: [Int], weekStartsOn: String) -> [StatsWeekdayCount] {
        guard counts.count == 7 else { return [] }
        let days = zip(weekdayNames, counts).map { StatsWeekdayCount(name: $0, count: $1) }
        guard weekStartsOn == "sunday" else { return days }
        return [days[6]] + days[0 ..< 6]
    }

    /// The busiest of the seven; a tie goes to the earlier day in the week shown.
    static func busiestWeekday(_ days: [StatsWeekdayCount]) -> StatsWeekdayCount? {
        guard let top = days.map(\.count).max(), top > 0 else { return nil }
        return days.first { $0.count == top }
    }

    /// "2025-08" → "August 2025".
    static func monthTitle(_ month: String, locale: Locale = .current) -> String {
        guard let date = utcDate(month: month) else { return month }
        return utcFormatter(template: "MMMMyyyy", locale: locale).string(from: date)
    }

    /// "August 2025". No count: it counts each title once per day, so a title on five days
    /// counts five times and "31 titles" would overstate it.
    static func busiestMonthText(_ month: StatsBusiestMonth, locale: Locale = .current) -> String {
        monthTitle(month.month, locale: locale)
    }

    /// "Sat, 2 Aug 2025 · 6 titles".
    static func busiestDayText(_ day: StatsBusiestDay, locale: Locale = .current) -> String {
        let count = StatsChartCopy.count(day.count, "title", "titles")
        guard let date = StatsActivityDay.parse(day.date) else { return "\(day.date) · \(count)" }
        return "\(utcFormatter(template: "EEEdMMMyyyy", locale: locale).string(from: date)) · \(count)"
    }

    /// Each month's bar as stacked parts: by type when `stacked` and the month has a split,
    /// else one part. Months with nothing, or that don't read as "YYYY-MM", have no parts.
    static func parts(_ months: [StatsEventMonth], stacked: Bool) -> [StatsMonthBarPart] {
        months.flatMap { month -> [StatsMonthBarPart] in
            guard month.count > 0, yearMonth(month.month) != nil else { return [] }
            let types = StatsChartStyle.orderedTypes(month.byType)
            if stacked, !types.isEmpty {
                return types.map { StatsMonthBarPart(month: month.month, mediaType: $0.mediaType, count: $0.count) }
            }
            return [StatsMonthBarPart(month: month.month, mediaType: nil, count: month.count)]
        }
    }

    /// An edge label under the month chart: the month ("Jan") for a year or so, the year
    /// ("2019") for a longer span.
    static func axisLabel(forMonth month: String, monthCount: Int, locale: Locale = .current) -> String {
        guard let date = utcDate(month: month) else { return month }
        let template = monthCount > 14 ? "yyyy" : "MMM"
        return utcFormatter(template: template, locale: locale).string(from: date)
    }

    /// "14", or on Global "14 · 9 movies, 5 TV": a bar's count, then its split by type. The
    /// count is events, each title once a day, so it isn't called titles.
    static func countDetail(_ count: Int, byType: [String: Int], stacked: Bool) -> String {
        let types = stacked ? StatsChartStyle.orderedTypes(byType) : []
        guard types.count > 1 else { return count.formatted() }
        let split = types.map { "\($0.count.formatted()) \(StatsChartCopy.typeNoun($0.mediaType))" }
        return "\(count.formatted()) · \(split.joined(separator: ", "))"
    }

    /// A picked month's readout and its logs, each type's too on Global.
    static func selection(for month: StatsEventMonth, stacked: Bool, locale: Locale = .current) -> StatsChartSelection {
        let parts = stacked
            ? StatsChartStyle.orderedTypes(month.byType).map { (mediaType: $0.mediaType, drillDown: StatsDrillDown.month(month.month, mediaType: $0.mediaType)) }
            : []
        return StatsChartSelection(
            title: monthTitle(month.month, locale: locale),
            detail: countDetail(month.count, byType: month.byType, stacked: stacked),
            drillOptions: month.count > 0 ? StatsChartDrillOption.options(whole: .month(month.month), parts: parts) : []
        )
    }

    private static func yearMonth(_ month: String) -> (Int, Int)? {
        let parts = month.split(separator: "-")
        guard parts.count == 2, let year = Int(parts[0]), let number = Int(parts[1]), (1 ... 12).contains(number) else {
            return nil
        }
        return (year, number)
    }

    private static func utcDate(month: String) -> Date? {
        guard let (year, number) = yearMonth(month) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return calendar.date(from: DateComponents(year: year, month: number, day: 1))
    }

    private static func utcFormatter(template: String, locale: Locale) -> DateFormatter {
        StatsDateFormatters.utc(template: template, locale: locale)
    }
}

// MARK: - Months

private struct StatsMonthsChartCard: View {
    let months: [StatsEventMonth]
    let mediaType: String?
    let onDrillDown: ((StatsDrillDown) -> Void)?

    /// The month under the finger while scrubbing.
    @State private var scrubbed: String?
    /// The last picked month, kept after the finger lifts.
    @State private var picked: String?

    private var isStacked: Bool { mediaType == nil }
    private var legendTypes: [String] { StatsChartStyle.types(in: months.map(\.byType)) }

    var body: some View {
        let parts = StatsActivityChartData.parts(months, stacked: isStacked)
        let maximum = months.map(\.count).max() ?? 0
        let ticks = StatsChartStyle.countTicks(maximum: maximum)
        let seriesNames = Array(Set(parts.map(\.seriesName)))
        let domain = (legendTypes + ["all"]).filter(seriesNames.contains)
        let range = domain.map { name in
            StatsChartStyle.color(for: name == "all" ? mediaType : name)
        }
        let keys = months.map(\.month)
        let pickedMonth = months.first { $0.month == (scrubbed ?? picked) }

        VStack(alignment: .leading, spacing: 12) {
            StatsChartReadout(
                selection: pickedMonth.map { StatsActivityChartData.selection(for: $0, stacked: isStacked) },
                onDrillDown: onDrillDown
            ) {
                StatsChartIdleLine(title: "By month", hint: "Drag across the chart to see each month.")
            }

            Chart(parts) { part in
                BarMark(
                    x: .value("Month", part.month),
                    y: .value("Count", part.count)
                )
                .foregroundStyle(by: .value("Type", part.seriesName))
                .opacity(pickedMonth == nil || pickedMonth?.month == part.month ? 1 : 0.35)
            }
            .chartForegroundStyleScale(domain: domain, range: range)
            .chartLegend(.hidden)
            .chartXScale(domain: keys)
            .statsEdgeXAxis(labels: StatsChartAxis.edgeLabels(keys)) { month in
                StatsActivityChartData.axisLabel(forMonth: month, monthCount: keys.count)
            }
            .statsCountYAxis(ticks: ticks)
            .chartXSelection(value: $scrubbed)
            .frame(height: 170)
            .accessibilityLabel("Activity by month")
            .accessibilityChartDescriptor(descriptor)
            .accessibilityActions {
                // VoiceOver can't drag, so each month with logs gets an action.
                if let onDrillDown {
                    ForEach(months.filter { $0.count > 0 }) { month in
                        Button("Show \(StatsActivityChartData.monthTitle(month.month)) logs") {
                            onDrillDown(.month(month.month))
                        }
                    }
                }
            }

            if isStacked, legendTypes.count > 1 {
                StatsTypeLegend(mediaTypes: legendTypes)
            }
        }
        .statsChartCard()
        // Scrubbing is the user's own switch; the finger lifting isn't.
        .sensoryFeedback(.selection, trigger: scrubbed) { _, new in new != nil }
        .onChange(of: scrubbed) { _, new in
            if let new { picked = new }
        }
    }

    private var descriptor: StatsChartDescriptor {
        StatsChartDescriptor(
            title: "Activity by month",
            summary: nil,
            categoryTitle: "Month",
            valueTitle: "Count",
            points: months.map { (StatsActivityChartData.monthTitle($0.month), Double($0.count)) }
        )
    }
}

// MARK: - Weekdays

private struct StatsWeekdaysChartCard: View {
    let days: [StatsWeekdayCount]
    let mediaType: String?

    /// The day under the finger while scrubbing.
    @State private var scrubbed: String?
    /// The last picked day, kept after the finger lifts.
    @State private var picked: String?

    var body: some View {
        let ticks = StatsChartStyle.countTicks(maximum: days.map(\.count).max() ?? 0)
        let busiest = StatsActivityChartData.busiestWeekday(days)
        let pickedDay = days.first { $0.name == (scrubbed ?? picked) }
        // Nothing picked, the busiest day stands out; once one is picked, that one does.
        let highlighted = pickedDay ?? busiest

        VStack(alignment: .leading, spacing: 12) {
            StatsChartReadout(
                selection: pickedDay.map { StatsChartSelection(title: $0.name, detail: $0.count.formatted()) },
                onDrillDown: nil
            ) {
                StatsChartIdleLine(title: "By weekday", hint: "Drag to see each day.") {
                    if let busiest {
                        Text("Most on \(busiest.name)s")
                            .font(.subheadline)
                            .foregroundStyle(.white)
                    }
                }
            }

            Chart(days) { day in
                BarMark(
                    x: .value("Weekday", day.name),
                    y: .value("Count", day.count),
                    width: .ratio(0.6)
                )
                .foregroundStyle(StatsChartStyle.color(for: mediaType))
                .opacity(highlighted == nil || highlighted?.name == day.name ? 1 : 0.45)
                .clipShape(RoundedRectangle(cornerRadius: 3, style: .continuous))
            }
            .chartXAxis {
                AxisMarks { value in
                    AxisValueLabel {
                        if let name = value.as(String.self) {
                            Text(String(name.prefix(1)))
                                .foregroundStyle(StatsChartStyle.axisLabel)
                        }
                    }
                }
            }
            .statsCountYAxis(ticks: ticks)
            .chartXSelection(value: $scrubbed)
            .frame(height: 130)
            .accessibilityLabel("Activity by weekday")
            .accessibilityChartDescriptor(
                StatsChartDescriptor(
                    title: "Activity by weekday",
                    summary: busiest.map { "Most on \($0.name)s" },
                    categoryTitle: "Weekday",
                    valueTitle: "Count",
                    points: days.map { ($0.name, Double($0.count)) }
                )
            )
        }
        .statsChartCard()
        .sensoryFeedback(.selection, trigger: scrubbed) { _, new in new != nil }
        .onChange(of: scrubbed) { _, new in
            if let new { picked = new }
        }
    }
}

// MARK: - Busiest day and month

private struct StatsActivityFactRow: View {
    let label: String
    let value: String
    let roundsTop: Bool
    let roundsBottom: Bool
    let action: (() -> Void)?

    var body: some View {
        Group {
            if let action {
                Button(action: action) { content(showsChevron: true) }
                    .buttonStyle(DiaryRowButtonStyle())
            } else {
                content(showsChevron: false)
            }
        }
        .diaryCard(roundsTop: roundsTop, roundsBottom: roundsBottom)
    }

    private func content(showsChevron: Bool) -> some View {
        VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(label)
                    .font(.body)
                    .foregroundStyle(.white)
                Spacer(minLength: 12)
                Text(value)
                    .font(.body)
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                if showsChevron {
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.35))
                        .accessibilityHidden(true)
                }
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 48)

            if !roundsBottom {
                DiarySeparator()
                    .padding(.leading, 16)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
