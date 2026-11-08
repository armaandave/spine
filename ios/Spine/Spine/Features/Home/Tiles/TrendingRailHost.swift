import SwiftUI

/// The Trending panel's rail: the media page's own Liquid Glass `ActionRail` with a lead slot that follows where the
/// title stands in your library, the eye, Open in the middle as an icon, the stars and the heart. It is the only view
/// in the Tiles folder that touches the shared rail, and it adds no glass of its own.
///
/// The taps are the page's: Home owns a `MediaDetailViewModel` per title (`TrendingRailStore`) and calls the methods the
/// page calls. Where the page would open a screen (a log composer, a sheet), the tap goes to the title's page with
/// that intent attached (`RailHandoff`), and the page finishes it with its own UI.
struct TrendingRailHost: View {
    let title: TrendingTitle
    let entry: TrendingRailStore.Entry?
    let store: TrendingRailStore
    let actions: HomeTilesActions

    var body: some View {
        let viewModel = entry?.viewModel
        let phase = entry?.phase ?? .loading
        let viewer: UserMediaState? = {
            guard case .ready = phase else { return nil }
            return viewModel?.viewer
        }()
        let config = viewer.map { viewer in
            TrendingRailConfig.make(
                title: title.media.displayTitle,
                ref: title.media.ref,
                viewer: viewer,
                ring: TrendingRailRing.make(media: title.media, viewer: viewer),
                ratingUnavailableReason: viewModel.flatMap { Self.ratingUnavailableReason($0) }
            )
        }
        let router = entry.map { TrendingRailRouter(entry: $0, title: title, actions: actions) }
        let name = title.media.displayTitle

        ActionRail(
            ratingPicker: Binding(
                get: { entry?.picker ?? MediaRatingPickerState() },
                set: { entry?.picker = $0 }
            ),
            isLiked: config?.isLiked ?? false,
            showsEye: true,
            showsRating: true,
            offersRatingAfterBaseAction: config?.offersRatingAfterBaseAction ?? false,
            trackLabel: config?.leadLabel ?? "Plan \(name)",
            eyeLabel: config?.eyeLabel,
            eyeHint: config?.eyeHint,
            isEyeSelected: config?.isEyeSelected ?? false,
            isEyeLoading: viewModel?.confirmingEyeWrite ?? false,
            isRatingFilled: config?.isRatingFilled ?? false,
            onRatingTap: config?.ratingUnavailableReason.map { reason in { viewModel?.tvNotice = reason } },
            lead: config?.railLead ?? .symbol(name: "bookmark", size: 20),
            leadIdentifier: "trending.rail.lead",
            center: RailCenterButton(
                systemName: "arrow.up.forward",
                label: "Open \(name)",
                identifier: "trending.rail.open",
                action: { actions.onOpenTrending(title) }
            ),
            isLoadingState: config == nil && phase != .failed,
            stateFailed: phase == .failed,
            identifierPrefix: "trending.rail",
            onTrack: { if let config { router?.lead(config) } },
            onLike: { router?.like() },
            onEye: { if let config { router?.eye(config) } },
            onRating: { router?.rate(halfSteps: $0) }
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel(phase == .failed ? "Your actions on \(name) couldn\u{2019}t load" : "Your actions on \(name)")
        .onChange(of: viewModel?.viewer?.rating) { _, rating in
            entry?.picker.syncConfirmed(rating?.ratingHalfSteps ?? 0)
        }
        .onChange(of: viewModel?.viewer?.status) { _, status in
            entry?.picker.syncCompleted(status?.caseInsensitiveCompare("Completed") == .orderedSame)
        }
        .confirmationDialog(
            viewModel?.tvConfirmation?.prompt ?? "",
            isPresented: Binding(
                get: { viewModel?.tvConfirmation != nil },
                set: { if !$0 { viewModel?.tvConfirmation = nil } }
            ),
            titleVisibility: .visible,
            presenting: viewModel?.tvConfirmation
        ) { confirmation in
            Button(confirmation.confirmTitle, role: confirmation.isDestructive ? .destructive : nil) {
                viewModel?.confirmTV(confirmation)
            }
            Button("Cancel", role: .cancel) { viewModel?.tvConfirmation = nil }
        }
    }

    /// Show and anime stars can't be set before anything has aired: the page's reason (`tvRatingUnavailableReason`).
    @MainActor
    static func ratingUnavailableReason(_ viewModel: MediaDetailViewModel) -> String? {
        let ref = viewModel.ref
        guard ref.isTVFamily || viewModel.tracksAnime else { return nil }
        if let anime = viewModel.viewer?.anime {
            guard !anime.availableActions.contains("rate"), let reason = anime.actionReasons["rate"],
                  viewModel.viewer?.rating == nil else { return nil }
            return reason
        }
        guard let tv = viewModel.viewer?.tv else { return nil }
        let actions = tv.season?.availableActions ?? tv.availableActions
        let reasons = tv.season?.actionReasons ?? tv.actionReasons
        guard !actions.contains("rate"), let reason = reasons["rate"], viewModel.viewer?.rating == nil else { return nil }
        return reason
    }
}

/// What each tap on the rail does for a title, ported from the page's `trackAction`, `eyeAction`, `likeAction` and
/// `ratingAction`. A write goes through the title's model; a screen goes to the page as a handoff.
@MainActor
struct TrendingRailRouter {
    let entry: TrendingRailStore.Entry
    let title: TrendingTitle
    let actions: HomeTilesActions

    private var model: MediaDetailViewModel { entry.viewModel }

    private var pickerBinding: Binding<MediaRatingPickerState> {
        Binding(get: { entry.picker }, set: { entry.picker = $0 })
    }

    private func handoff(_ intent: RailHandoff) {
        actions.onRailHandoff(title, intent)
    }

    // MARK: Lead

    func lead(_ config: TrendingRailConfig) {
        guard let detail = model.detail else { return }
        let isTVFamily = detail.ref.isTVFamily || model.tracksAnime
        switch config.lead {
        case .plan:
            if isTVFamily {
                model.performTVAction(.planning)
            } else {
                model.performQuickAction(.planning, for: detail)
            }
        case .planned:
            // A show's or anime's Remove asks first, on the page: a Planning-only show goes there for it.
            if isTVFamily { handoff(.track) } else { model.removeTracking(for: detail) }
        case .ring:
            guard let viewer = model.viewer else { return }
            let item = LibraryItem(media: title.media, tracking: TrendingRailRing.trackingState(from: viewer))
            let run = actions.onQuickAction
            Task { _ = await run(item) }
        case .track:
            handoff(.track)
        }
    }

    // MARK: Eye

    func eye(_ config: TrendingRailConfig) {
        guard let detail = model.detail else { return }
        let ref = detail.ref
        if ref.isTVFamily || model.tracksAnime {
            model.tapTVEye()
            return
        }
        if ref.isAnime {
            // With no anime state the log screen is the one way to say it's watched.
            handoff(.eye)
            return
        }
        if ref.isSingleWeight {
            if config.isEyeSelected {
                let removal = model.removeTracking(for: detail)
                Task {
                    if await removal.value { entry.picker.resetAfterUnwatch() } else { entry.picker.rollbackWatch() }
                }
            } else {
                rollBackWatchIfFails(model.markConsumed(for: detail))
            }
            return
        }
        if ref.mediaType == "game" {
            if model.viewer?.game?.hasLivePlaythrough == true {
                handoff(.eye)
            } else if config.isEyeSelected {
                resetAfterUnwatchIfSucceeds(model.performGameAction("undo_completed", for: detail))
            } else {
                rollBackWatchIfFails(model.performGameAction("mark_completed", for: detail))
            }
            return
        }
        if ref.readsLikeBook {
            switch ReadingRouting.eyeTap(book: model.viewer?.book, isRead: config.isEyeSelected, noun: ref.mediaType) {
            case .openLog:
                handoff(.eye)
            case .undoRead:
                resetAfterUnwatchIfSucceeds(model.performBookAction("undo_read", for: detail))
            case let .explain(reason):
                model.quickActionErrorMessage = reason
            case .markRead:
                rollBackWatchIfFails(model.performBookAction("mark_read", for: detail))
            }
            return
        }
        handoff(.eye)
    }

    private func rollBackWatchIfFails(_ write: Task<Bool, Never>) {
        Task {
            if !(await write.value) { entry.picker.rollbackWatch() }
        }
    }

    private func resetAfterUnwatchIfSucceeds(_ write: Task<Bool, Never>) {
        Task {
            if await write.value { entry.picker.resetAfterUnwatch() }
        }
    }

    // MARK: Heart

    func like() {
        guard let detail = model.detail else { return }
        let isGiving = model.viewer?.hasLiked != true
        let usesBookGameActions = detail.ref.readsLikeBook || detail.ref.mediaType == "game"
        if usesBookGameActions,
           ReadingRouting.opinionOpensLog(book: model.viewer?.book, isGiving: isGiving)
            || (isGiving && model.viewer?.game?.hasLivePlaythrough == true) {
            handoff(.like)
            return
        }
        MediaOpinionRouter(page: model, picker: pickerBinding) { _, _ in handoff(.like) }.like(detail)
    }

    // MARK: Stars

    func rate(halfSteps: Int) {
        guard let detail = model.detail else { return }
        let ref = detail.ref
        if (ref.isTVFamily && ref.mediaType != "episode") || model.tracksAnime {
            MediaOpinionRouter(page: model, picker: pickerBinding) { steps, _ in
                handoff(.rate(halfSteps: steps ?? halfSteps))
            }.rate(halfSteps: halfSteps)
            return
        }
        guard !ref.isTVFamily else { return }
        if ReadingRouting.opinionOpensLog(book: model.viewer?.book, isGiving: halfSteps > 0)
            || (halfSteps > 0 && model.viewer?.game?.hasLivePlaythrough == true) {
            entry.picker.syncConfirmed(model.viewer?.rating?.ratingHalfSteps ?? 0)
            entry.picker.rollbackWatch()
            handoff(.rate(halfSteps: halfSteps))
            return
        }
        let write = model.setCurrentRating(for: detail, halfSteps: halfSteps)
        Task {
            if !(await write.value) {
                entry.picker.syncConfirmed(model.viewer?.rating?.ratingHalfSteps ?? 0)
            }
        }
    }
}
