import SwiftUI

// List progress (STAT-213): a ring per lasting featured list with at least two seen, most
// complete first. The section shows six, then "Show all N", which opens the rest from the same
// payload (`StatsListProgressListView`). A row opens the list.

enum StatsListProgressData {
    /// Rings shown before "Show all".
    static let collapsedCount = 6

    /// Most complete first, then most seen, then name: the server's order, kept on the phone.
    static func sorted(_ rings: [StatsListRing]) -> [StatsListRing] {
        rings.sorted { lhs, rhs in
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

    /// "62% · 155 of 250".
    static func caption(_ ring: StatsListRing) -> String {
        "\(percent(ring))% · \(ring.seen.formatted()) of \(ring.total.formatted())"
    }

    /// "Letterboxd's Top 500 Films, 62 percent, 155 of 250 seen".
    static func accessibilityLabel(_ ring: StatsListRing) -> String {
        "\(ring.name), \(percent(ring)) percent, \(ring.seen.formatted()) of \(ring.total.formatted()) seen"
    }
}

/// The List progress section's rows. `onShowAll` opens every ring; `onOpen` gets `.list(id:)`.
struct StatsListProgressSection: View {
    let rings: [StatsListRing]
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
    let isFirst: Bool
    let isLast: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 0) {
                HStack(alignment: .center, spacing: 14) {
                    Gauge(value: ring.fraction) {
                        EmptyView()
                    }
                    .gaugeStyle(.accessoryCircularCapacity)
                    .tint(StatsChartStyle.color(for: ring.mediaType))
                    .scaleEffect(0.72)
                    .frame(width: 44, height: 44)

                    VStack(alignment: .leading, spacing: 3) {
                        Text(ring.name)
                            .font(.body)
                            .foregroundStyle(.white)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(StatsListProgressData.caption(ring))
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
            .accessibilityLabel(StatsListProgressData.accessibilityLabel(ring))
        }
        .buttonStyle(DiaryRowButtonStyle())
        .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
    }
}
