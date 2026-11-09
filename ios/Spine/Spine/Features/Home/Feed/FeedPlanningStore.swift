import Foundation
import Observation

/// Loads the titles behind a Planning card, lazily and only as cards ask. A Planning row says "6 movies" and shows its
/// first title's poster; once `GET /planning/<id>/` answers, the card fans up to three posters and names the titles.
///
/// Rules, so scrolling a feed full of Planning days costs a few requests at most:
/// - a day is loaded once per row id and count (the count grows when more titles are added that day);
/// - at most two requests run at once, the rest wait in order;
/// - a failure is remembered for 60 seconds, then the next card appearance may try again;
/// - a card scrolling off does not cancel a request that has started.
@MainActor
@Observable
final class FeedPlanningStore {
    static let maxInFlight = 2
    static let failureBackoff: TimeInterval = 60

    /// Loaded days by `key(for:)`.
    private(set) var days: [String: PlanningDay] = [:]

    /// Called after a day arrives, so the host can rebuild the feed's entries.
    @ObservationIgnored var onChange: (() -> Void)?

    @ObservationIgnored private let activityRepository: ActivityRepository
    @ObservationIgnored private let onUnauthorized: () -> Void
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var waiting: [Pending] = []
    @ObservationIgnored private var waitingKeys = Set<String>()
    @ObservationIgnored private var running = Set<String>()
    @ObservationIgnored private var failedAt: [String: Date] = [:]

    private struct Pending {
        let key: String
        let id: Int
        let mediaType: String?
    }

    init(
        activityRepository: ActivityRepository,
        onUnauthorized: @escaping () -> Void,
        now: @escaping () -> Date = Date.init
    ) {
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
        self.now = now
    }

    /// "<object id>-<count>", or nil for a row that is not a Planning day.
    static func key(for item: ActivityItem) -> String? {
        guard item.planning != nil else { return nil }
        return "\(item.object.id)-\(item.object.count ?? 0)"
    }

    /// The loaded titles of a Planning row.
    func day(for item: ActivityItem) -> PlanningDay? {
        Self.key(for: item).flatMap { days[$0] }
    }

    /// Loaded days by the row's `object.id`, the shape `FeedEntries.make(planning:)` takes.
    func daysByObjectID(for items: [ActivityItem]) -> [Int: PlanningDay] {
        var result: [Int: PlanningDay] = [:]
        for item in items {
            if let day = day(for: item) { result[item.object.id] = day }
        }
        return result
    }

    /// Asks for a Planning row's titles unless they are loaded, on their way or failed a moment ago. Anything that is
    /// not a Planning row is ignored.
    func load(for item: ActivityItem) {
        guard let planning = item.planning, let key = Self.key(for: item) else { return }
        guard days[key] == nil, !running.contains(key), !waitingKeys.contains(key) else { return }
        if let failed = failedAt[key], now().timeIntervalSince(failed) < Self.failureBackoff { return }
        waiting.append(Pending(key: key, id: planning.id, mediaType: planning.mediaType))
        waitingKeys.insert(key)
        startWaiting()
    }

    private func startWaiting() {
        while running.count < Self.maxInFlight, !waiting.isEmpty {
            let next = waiting.removeFirst()
            waitingKeys.remove(next.key)
            running.insert(next.key)
            Task { await self.run(next) }
        }
    }

    private func run(_ pending: Pending) async {
        do {
            let day = try await activityRepository.planningTitles(id: pending.id, mediaType: pending.mediaType)
            running.remove(pending.key)
            failedAt[pending.key] = nil
            days[pending.key] = day
            startWaiting()
            onChange?()
        } catch is CancellationError {
            running.remove(pending.key)
            startWaiting()
        } catch {
            running.remove(pending.key)
            failedAt[pending.key] = now()
            startWaiting()
            if case APIError.unauthorized = error { onUnauthorized() }
        }
    }
}
