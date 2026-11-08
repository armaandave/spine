import Foundation

/// Where a title's poster colors come from: the server's palette when the payload has one, else the colors counted
/// on this device from the poster the app already loaded, kept in memory per image URL so each poster is counted once.
nonisolated final class PosterPaletteStore: @unchecked Sendable {
    static let shared = PosterPaletteStore()

    typealias Loader = @Sendable (URL) async -> [PosterSwatch]?

    private let load: Loader
    private let lock = NSLock()
    private var counted: [URL: [PosterSwatch]] = [:]
    private var running: [URL: Task<[PosterSwatch]?, Never>] = [:]

    init(load: @escaping Loader = { await PosterPaletteExtractor.load(url: $0) }) {
        self.load = load
    }

    /// The colors known right now without any work: the server's, else ones already counted here. Lets a view start
    /// with its colors instead of fading them in.
    func known(server: [PosterSwatch]?, url: URL?) -> [PosterSwatch]? {
        if let server, !server.isEmpty { return server }
        guard let url else { return nil }
        lock.lock()
        defer { lock.unlock() }
        return counted[url]
    }

    /// The server's colors if it sent any (nothing is counted), else the on-device count for `url`, started once per
    /// URL however many panels ask. Nil when there is no poster or its image can't be read.
    func swatches(server: [PosterSwatch]?, url: URL?) async -> [PosterSwatch]? {
        if let known = known(server: server, url: url) { return known }
        guard let url else { return nil }
        let task: Task<[PosterSwatch]?, Never>
        lock.lock()
        if let existing = running[url] {
            task = existing
        } else {
            let load = load
            task = Task { await load(url) }
            running[url] = task
        }
        lock.unlock()

        let result = await task.value
        lock.lock()
        running[url] = nil
        if let result, !result.isEmpty { counted[url] = result }
        lock.unlock()
        return result
    }
}
