import Foundation

// Swift mirror of the TV tracking payloads (implementation plan section 2.3). Every key is new and additive, so a
// payload without them decodes as before (TV-900). Unknown enum strings decode to a fallback and never fail.

// MARK: Enums

nonisolated enum TVEyeTap: String, Codable, Hashable {
    case mark
    case markConfirm = "mark_confirm"
    case unmarkMarks = "unmark_marks"
    case unmarkConfirm = "unmark_confirm"
    case blocked
    case nothingAired = "nothing_aired"
    case unknown

    init(from decoder: Decoder) throws {
        self = Self(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
    }
}

/// A season card's state (TV-200). `unknown` means Spine has no stored episode list: show no count (TV-103).
nonisolated enum TVSeasonProgress: String, Codable, Hashable {
    case watched
    case airing
    case inProgress = "in_progress"
    case notStarted = "not_started"
    case unaired
    case unknown

    init(from decoder: Decoder) throws {
        self = Self(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
    }
}

/// The level of a log, a composer target, a rewatch or a finished write.
nonisolated enum TVLevel: String, Codable, Hashable {
    case show
    case season
    case episode
    case unknown

    init(from decoder: Decoder) throws {
        self = Self(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
    }
}

/// `POST tracking/{source}/tv/{id}/actions/{action}/` (plan section 2.3.7).
nonisolated enum TVAction: String, Codable, Hashable, CaseIterable {
    case watching
    case resume
    case planning
    case pause
    case drop
    case rewatch
    case stopRewatch = "stop_rewatch"
    case remove
    case markWatched = "mark_watched"
    case consume
    case unmark
    case markUpTo = "mark_up_to"
    case markEarlier = "mark_earlier"
    case tick
    case untick
    case rate
}

// MARK: Show

nonisolated struct TVEpisodeCoordinate: Codable, Hashable {
    var seasonNumber: Int
    var episodeNumber: Int

    init(seasonNumber: Int, episodeNumber: Int) {
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
    }
}

/// `up_next`: the next episode to watch (TV-105, TV-701).
nonisolated struct TVEpisodePointer: Codable, Hashable {
    var seasonNumber: Int
    var episodeNumber: Int
    var title: String?
    var airDate: String?
    var isNew: Bool

    init(seasonNumber: Int, episodeNumber: Int, title: String? = nil, airDate: String? = nil, isNew: Bool = false) {
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
        self.title = title
        self.airDate = airDate
        self.isNew = isNew
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        seasonNumber = try c.decode(Int.self, forKey: .seasonNumber)
        episodeNumber = try c.decode(Int.self, forKey: .episodeNumber)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        airDate = try c.decodeIfPresent(String.self, forKey: .airDate)
        isNew = try c.decodeIfPresent(Bool.self, forKey: .isNew) ?? false
    }

    var coordinate: TVEpisodeCoordinate { TVEpisodeCoordinate(seasonNumber: seasonNumber, episodeNumber: episodeNumber) }
}

/// `next_airing` (the next regular episode to air, TV-202) and `premiere` (while nothing regular has aired).
nonisolated struct TVAiring: Codable, Hashable {
    var seasonNumber: Int
    var episodeNumber: Int?
    var airDate: String?

    init(seasonNumber: Int, episodeNumber: Int? = nil, airDate: String? = nil) {
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
        self.airDate = airDate
    }
}

nonisolated struct TVRewatch: Codable, Hashable {
    var scope: TVLevel
    var seasonNumber: Int?
    var startedAt: String

    init(scope: TVLevel, seasonNumber: Int? = nil, startedAt: String) {
        self.scope = scope
        self.seasonNumber = seasonNumber
        self.startedAt = startedAt
    }
}

/// The show and season eyes (plan section 2.3.2). `count` is how many episodes the tap marks or unmarks.
nonisolated struct TVEyeState: Codable, Hashable {
    var filled: Bool
    var tap: TVEyeTap
    var count: Int?
    var prompt: String?
    var message: String?

    init(filled: Bool, tap: TVEyeTap, count: Int? = nil, prompt: String? = nil, message: String? = nil) {
        self.filled = filled
        self.tap = tap
        self.count = count
        self.prompt = prompt
        self.message = message
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filled = try c.decodeIfPresent(Bool.self, forKey: .filled) ?? false
        tap = try c.decodeIfPresent(TVEyeTap.self, forKey: .tap) ?? .unknown
        count = try c.decodeIfPresent(Int.self, forKey: .count)
        prompt = try c.decodeIfPresent(String.self, forKey: .prompt)
        message = try c.decodeIfPresent(String.self, forKey: .message)
    }
}

/// `tv` on a show: the detail variant on pages and in write envelopes, the list variant (keys marked L) in
/// Library rows and primed collections. Detail-only keys are optional, so both variants decode into this type.
nonisolated struct TVShowState: Codable, Hashable {
    var version: Int
    var inLibrary: Bool
    var status: String?
    var choice: String?
    var selectedAction: String?
    var started: Bool
    var finished: Bool
    var caughtUp: Bool
    var watchedCount: Int?
    var airedCount: Int?
    var upNext: TVEpisodePointer?
    var nextAiring: TVAiring?
    var premiere: TVAiring?
    var rewatch: TVRewatch?
    var lastWatchedOn: String?
    var removeCount: Int?
    var ratingSourceDiaryEntryId: Int?
    var likeSourceDiaryEntryId: Int?
    var likeIsIndependent: Bool?
    var eye: TVEyeState?
    var availableActions: [String]
    var actionReasons: [String: String]
    var composer: TVComposer?
    var season: TVSeasonState?
    var episode: TVEpisodeState?

    enum CodingKeys: String, CodingKey {
        case version, inLibrary, status, choice, selectedAction, started, finished, caughtUp, watchedCount, airedCount
        case upNext, nextAiring, premiere, rewatch, lastWatchedOn, removeCount, ratingSourceDiaryEntryId
        case likeSourceDiaryEntryId, likeIsIndependent, eye, availableActions, actionReasons, composer, season, episode
    }

    init(
        version: Int = 0,
        inLibrary: Bool = false,
        status: String? = nil,
        choice: String? = nil,
        selectedAction: String? = nil,
        started: Bool = false,
        finished: Bool = false,
        caughtUp: Bool = false,
        watchedCount: Int? = nil,
        airedCount: Int? = nil,
        upNext: TVEpisodePointer? = nil,
        nextAiring: TVAiring? = nil,
        premiere: TVAiring? = nil,
        rewatch: TVRewatch? = nil,
        lastWatchedOn: String? = nil,
        removeCount: Int? = nil,
        ratingSourceDiaryEntryId: Int? = nil,
        likeSourceDiaryEntryId: Int? = nil,
        likeIsIndependent: Bool? = nil,
        eye: TVEyeState? = nil,
        availableActions: [String] = [],
        actionReasons: [String: String] = [:],
        composer: TVComposer? = nil,
        season: TVSeasonState? = nil,
        episode: TVEpisodeState? = nil
    ) {
        self.version = version
        self.inLibrary = inLibrary
        self.status = status
        self.choice = choice
        self.selectedAction = selectedAction
        self.started = started
        self.finished = finished
        self.caughtUp = caughtUp
        self.watchedCount = watchedCount
        self.airedCount = airedCount
        self.upNext = upNext
        self.nextAiring = nextAiring
        self.premiere = premiere
        self.rewatch = rewatch
        self.lastWatchedOn = lastWatchedOn
        self.removeCount = removeCount
        self.ratingSourceDiaryEntryId = ratingSourceDiaryEntryId
        self.likeSourceDiaryEntryId = likeSourceDiaryEntryId
        self.likeIsIndependent = likeIsIndependent
        self.eye = eye
        self.availableActions = availableActions
        self.actionReasons = actionReasons
        self.composer = composer
        self.season = season
        self.episode = episode
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 0
        inLibrary = try c.decodeIfPresent(Bool.self, forKey: .inLibrary) ?? false
        status = try c.decodeIfPresent(String.self, forKey: .status)
        choice = try c.decodeIfPresent(String.self, forKey: .choice)
        selectedAction = try c.decodeIfPresent(String.self, forKey: .selectedAction)
        started = try c.decodeIfPresent(Bool.self, forKey: .started) ?? false
        finished = try c.decodeIfPresent(Bool.self, forKey: .finished) ?? false
        caughtUp = try c.decodeIfPresent(Bool.self, forKey: .caughtUp) ?? false
        watchedCount = try c.decodeIfPresent(Int.self, forKey: .watchedCount)
        airedCount = try c.decodeIfPresent(Int.self, forKey: .airedCount)
        upNext = try c.decodeIfPresent(TVEpisodePointer.self, forKey: .upNext)
        nextAiring = try c.decodeIfPresent(TVAiring.self, forKey: .nextAiring)
        premiere = try c.decodeIfPresent(TVAiring.self, forKey: .premiere)
        rewatch = try c.decodeIfPresent(TVRewatch.self, forKey: .rewatch)
        lastWatchedOn = try c.decodeIfPresent(String.self, forKey: .lastWatchedOn)
        removeCount = try c.decodeIfPresent(Int.self, forKey: .removeCount)
        ratingSourceDiaryEntryId = try c.decodeIfPresent(Int.self, forKey: .ratingSourceDiaryEntryId)
        likeSourceDiaryEntryId = try c.decodeIfPresent(Int.self, forKey: .likeSourceDiaryEntryId)
        likeIsIndependent = try c.decodeIfPresent(Bool.self, forKey: .likeIsIndependent)
        eye = try c.decodeIfPresent(TVEyeState.self, forKey: .eye)
        availableActions = try c.decodeIfPresent([String].self, forKey: .availableActions) ?? []
        actionReasons = try TVActionReasons.decode(from: c, forKey: .actionReasons)
        composer = try c.decodeIfPresent(TVComposer.self, forKey: .composer)
        season = try c.decodeIfPresent(TVSeasonState.self, forKey: .season)
        episode = try c.decodeIfPresent(TVEpisodeState.self, forKey: .episode)
    }

    /// "S2 E4" for up next.
    var upNextCode: String? {
        upNext.map { TVTrackingPresentation.episodeCode(season: $0.seasonNumber, episode: $0.episodeNumber) }
    }
}

// MARK: Seasons and episodes

/// `tv` on a season: the card variant (keys marked C) in `seasons[].tv` and `tv_unlisted_seasons[].tv`, the
/// detail variant as `user_state.tv.season` on a season page and in write envelopes.
nonisolated struct TVSeasonState: Codable, Hashable {
    var seasonNumber: Int
    var isSpecials: Bool
    var listed: Bool
    var started: Bool
    var status: String?
    var state: TVSeasonProgress
    var watchedCount: Int?
    var airedCount: Int?
    var episodeCount: Int?
    var firstGapEpisode: Int?
    var nextAirDate: String?
    var rewatching: Bool
    var rating: String?
    var liked: Bool
    var ratingSourceDiaryEntryId: Int?
    var likeSourceDiaryEntryId: Int?
    var likeIsIndependent: Bool?
    var logCount: Int?
    var eye: TVEyeState?
    var availableActions: [String]
    var actionReasons: [String: String]

    enum CodingKeys: String, CodingKey {
        case seasonNumber, isSpecials, listed, started, status, state, watchedCount, airedCount, episodeCount
        case firstGapEpisode, nextAirDate, rewatching, rating, liked, ratingSourceDiaryEntryId, likeSourceDiaryEntryId
        case likeIsIndependent, logCount, eye, availableActions, actionReasons
    }

    init(
        seasonNumber: Int,
        isSpecials: Bool? = nil,
        listed: Bool = true,
        started: Bool = false,
        status: String? = nil,
        state: TVSeasonProgress = .notStarted,
        watchedCount: Int? = nil,
        airedCount: Int? = nil,
        episodeCount: Int? = nil,
        firstGapEpisode: Int? = nil,
        nextAirDate: String? = nil,
        rewatching: Bool = false,
        rating: String? = nil,
        liked: Bool = false,
        ratingSourceDiaryEntryId: Int? = nil,
        likeSourceDiaryEntryId: Int? = nil,
        likeIsIndependent: Bool? = nil,
        logCount: Int? = nil,
        eye: TVEyeState? = nil,
        availableActions: [String] = [],
        actionReasons: [String: String] = [:]
    ) {
        self.seasonNumber = seasonNumber
        self.isSpecials = isSpecials ?? (seasonNumber == 0)
        self.listed = listed
        self.started = started
        self.status = status
        self.state = state
        self.watchedCount = watchedCount
        self.airedCount = airedCount
        self.episodeCount = episodeCount
        self.firstGapEpisode = firstGapEpisode
        self.nextAirDate = nextAirDate
        self.rewatching = rewatching
        self.rating = rating
        self.liked = liked
        self.ratingSourceDiaryEntryId = ratingSourceDiaryEntryId
        self.likeSourceDiaryEntryId = likeSourceDiaryEntryId
        self.likeIsIndependent = likeIsIndependent
        self.logCount = logCount
        self.eye = eye
        self.availableActions = availableActions
        self.actionReasons = actionReasons
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        seasonNumber = try c.decode(Int.self, forKey: .seasonNumber)
        isSpecials = try c.decodeIfPresent(Bool.self, forKey: .isSpecials) ?? (seasonNumber == 0)
        listed = try c.decodeIfPresent(Bool.self, forKey: .listed) ?? true
        started = try c.decodeIfPresent(Bool.self, forKey: .started) ?? false
        status = try c.decodeIfPresent(String.self, forKey: .status)
        state = try c.decodeIfPresent(TVSeasonProgress.self, forKey: .state) ?? .unknown
        watchedCount = try c.decodeIfPresent(Int.self, forKey: .watchedCount)
        airedCount = try c.decodeIfPresent(Int.self, forKey: .airedCount)
        episodeCount = try c.decodeIfPresent(Int.self, forKey: .episodeCount)
        firstGapEpisode = try c.decodeIfPresent(Int.self, forKey: .firstGapEpisode)
        nextAirDate = try c.decodeIfPresent(String.self, forKey: .nextAirDate)
        rewatching = try c.decodeIfPresent(Bool.self, forKey: .rewatching) ?? false
        rating = try c.decodeIfPresent(String.self, forKey: .rating)
        liked = try c.decodeIfPresent(Bool.self, forKey: .liked) ?? false
        ratingSourceDiaryEntryId = try c.decodeIfPresent(Int.self, forKey: .ratingSourceDiaryEntryId)
        likeSourceDiaryEntryId = try c.decodeIfPresent(Int.self, forKey: .likeSourceDiaryEntryId)
        likeIsIndependent = try c.decodeIfPresent(Bool.self, forKey: .likeIsIndependent)
        logCount = try c.decodeIfPresent(Int.self, forKey: .logCount)
        eye = try c.decodeIfPresent(TVEyeState.self, forKey: .eye)
        availableActions = try c.decodeIfPresent([String].self, forKey: .availableActions) ?? []
        actionReasons = try TVActionReasons.decode(from: c, forKey: .actionReasons)
    }
}

/// `tv` on an episode: `episodes[].tv`, `tv_unlisted_episodes[].tv` and `user_state.tv.episode`. The actions,
/// reasons and composer come only on the episode page.
nonisolated struct TVEpisodeState: Codable, Hashable {
    var seasonNumber: Int
    var episodeNumber: Int
    var isSpecial: Bool
    var listed: Bool
    var aired: Bool
    var airDate: String?
    var watched: Bool
    var viewingCount: Int
    var logCount: Int
    var canTick: Bool
    var untickViewingId: Int?
    var untickMessage: String?
    var canMarkUpTo: Bool
    var markUpToPrompt: String?
    var userRating: String?
    var liked: Bool
    var isUpNext: Bool
    var availableActions: [String]
    var actionReasons: [String: String]
    var composer: TVComposer?

    enum CodingKeys: String, CodingKey {
        case seasonNumber, episodeNumber, isSpecial, listed, aired, airDate, watched, viewingCount, logCount, canTick
        case untickViewingId, untickMessage, canMarkUpTo, markUpToPrompt, userRating, liked, isUpNext
        case availableActions, actionReasons, composer
    }

    init(
        seasonNumber: Int,
        episodeNumber: Int,
        isSpecial: Bool? = nil,
        listed: Bool = true,
        aired: Bool = true,
        airDate: String? = nil,
        watched: Bool = false,
        viewingCount: Int = 0,
        logCount: Int = 0,
        canTick: Bool? = nil,
        untickViewingId: Int? = nil,
        untickMessage: String? = nil,
        canMarkUpTo: Bool = false,
        markUpToPrompt: String? = nil,
        userRating: String? = nil,
        liked: Bool = false,
        isUpNext: Bool = false,
        availableActions: [String] = [],
        actionReasons: [String: String] = [:],
        composer: TVComposer? = nil
    ) {
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
        self.isSpecial = isSpecial ?? (seasonNumber == 0)
        self.listed = listed
        self.aired = aired
        self.airDate = airDate
        self.watched = watched
        self.viewingCount = viewingCount
        self.logCount = logCount
        self.canTick = canTick ?? (aired && listed && !watched)
        self.untickViewingId = untickViewingId
        self.untickMessage = untickMessage
        self.canMarkUpTo = canMarkUpTo
        self.markUpToPrompt = markUpToPrompt
        self.userRating = userRating
        self.liked = liked
        self.isUpNext = isUpNext
        self.availableActions = availableActions
        self.actionReasons = actionReasons
        self.composer = composer
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        seasonNumber = try c.decode(Int.self, forKey: .seasonNumber)
        episodeNumber = try c.decode(Int.self, forKey: .episodeNumber)
        isSpecial = try c.decodeIfPresent(Bool.self, forKey: .isSpecial) ?? (seasonNumber == 0)
        listed = try c.decodeIfPresent(Bool.self, forKey: .listed) ?? true
        aired = try c.decodeIfPresent(Bool.self, forKey: .aired) ?? false
        airDate = try c.decodeIfPresent(String.self, forKey: .airDate)
        watched = try c.decodeIfPresent(Bool.self, forKey: .watched) ?? false
        viewingCount = try c.decodeIfPresent(Int.self, forKey: .viewingCount) ?? 0
        logCount = try c.decodeIfPresent(Int.self, forKey: .logCount) ?? 0
        canTick = try c.decodeIfPresent(Bool.self, forKey: .canTick) ?? false
        untickViewingId = try c.decodeIfPresent(Int.self, forKey: .untickViewingId)
        untickMessage = try c.decodeIfPresent(String.self, forKey: .untickMessage)
        canMarkUpTo = try c.decodeIfPresent(Bool.self, forKey: .canMarkUpTo) ?? false
        markUpToPrompt = try c.decodeIfPresent(String.self, forKey: .markUpToPrompt)
        userRating = try c.decodeIfPresent(String.self, forKey: .userRating)
        liked = try c.decodeIfPresent(Bool.self, forKey: .liked) ?? false
        isUpNext = try c.decodeIfPresent(Bool.self, forKey: .isUpNext) ?? false
        availableActions = try c.decodeIfPresent([String].self, forKey: .availableActions) ?? []
        actionReasons = try TVActionReasons.decode(from: c, forKey: .actionReasons)
        composer = try c.decodeIfPresent(TVComposer.self, forKey: .composer)
    }

    var coordinate: TVEpisodeCoordinate { TVEpisodeCoordinate(seasonNumber: seasonNumber, episodeNumber: episodeNumber) }

    /// Counts toward progress when watched: aired, listed and regular (TV-103, TV-104).
    var countsTowardProgress: Bool { aired && listed && !isSpecial }
}

/// `tv_unlisted_seasons` on a show page: tracked seasons the provider no longer lists, shown after Specials.
nonisolated struct TVUnlistedSeason: Codable, Hashable, Identifiable {
    var seasonNumber: Int
    var title: String
    var tv: TVSeasonState

    var id: Int { seasonNumber }
}

/// `tv_unlisted_episodes` on a season page: tracked episodes the provider no longer lists, shown last.
nonisolated struct TVUnlistedEpisode: Codable, Hashable, Identifiable {
    var episodeNumber: Int
    var title: String
    var tv: TVEpisodeState

    var id: Int { episodeNumber }
}

// MARK: Composer (TV-404)

nonisolated struct TVComposerTarget: Codable, Hashable, Identifiable {
    var id: String
    var level: TVLevel
    var seasonNumber: Int?
    var episodeNumber: Int?
    var title: String
    var date: String
    var rating: String?
    var liked: Bool
    var isRewatch: Bool
    var claimsViewingId: Int?
    var available: Bool
    var reason: String?

    init(
        id: String,
        level: TVLevel,
        seasonNumber: Int? = nil,
        episodeNumber: Int? = nil,
        title: String,
        date: String,
        rating: String? = nil,
        liked: Bool = false,
        isRewatch: Bool = false,
        claimsViewingId: Int? = nil,
        available: Bool = true,
        reason: String? = nil
    ) {
        self.id = id
        self.level = level
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
        self.title = title
        self.date = date
        self.rating = rating
        self.liked = liked
        self.isRewatch = isRewatch
        self.claimsViewingId = claimsViewingId
        self.available = available
        self.reason = reason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        level = try c.decodeIfPresent(TVLevel.self, forKey: .level) ?? .unknown
        seasonNumber = try c.decodeIfPresent(Int.self, forKey: .seasonNumber)
        episodeNumber = try c.decodeIfPresent(Int.self, forKey: .episodeNumber)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""
        rating = try c.decodeIfPresent(String.self, forKey: .rating)
        liked = try c.decodeIfPresent(Bool.self, forKey: .liked) ?? false
        isRewatch = try c.decodeIfPresent(Bool.self, forKey: .isRewatch) ?? false
        claimsViewingId = try c.decodeIfPresent(Int.self, forKey: .claimsViewingId)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? true
        reason = try c.decodeIfPresent(String.self, forKey: .reason)
    }

    /// The ref a log of this target saves against, under the show's source and id.
    func ref(show: MediaRef) -> MediaRef {
        switch level {
        case .season:
            MediaRef(itemId: nil, source: show.source, mediaType: "season", mediaId: show.mediaId,
                     seasonNumber: seasonNumber, episodeNumber: nil)
        case .episode:
            MediaRef(itemId: nil, source: show.source, mediaType: "episode", mediaId: show.mediaId,
                     seasonNumber: seasonNumber, episodeNumber: episodeNumber)
        case .show, .unknown:
            MediaRef(itemId: nil, source: show.source, mediaType: "tv", mediaId: show.mediaId,
                     seasonNumber: nil, episodeNumber: nil)
        }
    }
}

nonisolated struct TVComposer: Codable, Hashable {
    var defaultTarget: String
    var targets: [TVComposerTarget]

    init(defaultTarget: String, targets: [TVComposerTarget]) {
        self.defaultTarget = defaultTarget
        self.targets = targets
    }

    func target(id: String) -> TVComposerTarget? {
        targets.first { $0.id == id }
    }

    /// The target the composer opens on: `default_target`, else the first target.
    var initialTarget: TVComposerTarget? {
        target(id: defaultTarget) ?? targets.first
    }
}

// MARK: Write envelope

/// `finished` in a write envelope: the season (or show) an app tick or episode log just finished (TV-403).
nonisolated struct TVFinished: Codable, Hashable {
    var level: TVLevel
    var seasonNumber: Int?
    var showFinished: Bool
    var composer: TVComposer

    init(level: TVLevel = .season, seasonNumber: Int?, showFinished: Bool = false, composer: TVComposer) {
        self.level = level
        self.seasonNumber = seasonNumber
        self.showFinished = showFinished
        self.composer = composer
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        level = try c.decodeIfPresent(TVLevel.self, forKey: .level) ?? .season
        seasonNumber = try c.decodeIfPresent(Int.self, forKey: .seasonNumber)
        showFinished = try c.decodeIfPresent(Bool.self, forKey: .showFinished) ?? false
        composer = try c.decode(TVComposer.self, forKey: .composer)
    }
}

/// `earlier_unwatched`: the show's first regular write left earlier aired regular episodes unwatched (TV-107).
nonisolated struct TVEarlierUnwatched: Codable, Hashable {
    var from: TVEpisodeCoordinate
    var to: TVEpisodeCoordinate
    var count: Int
    var seasons: [Int]?
    var question: String
    var offerSeasonNumber: Int?

    init(
        from: TVEpisodeCoordinate,
        to: TVEpisodeCoordinate,
        count: Int,
        seasons: [Int]? = nil,
        question: String,
        offerSeasonNumber: Int? = nil
    ) {
        self.from = from
        self.to = to
        self.count = count
        self.seasons = seasons
        self.question = question
        self.offerSeasonNumber = offerSeasonNumber
    }
}

/// The keys every TV write response adds (plan section 2.3.5).
nonisolated struct TVWriteEnvelope: Codable, Hashable {
    var tv: TVShowState?
    var season: TVSeasonState?
    var episode: TVEpisodeState?
    var changed: Bool
    var finished: TVFinished?
    var earlierUnwatched: TVEarlierUnwatched?
    /// A write from a season page also carries that season's episode states, so its rows show the write at once
    /// (TV-302, TV-705). Nil elsewhere.
    var seasonEpisodes: [TVEpisodeState]?

    static let empty = TVWriteEnvelope()

    init(
        tv: TVShowState? = nil,
        season: TVSeasonState? = nil,
        episode: TVEpisodeState? = nil,
        changed: Bool = false,
        finished: TVFinished? = nil,
        earlierUnwatched: TVEarlierUnwatched? = nil,
        seasonEpisodes: [TVEpisodeState]? = nil
    ) {
        self.tv = tv
        self.season = season
        self.episode = episode
        self.changed = changed
        self.finished = finished
        self.earlierUnwatched = earlierUnwatched
        self.seasonEpisodes = seasonEpisodes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tv = try c.decodeIfPresent(TVShowState.self, forKey: .tv)
        season = try c.decodeIfPresent(TVSeasonState.self, forKey: .season)
        episode = try c.decodeIfPresent(TVEpisodeState.self, forKey: .episode)
        changed = try c.decodeIfPresent(Bool.self, forKey: .changed) ?? false
        finished = try c.decodeIfPresent(TVFinished.self, forKey: .finished)
        earlierUnwatched = try c.decodeIfPresent(TVEarlierUnwatched.self, forKey: .earlierUnwatched)
        seasonEpisodes = try c.decodeIfPresent([TVEpisodeState].self, forKey: .seasonEpisodes)
    }

    /// A diary create or update returns the entry plus `tv`, the log target's `season` and `episode`, `finished`
    /// and `earlier_unwatched` (and `season_episodes` from a season page).
    @MainActor
    init(diaryEntry entry: DiaryEntry) {
        self.init(
            tv: entry.tv,
            season: entry.season,
            episode: entry.episode,
            changed: entry.tv != nil,
            finished: entry.finished,
            earlierUnwatched: entry.earlierUnwatched,
            seasonEpisodes: entry.seasonEpisodes
        )
    }

    /// Whether the write asks anything afterwards: the earlier-episodes question or the season-finish composer.
    var hasFollowUp: Bool { earlierUnwatched != nil || finished != nil }
}

// MARK: Diary

/// `tv_log` on the owner's TV diary entries.
nonisolated struct TVLogInfo: Codable, Hashable {
    var level: TVLevel
    var seasonNumber: Int?
    var episodeNumber: Int?
    var unmarksCount: Int
    var coversCount: Int
    var viewingId: Int?
    var stateVersion: Int

    init(
        level: TVLevel,
        seasonNumber: Int? = nil,
        episodeNumber: Int? = nil,
        unmarksCount: Int = 0,
        coversCount: Int = 0,
        viewingId: Int? = nil,
        stateVersion: Int = 0
    ) {
        self.level = level
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
        self.unmarksCount = unmarksCount
        self.coversCount = coversCount
        self.viewingId = viewingId
        self.stateVersion = stateVersion
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        level = try c.decodeIfPresent(TVLevel.self, forKey: .level) ?? .unknown
        seasonNumber = try c.decodeIfPresent(Int.self, forKey: .seasonNumber)
        episodeNumber = try c.decodeIfPresent(Int.self, forKey: .episodeNumber)
        unmarksCount = try c.decodeIfPresent(Int.self, forKey: .unmarksCount) ?? 0
        coversCount = try c.decodeIfPresent(Int.self, forKey: .coversCount) ?? 0
        viewingId = try c.decodeIfPresent(Int.self, forKey: .viewingId)
        stateVersion = try c.decodeIfPresent(Int.self, forKey: .stateVersion) ?? 0
    }
}

// MARK: Requests and responses

/// The body of a TV action. Nil keys are omitted; `rating` is sent as `null` (to clear the stars) only when
/// `includesRating`, as `TrackingWriteRequest` does.
nonisolated struct TVActionRequest: Encodable, Hashable {
    var seasonNumber: Int?
    var episodeNumber: Int?
    var viewingId: Int?
    var mutationId: UUID?
    var expectedVersion: Int?
    var confirm: Bool?
    var from: TVEpisodeCoordinate?
    var to: TVEpisodeCoordinate?
    var offerSeasonNumber: Int?
    var rating: Int?
    var includesRating: Bool
    /// The season or episode page the action comes from, so the response's `tv` carries that page's state and
    /// composer (TV-404, TV-705). Nil from the show page, Home and Profile.
    var pageSeasonNumber: Int?
    var pageEpisodeNumber: Int?

    init(
        seasonNumber: Int? = nil,
        episodeNumber: Int? = nil,
        viewingId: Int? = nil,
        mutationId: UUID? = nil,
        expectedVersion: Int? = nil,
        confirm: Bool? = nil,
        from: TVEpisodeCoordinate? = nil,
        to: TVEpisodeCoordinate? = nil,
        offerSeasonNumber: Int? = nil,
        rating: Int? = nil,
        includesRating: Bool = false
    ) {
        self.seasonNumber = seasonNumber
        self.episodeNumber = episodeNumber
        self.viewingId = viewingId
        self.mutationId = mutationId
        self.expectedVersion = expectedVersion
        self.confirm = confirm
        self.from = from
        self.to = to
        self.offerSeasonNumber = offerSeasonNumber
        self.rating = rating
        self.includesRating = includesRating
    }

    enum CodingKeys: String, CodingKey {
        case seasonNumber, episodeNumber, viewingId, mutationId, expectedVersion, confirm, from, to
        case offerSeasonNumber, rating, pageSeasonNumber, pageEpisodeNumber
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(seasonNumber, forKey: .seasonNumber)
        try c.encodeIfPresent(episodeNumber, forKey: .episodeNumber)
        try c.encodeIfPresent(viewingId, forKey: .viewingId)
        try c.encodeIfPresent(mutationId, forKey: .mutationId)
        try c.encodeIfPresent(expectedVersion, forKey: .expectedVersion)
        try c.encodeIfPresent(confirm, forKey: .confirm)
        try c.encodeIfPresent(from, forKey: .from)
        try c.encodeIfPresent(to, forKey: .to)
        try c.encodeIfPresent(offerSeasonNumber, forKey: .offerSeasonNumber)
        if includesRating || rating != nil {
            try c.encode(rating, forKey: .rating)
        }
        try c.encodeIfPresent(pageSeasonNumber, forKey: .pageSeasonNumber)
        try c.encodeIfPresent(pageEpisodeNumber, forKey: .pageEpisodeNumber)
    }
}

/// `{tracking, tv, season, episode, changed, finished, earlier_unwatched}` from a TV action. `tracking` is the show's
/// TrackingState, or nil when the show isn't in the library.
struct TVActionResponse: Decodable, Equatable {
    let tracking: TrackingState?
    let envelope: TVWriteEnvelope

    init(tracking: TrackingState?, envelope: TVWriteEnvelope) {
        self.tracking = tracking
        self.envelope = envelope
    }

    private enum CodingKeys: String, CodingKey { case tracking }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tracking = try c.decodeIfPresent(TrackingState.self, forKey: .tracking)
        envelope = try TVWriteEnvelope(from: decoder)
    }

    var tv: TVShowState? { envelope.tv }
    var finished: TVFinished? { envelope.finished }
    var earlierUnwatched: TVEarlierUnwatched? { envelope.earlierUnwatched }
}

// MARK: Errors

extension APIError {
    /// `error.code` from the API's error envelope, for example "tv_state_changed", "tv_log_blocks" or
    /// "tv_action_unavailable" (plan section 2.3.8). TV details being unavailable is "provider_unavailable" with
    /// `error.reason` "tv_details_unavailable" (TV-103).
    var apiErrorCode: String? {
        apiErrorEnvelope?["code"] as? String
    }

    /// `error.message` from the API's error envelope. TV errors carry user copy, including the retryable 503
    /// "TV details are unavailable right now" that the generic description would report as an unreachable server.
    var apiErrorMessage: String? {
        guard let message = apiErrorEnvelope?["message"] as? String, !message.isEmpty else { return nil }
        return message
    }

    private var apiErrorEnvelope: [String: Any]? {
        guard case let .httpStatus(_, body?) = self,
              let data = body.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return json["error"] as? [String: Any]
    }
}

// MARK: Key normalisation

/// `action_reasons` keys are action names ("stop_rewatch"). The app's decoder converts snake_case keys, and a
/// decoder that also rewrites dictionary keys would hand them over as "stopRewatch"; this puts them back, so
/// lookups use the wire names whichever way the decoder treats dictionary keys (plan section 2.3.6).
nonisolated enum TVActionReasons {
    static func decode<Key: CodingKey>(from container: KeyedDecodingContainer<Key>, forKey key: Key) throws -> [String: String] {
        let reasons = try container.decodeIfPresent([String: String].self, forKey: key) ?? [:]
        return restoringSnakeCase(reasons)
    }

    static func restoringSnakeCase(_ reasons: [String: String]) -> [String: String] {
        var restored: [String: String] = [:]
        for (key, value) in reasons {
            restored[snakeCase(key)] = value
        }
        return restored
    }

    static func snakeCase(_ key: String) -> String {
        var result = ""
        for character in key {
            if character.isUppercase {
                result += "_" + character.lowercased()
            } else {
                result.append(character)
            }
        }
        return result
    }
}
