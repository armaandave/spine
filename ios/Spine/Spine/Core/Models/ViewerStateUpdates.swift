import Foundation

/// A write the page shows before the server confirms it. Display only: requests never read a projection.
struct PendingWrite: Identifiable {
    let id = UUID()
    let project: (inout UserMediaState) -> Void
}

enum WriteOutcome {
    /// A 200 carrying the canonical tracking state.
    case state(TrackingState)
    /// A 204, or DELETE /tracking.
    case untracked
    /// `{tracking, diary_entry}` from a completion log.
    case completion(BookCompletionResponse)
    /// POST /me/liked-media/ returns the media's user state; DELETE returns only `{liked}`.
    case liked(Bool, UserMediaState?)
    /// A TV action's `{tracking, tv, season, episode, changed, finished, earlier_unwatched}`. Fold it in with
    /// `applying(tvEnvelope:tracking:)`, then hand the envelope to `TVWriteFollowUp`.
    case tv(TVActionResponse)
    /// A result the client can't fold in: needs a full refresh.
    case unknown
}

enum RefreshNeed: Comparable {
    case none, community, communityAndReviews, full
}

nonisolated extension UserMediaState {
    static let untracked = UserMediaState(isTracked: false)

    /// The game's dates come from its current playthrough (a plain date); other types use the tracking row's.
    var displayStartDate: String? {
        game != nil ? game?.currentPlaythrough?.startDate : startDate
    }

    /// Folds in a write's canonical state. Diary fields and list membership aren't part of TrackingState, so
    /// they are kept.
    func applying(_ tracking: TrackingState) -> UserMediaState {
        var state = self
        state.isTracked = true
        state.trackingId = tracking.trackingId
        state.status = tracking.status
        state.rating = tracking.rating
        state.progress = tracking.progress
        state.book = tracking.book
        state.game = tracking.game
        if let tv = tracking.tv {
            state.tv = tv.keepingPageScope(of: self.tv)
        }
        state.startDate = tracking.game == nil ? tracking.startDate : nil
        // Only half-star payloads (movie, music, book, game) carry the heart and its provenance.
        if let liked = tracking.liked {
            state.hasLiked = liked
            state.diaryCount = tracking.diaryCount
            state.directConsumption = tracking.directConsumption
            state.ratingSourceDiaryEntryId = tracking.ratingSourceDiaryEntryId
            state.likeSourceDiaryEntryId = tracking.likeSourceDiaryEntryId
            state.likeIsIndependent = tracking.likeIsIndependent
        }
        return state
    }

    /// Makes a new log the latest one, unless the latest log is later. The server orders logs by
    /// `-consumed_at, -id`, so a backdated log never replaces a newer one.
    func applying(diaryEntry entry: DiaryEntry) -> UserMediaState {
        let entryDate = entry.consumedAt.map { String($0.prefix(10)) }
        if let current = diaryConsumedAt.map({ String($0.prefix(10)) }) {
            guard let entryDate, current <= entryDate else { return self }
        }
        var state = self
        state.diaryEntryId = entry.id
        state.diaryConsumedAt = entryDate
        state.diaryRating = entry.rating
        return state
    }

    /// Removing tracking clears current opinions (SW-008, BK-002, GM-304) but keeps logs and custom lists.
    func removed() -> UserMediaState {
        var state = self
        state.isTracked = false
        state.trackingId = nil
        state.status = nil
        state.rating = nil
        state.progress = nil
        state.startDate = nil
        state.book = nil
        state.game = nil
        state.tv = nil
        state.directConsumption = nil
        state.ratingSourceDiaryEntryId = nil
        state.likeSourceDiaryEntryId = nil
        state.likeIsIndependent = nil
        state.hasLiked = false
        return state
    }

    /// Folds a TV write's envelope into this page's state. `tracking` is the response's TrackingState of the show,
    /// nil when the show isn't in the library.
    ///
    /// On the show page the show's tracking replaces the page's (`removed()` when it is nil). A season or episode
    /// page shows that level's own tracking, stars and heart, so there the envelope's `season` or `episode` supplies
    /// them and the show's tracking is not applied. `tv` then comes from the envelope, keeping the page's own
    /// season, episode and composer when the envelope describes the show (TV-705).
    func applying(tvEnvelope envelope: TVWriteEnvelope, tracking: TrackingState?) -> UserMediaState {
        let pageSeason = tv?.season
        let pageEpisode = tv?.episode
        var state = self
        if let pageEpisode {
            if let episode = envelope.episode, episode.coordinate == pageEpisode.coordinate {
                state.isTracked = episode.viewingCount > 0
                state.hasLiked = episode.liked
            }
        } else if let pageSeason {
            if let season = envelope.season, season.seasonNumber == pageSeason.seasonNumber {
                state.isTracked = season.started
                state.status = season.status
                state.rating = season.rating
                state.hasLiked = season.liked
                state.ratingSourceDiaryEntryId = season.ratingSourceDiaryEntryId
                state.likeSourceDiaryEntryId = season.likeSourceDiaryEntryId
                state.likeIsIndependent = season.likeIsIndependent
            }
        } else {
            state = tracking.map { applying($0) } ?? removed()
        }
        if let tv = envelope.tv {
            state.tv = tv.keepingPageScope(of: self.tv, season: envelope.season, episode: envelope.episode)
        }
        return state
    }

    /// Whether an episode shows as watched, counting a pending tick, untick or mark (TV-100, TV-705).
    func tvIsWatched(_ episode: TVEpisodeState) -> Bool {
        tvPending.isWatched(episode)
    }

    /// Whether a season's eye shows as filled, counting a pending mark of the season or show.
    func tvIsMarked(_ season: TVSeasonState) -> Bool {
        if tvPending.markedSeasons.contains(season.seasonNumber) { return true }
        if tvPending.markedShow, !season.isSpecials, season.state != .unaired { return true }
        return season.eye?.filled ?? (season.state == .watched || season.state == .airing)
    }
}

private nonisolated extension UserMediaState {
    /// On the show page the page's status is the show's; season and episode pages keep their own.
    mutating func adoptShowStatus(of tv: TVShowState) {
        guard tv.season == nil, tv.episode == nil else { return }
        status = tv.status
    }
}

/// Ticks and marks the page shows before the server confirms them. Display only: it lives in the projected state
/// and never in confirmed state, so a failure or a confirmation drops it with its projection (SW-700).
nonisolated struct TVPendingWatches: Hashable {
    /// A pending tick (true) or untick (false) per episode.
    var episodes: [TVEpisodeCoordinate: Bool] = [:]
    /// Seasons with a pending eye mark.
    var markedSeasons: Set<Int> = []
    /// A pending show eye mark (regular episodes only, TV-004).
    var markedShow = false

    func isWatched(_ episode: TVEpisodeState) -> Bool {
        if let pending = episodes[episode.coordinate] { return pending }
        if episode.aired, episode.listed {
            if markedSeasons.contains(episode.seasonNumber) { return true }
            if markedShow, !episode.isSpecial { return true }
        }
        return episode.watched
    }
}

nonisolated extension TVShowState {
    /// A show state from a write or a tracking read describes the show, but a season or episode page also holds
    /// that level's state and its own composer. Keeps those when this state doesn't carry them, taking `season` or
    /// `episode` from the same response when it is the page's.
    func keepingPageScope(
        of current: TVShowState?,
        season: TVSeasonState? = nil,
        episode: TVEpisodeState? = nil
    ) -> TVShowState {
        guard let current else { return self }
        var next = self
        if next.season == nil, let pageSeason = current.season {
            next.season = season?.seasonNumber == pageSeason.seasonNumber ? season : pageSeason
            next.composer = current.composer
        }
        if next.episode == nil, let pageEpisode = current.episode {
            next.episode = episode?.coordinate == pageEpisode.coordinate ? episode : pageEpisode
            next.composer = current.composer
        }
        return next
    }

    /// A tick, log or mark ends a Planning, Paused or Dropped choice (TV-203).
    mutating func endChoiceForWatch() {
        inLibrary = true
        started = true
        guard status == nil || ["Planning", "Paused", "Dropped"].contains(choice ?? status ?? "") else { return }
        choice = nil
        status = finished ? "Completed" : "In progress"
        selectedAction = finished ? nil : "watching"
    }
}

nonisolated extension GamePlaythroughState {
    /// Marks a playthrough the server hasn't created yet. It must never reach a request.
    static let pendingID = -1

    var isPending: Bool { id == Self.pendingID }

    static func pending(startDate: String, isReplay: Bool) -> GamePlaythroughState {
        GamePlaythroughState(
            id: pendingID,
            status: "In progress",
            origin: "live",
            startDate: startDate,
            endDate: nil,
            totalMinutes: nil,
            percentage: nil,
            completionDiaryEntryId: nil,
            isReplay: isReplay
        )
    }
}

nonisolated extension GameTrackingState {
    static let untracked = GameTrackingState(
        currentPlaythrough: nil,
        playHistory: [],
        undatedCompletion: nil,
        statusSource: nil,
        completionDates: [],
        completedPlaythroughCount: 0,
        lifetimeCompletionCount: 0,
        isReplaying: false,
        canRemoveTracking: true,
        availableActions: [],
        actionReasons: [:],
        importedLifetimeMinutes: nil,
        importedLifetimeSource: nil
    )
}

nonisolated extension BookJourneyState {
    /// Marks a journey the server hasn't created yet. It must never reach a request.
    static let pendingID = -1

    var isPending: Bool { id == Self.pendingID }

    static func pending(startDate: String, isReread: Bool = false) -> BookJourneyState {
        BookJourneyState(id: pendingID, status: "In progress", origin: "live", startDate: startDate, isReread: isReread)
    }
}

/// What the page shows for a write until its response arrives.
enum TrackingProjection {
    typealias Projection = (inout UserMediaState) -> Void

    static func status(_ status: String) -> Projection {
        { state in
            state.isTracked = true
            state.status = status
        }
    }

    static func startGame(today: String) -> Projection {
        { state in
            state.isTracked = true
            state.status = "In progress"
            var game = state.game ?? .untracked
            if game.currentPlaythrough?.isUnfinished == true {
                game.currentPlaythrough?.status = "In progress"
            } else {
                let isReplay = game.lifetimeCompletionCount > 0
                game.currentPlaythrough = .pending(startDate: today, isReplay: isReplay)
                game.canRemoveTracking = false
                game.isReplaying = isReplay
            }
            state.game = game
        }
    }

    static func pauseGame() -> Projection {
        { state in
            state.isTracked = true
            state.status = "Paused"
            if state.game?.hasLivePlaythrough == true {
                state.game?.currentPlaythrough?.status = "Paused"
            }
        }
    }

    static func dropGame(today: String) -> Projection {
        { state in
            state.isTracked = true
            state.status = "Dropped"
            if state.game?.hasLivePlaythrough == true {
                state.game?.currentPlaythrough?.status = "Dropped"
                state.game?.currentPlaythrough?.endDate = today
            }
        }
    }

    static func startBook(today: String) -> Projection {
        { state in
            state.isTracked = true
            state.status = "In progress"
            var book = state.book ?? BookTrackingState()
            if book.currentJourney?.status == "Paused" {
                book.currentJourney?.status = "In progress"
            } else if book.currentJourney?.status != "In progress" {
                // BK-104: a new journey starts today with no progress; after a completion it is a reread.
                let isReread = book.lifetimeReadCount > 0
                book.currentJourney = .pending(startDate: today, isReread: isReread)
                book.canRemoveTracking = false
                book.isRereading = isReread
                state.startDate = today
                state.progress = nil
            }
            state.book = book
        }
    }

    static func pauseBook() -> Projection {
        { state in
            state.isTracked = true
            state.status = "Paused"
            if state.book?.hasLiveJourney == true {
                state.book?.currentJourney?.status = "Paused"
            }
        }
    }

    static func dropBook(today: String) -> Projection {
        { state in
            state.isTracked = true
            state.status = "Dropped"
            if state.book?.hasLiveJourney == true {
                state.book?.currentJourney?.status = "Dropped"
                state.book?.currentJourney?.endDate = today
            }
        }
    }

    /// `impliesCompletion`: the server marks the item Completed when it is rated (GM-300, BK-203).
    static func rating(_ wire: String?, impliesCompletion: Bool) -> Projection {
        { state in
            state.rating = wire
            guard wire != nil else { return }
            state.isTracked = true
            if impliesCompletion { state.status = "Completed" }
        }
    }

    /// `impliesCompletion`: liking marks movies, music, books and games Completed (SW-200, GM-300).
    static func like(_ liked: Bool, impliesCompletion: Bool) -> Projection {
        { state in
            state.hasLiked = liked
            guard liked, impliesCompletion else { return }
            state.isTracked = true
            state.status = "Completed"
        }
    }

    static var removed: Projection {
        { state in state = state.removed() }
    }

    // MARK: TV (TV-100, TV-705)

    /// A tick shows at once: the episode reads watched and the show is in the library and started. Counts move only
    /// where the page knows the episode counts (up next, or the episode page's own episode).
    static func tvTick(season: Int, episode: Int) -> Projection {
        { state in
            let key = TVEpisodeCoordinate(seasonNumber: season, episodeNumber: episode)
            state.tvPending.episodes[key] = true
            state.isTracked = true
            guard var tv = state.tv else { return }
            var counts = tv.upNext?.coordinate == key
            if var pageEpisode = tv.episode, pageEpisode.coordinate == key {
                counts = !pageEpisode.watched && pageEpisode.countsTowardProgress
                if !pageEpisode.watched { pageEpisode.viewingCount += 1 }
                pageEpisode.watched = true
                pageEpisode.canTick = false
                tv.episode = pageEpisode
            }
            tv.endChoiceForWatch()
            if counts, let watched = tv.watchedCount { tv.watchedCount = watched + 1 }
            if var pageSeason = tv.season, pageSeason.seasonNumber == season {
                pageSeason.started = true
                pageSeason.status = pageSeason.status ?? "In progress"
                if counts, let watched = pageSeason.watchedCount { pageSeason.watchedCount = watched + 1 }
                tv.season = pageSeason
                state.status = pageSeason.status
            }
            state.adoptShowStatus(of: tv)
            state.tv = tv
        }
    }

    /// An untick shows at once. The status waits for the server, which may restore an earlier choice (TV-208).
    static func tvUntick(season: Int, episode: Int) -> Projection {
        { state in
            let key = TVEpisodeCoordinate(seasonNumber: season, episodeNumber: episode)
            state.tvPending.episodes[key] = false
            guard var tv = state.tv else { return }
            if var pageEpisode = tv.episode, pageEpisode.coordinate == key, pageEpisode.watched {
                if pageEpisode.countsTowardProgress, let watched = tv.watchedCount, watched > 0 {
                    tv.watchedCount = watched - 1
                }
                pageEpisode.watched = false
                pageEpisode.viewingCount = max(0, pageEpisode.viewingCount - 1)
                pageEpisode.canTick = pageEpisode.aired && pageEpisode.listed
                tv.episode = pageEpisode
                state.isTracked = pageEpisode.viewingCount > 0
            }
            state.tv = tv
        }
    }

    /// Watching, Planning, Pause or Drop from the show's + menu (TV-005 to TV-007). Watching on a finished show shows
    /// Completed (A-54).
    static func tvChoice(status: String) -> Projection {
        { state in
            state.isTracked = true
            guard var tv = state.tv else {
                state.status = status
                return
            }
            let shown = status == "In progress" && tv.finished ? "Completed" : status
            tv.inLibrary = true
            tv.choice = status
            tv.status = shown
            tv.selectedAction = TVTrackingPresentation.selectedActionKey(forStatus: shown)
            state.adoptShowStatus(of: tv)
            state.tv = tv
        }
    }

    /// The show eye (`season` nil) or a season eye marks every aired episode under it (TV-300). The show reads
    /// Completed, or Watching when the next episode is mid-season (caught up, TV-202), until the server answers.
    static func tvMarked(season: Int?) -> Projection {
        { state in
            state.isTracked = true
            if let season {
                state.tvPending.markedSeasons.insert(season)
            } else {
                state.tvPending.markedShow = true
            }
            guard var tv = state.tv else { return }
            tv.endChoiceForWatch()
            if let season {
                if var pageSeason = tv.season, pageSeason.seasonNumber == season {
                    if season > 0, let watched = tv.watchedCount, let seasonWatched = pageSeason.watchedCount,
                       let seasonAired = pageSeason.airedCount {
                        tv.watchedCount = watched + max(0, seasonAired - seasonWatched)
                    }
                    pageSeason.started = true
                    pageSeason.watchedCount = pageSeason.airedCount
                    pageSeason.firstGapEpisode = nil
                    pageSeason.eye?.filled = true
                    pageSeason.status = pageSeason.nextAirDate == nil ? "Completed" : "In progress"
                    tv.season = pageSeason
                    state.status = pageSeason.status
                }
            } else {
                let midSeason = (tv.nextAiring?.episodeNumber ?? 1) > 1
                tv.watchedCount = tv.airedCount ?? tv.watchedCount
                tv.upNext = nil
                tv.eye?.filled = true
                tv.finished = !midSeason
                tv.caughtUp = tv.nextAiring != nil
                tv.choice = nil
                tv.status = midSeason ? "In progress" : "Completed"
                tv.selectedAction = midSeason ? "watching" : nil
            }
            state.adoptShowStatus(of: tv)
            state.tv = tv
        }
    }
}
