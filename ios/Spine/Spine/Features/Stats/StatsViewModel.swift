import Foundation

/// One stats request: a period and a tab (`mediaType` nil is Global).
struct StatsScope: Hashable {
    var period: StatsPeriod
    var mediaType: String?
}

/// The last pages shown, least recently used out first.
struct StatsPageCache {
    let capacity: Int
    private var pages: [StatsScope: StatsPage] = [:]
    /// Least recently used first.
    private(set) var scopes: [StatsScope] = []

    init(capacity: Int) {
        self.capacity = max(capacity, 1)
    }

    mutating func page(for scope: StatsScope) -> StatsPage? {
        guard let page = pages[scope] else { return nil }
        touch(scope)
        return page
    }

    mutating func insert(_ page: StatsPage, for scope: StatsScope) {
        pages[scope] = page
        touch(scope)
        while scopes.count > capacity {
            pages[scopes.removeFirst()] = nil
        }
    }

    mutating func removeAll() {
        pages = [:]
        scopes = []
    }

    private mutating func touch(_ scope: StatsScope) {
        scopes.removeAll { $0 == scope }
        scopes.append(scope)
    }
}

/// Remembers, per person, the years block (for the pills) and the exposed types (for the
/// picker), so the next visit shows them before the first response arrives.
struct StatsPickerCache {
    let defaults: UserDefaults

    func years(for username: String?) -> StatsYears? {
        defaults.data(forKey: key("years", username)).flatMap { try? JSONDecoder().decode(StatsYears.self, from: $0) }
    }

    func setYears(_ years: StatsYears, for username: String?) {
        guard let data = try? JSONEncoder().encode(years) else { return }
        defaults.set(data, forKey: key("years", username))
    }

    func mediaTypes(for username: String?) -> [String]? {
        defaults.stringArray(forKey: key("media-types", username))
    }

    func setMediaTypes(_ mediaTypes: [String], for username: String?) {
        defaults.set(mediaTypes, forKey: key("media-types", username))
    }

    private func key(_ kind: String, _ username: String?) -> String {
        // "@" can't start a username, so your own stats never share a key with someone's.
        "spine.stats.\(kind).\(username ?? "@me")"
    }
}

/// Drives the Stats page. Each period and tab is its own request; switching keeps the old page
/// on screen, dimmed, until the new one arrives.
@MainActor
@Observable
final class StatsViewModel {
    nonisolated static let cacheCapacity = 12
    nonisolated static let defaultDebounce: Duration = .milliseconds(200)

    /// What the pills and picker show as selected. Changes at once on a tap.
    private(set) var selected: StatsScope
    /// What's on screen. It may belong to an older scope while a load runs or after it failed.
    private(set) var page: StatsPage?
    private(set) var pageScope: StatsScope?
    private(set) var isLoading = false
    /// The last load's error. With a page on screen it's a banner above it; with none, the
    /// whole state.
    private(set) var errorMessage: String?
    private(set) var errorScope: StatsScope?
    /// The years block for the pills: the latest response's, else the last cached one.
    private(set) var years: StatsYears?
    /// The picker's types: the latest response's, else the last cached ones, else the app's.
    private(set) var mediaTypes: [String]
    /// Bumped only by the user's own pill and picker taps, for the selection haptic.
    private(set) var userSwitchCount = 0
    /// Set by a change elsewhere in the app; the next `appeared()` reloads once.
    private(set) var needsRefresh = false

    @ObservationIgnored private let profileRepository: ProfileRepository
    @ObservationIgnored private let username: String?
    @ObservationIgnored private let onUnauthorized: () -> Void
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private let pickerCache: StatsPickerCache
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var cache = StatsPageCache(capacity: StatsViewModel.cacheCapacity)
    @ObservationIgnored private var loadTask: Task<Void, Never>?
    @ObservationIgnored private var generation = 0

    init(
        profileRepository: ProfileRepository,
        username: String? = nil,
        selectedPeriod: StatsPeriod = .allTime,
        selectedMediaType: String? = nil,
        debounce: Duration = StatsViewModel.defaultDebounce,
        defaults: UserDefaults = .standard,
        now: @escaping () -> Date = Date.init,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.username = username
        self.onUnauthorized = onUnauthorized
        self.debounce = debounce
        self.now = now
        pickerCache = StatsPickerCache(defaults: defaults)
        selected = StatsScope(period: selectedPeriod, mediaType: selectedMediaType)
        years = pickerCache.years(for: username)
        mediaTypes = pickerCache.mediaTypes(for: username) ?? APIConstants.fallbackMediaTypes
    }

    var selectedPeriod: StatsPeriod { selected.period }
    var selectedMediaType: String? { selected.mediaType }

    /// All Time, then every year since the first one the viewer can count, newest first.
    var periods: [StatsPeriod] {
        [.allTime] + StatsPeriod.pillYears(years, from: now())
    }

    /// The old page stays up, dimmed and not taking taps, while another scope loads. A reload
    /// of the same scope keeps it undimmed.
    var isDimmed: Bool {
        isLoading && page != nil && pageScope != selected
    }

    /// Nothing to show yet: the first load's spinner.
    var isFirstLoad: Bool {
        page == nil && errorMessage == nil
    }

    /// An error with a page still on screen.
    var bannerMessage: String? {
        page == nil ? nil : errorMessage
    }

    /// The banner's title. It names the tab and the year: after a failed switch the picker
    /// shows the new tab over the old page.
    var bannerTitle: String {
        errorScope.map {
            "Couldn't load \(StatsPageSection.scopeTitle(mediaType: $0.mediaType, period: $0.period))"
        } ?? "Couldn't load stats"
    }

    /// An error with nothing on screen.
    var fullErrorMessage: String? {
        page == nil ? errorMessage : nil
    }

    // MARK: Actions

    /// Call from `onAppear`: loads the first page, or reloads once after a change elsewhere.
    func appeared() {
        if needsRefresh {
            needsRefresh = false
            startLoad(force: true, debounced: false)
        } else if page == nil, loadTask == nil {
            startLoad(force: false, debounced: false)
        }
    }

    func selectPeriod(_ period: StatsPeriod) {
        guard period != selected.period else { return }
        selected.period = period
        userSwitchCount &+= 1
        startLoad(force: false, debounced: true)
    }

    /// `nil` or "" is Global.
    func selectMediaType(_ mediaType: String?) {
        let mediaType = mediaType?.isEmpty == true ? nil : mediaType
        guard mediaType != selected.mediaType else { return }
        selected.mediaType = mediaType
        userSwitchCount &+= 1
        startLoad(force: false, debounced: true)
    }

    func retry() {
        startLoad(force: true, debounced: false)
    }

    /// Pull to refresh: forget every cached page and reload the selected one.
    func refresh() async {
        cache.removeAll()
        needsRefresh = false
        startLoad(force: true, debounced: false)
        await loadTask?.value
    }

    /// A log, tick, rating, list or other change elsewhere: cached pages are out of date. The
    /// reload waits for the next `appeared()`, so a burst of changes costs one request.
    func markNeedsRefresh() {
        cache.removeAll()
        needsRefresh = true
    }

    // MARK: Loading

    private func startLoad(force: Bool, debounced: Bool) {
        loadTask?.cancel()
        generation &+= 1
        let scope = selected
        let requestGeneration = generation

        if !force, let cached = cache.page(for: scope) {
            loadTask = nil
            isLoading = false
            errorMessage = nil
            errorScope = nil
            apply(cached, for: scope)
            return
        }

        isLoading = true
        errorMessage = nil
        errorScope = nil
        let delay = debounced ? debounce : .zero
        loadTask = Task { [weak self] in
            if delay > .zero {
                do {
                    try await Task.sleep(for: delay)
                } catch {
                    return
                }
            }
            await self?.fetch(scope, generation: requestGeneration)
        }
    }

    private func fetch(_ scope: StatsScope, generation requestGeneration: Int) async {
        do {
            let page = try await profileRepository.statsSummary(
                username: username,
                period: scope.period,
                mediaType: scope.mediaType
            )
            // A newer selection or load replaced this one: drop the answer.
            guard requestGeneration == generation, scope == selected else { return }
            cache.insert(page, for: scope)
            apply(page, for: scope)
            isLoading = false
            loadTask = nil
        } catch is CancellationError {
            guard requestGeneration == generation else { return }
            isLoading = false
            loadTask = nil
        } catch {
            guard requestGeneration == generation, scope == selected else { return }
            isLoading = false
            loadTask = nil
            errorMessage = error.localizedDescription
            errorScope = scope
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func apply(_ page: StatsPage, for scope: StatsScope) {
        self.page = page
        pageScope = scope
        if let pageYears = page.years {
            years = pageYears
            pickerCache.setYears(pageYears, for: username)
        }
        let types = page.mediaTypes.map(\.mediaType)
        if !types.isEmpty {
            mediaTypes = types
            pickerCache.setMediaTypes(types, for: username)
        }
    }
}
