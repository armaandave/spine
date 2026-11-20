import SwiftUI

/// Runs what a poster's hold menu asks for. Each title gets the page's own model (`MediaDetailViewModel`), filled the way
/// Home's Trending rail fills it (`TrendingRailStore`), and each choice takes the rail's route (`TrendingRailRouter`):
/// a write goes through the model's queue, and a screen (a log, a sheet) opens on the title's page as a handoff.
///
/// One per signed-in app, in the environment from `AppShellView`, where `posterHoldHost` shows its questions, notices
/// and the Add to List sheet.
@MainActor
@Observable
final class PosterHoldController {
    enum Choice: Equatable {
        /// The eye, as the menu showed it: nothing happens if the title has changed since.
        case watched(wasFilled: Bool)
        /// The bookmark, as the menu showed it.
        case planning(wasFilled: Bool)
        case removeFromLibrary
    }

    /// A question to answer before a write the page model doesn't ask itself (a book's or game's Remove from Library).
    struct Question: Identifiable {
        let id = UUID()
        let prompt: String
        let confirmTitle: String
        let confirm: () -> Void
    }

    /// The title whose Add to List sheet is up.
    var addToList: MediaSummary?
    var question: Question?
    /// A notice of the menu's own, when a title couldn't load.
    var notice: String?
    /// The model of the title acted on last: its questions (a TV eye or Remove asks first) and notices show until
    /// they're answered.
    private(set) var active: MediaDetailViewModel?

    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let listRepository: ListRepository
    let peopleRepository: PeopleRepository
    let onUnauthorized: () -> Void

    nonisolated static let freshFor: TimeInterval = 60
    nonisolated static let capacity = 4

    private struct Loaded {
        let model: MediaDetailViewModel
        let at: Date
    }

    @ObservationIgnored private var loaded: [MediaRef.ID: Loaded] = [:]
    @ObservationIgnored private var recency: [MediaRef.ID] = []
    /// Your state for a title after a hold menu's write, newer than the copy a poster holds until its screen reloads.
    /// A change made anywhere else drops it.
    @ObservationIgnored private var fresh: [MediaRef.ID: UserMediaState?] = [:]
    @ObservationIgnored private var observer: Task<Void, Never>?

    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository,
        peopleRepository: PeopleRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.onUnauthorized = onUnauthorized
        observer = Task { [weak self] in
            for await notification in NotificationCenter.default.notifications(named: .mediaStateDidChange) {
                guard let self else { return }
                handleChange(notification.userInfo)
            }
        }
    }

    /// Your state for the title as the menu should show it: a hold menu's own newer answer, else the poster's copy.
    func viewerState(for ref: MediaRef, poster: UserMediaState?) -> UserMediaState? {
        if let state = fresh[ref.id] { return state }
        return poster
    }

    /// Loads the title (or reuses it), then makes the choice the way the page would. `open` opens the title's page
    /// with a handoff, for a choice the page finishes with its own screens.
    func run(_ choice: Choice, on ref: MediaRef, open: @escaping (RailHandoff) -> Void) {
        Task { [weak self] in
            guard let self, let model = await model(for: ref), let detail = model.detail else { return }
            active = model
            perform(choice, model: model, detail: detail, open: open)
        }
    }

    // MARK: The choices, by the rail's routes

    private func perform(_ choice: Choice, model: MediaDetailViewModel, detail: MediaDetail, open: (RailHandoff) -> Void) {
        let config = model.viewer.map { TrendingRailConfig.make(title: detail.displayTitle, ref: detail.ref, viewer: $0) }
        switch choice {
        case let .watched(wasFilled):
            guard (config?.isEyeSelected ?? false) == wasFilled else { return }
            watch(isFilled: wasFilled, model: model, detail: detail, open: open)
        case let .planning(wasFilled):
            let lead = config?.lead ?? .plan
            guard lead == (wasFilled ? .planned : .plan) else { return }
            plan(isPlanned: wasFilled, model: model, detail: detail, open: open)
        case .removeFromLibrary:
            remove(model: model, detail: detail)
        }
    }

    /// The page's eye (`TrendingRailRouter.eye`).
    private func watch(isFilled: Bool, model: MediaDetailViewModel, detail: MediaDetail, open: (RailHandoff) -> Void) {
        let ref = detail.ref
        if ref.isTVFamily || model.tracksAnime {
            // Marks every aired episode, or asks first; a filled eye undoes only what it added.
            model.tapTVEye()
            return
        }
        if ref.isAnime {
            // With no anime state the log screen is the one way to say it's watched.
            open(.eye)
            return
        }
        if ref.isSingleWeight {
            _ = isFilled ? model.removeTracking(for: detail) : model.markConsumed(for: detail)
            return
        }
        if ref.mediaType == "game" {
            if model.viewer?.game?.hasLivePlaythrough == true {
                open(.eye)
            } else {
                _ = model.performGameAction(isFilled ? "undo_completed" : "mark_completed", for: detail)
            }
            return
        }
        if ref.readsLikeBook {
            switch ReadingRouting.eyeTap(book: model.viewer?.book, isRead: isFilled, noun: ref.mediaType) {
            case .openLog: open(.eye)
            case .undoRead: _ = model.performBookAction("undo_read", for: detail)
            case let .explain(reason): model.quickActionErrorMessage = reason
            case .markRead: _ = model.performBookAction("mark_read", for: detail)
            }
            return
        }
        open(.eye)
    }

    /// The bookmark (`TrendingRailRouter.lead`).
    private func plan(isPlanned: Bool, model: MediaDetailViewModel, detail: MediaDetail, open: (RailHandoff) -> Void) {
        let isTVFamily = detail.ref.isTVFamily || model.tracksAnime
        if !isPlanned {
            if isTVFamily {
                _ = model.performTVAction(.planning)
            } else {
                _ = model.performQuickAction(.planning, for: detail)
            }
        } else if isTVFamily {
            // A show's or anime's Remove asks first, on the page: a Planning-only show goes there for it.
            open(.track)
        } else {
            _ = model.removeTracking(for: detail)
        }
    }

    /// Remove from Library asks first, as on the page: a show or anime with its count (TV-003), a book or game plainly.
    private func remove(model: MediaDetailViewModel, detail: MediaDetail) {
        if model.tracksAnime, let anime = model.viewer?.anime {
            model.tvConfirmation = TVPageConfirmation(
                kind: .remove,
                prompt: AnimeTrackingPresentation.removePrompt(for: anime),
                expectedVersion: anime.version
            )
        } else if detail.ref.isTVFamily, let tv = model.viewer?.tv {
            model.tvConfirmation = TVPageConfirmation(
                kind: .remove,
                prompt: TVTrackingPresentation.removePrompt(count: tv.removeCount),
                expectedVersion: tv.version
            )
        } else {
            question = Question(prompt: "Remove from Library?", confirmTitle: "Remove from Library") {
                _ = model.removeTracking(for: detail)
            }
        }
    }

    // MARK: Models

    /// The title's page model with its detail and tracking loaded, as Home's Trending rail loads it: not the page's
    /// `load()`, which also fetches reviews, friends and outside ratings.
    private func model(for ref: MediaRef) async -> MediaDetailViewModel? {
        if let entry = loaded[ref.id],
           entry.model.hasPendingWrites || Date().timeIntervalSince(entry.at) < Self.freshFor {
            touch(ref.id)
            return entry.model
        }
        let model = MediaDetailViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        )
        do {
            var detail = try await mediaRepository.detail(ref: ref)
            // The page's rule (`trackingToMerge`): games carry everything in the detail; a title with no tracking has
            // nothing to read.
            if detail.ref.mediaType != "game", detail.userState?.isTracked == true || detail.userState?.status != nil,
               let tracking = try? await trackingRepository.detail(ref: detail.ref), let state = detail.userState {
                detail.userState = state.applying(tracking)
            }
            model.detail = detail
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            } else {
                notice = "Spine couldn't load this title. Try again."
            }
            return nil
        }
        loaded[ref.id] = Loaded(model: model, at: Date())
        touch(ref.id)
        evict()
        return model
    }

    private func touch(_ key: MediaRef.ID) {
        recency.removeAll { $0 == key }
        recency.append(key)
    }

    /// Four titles stay loaded: the least recently used goes first, never one with a write on its way or a question up.
    private func evict() {
        while loaded.count > Self.capacity {
            guard let key = recency.first(where: { key in
                guard let model = loaded[key]?.model else { return true }
                return !model.hasPendingWrites && model !== active
            }) else { return }
            loaded[key] = nil
            recency.removeAll { $0 == key }
        }
    }

    // MARK: Changes

    /// A hold menu's own write keeps its answer for the menu; a change made anywhere else drops what was kept, so the
    /// next menu and the next choice start from the poster's screen and the server again.
    private func handleChange(_ userInfo: [AnyHashable: Any]?) {
        guard let ref = userInfo?["ref"] as? MediaRef else { return }
        let origin = userInfo?["origin"] as? UUID
        if let entry = loaded[ref.id], origin == entry.model.changeOrigin {
            fresh.updateValue(entry.model.viewer, forKey: ref.id)
            return
        }
        fresh[ref.id] = nil
        if let entry = loaded[ref.id], !entry.model.hasPendingWrites, entry.model !== active {
            loaded[ref.id] = nil
            recency.removeAll { $0 == ref.id }
        }
    }
}
