import Foundation
import Observation

/// One poster in a tile: an In progress title or a Trending one. `id` is the title's own id, which is what focus is
/// kept by.
struct TilePoster: Identifiable, Equatable {
    let id: String
    let media: MediaSummary
}

/// What a closed tile shows.
enum TileFace: Equatable {
    /// The first load is still on its way.
    case loading
    /// Titles to show; the tile opens.
    case items
    /// Nothing to show, with a way to get something: the whole tile is that button.
    case offer(String)
    /// Nothing to show and nothing to do; the line goes where the count would.
    case message(String)
}

/// The data the tiles show, written by `HomeViewModel` and read only inside the tiles' own views, so a change here
/// redraws the tiles and never the page. Every property is plain data: the model decides nothing.
@MainActor
@Observable
final class HomeTilesModel {
    // MARK: In progress

    /// The viewer's In progress rail, as loaded (ten at most).
    var inProgress: [LibraryItem] = []
    var isLoadingInProgress = false
    var inProgressError: String?
    /// What was done today per title (`HomeTodayStore.titleKey`).
    var todayParts: [String: TodayPart] = [:]
    /// Where each In progress title stood when the rail loaded, in its tile unit, by item id. Gains since then are lit.
    var baselines: [String: Double] = [:]
    /// Each show's current season once read, by item id: a show's panel counts its season.
    var seasons: [String: TileSeason] = [:]
    /// Item ids with a quick action on its way: their main button waits.
    var busyItemIDs: Set<String> = []

    // MARK: Trending

    var trending: [TrendingTitle] = []
    /// "This week", "Lately" or "Popular".
    var trendingSubtitle = "Popular"
    var isLoadingTrending = false
    /// The viewer follows nobody, so an empty Trending can offer to find people.
    var followsNobody = false
    /// Trending titles (by `TrendingTitle.key`) that are in the viewer's library as Planning.
    var plannedIDs: Set<String> = []

    // MARK: Posters

    var inProgressPosters: [TilePoster] {
        inProgress.prefix(TileGeometry.maxPosters).map { TilePoster(id: $0.id, media: $0.media) }
    }

    var trendingPosters: [TilePoster] {
        trending.prefix(TileGeometry.maxPosters).map { TilePoster(id: $0.id, media: $0.media) }
    }

    func posters(for tile: HomeTileID) -> [TilePoster] {
        switch tile {
        case .inProgress: inProgressPosters
        case .trending: trendingPosters
        }
    }

    func ids(for tile: HomeTileID) -> [String] {
        posters(for: tile).map(\.id)
    }

    func media(for tile: HomeTileID, id: String) -> MediaSummary? {
        posters(for: tile).first { $0.id == id }?.media
    }

    func item(id: String) -> LibraryItem? {
        inProgress.first { $0.id == id }
    }

    func trendingTitle(id: String) -> TrendingTitle? {
        trending.first { $0.id == id }
    }

    // MARK: Faces

    func face(for tile: HomeTileID) -> TileFace {
        switch tile {
        case .inProgress:
            if !inProgress.isEmpty { return .items }
            if isLoadingInProgress { return .loading }
            if inProgressError != nil { return .message("Couldn\u{2019}t load") }
            return .offer("Find something")
        case .trending:
            if !trending.isEmpty { return .items }
            if isLoadingTrending { return .loading }
            return followsNobody ? .offer("Find people") : .message("Nothing yet")
        }
    }

    /// The closed tile's second line: the count, or the empty state's words.
    func subtitle(for tile: HomeTileID) -> String {
        switch face(for: tile) {
        case .items:
            switch tile {
            case .inProgress: InProgressTilePresenter.subtitle(count: inProgressPosters.count)
            case .trending: trendingSubtitle
            }
        case .loading:
            ""
        case let .offer(words), let .message(words):
            words
        }
    }

    /// "Open In progress, 5 titles": a closed tile's VoiceOver label.
    func openLabel(for tile: HomeTileID) -> String {
        let count = posters(for: tile).count
        let titles = InProgressTilePresenter.subtitle(count: count)
        switch tile {
        case .inProgress: return "Open \(tile.title), \(titles)"
        case .trending: return "Open \(tile.title), \(titles), \(trendingSubtitle)"
        }
    }

    // MARK: Panels

    /// The In progress panel's words, meter and buttons for `item`.
    func detail(for item: LibraryItem) -> TileDetailModel {
        InProgressTilePresenter.detail(
            for: item,
            today: todayParts[HomeTodayStore.titleKey(for: item.media.ref)],
            baseline: baselines[item.id],
            season: seasons[item.id]
        )
    }

    func trendingDetail(for title: TrendingTitle) -> TrendingTileDetail {
        TrendingTileModel.detail(for: title, planned: plannedIDs.contains(title.key))
    }
}
