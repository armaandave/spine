import Foundation
import Observation
import SwiftUI

/// The two tiles, left to right.
enum HomeTileID: String, CaseIterable, Hashable {
    case inProgress
    case trending

    /// 0 for the left tile, 1 for the right.
    var index: Int {
        switch self {
        case .inProgress: 0
        case .trending: 1
        }
    }

    var title: String {
        switch self {
        case .inProgress: "In progress"
        case .trending: "Trending"
        }
    }

    var identifier: String {
        switch self {
        case .inProgress: "home.tile.in-progress"
        case .trending: "home.tile.trending"
        }
    }
}

/// Which tile is open and which poster it is on. The scroll offset is never kept here: the carousel reduces it to a
/// whole poster number before it reaches this state, so a swipe changes state only when the number does.
///
/// Focus is an item id, not a position, so it survives the rail reloading under an open tile: the tile follows the
/// title, and when the title is gone the focus moves to its nearest surviving neighbor.
@MainActor
@Observable
final class HomeTilesState {
    /// The open tile; nil when both are closed.
    private(set) var open: HomeTileID?
    /// The tile whose every poster is mounted: from the start of an open until the flight home has finished. A
    /// closed tile mounts only the fan's three.
    private(set) var mounted: HomeTileID?
    /// The focused poster's item id while a tile is open (kept until the tile has finished closing).
    private(set) var focusID: String?
    /// The item ids each tile shows, in order, as the section last reported them.
    private(set) var ids: [HomeTileID: [String]] = [:]

    /// The open tile's carousel offset in poster units (0 on the first poster, 1.5 halfway to the third). Written by
    /// the carousel on every scroll frame and read only by the In progress panel, so nothing else is invalidated by
    /// a scroll. Nil until the carousel reports after an open: a panel that appears first falls back to `focusIndex`.
    var scrollPosition: Double?

    /// The focused poster's place in the open tile's list.
    var focusIndex: Int? {
        guard let open, let focusID else { return nil }
        return ids[open]?.firstIndex(of: focusID)
    }

    func isOpen(_ tile: HomeTileID) -> Bool {
        open == tile
    }

    /// Whether every poster of `tile` is mounted.
    func isFullyMounted(_ tile: HomeTileID) -> Bool {
        mounted == tile
    }

    // MARK: Lists

    /// The ids `tile` now shows. A reload keeps the focus on its title; a focused title that has gone moves the
    /// focus to the nearest one left, and a list that has emptied closes the tile.
    func sync(_ newIDs: [String], for tile: HomeTileID) {
        let oldIDs = ids[tile] ?? []
        guard newIDs != oldIDs else { return }
        ids[tile] = newIDs

        guard open == tile else { return }
        guard !newIDs.isEmpty else {
            close()
            focusID = nil
            mounted = nil
            return
        }
        guard let focusID, !newIDs.contains(focusID) else { return }
        self.focusID = Self.nearest(to: focusID, in: oldIDs, surviving: newIDs)
    }

    /// The title closest to `gone` in the old list that is still in the new one: looking outward from where it was,
    /// the one after it before the one before it. Failing that, the new list's end nearest its old place.
    nonisolated static func nearest(to gone: String, in oldIDs: [String], surviving newIDs: [String]) -> String? {
        guard !newIDs.isEmpty else { return nil }
        let survivors = Set(newIDs)
        if let oldIndex = oldIDs.firstIndex(of: gone) {
            for distance in 1 ..< max(oldIDs.count, 2) {
                for candidate in [oldIndex + distance, oldIndex - distance] where oldIDs.indices.contains(candidate) {
                    if survivors.contains(oldIDs[candidate]) { return oldIDs[candidate] }
                }
            }
            return newIDs[min(oldIndex, newIDs.count - 1)]
        }
        return newIDs.first
    }

    // MARK: Opening and closing

    /// Mounts all of `tile`'s posters, in the fan, ahead of `openTile(_:)`. A newly mounted poster has no earlier pose to
    /// fly from, so the section mounts first and opens a frame or two later.
    func mount(_ tile: HomeTileID) {
        guard open == nil else { return }
        mounted = tile
    }

    /// Opens `tile` on its first poster. Nothing happens for a tile with no titles or while a tile is open.
    func openTile(_ tile: HomeTileID) {
        guard open == nil, let first = ids[tile]?.first else { return }
        mounted = tile
        open = tile
        focusID = first
        scrollPosition = nil
    }

    /// What a tap on a closed tile does. The change runs inside `animation` so that every view the change moves
    /// (the rest of the page sliding down as the tile grows, not only the tile) moves under the same curve. A tile of
    /// more than three posters mounts them first and opens a frame or two later, so the new ones have a place in the
    /// fan to fly from.
    func requestOpen(_ tile: HomeTileID, posterCount: Int, animation: Animation, defersMount: Bool = true) {
        guard open == nil else { return }
        guard defersMount, posterCount > TileGeometry.fanMountedCount else {
            withAnimation(animation) { openTile(tile) }
            return
        }
        mount(tile)
        Task { [weak self] in
            try? await Task.sleep(for: TileMotion.mountLead)
            self?.openAfterMount(tile, animation: animation)
        }
    }

    /// The tile may have lost its mount in the lead (the flight home of an earlier close can end just then), and
    /// `openTile` mounts it again, so only an open tile stops this.
    private func openAfterMount(_ tile: HomeTileID, animation: Animation) {
        guard open == nil else { return }
        withAnimation(animation) { openTile(tile) }
    }

    /// Closes the open tile inside `animation`, for the same reason `requestOpen` opens inside one. Whoever closes a
    /// tile from outside (a tab change, a refresh) should use this, not `close()`.
    func requestClose(animation: Animation) {
        guard open != nil else { return }
        withAnimation(animation) { close() }
    }

    /// Starts closing. The focus and the mounted posters stay until `finishClosing()`, so the posters can fly home.
    func close() {
        open = nil
    }

    /// The flight home is over: the tile goes back to mounting only its fan, and the focus resets. Does nothing if a
    /// tile has opened since.
    func finishClosing() {
        guard open == nil else { return }
        mounted = nil
        focusID = nil
    }

    /// Moves the focus to the title `id` in the open tile. Returns whether the focus changed.
    @discardableResult
    func focus(_ id: String) -> Bool {
        guard let open, ids[open]?.contains(id) == true, focusID != id else { return false }
        focusID = id
        return true
    }

    // MARK: Glow

    /// The open tile's focused poster, or nil while both tiles are closed.
    func focusedMedia(model: HomeTilesModel) -> MediaSummary? {
        guard let open, let focusID else { return nil }
        return model.media(for: open, id: focusID)
    }

    /// Points Home's glow at the focused poster while a tile is open, and back at its own picture when both are
    /// closed.
    func syncGlow(_ glow: HomeGlowModel, model: HomeTilesModel) {
        let media = focusedMedia(model: model)
        if glow.focused != media { glow.focused = media }
    }
}
