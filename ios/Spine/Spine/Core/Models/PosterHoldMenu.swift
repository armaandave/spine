import Foundation

/// What touching and holding a poster offers, worked out from your own state for the title with the rules the title's
/// page and Home's Trending rail already use, so each choice does what the same button does there.
///
/// The top follows where you are with the title: the eye (to mark it watched) and Planning while you haven't finished
/// it, and no row at all once it's in progress or finished, just the shelf line. Planning shows only while the title can
/// be planned, as on the title page, and is filled while the title is in Planning. The shelf line shows only for a
/// title in your library.
struct PosterHoldMenu: Equatable {
    enum Quick: Equatable {
        /// The page's eye: marks the title watched (listened, read, played). A finished title has no eye.
        case watched(word: String)
        /// The bookmark: adds the title to Planning, or, filled, takes it back out.
        case planning(isFilled: Bool)
    }

    /// The Library shelf the title is on; nil when it isn't in your library.
    let shelf: String?
    let quick: [Quick]
    /// Remove from Library: for a title past Planning whose page offers it (shows, anime, books, manga, comics, games).
    let offersRemoval: Bool
    /// The eye writes at once, so the menu stays open and the eye flips where you tapped it. Otherwise the tap opens the
    /// title's log or asks first (a show or anime marks every aired episode), and the menu closes.
    let eyeStaysOpen: Bool

    init(ref: MediaRef, viewer: UserMediaState?) {
        let inLibrary = viewer.map(TrendingRailConfig.isInLibrary) ?? false
        let status = viewer.flatMap { $0.tv?.status ?? $0.anime?.status ?? $0.status }
        shelf = inLibrary ? Self.shelfName(status: status, mediaType: ref.mediaType) : nil

        let isWatched = viewer.map { TrendingRailConfig.make(title: "", ref: ref, viewer: $0).isEyeSelected } ?? false
        if Self.matches(status, "In progress") || isWatched {
            // Started or finished: no Planning, and no eye. The shelf line says where you are.
            quick = []
            eyeStaysOpen = false
        } else {
            var quick: [Quick] = [.watched(word: Self.watchedWord(for: ref))]
            switch Self.planning(ref: ref, viewer: viewer) {
            case .add: quick.append(.planning(isFilled: false))
            case .remove: quick.append(.planning(isFilled: true))
            case .hidden: break
            }
            self.quick = quick
            eyeStaysOpen = Self.eyeWritesAtOnce(ref: ref, viewer: viewer)
        }

        offersRemoval = inLibrary && !Self.matches(status, "Planning") && Self.pageOffersRemoval(ref: ref, viewer: viewer)
    }

    /// Whether marking the title watched is a write by itself, the way the page's eye routes it (`TrendingRailRouter.eye`).
    static func eyeWritesAtOnce(ref: MediaRef, viewer: UserMediaState?) -> Bool {
        // A show's or anime's eye marks every aired episode and can ask first.
        if ref.isTVFamily || ref.isAnime { return false }
        if ref.isSingleWeight { return true }
        // A game played right now finishes in its log.
        if ref.mediaType == "game" { return viewer?.game?.hasLivePlaythrough != true }
        if ref.readsLikeBook {
            switch ReadingRouting.eyeTap(book: viewer?.book, isRead: false, noun: ref.mediaType) {
            case .markRead, .undoRead: return true
            case .openLog, .explain: return false
            }
        }
        return false
    }

    /// The page's Planning bookmark (`PlanningBookmark`): seasons and episodes leave it to their show. A list's copy of
    /// a show sends no actions, and a show that isn't in your library can always be planned (TV-006).
    static func planning(ref: MediaRef, viewer: UserMediaState?) -> PlanningBookmark {
        let bookmark = PlanningBookmark.state(for: ref, viewer: viewer)
        let inLibrary = viewer.map(TrendingRailConfig.isInLibrary) ?? false
        if bookmark == .hidden, !inLibrary, ref.mediaType != "season", !ref.isEpisode { return .add }
        return bookmark
    }

    /// The eye's word, short enough for the row of buttons at the top of the menu.
    static func watchedWord(for ref: MediaRef) -> String {
        if ref.mediaType == "music" { return "Listened" }
        if ref.readsLikeBook { return "Read" }
        if ref.mediaType == "game" { return "Played" }
        return "Watched"
    }

    /// Library's own name for the shelf a status puts the title on. Types without status shelves (movies, albums) use
    /// the show words: Watching, Planning, Paused, Dropped, Completed.
    static func shelfName(status: String?, mediaType: String) -> String? {
        let shelf: LibraryShelf
        if matches(status, "Planning") {
            shelf = .planning
        } else if matches(status, "In progress") {
            shelf = .currentlyReading
        } else if matches(status, "Paused") {
            shelf = .paused
        } else if matches(status, "Dropped") {
            shelf = .didNotFinish
        } else if matches(status, "Completed") {
            shelf = .read
        } else {
            return nil
        }
        return shelf.title(mediaType: LibraryShelf.usesStatusShelves(mediaType) ? mediaType : "tv")
    }

    /// The pages that offer Remove from Library: a show's or anime's + menu when the server lists it, and a book's or
    /// game's unless something blocks it. A movie or an album leaves the library from its eye, so it has none. A list's
    /// copy of a show leaves its actions out: then it's offered, and the choice checks with the title before asking.
    private static func pageOffersRemoval(ref: MediaRef, viewer: UserMediaState?) -> Bool {
        guard let viewer else { return false }
        if ref.isAnime { return offers("remove", in: viewer.anime?.availableActions) }
        if ref.mediaType == "tv" { return offers("remove", in: viewer.tv?.availableActions) }
        if ref.readsLikeBook { return viewer.book?.canRemoveTracking != false }
        if ref.mediaType == "game" { return viewer.game?.canRemoveTracking != false }
        return false
    }

    /// An action the server listed, or any action when it sent no list at all.
    private static func offers(_ action: String, in actions: [String]?) -> Bool {
        guard let actions, !actions.isEmpty else { return true }
        return actions.contains(action)
    }

    private static func matches(_ status: String?, _ name: String) -> Bool {
        status?.caseInsensitiveCompare(name) == .orderedSame
    }
}
