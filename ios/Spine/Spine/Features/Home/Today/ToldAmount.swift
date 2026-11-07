import Foundation

/// The unit of what a day told. The server's `unit` says minutes for TV, anime, games, movies and albums, so it is
/// never used to pick words: the count comes from the label ("3 episodes").
nonisolated enum TodayUnit: Equatable, Sendable {
    case episodes
    case chapters
    case issues
    case pages
    case percent
    case minutes
}

/// What was done today on one title: an amount in a unit.
nonisolated struct TodayPart: Equatable, Sendable {
    var unit: TodayUnit
    var amount: Double
}

/// Reads a progress day's amount from the server's own words.
nonisolated enum ToldAmount {
    /// - Parameters:
    ///   - label: the day's words, "3 episodes", "86 pages", "15%", "2h 30m", "Started".
    ///   - unit: the row's `unit`, matched by prefix because the server sends minutes, pages, chapters and issues
    ///     and tests have used singulars.
    ///   - amount: the row's number. Minutes on TV, anime, games, movies and albums, so it counts only when `unit`
    ///     says minutes.
    ///   - percent: a percent told with no length.
    /// - Returns: nil for "Started", a missing label (unless `percent` is told) and words it doesn't know.
    static func parse(label: String?, unit: String?, amount: Double?, percent: Double?) -> TodayPart? {
        let text = label?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if text.lowercased().hasPrefix("started") { return nil }

        let told = text.isEmpty ? nil : parse(text, unit: unit)
        if let told, told.unit != .percent, told.unit != .minutes { return positive(told) }
        if let percent { return positive(TodayPart(unit: .percent, amount: percent)) }
        guard let told else { return nil }
        if told.unit == .minutes, let amount, hasPrefix(unit, "minute") {
            return positive(TodayPart(unit: .minutes, amount: amount))
        }
        return positive(told)
    }

    private static func positive(_ part: TodayPart) -> TodayPart? {
        part.amount.isFinite && part.amount > 0 ? part : nil
    }

    private static func hasPrefix(_ value: String?, _ prefix: String) -> Bool {
        value?.trimmingCharacters(in: .whitespaces).lowercased().hasPrefix(prefix) ?? false
    }

    /// "<number><word>" pairs, all of the text or nothing.
    private static func parse(_ text: String, unit: String?) -> TodayPart? {
        var pairs: [(number: Double, word: String)] = []
        let characters = Array(text.lowercased())
        var index = 0
        if index < characters.count, characters[index] == "+" { index += 1 }

        while index < characters.count {
            while index < characters.count, characters[index] == " " { index += 1 }
            var digits = ""
            while index < characters.count,
                  characters[index].isNumber || characters[index] == "," || characters[index] == "." {
                if characters[index] != "," { digits.append(characters[index]) }
                index += 1
            }
            guard let number = Double(digits) else { return nil }
            while index < characters.count, characters[index] == " " { index += 1 }
            var word = ""
            while index < characters.count, characters[index].isLetter || characters[index] == "%" {
                word.append(characters[index])
                index += 1
            }
            pairs.append((number, word))
        }
        guard let first = pairs.first else { return nil }

        if pairs.count == 1 {
            let word = first.word
            if word == "%" { return TodayPart(unit: .percent, amount: first.number) }
            if let counted = countedUnit(word) { return TodayPart(unit: counted, amount: first.number) }
            if word.isEmpty, let counted = countedUnit(unit?.trimmingCharacters(in: .whitespaces).lowercased() ?? "") {
                return TodayPart(unit: counted, amount: first.number)
            }
        }

        var minutes = 0.0
        for pair in pairs {
            if hourWords.contains(pair.word) {
                minutes += pair.number * 60
            } else if minuteWords.contains(pair.word) {
                minutes += pair.number
            } else {
                return nil
            }
        }
        return TodayPart(unit: .minutes, amount: minutes)
    }

    private static let hourWords: Set<String> = ["h", "hr", "hrs", "hour", "hours"]
    private static let minuteWords: Set<String> = ["m", "min", "mins", "minute", "minutes"]

    private static func countedUnit(_ word: String) -> TodayUnit? {
        if word.hasPrefix("episode") { return .episodes }
        if word.hasPrefix("chapter") { return .chapters }
        if word.hasPrefix("issue") { return .issues }
        if word.hasPrefix("page") { return .pages }
        return nil
    }
}
