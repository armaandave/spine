import Foundation

/// The number a Stats tap opens the Activity list with: the `counts` of the request (activity contract, "The
/// Activity list"). A list with no count shows every day's row.
enum ActivityListCounts: String, Hashable {
    case titles
    case days
    case weeks
    case multiTitleDays = "multi_title_days"
    case episodes
    case hours
}

/// What an Activity list asks for: one type, a range of days on the person's calendar, a number to add up. Every
/// page of the list sends it again, because a next page is rebuilt from `page_size` and `cursor` only.
struct ActivityListFilter: Hashable {
    /// A show's seasons and episodes are the `tv` type.
    var mediaType: String?
    /// "YYYY-MM-DD", inclusive. All Time sends neither date.
    var startDate: String?
    var endDate: String?
    var counts: ActivityListCounts?

    /// The profile's own Activity row: every type, every day.
    static let everything = ActivityListFilter()

    /// The list's request. `view=list` makes even a plain request read as the Diary does, with no review text.
    var queryItems: [URLQueryItem] {
        var items = [URLQueryItem(name: "view", value: "list")]
        if let mediaType {
            items.append(URLQueryItem(name: "media_type", value: mediaType))
        }
        if let startDate {
            items.append(URLQueryItem(name: "start_date", value: startDate))
        }
        if let endDate {
            items.append(URLQueryItem(name: "end_date", value: endDate))
        }
        if let counts {
            items.append(URLQueryItem(name: "counts", value: counts.rawValue))
        }
        return items
    }
}

/// The type's number as Stats prints it (`total` beside the list's rows). Every field is optional so a shape the app
/// doesn't know never costs the list its rows.
struct ActivityListTotal: Codable, Hashable {
    var value: Double?
    var unit: String?
    var known: Double?
    var total: Double?
    var approximate: Bool?
    var timeLabel: String?

    private enum CodingKeys: String, CodingKey { case value, unit, known, total, approximate, timeLabel }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        value = (try? container.decodeIfPresent(Double.self, forKey: .value)) ?? nil
        unit = (try? container.decodeIfPresent(String.self, forKey: .unit)) ?? nil
        known = (try? container.decodeIfPresent(Double.self, forKey: .known)) ?? nil
        total = (try? container.decodeIfPresent(Double.self, forKey: .total)) ?? nil
        approximate = (try? container.decodeIfPresent(Bool.self, forKey: .approximate)) ?? nil
        timeLabel = (try? container.decodeIfPresent(String.self, forKey: .timeLabel)) ?? nil
    }
}

/// What the number includes with no date, on All Time's first page: "Includes 200 books and 48,829 pages with no
/// date". The server writes the line; the rest is for tests and accessibility.
struct ActivityUndated: Codable, Hashable {
    var amount: Double?
    var unit: String?
    var titles: Int?
    var told: Double?
    var label: String?

    private enum CodingKeys: String, CodingKey { case amount, unit, titles, told, label }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        amount = (try? container.decodeIfPresent(Double.self, forKey: .amount)) ?? nil
        unit = (try? container.decodeIfPresent(String.self, forKey: .unit)) ?? nil
        titles = (try? container.decodeIfPresent(Int.self, forKey: .titles)) ?? nil
        told = (try? container.decodeIfPresent(Double.self, forKey: .told)) ?? nil
        label = (try? container.decodeIfPresent(String.self, forKey: .label)) ?? nil
    }
}

/// The words on an Activity list: its title, its header and what each row says under its title.
enum ActivityListPresentation {
    /// "Activity" for the profile's row, else the scope a number named: "Books · 2026", "All Media · All Time".
    static func title(for filter: ActivityListFilter, period: StatsPeriod?) -> String {
        guard let period else { return "Activity" }
        return StatsPageSection.scopeTitle(mediaType: filter.mediaType, period: period)
    }

    /// The server's line for the number tapped, or nil for a plain list or an older server.
    static func topLine(_ text: String?) -> String? {
        ActivityFeedPresentation.clean(text)
    }

    /// "Includes 1 book and 350 pages with no date", shown with or without rows.
    static func undatedLine(_ undated: ActivityUndated?) -> String? {
        ActivityFeedPresentation.clean(undated?.label)
    }

    /// A status-only day: a pause, a stop or a pick-up with nothing told, which the server sends with no label.
    /// A rail of recent titles skips it; a list shows it.
    static func isStatusOnly(_ activity: ActivityItem) -> Bool {
        guard case .progress = activity.post else { return false }
        return ActivityFeedPresentation.clean(activity.object.label) == nil && !(activity.object.moves ?? []).isEmpty
    }

    /// The line under a row's title, in pieces so the row can set marks beside them. A progress row reads the
    /// server's words ("Read 40 pages, paused"), a log what it added ("+200 pages"); either ends with its time
    /// ("2h 15m"). No Swift time formatter: the server's `time_label` is the only spelling.
    static func caption(for activity: ActivityItem) -> String? {
        let words: String?
        switch activity.post {
        case .progress:
            words = ActivityFeedPresentation.progressHeadline(for: activity)
        case .log:
            words = ActivityFeedPresentation.clean(activity.object.label).map { "+\($0)" }
        case nil:
            words = nil
        }
        let parts = [words, ActivityFeedPresentation.clean(activity.object.timeLabel)].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The months of a list, newest first, each with its rows in the order the server sent them.
    static func monthSections(from items: [ActivityItem]) -> [ActivityMonthSection] {
        var indexes: [String: Int] = [:]
        return items.reduce(into: []) { sections, item in
            guard item.media != nil, item.post != nil else { return }
            let raw = item.consumedAt ?? item.createdAt
            let id = DiaryDateFormatter.monthID(from: raw) ?? "undated"
            if let index = indexes[id] {
                sections[index].items.append(item)
            } else {
                indexes[id] = sections.count
                sections.append(ActivityMonthSection(
                    id: id,
                    title: DiaryDateFormatter.monthHeader(from: raw) ?? "Undated",
                    items: [item]
                ))
            }
        }
    }
}

struct ActivityMonthSection: Identifiable {
    let id: String
    let title: String
    var items: [ActivityItem]
}
