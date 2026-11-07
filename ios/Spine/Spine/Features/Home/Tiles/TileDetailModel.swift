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

/// Everything the In progress panel shows and decides for one title.
struct TileDetailModel: Equatable {
    let title: String
    /// "+2 ep", "+20 pages", "+3%", "+1h". Nil when nothing was done today.
    let todayText: String?
    /// "9 of 62 episodes", "p. 212 of 400", "47%", "41 of 90 chapters", "14h". Empty when the title's place is not known.
    let whereText: String
    /// Nil when there is nothing honest to draw: no known end, or a game counted in hours.
    let meter: ProgressMeterModel?
    /// The poster's accent color; the view falls back to white when the server sent none.
    let accentHex: String?
    /// Nil for a type with nothing to tick (movies, music).
    let primary: QuickActionPlan?
    /// "Update" when the title has its own progress UI, else "Open".
    let secondaryTitle: String
    let accessibilityLabel: String
}

enum InProgressTilePresenter {
    /// - Parameters:
    ///   - today: what the server counted today for this title (`HomeTodayStore.part(for:)`).
    ///   - baseline: where the title stood when the rail loaded, from `position(for:)`; local gains since then are
    ///     lit too.
    static func detail(for item: LibraryItem, today: TodayPart?, baseline: Double?) -> TileDetailModel {
        let title = item.media.displayTitle
        let position = position(for: item)

        var todayAmount = 0.0
        var meter: ProgressMeterModel?
        var whereText = ""
        if let position {
            todayAmount = TileTodayMath.today(
                server: today,
                baseline: baseline,
                current: position.current,
                unit: position.unit,
                total: position.pagesTotal ?? position.total
            )
            meter = ProgressMeterModel.make(
                unit: meterUnit(for: position.unit),
                from: wholeNumber(position.current - todayAmount),
                to: wholeNumber(position.current),
                total: position.total.map(wholeNumber)
            )
            whereText = placeText(position)
        }
        let todayText = position.flatMap { text(forToday: todayAmount, unit: $0.unit) }

        return TileDetailModel(
            title: title,
            todayText: todayText,
            whereText: whereText,
            meter: meter,
            accentHex: item.media.posterAccentColor,
            primary: QuickActionPlan.make(for: item),
            secondaryTitle: InProgressUpdates.route(for: item) == .page ? "Open" : "Update",
            accessibilityLabel: accessibilityLabel(title: title, whereText: whereText, todayText: todayText)
        )
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

    private static func placeText(_ position: TilePosition) -> String {
        let current = wholeNumber(position.current)
        let total = position.total.map(wholeNumber)
        switch position.unit {
        case .episodes:
            return counted(current, of: total, noun: "episode")
        case .chapters:
            return counted(current, of: total, noun: "chapter")
        case .issues:
            return counted(current, of: total, noun: "issue")
        case .pages:
            guard current > 0 || total != nil else { return "" }
            return total.map { "p. \(grouped(current)) of \(grouped($0))" } ?? "p. \(grouped(current))"
        case .percent:
            return "\(number(position.current))%"
        case .minutes:
            return position.current > 0 ? GameProgressValues.timeLabel(current) : ""
        }
    }

    /// "9 of 62 episodes", or "7 episodes" when the end is not known. Nothing for nothing yet.
    private static func counted(_ current: Int, of total: Int?, noun: String) -> String {
        if let total {
            return "\(grouped(current)) of \(grouped(total)) \(total == 1 ? noun : noun + "s")"
        }
        guard current > 0 else { return "" }
        return "\(grouped(current)) \(current == 1 ? noun : noun + "s")"
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

    private static func accessibilityLabel(title: String, whereText: String, todayText: String?) -> String {
        guard !whereText.isEmpty else { return title }
        let place = whereText.hasPrefix("p. ") ? "page " + whereText.dropFirst(3) : whereText
        return [title, place, todayText.map { "\($0.dropFirst()) today" }].compactMap { $0 }.joined(separator: ", ")
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
    static func grouped(_ value: Int) -> String {
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
