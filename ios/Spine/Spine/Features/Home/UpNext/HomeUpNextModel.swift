import Foundation
import Observation

/// What Home's Up next tile and page show: dated TV and anime episodes that haven't aired yet, soonest first
/// (docs/up-next-contract.md). The tile and the page read it in their own bodies, so a load redraws only them.
@MainActor
@Observable
final class HomeUpNextModel {
    private(set) var items: [UpNextItem] = []
    private(set) var isLoading = false
    /// Why the last read failed; a failed read keeps what was shown.
    private(set) var errorMessage: String?
    /// Whether a read has answered at least once.
    private(set) var hasLoaded = false

    /// Nil makes no read: a Home built without its extras.
    @ObservationIgnored private let repository: TrackingRepository?
    @ObservationIgnored private let onUnauthorized: () -> Void

    init(repository: TrackingRepository?, onUnauthorized: @escaping () -> Void) {
        self.repository = repository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        guard let repository else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let fresh = try await repository.upNext()
            guard !Task.isCancelled else { return }
            if fresh != items { items = fresh }
            errorMessage = nil
            hasLoaded = true
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
        }
    }
}

/// The words and dates Up next shows, worked out from the server's rows and today on the phone's calendar.
nonisolated enum UpNextPresentation {
    /// One month of the page: "November", or "January 2027" outside today's year.
    struct MonthSection: Identifiable, Equatable {
        let id: String
        let title: String
        let items: [UpNextItem]
    }

    /// What airs: "S1 E8", "Season 3 premiere", "Series premiere"; anime reads "Episode 1,152 · Anime" or
    /// "Premiere · Anime".
    static func line(for item: UpNextItem) -> String {
        let airing = item.airing
        if item.media.ref.mediaType == "anime" {
            if !airing.isSeriesPremiere, let episode = airing.episodeNumber {
                return "Episode \(episode.formatted()) · Anime"
            }
            return "Premiere · Anime"
        }
        if airing.isSeriesPremiere { return "Series premiere" }
        if airing.isSeasonPremiere, let season = airing.seasonNumber { return "Season \(season) premiere" }
        if let season = airing.seasonNumber, let episode = airing.episodeNumber { return "S\(season) E\(episode)" }
        if let season = airing.seasonNumber { return "Season \(season)" }
        return "New episode"
    }

    /// The air date as a day on `calendar`, or nil when it isn't "YYYY-MM-DD".
    static func day(of airDate: String, calendar: Calendar = .autoupdatingCurrent) -> Date? {
        let parts = airDate.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    /// Whole days from today to the air date; 0 is today.
    static func daysAway(_ airDate: String, today: Date, calendar: Calendar = .autoupdatingCurrent) -> Int? {
        guard let day = day(of: airDate, calendar: calendar) else { return nil }
        return calendar.dateComponents([.day], from: calendar.startOfDay(for: today), to: calendar.startOfDay(for: day)).day
    }

    /// "Today", "Tomorrow", "in 5 days", "in 1 week", "in 3 weeks", "in 4 months".
    static func relative(days: Int) -> String {
        switch days {
        case ...0:
            return "Today"
        case 1:
            return "Tomorrow"
        case 2 ..< 7:
            return "in \(days) days"
        case 7 ..< 14:
            return "in 1 week"
        case 14 ..< 60:
            return "in \(Int((Double(days) / 7).rounded())) weeks"
        default:
            return "in \(Int((Double(days) / 30.44).rounded())) months"
        }
    }

    /// The rows by month, in the server's order.
    static func monthSections(
        _ items: [UpNextItem],
        today: Date,
        calendar: Calendar = .autoupdatingCurrent
    ) -> [MonthSection] {
        let thisYear = calendar.component(.year, from: today)
        var order: [String] = []
        var grouped: [String: (title: String, items: [UpNextItem])] = [:]
        for item in items {
            let id: String
            let title: String
            if let day = day(of: item.airing.airDate, calendar: calendar) {
                let year = calendar.component(.year, from: day)
                let month = calendar.component(.month, from: day)
                let name = calendar.standaloneMonthSymbols[month - 1]
                id = String(format: "%04d-%02d", year, month)
                title = year == thisYear ? name : "\(name) \(year)"
            } else {
                id = "later"
                title = "Later"
            }
            if grouped[id] == nil {
                order.append(id)
                grouped[id] = (title, [])
            }
            grouped[id]?.items.append(item)
        }
        return order.compactMap { id in
            grouped[id].map { MonthSection(id: id, title: $0.title, items: $0.items) }
        }
    }

    /// "SUN" over "8": the row's date.
    static func badge(for airDate: String, calendar: Calendar = .autoupdatingCurrent) -> (weekday: String, day: String) {
        guard let day = day(of: airDate, calendar: calendar) else { return ("", "–") }
        let weekday = calendar.shortStandaloneWeekdaySymbols[calendar.component(.weekday, from: day) - 1]
        return (weekday.uppercased(), String(calendar.component(.day, from: day)))
    }

    /// When it airs, read aloud: "today", "tomorrow", "Friday", or "January 16".
    static func spokenWhen(_ airDate: String, today: Date, calendar: Calendar = .autoupdatingCurrent) -> String {
        guard let day = day(of: airDate, calendar: calendar),
              let days = daysAway(airDate, today: today, calendar: calendar) else { return airDate }
        if days <= 0 { return "today" }
        if days == 1 { return "tomorrow" }
        if days < 7 { return calendar.standaloneWeekdaySymbols[calendar.component(.weekday, from: day) - 1] }
        return "\(calendar.standaloneMonthSymbols[calendar.component(.month, from: day) - 1]) \(calendar.component(.day, from: day))"
    }

    /// "1 coming up", "5 coming up".
    static func countLine(_ count: Int) -> String {
        "\(count) coming up"
    }

    /// The tile's VoiceOver value: the first few titles and when.
    static func tileValue(_ items: [UpNextItem], today: Date, calendar: Calendar = .autoupdatingCurrent) -> String {
        let named = items.prefix(5).map { "\($0.media.displayTitle) \(spokenWhen($0.airing.airDate, today: today, calendar: calendar))" }
        return ([countLine(items.count)] + named).joined(separator: ", ")
    }
}
