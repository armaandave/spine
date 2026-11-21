import Foundation

/// A book update typed in percent also says how many pages that was, pages being what every book is measured in:
/// "+10% (37 p.)". The pages are the server's own amount for the day or log, worked out from the book's page count.
enum PercentPages {
    /// The pages a book's day or log told in percent covered. Nil for any other type, an update told in pages, a book
    /// with no page count, and an update under one page.
    static func count(for activity: ActivityItem) -> Int? {
        guard activity.media?.ref.mediaType == "book",
              let label = activity.object.label?.trimmingCharacters(in: .whitespacesAndNewlines),
              label.hasSuffix("%"),
              let amount = activity.object.amount, amount.isFinite
        else { return nil }
        if let unit = activity.object.unit, !unit.lowercased().hasPrefix("page") { return nil }
        let pages = Int(amount.rounded())
        return pages >= 1 ? pages : nil
    }

    /// "(37 p.)", "(1,016 p.)".
    static func text(_ pages: Int) -> String {
        "(\(InProgressTilePresenter.grouped(pages)) p.)"
    }

    /// What VoiceOver says for them: "37 pages", "1 page".
    static func spoken(_ pages: Int) -> String {
        pages == 1 ? "1 page" : "\(InProgressTilePresenter.grouped(pages)) pages"
    }
}
