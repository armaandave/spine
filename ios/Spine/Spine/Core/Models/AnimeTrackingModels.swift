import Foundation

// Swift mirror of the anime payloads (docs/anime-tracking-coverage.md, "App payload"). An anime title works like one TV
// season with no show around it (AN-000), so its writes, follow-ups and rows reuse TV's types: every key here is new
// and additive, and an episode's coordinate is season `AnimeRows.seasonNumber`.

/// An anime value on a payload shared with other things: a page, a list, a write's answer. One that can't be read decodes
/// as absent, so it loses only itself and never what it came with.
@propertyWrapper
nonisolated struct LossyAnimeValue<Value: Codable & Hashable>: Codable, Hashable {
    var wrappedValue: Value?

    init(wrappedValue: Value? = nil) {
        self.wrappedValue = wrappedValue
    }

    init(from decoder: Decoder) throws {
        wrappedValue = try? Value(from: decoder)
    }

    func encode(to encoder: Encoder) throws {
        try wrappedValue?.encode(to: encoder)
    }
}

nonisolated extension KeyedDecodingContainer {
    /// The value at `key`, nil when it's missing or can't be read.
    func decodeLossily<Value: Codable & Hashable>(_ type: Value.Type, forKey key: Key) -> Value? {
        ((try? decodeIfPresent(LossyAnimeValue<Value>.self, forKey: key)) ?? nil)?.wrappedValue
    }

    /// A missing key is an absent value, which a synthesized decoder asks for here.
    func decode<Value>(_ type: LossyAnimeValue<Value>.Type, forKey key: Key) throws -> LossyAnimeValue<Value> {
        LossyAnimeValue(wrappedValue: decodeLossily(Value.self, forKey: key))
    }
}

nonisolated extension KeyedEncodingContainer {
    /// An absent value leaves its key out, as a plain optional does.
    mutating func encode<Value>(_ value: LossyAnimeValue<Value>, forKey key: Key) throws {
        try encodeIfPresent(value.wrappedValue, forKey: key)
    }
}

/// `up_next`: the next episode to watch (TV-105, AN-701).
nonisolated struct AnimeEpisodePointer: Codable, Hashable {
    var episodeNumber: Int
    var title: String?
    var imageUrl: String?
    var airDate: String?
    var isNew: Bool

    init(episodeNumber: Int, title: String? = nil, imageUrl: String? = nil, airDate: String? = nil, isNew: Bool = false) {
        self.episodeNumber = episodeNumber
        self.title = title
        self.imageUrl = imageUrl
        self.airDate = airDate
        self.isNew = isNew
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        episodeNumber = try c.decode(Int.self, forKey: .episodeNumber)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        imageUrl = try c.decodeIfPresent(String.self, forKey: .imageUrl)
        airDate = try c.decodeIfPresent(String.self, forKey: .airDate)
        isNew = try c.decodeIfPresent(Bool.self, forKey: .isNew) ?? false
    }

    /// Up next as TV's rows and writes take it.
    var tvPointer: TVEpisodePointer {
        TVEpisodePointer(
            seasonNumber: AnimeRows.seasonNumber,
            episodeNumber: episodeNumber,
            title: title,
            airDate: airDate,
            isNew: isNew,
            imageUrl: imageUrl
        )
    }
}

/// `next_airing` (AN-201) and `premiere` (AN-700).
nonisolated struct AnimeAiring: Codable, Hashable {
    var episodeNumber: Int?
    var airDate: String?

    init(episodeNumber: Int? = nil, airDate: String? = nil) {
        self.episodeNumber = episodeNumber
        self.airDate = airDate
    }
}

/// `rewatch`, during a rewatch (AN-600).
nonisolated struct AnimeRewatch: Codable, Hashable {
    var startedAt: String?
    var passNumber: Int?

    init(startedAt: String? = nil, passNumber: Int? = nil) {
        self.startedAt = startedAt
        self.passNumber = passNumber
    }
}

/// `next_title`: the title after this one in MAL's order, once this one is finished (AN-500).
nonisolated struct AnimeNextTitle: Codable, Hashable {
    var source: String
    var mediaId: String
    var title: String
    var imageUrl: String?
    var hasAired: Bool
    /// A movie or a one-episode title: it has no rows, so Home's sheet doesn't move on to it (AN-501).
    var single: Bool

    init(
        source: String = "mal",
        mediaId: String,
        title: String,
        imageUrl: String? = nil,
        hasAired: Bool = false,
        single: Bool = false
    ) {
        self.source = source
        self.mediaId = mediaId
        self.title = title
        self.imageUrl = imageUrl
        self.hasAired = hasAired
        self.single = single
    }

    private enum CodingKeys: String, CodingKey {
        case source, mediaId, title, imageUrl, hasAired, single
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "mal"
        if let number = try? c.decode(Int.self, forKey: .mediaId) {
            mediaId = String(number)
        } else {
            mediaId = try c.decode(String.self, forKey: .mediaId)
        }
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        imageUrl = try c.decodeIfPresent(String.self, forKey: .imageUrl)
        hasAired = try c.decodeIfPresent(Bool.self, forKey: .hasAired) ?? false
        single = try c.decodeIfPresent(Bool.self, forKey: .single) ?? false
    }

    var ref: MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: "anime", mediaId: mediaId, seasonNumber: nil, episodeNumber: nil)
    }
}

/// `anime` on a title: the detail variant on pages, the rows route and in write envelopes, the list variant in
/// TrackingState and media summaries, from stored values. Detail-only keys are optional, so both decode into this type.
nonisolated struct AnimeState: Codable, Hashable {
    var version: Int
    var inLibrary: Bool
    var status: String?
    var choice: String?
    var started: Bool
    var finished: Bool
    var caughtUp: Bool
    /// A movie or a one-episode title (AN-002). The list variant leaves it out, and a single is never partway there.
    var single: Bool
    var total: Int?
    var watchedCount: Int?
    /// Nil when Spine doesn't know how many have aired: the title reads "7 episodes" (AN-102).
    var airedCount: Int?
    var firstGapEpisode: Int?
    var upNext: AnimeEpisodePointer?
    var nextAiring: AnimeAiring?
    var premiere: AnimeAiring?
    var rewatch: AnimeRewatch?
    var lastWatchedOn: String?
    var selectedAction: String?
    var removeCount: Int?
    var ratingSourceDiaryEntryId: Int?
    var likeSourceDiaryEntryId: Int?
    var likeIsIndependent: Bool?
    /// Whether the heart is on. The detail variant has it; a TrackingState doesn't (AN-301).
    var liked: Bool?
    var eye: TVEyeState?
    var availableActions: [String]
    var actionReasons: [String: String]
    var opinionAction: OpinionAction?
    var composer: TVComposer?
    var nextTitle: AnimeNextTitle?

    enum CodingKeys: String, CodingKey {
        case version, inLibrary, status, choice, started, finished, caughtUp, single, total, watchedCount, airedCount
        case firstGapEpisode, upNext, nextAiring, premiere, rewatch, lastWatchedOn, selectedAction, removeCount
        case ratingSourceDiaryEntryId, likeSourceDiaryEntryId, likeIsIndependent, liked, eye, availableActions, actionReasons
        case opinionAction, composer, nextTitle
    }

    init(
        version: Int = 0,
        inLibrary: Bool = false,
        status: String? = nil,
        choice: String? = nil,
        started: Bool = false,
        finished: Bool = false,
        caughtUp: Bool = false,
        single: Bool = false,
        total: Int? = nil,
        watchedCount: Int? = nil,
        airedCount: Int? = nil,
        firstGapEpisode: Int? = nil,
        upNext: AnimeEpisodePointer? = nil,
        nextAiring: AnimeAiring? = nil,
        premiere: AnimeAiring? = nil,
        rewatch: AnimeRewatch? = nil,
        lastWatchedOn: String? = nil,
        selectedAction: String? = nil,
        removeCount: Int? = nil,
        ratingSourceDiaryEntryId: Int? = nil,
        likeSourceDiaryEntryId: Int? = nil,
        likeIsIndependent: Bool? = nil,
        liked: Bool? = nil,
        eye: TVEyeState? = nil,
        availableActions: [String] = [],
        actionReasons: [String: String] = [:],
        opinionAction: OpinionAction? = nil,
        composer: TVComposer? = nil,
        nextTitle: AnimeNextTitle? = nil
    ) {
        self.version = version
        self.inLibrary = inLibrary
        self.status = status
        self.choice = choice
        self.started = started
        self.finished = finished
        self.caughtUp = caughtUp
        self.single = single
        self.total = total
        self.watchedCount = watchedCount
        self.airedCount = airedCount
        self.firstGapEpisode = firstGapEpisode
        self.upNext = upNext
        self.nextAiring = nextAiring
        self.premiere = premiere
        self.rewatch = rewatch
        self.lastWatchedOn = lastWatchedOn
        self.selectedAction = selectedAction
        self.removeCount = removeCount
        self.ratingSourceDiaryEntryId = ratingSourceDiaryEntryId
        self.likeSourceDiaryEntryId = likeSourceDiaryEntryId
        self.likeIsIndependent = likeIsIndependent
        self.liked = liked
        self.eye = eye
        self.availableActions = availableActions
        self.actionReasons = actionReasons
        self.opinionAction = opinionAction
        self.composer = composer
        self.nextTitle = nextTitle
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 0
        inLibrary = try c.decodeIfPresent(Bool.self, forKey: .inLibrary) ?? false
        status = try c.decodeIfPresent(String.self, forKey: .status)
        choice = try c.decodeIfPresent(String.self, forKey: .choice)
        started = try c.decodeIfPresent(Bool.self, forKey: .started) ?? false
        finished = try c.decodeIfPresent(Bool.self, forKey: .finished) ?? false
        caughtUp = try c.decodeIfPresent(Bool.self, forKey: .caughtUp) ?? false
        single = try c.decodeIfPresent(Bool.self, forKey: .single) ?? false
        total = try c.decodeIfPresent(Int.self, forKey: .total)
        watchedCount = try c.decodeIfPresent(Int.self, forKey: .watchedCount)
        airedCount = try c.decodeIfPresent(Int.self, forKey: .airedCount)
        firstGapEpisode = try c.decodeIfPresent(Int.self, forKey: .firstGapEpisode)
        upNext = try c.decodeIfPresent(AnimeEpisodePointer.self, forKey: .upNext)
        nextAiring = try c.decodeIfPresent(AnimeAiring.self, forKey: .nextAiring)
        premiere = try c.decodeIfPresent(AnimeAiring.self, forKey: .premiere)
        rewatch = try c.decodeIfPresent(AnimeRewatch.self, forKey: .rewatch)
        lastWatchedOn = try c.decodeIfPresent(String.self, forKey: .lastWatchedOn)
        selectedAction = try c.decodeIfPresent(String.self, forKey: .selectedAction)
        removeCount = try c.decodeIfPresent(Int.self, forKey: .removeCount)
        ratingSourceDiaryEntryId = try c.decodeIfPresent(Int.self, forKey: .ratingSourceDiaryEntryId)
        likeSourceDiaryEntryId = try c.decodeIfPresent(Int.self, forKey: .likeSourceDiaryEntryId)
        likeIsIndependent = try c.decodeIfPresent(Bool.self, forKey: .likeIsIndependent)
        liked = try c.decodeIfPresent(Bool.self, forKey: .liked)
        eye = try c.decodeIfPresent(TVEyeState.self, forKey: .eye)
        availableActions = try c.decodeIfPresent([String].self, forKey: .availableActions) ?? []
        actionReasons = try TVActionReasons.decode(from: c, forKey: .actionReasons)
        opinionAction = try c.decodeIfPresent(OpinionAction.self, forKey: .opinionAction)
        composer = try c.decodeIfPresent(TVComposer.self, forKey: .composer)
        nextTitle = try c.decodeIfPresent(AnimeNextTitle.self, forKey: .nextTitle)
    }
}

/// One compact row of a title's episode list (AN-101). Keys are short because One Piece has over 1,100 rows, and a key
/// is left out when it has nothing to say.
nonisolated struct AnimeRow: Codable, Hashable, Identifiable {
    var number: Int
    var title: String?
    var imageUrl: String?
    /// 1 once it has aired: the row shows a tick.
    var aired: Int?
    /// An unaired row's air date. With neither this nor `aired` it reads "TBA".
    var airDate: String?
    /// 1 when it is watched in the current pass.
    var watched: Int?
    /// The viewing an untick removes.
    var viewingId: Int?
    /// 1 when a log covers it: an untick says `log_message` and sends nothing.
    var logged: Int?

    var id: Int { number }
    var hasAired: Bool { aired == 1 }
    var isWatched: Bool { watched == 1 }
    var isLogged: Bool { logged == 1 }

    enum CodingKeys: String, CodingKey {
        case number = "n"
        case title = "t"
        case imageUrl = "i"
        case aired = "a"
        case airDate = "d"
        case watched = "w"
        case viewingId = "u"
        case logged = "l"
    }

    init(
        number: Int,
        title: String? = nil,
        imageUrl: String? = nil,
        aired: Int? = nil,
        airDate: String? = nil,
        watched: Int? = nil,
        viewingId: Int? = nil,
        logged: Int? = nil
    ) {
        self.number = number
        self.title = title
        self.imageUrl = imageUrl
        self.aired = aired
        self.airDate = airDate
        self.watched = watched
        self.viewingId = viewingId
        self.logged = logged
    }

    /// "Episode 5" when AniList doesn't list a title (AN-101).
    var displayTitle: String {
        let name = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return name.isEmpty ? "Episode \(number)" : name
    }
}

/// `anime_rows`, the rows route's `rows` and a write's `rows`: one row per aired episode, then the unaired ones Spine
/// knows of (AN-101).
nonisolated struct AnimeRows: Codable, Hashable {
    /// The season an anime episode's coordinate names: a title works like one season (AN-000).
    static let seasonNumber = 1

    var count: Int
    /// Where the list opens.
    var upNext: Int?
    /// Starts the "Mark E1–E4 watched?" prompt (TV-108).
    var firstUnwatched: Int?
    var logMessage: String?
    var episodes: [AnimeRow]

    init(
        count: Int? = nil,
        upNext: Int? = nil,
        firstUnwatched: Int? = nil,
        logMessage: String? = nil,
        episodes: [AnimeRow] = []
    ) {
        self.count = count ?? episodes.count
        self.upNext = upNext
        self.firstUnwatched = firstUnwatched
        self.logMessage = logMessage
        self.episodes = episodes
    }

    private enum CodingKeys: String, CodingKey {
        case count, upNext, firstUnwatched, logMessage, episodes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // A row that can't be read loses only itself (AN-101).
        episodes = try c.decodeIfPresent([LossyAnimeValue<AnimeRow>].self, forKey: .episodes)?.compactMap(\.wrappedValue) ?? []
        count = try c.decodeIfPresent(Int.self, forKey: .count) ?? episodes.count
        upNext = try c.decodeIfPresent(Int.self, forKey: .upNext)
        firstUnwatched = try c.decodeIfPresent(Int.self, forKey: .firstUnwatched)
        logMessage = try c.decodeIfPresent(String.self, forKey: .logMessage)
    }

    /// A write that names one row answers only that row: it replaces its row where it is, and up next and the first
    /// unwatched follow the title's new state.
    mutating func apply(_ row: AnimeRow, state: AnimeState?) {
        if let index = episodes.firstIndex(where: { $0.number == row.number }) {
            episodes[index] = row
        }
        upNext = state?.upNext?.episodeNumber
        firstUnwatched = episodes.first { $0.hasAired && !$0.isWatched }?.number
    }

    /// The index of the row the list opens on: up next's, or with nothing up next the last aired row.
    var startIndex: Int {
        if let upNext, let index = episodes.firstIndex(where: { $0.number == upNext }) { return index }
        return episodes.lastIndex(where: \.hasAired) ?? 0
    }

    /// More rows than a page lists: the rest are in the sheet (AN-101).
    var isLong: Bool { episodes.count > Self.pageRowLimit }

    /// A page lists every row of a title up to this long, as a season page does.
    static let pageRowLimit = 50
    /// A long title's page lists this many rows from up next on.
    static let longPageRows = 10

    /// The rows a page lists when a long title's start at `start`.
    func pageRange(start: Int) -> Range<Int> {
        guard isLong else { return 0..<episodes.count }
        let lower = max(0, min(start, episodes.count - Self.longPageRows))
        return lower..<(lower + Self.longPageRows)
    }

    /// The episode the list opens on.
    var startEpisode: Int? {
        episodes.indices.contains(startIndex) ? episodes[startIndex].number : nil
    }

    /// A row as TV's episode rows take it.
    @MainActor
    func rowModel(at index: Int) -> TVEpisodeRowModel {
        rowModel(episodes[index])
    }

    @MainActor
    func rowModel(_ row: AnimeRow) -> TVEpisodeRowModel {
        let tv = state(of: row)
        let title = row.displayTitle
        return TVEpisodeRowModel(
            episodeNumber: row.number,
            title: title,
            summary: EpisodeSummary(
                episodeNumber: row.number,
                title: title,
                overview: nil,
                airDate: nil,
                runtime: nil,
                imageUrl: row.imageUrl,
                rating: nil,
                tv: tv
            ),
            tv: tv
        )
    }

    /// A row's state as TV's writes and rows read it. Touch and hold offers Mark watched up to here on an aired row
    /// with something unwatched up to it, as TV's does (TV-108).
    func state(of row: AnimeRow) -> TVEpisodeState {
        var prompt: String?
        if row.hasAired, let first = firstUnwatched, first <= row.number {
            prompt = first == row.number ? "Mark E\(first) watched?" : "Mark E\(first)–E\(row.number) watched?"
        }
        return TVEpisodeState(
            seasonNumber: Self.seasonNumber,
            episodeNumber: row.number,
            aired: row.hasAired,
            airDate: row.airDate,
            watched: row.isWatched,
            viewingCount: row.isWatched ? 1 : 0,
            canTick: row.hasAired && !row.isWatched,
            untickViewingId: row.viewingId,
            untickMessage: row.isLogged ? logMessage ?? "Delete your log first." : nil,
            canMarkUpTo: row.hasAired,
            markUpToPrompt: prompt,
            isUpNext: row.number == upNext
        )
    }

    /// An episode's coordinate, as TV's write queue and pending watches name it.
    static func coordinate(_ episodeNumber: Int) -> TVEpisodeCoordinate {
        TVEpisodeCoordinate(seasonNumber: seasonNumber, episodeNumber: episodeNumber)
    }
}

/// `{"anime": state, "rows": rows}` from `GET tracking/{source}/anime/{id}/rows/`: what Home's sheet reads (AN-701).
nonisolated struct AnimeRowsResponse: Decodable, Hashable {
    let anime: AnimeState
    let rows: AnimeRows
}
