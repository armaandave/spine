import Foundation

/// What a feed card is about, which decides its headline and whether it can carry a meter.
nonisolated enum FeedCardKind: Equatable, Sendable {
    /// A diary log.
    case log
    /// A day's progress on a title.
    case progress
    /// A day that only moved a title's status (paused, stopped, picked back up): nothing lit.
    case status
    /// Starting a title.
    case start
    /// A day of Planning adds, shown as one card.
    case planning
    /// Anything else the feed sends.
    case other
}

/// The line under a card's title. A log shows only its marks; no card says "Finished", "Watched" or "Listened".
nonisolated enum FeedHeadline: Equatable, Sendable {
    /// A log's stars, heart and rewatch glyph. `repeatLabel` names the glyph for VoiceOver ("Rewatch", "Reread").
    case marks(starSymbols: [String], liked: Bool, rewatch: Bool, repeatLabel: String)
    /// A day's amount ("+3 episodes") in the card accent, then the muted real "where" when the friend's standing
    /// knows it, and the status pills. A card draws the pills when it has them, else the tail.
    case progress(amount: String, tail: String?, pills: [String])
    /// A day that was only a status move ("Paused", "Picked back up"), nothing lit: the same pills a progress day
    /// draws, then the muted "where" when the friend's standing knows it.
    case moves(pills: [String], tail: String?)
    /// The old-server fallback: a day's own words, and an activity row's action.
    case status(words: String, tail: String?)
    /// "Started".
    case started
}

/// What a card's artwork needs, so the view and the prefetcher request the same URLs.
nonisolated struct FeedCardArtSpec: Equatable, Sendable {
    /// The poster in the 60 x 90 slot (the 90 x 90 slot when `isSquare`). An episode row has no poster, so this
    /// is its still, aspect-filled.
    let posterURL: String?
    /// What the card's baked blurred background is made from: the same image as the poster.
    let backgroundURL: String?
    /// The poster's accent color, the first guess for the ink.
    let accentHex: String?
    /// The ink before the baked art is measured.
    let guessInk: ArtInk
    let isSquare: Bool
    /// A Planning card's fan: up to three posters of the titles added, once they have loaded. Empty until then.
    let planningPosterURLs: [String]
}

/// Everything a feed card says, worked out once and compared by value so a row skips drawing when nothing visible
/// changed.
struct FeedCardModel: Identifiable, Equatable {
    /// The activity item's id.
    let id: Int
    let kind: FeedCardKind
    let username: String
    let avatarURL: String?
    /// The card's day, shown after the username on line one: "Today", "Yesterday", "Friday", "Oct 21" or
    /// "Oct 21, 2025". Nil when the row has no date.
    let dayLabel: String?
    let title: String
    /// "S2 E7", "E7", "Season 2", "Planning".
    let tag: String?
    let headline: FeedHeadline?
    /// Zero, one or two lines under the headline (one when a meter takes the other).
    let details: [FeedDetailRow]
    let meter: ProgressMeterModel?
    /// Likes and comments, read-only. Nil on a Planning card, which has neither.
    let engagement: PostEngagement?
    let art: FeedCardArtSpec
    let accessibilityLabel: String
    let accessibilityHint: String
    let destination: ActivityDestination
}
