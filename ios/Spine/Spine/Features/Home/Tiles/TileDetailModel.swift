import Foundation

/// Where a title in the In progress tile stands, in the one unit its tile counts. `HomeViewModel` captures
/// `current` as the baseline when the rail loads; the panel and the quick action read the same numbers.
struct TilePosition: Equatable {
    var unit: TodayUnit
    /// Done so far, in `unit`. Percent keeps its decimals; everything else is whole.
    var current: Double
    /// The end of the run in `unit`: aired episodes, pages, chapters, 100 for percent. Nil when unknown, and always
    /// nil for a game's hours, which have no end.
    var total: Double?
    /// The title's length in pages when it is known, which converts the server's pages and percent to each other.
    var pagesTotal: Double?
}

/// The season a show in the In progress tile is counted by, as its season's tracking read it. A show is always partway
/// through a season, never through the whole show, so its panel counts this season's episodes.
struct TileSeason: Equatable {
    let seasonNumber: Int
    let watched: Int
    /// The season's episodes: its episode count when known (a season still airing has more to come), else the aired.
    let total: Int?
    /// The show's watched count when the season was read. Episodes watched on this phone since then move the season
    /// on at once, before it is read again.
    let showWatchedAtRead: Int

    init(seasonNumber: Int, watched: Int, total: Int?, showWatchedAtRead: Int) {
        self.seasonNumber = seasonNumber
        self.watched = watched
        self.total = total
        self.showWatchedAtRead = showWatchedAtRead
    }

    /// Nil for a season with nothing to count.
    init?(state: TVSeasonState, showWatched: Int?) {
        let total = [state.episodeCount, state.airedCount].compactMap { $0 }.first { $0 > 0 }
        guard state.watchedCount != nil || total != nil else { return nil }
        self.init(
            seasonNumber: state.seasonNumber,
            watched: max(0, state.watchedCount ?? 0),
            total: total,
            showWatchedAtRead: showWatched ?? 0
        )
    }
}

/// Where a title stands, in the parts the panel draws separately: the number that rolls, the words around it.
/// `text` is the one-line string the panel used before it had parts, so nothing drifts between the two.
nonisolated struct TilePlace: Hashable {
    /// "S2 \u{00B7} ", "p. ", "", or the whole text when `value` is nil ("Season 2", "14h", "2h 30m").
    var prefix: String
    /// The rolling number; nil for a text-only place.
    var value: Int?
    /// " of " when `total` is known, else "".
    var joiner: String
    var total: Int?
    /// " episodes", " chapter", ".5%", "%", "".
    var suffix: String

    static let none = TilePlace(prefix: "", value: nil, joiner: "", total: nil, suffix: "")

    var text: String {
        var text = prefix
        if let value { text += InProgressTilePresenter.grouped(value) }
        text += joiner
        if let total { text += InProgressTilePresenter.grouped(total) }
        return text + suffix
    }
}

/// Everything the In progress panel shows and decides for one title.
struct TileDetailModel: Equatable {
    let title: String
    /// "+2 ep", "+20 pages", "+3%", "+1h". Nil when nothing was done today. Not drawn (the meter's lit part shows
    /// today, and a number of days ago reads as random); VoiceOver says it.
    let todayText: String?
    /// "S2 · 7 of 10 episodes", "p. 212 of 400", "47%", "41 of 90 chapters", "14h": where the title stands now, in
    /// parts. `.none` when the title's place is not known.
    let place: TilePlace
    /// Nil when there is nothing honest to draw: no known end, or a game counted in hours.
    let meter: ProgressMeterModel?
    /// The poster's accent color; the view falls back to white when the server sent none.
    let accentHex: String?
    /// Nil for a type with nothing to tick (movies, music).
    let primary: QuickActionPlan?
    /// "Update" when the title has its own progress UI, else "Open".
    let secondaryTitle: String
    let accessibilityLabel: String

    /// `place` as one line.
    var whereText: String { place.text }
}

enum InProgressTilePresenter {
    /// - Parameters:
    ///   - today: what the server counted today for this title (`HomeTodayStore.part(for:)`).
    ///   - baseline: where the title stood when the rail loaded, from `position(for:)`; local gains since then are
    ///     lit too.
    ///   - season: a show's current season once it has been read (`currentSeasonNumber`); a show is counted by it.
    static func detail(
        for item: LibraryItem,
        today: TodayPart?,
        baseline: Double?,
        season: TileSeason? = nil
    ) -> TileDetailModel {
        let title = item.media.displayTitle
        let position = position(for: item)

        var todayAmount = 0.0
        var meter: ProgressMeterModel?
        var place = TilePlace.none
        var spokenWhere = ""
        if let position {
            todayAmount = TileTodayMath.today(
                server: today,
                baseline: baseline,
                current: position.current,
                unit: position.unit,
                total: position.pagesTotal ?? position.total
            )
            if let seasonNumber = currentSeasonNumber(for: item) {
                let counted = seasonPlace(
                    number: seasonNumber,
                    season: season,
                    showWatched: wholeNumber(position.current),
                    today: todayAmount
                )
                meter = counted.meter
                place = counted.place
                spokenWhere = counted.spoken
            } else {
                meter = ProgressMeterModel.make(
                    unit: meterUnit(for: position.unit),
                    from: wholeNumber(position.current - todayAmount),
                    to: wholeNumber(position.current),
                    total: position.total.map(wholeNumber)
                )
                place = placeParts(position)
                let text = place.text
                spokenWhere = text.hasPrefix("p. ") ? "page " + text.dropFirst(3) : text
            }
        }
        let todayText = position.flatMap { text(forToday: todayAmount, unit: $0.unit) }

        return TileDetailModel(
            title: title,
            todayText: todayText,
            place: place,
            meter: meter,
            accentHex: item.media.posterAccentColor,
            primary: QuickActionPlan.make(for: item),
            secondaryTitle: InProgressUpdates.route(for: item) == .page ? "Open" : "Update",
            accessibilityLabel: accessibilityLabel(title: title, spokenWhere: spokenWhere, todayText: todayText)
        )
    }

    // MARK: Seasons

    /// The season a show's panel counts: the one up next is in, else the one airing next. Nil for anything but a show,
    /// and for a show with neither, which is counted as a whole.
    static func currentSeasonNumber(for item: LibraryItem) -> Int? {
        guard item.media.ref.mediaType == "tv", let tv = item.tracking.tv else { return nil }
        return tv.upNext?.seasonNumber ?? tv.nextAiring?.seasonNumber
    }

    /// "S2 · 7 of 10 episodes" and the season's ticks, today's episodes lit (never more than the season has watched).
    /// Until the season has been read: "Season 2" and no meter, never the whole show's numbers.
    private static func seasonPlace(
        number: Int,
        season: TileSeason?,
        showWatched: Int,
        today: Double
    ) -> (place: TilePlace, spoken: String, meter: ProgressMeterModel?) {
        let name = "Season \(number)"
        let unread = TilePlace(prefix: name, value: nil, joiner: "", total: nil, suffix: "")
        guard let season, season.seasonNumber == number else { return (unread, name, nil) }
        var watched = max(0, season.watched + showWatched - season.showWatchedAtRead)
        if let total = season.total { watched = min(watched, total) }
        var place = countedPlace(watched, of: season.total, noun: "episode")
        guard place != .none else { return (unread, name, nil) }
        let lit = min(wholeNumber(today), watched)
        let meter = ProgressMeterModel.make(unit: .episodes, from: watched - lit, to: watched, total: season.total)
        let episodes = place.text
        place.prefix = "S\(number) \u{00B7} "
        return (place, "\(name), \(episodes)", meter)
    }

    /// "5 titles", "1 title".
    static func subtitle(count: Int) -> String {
        count == 1 ? "1 title" : "\(count) titles"
    }

    // MARK: Position

    /// Where the title stands, or nil for a type the tile has no numbers for (movies, music, board games) and for a
    /// title with nothing stored yet.
    static func position(for item: LibraryItem) -> TilePosition? {
        let tracking = item.tracking
        let ref = item.media.ref
        switch ref.mediaType {
        case "tv":
            guard let tv = tracking.tv, let watched = tv.watchedCount else { return nil }
            return TilePosition(
                unit: .episodes, current: Double(watched), total: tv.airedCount.flatMap(positive), pagesTotal: nil
            )
        case "anime":
            guard let anime = tracking.anime, let watched = anime.watchedCount else { return nil }
            return TilePosition(
                unit: .episodes,
                current: Double(watched),
                total: (anime.airedCount ?? anime.total).flatMap(positive),
                pagesTotal: nil
            )
        case "book":
            return bookPosition(item)
        case "manga", "comic":
            return seriesPosition(item)
        case "game":
            return gamePosition(item)
        default:
            return nil
        }
    }

    private static func positive(_ value: Int) -> Double? {
        value > 0 ? Double(value) : nil
    }

    private static func double(_ value: Decimal?) -> Double? {
        value.map { NSDecimalNumber(decimal: $0).doubleValue }
    }

    private static func bookPosition(_ item: LibraryItem) -> TilePosition? {
        guard let progress = item.tracking.reading?.currentJourney?.progress ?? item.tracking.progress,
              progress.value != nil else { return nil }
        if progress.mode == .percentage {
            // A percent has no page count behind it, so it cannot be shown in pages.
            return percentPosition(progress.savedPercentage, pagesTotal: nil)
        }
        let pages = double(progress.max).flatMap { $0 > 0 ? $0 : nil }
        if ProgressDisplayPreferences.mode(for: item.media.ref) == .percentage, pages != nil,
           let percent = progress.savedPercentage {
            return percentPosition(percent, pagesTotal: pages)
        }
        guard let value = progress.value(in: .pages) else { return nil }
        return TilePosition(unit: .pages, current: double(value) ?? 0, total: pages, pagesTotal: pages)
    }

    private static func percentPosition(_ percent: Decimal?, pagesTotal: Double?) -> TilePosition? {
        guard let percent else { return nil }
        return TilePosition(unit: .percent, current: double(percent) ?? 0, total: 100, pagesTotal: pagesTotal)
    }

    private static func seriesPosition(_ item: LibraryItem) -> TilePosition? {
        let reading = item.tracking.reading
        guard let progress = reading?.currentJourney?.progress ?? item.tracking.progress,
              let value = double(progress.value) else { return nil }
        let total = double(progress.max) ?? reading?.total.map(Double.init)
        let issues = reading?.unit == "issue" || item.media.ref.mediaType == "comic"
        return TilePosition(
            unit: issues ? .issues : .chapters,
            current: value.rounded(),
            total: total.flatMap { $0 > 0 ? $0 : nil },
            pagesTotal: nil
        )
    }

    private static func gamePosition(_ item: LibraryItem) -> TilePosition? {
        guard let playthrough = item.tracking.game?.currentPlaythrough else { return nil }
        switch GameProgressMode.initial(for: item.media.ref, playthrough: playthrough) {
        case .percent:
            return percentPosition(playthrough.completion, pagesTotal: nil)
        case .hours:
            return TilePosition(
                unit: .minutes, current: Double(playthrough.totalMinutes ?? 0), total: nil, pagesTotal: nil
            )
        }
    }

    // MARK: Text

    private static func placeParts(_ position: TilePosition) -> TilePlace {
        let current = wholeNumber(position.current)
        let total = position.total.map(wholeNumber)
        switch position.unit {
        case .episodes:
            return countedPlace(current, of: total, noun: "episode")
        case .chapters:
            return countedPlace(current, of: total, noun: "chapter")
        case .issues:
            return countedPlace(current, of: total, noun: "issue")
        case .pages:
            guard current > 0 || total != nil else { return .none }
            return TilePlace(
                prefix: "p. ", value: current, joiner: total == nil ? "" : " of ", total: total, suffix: ""
            )
        case .percent:
            // Only the whole part rolls; the decimals ride in the suffix.
            let text = number(position.current)
            let parts = text.split(separator: ".", maxSplits: 1)
            let decimals = parts.count > 1 ? "." + parts[1] : ""
            return TilePlace(
                prefix: "", value: Int(parts[0]) ?? 0, joiner: "", total: nil, suffix: decimals + "%"
            )
        case .minutes:
            guard position.current > 0 else { return .none }
            return TilePlace(
                prefix: GameProgressValues.timeLabel(current), value: nil, joiner: "", total: nil, suffix: ""
            )
        }
    }

    /// "9 of 62 episodes", or "7 episodes" when the end is not known, in parts. Nothing for nothing yet.
    private static func countedPlace(_ current: Int, of total: Int?, noun: String) -> TilePlace {
        if let total {
            return TilePlace(
                prefix: "", value: current, joiner: " of ", total: total, suffix: " " + (total == 1 ? noun : noun + "s")
            )
        }
        guard current > 0 else { return .none }
        return TilePlace(
            prefix: "", value: current, joiner: "", total: nil, suffix: " " + (current == 1 ? noun : noun + "s")
        )
    }

    private static func text(forToday amount: Double, unit: TodayUnit) -> String? {
        guard amount > 0.004 else { return nil }
        let whole = wholeNumber(amount)
        switch unit {
        case .episodes: return "+\(grouped(whole)) ep"
        case .chapters: return "+\(grouped(whole)) \(whole == 1 ? "chapter" : "chapters")"
        case .issues: return "+\(grouped(whole)) \(whole == 1 ? "issue" : "issues")"
        case .pages: return "+\(grouped(whole)) \(whole == 1 ? "page" : "pages")"
        case .percent: return "+\(number(amount))%"
        case .minutes: return whole > 0 ? "+" + GameProgressValues.timeLabel(whole) : nil
        }
    }

    private static func accessibilityLabel(title: String, spokenWhere: String, todayText: String?) -> String {
        guard !spokenWhere.isEmpty else { return title }
        return [title, spokenWhere, todayText.map { "\($0.dropFirst()) today" }].compactMap { $0 }.joined(separator: ", ")
    }

    // MARK: Numbers

    static func meterUnit(for unit: TodayUnit) -> MeterUnit {
        switch unit {
        case .episodes: .episodes
        case .chapters: .chapters
        case .issues: .issues
        case .pages: .pages
        case .percent: .percent
        case .minutes: .minutes
        }
    }

    private static func wholeNumber(_ value: Double) -> Int {
        Int(max(0, value).rounded())
    }

    /// "212", or "1,089": the grouping the page shows.
    nonisolated static func grouped(_ value: Int) -> String {
        let digits = Array(String(abs(value)))
        var result = ""
        for (index, digit) in digits.enumerated() {
            if index > 0, (digits.count - index) % 3 == 0 { result.append(",") }
            result.append(digit)
        }
        return value < 0 ? "-" + result : result
    }

    /// "5", "42.5", never "5.0": a percent as the sheets write it.
    static func number(_ value: Double) -> String {
        let rounded = (value * 100).rounded() / 100
        if rounded == rounded.rounded() { return String(Int(rounded)) }
        var text = String(format: "%.2f", rounded)
        while text.hasSuffix("0") { text.removeLast() }
        return text
    }
}
