import Foundation

// Activity Phase 5 (docs/activity-contract.md, AC-8 and AC-9): the Planning row, and fixing or taking back a day's
// progress. Every field decodes optionally, so a server that sends none of them costs the screens nothing.

/// A day of Planning adds, as the row addresses it: `object.id`, and the one type the titles share, which the titles
/// screen sends back so a filtered row opens the same titles it counted.
struct PlanningRef: Hashable, Identifiable {
    let id: Int
    let mediaType: String?
}

/// The titles a Planning row counted, oldest add first (`GET /planning/<id>/`).
struct PlanningDay: Decodable {
    let actor: UserSummary?
    let consumedAt: String?
    let summary: String?
    let count: Int?
    let results: [MediaSummary]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        actor = try container.decodeIfPresent(UserSummary.self, forKey: .actor)
        consumedAt = try container.decodeIfPresent(String.self, forKey: .consumedAt)
        summary = try container.decodeIfPresent(String.self, forKey: .summary)
        count = try container.decodeIfPresent(Int.self, forKey: .count)
        results = try container.decodeIfPresent([MediaSummary].self, forKey: .results) ?? []
    }

    private enum CodingKeys: String, CodingKey { case actor, consumedAt, summary, count, results }
}

extension ActivityItem {
    /// Set on a Planning row only. A Planning row has no post page, so `post` stays nil.
    var planning: PlanningRef? {
        guard object.type == "planning" else { return nil }
        return PlanningRef(id: object.id, mediaType: object.mediaType)
    }
}

enum PlanningWords {
    /// "Added 3 shows to Planning": the server's line, capitalized. A line it left out is built from the count and
    /// the type, so an unexpected shape still reads as words.
    static func headline(for activity: ActivityItem) -> String {
        if let summary = ActivityFeedPresentation.clean(activity.object.summary) {
            return summary.prefix(1).uppercased() + summary.dropFirst()
        }
        let count = activity.object.count ?? 0
        let noun = noun(for: activity.object.mediaType, count: count)
        return count > 0 ? "Added \(count) \(noun) to Planning" : "Added titles to Planning"
    }

    /// The titles under the count: "Band of Brothers, The Wire, Chernobyl", cut where the line ends. Nil when the
    /// server sent no names (an older server), so the row then shows only its count.
    static func names(for activity: ActivityItem) -> String? {
        let names = (activity.object.titles ?? []).compactMap { ActivityFeedPresentation.clean($0) }
        return names.isEmpty ? nil : names.joined(separator: ", ")
    }

    private static func noun(for mediaType: String?, count: Int) -> String {
        let plural = count != 1
        switch mediaType {
        case "movie": return plural ? "movies" : "movie"
        case "tv": return plural ? "shows" : "show"
        case "anime": return "anime"
        case "manga": return "manga"
        case "game": return plural ? "games" : "game"
        case "book": return plural ? "books" : "book"
        case "comic": return plural ? "comics" : "comic"
        case "music": return plural ? "albums" : "album"
        default: return plural ? "titles" : "title"
        }
    }
}

/// What Edit changes on a day's progress page (AC-9). `mode` "save" is the newest row: the normal save again, on
/// `date`. "number" is an older row: a `PATCH` held between `min` and `max`, the saves either side.
struct ProgressEdit: Codable, Equatable {
    var mode: String? = nil
    var value: Decimal? = nil
    /// "page", "percent", "chapter", "issue" or "minute".
    var unit: String? = nil
    /// "pages", "percentage", "count" or "minutes".
    var kind: String? = nil
    var date: String? = nil
    var min: Decimal? = nil
    var max: Decimal? = nil

    var isSave: Bool { mode == "save" }
    var isNumber: Bool { mode == "number" }
}

/// `PATCH /progress/<id>/`: the number an older row's day should read. `unit` picks which number a game's day edits
/// when it saved both.
struct ProgressEditRequest: Encodable, Equatable {
    let value: Decimal
    let unit: String?
}

/// Which controls a progress page shows its viewer. The server decides (AC-9); the page holds no rule of its own.
struct ProgressRowControls: Equatable {
    let showsEdit: Bool
    let showsDelete: Bool

    var showsAny: Bool { showsEdit || showsDelete }

    init(showsEdit: Bool, showsDelete: Bool) {
        self.showsEdit = showsEdit
        self.showsDelete = showsDelete
    }

    init(activity: ActivityItem?) {
        let viewer = activity?.viewer
        showsEdit = viewer?.canEdit == true && viewer?.edit?.mode != nil
        showsDelete = viewer?.canDelete == true
    }
}

/// The words and checks of the sheet Edit opens: the shared progress form, with the row's number in it.
struct ProgressRowEditPresentation {
    let edit: ProgressEdit
    let mediaType: String

    var numberLabel: String {
        switch edit.unit {
        case "page": "Page"
        case "percent": mediaType == "game" ? "Percent complete" : "Progress"
        case "chapter": "Chapters"
        case "issue": "Issues"
        case "minute": "Minutes played"
        default: "Progress"
        }
    }

    var unitText: String {
        switch edit.unit {
        case "percent": "%"
        case "minute": "min"
        case "page", "chapter", "issue": edit.max.map { "of \(ProgressPercent.text($0))" } ?? ""
        default: ""
        }
    }

    var allowsDecimal: Bool { edit.unit == "percent" }

    /// The box holds digits only, plus a decimal point for a percent.
    func sanitized(_ raw: String) -> String {
        allowsDecimal ? ProgressPercent.sanitized(raw) : String(raw.filter(\.isNumber).prefix(6))
    }

    var initialText: String {
        edit.value.map(ProgressPercent.text) ?? ""
    }

    /// The number the box holds, once it is one the row can take.
    func value(from input: String) -> Decimal? {
        ProgressPercent.value(input)
    }

    /// What is wrong with the number, or nil. An older row stays between the saves either side of it.
    func validation(for input: String) -> String? {
        guard let value = value(from: input) else { return nil }
        if let low = edit.isNumber ? edit.min : nil, value < low {
            return "Enter \(ProgressPercent.text(low)) or more."
        }
        if let high = edit.max, value > high {
            return "Enter \(ProgressPercent.text(high)) or less."
        }
        return nil
    }

    /// A line under the number: where this row's neighbors leave it.
    var footer: String? {
        guard edit.isNumber, let low = edit.min, let high = edit.max else { return nil }
        return "Between \(ProgressPercent.text(low)) and \(ProgressPercent.text(high)), the saves either side of it."
    }

    /// A different number. A new day alone isn't a change: the server records a save only when the number moves.
    func canSave(input: String) -> Bool {
        guard let value = value(from: input), validation(for: input) == nil else { return false }
        return value != edit.value
    }

    /// The `PATCH` an older row sends.
    func patch(for input: String) -> ProgressEditRequest? {
        guard edit.isNumber, canSave(input: input), let value = value(from: input) else { return nil }
        return ProgressEditRequest(value: value, unit: edit.unit)
    }
}

/// The date row on a progress sheet: "Today", "Yesterday", or the day.
enum ProgressDateWords {
    static func text(
        for date: Date,
        now: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> String {
        if calendar.isDate(date, inSameDayAs: now) { return "Today" }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(date, inSameDayAs: yesterday) {
            return "Yesterday"
        }
        var style = Date.FormatStyle.dateTime.month(.abbreviated).day()
        if !calendar.isDate(date, equalTo: now, toGranularity: .year) {
            style = style.year()
        }
        style.calendar = calendar
        style.timeZone = calendar.timeZone
        return date.formatted(style)
    }
}

extension ProgressUpdateSaveRequest {
    /// The same save on the day the sheet's date row names, sent as `progressed_on`. Today sends none, so the server
    /// puts the save on its own today, as an older build's save did, not on the phone's calendar day.
    func dated(_ date: Date, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> ProgressUpdateSaveRequest {
        var request = self
        guard !calendar.isDate(date, inSameDayAs: now) else { return request }
        request.progressedOn = CalendarDateCodec.string(from: date, calendar: calendar)
        return request
    }
}
