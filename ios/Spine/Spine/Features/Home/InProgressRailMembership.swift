import Foundation

/// Whether a title still belongs in Home's In progress rail, from the tracking a write just answered with. The server
/// lists a title there while it is In progress (a show or an anime: not caught up, not finished), so a card that a
/// write has finished, paused, dropped or caught up leaves the rail at once instead of at the next refresh.
enum InProgressRailMembership {
    nonisolated static func stays(_ item: LibraryItem) -> Bool {
        let tracking = item.tracking
        switch item.media.ref.mediaType {
        case "tv":
            // A show's status and flags are in its own state; the card's status was only the list's.
            guard let tv = tracking.tv else { return tracking.status == InProgressLibraryLoader.status }
            return !tv.finished && !tv.caughtUp && isInProgress(tv.status)
        case "anime":
            guard let anime = tracking.anime else { return tracking.status == InProgressLibraryLoader.status }
            return !anime.finished && !anime.caughtUp && isInProgress(anime.status)
        default:
            return tracking.status == InProgressLibraryLoader.status
        }
    }

    /// A state with no status of its own does not say the title left.
    private nonisolated static func isInProgress(_ status: String?) -> Bool {
        status == nil || status == InProgressLibraryLoader.status
    }
}
