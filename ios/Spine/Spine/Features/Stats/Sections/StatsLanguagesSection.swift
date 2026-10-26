import SwiftUI

// Languages (STAT-212): each movie and show once, by its original language, with the same
// "Most watched / Highest rated" control and "Show all" as Genres. On Global the note says which
// types it's counted from ("From movies and TV"). A row opens the diary for that language.

/// The Languages section's rows. `onDrillDown` is nil on someone else's stats.
struct StatsLanguagesSection: View {
    let languages: StatsLanguages
    /// The tab: nil on Global.
    let mediaType: String?
    var onDrillDown: ((StatsDrillDown) -> Void)?

    var body: some View {
        if !languages.rows.isEmpty {
            StatsRankedBarsRows(
                title: "Languages",
                rows: StatsRankedRowsData.rows(languages),
                mediaType: mediaType,
                // Only movies and TV have languages.
                mostTitle: "Most watched",
                footnote: StatsLanguagesSection.footnote(languages, mediaType: mediaType),
                onDrillDown: onDrillDown
            )
        }
    }

    /// "From movies and TV · Based on 150 of 158 titles with a language" on Global; on a type
    /// tab only the coverage part, when some titles have no language.
    static func footnote(_ languages: StatsLanguages, mediaType: String?) -> String? {
        StatsChartCopy.footnote([
            mediaType == nil ? StatsChartCopy.typesNote(languages.mediaTypes) : nil,
            StatsChartCopy.coverageNote(languages.coverage, qualifier: "with a language"),
        ])
    }
}
