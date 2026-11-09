import Foundation

/// What a day's progress page says about the day, taken from the same `FeedCardModel` the feed card is drawn from, so
/// the page and its card cannot drift: title and tag, the amount, the status pills, where the person stands, and the
/// bar. The page is roomier than the card, so it shows the standing beside the pills too, where the card keeps one.
struct ProgressPageContent: Equatable {
    /// The show's or title's name, never an episode's.
    let title: String
    /// "S2 E7", "E7", "Season 2", "Specials".
    let tag: String?
    /// "+20 pages", "+3 episodes", "+1h 30m", "+25%": drawn in the poster's accent.
    let amount: String?
    /// "Started": drawn in the accent too.
    let isStart: Bool
    /// An old server's own words for the day, drawn in plain white.
    let words: String?
    /// "Paused", "Picked back up", "Stopped", in the order sent.
    let pills: [String]
    /// "p. 212", "total 71%", "63/70", "S2 · 7/10", "14h": the muted where.
    let standing: String?
    let meter: ProgressMeterModel?
    let art: FeedCardArtSpec

    init(model: FeedCardModel) {
        title = model.title
        tag = model.tag
        art = model.art
        meter = model.meter
        var amount: String?
        var isStart = false
        var words: String?
        var pills: [String] = []
        var standing: String?
        switch model.headline {
        case let .progress(headlineAmount, tail, headlinePills):
            amount = headlineAmount
            pills = headlinePills
            standing = tail
        case let .moves(headlinePills, tail):
            pills = headlinePills
            standing = tail
        case let .status(headlineWords, tail):
            words = headlineWords
            standing = tail
        case .started:
            isStart = true
        case .marks, nil:
            break
        }
        self.amount = amount
        self.isStart = isStart
        self.words = words
        self.pills = pills
        self.standing = standing
    }

    /// Whether the page has anything to draw under the title.
    var hasHeadline: Bool {
        amount != nil || isStart || words != nil || !pills.isEmpty
    }

    /// The words VoiceOver says for the headline: the amount, the pills, then where the person stands.
    var spokenHeadline: String {
        var parts: [String] = []
        if let amount { parts.append(amount) }
        if isStart { parts.append("Started") }
        if let words { parts.append(words) }
        parts += pills
        if let standing { parts.append(FeedStandingText.spoken(standing)) }
        return parts.joined(separator: ", ")
    }

    // MARK: Which model

    /// The model a progress page draws.
    /// - Parameters:
    ///   - card: the feed card the page was opened from, when it was opened from one. It already knows whether it was
    ///     the newest card of its title and what the person's standing was, so it is used as it is.
    ///   - standing: where the person stands on the title now, loaded by the page itself.
    ///   - isNewestOfTitle: whether this day is the person's newest on the title (nil until known). A standing is
    ///     where the person is now, so an older day never gets one, as on the feed.
    static func model(
        for activity: ActivityItem,
        card: FeedCardModel?,
        standing: FeedStanding?,
        isNewestOfTitle: Bool?
    ) -> FeedCardModel {
        if isNewestOfTitle == true, let standing {
            return FeedCardPresenter.model(for: activity, standing: standing, isNewestOfTitle: true)
        }
        if let card { return card }
        return FeedCardPresenter.model(for: activity, standing: nil, isNewestOfTitle: false)
    }

    /// Whether a card already carries a standing (a meter or a muted where), so the page need not load one.
    static func hasStanding(_ card: FeedCardModel?) -> Bool {
        guard let card else { return false }
        if card.meter != nil { return true }
        return ProgressPageContent(model: card).standing != nil
    }

    /// Whether `activity` is the first progress day of its title among `items` (newest first), the rule the feed uses
    /// for a card's standing. False when it is not among them (an older day) or has no title.
    static func isNewestOfTitle(_ activity: ActivityItem, among items: [ActivityItem]) -> Bool {
        guard let ref = activity.media?.ref else { return false }
        let key = FeedEntries.titleKey(for: ref)
        let first = items.first {
            $0.object.type == "progress_day" && $0.media.map { FeedEntries.titleKey(for: $0.ref) } == key
        }
        return first?.object.id == activity.object.id
    }
}
