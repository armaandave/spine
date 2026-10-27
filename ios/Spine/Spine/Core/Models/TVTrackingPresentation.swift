import Foundation

/// Pure text and menu logic for TV surfaces. Every value comes from the server's TV state; nothing here works out
/// tracking rules of its own.
nonisolated enum TVTrackingPresentation {
    // MARK: Codes and titles

    static func episodeCode(season: Int, episode: Int) -> String {
        "S\(season) E\(episode)"
    }

    /// "Specials" for season 0 (TV-004), otherwise "Season N".
    static func seasonTitle(_ seasonNumber: Int) -> String {
        seasonNumber == 0 ? "Specials" : "Season \(seasonNumber)"
    }

    // MARK: Progress (TV-104)

    /// "13 of 20": aired regular episodes watched in the current pass, out of the aired regular episodes. A count,
    /// never an episode number. Nil when the counts are unknown or nothing has aired.
    static func progressText(watched: Int?, aired: Int?) -> String? {
        guard let watched, let aired, aired > 0 else { return nil }
        return "\(watched) of \(aired)"
    }

    // MARK: Library card (TV-700)

    /// The line under a show's Library card, for its section.
    static func cardLine(
        for tv: TVShowState,
        today: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String? {
        switch tv.status {
        case "In progress":
            if let rewatch = rewatchLine(for: tv) { return rewatch }
            if tv.caughtUp { return caughtUpLine(for: tv, today: today, calendar: calendar) }
            let progress = progressText(watched: tv.watchedCount, aired: tv.airedCount)
            guard let upNext = tv.upNextCode else { return progress }
            return ["\(upNext) next", progress].compactMap { $0 }.joined(separator: " · ")
        case "Planning":
            guard let airDate = calendarDate(tv.premiere?.airDate, calendar: calendar) else { return nil }
            return "Premieres \(shortDate(airDate, calendar: calendar))"
        case "Paused":
            guard tv.started, let upNext = tv.upNextCode else { return "Paused" }
            return "Paused at \(upNext)"
        case "Dropped":
            guard tv.started, let upNext = tv.upNextCode else { return "Dropped" }
            return "Stopped at \(upNext)"
        case "Completed":
            return tv.caughtUp ? caughtUpLine(for: tv, today: today, calendar: calendar) : nil
        default:
            return nil
        }
    }

    // MARK: Caught up (TV-202)

    /// "Caught up · S3 on Mar 12" for a Completed show waiting for its next season, "Caught up · E7 on Thu" for a
    /// Watching show mid-season, and "Caught up" when the next episode has no date. Only the mid-season line reads
    /// the day as a weekday when it airs within six days; the next season's line always names the date (TV-202).
    static func caughtUpLine(
        for tv: TVShowState,
        today: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String {
        guard let next = tv.nextAiring, let airDate = calendarDate(next.airDate, calendar: calendar)
        else { return "Caught up" }
        if tv.status == "In progress", let episode = next.episodeNumber, episode > 1 {
            return "Caught up · E\(episode) on \(airDayText(airDate, today: today, calendar: calendar))"
        }
        return "Caught up · S\(next.seasonNumber) on \(shortDate(airDate, calendar: calendar))"
    }

    /// "Thu" when the day is within six days of today, otherwise "Mar 12".
    static func airDayText(_ date: Date, today: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> String {
        let days = calendar.dateComponents(
            [.day],
            from: calendar.startOfDay(for: today),
            to: calendar.startOfDay(for: date)
        ).day
        if let days, (0...6).contains(days) {
            return format(date, "EEE", calendar: calendar)
        }
        return shortDate(date, calendar: calendar)
    }

    static func shortDate(_ date: Date, calendar: Calendar = .autoupdatingCurrent) -> String {
        format(date, "MMM d", calendar: calendar)
    }

    /// `YYYY-MM-DD` (or a datetime whose first ten characters are the day) → the start of that day on `calendar`.
    static func calendarDate(_ value: String?, calendar: Calendar = .autoupdatingCurrent) -> Date? {
        guard let value else { return nil }
        let parts = value.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    /// The `YYYY-MM-DD` of `date` on `calendar`.
    static func calendarString(_ date: Date, calendar: Calendar = .autoupdatingCurrent) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    private static func format(_ date: Date, _ pattern: String, calendar: Calendar) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = pattern
        return formatter.string(from: date)
    }

    // MARK: Rewatch (TV-600)

    /// "Rewatching · S1 E3 next" for the show, "Rewatching S2 · E1 next" for a season.
    static func rewatchLine(for tv: TVShowState) -> String? {
        guard let rewatch = tv.rewatch else { return nil }
        if rewatch.scope == .season, let season = rewatch.seasonNumber {
            guard let upNext = tv.upNext, upNext.seasonNumber == season else { return "Rewatching S\(season)" }
            return "Rewatching S\(season) · E\(upNext.episodeNumber) next"
        }
        guard let upNext = tv.upNextCode else { return "Rewatching" }
        return "Rewatching · \(upNext) next"
    }

    // MARK: Continue Watching (TV-701)

    /// Where a show on an In progress card stands, which its card no longer shows but VoiceOver reads: "S2 E5 next ·
    /// 4 of 10", or "S3 E1 · New · 20 of 21" when up next aired after the user's latest viewing of the show. A card
    /// that a tick has just caught up or finished stays put until Home loads again, and reads as the show's Library
    /// card does ("Caught up · E7 on Thu"), else its count.
    static func continueWatchingLine(
        for tv: TVShowState,
        today: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String? {
        let progress = progressText(watched: tv.watchedCount, aired: tv.airedCount)
        guard let upNext = tv.upNext else {
            return cardLine(for: tv, today: today, calendar: calendar) ?? progress
        }
        let code = episodeCode(season: upNext.seasonNumber, episode: upNext.episodeNumber)
        return [upNext.isNew ? "\(code) · New" : "\(code) next", progress].compactMap { $0 }.joined(separator: " · ")
    }

    /// VoiceOver's label for a show card's title and "Update progress ›", the button that opens up next's season:
    /// "The Crown, update progress, S2 E5 next, 4 of 10".
    static func continueWatchingAccessibilityLabel(show: String, tv: TVShowState) -> String {
        let line = continueWatchingLine(for: tv)?.replacingOccurrences(of: " · ", with: ", ")
        return [show, "update progress", line].compactMap { $0 }.joined(separator: ", ")
    }

    /// The title of a card's episode sheet: "The Crown · Season 2", or the provider's name for the season when the
    /// season's page has one ("Doctor Who · Series 2").
    static func episodeSheetTitle(show: String, seasonNumber: Int, seasonName: String? = nil) -> String {
        "\(show) · \(nonEmpty(seasonName) ?? seasonTitle(seasonNumber))"
    }

    // MARK: Show page Up next (TV-105)

    /// The label over the show page's Up next row, which names the season the row's episode is in: "Up next ·
    /// Season 2", or "New episode · Season 3" when it aired after the user's latest viewing of the show.
    static func upNextLabel(for upNext: TVEpisodePointer) -> String {
        "\(upNext.isNew ? "New episode" : "Up next") · \(seasonTitle(upNext.seasonNumber))"
    }

    // MARK: Seasons (TV-200, TV-204)

    /// A season card's label: "Watched", "Airing", "6 of 10", or "9 of 10 · E4 not watched". A started, unfinished
    /// season of a Paused or Dropped show leads with that status (TV-204). Nil when the season isn't started, hasn't
    /// aired, or has no stored list (TV-103: no invented count).
    static func seasonLabel(for season: TVSeasonState) -> String? {
        switch season.state {
        case .watched:
            return "Watched"
        case .airing:
            return "Airing"
        case .inProgress:
            guard let count = progressText(watched: season.watchedCount, aired: season.airedCount) else { return nil }
            let label = season.firstGapEpisode.map { "\(count) · E\($0) not watched" } ?? count
            guard let status = season.status, ["Paused", "Dropped"].contains(status) else { return label }
            return "\(status) · \(label)"
        case .notStarted, .unaired, .unknown:
            return nil
        }
    }

    // MARK: Diary (TV-702)

    /// "Show · Season 1", "Show · S1 E4 · Title", "Show · S1 E4" when the episode title is unknown, and the show's
    /// title for show logs. Other media keep their display title.
    @MainActor
    static func diaryTitle(for media: DiaryMedia) -> String {
        let ref = media.ref
        let show = nonEmpty(media.showTitle) ?? media.title
        switch ref.mediaType {
        case "tv":
            return show
        case "season":
            guard let season = ref.seasonNumber else { return show }
            return "\(show) · \(seasonTitle(season))"
        case "episode":
            guard let season = ref.seasonNumber, let episode = ref.episodeNumber else { return show }
            let code = episodeCode(season: season, episode: episode)
            guard let title = nonEmpty(media.episodeTitle) else { return "\(show) · \(code)" }
            return "\(show) · \(code) · \(title)"
        default:
            return media.displayTitle
        }
    }

    private static func nonEmpty(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    // MARK: Dialog copy

    /// TV-007: Drop asks first.
    static let dropPrompt = "Drop this show? Your progress will be kept."

    /// TV-003: Remove from Library says what it removes, from `remove_count`.
    static func removePrompt(count: Int?) -> String {
        guard let count, count > 0 else { return "Remove this show?" }
        let episodes = count == 1 ? "Your 1 watched episode" : "Your \(count) watched episodes"
        return "Remove this show? \(episodes) will be unmarked."
    }

    /// The confirmation an eye tap asks first (TV-300, TV-302): the server's `prompt`, else the contract's wording.
    /// Nil for taps that don't ask.
    static func eyePrompt(for eye: TVEyeState) -> String? {
        if let prompt = nonEmpty(eye.prompt) { return prompt }
        switch eye.tap {
        case .markConfirm:
            return eye.count.map { "Mark all \($0) aired episodes watched?" } ?? "Mark all aired episodes watched?"
        case .unmarkConfirm:
            return eye.count.map { "Mark all \($0) episodes unwatched?" } ?? "Mark all episodes unwatched?"
        default:
            return nil
        }
    }

    /// What an eye that records nothing says instead (TV-300, TV-302).
    static func eyeMessage(for eye: TVEyeState) -> String? {
        if let message = nonEmpty(eye.message) { return message }
        switch eye.tap {
        case .blocked: return "Delete the logs first."
        case .nothingAired: return "Nothing has aired yet."
        default: return nil
        }
    }

    /// TV-108's confirmation: the server's range ("Mark S1 E1–S2 E4 watched?"), else a plain fallback.
    static func markUpToPrompt(for episode: TVEpisodeState) -> String {
        nonEmpty(episode.markUpToPrompt)
            ?? "Mark everything up to \(episodeCode(season: episode.seasonNumber, episode: episode.episodeNumber)) watched?"
    }

    /// TV-406: deleting a log that unmarks episodes asks first. Nil when it unmarks nothing.
    static func deleteLogPrompt(unmarksCount: Int) -> String? {
        guard unmarksCount > 0 else { return nil }
        let episodes = unmarksCount == 1 ? "1 episode it marked watched" : "\(unmarksCount) episodes it marked watched"
        return "Delete this log? \(episodes) will be unmarked."
    }

    // MARK: + menu (TV-005)

    /// `selected_action` for a show status: shown as selected in the + menu, never offered as an action.
    static func selectedActionKey(forStatus status: String?) -> String? {
        switch status {
        case "In progress": "watching"
        case "Planning": "planning"
        case "Paused": "pause"
        case "Dropped": "drop"
        default: nil
        }
    }

    /// The show's + menu: Watching (Resume in its place when Paused), Planning, Pause, Drop, Rewatch or Stop
    /// Rewatching, Log, and Remove from Library for a show in the library. Base options are always listed;
    /// unavailable ones carry the server's reason. A show that isn't in the library has nothing to remove, whatever
    /// the server sends.
    static func showMenu(for tv: TVShowState) -> [TVMenuItem] {
        var items: [TVMenuItem] = []
        let menu = MenuSource(available: tv.availableActions, reasons: tv.actionReasons, selected: tv.selectedAction)
        if tv.status == "Paused" {
            // One row: Resume continues where the user was, as Watching does (TV-007).
            items.append(menu.optionalItem("resume", "Resume", "play.fill", .action(.resume))
                ?? menu.item("watching", "Resume", "play.fill", .action(.watching)))
        } else {
            items.append(menu.item("watching", "Watching", "play.fill", .action(.watching)))
        }
        items.append(menu.item("planning", "Planning", "bookmark", .action(.planning)))
        items.append(menu.item("pause", "Pause", "pause.fill", .action(.pause)))
        items.append(menu.item("drop", "Drop", "xmark", .confirmDrop))
        if let rewatch = menu.optionalItem("rewatch", "Rewatch", "arrow.counterclockwise", .action(.rewatch)) {
            items.append(rewatch)
        }
        if let stop = menu.optionalItem("stop_rewatch", "Stop Rewatching", "stop.fill", .action(.stopRewatch)) {
            items.append(stop)
        }
        items.append(menu.item("log", "Log", "square.and.pencil", .log))
        if tv.inLibrary {
            items.append(menu.item("remove", "Remove from Library", "trash", .confirmRemove))
        }
        return items
    }

    /// A season's + menu: Log Season, and Rewatch Season once the season is finished. During a rewatch Rewatch
    /// Season is left out rather than greyed out (TV-600).
    static func seasonMenu(for season: TVSeasonState) -> [TVMenuItem] {
        let menu = MenuSource(available: season.availableActions, reasons: season.actionReasons, selected: nil)
        var items = [menu.item("log_season", "Log Season", "square.and.pencil", .log)]
        if season.availableActions.contains("rewatch_season") {
            items.append(menu.item(
                "rewatch_season", "Rewatch Season", "arrow.counterclockwise", .rewatchSeason(season.seasonNumber)
            ))
        }
        return items
    }

    /// An episode's + menu: Log Episode.
    static func episodeMenu(for episode: TVEpisodeState) -> [TVMenuItem] {
        let menu = MenuSource(available: episode.availableActions, reasons: episode.actionReasons, selected: nil)
        return [menu.item("log_episode", "Log Episode", "square.and.pencil", .log)]
    }

    private struct MenuSource {
        let available: [String]
        let reasons: [String: String]
        let selected: String?

        func item(_ key: String, _ title: String, _ systemImage: String, _ command: TVMenuItem.Command) -> TVMenuItem {
            let isSelected = selected == key
            let isAvailable = !isSelected && available.contains(key)
            let reason = isSelected || isAvailable ? nil : (reasons[key] ?? "Not available right now.")
            return TVMenuItem(
                key: key,
                title: title,
                systemImage: systemImage,
                isSelected: isSelected,
                isAvailable: isAvailable,
                reason: reason,
                command: command
            )
        }

        /// Options listed only in some states (Resume, Rewatch, Stop Rewatching): shown when the server lists them
        /// as available or explains why they aren't.
        func optionalItem(_ key: String, _ title: String, _ systemImage: String, _ command: TVMenuItem.Command) -> TVMenuItem? {
            guard available.contains(key) || reasons[key] != nil || selected == key else { return nil }
            return item(key, title, systemImage, command)
        }
    }
}

/// One row of a TV + menu (TV-005).
nonisolated struct TVMenuItem: Hashable, Identifiable {
    enum Command: Hashable {
        /// Sends the action at once.
        case action(TVAction)
        /// Asks `TVTrackingPresentation.dropPrompt`, then sends `drop` (TV-007).
        case confirmDrop
        /// Asks `TVTrackingPresentation.removePrompt(count:)`, then sends `remove` with `expected_version` and a
        /// mutation ID (TV-003, TV-705).
        case confirmRemove
        /// Sends `rewatch` with the season number (TV-600).
        case rewatchSeason(Int)
        /// Opens the composer on this page's targets; saving posts a diary entry (TV-404).
        case log
    }

    let key: String
    let title: String
    let systemImage: String
    /// The current status: shown as selected, not offered (TV-005).
    let isSelected: Bool
    let isAvailable: Bool
    /// Why an unavailable option can't be chosen.
    let reason: String?
    let command: Command

    var id: String { key }
}

// MARK: Composer target switching (TV-404)

/// The composer's editable defaults for one target. Switching the target replaces only the values the user
/// hasn't changed with the new target's defaults, which the server sends for every target the picker offers.
nonisolated struct TVComposerDraft: Hashable {
    enum Field: Hashable {
        case date, rating, liked, rewatch
    }

    let composer: TVComposer
    private(set) var target: TVComposerTarget
    private(set) var editedFields: Set<Field> = []
    private var storedDate: String
    private var storedRatingSteps: Int
    private var storedLiked: Bool
    private var storedIsRewatch: Bool

    /// Opens on `targetId`, else the composer's default target. Nil when the composer has no such target.
    init?(composer: TVComposer, targetId: String? = nil) {
        guard let target = targetId.flatMap(composer.target(id:)) ?? composer.initialTarget else { return nil }
        self.composer = composer
        self.target = target
        storedDate = target.date
        storedRatingSteps = Self.ratingSteps(from: target.rating)
        storedLiked = target.liked
        storedIsRewatch = target.isRewatch
    }

    /// `YYYY-MM-DD` on the user's calendar (TV-407).
    var date: String {
        get { storedDate }
        set { storedDate = newValue; editedFields.insert(.date) }
    }

    /// 0 for no stars, else 1-10 on TV's wire scale (TV-504).
    var ratingSteps: Int {
        get { storedRatingSteps }
        set { storedRatingSteps = min(max(newValue, 0), 10); editedFields.insert(.rating) }
    }

    var liked: Bool {
        get { storedLiked }
        set { storedLiked = newValue; editedFields.insert(.liked) }
    }

    var isRewatch: Bool {
        get { storedIsRewatch }
        set { storedIsRewatch = newValue; editedFields.insert(.rewatch) }
    }

    func dateValue(calendar: Calendar = .autoupdatingCurrent) -> Date? {
        TVTrackingPresentation.calendarDate(storedDate, calendar: calendar)
    }

    mutating func setDate(_ date: Date, calendar: Calendar = .autoupdatingCurrent) {
        self.date = TVTrackingPresentation.calendarString(date, calendar: calendar)
    }

    /// Switches to another target the composer offers, replacing every default the user hasn't changed.
    mutating func selectTarget(id: String) {
        guard let next = composer.target(id: id) else { return }
        target = next
        if !editedFields.contains(.date) { storedDate = next.date }
        if !editedFields.contains(.rating) { storedRatingSteps = Self.ratingSteps(from: next.rating) }
        if !editedFields.contains(.liked) { storedLiked = next.liked }
        if !editedFields.contains(.rewatch) { storedIsRewatch = next.isRewatch }
    }

    /// "8.0" → 8 on TV's 0-10 scale; no stars → 0.
    static func ratingSteps(from rating: String?) -> Int {
        guard let rating, let value = Decimal(string: rating) else { return 0 }
        return min(max(NSDecimalNumber(decimal: value).intValue, 0), 10)
    }
}
