import Charts
import SwiftUI

// Ratings (STAT-204): rated logs in ten half-star buckets, stacked by type on Global, with the
// average in stars. Tapping a bar opens the diary between the bucket's stored bounds.

/// The Ratings section's rows. `onDrillDown` is nil on someone else's stats. A bar opens the
/// logs between its bucket's stored bounds; on Global, a tap on one type's part opens only that
/// type's.
struct StatsRatingsChartSection: View {
    let ratings: StatsRatings
    /// The tab: nil on Global, where bars stack by type.
    let mediaType: String?
    var onDrillDown: ((StatsDrillDown) -> Void)?

    var body: some View {
        StatsSectionTitle("Ratings")
        StatsRatingsChartCard(ratings: ratings, mediaType: mediaType, onDrillDown: onDrillDown)
        StatsSectionEnd()
    }
}

// MARK: - Data

/// One stacked part of a bucket's bar.
struct StatsRatingsBarPart: Identifiable, Hashable {
    /// The bucket's stars, "3.5".
    let stars: String
    let mediaType: String?
    let count: Int

    var id: String { "\(stars).\(mediaType ?? "all")" }
    /// The chart series: the type, or "all" for an unsplit bar.
    var seriesName: String { mediaType ?? "all" }
}

enum StatsRatingsChartData {
    /// The ten buckets and their inclusive stored bounds (STAT-204).
    static let table: [(stars: String, min: String, max: String)] = [
        ("0.5", "0.0", "1.4"),
        ("1.0", "1.5", "2.4"),
        ("1.5", "2.5", "3.4"),
        ("2.0", "3.5", "4.4"),
        ("2.5", "4.5", "5.4"),
        ("3.0", "5.5", "6.4"),
        ("3.5", "6.5", "7.4"),
        ("4.0", "7.5", "8.4"),
        ("4.5", "8.5", "9.4"),
        ("5.0", "9.5", "10.0"),
    ]

    /// Exactly ten buckets from 0.5★ to 5★. Each sent bucket goes in the slot of its stars;
    /// a slot the server didn't send is empty, with the contract's bounds.
    static func buckets(_ distribution: [StatsRatingsBucket]) -> [StatsRatingsBucket] {
        var bySlot: [Int: StatsRatingsBucket] = [:]
        for bucket in distribution {
            let slot = Int((bucket.stars.value * 2).rounded())
            guard (1 ... 10).contains(slot), bySlot[slot] == nil else { continue }
            bySlot[slot] = bucket
        }
        return table.enumerated().map { index, row in
            bySlot[index + 1] ?? StatsRatingsBucket(
                stars: StatsDecimal(row.stars)!,
                count: 0,
                ratingMin: StatsDecimal(row.min)!,
                ratingMax: StatsDecimal(row.max)!,
                byType: [:]
            )
        }
    }

    /// The bars' parts: by type when `stacked` and the bucket has a split, else one part.
    static func parts(_ buckets: [StatsRatingsBucket], stacked: Bool) -> [StatsRatingsBarPart] {
        buckets.flatMap { bucket -> [StatsRatingsBarPart] in
            guard bucket.count > 0 else { return [] }
            let types = StatsChartStyle.orderedTypes(bucket.byType)
            if stacked, !types.isEmpty {
                return types.map { StatsRatingsBarPart(stars: bucket.stars.text, mediaType: $0.mediaType, count: $0.count) }
            }
            return [StatsRatingsBarPart(stars: bucket.stars.text, mediaType: nil, count: bucket.count)]
        }
    }

    /// The axis label under a bucket: whole stars only ("1★" … "5★").
    static func axisLabel(forStars text: String) -> String? {
        guard let value = StatsDecimal.value(text), value.rounded() == value, value >= 1 else { return nil }
        return "\(Int(value))★"
    }

    /// "3.65 average · 140 ratings".
    static func summary(_ ratings: StatsRatings) -> String {
        let count = StatsChartCopy.count(ratings.rated, "rating", "ratings")
        guard let average = ratings.averageStars else { return count }
        return "\(average.text) average · \(count)"
    }
}

// MARK: - Chart

private struct StatsRatingsChartCard: View {
    let ratings: StatsRatings
    let mediaType: String?
    let onDrillDown: ((StatsDrillDown) -> Void)?

    private var isStacked: Bool { mediaType == nil }

    var body: some View {
        let buckets = StatsRatingsChartData.buckets(ratings.distribution)
        let parts = StatsRatingsChartData.parts(buckets, stacked: isStacked)
        let legendTypes = StatsChartStyle.types(in: buckets.map(\.byType))
        let seriesNames = Set(parts.map(\.seriesName))
        let domain = (legendTypes + ["all"]).filter(seriesNames.contains)
        let range = domain.map { name in
            StatsChartStyle.color(for: name == "all" ? mediaType : name)
        }
        let ticks = StatsChartStyle.countTicks(maximum: buckets.map(\.count).max() ?? 0)

        VStack(alignment: .leading, spacing: 14) {
            headline

            Chart(parts) { part in
                BarMark(
                    x: .value("Stars", part.stars),
                    y: .value("Ratings", part.count),
                    width: .ratio(0.7)
                )
                .foregroundStyle(by: .value("Type", part.seriesName))
            }
            .chartForegroundStyleScale(domain: domain, range: range)
            .chartLegend(.hidden)
            .chartXScale(domain: buckets.map(\.stars.text))
            .chartXAxis {
                AxisMarks(values: buckets.map(\.stars.text)) { value in
                    AxisValueLabel(centered: true) {
                        if let text = value.as(String.self), let label = StatsRatingsChartData.axisLabel(forStars: text) {
                            Text(label)
                                .foregroundStyle(StatsChartStyle.axisLabel)
                        }
                    }
                }
            }
            .statsCountYAxis(ticks: ticks)
            .chartOverlay { proxy in
                if onDrillDown != nil {
                    StatsChartTapOverlay(proxy: proxy) { location in
                        handleTap(at: location, proxy: proxy, buckets: buckets)
                    }
                }
            }
            .frame(height: 160)
            .accessibilityLabel("Ratings")
            .accessibilityValue(StatsRatingsChartData.summary(ratings))
            .accessibilityChartDescriptor(
                StatsChartDescriptor(
                    title: "Ratings",
                    summary: StatsRatingsChartData.summary(ratings),
                    categoryTitle: "Stars",
                    valueTitle: "Ratings",
                    points: buckets.map { (StatsChartCopy.spokenStars($0.stars.value), Double($0.count)) }
                )
            )
            .accessibilityActions {
                if let onDrillDown {
                    ForEach(buckets.filter { $0.count > 0 }) { bucket in
                        Button("Show \(StatsChartCopy.spokenStars(bucket.stars.value)) logs") {
                            onDrillDown(.rating(bucket))
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

    private var headline: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            if let average = ratings.averageStars {
                Text(average.text)
                    .font(.title2.bold())
                    .foregroundStyle(.white)
                    .monospacedDigit()
                Image(systemName: "star.fill")
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(LogComposerStyle.starGold)
                    .accessibilityHidden(true)
                Text("average")
                    .font(.subheadline)
                    .foregroundStyle(StatsChartStyle.secondaryText)
            }
            Spacer(minLength: 8)
            Text(StatsChartCopy.count(ratings.rated, "rating", "ratings"))
                .font(.subheadline)
                .foregroundStyle(StatsChartStyle.secondaryText)
                .monospacedDigit()
        }
        .accessibilityElement(children: .combine)
    }

    private func handleTap(at location: CGPoint, proxy: ChartProxy, buckets: [StatsRatingsBucket]) {
        guard let onDrillDown, let stars: String = proxy.value(atX: location.x),
              let bucket = buckets.first(where: { $0.stars.text == stars }), bucket.count > 0 else { return }
        var tappedType: String?
        if isStacked, let value: Double = proxy.value(atY: location.y) {
            let types = StatsChartStyle.orderedTypes(bucket.byType)
            tappedType = statsStackedPart(at: value, parts: types)
        }
        onDrillDown(.rating(bucket, mediaType: tappedType))
    }
}
