import Foundation

/// Turns where a friend stands on a title (the position the In progress tile already reads) and what a card's day
/// added into the `FeedStanding` the card draws: the meter's lit part, its end, and the muted "where". Pure.
enum FeedStandingMath {
    /// - Parameters:
    ///   - position: where the friend stands now, from `InProgressTilePresenter.position(for:)`.
    ///   - told: the card's day, in its own unit. Nil for a status-only day.
    ///   - place: where the day ended, by unit, as the server sent it (`object.place`). A log that finished the read
    ///     after the day took the rest, so the day stops here, not at the end. Nil (an older server, or no number in
    ///     the position's unit) keeps where the friend stands now.
    /// - Returns: nil when there is neither an end for a meter nor words to say.
    static func standing(position: TilePosition, told: TodayPart?, place: [String: Double]? = nil) -> FeedStanding? {
        var position = position
        if let end = dayEnd(place, in: position) { position.current = end }
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

    /// A friend's show, counted by the season they are in: "S1 · 3 of 12 episodes" and that season's ticks, the card's
    /// day lit (never more than the season has watched). A show is always partway through a season, never through
    /// the whole show, so this never reads the show's own counts.
    /// - Parameters:
    ///   - watched: episodes watched in the season; nil when the season is complete.
    ///   - total: the season's episodes. Without a positive total nothing is drawn.
    ///   - told: the card's day, in episodes. Nil for a status-only day.
    static func seasonStanding(season: Int, watched: Int?, total: Int, told: TodayPart?) -> FeedStanding? {
        guard total > 0 else { return nil }
        let done = min(max(0, watched ?? total), total)
        let position = TilePosition(unit: .episodes, current: Double(done), total: Double(total), pagesTotal: nil)
        guard let counted = standing(position: position, told: told) else { return nil }
        return FeedStanding(
            unit: counted.unit,
            from: counted.from,
            to: counted.to,
            total: counted.total,
            whereText: FeedStandingText.seasonText(season: season, current: done, total: total)
        )
    }

    /// The day's end in the position's unit: the server's number in that unit, else pages and percent turned into
    /// each other through the page count. Nil when it cannot be said.
    static func dayEnd(_ place: [String: Double]?, in position: TilePosition) -> Double? {
        guard let place else { return nil }
        let key: String? = switch position.unit {
        case .pages: "pages"
        case .percent: "percent"
        case .chapters: "chapters"
        case .issues: "issues"
        case .minutes: "minutes"
        case .episodes: "episodes"
        }
        if let key, let value = place[key], value.isFinite { return value }
        guard let pages = position.pagesTotal, pages > 0 else { return nil }
        switch position.unit {
        case .pages:
            return place["percent"].map { ($0 / 100 * pages).rounded() }
        case .percent:
            return place["pages"].map { ($0 / pages * 100 * 100).rounded() / 100 }
        default:
            return nil
        }
    }

    private static func whole(_ value: Double) -> Int {
        Int(max(0, value).rounded())
    }
}

/// Where each friend stands on the titles their newest feed cards are about, loaded from the lists the profile's In
/// progress rail already reads (`GET /users/<username>/tracking/`), one request per friend, type and status however many
/// cards, a few at a time, after the feed's first page is in. A show is counted by the season the friend is in, whose
/// episode count comes from the season's public page (one request per show and season); a show's card that says
/// where its day ended (`object.place`) is counted by that season instead, with no list request. A title the friend finished
/// that day is no longer in their In progress list, so a wanted title the list did not have is looked up by name in
/// their Completed list (one small request each) and drawn where they ended. A card whose standing is not
/// there yet is drawn without one and gets it when it lands; a card without a meter is a complete card, so a failure
/// shows nothing.
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
        /// The title's name, which finds it in the Completed list. Nil when the card has none.
        let title: String?
        /// The card's day, in its own unit; nil for a status-only day.
        let told: TodayPart?
        /// Where the day ended, by unit (`object.place`); nil from an older server.
        var place: [String: Double]? = nil
        /// A show's day: the season and episode it ended on, from `place`. Drawn from that season's page alone, so the
        /// friend's list is not asked. Nil for anything else, or from an older server.
        var daySeason: SeasonPlace? = nil
    }

    enum Phase: Equatable {
        case idle
        case loading
        case loaded
        /// The account is private to the viewer now, unfollowed or blocked (404).
        case unavailable
        case failed(Date)
    }

    /// The season a friend is in on a show: the show, the season and how many of its episodes they have watched
    /// (nil when they have finished it).
    struct SeasonPlace: Equatable {
        let show: MediaRef
        let number: Int
        let watched: Int?

        var seasonRef: MediaRef { ContinueWatchingEpisodes.seasonRef(show: show, seasonNumber: number) }
        var key: String { "\(show.source)|\(show.mediaId)|\(number)" }
    }

    private struct SeasonTotal {
        let total: Int
        let at: Date
    }

    private struct Entry {
        /// By title key. Kept while the pair loads again, so a refresh never blanks a meter that was showing.
        var positions: [String: TilePosition] = [:]
        /// A show's season by title key. Shows have no position of their own here: they are counted by their season.
        var seasons: [String: SeasonPlace] = [:]
        var phase: Phase = .idle
        var loadedAt: Date?
        /// The latest request's number: a result carrying any other is dropped.
        var token = 0
    }

    private struct Job {
        let pair: Pair
        var titleKeys: Set<String>
        /// The wanted titles' names by title key, for the Completed lookup.
        var titles: [String: String]
    }

    private enum Outcome {
        case loaded(positions: [String: TilePosition], seasons: [String: SeasonPlace])
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
    /// Reads a season's page for its episode count. Without it a show is drawn with no meter and no "where".
    private let seasonPages: MediaRepository?
    private let onUnauthorized: () -> Void
    private let now: () -> Date
    private let maxInFlight: Int
    private let maxPages: Int
    private let notifyDelay: Duration
    private let maxSeasonsInFlight = 2

    private var entries: [Pair: Entry] = [:]
    private var queue: [Job] = []
    private var inFlight = 0
    private var tasks: [Pair: Task<Void, Never>] = [:]
    private var onChange: (() -> Void)?
    private var reportedUnauthorized = false
    /// Seasons' episode counts by `SeasonPlace.key`, and the reads on their way or waiting.
    private var seasonTotals: [String: SeasonTotal] = [:]
    private var seasonFailedAt: [String: Date] = [:]
    private var seasonLoading: Set<String> = []
    private var seasonQueue: [SeasonPlace] = []
    private var seasonsInFlight = 0
    /// The coalescing wait on its way, so a test can wait for the one `onChange` it ends in.
    private(set) var notifyTask: Task<Void, Never>?

    init(
        trackingRepository: TrackingRepository,
        seasonPages: MediaRepository? = nil,
        onUnauthorized: @escaping () -> Void,
        now: @escaping () -> Date = Date.init,
        maxInFlight: Int = 3,
        maxPages: Int = 3,
        notifyDelay: Duration = .milliseconds(120)
    ) {
        self.trackingRepository = trackingRepository
        self.seasonPages = seasonPages
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
        var daySeason: SeasonPlace?
        if mediaType == "tv", let season = item.object.place?["season"], let episode = item.object.place?["episodes"],
           season.isFinite, episode.isFinite, season >= 0, episode >= 1 {
            daySeason = SeasonPlace(show: ref.parentTVRef ?? ref, number: Int(season), watched: Int(episode))
        }
        return Want(
            pair: Pair(username: item.actor.username, mediaType: mediaType, status: status),
            titleKey: FeedEntries.titleKey(for: ref),
            title: ActivityFeedPresentation.clean(item.media?.title),
            told: told,
            place: item.object.place,
            daySeason: daySeason
        )
    }

    /// The media type a list is asked for: a show's seasons and episodes are the show, a MAL title is anime.
    static func requestType(for ref: MediaRef) -> String {
        let isAnime = ref.isAnime || (ref.isTVFamily && ref.source == "mal")
        return isAnime ? "anime" : (ref.isTVFamily ? "tv" : ref.mediaType)
    }

    /// The season a friend's show row is in, or nil when it cannot be known (and so nothing is drawn). The season of
    /// the episode up next, with every episode before it watched; caught up, the season of the next episode to air. A
    /// show whose next episode is the first of a season after the first has just finished the season before, which is
    /// the one it is counted by, complete.
    static func seasonPlace(for row: LibraryItem) -> SeasonPlace? {
        guard row.media.ref.mediaType == "tv", let tv = row.tracking.tv else { return nil }
        let season: Int
        let episode: Int
        if let next = tv.upNext {
            season = next.seasonNumber
            episode = next.episodeNumber
        } else if let airing = tv.nextAiring, let number = airing.episodeNumber {
            season = airing.seasonNumber
            episode = number
        } else {
            return nil
        }
        guard episode >= 1 else { return nil }
        if episode == 1 {
            guard season > 1 else { return SeasonPlace(show: row.media.ref, number: season, watched: 0) }
            return SeasonPlace(show: row.media.ref, number: season - 1, watched: nil)
        }
        return SeasonPlace(show: row.media.ref, number: season, watched: episode - 1)
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
        guard isNewestOfTitle, let want = Self.want(for: item) else { return nil }
        if let day = want.daySeason {
            // The season and episode the day ended on, not where the friend is now: a season log after it took the
            // rest. Nothing is drawn until that season's episodes are known.
            guard let known = seasonTotals[day.key] else { return nil }
            return FeedStandingMath.seasonStanding(
                season: day.number, watched: day.watched, total: known.total, told: want.told
            )
        }
        guard let entry = entries[want.pair] else { return nil }
        if want.pair.mediaType == "tv" {
            // Counted by the season the friend is in; until that season's episodes are known, nothing is drawn.
            guard let place = entry.seasons[want.titleKey], let known = seasonTotals[place.key] else { return nil }
            return FeedStandingMath.seasonStanding(
                season: place.number, watched: place.watched, total: known.total, told: want.told
            )
        }
        guard let position = entry.positions[want.titleKey] else { return nil }
        return FeedStandingMath.standing(position: position, told: want.told, place: want.place)
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
        // A show's day that says its own season needs only that season's page, not the friend's list.
        for want in wants {
            if let day = want.daySeason { queueSeason(day) }
        }
        pumpSeasons()
        var keys: [Pair: Set<String>] = [:]
        var names: [Pair: [String: String]] = [:]
        var order: [Pair] = []
        for want in wants where want.daySeason == nil {
            if keys[want.pair] == nil { order.append(want.pair) }
            keys[want.pair, default: []].insert(want.titleKey)
            if let title = want.title { names[want.pair, default: [:]][want.titleKey] = title }
        }
        for pair in order {
            let entry = entries[pair] ?? Entry()
            let wanted = keys[pair] ?? []
            let titles = names[pair] ?? [:]
            // A pair already loaded can have shows whose season has not been read.
            loadSeasons(pair: pair, titleKeys: wanted)
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
                queue[queued].titles.merge(titles) { _, new in new }
            } else {
                queue.append(Job(pair: pair, titleKeys: wanted, titles: titles))
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
        var places: [String: SeasonPlace] = [:]
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
                    if row.media.ref.mediaType == "tv" {
                        if let place = Self.seasonPlace(for: row) { places[key] = place }
                    } else if let position = InProgressTilePresenter.position(for: row) {
                        found[key] = position
                    }
                }
                if job.titleKeys.isSubset(of: seen) { break }
                guard let next = APIPageCursor.nextPage(from: response.next) else { break }
                page = next
            }
            // A title finished that day is in Completed now. Only the wanted titles the list did not have are asked
            // for, by name, one small request each. A finished show has no place to read (its season comes from the
            // episode up next), so shows are not asked.
            if job.pair.status == "In progress", job.pair.mediaType != "tv" {
                for key in job.titleKeys.subtracting(seen).sorted() {
                    guard let title = job.titles[key] else { continue }
                    let finished = try await repository.list(
                        mediaType: job.pair.mediaType, page: nil, status: "Completed", query: title
                    )
                    for row in finished.results where FeedEntries.titleKey(for: row.media.ref) == key {
                        if let position = InProgressTilePresenter.position(for: row) { found[key] = position }
                    }
                }
            }
            outcome = .loaded(positions: found, seasons: places)
        } catch is CancellationError {
            outcome = .cancelled
        } catch APIError.unauthorized {
            outcome = .unauthorized
        } catch APIError.httpStatus(404, _) {
            outcome = .unavailable
        } catch {
            outcome = Task.isCancelled ? .cancelled : .failed
        }
        finish(job.pair, token: token, outcome: outcome, titleKeys: job.titleKeys)
    }

    private func finish(_ pair: Pair, token: Int, outcome: Outcome, titleKeys: Set<String>) {
        inFlight -= 1
        defer { pump() }
        guard var entry = entries[pair], entry.token == token else { return }
        tasks[pair] = nil
        switch outcome {
        case let .loaded(positions, seasons):
            entry.positions = positions
            entry.seasons = seasons
            entry.phase = .loaded
            entry.loadedAt = now()
            reportedUnauthorized = false
        case .unavailable:
            // Private to the viewer now: nothing of theirs is shown, and nothing is retried until the next reload.
            entry.positions = [:]
            entry.seasons = [:]
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
        loadSeasons(pair: pair, titleKeys: titleKeys)
        scheduleNotify()
    }

    // MARK: Seasons

    /// Asks for the episode count of each wanted show's season that is not known (or not fresh), a few at a time. A
    /// failed read is left alone for a minute. Home is told as each lands.
    private func loadSeasons(pair: Pair, titleKeys: Set<String>) {
        guard seasonPages != nil, let entry = entries[pair] else { return }
        for titleKey in titleKeys.sorted() {
            if let place = entry.seasons[titleKey] { queueSeason(place) }
        }
        pumpSeasons()
    }

    /// Lines up one season's read unless it is known and fresh, on its way, or failed under a minute ago.
    private func queueSeason(_ place: SeasonPlace) {
        guard seasonPages != nil, !seasonLoading.contains(place.key) else { return }
        if let known = seasonTotals[place.key], now().timeIntervalSince(known.at) < Self.freshFor { return }
        if let failed = seasonFailedAt[place.key], now().timeIntervalSince(failed) < Self.retryAfterFailure { return }
        seasonLoading.insert(place.key)
        seasonQueue.append(place)
    }

    private func pumpSeasons() {
        guard seasonPages != nil else { return }
        while seasonsInFlight < maxSeasonsInFlight, !seasonQueue.isEmpty {
            let place = seasonQueue.removeFirst()
            seasonsInFlight += 1
            Task { [weak self] in
                await self?.readSeason(place)
            }
        }
    }

    private func readSeason(_ place: SeasonPlace) async {
        guard let pages = seasonPages else { return }
        var total: Int?
        var expired = false
        do {
            let count = try await pages.detail(ref: place.seasonRef).episodes?.count ?? 0
            total = count > 0 ? count : nil
        } catch APIError.unauthorized {
            expired = true
        } catch {}
        seasonsInFlight -= 1
        seasonLoading.remove(place.key)
        defer { pumpSeasons() }
        if expired, !reportedUnauthorized {
            reportedUnauthorized = true
            onUnauthorized()
        }
        guard let total else {
            seasonFailedAt[place.key] = now()
            return
        }
        seasonFailedAt[place.key] = nil
        seasonTotals[place.key] = SeasonTotal(total: total, at: now())
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
