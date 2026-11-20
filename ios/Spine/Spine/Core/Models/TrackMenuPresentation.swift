import Foundation

/// A game's + menu (GM-009 to GM-012), worked out from its state the way `ReadingMenu` works out a book's: Planning,
/// Playing (Resume when Paused, Replay once Completed), Paused, Dropped, Log, Update Progress, then Remove from Library.
/// Any shelf can be picked directly. Only rows you can use are listed, and the current status, ticked; a line under the
/// menu says why Planning or Remove can't be used.
nonisolated struct GameMenu: Equatable {
    nonisolated struct Row: Equatable, Identifiable {
        enum Command: Equatable {
            case planning
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
    /// Remove from Library is listed for a game in the library when nothing blocks it.
    let offersRemoval: Bool
    /// Lines under the menu for a row people look for that can't be used now: Planning during a playthrough, Remove
    /// from Library while playthroughs or logs hold the game.
    let notes: [String]
    /// The shelf it sits on: "Playing", "Completed", "Not in your library".
    let shelf: String

    init(status: String?, game: GameTrackingState?) {
        // A playthrough in progress or paused: Planning waits until it's finished or dropped (GM-011).
        let isLive = game?.hasLivePlaythrough == true
        var rows: [Row] = []
        var notes: [String] = []

        if isLive {
            notes.append("Finish or drop this playthrough to move it to Planning.")
        } else {
            rows.append(Row(title: "Planning", systemImage: "bookmark", command: .planning, isSelected: status == "Planning"))
        }
        if status == "Completed" {
            rows.append(Row(title: "Replay", systemImage: "arrow.counterclockwise", command: .playing))
        } else if status == "Paused" {
            rows.append(Row(title: "Resume", systemImage: "play.fill", command: .resume))
        } else {
            let playing = status == "In progress" || game?.hasPlayingPlaythrough == true
            rows.append(Row(title: "Playing", systemImage: "play.fill", command: .playing, isSelected: playing))
        }
        rows.append(Row(title: "Paused", systemImage: "pause.fill", command: .paused, isSelected: status == "Paused"))
        rows.append(Row(title: "Dropped", systemImage: "xmark", command: .dropped(asks: isLive), isSelected: status == "Dropped"))
        rows.append(Row(title: "Log", systemImage: "square.and.pencil", command: .log))
        if game?.canUpdateProgress == true {
            rows.append(Row(title: "Update Progress", systemImage: "slider.horizontal.3", command: .updateProgress))
        }
        self.rows = rows

        let canRemove = game?.canRemoveTracking != false
        offersRemoval = status != nil && canRemove
        if status != nil, !canRemove {
            notes.append("To remove this game, delete its playthroughs and logs first.")
        }
        self.notes = notes
        shelf = status.map { $0 == "In progress" ? "Playing" : $0 } ?? "Not in your library"
    }
}

/// The rail's slot after the eye, the same on every type: the stars once the title is finished or rated, else the
/// Planning bookmark while it can be planned, else nothing. It's one slot, so nothing slides in or out as a title is
/// watched, unwatched or liked.
nonisolated enum RailOpinionSlot: Equatable {
    case stars
    case bookmark
    case none

    static func make(finished: Bool, rated: Bool, bookmark: PlanningBookmark) -> RailOpinionSlot {
        if finished || rated { return .stars }
        return bookmark == .hidden ? .none : .bookmark
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
