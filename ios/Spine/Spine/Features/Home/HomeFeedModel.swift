import Foundation
import Observation

/// The flat list of cards Home's feed draws, built once from what they depend on and
/// rebuilt only when one of those changes (a load, a next page, a like or comment count coming back from a post,
/// a Planning day arriving, a friend's standing arriving), never on a body pass.
///
/// `HomeFeedRows` is the only reader of `entries`, so a rebuild redraws the rows and nothing else. Entries are
/// equatable, so a rebuild that changes one card leaves every other card's view alone.
@MainActor
@Observable
final class HomeFeedModel {
    private(set) var entries: [FeedCardModel] = []

    let feed: ActivityFeedViewModel
    let planning: FeedPlanningStore
    /// Where each friend stands on a title. A provider that loads something later calls `rebuild()` when it arrives.
    @ObservationIgnored var standing: FeedStandingProviding
    /// Told after the entries were rebuilt because the feed's items changed (not for a Planning day or a standing),
    /// so what else is made from the items (Trending, the glow) can follow.
    @ObservationIgnored var onFeedChange: (() -> Void)?
    /// Told after a next page was added to the feed.
    @ObservationIgnored var onNextPage: (() -> Void)?

    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let calendar: () -> Calendar

    init(
        feed: ActivityFeedViewModel,
        planning: FeedPlanningStore,
        standing: FeedStandingProviding? = nil,
        now: @escaping () -> Date = Date.init,
        calendar: @escaping () -> Calendar = { .autoupdatingCurrent }
    ) {
        self.feed = feed
        self.planning = planning
        self.standing = standing ?? NoFeedStanding()
        self.now = now
        self.calendar = calendar
        feed.onItemsChange = { [weak self] in
            guard let self else { return }
            rebuild()
            onFeedChange?()
        }
        planning.onChange = { [weak self] in self?.rebuild() }
        rebuild()
    }

    /// Builds the list again from the feed's items, the loaded Planning days and the standings. Assigns only when
    /// something changed, so the rows are not asked to redraw for nothing.
    func rebuild() {
        let built = FeedEntries.make(
            items: feed.items,
            standing: standing,
            planning: planning.daysByObjectID(for: feed.items),
            now: now(),
            calendar: calendar()
        )
        if built != entries { entries = built }
    }

    /// The feed's current row for a card, looked up at tap time so a refreshed row never opens stale data.
    func item(forCardID id: Int) -> ActivityItem? {
        feed.items.first { $0.id == id }
    }

    /// The last card's row appeared: loads the next page, and tells `onNextPage` when it added rows.
    func loadNextPageIfNeeded(cardID: Int) async {
        let before = feed.items.count
        await feed.loadNextPageIfNeeded(itemID: cardID)
        if feed.items.count > before { onNextPage?() }
    }

    /// A Planning card came on screen: asks for the titles it counted (the store loads each day once).
    func cardAppeared(_ model: FeedCardModel) {
        guard model.kind == .planning, let item = item(forCardID: model.id) else { return }
        planning.load(for: item)
    }
}
