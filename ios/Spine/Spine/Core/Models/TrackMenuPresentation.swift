import Foundation

/// A game's + menu (GM-009 to GM-012), worked out from its state the way `ReadingMenu` works out a book's: the rows
/// you can use, the current status ticked, and a line for Remove from Library when it can't be used yet. Planning is
/// the rail's bookmark, so it isn't here.
nonisolated struct GameMenu: Equatable {
    nonisolated struct Row: Equatable, Identifiable {
        enum Command: Equatable {
            /// Starts a playthrough: Playing, or Replay on a finished game.
            case playing
            case resume
            case paused
            /// A playthrough under way asks first; with none under way it's a status.
            case dropped(asks: Bool)
            case log
            case updateProgress
        }

        let title: String
        let systemImage: String
        let command: Command
        /// The current status: ticked, and a tap does nothing.
        var isSelected = false

        var id: String { title }
    }

    let rows: [Row]
    /// Remove from Library is listed for a game in the library past Planning (the bookmark takes a planned one out)
    /// when nothing blocks it.
    let offersRemoval: Bool
    /// A line under the menu for a row people look for that can't be used yet.
    let notes: [String]
    /// The shelf it sits on: "Playing", "Completed", "Not in your library".
    let shelf: String

    init(status: String?, game: GameTrackingState?) {
        let isLive = game?.hasLivePlaythrough == true
        // Paused and Dropped wait until it's started: a status past Planning, or a playthrough on record.
        let started = (status != nil && status != "Planning") || game?.currentPlaythrough != nil

        var rows: [Row] = []
        if status == "Completed" {
            rows.append(Row(title: "Replay", systemImage: "arrow.counterclockwise", command: .playing))
        } else if status == "Paused" {
            rows.append(Row(title: "Resume", systemImage: "play.fill", command: .resume))
        } else {
            let playing = status == "In progress" || game?.hasPlayingPlaythrough == true
            rows.append(Row(title: "Playing", systemImage: "play.fill", command: .playing, isSelected: playing))
        }
        if started {
            rows.append(Row(title: "Paused", systemImage: "pause.fill", command: .paused, isSelected: status == "Paused"))
            rows.append(Row(title: "Dropped", systemImage: "xmark", command: .dropped(asks: isLive), isSelected: status == "Dropped"))
        }
        rows.append(Row(title: "Log", systemImage: "square.and.pencil", command: .log))
        if game?.canUpdateProgress == true {
            rows.append(Row(title: "Update Progress", systemImage: "slider.horizontal.3", command: .updateProgress))
        }
        self.rows = rows

        let pastPlanning = status != nil && status != "Planning"
        let canRemove = game?.canRemoveTracking != false
        offersRemoval = pastPlanning && canRemove
        notes = pastPlanning && !canRemove ? ["To remove this game, delete its playthroughs and logs first."] : []
        shelf = status.map { $0 == "In progress" ? "Playing" : $0 } ?? "Not in your library"
    }
}

/// The rail's bookmark: Planning, on every type, until the title is started. It adds to Planning, or, filled, takes
/// the title back out. Seasons and episodes leave Planning to their show's page, as they do Paused and Dropped.
nonisolated enum PlanningBookmark: Equatable {
    case hidden
    case add
    case remove

    static func state(for ref: MediaRef, viewer: UserMediaState?) -> PlanningBookmark {
        if ref.mediaType == "season" || ref.isEpisode { return .hidden }
        if ref.isAnime, let anime = viewer?.anime {
            return showLike(status: anime.status, inLibrary: anime.inLibrary, available: anime.availableActions)
        }
        if ref.mediaType == "tv", let tv = viewer?.tv {
            return showLike(status: tv.status, inLibrary: tv.inLibrary, available: tv.availableActions)
        }
        switch viewer?.status {
        case nil: return .add
        case "Planning": return .remove
        default: return .hidden
        }
    }

    /// A show or an anime title can be planned until anything is ticked or logged, which the server says by listing
    /// `planning` (TV-006).
    private static func showLike(status: String?, inLibrary: Bool, available: [String]) -> PlanningBookmark {
        if inLibrary, status == "Planning" { return .remove }
        return available.contains("planning") ? .add : .hidden
    }
}
