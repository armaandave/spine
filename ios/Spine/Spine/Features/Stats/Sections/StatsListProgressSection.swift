import SwiftUI

// List progress (STAT-213): a ring per lasting featured list with at least two seen, most
// complete first. In a year, only the lists that year moved forward, most moved first, each
// ring showing the whole progress with the year's part bright. The section shows six, then
// "Show all N", which opens the rest from the same payload (`StatsListProgressListView`). A row
// opens the list.

enum StatsListProgressData {
    /// Rings shown before "Show all".
    static let collapsedCount = 6

    /// The server's order, kept on the phone: in a year the most first seen that year first,
    /// then most complete, then most seen, then name.
    static func sorted(_ rings: [StatsListRing]) -> [StatsListRing] {
        rings.sorted { lhs, rhs in
            let left = lhs.seenInRange ?? 0
            let right = rhs.seenInRange ?? 0
            if left != right { return left > right }
            if lhs.fraction != rhs.fraction { return lhs.fraction > rhs.fraction }
            if lhs.seen != rhs.seen { return lhs.seen > rhs.seen }
            return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
        }
    }

    /// Whole percent seen. Never "100%" until every item is seen, and never "0%" once one is.
    static func percent(_ ring: StatsListRing) -> Int {
        guard ring.total > 0 else { return 0 }
        if ring.seen >= ring.total { return 100 }
        let rounded = Int((ring.fraction * 100).rounded())
        return min(max(rounded, ring.seen > 0 ? 1 : 0), 99)
    }

    /// "62% · 155 of 250"; in a year, "+12 in 2025 · 46 of 98".
    static func caption(_ ring: StatsListRing, period: StatsPeriod = .allTime) -> String {
        let seen = "\(ring.seen.formatted()) of \(ring.total.formatted())"
        guard let added = addedInPeriod(ring, period: period) else { return "\(percent(ring))% · \(seen)" }
        return "+\(added.formatted()) in \(period.title) · \(seen)"
    }

    /// "Letterboxd's Top 500 Films, 62 percent, 155 of 250 seen"; in a year,
    /// "Booker Prize Winners, 12 more in 2025, 46 of 98 seen".
    static func accessibilityLabel(_ ring: StatsListRing, period: StatsPeriod = .allTime) -> String {
        let seen = "\(ring.seen.formatted()) of \(ring.total.formatted()) seen"
        guard let added = addedInPeriod(ring, period: period) else {
            return "\(ring.name), \(percent(ring)) percent, \(seen)"
        }
        return "\(ring.name), \(added.formatted()) more in \(period.title), \(seen)"
    }

    /// The titles first seen in a year, when the server counts them.
    private static func addedInPeriod(_ ring: StatsListRing, period: StatsPeriod) -> Int? {
        guard period != .allTime else { return nil }
        return ring.seenInRange
    }
}

/// The List progress section's rows. `onShowAll` opens every ring; `onOpen` gets `.list(id:)`.
struct StatsListProgressSection: View {
    let rings: [StatsListRing]
    /// The range, for a year's captions.
    var period: StatsPeriod = .allTime
    let onOpen: (StatsDrillDown) -> Void
    var onShowAll: (() -> Void)?

    var body: some View {
        let sorted = StatsListProgressData.sorted(rings)
        let hasMore = onShowAll != nil && sorted.count > StatsListProgressData.collapsedCount
        let visible = hasMore ? Array(sorted.prefix(StatsListProgressData.collapsedCount)) : sorted

        if !visible.isEmpty {
            StatsSectionTitle("List progress")

            ForEach(Array(visible.enumerated()), id: \.element.id) { index, ring in
                StatsListRingRow(
                    ring: ring,
                    period: period,
                    isFirst: index == 0,
                    isLast: index == visible.count - 1 && !hasMore
                ) {
                    onOpen(.list(id: ring.id))
                }
            }

            if hasMore, let onShowAll {
                StatsShowAllRow(title: "Show all \(sorted.count.formatted())", action: onShowAll)
            }

            StatsSectionEnd()
        }
    }
}

/// One ring row: the ring, the list's name, and "62% · 155 of 250".
struct StatsListRingRow: View {
    let ring: StatsListRing
    var period: StatsPeriod = .allTime
    let isFirst: Bool
    let isLast: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 0) {
                HStack(alignment: .center, spacing: 14) {
                    StatsProgressRing(
                        fraction: ring.fraction,
                        dimmedFraction: period == .allTime ? ring.fraction : ring.fractionBeforeRange,
                        tint: StatsChartStyle.color(for: ring.mediaType)
                    )
                    .frame(width: 44, height: 44)

                    VStack(alignment: .leading, spacing: 3) {
                        Text(ring.name)
                            .font(.body)
                            .foregroundStyle(.white)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(StatsListProgressData.caption(ring, period: period))
                            .font(.subheadline)
                            .monospacedDigit()
                            .foregroundStyle(StatsChartStyle.secondaryText)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.35))
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                if !isLast {
                    DiarySeparator()
                        .padding(.leading, 16 + 44 + 14)
                }
            }
            .contentShape(Rectangle())
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(StatsListProgressData.accessibilityLabel(ring, period: period))
        }
        .buttonStyle(DiaryRowButtonStyle())
        .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
    }
}

/// A progress ring like the system's circular capacity gauge: a faint track, the seen share in
/// the tint, and in a year the part seen before it dimmed, so the year's part stands out.
struct StatsProgressRing: View {
    /// Seen share, 0 to 1.
    let fraction: Double
    /// Where the bright part starts: the share seen before the year. Equal to `fraction`, the
    /// whole arc is bright.
    let dimmedFraction: Double
    let tint: Color
    var lineWidth: CGFloat = 4.5

    var body: some View {
        let isSplit = dimmedFraction < fraction
        ZStack {
            Circle()
                .stroke(.white.opacity(0.14), lineWidth: lineWidth)
            if isSplit {
                arc(from: 0, to: dimmedFraction)
                    .stroke(tint.opacity(0.4), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                arc(from: dimmedFraction, to: fraction)
                    .stroke(tint, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
            } else {
                arc(from: 0, to: fraction)
                    .stroke(tint, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
            }
        }
        .rotationEffect(.degrees(-90))
        .padding(lineWidth / 2 + 4)
        .accessibilityHidden(true)
    }

    private func arc(from start: Double, to end: Double) -> some Shape {
        Circle().trim(from: min(max(start, 0), 1), to: min(max(end, 0), 1))
    }
}
