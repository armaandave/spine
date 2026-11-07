import Foundation

/// The one thing the tile's main button does for a title. The plan is a decision, not a write: it says what to ask
/// the page's model for, and `openSheet` means "do not write; open the progress UI", which every plan that would
/// reach or pass the end of the title becomes, so finishing always goes through the screens that know how.
enum QuickActionPlan: Equatable {
    /// Watch the show's or anime's up next episode. `season` is nil for an anime, which has none.
    case tickUpNext(season: Int?, episode: Int)
    /// Add pages to a book read in pages.
    case addPages(Int)
    /// Add percent to a book (+5) or a game (+2) counted in percent.
    case addPercent(Int)
    /// Add chapters to a manga or issues to a comic (`unit` is "chapter" or "issue").
    case addChapters(Int, unit: String)
    /// Add hours to a game counted in hours.
    case addHours(Int)
    /// Write nothing: open the title's progress UI.
    case openSheet(label: String)

    static let bookPages = 20
    static let bookPercent = 5
    static let gamePercent = 2

    /// The button's words: "Watched E8", "Watched S2 E8", "+20 pages", "+5%", "+1 chapter", "+1h", "Update".
    ///
    /// A show's season is named only once the show is known to have more than one. The list row carries no season
    /// count, so up next in season 2 or later (or Specials) is the proof; season 1 reads "Watched E8", as the update
    /// sheet's button does. An anime has no season at all.
    var label: String {
        switch self {
        case let .tickUpNext(season, episode):
            let number = EpisodeTickModel.grouped(episode)
            if let season, season != 1 {
                return "Watched S\(season) E\(number)"
            }
            return "Watched E\(number)"
        case let .addPages(count):
            return "+\(count) \(count == 1 ? "page" : "pages")"
        case let .addPercent(count):
            return "+\(count)%"
        case let .addChapters(count, unit):
            return "+\(count) \(count == 1 ? unit : unit + "s")"
        case let .addHours(count):
            return "+\(count)h"
        case let .openSheet(label):
            return label
        }
    }

    /// True for the plans that write.
    var writes: Bool {
        if case .openSheet = self { return false }
        return true
    }

    private static let update = QuickActionPlan.openSheet(label: "Update")
    private static let inProgress = "In progress"

    /// Nil for a type with no progress to move (movies, music, board games).
    static func make(for item: LibraryItem) -> QuickActionPlan? {
        let tracking = item.tracking
        switch item.media.ref.mediaType {
        case "tv":
            guard let tv = tracking.tv else { return update }
            guard (tv.status ?? tracking.status) == inProgress, let next = tv.upNext else { return update }
            if reachesEnd(watched: tv.watchedCount, aired: tv.airedCount) { return update }
            return .tickUpNext(season: next.seasonNumber, episode: next.episodeNumber)
        case "anime":
            guard let anime = tracking.anime else { return update }
            guard (anime.status ?? tracking.status) == inProgress, let next = anime.upNext else { return update }
            if reachesEnd(watched: anime.watchedCount, aired: anime.airedCount ?? anime.total) { return update }
            return .tickUpNext(season: nil, episode: next.episodeNumber)
        case "book", "manga", "comic":
            guard (tracking.reading?.currentJourney?.status ?? tracking.status) == inProgress,
                  let position = InProgressTilePresenter.position(for: item) else { return update }
            return readingPlan(position)
        case "game":
            guard (tracking.game?.currentPlaythrough?.status ?? tracking.status) == inProgress,
                  let position = InProgressTilePresenter.position(for: item) else { return update }
            return gamePlan(position)
        default:
            return nil
        }
    }

    /// Ticking one more would catch the title up or finish it, which is the sheet's to do.
    private static func reachesEnd(watched: Int?, aired: Int?) -> Bool {
        guard let watched, let aired, aired > 0 else { return false }
        return watched + 1 >= aired
    }

    private static func readingPlan(_ position: TilePosition) -> QuickActionPlan {
        switch position.unit {
        case .pages:
            return step(bookPages, from: position, makes: .addPages(bookPages))
        case .percent:
            return step(bookPercent, from: position, makes: .addPercent(bookPercent))
        case .chapters:
            return step(1, from: position, makes: .addChapters(1, unit: "chapter"))
        case .issues:
            return step(1, from: position, makes: .addChapters(1, unit: "issue"))
        default:
            return update
        }
    }

    private static func gamePlan(_ position: TilePosition) -> QuickActionPlan {
        switch position.unit {
        case .percent:
            return step(gamePercent, from: position, makes: .addPercent(gamePercent))
        case .minutes:
            return .addHours(1)
        default:
            return update
        }
    }

    /// `plan`, unless adding `amount` would reach or pass a known end.
    private static func step(_ amount: Int, from position: TilePosition, makes plan: QuickActionPlan) -> QuickActionPlan {
        if let total = position.total, position.current + Double(amount) >= total { return update }
        return plan
    }
}
