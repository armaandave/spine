import Foundation

/// What touching and holding a poster offers, worked out from your own state for the title with the rules the title's
/// page and Home's Trending rail already use, so each choice does what the same button does there.
///
/// The top follows where you are with the title: Watched and Planning while it isn't started, + Log in Watched's place
/// while it's in progress. Planning shows only while the title can be planned, as on the Trending rail, and is filled
/// while the title is in Planning. The shelf line shows only for a title in your library.
struct PosterHoldMenu: Equatable {
    enum Quick: Equatable {
        /// The page's eye: marks the title watched (listened, read, played), or, filled, undoes that.
        case watched(word: String, isFilled: Bool)
        /// The bookmark: adds the title to Planning, or, filled, takes it back out.
        case planning(isFilled: Bool)
        /// + Log: the title's log screen, opened on its page.
        case log
    }

    /// The Library shelf the title is on; nil when it isn't in your library.
    let shelf: String?
    let quick: [Quick]
    /// Remove from Library: for a title past Planning whose page offers it (shows, anime, books, manga, comics, games).
    let offersRemoval: Bool

    init(ref: MediaRef, viewer: UserMediaState?) {
        let inLibrary = viewer.map(TrendingRailConfig.isInLibrary) ?? false
        let status = viewer.flatMap { $0.tv?.status ?? $0.anime?.status ?? $0.status }
        shelf = inLibrary ? Self.shelfName(status: status, mediaType: ref.mediaType) : nil

        if Self.matches(status, "In progress") {
            quick = [.log]
        } else {
            let config = viewer.map { TrendingRailConfig.make(title: "", ref: ref, viewer: $0) }
            var quick: [Quick] = [.watched(word: Self.watchedWord(for: ref), isFilled: config?.isEyeSelected ?? false)]
            // Seasons and episodes leave Planning to their show.
            if ref.mediaType != "season", !ref.isEpisode {
                switch config?.lead ?? .plan {
                case .plan: quick.append(.planning(isFilled: false))
                case .planned: quick.append(.planning(isFilled: true))
                case .ring, .track: break
                }
            }
            self.quick = quick
        }

        offersRemoval = inLibrary && !Self.matches(status, "Planning") && Self.pageOffersRemoval(ref: ref, viewer: viewer)
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
    /// game's unless something blocks it. A movie or an album leaves the library from its eye, so it has none.
    private static func pageOffersRemoval(ref: MediaRef, viewer: UserMediaState?) -> Bool {
        guard let viewer else { return false }
        if ref.isAnime, let anime = viewer.anime { return anime.availableActions.contains("remove") }
        if ref.mediaType == "tv", let tv = viewer.tv { return tv.availableActions.contains("remove") }
        if ref.readsLikeBook { return viewer.book?.canRemoveTracking != false }
        if ref.mediaType == "game" { return viewer.game?.canRemoveTracking != false }
        return false
    }

    private static func matches(_ status: String?, _ name: String) -> Bool {
        status?.caseInsensitiveCompare(name) == .orderedSame
    }
}
