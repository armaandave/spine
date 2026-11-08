import CoreGraphics
import Foundation

/// A favorite song or episode line: a glyph and the name.
nonisolated struct FeedFavorite: Equatable, Sendable {
    let glyph: String
    let text: String
}

/// One line under a card's headline. Each kind has a fixed height and the gap above it, so a card's text column is
/// laid out from numbers and never measured.
nonisolated enum FeedDetailRow: Equatable, Sendable {
    /// The "Contains spoilers" chip, which hides the review's text.
    case spoilers
    case review(text: String, lines: Int)
    case favorite(glyph: String, text: String)
    case quiet(text: String, lines: Int)

    /// The row's own height: the chip 22 (a pill's height), a review line 19, a favorite or quiet line 18.
    var height: CGFloat {
        switch self {
        case .spoilers: 22
        case .review(_, let lines): CGFloat(lines) * 19
        case .favorite: 18
        case .quiet(_, let lines): CGFloat(lines) * 18
        }
    }

    /// The gap above the row.
    var topMargin: CGFloat {
        switch self {
        case .spoilers, .favorite, .quiet: 3
        case .review: 4
        }
    }

    var lines: Int {
        switch self {
        case .review(_, let lines), .quiet(_, let lines): lines
        case .spoilers, .favorite: 1
        }
    }

    /// The same row cut to `lines` (a row of one line stays as it is).
    fileprivate func limited(to lines: Int) -> FeedDetailRow {
        switch self {
        case .review(let text, _): .review(text: text, lines: lines)
        case .quiet(let text, _): .quiet(text: text, lines: lines)
        case .spoilers, .favorite: self
        }
    }

    /// For VoiceOver. The chip says there are spoilers and never the review.
    var accessibilityText: String {
        switch self {
        case .spoilers: "Contains spoilers"
        case .review(let text, _): text
        case .favorite(_, let text): "Favorite: \(text)"
        case .quiet(let text, _): text
        }
    }
}

/// Which lines go under a card's headline, always inside the card's 115 pt: at most one under a headline and two
/// without one, and none under a headline when a meter takes the bottom. The same order as the mock's card():
/// spoilers chip, then review, then favorite, then the quiet line.
nonisolated enum FeedDetailPlanner {
    /// Where the rows start (the end of the headline row, or of the title line without a headline), measured from
    /// the card's top: 9 top + 22 line one + 3 + 21 title (+ 2 + 20 headline).
    static func rowsStart(hasHeadline: Bool) -> CGFloat { hasHeadline ? 77 : 55 }

    /// The lowest the rows may reach. A meter's drawing ends 15 above the bottom (100) and a window meter, the
    /// tallest, starts at 83, so a card with a meter ends its rows by 81. Without one the rows end where the counts
    /// end, 12 above the bottom (103): the last row shares that band with the counts and stops short of them.
    static func rowsLimit(hasMeter: Bool) -> CGFloat { hasMeter ? 81 : 103 }

    /// Where the rows end, from the card's top.
    static func bottom(of rows: [FeedDetailRow], hasHeadline: Bool) -> CGFloat {
        rows.reduce(rowsStart(hasHeadline: hasHeadline)) { $0 + $1.topMargin + $1.height }
    }

    /// - Parameters:
    ///   - spoilers: the log's contains_spoilers flag. The chip shows only when there is review text to hide.
    ///   - review: the review's text, else the review title's.
    ///   - quietLines: how many lines the quiet text may take (2 for a Planning day's names).
    /// A row that does not fit is cut to one line and then dropped, lowest priority first, never overlapped. A chip
    /// that cannot fit beside a meter gives no rows: showing the review in its place would give the spoiler away.
    static func rows(
        hasMeter: Bool,
        hasHeadline: Bool,
        spoilers: Bool,
        review: String?,
        favorite: FeedFavorite?,
        quiet: String?,
        quietLines: Int = 1
    ) -> [FeedDetailRow] {
        let review = clean(review)
        let quiet = clean(quiet)
        let lines = max(1, quietLines)

        var candidates: [FeedDetailRow]
        if spoilers, review != nil {
            candidates = [.spoilers]
        } else if hasMeter {
            if let review {
                candidates = [.review(text: review, lines: 1)]
            } else if let favorite {
                candidates = [.favorite(glyph: favorite.glyph, text: favorite.text)]
            } else if let quiet {
                candidates = [.quiet(text: quiet, lines: lines)]
            } else {
                candidates = []
            }
        } else if let review {
            candidates = [.review(text: review, lines: favorite == nil ? 2 : 1)]
            if let favorite { candidates.append(.favorite(glyph: favorite.glyph, text: favorite.text)) }
        } else {
            candidates = []
            if let favorite { candidates.append(.favorite(glyph: favorite.glyph, text: favorite.text)) }
            if let quiet { candidates.append(.quiet(text: quiet, lines: lines)) }
        }

        let limit = rowsLimit(hasMeter: hasMeter)
        while let last = candidates.last, bottom(of: candidates, hasHeadline: hasHeadline) > limit {
            if last.lines > 1 {
                candidates[candidates.count - 1] = last.limited(to: last.lines - 1)
            } else {
                candidates.removeLast()
            }
        }
        return candidates
    }

    private static func clean(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else { return nil }
        return trimmed
    }
}
