import Foundation
import Observation

/// The viewer's state for the Trending titles the panel's rail is about, so the rail says what is true of your library
/// (a title you already have never says "Plan") and its taps go through the same write queue the media page uses.
///
/// Each entry owns a `MediaDetailViewModel` for the title and fills it the way the page does, with the detail and, for
/// a tracked title that is not a game, the tracking row merged in. It does not call the page's `load()`, which also
/// fetches reviews, friends, music enrichment and external ratings.
///
/// Loading follows focus: the focused title at once, then its neighbors after 250 ms of the focus holding still (a
/// fast fling loads nothing in between). An entry is fresh for 60 s. Four entries are kept, least recently focused
/// first out. No glass and no geometry here: this is data.
@MainActor
@Observable
final class TrendingRailStore {
    enum Phase: Equatable {
        case loading
        case ready(Date)
        case failed
    }

    /// One title's rail state. A class, so the rail's views observe just the entry they show.
    @MainActor
    @Observable
    final class Entry {
        let key: String
        let title: TrendingTitle
        let viewModel: MediaDetailViewModel
        fileprivate(set) var phase = Phase.loading
        /// The rating picker's state for the rail.
        var picker = MediaRatingPickerState()

        fileprivate init(title: TrendingTitle, viewModel: MediaDetailViewModel) {
            key = title.key
            self.title = title
            self.viewModel = viewModel
        }

        var isReady: Bool {
            if case .ready = phase { true } else { false }
        }
    }

    nonisolated static let freshFor: TimeInterval = 60
    nonisolated static let neighborDelay: Duration = .milliseconds(250)
    nonisolated static let capacity = 4

    private(set) var entries: [String: Entry] = [:]
    /// Every title's page model's `changeOrigin`, so Home can tell a rail write from a foreign change and skip the
    /// full reload for it.
    @ObservationIgnored var changeOrigins: Set<UUID> = []

    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let diaryRepository: DiaryRepository
    @ObservationIgnored private let onUnauthorized: () -> Void
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let sleep: (Duration) async throws -> Void
    @ObservationIgnored private var recency: [String] = []
    @ObservationIgnored private var focusedKey: String?
    @ObservationIgnored private var loads: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var neighborTask: Task<Void, Never>?
    @ObservationIgnored private var observer: Task<Void, Never>?

    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        now: @escaping () -> Date = Date.init,
        sleep: @escaping (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
        self.now = now
        self.sleep = sleep
        observer = Task { [weak self] in
            for await notification in NotificationCenter.default.notifications(named: .mediaStateDidChange) {
                guard let self else { return }
                handleChange(notification.userInfo)
            }
        }
    }

    func entry(for key: String) -> Entry? {
        entries[key]
    }

    // MARK: Focus

    /// The title in focus now, and the titles beside it to have ready. `neighborDelay` is how long the focus must hold
    /// before the neighbors load; `.zero` loads them with it (a tile that has just opened).
    func focus(_ title: TrendingTitle, neighbors: [TrendingTitle] = [], neighborDelay: Duration = TrendingRailStore.neighborDelay) {
        let isSameFocus = focusedKey == title.key && neighborTask != nil
        let entry = makeEntry(for: title)
        focusedKey = title.key
        load(entry)
        // The tile that has just opened asked for this focus and its neighbor already.
        guard !isSameFocus else { return }
        neighborTask?.cancel()
        neighborTask = nil
        guard !neighbors.isEmpty else { return }
        let delay = neighborDelay
        neighborTask = Task { [weak self] in
            if delay > .zero {
                try? await self?.sleep(delay)
            }
            for neighbor in neighbors.prefix(2) {
                guard !Task.isCancelled, let self, focusedKey == title.key else { return }
                let entry = makeEntry(for: neighbor)
                load(entry)
                await loads[entry.key]?.value
            }
        }
    }

    /// The tile closed: nothing more is fetched on its behalf. Entries stay until they go stale or are pushed out.
    func tileClosed() {
        neighborTask?.cancel()
        neighborTask = nil
        for task in loads.values { task.cancel() }
    }

    func dismissPicker(for key: String) {
        guard var picker = entries[key]?.picker, picker.isPresented else { return }
        picker.dismiss()
        entries[key]?.picker = picker
    }

    // MARK: Entries

    private func makeEntry(for title: TrendingTitle) -> Entry {
        if let existing = entries[title.key] {
            touch(title.key)
            return existing
        }
        let model = MediaDetailViewModel(
            ref: title.media.ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        changeOrigins.insert(model.changeOrigin)
        let entry = Entry(title: title, viewModel: model)
        entries[title.key] = entry
        touch(title.key)
        evict()
        return entry
    }

    private func touch(_ key: String) {
        recency.removeAll { $0 == key }
        recency.append(key)
    }

    /// Four entries stay: the least recently focused goes first, never the one in focus and never one with a write on
    /// its way.
    private func evict() {
        while entries.count > Self.capacity {
            guard let key = recency.first(where: { key in
                key != focusedKey && entries[key]?.viewModel.hasPendingWrites != true
            }) else { return }
            loads[key]?.cancel()
            loads[key] = nil
            entries[key] = nil
            recency.removeAll { $0 == key }
        }
    }

    // MARK: Loading

    private func load(_ entry: Entry) {
        guard loads[entry.key] == nil, !entry.viewModel.hasPendingWrites else { return }
        if case let .ready(at) = entry.phase, now().timeIntervalSince(at) < Self.freshFor { return }
        entry.phase = .loading
        let key = entry.key
        loads[key] = Task { [weak self, weak entry] in
            guard let self, let entry else { return }
            await fetch(entry)
            loads[key] = nil
        }
    }

    private func fetch(_ entry: Entry) async {
        let model = entry.viewModel
        let ref = entry.title.media.ref
        do {
            var loaded = try await mediaRepository.detail(ref: ref)
            // The page's rule (`trackingToMerge`): games carry everything in the detail; a title with no tracking
            // has nothing to read.
            if loaded.ref.mediaType != "game", loaded.userState?.isTracked == true || loaded.userState?.status != nil {
                if let tracking = await trackingState(for: loaded.ref), let state = loaded.userState {
                    loaded.userState = state.applying(tracking)
                }
            }
            try Task.checkCancellation()
            // A write made while this loaded is newer than what came back.
            if !model.hasPendingWrites || model.detail == nil {
                model.detail = loaded
            }
            entry.phase = .ready(now())
            syncPicker(of: entry)
        } catch is CancellationError {
            return
        } catch {
            entry.phase = .failed
            if case APIError.unauthorized = error { onUnauthorized() }
        }
    }

    private func trackingState(for ref: MediaRef) async -> TrackingState? {
        do {
            return try await trackingRepository.detail(ref: ref)
        } catch {
            if case APIError.unauthorized = error { onUnauthorized() }
            return nil
        }
    }

    /// The picker shows what the server says (the page's two `onChange` syncs).
    func syncPicker(of entry: Entry) {
        let viewer = entry.viewModel.viewer
        entry.picker.syncConfirmed(viewer?.rating?.ratingHalfSteps ?? 0)
        entry.picker.syncCompleted(viewer?.status?.caseInsensitiveCompare("Completed") == .orderedSame)
    }

    // MARK: What the writes have to say

    /// Notices and failures the titles' page models raised, which Home shows in its own alert. A write for a title you
    /// have swiped away from still raises its message here.
    var pendingMessages: [String] {
        entries.values.flatMap { entry in
            [entry.viewModel.tvNotice, entry.viewModel.quickActionErrorMessage, entry.viewModel.likeErrorMessage]
                .compactMap { $0 }
        }
    }

    /// The messages now, cleared on the models so each is shown once.
    func takeMessages() -> [String] {
        let messages = pendingMessages
        for entry in entries.values {
            entry.viewModel.tvNotice = nil
            entry.viewModel.quickActionErrorMessage = nil
            entry.viewModel.likeErrorMessage = nil
        }
        return messages
    }

    // MARK: Changes from elsewhere

    private func handleChange(_ userInfo: [AnyHashable: Any]?) {
        for entry in entries.values where entry.isReady {
            entry.viewModel.handleMediaStateChange(userInfo)
        }
    }
}
