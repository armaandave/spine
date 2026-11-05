import Foundation

/// Pure text logic for anime surfaces, as `TVTrackingPresentation` is for TV. Every value comes from the server's state;
/// nothing here works out tracking rules of its own. A single has no number anywhere (AN-002).
nonisolated enum AnimeTrackingPresentation {
    static func episodeCode(_ episode: Int) -> String {
        "E\(episode)"
    }

    // MARK: Progress (AN-102)

    /// "7 of 24", or "7 episodes" when Spine doesn't know how many have aired. Nil with nothing to say.
    static func progressText(watched: Int?, aired: Int?) -> String? {
        if let aired {
            guard aired > 0 else { return nil }
            return "\(watched ?? 0) of \(aired)"
        }
        guard let watched, watched > 0 else { return nil }
        return watched == 1 ? "1 episode" : "\(watched) episodes"
    }

    /// "11 of 12 · E4 not watched" when a skipped episode keeps the title unfinished (AN-200), else its count.
    static func progressLine(for anime: AnimeState) -> String? {
        guard let progress = progressText(watched: anime.watchedCount, aired: anime.airedCount) else { return nil }
        return anime.firstGapEpisode.map { "\(progress) · \(episodeCode($0)) not watched" } ?? progress
    }

    // MARK: Library card (AN-700)

    /// The line under a title's Library card, for its section. Nil when the card has nothing extra to say.
    static func cardLine(
        for anime: AnimeState,
        today: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String? {
        switch anime.status {
        case "In progress":
            if let rewatch = rewatchLine(for: anime) { return rewatch }
            if anime.caughtUp { return caughtUpLine(for: anime, today: today, calendar: calendar) }
            if anime.firstGapEpisode != nil { return progressLine(for: anime) }
            guard let upNext = anime.upNext else {
                // The list variant doesn't say whether a title is a single, which has neither up next nor a count.
                return anime.single || (anime.airedCount ?? 1) <= 1 ? nil : progressLine(for: anime)
            }
            let progress = progressText(watched: anime.watchedCount, aired: anime.airedCount)
            return ["\(episodeCode(upNext.episodeNumber)) next", progress].compactMap { $0 }.joined(separator: " · ")
        case "Planning":
            guard let airDate = TVTrackingPresentation.calendarDate(anime.premiere?.airDate, calendar: calendar)
            else { return nil }
            return "Premieres \(TVTrackingPresentation.shortDate(airDate, calendar: calendar))"
        case "Paused":
            return placeLine("Paused at", anime) ?? "Paused"
        case "Dropped":
            return placeLine("Stopped at", anime) ?? "Dropped"
        default:
            return nil
        }
    }

    /// "Paused at E8", naming up next, once something is watched (AN-700).
    private static func placeLine(_ words: String, _ anime: AnimeState) -> String? {
        guard (anime.watchedCount ?? 0) > 0, let upNext = anime.upNext else { return nil }
        return "\(words) \(episodeCode(upNext.episodeNumber))"
    }

    /// "Caught up · E8 on Thu": the weekday within six days, the date after that, and just "Caught up" with no date
    /// (AN-201).
    static func caughtUpLine(
        for anime: AnimeState,
        today: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String {
        guard let next = anime.nextAiring,
              let episode = next.episodeNumber,
              let airDate = TVTrackingPresentation.calendarDate(next.airDate, calendar: calendar)
        else { return "Caught up" }
        let day = TVTrackingPresentation.airDayText(airDate, today: today, calendar: calendar)
        return "Caught up · \(episodeCode(episode)) on \(day)"
    }

    /// "Rewatching · E3 next" (AN-600).
    static func rewatchLine(for anime: AnimeState) -> String? {
        guard anime.rewatch != nil else { return nil }
        guard let upNext = anime.upNext else { return "Rewatching" }
        return "Rewatching · \(episodeCode(upNext.episodeNumber)) next"
    }

    /// The short badge on a grid poster, and on Home's card above its bar: up next, once an episode has been watched in
    /// the current pass, while the title is Watching, Paused or Dropped and not caught up (AN-700).
    static func gridBadge(for anime: AnimeState) -> String? {
        guard !anime.caughtUp, (anime.watchedCount ?? 0) > 0,
              ["In progress", "Paused", "Dropped"].contains(anime.status ?? ""),
              let upNext = anime.upNext
        else { return nil }
        return episodeCode(upNext.episodeNumber)
    }

    // MARK: Home (AN-701)

    /// Where a title on an In progress card stands, which its line doesn't show but VoiceOver reads: "E8 next · 7 of 24".
    static func continueWatchingLine(
        for anime: AnimeState,
        today: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String? {
        guard let upNext = anime.upNext else { return cardLine(for: anime, today: today, calendar: calendar) }
        let progress = progressText(watched: anime.watchedCount, aired: anime.airedCount)
        let code = episodeCode(upNext.episodeNumber)
        return [upNext.isNew ? "\(code) · New" : "\(code) next", progress].compactMap { $0 }.joined(separator: " · ")
    }

    /// VoiceOver's label for a title's card: "Attack on Titan, update progress, E8 next, 7 of 24".
    static func continueWatchingAccessibilityLabel(title: String, anime: AnimeState) -> String {
        let line = continueWatchingLine(for: anime)?.replacingOccurrences(of: " · ", with: ", ")
        return [title, "update progress", line].compactMap { $0 }.joined(separator: ", ")
    }

    // MARK: The page (AN-702)

    /// The label over the page's Up next row.
    static func upNextLabel(for upNext: TVEpisodePointer) -> String {
        upNext.isNew ? "New episode" : "Up next"
    }

    /// The page's bold status: In progress reads Watching (TV-002).
    static func pageStatus(for anime: AnimeState) -> String? {
        guard anime.inLibrary, let status = anime.status else { return nil }
        return status == "In progress" ? "Watching" : status
    }

    /// The page's lines: the rewatch, caught-up or premiere line, and progress as a count. A single has none (AN-002).
    static func pageLines(for anime: AnimeState) -> [String] {
        guard anime.inLibrary, !anime.single else { return [] }
        var lines: [String] = []
        if let rewatch = rewatchLine(for: anime) {
            lines.append(rewatch)
        } else if anime.caughtUp {
            lines.append(caughtUpLine(for: anime))
        } else if !anime.started, anime.status == "Planning", let card = cardLine(for: anime) {
            lines.append(card)
        }
        if anime.started, !anime.finished || anime.rewatch != nil, let progress = progressLine(for: anime) {
            lines.append(anime.firstGapEpisode == nil ? "\(progress) watched" : progress)
        }
        return lines
    }

    // MARK: Dialog copy

    /// TV-007: Drop asks first.
    static let dropPrompt = "Drop this anime? Your progress will be kept."

    /// TV-003: Remove from Library says what it removes, from `remove_count`. A single has no number (AN-002).
    static func removePrompt(for anime: AnimeState) -> String {
        removePrompt(count: anime.single ? nil : anime.removeCount)
    }

    static func removePrompt(count: Int?) -> String {
        guard let count, count > 0 else { return "Remove this anime?" }
        let episodes = count == 1 ? "Your 1 watched episode" : "Your \(count) watched episodes"
        return "Remove this anime? \(episodes) will be unmarked."
    }
}
