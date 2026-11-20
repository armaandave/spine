import SwiftUI
import UIKit

/// One title's hold-menu state, watched by that title's posters only: your state after the latest change, newer than a
/// poster's copy, and what a tap just asked for, shown at once while its write is on its way.
@MainActor
@Observable
final class PosterHoldTitle {
    /// Your state after the latest change, newer than the poster's copy: `.some(nil)` once it isn't in your library,
    /// nil while there's nothing newer.
    fileprivate(set) var fresh: UserMediaState??
    /// The eye as just tapped, until its write lands or fails.
    fileprivate(set) var eye: Bool?
    /// The bookmark as just tapped, until its write lands or fails.
    fileprivate(set) var planning: Bool?

    fileprivate func clearTaps() {
        eye = nil
        planning = nil
    }
}

/// Runs what a poster's hold menu asks for. Each title gets the page's own model (`MediaDetailViewModel`), filled the way
/// Home's Trending rail fills it (`TrendingRailStore`), and each choice takes the page's route: a write goes through the
/// model's queue, and a screen (a log, a sheet) opens on the title's page as a handoff.
///
/// The eye and the bookmark flip where they were tapped, with a tap you feel, and the menu stays open; a write that
/// fails flips back with an error buzz. One per signed-in app, in the environment from `AppShellView`, where
/// `posterHoldHost` shows its questions, notices and the Add to List sheet.
@MainActor
@Observable
final class PosterHoldController {
    enum Choice: Equatable {
        /// The eye: marks the title watched. Nothing happens if it's watched already.
        case markWatched
        /// The bookmark, as the menu showed it: nothing happens if the title has changed since.
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
    @ObservationIgnored private var loading: [MediaRef.ID: Task<MediaDetailViewModel?, Never>] = [:]
    @ObservationIgnored private var recency: [MediaRef.ID] = []
    @ObservationIgnored private var titles: [MediaRef.ID: PosterHoldTitle] = [:]
    /// Titles whose state is being read again after a change made elsewhere, and ones changed again meanwhile.
    @ObservationIgnored private var refreshing: Set<MediaRef.ID> = []
    @ObservationIgnored private var changedElsewhere: Set<MediaRef.ID> = []
    @ObservationIgnored private var observer: Task<Void, Never>?
    @ObservationIgnored private let tapFeedback = UIImpactFeedbackGenerator(style: .medium)
    @ObservationIgnored private let resultFeedback = UINotificationFeedbackGenerator()

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

    /// A title's hold-menu state, made the first time a menu asks.
    func title(for ref: MediaRef) -> PosterHoldTitle {
        if let title = titles[ref.id] { return title }
        let title = PosterHoldTitle()
        titles[ref.id] = title
        return title
    }

    /// Your state for the title as the menu should show it: the latest change's, else the poster's copy.
    func viewerState(for ref: MediaRef, poster: UserMediaState?) -> UserMediaState? {
        if case let .some(state) = title(for: ref).fresh { return state }
        return poster
    }

    /// Loads the title (or reuses it), then makes the choice the way the page would. `flips` shows the tapped eye or
    /// bookmark's new state at once, with a tap you feel, while the write is on its way (the menu stays open for it).
    /// `open` opens the title's page with a handoff, for a choice the page finishes with its own screens.
    func run(_ choice: Choice, on ref: MediaRef, flips: Bool = false, open: @escaping (RailHandoff) -> Void) {
        let title = title(for: ref)
        if flips {
            switch choice {
            case .markWatched:
                // A second tap while the first is on its way does nothing.
                guard title.eye == nil else { return }
                title.eye = true
            case let .planning(wasFilled):
                guard title.planning == nil else { return }
                title.planning = !wasFilled
            case .removeFromLibrary: break
            }
            tapFeedback.impactOccurred()
        }
        Task { [weak self] in
            guard let self else { return }
            guard let model = await model(for: ref), let detail = model.detail else {
                if flips { failed(title) }
                return
            }
            active = model
            // The menu now shows the title as it is, in case the poster's copy was out of date (a choice that no
            // longer applies then does nothing, and the menu has already changed to say why).
            title.fresh = .some(model.viewer)
            let write = perform(choice, model: model, detail: detail, open: open)
            guard flips else { return }
            // Nothing written: the title changed since the menu opened, or the page has to finish it.
            guard let write else {
                title.clearTaps()
                return
            }
            if await write.value {
                title.fresh = .some(model.viewer)
                title.clearTaps()
            } else {
                failed(title)
            }
        }
    }

    private func failed(_ title: PosterHoldTitle) {
        title.clearTaps()
        resultFeedback.notificationOccurred(.error)
    }

    // MARK: The choices, by the page's routes

    /// The choice's write, or nil when it wrote nothing here (it opened the page, asked first, or the title changed).
    private func perform(
        _ choice: Choice,
        model: MediaDetailViewModel,
        detail: MediaDetail,
        open: (RailHandoff) -> Void
    ) -> Task<Bool, Never>? {
        let config = model.viewer.map { TrendingRailConfig.make(title: detail.displayTitle, ref: detail.ref, viewer: $0) }
        switch choice {
        case .markWatched:
            guard config?.isEyeSelected != true else { return nil }
            return markWatched(model: model, detail: detail, open: open)
        case let .planning(wasFilled):
            guard PosterHoldMenu.planning(ref: detail.ref, viewer: model.viewer) == (wasFilled ? .remove : .add) else {
                return nil
            }
            return plan(isPlanned: wasFilled, model: model, detail: detail)
        case .removeFromLibrary:
            remove(model: model, detail: detail)
            return nil
        }
    }

    /// The page's eye on a title you haven't finished (`TrendingRailRouter.eye`): it marks the title watched, or hands it
    /// to the page when the page has to finish it.
    private func markWatched(
        model: MediaDetailViewModel,
        detail: MediaDetail,
        open: (RailHandoff) -> Void
    ) -> Task<Bool, Never>? {
        let ref = detail.ref
        if ref.isTVFamily || model.tracksAnime {
            // Marks every aired episode, or asks first.
            model.tapTVEye()
            return nil
        }
        if ref.isAnime {
            // With no anime state the log screen is the one way to say it's watched.
            open(.eye)
            return nil
        }
        if ref.isSingleWeight {
            return model.markConsumed(for: detail)
        }
        if ref.mediaType == "game" {
            if model.viewer?.game?.hasLivePlaythrough == true {
                open(.eye)
                return nil
            }
            return model.performGameAction("mark_completed", for: detail)
        }
        if ref.readsLikeBook {
            switch ReadingRouting.eyeTap(book: model.viewer?.book, isRead: false, noun: ref.mediaType) {
            case .openLog:
                open(.eye)
                return nil
            case let .explain(reason):
                model.quickActionErrorMessage = reason
                return nil
            case .markRead:
                return model.performBookAction("mark_read", for: detail)
            case .undoRead:
                return nil
            }
        }
        open(.eye)
        return nil
    }

    /// The page's bookmark (`planningAction`): adds the title to Planning, or takes a planned one back out.
    private func plan(isPlanned: Bool, model: MediaDetailViewModel, detail: MediaDetail) -> Task<Bool, Never> {
        if !isPlanned {
            if detail.ref.isTVFamily || model.tracksAnime {
                return model.performTVAction(.planning)
            }
            return model.performQuickAction(.planning, for: detail)
        }
        if model.tracksAnime, let anime = model.viewer?.anime {
            return model.removeTVShow(expectedVersion: anime.version)
        }
        if detail.ref.isTVFamily, let tv = model.viewer?.tv {
            return model.removeTVShow(expectedVersion: tv.version)
        }
        return model.removeTracking(for: detail)
    }

    /// Remove from Library asks first, as on the page: a show or anime with its count (TV-003), a book or game plainly.
    /// When the loaded title says it can't be removed yet (its logs block it), the page's reason shows instead.
    private func remove(model: MediaDetailViewModel, detail: MediaDetail) {
        if model.tracksAnime, let anime = model.viewer?.anime {
            guard anime.availableActions.contains("remove") else {
                model.tvNotice = anime.actionReasons["remove"] ?? Self.cantRemoveYet
                return
            }
            model.tvConfirmation = TVPageConfirmation(
                kind: .remove,
                prompt: AnimeTrackingPresentation.removePrompt(for: anime),
                expectedVersion: anime.version
            )
        } else if detail.ref.isTVFamily, let tv = model.viewer?.tv {
            guard tv.availableActions.contains("remove") else {
                model.tvNotice = tv.actionReasons["remove"] ?? Self.cantRemoveYet
                return
            }
            model.tvConfirmation = TVPageConfirmation(
                kind: .remove,
                prompt: TVTrackingPresentation.removePrompt(count: tv.removeCount),
                expectedVersion: tv.version
            )
        } else if let reason = Self.removalBlock(model.viewer) {
            model.quickActionErrorMessage = reason
        } else {
            question = Question(prompt: "Remove from Library?", confirmTitle: "Remove from Library") {
                _ = model.removeTracking(for: detail)
            }
        }
    }

    private static let cantRemoveYet = "This can't be removed from your library yet."

    /// A book's or game's reason it can't leave the library yet, from the loaded title.
    private static func removalBlock(_ viewer: UserMediaState?) -> String? {
        if let book = viewer?.book, !book.canRemoveTracking {
            return book.removeTrackingReason ?? cantRemoveYet
        }
        if let game = viewer?.game, !game.canRemoveTracking {
            return "To remove this game, delete its playthroughs and logs first."
        }
        return nil
    }

    // MARK: Models

    /// The title's page model with its detail and tracking loaded, as Home's Trending rail loads it: not the page's
    /// `load()`, which also fetches reviews, friends and outside ratings. A load already on its way is shared, so two
    /// quick taps write through one model.
    private func model(for ref: MediaRef, reportsFailure: Bool = true) async -> MediaDetailViewModel? {
        if let entry = loaded[ref.id],
           entry.model.hasPendingWrites || Date().timeIntervalSince(entry.at) < Self.freshFor {
            touch(ref.id)
            return entry.model
        }
        if let loading = loading[ref.id] { return await loading.value }
        let load = Task { await self.load(ref, reportsFailure: reportsFailure) }
        loading[ref.id] = load
        let model = await load.value
        loading[ref.id] = nil
        return model
    }

    private func load(_ ref: MediaRef, reportsFailure: Bool) async -> MediaDetailViewModel? {
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
            } else if reportsFailure {
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

    /// A hold menu's own write gives its title's menus the new state. A change made anywhere else (the title's page,
    /// another screen) reads the title again, so a poster whose screen doesn't reload still opens on the truth.
    private func handleChange(_ userInfo: [AnyHashable: Any]?) {
        guard let ref = userInfo?["ref"] as? MediaRef, let title = titles[ref.id] else { return }
        let origin = userInfo?["origin"] as? UUID
        if let entry = loaded[ref.id], origin == entry.model.changeOrigin {
            title.fresh = .some(entry.model.viewer)
            return
        }
        // A burst of changes (ticking episodes) reads the title once after it, and once more if more came meanwhile.
        changedElsewhere.insert(ref.id)
        guard !refreshing.contains(ref.id) else { return }
        refreshing.insert(ref.id)
        Task { [weak self] in
            guard let self else { return }
            while changedElsewhere.contains(ref.id) {
                changedElsewhere.remove(ref.id)
                try? await Task.sleep(for: .milliseconds(800))
                // The loaded copy is out of date; one with a write still on its way is newer than the server.
                if let entry = loaded[ref.id], !entry.model.hasPendingWrites {
                    loaded[ref.id] = nil
                    recency.removeAll { $0 == ref.id }
                }
                if let model = await model(for: ref, reportsFailure: false) {
                    title.fresh = .some(model.viewer)
                }
            }
            refreshing.remove(ref.id)
        }
    }
}
