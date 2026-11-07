import Foundation

/// How much of a tile's title is today's: what the server already counted for today, plus whatever moved on this
/// phone since the rail loaded. Pure, so the lit part of the meter can be tested without a screen.
nonisolated enum TileTodayMath {
    /// - Parameters:
    ///   - server: today's part from the Activity rows (`HomeTodayStore`).
    ///   - baseline: where the title stood, in `unit`, when the rail loaded. Nil when it wasn't captured.
    ///   - current: where the title stands now, in `unit`.
    ///   - unit: what the tile counts.
    ///   - total: the title's length in pages, when known. It is what turns the server's pages into percent and
    ///     percent into pages; the percent bar itself always runs to 100.
    /// - Returns: the server's part (converted to `unit` when it can be) plus any local gain, never negative. A
    ///   server part in a unit that cannot convert (hours against percent, always) is left out and only the local
    ///   change counts.
    static func today(
        server: TodayPart?,
        baseline: Double?,
        current: Double?,
        unit: TodayUnit,
        total: Double?
    ) -> Double {
        var local = 0.0
        if let baseline, let current { local = max(0, current - baseline) }
        let remote = server.flatMap { convert($0, to: unit, total: total) } ?? 0
        return max(0, remote + local)
    }

    /// The server's part in the tile's unit, or nil when the two cannot be compared.
    static func convert(_ part: TodayPart, to unit: TodayUnit, total: Double?) -> Double? {
        guard part.amount.isFinite, part.amount > 0 else { return nil }
        if part.unit == unit { return part.amount }
        guard let total, total > 0 else { return nil }
        switch (part.unit, unit) {
        case (.pages, .percent):
            return (part.amount / total * 100 * 100).rounded() / 100
        case (.percent, .pages):
            return (part.amount / 100 * total).rounded()
        default:
            // Minutes never become percent, and a count of one thing never becomes another.
            return nil
        }
    }
}
