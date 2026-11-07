import Foundation

/// Turns the feed's items (newest first) into the flat list Home draws: a day header, then that day's cards, with
/// headers and cards as siblings. Days come from the log's own date (`consumed_at`), never from when it was posted,
/// because a Phase 5 dated save can land a row on an earlier day.
enum FeedEntries {
    /// - Parameters:
    ///   - standing: where each friend stands on a title; asked only for the newest progress or status card of a
    ///     person's title.
    ///   - planning: loaded Planning days by the row's `object.id`.
    static func make(
        items: [ActivityItem],
        standing: FeedStandingProviding? = nil,
        planning: [Int: PlanningDay] = [:],
        now: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> [FeedListEntry] {
        let standing = standing ?? NoFeedStanding()
        var entries: [FeedListEntry] = []
        entries.reserveCapacity(items.count + 8)
        var currentDay: String?
        var seenDays = Set<String>()
        var seenTitles = Set<String>()

        for item in items {
            if let day = dayKey(for: item, calendar: calendar), day != currentDay {
                // The first run of a day is "day-<date>"; a later run of it (a dated row landed under other days)
                // adds its first card, so no id repeats and none changes when rows are added elsewhere.
                let id = seenDays.insert(day).inserted ? "day-\(day)" : "day-\(day)-\(item.id)"
                entries.append(.day(id: id, title: dayTitle(forKey: day, now: now, calendar: calendar)))
                currentDay = day
            }

            // Newest first, so the first progress or status card of a person's title is its newest.
            var isNewest = false
            var rowStanding: FeedStanding?
            if item.object.type == "progress_day" {
                if let ref = item.media?.ref {
                    isNewest = seenTitles.insert("\(item.actor.id)|\(titleKey(for: ref))").inserted
                }
                rowStanding = standing.standing(for: item, isNewestOfTitle: isNewest)
            }
            entries.append(.card(FeedCardPresenter.model(
                for: item,
                standing: rowStanding,
                isNewestOfTitle: isNewest,
                planning: item.planning == nil ? nil : planning[item.object.id]
            )))
        }
        return entries
    }

    /// One title whatever its season or episode: a show's episodes and seasons are the show, an anime's too.
    static func titleKey(for ref: MediaRef) -> String {
        let isAnime = ref.isAnime || (ref.isTVFamily && ref.source == "mal")
        let type = isAnime ? "anime" : (ref.isTVFamily ? "tv" : ref.mediaType)
        return "\(ref.source):\(type):\(ref.mediaId)"
    }

    // MARK: Days

    /// "yyyy-MM-dd" of the log's date, else of the day it was posted on the phone's calendar. Nil when neither parses.
    static func dayKey(for item: ActivityItem, calendar: Calendar) -> String? {
        if let day = calendarDay(from: item.consumedAt) { return day }
        guard let created = instant(from: item.createdAt) else { return nil }
        let parts = calendar.dateComponents([.year, .month, .day], from: created)
        guard let year = parts.year, let month = parts.month, let day = parts.day else { return nil }
        return String(format: "%04d-%02d-%02d", year, month, day)
    }

    /// The date part of "2026-11-07" (or of a datetime that starts with it), checked to be a real day.
    private static func calendarDay(from raw: String?) -> String? {
        guard let raw else { return nil }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines).prefix(10)
        let parts = text.split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2,
              let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
              (1 ... 12).contains(month), day >= 1, day <= daysIn(month: month, year: year)
        else { return nil }
        return String(text)
    }

    private static func daysIn(month: Int, year: Int) -> Int {
        switch month {
        case 2: (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 ? 29 : 28
        case 4, 6, 9, 11: 30
        default: 31
        }
    }

    private static let fractionalISO: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let plainISO = ISO8601DateFormatter()

    private static func instant(from raw: String?) -> Date? {
        guard let raw else { return nil }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return fractionalISO.date(from: text) ?? plainISO.date(from: text)
    }

    /// Today, Yesterday, the weekday within the last six days, otherwise "Nov 3" ("Nov 3, 2025" outside this year).
    /// Both the day and `now` are read on the phone's calendar, so the same instant can be two different days.
    static func dayTitle(forKey key: String, now: Date, calendar: Calendar) -> String {
        let numbers = key.split(separator: "-").compactMap { Int($0) }
        guard numbers.count == 3 else { return key }
        var calendar = calendar
        if calendar.locale == nil { calendar.locale = Locale.current }
        let components = DateComponents(year: numbers[0], month: numbers[1], day: numbers[2])
        guard let date = calendar.date(from: components) else { return key }

        let age = calendar.dateComponents([.day], from: date, to: calendar.startOfDay(for: now)).day ?? 0
        switch age {
        case 0:
            return "Today"
        case 1:
            return "Yesterday"
        case 2 ... 6:
            let weekday = calendar.component(.weekday, from: date)
            return calendar.weekdaySymbols[weekday - 1]
        default:
            let month = calendar.shortMonthSymbols[numbers[1] - 1]
            let thisYear = calendar.component(.year, from: now)
            return numbers[0] == thisYear ? "\(month) \(numbers[2])" : "\(month) \(numbers[2]), \(numbers[0])"
        }
    }
}
