import Accessibility
import SwiftUI
import UIKit

// MARK: - Palette

enum StatsPalette {
    static let neutral = Color.white.opacity(0.88)
    static let rating = Color(red: 0.99, green: 0.77, blue: 0.29)
    static let surface = Color.white.opacity(0.045)

    static func accent(for mediaType: String?) -> Color {
        mediaType.map { MediaTypeTheme.theme(for: $0).statsColor } ?? neutral
    }

    static func mediaMixColor(for mediaType: String) -> Color {
        MediaTypeTheme.theme(for: mediaType).statsColor
    }
}

// MARK: - Layout primitives

struct StatsCard<Content: View>: View {
    var padding: CGFloat = 18
    @ViewBuilder let content: () -> Content

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 22, style: .continuous)

        content()
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(StatsPalette.surface, in: shape)
            .overlay {
                shape.strokeBorder(.white.opacity(0.06), lineWidth: 0.5)
            }
    }
}

struct StatsSection<Content: View, Accessory: View>: View {
    let title: String
    @ViewBuilder let accessory: () -> Accessory
    @ViewBuilder let content: () -> Content

    init(
        title: String,
        @ViewBuilder accessory: @escaping () -> Accessory,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.title = title
        self.accessory = accessory
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center) {
                Text(title.uppercased())
                    .font(.system(size: 13, weight: .black))
                    .foregroundStyle(.white.opacity(0.58))
                    .tracking(0.8)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                accessory()
            }
            .frame(minHeight: 22)

            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension StatsSection where Accessory == EmptyView {
    init(title: String, @ViewBuilder content: @escaping () -> Content) {
        self.init(title: title, accessory: { EmptyView() }, content: content)
    }
}

/// Large value + caption that leads every chart card. The text swaps to the
/// selected bucket while a chart is being scrubbed.
struct StatsHeadline: View {
    let value: String
    var unit: String?
    var symbol: String?
    var symbolTint: Color = StatsPalette.rating
    let caption: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(value)
                    .font(.system(size: 30, weight: .bold))
                    .foregroundStyle(.white)
                    .monospacedDigit()
                    .contentTransition(.numericText())

                if let symbol {
                    Image(systemName: symbol)
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(symbolTint)
                }

                if let unit {
                    Text(unit)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.5))
                }
            }

            Text(caption)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.white.opacity(0.5))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .animation(.snappy(duration: 0.22), value: value)
        .animation(.snappy(duration: 0.22), value: caption)
        .accessibilityElement(children: .combine)
    }
}

struct StatsInlineMetric: Identifiable {
    let title: String
    let value: String

    var id: String { title }
}

struct StatsInlineMetrics: View {
    let metrics: [StatsInlineMetric]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(metrics.enumerated()), id: \.element.id) { index, metric in
                if index > 0 {
                    Rectangle()
                        .fill(.white.opacity(0.08))
                        .frame(width: 0.5, height: 30)
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text(metric.value)
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(.white.opacity(0.94))
                        .monospacedDigit()
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .contentTransition(.numericText())
                    Text(metric.title)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.white.opacity(0.46))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, index > 0 ? 14 : 0)
                .accessibilityElement(children: .combine)
            }
        }
    }
}

/// Small segmented control used in section headers (e.g. Genres / Languages).
struct StatsSegmentedToggle<Option: Hashable>: View {
    let options: [Option]
    @Binding var selection: Option
    let title: (Option) -> String

    @Namespace private var namespace

    var body: some View {
        HStack(spacing: 2) {
            ForEach(options, id: \.self) { option in
                let isSelected = option == selection

                Button {
                    withAnimation(.snappy(duration: 0.24)) {
                        selection = option
                    }
                } label: {
                    Text(title(option))
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.white.opacity(isSelected ? 0.95 : 0.46))
                        .padding(.horizontal, 11)
                        .frame(minHeight: 28)
                        .background {
                            if isSelected {
                                Capsule()
                                    .fill(.white.opacity(0.13))
                                    .matchedGeometryEffect(id: "selection", in: namespace)
                            }
                        }
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
        .padding(2)
        .background(.white.opacity(0.05), in: Capsule())
        .sensoryFeedback(.selection, trigger: selection)
    }
}

// MARK: - Reveal animation

/// Grows chart marks from zero the first time a chart scrolls into view.
private struct StatsRevealModifier: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var isRevealed: Bool

    func body(content: Content) -> some View {
        content
            .onScrollVisibilityChange(threshold: 0.2) { isVisible in
                guard isVisible, !isRevealed else { return }
                if reduceMotion {
                    isRevealed = true
                } else {
                    withAnimation(.smooth(duration: 0.75)) {
                        isRevealed = true
                    }
                }
            }
    }
}

private extension View {
    func statsReveal(_ isRevealed: Binding<Bool>) -> some View {
        modifier(StatsRevealModifier(isRevealed: isRevealed))
    }
}

// MARK: - Selection

enum SWStatsChartSelection {
    static func index(for value: Double?, count: Int) -> Int? {
        guard let value, value.isFinite, count > 0 else { return nil }
        let clamped = min(max(value, 0), Double(count - 1))
        return Int(clamped.rounded())
    }
}

/// Horizontal-only pan for scrubbing charts. It refuses to begin on vertical
/// drags, so swipes that start on a chart still scroll the page, and the page
/// scroll and the iOS 26 content back-swipe wait for it on horizontal drags.
struct StatsHorizontalScrubGesture: UIGestureRecognizerRepresentable {
    let onChange: (CGPoint) -> Void

    func makeCoordinator(converter _: CoordinateSpaceConverter) -> Coordinator {
        Coordinator()
    }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let recognizer = UIPanGestureRecognizer()
        recognizer.delegate = context.coordinator
        return recognizer
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        switch recognizer.state {
        case .began, .changed:
            onChange(context.converter.localLocation)
        default:
            break
        }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard let pan = gestureRecognizer as? UIPanGestureRecognizer else { return true }
            let velocity = pan.velocity(in: pan.view)
            return abs(velocity.x) > abs(velocity.y)
        }

        func gestureRecognizer(
            _: UIGestureRecognizer,
            shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            otherGestureRecognizer.view is UIScrollView || otherGestureRecognizer is UIPanGestureRecognizer
        }
    }
}

// MARK: - Bar chart

struct StatsBarItem: Identifiable, Equatable {
    let id: String
    let value: Int
    var axisLabel: String?
    let accessibilityLabel: String
}

/// Rounded-bar chart drawn with plain SwiftUI so the bars stay crisp at any
/// density. Tap a bar or drag horizontally to inspect it; vertical drags still
/// scroll the page.
struct StatsBarChart: View {
    let items: [StatsBarItem]
    let tint: Color
    var height: CGFloat = 132
    var emphasizedID: String?
    var maximumBarWidth: CGFloat = 22
    let accessibilityTitle: String
    @Binding var selectedID: String?

    @State private var isRevealed = false

    private struct Metrics {
        let barWidth: CGFloat
        let spacing: CGFloat

        var step: CGFloat { barWidth + spacing }
    }

    private var maximum: Int {
        max(1, items.map(\.value).max() ?? 1)
    }

    private var showsAxis: Bool {
        items.contains { $0.axisLabel != nil }
    }

    var body: some View {
        GeometryReader { proxy in
            let metrics = metrics(for: proxy.size.width)
            let barAreaHeight = proxy.size.height - (showsAxis ? 20 : 0)

            VStack(spacing: 0) {
                HStack(alignment: .bottom, spacing: metrics.spacing) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        bar(item, index: index, width: metrics.barWidth, maxHeight: barAreaHeight)
                    }
                }
                .frame(width: proxy.size.width, height: barAreaHeight, alignment: .bottom)
                .contentShape(Rectangle())
                .onTapGesture { location in
                    toggleSelection(at: location.x, metrics: metrics)
                }
                .gesture(StatsHorizontalScrubGesture { location in
                    scrub(to: location.x, metrics: metrics)
                })

                if showsAxis {
                    HStack(alignment: .top, spacing: metrics.spacing) {
                        ForEach(items) { item in
                            Text(item.axisLabel ?? "")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundStyle(.white.opacity(item.id == selectedID ? 0.85 : 0.38))
                                .fixedSize()
                                .frame(width: metrics.barWidth)
                        }
                    }
                    .frame(width: proxy.size.width, height: 20, alignment: .bottom)
                }
            }
        }
        .frame(height: height)
        .statsReveal($isRevealed)
        .sensoryFeedback(.selection, trigger: selectedID)
        .onChange(of: items.map(\.id)) { _, ids in
            if let selectedID, !ids.contains(selectedID) {
                self.selectedID = nil
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityTitle)
        .accessibilityValue(accessibilityValue)
        .accessibilityChartDescriptor(
            StatsCategoryChartDescriptor(
                title: accessibilityTitle,
                points: items.map { ($0.accessibilityLabel, $0.value) }
            )
        )
    }

    private func bar(_ item: StatsBarItem, index: Int, width: CGFloat, maxHeight: CGFloat) -> some View {
        let ratio = CGFloat(item.value) / CGFloat(maximum)
        let restingHeight: CGFloat = 3
        let targetHeight = item.value == 0 ? restingHeight : max(6, ratio * maxHeight)

        return RoundedRectangle(cornerRadius: min(width / 2, 5), style: .continuous)
            .fill(fill(for: item))
            .frame(width: width, height: isRevealed ? targetHeight : restingHeight)
            .animation(
                .smooth(duration: 0.7).delay(min(Double(index) * 0.018, 0.35)),
                value: isRevealed
            )
            .animation(.smooth(duration: 0.35), value: item.value)
            .animation(.snappy(duration: 0.18), value: selectedID)
    }

    private func fill(for item: StatsBarItem) -> AnyShapeStyle {
        if item.value == 0 {
            return AnyShapeStyle(Color.white.opacity(0.08))
        }
        if let selectedID {
            return AnyShapeStyle(item.id == selectedID ? tint : tint.opacity(0.24))
        }
        if let emphasizedID {
            return AnyShapeStyle(item.id == emphasizedID ? tint : tint.opacity(0.38))
        }
        return AnyShapeStyle(
            LinearGradient(
                colors: [tint.opacity(0.55), tint],
                startPoint: .bottom,
                endPoint: .top
            )
        )
    }

    private func metrics(for width: CGFloat) -> Metrics {
        let count = CGFloat(max(items.count, 1))
        let preferredSpacing: CGFloat = items.count > 24 ? 3 : (items.count > 12 ? 5 : 7)
        let barWidth = min(
            maximumBarWidth,
            max(2, (width - preferredSpacing * (count - 1)) / count)
        )
        let spacing = count > 1 ? max(0, (width - barWidth * count) / (count - 1)) : 0
        return Metrics(barWidth: barWidth, spacing: spacing)
    }

    private func index(at x: CGFloat, metrics: Metrics) -> Int? {
        guard metrics.step > 0 else { return nil }
        let position = Double((x - metrics.barWidth / 2) / metrics.step)
        return SWStatsChartSelection.index(for: position, count: items.count)
    }

    private func toggleSelection(at x: CGFloat, metrics: Metrics) {
        guard let index = index(at: x, metrics: metrics) else { return }
        let id = items[index].id
        selectedID = selectedID == id ? nil : id
    }

    private func scrub(to x: CGFloat, metrics: Metrics) {
        guard let index = index(at: x, metrics: metrics) else { return }
        let id = items[index].id
        if selectedID != id {
            selectedID = id
        }
    }

    private var accessibilityValue: String {
        let populated = items.filter { $0.value > 0 }
        guard !populated.isEmpty else { return "No data" }
        return populated
            .map { "\($0.accessibilityLabel), \($0.value.formatted())" }
            .joined(separator: "; ")
    }
}

private struct StatsCategoryChartDescriptor: AXChartDescriptorRepresentable {
    let title: String
    let points: [(String, Int)]

    func makeChartDescriptor() -> AXChartDescriptor {
        let maximum = Double(max(1, points.map(\.1).max() ?? 1))
        let xAxis = AXCategoricalDataAxisDescriptor(
            title: title,
            categoryOrder: points.map(\.0)
        )
        let yAxis = AXNumericDataAxisDescriptor(
            title: "Count",
            range: 0 ... maximum,
            gridlinePositions: []
        ) { Int($0).formatted() }
        let series = AXDataSeriesDescriptor(
            name: title,
            isContinuous: false,
            dataPoints: points.map { AXDataPoint(x: $0.0, y: Double($0.1)) }
        )
        return AXChartDescriptor(
            title: title,
            summary: nil,
            xAxis: xAxis,
            yAxis: yAxis,
            additionalAxes: [],
            series: [series]
        )
    }
}

// MARK: - Activity

struct StatsActivityBucket: Identifiable, Equatable {
    let id: String
    let title: String
    let axisLabel: String?
    let count: Int
}

struct StatsActivitySeries: Equatable {
    enum Granularity: Equatable {
        case month
        case year
    }

    let granularity: Granularity
    let buckets: [StatsActivityBucket]

    var total: Int {
        buckets.reduce(0) { $0 + $1.count }
    }

    var busiest: StatsActivityBucket? {
        buckets
            .filter { $0.count > 0 }
            .max { lhs, rhs in
                lhs.count == rhs.count ? lhs.id < rhs.id : lhs.count < rhs.count
            }
    }

    /// Year scopes chart each month of that year. All Time charts each year
    /// through the current one, falling back to the trailing twelve months
    /// when the history is too short for a yearly view to say anything.
    static func make(
        months: [StatsActivityMonth],
        range: StatsRange,
        now: Date = .now,
        calendar: Calendar = .current
    ) -> StatsActivitySeries {
        let counts = months.reduce(into: [String: Int]()) { result, month in
            result[month.month, default: 0] += max(0, month.count)
        }
        let currentYear = calendar.component(.year, from: now)

        if !range.isAllTime {
            let year = range.startDate.flatMap { Int($0.prefix(4)) } ?? currentYear
            return StatsActivitySeries(
                granularity: .month,
                buckets: (1 ... 12).map { month in
                    monthBucket(year: year, month: month, counts: counts, calendar: calendar)
                }
            )
        }

        let activeYears = counts.compactMap { key, count in
            count > 0 ? Int(key.prefix(4)) : nil
        }
        guard let firstYear = activeYears.min() else {
            return StatsActivitySeries(granularity: .year, buckets: [])
        }
        let lastYear = max(currentYear, activeYears.max() ?? currentYear)

        if lastYear - firstYear + 1 >= 3 {
            let yearlyCounts = counts.reduce(into: [Int: Int]()) { result, entry in
                guard let year = Int(entry.key.prefix(4)) else { return }
                result[year, default: 0] += entry.value
            }
            let years = Array(firstYear ... lastYear)
            let labelIndices = axisLabelIndices(count: years.count, maximumLabels: 7)
            return StatsActivitySeries(
                granularity: .year,
                buckets: years.enumerated().map { index, year in
                    StatsActivityBucket(
                        id: String(year),
                        title: String(year),
                        axisLabel: labelIndices.contains(index) ? String(year) : nil,
                        count: yearlyCounts[year, default: 0]
                    )
                }
            )
        }

        let currentMonth = calendar.component(.month, from: now)
        let buckets = (0 ..< 12).reversed().map { offset -> StatsActivityBucket in
            let absoluteMonth = currentYear * 12 + (currentMonth - 1) - offset
            return monthBucket(
                year: absoluteMonth / 12,
                month: absoluteMonth % 12 + 1,
                counts: counts,
                calendar: calendar
            )
        }
        return StatsActivitySeries(granularity: .month, buckets: buckets)
    }

    static func axisLabelIndices(count: Int, maximumLabels: Int) -> Set<Int> {
        guard count > 0 else { return [] }
        guard count > maximumLabels else { return Set(0 ..< count) }
        let stride = Int((Double(count - 1) / Double(maximumLabels - 1)).rounded(.up))
        var indices = Set(Swift.stride(from: 0, to: count, by: stride))
        let last = count - 1
        if let previous = indices.filter({ $0 != last }).max(), last - previous < max(2, stride / 2 + 1) {
            indices.remove(previous)
        }
        indices.insert(last)
        return indices
    }

    private static func monthBucket(
        year: Int,
        month: Int,
        counts: [String: Int],
        calendar: Calendar
    ) -> StatsActivityBucket {
        let id = String(format: "%04d-%02d", year, month)
        let symbols = calendar.veryShortStandaloneMonthSymbols
        let names = calendar.standaloneMonthSymbols
        let name = names.indices.contains(month - 1) ? names[month - 1] : id
        return StatsActivityBucket(
            id: id,
            title: "\(name) \(year)",
            axisLabel: symbols.indices.contains(month - 1) ? symbols[month - 1] : nil,
            count: counts[id, default: 0]
        )
    }
}

enum StatsWeekdayDistribution {
    static let shortNames = ["M", "T", "W", "T", "F", "S", "S"]
    static let names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    /// Active days per weekday, Monday first, matching the API's
    /// `most_active_weekday` numbering.
    static func activeDays(from days: [StatsActivityDay]) -> [Int] {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        var counts = Array(repeating: 0, count: 7)
        for day in days where day.count > 0 {
            guard let date = day.parsedDate else { continue }
            let weekday = calendar.component(.weekday, from: date)
            counts[(weekday + 5) % 7] += 1
        }
        return counts
    }
}

// MARK: - Ratings

struct SWStatsRatingPoint: Identifiable {
    let rating: Double
    let count: Int

    var id: Double { rating }
}

struct StatsStarBucket: Identifiable, Equatable {
    /// Half-star step, 1 (½★) through 10 (5★).
    let step: Int
    let count: Int

    var id: Int { step }
    var stars: Double { Double(step) / 2 }
}

enum SWStatsRatingChart {
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
        let usesFiveStarScale = mediaType.map { ["movie", "music", "book"].contains($0) } == true
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

    /// Folds ten-point ratings onto the app's ½–5 star display scale.
    static func starBuckets(from points: [SWStatsRatingPoint]) -> [StatsStarBucket] {
        let counts = normalizedPoints(from: points).reduce(into: [Int: Int]()) { result, point in
            guard point.count > 0 else { return }
            let step = min(max(Int(point.rating.rounded(.toNearestOrAwayFromZero)), 1), 10)
            result[step, default: 0] += point.count
        }
        return (1 ... 10).map { StatsStarBucket(step: $0, count: counts[$0, default: 0]) }
    }

    static func starLabel(_ stars: Double) -> String {
        stars.formatted(.number.precision(.fractionLength(0 ... 1)))
    }
}

struct StatsStarRow: View {
    let stars: Double
    var size: CGFloat = 10

    var body: some View {
        HStack(spacing: 1) {
            ForEach(Array(symbolNames.enumerated()), id: \.offset) { _, name in
                Image(systemName: name)
                    .font(.system(size: size, weight: .bold))
            }
        }
        .foregroundStyle(StatsPalette.rating)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Rated \(SWStatsRatingChart.starLabel(stars)) out of 5 stars")
    }

    private var symbolNames: [String] {
        let clamped = min(max(stars, 0), 5)
        let full = Int(clamped.rounded(.down))
        let hasHalf = clamped - Double(full) >= 0.5
        return Array(repeating: "star.fill", count: full) + (hasHalf ? ["star.leadinghalf.filled"] : [])
    }
}

// MARK: - Release years

struct SWStatsYearPoint: Identifiable, Equatable {
    let year: Int
    let count: Int

    var id: Int { year }
}

struct StatsDecade: Equatable {
    let decade: Int
    let count: Int

    var title: String { "\(decade)s" }
}

/// Release-year buckets for the bar chart: decades when the history spans
/// generations, individual years when it is short enough to read year by year.
struct StatsReleaseSeries: Equatable {
    enum Granularity: Equatable {
        case decade
        case year
    }

    let granularity: Granularity
    let buckets: [StatsActivityBucket]

    var peak: StatsActivityBucket? {
        buckets
            .filter { $0.count > 0 }
            .max { lhs, rhs in lhs.count == rhs.count ? lhs.id < rhs.id : lhs.count < rhs.count }
    }

    static func make(points: [SWStatsYearPoint], maximumYearBuckets: Int = 16) -> StatsReleaseSeries {
        let filled = SWStatsYearChart.filledPoints(from: points)
        guard let first = filled.first?.year, let last = filled.last?.year else {
            return StatsReleaseSeries(granularity: .year, buckets: [])
        }

        if last - first + 1 <= maximumYearBuckets {
            let labels = StatsActivitySeries.axisLabelIndices(count: filled.count, maximumLabels: 5)
            return StatsReleaseSeries(
                granularity: .year,
                buckets: filled.enumerated().map { index, point in
                    StatsActivityBucket(
                        id: String(point.year),
                        title: String(point.year),
                        axisLabel: labels.contains(index) ? String(point.year) : nil,
                        count: point.count
                    )
                }
            )
        }

        let totals = filled.reduce(into: [Int: Int]()) { result, point in
            result[decade(of: point.year), default: 0] += point.count
        }
        let decades = Array(stride(from: decade(of: first), through: decade(of: last), by: 10))
        let labels = StatsActivitySeries.axisLabelIndices(count: decades.count, maximumLabels: 9)
        return StatsReleaseSeries(
            granularity: .decade,
            buckets: decades.enumerated().map { index, decade in
                StatsActivityBucket(
                    id: "\(decade)s",
                    title: "\(decade)s",
                    axisLabel: labels.contains(index) ? "’" + String(format: "%02d", abs(decade) % 100) + "s" : nil,
                    count: totals[decade, default: 0]
                )
            }
        )
    }

    private static func decade(of year: Int) -> Int {
        Int((Double(year) / 10).rounded(.down)) * 10
    }
}

enum SWStatsYearChart {
    /// Sums duplicate years and fills gaps with zero so buckets form a
    /// continuous timeline.
    static func filledPoints(from points: [SWStatsYearPoint]) -> [SWStatsYearPoint] {
        let totals = points.reduce(into: [Int: Int]()) { result, point in
            result[point.year, default: 0] += max(0, point.count)
        }
        guard let first = totals.keys.min(), let last = totals.keys.max() else { return [] }
        return (first ... last).map {
            SWStatsYearPoint(year: $0, count: totals[$0, default: 0])
        }
    }

    static func peakDecade(from points: [SWStatsYearPoint]) -> StatsDecade? {
        let totals = points.reduce(into: [Int: Int]()) { result, point in
            guard point.count > 0 else { return }
            result[point.year / 10 * 10, default: 0] += point.count
        }
        return totals
            .max { lhs, rhs in lhs.value == rhs.value ? lhs.key < rhs.key : lhs.value < rhs.value }
            .map { StatsDecade(decade: $0.key, count: $0.value) }
    }
}

// MARK: - Media mix

struct SWStatsSlice: Identifiable, Equatable {
    let id: String
    let title: String
    let value: Int
    let color: Color
}

/// Single stacked capsule, Storage-settings style.
struct StatsMixBar: View {
    let slices: [SWStatsSlice]

    @State private var isRevealed = false

    static func widths(for values: [Int], totalWidth: CGFloat, spacing: CGFloat, minimum: CGFloat) -> [CGFloat] {
        let positive = values.map { max(0, $0) }
        let total = positive.reduce(0, +)
        guard total > 0, !positive.isEmpty else { return positive.map { _ in 0 } }
        let available = max(0, totalWidth - spacing * CGFloat(positive.count - 1))
        let raw = positive.map { available * CGFloat($0) / CGFloat(total) }
        let small = raw.indices.filter { raw[$0] < minimum }
        let reserved = minimum * CGFloat(small.count)
        let largeTotal = raw.indices.filter { !small.contains($0) }.reduce(CGFloat(0)) { $0 + raw[$1] }
        let scale = largeTotal > 0 ? max(0, available - reserved) / largeTotal : 0
        return raw.indices.map { small.contains($0) ? minimum : raw[$0] * scale }
    }

    var body: some View {
        GeometryReader { proxy in
            let widths = Self.widths(
                for: slices.map(\.value),
                totalWidth: proxy.size.width,
                spacing: 3,
                minimum: 5
            )
            HStack(spacing: 3) {
                ForEach(Array(slices.enumerated()), id: \.element.id) { index, slice in
                    Rectangle()
                        .fill(slice.color)
                        .frame(width: widths[index])
                }
            }
            .frame(width: proxy.size.width, alignment: .leading)
            .scaleEffect(x: isRevealed ? 1 : 0.001, anchor: .leading)
        }
        .frame(height: 12)
        .clipShape(Capsule())
        .background(.white.opacity(0.06), in: Capsule())
        .statsReveal($isRevealed)
        .animation(.smooth(duration: 0.35), value: slices.map(\.value))
        .accessibilityHidden(true)
    }
}

// MARK: - Ranked bars

struct StatsRankedBars: View {
    let items: [StatsNamedCount]
    let tint: Color

    @State private var isRevealed = false

    private var maximum: Int {
        max(1, items.map(\.count).max() ?? 1)
    }

    var body: some View {
        VStack(spacing: 16) {
            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                let ratio = CGFloat(item.count) / CGFloat(maximum)

                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(String(index + 1))
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.white.opacity(0.32))
                            .monospacedDigit()
                            .frame(width: 16, alignment: .leading)

                        Text(item.name)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.94))
                            .lineLimit(1)

                        Spacer(minLength: 8)

                        Text(item.count.formatted())
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.5))
                            .monospacedDigit()
                    }

                    GeometryReader { proxy in
                        Capsule()
                            .fill(.white.opacity(0.06))
                            .overlay(alignment: .leading) {
                                Capsule()
                                    .fill(index == 0 ? tint : tint.opacity(0.5))
                                    .frame(width: max(6, proxy.size.width * ratio * (isRevealed ? 1 : 0)))
                                    .animation(
                                        .smooth(duration: 0.7).delay(Double(index) * 0.05),
                                        value: isRevealed
                                    )
                                    .animation(.smooth(duration: 0.35), value: ratio)
                            }
                    }
                    .frame(height: 5)
                    .padding(.leading, 24)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(index + 1). \(item.name)")
                .accessibilityValue(item.count.formatted())
            }
        }
        .statsReveal($isRevealed)
    }
}

// MARK: - Poster rail

struct StatsPosterItem: Identifiable {
    let media: MediaSummary
    var stars: Double?
    var caption: String?
    let accessibilityCaption: String

    var id: MediaSummary.ID { media.id }
}

/// Four-column poster grid matching the app's tag and likes grids, with an
/// optional count badge in the corner of each poster.
struct StatsPosterGrid: View {
    let items: [StatsPosterItem]
    /// Called as each lazily built cell appears, so callers can prefetch the next page.
    var onItemAppear: ((StatsPosterItem) -> Void)?
    let action: (MediaSummary) -> Void

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)

    var body: some View {
        LazyVGrid(columns: columns, spacing: 10) {
            ForEach(items) { item in
                Button {
                    action(item.media)
                } label: {
                    MediaArtwork(
                        url: item.media.displayPosterURL,
                        title: item.media.title,
                        slot: .tagGrid,
                        mediaType: item.media.ref.mediaType,
                        orientation: item.media.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.28), radius: 10, y: 5)
                    .overlay(alignment: .bottomTrailing) {
                        if let caption = item.caption {
                            Text(caption)
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(.white.opacity(0.95))
                                .monospacedDigit()
                                .padding(.horizontal, 6)
                                .frame(minHeight: 20)
                                .background(.black.opacity(0.62), in: Capsule())
                                .overlay {
                                    Capsule().strokeBorder(.white.opacity(0.16), lineWidth: 0.5)
                                }
                                .padding(5)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(item.media.displayTitle), \(item.accessibilityCaption)")
                .accessibilityHint("Opens media details")
                .onAppear {
                    onItemAppear?(item)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct StatsPosterRail: View {
    let items: [StatsPosterItem]
    var gutter: CGFloat = 16
    let action: (MediaSummary) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(alignment: .top, spacing: 12) {
                ForEach(items) { item in
                    Button {
                        action(item.media)
                    } label: {
                        VStack(alignment: .leading, spacing: 8) {
                            MediaArtwork(
                                url: item.media.displayPosterURL,
                                title: item.media.title,
                                slot: .carousel,
                                mediaType: item.media.ref.mediaType,
                                orientation: item.media.posterOrientation
                            )
                            .shadow(color: .black.opacity(0.28), radius: 10, y: 5)

                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.media.displayTitle)
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(.white)
                                    .lineLimit(2)
                                    .multilineTextAlignment(.leading)
                                    .fixedSize(horizontal: false, vertical: true)

                                if let stars = item.stars {
                                    StatsStarRow(stars: stars)
                                } else if let caption = item.caption {
                                    Text(caption)
                                        .font(.caption2.weight(.bold))
                                        .foregroundStyle(.white.opacity(0.54))
                                        .lineLimit(1)
                                }
                            }
                        }
                        .frame(width: PosterSlot.carousel.size.width, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(item.media.displayTitle), \(item.accessibilityCaption)")
                    .accessibilityHint("Opens media details")
                }
            }
            .scrollTargetLayout()
        }
        .contentMargins(.horizontal, gutter, for: .scrollContent)
        .scrollTargetBehavior(.viewAligned)
        .scrollClipDisabled()
        .padding(.horizontal, -gutter)
    }
}
