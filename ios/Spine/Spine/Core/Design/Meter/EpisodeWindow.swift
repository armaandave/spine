import Foundation

/// Where the 20-tick window of a long series starts. One function and three
/// presets, so the feed card, the tile panel and the update sheet agree on how
/// the window sits around the number being shown.
nonisolated enum EpisodeWindow {
    /// Ticks in the window.
    static let span = 20
    /// Episodes shown before the anchor on a feed card (the anchor sits near the right).
    static let feedLead = 17
    /// Episodes shown before the anchor on the tile panel and the sheets.
    static let panelLead = 15

    /// The first episode of the window: `anchor - lead`, kept inside 1...max(1, total - span + 1)
    /// so the window never starts before episode 1 or runs past the last episode.
    static func first(anchor: Int, lead: Int, span: Int = EpisodeWindow.span, total: Int) -> Int {
        let last = max(1, total - span + 1)
        return min(max(anchor - lead, 1), last)
    }

    /// Feed card: the day's last episode near the right edge.
    static func feedFirst(to: Int, total: Int) -> Int {
        first(anchor: to, lead: feedLead, total: total)
    }

    /// Tile panel: the current episode a little further in.
    static func panelFirst(value: Int, total: Int) -> Int {
        first(anchor: value, lead: panelLead, total: total)
    }

    /// Update sheet: like the panel, moved by however far the person has paged the window.
    static func sheetFirst(value: Int, pagingOffset: Int, total: Int) -> Int {
        first(anchor: value + pagingOffset, lead: panelLead, total: total)
    }
}
