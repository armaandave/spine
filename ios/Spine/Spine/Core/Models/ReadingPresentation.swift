import Foundation

/// The + menu of a book, a manga or a comic (BK-100, MC-200), worked out from its state as `TVTrackingPresentation`
/// does a show's.
nonisolated struct ReadingMenu: Equatable {
    nonisolated struct Row: Equatable, Identifiable {
        enum Command: Equatable {
            case toRead
            case currentlyReading
            case keepReading
            case reread
            case paused
            /// A read under way asks first (BK-301); with nothing under way it's a status.
            case didNotFinish(asks: Bool)
            case resume
            case log
        }

        let title: String
        let systemImage: String?
        let command: Command
        var isDisabled = false

        var id: String { title }
    }

    nonisolated struct Removal: Equatable {
        let title: String
        let isDisabled: Bool
        let reason: String?
    }

    let rows: [Row]
    /// A read under way: why it can't go to To Read, and its Update Progress.
    let liveReason: String?
    /// Nil for a title that isn't in the library: it has nothing to remove (TV-005's rule).
    let removal: Removal?

    init(mediaType: String, status: String?, book: BookTrackingState?) {
        let isSeries = ["manga", "comic"].contains(mediaType)
        let isLive = book?.hasLiveJourney == true

        func statusRow(_ title: String, _ image: String, _ command: Row.Command, for value: String, disabledAlso: Bool = false) -> Row {
            let selected = status == value
            return Row(title: selected ? "\(title) ✓" : title, systemImage: image, command: command, isDisabled: selected || disabledAlso)
        }

        var rows = [statusRow("To Read", "bookmark", .toRead, for: "Planning", disabledAlso: isLive)]
        if isSeries, status == "Completed" {
            // Picking it back up says how: the old place or from the top (MC-200).
            if book?.offersKeepReading == true {
                rows.append(Row(title: "Keep Reading", systemImage: "book.pages", command: .keepReading))
            }
            rows.append(Row(title: "Reread", systemImage: "arrow.counterclockwise", command: .reread))
        } else {
            rows.append(statusRow("Currently Reading", "book.pages", .currentlyReading, for: "In progress"))
        }
        rows.append(statusRow("Paused", "pause.fill", .paused, for: "Paused"))
        rows.append(statusRow("Did Not Finish", "xmark", .didNotFinish(asks: isLive), for: "Dropped"))
        if status == "Paused" {
            rows.append(Row(title: "Resume Reading", systemImage: nil, command: .resume))
        }
        rows.append(Row(title: "Log Read", systemImage: "square.and.pencil", command: .log))
        self.rows = rows

        liveReason = isLive
            ? book?.actionReasons["planning"] ?? (
                isSeries
                    ? "Pause or mark this attempt Did Not Finish first."
                    : "Pause or finish the active journey before moving this book to To Read."
            )
            : nil
        removal = status == nil ? nil : Removal(
            title: isSeries ? "Remove from Library" : "Remove Tracking",
            isDisabled: book?.canRemoveTracking == false,
            reason: book?.canRemoveTracking == false ? book?.removeTrackingReason : nil
        )
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
