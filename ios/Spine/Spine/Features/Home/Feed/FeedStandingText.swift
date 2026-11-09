import Foundation

/// The muted "where" after a friend's amount on a feed card: where they stand on the title, in a few words. The tile's
/// panel says more ("p. 212 of 448"); a feed line is short.
enum FeedStandingText {
    /// "p. 212", "total 71%", "63/70" episodes ("9 episodes" when the end is not known), "41/90" chapters, "14h" or
    /// "1h 30m" for a game counted in time. Nil when there is nothing to say yet.
    static func whereText(for position: TilePosition) -> String? {
        let current = whole(position.current)
        let total = position.total.map(whole)
        switch position.unit {
        case .pages:
            guard current > 0 else { return nil }
            return "p. \(InProgressTilePresenter.grouped(current))"
        case .percent:
            return "total \(InProgressTilePresenter.number(position.current))%"
        case .episodes:
            return counted(current, of: total, noun: "episode")
        case .chapters:
            return counted(current, of: total, noun: "chapter")
        case .issues:
            return counted(current, of: total, noun: "issue")
        case .minutes:
            return current > 0 ? GameProgressValues.timeLabel(current) : nil
        }
    }

    /// A show's place in the season a friend is in: "S2 · 7/10" ("Specials" for season 0).
    static func seasonText(season: Int, current: Int, total: Int) -> String {
        let name = season == 0 ? "Specials" : "S\(season)"
        return "\(name) \u{00B7} " + (counted(current, of: total, noun: "episode") ?? "")
    }

    /// "63/70", or "9 episodes" when the end is not known. Nothing for nothing yet.
    private static func counted(_ current: Int, of total: Int?, noun: String) -> String? {
        if let total {
            return "\(InProgressTilePresenter.grouped(current))/\(InProgressTilePresenter.grouped(total))"
        }
        guard current > 0 else { return nil }
        return "\(InProgressTilePresenter.grouped(current)) " + (current == 1 ? noun : noun + "s")
    }

    /// The words VoiceOver says for a where: "63/70" is "63 of 70", never a date or a fraction.
    static func spoken(_ text: String) -> String {
        text.replacing(#/(\d[\d,]*)/(\d[\d,]*)/#) { match in "\(match.1) of \(match.2)" }
    }

    private static func whole(_ value: Double) -> Int {
        Int(max(0, value).rounded())
    }
}
