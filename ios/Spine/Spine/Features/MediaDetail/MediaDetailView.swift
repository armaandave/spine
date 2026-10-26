import SwiftUI
import UIKit

enum MusicEnrichmentState: Equatable {
    case idle
    case loading
    case loaded
    case failed
}

@MainActor
@Observable
final class MediaDetailViewModel {
    var detail: MediaDetail?
    var reviews: [MediaReview] = []
    var reviewCount = 0
    var isLoading = true
    var isLoadingReviews = false
    var isSavingProgress = false
    var errorMessage: String?
    var reviewsErrorMessage: String?
    var quickActionErrorMessage: String?
    var progressErrorMessage: String?
    var likeErrorMessage: String?
    var formErrorMessage: String?
    var musicEnrichmentState = MusicEnrichmentState.idle
    /// What the page says instead of writing: a blocked eye or untick, or nothing aired yet (TV-102, TV-300, TV-302).
    var tvNotice: String?
    /// The native confirmation a TV write waits for, including the re-ask after a stale count (TV-705).
    var tvConfirmation: TVPageConfirmation?
    /// Confirmed TV writes the loaded episode and season lists don't show yet; a refresh that replaces the detail
    /// clears them.
    private(set) var tvConfirmed = TVConfirmedWrites()
    /// After a TV write, log or heart: the earlier-episodes question, then the season-finish composer (TV-107, TV-403).
    let tvFollowUp: TVWriteFollowUp

    /// Tags this page's MediaStateChange posts, so its own observer can skip them.
    let changeOrigin = UUID()
    let writeRefreshDelay: Duration
    let externalRefreshDelay: Duration
    private(set) var pending: [PendingWrite] = []
    private(set) var pendingWriteCount = 0
    private var eyeWritesInFlight = 0

    @ObservationIgnored private var mutationTail: Task<Void, Never>?
    @ObservationIgnored private var stateGeneration = 0
    @ObservationIgnored private var failedWriteCount = 0
    @ObservationIgnored private var retainedMutationIds: [String: UUID] = [:]
    @ObservationIgnored private var refreshTask: Task<Void, Never>?
    @ObservationIgnored private var refreshIsWaiting = false
    @ObservationIgnored private var scheduledNeed = RefreshNeed.none
    /// A saved log's envelope, handed to `tvFollowUp` once its composer has closed.
    @ObservationIgnored private var deferredTVEnvelope: TVWriteEnvelope?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void
    private let externalRatingPollInterval: Duration?
    private let externalRatingMaxPollAttempts: Int

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        externalRatingPollInterval: Duration? = nil,
        externalRatingMaxPollAttempts: Int = 30,
        writeRefreshDelay: Duration = .milliseconds(500),
        externalRefreshDelay: Duration = .milliseconds(300)
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
        self.externalRatingPollInterval = externalRatingPollInterval
        self.externalRatingMaxPollAttempts = externalRatingMaxPollAttempts
        self.writeRefreshDelay = writeRefreshDelay
        self.externalRefreshDelay = externalRefreshDelay
        tvFollowUp = TVWriteFollowUp(showRef: ref, trackingRepository: trackingRepository)
        tvFollowUp.context = { [weak self] in self?.detail }
        tvFollowUp.onResponse = { [weak self] response in
            self?.applyTVFollowUpResponse(response)
        }
    }

    /// The page's one tracking state: the confirmed `detail.userState` with pending writes applied in tap order.
    var viewer: UserMediaState? {
        guard let detail else { return nil }
        var confirmed = detail.userState ?? .untracked
        if tvConfirmed.watches != TVPendingWatches() { confirmed.tvPending = tvConfirmed.watches }
        return pending.reduce(into: confirmed) { state, write in write.project(&state) }
    }

    var hasPendingWrites: Bool { pendingWriteCount > 0 }
    var confirmingEyeWrite: Bool { eyeWritesInFlight > 0 }

    /// Waits for every write queued so far and reports whether they all succeeded. Screens that capture ids read
    /// confirmed state after this, and don't open at all when a write they waited for failed.
    @discardableResult
    func settled() async -> Bool {
        let failures = failedWriteCount
        await mutationTail?.value
        return failedWriteCount == failures
    }

    func refreshFinished() async {
        while let task = refreshTask {
            await task.value
            if task == refreshTask { return }
        }
    }

    func load() async {
        if detail != nil {
            isLoading = false
            await refreshFully()
            return
        }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loaded = try await mediaRepository.detail(ref: ref)
            detail = loaded
            isLoading = false
            async let enrichment: Void = loadMusicEnrichmentIfNeeded(for: loaded)
            async let tracking: Void = mergeTrackingIfNeeded(for: loaded)
            async let reviewLoad: Void = loadReviews()
            _ = await (enrichment, tracking, reviewLoad)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func loadMusicEnrichmentIfNeeded(for loaded: MediaDetail) async {
        guard loaded.ref.mediaType == "music" else {
            musicEnrichmentState = .idle
            return
        }
        musicEnrichmentState = .loading
        do {
            var enriched = try await mediaRepository.enrichedMusicDetail(ref: ref)
            guard detail?.id == loaded.id else { return }
            // The enrichment carries the user state from before any write made while it loaded.
            enriched.userState = detail?.userState
            detail = enriched
            musicEnrichmentState = .loaded
        } catch is CancellationError {
            musicEnrichmentState = .idle
        } catch {
            musicEnrichmentState = .failed
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    var externalRatingsPollingID: String {
        let state = detail?.externalRatingsPreparation?.state.rawValue ?? "none"
        return "\(detail?.id ?? ref.id):\(state)"
    }

    func pollExternalRatingsIfNeeded() async {
        guard detail?.externalRatingsPreparation?.state == .pending else { return }
        let expectedID = detail?.id

        for _ in 0..<externalRatingMaxPollAttempts {
            do {
                let seconds = detail?.externalRatingsPreparation?.retryAfterSeconds ?? 2
                try await Task.sleep(for: externalRatingPollInterval ?? .seconds(seconds))
                try Task.checkCancellation()
                let response = try await mediaRepository.externalRatings(ref: ref)
                guard detail?.id == expectedID else { return }
                detail = detail?.replacingExternalRatings(with: response)
                if response.externalRatingsPreparation.state != .pending {
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                if case APIError.unauthorized = error {
                    onUnauthorized()
                }
                return
            }
        }
    }

    /// Games never fetch GET /tracking: their state comes complete in the detail payload.
    private func mergeTrackingIfNeeded(for loaded: MediaDetail) async {
        let generation = stateGeneration
        guard let tracking = await trackingToMerge(for: loaded),
              generation == stateGeneration, !hasPendingWrites,
              let current = detail?.userState
        else { return }
        let merged = current.applying(tracking)
        if merged != current { detail?.userState = merged }
    }

    private func trackingToMerge(for loaded: MediaDetail) async -> TrackingState? {
        guard loaded.ref.mediaType != "game",
              loaded.userState?.isTracked == true || loaded.userState?.status != nil
        else { return nil }
        do {
            return try await trackingRepository.detail(ref: loaded.ref)
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return nil
        }
    }

    func loadReviews() async {
        isLoadingReviews = true
        reviewsErrorMessage = nil
        defer { isLoadingReviews = false }

        do {
            let page = try await mediaRepository.reviewPage(ref: ref, page: nil)
            reviews = page.results
            reviewCount = page.count
        } catch {
            reviewsErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func toggleLike(for review: MediaReview) async {
        do {
            let state = try await diaryRepository.setLike(entryId: review.id, liked: !review.viewerHasLiked)
            guard let index = reviews.firstIndex(where: { $0.id == review.id }) else { return }
            reviews[index].viewerHasLiked = state.liked
            reviews[index].likeCount = state.likeCount
        } catch {
            reviewsErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    // MARK: Refresh policy

    private var refreshesWholeDetail: Bool {
        ["tv", "season", "episode"].contains(ref.mediaType)
    }

    /// Coalesces refreshes: one waits at a time, keeping the strongest need, and runs after the write queue settles.
    func scheduleRefresh(_ need: RefreshNeed, after delay: Duration) {
        guard need > .none else { return }
        scheduledNeed = max(scheduledNeed, refreshesWholeDetail ? .full : need)
        let previous = refreshTask
        if refreshIsWaiting { previous?.cancel() }
        refreshIsWaiting = true
        refreshTask = Task {
            if delay > .zero { try? await Task.sleep(for: delay) }
            guard !Task.isCancelled else { return }
            await previous?.value
            await settled()
            guard !Task.isCancelled else { return }
            refreshIsWaiting = false
            let need = scheduledNeed
            scheduledNeed = .none
            await runRefresh(need)
        }
    }

    /// A MediaStateChange post. The page has already applied its own writes, so it skips posts carrying its origin.
    func handleMediaStateChange(_ userInfo: [AnyHashable: Any]?) {
        guard userInfo?["origin"] as? UUID != changeOrigin,
              let changedRef = userInfo?["ref"] as? MediaRef,
              let detail,
              completionMayHaveChanged(in: detail, for: changedRef)
        else { return }
        scheduleRefresh(.full, after: externalRefreshDelay)
    }

    private func completionMayHaveChanged(in detail: MediaDetail, for changedRef: MediaRef) -> Bool {
        if changedRef.id == ref.id {
            return true
        }
        // A show or season write elsewhere changes which episodes are watched, so episode pages refresh too (TV-705).
        if ["tv", "season", "episode"].contains(detail.ref.mediaType),
           detail.ref.source == changedRef.source,
           detail.ref.mediaId == changedRef.mediaId {
            return true
        }
        return false
    }

    private func runRefresh(_ need: RefreshNeed) async {
        guard detail != nil else { return }
        switch need {
        case .none:
            return
        case .community:
            await refreshCommunity()
        case .communityAndReviews:
            async let community: Void = refreshCommunity()
            async let reviewLoad: Void = loadReviews()
            _ = await (community, reviewLoad)
        case .full:
            await refreshFully()
        }
    }

    private func refreshCommunity() async {
        do {
            let community = try await mediaRepository.community(ref: ref)
            if detail?.community != community { detail?.community = community }
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    /// Never clears anything first, and never overwrites a write that confirmed (or is pending) while it loaded.
    private func refreshFully() async {
        let generation = stateGeneration
        do {
            let loaded = try await mediaRepository.detail(ref: ref)
            var userState = loaded.userState
            if let tracking = await trackingToMerge(for: loaded) {
                userState = (userState ?? .untracked).applying(tracking)
            }
            let lostRace = generation != stateGeneration || hasPendingWrites
            if refreshesWholeDetail || detail == nil {
                var next = loaded
                next.userState = lostRace ? detail?.userState : userState
                detail = next
                // The fresh lists show every confirmed write, unless one confirmed while they loaded.
                if !lostRace { tvConfirmed = TVConfirmedWrites() }
            } else {
                // Patch only what can change, so music enrichment and other loaded extras stay.
                if !lostRace, detail?.userState != userState { detail?.userState = userState }
                if detail?.community != loaded.community { detail?.community = loaded.community }
                if detail?.completion != loaded.completion { detail?.completion = loaded.completion }
            }
            errorMessage = nil
            if lostRace { scheduleRefresh(.full, after: .zero) }
            await loadReviews()
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    // MARK: Write queue

    /// Shows `project` at once, then sends on the serial queue. `send` builds its request when it runs, from
    /// confirmed state only. Its outcome replaces the confirmed state in the same turn that drops the projection.
    ///
    /// `onSuccess` runs in the same turn as the confirmation. `onConflict` may claim an error (returning true): the
    /// write then fails without an error message, and the page refreshes.
    @discardableResult
    private func enqueue(
        _ project: TrackingProjection.Projection?,
        ref: MediaRef,
        refresh: RefreshNeed = .none,
        errorsTo errorTarget: ReferenceWritableKeyPath<MediaDetailViewModel, String?>? = nil,
        eyeSpinner: Bool = false,
        onSuccess: ((WriteOutcome) -> Void)? = nil,
        onConflict: ((Error) -> Bool)? = nil,
        send: @escaping () async throws -> WriteOutcome
    ) -> Task<Bool, Never> {
        let errorPath = errorTarget ?? \.quickActionErrorMessage
        let write = project.map { PendingWrite(project: $0) }
        if let write { pending.append(write) }
        pendingWriteCount += 1
        if eyeSpinner { eyeWritesInFlight += 1 }
        self[keyPath: errorPath] = nil
        let previous = mutationTail
        let task = Task<Bool, Never> {
            await previous?.value
            defer {
                pendingWriteCount -= 1
                if eyeSpinner { eyeWritesInFlight -= 1 }
            }
            do {
                let outcome = try await send()
                pending.removeAll { $0.id == write?.id }
                confirm(outcome)
                onSuccess?(outcome)
                retainedMutationIds.removeAll()
                MediaStateChange.post(ref: ref, origin: changeOrigin)
                switch outcome {
                case .unknown:
                    scheduleRefresh(.full, after: .zero)
                case .untracked:
                    // Removal also removes the rating and heart from the community stats.
                    scheduleRefresh(max(refresh, .community), after: writeRefreshDelay)
                default:
                    scheduleRefresh(refresh, after: writeRefreshDelay)
                }
                return true
            } catch {
                pending.removeAll { $0.id == write?.id }
                failedWriteCount += 1
                if onConflict?(error) == true {
                    scheduleRefresh(.full, after: .zero)
                    return false
                }
                // TV errors carry user copy, including the retryable 503 (TV-103).
                let message = ref.isTVFamily ? (error as? APIError)?.apiErrorMessage : nil
                self[keyPath: errorPath] = message ?? error.localizedDescription
                if case APIError.unauthorized = error { onUnauthorized() }
                // A lost response may still have landed.
                if case APIError.network = error { scheduleRefresh(.full, after: .zero) }
                return false
            }
        }
        mutationTail = Task { _ = await task.value }
        return task
    }

    private func confirm(_ outcome: WriteOutcome) {
        let current = detail?.userState ?? .untracked
        let next: UserMediaState
        switch outcome {
        case let .state(tracking):
            next = current.applying(tracking)
        case .untracked:
            next = current.removed()
        case let .completion(response):
            next = current.applying(response.tracking).applying(diaryEntry: response.diaryEntry)
        case let .liked(liked, serverState):
            if var serverState {
                serverState.progress = current.progress
                serverState.startDate = current.startDate
                next = serverState
            } else {
                var state = current
                state.hasLiked = liked
                next = state
            }
        case let .tv(response):
            next = Self.foldTV(response, into: current, page: ref)
        case .unknown:
            next = current
        }
        stateGeneration += 1
        if detail != nil, detail?.userState != next { detail?.userState = next }
    }

    /// Folds a TV action's response into the page (TV-705). A season or episode page whose payload had no TV state
    /// of its own takes it from the response when the response names that level, and never takes the show's
    /// tracking as its own.
    static func foldTV(_ response: TVActionResponse, into current: UserMediaState, page: MediaRef) -> UserMediaState {
        guard page.mediaType != "tv", current.tv?.season == nil, current.tv?.episode == nil else {
            return current.applying(tvEnvelope: response.envelope, tracking: response.tracking)
        }
        let envelope = response.envelope
        var tv = current.tv ?? envelope.tv ?? TVShowState()
        if page.mediaType == "season", let season = envelope.season, season.seasonNumber == page.seasonNumber {
            tv.season = season
        } else if page.isEpisode, let episode = envelope.episode,
                  episode.seasonNumber == page.seasonNumber, episode.episodeNumber == page.episodeNumber {
            tv.episode = episode
        }
        var state = current
        guard tv.season != nil || tv.episode != nil else {
            state.tv = envelope.tv ?? current.tv
            return state
        }
        state.tv = tv
        return state.applying(tvEnvelope: envelope, tracking: response.tracking)
    }

    /// Reuses the id of a failed write so a retry stays idempotent. Any success clears them all.
    private func mutationId(for key: String) -> UUID {
        if let id = retainedMutationIds[key] { return id }
        let id = UUID()
        retainedMutationIds[key] = id
        return id
    }

    private static func outcome(_ state: TrackingState?) -> WriteOutcome {
        state.map { .state($0) } ?? .untracked
    }

    /// A rating or heart marks movies, music, books and games completed (SW-200, GM-300, BK-203).
    private static func opinionImpliesCompletion(_ ref: MediaRef) -> Bool {
        ref.isSingleWeight || ["book", "game"].contains(ref.mediaType)
    }

    private var confirmedState: UserMediaState? { detail?.userState }

    private func confirmedPlaythroughId(for playthrough: GamePlaythroughState) throws -> Int {
        guard playthrough.isPending else { return playthrough.id }
        guard let current = confirmedState?.game?.currentPlaythrough, !current.isPending else { throw PendingWriteError() }
        return current.id
    }

    private func confirmedJourneyId(for journey: BookJourneyState) throws -> Int {
        guard journey.isPending else { return journey.id }
        guard let current = confirmedState?.book?.currentJourney, !current.isPending else { throw PendingWriteError() }
        return current.id
    }

    // MARK: Writes

    @discardableResult
    func toggleMediaLike(for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        let liked = !((viewer ?? detail.userState)?.hasLiked ?? false)
        let repository = mediaRepository
        if ref.isTVFamily {
            return toggleTVLike(ref: ref, liked: liked)
        }
        return enqueue(
            TrackingProjection.like(liked, impliesCompletion: Self.opinionImpliesCompletion(ref)),
            ref: ref,
            refresh: .community,
            errorsTo: \.likeErrorMessage
        ) {
            let response = try await repository.setLiked(ref: ref, liked: liked)
            return .liked(response.liked, response.media?.userState)
        }
    }

    @discardableResult
    func markConsumed(for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        let repository = trackingRepository
        return enqueue(TrackingProjection.status("Completed"), ref: ref) {
            .state(try await repository.consume(ref: ref, consumedAt: nil))
        }
    }

    @discardableResult
    func setCurrentRating(for detail: MediaDetail, halfSteps: Int) -> Task<Bool, Never> {
        let ref = detail.ref
        if ref.isTVFamily {
            // TV keeps its 0-10 wire scale: the picker's half steps are the whole values 1-10 (TV-504).
            return rateTV(steps: halfSteps)
        }
        let rating = halfSteps > 0 ? Decimal(halfSteps) / 2 : nil
        let wire = halfSteps > 0 ? String(format: "%.1f", Double(halfSteps) / 2) : nil
        let repository = trackingRepository
        return enqueue(
            TrackingProjection.rating(wire, impliesCompletion: Self.opinionImpliesCompletion(ref)),
            ref: ref,
            refresh: .community
        ) {
            .state(try await repository.update(ref: ref, request: TrackingWriteRequest(rating: rating, includesRating: true)))
        }
    }

    @discardableResult
    func performQuickAction(_ action: MediaDetailQuickAction, for detail: MediaDetail, completedAt: Date = Date()) -> Task<Bool, Never> {
        let ref = detail.ref
        if ref.mediaType == "game", action != .planning {
            let name: String
            switch action {
            case .currently: name = "start"
            case .paused: name = "pause"
            case .stopped: name = "drop"
            case .finished: name = "mark_completed"
            case .planning: name = "planning"
            }
            return performGameAction(name, for: detail)
        }
        let isBook = ref.mediaType == "book"
        let day = CalendarDateCodec.string(from: completedAt)
        let projection: TrackingProjection.Projection
        switch action {
        case .planning: projection = TrackingProjection.status("Planning")
        case .currently: projection = isBook ? TrackingProjection.startBook(today: day) : TrackingProjection.status("In progress")
        case .paused: projection = isBook ? TrackingProjection.pauseBook() : TrackingProjection.status("Paused")
        case .stopped: projection = isBook ? TrackingProjection.dropBook(today: day) : TrackingProjection.status("Dropped")
        case .finished: projection = TrackingProjection.status("Completed")
        }
        return enqueue(projection, ref: ref) { [self] in
            // Book routing reads the state confirmed when this write is sent, not the state at the tap.
            let confirmed = confirmedState
            let state: TrackingState?
            switch action {
            case .planning:
                state = try await trackingRepository.update(ref: ref, request: TrackingWriteRequest(status: "Planning"))
            case .currently:
                if isBook, confirmed?.status == "Paused" {
                    state = try await trackingRepository.performBookAction(
                        source: ref.source,
                        mediaId: ref.mediaId,
                        action: "resume",
                        request: BookActionRequest()
                    )
                } else {
                    state = try await trackingRepository.update(
                        ref: ref,
                        request: TrackingWriteRequest(
                            status: "In progress",
                            startDate: isBook ? day : nil,
                            mutationId: isBook ? mutationId(for: "book.start") : nil
                        )
                    )
                }
            case .paused:
                // The server keeps a closed journey as current; only an open one can take the pause action.
                if isBook, confirmed?.book?.hasLiveJourney == true {
                    state = try await trackingRepository.performBookAction(
                        source: ref.source,
                        mediaId: ref.mediaId,
                        action: "pause",
                        request: BookActionRequest()
                    )
                } else {
                    state = try await trackingRepository.update(ref: ref, request: TrackingWriteRequest(status: "Paused"))
                }
            case .finished:
                if isBook {
                    state = try await trackingRepository.completeBook(
                        source: ref.source,
                        mediaId: ref.mediaId,
                        completedAt: completedAt
                    )
                } else {
                    state = try await trackingRepository.consume(
                        ref: ref,
                        consumedAt: ref.isSingleWeight ? nil : completedAt
                    )
                }
            case .stopped:
                if isBook, confirmed?.book?.currentJourney != nil {
                    state = try await trackingRepository.performBookAction(
                        source: ref.source,
                        mediaId: ref.mediaId,
                        action: "drop",
                        request: BookActionRequest(endDate: day)
                    )
                } else {
                    state = try await trackingRepository.update(ref: ref, request: TrackingWriteRequest(status: "Dropped"))
                }
            }
            return Self.outcome(state)
        }
    }

    @discardableResult
    func removeTracking(for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        if ref.isSingleWeight, (viewer?.diaryCount ?? 0) > 0 {
            // SW-008: logs keep it watched and the server refuses, so don't show an unwatch that can't land.
            quickActionErrorMessage = "Delete your logs before marking this as unwatched."
            return Task { false }
        }
        let repository = trackingRepository
        return enqueue(TrackingProjection.removed, ref: ref, refresh: .community) {
            try await repository.delete(ref: ref)
            return .untracked
        }
    }

    // MARK: TV writes

    /// TV actions go to the show, whichever page sends them.
    private var tvShowRef: MediaRef { ref.parentTVRef ?? ref }

    /// The season a season page is about; nil on show and episode pages.
    private var pageSeasonNumber: Int? { ref.mediaType == "season" ? ref.seasonNumber : nil }

    /// The confirmed state of one episode: the episode page's own, a write's newer answer, or the season's list.
    func confirmedTVEpisode(seasonNumber: Int, episodeNumber: Int) -> TVEpisodeState? {
        let coordinate = TVEpisodeCoordinate(seasonNumber: seasonNumber, episodeNumber: episodeNumber)
        if let page = detail?.userState?.tv?.episode, page.coordinate == coordinate { return page }
        if let confirmed = tvConfirmed.episodes[coordinate] { return confirmed }
        if let listed = detail?.episodes?.first(where: { $0.tv?.coordinate == coordinate })?.tv { return listed }
        return detail?.tvUnlistedEpisodes?.first(where: { $0.tv.coordinate == coordinate })?.tv
    }

    /// An episode row's state: a confirmed write's answer when the loaded list predates it.
    func tvEpisodeState(_ listed: TVEpisodeState?) -> TVEpisodeState? {
        guard let listed else { return nil }
        return tvConfirmed.episodes[listed.coordinate] ?? listed
    }

    /// A season card's state: a confirmed write's answer when the loaded cards predate it.
    func tvSeasonState(_ listed: TVSeasonState?) -> TVSeasonState? {
        guard let listed else { return nil }
        return tvConfirmed.seasons[listed.seasonNumber] ?? listed
    }

    /// Sends a TV action through the write queue. `request` runs when the write is sent, from confirmed state;
    /// nil means there is nothing left to send. It names this page when it is a season or episode page, so the
    /// response's `tv` carries the page's state and composer (TV-404). The response folds in, then goes to
    /// `tvFollowUp`.
    @discardableResult
    private func sendTV(
        _ action: TVAction,
        projection: TrackingProjection.Projection? = nil,
        eyeSpinner: Bool = false,
        onStateChanged: ((TVStateChange) -> Void)? = nil,
        afterSuccess: ((TVWriteEnvelope) -> Void)? = nil,
        request: @escaping () throws -> TVActionRequest?
    ) -> Task<Bool, Never> {
        let show = tvShowRef
        let repository = trackingRepository
        let page = ref
        return enqueue(
            projection,
            ref: ref,
            refresh: .community,
            eyeSpinner: eyeSpinner,
            onSuccess: { [self] outcome in
                guard case let .tv(response) = outcome else { return }
                var projected = UserMediaState.untracked
                projection?(&projected)
                tvConfirmed.record(projected: projected.tvPending, envelope: response.envelope)
                afterSuccess?(response.envelope)
                tvFollowUp.handle(response.envelope)
            },
            onConflict: onStateChanged.map { handler in
                { error in
                    guard let change = (error as? APIError)?.tvStateChange else { return false }
                    handler(change)
                    return true
                }
            }
        ) {
            guard var body = try request() else { return .unknown }
            if page.mediaType == "season" || page.isEpisode {
                body.pageSeasonNumber = page.seasonNumber
                body.pageEpisodeNumber = page.isEpisode ? page.episodeNumber : nil
            }
            return .tv(try await repository.performTVAction(
                source: show.source,
                mediaId: show.mediaId,
                action: action,
                request: body
            ))
        }
    }

    /// Ticks an aired episode, dated now by the server (TV-100). It shows at once and a failure restores the
    /// confirmed state (TV-705).
    @discardableResult
    func tickTVEpisode(seasonNumber: Int, episodeNumber: Int) -> Task<Bool, Never> {
        sendTV(.tick, projection: TrackingProjection.tvTick(season: seasonNumber, episode: episodeNumber)) { [self] in
            TVActionRequest(
                seasonNumber: seasonNumber,
                episodeNumber: episodeNumber,
                mutationId: mutationId(for: "tv.tick.\(seasonNumber).\(episodeNumber)")
            )
        }
    }

    /// Unticks the viewing the episode's confirmed state names (TV-102). An episode no longer watched sends nothing.
    @discardableResult
    func untickTVEpisode(seasonNumber: Int, episodeNumber: Int) -> Task<Bool, Never> {
        sendTV(.untick, projection: TrackingProjection.tvUntick(season: seasonNumber, episode: episodeNumber)) { [self] in
            guard let episode = confirmedTVEpisode(seasonNumber: seasonNumber, episodeNumber: episodeNumber),
                  episode.watched
            else { return nil }
            guard let viewingId = episode.untickViewingId else {
                throw TVPageError(message: episode.untickMessage ?? "Delete your log of this episode first.")
            }
            return TVActionRequest(
                seasonNumber: seasonNumber,
                episodeNumber: episodeNumber,
                viewingId: viewingId,
                mutationId: mutationId(for: "tv.untick.\(viewingId)")
            )
        }
    }

    /// An episode tick, from a season's list, the Up next row or the episode page's eye: ticks it, unticks it, or
    /// says why it can't (TV-100, TV-102).
    func toggleTVEpisode(_ episode: TVEpisodeState) {
        let isWatched = viewer?.tvIsWatched(episode) ?? episode.watched
        if isWatched {
            let confirmed = confirmedTVEpisode(seasonNumber: episode.seasonNumber, episodeNumber: episode.episodeNumber)
            if let confirmed, confirmed.watched, confirmed.untickViewingId == nil {
                tvNotice = confirmed.untickMessage ?? "Delete your log of this episode first."
                return
            }
            untickTVEpisode(seasonNumber: episode.seasonNumber, episodeNumber: episode.episodeNumber)
        } else if episode.aired, episode.listed {
            tickTVEpisode(seasonNumber: episode.seasonNumber, episodeNumber: episode.episodeNumber)
        } else if !episode.aired {
            tvNotice = "This episode hasn't aired yet."
        }
    }

    /// The Up next row's tick: up next is aired and unwatched, so a tap ticks it, and a second tap before the page
    /// moves on unticks it.
    func toggleTVUpNext(_ upNext: TVEpisodePointer) {
        if viewer?.tvPending.episodes[upNext.coordinate] == true {
            untickTVEpisode(seasonNumber: upNext.seasonNumber, episodeNumber: upNext.episodeNumber)
        } else {
            tickTVEpisode(seasonNumber: upNext.seasonNumber, episodeNumber: upNext.episodeNumber)
        }
    }

    /// The rail's eye on a TV page. The episode eye ticks and unticks; the show and season eyes follow `eye.tap`
    /// (TV-300, TV-302): mark at once, confirm first, unmark at once, confirm first, or say why not.
    func tapTVEye() {
        guard let viewer, !confirmingEyeWrite else { return }
        switch ref.mediaType {
        case "episode":
            if let episode = viewer.tv?.episode {
                toggleTVEpisode(episode)
            } else if !viewer.isTracked, let seasonNumber = ref.seasonNumber, let episodeNumber = ref.episodeNumber {
                tickTVEpisode(seasonNumber: seasonNumber, episodeNumber: episodeNumber)
            }
        case "season":
            guard let season = viewer.tv?.season, let eye = season.eye else { return }
            handleTVEye(eye, seasonNumber: season.seasonNumber, version: viewer.tv?.version)
        default:
            guard let tv = viewer.tv, let eye = tv.eye else { return }
            handleTVEye(eye, seasonNumber: nil, version: tv.version)
        }
    }

    private func handleTVEye(_ eye: TVEyeState, seasonNumber: Int?, version: Int?) {
        switch eye.tap {
        case .mark:
            markTVWatched(seasonNumber: seasonNumber)
        case .markConfirm:
            tvConfirmation = TVPageConfirmation(
                kind: .mark(seasonNumber: seasonNumber),
                prompt: TVTrackingPresentation.eyePrompt(for: eye) ?? "Mark all aired episodes watched?",
                expectedVersion: nil
            )
        case .unmarkMarks:
            unmarkTV(seasonNumber: seasonNumber, confirmed: false, expectedVersion: nil)
        case .unmarkConfirm:
            tvConfirmation = TVPageConfirmation(
                kind: .unmark(seasonNumber: seasonNumber),
                prompt: TVTrackingPresentation.eyePrompt(for: eye) ?? "Mark all episodes unwatched?",
                expectedVersion: version
            )
        case .blocked, .nothingAired:
            tvNotice = TVTrackingPresentation.eyeMessage(for: eye)
        case .unknown:
            break
        }
    }

    /// The user confirmed a TV dialog.
    func confirmTV(_ confirmation: TVPageConfirmation) {
        if tvConfirmation == confirmation { tvConfirmation = nil }
        switch confirmation.kind {
        case let .mark(seasonNumber):
            markTVWatched(seasonNumber: seasonNumber)
        case let .unmark(seasonNumber):
            unmarkTV(seasonNumber: seasonNumber, confirmed: true, expectedVersion: confirmation.expectedVersion)
        case let .markUpTo(coordinate):
            markTVUpTo(coordinate)
        case .remove:
            removeTVShow(expectedVersion: confirmation.expectedVersion)
        }
    }

    /// The show eye (`seasonNumber` nil) or a season or Specials eye marks every aired episode under it, undated
    /// (TV-300, TV-004).
    @discardableResult
    func markTVWatched(seasonNumber: Int?) -> Task<Bool, Never> {
        sendTV(.markWatched, projection: TrackingProjection.tvMarked(season: seasonNumber), eyeSpinner: true) { [self] in
            TVActionRequest(seasonNumber: seasonNumber, mutationId: mutationId(for: "tv.mark.\(seasonNumber ?? -1)"))
        }
    }

    /// A filled eye's undo (TV-302). Case 3 is `confirmed`: it carries the version its dialog showed, and a show
    /// that changed since asks again with the new count (TV-705).
    @discardableResult
    func unmarkTV(seasonNumber: Int?, confirmed: Bool, expectedVersion: Int?) -> Task<Bool, Never> {
        sendTV(
            .unmark,
            eyeSpinner: true,
            onStateChanged: { [self] change in
                guard change.count > 0 else { return }
                tvConfirmation = TVPageConfirmation(
                    kind: .unmark(seasonNumber: seasonNumber),
                    prompt: TVTrackingPresentation.eyePrompt(
                        for: TVEyeState(filled: true, tap: .unmarkConfirm, count: change.count)
                    ) ?? "Mark all \(change.count) episodes unwatched?",
                    expectedVersion: change.stateVersion
                )
            },
            afterSuccess: { [self] envelope in tvConfirmed.recordUnmark(seasonNumber: seasonNumber, envelope: envelope) }
        ) { [self] in
            TVActionRequest(
                seasonNumber: seasonNumber,
                mutationId: mutationId(for: "tv.unmark.\(seasonNumber ?? -1).\(expectedVersion ?? -1)"),
                expectedVersion: confirmed ? expectedVersion : nil,
                confirm: confirmed ? true : nil
            )
        }
    }

    /// Long-press "Mark watched up to here" on an episode row asks first, naming the range (TV-108).
    func askMarkTVUpTo(_ episode: TVEpisodeState) {
        guard episode.canMarkUpTo, episode.markUpToPrompt != nil else { return }
        tvConfirmation = TVPageConfirmation(
            kind: .markUpTo(episode.coordinate),
            prompt: TVTrackingPresentation.markUpToPrompt(for: episode),
            expectedVersion: nil
        )
    }

    @discardableResult
    func markTVUpTo(_ coordinate: TVEpisodeCoordinate) -> Task<Bool, Never> {
        // This season's aired regular episodes up to here show as watched until the server answers.
        let shown = (detail?.episodes ?? []).compactMap(\.tv).filter {
            $0.seasonNumber == coordinate.seasonNumber && $0.episodeNumber <= coordinate.episodeNumber
                && $0.aired && $0.listed && !$0.isSpecial
        }.map(\.coordinate)
        let projection: TrackingProjection.Projection = { state in
            for episode in shown { state.tvPending.episodes[episode] = true }
        }
        return sendTV(.markUpTo, projection: projection) { [self] in
            TVActionRequest(
                seasonNumber: coordinate.seasonNumber,
                episodeNumber: coordinate.episodeNumber,
                mutationId: mutationId(for: "tv.markUpTo.\(coordinate.seasonNumber).\(coordinate.episodeNumber)")
            )
        }
    }

    /// Watching, Resume, Planning, Pause, Drop (already confirmed), Rewatch and Stop Rewatching from a + menu
    /// (TV-005 to TV-007, TV-600, TV-602). `seasonNumber` makes Rewatch a season rewatch.
    @discardableResult
    func performTVAction(_ action: TVAction, seasonNumber: Int? = nil) -> Task<Bool, Never> {
        let projection: TrackingProjection.Projection?
        switch action {
        case .watching, .resume: projection = TrackingProjection.tvChoice(status: "In progress")
        case .planning: projection = TrackingProjection.tvChoice(status: "Planning")
        case .pause: projection = TrackingProjection.tvChoice(status: "Paused")
        case .drop: projection = TrackingProjection.tvChoice(status: "Dropped")
        default: projection = nil
        }
        return sendTV(action, projection: projection) { [self] in
            TVActionRequest(
                seasonNumber: seasonNumber,
                mutationId: mutationId(for: "tv.\(action.rawValue).\(seasonNumber ?? -1)")
            )
        }
    }

    /// Remove from Library, after the count dialog (TV-003). A show that changed since the dialog changes nothing,
    /// and the page asks again with the new count (TV-705).
    @discardableResult
    func removeTVShow(expectedVersion: Int?) -> Task<Bool, Never> {
        sendTV(
            .remove,
            projection: TrackingProjection.removed,
            onStateChanged: { [self] change in
                tvConfirmation = TVPageConfirmation(
                    kind: .remove,
                    prompt: TVTrackingPresentation.removePrompt(count: change.count),
                    expectedVersion: change.stateVersion
                )
            },
            afterSuccess: { [self] _ in tvConfirmed = TVConfirmedWrites() }
        ) { [self] in
            TVActionRequest(
                mutationId: mutationId(for: "tv.remove.\(expectedVersion ?? -1)"),
                expectedVersion: expectedVersion
            )
        }
    }

    /// Show and season stars: whole values 1-10 on TV's wire, or nil to clear, with the season number on a season
    /// page (TV-504). On a show the user hasn't started they mark it watched; on a started one they are opinion
    /// only (TV-303, TV-304).
    @discardableResult
    func rateTV(steps: Int) -> Task<Bool, Never> {
        let steps = min(max(steps, 0), 10)
        let wire = steps > 0 ? "\(steps).0" : nil
        let seasonNumber = pageSeasonNumber
        return sendTV(.rate, projection: TrackingProjection.rating(wire, impliesCompletion: false)) { [self] in
            TVActionRequest(
                seasonNumber: seasonNumber,
                mutationId: mutationId(for: "tv.rate.\(seasonNumber ?? -1).\(steps)"),
                rating: steps > 0 ? steps : nil,
                includesRating: true
            )
        }
    }

    /// A TV heart (TV-303, TV-305): the like response's TV keys fold in, and the earlier-episodes question it may
    /// carry goes to `tvFollowUp`. Like requests carry no mutation ID.
    private func toggleTVLike(ref: MediaRef, liked: Bool) -> Task<Bool, Never> {
        let repository = mediaRepository
        let received = TVLikeEnvelopeBox()
        return enqueue(
            TrackingProjection.like(liked, impliesCompletion: false),
            ref: ref,
            refresh: .community,
            errorsTo: \.likeErrorMessage,
            onSuccess: { [self] _ in
                guard let envelope = received.envelope else { return }
                tvConfirmed.record(projected: TVPendingWatches(), envelope: envelope)
                tvFollowUp.handle(envelope)
            }
        ) { [self] in
            let response = try await repository.setLiked(ref: ref, liked: liked)
            received.envelope = response.tvEnvelope
            return .liked(response.liked, tvLikeState(response))
        }
    }

    /// The page's state after a TV heart, from the confirmed state the response lands on.
    private func tvLikeState(_ response: MediaLikeResponse) -> UserMediaState {
        let current = detail?.userState ?? .untracked
        var state = response.media?.userState ?? current
        state.hasLiked = response.liked
        let envelope = response.tvEnvelope
        if let tv = envelope.tv {
            state.tv = tv.keepingPageScope(of: current.tv, season: envelope.season, episode: envelope.episode)
        } else {
            state.tv = current.tv
        }
        if let pageSeason = current.tv?.season, let season = envelope.season,
           season.seasonNumber == pageSeason.seasonNumber {
            state.isTracked = season.started
            state.status = season.status
            state.rating = season.rating
            state.hasLiked = season.liked
        }
        if let pageEpisode = current.tv?.episode, let episode = envelope.episode,
           episode.coordinate == pageEpisode.coordinate {
            state.isTracked = episode.viewingCount > 0
            state.hasLiked = episode.liked
        }
        return state
    }

    /// The earlier-episodes Yes, which `tvFollowUp` sends itself (TV-107).
    private func applyTVFollowUpResponse(_ response: TVActionResponse) {
        confirm(.tv(response))
        tvConfirmed.record(projected: TVPendingWatches(), envelope: response.envelope)
        retainedMutationIds.removeAll()
        MediaStateChange.post(ref: ref, origin: changeOrigin)
        scheduleRefresh(.community, after: writeRefreshDelay)
    }

    /// Hands a saved log's envelope to `tvFollowUp` once its composer has closed, so the question or the next
    /// composer doesn't collide with the closing one.
    func releaseDeferredTVFollowUp() {
        guard let envelope = deferredTVEnvelope else { return }
        deferredTVEnvelope = nil
        tvFollowUp.handle(envelope)
    }

    @discardableResult
    func saveProgress(_ request: ProgressUpdateSaveRequest, for detail: MediaDetail) -> Task<Bool, Never> {
        guard detail.ref.mediaType != "game" else {
            progressErrorMessage = "Update hours and percentage through the game's playthrough."
            return Task { false }
        }
        guard !isSavingProgress else { return Task { false } }
        isSavingProgress = true
        let ref = detail.ref
        return enqueue(nil, ref: ref, errorsTo: \.progressErrorMessage) { [self] in
            defer { isSavingProgress = false }
            var state: TrackingState
            if ref.mediaType == "book" {
                state = try await trackingRepository.updateBookProgress(
                    source: ref.source,
                    mediaId: ref.mediaId,
                    progressType: request.mode.apiValue,
                    value: Decimal(request.value),
                    notes: ""
                )
            } else {
                state = try await trackingRepository.update(
                    ref: ref,
                    request: TrackingWriteRequest(
                        status: "In progress",
                        progress: request.value
                    )
                )
            }
            state = state.replacingProgress(progressState(for: request, detail: detail, fallback: state.progress))
            ProgressDisplayPreferences.setMode(request.mode, for: ref)
            return .state(state)
        }
    }

    @discardableResult
    func performBookAction(_ action: String, for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        let today = CalendarDateCodec.string(from: Date())
        let projection: TrackingProjection.Projection?
        switch action {
        case "pause": projection = TrackingProjection.pauseBook()
        case "resume": projection = TrackingProjection.startBook(today: today)
        case "drop": projection = TrackingProjection.dropBook(today: today)
        case "mark_read": projection = TrackingProjection.status("Completed")
        default: projection = nil
        }
        let refresh: RefreshNeed = ["undo_read", "delete_undated_read"].contains(action) ? .community : .none
        return enqueue(projection, ref: ref, refresh: refresh, eyeSpinner: action == "undo_read") { [self] in
            if action == "undo_read" {
                return Self.outcome(try await trackingRepository.undoBookRead(source: ref.source, mediaId: ref.mediaId))
            }
            return Self.outcome(try await trackingRepository.performBookAction(
                source: ref.source,
                mediaId: ref.mediaId,
                action: action,
                request: BookActionRequest(
                    mutationId: action == "restart" ? mutationId(for: "book.restart") : UUID(),
                    startDate: action == "restart" || action == "resume" ? today : nil,
                    endDate: action == "drop" || action == "restart" ? today : nil
                )
            ))
        }
    }

    @discardableResult
    func updateBookJourney(_ journey: BookJourneyState, startDate: Date?, endDate: Date?, for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        let request = BookJourneyWriteRequest(
            startDate: startDate.map { CalendarDateCodec.string(from: $0) },
            endDate: endDate.map { CalendarDateCodec.string(from: $0) }
        )
        return enqueue(nil, ref: ref, errorsTo: \.formErrorMessage) { [self] in
            let journeyId = try confirmedJourneyId(for: journey)
            return .state(try await trackingRepository.updateBookJourney(
                source: ref.source,
                mediaId: ref.mediaId,
                journeyId: journeyId,
                request: request
            ))
        }
    }

    @discardableResult
    func deleteBookJourney(_ journey: BookJourneyState, for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        return enqueue(nil, ref: ref) { [self] in
            let journeyId = try confirmedJourneyId(for: journey)
            return Self.outcome(try await trackingRepository.deleteBookJourney(
                source: ref.source,
                mediaId: ref.mediaId,
                journeyId: journeyId
            ))
        }
    }

    @discardableResult
    func performGameAction(_ action: String, for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        let today = CalendarDateCodec.string(from: Date())
        let projection: TrackingProjection.Projection?
        switch action {
        case "start", "resume": projection = TrackingProjection.startGame(today: today)
        case "pause": projection = TrackingProjection.pauseGame()
        case "drop": projection = TrackingProjection.dropGame(today: today)
        case "mark_completed": projection = TrackingProjection.status("Completed")
        default: projection = nil
        }
        let refresh: RefreshNeed = ["undo_completed", "delete_undated_completion"].contains(action) ? .community : .none
        return enqueue(projection, ref: ref, refresh: refresh, eyeSpinner: action == "undo_completed") { [self] in
            Self.outcome(try await trackingRepository.performGameAction(ref: ref, action: action, request: BookActionRequest(
                mutationId: mutationId(for: "game.\(action)"),
                startDate: ["start", "restart", "resume"].contains(action) ? today : nil,
                endDate: ["drop", "restart"].contains(action) ? today : nil
            )))
        }
    }

    @discardableResult
    func updateGamePlaythrough(_ playthrough: GamePlaythroughState, request: GamePlaythroughWriteRequest, for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        return enqueue(nil, ref: ref, errorsTo: \.formErrorMessage) { [self] in
            let playthroughId = try confirmedPlaythroughId(for: playthrough)
            return .state(try await trackingRepository.updateGamePlaythrough(ref: ref, playthroughId: playthroughId, request: request))
        }
    }

    @discardableResult
    func deleteGamePlaythrough(_ playthrough: GamePlaythroughState, for detail: MediaDetail) -> Task<Bool, Never> {
        let ref = detail.ref
        return enqueue(nil, ref: ref) { [self] in
            let playthroughId = try confirmedPlaythroughId(for: playthrough)
            do {
                return Self.outcome(try await trackingRepository.deleteGamePlaythrough(ref: ref, playthroughId: playthroughId))
            } catch APIError.httpStatus(404, _) {
                // A retry after a lost response can find the playthrough already deleted.
                return .unknown
            }
        }
    }

    /// Folds a saved log's response in before the composer dismisses. MediaLogView has already posted the change
    /// with this page's origin.
    ///
    /// A TV log's envelope folds the show's state in, and is kept for `tvFollowUp` until the composer has closed
    /// (`releaseDeferredTVFollowUp`): an episode log can finish a season, and a first log can ask about earlier
    /// episodes (TV-107, TV-403). `followsUp` is false for the season-finish composer's own log.
    func applyLogSave(_ result: MediaLogSaveResult, followsUp: Bool = true) {
        // A saved log is a successful write: an id kept from an earlier failure must not make a later start a no-op.
        retainedMutationIds.removeAll()
        if let envelope = result.tvEnvelope {
            if let tv = envelope.tv, detail != nil {
                var state = detail?.userState ?? .untracked
                if ref.mediaType == "tv" {
                    state.tv = tv.keepingPageScope(of: state.tv, season: envelope.season, episode: envelope.episode)
                    // Every log puts the show in the library (TV-400).
                    state.isTracked = tv.inLibrary
                    state.status = tv.status
                } else {
                    // A season or episode page takes its own level's state, and the composer defaults after the
                    // log, from the response at once (TV-400, TV-404, TV-705).
                    state = state.applying(tvEnvelope: envelope, tracking: nil)
                }
                stateGeneration += 1
                if detail?.userState != state { detail?.userState = state }
            }
            // A season or show log counts every aired episode under it watched (TV-401), as that level's eye does.
            var counted = TVPendingWatches()
            switch result.ref.mediaType {
            case "season": if let seasonNumber = result.ref.seasonNumber { counted.markedSeasons.insert(seasonNumber) }
            case "tv": counted.markedShow = true
            default: break
            }
            tvConfirmed.record(projected: counted, envelope: envelope)
            if followsUp, envelope.hasFollowUp { deferredTVEnvelope = envelope }
            scheduleRefresh(.communityAndReviews, after: writeRefreshDelay)
            return
        }
        guard result.ref.id == ref.id, let tracking = result.tracking else {
            scheduleRefresh(.full, after: .zero)
            return
        }
        if let entry = result.diaryEntry {
            confirm(.completion(BookCompletionResponse(tracking: tracking, diaryEntry: entry)))
        } else {
            confirm(.state(tracking))
        }
        scheduleRefresh(result.diaryEntry == nil ? .community : .communityAndReviews, after: writeRefreshDelay)
    }

    private func progressState(
        for request: ProgressUpdateSaveRequest,
        detail: MediaDetail,
        fallback: ProgressState?
    ) -> ProgressState {
        let max: Decimal?
        switch request.mode {
        case .percentage:
            max = Decimal(100)
        case .pages:
            max = detail.progressTotalPages.map { Decimal($0) } ?? fallback?.max
        }
        return ProgressState(
            kind: request.mode.apiValue,
            value: Decimal(request.value),
            max: max,
            unit: request.mode.unit
        )
    }

    func applyPosterSave(_ response: PosterSaveResponse) {
        guard let ref = detail?.ref else { return }
        detail = detail?.replacingPoster(with: response)
        MediaStateChange.post(ref: ref, origin: changeOrigin)
    }

    func applyBackdropSave(_ response: BackdropSaveResponse) {
        detail = detail?.replacingBackdrop(with: response)
    }

    func applyLogoSave(_ response: LogoSaveResponse) {
        detail = detail?.replacingLogo(with: response)
    }
}

/// A write that would need a playthrough or journey the server hasn't created yet.
private struct PendingWriteError: LocalizedError {
    var errorDescription: String? { "This is still saving. Try again in a moment." }
}

/// A TV write the page refuses before sending, with the server's reason (an untick of a logged viewing, TV-102).
private struct TVPageError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Carries a TV heart's envelope from the request to the moment it confirms.
private final class TVLikeEnvelopeBox {
    var envelope: TVWriteEnvelope?
}

enum MediaDetailQuickAction {
    case planning
    case currently
    case paused
    case finished
    case stopped
}

struct MediaRatingPickerState: Equatable {
    private(set) var isPresented = false
    var draftHalfSteps = 0
    private(set) var confirmedHalfSteps = 0
    private(set) var hasLocallyWatched = false

    var showsConfirm: Bool { draftHalfSteps != confirmedHalfSteps }

    mutating func open(markLocallyWatched: Bool = true) {
        draftHalfSteps = confirmedHalfSteps
        if markLocallyWatched { hasLocallyWatched = true }
        isPresented = true
    }

    mutating func dismiss() {
        draftHalfSteps = confirmedHalfSteps
        isPresented = false
    }

    mutating func confirm() {
        confirmedHalfSteps = draftHalfSteps
        isPresented = false
    }

    mutating func syncConfirmed(_ halfSteps: Int) {
        confirmedHalfSteps = halfSteps
        if !isPresented {
            draftHalfSteps = halfSteps
        }
    }

    mutating func rollbackWatch() {
        hasLocallyWatched = false
    }

    /// The local watch only bridges the wait for the server. Once the server says the item
    /// isn't completed (a deleted playthrough, completion or log), the eye follows it.
    mutating func syncCompleted(_ isCompleted: Bool) {
        if !isCompleted { hasLocallyWatched = false }
    }

    mutating func resetAfterUnwatch() {
        self = MediaRatingPickerState()
    }
}

private struct GameCompletionPresentation: Identifiable {
    let id = UUID()
    let playthroughId: Int?
    let liked: Bool?
    let ratingSteps: Int?
}

private enum MediaDetailSheet: Identifiable {
    case posterMenu
    case bookGameActions
    case tvActions
    case addToList

    var id: String {
        switch self {
        case .posterMenu: "posterMenu"
        case .bookGameActions: "bookGameActions"
        case .tvActions: "tvActions"
        case .addToList: "addToList"
        }
    }
}

private struct PresentedDiaryEntry: Identifiable {
    let id: Int
}

private struct PresentedMediaDiary: Identifiable {
    let detail: MediaDetail

    var id: String { detail.ref.id }
    var title: String { "\(detail.displayTitle) Logs" }
}

struct MediaPersonCredit: Hashable {
    let name: String
    let personRef: PersonRef?
}

struct MediaCreditPresentation: Hashable {
    let label: String
    let people: [MediaPersonCredit]

    var heroPeople: [MediaPersonCredit] {
        Array(people.prefix(2))
    }

    var heroMoreCount: Int {
        max(0, people.count - heroPeople.count)
    }

    static func make(for detail: MediaDetail) -> MediaCreditPresentation? {
        if detail.ref.source == "musicbrainz", detail.ref.mediaType == "music" {
            return musicArtists(detail.music?.artistCredit ?? [])
        }
        if detail.ref.mediaType == "manga" {
            var seen = Set<String>()
            let authors = detail.details?["authors"]?.displayStrings ?? []
            let names = authors.isEmpty ? (detail.crew ?? []).prefix(1).map(\.name) : authors
            let creatorsByName = (detail.crew ?? []).reduce(into: [String: CreditPerson]()) {
                $0[$1.name.lowercased()] = $0[$1.name.lowercased()] ?? $1
            }
            let people = names.compactMap { name -> MediaPersonCredit? in
                let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty, seen.insert(name.lowercased()).inserted else { return nil }
                return MediaPersonCredit(
                    name: name,
                    personRef: creatorsByName[name.lowercased()]?.personRef
                )
            }
            guard !people.isEmpty else { return nil }
            return MediaCreditPresentation(
                label: people.count == 1 ? "Author" : "Authors",
                people: people
            )
        }
        guard detail.ref.source == "tmdb" else { return nil }

        let configuration: (pluralKey: String, singularKey: String, singularIDKey: String, singularLabel: String, pluralLabel: String)
        switch detail.ref.mediaType {
        case "movie":
            configuration = ("directors", "director", "director_id", "Director", "Directors")
        case "tv":
            configuration = ("creators", "creator", "creator_id", "Creator", "Creators")
        default:
            return nil
        }

        let people = pluralPeople(in: detail.details?[configuration.pluralKey])
        let resolvedPeople = people.isEmpty
            ? legacyPerson(
                name: detail.details?[configuration.singularKey]?.stringValue,
                id: identifier(in: detail.details?[configuration.singularIDKey])
            ).map { [$0] } ?? []
            : people
        guard !resolvedPeople.isEmpty else { return nil }

        return MediaCreditPresentation(
            label: resolvedPeople.count == 1 ? configuration.singularLabel : configuration.pluralLabel,
            people: resolvedPeople
        )
    }

    static func musicArtists(_ credits: [MusicArtistCredit]) -> MediaCreditPresentation? {
        var seen = Set<String>()
        let people = credits.compactMap { credit -> MediaPersonCredit? in
            let name = credit.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }
            let key = credit.personRef.map { "id:\($0.id)" } ?? "name:\(name.lowercased())"
            guard seen.insert(key).inserted else { return nil }
            return MediaPersonCredit(name: name, personRef: credit.personRef)
        }
        guard !people.isEmpty else { return nil }
        return MediaCreditPresentation(
            label: people.count == 1 ? "Artist" : "Artists",
            people: people
        )
    }

    private static func pluralPeople(in value: JSONValue?) -> [MediaPersonCredit] {
        guard case let .array(values) = value else { return [] }
        var seen = Set<String>()
        return values.compactMap { value in
            guard case let .object(person) = value,
                  let name = person["name"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !name.isEmpty
            else { return nil }
            let id = identifier(in: person["id"])?.trimmingCharacters(in: .whitespacesAndNewlines)
            let key = id.map { "id:\($0)" } ?? "name:\(name.lowercased())"
            guard seen.insert(key).inserted else { return nil }
            return MediaPersonCredit(
                name: name,
                personRef: id.flatMap { $0.isEmpty ? nil : PersonRef(source: "tmdb", id: $0) }
            )
        }
    }

    private static func legacyPerson(name: String?, id: String?) -> MediaPersonCredit? {
        guard let name = name?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty else { return nil }
        let id = id?.trimmingCharacters(in: .whitespacesAndNewlines)
        return MediaPersonCredit(
            name: name,
            personRef: id.flatMap { $0.isEmpty ? nil : PersonRef(source: "tmdb", id: $0) }
        )
    }

    private static func identifier(in value: JSONValue?) -> String? {
        switch value {
        case let .string(id):
            id
        case let .number(id) where id.rounded() == id:
            String(Int(id))
        default:
            nil
        }
    }
}

private struct MediaDetailChip: Hashable, Identifiable {
    let label: String
    let discoverRequest: MediaDiscoverRequest?

    var id: String {
        [label, discoverRequest?.id ?? "_"].joined(separator: ":")
    }
}

enum MediaArtworkCustomization {
    static func supportsPoster(source: String, mediaType: String) -> Bool {
        if source == "mal", mediaType == "anime" {
            return true
        }
        if mediaType == "manga", ["mal", "mangaupdates"].contains(source) {
            return true
        }
        if source == "tmdb", ["movie", "tv", "season"].contains(mediaType) {
            return true
        }
        if source == "musicbrainz", mediaType == "music" {
            return true
        }
        if source == "igdb", mediaType == "game" {
            return true
        }
        return mediaType == "book" && ["openlibrary", "hardcover"].contains(source)
    }

    static func supportsBackdrop(source: String, mediaType: String) -> Bool {
        if source == "mal", mediaType == "anime" {
            return true
        }
        if mediaType == "manga", ["mal", "mangaupdates"].contains(source) {
            return true
        }
        if source == "tmdb", ["movie", "tv", "season", "episode"].contains(mediaType) {
            return true
        }
        return source == "igdb" && mediaType == "game"
    }

    static func supportsLogo(source: String, mediaType: String) -> Bool {
        if source == "tmdb", ["movie", "tv"].contains(mediaType) {
            return true
        }
        return source == "igdb" && mediaType == "game"
    }
}

enum MediaExternalRatingPresentation {
    static func includes(source: String, mediaType: String) -> Bool {
        let normalizedSource = source.lowercased()
        if mediaType == "season" {
            return normalizedSource == "tmdb"
        }
        if mediaType == "music" || ["spine", "google books", "igdb"].contains(normalizedSource) {
            return false
        }
        if mediaType == "manga", normalizedSource == "mangaupdates" {
            return false
        }
        if normalizedSource == "tmdb", ["movie", "tv"].contains(mediaType) {
            return false
        }
        return true
    }

    static func order(for source: String) -> Int {
        switch source.lowercased() {
        case "letterboxd": 0
        case "rotten tomatoes": 1
        case "imdb": 2
        case "metacritic": 3
        case "steam": 4
        case "mal", "myanimelist": 5
        case "anilist": 6
        default: 7
        }
    }

    static func showsExternalRatingPlaceholder(
        mediaType: String,
        ratings: [ExternalRating],
        preparation: MediaExternalRatingsPreparation?
    ) -> Bool {
        mediaType != "music"
            && preparation?.state == .pending
            && !ratings.contains {
                !$0.value.isEmpty && includes(source: $0.source, mediaType: mediaType)
            }
    }
}

enum MangaYearPresentation {
    static func value(startDate: String?, endDate: String?, status: String?) -> String? {
        guard let startYear = startDate?.yearPrefix else { return nil }
        let normalizedStatus = status?.lowercased() ?? ""
        if ["publishing", "hiatus", "releasing", "ongoing"].contains(where: normalizedStatus.contains) {
            return "\(startYear)–"
        }
        guard let endYear = endDate?.yearPrefix else { return startYear }
        return endYear == startYear ? startYear : "\(startYear)–\(endYear)"
    }
}

struct MusicStreamingDestination: Hashable, Identifiable {
    let label: String
    let url: URL

    var id: String { url.absoluteString }
}

enum MusicAlbumPresentation {
    static func artistCreditText(_ credits: [MusicArtistCredit]) -> String? {
        let value = credits
            .map { "\($0.name)\($0.joinPhrase)" }
            .joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    static func releaseType(_ music: MusicDetail) -> String? {
        ([music.primaryType] + music.secondaryTypes.map(Optional.some))
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " · ")
            .nilIfEmpty
    }

    static func duration(_ milliseconds: Int?) -> String? {
        guard let milliseconds, milliseconds >= 0 else { return nil }
        let seconds = milliseconds / 1_000
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    /// Music videos on the tracklist aren't songs, so they're left out once the tracks have loaded.
    static func songCount(_ release: MusicRepresentativeRelease) -> String? {
        let songs = Self.songs(release)
        let count = songs.isEmpty ? release.trackCount : songs.count
        guard count > 0 else { return nil }
        return count == 1 ? "1 song" : "\(count) songs"
    }

    /// The album's length, only when every song has one: a partial sum would understate it.
    static func totalLength(_ release: MusicRepresentativeRelease) -> String? {
        let lengths = songs(release).map { $0.lengthMs ?? $0.recording.lengthMs }
        guard !lengths.isEmpty, !lengths.contains(nil) else { return nil }
        let minutes = Int((Double(lengths.compactMap { $0 }.reduce(0, +)) / 60_000).rounded())
        return "\(max(minutes, 1)) min"
    }

    private static func songs(_ release: MusicRepresentativeRelease) -> [MusicTrack] {
        release.media.flatMap(\.tracks).filter { !$0.recording.isVideo }
    }

    static func countryName(_ code: String?, locale: Locale = .current) -> String? {
        guard let code = code?.nilIfEmpty else { return nil }
        if code.caseInsensitiveCompare("XW") == .orderedSame {
            return "Worldwide"
        }
        return locale.localizedString(forRegionCode: code.uppercased()) ?? code
    }

    static func format(_ release: MusicRepresentativeRelease) -> String? {
        if let format = release.format?.nilIfEmpty {
            return format
        }
        var seen = Set<String>()
        return release.media
            .compactMap { $0.format?.nilIfEmpty }
            .filter { seen.insert($0.lowercased()).inserted }
            .joined(separator: ", ")
            .nilIfEmpty
    }

    static func differingArtistCredit(
        track: MusicTrack,
        albumCredits: [MusicArtistCredit]
    ) -> String? {
        guard let trackArtist = artistCreditText(track.artistCredit) else { return nil }
        guard let albumArtist = artistCreditText(albumCredits) else { return trackArtist }
        return trackArtist.caseInsensitiveCompare(albumArtist) == .orderedSame ? nil : trackArtist
    }

    static func trackAccessibilityLabel(track: MusicTrack, albumCredits: [MusicArtistCredit]) -> String {
        var values = ["Disc \(track.discNumber), track \(track.number)", track.title]
        if let artist = artistCreditText(track.artistCredit) ?? artistCreditText(albumCredits) {
            values.append("by \(artist)")
        }
        if let duration = duration(track.lengthMs) {
            values.append(duration)
        }
        return values.joined(separator: ", ")
    }

    static func streamingDestinations(_ links: [MusicStreamingLink]) -> [MusicStreamingDestination] {
        var seen = Set<String>()
        return links.compactMap { streamingDestination($0) }.filter { seen.insert($0.id).inserted }
    }

    static func musicBrainzURL(releaseGroupMbid: String) -> URL {
        URL(string: "https://musicbrainz.org")!
            .appending(path: "release-group")
            .appending(path: releaseGroupMbid)
    }

    private static func streamingDestination(_ link: MusicStreamingLink) -> MusicStreamingDestination? {
        guard
            let url = URL(string: link.url),
            ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
            let rawHost = url.host?.lowercased(),
            !rawHost.isEmpty
        else { return nil }
        let host = rawHost.hasPrefix("www.") ? String(rawHost.dropFirst(4)) : rawHost

        let label: String
        if host == "music.apple.com" {
            label = "Apple Music"
        } else if host == "open.spotify.com" {
            label = "Spotify"
        } else if host == "qobuz.com" || host.hasSuffix(".qobuz.com") {
            label = "Qobuz"
        } else {
            return nil
        }
        return MusicStreamingDestination(label: label, url: url)
    }
}

private struct TopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

struct MediaDetailView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var posterTransitionNamespace
    @State private var selectedID: MediaRef.ID?
    @State private var presentedPoster: PosterViewerItem?

    private let ref: MediaRef
    private let browsingContext: MediaBrowsingContext?
    private let mediaRepository: MediaRepository
    private let musicRepository: MusicRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let companyRepository: CompanyRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void
    private let onReturnToOriginSeason: (() -> Void)?

    init(
        ref: MediaRef,
        browsingContext: MediaBrowsingContext? = nil,
        mediaRepository: MediaRepository,
        musicRepository: MusicRepository = AppRepositories.current().music,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        peopleRepository: PeopleRepository = AppRepositories.current().people,
        companyRepository: CompanyRepository = AppRepositories.current().companies,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {},
        onReturnToOriginSeason: (() -> Void)? = nil
    ) {
        self.ref = ref
        self.browsingContext = browsingContext
        self.mediaRepository = mediaRepository
        self.musicRepository = musicRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.companyRepository = companyRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        self.onReturnToOriginSeason = onReturnToOriginSeason
        _selectedID = State(initialValue: browsingContext?.selectedID ?? ref.id)
    }

    var body: some View {
        ZStack {
            GeometryReader { safeAreaProxy in
                Group {
                    if let browsingContext, browsingContext.refs.count > 1 {
                        ScrollViewReader { scrollProxy in
                            ScrollView(.horizontal) {
                                LazyHStack(spacing: 0) {
                                    ForEach(Array(browsingContext.refs.enumerated()), id: \.element.id) { index, pageRef in
                                        detailPage(
                                            ref: pageRef,
                                            shouldLoad: abs(index - selectedIndex(in: browsingContext)) <= 1,
                                            topSafeAreaInset: safeAreaProxy.safeAreaInsets.top
                                        )
                                        .containerRelativeFrame(.horizontal)
                                        .accessibilityHidden(pageRef.id != selectedID)
                                        .id(pageRef.id)
                                    }
                                }
                                .scrollTargetLayout()
                            }
                            .scrollIndicators(.hidden)
                            .scrollTargetBehavior(.paging)
                            .scrollPosition(id: $selectedID, anchor: .center)
                            .task(id: browsingContext.selectedID) {
                                await Task.yield()
                                scrollProxy.scrollTo(browsingContext.selectedID, anchor: .center)
                            }
                            .ignoresSafeArea(edges: .top)
                        }
                    } else {
                        detailPage(
                            ref: ref,
                            shouldLoad: true,
                            topSafeAreaInset: safeAreaProxy.safeAreaInsets.top
                        )
                    }
                }
                .ignoresSafeArea(edges: .top)
                .scrollDisabled(presentedPoster != nil)
                .accessibilityHidden(presentedPoster != nil)
            }

            if let presentedPoster {
                PosterViewer(
                    item: presentedPoster,
                    namespace: posterTransitionNamespace,
                    onDismiss: dismissPoster
                )
                .zIndex(100)
            }
        }
        .toolbar(.hidden, for: .tabBar)
        .dismissExplorationOnReturnHome()
    }

    private func selectedIndex(in context: MediaBrowsingContext) -> Int {
        context.refs.firstIndex(where: { $0.id == selectedID }) ?? 0
    }

    private func detailPage(
        ref: MediaRef,
        shouldLoad: Bool,
        topSafeAreaInset: CGFloat? = nil
    ) -> some View {
        MediaDetailPageView(
            ref: ref,
            shouldLoad: shouldLoad,
            topSafeAreaInset: topSafeAreaInset,
            mediaRepository: mediaRepository,
            musicRepository: musicRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            peopleRepository: peopleRepository,
            companyRepository: companyRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            posterTransitionNamespace: posterTransitionNamespace,
            presentedPosterID: presentedPoster?.id,
            onOpenPoster: presentPoster,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized,
            onReturnToOriginSeason: onReturnToOriginSeason
        )
    }

    private func presentPoster(_ poster: PosterViewerItem) {
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.88)) {
            presentedPoster = poster
        }
    }

    private func dismissPoster() {
        withAnimation(reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.9)) {
            presentedPoster = nil
        }
    }
}

// Keep content evaluation outside the page's presentation-modifier stack.
// Calling a computed view here eagerly overflowed the 1 MB iPhone main-thread stack.
private struct MediaDetailScrollContent<Hero: View, Content: View>: View {
    let viewModel: MediaDetailViewModel
    let mediaType: String
    let topSafeAreaInset: CGFloat
    let hero: (MediaDetail) -> Hero
    let content: (MediaDetail) -> Content

    var body: some View {
        Group {
            if viewModel.isLoading, viewModel.detail == nil {
                if mediaType == "episode" {
                    EpisodeDetailLoadingView()
                } else {
                    ProgressView()
                        .tint(.white)
                        .frame(maxWidth: .infinity, minHeight: 520)
                }
            } else if let detail = viewModel.detail {
                VStack(spacing: 0) {
                    hero(detail)
                        .padding(.top, -topSafeAreaInset)
                    content(detail)
                }
            } else if let error = viewModel.errorMessage, viewModel.detail == nil {
                VStack(spacing: 18) {
                    ContentUnavailableView("Could not load media", systemImage: "exclamationmark.triangle", description: Text(error))
                        .foregroundStyle(.white)
                    Button("Try Again") {
                        Task { await viewModel.load() }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.white.opacity(0.16))
                }
                .padding()
                .frame(maxWidth: .infinity, minHeight: 520)
            }
        }
        .padding(.bottom, 116)
    }
}

private struct MediaDetailPageView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var viewModel: MediaDetailViewModel
    @State private var presentedSheet: MediaDetailSheet?
    @State private var presentedRef: MediaRef?
    @State private var presentedMediaSelection: MediaBrowsingSelection?
    @State private var presentedDiaryEntry: PresentedDiaryEntry?
    @State private var presentedMediaDiary: PresentedMediaDiary?
    @State private var presentedDiscover: MediaDiscoverRequest?
    @State private var presentedSeries: SeriesRef?
    @State private var presentedPerson: PersonRef?
    @State private var presentedCompany: CompanyRef?
    @State private var presentedSong: MusicSongSelection?
    @State private var isPosterPickerPresented = false
    @State private var isBackdropPickerPresented = false
    @State private var isLogoPickerPresented = false
    @State private var isReviewsPresented = false
    @State private var isAniListReviewsPresented = false
    @State private var pendingPosterSave: PosterSaveResponse?
    @State private var pendingBackdropSave: BackdropSaveResponse?
    @State private var pendingLogoSave: LogoSaveResponse?
    @State private var isLogPresented = false
    @State private var completionJourneyId: Int?
    @State private var completionLiked: Bool?
    @State private var completionRatingSteps: Int?
    @State private var progressUpdateDetail: MediaDetail?
    @State private var editingBookJourney: BookJourneyState?
    @State private var editingGamePlaythrough: GamePlaythroughState?
    @State private var pendingGameProgress: GamePlaythroughState?
    @State private var progressGamePlaythrough: GamePlaythroughState?
    @State private var pendingGameEdit: GamePlaythroughState?
    @State private var pendingGameCompletion: GameCompletionPresentation?
    @State private var gameCompletionPresentation: GameCompletionPresentation?
    @State private var pendingBookLog = false
    /// True from a choice that closes a tracking sheet until that sheet's onDismiss, so an alert or the next
    /// screen waits for the dismissal instead of colliding with it.
    @State private var isClosingPresentation = false
    @State private var alertAfterClose = false
    @State private var likeAlertAfterClose = false
    @State private var isQuickActionAlertPresented = false
    @State private var isLikeAlertPresented = false
    @State private var showsTitleLogo = true
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var edgeDragOffset: CGFloat = 0
    @State private var ratingPicker = MediaRatingPickerState()
    @State private var albumBackdropTint: Color?

    private let mediaRepository: MediaRepository
    private let musicRepository: MusicRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let companyRepository: CompanyRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let posterTransitionNamespace: Namespace.ID
    private let presentedPosterID: PosterViewerItem.ID?
    private let onOpenPoster: (PosterViewerItem) -> Void
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void
    private let onReturnToOriginSeason: (() -> Void)?
    private let ref: MediaRef
    private let shouldLoad: Bool
    private let topSafeAreaInsetOverride: CGFloat?

    init(
        ref: MediaRef,
        shouldLoad: Bool,
        topSafeAreaInset: CGFloat? = nil,
        mediaRepository: MediaRepository,
        musicRepository: MusicRepository = AppRepositories.current().music,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        peopleRepository: PeopleRepository = AppRepositories.current().people,
        companyRepository: CompanyRepository = AppRepositories.current().companies,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        posterTransitionNamespace: Namespace.ID,
        presentedPosterID: PosterViewerItem.ID?,
        onOpenPoster: @escaping (PosterViewerItem) -> Void,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {},
        onReturnToOriginSeason: (() -> Void)? = nil
    ) {
        self.ref = ref
        self.shouldLoad = shouldLoad
        topSafeAreaInsetOverride = topSafeAreaInset
        self.mediaRepository = mediaRepository
        self.musicRepository = musicRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.companyRepository = companyRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.posterTransitionNamespace = posterTransitionNamespace
        self.presentedPosterID = presentedPosterID
        self.onOpenPoster = onOpenPoster
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        self.onReturnToOriginSeason = onReturnToOriginSeason
        _viewModel = State(initialValue: MediaDetailViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                MediaDetailScrollContent(
                    viewModel: viewModel,
                    mediaType: ref.mediaType,
                    topSafeAreaInset: resolvedTopSafeAreaInset,
                    hero: hero,
                    content: content
                )
                    .spineContentTransition(value: contentPhase)
                    .background {
                        if let albumBackdropTint {
                            AlbumBackdrop(tint: albumBackdropTint)
                                .transition(.opacity)
                        }
                    }
            }
            .onScrollPhaseChange { _, phase in
                if phase != .idle {
                    dismissRatingPicker()
                }
            }
            .simultaneousGesture(
                TapGesture().onEnded { dismissRatingPicker() }
            )
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)

            topButtons
                .padding(.horizontal, 16)
                .padding(.top, resolvedTopSafeAreaInset + 6)

            if progressUpdateDetail == nil, let detail = viewModel.detail {
                bottomActionRail(detail)
                .padding(.bottom, 8)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            }

            if progressUpdateDetail == nil, selectedTab == .search {
                ExplorationSearchButton(glass: .clear.interactive())
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            }

            if progressUpdateDetail == nil {
                ExplorationHomeButton(glass: .clear.interactive())
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            }
        }
        .navigationBarBackButtonHidden()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            if presentedPosterID == nil {
                Color.clear
                    .frame(width: 28)
                    .contentShape(Rectangle())
                    .gesture(edgeSwipeBackGesture)
            }
        }
        .overlay {
            progressUpdateOverlay
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: TopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(TopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
        .sheet(item: $presentedSheet, onDismiss: presentPendingSheet) { sheet in
            switch sheet {
            case .posterMenu:
                PosterMenuSheet(
                    posterLabel: "Customize Poster",
                    showsSeasonOption: parentSeasonRef(viewModel.detail) != nil,
                    showsTVShowOption: parentTVRef(viewModel.detail) != nil,
                    showsPosterOption: canCustomizePoster(viewModel.detail),
                    showsBackdropOption: canCustomizeBackdrop(viewModel.detail),
                    showsLogoOption: canCustomizeLogo(viewModel.detail),
                    onViewSeason: {
                        guard let ref = parentSeasonRef(viewModel.detail) else { return }
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            navigateToParent(ref)
                        }
                    },
                    onViewTVShow: {
                        guard let ref = parentTVRef(viewModel.detail) else { return }
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            presentedRef = ref
                        }
                    },
                    onAddToList: {
                        presentedSheet = .addToList
                    },
                    onCustomizePoster: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isPosterPickerPresented = true
                        }
                    },
                    onCustomizeBackdrop: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isBackdropPickerPresented = true
                        }
                    },
                    onCustomizeLogo: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isLogoPickerPresented = true
                        }
                    }
                )
                .presentationDetents([.height(posterMenuHeight(for: viewModel.detail))])
                .presentationDragIndicator(.visible)
            case .bookGameActions:
                if let detail = viewModel.detail {
                    if detail.ref.mediaType == "game" {
                        GameActionSheet(status: currentStatus(detail), game: gameState(detail),
                            onAction: { closeSheetForChoice(); viewModel.performQuickAction($0, for: detail) },
                            onLog: { openBookCompletion(for: detail) },
                            onProgress: { openProgressUpdate(for: detail) },
                            onRemove: { closeSheetForChoice(); viewModel.removeTracking(for: detail) })
                    } else {
                    BookGameActionSheet(
                        mediaType: detail.ref.mediaType,
                        status: currentStatus(detail),
                        bookState: bookState(detail),
                        onAction: { action in
                            closeSheetForChoice()
                            viewModel.performQuickAction(action, for: detail)
                        },
                        onRemove: {
                            closeSheetForChoice()
                            viewModel.removeTracking(for: detail)
                        },
                        onUpdateProgress: {
                            openProgressUpdate(for: detail)
                        },
                        onLog: {
                            if detail.ref.mediaType == "book" {
                                openBookCompletion(for: detail)
                            } else {
                                presentedSheet = nil
                                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                                    isLogPresented = true
                                }
                            }
                        }
                    )
                    }
                }
            case .tvActions:
                if let detail = viewModel.detail {
                    tvActionSheet(detail)
                }
            case .addToList:
                if let detail = viewModel.detail {
                    AddToListSheet(
                        target: .media(detail.ref),
                        initialMedia: detail.listSummary,
                        listRepository: listRepository,
                        mediaRepository: mediaRepository,
                        peopleRepository: peopleRepository,
                        onUnauthorized: onUnauthorized
                    )
                }
            }
        }
        .sheet(item: $progressGamePlaythrough, onDismiss: presentPendingSheet) { playthrough in
            if let detail = viewModel.detail {
                GameProgressSheet(detail: detail, playthrough: playthrough,
                    onSave: { await viewModel.updateGamePlaythrough(playthrough, request: $0, for: detail).value },
                    onAction: { isClosingPresentation = true; viewModel.performGameAction($0, for: detail) },
                    onDelete: { isClosingPresentation = true; viewModel.deleteGamePlaythrough(playthrough, for: detail) },
                    onFinish: { openBookCompletion(for: detail) },
                    onEditDates: {
                        pendingGameEdit = playthrough
                        progressGamePlaythrough = nil
                    },
                    errorMessage: { viewModel.formErrorMessage })
            }
        }
        .sheet(item: $editingGamePlaythrough, onDismiss: presentPendingSheet) { playthrough in
            if let detail = viewModel.detail {
                GamePlaythroughEditor(playthrough: playthrough,
                    onSave: { await viewModel.updateGamePlaythrough(playthrough, request: $0, for: detail).value },
                    onAction: { isClosingPresentation = true; viewModel.performGameAction($0, for: detail) },
                    onDelete: { isClosingPresentation = true; viewModel.deleteGamePlaythrough(playthrough, for: detail) },
                    onFinish: { openBookCompletion(for: detail) },
                    errorMessage: { viewModel.formErrorMessage })
            }
        }
        .sheet(item: $editingBookJourney, onDismiss: presentPendingSheet) { journey in
            if let detail = viewModel.detail {
                BookJourneyDateEditor(
                    journey: journey,
                    isSaving: viewModel.hasPendingWrites,
                    errorMessage: viewModel.formErrorMessage,
                    onSave: { startDate, endDate in
                        if await viewModel.updateBookJourney(
                            journey,
                            startDate: startDate,
                            endDate: endDate,
                            for: detail
                        ).value {
                            editingBookJourney = nil
                            return true
                        }
                        return false
                    }
                )
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
            }
        }
        .alert("Tracking Update Failed", isPresented: $isQuickActionAlertPresented) {
            Button("OK") {
                viewModel.quickActionErrorMessage = nil
            }
        } message: {
            Text(viewModel.quickActionErrorMessage ?? "")
        }
        .alert("Like Update Failed", isPresented: $isLikeAlertPresented) {
            Button("OK") {
                viewModel.likeErrorMessage = nil
            }
        } message: {
            Text(viewModel.likeErrorMessage ?? "")
        }
        .alert(
            viewModel.tvNotice ?? "",
            isPresented: Binding(
                get: { viewModel.tvNotice != nil && !defersAlerts },
                set: { if !$0 { viewModel.tvNotice = nil } }
            )
        ) {
            Button("OK", role: .cancel) { viewModel.tvNotice = nil }
        }
        .confirmationDialog(
            viewModel.tvConfirmation?.prompt ?? "",
            isPresented: Binding(
                get: { viewModel.tvConfirmation != nil && !defersAlerts },
                set: { if !$0 { viewModel.tvConfirmation = nil } }
            ),
            titleVisibility: .visible,
            presenting: viewModel.tvConfirmation
        ) { confirmation in
            Button(confirmation.confirmTitle, role: confirmation.isDestructive ? .destructive : nil) {
                viewModel.confirmTV(confirmation)
            }
            Button("Cancel", role: .cancel) { viewModel.tvConfirmation = nil }
        }
        .tvWriteFollowUp(viewModel.tvFollowUp) { item in
            MediaLogView(
                detail: item.detail,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                stateChangeOrigin: viewModel.changeOrigin,
                onResult: { viewModel.applyLogSave($0, followsUp: false) },
                onUnauthorized: onUnauthorized,
                onSaved: {},
                tvComposer: item.finished.composer,
                tvPage: ref
            )
        }
        .fullScreenCover(isPresented: $isBackdropPickerPresented, onDismiss: applyPendingBackdropSave) {
            if let detail = viewModel.detail {
                BackdropPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    initialBackdropURL: detail.ref.mediaType == "manga"
                        ? detail.displayBackdropURL
                        : nil,
                    onUnauthorized: onUnauthorized
                ) { response in
                    pendingBackdropSave = response
                    presentedSheet = nil
                }
            }
        }
        .fullScreenCover(isPresented: $isLogoPickerPresented, onDismiss: applyPendingLogoSave) {
            if let detail = viewModel.detail {
                LogoPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                ) { response in
                    pendingLogoSave = response
                    presentedSheet = nil
                }
            }
        }
        .fullScreenCover(item: $gameCompletionPresentation, onDismiss: restoreCanonicalGameRating) { presentation in
            if let detail = viewModel.detail {
                MediaLogView(detail: detail, trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    completionJourneyId: presentation.playthroughId,
                    preselectedLiked: presentation.liked,
                    preselectedRatingSteps: presentation.ratingSteps,
                    stateChangeOrigin: viewModel.changeOrigin,
                    onResult: { viewModel.applyLogSave($0) },
                    onUnauthorized: onUnauthorized) {}
            }
        }
        .fullScreenCover(isPresented: $isLogPresented, onDismiss: logComposerDismissed) {
            if let detail = viewModel.detail {
                MediaLogView(
                    detail: detail,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    completionJourneyId: completionJourneyId,
                    preselectedLiked: completionLiked,
                    preselectedRatingSteps: completionRatingSteps,
                    stateChangeOrigin: viewModel.changeOrigin,
                    onResult: { viewModel.applyLogSave($0) },
                    onUnauthorized: onUnauthorized,
                    onSaved: {},
                    tvPage: ref
                )
            }
        }
        .fullScreenCover(isPresented: $isPosterPickerPresented, onDismiss: applyPendingPosterSave) {
            if let detail = viewModel.detail {
                PosterPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    title: "Customize Poster",
                    showsLanguageFilter: !["book", "game", "music"].contains(detail.ref.mediaType),
                    contentMode: isBook(detail) ? .fit : .fill,
                    onUnauthorized: onUnauthorized
                ) { response in
                    pendingPosterSave = response
                    presentedSheet = nil
                }
            }
        }
        .fullScreenCover(isPresented: $isReviewsPresented) {
            if let detail = viewModel.detail {
                MediaReviewsView(
                    detail: detail,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .fullScreenCover(isPresented: $isAniListReviewsPresented) {
            if let detail = viewModel.detail {
                AniListReviewsView(
                    detail: detail,
                    mediaRepository: mediaRepository
                )
            }
        }
        .fullScreenCover(item: $presentedRef) { ref in
            MediaDetailView(
                ref: ref,
                mediaRepository: mediaRepository,
                musicRepository: musicRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedMediaSelection) { selection in
            MediaDetailView(
                ref: selection.ref,
                browsingContext: selection.context,
                mediaRepository: mediaRepository,
                musicRepository: musicRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized,
                onReturnToOriginSeason: {
                    presentedMediaSelection = nil
                }
            )
        }
        .fullScreenCover(item: $presentedPerson) { person in
            PersonDetailView(
                ref: person,
                peopleRepository: peopleRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedCompany) { company in
            CompanyDetailView(
                ref: company,
                companyRepository: companyRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedDiaryEntry) { entry in
            DiaryLogDetailNavigationCover(
                entryId: entry.id,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedMediaDiary) { diary in
            if let itemId = diary.detail.ref.itemId {
                MediaDiaryView(
                    title: diary.title,
                    itemId: itemId,
                    posterURL: diary.detail.displayPosterURL,
                    posterOrientation: diary.detail.posterOrientation,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .fullScreenCover(item: $presentedDiscover) { request in
            MediaDiscoverView(
                request: request,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedSeries) { series in
            SeriesDetailView(
                ref: series,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedSong) { selection in
            SongDetailView(
                selection: selection,
                musicRepository: musicRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .task(id: shouldLoad) {
            if shouldLoad, viewModel.detail == nil {
                await viewModel.load()
            }
        }
        .task(id: viewModel.externalRatingsPollingID) {
            await viewModel.pollExternalRatingsIfNeeded()
        }
        .task(id: albumArtworkURL) {
            var tint: Color?
            if let albumArtworkURL {
                tint = await AlbumBackdropColor.load(url: albumArtworkURL)
            }
            guard !Task.isCancelled else { return }
            withAnimation(.easeOut(duration: reduceMotion ? SpineMotion.reducedDuration : 0.45)) {
                albumBackdropTint = tint
            }
        }
        .onChange(of: viewModel.detail?.id) {
            showsTitleLogo = true
        }
        .onChange(of: viewModel.viewer?.rating) { _, rating in
            ratingPicker.syncConfirmed(ratingHalfSteps(rating))
        }
        .onChange(of: viewModel.quickActionErrorMessage) { _, message in
            guard message != nil else { return }
            if defersAlerts { alertAfterClose = true } else { isQuickActionAlertPresented = true }
        }
        .onChange(of: viewModel.likeErrorMessage) { _, message in
            guard message != nil else { return }
            if defersAlerts { likeAlertAfterClose = true } else { isLikeAlertPresented = true }
        }
        .onChange(of: viewModel.detail.map { isEyeCompleted($0) } ?? false) { _, isCompleted in
            ratingPicker.syncCompleted(isCompleted)
        }
        .onReceive(NotificationCenter.default.publisher(for: .diaryEntriesDidChange)) { _ in
            viewModel.scheduleRefresh(.full, after: viewModel.externalRefreshDelay)
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
            viewModel.handleMediaStateChange(notification.userInfo)
        }
        .onReceive(NotificationCenter.default.publisher(for: .streamingPreferencesDidChange)) { _ in
            // A new country or new services change Where to Watch.
            guard let detail = viewModel.detail, WhereToWatch.isAvailable(for: detail.ref) else { return }
            Swift.Task<Void, Never> { await viewModel.load() }
        }
    }

    @ViewBuilder
    private var progressUpdateOverlay: some View {
        if let detail = progressUpdateDetail {
            ProgressUpdateSheet(
                detail: detail,
                progress: currentProgress(detail),
                isSaving: viewModel.isSavingProgress,
                errorMessage: viewModel.progressErrorMessage,
                journeyStatus: viewModel.viewer?.book?.currentJourney?.status,
                onSave: { request in
                    await viewModel.saveProgress(request, for: detail).value
                },
                onDismiss: {
                    progressUpdateDetail = nil
                },
                onLogFinished: {
                    progressUpdateDetail = nil
                    openBookCompletion(for: detail)
                },
                onPause: {
                    viewModel.performBookAction("pause", for: detail)
                },
                onResume: {
                    viewModel.performBookAction("resume", for: detail)
                },
                onDrop: {
                    viewModel.performBookAction("drop", for: detail)
                },
                onRestart: {
                    viewModel.performBookAction("restart", for: detail)
                },
                onDelete: {
                    guard let journey = viewModel.viewer?.book?.currentJourney else { return }
                    viewModel.deleteBookJourney(journey, for: detail)
                }
            )
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    /// The image an album page takes its background color from, the one its hero draws; nil for every other
    /// media type.
    private var albumArtworkURL: URL? {
        guard let detail = viewModel.detail, detail.ref.mediaType == "music" else { return nil }
        return (detail.displayBackdropURL ?? detail.displayPosterURL).flatMap(URL.init(string:))
    }

    private func applyPendingPosterSave() {
        guard let response = pendingPosterSave else { return }
        pendingPosterSave = nil
        viewModel.applyPosterSave(response)
    }

    private func applyPendingBackdropSave() {
        guard let response = pendingBackdropSave else { return }
        pendingBackdropSave = nil
        viewModel.applyBackdropSave(response)
    }

    private func applyPendingLogoSave() {
        guard let response = pendingLogoSave else { return }
        pendingLogoSave = nil
        viewModel.applyLogoSave(response)
        showsTitleLogo = true
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    private var topButtons: some View {
        HStack {
            CircleIconButton(systemName: "chevron.left", label: "Back") {
                dismissRatingPicker()
                dismiss()
            }
            Spacer()
            if viewModel.detail != nil {
                CircleIconButton(systemName: "ellipsis", label: "More") {
                    dismissRatingPicker()
                    presentedSheet = .posterMenu
                }
            }
        }
    }

    private func dismissRatingPicker() {
        guard ratingPicker.isPresented else { return }
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84)) {
            ratingPicker.dismiss()
        }
    }

    private var resolvedTopSafeAreaInset: CGFloat {
        topSafeAreaInsetOverride ?? topSafeAreaInset
    }

    private func posterMenuHeight(for detail: MediaDetail?) -> CGFloat {
        let optionalRows = [
            parentSeasonRef(detail) != nil,
            parentTVRef(detail) != nil,
            canCustomizePoster(detail),
            canCustomizeBackdrop(detail),
            canCustomizeLogo(detail),
        ].filter { $0 }.count
        return 80 + CGFloat(optionalRows * 68)
    }

    private func canCustomizePoster(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsPoster(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func canCustomizeBackdrop(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsBackdrop(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func canCustomizeLogo(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsLogo(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func isBook(_ detail: MediaDetail) -> Bool {
        detail.ref.mediaType == "book"
    }

    private func parentTVRef(_ detail: MediaDetail?) -> MediaRef? {
        guard let detail, ["season", "episode"].contains(detail.ref.mediaType) else { return nil }
        return MediaRef(
            itemId: nil,
            source: detail.ref.source,
            mediaType: "tv",
            mediaId: detail.ref.mediaId,
            seasonNumber: nil,
            episodeNumber: nil
        )
    }

    private func parentSeasonRef(_ detail: MediaDetail?) -> MediaRef? {
        guard
            let detail,
            detail.ref.mediaType == "episode",
            let seasonNumber = detail.ref.seasonNumber
        else { return nil }
        return MediaRef(
            itemId: nil,
            source: detail.ref.source,
            mediaType: "season",
            mediaId: detail.ref.mediaId,
            seasonNumber: seasonNumber,
            episodeNumber: nil
        )
    }

    private func navigateToParent(_ ref: MediaRef) {
        if ref.mediaType == "season", let onReturnToOriginSeason {
            onReturnToOriginSeason()
        } else {
            presentedRef = ref
        }
    }

    private func usesBookGameActions(_ detail: MediaDetail) -> Bool {
        ["book", "game"].contains(detail.ref.mediaType)
    }

    // MARK: TV (TV-005, TV-100, TV-300, TV-302, TV-502)

    /// The show's or the season's + menu. The episode page's + opens Log Episode directly, its one action.
    @ViewBuilder
    private func tvActionSheet(_ detail: MediaDetail) -> some View {
        if let tv = viewModel.viewer?.tv {
            let season = detail.ref.mediaType == "season" ? tv.season : nil
            TVActionSheet(
                title: season.map { TVTrackingPresentation.seasonTitle($0.seasonNumber) } ?? "Track this show",
                items: season.map { TVTrackingPresentation.seasonMenu(for: $0) } ?? TVTrackingPresentation.showMenu(for: tv),
                removePrompt: TVTrackingPresentation.removePrompt(count: tv.removeCount),
                onCommand: { command in performTVCommand(command, version: tv.version) }
            )
        }
    }

    private func performTVCommand(_ command: TVMenuItem.Command, version: Int) {
        switch command {
        case .log:
            openTVComposer()
        case let .action(action):
            closeSheetForChoice()
            viewModel.performTVAction(action)
        case let .rewatchSeason(seasonNumber):
            closeSheetForChoice()
            viewModel.performTVAction(.rewatch, seasonNumber: seasonNumber)
        case .confirmRemove:
            closeSheetForChoice()
            viewModel.removeTVShow(expectedVersion: version)
        case .confirmDrop:
            // The sheet asks first and sends `.action(.drop)`.
            break
        }
    }

    /// Opens Log Show, Log Season or Log Episode with the server's defaults (TV-404), once the write queue has
    /// settled so the defaults are current. `ratingSteps` are an episode's stars (TV-502).
    private func openTVComposer(ratingSteps: Int? = nil) {
        if presentedSheet != nil { closeSheetForChoice() }
        presentAfterSettling {
            completionJourneyId = nil
            completionLiked = nil
            completionRatingSteps = ratingSteps
            if isClosingPresentation { pendingBookLog = true } else { isLogPresented = true }
        }
    }

    /// The log composer closed: a TV log's question or season composer can follow now (TV-107, TV-403).
    private func logComposerDismissed() {
        completionRatingSteps = nil
        presentDeferredAlerts()
        viewModel.releaseDeferredTVFollowUp()
    }

    private func tvTrackAction(for detail: MediaDetail) {
        guard let tv = viewModel.viewer?.tv else {
            presentAfterSettling { isLogPresented = true }
            return
        }
        if detail.ref.mediaType == "episode" {
            let episode = tv.episode
            if let episode, !episode.availableActions.contains("log_episode"),
               let reason = episode.actionReasons["log_episode"] {
                viewModel.tvNotice = reason
                return
            }
            openTVComposer()
            return
        }
        presentedSheet = .tvActions
    }

    /// The episode page's stars show the newest rated log's stars and open Log Episode with them (TV-502).
    private func tvEpisodeRatingSteps() -> Int? {
        guard let rating = viewModel.viewer?.tv?.episode?.userRating, let value = Double(rating) else { return nil }
        return min(max(Int(value.rounded()), 0), 10)
    }

    /// Show and season stars can't be set before anything has aired (TV-303).
    private func tvRatingUnavailableReason() -> String? {
        guard let tv = viewModel.viewer?.tv else { return nil }
        let actions = tv.season?.availableActions ?? tv.availableActions
        let reasons = tv.season?.actionReasons ?? tv.actionReasons
        guard !actions.contains("rate"), let reason = reasons["rate"], viewModel.viewer?.rating == nil else { return nil }
        return reason
    }

    private func isTVEyeFilled(_ detail: MediaDetail) -> Bool {
        guard let viewer = viewModel.viewer else { return false }
        switch detail.ref.mediaType {
        case "episode":
            guard let episode = viewer.tv?.episode else { return viewer.isTracked }
            return viewer.tvIsWatched(episode)
        case "season":
            guard let season = viewer.tv?.season else { return false }
            return viewer.tvIsMarked(season)
        default:
            guard let tv = viewer.tv else { return false }
            return viewer.tvPending.markedShow || tv.eye?.filled == true
        }
    }

    private func tvEyeLabel(_ detail: MediaDetail) -> String {
        let filled = isTVEyeFilled(detail)
        switch detail.ref.mediaType {
        case "episode": return filled ? "Episode watched" : "Mark episode watched"
        case "season": return filled ? "Season watched" : "Mark season watched"
        default: return filled ? "Show watched" : "Mark show watched"
        }
    }

    private func tvEyeHint(_ detail: MediaDetail) -> String {
        let filled = isTVEyeFilled(detail)
        if detail.ref.mediaType == "episode" {
            return filled ? "Unticks this episode" : "Ticks this episode watched today"
        }
        return filled ? "Undoes marking these episodes watched" : "Marks every aired episode watched, without a date"
    }

    private func trackAction(for detail: MediaDetail) {
        if detail.ref.isTVFamily {
            tvTrackAction(for: detail)
        } else if usesBookGameActions(detail) {
            presentedSheet = .bookGameActions
        } else {
            // The composer prefills the heart, rating and rewatch from confirmed state, so a heart or rating
            // still being sent must land first.
            presentAfterSettling { isLogPresented = true }
        }
    }

    private func eyeAction(for detail: MediaDetail) {
        if detail.ref.isTVFamily {
            viewModel.tapTVEye()
            return
        }
        if detail.ref.isSingleWeight {
            if isEyeCompleted(detail) {
                let removal = viewModel.removeTracking(for: detail)
                Task {
                    if await removal.value { ratingPicker.resetAfterUnwatch() } else { ratingPicker.rollbackWatch() }
                }
            } else {
                rollBackWatchIfFails(viewModel.markConsumed(for: detail))
            }
            return
        }
        if detail.ref.mediaType == "game" {
            if gameState(detail)?.hasLivePlaythrough == true {
                openBookCompletion(for: detail)
            } else if isEyeCompleted(detail) {
                resetAfterUnwatchIfSucceeds(viewModel.performGameAction("undo_completed", for: detail))
            } else {
                rollBackWatchIfFails(viewModel.performGameAction("mark_completed", for: detail))
            }
            return
        }
        if detail.ref.mediaType == "book" {
            if bookState(detail)?.hasLiveJourney == true {
                openBookCompletion(for: detail)
            } else if isEyeCompleted(detail) {
                if bookState(detail)?.supports("undo_read") == true {
                    resetAfterUnwatchIfSucceeds(viewModel.performBookAction("undo_read", for: detail))
                } else {
                    viewModel.quickActionErrorMessage = bookState(detail)?.actionReasons["undo_read"]
                        ?? "Delete the completion log to mark this book unread."
                }
            } else {
                rollBackWatchIfFails(viewModel.performBookAction("mark_read", for: detail))
            }
            return
        }
        guard !isEyeCompleted(detail) else { return }
        guard usesBookGameActions(detail) else { return }
        viewModel.performQuickAction(.finished, for: detail)
    }

    /// The write shows its result at once; the picker's local watch only needs undoing if it fails.
    private func rollBackWatchIfFails(_ write: Task<Bool, Never>) {
        Task {
            if !(await write.value) { ratingPicker.rollbackWatch() }
        }
    }

    private func resetAfterUnwatchIfSucceeds(_ write: Task<Bool, Never>) {
        Task {
            if await write.value { ratingPicker.resetAfterUnwatch() }
        }
    }

    private func isEyeCompleted(_ detail: MediaDetail) -> Bool {
        if detail.ref.isTVFamily {
            return isTVEyeFilled(detail)
        }
        return currentStatus(detail)?.caseInsensitiveCompare("Completed") == .orderedSame
    }

    private func likeAction(for detail: MediaDetail) {
        if ["book", "game"].contains(detail.ref.mediaType),
           viewModel.viewer?.hasLiked != true,
           (bookState(detail)?.hasLiveJourney == true || gameState(detail)?.hasLivePlaythrough == true) {
            openBookCompletion(for: detail, liked: true)
            return
        }
        rollBackWatchIfFails(viewModel.toggleMediaLike(for: detail))
    }

    private func ratingAction(for detail: MediaDetail, halfSteps: Int) {
        if detail.ref.isTVFamily, detail.ref.mediaType != "episode" {
            // Show and season stars: whole steps on TV's 0-10 wire, with the season number on a season page (TV-504).
            let write = viewModel.rateTV(steps: halfSteps)
            Task {
                if !(await write.value) {
                    ratingPicker.syncConfirmed(ratingHalfSteps(viewModel.viewer?.rating))
                }
            }
            return
        }
        guard detail.ref.usesFiveStarRatingScale else { return }
        if halfSteps > 0, bookState(detail)?.hasLiveJourney == true || (halfSteps > 0 && gameState(detail)?.hasLivePlaythrough == true) {
            ratingPicker.syncConfirmed(ratingHalfSteps(viewModel.viewer?.rating))
            ratingPicker.rollbackWatch()
            openBookCompletion(for: detail, ratingSteps: halfSteps)
            return
        }
        let write = viewModel.setCurrentRating(for: detail, halfSteps: halfSteps)
        Task {
            if !(await write.value) {
                ratingPicker.syncConfirmed(ratingHalfSteps(viewModel.viewer?.rating))
            }
        }
    }

    private func ratingHalfSteps(_ rating: String?) -> Int {
        guard let rating, let value = Decimal(string: rating) else { return 0 }
        let steps = viewModel.detail?.ref.usesFiveStarRatingScale == true ? value * 2 : value
        return NSDecimalNumber(decimal: steps).intValue
    }

    private func gameState(_ detail: MediaDetail) -> GameTrackingState? {
        viewModel.viewer?.game
    }

    private func bookState(_ detail: MediaDetail) -> BookTrackingState? {
        viewModel.viewer?.book
    }

    /// The composer captures playthrough and journey ids, so it opens only once the write queue has settled,
    /// from confirmed state. A tracking sheet still open closes first; the composer follows its dismissal.
    private func openBookCompletion(for detail: MediaDetail, liked: Bool? = nil, ratingSteps: Int? = nil) {
        if presentedSheet != nil || editingGamePlaythrough != nil || progressGamePlaythrough != nil {
            closeSheetForChoice()
        }
        progressUpdateDetail = nil
        presentAfterSettling {
            let confirmed = viewModel.detail?.userState
            if detail.ref.mediaType == "game" {
                let game = confirmed?.game
                let presentation = GameCompletionPresentation(
                    playthroughId: game?.hasLivePlaythrough == true ? game?.currentPlaythrough?.id : nil,
                    liked: liked, ratingSteps: ratingSteps)
                if isClosingPresentation { pendingGameCompletion = presentation } else { gameCompletionPresentation = presentation }
                return
            }
            completionJourneyId = confirmed?.book?.currentJourney?.id
            completionLiked = liked
            completionRatingSteps = ratingSteps
            if isClosingPresentation { pendingBookLog = true } else { isLogPresented = true }
        }
    }

    private func openProgressUpdate(for detail: MediaDetail) {
        viewModel.progressErrorMessage = nil
        viewModel.formErrorMessage = nil
        if presentedSheet != nil { closeSheetForChoice() }
        presentAfterSettling {
            guard let confirmed = viewModel.detail else { return }
            if detail.ref.mediaType == "game" {
                let playthrough = confirmed.userState?.game?.currentPlaythrough
                if isClosingPresentation { pendingGameProgress = playthrough } else { progressGamePlaythrough = playthrough }
            } else {
                progressUpdateDetail = confirmed
            }
        }
    }

    /// Presents once the write queue settles. Skipped when a write it waited for failed, since the tap was routed
    /// from a state that never landed and that write's alert explains it, or when another screen or alert opened
    /// during the wait, since SwiftUI shows only one at a time.
    private func presentAfterSettling(_ present: @escaping () -> Void) {
        Task {
            guard await viewModel.settled(),
                  !isPresentingTrackingScreen, !isQuickActionAlertPresented, !isLikeAlertPresented
            else { return }
            present()
        }
    }

    private func restoreCanonicalGameRating() {
        ratingPicker.dismiss()
        ratingPicker.syncConfirmed(ratingHalfSteps(viewModel.viewer?.rating))
        ratingPicker.rollbackWatch()
        presentDeferredAlerts()
    }

    /// An alert can't present over a sheet or composer, or while a sheet is still sliding away.
    private var defersAlerts: Bool {
        isClosingPresentation || isPresentingTrackingScreen
    }

    private var isPresentingTrackingScreen: Bool {
        presentedSheet != nil || progressGamePlaythrough != nil
            || editingGamePlaythrough != nil || editingBookJourney != nil
            || gameCompletionPresentation != nil || isLogPresented
    }

    /// Closes the tracking sheet in the tap's frame. Its onDismiss, `presentPendingSheet`, ends the closing.
    private func closeSheetForChoice() {
        isClosingPresentation = true
        presentedSheet = nil
        editingGamePlaythrough = nil
        progressGamePlaythrough = nil
    }

    private func presentPendingSheet() {
        isClosingPresentation = false
        if let playthrough = pendingGameProgress {
            pendingGameProgress = nil
            progressGamePlaythrough = playthrough
        } else if let playthrough = pendingGameEdit {
            pendingGameEdit = nil
            viewModel.formErrorMessage = nil
            editingGamePlaythrough = playthrough
        } else if let presentation = pendingGameCompletion {
            pendingGameCompletion = nil
            gameCompletionPresentation = presentation
        } else if pendingBookLog {
            pendingBookLog = false
            isLogPresented = true
        }
        presentDeferredAlerts()
    }

    /// Shows an alert held back by `defersAlerts`, once nothing covers the page.
    private func presentDeferredAlerts() {
        guard !defersAlerts else { return }
        if alertAfterClose {
            alertAfterClose = false
            isQuickActionAlertPresented = viewModel.quickActionErrorMessage != nil
        }
        if likeAlertAfterClose {
            likeAlertAfterClose = false
            isLikeAlertPresented = viewModel.likeErrorMessage != nil
        }
    }

    private func openPosterPicker(for detail: MediaDetail) {
        guard canCustomizePoster(detail) else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        isPosterPickerPresented = true
    }

    private func openBackdropPicker(for detail: MediaDetail) {
        guard canCustomizeBackdrop(detail) else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        isBackdropPickerPresented = true
    }

    private func openLogoPicker(for detail: MediaDetail) {
        guard canCustomizeLogo(detail), detail.displayLogoURL != nil else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        isLogoPickerPresented = true
    }

    private func isShowingTitleLogo(_ detail: MediaDetail) -> Bool {
        showsTitleLogo && supportsTitleLogo(detail)
    }

    @ViewBuilder
    private func bottomActionRail(_ detail: MediaDetail) -> some View {
        if detail.ref.isTVFamily {
            tvActionRail(detail)
        } else {
            standardActionRail(detail)
        }
    }

    /// The TV rail: + (the + menu, or Log Episode), the eye (TV-100, TV-300, TV-302), stars and the heart. Show and
    /// season stars rate directly; the episode's show its newest rated log and open Log Episode (TV-502).
    private func tvActionRail(_ detail: MediaDetail) -> some View {
        let isEpisode = detail.ref.mediaType == "episode"
        let episodeSteps = isEpisode ? tvEpisodeRatingSteps() : nil
        let unavailableReason = isEpisode ? nil : tvRatingUnavailableReason()
        return ActionRail(
            ratingPicker: $ratingPicker,
            isLiked: viewModel.viewer?.hasLiked ?? false,
            showsEye: true,
            showsRating: true,
            offersRatingAfterBaseAction: false,
            trackLabel: isEpisode ? "Log episode" : (detail.ref.mediaType == "season" ? "Season actions" : "Track show"),
            eyeLabel: tvEyeLabel(detail),
            eyeHint: tvEyeHint(detail),
            isEyeSelected: isEyeCompleted(detail),
            isEyeLoading: viewModel.confirmingEyeWrite,
            isRatingFilled: isEpisode ? (episodeSteps ?? 0) > 0 : nil,
            onRatingTap: isEpisode
                ? { openTVComposer(ratingSteps: episodeSteps) }
                : unavailableReason.map { reason in { viewModel.tvNotice = reason } },
            onTrack: { trackAction(for: detail) },
            onLike: { likeAction(for: detail) },
            onEye: { eyeAction(for: detail) },
            onRating: { ratingAction(for: detail, halfSteps: $0) }
        )
    }

    private func standardActionRail(_ detail: MediaDetail) -> some View {
        let usesQuickActions = usesBookGameActions(detail)

        return ActionRail(
            ratingPicker: $ratingPicker,
            isLiked: viewModel.viewer?.hasLiked ?? false,
            showsEye: true,
            showsRating: (detail.ref.isSingleWeight && isEyeCompleted(detail)) || ["book", "game"].contains(detail.ref.mediaType),
            offersRatingAfterBaseAction: detail.ref.isSingleWeight
                || (detail.ref.mediaType == "book" && bookState(detail)?.hasLiveJourney != true)
                || (detail.ref.mediaType == "game" && gameState(detail)?.hasLivePlaythrough != true),
            trackLabel: usesQuickActions ? "Track" : nil,
            eyeLabel: detail.ref.isSingleWeight
                ? (detail.ref.mediaType == "music"
                    ? (isEyeCompleted(detail) ? "Listened" : "Mark as listened")
                    : (isEyeCompleted(detail) ? "Watched" : "Mark as watched"))
                : (usesQuickActions ? bookGameCopy(for: detail.ref.mediaType).finished : nil),
            isEyeSelected: isEyeCompleted(detail),
            isEyeLoading: viewModel.confirmingEyeWrite,
            onTrack: { trackAction(for: detail) },
            onLike: { likeAction(for: detail) },
            onEye: { eyeAction(for: detail) },
            onRating: { ratingAction(for: detail, halfSteps: $0) }
        )
    }

    @ViewBuilder
    private func hero(_ detail: MediaDetail) -> some View {
        if detail.ref.mediaType == "music" {
            musicHero(detail)
        } else if detail.ref.mediaType == "episode" {
            episodeHero(detail)
        } else {
            heroHeader(detail)
                .padding(.horizontal, 14)
                .padding(.bottom, 8)
                .padding(.top, resolvedTopSafeAreaInset + heroPosterTopOffset(for: detail))
                .background {
                    ZStack(alignment: .top) {
                        HeroArtwork(detail: detail)
                        if let backdropURL = backdropURLString(for: detail) {
                            GeometryReader { proxy in
                                BackdropArtwork(urlString: backdropURL)
                                    .frame(height: BackdropLayout.stripHeight(
                                        width: proxy.size.width,
                                        topInset: resolvedTopSafeAreaInset
                                    ))
                                    .onLongPressGesture {
                                        openBackdropPicker(for: detail)
                                    }
                            }
                        }
                    }
                    .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
                }
        }
    }

    private func musicHero(_ detail: MediaDetail) -> some View {
        let artworkScale: CGFloat = 1.28
        let artworkSize = MediaDetailLayout.heroPosterWidth * artworkScale

        return VStack(spacing: 0) {
            VStack(spacing: 0) {
                heroPoster(detail)
                    .scaleEffect(artworkScale)
                    .frame(width: artworkSize, height: artworkSize)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 16)

                VStack(alignment: .leading, spacing: 11) {
                    titleDisplay(
                        detail: detail,
                        title: detail.displayTitle,
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 33, weight: .heavy),
                        lineLimit: nil,
                        minimumScaleFactor: 0.66,
                        maxLogoHeight: 48
                    )

                    if let credits = MediaCreditPresentation.make(for: detail) {
                        creditBylineView(credits, textAlignment: .leading)
                    } else if let artist = musicArtist(detail) {
                        bylineText(artist, lineLimit: 1, alignment: .leading)
                    }

                    musicHeroChips(detail)
                    RatingChipRow(chips: ratingChips(detail), stacked: false)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 14)
            .padding(.top, resolvedTopSafeAreaInset + MediaDetailLayout.heroPosterTopOffset)
            .padding(.bottom, 18)
            .background {
                HeroArtwork(detail: detail, fadesToTransparent: true)
                    .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
            }

            MusicStreamingButtons(
                links: detail.music?.representativeRelease?.streamingLinks ?? []
            )
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.top, 12)
            .padding(.bottom, 28)
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func musicHeroChips(_ detail: MediaDetail) -> some View {
        let genres = detailArray(detail, "genres").map { genre in
            MediaDetailChip(
                label: genre,
                discoverRequest: discoverRequest(detail, filter: .genre(genre))
            )
        }
        let representativeRelease = detail.music?.representativeRelease
        let release = [
            detail.music.flatMap { MusicAlbumPresentation.releaseType($0) },
            representativeRelease.flatMap { MusicAlbumPresentation.songCount($0) },
            representativeRelease.flatMap { MusicAlbumPresentation.totalLength($0) },
            formattedDate(detail.music?.firstReleaseDate),
        ].compactMap { $0?.nilIfEmpty }.map { value in
            MediaDetailChip(label: value, discoverRequest: nil)
        }

        VStack(spacing: 8) {
            musicHeroChipRow(genres)
            musicHeroChipRow(release)
        }
    }

    @ViewBuilder
    private func musicHeroChipRow(_ chips: [MediaDetailChip]) -> some View {
        if !chips.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    ForEach(chips) { chip in
                        genreChip(chip)
                    }
                }
                .fixedSize(horizontal: true, vertical: false)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(chips) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .contentMargins(.horizontal, 2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func musicArtist(_ detail: MediaDetail) -> String? {
        guard let music = detail.music else { return detail.subtitle?.nilIfEmpty }
        return MusicAlbumPresentation.artistCreditText(music.artistCredit)
            ?? detail.subtitle?.nilIfEmpty
            ?? detailString(detail, "artist")
    }

    private func musicProviderAttribution(_ detail: MediaDetail) -> String {
        guard let music = detail.music, !music.coverArt.fallbackUsed else {
            return "Metadata by MusicBrainz"
        }
        return "Metadata by MusicBrainz · Cover art by Cover Art Archive"
    }

    @ViewBuilder
    private func episodeHero(_ detail: MediaDetail) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 0) {
                EpisodeHeroArtwork(
                    urlString: detail.episodeStillURL,
                    title: detail.title
                )
                .frame(height: resolvedTopSafeAreaInset + MediaDetailLayout.episodeAccessibilityArtworkHeight)
                .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
                .onLongPressGesture {
                    openBackdropPicker(for: detail)
                }

                episodeHeroContent(detail, overlaysArtwork: false)
                    .padding(.horizontal, 16)
                    .padding(.top, 18)
                    .padding(.bottom, 24)
            }
        } else {
            ZStack(alignment: .bottomLeading) {
                EpisodeHeroArtwork(
                    urlString: detail.episodeStillURL,
                    title: detail.title
                )
                .frame(height: resolvedTopSafeAreaInset + MediaDetailLayout.episodeHeroHeight)
                .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
                .onLongPressGesture {
                    openBackdropPicker(for: detail)
                }

                episodeHeroContent(detail, overlaysArtwork: true)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
                    .offset(y: MediaDetailLayout.backdropTopSpacing)
            }
            .frame(height: resolvedTopSafeAreaInset + MediaDetailLayout.episodeHeroHeight)
            .padding(.bottom, MediaDetailLayout.backdropTopSpacing)
        }
    }

    private func episodeHeroContent(_ detail: MediaDetail, overlaysArtwork: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(episodeEyebrow(detail))
                .font(.caption.weight(.heavy))
                .foregroundStyle(.white.opacity(0.7))
                .textCase(.uppercase)
                .tracking(0.7)

            Text(detail.title)
                .font(.largeTitle.weight(.black))
                .foregroundStyle(.white)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 3)
                .minimumScaleFactor(0.72)
                .accessibilityAddTraits(.isHeader)

            if let context = episodeParentContext(detail) {
                Group {
                    if let parent = parentSeasonRef(detail) ?? parentTVRef(detail) {
                        Button {
                            navigateToParent(parent)
                        } label: {
                            episodeParentLabel(context)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(context)")
                    } else {
                        episodeParentLabel(context)
                    }
                }
            }

            episodeMetadata(detail)
            episodeRatings(detail)
        }
        .padding(.top, overlaysArtwork ? 56 : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func episodeParentLabel(_ context: String) -> some View {
        HStack(spacing: 6) {
            Text(context)
                .font(.subheadline.weight(.semibold))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.bold))
        }
        .foregroundStyle(.white.opacity(0.78))
    }

    @ViewBuilder
    private func episodeMetadata(_ detail: MediaDetail) -> some View {
        let values = [
            formattedDate(detailString(detail, "air_date") ?? detail.releaseDate),
            detailString(detail, "runtime"),
        ].compactMap { $0?.nilIfEmpty }

        if !values.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 7) {
                    ForEach(Array(values.enumerated()), id: \.offset) { index, value in
                        if index > 0 {
                            Circle()
                                .fill(.white.opacity(0.42))
                                .frame(width: 3, height: 3)
                        }
                        Text(value)
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    ForEach(values, id: \.self) { value in
                        Text(value)
                    }
                }
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.66))
        }
    }

    @ViewBuilder
    private func episodeRatings(_ detail: MediaDetail) -> some View {
        let chips = ratingChips(detail)
        let hasIMDbRating = (detail.externalRatings ?? []).contains {
            $0.source.caseInsensitiveCompare("IMDb") == .orderedSame && !$0.value.isEmpty
        }

        VStack(alignment: .leading, spacing: 8) {
            RatingChipRow(chips: chips, stacked: true)
            if !hasIMDbRating, let destination = episodeIMDbURL(detail) {
                IMDbExternalLinkPill(destination: destination)
            }
        }
    }

    private func episodeEyebrow(_ detail: MediaDetail) -> String {
        let season = detail.ref.seasonNumber.map { "S\($0)" }
        let episode = detail.ref.episodeNumber.map { "E\($0)" }
        return [season, episode].compactMap { $0 }.joined(separator: " · ")
    }

    private func episodeParentContext(_ detail: MediaDetail) -> String? {
        let showTitle = detailString(detail, "show_title")
            ?? detailString(detail, "series_title")
            ?? detailString(detail, "parent_title")
            ?? detail.subtitle
        let seasonTitle = detailString(detail, "season_title")
            ?? detail.ref.seasonNumber.map { "Season \($0)" }
        return [showTitle, seasonTitle]
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " · ")
            .nilIfEmpty
    }

    private func episodeIMDbURL(_ detail: MediaDetail) -> URL? {
        if let destination = detail.externalRatings?.first(where: {
            $0.source.caseInsensitiveCompare("IMDb") == .orderedSame
        })?.destinationURL {
            return destination
        }
        for key in ["imdb_url", "imdb_link"] {
            if let value = detailString(detail, key), let destination = URL(string: value) {
                return destination
            }
        }
        return nil
    }

    private func titleDisplay(
        detail: MediaDetail,
        title: String,
        showsLogo: Binding<Bool>,
        font: Font,
        lineLimit: Int?,
        minimumScaleFactor: CGFloat,
        maxLogoHeight: CGFloat
    ) -> some View {
        MediaTitleDisplay(
            detail: detail,
            title: title,
            showsLogo: showsLogo,
            font: font,
            lineLimit: lineLimit,
            minimumScaleFactor: minimumScaleFactor,
            maxLogoHeight: maxLogoHeight,
            onTap: parentTVRef(detail).map { ref in
                { presentedRef = ref }
            },
            onLongPress: canCustomizeLogo(detail) && detail.displayLogoURL != nil
                ? { openLogoPicker(for: detail) }
                : nil
        )
    }

    @ViewBuilder
    private func heroHeader(_ detail: MediaDetail) -> some View {
        if backdropURLString(for: detail) != nil {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 11) {
                    titleDisplay(
                        detail: detail,
                        title: detail.displayTitle,
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 32, weight: .black),
                        lineLimit: 3,
                        minimumScaleFactor: 0.72,
                        maxLogoHeight: 44
                    )

                    if let credits = heroCompanyCredits(detail) {
                        companyCreditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "anime" ? .leading : nil
                        )
                    } else if let credits = MediaCreditPresentation.make(for: detail) {
                        creditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "manga" ? .leading : nil
                        )
                    } else if let byline = byline(detail) {
                        bylineView(byline, detail: detail, lineLimit: 2)
                    }

                    genreChips(detail, wrapsAfterThird: true)
                    RatingChipRow(chips: ratingChips(detail), stacked: true, streaming: streamingPills(detail))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 10)

                heroPoster(detail)
                    .frame(maxWidth: .infinity, alignment: .center)
            }
        } else {
            VStack(spacing: 0) {
                heroPoster(detail)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 16)

                VStack(alignment: .leading, spacing: 11) {
                    titleDisplay(
                        detail: detail,
                        title: detail.displayTitle,
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 33, weight: .heavy),
                        lineLimit: nil,
                        minimumScaleFactor: 0.66,
                        maxLogoHeight: 48
                    )

                    if let credits = heroCompanyCredits(detail) {
                        companyCreditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "anime" ? .leading : nil
                        )
                    } else if let credits = MediaCreditPresentation.make(for: detail) {
                        creditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "manga" ? .leading : nil
                        )
                    } else if let byline = byline(detail) {
                        bylineView(byline, detail: detail, lineLimit: 1)
                    }

                    genreChips(detail, wrapsAfterThird: false)
                    RatingChipRow(chips: ratingChips(detail), stacked: false, streaming: streamingPills(detail))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder
    private func heroPoster(_ detail: MediaDetail) -> some View {
        let poster = PosterViewerItem(detail: detail)
        let posterSize = PosterSlot.hero.artworkSize(
            mediaType: detail.ref.mediaType,
            orientation: detail.posterOrientation
        )
        let artwork = MediaArtwork(
            url: detail.displayPosterURL,
            title: detail.title,
            slot: .hero,
            mediaType: detail.ref.mediaType,
            orientation: detail.posterOrientation
        )
        .shadow(color: .black.opacity(0.48), radius: 22, y: 12)

        if presentedPosterID == detail.ref.id {
            Color.clear
                .frame(width: posterSize.width, height: posterSize.height)
        } else if let poster {
            artwork
                .matchedGeometryEffect(
                    id: detail.ref.id,
                    in: posterTransitionNamespace,
                    isSource: true
                )
                .onTapGesture {
                    onOpenPoster(poster)
                }
                .onLongPressGesture {
                    openPosterPicker(for: detail)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("View poster for \(detail.displayTitle)")
                .accessibilityHint("Opens the poster full screen")
                .accessibilityAddTraits(.isButton)
                .accessibilityAction {
                    onOpenPoster(poster)
                }
                .accessibilityIdentifier("media-detail.poster")
        } else {
            artwork
                .onLongPressGesture {
                    openPosterPicker(for: detail)
                }
        }
    }

    private func bylineView(_ byline: String, detail: MediaDetail, lineLimit: Int) -> some View {
        let alignment: Alignment = isShowingTitleLogo(detail) ? .center : .leading
        let textAlignment: TextAlignment = isShowingTitleLogo(detail) ? .center : .leading

        return Group {
            if let personRef = bylinePersonRef(detail) {
                Button {
                    presentedPerson = personRef
                } label: {
                    bylineText(byline, lineLimit: lineLimit, alignment: textAlignment)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(byline)")
            } else {
                bylineText(byline, lineLimit: lineLimit, alignment: textAlignment)
            }
        }
        .frame(maxWidth: .infinity, alignment: alignment)
    }

    private func bylineText(_ byline: String, lineLimit: Int, alignment: TextAlignment) -> some View {
        Text(byline)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.white.opacity(0.62))
            .lineLimit(lineLimit)
            .multilineTextAlignment(alignment)
    }

    private func creditBylineView(
        _ credits: MediaCreditPresentation,
        textAlignment: TextAlignment? = nil
    ) -> some View {
        let textAlignment = textAlignment ?? (showsTitleLogo ? .center : .leading)
        let alignment: Alignment = textAlignment == .center ? .center : .leading

        return VStack(alignment: textAlignment == .center ? .center : .leading, spacing: 2) {
            ForEach(credits.heroPeople, id: \.self) { person in
                personCreditText(person, alignment: textAlignment)
            }
            if credits.heroMoreCount > 0 {
                Text("+\(credits.heroMoreCount) more")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.44))
            }
        }
        .multilineTextAlignment(textAlignment)
        .frame(maxWidth: .infinity, alignment: alignment)
    }

    @ViewBuilder
    private func personCreditText(_ person: MediaPersonCredit, alignment: TextAlignment) -> some View {
        if let personRef = person.personRef {
            Button {
                presentedPerson = personRef
            } label: {
                bylineText(person.name, lineLimit: 1, alignment: alignment)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("View \(person.name)")
        } else {
            bylineText(person.name, lineLimit: 1, alignment: alignment)
        }
    }

    private func companyCreditBylineView(
        _ credits: [MediaCompanyCredit],
        textAlignment: TextAlignment? = nil
    ) -> some View {
        let textAlignment = textAlignment ?? (showsTitleLogo ? .center : .leading)
        let alignment: Alignment = textAlignment == .center ? .center : .leading
        let visibleCredits = Array(credits.prefix(2))
        let moreCount = max(0, credits.count - visibleCredits.count)

        return VStack(alignment: textAlignment == .center ? .center : .leading, spacing: 2) {
            ForEach(visibleCredits) { credit in
                Button {
                    presentedCompany = credit.ref
                } label: {
                    bylineText(credit.name, lineLimit: 1, alignment: textAlignment)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(credit.name)")
            }
            if moreCount > 0 {
                Text("+\(moreCount) more")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.44))
            }
        }
        .multilineTextAlignment(textAlignment)
        .frame(maxWidth: .infinity, alignment: alignment)
    }

    private func backdropURLString(for detail: MediaDetail) -> String? {
        return detail.displayBackdropURL
    }

    private func heroPosterTopOffset(for detail: MediaDetail) -> CGFloat {
        MediaDetailLayout.heroPosterTopOffset + (backdropURLString(for: detail) == nil ? 0 : MediaDetailLayout.backdropTopSpacing)
    }

    @ViewBuilder
    private func content(_ detail: MediaDetail) -> some View {
        if detail.ref.mediaType == "music" {
            musicContent(detail)
        } else {
            VStack(alignment: .leading, spacing: 28) {
                SynopsisText(text: synopsisPreview(detail))
                trackingSummarySection(detail)
                if detail.ref.mediaType == "book", let book = bookState(detail) {
                    BookReadingHistorySection(
                        book: book,
                        isSaving: viewModel.hasPendingWrites,
                        onEdit: { viewModel.formErrorMessage = nil; editingBookJourney = $0 },
                        onDelete: { journey in
                            viewModel.deleteBookJourney(journey, for: detail)
                        },
                        onDeleteUndated: {
                            viewModel.performBookAction("delete_undated_read", for: detail)
                        },
                        onOpenDiary: { entryId in
                            presentedDiaryEntry = PresentedDiaryEntry(id: entryId)
                        }
                    )
                }
                if detail.ref.mediaType != "episode"
                    || (detail.community?.ratingCount ?? 0) > 0
                    || viewModel.reviewCount > 0 {
                    SpineRatingDistributionSection(
                        community: detail.community,
                        mediaType: detail.ref.mediaType,
                        reviewCount: viewModel.reviewCount
                    ) {
                        isReviewsPresented = true
                    }
                }
                if let rating = AniListRatingSummary(detail: detail) {
                    AniListRatingCard(summary: rating) {
                        isAniListReviewsPresented = true
                    }
                }

                if detail.ref.mediaType == "tv" {
                    seasonsSection(detail)
                    CreditSection(title: creditTitle(detail), cast: castCredits(detail), crew: crewCredits(detail)) { person in
                        presentedPerson = person
                    }
                } else if detail.ref.mediaType == "anime",
                          let series = relatedSections(detail).first(where: { $0.id == "series" }) {
                    RecommendationsSection(
                        sections: [series],
                        onSelectSection: { _ in
                            presentedSeries = seriesRef(detail)
                        }
                    ) { item in
                        presentedMediaSelection = MediaBrowsingSelection(
                            ref: item.ref,
                            within: series.items.map(\.ref)
                        )
                    }
                    CreditSection(title: creditTitle(detail), cast: castCredits(detail), crew: crewCredits(detail)) { person in
                        presentedPerson = person
                    }
                } else {
                    CreditSection(title: creditTitle(detail), cast: castCredits(detail), crew: crewCredits(detail)) { person in
                        presentedPerson = person
                    }
                    if detail.ref.mediaType != "episode" {
                        seasonsSection(detail)
                    }
                }

                MediaFactsSection(
                    rows: detailRows(detail),
                    onPersonSelected: { person in
                        presentedPerson = person
                    },
                    onCompanySelected: { company in
                        presentedCompany = company
                    }
                )
                if detail.ref.mediaType == "season" {
                    EpisodesSection(
                        rows: TVEpisodeRowModel.rows(episodes: detail.episodes ?? [], unlisted: detail.tvUnlistedEpisodes),
                        completion: detail.completion,
                        state: { viewModel.tvEpisodeState($0.tv) },
                        isWatched: { episode in viewModel.viewer?.tvIsWatched(episode) ?? episode.watched },
                        onTick: { viewModel.toggleTVEpisode($0) },
                        onMarkUpTo: { viewModel.askMarkTVUpTo($0) }
                    ) { episode in
                        presentEpisode(episode, from: detail)
                    }
                }
                RecommendationsSection(
                    sections: relatedSections(detail).filter {
                        detail.ref.mediaType != "anime" || $0.id != "series"
                    },
                    onSelectSection: { section in
                        if section.id == "series" || section.id == "collection" {
                            presentedSeries = seriesRef(detail)
                        }
                    }
                ) { item in
                    presentedRef = item.ref
                }
                if detail.ref.mediaType == "game", let game = gameState(detail) {
                    GamePlayHistorySection(game: game,
                        onEdit: { viewModel.formErrorMessage = nil; editingGamePlaythrough = $0 },
                        onDeleteUndated: { viewModel.performGameAction("delete_undated_completion", for: detail) })
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)
        }
    }

    private func musicContent(_ detail: MediaDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            if let annotation = detail.music?.annotation?.nilIfEmpty {
                SynopsisText(text: annotation)
            }
            trackingSummarySection(detail)
            SpineRatingDistributionSection(
                community: detail.community,
                mediaType: detail.ref.mediaType,
                reviewCount: viewModel.reviewCount
            ) {
                isReviewsPresented = true
            }
            MusicAlbumTracklistSection(
                release: detail.music?.representativeRelease,
                albumCredits: detail.music?.artistCredit ?? [],
                enrichmentState: viewModel.musicEnrichmentState,
                onSelectTrack: { track in
                    presentedSong = MusicSongSelection(
                        album: detail.ref,
                        track: track,
                        artworkURL: detail.displayPosterURL
                    )
                }
            )
            MediaFactsSection(
                rows: detailRows(detail),
                onPersonSelected: { person in
                    presentedPerson = person
                },
                onCompanySelected: { company in
                    presentedCompany = company
                }
            )
            RecommendationsSection(sections: relatedSections(detail)) { item in
                presentedRef = item.ref
            }
            Text(musicProviderAttribution(detail))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.white.opacity(0.46))
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
    }

    private func presentEpisode(_ episode: EpisodeSummary, from season: MediaDetail) {
        guard let seasonNumber = season.ref.seasonNumber else { return }
        let refs = (season.episodes ?? []).map {
            MediaRef(
                itemId: nil,
                source: season.ref.source,
                mediaType: "episode",
                mediaId: season.ref.mediaId,
                seasonNumber: seasonNumber,
                episodeNumber: $0.episodeNumber
            )
        }
        let selected = MediaRef(
            itemId: nil,
            source: season.ref.source,
            mediaType: "episode",
            mediaId: season.ref.mediaId,
            seasonNumber: seasonNumber,
            episodeNumber: episode.episodeNumber
        )
        presentedMediaSelection = MediaBrowsingSelection(ref: selected, within: refs)
    }

    @ViewBuilder
    private func trackingSummarySection(_ detail: MediaDetail) -> some View {
        TrackingSummarySection(
            detail: detail,
            state: viewModel.viewer,
            onOpenDiaryEntry: {
                Task {
                    await openTrackingDiary(for: detail)
                }
            },
            onUpdateProgress: {
                openProgressUpdate(for: detail)
            },
            onTickUpNext: { viewModel.toggleTVUpNext($0) },
            onOpenUpNext: { upNext in
                presentedRef = MediaRef(
                    itemId: nil,
                    source: detail.ref.source,
                    mediaType: "episode",
                    mediaId: detail.ref.mediaId,
                    seasonNumber: upNext.seasonNumber,
                    episodeNumber: upNext.episodeNumber
                )
            }
        )
    }

    private func openTrackingDiary(for detail: MediaDetail) async {
        if isMultipleLogMedia(detail), detail.ref.itemId != nil {
            presentedMediaDiary = PresentedMediaDiary(detail: detail)
            return
        }

        if let entryId = viewModel.viewer?.diaryEntryId {
            presentedDiaryEntry = PresentedDiaryEntry(id: entryId)
            return
        }

        do {
            if let entry = try await diaryRepository.list().first(where: { matches($0.media.ref, detail.ref) }) {
                presentedDiaryEntry = PresentedDiaryEntry(id: entry.id)
            }
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func matches(_ lhs: MediaRef, _ rhs: MediaRef) -> Bool {
        lhs.source == rhs.source
            && lhs.mediaType == rhs.mediaType
            && lhs.mediaId == rhs.mediaId
            && lhs.seasonNumber == rhs.seasonNumber
            && lhs.episodeNumber == rhs.episodeNumber
    }

    private func isMultipleLogMedia(_ detail: MediaDetail) -> Bool {
        (viewModel.viewer?.diaryCount ?? 0) > 1
    }

    private func seasonsSection(_ detail: MediaDetail) -> some View {
        SeasonsSection(
            cards: TVSeasonCardModel.cards(seasons: detail.seasons ?? [], unlisted: detail.tvUnlistedSeasons),
            label: { card in
                TVSeasonCardModel(
                    seasonNumber: card.seasonNumber,
                    title: card.title,
                    imageUrl: card.imageUrl,
                    episodeCount: card.episodeCount,
                    tv: viewModel.tvSeasonState(card.tv),
                    listed: card.listed
                ).label(pending: viewModel.viewer?.tvPending ?? TVPendingWatches())
            }
        ) { season in
            presentedRef = MediaRef(
                itemId: nil,
                source: detail.ref.source,
                mediaType: "season",
                mediaId: detail.ref.mediaId,
                seasonNumber: season.seasonNumber,
                episodeNumber: nil
            )
        }
    }

    private func genreChips(_ detail: MediaDetail, wrapsAfterThird: Bool) -> some View {
        let chips = primaryChips(detail)
        let wrapsAfterThird = wrapsAfterThird || detail.ref.mediaType == "manga"

        if !wrapsAfterThird {
            return AnyView(
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(chips.prefix(6))) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.88),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }
            )
        }

        return AnyView(
            VStack(alignment: .leading, spacing: 8) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(chips.prefix(3))) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.88),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }

                if chips.count > 3 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Array(chips.dropFirst(3))) { chip in
                                genreChip(chip)
                            }
                        }
                    }
                    .mask(alignment: .trailing) {
                        LinearGradient(
                            stops: [
                                .init(color: .black, location: 0),
                                .init(color: .black, location: 0.88),
                                .init(color: .clear, location: 1),
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    }
                }
            }
        )
    }

    private func genreChip(_ chip: MediaDetailChip) -> some View {
        Group {
            if let request = chip.discoverRequest {
                Button {
                    presentedDiscover = request
                } label: {
                    chipLabel(chip.label)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Browse \(chip.label)")
            } else {
                chipLabel(chip.label)
            }
        }
    }

    private func chipLabel(_ label: String) -> some View {
        Text(label)
            .font(.system(size: 11, weight: .bold))
            .foregroundStyle(.white.opacity(0.82))
            .lineLimit(1)
            .padding(.horizontal, 11)
            .frame(height: MediaDetailLayout.genrePillHeight)
            .background(.white.opacity(0.12), in: Capsule())
    }

    private func primaryChips(_ detail: MediaDetail) -> [MediaDetailChip] {
        var chips = [MediaDetailChip(label: mediaTypeChipLabel(detail.ref.mediaType), discoverRequest: nil)]
        if detail.ref.mediaType == "anime", let season = detailString(detail, "season")?.nilIfEmpty {
            chips.append(MediaDetailChip(label: season, discoverRequest: nil))
        } else if let year = year(detail) {
            chips.append(MediaDetailChip(label: year, discoverRequest: discoverRequest(detail, filter: .year(String(year.prefix(4))))))
        }
        if let seasonLabel = seasonChipLabel(detail) {
            chips.append(MediaDetailChip(label: seasonLabel, discoverRequest: nil))
        }
        if ["movie", "tv"].contains(detail.ref.mediaType), let runtime = detailString(detail, "runtime"), !runtime.isEmpty {
            chips.append(MediaDetailChip(label: runtime, discoverRequest: nil))
        }
        if let contentRating = contentRating(detail) {
            chips.append(MediaDetailChip(label: contentRating, discoverRequest: nil))
        }
        if ["tv", "anime"].contains(detail.ref.mediaType), let episodes = detailString(detail, "episodes") {
            chips.append(MediaDetailChip(label: "\(episodes) episodes", discoverRequest: nil))
        }
        if detail.ref.mediaType == "book", let pages = detailString(detail, "number_of_pages") ?? detailString(detail, "pages") {
            chips.append(MediaDetailChip(label: "\(pages) pages", discoverRequest: nil))
        }
        if detail.ref.mediaType == "manga", let chapters = detailString(detail, "number_of_chapters") {
            chips.append(MediaDetailChip(label: "\(chapters) chapters", discoverRequest: nil))
        }
        if detail.ref.mediaType == "anime", let format = detailString(detail, "format") {
            chips.append(MediaDetailChip(label: format, discoverRequest: nil))
        }
        if detail.ref.mediaType == "game", let platform = detailArray(detail, "platforms").first {
            chips.append(MediaDetailChip(label: platform, discoverRequest: discoverRequest(detail, filter: .platform(platform))))
        }
        for genre in detailArray(detail, "genres") {
            chips.append(MediaDetailChip(label: genre, discoverRequest: discoverRequest(detail, filter: .genre(genre))))
        }

        var seen = Set<String>()
        let uniqueChips = chips.filter { chip in
            seen.insert(chip.label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()).inserted
        }
        return uniqueChips
    }

    private func discoverRequest(_ detail: MediaDetail, filter: MediaDiscoverRequest.Filter) -> MediaDiscoverRequest? {
        MediaDiscoverRequest.detailPillRequest(ref: detail.ref, filter: filter)
    }

    private func contentRating(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "game" {
            return detailString(detail, "age_rating") ?? detailArray(detail, "age_ratings").first
        }
        guard ["movie", "tv"].contains(detail.ref.mediaType) else { return nil }
        guard let rating = detailString(detail, "rating"), !rating.isEmpty else { return nil }
        return rating
    }

    private func mediaTypeChipLabel(_ mediaType: String) -> String {
        switch mediaType {
        case "tv":
            "TV"
        case "season":
            "TV"
        default:
            mediaType.capitalized
        }
    }

    private func seasonChipLabel(_ detail: MediaDetail) -> String? {
        guard let seasonNumber = detail.ref.seasonNumber, detail.ref.mediaType == "season" else { return nil }
        return TVTrackingPresentation.seasonTitle(seasonNumber)
    }

    private func currentStatus(_ detail: MediaDetail) -> String? {
        viewModel.viewer?.status
    }

    private func currentProgress(_ detail: MediaDetail) -> ProgressState? {
        viewModel.viewer?.progress
    }

    private func ratingChips(_ detail: MediaDetail) -> [RatingChip] {
        var chips: [RatingChip] = []
        if let rating = detail.community?.averageRating, !rating.isEmpty {
            chips.append(RatingChip(
                source: "SP",
                value: "\(rating.starRatingValue(mediaType: detail.ref.mediaType))/5",
                assetName: nil,
                providerName: "Spine",
                voteCount: detail.community?.ratingCount,
                voteCountLabel: "ratings"
            ))
        }
        for rating in sortedExternalRatings(detail.externalRatings ?? []) where !rating.value.isEmpty {
            if !MediaExternalRatingPresentation.includes(
                source: rating.source,
                mediaType: detail.ref.mediaType
            ) {
                continue
            }
            chips.append(RatingChip(
                source: rating.source.ratingAbbreviation,
                value: rating.displayValue,
                assetName: rating.ratingAssetName,
                providerName: rating.source,
                destination: rating.destinationURL,
                voteCount: rating.voteCount,
                voteCountLabel: rating.source.ratingCountLabel
            ))
        }
        if MediaExternalRatingPresentation.showsExternalRatingPlaceholder(
            mediaType: detail.ref.mediaType,
            ratings: detail.externalRatings ?? [],
            preparation: detail.externalRatingsPreparation
        ) {
            chips.append(RatingChip(
                source: "",
                value: "",
                assetName: nil,
                providerName: "External ratings",
                isLoading: true
            ))
        }
        return chips
    }

    /// The services to show as pills next to the ratings (ST-200); nil when there are none.
    private func streamingPills(_ detail: MediaDetail) -> WhereToWatch? {
        guard WhereToWatch.isAvailable(for: detail.ref),
              let whereToWatch = detail.whereToWatch,
              !whereToWatch.providers.isEmpty else { return nil }
        return whereToWatch
    }

    private func sortedExternalRatings(_ ratings: [ExternalRating]) -> [ExternalRating] {
        ratings.enumerated().sorted { lhs, rhs in
            let lhsOrder = MediaExternalRatingPresentation.order(for: lhs.element.source)
            let rhsOrder = MediaExternalRatingPresentation.order(for: rhs.element.source)
            return lhsOrder == rhsOrder ? lhs.offset < rhs.offset : lhsOrder < rhsOrder
        }.map {
            $0.element
        }
    }

    private func byline(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "anime", let studio = detailArray(detail, "studios").first {
            return studio
        }
        if let author = authors(detail).first {
            return author
        }
        for key in ["director", "creator", "developer", "publisher"] {
            if let value = detailString(detail, key) {
                return value
            }
        }
        if let person = detailArray(detail, "people").first {
            return person
        }
        return detail.subtitle
    }

    private func bylinePersonRef(_ detail: MediaDetail) -> PersonRef? {
        if detail.ref.mediaType == "book" {
            return bookAuthorCredits(detail).first?.personRef
        }
        if detail.ref.mediaType == "manga" {
            return detail.crew?.first?.personRef
        }
        guard detail.ref.source == "tmdb" else { return nil }
        let key = detail.ref.mediaType == "movie" ? "director_id" : detail.ref.mediaType == "tv" ? "creator_id" : nil
        guard let key,
              detailString(detail, key.replacingOccurrences(of: "_id", with: "")) != nil,
              let id = detailString(detail, key),
              !id.isEmpty
        else { return nil }
        return PersonRef(source: "tmdb", id: id)
    }

    private func synopsisPreview(_ detail: MediaDetail) -> String {
        detail.displaySynopsis ?? "No synopsis available yet."
    }

    private func detailRows(_ detail: MediaDetail) -> [DetailFactRow] {
        let mediaType = detail.ref.mediaType
        if mediaType == "music" {
            return musicDetailRows(detail)
        }
        var rows: [DetailFactRow] = [
            DetailFactRow(label: "Status", value: detailString(detail, "status")),
            DetailFactRow(label: "Format", value: detailString(detail, "format")),
        ]

        switch mediaType {
        case "movie":
            rows += [
                DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
                creditDetailRow(MediaCreditPresentation.make(for: detail)),
                DetailFactRow(label: "Box Office", value: moneyString(detail, "revenue")),
            ]
        case "tv", "season":
            rows += [
                DetailFactRow(label: "First Aired", value: formattedDate(detailString(detail, "first_air_date") ?? detail.releaseDate)),
                DetailFactRow(label: "Last Aired", value: formattedDate(detailString(detail, "last_air_date"))),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
                DetailFactRow(label: "Seasons", value: detailString(detail, "seasons")),
                DetailFactRow(label: "Episodes", value: detailString(detail, "episodes")),
                creditDetailRow(MediaCreditPresentation.make(for: detail)),
            ]
        case "episode":
            rows += [
                DetailFactRow(label: "Aired", value: formattedDate(detailString(detail, "air_date") ?? detail.releaseDate)),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Season", value: detailString(detail, "season_title") ?? detail.ref.seasonNumber.map { "Season \($0)" }),
                DetailFactRow(label: "Episode", value: detail.ref.episodeNumber.map(String.init)),
                DetailFactRow(label: "Production Code", value: detailString(detail, "production_code")),
            ]
        case "anime":
            rows += [
                DetailFactRow(label: "Episodes", value: detailString(detail, "episodes")),
                DetailFactRow(label: "Aired", value: detailString(detail, "season")),
                DetailFactRow(label: "Broadcast", value: detailString(detail, "broadcast")),
                DetailFactRow(label: "Source", value: detailString(detail, "source")),
                companyDetailRow(label: "Studios", detail: detail, role: .studio),
            ]
        case "manga":
            rows += [
                DetailFactRow(label: "Chapters", value: detailString(detail, "number_of_chapters")),
                DetailFactRow(label: "Latest Chapter", value: detailString(detail, "latest_chapter_translated")),
                DetailFactRow(label: "Year", value: detailString(detail, "year")),
            ]
        case "game":
            rows += [
                DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)),
                DetailFactRow(label: "Age Rating", value: detailString(detail, "age_rating") ?? detailArray(detail, "age_ratings").joinedOrNil),
                DetailFactRow(label: "Collection", value: detailString(detail, "collection")),
                DetailFactRow(label: "Franchise", value: detailString(detail, "franchise") ?? detailArray(detail, "franchises").joinedOrNil),
                companyDetailRow(label: "Developer", detail: detail, role: .developed),
                companyDetailRow(label: "Publisher", detail: detail, role: .published),
                DetailFactRow(label: "Themes", value: detailArray(detail, "themes").joinedOrNil),
                DetailFactRow(label: "Time to Beat", value: timeToBeatString(detail)),
            ]
        case "comic":
            rows += [
                DetailFactRow(label: "Publisher", value: detailString(detail, "publisher")),
                DetailFactRow(label: "Issues", value: detailString(detail, "issues_count")),
                DetailFactRow(label: "Last Issue", value: lastIssueString(detail)),
            ]
        case "book":
            rows += [
                DetailFactRow(label: "Pages", value: detailString(detail, "number_of_pages") ?? detailString(detail, "pages")),
                DetailFactRow(label: "Publish Date", value: formattedDate(detailString(detail, "publish_date") ?? detailString(detail, "published_date") ?? detailString(detail, "release_date"))),
                DetailFactRow(label: "Physical Format", value: detailString(detail, "physical_format")),
                DetailFactRow(label: "Series", value: bookSeries(detail)),
                DetailFactRow(label: "Maturity Rating", value: detailString(detail, "maturity_rating")),
                DetailFactRow(label: "Google Books Price", value: googleBooksPrice(detail)),
            ]
        default:
            rows.append(DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)))
        }

        for (label, key) in [
            ("Authors", "authors"),
            ("Genres", "genres"),
            ("Studios", "studios"),
            ("Country", "country"),
            ("Languages", "languages"),
            ("Platforms", "platforms"),
            ("Companies", "companies"),
            ("Publishers", "publishers"),
            ("ISBN", "isbn"),
        ] {
            if mediaType == "book", key == "authors" {
                continue
            }
            if mediaType == "anime", key == "studios" {
                continue
            }
            let values = detailArray(detail, key)
            if !values.isEmpty {
                rows.append(DetailFactRow(label: label, value: values.joined(separator: ", ")))
            }
        }
        return rows.filter { !$0.isEmpty }
    }

    private func bookSeries(_ detail: MediaDetail) -> String? {
        guard let name = detailString(detail, "series_name")?.nilIfEmpty else { return nil }
        guard let position = detailString(detail, "series_position")?.nilIfEmpty else { return name }
        return "\(name) · Book \(position)"
    }

    private func seriesRef(_ detail: MediaDetail) -> SeriesRef? {
        guard ["book", "movie", "game", "anime"].contains(detail.ref.mediaType),
              let id = detailString(detail, "series_id")?.nilIfEmpty
        else { return nil }
        return SeriesRef(
            source: detailString(detail, "series_source")?.nilIfEmpty ?? detail.ref.source,
            id: id,
            mediaType: detailString(detail, "series_media_type")?.nilIfEmpty ?? detail.ref.mediaType
        )
    }

    private func googleBooksPrice(_ detail: MediaDetail) -> String? {
        guard
            let amount = detailString(detail, "google_books_price_amount")?.nilIfEmpty,
            let currency = detailString(detail, "google_books_price_currency")?.nilIfEmpty,
            let country = detailString(detail, "google_books_price_country")?.nilIfEmpty
        else {
            return nil
        }
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = currency
        let formatted = Decimal(string: amount).flatMap {
            formatter.string(from: $0 as NSDecimalNumber)
        }
        return "\(formatted ?? amount) \(currency) · \(country)"
    }

    private func musicDetailRows(_ detail: MediaDetail) -> [DetailFactRow] {
        guard let music = detail.music else { return [] }
        let release = music.representativeRelease
        return [
            DetailFactRow(label: "Artist", value: musicArtist(detail)),
            DetailFactRow(label: "Release Date", value: formattedDate(music.firstReleaseDate)),
            DetailFactRow(label: "Release Type", value: MusicAlbumPresentation.releaseType(music)),
            DetailFactRow(label: "Track Count", value: release.map { String($0.trackCount) }),
        ].filter { !$0.isEmpty }
    }

    private func creditDetailRow(_ credits: MediaCreditPresentation?) -> DetailFactRow {
        DetailFactRow(label: credits?.label ?? "", people: credits?.people ?? [])
    }

    @MainActor
    private func heroCompanyCredits(_ detail: MediaDetail) -> [MediaCompanyCredit]? {
        let role: CompanyCatalogRole
        switch detail.ref.mediaType {
        case "anime":
            role = .studio
        case "game":
            role = .developed
        default:
            return nil
        }
        let credits = companyCredits(detail, role: role)
        guard !credits.isEmpty else { return nil }
        return detail.ref.mediaType == "anime"
            ? Array(credits.prefix(1))
            : credits
    }

    @MainActor
    private func companyDetailRow(label: String, detail: MediaDetail, role: CompanyCatalogRole) -> DetailFactRow {
        let credits = companyCredits(detail, role: role)
        if !credits.isEmpty {
            return DetailFactRow(label: label, companies: credits)
        }
        let legacyValue: String?
        switch role {
        case .developed:
            legacyValue = detailString(detail, "developer")
        case .published:
            legacyValue = detailString(detail, "publisher")
        case .studio:
            legacyValue = detailArray(detail, "studios").joinedOrNil
        }
        return DetailFactRow(label: label, value: legacyValue)
    }

    @MainActor
    private func companyCredits(_ detail: MediaDetail, role: CompanyCatalogRole? = nil) -> [MediaCompanyCredit] {
        let credits = (detail.details?["company_credits"]?.arrayValue ?? []).compactMap(MediaCompanyCredit.init(json:))
        guard let role else { return credits }
        return credits.filter { $0.hasRole(role) }
    }

    private func creditTitle(_ detail: MediaDetail) -> String {
        switch detail.ref.mediaType {
        case "book":
            "Authors"
        case "comic":
            "People"
        case "anime":
            "Cast & Characters"
        case "manga":
            "Characters & Creators"
        default:
            "Cast & Crew"
        }
    }

    private func castCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        if detail.ref.mediaType == "book" {
            return bookAuthorCredits(detail)
        }
        if detail.ref.mediaType == "manga" {
            return (detail.characters ?? []).map {
                CreditDisplay(
                    name: $0.name,
                    subtitle: $0.role,
                    imageUrl: $0.imageUrl,
                    personRef: nil
                )
            }
        }
        let supportsPeoplePages = detail.ref.source == "tmdb" && ["movie", "tv", "episode"].contains(detail.ref.mediaType)
        let credits = (detail.cast ?? []).map {
            CreditDisplay(
                name: $0.name,
                subtitle: $0.character,
                imageUrl: $0.imageUrl,
                personRef: $0.personRef
                    ?? (supportsPeoplePages && !$0.id.isEmpty
                        ? PersonRef(source: "tmdb", id: $0.id)
                        : nil)
            )
        }
        if credits.isEmpty, detail.ref.mediaType == "comic" {
            return detailArray(detail, "people").map { CreditDisplay(name: $0, subtitle: nil, imageUrl: nil, personRef: nil) }
        }
        return credits
    }

    private func crewCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        if detail.ref.mediaType == "anime" {
            return (detail.characters ?? []).map {
                CreditDisplay(
                    name: $0.name,
                    subtitle: $0.role,
                    imageUrl: $0.imageUrl,
                    personRef: nil
                )
            }
        }
        if detail.ref.mediaType == "manga" {
            return (detail.crew ?? []).map {
                CreditDisplay(
                    name: $0.name,
                    subtitle: $0.role,
                    imageUrl: $0.imageUrl,
                    personRef: $0.personRef
                )
            }
        }
        let supportsPeoplePages = detail.ref.source == "tmdb" && ["movie", "tv", "episode"].contains(detail.ref.mediaType)
        return (detail.crew ?? []).map {
            CreditDisplay(
                name: $0.name,
                subtitle: $0.role,
                imageUrl: $0.imageUrl,
                personRef: supportsPeoplePages && !$0.id.isEmpty ? PersonRef(source: "tmdb", id: $0.id) : nil
            )
        }
    }

    private func year(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "manga" {
            return MangaYearPresentation.value(
                startDate: detailString(detail, "start_date") ?? detail.releaseDate,
                endDate: detailString(detail, "end_date"),
                status: detailString(detail, "status") ?? detailString(detail, "status_in_country_of_origin")
            )
        }
        if detail.ref.mediaType == "tv" {
            let start = detailString(detail, "first_air_date") ?? detail.releaseDate
            let end = detailString(detail, "last_air_date")
            guard let startYear = start?.yearPrefix else { return nil }
            let status = detailString(detail, "status")?.lowercased()
            if let status, ["ended", "canceled", "cancelled"].contains(status), let endYear = end?.yearPrefix, endYear != startYear {
                return "\(startYear)-\(endYear)"
            }
            return startYear
        }
        let value = detail.releaseDate ?? detailString(detail, "release_date") ?? detailString(detail, "first_air_date") ?? detailString(detail, "start_date") ?? detailString(detail, "publish_date")
        guard let value, value.count >= 4 else { return nil }
        return String(value.prefix(4))
    }

    private func authors(_ detail: MediaDetail) -> [String] {
        detailArray(detail, "authors")
    }

    private func bookAuthorCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        guard detail.ref.mediaType == "book" else { return [] }
        let rawAuthors = detail.details?["authors"]?.arrayValue ?? []
        let credits = rawAuthors.compactMap { value -> CreditDisplay? in
            if let object = value.objectValue {
                guard let name = object["name"]?.displayString, !name.isEmpty else { return nil }
                let source = object["source"]?.displayString ?? bookAuthorSourceFallback(detail)
                let personId = object["person_id"]?.displayString ?? object["id"]?.displayString
                let ref = bookAuthorRef(source: source, personId: personId ?? name)
                return CreditDisplay(name: name, subtitle: "Author", imageUrl: bookAuthorImageURL(object), personRef: ref)
            }
            guard let name = value.displayString, !name.isEmpty else { return nil }
            return CreditDisplay(
                name: name,
                subtitle: "Author",
                imageUrl: nil,
                personRef: bookAuthorRef(source: bookAuthorSourceFallback(detail), personId: name)
            )
        }
        return credits.isEmpty ? authors(detail).map {
            CreditDisplay(
                name: $0,
                subtitle: "Author",
                imageUrl: nil,
                personRef: bookAuthorRef(source: bookAuthorSourceFallback(detail), personId: $0)
            )
        } : credits
    }

    private func bookAuthorSourceFallback(_ detail: MediaDetail) -> String? {
        ["hardcover", "openlibrary"].contains(detail.ref.source) ? detail.ref.source : nil
    }

    private func bookAuthorRef(source: String?, personId: String?) -> PersonRef? {
        guard let source, let personId, !source.isEmpty, !personId.isEmpty else { return nil }
        return PersonRef(source: source, id: personId)
    }

    private func bookAuthorImageURL(_ object: [String: JSONValue]) -> String? {
        object["image_url"]?.displayString
            ?? object["image"]?.displayString
            ?? object["profile_url"]?.displayString
            ?? object["cached_image"]?.displayString
    }

    private func releaseDate(_ detail: MediaDetail) -> String? {
        detail.releaseDate
            ?? detailString(detail, "release_date")
            ?? detailString(detail, "first_air_date")
            ?? detailString(detail, "start_date")
            ?? detailString(detail, "publish_date")
            ?? detailString(detail, "published_date")
    }

    private func formattedReleaseDate(_ detail: MediaDetail) -> String? {
        formattedDate(releaseDate(detail))
    }

    private func formattedDate(_ raw: String?) -> String? {
        guard let raw else { return nil }
        return Self.longDateFormatter.string(from: raw) ?? raw
    }

    private func moneyString(_ detail: MediaDetail, _ key: String) -> String? {
        guard let value = detail.details?[key]?.numberValue, value > 0 else { return detailString(detail, key) }
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        formatter.maximumFractionDigits = 0
        formatter.usesGroupingSeparator = true
        return formatter.string(from: NSNumber(value: value))
    }

    private func timeToBeatString(_ detail: MediaDetail) -> String? {
        guard let object = detail.details?["time_to_beat"]?.objectValue else { return detailString(detail, "time_to_beat") }
        let keys = [
            ("normally", "Main"),
            ("hastily", "Rush"),
            ("completely", "Complete"),
        ]
        let parts = keys.compactMap { key, label -> String? in
            guard let value = object[key]?.numberValue, value > 0 else { return nil }
            return "\(label) \(Int(value / 3600))h"
        }
        return parts.joinedOrNil
    }

    private func lastIssueString(_ detail: MediaDetail) -> String? {
        let name = detailString(detail, "last_issue_name")
        let number = detailString(detail, "last_issue_number")
        return [name, number.map { "#\($0)" }].compactMap { $0 }.joined(separator: " ").nilIfEmpty
    }

    private static let longDateFormatter: LongDateFormatter = LongDateFormatter()

    private func detailString(_ detail: MediaDetail, _ key: String) -> String? {
        detail.details?[key]?.displayString
    }

    private func detailArray(_ detail: MediaDetail, _ key: String) -> [String] {
        detail.details?[key]?.displayStrings ?? []
    }

    private func relatedSections(_ detail: MediaDetail) -> [RelatedMediaSection] {
        let sections: [RelatedMediaSection]
        if let relatedSections = detail.relatedSections, !relatedSections.isEmpty {
            sections = relatedSections
        } else if let related = detail.related {
            sections = related.compactMap { key, value in
                guard key != "seasons", let values = value.arrayValue else { return nil }
                let items = values.compactMap { rawRelatedSummary($0, parent: detail) }
                guard !items.isEmpty else { return nil }
                let isCollection = detail.ref.mediaType == "movie" && key != "recommendations"
                let id = isCollection ? "collection" : key
                let title = isCollection ? "Collection" : key.replacingOccurrences(of: "_", with: " ").capitalized
                return RelatedMediaSection(id: id, title: title, items: items)
            }
        } else {
            return []
        }
        let normalized: [RelatedMediaSection] = sections.compactMap { section -> RelatedMediaSection? in
            if section.id == "all_related" {
                return nil
            }
            return section
        }
        return normalized.sorted { lhs, rhs in
            if lhs.id == "collection", rhs.id != "collection" { return true }
            if rhs.id == "collection", lhs.id != "collection" { return false }
            return false
        }
    }

    private func rawRelatedSummary(_ value: JSONValue, parent: MediaDetail) -> MediaSummary? {
        guard let object = value.objectValue else { return nil }
        let source = object["source"]?.displayString ?? parent.ref.source
        let mediaType = object["media_type"]?.displayString ?? parent.ref.mediaType
        guard let mediaId = object["media_id"]?.displayString ?? object["id"]?.displayString,
              let title = object["title"]?.displayString ?? object["name"]?.displayString else { return nil }
        return MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: source,
                mediaType: mediaType,
                mediaId: mediaId,
                seasonNumber: object["season_number"]?.intValue,
                episodeNumber: object["episode_number"]?.intValue
            ),
            title: title,
            preferredTitle: object["display_title"]?.displayString,
            relation: object["relation"]?.displayString,
            subtitle: object["year"]?.displayString,
            overview: object["overview"]?.displayString,
            imageUrl: object["image_url"]?.displayString ?? object["image"]?.displayString,
            posterUrl: object["poster_url"]?.displayString,
            customPosterUrl: object["custom_poster_url"]?.displayString,
            posterOrientation: PosterOrientation(rawValue: object["poster_orientation"]?.displayString ?? "") ?? .unknown,
            posterAccentColor: object["poster_accent_color"]?.displayString,
            releaseDate: object["release_date"]?.displayString ?? object["first_air_date"]?.displayString,
            defaultSource: source,
            userState: nil
        )
    }
}

private enum MediaDetailLayout {
    static let heroPosterWidth: CGFloat = 191
    static let episodeHeroHeight: CGFloat = 520
    static let episodeAccessibilityArtworkHeight: CGFloat = 280
    static let heroPosterTopOffset: CGFloat = 108
    static let backdropTopSpacing: CGFloat = 137.5
    static let genrePillHeight: CGFloat = 31
    static let ratingBadgeSize: CGFloat = 24
    static let ratingPillVerticalPadding: CGFloat = 6
    static var ratingPillHeight: CGFloat { ratingBadgeSize + ratingPillVerticalPadding * 2 }
    static let recommendationPosterSize = CGSize(width: 100, height: 150)
    static let recommendationCardHeight: CGFloat = 190
    static let seasonPosterSize = CGSize(width: 90, height: 135)
}

enum SpinePalette {
    static let pageBackground = Color(red: 0.07, green: 0.07, blue: 0.065)
}

struct SpinePageBackground: View {
    var body: some View {
        SpinePalette.pageBackground
            .ignoresSafeArea()
    }
}

private struct CircleIconButton: View {
    let systemName: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

func bookGameCopy(for mediaType: String) -> (currently: String, finished: String, stopped: String) {
    switch mediaType {
    case "book":
        return ("Currently Reading", "Finished Reading", "Stopped Reading")
    case "music":
        return ("Start Listening", "Mark Listened", "Stopped")
    default:
        return ("Currently Playing", "Finished Playing", "Stopped Playing")
    }
}

private struct PosterMenuSheet: View {
    let posterLabel: String
    let showsSeasonOption: Bool
    let showsTVShowOption: Bool
    let showsPosterOption: Bool
    let showsBackdropOption: Bool
    let showsLogoOption: Bool
    let onViewSeason: () -> Void
    let onViewTVShow: () -> Void
    let onAddToList: () -> Void
    let onCustomizePoster: () -> Void
    let onCustomizeBackdrop: () -> Void
    let onCustomizeLogo: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            if showsSeasonOption {
                Button(action: onViewSeason) {
                    Label("View Season", systemImage: "rectangle.stack")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            if showsTVShowOption {
                Button(action: onViewTVShow) {
                    Label("View TV Show", systemImage: "tv")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            Button(action: onAddToList) {
                Label("Add to List", systemImage: "list.bullet.rectangle")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .frame(height: 54)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)

            if showsPosterOption {
                Button(action: onCustomizePoster) {
                    Label(posterLabel, systemImage: "photo.on.rectangle.angled")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            if showsBackdropOption {
                Button(action: onCustomizeBackdrop) {
                    Label("Customize Backdrop", systemImage: "photo.on.rectangle")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            if showsLogoOption {
                Button(action: onCustomizeLogo) {
                    Label("Customize Logo", systemImage: "textformat")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }
        }
        .presentationBackground(.regularMaterial)
    }
}

/// Only books open this sheet: games use GameActionSheet, so the non-book branch is unreachable today.
private struct BookGameActionSheet: View {
    /// What the book list showed when a choice was made, kept while the sheet slides away (see GameActionSheet).
    private struct Snapshot {
        let status: String?
        let bookState: BookTrackingState?
    }

    @State private var isRemoveConfirmationPresented = false
    @State private var isDNFConfirmationPresented = false
    @State private var committed: Snapshot?

    let mediaType: String
    let status: String?
    let bookState: BookTrackingState?
    let onAction: (MediaDetailQuickAction) -> Void
    let onRemove: () -> Void
    let onUpdateProgress: () -> Void
    let onLog: () -> Void

    private var shown: Snapshot { committed ?? Snapshot(status: status, bookState: bookState) }

    private var isInProgress: Bool {
        status == "In progress"
    }

    @ViewBuilder
    var body: some View {
        if mediaType == "book" {
            bookActions
        } else {
            let copy = bookGameCopy(for: mediaType)
            VStack(spacing: 16) {
                HStack(spacing: 12) {
                    if mediaType == "music" && isInProgress {
                        actionButton(title: "Pause", systemName: "pause.fill", action: .paused)
                    } else if isInProgress {
                        progressButton
                    } else {
                        actionButton(
                            title: mediaType == "music" && (status == "Paused" || status == "Dropped") ? "Resume Listening" : copy.currently,
                            systemName: "play.fill",
                            action: .currently
                        )
                    }
                    actionButton(title: copy.finished, systemName: "checkmark", action: .finished)
                    actionButton(title: copy.stopped, systemName: "xmark", action: .stopped)
                    logButton
                }
                .padding(.horizontal, 16)
                .padding(.top, 36)

                if mediaType == "music" {
                    HStack(spacing: 12) {
                        Button {
                            onAction(.planning)
                        } label: {
                            Label(status == "Planning" ? "Planning" : "Add to Planning", systemImage: "bookmark")
                        }
                        .disabled(status == "Planning")

                        if status != nil {
                            Button(role: .destructive) {
                                isRemoveConfirmationPresented = true
                            } label: {
                                Label("Remove Tracking", systemImage: "trash")
                            }
                        }
                    }
                    .font(.system(size: 13, weight: .bold))
                    .buttonStyle(.bordered)
                    .tint(.white.opacity(0.82))
                    .padding(.horizontal, 16)
                }
            }
            .presentationBackground(.regularMaterial)
            .presentationDetents([.height(mediaType == "music" ? 292 : 224)])
            .presentationDragIndicator(.visible)
            .confirmationDialog("Remove album tracking?", isPresented: $isRemoveConfirmationPresented, titleVisibility: .visible) {
                Button("Remove Tracking", role: .destructive, action: onRemove)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Diary logs are kept.")
            }
        }
    }

    /// The same list as the game sheet, sized to its rows.
    private var bookActions: some View {
        let status = shown.status
        let bookState = shown.bookState
        let isInProgress = status == "In progress"
        return NavigationStack {
            List {
                Section {
                    bookStatusButton("To Read", systemImage: "bookmark", action: .planning,
                                     selected: status == "Planning",
                                     disabled: status == "Planning" || bookState?.hasLiveJourney == true)
                    bookStatusButton("Currently Reading", systemImage: "book.pages", action: .currently,
                                     selected: isInProgress, disabled: isInProgress)
                    bookStatusButton("Paused", systemImage: "pause.fill", action: .paused,
                                     selected: status == "Paused", disabled: status == "Paused")
                    Button {
                        if bookState?.hasLiveJourney == true { isDNFConfirmationPresented = true }
                        else { commit { onAction(.stopped) } }
                    } label: {
                        Label(status == "Dropped" ? "Did Not Finish ✓" : "Did Not Finish", systemImage: "xmark")
                    }
                    .disabled(status == "Dropped")
                    if status == "Paused" { Button("Resume Reading") { commit { onAction(.currently) } } }
                    Button("Log Read", systemImage: "square.and.pencil") { commit(onLog) }
                }
                if bookState?.hasLiveJourney == true {
                    Text(bookState?.actionReasons["planning"] ?? "Pause or finish the active journey before moving this book to To Read.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Update Progress", systemImage: "slider.horizontal.3") { commit(onUpdateProgress) }
                }
                if status != nil {
                    Section {
                        Button("Remove Tracking", role: .destructive) {
                            if bookState?.canRemoveTracking != false { isRemoveConfirmationPresented = true }
                        }
                        .disabled(bookState?.canRemoveTracking == false)
                        if bookState?.canRemoveTracking == false, let reason = bookState?.removeTrackingReason {
                            Text(reason).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .fitsSheetToContent()
            .navigationTitle("Track this book").navigationBarTitleDisplayMode(.inline)
        }
        .preferredColorScheme(.dark)
        .confirmationDialog("Mark this book as Did Not Finish?", isPresented: $isDNFConfirmationPresented, titleVisibility: .visible) {
            Button("Mark as DNF", role: .destructive) {
                commit { onAction(.stopped) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Your current progress will remain in reading history.")
        }
        .confirmationDialog("Remove book tracking?", isPresented: $isRemoveConfirmationPresented, titleVisibility: .visible) {
            Button("Remove Tracking", role: .destructive) {
                commit(onRemove)
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Diary logs are kept. Reading journeys must be deleted first.")
        }
    }

    private func commit(_ choice: () -> Void) {
        guard committed == nil else { return }
        committed = Snapshot(status: status, bookState: bookState)
        choice()
    }

    private func bookStatusButton(_ title: String, systemImage: String, action: MediaDetailQuickAction,
                                  selected: Bool, disabled: Bool) -> some View {
        Button { commit { onAction(action) } } label: {
            Label(selected ? "\(title) ✓" : title, systemImage: systemImage)
        }
        .disabled(disabled)
    }

    private func actionButton(title: String, systemName: String, action: MediaDetailQuickAction) -> some View {
        Button {
            onAction(action)
        } label: {
            actionLabel(title: title, systemName: systemName)
        }
        .buttonStyle(.plain)
    }

    private var progressButton: some View {
        Button(action: onUpdateProgress) {
            actionLabel(title: "Update Progress", systemName: "slider.horizontal.3")
        }
        .buttonStyle(.plain)
    }

    private var logButton: some View {
        Button(action: onLog) {
            actionLabel(title: mediaType == "music" ? "Log Album" : "Log", systemName: "square.and.pencil")
        }
        .buttonStyle(.plain)
    }

    private func actionLabel(title: String, systemName: String) -> some View {
        VStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(.white.opacity(0.055))
                    .overlay {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(.white.opacity(0.13), lineWidth: 1.25)
                    }
                Image(systemName: systemName)
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.94))
            }
            .frame(height: 86)
            .shadow(color: .black.opacity(0.18), radius: 10, y: 6)

            Text(title)
                .font(.system(size: 14, weight: .heavy))
                .foregroundStyle(.white.opacity(0.92))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.82)
                .frame(height: 36, alignment: .top)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct EpisodeDetailLoadingView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            RoundedRectangle(cornerRadius: 0)
                .fill(.white.opacity(0.08))
                .frame(height: 330)

            VStack(alignment: .leading, spacing: 10) {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .fill(.white.opacity(0.1))
                    .frame(width: 74, height: 12)
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(.white.opacity(0.12))
                    .frame(maxWidth: .infinity)
                    .frame(height: 34)
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(.white.opacity(0.08))
                    .frame(width: 210, height: 16)
                HStack(spacing: 10) {
                    Capsule().fill(.white.opacity(0.1)).frame(width: 112, height: 38)
                    Spacer()
                    ForEach(0 ..< 3, id: \.self) { _ in
                        Circle().fill(.white.opacity(0.1)).frame(width: 42, height: 42)
                    }
                }
            }
            .padding(.horizontal, 16)
        }
        .redacted(reason: .placeholder)
        .accessibilityLabel("Loading episode details")
        .frame(maxWidth: .infinity, minHeight: 520, alignment: .top)
    }
}

private struct EpisodeHeroArtwork: View {
    let urlString: String?
    let title: String

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                placeholder

                if let urlString, let url = URL(string: urlString) {
                    SpineAsyncImage(url: url) { phase in
                        if case let .success(image) = phase {
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .clipped()
                        }
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.5), location: 0),
                        .init(color: .black.opacity(0.12), location: 0.3),
                        .init(color: .clear, location: 0.48),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.25),
                        .init(color: .black.opacity(0.2), location: 0.5),
                        .init(color: SpinePalette.pageBackground.opacity(0.72), location: 0.72),
                        .init(color: SpinePalette.pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                LinearGradient(
                    colors: [.black.opacity(0.28), .clear, .black.opacity(0.08)],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .clipped()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Episode still for \(title)")
    }

    private var placeholder: some View {
        let theme = MediaTypeTheme.theme(for: "episode")
        return LinearGradient(
            colors: theme.gradientColors.map { $0.opacity(0.52) } + [SpinePalette.pageBackground],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

private struct IMDbExternalLinkPill: View {
    let destination: URL

    var body: some View {
        Link(destination: destination) {
            HStack(spacing: 6) {
                Image("RatingIMDb")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 24, height: 24)
                    .clipShape(Circle())
                Text("IMDb")
                    .font(.caption.weight(.heavy))
                Image(systemName: "arrow.up.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.white.opacity(0.5))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 9)
            .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
            .background(.white.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View this episode on IMDb")
    }
}

struct HeroArtwork: View {
    /// How a poster or cover is blurred when there's no backdrop. List headers without one blur a cover the same way.
    enum PosterFallback {
        static let blurRadius: CGFloat = 30
        static let scale: CGFloat = 1.28
        static let saturation: Double = 1.18
    }

    private let artworkURL: URL?
    private let usesPosterFallback: Bool
    private let accentColor: Color
    /// Fades out to whatever is behind it instead of into the page's near-black, so an album page's color
    /// can continue below the hero.
    private let fadesToTransparent: Bool

    init(detail: MediaDetail, fadesToTransparent: Bool = false) {
        artworkURL = URL(string: detail.displayBackdropURL ?? detail.displayPosterURL ?? "")
        usesPosterFallback = detail.displayBackdropURL == nil && detail.displayPosterURL != nil
        accentColor = Color(hex: detail.posterAccentColor) ?? SpinePalette.pageBackground
        self.fadesToTransparent = fadesToTransparent
    }

    init(artworkURL: URL?, accentColor: Color = SpinePalette.pageBackground) {
        self.artworkURL = artworkURL
        usesPosterFallback = artworkURL != nil
        self.accentColor = accentColor
        fadesToTransparent = false
    }

    @ViewBuilder
    var body: some View {
        if fadesToTransparent {
            // The same curve as the near-black fade below, as opacity.
            layers.mask {
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0.48),
                        .init(color: .white.opacity(0.88), location: 0.62),
                        .init(color: .white.opacity(0.55), location: 0.78),
                        .init(color: .white.opacity(0.18), location: 0.9),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
        } else {
            layers
        }
    }

    private var layers: some View {
        ZStack {
            SpinePalette.pageBackground

            GeometryReader { proxy in
                SpineAsyncImage(url: artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        accentColor
                            .frame(width: proxy.size.width, height: proxy.size.height)
                    }
                }
                .blur(radius: blurRadius, opaque: true)
                .scaleEffect(scale)
                .brightness(0.02)
                .saturation(usesPosterFallback ? PosterFallback.saturation : 1.34)
                .frame(width: proxy.size.width, height: proxy.size.height)
            }

            accentColor
                .opacity(0.07)
                .blendMode(.softLight)

            LinearGradient(
                stops: [
                    .init(color: .black.opacity(0.22), location: 0),
                    .init(color: .black.opacity(0.08), location: 0.14),
                    .init(color: .clear, location: 0.38),
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            if !fadesToTransparent {
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.48),
                        .init(color: SpinePalette.pageBackground.opacity(0.12), location: 0.62),
                        .init(color: SpinePalette.pageBackground.opacity(0.45), location: 0.78),
                        .init(color: SpinePalette.pageBackground.opacity(0.82), location: 0.9),
                        .init(color: SpinePalette.pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }

            RadialGradient(
                colors: [.clear, .black.opacity(0.1)],
                center: .center,
                startRadius: 60,
                endRadius: 360
            )
            .blendMode(.multiply)
        }
        .clipped()
    }

    private var blurRadius: CGFloat {
        usesPosterFallback ? PosterFallback.blurRadius : 22
    }

    private var scale: CGFloat {
        usesPosterFallback ? PosterFallback.scale : 1.2
    }

}

struct BackdropArtwork: View {
    let urlString: String

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                SpineAsyncImage(url: URL(string: urlString)) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        Color.clear
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.42), location: 0),
                        .init(color: .black.opacity(0.18), location: 0.36),
                        .init(color: .clear, location: 0.72),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .mask(
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0),
                        .init(color: .white, location: 0.52),
                        .init(color: .white.opacity(0.35), location: 0.78),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
        .clipped()
    }
}

private struct ActionRail: View {
    private static let buttonSize: CGFloat = 48

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPressing = false
    @State private var pressHaptics = UIImpactFeedbackGenerator(style: .light)
    @State private var actionHaptics = UISelectionFeedbackGenerator()

    @Binding var ratingPicker: MediaRatingPickerState
    let isLiked: Bool
    var showsEye = true
    var showsRating = false
    var offersRatingAfterBaseAction = false
    var trackLabel: String?
    var eyeLabel: String?
    /// What the eye does, for VoiceOver. Without one, an eye that opens the rating picker says so.
    var eyeHint: String?
    var isEyeSelected = false
    var isEyeLoading = false
    var isLikeLoading = false
    /// Overrides the star's fill, for stars that aren't a current rating (an episode's newest rated log, TV-502).
    var isRatingFilled: Bool?
    /// When set, the star runs this instead of opening the rating picker.
    var onRatingTap: (() -> Void)?
    let onTrack: () -> Void
    let onLike: () -> Void
    var onEye: () -> Void = {}
    var onRating: (Int) -> Void = { _ in }

    var body: some View {
        VStack(spacing: -10) {
            if showsEye, ratingPicker.isPresented {
                ratingComposer
                    .transition(.opacity)
            }

            rail
        }
        .frame(width: surfaceWidth)
        .glassEffect(.regular.interactive(), in: glassShape)
        .onAppear {
            pressHaptics.prepare()
            actionHaptics.prepare()
        }
    }

    private var surfaceWidth: CGFloat {
        let buttonCount = 2 + (showsEye ? 1 : 0) + (showsRating ? 1 : 0)
        let railWidth = CGFloat(buttonCount) * Self.buttonSize + 10
        return ratingPicker.isPresented ? max(railWidth, 183) : railWidth
    }

    private var glassShape: RoundedRectangle {
        RoundedRectangle(
            cornerRadius: ratingPicker.isPresented ? 26 : 29,
            style: .continuous
        )
    }

    private var rail: some View {
        HStack(spacing: 0) {
            railButton(
                systemName: "plus",
                // The + opens a new log for these types, whether or not they're tracked.
                label: trackLabel ?? "Log",
                usesLargePlus: true,
                action: handleTrack
            )
            if showsEye {
                railButton(
                    systemName: isEyeSelected || ratingPicker.hasLocallyWatched ? "eye.slash.fill" : "eye",
                    label: eyeLabel ?? "Mark as watched",
                    usesLargePlus: false,
                    isLoading: isEyeLoading && !ratingPicker.isPresented,
                    action: handleEye
                )
                .accessibilityValue(offersRatingAfterBaseAction ? (ratingPicker.isPresented ? "Expanded" : "Collapsed") : "")
                .accessibilityHint(eyeAccessibilityHint)
            }
            if showsRating {
                let isFilled = isRatingFilled ?? (ratingPicker.confirmedHalfSteps > 0)
                railButton(
                    systemName: isFilled ? "star.fill" : "star",
                    label: isFilled ? "Edit rating" : "Rate",
                    usesLargePlus: false,
                    action: handleRating
                )
            }
            railButton(
                systemName: isLiked ? "heart.fill" : "heart",
                label: isLiked ? "Unlike" : "Like",
                usesLargePlus: false,
                isLoading: isLikeLoading,
                action: handleLike
            )
        }
        .padding(5)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !isPressing else { return }
                    isPressing = true
                    pressHaptics.impactOccurred()
                    pressHaptics.prepare()
                }
                .onEnded { _ in isPressing = false }
        )
        .accessibilityIdentifier("media-detail.actions")
    }

    private var ratingComposer: some View {
        ZStack {
            StarRatingPill(halfSteps: $ratingPicker.draftHalfSteps)

            if ratingPicker.showsConfirm {
                Button(action: confirmRating) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white.opacity(0.9))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isEyeLoading || isLikeLoading)
                .offset(x: 86)
                .transition(.offset(x: 23.75).combined(with: .opacity))
                .accessibilityLabel("Confirm rating")
                .accessibilityIdentifier("media-detail.rating-confirm")
            }
        }
        .frame(width: 280, height: 60)
        .offset(x: ratingPicker.showsConfirm ? -9.75 : 0)
        .animation(
            reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84),
            value: ratingPicker.showsConfirm
        )
        .accessibilityIdentifier("media-detail.rating-picker")
    }

    private func handleTrack() {
        dismissRatingPickerIfNeeded()
        onTrack()
    }

    private func handleEye() {
        guard offersRatingAfterBaseAction else {
            dismissRatingPickerIfNeeded()
            onEye()
            return
        }
        if isEyeSelected {
            dismissRatingPickerIfNeeded()
            onEye()
        } else if ratingPicker.isPresented {
            animate { ratingPicker.dismiss() }
        } else {
            let shouldTrack = !isEyeSelected && !ratingPicker.hasLocallyWatched
            animate { ratingPicker.open() }
            if shouldTrack {
                onEye()
            }
        }
    }

    private func handleLike() {
        if !isLiked, offersRatingAfterBaseAction, !ratingPicker.isPresented {
            animate { ratingPicker.open() }
        } else {
            dismissRatingPickerIfNeeded()
        }
        onLike()
    }

    private var eyeAccessibilityHint: String {
        if let eyeHint { return eyeHint }
        guard offersRatingAfterBaseAction, !isEyeSelected else { return "" }
        return ratingPicker.isPresented ? "Closes the rating picker" : "Opens the rating picker"
    }

    private func handleRating() {
        if let onRatingTap {
            dismissRatingPickerIfNeeded()
            onRatingTap()
            return
        }
        if ratingPicker.isPresented {
            animate { ratingPicker.dismiss() }
        } else {
            animate { ratingPicker.open(markLocallyWatched: false) }
        }
    }

    private func confirmRating() {
        let halfSteps = ratingPicker.draftHalfSteps
        actionHaptics.selectionChanged()
        actionHaptics.prepare()
        animate { ratingPicker.confirm() }
        onRating(halfSteps)
    }

    private func dismissRatingPickerIfNeeded() {
        guard ratingPicker.isPresented else { return }
        animate { ratingPicker.dismiss() }
    }

    private func animate(_ changes: () -> Void) {
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84)) {
            changes()
        }
    }

    private func railButton(
        systemName: String,
        label: String,
        usesLargePlus: Bool,
        isLoading: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button {
            actionHaptics.selectionChanged()
            actionHaptics.prepare()
            action()
        } label: {
            ZStack {
                Group {
                    if isLoading {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: systemName)
                            .font(.system(size: usesLargePlus ? 22 : 16, weight: .semibold))
                            .foregroundStyle(railIconColor(systemName: systemName))
                    }
                }
                .spineContentTransition(value: isLoading)
            }
            .frame(width: Self.buttonSize, height: Self.buttonSize)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading)
        .accessibilityLabel(label)
    }

    private func railIconColor(systemName: String) -> Color {
        if systemName == "heart.fill" { return .pink }
        if systemName == "star.fill" { return .yellow }
        return .white.opacity(0.84)
    }
}

private struct StarRatingPill: View {
    @Binding var halfSteps: Int
    @State private var haptics = UISelectionFeedbackGenerator()

    private let duneGold = Color(red: 0.94, green: 0.64, blue: 0.24)

    var body: some View {
        GeometryReader { proxy in
            HStack(spacing: 1.25) {
                ForEach(1...5, id: \.self) { value in
                    Image(systemName: starSymbol(for: value))
                        .font(.system(size: 17.5, weight: .medium))
                        .foregroundStyle(halfSteps >= value * 2 - 1 ? duneGold : .white.opacity(0.28))
                        .frame(width: 22.5, height: 30)
                }
            }
            .accessibilityHidden(true)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Capsule())
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { gesture in
                        let clamped = min(max(gesture.location.x, 0), proxy.size.width)
                        let nextValue = min(
                            max(Int(ceil((clamped / proxy.size.width) * 10)), 0),
                            10
                        )
                        setRating(nextValue)
                    }
            )
        }
        .frame(width: 127.5, height: 30)
        .padding(.horizontal, 18)
        .padding(.vertical, 15)
        .onAppear { haptics.prepare() }
        .accessibilityRepresentation {
            Slider(value: Binding(get: { Double(halfSteps) }, set: { setRating(Int($0)) }), in: 0...10, step: 1) {
                Text("Your rating")
            }
            .accessibilityValue(Text(verbatim: halfSteps == 0 ? "Not rated" : "\(Double(halfSteps) / 2) out of 5"))
            .accessibilityIdentifier("media-detail.star-rating")
        }
    }

    private func starSymbol(for value: Int) -> String {
        if halfSteps >= value * 2 { return "star.fill" }
        if halfSteps == value * 2 - 1 { return "star.leadinghalf.filled" }
        return "star"
    }

    private func setRating(_ newValue: Int) {
        guard newValue != halfSteps else { return }
        halfSteps = newValue
        haptics.selectionChanged()
        haptics.prepare()
    }
}

struct SectionLabel: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 11, weight: .heavy))
            .foregroundStyle(.white.opacity(0.62))
            .tracking(0)
    }
}

struct RatingChip: Hashable {
    static let loadingID = "external-rating-loading"

    let source: String
    let value: String
    let assetName: String?
    let providerName: String
    let destination: URL?
    let voteCount: Int?
    let voteCountLabel: String?
    let isAttributionOnly: Bool
    let isLoading: Bool

    init(
        source: String,
        value: String,
        assetName: String?,
        providerName: String? = nil,
        destination: URL? = nil,
        voteCount: Int? = nil,
        voteCountLabel: String? = nil,
        isAttributionOnly: Bool = false,
        isLoading: Bool = false
    ) {
        self.source = source
        self.value = value
        self.assetName = assetName
        self.providerName = providerName ?? source
        self.destination = destination
        self.voteCount = voteCount
        self.voteCountLabel = voteCountLabel
        self.isAttributionOnly = isAttributionOnly
        self.isLoading = isLoading
    }

    var id: String {
        isLoading ? Self.loadingID : "rating:\(providerName.lowercased())"
    }

    var accessibilityLabel: String {
        var components = [
            isAttributionOnly ? "\(providerName) \(value)" : "\(providerName) rating \(value)"
        ]
        if let voteCount, voteCount > 0 {
            components.append("\(voteCount.formatted()) \(voteCountLabel ?? "votes")")
        }
        return components.joined(separator: ", ")
    }
}

private struct RatingChipRow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let chips: [RatingChip]
    let stacked: Bool
    /// Streaming services shown as logo pills at the right end of the rows (ST-200).
    var streaming: WhereToWatch?

    private var chipIDs: [String] {
        chips.map(\.id)
    }

    private var streamingProviders: [WhereToWatchProvider] {
        streaming?.providers ?? []
    }

    private var chipTransition: AnyTransition {
        reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.96))
    }

    var body: some View {
        if !chips.isEmpty || !streamingProviders.isEmpty {
            if stacked {
                // Next to the poster there's room for one streaming pill per row, from the top.
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(0..<max(chips.count, streamingProviders.count), id: \.self) { row in
                        HStack(spacing: 8) {
                            if row < chips.count {
                                ratingChip(chips[row])
                                    .id(chips[row].id)
                                    .transition(chipTransition)
                            }
                            Spacer(minLength: 0)
                            if row < streamingProviders.count {
                                streamingPill(streamingProviders[row])
                            }
                        }
                    }
                }
                .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: chipIDs)
            } else {
                HStack(spacing: 8) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(chips, id: \.id) { chip in
                                ratingChip(chip)
                                    .transition(chipTransition)
                            }
                        }
                        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: chipIDs)
                    }
                    ForEach(streamingProviders) { provider in
                        streamingPill(provider)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func streamingPill(_ provider: WhereToWatchProvider) -> some View {
        if let link = streaming?.linkURL {
            Link(destination: link) {
                streamingPillContent(provider)
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(provider.accessibilityLabel)
            .accessibilityHint("Opens where to watch in your browser")
        } else {
            streamingPillContent(provider)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(provider.accessibilityLabel)
        }
    }

    /// Only the service's logo, in the rating pills' capsule.
    private func streamingPillContent(_ provider: WhereToWatchProvider) -> some View {
        StreamingProviderLogo(url: provider.logoUrl, name: provider.name, size: MediaDetailLayout.ratingBadgeSize)
            .padding(.horizontal, 9)
            .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
            .background(.white.opacity(0.12), in: Capsule())
    }

    @ViewBuilder
    private func ratingChip(_ chip: RatingChip) -> some View {
        if chip.isLoading {
            ExternalRatingLoadingChip()
        } else if let destination = chip.destination {
            Link(destination: destination) {
                ratingChipContent(chip)
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(chip.accessibilityLabel)
            .accessibilityHint("Opens the \(chip.providerName) media page")
        } else {
            ratingChipContent(chip)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(chip.accessibilityLabel)
        }
    }

    private func ratingChipContent(_ chip: RatingChip) -> some View {
        HStack(spacing: 6) {
            RatingSourceBadge(chip: chip)
            VStack(alignment: .leading, spacing: 1) {
                Text(chip.value)
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white)
                if let voteCount = chip.voteCount, voteCount > 0 {
                    Text("\(voteCount.formatted(.number.notation(.compactName))) \(chip.voteCountLabel ?? "votes")")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.58))
                        .lineLimit(1)
                }
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
        .background(.white.opacity(0.12), in: Capsule())
    }
}

private struct ExternalRatingLoadingChip: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(.white.opacity(isPulsing ? 0.20 : 0.10))
                .frame(
                    width: MediaDetailLayout.ratingBadgeSize,
                    height: MediaDetailLayout.ratingBadgeSize
                )
            VStack(alignment: .leading, spacing: 4) {
                Capsule()
                    .fill(.white.opacity(isPulsing ? 0.24 : 0.12))
                    .frame(width: 34, height: 8)
                Capsule()
                    .fill(.white.opacity(isPulsing ? 0.16 : 0.08))
                    .frame(width: 48, height: 6)
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
        .background(.white.opacity(isPulsing ? 0.09 : 0.05), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(isPulsing ? 0.2 : 0.09), lineWidth: 1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("External ratings loading")
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) {
                isPulsing = true
            }
        }
    }
}

private struct RatingSourceBadge: View {
    let chip: RatingChip

    var body: some View {
        if chip.providerName.caseInsensitiveCompare("Google Books") == .orderedSame {
            Text("Google Books")
                .font(.system(size: 10, weight: .heavy))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(1)
        } else {
            Group {
                if let assetName = chip.assetName {
                    ratingLogo(assetName: assetName)
                } else {
                    Text(chip.source)
                        .font(.system(size: 10, weight: .black))
                        .foregroundStyle(.black)
                }
            }
            .frame(width: MediaDetailLayout.ratingBadgeSize, height: MediaDetailLayout.ratingBadgeSize)
            .background(["RatingMAL", "RatingAniList", "RatingMetacritic", "RatingSteam"].contains(chip.assetName) ? .clear : .white, in: Circle())
            .clipShape(Circle())
        }
    }

    @ViewBuilder
    private func ratingLogo(assetName: String) -> some View {
        switch assetName {
        case "RatingIMDb":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.15)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingLetterboxd":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.1)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingMAL", "RatingAniList":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingMetacritic", "RatingSteam":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingRottenTomatoesCertifiedFresh":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.045)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingHardcover":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .offset(y: 3)
                .clipped()
        case "RatingIGDB":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .scaleEffect(1.55)
                .frame(width: 24, height: 24)
                .clipped()
        default:
            Image(assetName)
                .resizable()
                .scaledToFit()
                .padding(3)
        }
    }
}

private struct BookReadingHistorySection: View {
    @State private var pendingDeleteJourney: BookJourneyState?
    @State private var isUndatedDeletePresented = false

    let book: BookTrackingState
    let isSaving: Bool
    let onEdit: (BookJourneyState) -> Void
    let onDelete: (BookJourneyState) -> Void
    let onDeleteUndated: () -> Void
    let onOpenDiary: (Int) -> Void

    var body: some View {
        if !dnfJourneys.isEmpty || book.undatedRead != nil {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    SectionLabel(title: "Reading History")
                    Spacer()
                    if book.lifetimeReadCount > 0 {
                        Text("\(book.lifetimeReadCount) lifetime read\(book.lifetimeReadCount == 1 ? "" : "s")")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.52))
                    }
                }

                ForEach(dnfJourneys) { journey in
                    historyRow(journey)
                }

                if book.undatedRead != nil {
                    HStack(spacing: 12) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Read")
                                .font(.subheadline.weight(.bold))
                            Text("Date unknown")
                                .font(.caption.weight(.medium))
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(role: .destructive) {
                            isUndatedDeletePresented = true
                        } label: {
                            Image(systemName: "trash")
                        }
                        .disabled(isSaving)
                        .accessibilityLabel("Delete undated read")
                    }
                    .padding(12)
                    .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 14))
                }
            }
            .confirmationDialog("Delete this reading journey?", isPresented: Binding(
                get: { pendingDeleteJourney != nil },
                set: { if !$0 { pendingDeleteJourney = nil } }
            ), titleVisibility: .visible) {
                Button("Delete Journey", role: .destructive) {
                    if let journey = pendingDeleteJourney { onDelete(journey) }
                    pendingDeleteJourney = nil
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The Did Not Finish journey and its progress will be removed.")
            }
            .confirmationDialog("Delete undated Read?", isPresented: $isUndatedDeletePresented, titleVisibility: .visible) {
                Button("Delete Undated Read", role: .destructive, action: onDeleteUndated)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This removes only the date-unknown Read state.")
            }
        }
    }

    private var dnfJourneys: [BookJourneyState] {
        book.readingHistory.filter { $0.status == "Dropped" }
    }

    private func historyRow(_ journey: BookJourneyState) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: journey.status == "Completed" ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(journey.status == "Completed" ? .green : .orange)

            VStack(alignment: .leading, spacing: 4) {
                Text(journey.status == "Completed" ? "Read" : "Did Not Finish")
                    .font(.subheadline.weight(.bold))
                Text(journeyDateLine(journey))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                if let progressText = journey.progress?.detailDisplayText(preferredMode: nil) {
                    Text(progressText)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.58))
                }
            }

            Spacer()

            Menu {
                Button {
                    onEdit(journey)
                } label: {
                    Label("Edit Dates", systemImage: "calendar")
                }
                if let entryId = journey.completionDiaryEntryId {
                    Button {
                        onOpenDiary(entryId)
                    } label: {
                        Label("Open Log", systemImage: "square.and.pencil")
                    }
                } else {
                    Button(role: .destructive) {
                        pendingDeleteJourney = journey
                    } label: {
                        Label("Delete Journey", systemImage: "trash")
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.title3)
            }
            .disabled(isSaving)
            .accessibilityLabel("Reading history actions")
        }
        .padding(12)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 14))
    }

    private func journeyDateLine(_ journey: BookJourneyState) -> String {
        switch (journey.startDate, journey.endDate) {
        case let (start?, end?): "\(start.shortDateLabel) – \(end.shortDateLabel)"
        case let (start?, nil): "Started \(start.shortDateLabel)"
        case let (nil, end?): "Ended \(end.shortDateLabel)"
        case (nil, nil): "Dates unknown"
        }
    }
}

private struct BookJourneyDateEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State private var includesStartDate: Bool
    @State private var includesEndDate: Bool
    @State private var startDate: Date
    @State private var endDate: Date

    let journey: BookJourneyState
    let isSaving: Bool
    let errorMessage: String?
    let onSave: (Date?, Date?) async -> Bool

    init(
        journey: BookJourneyState,
        isSaving: Bool,
        errorMessage: String?,
        onSave: @escaping (Date?, Date?) async -> Bool
    ) {
        self.journey = journey
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSave = onSave
        let parsedStart = CalendarDateCodec.date(from: journey.startDate)
        let parsedEnd = CalendarDateCodec.date(from: journey.endDate)
        _includesStartDate = State(initialValue: parsedStart != nil)
        _includesEndDate = State(initialValue: parsedEnd != nil)
        _startDate = State(initialValue: parsedStart ?? parsedEnd ?? Date())
        _endDate = State(initialValue: parsedEnd ?? Date())
    }

    var body: some View {
        NavigationStack {
            Form {
                Toggle("Started on", isOn: $includesStartDate)
                if includesStartDate {
                    DatePicker("Start date", selection: $startDate, in: ...Date(), displayedComponents: .date)
                }
                Toggle("Ended on", isOn: $includesEndDate)
                if includesEndDate {
                    DatePicker("End date", selection: $endDate, in: ...Date(), displayedComponents: .date)
                }
                if let errorMessage {
                    Text(errorMessage)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.red)
                }
            }
            .navigationTitle("Edit Reading Dates")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            _ = await onSave(
                                includesStartDate ? startDate : nil,
                                includesEndDate ? endDate : nil
                            )
                        }
                    }
                    .disabled(isSaving)
                }
            }
        }
    }
}

struct TrackingSummarySection: View {
    let detail: MediaDetail
    let state: UserMediaState?
    let onOpenDiaryEntry: () -> Void
    let onUpdateProgress: () -> Void
    /// The show page's Up next tick (TV-100, TV-105).
    var onTickUpNext: ((TVEpisodePointer) -> Void)?
    /// Opens the Up next episode's page.
    var onOpenUpNext: ((TVEpisodePointer) -> Void)?

    var body: some View {
        if hasState {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: detail.ref.mediaType == "game" ? "Your Progress" : "Your Tracking")
                HStack(alignment: .top, spacing: 10) {
                    if detail.ref.mediaType != "episode" {
                        MediaArtwork(
                            url: detail.displayPosterURL,
                            title: detail.title,
                            slot: .libraryRow,
                            mediaType: detail.ref.mediaType,
                            orientation: detail.posterOrientation
                        )
                        .onTapGesture { if hasLogs { onOpenDiaryEntry() } }
                        .allowsHitTesting(hasLogs)
                        .accessibilityLabel(hasLogs ? "View diary log for \(detail.title)" : "\(detail.title) cover")
                        .accessibilityAddTraits(hasLogs ? .isButton : [])
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        if let status {
                            Text(detail.ref.trackingStatusLabel(status))
                                .font(.system(size: 14, weight: .heavy))
                                .foregroundStyle(.white)
                                .onTapGesture {
                                    if hasMultipleLogs {
                                        onOpenDiaryEntry()
                                    }
                                }
                                .accessibilityAddTraits(hasMultipleLogs ? .isButton : [])
                            if showsUpdateProgressButton {
                                Button(action: onUpdateProgress) {
                                    Text("Update Progress")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(.white.opacity(0.82))
                                        .padding(.horizontal, 11)
                                        .frame(height: 24)
                                        .background(.white.opacity(0.12), in: Capsule())
                                }
                                .buttonStyle(.plain)
                                .padding(.top, 3)
                            }
                        }
                        if showsRatingLine {
                            ratingLine
                        }
                        ForEach(lines, id: \.self) { line in
                            Text(line)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(.white.opacity(0.76))
                                .onTapGesture {
                                    if line == logLine {
                                        onOpenDiaryEntry()
                                    }
                                }
                                .accessibilityAddTraits(line == logLine ? .isButton : [])
                        }
                    }

                    Spacer()
                }

                if let upNext = Self.tvUpNext(detail: detail, state: state), let onTickUpNext {
                    // The season page's episode row, so the two look and behave alike.
                    let row = TVEpisodeRowModel.upNext(upNext)
                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel(title: TVTrackingPresentation.upNextLabel(for: upNext))
                        EpisodeCard(
                            row: row,
                            trailing: TVEpisodeTrailing.resolve(
                                row.tv,
                                isWatched: state?.tvPending.episodes[upNext.coordinate] == true
                            ),
                            onTick: { onTickUpNext(upNext) },
                            onMarkUpTo: nil,
                            onSelect: onOpenUpNext.map { open in { open(upNext) } }
                        )
                    }
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("tv.up-next")
                }
            }
        }
    }

    private var status: String? { Self.status(detail: detail, state: state) }
    private var hasState: Bool {
        status != nil || stars != nil || !lines.isEmpty || Self.tvUpNext(detail: detail, state: state) != nil
    }

    /// The show page's next episode to watch, once the show is in the library (TV-105).
    static func tvUpNext(detail: MediaDetail, state: UserMediaState?) -> TVEpisodePointer? {
        guard detail.ref.mediaType == "tv", let tv = state?.tv, tv.inLibrary else { return nil }
        return tv.upNext
    }
    private var stars: String? { Self.stars(detail: detail, state: state) }
    private var isLiked: Bool { state?.hasLiked == true }
    private var showsRatingLine: Bool { stars != nil || isLiked }

    /// "3.5★ ♥" in the rail's star yellow and heart pink.
    private var ratingLine: some View {
        HStack(spacing: 6) {
            if let stars {
                HStack(spacing: 2) {
                    Text(stars)
                    Image(systemName: "star.fill")
                }
                .foregroundStyle(.yellow)
            }
            if isLiked {
                Image(systemName: "heart.fill")
                    .foregroundStyle(.pink)
            }
        }
        .font(.system(size: 13, weight: .bold))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [stars.map { "Rated \($0) out of 5 stars" }, isLiked ? "Liked" : nil]
                .compactMap { $0 }
                .joined(separator: ", ")
        )
    }

    private var showsUpdateProgressButton: Bool {
        if detail.ref.mediaType == "game" { return state?.game?.canUpdateProgress == true }
        return (status == "In progress" || (detail.ref.mediaType == "book" && status == "Paused"))
            && ["book", "game"].contains(detail.ref.mediaType)
    }
    private var hasLogs: Bool { (state?.diaryCount ?? 0) > 0 || state?.diaryEntryId != nil }
    private var hasMultipleLogs: Bool { Self.hasMultipleLogs(state) }
    private var logLine: String? { Self.logLine(state) }

    private var lines: [String] {
        Self.lines(detail: detail, state: state, preferredMode: ProgressDisplayPreferences.mode(for: detail.ref))
    }

    static func lines(detail: MediaDetail, state: UserMediaState?, preferredMode: ProgressUpdateMode?) -> [String] {
        let status = Self.status(detail: detail, state: state)
        var values: [String] = []
        if detail.ref.mediaType == "game", let game = state?.game,
           let text = game.currentPlaythrough?.progress.summary, !text.isEmpty {
            values.append(text)
        }
        if detail.ref.isTVFamily, let tv = state?.tv {
            values += tvLines(detail: detail, tv: tv)
        } else if detail.ref.mediaType != "game", (status == "In progress" || (detail.ref.mediaType == "book" && status == "Paused")),
           detail.ref.mediaType != "movie",
            let progressText = state?.progress?.detailDisplayText(preferredMode: preferredMode) {
            values.append(progressText)
        }
        if let logLine = Self.logLine(state) {
            values.append(logLine)
        }
        if !Self.hasMultipleLogs(state), let consumedAt = state?.diaryConsumedAt {
            values.append("Logged on \(consumedAt.longDateLabel)")
        }
        if detail.ref.mediaType != "movie" {
            if let startDate = state?.displayStartDate {
                values.append("Started \(startDate.longDateLabel)")
            }
        }
        return values
    }

    private static func status(detail: MediaDetail, state: UserMediaState?) -> String? {
        if detail.ref.isTVFamily, let tv = state?.tv {
            return tvStatus(detail: detail, tv: tv, state: state)
        }
        return state?.status ?? (detail.ref.isEpisode && state?.isTracked == true ? "Watched" : nil)
    }

    /// The bold line of a TV page: the show's status, with Watching for In progress (TV-000); a season's "Watched",
    /// "Airing" or "6 of 10" (TV-200); "Watched" for an episode watched in the current pass.
    static func tvStatus(detail: MediaDetail, tv: TVShowState, state: UserMediaState?) -> String? {
        switch detail.ref.mediaType {
        case "season":
            guard let season = tv.season else { return nil }
            if state?.tvIsMarked(season) == true, season.state != .watched, season.state != .airing {
                return season.nextAirDate == nil ? "Watched" : "Airing"
            }
            return TVTrackingPresentation.seasonLabel(for: season)
        case "episode":
            guard let episode = tv.episode else { return state?.isTracked == true ? "Watched" : nil }
            return state?.tvIsWatched(episode) ?? episode.watched ? "Watched" : nil
        default:
            guard tv.inLibrary, let status = tv.status else { return nil }
            return status == "In progress" ? "Watching" : status
        }
    }

    /// The show page's lines: the rewatch, caught-up or premiere line (TV-600, TV-202, TV-700) and progress as a
    /// count of aired regular episodes (TV-104).
    static func tvLines(detail: MediaDetail, tv: TVShowState) -> [String] {
        guard detail.ref.mediaType == "tv", tv.inLibrary else { return [] }
        var lines: [String] = []
        if let rewatch = TVTrackingPresentation.rewatchLine(for: tv) {
            lines.append(rewatch)
        } else if tv.caughtUp {
            lines.append(TVTrackingPresentation.caughtUpLine(for: tv))
        } else if !tv.started, tv.status == "Planning", let card = TVTrackingPresentation.cardLine(for: tv) {
            lines.append(card)
        }
        if tv.started, !tv.finished || tv.rewatch != nil,
           let progress = TVTrackingPresentation.progressText(watched: tv.watchedCount, aired: tv.airedCount) {
            lines.append("\(progress) watched")
        }
        return lines
    }

    static func stars(detail: MediaDetail, state: UserMediaState?) -> String? {
        state?.rating?.starCount(mediaType: detail.ref.mediaType)
    }

    private static func hasMultipleLogs(_ state: UserMediaState?) -> Bool {
        (state?.diaryCount ?? 0) > 1
    }

    private static func logLine(_ state: UserMediaState?) -> String? {
        guard hasMultipleLogs(state), let diaryCount = state?.diaryCount else { return nil }
        return "\(diaryCount) logs"
    }
}

struct SynopsisText: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let text: String
    @State private var isExpanded = false

    var body: some View {
        Button {
            withAnimation(reduceMotion ? nil : .smooth(duration: 0.3)) {
                isExpanded.toggle()
            }
        } label: {
            synopsisCopy
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isExpanded ? "Collapse synopsis" : "Expand synopsis")
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(reduceMotion ? nil : .smooth(duration: 0.3), value: isExpanded)
    }

    private var synopsisCopy: some View {
        Text(text)
            .font(synopsisFont)
            .foregroundStyle(.white.opacity(0.88))
            .lineSpacing(3)
            .lineLimit(isExpanded ? nil : 3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
    }

    private var synopsisFont: Font {
        .system(size: 15, weight: .medium)
    }
}

struct DetailFactRow: Identifiable {
    let label: String
    var value: String?
    var people: [MediaPersonCredit]
    var companies: [MediaCompanyCredit]
    var destination: URL?

    var id: String { label }
    var isEmpty: Bool { label.isEmpty || (value?.isEmpty != false && people.isEmpty && companies.isEmpty) }
    var symbolName: String {
        Self.symbolNames[label] ?? (!people.isEmpty ? "person" : !companies.isEmpty ? "building.2" : "info.circle")
    }

    private static let symbolNames: [String: String] = [
        "Status": "flag", "Format": "tag", "Physical Format": "book.closed", "Release Type": "opticaldisc",
        "Release Date": "calendar", "First Aired": "calendar", "Aired": "calendar", "Publish Date": "calendar",
        "Year": "calendar", "Last Aired": "calendar.badge.clock", "Broadcast": "antenna.radiowaves.left.and.right",
        "Runtime": "clock", "Time to Beat": "hourglass",
        "Certification": "checkmark.shield", "Age Rating": "checkmark.shield", "Maturity Rating": "checkmark.shield",
        "Box Office": "dollarsign.circle", "Google Books Price": "creditcard",
        "Seasons": "square.stack", "Season": "square.stack",
        "Episodes": "play.rectangle.on.rectangle", "Episode": "play.rectangle", "Production Code": "number",
        "Chapters": "book.pages", "Pages": "book.pages", "Latest Chapter": "bookmark", "Last Issue": "bookmark",
        "Issues": "books.vertical", "Source": "book", "ISBN": "barcode", "Track Count": "music.note.list",
        "Series": "rectangle.stack", "Collection": "rectangle.stack", "Franchise": "rectangle.stack",
        "Genres": "theatermasks", "Themes": "sparkles",
        "Director": "megaphone", "Directors": "megaphone", "Creator": "lightbulb", "Creators": "lightbulb",
        "Author": "person", "Authors": "person.2", "Artist": "music.mic", "Artists": "music.mic",
        "Studios": "building.2", "Companies": "building.2", "Developer": "hammer",
        "Publisher": "building.columns", "Publishers": "building.columns",
        "Country": "globe", "Languages": "character.bubble", "Platforms": "gamecontroller",
    ]

    init(label: String, value: String?, destination: URL? = nil) {
        self.label = label
        self.value = value
        people = []
        companies = []
        self.destination = destination
    }

    init(label: String, people: [MediaPersonCredit]) {
        self.label = label
        value = nil
        self.people = people
        companies = []
        destination = nil
    }

    init(label: String, companies: [MediaCompanyCredit]) {
        self.label = label
        value = nil
        people = []
        self.companies = companies
        destination = nil
    }
}

private struct MediaFactsSection: View {
    let rows: [DetailFactRow]
    let onPersonSelected: (PersonRef) -> Void
    let onCompanySelected: (CompanyRef) -> Void

    var body: some View {
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Details")

                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        DetailFactRowView(
                            row: row,
                            showsDivider: index > 0,
                            onPersonSelected: onPersonSelected,
                            onCompanySelected: onCompanySelected
                        )
                    }
                }
                .mediaDetailGroupSurface()
            }
        }
    }
}

struct DetailFactRowView: View {
    private static let iconWidth: CGFloat = 20
    private static let iconSpacing: CGFloat = 10

    let row: DetailFactRow
    let showsDivider: Bool
    let onPersonSelected: (PersonRef) -> Void
    let onCompanySelected: (CompanyRef) -> Void

    var body: some View {
        content
            .overlay(alignment: .top) {
                if showsDivider {
                    MediaDetailRowDivider(
                        leadingInset: MediaDetailGroup.horizontalPadding + Self.iconWidth + Self.iconSpacing
                    )
                }
            }
    }

    /// A row naming one person or company opens it like a settings row; rows with several make each name tappable.
    @ViewBuilder
    private var content: some View {
        if let destination = row.destination {
            Link(destination: destination) {
                layout(trailingSymbol: "arrow.up.right")
            }
            .buttonStyle(MediaDetailRowButtonStyle())
            .accessibilityLabel("\(row.label), \(row.value ?? "")")
            .accessibilityHint("Opens in the system browser")
        } else if row.people.count == 1, let person = row.people.first, let personRef = person.personRef {
            Button {
                onPersonSelected(personRef)
            } label: {
                layout(trailingSymbol: "chevron.right")
            }
            .buttonStyle(MediaDetailRowButtonStyle())
            .accessibilityLabel("\(row.label), \(person.name)")
            .accessibilityHint("Opens person details")
        } else if row.companies.count == 1, let company = row.companies.first {
            Button {
                onCompanySelected(company.ref)
            } label: {
                layout(trailingSymbol: "chevron.right")
            }
            .buttonStyle(MediaDetailRowButtonStyle())
            .accessibilityLabel("\(row.label), \(company.name)")
            .accessibilityHint("Opens company details")
        } else {
            layout(trailingSymbol: nil)
                .accessibilityElement(children: row.people.count > 1 || row.companies.count > 1 ? .contain : .combine)
        }
    }

    /// Sits the value beside its label, wrapping a long one onto more lines against the trailing edge.
    private func layout(trailingSymbol: String?) -> some View {
        HStack(spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 16) {
                label
                    .fixedSize()
                values
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }

            if let trailingSymbol {
                Image(systemName: trailingSymbol)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white.opacity(0.26))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, MediaDetailGroup.horizontalPadding)
        .padding(.vertical, 13)
        .frame(maxWidth: .infinity, minHeight: MediaDetailGroup.rowMinHeight, alignment: .leading)
        .contentShape(Rectangle())
    }

    private var label: some View {
        HStack(spacing: Self.iconSpacing) {
            Image(systemName: row.symbolName)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(.white.opacity(0.42))
                .frame(width: Self.iconWidth)
                .accessibilityHidden(true)

            Text(row.label)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.white.opacity(0.55))
                .lineLimit(1)
        }
    }

    @ViewBuilder
    private var values: some View {
        if !row.people.isEmpty {
            VStack(alignment: .trailing, spacing: 6) {
                ForEach(row.people, id: \.self) { person in
                    if row.people.count > 1, let personRef = person.personRef {
                        Button {
                            onPersonSelected(personRef)
                        } label: {
                            linkedValue(person.name)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(person.name)")
                    } else {
                        valueText(person.name)
                    }
                }
            }
        } else if !row.companies.isEmpty {
            VStack(alignment: .trailing, spacing: 6) {
                ForEach(row.companies) { company in
                    if row.companies.count > 1 {
                        Button {
                            onCompanySelected(company.ref)
                        } label: {
                            linkedValue(company.name)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(company.name)")
                    } else {
                        valueText(company.name)
                    }
                }
            }
        } else {
            valueText(row.value ?? "")
        }
    }

    private func linkedValue(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            valueText(text)
            Image(systemName: "chevron.right")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.white.opacity(0.3))
                .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
    }

    private func valueText(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(.white.opacity(0.92))
            .multilineTextAlignment(.trailing)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// Inset-grouped Liquid Glass card for the credits and details lists, matching the profile menu.
private enum MediaDetailGroup {
    static let cornerRadius: CGFloat = 22
    static let horizontalPadding: CGFloat = 16
    static let rowMinHeight: CGFloat = 48
}

private extension View {
    func mediaDetailGroupSurface() -> some View {
        let shape = RoundedRectangle(cornerRadius: MediaDetailGroup.cornerRadius, style: .continuous)

        return self
            .clipShape(shape)
            .glassEffect(.regular, in: shape)
            // The page is always dark; light-mode glass would wash out the white text.
            .environment(\.colorScheme, .dark)
    }
}

private struct MediaDetailRowDivider: View {
    let leadingInset: CGFloat

    var body: some View {
        Rectangle()
            .fill(.white.opacity(0.12))
            .frame(height: 0.5)
            .padding(.leading, leadingInset)
    }
}

/// Fills the whole row while pressed; the group clips it to its rounded corners.
private struct MediaDetailRowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        MediaDetailRowPressHighlight(configuration: configuration)
    }
}

private struct MediaDetailRowPressHighlight: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let configuration: ButtonStyleConfiguration

    var body: some View {
        configuration.label
            .background {
                Color.white
                    .opacity(configuration.isPressed ? 0.12 : 0)
            }
            .animation(
                configuration.isPressed ? nil : SpineMotion.animation(reduceMotion: reduceMotion),
                value: configuration.isPressed
            )
    }
}

private struct MusicAlbumTracklistSection: View {
    let release: MusicRepresentativeRelease?
    let albumCredits: [MusicArtistCredit]
    let enrichmentState: MusicEnrichmentState
    let onSelectTrack: (MusicTrack) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(title: "Tracklist")

            if enrichmentState == .loading {
                LazyVStack(spacing: 0) {
                    ForEach(1...4, id: \.self) { position in
                        MusicAlbumTrackRowLayout(number: "\(position)") {
                            Text("Loading track title")
                                .font(.body)
                        } accessory: {
                            EmptyView()
                        }
                        if position < 4 {
                            MusicAlbumTrackSeparator()
                        }
                    }
                }
                .redacted(reason: .placeholder)
                .foregroundStyle(.white.opacity(0.7))
                .padding(.horizontal, -MusicTracklistMetrics.pageInset)
                .accessibilityHidden(true)
                .accessibilityIdentifier("music.tracklist.loading")
            } else if let release, hasTracks(release) {
                LazyVStack(spacing: 0) {
                    ForEach(release.media, id: \.position) { medium in
                        if release.discCount > 1 || release.media.count > 1 {
                            discHeader(medium)
                        }
                        ForEach(medium.tracks) { track in
                            MusicAlbumTrackRow(
                                track: track,
                                albumCredits: albumCredits,
                                onSelect: { onSelectTrack(track) }
                            )
                            if track.id != medium.tracks.last?.id {
                                MusicAlbumTrackSeparator()
                            }
                        }
                    }
                }
                // Rows run edge to edge, so a pressed row highlights the full width, as in Apple Music.
                .padding(.horizontal, -MusicTracklistMetrics.pageInset)
            } else {
                ContentUnavailableView(
                    "Tracklist unavailable",
                    systemImage: "music.note.list",
                    description: Text("Spine could not resolve a complete album edition.")
                )
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
                .mediaDetailSurface(cornerRadius: 14)
                .accessibilityIdentifier("music.tracklist.unavailable")
            }
        }
    }

    private func hasTracks(_ release: MusicRepresentativeRelease) -> Bool {
        release.media.contains { !$0.tracks.isEmpty }
    }

    private func discHeader(_ medium: MusicMedium) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Disc \(medium.position)")
                .font(.caption.weight(.semibold))
            if let title = medium.title?.nilIfEmpty {
                Text(title)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .foregroundStyle(.white.opacity(0.62))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, MusicTracklistMetrics.margin)
        .padding(.top, 14)
        .padding(.bottom, 6)
    }
}

/// Apple Music's album tracklist, measured from its iOS 26 app: 20 pt margins, the track number centred
/// 31 pt from the screen edge, titles 51 pt in, the trailing control 27 pt in, and 52 pt rows.
private enum MusicTracklistMetrics {
    /// The page's own horizontal padding, which the tracklist cancels to run edge to edge.
    static let pageInset: CGFloat = 14
    static let margin: CGFloat = 20
    static let numberWidth: CGFloat = 22
    static let numberSpacing: CGFloat = 9
    static let rowHeight: CGFloat = 51
}

private struct MusicAlbumTrackRowLayout<Label: View, Accessory: View>: View {
    @ScaledMetric(relativeTo: .body) private var numberWidth = MusicTracklistMetrics.numberWidth
    @ScaledMetric(relativeTo: .body) private var minHeight = MusicTracklistMetrics.rowHeight

    let number: String
    @ViewBuilder let label: Label
    @ViewBuilder let accessory: Accessory

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            Text(number)
                .font(.body)
                .foregroundStyle(.white.opacity(0.5))
                // Centred like Apple's; three-digit numbers spill evenly to both sides.
                .fixedSize()
                .frame(width: numberWidth)
                .padding(.trailing, MusicTracklistMetrics.numberSpacing)

            label
                .frame(maxWidth: .infinity, alignment: .leading)

            accessory
                .padding(.leading, 16)
                .padding(.trailing, 7)
        }
        .padding(.vertical, 11)
        .frame(minHeight: minHeight)
        .padding(.horizontal, MusicTracklistMetrics.margin)
    }
}

/// Starts under the titles and stops at the right margin, like Apple Music's.
private struct MusicAlbumTrackSeparator: View {
    @ScaledMetric(relativeTo: .body) private var numberWidth = MusicTracklistMetrics.numberWidth

    var body: some View {
        Rectangle()
            .fill(.white.opacity(0.13))
            .frame(height: 1)
            .padding(.leading, MusicTracklistMetrics.margin + numberWidth + MusicTracklistMetrics.numberSpacing)
            .padding(.trailing, MusicTracklistMetrics.margin)
    }
}

private struct MusicAlbumTrackRow: View {
    let track: MusicTrack
    let albumCredits: [MusicArtistCredit]
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            MusicAlbumTrackRowLayout(number: track.number) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(track.title)
                        .font(.body)
                        .foregroundStyle(.white)
                        .fixedSize(horizontal: false, vertical: true)
                    if let artist = MusicAlbumPresentation.differingArtistCredit(
                        track: track,
                        albumCredits: albumCredits
                    ) {
                        Text(artist)
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.5))
                    }
                }
            } accessory: {
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.45))
                    // Apple's "•••" is 15 pt wide; the chevron sits centred where it would.
                    .frame(width: 15)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(MediaDetailRowButtonStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(MusicAlbumPresentation.trackAccessibilityLabel(track: track, albumCredits: albumCredits))
        .accessibilityHint("Opens song details")
        .accessibilityIdentifier("music.track.\(track.recording.recordingMbid)")
    }
}

private struct MusicStreamingButtons: View {
    let links: [MusicStreamingLink]

    private var destinations: [MusicStreamingDestination] {
        var seen = Set<String>()
        return MusicAlbumPresentation.streamingDestinations(links).filter {
            ($0.label == "Apple Music" || $0.label == "Spotify")
                && seen.insert($0.label).inserted
        }
    }

    var body: some View {
        if !destinations.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    ForEach(destinations) { destination in
                        MusicStreamingButton(destination: destination)
                    }
                }
                .fixedSize(horizontal: true, vertical: false)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(destinations) { destination in
                            MusicStreamingButton(destination: destination)
                        }
                    }
                }
            }
        }
    }
}

private struct MusicStreamingButton: View {
    let destination: MusicStreamingDestination

    var body: some View {
        Link(destination: destination.url) {
            HStack(spacing: 8) {
                serviceLogo

                Image(systemName: "play.fill")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(accentColor)
            }
            .padding(.horizontal, 12)
            .frame(height: 40)
            .background(.white.opacity(0.07), in: Capsule())
            .glassEffect(
                .regular.tint(accentColor.opacity(0.08)).interactive(),
                in: .rect(cornerRadius: 20)
            )
            .overlay {
                Capsule().stroke(.white.opacity(0.14), lineWidth: 0.75)
            }
            .shadow(color: accentColor.opacity(0.15), radius: 7, y: 2)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Listen on \(destination.label)")
        .accessibilityHint("Opens using the system URL behavior")
    }

    private var accentColor: Color {
        destination.label == "Spotify" ? Color(red: 0.12, green: 0.84, blue: 0.38) : .pink
    }

    @ViewBuilder
    private var serviceLogo: some View {
        switch destination.label {
        case "Apple Music":
            Image("AppleMusicIcon")
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        case "Spotify":
            Image("SpotifyIcon")
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .clipShape(Circle())
        default:
            Image(systemName: "music.note")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.9))
                .frame(width: 24, height: 24)
        }
    }
}

private extension View {
    func mediaDetailSurface(cornerRadius: CGFloat) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)

        return self
            .background(Color.white.opacity(0.028), in: shape)
            .overlay { shape.stroke(.white.opacity(0.045), lineWidth: 1) }
    }
}

struct AniListRatingSummary: Equatable {
    struct Bucket: Equatable, Identifiable {
        let score: Int
        let count: Int

        var id: Int { score }
    }

    let averageScore: Int
    let ratingCount: Int
    let buckets: [Bucket]
    let hasReviews: Bool
    let url: URL?

    func bucket(at x: CGFloat, width: CGFloat) -> Bucket? {
        guard x.isFinite, width.isFinite, width > 0, !buckets.isEmpty else { return nil }
        let index = min(max(Int(x / width * CGFloat(buckets.count)), 0), buckets.count - 1)
        return buckets[index]
    }

    init?(detail: MediaDetail) {
        guard
            detail.ref.mediaType == "anime",
            let object = detail.details?["anilist_rating"]?.objectValue,
            let averageScore = object["average_score"]?.intValue,
            (0...100).contains(averageScore),
            let declaredRatingCount = object["rating_count"]?.intValue,
            declaredRatingCount >= 0,
            let values = object["score_distribution"]?.arrayValue
        else { return nil }

        var counts: [Int: Int] = [:]
        for value in values {
            guard
                let bucket = value.objectValue,
                let score = bucket["score"]?.intValue,
                stride(from: 10, through: 100, by: 10).contains(score),
                let count = bucket["count"]?.intValue,
                count >= 0
            else { continue }
            counts[score, default: 0] += count
        }

        let buckets = stride(from: 10, through: 100, by: 10).map {
            Bucket(score: $0, count: counts[$0, default: 0])
        }
        self.averageScore = averageScore
        ratingCount = buckets.reduce(0) { $0 + $1.count }
        self.buckets = buckets
        if case let .bool(value)? = object["has_reviews"] {
            hasReviews = value
        } else {
            hasReviews = false
        }
        url = aniListDestination(object["url"]?.displayString)
    }
}

@MainActor
@Observable
final class MediaReviewsViewModel {
    var reviews: [MediaReview] = []
    var reviewCount = 0
    var nextPage: String?
    var isLoadingInitial = false
    var isLoadingMore = false
    var initialError: String?
    var paginationError: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func loadInitial() async {
        guard reviews.isEmpty, !isLoadingInitial else { return }
        isLoadingInitial = true
        initialError = nil
        defer { isLoadingInitial = false }

        do {
            apply(try await mediaRepository.reviewPage(ref: ref, page: nil))
        } catch is CancellationError {
            return
        } catch {
            initialError = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func retryInitial() async {
        reviews = []
        reviewCount = 0
        nextPage = nil
        await loadInitial()
    }

    func loadNextPage() async {
        guard let page = nextPage, !isLoadingInitial, !isLoadingMore else { return }
        isLoadingMore = true
        paginationError = nil
        defer { isLoadingMore = false }

        do {
            apply(try await mediaRepository.reviewPage(ref: ref, page: page))
        } catch is CancellationError {
            return
        } catch {
            paginationError = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func shouldLoadNext(after reviewID: Int) -> Bool {
        guard
            nextPage != nil,
            let index = reviews.firstIndex(where: { $0.id == reviewID })
        else { return false }
        return index >= max(reviews.count - 5, 0)
    }

    private func apply(_ page: PagedResponse<MediaReview>) {
        var seen = Set(reviews.map(\.id))
        reviews.append(contentsOf: page.results.filter { seen.insert($0.id).inserted })
        reviewCount = page.count
        nextPage = APIPageCursor.nextPage(from: page.next)
        initialError = nil
        paginationError = nil
    }
}

@MainActor
@Observable
final class AniListReviewsViewModel {
    var reviews: [AniListReview] = []
    var nextPage: Int?
    var isLoadingInitial = false
    var isLoadingMore = false
    var initialError: String?
    var paginationError: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private var loadedPages: Set<Int> = []

    init(ref: MediaRef, mediaRepository: MediaRepository) {
        self.ref = ref
        self.mediaRepository = mediaRepository
    }

    func loadInitial() async {
        guard reviews.isEmpty, !isLoadingInitial else { return }
        isLoadingInitial = true
        initialError = nil
        defer { isLoadingInitial = false }
        do {
            apply(try await mediaRepository.anilistReviews(ref: ref, page: 1))
        } catch is CancellationError {
            return
        } catch {
            initialError = error.localizedDescription
        }
    }

    func retryInitial() async {
        reviews = []
        nextPage = nil
        loadedPages = []
        await loadInitial()
    }

    func loadNextPage() async {
        guard
            let page = nextPage,
            !loadedPages.contains(page),
            !isLoadingInitial,
            !isLoadingMore
        else { return }
        isLoadingMore = true
        paginationError = nil
        defer { isLoadingMore = false }
        do {
            apply(try await mediaRepository.anilistReviews(ref: ref, page: page))
        } catch is CancellationError {
            return
        } catch {
            paginationError = error.localizedDescription
        }
    }

    func shouldLoadNext(after reviewID: String) -> Bool {
        guard
            nextPage != nil,
            let index = reviews.firstIndex(where: { $0.id == reviewID })
        else { return false }
        return index >= max(reviews.count - 5, 0)
    }

    private func apply(_ page: AniListReviewPage) {
        loadedPages.insert(page.currentPage)
        var seen = Set(reviews.map(\.id))
        reviews.append(
            contentsOf: page.results.filter { seen.insert($0.id).inserted }
        )
        nextPage = page.nextPage
        initialError = nil
        paginationError = nil
    }
}

private struct AniListRatingCard: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var selectedBucketScore: Int?
    @State private var haptics = UISelectionFeedbackGenerator()

    let summary: AniListRatingSummary
    let onSeeReviews: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                HStack(spacing: 8) {
                    Image("RatingAniList")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 38, height: 38)
                        .accessibilityLabel("AniList")
                    metric(title: "AVERAGE SCORE", value: "\(summary.averageScore)%")
                }
                Spacer()
                metric(
                    title: selectedBucket.map { "\($0.score) SCORE VOTES" } ?? "NUMBER OF RATINGS",
                    value: (selectedBucket?.count ?? summary.ratingCount).formatted()
                )
            }

            GeometryReader { proxy in
                HStack(alignment: .bottom, spacing: 4) {
                    ForEach(summary.buckets) { bucket in
                        VStack(spacing: 4) {
                            RoundedRectangle(cornerRadius: 2, style: .continuous)
                                .fill(
                                    .white.opacity(
                                        selectedBucketScore == nil || selectedBucketScore == bucket.score
                                            ? 0.86
                                            : 0.28
                                    )
                                )
                                .frame(
                                    height: max(
                                        3,
                                        CGFloat(bucket.count) / CGFloat(maxCount) * 42
                                    )
                                )
                            Text("\(bucket.score)")
                                .font(.system(size: 8, weight: .heavy))
                                .foregroundStyle(.white.opacity(0.52))
                        }
                        .frame(maxWidth: .infinity)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(accessibilityLabel(for: bucket))
                    }
                }
                .contentShape(Rectangle())
                .gesture(scrubGesture(width: proxy.size.width))
            }
            .frame(height: 58, alignment: .bottom)
            .onAppear { haptics.prepare() }

            HStack(alignment: .center) {
                if summary.hasReviews {
                    Button(action: onSeeReviews) {
                        HStack(spacing: 5) {
                            Text("SEE ALL REVIEWS")
                            Image(systemName: "arrow.right")
                        }
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens AniList written reviews")
                } else {
                    Text("No written reviews.")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.52))
                }

                Spacer()

                Text("AniList")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.56))
            }
        }
        .padding(14)
        .frame(
            minHeight: dynamicTypeSize.isAccessibilitySize ? 216 : 176,
            alignment: .top
        )
        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 16))
    }

    private func metric(title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 9, weight: .heavy))
                .foregroundStyle(.white.opacity(0.5))
            Text(value)
                .font(.system(size: 22, weight: .heavy))
                .foregroundStyle(.white)
        }
    }

    private var maxCount: Int {
        max(summary.buckets.map(\.count).max() ?? 1, 1)
    }

    private var selectedBucket: AniListRatingSummary.Bucket? {
        summary.buckets.first { $0.score == selectedBucketScore }
    }

    private func scrubGesture(width: CGFloat) -> some Gesture {
        LongPressGesture(minimumDuration: 0.2)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { value in
                guard case let .second(true, drag) = value, let drag else { return }
                selectBucket(at: drag.location.x, width: width)
            }
            .onEnded { _ in
                selectedBucketScore = nil
            }
    }

    private func selectBucket(at x: CGFloat, width: CGFloat) {
        guard
            let bucket = summary.bucket(at: x, width: width),
            bucket.score != selectedBucketScore
        else { return }
        selectedBucketScore = bucket.score
        haptics.selectionChanged()
        haptics.prepare()
    }

    private func accessibilityLabel(for bucket: AniListRatingSummary.Bucket) -> String {
        let percentage = summary.ratingCount > 0
            ? Double(bucket.count) / Double(summary.ratingCount) * 100
            : 0
        return "\(bucket.score) score, \(bucket.count.formatted()) ratings, \(percentage.formatted(.number.precision(.fractionLength(0)))) percent"
    }
}

private struct MediaReviewsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaReviewsViewModel

    private let detail: MediaDetail

    init(
        detail: MediaDetail,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.detail = detail
        _viewModel = State(
            initialValue: MediaReviewsViewModel(
                ref: detail.ref,
                mediaRepository: mediaRepository,
                onUnauthorized: onUnauthorized
            )
        )
    }

    var body: some View {
        ZStack {
            SpinePageBackground()
            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    reviewsContent
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .task {
            await viewModel.loadInitial()
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            reviewPoster
                .frame(width: 40, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(headerLabel)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
                Text(detail.displayTitle)
                    .font(.system(size: 19, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(.white.opacity(0.06), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close reviews")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.08))
                .frame(height: 1)
        }
    }

    @ViewBuilder
    private var reviewPoster: some View {
        SpineAsyncImage(url: detail.displayPosterURL.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                let theme = MediaTypeTheme.theme(for: detail.ref.mediaType)
                LinearGradient(
                    colors: theme.gradientColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .overlay {
                    MediaTypeGlyph(theme: theme, size: 17)
                        .opacity(0.5)
                }
            }
        }
        .accessibilityHidden(true)
    }

    @ViewBuilder
    private var reviewsContent: some View {
        if viewModel.isLoadingInitial, viewModel.reviews.isEmpty {
            ProgressView("Loading reviews…")
                .tint(.white)
                .foregroundStyle(.white.opacity(0.7))
                .frame(maxWidth: .infinity, minHeight: 220)
        } else if let error = viewModel.initialError, viewModel.reviews.isEmpty {
            errorState(message: error) {
                Task { await viewModel.retryInitial() }
            }
            .frame(minHeight: 220)
        } else if viewModel.reviews.isEmpty {
            Text("No written reviews.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
                .frame(maxWidth: .infinity, minHeight: 220)
        } else {
            ForEach(viewModel.reviews) { review in
                MediaReviewRow(review: review, mediaType: detail.ref.mediaType)
                    .padding(.horizontal, 16)
                    .task {
                        guard viewModel.shouldLoadNext(after: review.id) else { return }
                        await viewModel.loadNextPage()
                    }

                if review.id != viewModel.reviews.last?.id {
                    Divider()
                        .overlay(.white.opacity(0.08))
                        .padding(.horizontal, 16)
                }
            }
            paginationState
        }
    }

    @ViewBuilder
    private var paginationState: some View {
        if viewModel.isLoadingMore {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity)
                .padding(24)
        } else if let error = viewModel.paginationError {
            errorState(message: error) {
                Task { await viewModel.loadNextPage() }
            }
        }
    }

    private func errorState(message: String, retry: @escaping () -> Void) -> some View {
        VStack(spacing: 10) {
            Text(message)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.red.opacity(0.82))
                .multilineTextAlignment(.center)
            Button("Retry", action: retry)
                .font(.system(size: 13, weight: .heavy))
                .buttonStyle(.bordered)
                .tint(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
    }

    private var headerLabel: String {
        guard viewModel.reviewCount > 0 else { return "Reviews for…" }
        let noun = viewModel.reviewCount == 1 ? "review" : "reviews"
        return "\(viewModel.reviewCount.formatted()) \(noun) for…"
    }
}

private struct MediaReviewRow: View {
    @State private var revealsSpoiler = false

    let review: MediaReview
    let mediaType: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                reviewerAvatar

                VStack(alignment: .leading, spacing: 2) {
                    Text(review.user.displayName)
                        .font(.system(size: 15, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    HStack(spacing: 7) {
                        Text("@\(review.user.username)")
                        if let date = review.createdAt?.shortDateLabel {
                            Text(date)
                        }
                    }
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.46))
                }

                Spacer()

                if let rating = review.rating {
                    Label(
                        rating.starRatingLabel(mediaType: mediaType),
                        systemImage: "star.fill"
                    )
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.86))
                }
            }

            if review.containsSpoilers, !revealsSpoiler {
                Button {
                    revealsSpoiler = true
                } label: {
                    Label("This review contains spoilers. Tap to reveal.", systemImage: "eye.slash.fill")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.yellow.opacity(0.84))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(.yellow.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                .accessibilityHint("Reveals the review title and text")
            } else {
                if let title = review.reviewTitle?.nilIfEmpty {
                    Text(title)
                        .font(.system(size: 16, weight: .heavy))
                        .foregroundStyle(.white)
                }

                Text(review.review)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if review.likeCount > 0 {
                Text("\(review.likeCount.formatted()) \(review.likeCount == 1 ? "like" : "likes")")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.white.opacity(0.46))
            }
        }
        .padding(.vertical, 18)
    }

    private var reviewerAvatar: some View {
        SpineAsyncImage(url: review.user.avatarUrl.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                Circle()
                    .fill(.white.opacity(0.08))
                    .overlay {
                        Image(systemName: "person.fill")
                            .foregroundStyle(.white.opacity(0.42))
                    }
            }
        }
        .frame(width: 42, height: 42)
        .clipShape(Circle())
        .accessibilityHidden(true)
    }
}

private struct AniListReviewsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AniListReviewsViewModel

    private let detail: MediaDetail

    init(detail: MediaDetail, mediaRepository: MediaRepository) {
        self.detail = detail
        _viewModel = State(
            initialValue: AniListReviewsViewModel(
                ref: detail.ref,
                mediaRepository: mediaRepository
            )
        )
    }

    var body: some View {
        ZStack {
            SpinePageBackground()
            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    spoilerWarning
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                    reviewsContent
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            doneButton
        }
        .task {
            await viewModel.loadInitial()
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            reviewPoster
                .frame(width: 40, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text("AniList reviews for…")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
                Text(detail.displayTitle)
                    .font(.system(size: 19, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(.white.opacity(0.06), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close AniList reviews")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.08))
                .frame(height: 1)
        }
    }

    @ViewBuilder
    private var reviewPoster: some View {
        SpineAsyncImage(url: detail.displayPosterURL.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                LinearGradient(
                    colors: MediaTypeTheme.theme(for: "anime").gradientColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                    .overlay {
                        Image(systemName: "play.rectangle.fill")
                            .foregroundStyle(.white.opacity(0.5))
                    }
            }
        }
        .accessibilityHidden(true)
    }

    private var spoilerWarning: some View {
        Label(
            "AniList reviews may contain spoilers.",
            systemImage: "exclamationmark.triangle.fill"
        )
        .font(.system(size: 12, weight: .bold))
        .foregroundStyle(.yellow.opacity(0.82))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(11)
        .background(.yellow.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private var reviewsContent: some View {
        if viewModel.isLoadingInitial, viewModel.reviews.isEmpty {
            ProgressView("Loading AniList reviews…")
                .tint(.white)
                .foregroundStyle(.white.opacity(0.7))
                .frame(maxWidth: .infinity, minHeight: 180)
        } else if let error = viewModel.initialError, viewModel.reviews.isEmpty {
            errorState(message: error) {
                Task { await viewModel.retryInitial() }
            }
            .frame(minHeight: 180)
        } else if viewModel.reviews.isEmpty {
            Text("No written reviews.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
                .frame(maxWidth: .infinity, minHeight: 180)
        } else {
            ForEach(viewModel.reviews) { review in
                AniListReviewRow(review: review)
                    .padding(.horizontal, 16)
                    .onAppear {
                        guard viewModel.shouldLoadNext(after: review.id) else { return }
                        Task { await viewModel.loadNextPage() }
                    }
                Divider()
                    .overlay(.white.opacity(0.08))
                    .padding(.horizontal, 16)
            }
            paginationState
        }
    }

    @ViewBuilder
    private var paginationState: some View {
        if viewModel.isLoadingMore {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity)
                .padding(24)
        } else if let error = viewModel.paginationError {
            errorState(message: error) {
                Task { await viewModel.loadNextPage() }
            }
        }
    }

    private func errorState(message: String, retry: @escaping () -> Void) -> some View {
        VStack(spacing: 10) {
            Text(message)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.red.opacity(0.82))
                .multilineTextAlignment(.center)
            Button("Retry", action: retry)
                .font(.system(size: 13, weight: .heavy))
                .buttonStyle(.bordered)
                .tint(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
    }

    private var doneButton: some View {
        Button {
            dismiss()
        } label: {
            Text("Done")
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(.black)
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(.white, in: RoundedRectangle(cornerRadius: 15))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(SpinePalette.pageBackground.opacity(0.96))
        .accessibilityHint("Closes AniList reviews")
    }
}

private struct AniListReviewRow: View {
    @State private var isExpanded = false

    let review: AniListReview

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            reviewerHeader

            HStack {
                if let score = review.score {
                    Text("\(score)%")
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(.white)
                }
                Spacer()
                if let date = review.createdAt?.shortDateLabel {
                    Text(date)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.48))
                }
            }

            if let summary = review.summary?.nilIfEmpty {
                Text(summary)
                    .font(.system(size: 15, weight: .heavy))
                    .foregroundStyle(.white)
            }

            Text(review.body)
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(.white.opacity(0.76))
                .lineSpacing(3)
                .lineLimit(isExpanded ? nil : 5)

            if review.body.count > 180 {
                Button(isExpanded ? "SHOW LESS" : "SHOW MORE") {
                    isExpanded.toggle()
                }
                .font(.system(size: 11, weight: .heavy))
                .foregroundStyle(.white.opacity(0.58))
                .buttonStyle(.plain)
                .accessibilityValue(isExpanded ? "Expanded" : "Collapsed")
            }

            HStack(spacing: 14) {
                if let helpfulness {
                    Text(helpfulness)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.46))
                }
                Spacer()
                if let destination = aniListDestination(review.url) {
                    Link(destination: destination) {
                        Label("Original review", systemImage: "arrow.up.right")
                            .font(.system(size: 11, weight: .heavy))
                            .foregroundStyle(.white.opacity(0.72))
                    }
                }
            }
        }
        .padding(.vertical, 18)
    }

    @ViewBuilder
    private var reviewerHeader: some View {
        if let destination = aniListDestination(review.user.profileUrl) {
            Link(destination: destination) {
                reviewerIdentity
            }
            .accessibilityLabel("Open \(review.user.name)'s AniList profile")
        } else {
            reviewerIdentity
        }
    }

    private var reviewerIdentity: some View {
        HStack(spacing: 10) {
            SpineAsyncImage(url: aniListDestination(review.user.avatarUrl)) { phase in
                switch phase {
                case let .success(image):
                    image
                        .resizable()
                        .scaledToFill()
                default:
                    Circle()
                        .fill(.white.opacity(0.08))
                        .overlay {
                            Image(systemName: "person.fill")
                                .foregroundStyle(.white.opacity(0.42))
                        }
                }
            }
            .frame(width: 42, height: 42)
            .clipShape(Circle())

            Text(review.user.name)
                .font(.system(size: 15, weight: .heavy))
                .foregroundStyle(.white)
                .lineLimit(2)
        }
    }

    private var helpfulness: String? {
        switch (review.communityRating, review.communityRatingCount) {
        case let (rating?, count?):
            "\(rating.formatted()) helpful score · \(count.formatted()) votes"
        case let (rating?, nil):
            "\(rating.formatted()) helpful score"
        case let (nil, count?):
            "\(count.formatted()) votes"
        case (nil, nil):
            nil
        }
    }
}

private func aniListDestination(_ rawValue: String?) -> URL? {
    guard
        let rawValue,
        let url = URL(string: rawValue),
        ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
        url.host != nil
    else { return nil }
    return url
}

private struct SpineRatingDistributionSection: View {
    let community: CommunityStats?
    let mediaType: String
    let reviewCount: Int
    let onSeeReviews: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                if let average = community?.averageRating {
                    Label(average.starRatingLabel(mediaType: mediaType), systemImage: "star.fill")
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(.white)
                }
                Spacer()
                Text("\((community?.ratingCount ?? 0).formatted()) ratings")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.52))
            }

            if buckets.isEmpty {
                Text("No ratings yet.")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.58))
            } else {
                HStack(alignment: .bottom, spacing: 8) {
                    ForEach(buckets, id: \.rating) { bucket in
                        VStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.white.opacity(0.82))
                                .frame(width: 18, height: max(4, CGFloat(bucket.count) / CGFloat(maxCount) * 70))
                            Text(bucket.rating)
                                .font(.system(size: 9, weight: .heavy))
                                .foregroundStyle(.white.opacity(0.58))
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
                .frame(height: 96, alignment: .bottom)
            }

            if reviewCount > 0 {
                Button(action: onSeeReviews) {
                    HStack(spacing: 5) {
                        Text(reviewsButtonTitle)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .heavy))
                    }
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.82))
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .accessibilityHint("Opens every written review")
            }
        }
        .padding(14)
        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 16))
    }

    private var buckets: [RatingDistributionBucket] {
        let rawBuckets = community?.ratingDistribution ?? []
        guard !rawBuckets.isEmpty else { return [] }
        let counts = Dictionary(
            grouping: rawBuckets,
            by: { $0.rating.starRatingStep(mediaType: mediaType) }
        )
            .mapValues { $0.reduce(0) { $0 + $1.count } }
        return (1...10).map { step in
            RatingDistributionBucket(rating: String.starRatingLabel(forStep: step), count: counts[step, default: 0])
        }
    }

    private var maxCount: Int {
        max(buckets.map(\.count).max() ?? 1, 1)
    }

    private var reviewsButtonTitle: String {
        reviewCount == 1 ? "See 1 review" : "See all \(reviewCount.formatted()) reviews"
    }
}

private struct CreditDisplay: Identifiable {
    let name: String
    let subtitle: String?
    let imageUrl: String?
    let personRef: PersonRef?

    var id: String { "\(name):\(subtitle ?? ""):\(personRef?.id ?? "")" }
}

private struct CreditSection: View {
    private static let collapsedLimit = 6

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let title: String
    let cast: [CreditDisplay]
    let crew: [CreditDisplay]
    let onSelect: (PersonRef) -> Void
    @State private var selectedTab: CreditTab
    @State private var expandedTab: CreditTab?

    init(
        title: String,
        cast: [CreditDisplay],
        crew: [CreditDisplay],
        onSelect: @escaping (PersonRef) -> Void
    ) {
        self.title = title
        self.cast = cast
        self.crew = crew
        self.onSelect = onSelect
        _selectedTab = State(initialValue: title == "Characters & Creators" ? .crew : .cast)
    }

    private var visiblePeople: [CreditDisplay] {
        switch selectedTab {
        case .cast:
            cast.isEmpty ? crew : cast
        case .crew:
            crew.isEmpty ? cast : crew
        }
    }

    private var tabs: [CreditTab] {
        title == "Characters & Creators" ? [.crew, .cast] : [.cast, .crew]
    }

    private var isExpanded: Bool { expandedTab == selectedTab }

    /// Short lists show everyone; longer ones open on the first few and expand in place.
    private var collapsedCount: Int {
        visiblePeople.count > Self.collapsedLimit + 2 ? Self.collapsedLimit : visiblePeople.count
    }

    private var shownPeople: [CreditDisplay] {
        isExpanded ? visiblePeople : Array(visiblePeople.prefix(collapsedCount))
    }

    var body: some View {
        if !visiblePeople.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    SectionLabel(title: title)
                    Spacer(minLength: 0)
                    if !cast.isEmpty && !crew.isEmpty {
                        creditTabs
                    }
                }

                // Swapping identity per tab crossfades the two lists inside one card.
                ZStack(alignment: .top) {
                    creditList
                        .id(selectedTab)
                        .transition(.opacity)
                }
                .mediaDetailGroupSurface()
            }
        }
    }

    private var creditTabs: some View {
        Picker(title, selection: $selectedTab.animation(reduceMotion ? nil : .smooth(duration: 0.3))) {
            ForEach(tabs, id: \.self) { tab in
                Text(tabTitle(tab)).tag(tab)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .controlSize(.small)
        .fixedSize()
        // The page is always dark; keep the system control from drawing its light-mode thumb.
        .environment(\.colorScheme, .dark)
    }

    private var creditList: some View {
        VStack(spacing: 0) {
            ForEach(Array(shownPeople.enumerated()), id: \.element.id) { index, person in
                creditRow(person)
                    .overlay(alignment: .top) {
                        if index > 0 {
                            MediaDetailRowDivider(leadingInset: CreditRow.textInset)
                        }
                    }
            }

            if collapsedCount < visiblePeople.count {
                expandButton
                    .overlay(alignment: .top) {
                        MediaDetailRowDivider(leadingInset: MediaDetailGroup.horizontalPadding)
                    }
            }
        }
    }

    @ViewBuilder
    private func creditRow(_ person: CreditDisplay) -> some View {
        if let personRef = person.personRef {
            Button {
                onSelect(personRef)
            } label: {
                CreditRow(person: person, showsChevron: true)
            }
            .buttonStyle(MediaDetailRowButtonStyle())
            .accessibilityLabel(accessibilityLabel(for: person))
            .accessibilityHint("Opens person details")
        } else {
            CreditRow(person: person, showsChevron: false)
                .accessibilityElement(children: .combine)
        }
    }

    private var expandButton: some View {
        Button {
            withAnimation(reduceMotion ? nil : .smooth(duration: 0.35)) {
                expandedTab = isExpanded ? nil : selectedTab
            }
        } label: {
            HStack(spacing: 6) {
                Text(isExpanded ? "Show Less" : "Show All")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.82))

                if !isExpanded {
                    Text(visiblePeople.count.formatted())
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(.white.opacity(0.4))
                }

                Spacer(minLength: 8)

                Image(systemName: "chevron.down")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white.opacity(0.32))
                    .rotationEffect(.degrees(isExpanded ? 180 : 0))
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, MediaDetailGroup.horizontalPadding)
            .frame(maxWidth: .infinity, minHeight: MediaDetailGroup.rowMinHeight)
            .contentShape(Rectangle())
        }
        .buttonStyle(MediaDetailRowButtonStyle())
    }

    private func tabTitle(_ tab: CreditTab) -> String {
        if tab == .crew, title == "Cast & Characters" {
            return "Characters"
        }
        if title == "Characters & Creators" {
            return tab == .cast ? "Characters" : "Creators"
        }
        return tab.title
    }

    private func accessibilityLabel(for person: CreditDisplay) -> String {
        guard let subtitle = person.subtitle, !subtitle.isEmpty else {
            return "View \(person.name)"
        }
        return "View \(person.name), \(subtitle)"
    }
}

private enum CreditTab {
    case cast
    case crew

    var title: String {
        switch self {
        case .cast: "Cast"
        case .crew: "Crew"
        }
    }
}

private struct CreditRow: View {
    private static let avatarSize: CGFloat = 42
    private static let avatarSpacing: CGFloat = 12
    static let textInset = MediaDetailGroup.horizontalPadding + avatarSize + avatarSpacing

    let person: CreditDisplay
    let showsChevron: Bool

    var body: some View {
        HStack(spacing: Self.avatarSpacing) {
            avatar

            VStack(alignment: .leading, spacing: 2) {
                Text(person.name)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.92))
                    .lineLimit(1)

                if let role = person.subtitle?.nilIfEmpty {
                    Text(role)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.white.opacity(0.5))
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white.opacity(0.26))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, MediaDetailGroup.horizontalPadding)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, minHeight: 62, alignment: .leading)
        .contentShape(Rectangle())
    }

    private var avatar: some View {
        SpineAsyncImage(
            url: URL(string: person.imageUrl ?? ""),
            thumbnailSize: CGSize(width: Self.avatarSize, height: Self.avatarSize)
        ) { phase in
            switch phase {
            case let .success(image):
                // Centered like the person page: a top-pinned crop cut off faces that fill the headshot.
                image
                    .resizable()
                    .scaledToFill()
            default:
                monogram
            }
        }
        .frame(width: Self.avatarSize, height: Self.avatarSize)
        .clipShape(Circle())
        .overlay {
            Circle().strokeBorder(.white.opacity(0.12), lineWidth: 0.5)
        }
    }

    private var monogram: some View {
        Circle()
            .fill(.white.opacity(0.08))
            .overlay {
                if let initials {
                    Text(initials)
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.6))
                } else {
                    Image(systemName: "person.fill")
                        .font(.system(size: 16))
                        .foregroundStyle(.white.opacity(0.4))
                }
            }
    }

    private var initials: String? {
        let words = person.name.split(whereSeparator: \.isWhitespace)
        let letters = [words.first, words.count > 1 ? words.last : nil]
            .compactMap { $0?.first(where: \.isLetter) }
        return letters.isEmpty ? nil : String(letters).uppercased()
    }
}

private struct SeasonsSection: View {
    let cards: [TVSeasonCardModel]
    /// The card's line from the viewer's state, counting pending marks (TV-200).
    let label: (TVSeasonCardModel) -> String?
    let onSelect: (TVSeasonCardModel) -> Void

    var body: some View {
        if !cards.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Seasons")
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(cards) { card in
                            Button {
                                onSelect(card)
                            } label: {
                                TVSeasonCard(
                                    card: card,
                                    label: label(card),
                                    width: MediaDetailLayout.seasonPosterSize.width
                                )
                            }
                            .buttonStyle(.plain)
                            .accessibilityHint("Opens \(card.title)")
                            .frame(width: MediaDetailLayout.seasonPosterSize.width, alignment: .topLeading)
                        }
                    }
                }
            }
        }
    }
}

private struct EpisodesSection: View {
    let rows: [TVEpisodeRowModel]
    let completion: CompletionProgress?
    /// An episode's current TV state: a confirmed write's answer when the list predates it.
    let state: (TVEpisodeRowModel) -> TVEpisodeState?
    /// Whether an episode shows as watched, counting a pending tick (TV-705).
    let isWatched: (TVEpisodeState) -> Bool
    let onTick: (TVEpisodeState) -> Void
    let onMarkUpTo: (TVEpisodeState) -> Void
    let onSelect: (EpisodeSummary) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                SectionLabel(title: "Episodes")
                Spacer()
                if let completion, completion.isVisible {
                    SWCompletionProgressButton(progress: completion)
                }
            }
            if rows.isEmpty {
                ContentUnavailableView(
                    "No episodes available",
                    systemImage: "play.rectangle",
                    description: Text("Episode information has not been released yet.")
                )
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 130)
                .mediaDetailSurface(cornerRadius: 18)
            } else {
                LazyVStack(spacing: 16) {
                    ForEach(rows) { row in
                        let tv = state(row)
                        EpisodeCard(
                            row: row,
                            trailing: TVEpisodeTrailing.resolve(tv, isWatched: tv.map(isWatched) ?? false),
                            onTick: { if let tv { onTick(tv) } },
                            onMarkUpTo: tv.flatMap { tv in
                                tv.canMarkUpTo && tv.markUpToPrompt != nil ? { onMarkUpTo(tv) } : nil
                            },
                            onSelect: row.summary.map { summary in { onSelect(summary) } }
                        )
                    }
                }
            }
        }
    }
}

/// An episode row: the still, number, title, air date and overview, and a tick (TV-100), the air date or "TBA" for
/// an unaired episode. Long-press offers Mark watched up to here (TV-108).
private struct EpisodeCard: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    let row: TVEpisodeRowModel
    let trailing: TVEpisodeTrailing
    let onTick: () -> Void
    let onMarkUpTo: (() -> Void)?
    /// Nil for an episode the provider no longer lists: it has no page.
    let onSelect: (() -> Void)?

    var body: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                accessibilityLayout
            } else {
                compactLayout
            }
        }
        .background(cardBackground)
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contextMenu {
            if let onMarkUpTo {
                Button("Mark Watched Up to Here", systemImage: "checkmark.circle", action: onMarkUpTo)
            }
        }
    }

    private var compactLayout: some View {
        HStack(spacing: 4) {
            Button {
                onSelect?()
            } label: {
                HStack(spacing: 12) {
                    EpisodeCardStill(episodeNumber: row.episodeNumber, imageUrl: row.summary?.imageUrl, fillsWidth: false)
                    episodeCopy
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(onSelect == nil)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityDescription)
            .accessibilityHint(onSelect == nil ? "" : "Opens episode details")
            .accessibilityAddTraits(onSelect == nil ? [] : .isButton)

            trailingView
                .padding(.trailing, trailingPadding)
        }
        .frame(maxWidth: .infinity, minHeight: 84, maxHeight: 84, alignment: .leading)
    }

    private var accessibilityLayout: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                onSelect?()
            } label: {
                VStack(alignment: .leading, spacing: 12) {
                    EpisodeCardStill(episodeNumber: row.episodeNumber, imageUrl: row.summary?.imageUrl, fillsWidth: true)
                    episodeCopy
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(onSelect == nil)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityDescription)
            .accessibilityAddTraits(onSelect == nil ? [] : .isButton)
            trailingView
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var trailingView: some View {
        switch trailing {
        case let .tick(watched):
            TVEpisodeTickButton(isWatched: watched, episodeTitle: "Episode \(row.episodeNumber)", action: onTick)
        case let .airDate(text):
            // The row's own label says when it airs.
            Text(text)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.5))
                .accessibilityHidden(true)
        case .none:
            if onSelect != nil {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white.opacity(0.32))
                    .accessibilityHidden(true)
            }
        }
    }

    private var trailingPadding: CGFloat {
        if case .tick = trailing { return 2 }
        return 12
    }

    private var episodeCopy: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(row.title)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(.white)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                .frame(maxWidth: .infinity, alignment: .leading)

            if !metadata.isEmpty {
                Text(metadata)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
            }

            if let overview = row.summary?.overview?.nilIfEmpty {
                Text(overview)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.white.opacity(0.62))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? 3 : 1)
            }
        }
    }

    private var metadata: String {
        [row.summary?.airDate?.longDateLabel, row.summary?.runtime]
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " · ")
    }

    private var accessibilityDescription: String {
        var components = ["Episode \(row.episodeNumber), \(row.title)"]
        if let airs = trailing.accessibilityAirDate {
            components.append(airs)
        } else if let airDate = row.summary?.airDate?.longDateLabel.nilIfEmpty {
            components.append("Aired \(airDate)")
        }
        if let runtime = row.summary?.runtime?.nilIfEmpty {
            components.append(runtime)
        }
        if let overview = row.summary?.overview?.nilIfEmpty {
            components.append(overview)
        }
        return components.joined(separator: ". ")
    }

    private var cardBackground: some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        return shape
            .fill(.white.opacity(0.035))
            .overlay { shape.stroke(.white.opacity(0.055), lineWidth: 0.75) }
    }
}

private struct EpisodeCardStill: View {
    let episodeNumber: Int
    let imageUrl: String?
    let fillsWidth: Bool

    var body: some View {
        artwork
            .aspectRatio(16 / 9, contentMode: .fill)
            .frame(maxWidth: fillsWidth ? .infinity : nil)
            .frame(width: fillsWidth ? nil : 148, height: fillsWidth ? nil : 84)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(alignment: .bottomLeading) {
                Text("E\(episodeNumber)")
                    .font(.caption2.weight(.black))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 4)
                    .background(.black.opacity(0.62), in: Capsule())
                    .padding(7)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(.white.opacity(0.08), lineWidth: 0.75)
            }
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var artwork: some View {
        SpineAsyncImage(url: imageUrl.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                placeholder
            }
        }
    }

    private var placeholder: some View {
        let theme = MediaTypeTheme.theme(for: "episode")
        return ZStack {
            LinearGradient(
                colors: theme.gradientColors.map { $0.opacity(0.78) },
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Image(systemName: "play.rectangle.fill")
                .font(.title2)
                .foregroundStyle(.white.opacity(0.56))
        }
    }
}

private struct RecommendationsSection: View {
    let sections: [RelatedMediaSection]
    let onSelectSection: ((RelatedMediaSection) -> Void)?
    let onSelect: (MediaSummary) -> Void

    init(
        sections: [RelatedMediaSection],
        onSelectSection: ((RelatedMediaSection) -> Void)? = nil,
        onSelect: @escaping (MediaSummary) -> Void
    ) {
        self.sections = sections
        self.onSelectSection = onSelectSection
        self.onSelect = onSelect
    }

    var body: some View {
        ForEach(sections.filter { !$0.items.isEmpty }) { section in
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 12) {
                    if (section.id == "series" || section.id == "collection"),
                       let onSelectSection {
                        Button {
                            onSelectSection(section)
                        } label: {
                            HStack(spacing: 6) {
                                SectionLabel(title: section.title)
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 9, weight: .heavy))
                                    .foregroundStyle(.white.opacity(0.42))
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(section.title) series")
                    } else {
                        SectionLabel(title: section.title)
                    }

                    Spacer()
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(section.items) { item in
                            Button {
                                onSelect(item)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    MediaArtwork(
                                        url: item.displayPosterURL,
                                        title: item.displayTitle,
                                        slot: .carousel,
                                        mediaType: item.ref.mediaType,
                                        orientation: item.posterOrientation
                                    )
                                    .overlay(alignment: .bottomLeading) {
                                        if let relation = item.relation?.nilIfEmpty {
                                            Text(relation)
                                                .font(.system(size: 9, weight: .heavy))
                                                .foregroundStyle(.white.opacity(0.92))
                                                .lineLimit(1)
                                                .padding(.horizontal, 7)
                                                .frame(height: 20)
                                                .background(.black.opacity(0.72), in: Capsule())
                                                .padding(6)
                                        }
                                    }
                                    Text(item.displayTitle)
                                        .font(.system(size: 12, weight: .heavy))
                                        .foregroundStyle(.white)
                                        .lineLimit(2)
                                        .frame(height: 32, alignment: .topLeading)
                                }
                                .frame(
                                    width: MediaDetailLayout.recommendationPosterSize.width,
                                    height: MediaDetailLayout.recommendationCardHeight,
                                    alignment: .topLeading
                                )
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Open \(item.displayTitle)")
                        }
                    }
                }
            }
        }
    }

}

private extension JSONValue {
    var displayString: String? {
        switch self {
        case let .string(value):
            value
        case let .number(value):
            value.rounded() == value ? String(Int(value)) : String(value)
        case let .bool(value):
            value ? "Yes" : "No"
        case let .object(value):
            value["name"]?.displayString ?? value["provider_name"]?.displayString
        case .array, .null:
            nil
        }
    }

    var displayStrings: [String] {
        switch self {
        case let .array(values):
            values.flatMap(\.displayStrings)
        case let .object(value):
            if let string = displayString {
                [string]
            } else {
                value.values.flatMap(\.displayStrings)
            }
        default:
            displayString.map { [$0] } ?? []
        }
    }

    var numberValue: Double? {
        switch self {
        case let .number(value):
            value
        case let .string(value):
            Double(value)
        default:
            nil
        }
    }

    var intValue: Int? {
        numberValue.map(Int.init)
    }

    var objectValue: [String: JSONValue]? {
        if case let .object(value) = self {
            return value
        }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case let .array(value) = self {
            return value
        }
        return nil
    }
}

extension Color {
    init?(hex: String?) {
        guard let hex else { return nil }
        let value = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        guard value.count == 6, let int = Int(value, radix: 16) else { return nil }
        self.init(
            red: Double((int >> 16) & 0xFF) / 255,
            green: Double((int >> 8) & 0xFF) / 255,
            blue: Double(int & 0xFF) / 255
        )
    }
}

func supportsTitleLogo(_ detail: MediaDetail) -> Bool {
    detail.displayLogoURL != nil
}

struct MediaTitleDisplay: View {
    let detail: MediaDetail
    let title: String
    @Binding var showsLogo: Bool
    let font: Font
    let lineLimit: Int?
    let minimumScaleFactor: CGFloat
    let maxLogoHeight: CGFloat
    let alignment: Alignment
    let onTap: (() -> Void)?
    let onLongPress: (() -> Void)?

    init(
        detail: MediaDetail,
        title: String,
        showsLogo: Binding<Bool>,
        font: Font,
        lineLimit: Int?,
        minimumScaleFactor: CGFloat,
        maxLogoHeight: CGFloat,
        alignment: Alignment = .center,
        onTap: (() -> Void)?,
        onLongPress: (() -> Void)?
    ) {
        self.detail = detail
        self.title = title
        _showsLogo = showsLogo
        self.font = font
        self.lineLimit = lineLimit
        self.minimumScaleFactor = minimumScaleFactor
        self.maxLogoHeight = maxLogoHeight
        self.alignment = alignment
        self.onTap = onTap
        self.onLongPress = onLongPress
    }

    private var canToggle: Bool {
        supportsTitleLogo(detail)
    }

    private var isInteractive: Bool {
        onTap != nil || canToggle
    }

    var body: some View {
        Group {
            if canToggle, showsLogo, let logoUrl = detail.displayLogoURL, let url = URL(string: logoUrl) {
                SpineAsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        TitleLogoLayout(maxLogoHeight: maxLogoHeight, aspectRatio: aspectRatio, alignment: alignment) {
                            image
                                .resizable()
                                .scaledToFit()
                        }
                    case .failure:
                        titleText
                    default:
                        Color.clear
                            .frame(height: maxLogoHeight)
                    }
                }
                .frame(maxWidth: .infinity, alignment: alignment)
            } else {
                titleText
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            if let onTap {
                onTap()
                return
            }
            guard canToggle else { return }
            withAnimation(.easeInOut(duration: 0.2)) {
                showsLogo.toggle()
            }
        }
        .onLongPressGesture {
            guard canToggle, showsLogo else { return }
            onLongPress?()
        }
        .accessibilityLabel(title)
        .accessibilityHint(onTap != nil ? "Open media" : canToggle ? "Double tap to switch between logo and text title" : "")
        .accessibilityAddTraits(isInteractive ? .isButton : [])
    }

    private var titleText: some View {
        Text(title)
            .font(font)
            .foregroundStyle(.white)
            .lineLimit(lineLimit)
            .minimumScaleFactor(minimumScaleFactor)
    }

    private var aspectRatio: CGFloat? {
        if let ratio = detail.logoAspectRatio, ratio > 0 {
            return CGFloat(ratio)
        }
        if let width = detail.logoWidth, let height = detail.logoHeight, height > 0 {
            return CGFloat(width) / CGFloat(height)
        }
        return nil
    }
}

private struct TitleLogoLayout: Layout {
    let maxLogoHeight: CGFloat
    let aspectRatio: CGFloat?
    let alignment: Alignment

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        return CGSize(width: width, height: logoSize(for: width).height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let subview = subviews.first else { return }
        let size = logoSize(for: bounds.width)
        let x = alignment == .leading
            ? bounds.minX
            : alignment == .trailing
                ? bounds.maxX - size.width
                : bounds.midX - size.width / 2
        let origin = CGPoint(
            x: x,
            y: bounds.minY
        )
        subview.place(
            at: origin,
            proposal: ProposedViewSize(size)
        )
    }

    private func logoSize(for availableWidth: CGFloat) -> CGSize {
        titleLogoSize(
            availableWidth: availableWidth,
            maxLogoHeight: maxLogoHeight,
            aspectRatio: aspectRatio
        )
    }
}

func titleLogoSize(
    availableWidth: CGFloat,
    maxLogoHeight: CGFloat,
    aspectRatio: CGFloat?
) -> CGSize {
    guard availableWidth > 0, let aspectRatio, aspectRatio > 0 else {
        return CGSize(width: availableWidth, height: maxLogoHeight)
    }

    let targetWidth = availableWidth * 0.82
    let preferredHeight = min(
        max(targetWidth / aspectRatio, maxLogoHeight),
        maxLogoHeight * 1.45
    )
    let width = min(availableWidth, preferredHeight * aspectRatio)
    return CGSize(width: width, height: width / aspectRatio)
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }

    var yearPrefix: String? {
        count >= 4 ? String(prefix(4)) : nil
    }

    var starRatingLabel: String {
        guard let raw = Double(self) else { return self }
        let stars = raw / 2
        return "\(Self.cleanRating(stars))/5"
    }

    var starRatingValue: String {
        guard let raw = Double(self) else { return self }
        return String(format: "%.1f", raw / 2)
    }

    var starRatingStep: Int {
        guard let raw = Double(self) else { return 0 }
        return min(max(Int(round(raw)), 1), 10)
    }

    func starRatingLabel(mediaType: String) -> String {
        guard let raw = Double(self) else { return self }
        let stars = ["movie", "music", "book", "game"].contains(mediaType) ? raw : raw / 2
        return "\(Self.cleanRating(stars))/5"
    }

    func starRatingValue(mediaType: String) -> String {
        guard let raw = Double(self) else { return self }
        let stars = ["movie", "music", "book", "game"].contains(mediaType) ? raw : raw / 2
        return String(format: "%.1f", stars)
    }

    func starRatingStep(mediaType: String) -> Int {
        guard let raw = Double(self) else { return 0 }
        let step = ["movie", "music", "book", "game"].contains(mediaType) ? raw * 2 : raw
        return min(max(Int(round(step)), 1), 10)
    }

    var shortDateLabel: String {
        let trimmed = String(prefix(10))
        let input = DateFormatter()
        input.calendar = Calendar(identifier: .gregorian)
        input.locale = Locale(identifier: "en_US_POSIX")
        input.dateFormat = "yyyy-MM-dd"
        guard let date = input.date(from: trimmed) else { return trimmed }

        let output = DateFormatter()
        output.calendar = Calendar(identifier: .gregorian)
        output.locale = Locale.current
        output.dateFormat = "MMM d, yyyy"
        return output.string(from: date)
    }

    var longDateLabel: String {
        LongDateFormatter().string(from: self) ?? self
    }

    var oneDecimalLabel: String {
        guard let value = Double(self) else { return self }
        return String(format: "%.1f", value)
    }

    static func starRatingLabel(forStep step: Int) -> String {
        cleanRating(Double(step) / 2)
    }

    private static func cleanRating(_ value: Double) -> String {
        value.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(value))" : String(format: "%.1f", value)
    }
}

extension ExternalRating {
    var displayValue: String {
        let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if ["anilist", "rotten tomatoes", "rottentomatoes"].contains(source.lowercased()) {
            return trimmedValue.hasSuffix("%") ? trimmedValue : "\(trimmedValue)%"
        }
        if source.lowercased() == "hardcover" {
            let displayRating = Double(trimmedValue).map { rawValue in
                let value = rawValue > 5 ? rawValue / 2 : rawValue
                return value.rounded() == value ? "\(Int(value))" : String(format: "%.1f", value)
            } ?? trimmedValue
            return "\(displayRating)/5"
        }
        if trimmedValue.contains("/") || trimmedValue.hasSuffix("%") {
            return trimmedValue
        }
        if let denominator = ratingDenominator ?? maxValue?.nilIfEmpty {
            return "\(trimmedValue)/\(denominator)"
        }
        return trimmedValue
    }

    private var ratingDenominator: String? {
        switch source.lowercased() {
        case "spine", "letterboxd", "hardcover", "google books":
            "5"
        case "imdb":
            "10"
        default:
            nil
        }
    }

    var ratingAssetName: String? {
        switch source.lowercased() {
        case "imdb":
            "RatingIMDb"
        case "tmdb":
            "RatingTMDB"
        case "letterboxd":
            "RatingLetterboxd"
        case "rotten tomatoes":
            rottenTomatoesAssetName
        case "mal", "myanimelist":
            "RatingMAL"
        case "anilist":
            "RatingAniList"
        case "hardcover":
            "RatingHardcover"
        case "metacritic":
            "RatingMetacritic"
        case "steam":
            "RatingSteam"
        case "igdb":
            "RatingIGDB"
        default:
            nil
        }
    }

    private var rottenTomatoesAssetName: String {
        // ponytail: API only sends RT score; use percent thresholds until it sends certification.
        guard let score = value.split(whereSeparator: { !$0.isNumber && $0 != "." }).first.flatMap({ Double($0) }) else {
            return "RatingRottenTomatoes"
        }
        if score <= 59 {
            return "RatingRottenTomatoesRotten"
        }
        if score >= 75 {
            return "RatingRottenTomatoesCertifiedFresh"
        }
        return "RatingRottenTomatoes"
    }
}

extension String {
    /// Just the stars, "3.5" or "4", for places that draw the star themselves.
    func starCount(mediaType: String) -> String? {
        guard let raw = Double(self) else { return nil }
        let stars = ["movie", "music", "book", "game"].contains(mediaType) ? raw : raw / 2
        return Self.cleanRating(stars)
    }

    var ratingCountLabel: String {
        switch lowercased() {
        case "letterboxd", "google books":
            "ratings"
        case "rotten tomatoes", "rottentomatoes", "steam":
            "reviews"
        default:
            "votes"
        }
    }

    var ratingAbbreviation: String {
        switch lowercased() {
        case "imdb":
            "IM"
        case "letterboxd":
            "LB"
        case "rotten tomatoes":
            "RT"
        case "tmdb":
            "TM"
        case "hardcover":
            "HC"
        case "igdb":
            "IG"
        case "metacritic":
            "MC"
        case "steam":
            "ST"
        case "mal":
            "MA"
        case "anilist":
            "AL"
        case "mangaupdates":
            "MU"
        case "openlibrary":
            "OL"
        case "google books":
            "Google Books"
        default:
            String(prefix(2)).uppercased()
        }
    }
}

private extension Array where Element == String {
    var joinedOrNil: String? {
        let value = joined(separator: ", ")
        return value.isEmpty ? nil : value
    }
}

private extension MediaDetail {
    var listSummary: MediaSummary {
        MediaSummary(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview ?? synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            customPosterUrl: customPosterUrl,
            backdropUrl: backdropUrl,
            customBackdropUrl: customBackdropUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState
        )
    }
}

private struct LongDateFormatter {
    private let isoFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private let displayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale.current
        formatter.dateFormat = "MMMM d, yyyy"
        return formatter
    }()

    func string(from raw: String) -> String? {
        let trimmed = String(raw.prefix(10))
        guard let date = isoFormatter.date(from: trimmed) else { return nil }
        return displayFormatter.string(from: date)
    }
}
