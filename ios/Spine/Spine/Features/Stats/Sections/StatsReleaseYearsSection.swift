import Charts
import SwiftUI

// Release years (STAT-209): one fixed-width chart from the first year with titles to this year,
// never scrolling sideways. Years or decades (folded here, on the phone), and Titles, Ratings
// (average stars per year) or Logs. Scrubbing picks a year; "Show logs" opens the diary.

enum StatsReleaseGrouping: String, CaseIterable, Identifiable, Hashable {
    case years
    case decades

    var id: String { rawValue }
    var title: String { self == .years ? "Years" : "Decades" }
}

enum StatsReleaseMode: String, CaseIterable, Identifiable, Hashable {
    case titles
    case ratings
    case logs

    var id: String { rawValue }

    var title: String {
        switch self {
        case .titles: "Titles"
        case .ratings: "Ratings"
        case .logs: "Logs"
        }
    }
}

/// The Release years section's rows. `onDrillDown` is nil on someone else's stats; it opens a
/// year's logs (`year=1999`) or a decade's (`year_min=1990&year_max=1999`).
struct StatsReleaseYearsChartSection: View {
    let releaseYears: StatsReleaseYears
    /// The tab: nil on Global, where Titles stack by type.
    let mediaType: String?
    /// This year on the owner's calendar (`years.current`); the chart runs up to it.
    let currentYear: Int
    var onDrillDown: ((StatsDrillDown) -> Void)?

    @State private var grouping: StatsReleaseGrouping = .years

    var body: some View {
        StatsSectionTitle("Release years") {
            Picker("Group by", selection: $grouping) {
                ForEach(StatsReleaseGrouping.allCases) { grouping in
                    Text(grouping.title).tag(grouping)
                }
            }
            .pickerStyle(.segmented)
            .fixedSize()
            .sensoryFeedback(.selection, trigger: grouping)
        }

        StatsReleaseYearsChartCard(
            releaseYears: releaseYears,
            grouping: grouping,
            mediaType: mediaType,
            currentYear: currentYear,
            onDrillDown: onDrillDown
        )

        if let note = StatsChartCopy.coverageNote(releaseYears.coverage, qualifier: "with a release year") {
            StatsChartFootnote(text: note)
        }

        if let newVsOlder = releaseYears.newVsOlder {
            let items = StatsReleaseYearsChartData.newVsOlderItems(newVsOlder)
            if !items.isEmpty {
                StatsNewVsOlderCard(items: items)
                    .padding(.top, StatsChartStyle.cardSpacing)
            }
        }

        StatsSectionEnd()
    }
}

// MARK: - Data

/// One bar: a release year, or a decade folded from its years.
struct StatsReleasePoint: Identifiable, Hashable {
    let start: Int
    let end: Int
    let titles: Int
    let logs: Int
    let rated: Int
    let averageStars: Double?
    /// The average as text: the server's spelling for a year, two decimals at most for a decade.
    let averageText: String?
    let byType: [String: Int]

    var id: Int { start }
    var isDecade: Bool { end > start }
    /// "1999", or "1990s".
    var label: String { isDecade ? "\(start)s" : String(start) }

    func value(for mode: StatsReleaseMode) -> Double? {
        switch mode {
        case .titles: Double(titles)
        case .logs: Double(logs)
        case .ratings: rated > 0 ? averageStars : nil
        }
    }
}

/// One stacked part of a bar.
struct StatsReleaseBarPart: Identifiable, Hashable {
    let label: String
    let mediaType: String?
    let value: Double

    var id: String { "\(label).\(mediaType ?? "all")" }
    /// The chart series: the type, or "all" for an unsplit bar.
    var seriesName: String { mediaType ?? "all" }
}

enum StatsReleaseYearsChartData {
    /// Every year from the first with titles to this year (or the latest row, if later), with
    /// empty years as zero.
    static func yearSeries(_ rows: [StatsReleaseYearRow], currentYear: Int) -> [StatsReleasePoint] {
        guard let first = rows.map(\.year).min(), let lastRow = rows.map(\.year).max() else { return [] }
        let last = max(currentYear, lastRow)
        let byYear = Dictionary(rows.map { ($0.year, $0) }, uniquingKeysWith: { first, _ in first })
        return (first ... last).map { year in
            guard let row = byYear[year] else { return empty(start: year, end: year) }
            return StatsReleasePoint(
                start: year,
                end: year,
                titles: row.titles,
                logs: row.logs,
                rated: row.rated,
                averageStars: row.averageStars?.value,
                averageText: row.averageStars?.text,
                byType: row.byType
            )
        }
    }

    /// Every decade from the first with titles to this one. Counts add up; the average is
    /// weighted by each year's rated logs.
    static func decadeSeries(_ rows: [StatsReleaseYearRow], currentYear: Int) -> [StatsReleasePoint] {
        let years = yearSeries(rows, currentYear: currentYear)
        guard let first = years.first?.start, let last = years.last?.start else { return [] }
        let firstDecade = decade(of: first)
        let lastDecade = decade(of: last)
        return stride(from: firstDecade, through: lastDecade, by: 10).map { start in
            let members = years.filter { decade(of: $0.start) == start }
            var byType: [String: Int] = [:]
            for member in members {
                byType.merge(member.byType, uniquingKeysWith: +)
            }
            let weighted = members.filter { $0.rated > 0 && $0.averageStars != nil }
            let ratedWeight = weighted.map(\.rated).reduce(0, +)
            let average = ratedWeight > 0
                ? weighted.map { $0.averageStars! * Double($0.rated) }.reduce(0, +) / Double(ratedWeight)
                : nil
            return StatsReleasePoint(
                start: start,
                end: start + 9,
                titles: members.map(\.titles).reduce(0, +),
                logs: members.map(\.logs).reduce(0, +),
                rated: members.map(\.rated).reduce(0, +),
                averageStars: average,
                averageText: average.map(StatsDecimal.format),
                byType: byType
            )
        }
    }

    static func series(_ rows: [StatsReleaseYearRow], grouping: StatsReleaseGrouping, currentYear: Int) -> [StatsReleasePoint] {
        grouping == .years ? yearSeries(rows, currentYear: currentYear) : decadeSeries(rows, currentYear: currentYear)
    }

    /// The bars' parts. Titles stack by type when `stacked`; Logs and Ratings are one part.
    static func parts(_ points: [StatsReleasePoint], mode: StatsReleaseMode, stacked: Bool) -> [StatsReleaseBarPart] {
        points.flatMap { point -> [StatsReleaseBarPart] in
            guard let value = point.value(for: mode), value > 0 else { return [] }
            let types = StatsChartStyle.orderedTypes(point.byType)
            if mode == .titles, stacked, !types.isEmpty {
                return types.map { StatsReleaseBarPart(label: point.label, mediaType: $0.mediaType, value: Double($0.count)) }
            }
            return [StatsReleaseBarPart(label: point.label, mediaType: nil, value: value)]
        }
    }

    /// The labels the axis shows: about six, on round years (every 1, 2, 5, 10, 20, 25 or 50
    /// years, or decades).
    static func axisLabels(_ points: [StatsReleasePoint]) -> [String] {
        guard let first = points.first else { return [] }
        let unit = first.isDecade ? 10 : 1
        let count = points.count
        let step = [1, 2, 5, 10, 20, 25, 50].first { count / $0 <= 6 } ?? 50
        return points.filter { ($0.start / unit) % step == 0 }.map(\.label)
    }

    /// The bar with the most, a tie going to the most recent. Nil when nothing has a value.
    static func peak(_ points: [StatsReleasePoint], mode: StatsReleaseMode) -> StatsReleasePoint? {
        points
            .filter { ($0.value(for: mode) ?? 0) > 0 }
            .max { ($0.value(for: mode) ?? 0, $0.start) < ($1.value(for: mode) ?? 0, $1.start) }
    }

    /// "4 titles · 5 logs · 4.0 average".
    static func detail(_ point: StatsReleasePoint) -> String {
        var parts = [
            StatsChartCopy.count(point.titles, "title", "titles"),
            StatsChartCopy.count(point.logs, "log", "logs"),
        ]
        if point.rated > 0, let average = point.averageText {
            parts.append("\(average) average")
        }
        return parts.joined(separator: " · ")
    }

    /// The line over the chart when nothing is picked: "Most titles from 2024".
    static func summaryTitle(_ points: [StatsReleasePoint], mode: StatsReleaseMode) -> String? {
        guard let peak = peak(points, mode: mode) else { return nil }
        switch mode {
        case .titles: return "Most titles from \(peak.label)"
        case .logs: return "Most logs from \(peak.label)"
        case .ratings: return "Highest rated: \(peak.label)"
        }
    }

    /// A year's logs, or a decade's.
    static func drillDown(for point: StatsReleasePoint) -> StatsDrillDown {
        point.isDecade ? .releaseDecade(point.start) : .releaseYear(point.start)
    }

    /// The new-vs-older counts with their labels; empty when all three are 0.
    static func newVsOlderItems(_ counts: StatsNewVsOlder) -> [(label: String, value: Int)] {
        guard counts.new + counts.recent + counts.older > 0 else { return [] }
        return [
            ("New release", counts.new),
            ("1–7 years old", counts.recent),
            ("8+ years old", counts.older),
        ]
    }

    private static func decade(of year: Int) -> Int {
        Int((Double(year) / 10).rounded(.down)) * 10
    }

    private static func empty(start: Int, end: Int) -> StatsReleasePoint {
        StatsReleasePoint(
            start: start, end: end, titles: 0, logs: 0, rated: 0,
            averageStars: nil, averageText: nil, byType: [:]
        )
    }
}

// MARK: - Chart

private struct StatsReleaseYearsChartCard: View {
    let releaseYears: StatsReleaseYears
    let grouping: StatsReleaseGrouping
    let mediaType: String?
    let currentYear: Int
    let onDrillDown: ((StatsDrillDown) -> Void)?

    @State private var mode: StatsReleaseMode = .titles
    /// The label under the finger while scrubbing.
    @State private var scrubbed: String?
    /// The last picked bar, kept after the finger lifts.
    @State private var picked: String?

    private var isStacked: Bool { mediaType == nil }

    var body: some View {
        let points = StatsReleaseYearsChartData.series(releaseYears.rows, grouping: grouping, currentYear: currentYear)
        let parts = StatsReleaseYearsChartData.parts(points, mode: mode, stacked: isStacked)
        let legendTypes = mode == .titles && isStacked ? StatsChartStyle.types(in: points.map(\.byType)) : []
        let seriesNames = Set(parts.map(\.seriesName))
        let domain = (legendTypes + ["all"]).filter(seriesNames.contains)
        let range = domain.map { name in
            name == "all" ? allColor : StatsChartStyle.color(for: name)
        }
        let pickedPoint = points.first { $0.label == (scrubbed ?? picked) }

        VStack(alignment: .leading, spacing: 14) {
            Picker("Show", selection: $mode) {
                ForEach(StatsReleaseMode.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .sensoryFeedback(.selection, trigger: mode)

            readout(points: points, picked: pickedPoint)
                .frame(height: 44, alignment: .leading)

            Chart(parts) { part in
                BarMark(
                    x: .value("Year", part.label),
                    y: .value(mode.title, part.value)
                )
                .foregroundStyle(by: .value("Type", part.seriesName))
                .opacity(pickedPoint == nil || pickedPoint?.label == part.label ? 1 : 0.35)
            }
            .chartForegroundStyleScale(domain: domain, range: range)
            .chartLegend(.hidden)
            .chartXScale(domain: points.map(\.label))
            .chartXAxis {
                AxisMarks(values: StatsReleaseYearsChartData.axisLabels(points)) { value in
                    AxisValueLabel(centered: true, collisionResolution: .disabled) {
                        if let label = value.as(String.self) {
                            Text(label)
                                .foregroundStyle(StatsChartStyle.axisLabel)
                        }
                    }
                }
            }
            .modifier(StatsReleaseYAxis(mode: mode, points: points))
            .chartXSelection(value: $scrubbed)
            .frame(height: 160)
            .accessibilityLabel("Release years")
            .accessibilityValue(StatsReleaseYearsChartData.summaryTitle(points, mode: mode) ?? "")
            .accessibilityChartDescriptor(
                StatsChartDescriptor(
                    title: "Release years, \(mode.title.lowercased())",
                    summary: StatsReleaseYearsChartData.summaryTitle(points, mode: mode),
                    categoryTitle: grouping == .years ? "Year" : "Decade",
                    valueTitle: mode == .ratings ? "Average stars" : mode.title,
                    points: points.map { ($0.label, $0.value(for: mode) ?? 0) }
                )
            )

            if legendTypes.count > 1 {
                StatsTypeLegend(mediaTypes: legendTypes)
            }
        }
        .statsChartCard()
        // Scrubbing is the user's own switch; the finger lifting isn't.
        .sensoryFeedback(.selection, trigger: scrubbed) { _, new in new != nil }
        .onChange(of: scrubbed) { _, new in
            if let new { picked = new }
        }
        .onChange(of: grouping) {
            picked = nil
        }
    }

    private var allColor: Color {
        mode == .ratings ? LogComposerStyle.starGold : StatsChartStyle.color(for: mediaType)
    }

    @ViewBuilder
    private func readout(points: [StatsReleasePoint], picked: StatsReleasePoint?) -> some View {
        if let picked {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(picked.label)
                        .font(.headline)
                        .foregroundStyle(.white)
                        .monospacedDigit()
                    Text(StatsReleaseYearsChartData.detail(picked))
                        .font(.footnote)
                        .foregroundStyle(StatsChartStyle.secondaryText)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                Spacer(minLength: 8)
                if let onDrillDown, picked.logs > 0 {
                    Button("Show logs") {
                        onDrillDown(StatsReleaseYearsChartData.drillDown(for: picked))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(.white)
                }
            }
            .accessibilityElement(children: .contain)
        } else {
            VStack(alignment: .leading, spacing: 2) {
                Text(StatsReleaseYearsChartData.summaryTitle(points, mode: mode) ?? "Nothing rated yet")
                    .font(.headline)
                    .foregroundStyle(.white)
                Text("Drag across the chart to see each \(grouping == .years ? "year" : "decade").")
                    .font(.footnote)
                    .foregroundStyle(StatsChartStyle.secondaryText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
        }
    }
}

/// Counts get whole-number ticks; Ratings run from 0 to 5 stars.
private struct StatsReleaseYAxis: ViewModifier {
    let mode: StatsReleaseMode
    let points: [StatsReleasePoint]

    func body(content: Content) -> some View {
        if mode == .ratings {
            content
                .chartYScale(domain: 0 ... 5.0)
                .chartYAxis {
                    AxisMarks(position: .leading, values: [0.0, 1, 2, 3, 4, 5]) { value in
                        AxisGridLine().foregroundStyle(StatsChartStyle.gridLine)
                        AxisValueLabel {
                            if let stars = value.as(Double.self) {
                                Text("\(Int(stars))★")
                                    .foregroundStyle(StatsChartStyle.axisLabel)
                            }
                        }
                    }
                }
        } else {
            content.statsCountYAxis(
                ticks: StatsChartStyle.countTicks(maximum: Int(points.compactMap { $0.value(for: mode) }.max() ?? 0))
            )
        }
    }
}

// MARK: - New vs older

private struct StatsNewVsOlderCard: View {
    let items: [(label: String, value: Int)]

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                if index > 0 {
                    Rectangle()
                        .fill(.white.opacity(0.09))
                        .frame(width: 0.5, height: 34)
                }
                VStack(spacing: 3) {
                    Text(item.value.formatted())
                        .font(.title3.bold())
                        .foregroundStyle(.white)
                        .monospacedDigit()
                    Text(item.label)
                        .font(.caption)
                        .foregroundStyle(StatsChartStyle.secondaryText)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .combine)
            }
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 8)
        .frame(maxWidth: .infinity)
        .diaryCard()
        .accessibilityElement(children: .contain)
        .accessibilityLabel("When you got to them")
    }
}
