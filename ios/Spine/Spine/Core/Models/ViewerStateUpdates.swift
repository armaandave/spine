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
        state.directConsumption = nil
        state.ratingSourceDiaryEntryId = nil
        state.likeSourceDiaryEntryId = nil
        state.likeIsIndependent = nil
        state.hasLiked = false
        return state
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
}
