import Foundation

/// Where a friend stands on a title after a feed card's change: what the card's meter draws and the muted "where"
/// text after its amount. It is computed by something that knows the friend's library (WP17); the card logic only
/// draws what it is given. Any piece it cannot vouch for stays nil, and a standing without a total gives no meter.
nonisolated struct FeedStanding: Equatable, Sendable {
    let unit: MeterUnit
    /// Done before the card's change. Nil lights nothing.
    let from: Int?
    /// Done after it.
    let to: Int?
    /// The end of the run. Nil gives no meter (percent always runs to 100).
    let total: Int?
    /// "9 of 62 episodes", "p. 212", "now 71%".
    let whereText: String?

    init(unit: MeterUnit, from: Int?, to: Int?, total: Int?, whereText: String?) {
        self.unit = unit
        self.from = from
        self.to = to
        self.total = total
        self.whereText = whereText
    }
}

/// Answers `FeedStanding` for a feed card. `isNewestOfTitle` is true only for the first progress or status card of a
/// person's title in list order: older cards of the same title never get a standing (there is no walk-back).
@MainActor
protocol FeedStandingProviding {
    func standing(for item: ActivityItem, isNewestOfTitle: Bool) -> FeedStanding?
    /// Called with the items about to be shown so a provider can start whatever it loads lazily.
    func prepare(for items: [ActivityItem])
    /// The feed was loaded again (the first load, a pull to refresh): refresh whatever is wanted.
    func reload(for items: [ActivityItem])
    /// Told after new standings landed, so Home rebuilds its entries.
    func setOnChange(_ onChange: (() -> Void)?)
}

extension FeedStandingProviding {
    func reload(for items: [ActivityItem]) { prepare(for: items) }
    func setOnChange(_ onChange: (() -> Void)?) {}
}

/// No standings: cards show no meter and no "where". The default for a view model built to test the rail or the feed;
/// Home itself plugs in `FriendStandings`.
struct NoFeedStanding: FeedStandingProviding {
    func standing(for item: ActivityItem, isNewestOfTitle: Bool) -> FeedStanding? { nil }
    func prepare(for items: [ActivityItem]) {}
}
