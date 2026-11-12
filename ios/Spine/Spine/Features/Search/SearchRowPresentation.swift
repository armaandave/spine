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

    /// "Movie · 2024", "Album · 2021 · Hans Zimmer", "Anime · TV · 2023 · 28 episodes": the kind when the search
    /// covers every kind, then the year, then the result's own line. Neither the year nor the kind is said twice when
    /// that line has it too ("Toto · 1984 · Album").
    static func meta(mediaType: String, subtitle: String?, releaseDate: String?, showsKind: Bool) -> String? {
        let kind = TrendingTileModel.kind(of: mediaType)
        var own: String? = subtitle?
            .components(separatedBy: " · ")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && $0.caseInsensitiveCompare(kind ?? "") != .orderedSame }
            .joined(separator: " · ")
        if own?.isEmpty == true { own = nil }
        var year = TrendingTileModel.year(from: releaseDate)
        if let line = own, TrendingTileModel.year(from: line) == line {
            // A line that is only a year is the year.
            year = year ?? line
            own = nil
        }
        if let line = own, let shown = year, line.contains(shown) {
            year = nil
        }
        let parts = [showsKind ? kind : nil, year, own].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
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
