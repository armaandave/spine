import SwiftUI

// Collections (STAT-214): complete and almost complete series on the movie, game, book and
// anime tabs. Four tiles of each, a tile being three posters with "N of M" under the name, then
// "Show all" for a group with more (`StatsCollectionsListView`, the collections endpoint). A
// tile opens the series page. Each tile row is its own row of the page's lazy stack.

extension StatsCollectionGroup {
    /// "Complete", "Almost complete".
    var displayTitle: String {
        switch self {
        case .complete: "Complete"
        case .almost: "Almost complete"
        }
    }
}

/// One group of the section: its count from the payload and the first rows it sent.
struct StatsCollectionsGroupRows: Identifiable, Hashable {
    let group: StatsCollectionGroup
    let count: Int
    let series: [MediaSeriesSummary]

    var id: StatsCollectionGroup { group }
}

enum StatsCollectionsData {
    /// "Complete (33)".
    static func groupTitle(_ group: StatsCollectionGroup, count: Int) -> String {
        "\(group.displayTitle) (\(count.formatted()))"
    }

    /// "3 of 3": seen of released (STAT-214). Nil when the series has no counts.
    static func caption(_ series: MediaSeriesSummary) -> String? {
        guard let completion = series.completion, completion.totalCount > 0 else { return nil }
        return "\(completion.normalizedCompletedCount.formatted()) of \(completion.totalCount.formatted())"
    }

    /// The groups with something in them, with their counts and first rows.
    static func groups(_ collections: StatsCollections) -> [StatsCollectionsGroupRows] {
        [
            StatsCollectionsGroupRows(group: .complete, count: collections.completeCount, series: collections.complete),
            StatsCollectionsGroupRows(group: .almost, count: collections.almostCount, series: collections.almost),
        ]
        .filter { $0.count > 0 && !$0.series.isEmpty }
    }

    /// Whether a group has more than the section shows.
    static func hasMore(count: Int, shown: Int) -> Bool {
        count > shown
    }

    /// "The Lord of the Rings Collection, 3 of 3 seen".
    static func accessibilityLabel(_ series: MediaSeriesSummary) -> String {
        guard let caption = caption(series) else { return series.name }
        return "\(series.name), \(caption) seen"
    }
}

/// The Collections section's rows. `onOpen` gets `.collection(series)`; `onShowAll` opens a
/// group's full list.
struct StatsCollectionsSection: View {
    let collections: StatsCollections
    let onOpen: (StatsDrillDown) -> Void
    var onShowAll: ((StatsCollectionGroup) -> Void)?

    var body: some View {
        let groups = StatsCollectionsData.groups(collections)

        if !groups.isEmpty {
            StatsSectionTitle("Collections")

            ForEach(groups) { entry in
                HStack(alignment: .center, spacing: 12) {
                    Text(StatsCollectionsData.groupTitle(entry.group, count: entry.count))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(StatsChartStyle.secondaryText)
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 8)
                    if let onShowAll, StatsCollectionsData.hasMore(count: entry.count, shown: entry.series.count) {
                        StatsShowAllButton {
                            onShowAll(entry.group)
                        }
                    } else {
                        Color.clear.frame(width: 1, height: 44)
                    }
                }
                .padding(.leading, 4)

                ForEach(StatsPosterGridLayout.rows(entry.series, idPrefix: "collections.\(entry.group.rawValue)")) { row in
                    StatsPosterGridRowView(row: row) { series in
                        StatsCollectionTile(series: series) {
                            onOpen(.collection(series))
                        }
                    }
                    .padding(.bottom, StatsPosterGridLayout.rowSpacing)
                }
            }

            Color.clear
                .frame(height: max(StatsChartStyle.sectionSpacing - StatsPosterGridLayout.rowSpacing, 0))
                .accessibilityHidden(true)
        }
    }
}

/// A series tile: up to three posters fanned in a fixed cell, the name, then "N of M".
struct StatsCollectionTile: View {
    let series: MediaSeriesSummary
    let action: () -> Void

    private static var cell: CGSize { StatsPosterGridLayout.cellSize }
    private static let posterSlot: PosterSlot = .hofCrown

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                fan
                    .frame(width: Self.cell.width, height: Self.cell.height, alignment: .bottom)

                Text(series.name)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.88))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(width: Self.cell.width, height: 30, alignment: .topLeading)

                if let caption = StatsCollectionsData.caption(series) {
                    StatsPosterCaption(text: caption)
                        .frame(width: Self.cell.width, alignment: .leading)
                }
            }
            .contentShape(Rectangle())
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(StatsCollectionsData.accessibilityLabel(series))
        }
        .buttonStyle(.plain)
    }

    private var fan: some View {
        let posters = Self.posters(series)
        return ZStack(alignment: .bottom) {
            ForEach(Array(posters.enumerated()), id: \.offset) { index, url in
                MediaArtwork(
                    url: url,
                    title: series.name,
                    slot: Self.posterSlot,
                    mediaType: series.mediaType
                )
                .scaleEffect(posters.count > 1 && index != 0 ? 0.88 : 1, anchor: .bottom)
                .offset(x: Self.offset(index: index, count: posters.count))
                .zIndex(Double(posters.count - index))
            }
        }
        .compositingGroup()
        .shadow(color: .black.opacity(0.3), radius: 6, y: 3)
    }

    /// The first three posters, or one placeholder when there are none.
    static func posters(_ series: MediaSeriesSummary) -> [String?] {
        let urls = series.posterUrls.prefix(3).map { Optional($0) }
        return urls.isEmpty ? [nil] : Array(urls)
    }

    /// The front poster in the middle, the second behind on the left, the third on the right.
    private static func offset(index: Int, count: Int) -> CGFloat {
        guard count > 1 else { return 0 }
        switch index {
        case 1: return -9
        case 2: return 9
        default: return 0
        }
    }
}
