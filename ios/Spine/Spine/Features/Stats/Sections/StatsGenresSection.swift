import SwiftUI

// Genres (STAT-210, STAT-211): horizontal bars, stacked by type colour on Global, with a
// "Most watched / Highest rated" control and "Show all". On Global, music genres get their own
// card. Each bar is its own row of the page's lazy stack. Languages (STAT-212) reuse the same
// rows (`StatsRankedBarsRows`).

/// The two orders of a genre or language card.
enum StatsRankSort: String, CaseIterable, Identifiable, Hashable {
    case mostLogged
    case highestRated

    var id: String { rawValue }

    /// "Most watched" for movies, TV and anime, "Most read" for books, manga and comics,
    /// "Most played" for games, "Most listened" for music, "Most titles" on Global, where the
    /// rows are ranked by titles across every type.
    static func mostTitle(for mediaType: String?) -> String {
        switch mediaType {
        case "movie", "tv", "anime": "Most watched"
        case "book", "manga", "comic": "Most read"
        case "game": "Most played"
        case "music": "Most listened"
        default: "Most titles"
        }
    }

    /// The control's label, with `mostTitle` for the first order.
    func title(mostTitle: String) -> String {
        self == .mostLogged ? mostTitle : "Highest rated"
    }
}

/// One bar of a genre or language card.
struct StatsRankedRow: Identifiable, Hashable {
    let id: String
    let name: String
    let titles: Int
    let rated: Int
    let averageStars: StatsDecimal?
    let byType: [String: Int]
    let drillDown: StatsDrillDown
}

enum StatsRankedRowsData {
    /// Rows shown before "Show all".
    static let collapsedCount = 8
    /// "Highest rated" ranks only rows with at least this many rated logs (STAT-210).
    static let minimumRated = 3

    static func rows(_ genres: StatsGenres) -> [StatsRankedRow] {
        genres.rows.map { row in
            StatsRankedRow(
                id: "genre.\(row.id)",
                name: row.name,
                titles: row.titles,
                rated: row.rated,
                averageStars: row.averageStars,
                byType: row.byType,
                drillDown: .forGenre(row)
            )
        }
    }

    static func rows(_ languages: StatsLanguages) -> [StatsRankedRow] {
        languages.rows.map { row in
            StatsRankedRow(
                id: "language.\(row.id)",
                name: row.name,
                titles: row.titles,
                rated: row.rated,
                averageStars: row.averageStars,
                byType: row.byType,
                drillDown: .language(name: row.name)
            )
        }
    }

    /// Global's music genres (STAT-211), shown with their capitals; each opens that genre's
    /// music logs.
    static func rows(music: [StatsMusicGenre]) -> [StatsRankedRow] {
        music.map { genre in
            StatsRankedRow(
                id: "music.\(genre.id)",
                name: genre.name.capitalized(with: .current),
                titles: genre.titles,
                rated: 0,
                averageStars: nil,
                byType: ["music": genre.titles],
                drillDown: .genre(name: genre.name.capitalized(with: .current), values: genre.values ?? [genre.name], mediaType: "music")
            )
        }
    }

    /// Most logged: by titles, most first, then name (the server's order). Highest rated: only
    /// rows with at least 3 rated logs, by average stars, then titles, then name.
    static func sorted(_ rows: [StatsRankedRow], by sort: StatsRankSort) -> [StatsRankedRow] {
        switch sort {
        case .mostLogged:
            return rows.sorted { lhs, rhs in
                lhs.titles != rhs.titles ? lhs.titles > rhs.titles : lhs.name < rhs.name
            }
        case .highestRated:
            return rows
                .filter { $0.rated >= minimumRated && $0.averageStars != nil }
                .sorted { lhs, rhs in
                    let left = lhs.averageStars?.value ?? 0
                    let right = rhs.averageStars?.value ?? 0
                    if left != right { return left > right }
                    if lhs.titles != rhs.titles { return lhs.titles > rhs.titles }
                    return lhs.name < rhs.name
                }
        }
    }

    /// Whether the Highest rated order has anything to rank.
    static func canRankByRating(_ rows: [StatsRankedRow]) -> Bool {
        rows.contains { $0.rated >= minimumRated && $0.averageStars != nil }
    }

    /// The rows on screen: the first eight until "Show all".
    static func visible(_ rows: [StatsRankedRow], showsAll: Bool) -> [StatsRankedRow] {
        showsAll ? rows : Array(rows.prefix(collapsedCount))
    }

    /// A bar's share of the card's width: titles against the most titles, or stars. Stars run
    /// across `starRange` (the ranked rows' lowest and highest), not 0 to 5, so 3.7 and 4.0 look
    /// different; without one they're out of 5.
    static func fraction(
        _ row: StatsRankedRow,
        sort: StatsRankSort,
        maximumTitles: Int,
        starRange: ClosedRange<Double>? = nil
    ) -> Double {
        switch sort {
        case .mostLogged:
            return maximumTitles > 0 ? Double(row.titles) / Double(maximumTitles) : 0
        case .highestRated:
            let stars = row.averageStars?.value ?? 0
            guard let starRange, starRange.upperBound > starRange.lowerBound else {
                return min(max(stars / 5, 0), 1)
            }
            return min(max((stars - starRange.lowerBound) / (starRange.upperBound - starRange.lowerBound), 0), 1)
        }
    }

    /// The span Highest rated bars run across: from half a star below the lowest average,
    /// rounded down to a half star, to the highest. Nil when nothing is ranked by rating.
    static func starRange(_ rows: [StatsRankedRow]) -> ClosedRange<Double>? {
        let averages = rows
            .filter { $0.rated >= minimumRated }
            .compactMap { $0.averageStars?.value }
        guard let lowest = averages.min(), let highest = averages.max() else { return nil }
        let floor = max(((lowest - 0.5) * 2).rounded(.down) / 2, 0)
        return floor ... max(highest, floor + 0.5)
    }

    /// "Sci-Fi & Fantasy, 41 titles, 3.8 average from 33 ratings".
    static func accessibilityLabel(_ row: StatsRankedRow) -> String {
        var parts = [row.name, StatsChartCopy.count(row.titles, "title", "titles")]
        if row.rated > 0, let average = row.averageStars {
            parts.append("\(average.averageText) average from \(StatsChartCopy.count(row.rated, "rating", "ratings"))")
        }
        return parts.joined(separator: ", ")
    }
}

/// The Genres section's rows, then "Music genres" on Global. `onDrillDown` is nil on someone
/// else's stats.
struct StatsGenresSection: View {
    let genres: StatsGenres
    /// The tab: nil on Global.
    let mediaType: String?
    var onDrillDown: ((StatsDrillDown) -> Void)?

    var body: some View {
        if !genres.rows.isEmpty {
            StatsRankedBarsRows(
                title: "Genres",
                rows: StatsRankedRowsData.rows(genres),
                mediaType: mediaType,
                mostTitle: StatsRankSort.mostTitle(for: mediaType),
                footnote: StatsChartCopy.coverageNote(genres.coverage, qualifier: "with a genre"),
                onDrillDown: onDrillDown
            )
        }

        if mediaType == nil, !genres.music.isEmpty {
            // Each row opens the diary's music logs with that genre, as a Music tab genre does.
            let rows = StatsRankedRowsData.rows(music: genres.music)
            let maximum = rows.map(\.titles).max() ?? 0
            StatsSectionTitle("Music genres")
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                StatsRankedBarRow(
                    row: row,
                    sort: .mostLogged,
                    fraction: StatsRankedRowsData.fraction(row, sort: .mostLogged, maximumTitles: maximum),
                    stacked: false,
                    tint: StatsChartStyle.color(for: "music"),
                    isFirst: index == 0,
                    isLast: index == rows.count - 1,
                    action: onDrillDown.map { onDrillDown in { onDrillDown(row.drillDown) } }
                )
            }
            StatsSectionEnd()
        }
    }
}

/// A ranked bars card: title, the order control, one row per bar, "Show all", a legend and a
/// note. Shared by Genres and Languages.
struct StatsRankedBarsRows: View {
    let title: String
    let rows: [StatsRankedRow]
    /// The tab: nil on Global, where bars stack by type.
    let mediaType: String?
    /// The first order's label: "Most watched", "Most read".
    let mostTitle: String
    let footnote: String?
    let onDrillDown: ((StatsDrillDown) -> Void)?

    @State private var sort: StatsRankSort = .mostLogged
    @State private var showsAll = false

    var body: some View {
        let canRank = StatsRankedRowsData.canRankByRating(rows)
        let activeSort = canRank ? sort : .mostLogged
        let sorted = StatsRankedRowsData.sorted(rows, by: activeSort)
        let visible = StatsRankedRowsData.visible(sorted, showsAll: showsAll)
        let hasMore = sorted.count > StatsRankedRowsData.collapsedCount
        let maximumTitles = rows.map(\.titles).max() ?? 0
        let starRange = StatsRankedRowsData.starRange(rows)
        let legendTypes = mediaType == nil && activeSort == .mostLogged
            ? StatsChartStyle.types(in: rows.map(\.byType))
            : []

        StatsSectionTitle(title)

        if canRank {
            Picker("Order", selection: $sort) {
                ForEach(StatsRankSort.allCases) { sort in
                    Text(sort.title(mostTitle: mostTitle)).tag(sort)
                }
            }
            .pickerStyle(.segmented)
            .sensoryFeedback(.selection, trigger: sort)
            .padding(.bottom, 12)
        }

        ForEach(Array(visible.enumerated()), id: \.element.id) { index, row in
            StatsRankedBarRow(
                row: row,
                sort: activeSort,
                fraction: StatsRankedRowsData.fraction(
                    row,
                    sort: activeSort,
                    maximumTitles: maximumTitles,
                    starRange: starRange
                ),
                stacked: mediaType == nil,
                tint: StatsChartStyle.color(for: mediaType),
                isFirst: index == 0,
                isLast: index == visible.count - 1 && !hasMore,
                action: onDrillDown.map { onDrillDown in { onDrillDown(row.drillDown) } }
            )
        }

        if hasMore {
            StatsShowAllRow(title: showsAll ? "Show fewer" : "Show all \(sorted.count.formatted())") {
                withAnimation(.easeInOut(duration: 0.2)) { showsAll.toggle() }
            }
        }

        if legendTypes.count > 1 {
            StatsTypeLegend(mediaTypes: legendTypes)
                .padding(.horizontal, 4)
                .padding(.top, 10)
        }

        if let footnote {
            StatsChartFootnote(text: footnote)
        }

        StatsSectionEnd()
    }
}

/// One bar: the name and its number, then the bar under them.
private struct StatsRankedBarRow: View {
    let row: StatsRankedRow
    let sort: StatsRankSort
    let fraction: Double
    let stacked: Bool
    let tint: Color
    let isFirst: Bool
    let isLast: Bool
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
        .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
    }

    private func content(showsChevron: Bool) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(row.name)
                    .font(.body)
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 10)
                value
                if showsChevron {
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.35))
                        .accessibilityHidden(true)
                }
            }

            if sort == .highestRated {
                StatsStackedBar(parts: [], fraction: fraction, tint: LogComposerStyle.starGold)
            } else {
                StatsStackedBar(
                    parts: stacked ? StatsChartStyle.orderedTypes(row.byType) : [],
                    fraction: fraction,
                    tint: tint
                )
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, isFirst ? 14 : 9)
        .padding(.bottom, isLast ? 14 : 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(StatsRankedRowsData.accessibilityLabel(row))
        .accessibilityAddTraits(action == nil ? [] : .isButton)
    }

    @ViewBuilder
    private var value: some View {
        if sort == .highestRated, let average = row.averageStars {
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(average.averageText)
                    .monospacedDigit()
                Image(systemName: "star.fill")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(LogComposerStyle.starGold)
            }
            .font(.body)
            .foregroundStyle(.white.opacity(0.7))
        } else {
            Text(row.titles.formatted())
                .font(.body)
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.7))
        }
    }
}
