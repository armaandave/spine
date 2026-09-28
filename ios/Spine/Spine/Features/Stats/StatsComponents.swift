import Charts
import SwiftUI
import UIKit

struct SWStatsSlice: Identifiable {
    let id: String
    let title: String
    let value: Int
    let color: Color
}

struct SWStatsDonutChart: View {
    let slices: [SWStatsSlice]
    let centerTitle: String

    @State private var selectedAngle: Int?

    private var visibleSlices: [SWStatsSlice] {
        slices.filter { $0.value > 0 }
    }

    private var selectedSlice: SWStatsSlice? {
        guard let selectedAngle else { return nil }
        var upperBound = 0
        for slice in visibleSlices {
            upperBound += slice.value
            if selectedAngle <= upperBound {
                return slice
            }
        }
        return nil
    }

    private var total: Int {
        visibleSlices.reduce(0) { $0 + $1.value }
    }

    var body: some View {
        Chart(visibleSlices) { slice in
            let isSelected = selectedSlice?.id == slice.id
            SectorMark(
                angle: .value("Count", slice.value),
                innerRadius: .ratio(0.68),
                outerRadius: .ratio(isSelected ? 1 : 0.92),
                angularInset: 1.5
            )
            .cornerRadius(5)
            .foregroundStyle(slice.color)
            .opacity(selectedSlice == nil || isSelected ? 1 : 0.28)
        }
        .chartLegend(.hidden)
        .chartAngleSelection(value: $selectedAngle)
        .chartBackground { proxy in
            GeometryReader { geometry in
                if let plotFrame = proxy.plotFrame {
                    let frame = geometry[plotFrame]
                    VStack(spacing: 2) {
                        Text((selectedSlice?.value ?? total).formatted())
                            .font(.system(size: 25, weight: .black, design: .rounded))
                            .foregroundStyle(.white)
                            .monospacedDigit()

                        Text(selectedSlice?.title ?? centerTitle)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.white.opacity(0.5))
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                    }
                    .position(x: frame.midX, y: frame.midY)
                }
            }
        }
        .animation(.snappy(duration: 0.22), value: selectedSlice?.id)
        .onChange(of: visibleSlices.map(\.id)) { _, _ in
            selectedAngle = nil
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(centerTitle)
        .accessibilityValue(accessibilityValue)
    }

    private var accessibilityValue: String {
        visibleSlices
            .map { "\($0.title), \($0.value)" }
            .joined(separator: "; ")
    }
}

struct SWStatsRatingPoint: Identifiable {
    let rating: Double
    let count: Int

    var id: Double { rating }
}

enum SWStatsChartSelection {
    static func index(for value: Double?, count: Int) -> Int? {
        guard let value, value.isFinite, count > 0 else { return nil }
        let clamped = min(max(value, 0), Double(count - 1))
        return Int(clamped.rounded())
    }
}

private struct StatsChartSelectionPill: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 9, weight: .black))
            .foregroundStyle(.white.opacity(0.92))
            .monospacedDigit()
            .padding(.horizontal, 8)
            .frame(minHeight: 24)
            .background(.ultraThinMaterial, in: Capsule())
            .overlay {
                Capsule()
                    .strokeBorder(.white.opacity(0.14), lineWidth: 0.5)
            }
            .accessibilityHidden(true)
    }
}

struct SWStatsRatingChart: View {
    let points: [SWStatsRatingPoint]
    let average: Double?
    let tint: Color

    @State private var selectedIndex: Int?
    @State private var haptics = UISelectionFeedbackGenerator()

    static func normalizedPoints(from points: [SWStatsRatingPoint]) -> [SWStatsRatingPoint] {
        let counts = points.reduce(into: [Int: Int]()) { result, point in
            guard point.rating.isFinite,
                  (0 ... 10).contains(point.rating),
                  point.count >= 0 else { return }
            let index = Int((point.rating * 2).rounded())
            guard abs(Double(index) / 2 - point.rating) < 0.001 else { return }
            result[index, default: 0] += point.count
        }
        return (0 ... 20).map {
            SWStatsRatingPoint(rating: Double($0) / 2, count: counts[$0, default: 0])
        }
    }

    static func normalizedPoints(
        from buckets: [StatsRatingBucket],
        mediaType: String?
    ) -> [SWStatsRatingPoint] {
        // ponytail: the API omits rating-scale metadata; infer the app's native five-star types until it carries a scale.
        let usesFiveStarScale = mediaType.map { ["movie", "music", "book", "game"].contains($0) } == true
            && !buckets.contains { ($0.numericRating ?? 0) > 5 && $0.count > 0 }
        let multiplier = usesFiveStarScale ? 2.0 : 1.0
        return normalizedPoints(from: buckets.compactMap { bucket in
            guard let rating = bucket.numericRating else { return nil }
            return SWStatsRatingPoint(rating: rating * multiplier, count: bucket.count)
        })
    }

    static func normalizedPoints(from mediaTypes: [StatsMediaTypeSummary]) -> [SWStatsRatingPoint] {
        normalizedPoints(from: mediaTypes.flatMap {
            normalizedPoints(from: $0.ratingDistribution, mediaType: $0.mediaType)
        })
    }

    static func averageRating(from points: [SWStatsRatingPoint]) -> Double? {
        let normalized = normalizedPoints(from: points)
        let count = normalized.reduce(0) { $0 + $1.count }
        guard count > 0 else { return nil }
        return normalized.reduce(0) { $0 + $1.rating * Double($1.count) } / Double(count)
    }

    private var normalizedPoints: [SWStatsRatingPoint] {
        Self.normalizedPoints(from: points)
    }

    private var selectedPoint: SWStatsRatingPoint? {
        guard let selectedIndex, normalizedPoints.indices.contains(selectedIndex) else { return nil }
        return normalizedPoints[selectedIndex]
    }

    var body: some View {
        Chart {
            ForEach(normalizedPoints) { point in
                BarMark(
                    x: .value("Rating step", point.rating * 2),
                    y: .value("Count", point.count),
                    width: .fixed(8)
                )
                .cornerRadius(3)
                .foregroundStyle(
                    LinearGradient(
                        colors: [tint.opacity(0.58), tint],
                        startPoint: .bottom,
                        endPoint: .top
                    )
                )
                .opacity(
                    selectedIndex == nil || selectedIndex == Int(point.rating * 2)
                        ? 1
                        : 0.24
                )
            }

            if let selectedPoint {
                RuleMark(x: .value("Selected rating", selectedPoint.rating * 2))
                    .foregroundStyle(.white.opacity(0.34))
                    .lineStyle(StrokeStyle(lineWidth: 1))
                    .annotation(
                        position: .top,
                        alignment: selectedPoint.rating > 5 ? .trailing : .leading
                    ) {
                        StatsChartSelectionPill(text: selectionText(for: selectedPoint))
                    }
            } else if let average {
                RuleMark(x: .value("Average", min(20, max(0, average * 2))))
                    .foregroundStyle(.white.opacity(0.7))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    .annotation(position: .top, alignment: .trailing) {
                        Text("AVG \(average.formatted(.number.precision(.fractionLength(1))))")
                            .font(.system(size: 9, weight: .black))
                            .foregroundStyle(.white.opacity(0.62))
                }
            }
        }
        .chartOverlay { proxy in
            GeometryReader { geometry in
                if let plotFrame = proxy.plotFrame {
                    let frame = geometry[plotFrame]
                    Rectangle()
                        .fill(.clear)
                        .contentShape(Rectangle())
                        .frame(width: frame.width, height: frame.height)
                        .position(x: frame.midX, y: frame.midY)
                        .gesture(scrubGesture(proxy: proxy))
                }
            }
        }
        .onAppear { haptics.prepare() }
        .onChange(of: normalizedPoints.map(\.id)) { _, _ in
            selectedIndex = nil
        }
        .chartXScale(domain: -2 ... 22)
        .chartXAxis {
            AxisMarks(values: [0.0, 4.0, 8.0, 12.0, 16.0, 20.0]) { value in
                AxisGridLine().foregroundStyle(.clear)
                AxisTick().foregroundStyle(.white.opacity(0.18))
                AxisValueLabel {
                    if let index = value.as(Double.self) {
                        Text((index / 2).formatted(.number.precision(.fractionLength(0))))
                            .foregroundStyle(.white.opacity(0.42))
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine().foregroundStyle(.white.opacity(0.055))
                AxisValueLabel {
                    if let count = value.as(Int.self) {
                        Text(count.formatted())
                            .foregroundStyle(.white.opacity(0.36))
                    }
                }
            }
        }
        .accessibilityChartDescriptor(StatsRatingChartDescriptor(points: normalizedPoints, average: average))
        .accessibilityIdentifier("stats.ratingChart")
    }

    private func scrubGesture(proxy: ChartProxy) -> some Gesture {
        LongPressGesture(minimumDuration: 0.2)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { value in
                guard case let .second(true, drag) = value, let drag else { return }
                selectPoint(at: drag.location.x, proxy: proxy)
            }
            .onEnded { _ in
                selectedIndex = nil
            }
    }

    private func selectPoint(at x: CGFloat, proxy: ChartProxy) {
        guard
            let index = SWStatsChartSelection.index(
                for: proxy.value(atX: x, as: Double.self),
                count: normalizedPoints.count
            ),
            index != selectedIndex
        else { return }
        selectedIndex = index
        haptics.selectionChanged()
        haptics.prepare()
    }

    private func selectionText(for point: SWStatsRatingPoint) -> String {
        let rating = point.rating.formatted(.number.precision(.fractionLength(0...1)))
        let title = point.count == 1 ? "title" : "titles"
        return "\(rating) · \(point.count.formatted()) \(title)"
    }
}

private struct StatsRatingChartDescriptor: AXChartDescriptorRepresentable {
    let points: [SWStatsRatingPoint]
    let average: Double?

    func makeChartDescriptor() -> AXChartDescriptor {
        let ratedPoints = points.filter { $0.count > 0 }
        let xAxis = AXNumericDataAxisDescriptor(
            title: "Rating",
            range: 0...10,
            gridlinePositions: [0, 2, 4, 6, 8, 10]
        ) { value in
            value.formatted(.number.precision(.fractionLength(0...1)))
        }
        let maximum = Double(max(1, ratedPoints.map(\.count).max() ?? 1))
        let yAxis = AXNumericDataAxisDescriptor(
            title: "Titles",
            range: 0...maximum,
            gridlinePositions: []
        ) { value in
            Int(value).formatted()
        }
        let series = AXDataSeriesDescriptor(
            name: average.map { "Ratings, average \($0.formatted(.number.precision(.fractionLength(1))))" } ?? "Ratings",
            isContinuous: false,
            dataPoints: ratedPoints.map {
                AXDataPoint(x: $0.rating, y: Double($0.count), label: "\($0.rating) out of 10")
            }
        )
        return AXChartDescriptor(
            title: "Rating distribution",
            summary: nil,
            xAxis: xAxis,
            yAxis: yAxis,
            additionalAxes: [],
            series: [series]
        )
    }
}

struct SWStatsActivityPoint: Identifiable {
    let date: Date
    let count: Int

    var id: Date { date }
}

struct SWStatsActivityHeatmap: View {
    private struct Week: Identifiable {
        let startDate: Date
        let days: [SWStatsActivityPoint]

        var id: Date { startDate }
    }

    let points: [SWStatsActivityPoint]
    let tint: Color
    let startDate: Date?
    let endDate: Date?

    private let cellSize: CGFloat = 11
    private let spacing: CGFloat = 3

    private var weeks: [Week] {
        guard let firstDate = startDate ?? points.map(\.date).min(),
              let lastDate = endDate ?? points.map(\.date).max() else { return [] }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let firstDay = calendar.startOfDay(for: firstDate)
        let lastDay = calendar.startOfDay(for: lastDate)
        guard let interval = calendar.dateInterval(of: .weekOfYear, for: firstDay) else { return [] }
        let alignedStart = interval.start
        let counts = Dictionary(
            points.map { (calendar.startOfDay(for: $0.date), $0.count) },
            uniquingKeysWith: +
        )

        var allDays: [SWStatsActivityPoint] = []
        var cursor = alignedStart
        repeat {
            allDays.append(SWStatsActivityPoint(date: cursor, count: counts[cursor] ?? 0))
            guard let next = calendar.date(byAdding: .day, value: 1, to: cursor) else { break }
            cursor = next
        } while cursor <= lastDay || allDays.count % 7 != 0

        return stride(from: 0, to: allDays.count, by: 7).map { offset in
            let days = Array(allDays[offset..<min(offset + 7, allDays.count)])
            return Week(startDate: days[0].date, days: days)
        }
    }

    private var maximumCount: Int {
        max(1, points.map(\.count).max() ?? 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(alignment: .top, spacing: spacing) {
                        ForEach(weeks) { week in
                            VStack(spacing: spacing) {
                                ForEach(week.days) { day in
                                    RoundedRectangle(cornerRadius: 2.5, style: .continuous)
                                        .fill(color(for: day.count))
                                        .frame(width: cellSize, height: cellSize)
                                        .accessibilityHidden(true)
                                }
                            }
                            .id(week.id)
                        }
                    }
                    .padding(.horizontal, 1)
                }
                .onAppear {
                    guard let lastWeek = weeks.last else { return }
                    proxy.scrollTo(lastWeek.id, anchor: .trailing)
                }
                .onChange(of: weeks.last?.id) { _, lastID in
                    guard let lastID else { return }
                    proxy.scrollTo(lastID, anchor: .trailing)
                }
            }

            HStack(spacing: 5) {
                Text("LESS")
                ForEach(0..<5, id: \.self) { level in
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(level == 0 ? Color.white.opacity(0.055) : tint.opacity(0.18 + Double(level) * 0.19))
                        .frame(width: 9, height: 9)
                }
                Text("MORE")
            }
            .font(.system(size: 8, weight: .black))
            .foregroundStyle(.white.opacity(0.32))
            .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Activity history")
        .accessibilityValue("\(activeDays) active days and \(totalActivity) logged activities")
    }

    private var activeDays: Int {
        points.filter { $0.count > 0 }.count
    }

    private var totalActivity: Int {
        points.reduce(0) { $0 + $1.count }
    }

    private func color(for count: Int) -> Color {
        guard count > 0 else { return .white.opacity(0.055) }
        let ratio = Double(count) / Double(maximumCount)
        switch ratio {
        case ..<0.26: return tint.opacity(0.34)
        case ..<0.51: return tint.opacity(0.54)
        case ..<0.76: return tint.opacity(0.76)
        default: return tint
        }
    }
}

struct SWStatsYearPoint: Identifiable {
    let year: Int
    let count: Int

    var id: Int { year }
}

struct SWStatsYearChart: View {
    let points: [SWStatsYearPoint]
    let tint: Color

    @State private var selectedIndex: Int?
    @State private var haptics = UISelectionFeedbackGenerator()

    struct PlotPoint: Identifiable, Equatable {
        let index: Int
        let year: Int
        let count: Int

        var id: Int { year }
    }

    static func plotPoints(from points: [SWStatsYearPoint]) -> [PlotPoint] {
        Dictionary(grouping: points, by: \.year)
            .map { year, buckets in
                SWStatsYearPoint(year: year, count: buckets.reduce(0) { $0 + $1.count })
            }
            .sorted { $0.year < $1.year }
            .enumerated()
            .map { PlotPoint(index: $0.offset, year: $0.element.year, count: $0.element.count) }
    }

    private var plotPoints: [PlotPoint] {
        Self.plotPoints(from: points)
    }

    private var selectedPoint: PlotPoint? {
        guard let selectedIndex, plotPoints.indices.contains(selectedIndex) else { return nil }
        return plotPoints[selectedIndex]
    }

    private var labelStride: Int {
        max(1, Int(ceil(Double(max(plotPoints.count - 1, 1)) / 4)))
    }

    private var tickIndices: [Double] {
        let lastIndex = plotPoints.count - 1
        return plotPoints.indices.compactMap { index in
            index == 0 || index == lastIndex || index.isMultiple(of: labelStride)
                ? Double(index)
                : nil
        }
    }

    var body: some View {
        Chart {
            ForEach(plotPoints) { point in
                BarMark(
                    x: .value("Release year index", Double(point.index)),
                    y: .value("Titles", point.count),
                    width: .fixed(3)
                )
                .cornerRadius(3)
                .foregroundStyle(tint.gradient)
                .opacity(selectedIndex == nil || selectedIndex == point.index ? 1 : 0.24)
            }

            if let selectedPoint {
                RuleMark(x: .value("Selected release year", Double(selectedPoint.index)))
                    .foregroundStyle(.white.opacity(0.34))
                    .lineStyle(StrokeStyle(lineWidth: 1))
                    .annotation(
                        position: .top,
                        alignment: selectedPoint.index > plotPoints.count / 2 ? .trailing : .leading
                    ) {
                        StatsChartSelectionPill(text: selectionText(for: selectedPoint))
                    }
            }
        }
        .chartXScale(domain: -0.5 ... Double(max(1, plotPoints.count)) - 0.5)
        .chartXAxis {
            AxisMarks(values: tickIndices) { value in
                AxisGridLine().foregroundStyle(.clear)
                AxisTick().foregroundStyle(.white.opacity(0.16))
                AxisValueLabel {
                    if let rawIndex = value.as(Double.self) {
                        let index = Int(rawIndex.rounded())
                        if plotPoints.indices.contains(index) {
                            Text("’\(String(plotPoints[index].year).suffix(2))")
                                .font(.system(size: 8, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.42))
                                .fixedSize()
                        }
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine().foregroundStyle(.white.opacity(0.055))
                AxisValueLabel {
                    if let count = value.as(Int.self) {
                        Text(count.formatted())
                            .foregroundStyle(.white.opacity(0.36))
                    }
                }
            }
        }
        .chartOverlay { proxy in
            GeometryReader { geometry in
                if let plotFrame = proxy.plotFrame {
                    let frame = geometry[plotFrame]
                    Rectangle()
                        .fill(.clear)
                        .contentShape(Rectangle())
                        .frame(width: frame.width, height: frame.height)
                        .position(x: frame.midX, y: frame.midY)
                        .gesture(scrubGesture(proxy: proxy))
                }
            }
        }
        .onAppear { haptics.prepare() }
        .onChange(of: plotPoints.map(\.id)) { _, _ in
            selectedIndex = nil
        }
        .accessibilityChartDescriptor(StatsYearChartDescriptor(points: plotPoints))
        .accessibilityIdentifier("stats.yearChart")
    }

    private func scrubGesture(proxy: ChartProxy) -> some Gesture {
        LongPressGesture(minimumDuration: 0.2)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { value in
                guard case let .second(true, drag) = value, let drag else { return }
                selectPoint(at: drag.location.x, proxy: proxy)
            }
            .onEnded { _ in
                selectedIndex = nil
            }
    }

    private func selectPoint(at x: CGFloat, proxy: ChartProxy) {
        guard
            let index = SWStatsChartSelection.index(
                for: proxy.value(atX: x, as: Double.self),
                count: plotPoints.count
            ),
            index != selectedIndex
        else { return }
        selectedIndex = index
        haptics.selectionChanged()
        haptics.prepare()
    }

    private func selectionText(for point: PlotPoint) -> String {
        let title = point.count == 1 ? "title" : "titles"
        return "\(point.year) · \(point.count.formatted()) \(title)"
    }
}

private struct StatsYearChartDescriptor: AXChartDescriptorRepresentable {
    let points: [SWStatsYearChart.PlotPoint]

    func makeChartDescriptor() -> AXChartDescriptor {
        let firstYear = Double(points.first?.year ?? 0)
        let lastYear = Double(points.last?.year ?? 1)
        let xAxis = AXNumericDataAxisDescriptor(
            title: "Release year",
            range: firstYear ... max(firstYear + 1, lastYear),
            gridlinePositions: []
        ) { String(Int($0)) }
        let maximum = Double(max(1, points.map(\.count).max() ?? 1))
        let yAxis = AXNumericDataAxisDescriptor(
            title: "Titles",
            range: 0 ... maximum,
            gridlinePositions: []
        ) { Int($0).formatted() }
        let series = AXDataSeriesDescriptor(
            name: "Titles by release year",
            isContinuous: false,
            dataPoints: points.map {
                AXDataPoint(x: Double($0.year), y: Double($0.count), label: String($0.year))
            }
        )
        return AXChartDescriptor(
            title: "Titles by release year",
            summary: nil,
            xAxis: xAxis,
            yAxis: yAxis,
            additionalAxes: [],
            series: [series]
        )
    }
}
