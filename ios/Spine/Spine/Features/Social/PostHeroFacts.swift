import Foundation

/// What a post page's header says under the title: who made the title ("Directed by Celine Song") on one line, and its
/// year with how long it is ("2023 · 1h 46m") on the next. Games say no maker yet.
enum PostHeroFacts {
    /// The words before the names, by kind of title.
    enum Role: Equatable {
        case directed
        case created
        case animated
        case written
        case published
        case album
        case ep
        case single
    }

    /// Who made a title: one name, two joined ("Alan Moore & Dave Gibbons"), or the first and how many more.
    struct Maker: Equatable {
        let role: Role
        let name: String
        let more: Int
    }

    enum Unit: Equatable {
        case seasons
        case episodes
        case chapters
        case pages
        case issues
        case songs
    }

    /// How long a title is.
    enum Length: Equatable {
        /// A film's runtime as its source writes it ("1h 46m").
        case runtime(String)
        /// A count, and whether the title is still coming out ("1,160 chapters so far").
        case count(Int, Unit, soFar: Bool)
        /// An album's songs and minutes; either may be unknown, never both.
        case album(songs: Int?, minutes: Int?)
        /// A game's main story, in minutes.
        case timeToBeat(minutes: Int)
    }

    /// The two lines under the title. `lead` is the year, or a day's episode tag in its place ("S2 E7"); a day's page
    /// says no length, since the post is about that day.
    struct Lines: Equatable {
        var maker: Maker?
        var lead: String?
        var length: Length?
    }

    static func lines(displayTitle: String, detail: MediaDetail?, tag: String? = nil) -> Lines {
        let madeBy = maker(for: detail)
        if let tag = tag?.trimmingCharacters(in: .whitespacesAndNewlines), !tag.isEmpty {
            return Lines(maker: madeBy, lead: tag, length: nil)
        }
        return Lines(maker: madeBy, lead: year(displayTitle: displayTitle, detail: detail), length: length(for: detail))
    }

    /// The year: from a "(2000)" at the end of the title, else the release date's.
    static func year(displayTitle: String, detail: MediaDetail?) -> String? {
        PostHero.titleParts(displayTitle).year ?? DiaryLogFormat.year(detail?.releaseDate)
    }

    // MARK: Maker

    static func maker(for detail: MediaDetail?) -> Maker? {
        guard let detail else { return nil }
        let details = detail.details ?? [:]
        switch detail.ref.mediaType {
        case "movie":
            return maker(.directed, names(details["directors"]) + names(details["director"]))
        case "tv", "season":
            return maker(.created, names(details["creators"]) + names(details["creator"]))
        case "anime":
            return maker(.animated, names(details["studios"]))
        case "manga":
            let authors = names(details["authors"])
            return maker(.written, authors.isEmpty ? (detail.crew ?? []).prefix(1).map(\.name) : authors)
        case "book":
            return maker(.written, names(details["authors"]))
        case "comic":
            return maker(.published, names(details["publisher"]))
        case "music":
            return musicMaker(detail.music)
        default:
            return nil
        }
    }

    /// Music credits its artists as one name, joined as the source joins them ("Future & Metro Boomin"), so it is never
    /// "+1". The word follows the release: an EP, a single, else an album.
    private static func musicMaker(_ music: MusicDetail?) -> Maker? {
        guard let music else { return nil }
        let credit = music.artistCredit
            .map { $0.name + $0.joinPhrase }
            .joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let role: Role = switch (music.primaryType ?? "").lowercased() {
        case "ep": .ep
        case "single": .single
        default: .album
        }
        return maker(role, [credit])
    }

    /// One maker: their name. Two: both. Three or more: the first and how many more.
    private static func maker(_ role: Role, _ names: [String]) -> Maker? {
        var shown: [String] = []
        for name in names {
            let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty, !shown.contains(name) { shown.append(name) }
        }
        switch shown.count {
        case 0: return nil
        case 1: return Maker(role: role, name: shown[0], more: 0)
        case 2: return Maker(role: role, name: "\(shown[0]) & \(shown[1])", more: 0)
        default: return Maker(role: role, name: shown[0], more: shown.count - 1)
        }
    }

    // MARK: Length

    static func length(for detail: MediaDetail?) -> Length? {
        guard let detail else { return nil }
        let details = detail.details ?? [:]
        let stillComingOut = isStillComingOut(details["status"])
        switch detail.ref.mediaType {
        case "movie":
            return runtime(details["runtime"])
        case "tv":
            let seasons = count(details["seasons"])
            let episodes = count(details["episodes"])
            // "1 season" says nothing: a show with one gives its episodes.
            if let seasons, seasons > 1 { return .count(seasons, .seasons, soFar: stillComingOut) }
            return episodes.map { .count($0, .episodes, soFar: stillComingOut) }
        case "season":
            return count(details["episodes"]).map { .count($0, .episodes, soFar: false) }
        case "anime":
            // An anime's count is the whole run, even while it airs. A one-episode anime is a film: its runtime.
            let episodes = count(details["episodes"])
            if detail.userState?.anime?.single == true || episodes == 1 { return runtime(details["runtime"]) }
            return episodes.map { .count($0, .episodes, soFar: false) }
        case "manga":
            if let chapters = count(details["number_of_chapters"]) {
                return .count(chapters, .chapters, soFar: stillComingOut)
            }
            guard stillComingOut, let latest = count(details["latest_chapter_translated"]) else { return nil }
            return .count(latest, .chapters, soFar: true)
        case "book":
            return (count(details["number_of_pages"]) ?? count(details["pages"])).map { .count($0, .pages, soFar: false) }
        case "comic":
            return count(details["issues_count"]).map { .count($0, .issues, soFar: false) }
        case "music":
            return albumLength(detail.music?.representativeRelease)
        case "game":
            guard case let .object(times) = details["time_to_beat"], let seconds = number(times["normally"]),
                  seconds > 0
            else { return nil }
            return .timeToBeat(minutes: max(1, Int((seconds / 60).rounded())))
        default:
            return nil
        }
    }

    /// Songs and minutes. Minutes only when every song has a length, so a gap never shortens the album.
    private static func albumLength(_ release: MusicRepresentativeRelease?) -> Length? {
        guard let release else { return nil }
        let songs = release.trackCount > 0 ? release.trackCount : nil
        let tracks = release.media.flatMap(\.tracks)
        let lengths = tracks.compactMap(\.lengthMs).filter { $0 > 0 }
        let minutes = !tracks.isEmpty && lengths.count == tracks.count
            ? Int((Double(lengths.reduce(0, +)) / 60_000).rounded())
            : nil
        let shownMinutes = minutes.flatMap { $0 > 0 ? $0 : nil }
        guard songs != nil || shownMinutes != nil else { return nil }
        return .album(songs: songs, minutes: shownMinutes)
    }

    /// A show still airing, a manga still publishing or on a break.
    private static func isStillComingOut(_ value: JSONValue?) -> Bool {
        guard case let .string(text) = value else { return false }
        let status = text.lowercased()
        return ["returning series", "in production", "publishing", "ongoing", "on hiatus"].contains { status.contains($0) }
    }

    // MARK: Values

    /// Names from a list of people or companies (`{"name": ...}` or plain strings), or a single one.
    private static func names(_ value: JSONValue?) -> [String] {
        switch value {
        case let .string(name):
            [name]
        case let .object(object):
            object["name"]?.stringValue.map { [$0] } ?? []
        case let .array(values):
            values.flatMap { names($0) }
        default:
            []
        }
    }

    private static func runtime(_ value: JSONValue?) -> Length? {
        guard case let .string(text) = value else { return nil }
        let runtime = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return runtime.isEmpty ? nil : .runtime(runtime)
    }

    private static func count(_ value: JSONValue?) -> Int? {
        guard let number = number(value), number >= 1 else { return nil }
        return Int(number)
    }

    private static func number(_ value: JSONValue?) -> Double? {
        switch value {
        case let .number(number): number
        case let .string(text): Double(text.trimmingCharacters(in: .whitespacesAndNewlines))
        default: nil
        }
    }
}

// MARK: Words

extension PostHeroFacts {
    static func words(for role: Role) -> String {
        switch role {
        case .directed: "Directed by"
        case .created: "Created by"
        case .animated: "Animated by"
        case .written: "Written by"
        case .published: "Published by"
        case .album: "Album by"
        case .ep: "EP by"
        case .single: "Single by"
        }
    }

    static func words(for maker: Maker) -> String {
        maker.more > 0 ? "\(maker.name) +\(maker.more)" : maker.name
    }

    static func words(for length: Length) -> String {
        switch length {
        case let .runtime(runtime):
            return runtime
        case let .count(count, unit, soFar):
            let counted = "\(count.formatted()) \(words(for: unit, count: count))"
            return soFar ? "\(counted) so far" : counted
        case let .album(songs, minutes):
            return [
                songs.map { "\($0.formatted()) \($0 == 1 ? "song" : "songs")" },
                minutes.map(duration),
            ]
            .compactMap { $0 }
            .joined(separator: " · ")
        case let .timeToBeat(minutes):
            return minutes >= 60 ? "\(Int((Double(minutes) / 60).rounded()))h to beat" : "\(minutes)m to beat"
        }
    }

    private static func words(for unit: Unit, count: Int) -> String {
        let one = count == 1
        return switch unit {
        case .seasons: one ? "season" : "seasons"
        case .episodes: one ? "episode" : "episodes"
        case .chapters: one ? "chapter" : "chapters"
        case .pages: one ? "page" : "pages"
        case .issues: one ? "issue" : "issues"
        case .songs: one ? "song" : "songs"
        }
    }

    /// "1h 19m", "48m": a film's runtime's own form.
    private static func duration(_ minutes: Int) -> String {
        let (hours, rest) = minutes.quotientAndRemainder(dividingBy: 60)
        return hours > 0 ? "\(hours)h \(rest)m" : "\(rest)m"
    }
}
