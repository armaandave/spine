import Foundation

/// The + menu of a book, a manga or a comic (BK-100, MC-200), worked out from its state as `TVTrackingPresentation`
/// does a show's: the rows you can use, the current status ticked, and a line for Remove from Library when it can't be
/// used yet. Planning is the rail's bookmark, so it isn't here.
nonisolated struct ReadingMenu: Equatable {
    nonisolated struct Row: Equatable, Identifiable {
        enum Command: Equatable {
            case reading
            case keepReading
            case reread
            case paused
            /// A read under way asks first (BK-301); with nothing under way it's a status.
            case dropped(asks: Bool)
            case resume
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
    /// Remove from Library is listed for a title in the library past Planning (the bookmark takes a planned one out)
    /// when nothing blocks it.
    let offersRemoval: Bool
    /// A line under the menu for a row people look for that can't be used yet.
    let notes: [String]
    /// The shelf it sits on: "Reading", "Completed", "Not in your library".
    let shelf: String

    init(mediaType: String, status: String?, book: BookTrackingState?) {
        let isSeries = ["manga", "comic"].contains(mediaType)
        let isLive = book?.hasLiveJourney == true
        // Paused and Dropped wait until it's started: a status past Planning, or a read on record.
        let started = (status != nil && status != "Planning") || book?.currentJourney != nil

        var rows: [Row] = []
        if status == "Completed" {
            // Picking it back up says how: from the old place when a series has more out, or from the top (MC-200).
            if isSeries, book?.offersKeepReading == true {
                rows.append(Row(title: "Keep Reading", systemImage: "book.pages", command: .keepReading))
            }
            rows.append(Row(title: "Reread", systemImage: "arrow.counterclockwise", command: isSeries ? .reread : .reading))
        } else if status == "Paused" {
            // Resume takes Reading's place, as it takes Watching's on a show.
            rows.append(Row(title: "Resume", systemImage: "play.fill", command: .resume))
        } else {
            rows.append(Row(title: "Reading", systemImage: "book.pages", command: .reading, isSelected: status == "In progress"))
        }
        if started {
            rows.append(Row(title: "Paused", systemImage: "pause.fill", command: .paused, isSelected: status == "Paused"))
            rows.append(Row(title: "Dropped", systemImage: "xmark", command: .dropped(asks: isLive), isSelected: status == "Dropped"))
        }
        rows.append(Row(title: "Log", systemImage: "square.and.pencil", command: .log))
        if isLive {
            rows.append(Row(title: "Update Progress", systemImage: "slider.horizontal.3", command: .updateProgress))
        }
        self.rows = rows

        let pastPlanning = status != nil && status != "Planning"
        let canRemove = book?.canRemoveTracking != false
        offersRemoval = pastPlanning && canRemove
        notes = pastPlanning && !canRemove
            ? ["To remove this \(isSeries ? "series" : "book"), delete its reads and logs first."]
            : []
        shelf = status.map { $0 == "In progress" ? MediaRef.inProgressWord(mediaType: mediaType) : $0 }
            ?? "Not in your library"
    }
}

/// What a tap on the eye, the stars or the heart does to a book, a manga or a comic.
nonisolated enum ReadingRouting {
    enum EyeTap: Equatable {
        case openLog
        case undoRead
        case explain(String)
        case markRead
    }

    /// During a read the eye opens the log screen. With none it marks Read with no date, and the filled eye undoes only
    /// that mark (BK-102, BK-103).
    static func eyeTap(book: BookTrackingState?, isRead: Bool, noun: String) -> EyeTap {
        if book?.hasLiveJourney == true { return .openLog }
        guard isRead else { return .markRead }
        if book?.supports("undo_read") == true { return .undoRead }
        return .explain(book?.actionReasons["undo_read"] ?? "Delete the completion log to mark this \(noun) unread.")
    }

    /// Stars or a heart given during a read, paused ones too, open the log screen with that value set (BK-200, MC-104).
    /// Taking them away is only that.
    static func opinionOpensLog(book: BookTrackingState?, isGiving: Bool) -> Bool {
        isGiving && book?.hasLiveJourney == true
    }
}

/// What the log screen opens with, built as one value so the cover shows these and no older ones.
nonisolated struct MediaLogPresentation: Identifiable, Equatable {
    let id = UUID()
    var journeyId: Int?
    var liked: Bool?
    var ratingSteps: Int?
    /// Reaching the total opened it: its close button reads Not now (MC-100).
    var openedByTotal = false

    /// A read under way is what a log finishes. With none, a log makes a read of its own (BK-105).
    static func reading(
        _ book: BookTrackingState?,
        liked: Bool? = nil,
        ratingSteps: Int? = nil,
        openedByTotal: Bool = false
    ) -> MediaLogPresentation {
        MediaLogPresentation(
            // A closed read's id would answer with that read's old log and save nothing.
            journeyId: book?.hasLiveJourney == true ? book?.currentJourney?.id : nil,
            liked: liked,
            ratingSteps: ratingSteps,
            openedByTotal: openedByTotal
        )
    }
}

nonisolated enum ReadingWords {
    /// "1 chapter", "162 chapters".
    static func chapters(_ count: String) -> String {
        count == "1" ? "1 chapter" : "\(count) chapters"
    }
}
