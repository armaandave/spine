import Foundation
import Observation

/// Featured shelves for Search: the landing (`nil` media type) and each media type's grouped layer.
///
/// Shows what it last saw at once, from memory or disk, and revalidates in the background when that is more than
/// ten minutes old, on pull-to-refresh and when the app returns to the foreground. While a list is live and the
/// landing is on screen, the landing polls every minute; an upcoming window wakes it when the window starts.
/// The disk cache decodes, encodes and writes off the main actor.
@MainActor
@Observable
final class FeaturedListsStore {
    static let staleInterval: TimeInterval = 10 * 60
    static let livePollInterval: Duration = .seconds(60)

    struct Layer {
        var response: FeaturedShelvesResponse?
        var loadedAt: Date?
        var isLoading = false
        var errorMessage: String?
    }

    private(set) var layers: [String: Layer] = [:]

    @ObservationIgnored private let repository: ListRepository
    @ObservationIgnored private let cache: FeaturedShelvesCaching
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let sleep: (Duration) async throws -> Void
    @ObservationIgnored private let onUnauthorized: () -> Void
    @ObservationIgnored private var inFlight: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var restores: [String: Task<Void, Never>] = [:]

    init(
        repository: ListRepository,
        cache: FeaturedShelvesCaching? = nil,
        now: @escaping () -> Date = Date.init,
        sleep: @escaping (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.repository = repository
        self.cache = cache ?? FeaturedShelvesDiskCache(apiBaseURL: AppConfig.apiBaseURL)
        self.now = now
        self.sleep = sleep
        self.onUnauthorized = onUnauthorized
    }

    func layer(mediaType: String?) -> Layer {
        layers[Self.key(for: mediaType)] ?? Layer()
    }

    func hasLiveList(mediaType: String?) -> Bool {
        layer(mediaType: mediaType).response?.hasLiveList(now: now()) == true
    }

    /// How long the poller waits before its next refetch: a minute while a list is live, until the earliest
    /// upcoming window starts, or nil when nothing is live or upcoming.
    func nextPollDelay(mediaType: String?) -> Duration? {
        guard let response = layer(mediaType: mediaType).response else { return nil }
        let now = now()
        if response.hasLiveList(now: now) {
            return Self.livePollInterval
        }
        guard let start = response.nextLiveStart(after: now) else { return nil }
        return .seconds(max(1, start.timeIntervalSince(now)))
    }

    /// Shows the cached layer, then fetches it when it's stale, missing, or `force` is set.
    func load(mediaType: String?, force: Bool = false) async {
        let key = Self.key(for: mediaType)
        await restoreFromCacheIfNeeded(key: key)
        if !force, let loadedAt = layers[key]?.loadedAt, now().timeIntervalSince(loadedAt) < Self.staleInterval {
            return
        }
        if let running = inFlight[key] {
            await running.value
            return
        }
        let task = Task { await fetch(key: key, mediaType: mediaType) }
        inFlight[key] = task
        await task.value
        inFlight[key] = nil
    }

    /// Refetches every minute while the layer has a live list, and when an upcoming window starts. Run it from a
    /// view's `task`, so it stops when the view leaves the screen.
    func pollWhileLive(mediaType: String?) async {
        while !Task.isCancelled, let delay = nextPollDelay(mediaType: mediaType) {
            do {
                try await sleep(delay)
            } catch {
                return
            }
            guard !Task.isCancelled else { return }
            await load(mediaType: mediaType, force: true)
        }
    }

    private func fetch(key: String, mediaType: String?) async {
        layers[key, default: Layer()].isLoading = true
        do {
            let response = if let mediaType {
                try await repository.featuredShelves(mediaType: mediaType)
            } else {
                try await repository.featuredShelves()
            }
            let loadedAt = now()
            let previous = layers[key]?.response
            layers[key] = Layer(response: response, loadedAt: loadedAt)
            if previous?.hasSameContent(as: response) != true {
                await cache.save(FeaturedShelvesCacheEntry(savedAt: loadedAt, response: response), key: key)
            }
        } catch is CancellationError {
            layers[key, default: Layer()].isLoading = false
        } catch {
            layers[key, default: Layer()].isLoading = false
            layers[key, default: Layer()].errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    /// Reads a layer from disk once, the first time it's needed. Concurrent loads wait for the same read.
    private func restoreFromCacheIfNeeded(key: String) async {
        if let restore = restores[key] {
            await restore.value
            return
        }
        guard layers[key]?.response == nil else { return }
        let cache = cache
        let restore = Task {
            // A fetch that finished first wins over the disk.
            guard let entry = await cache.load(key: key), layers[key]?.response == nil else { return }
            layers[key, default: Layer()].response = entry.response
            layers[key, default: Layer()].loadedAt = entry.savedAt
        }
        restores[key] = restore
        await restore.value
    }

    private static func key(for mediaType: String?) -> String {
        mediaType ?? "landing"
    }
}

nonisolated struct FeaturedShelvesCacheEntry: Codable {
    let savedAt: Date
    let response: FeaturedShelvesResponse
}

nonisolated protocol FeaturedShelvesCaching: Sendable {
    func load(key: String) async -> FeaturedShelvesCacheEntry?
    func save(_ entry: FeaturedShelvesCacheEntry, key: String) async
}

/// The last shelves seen, in Caches, so a cold launch shows them before the network answers. Featured lists are
/// public and the same for everyone, so the cache isn't per user; it is per API server, so a Debug build that
/// switches backends never shows another server's lists. Reads and writes run off the main actor.
nonisolated struct FeaturedShelvesDiskCache: FeaturedShelvesCaching {
    private let directory: URL

    init(directory: URL) {
        self.directory = directory
    }

    init(apiBaseURL: URL) {
        self.init(
            directory: FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("FeaturedShelves.v1", isDirectory: true)
                .appendingPathComponent(Self.serverDirectoryName(for: apiBaseURL), isDirectory: true)
        )
    }

    /// "api.spine-api.com", "127.0.0.1-8001": one directory per API host and port.
    static func serverDirectoryName(for url: URL) -> String {
        let name = [url.host, url.port.map(String.init)].compactMap { $0 }.joined(separator: "-")
        let safe = name.filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "." }
        return safe.allSatisfy { $0 == "." } ? "server" : safe
    }

    @concurrent
    func load(key: String) async -> FeaturedShelvesCacheEntry? {
        guard let data = try? Data(contentsOf: fileURL(key)) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(FeaturedShelvesCacheEntry.self, from: data)
    }

    @concurrent
    func save(_ entry: FeaturedShelvesCacheEntry, key: String) async {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(entry) else { return }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? data.write(to: fileURL(key), options: .atomic)
    }

    private func fileURL(_ key: String) -> URL {
        let safe = key.filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        return directory.appendingPathComponent("\(safe.isEmpty ? "shelves" : safe).json")
    }
}
