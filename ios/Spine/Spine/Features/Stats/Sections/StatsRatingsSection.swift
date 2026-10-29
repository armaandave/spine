import Charts
import SwiftUI

// Ratings (STAT-204): rated logs in ten half-star buckets, stacked by type on Global, with the
// average in stars. Dragging picks a bar; "Show logs" opens the diary between the bucket's
// stored bounds.

/// The Ratings section's rows. `onDrillDown` is nil on someone else's stats. A picked bar opens
/// the logs between its bucket's stored bounds; on Global it can open one type's.
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

    /// The axis labels: the first and last bucket, "½★" and "5★".
    static func axisLabels(_ buckets: [StatsRatingsBucket]) -> [String] {
        StatsChartAxis.edgeLabels(buckets.map(\.stars.text))
    }

    /// A bucket's label under the axis: "½★", "3★", "3½★".
    static func axisLabel(forStars text: String) -> String? {
        guard let value = StatsDecimal.value(text), value > 0 else { return nil }
        let halves = Int((value * 2).rounded())
        let whole = halves / 2
        let half = halves % 2 == 1 ? "½" : ""
        return whole == 0 ? "\(half)★" : "\(whole)\(half)★"
    }

    /// A picked bucket's readout: "3½ stars", "37 ratings · 30 movies, 7 TV", and its logs, each
    /// type's too on Global.
    static func selection(for bucket: StatsRatingsBucket, stacked: Bool) -> StatsChartSelection {
        let count = StatsChartCopy.count(bucket.count, "rating", "ratings")
        let types = stacked ? StatsChartStyle.orderedTypes(bucket.byType) : []
        let detail = types.count > 1
            ? "\(count) · " + types.map { "\($0.count.formatted()) \(StatsChartCopy.typeNoun($0.mediaType))" }.joined(separator: ", ")
            : count
        let parts = types.map { (mediaType: $0.mediaType, drillDown: StatsDrillDown.rating(bucket, mediaType: $0.mediaType)) }
        return StatsChartSelection(
            title: StatsChartCopy.spokenStars(bucket.stars.value),
            detail: detail,
            drillOptions: bucket.count > 0 ? StatsChartDrillOption.options(whole: .rating(bucket), parts: parts) : []
        )
    }

    /// "3.65 average · 140 ratings".
    static func summary(_ ratings: StatsRatings) -> String {
        let count = StatsChartCopy.count(ratings.rated, "rating", "ratings")
        guard let average = ratings.averageStars else { return count }
        return "\(average.averageText) average · \(count)"
    }
}

// MARK: - Chart

private struct StatsRatingsChartCard: View {
    let ratings: StatsRatings
    let mediaType: String?
    let onDrillDown: ((StatsDrillDown) -> Void)?

    /// The bucket under the finger while scrubbing, by its stars ("3.5").
    @State private var scrubbed: String?
    /// The last picked bucket, kept after the finger lifts.
    @State private var picked: String?

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
        let pickedBucket = buckets.first { $0.stars.text == (scrubbed ?? picked) }

        VStack(alignment: .leading, spacing: 14) {
            StatsChartReadout(
                selection: pickedBucket.map { StatsRatingsChartData.selection(for: $0, stacked: isStacked) },
                onDrillDown: onDrillDown
            ) {
                VStack(alignment: .leading, spacing: 2) {
                    headline
                    Text("Drag across the chart to see each rating.")
                        .font(.footnote)
                        .foregroundStyle(StatsChartStyle.secondaryText)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
            }

            Chart(parts) { part in
                BarMark(
                    x: .value("Stars", part.stars),
                    y: .value("Ratings", part.count),
                    width: .ratio(0.7)
                )
                .foregroundStyle(by: .value("Type", part.seriesName))
                .opacity(pickedBucket == nil || pickedBucket?.stars.text == part.stars ? 1 : 0.35)
            }
            .chartForegroundStyleScale(domain: domain, range: range)
            .chartLegend(.hidden)
            .chartXScale(domain: buckets.map(\.stars.text))
            .statsEdgeXAxis(labels: StatsRatingsChartData.axisLabels(buckets)) { stars in
                StatsRatingsChartData.axisLabel(forStars: stars) ?? stars
            }
            .statsCountYAxis(ticks: ticks)
            .chartXSelection(value: $scrubbed)
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
        .sensoryFeedback(.selection, trigger: scrubbed) { _, new in new != nil }
        .onChange(of: scrubbed) { _, new in
            if let new { picked = new }
        }
    }

    private var headline: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            if let average = ratings.averageStars {
                Text(average.averageText)
                    .font(.title3.bold())
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
}
