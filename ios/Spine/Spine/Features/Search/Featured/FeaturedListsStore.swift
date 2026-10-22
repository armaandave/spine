import Foundation
import Observation

/// Featured shelves for Search: the landing (`nil` media type) and each media type's grouped layer.
///
/// Shows what it last saw at once, from memory or disk, and revalidates in the background when that is more than
/// ten minutes old, on pull-to-refresh and when the app returns to the foreground. While a list is live and the
/// landing is on screen, the landing polls every minute.
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

    init(
        repository: ListRepository,
        cache: FeaturedShelvesCaching = FeaturedShelvesDiskCache(),
        now: @escaping () -> Date = Date.init,
        sleep: @escaping (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.repository = repository
        self.cache = cache
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

    /// Shows the cached layer, then fetches it when it's stale, missing, or `force` is set.
    func load(mediaType: String?, force: Bool = false) async {
        let key = Self.key(for: mediaType)
        restoreFromCacheIfNeeded(key: key)
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

    /// Refetches every minute while the layer has a live list. Run it from a view's `task`, so it stops when the
    /// view leaves the screen.
    func pollWhileLive(mediaType: String?) async {
        while !Task.isCancelled, hasLiveList(mediaType: mediaType) {
            do {
                try await sleep(Self.livePollInterval)
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
            layers[key] = Layer(response: response, loadedAt: loadedAt)
            cache.save(FeaturedShelvesCacheEntry(savedAt: loadedAt, response: response), key: key)
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

    private func restoreFromCacheIfNeeded(key: String) {
        guard layers[key]?.response == nil, let entry = cache.load(key: key) else { return }
        layers[key, default: Layer()].response = entry.response
        layers[key, default: Layer()].loadedAt = entry.savedAt
    }

    private static func key(for mediaType: String?) -> String {
        mediaType ?? "landing"
    }
}

struct FeaturedShelvesCacheEntry: Codable {
    let savedAt: Date
    let response: FeaturedShelvesResponse
}

protocol FeaturedShelvesCaching {
    func load(key: String) -> FeaturedShelvesCacheEntry?
    func save(_ entry: FeaturedShelvesCacheEntry, key: String)
}

/// The last shelves seen, in Caches, so a cold launch shows them before the network answers. Featured lists are
/// public and the same for everyone, so the cache isn't per user.
struct FeaturedShelvesDiskCache: FeaturedShelvesCaching {
    private let directory: URL

    nonisolated init(directory: URL? = nil) {
        self.directory = directory
            ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("FeaturedShelves.v1", isDirectory: true)
    }

    func load(key: String) -> FeaturedShelvesCacheEntry? {
        guard let data = try? Data(contentsOf: fileURL(key)) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(FeaturedShelvesCacheEntry.self, from: data)
    }

    func save(_ entry: FeaturedShelvesCacheEntry, key: String) {
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
