import Foundation

/// What a meter counts.
nonisolated enum MeterUnit: String, Sendable, Equatable, CaseIterable {
    case episodes
    case chapters
    case issues
    case pages
    case percent
    case minutes
}

nonisolated enum MeterTickState: Sendable, Equatable {
    /// Done on an earlier day.
    case before
    /// Done today: the lit part.
    case today
    /// Still to come.
    case rest
}

/// One definition of when a progress meter exists and what shape it takes, shared by
/// feed cards, the tile panel and the update sheets. `from` and `to` are counts in
/// `unit` (episodes watched, pages read, percent done); the part between them is
/// today's and is lit. `from == to` lights nothing.
nonisolated struct ProgressMeterModel: Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        /// One tick per unit, for short runs (up to 30).
        case ticks(count: Int)
        /// 20 ticks of a long series starting at episode `first`, with a line for the whole series.
        case window(first: Int, span: Int, seriesTotal: Int)
        /// A bar for the whole length.
        case bar(total: Int)
    }

    /// Largest run drawn as one tick per unit.
    static let maxTicks = 30

    let kind: Kind
    let unit: MeterUnit
    /// Done before today, 0...total.
    let from: Int
    /// Done by the end of today, from...total.
    let to: Int

    /// The end of the run: tick count, series length or bar length.
    var total: Int {
        switch kind {
        case .ticks(let count): count
        case .window(_, _, let seriesTotal): seriesTotal
        case .bar(let total): total
        }
    }

    /// Ticks drawn.
    var tickCount: Int {
        switch kind {
        case .ticks(let count): count
        case .window(_, let span, _): span
        case .bar: 0
        }
    }

    /// How much of the run was done before today, 0...1.
    var beforeFraction: Double { Double(from) / Double(total) }

    /// How much of the run is today's, 0...1.
    var todayFraction: Double { Double(to - from) / Double(total) }

    /// Where the series line and the bar's lit part end, 0...1.
    var endFraction: Double { Double(to) / Double(total) }

    /// The meter, or nil when there is nothing honest to draw: the end is not known,
    /// `to` is not known, or the unit is minutes (game hours alone get no meter).
    /// Percent always runs to 100. `windowFirst` places a long series' window; when
    /// nil it sits as on a feed card.
    static func make(
        unit: MeterUnit,
        from: Int?,
        to: Int?,
        total: Int?,
        windowFirst: Int? = nil
    ) -> ProgressMeterModel? {
        guard unit != .minutes, let to else { return nil }
        let end: Int
        if unit == .percent {
            end = 100
        } else {
            guard let total, total > 0 else { return nil }
            end = total
        }
        let clampedTo = min(max(to, 0), end)
        let litFrom = min(max(from ?? clampedTo, 0), end)
        // Moved back (to below from): `to` becomes `from`, so nothing is lit.
        let finalTo = max(clampedTo, litFrom)

        let kind: Kind
        switch unit {
        case .episodes:
            if end <= maxTicks {
                kind = .ticks(count: end)
            } else {
                let first = windowFirst.map {
                    EpisodeWindow.first(anchor: $0, lead: 0, total: end)
                } ?? EpisodeWindow.feedFirst(to: finalTo, total: end)
                kind = .window(first: first, span: EpisodeWindow.span, seriesTotal: end)
            }
        case .chapters, .issues:
            kind = end <= maxTicks ? .ticks(count: end) : .bar(total: end)
        case .pages, .percent:
            kind = .bar(total: end)
        case .minutes:
            return nil
        }
        return ProgressMeterModel(kind: kind, unit: unit, from: litFrom, to: finalTo)
    }

    /// The state of tick `index` (0-based) for a ticks or window meter. A ticks meter's
    /// index n is unit n + 1; a window's index n is episode `first + n`. On a bar the
    /// index counts units from the start.
    func tickState(at index: Int) -> MeterTickState {
        switch kind {
        case .window(let first, _, _):
            let episode = first + index
            if episode <= from { return .before }
            if episode <= to { return .today }
            return .rest
        case .ticks, .bar:
            if index < from { return .before }
            if index < to { return .today }
            return .rest
        }
    }

    /// For VoiceOver, e.g. "Episode 18 of 28, 4 today", "71 percent, 13 today".
    var accessibilityLabel: String {
        let today = to - from
        let todayText = today > 0 ? "\(today) today" : "nothing today"
        let place: String
        switch unit {
        case .episodes: place = "Episode \(to) of \(total)"
        case .chapters: place = "Chapter \(to) of \(total)"
        case .issues: place = "Issue \(to) of \(total)"
        case .pages: place = "Page \(to) of \(total)"
        case .percent: place = "\(to) percent"
        case .minutes: place = "\(to) of \(total) minutes"
        }
        return "\(place), \(todayText)"
    }
}
