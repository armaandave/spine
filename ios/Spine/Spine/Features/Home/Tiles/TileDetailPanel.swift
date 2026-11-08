import SwiftUI

/// What an open tile's buttons do. The section is handed these by the page; the tiles write nothing themselves.
struct HomeTilesActions {
    /// A poster in the In progress carousel was tapped: open the title's page.
    var onOpenTitle: (MediaRef) -> Void = { _ in }
    /// "Update" (or "Open" for a title whose page is its progress UI): the title's own progress UI.
    var onUpdate: (LibraryItem) -> Void = { _ in }
    /// The main button's write (an episode, 20 pages, 5 percent). Returns whether it was saved, which picks the
    /// success or the error haptic. A plan that would reach the end of the title never gets here: the section opens
    /// the progress UI for it through `onUpdate`.
    var onQuickAction: (LibraryItem) async -> Bool = { _ in true }
    /// A poster in the Trending carousel was tapped, or the rail's Open: the title's page. (A face opens that person's profile by its own link.)
    var onOpenTrending: (TrendingTitle) -> Void = { _ in }
    /// A Trending rail tap that needs the title's own screen (a log composer, a sheet): the page opens and finishes it.
    var onRailHandoff: (TrendingTitle, RailHandoff) -> Void = { _, _ in }
    /// Something a Trending rail write has to say (a notice, a failure): Home's alert shows it.
    var onRailMessage: (String) -> Void = { _ in }
    /// A tile has closed: the page re-sorts what a Trending rail write changed.
    var onTileClosed: (HomeTileID) -> Void = { _ in }
    /// The empty In progress tile's button.
    var onFindSomething: () -> Void = {}
    /// The empty Trending tile's button, shown when you follow nobody.
    var onFindPeople: () -> Void = {}
}

/// The panel at the foot of an open tile: the focused title's name and where it stands, its meter, and two buttons.
/// Trending reads the focused id here and nowhere above, so swiping redraws this panel and the posters, not the page.
struct TileDetailPanel: View {
    let tile: HomeTileID
    let model: HomeTilesModel
    let state: HomeTilesState
    let actions: HomeTilesActions

    var body: some View {
        switch tile {
        case .inProgress:
            // One panel for the whole tile: it follows the carousel's scroll and morphs between titles, so it has no
            // identity per title and never reads the focus here.
            InProgressMorphPanel(
                model: model,
                state: state,
                actions: actions,
                quickID: "in-progress.quick",
                updateID: "in-progress.update"
            )
        case .trending:
            if let id = state.focusID, let title = model.trendingTitle(id: id) {
                // No `.id` on the panel: the rail's glass stays put as you swipe; its own title block is keyed.
                TrendingPanel(
                    title: title,
                    detail: model.trendingDetail(for: title),
                    neighbors: model.trendingNeighbors(of: title),
                    store: model.railStore,
                    actions: actions,
                    state: state,
                    titles: model.trendingPosters.map { $0.media.displayTitle }
                )
            }
        }
    }
}

/// Where a tile's panel sits inside the tile. The In progress panel hangs under the posters, inset 16 pt; the
/// Trending panel is a full-width, full-height layer that places its title block and anchors its rail to the tile's
/// foot, so a different tile height only moves the rail.
struct TilePanelPlacement: ViewModifier {
    let tile: HomeTileID
    let contentWidth: CGFloat

    @ViewBuilder
    func body(content: Content) -> some View {
        switch tile {
        case .inProgress:
            content
                .frame(width: contentWidth - 2 * TileGeometry.panelInset)
                .padding(.leading, TileGeometry.panelInset)
                .padding(.top, TileGeometry.panelTop)
        case .trending:
            content
                .frame(width: contentWidth, height: HomeDesign.Metric.tileOpenHeight, alignment: .top)
        }
    }
}
