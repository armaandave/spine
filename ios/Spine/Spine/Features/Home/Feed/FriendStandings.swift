import Foundation

/// Turns where a friend stands on a title (the position the In progress tile already reads) and what a card's day
/// added into the `FeedStanding` the card draws: the meter's lit part, its end, and the muted "where". Pure.
enum FeedStandingMath {
    /// - Parameters:
    ///   - position: where the friend stands now, from `InProgressTilePresenter.position(for:)`.
    ///   - told: the card's day, in its own unit. Nil for a status-only day.
    /// - Returns: nil when there is neither an end for a meter nor words to say.
    static func standing(position: TilePosition, told: TodayPart?) -> FeedStanding? {
        let to = whole(position.current)
        let total = position.total.map(whole)

        // `pagesTotal`, not `total`: a percent position's total is 100, which would turn pages into percent wrongly.
        // With no page count a day told in pages cannot be put on a percent bar, and nothing is lit.
        var from: Int?
        if let told, let amount = TileTodayMath.convert(told, to: position.unit, total: position.pagesTotal) {
            from = whole(position.current - amount)
        }

        let words = FeedStandingText.whereText(for: position)
        guard total != nil || words != nil else { return nil }
        return FeedStanding(
            unit: InProgressTilePresenter.meterUnit(for: position.unit),
            from: from,
            to: to,
            total: total,
            whereText: words
        )
    }

    private static func whole(_ value: Double) -> Int {
        Int(max(0, value).rounded())
    }
}

/// Where each friend stands on the titles their newest feed cards are about, loaded from the lists the profile's In
/// progress rail already reads (`GET /users/<username>/tracking/`), one request per friend, type and status however many
/// cards, a few at a time, after the feed's first page is in. A card whose standing is not there yet is drawn without
/// one and gets it when it lands; a card without a meter is a complete card, so a failure shows nothing.
@MainActor
final class FriendStandings: FeedStandingProviding {
    /// One list request: a friend's titles of a type in a status.
    struct Pair: Hashable, Sendable {
        let username: String
        let mediaType: String
        let status: String
    }

    /// One card that wants a standing.
    struct Want: Equatable {
        let pair: Pair
        let titleKey: String
        /// The card's day, in its own unit; nil for a status-only day.
        let told: TodayPart?
    }

    enum Phase: Equatable {
        case idle
        case loading
        case loaded
        /// The account is private to the viewer now, unfollowed or blocked (404).
        case unavailable
        case failed(Date)
    }

    private struct Entry {
        /// By title key. Kept while the pair loads again, so a refresh never blanks a meter that was showing.
        var positions: [String: TilePosition] = [:]
        var phase: Phase = .idle
        var loadedAt: Date?
        /// The latest request's number: a result carrying any other is dropped.
        var token = 0
    }

    private struct Job {
        let pair: Pair
        var titleKeys: Set<String>
    }

    private enum Outcome {
        case loaded([String: TilePosition])
        case unavailable
        case unauthorized
        case failed
        case cancelled
    }

    /// How long a loaded pair is trusted before `prepare` loads it again, and how long a failed one is left alone.
    static let freshFor: TimeInterval = 10 * 60
    static let retryAfterFailure: TimeInterval = 60
    /// The types with a position to show. A card's "Stopped" is the list status `Dropped`.
    static let types: Set<String> = ["tv", "anime", "book", "manga", "comic", "game"]

    private let trackingRepository: TrackingRepository
    private let onUnauthorized: () -> Void
    private let now: () -> Date
    private let maxInFlight: Int
    private let maxPages: Int
    private let notifyDelay: Duration

    private var entries: [Pair: Entry] = [:]
    private var queue: [Job] = []
    private var inFlight = 0
    private var tasks: [Pair: Task<Void, Never>] = [:]
    private var onChange: (() -> Void)?
    private var reportedUnauthorized = false
    /// The coalescing wait on its way, so a test can wait for the one `onChange` it ends in.
    private(set) var notifyTask: Task<Void, Never>?

    init(
        trackingRepository: TrackingRepository,
        onUnauthorized: @escaping () -> Void,
        now: @escaping () -> Date = Date.init,
        maxInFlight: Int = 3,
        maxPages: Int = 3,
        notifyDelay: Duration = .milliseconds(120)
    ) {
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
        self.now = now
        self.maxInFlight = maxInFlight
        self.maxPages = maxPages
        self.notifyDelay = notifyDelay
    }

    // MARK: Which pairs

    /// The one card's pair, title and day; nil for a card that never draws a standing (a log, a start, a title with
    /// no position such as a movie).
    static func want(for item: ActivityItem) -> Want? {
        guard item.object.type == "progress_day", item.planning == nil, let ref = item.media?.ref else { return nil }
        let mediaType = requestType(for: ref)
        guard types.contains(mediaType) else { return nil }

        let label = ActivityFeedPresentation.clean(item.object.label)
        let moves = (item.object.moves ?? [])
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
        // The presenter draws no meter for a "Started" card.
        if let label {
            if label.lowercased().hasPrefix("started") { return nil }
        } else if moves.isEmpty,
                  ActivityFeedPresentation.clean(item.object.summary)?.lowercased().hasPrefix("started") == true {
            return nil
        }

        // The day's last move decides the status the title is listed under (a paused title is not In progress).
        let status: String
        switch moves.last {
        case "paused": status = "Paused"
        case "stopped": status = "Dropped"
        default: status = "In progress"
        }
        let told = ToldAmount.parse(
            label: item.object.label, unit: item.object.unit, amount: item.object.amount, percent: item.object.percent
        )
        return Want(
            pair: Pair(username: item.actor.username, mediaType: mediaType, status: status),
            titleKey: FeedEntries.titleKey(for: ref),
            told: told
        )
    }

    /// The media type a list is asked for: a show's seasons and episodes are the show, a MAL title is anime.
    static func requestType(for ref: MediaRef) -> String {
        let isAnime = ref.isAnime || (ref.isTVFamily && ref.source == "mal")
        return isAnime ? "anime" : (ref.isTVFamily ? "tv" : ref.mediaType)
    }

    /// The newest progress card of each person's title that can have a standing, in feed order (the same rule as
    /// `FeedEntries.make`: the first card per person and title, even when it is a start).
    static func wants(in items: [ActivityItem]) -> [Want] {
        var seen = Set<String>()
        var wants: [Want] = []
        for item in items {
            guard item.object.type == "progress_day", let ref = item.media?.ref else { continue }
            guard seen.insert("\(item.actor.id)|\(FeedEntries.titleKey(for: ref))").inserted else { continue }
            if let want = want(for: item) { wants.append(want) }
        }
        return wants
    }

    // MARK: FeedStandingProviding

    func standing(for item: ActivityItem, isNewestOfTitle: Bool) -> FeedStanding? {
        guard isNewestOfTitle, let want = Self.want(for: item),
              let position = entries[want.pair]?.positions[want.titleKey] else { return nil }
        return FeedStandingMath.standing(position: position, told: want.told)
    }

    func prepare(for items: [ActivityItem]) {
        enqueue(wants: Self.wants(in: items), reloading: false)
    }

    func reload(for items: [ActivityItem]) {
        enqueue(wants: Self.wants(in: items), reloading: true)
    }

    func setOnChange(_ onChange: (() -> Void)?) {
        self.onChange = onChange
    }

    // MARK: Queue

    private func enqueue(wants: [Want], reloading: Bool) {
        var keys: [Pair: Set<String>] = [:]
        var order: [Pair] = []
        for want in wants {
            if keys[want.pair] == nil { order.append(want.pair) }
            keys[want.pair, default: []].insert(want.titleKey)
        }
        for pair in order {
            let entry = entries[pair] ?? Entry()
            let wanted = keys[pair] ?? []
            if reloading {
                // The person asked again: every pair is loaded again, a failed or unavailable one included. One
                // already loading is replaced, since its answer may be older than the refresh.
                if entry.phase == .loading { tasks[pair]?.cancel() }
            } else {
                switch entry.phase {
                case .loading, .unavailable: continue
                case let .failed(at) where now().timeIntervalSince(at) < Self.retryAfterFailure: continue
                case .loaded where entry.loadedAt.map({ now().timeIntervalSince($0) < Self.freshFor }) ?? false: continue
                default: break
                }
            }
            if let queued = queue.firstIndex(where: { $0.pair == pair }) {
                queue[queued].titleKeys.formUnion(wanted)
            } else {
                queue.append(Job(pair: pair, titleKeys: wanted))
            }
        }
        pump()
    }

    private func pump() {
        while inFlight < maxInFlight, !queue.isEmpty {
            start(queue.removeFirst())
        }
    }

    private func start(_ job: Job) {
        var entry = entries[job.pair] ?? Entry()
        entry.token += 1
        entry.phase = .loading
        entries[job.pair] = entry
        let token = entry.token
        inFlight += 1
        tasks[job.pair] = Task { [weak self] in
            await self?.run(job, token: token)
        }
    }

    private func run(_ job: Job, token: Int) async {
        var found: [String: TilePosition] = [:]
        var outcome: Outcome
        do {
            let repository = trackingRepository.owned(by: job.pair.username)
            var page: String?
            var seen = Set<String>()
            for _ in 0 ..< maxPages {
                let response = try await repository.list(
                    mediaType: job.pair.mediaType, page: page, status: job.pair.status, query: nil
                )
                for row in response.results {
                    let key = FeedEntries.titleKey(for: row.media.ref)
                    seen.insert(key)
                    if let position = InProgressTilePresenter.position(for: row) { found[key] = position }
                }
                if job.titleKeys.isSubset(of: seen) { break }
                guard let next = APIPageCursor.nextPage(from: response.next) else { break }
                page = next
            }
            outcome = .loaded(found)
        } catch is CancellationError {
            outcome = .cancelled
        } catch APIError.unauthorized {
            outcome = .unauthorized
        } catch APIError.httpStatus(404, _) {
            outcome = .unavailable
        } catch {
            outcome = Task.isCancelled ? .cancelled : .failed
        }
        finish(job.pair, token: token, outcome: outcome)
    }

    private func finish(_ pair: Pair, token: Int, outcome: Outcome) {
        inFlight -= 1
        defer { pump() }
        guard var entry = entries[pair], entry.token == token else { return }
        tasks[pair] = nil
        switch outcome {
        case let .loaded(positions):
            entry.positions = positions
            entry.phase = .loaded
            entry.loadedAt = now()
            reportedUnauthorized = false
        case .unavailable:
            // Private to the viewer now: nothing of theirs is shown, and nothing is retried until the next reload.
            entry.positions = [:]
            entry.phase = .unavailable
        case .unauthorized:
            entry.phase = .failed(now())
            if !reportedUnauthorized {
                reportedUnauthorized = true
                onUnauthorized()
            }
        case .failed:
            entry.phase = .failed(now())
        case .cancelled:
            entry.phase = .idle
        }
        entries[pair] = entry
        if case .cancelled = outcome { return }
        scheduleNotify()
    }

    /// Several pairs landing in a burst ask Home to rebuild once.
    private func scheduleNotify() {
        guard notifyTask == nil else { return }
        let delay = notifyDelay
        notifyTask = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard let self else { return }
            notifyTask = nil
            onChange?()
        }
    }
}
