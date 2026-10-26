import Charts
import SwiftUI

// Activity (STAT-203): events by month and by weekday, then the busiest day and month. No
// heatmap. Each chart is one fixed-height row of the page's lazy stack.

/// The Activity section's rows. `onDrillDown` is nil on someone else's stats, where charts
/// aren't tappable (STAT-401). A month bar opens that month's logs; on Global, a tap on one
/// type's part of the bar opens only that type's.
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
                action: nil
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
    let month: String
    let date: Date
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

    /// "YYYY-MM" as the first day of that month at midnight on `calendar`, so axis labels
    /// formatted on the same calendar name the right month.
    static func monthDate(_ month: String, calendar: Calendar = .current) -> Date? {
        guard let (year, number) = yearMonth(month) else { return nil }
        return calendar.date(from: DateComponents(year: year, month: number, day: 1))
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
    /// else one part. Months with nothing have no parts.
    static func parts(_ months: [StatsEventMonth], stacked: Bool, calendar: Calendar = .current) -> [StatsMonthBarPart] {
        months.flatMap { month -> [StatsMonthBarPart] in
            guard month.count > 0, let date = monthDate(month.month, calendar: calendar) else { return [] }
            let types = StatsChartStyle.orderedTypes(month.byType)
            if stacked, !types.isEmpty {
                return types.map { StatsMonthBarPart(month: month.month, date: date, mediaType: $0.mediaType, count: $0.count) }
            }
            return [StatsMonthBarPart(month: month.month, date: date, mediaType: nil, count: month.count)]
        }
    }

    /// Month labels for up to 14 months; beyond that, years every `yearStride`.
    static func yearStride(monthCount: Int) -> Int? {
        guard monthCount > 14 else { return nil }
        let years = Int((Double(monthCount) / 12).rounded(.up))
        return max(1, Int((Double(years) / 6).rounded(.up)))
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

    private var isStacked: Bool { mediaType == nil }
    private var parts: [StatsMonthBarPart] { StatsActivityChartData.parts(months, stacked: isStacked) }
    private var legendTypes: [String] { StatsChartStyle.types(in: months.map(\.byType)) }

    var body: some View {
        let parts = parts
        let maximum = months.map(\.count).max() ?? 0
        let ticks = StatsChartStyle.countTicks(maximum: maximum)
        let seriesNames = Array(Set(parts.map(\.seriesName)))
        let domain = (legendTypes + ["all"]).filter(seriesNames.contains)
        let range = domain.map { name in
            StatsChartStyle.color(for: name == "all" ? mediaType : name)
        }

        VStack(alignment: .leading, spacing: 12) {
            Text("By month")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(StatsChartStyle.secondaryText)

            Chart(parts) { part in
                BarMark(
                    x: .value("Month", part.date, unit: .month),
                    y: .value("Count", part.count)
                )
                .foregroundStyle(by: .value("Type", part.seriesName))
            }
            .chartForegroundStyleScale(domain: domain, range: range)
            .chartLegend(.hidden)
            .chartXScale(domain: xDomain)
            .chartXAxis { xAxis }
            .statsCountYAxis(ticks: ticks)
            .chartOverlay { proxy in
                if onDrillDown != nil {
                    StatsChartTapOverlay(proxy: proxy) { location in
                        handleTap(at: location, proxy: proxy)
                    }
                }
            }
            .frame(height: 170)
            .accessibilityLabel("Activity by month")
            .accessibilityChartDescriptor(descriptor)
            .accessibilityActions {
                // VoiceOver can't tap a bar, so each month with logs gets an action.
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
    }

    private var xDomain: ClosedRange<Date> {
        let calendar = Calendar.current
        let dates = months.compactMap { StatsActivityChartData.monthDate($0.month, calendar: calendar) }
        guard let first = dates.min(), let last = dates.max(),
              let end = calendar.date(byAdding: .month, value: 1, to: last) else {
            let now = Date.now
            return now ... now.addingTimeInterval(1)
        }
        return first ... end
    }

    @AxisContentBuilder
    private var xAxis: some AxisContent {
        if let stride = StatsActivityChartData.yearStride(monthCount: months.count) {
            AxisMarks(values: .stride(by: .year, count: stride)) { _ in
                AxisGridLine().foregroundStyle(StatsChartStyle.gridLine)
                AxisValueLabel(format: .dateTime.year())
                    .foregroundStyle(StatsChartStyle.axisLabel)
            }
        } else {
            AxisMarks(values: .stride(by: .month)) { _ in
                AxisValueLabel(format: .dateTime.month(.narrow), centered: true)
                    .foregroundStyle(StatsChartStyle.axisLabel)
            }
        }
    }

    private func handleTap(at location: CGPoint, proxy: ChartProxy) {
        guard let onDrillDown, let date: Date = proxy.value(atX: location.x) else { return }
        let components = Calendar.current.dateComponents([.year, .month], from: date)
        guard let year = components.year, let number = components.month else { return }
        let key = String(format: "%04d-%02d", year, number)
        guard let month = months.first(where: { $0.month == key }), month.count > 0 else { return }

        var tappedType: String?
        if isStacked, let value: Double = proxy.value(atY: location.y) {
            // Above the bar is the whole month.
            tappedType = statsStackedPart(at: value, parts: StatsChartStyle.orderedTypes(month.byType))
        }
        onDrillDown(.month(key, mediaType: tappedType))
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

    var body: some View {
        let ticks = StatsChartStyle.countTicks(maximum: days.map(\.count).max() ?? 0)
        let busiest = StatsActivityChartData.busiestWeekday(days)

        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("By weekday")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(StatsChartStyle.secondaryText)
                Spacer(minLength: 8)
                if let busiest {
                    Text("Most on \(busiest.name)s")
                        .font(.subheadline)
                        .foregroundStyle(.white)
                }
            }

            Chart(days) { day in
                BarMark(
                    x: .value("Weekday", day.name),
                    y: .value("Count", day.count),
                    width: .ratio(0.6)
                )
                .foregroundStyle(StatsChartStyle.color(for: mediaType))
                .opacity(busiest == nil || busiest?.name == day.name ? 1 : 0.45)
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
