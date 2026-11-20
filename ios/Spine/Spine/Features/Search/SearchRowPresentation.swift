import Foundation

/// What a search result row says beside its art.
enum SearchRowText {
    /// The title around the first place the search text appears, ignoring case and accents, so the row can set the
    /// matched part apart.
    struct TitleMatch: Equatable {
        let before: String
        let match: String
        let after: String
    }

    /// Where you stand on a result, in the fewest words.
    enum Standing: Equatable {
        /// Your stars ("4.5"), and whether you liked it.
        case rated(stars: String, liked: Bool)
        /// "Watching", "Reading", "Planning", "Finished", "Paused" or "Stopped".
        case status(String)
        /// A heart and nothing else.
        case liked
    }

    /// One way the line under a title can say who made it: a name, then "+N" for the others credited too.
    struct Credit: Equatable {
        let name: String
        let more: Int
    }

    /// Who made a result, in the ways its line can say it, fullest first; the row shows the first that fits. One
    /// maker: their full name. Two: both ("Alan Moore & Dave Gibbons"), else the first and "+1". Three or more: the
    /// first and "+N". Never first names alone.
    static func credits(_ makers: [String]?) -> [Credit] {
        let names = madeBy(makers)
        switch names.count {
        case 0: return []
        case 1: return [Credit(name: names[0], more: 0)]
        case 2: return [Credit(name: "\(names[0]) & \(names[1])", more: 0), Credit(name: names[0], more: 1)]
        default: return [Credit(name: names[0], more: names.count - 1)]
        }
    }

    /// The year a result came out: the server's, else its release date's, else its own line's when that's only a
    /// year (a title opened before the server sent years).
    static func year(of result: MediaSummary) -> String? {
        if let year = result.year, year > 0 { return String(year) }
        if let year = TrendingTileModel.year(from: result.releaseDate) { return year }
        guard let line = result.subtitle?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        return TrendingTileModel.year(from: line) == line ? line : nil
    }

    /// What VoiceOver reads for that line: the kind when the search covers every kind, everyone who made it, and the
    /// year ("Comic, Alan Moore and Dave Gibbons, 1986").
    static func spokenLine(mediaType: String, makers: [String]?, year: String?, showsKind: Bool) -> String? {
        let names = madeBy(makers)
        let parts = [
            showsKind ? TrendingTileModel.kind(of: mediaType) : nil,
            names.isEmpty ? nil : names.formatted(.list(type: .and)),
            year,
        ].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }

    private static func madeBy(_ makers: [String]?) -> [String] {
        var names: [String] = []
        for maker in makers ?? [] {
            let name = maker.trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty, !names.contains(name) { names.append(name) }
        }
        return names
    }

    static func match(in title: String, query: String) -> TitleMatch? {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty,
              let range = title.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive]) else {
            return nil
        }
        return TitleMatch(
            before: String(title[..<range.lowerBound]),
            match: String(title[range]),
            after: String(title[range.upperBound...])
        )
    }

    /// Your stars first (with your heart), else how far along you are, else a lone heart. Nothing for a title you
    /// haven't touched.
    static func standing(for state: UserMediaState?, mediaType: String) -> Standing? {
        guard let state else { return nil }
        if let stars = state.rating?.starCount, stars.ratingHalfSteps > 0 {
            return .rated(stars: stars, liked: state.hasLiked)
        }
        if let word = statusWord(state.status ?? state.tv?.status ?? state.anime?.status, mediaType: mediaType) {
            return .status(word)
        }
        return state.hasLiked ? .liked : nil
    }

    /// A status as the row says it: "In progress" is Watching, Reading, Playing or Listening by kind.
    static func statusWord(_ status: String?, mediaType: String) -> String? {
        switch status {
        case "In progress":
            switch mediaType {
            case "book", "manga", "comic": "Reading"
            case "game": "Playing"
            case "music": "Listening"
            default: "Watching"
            }
        case "Planning": "Planning"
        case "Completed": "Finished"
        case "Paused": "Paused"
        case "Dropped": "Stopped"
        default: nil
        }
    }

    /// "maya", "maya and theo", "maya, theo and jules", "maya, theo and 4 more".
    static func friendsLine(_ friends: MediaFriendsSummary) -> String? {
        let names = friends.people.map(\.username)
        guard friends.count > 0 else { return nil }
        if names.isEmpty {
            return friends.count == 1 ? "1 friend" : "\(friends.count) friends"
        }
        if friends.count == names.count, names.count <= 3 {
            return names.count == 1 ? names[0] : "\(names.dropLast().joined(separator: ", ")) and \(names[names.count - 1])"
        }
        let shown = Array(names.prefix(2))
        return "\(shown.joined(separator: ", ")) and \(friends.count - shown.count) more"
    }
}
