import Foundation
import SwiftUI

/// Sizes, gaps and colors of the update sheet's big ticks and its long-series window (R3SheetTV and R3SheetLong).
/// The sheet is a different surface from the feed card, so these live with the sheet pieces, not in `HomeDesign`.
nonisolated enum EpisodeTickStyle {
    // Strip: one row of big ticks.
    static let stripTickHeight: CGFloat = 72
    static let stripTickRadius: CGFloat = 8
    static let stripGap: CGFloat = 6
    static let stripLabelGap: CGFloat = 7
    static let stripRestScaleY: CGFloat = 0.9

    // Window: 20 ticks, a paging row and the whole-series line.
    static let windowTickRowHeight: CGFloat = 60
    static let windowTodayHeight: CGFloat = 60
    static let windowWatchedHeight: CGFloat = 48
    static let windowRestHeight: CGFloat = 40
    static let windowTickGap: CGFloat = 4
    static let windowTickRadius: CGFloat = 5
    static let cardRadius: CGFloat = 20
    static let cardPaddingV: CGFloat = 14
    static let cardPaddingH: CGFloat = 16
    static let typedWidth: CGFloat = 150
    static let pagerTop: CGFloat = 18
    static let tickRowTop: CGFloat = 12
    static let lineTop: CGFloat = 18
    static let lineHeight: CGFloat = 6
    static let lineBoxHeight: CGFloat = 14
    static let lineMarkSize = CGSize(width: 4, height: 16)
    /// The window box on the series line never gets narrower than this share of the line.
    static let lineBoxMinFraction = 0.016

    // Header block.
    static let minHit: CGFloat = 44
    static let headerTop: CGFloat = 16
    static let summaryTop: CGFloat = 16
    static let hintTop: CGFloat = 6
    static let primaryTop: CGFloat = 22
    static let primaryHeight: CGFloat = 52

    // Colors: today is the caller's accent, the rest are fixed whites.
    static let before = Color.white.opacity(0.55)
    static let rest = Color.white.opacity(0.12)
    static let labelWatched = Color.white
    static let labelRest = Color.white.opacity(0.62)
    static let labelUnavailable = Color.white.opacity(0.38)
    static let secondary = Color.white.opacity(0.75)
    static let card = Color.white.opacity(0.08)
    static let control = Color.white.opacity(0.1)
    static let lineTrack = Color.white.opacity(0.14)
    static let lineFill = Color.white.opacity(0.5)
    static let lineBox = Color.white.opacity(0.22)
    static let lineBoxStroke = Color.white.opacity(0.5)
    static let lineCaption = Color.white.opacity(0.72)
}

/// What a season's (or a long series') update sheet draws and what a tap on it means. A pure value: the host builds
/// one from the episodes it has loaded and rebuilds it when they change; nothing here writes.
nonisolated struct EpisodeTickModel: Equatable, Sendable {
    enum EpisodeState: Equatable, Sendable {
        case watched
        case unwatched
        /// Not out yet.
        case unaired
        /// A number in the run that has no entry to mark.
        case unlisted
    }

    struct Episode: Equatable, Sendable {
        let number: Int
        let state: EpisodeState
        let title: String?

        init(number: Int, state: EpisodeState, title: String? = nil) {
            self.number = number
            self.state = state
            self.title = title
        }
    }

    /// What a tap on an episode asks the host to do. Exactly one per episode; the strip never invents a bulk unmark.
    enum TapAction: Equatable, Sendable {
        /// The episode is up next (or an aired one with nothing before it to ask about): the host ticks it.
        case tick
        /// An aired, listed episode beyond up next: the host asks its existing "mark everything up to it" question.
        case markUpTo
        /// A watched episode: the host calls its existing toggle, which keeps its own refusals.
        case untick
        /// Unaired or unlisted: nothing happens.
        case none
    }

    /// One tick as drawn.
    struct Tick: Equatable, Sendable, Identifiable {
        let number: Int
        let state: MeterTickState
        let episodeState: EpisodeState
        let title: String?
        let action: TapAction
        var id: Int { number }

        /// "Watched up to episode 7, Title".
        var accessibilityLabel: String {
            let place = "Watched up to episode \(EpisodeTickModel.grouped(number))"
            guard let title, !title.isEmpty else { return place }
            return "\(place), \(title)"
        }

        var accessibilityValue: String {
            switch episodeState {
            case .watched: state == .today ? "Watched today" : "Watched"
            case .unwatched: "Not watched"
            case .unaired: "Not out yet"
            case .unlisted: "Not listed"
            }
        }
    }

    // MARK: Thresholds

    /// A single row of big ticks for this many episodes or fewer; above it, the window.
    static let stripLimit = 14
    static let windowSpan = EpisodeWindow.span

    static let hint = "Tap an episode to mark everything up to it."
    static let windowHint = "Type the episode you are on, or tap one below."

    // MARK: Input

    /// Ascending by number, one entry per number.
    let episodes: [Episode]
    /// The episode a tap on the primary button would tick: the first aired, listed, unwatched one.
    let upNext: Int?
    /// How many of the last watched episodes were watched today.
    let todayCount: Int
    /// The last episode number of the run (the series length for a long series).
    let total: Int
    /// How far the person has paged the window from where it opens.
    let pagingOffset: Int

    /// The highest watched number, 0 when none.
    let upTo: Int
    /// The lowest number lit as today's, nil when nothing is.
    private let todayFloor: Int?
    private let hasUnaired: Bool

    init(
        episodes: [Episode],
        upNext: Int?,
        todayCount: Int = 0,
        seriesTotal: Int? = nil,
        pagingOffset: Int = 0
    ) {
        var seen = Set<Int>()
        let sorted = episodes
            .sorted { $0.number < $1.number }
            .filter { seen.insert($0.number).inserted }
        self.episodes = sorted
        self.upNext = upNext
        self.todayCount = max(todayCount, 0)
        self.total = max(seriesTotal ?? 0, sorted.last?.number ?? 0)
        self.pagingOffset = pagingOffset

        let watched = sorted.filter { $0.state == .watched }
        self.upTo = watched.last?.number ?? 0
        let lit = min(max(todayCount, 0), watched.count)
        self.todayFloor = lit > 0 ? watched[watched.count - lit].number : nil
        self.hasUnaired = sorted.contains { $0.state == .unaired }
    }

    func withPagingOffset(_ offset: Int) -> EpisodeTickModel {
        guard offset != pagingOffset else { return self }
        return EpisodeTickModel(
            episodes: episodes,
            upNext: upNext,
            todayCount: todayCount,
            seriesTotal: total,
            pagingOffset: offset
        )
    }

    // MARK: Layout choice

    /// More than 14 episodes uses the window.
    var usesWindow: Bool { total > Self.stripLimit }

    // MARK: Episodes and ticks

    func episode(_ number: Int) -> Episode? {
        var low = 0
        var high = episodes.count - 1
        while low <= high {
            let mid = (low + high) / 2
            let found = episodes[mid].number
            if found == number { return episodes[mid] }
            if found < number { low = mid + 1 } else { high = mid - 1 }
        }
        return nil
    }

    func state(of number: Int) -> EpisodeState {
        episode(number)?.state ?? .unlisted
    }

    /// before, today or rest for an episode: watched ones are today's when among the last `todayCount`.
    func tickState(of number: Int) -> MeterTickState {
        guard state(of: number) == .watched else { return .rest }
        if let todayFloor, number >= todayFloor { return .today }
        return .before
    }

    func tick(_ number: Int) -> Tick {
        let found = episode(number)
        return Tick(
            number: number,
            state: tickState(of: number),
            episodeState: found?.state ?? .unlisted,
            title: found?.title,
            action: action(for: number)
        )
    }

    /// One tick per listed number, for a short run.
    var stripTicks: [Tick] {
        episodes.map { tick($0.number) }
    }

    // MARK: Tap rules

    func action(for number: Int) -> TapAction {
        switch state(of: number) {
        case .unaired, .unlisted:
            return .none
        case .watched:
            return .untick
        case .unwatched:
            if let upNext, number > upNext { return .markUpTo }
            return .tick
        }
    }

    // MARK: Words

    /// "Up to E7 · Title", or the title left off when the caller has none, or "Nothing watched yet".
    var summaryLine: String {
        guard upTo > 0 else { return "Nothing watched yet" }
        let place = "Up to E\(Self.grouped(upTo))"
        guard let title = episode(upTo)?.title, !title.isEmpty else { return place }
        return "\(place) · \(title)"
    }

    /// "+3 today", or empty.
    var todayLine: String {
        Self.todayText(count: todayCount)
    }

    var primaryLabel: String {
        if let upNext { return "Watched E\(Self.grouped(upNext))" }
        return usesWindow || hasUnaired ? "All caught up" : "Season finished"
    }

    var primaryEnabled: Bool { upNext != nil }

    var primaryAccessibilityLabel: String {
        if let upNext { return "Mark episode \(Self.grouped(upNext)) watched" }
        return primaryLabel
    }

    static func todayText(count: Int) -> String {
        count > 0 ? "+\(grouped(count)) today" : ""
    }

    /// "1,150" style grouping that does not depend on the device's locale.
    static func grouped(_ number: Int) -> String {
        var out = ""
        for (index, digit) in String(abs(number)).reversed().enumerated() {
            if index > 0, index % 3 == 0 { out.append(",") }
            out.append(digit)
        }
        return (number < 0 ? "-" : "") + String(out.reversed())
    }

    // MARK: Window

    /// The first of the 20 episodes in view: the sheet preset around the highest watched, moved by the paging.
    var windowFirst: Int {
        EpisodeWindow.sheetFirst(value: upTo, pagingOffset: pagingOffset, total: max(total, 1))
    }

    var windowLast: Int { min(windowFirst + Self.windowSpan - 1, max(total, 1)) }

    /// "E1,074 – E1,093".
    var windowLabel: String {
        "E\(Self.grouped(windowFirst)) – E\(Self.grouped(windowLast))"
    }

    var windowTicks: [Tick] {
        (windowFirst...windowLast).map { tick($0) }
    }

    /// The model paged a window earlier (-1) or later (+1), never past either end and never storing an offset that
    /// would have to be paged back before the window moves again.
    func paged(_ direction: Int) -> EpisodeTickModel {
        let step = direction < 0 ? -1 : (direction > 0 ? 1 : 0)
        let lastStart = max(1, total - Self.windowSpan + 1)
        let target = min(max(windowFirst + step * Self.windowSpan, 1), lastStart)
        return withPagingOffset(target + EpisodeWindow.panelLead - upTo)
    }

    /// The whole-series line: how far the person is, where the window sits and how wide it is, each 0...1.
    var seriesFill: Double {
        total > 0 ? Double(upTo) / Double(total) : 0
    }

    var seriesWindowStart: Double {
        total > 0 ? Double(windowFirst - 1) / Double(total) : 0
    }

    var seriesWindowWidth: Double {
        total > 0 ? max(EpisodeTickStyle.lineBoxMinFraction, Double(Self.windowSpan) / Double(total)) : 1
    }

    var seriesAccessibilityLabel: String {
        "Episode \(Self.grouped(upTo)) of \(Self.grouped(total))"
    }

    static func windowTickHeight(_ state: MeterTickState) -> CGFloat {
        switch state {
        case .today: EpisodeTickStyle.windowTodayHeight
        case .before: EpisodeTickStyle.windowWatchedHeight
        case .rest: EpisodeTickStyle.windowRestHeight
        }
    }

    // MARK: Typing

    /// What the number field holds after an edit: digits only, no leading zeros, nothing above the last episode.
    /// An edit that would go out of range changes nothing (`previous` stays).
    func sanitizedTyped(_ raw: String, previous: String) -> String {
        let digits = raw.filter { $0 >= "0" && $0 <= "9" }
        let trimmed = String(digits.drop { $0 == "0" })
        guard !trimmed.isEmpty else { return "" }
        guard trimmed.count <= String(total).count, let value = Int(trimmed), value <= total else {
            return previous
        }
        return trimmed
    }

    /// The episode a typed text asks for, nil when it is empty or out of range.
    func typedEpisode(_ text: String) -> Int? {
        guard let value = Int(text), (1...max(total, 1)).contains(value) else { return nil }
        return value
    }

    /// The right-hand note on the number card: "+N today", "moved back" when the typed number is below where the
    /// person is, or the plain today line. A typed number above where the person is adds its distance.
    func windowTodayLine(typed: Int?) -> String {
        guard let typed, typed != upTo else { return todayLine }
        if typed < upTo { return "moved back" }
        return Self.todayText(count: todayCount + typed - upTo)
    }
}
