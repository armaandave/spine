import Accessibility
import Charts
import SwiftUI

// Shared pieces for the Stats chart and list sections: colours, copy, card slices, the stacked
// type bar, the chart tap overlay and the accessibility descriptor. Type colours
// (`MediaTypeTheme.statsColor`) appear only on chart marks.

// MARK: - Style

enum StatsChartStyle {
    /// Marks with no type: Global charts that carry no per-type split.
    static let neutral = Color.white.opacity(0.82)
    static let axisLabel = Color.white.opacity(0.5)
    static let gridLine = Color.white.opacity(0.08)
    static let track = Color.white.opacity(0.08)
    static let secondaryText = Color.white.opacity(0.55)
    /// Space after a section's last row, as the other Stats sections leave.
    static var sectionSpacing: CGFloat { StatsSectionLayout.sectionGap }
    /// Space between two cards of one section.
    static var cardSpacing: CGFloat { StatsSectionLayout.cardGap }

    /// The mark colour of a type, or the neutral colour on Global.
    static func color(for mediaType: String?) -> Color {
        guard let mediaType else { return neutral }
        return MediaTypeTheme.theme(for: mediaType).statsColor
    }

    /// The type's name for legends and labels: "Movies", "TV".
    static func typeName(_ mediaType: String) -> String {
        MediaTypeTheme.theme(for: mediaType).displayName
    }

    /// The order types stack and list in: the app's primary type order, unknown types after.
    static let typeOrder = APIConstants.fallbackMediaTypes

    /// A `by_type` object as ordered parts, leaving out types with nothing.
    static func orderedTypes(_ byType: [String: Int]) -> [StatsTypeCount] {
        byType
            .filter { $0.value > 0 }
            .map { StatsTypeCount(mediaType: $0.key, count: $0.value) }
            .sorted { lhs, rhs in
                let left = typeOrder.firstIndex(of: lhs.mediaType) ?? Int.max
                let right = typeOrder.firstIndex(of: rhs.mediaType) ?? Int.max
                return left == right ? lhs.mediaType < rhs.mediaType : left < right
            }
    }

    /// Every type that appears in any of the `by_type` objects, in type order.
    static func types(in byTypes: [[String: Int]]) -> [String] {
        var totals: [String: Int] = [:]
        for byType in byTypes {
            for (type, count) in byType where count > 0 {
                totals[type, default: 0] += count
            }
        }
        return orderedTypes(totals).map(\.mediaType)
    }

    /// Whole-number axis ticks from 0 that cover `maximum` in about three steps of 1, 2 or 5
    /// times a power of ten, so a count axis never shows "0.5".
    static func countTicks(maximum: Int, desiredSteps: Int = 3) -> [Int] {
        guard maximum > 0 else { return [0, 1] }
        let raw = Double(maximum) / Double(max(desiredSteps, 1))
        let magnitude = pow(10, floor(log10(max(raw, 1))))
        let step = max(
            [1.0, 2.0, 5.0, 10.0].map { Int($0 * magnitude) }.first { Double($0) >= raw } ?? Int(10 * magnitude),
            1
        )
        let top = Int((Double(maximum) / Double(step)).rounded(.up)) * step
        return Array(stride(from: 0, through: top, by: step))
    }
}

struct StatsTypeCount: Hashable, Identifiable {
    let mediaType: String
    let count: Int

    var id: String { mediaType }
}

// MARK: - Copy

enum StatsChartCopy {
    /// "1 title", "12 titles".
    static func count(_ value: Int, _ singular: String, _ plural: String) -> String {
        "\(value.formatted()) \(value == 1 ? singular : plural)"
    }

    /// The plural noun of a type inside a sentence: "movies", "TV", "books".
    static func typeNoun(_ mediaType: String) -> String {
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

    /// "a", "a and b", "a, b and c".
    static func joined(_ words: [String]) -> String {
        switch words.count {
        case 0: ""
        case 1: words[0]
        default: words.dropLast().joined(separator: ", ") + " and " + words[words.count - 1]
        }
    }

    /// "From movies and TV", for a card counted from only some types.
    static func typesNote(_ mediaTypes: [String]) -> String? {
        guard !mediaTypes.isEmpty else { return nil }
        return "From " + joined(mediaTypes.map { typeNoun($0) })
    }

    /// "Based on 110 of 120 titles with a release year", only when some titles are missing.
    static func coverageNote(_ coverage: StatsCoverage?, qualifier: String) -> String? {
        guard let coverage, coverage.total > 0, coverage.known < coverage.total else { return nil }
        let base = "Based on \(coverage.known.formatted()) of \(coverage.total.formatted()) \(coverage.total == 1 ? "title" : "titles")"
        return qualifier.isEmpty ? base : "\(base) \(qualifier)"
    }

    /// Footnote lines joined into one line.
    static func footnote(_ parts: [String?]) -> String? {
        let parts = parts.compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// A star count spoken: "½ star", "3 stars", "3½ stars".
    static func spokenStars(_ value: Double) -> String {
        let halves = Int((value * 2).rounded())
        let whole = halves / 2
        let half = halves % 2 == 1
        let text = whole == 0 && half ? "½" : "\(whole)\(half ? "½" : "")"
        return "\(text) \(halves == 1 || halves == 2 ? "star" : "stars")"
    }
}

// MARK: - Section pieces

/// The empty row that ends a chart section.
struct StatsSectionEnd: View {
    var body: some View {
        Color.clear
            .frame(height: StatsChartStyle.sectionSpacing)
            .accessibilityHidden(true)
    }
}

/// A quiet line under a card.
struct StatsChartFootnote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(StatsChartStyle.secondaryText)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 4)
            .padding(.top, 8)
    }
}

/// The last slice of a card: "Show all 14" with a chevron.
struct StatsShowAllRow: View {
    let title: String
    var roundsTop = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.body)
                    .foregroundStyle(.white)
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.35))
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(DiaryRowButtonStyle())
        .diaryCard(roundsTop: roundsTop, roundsBottom: true)
    }
}

extension View {
    /// A whole card in one row: padding, full width, the diary's glassy fill.
    func statsChartCard() -> some View {
        padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .diaryCard()
    }

    /// Count axes: whole-number ticks on the leading edge, quiet grid lines.
    func statsCountYAxis(ticks: [Int]) -> some View {
        chartYScale(domain: 0 ... Double(max(ticks.last ?? 1, 1)))
            .chartYAxis {
                AxisMarks(position: .leading, values: ticks.map(Double.init)) { value in
                    AxisGridLine()
                        .foregroundStyle(StatsChartStyle.gridLine)
                    AxisValueLabel {
                        if let number = value.as(Double.self) {
                            Text(Int(number).formatted())
                                .foregroundStyle(StatsChartStyle.axisLabel)
                        }
                    }
                }
            }
    }
}

// MARK: - Legend and bars

/// Coloured dots naming the types in a stacked chart.
struct StatsTypeLegend: View {
    let mediaTypes: [String]

    var body: some View {
        FlowLayout(spacing: 12) {
            ForEach(mediaTypes, id: \.self) { type in
                HStack(spacing: 5) {
                    Circle()
                        .fill(StatsChartStyle.color(for: type))
                        .frame(width: 7, height: 7)
                    Text(StatsChartStyle.typeName(type))
                        .font(.caption)
                        .foregroundStyle(StatsChartStyle.secondaryText)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// A thin horizontal bar `fraction` of the row wide, split into its types' colours.
struct StatsStackedBar: View {
    let parts: [StatsTypeCount]
    let fraction: Double
    /// The colour when there's no per-type split.
    var tint: Color = StatsChartStyle.neutral
    var height: CGFloat = 6

    /// Each part's width: `fraction` of `totalWidth`, shared by count. A bar with no parts is
    /// one part of the whole bar.
    static func widths(for counts: [Int], fraction: Double, totalWidth: CGFloat) -> [CGFloat] {
        let barWidth = totalWidth * CGFloat(min(max(fraction, 0), 1))
        let positive = counts.map { max($0, 0) }
        let total = positive.reduce(0, +)
        guard total > 0 else { return positive.map { _ in 0 } }
        return positive.map { barWidth * CGFloat($0) / CGFloat(total) }
    }

    var body: some View {
        GeometryReader { proxy in
            let counts = parts.isEmpty ? [1] : parts.map(\.count)
            let widths = Self.widths(for: counts, fraction: fraction, totalWidth: proxy.size.width)
            HStack(spacing: 0) {
                if parts.isEmpty {
                    Rectangle().fill(tint).frame(width: widths.first ?? 0)
                } else {
                    ForEach(Array(parts.enumerated()), id: \.element.id) { index, part in
                        Rectangle()
                            .fill(StatsChartStyle.color(for: part.mediaType))
                            .frame(width: widths[index])
                    }
                }
            }
            .clipShape(Capsule())
            .frame(width: proxy.size.width, alignment: .leading)
        }
        .frame(height: height)
        .background(StatsChartStyle.track, in: Capsule())
        .accessibilityHidden(true)
    }
}

// MARK: - Chart taps

/// A clear layer over a chart that reports taps in the plot's own coordinates.
struct StatsChartTapOverlay: View {
    let proxy: ChartProxy
    let onTap: (CGPoint) -> Void

    var body: some View {
        GeometryReader { geometry in
            Rectangle()
                .fill(.clear)
                .contentShape(Rectangle())
                .onTapGesture { location in
                    guard let plotFrame = proxy.plotFrame else { return }
                    let origin = geometry[plotFrame].origin
                    onTap(CGPoint(x: location.x - origin.x, y: location.y - origin.y))
                }
        }
    }
}

/// Which stacked part a tap at `value` on the count axis lands in: parts stack upwards in
/// order. Nil above the bar.
func statsStackedPart(at value: Double, parts: [StatsTypeCount]) -> String? {
    guard value >= 0 else { return parts.first?.mediaType }
    var top = 0.0
    for part in parts {
        top += Double(part.count)
        if value <= top { return part.mediaType }
    }
    return nil
}

// MARK: - Accessibility

/// A bar chart read out as categories with counts.
struct StatsChartDescriptor: AXChartDescriptorRepresentable {
    let title: String
    let summary: String?
    let categoryTitle: String
    let valueTitle: String
    let points: [(label: String, value: Double)]

    func makeChartDescriptor() -> AXChartDescriptor {
        let maximum = max(1, points.map(\.value).max() ?? 1)
        let xAxis = AXCategoricalDataAxisDescriptor(
            title: categoryTitle,
            categoryOrder: points.map(\.label)
        )
        let yAxis = AXNumericDataAxisDescriptor(
            title: valueTitle,
            range: 0 ... maximum,
            gridlinePositions: []
        ) { value in
            value.rounded() == value ? Int(value).formatted() : String(format: "%.2g", value)
        }
        let series = AXDataSeriesDescriptor(
            name: title,
            isContinuous: false,
            dataPoints: points.map { AXDataPoint(x: $0.label, y: $0.value) }
        )
        return AXChartDescriptor(
            title: title,
            summary: summary,
            xAxis: xAxis,
            yAxis: yAxis,
            additionalAxes: [],
            series: [series]
        )
    }
}
