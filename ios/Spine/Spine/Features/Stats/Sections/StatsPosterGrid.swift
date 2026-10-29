import SwiftUI

// Poster pieces shared by the Stats ranking sections and their show-all pages. Grids are cut
// into rows of four, and each row is its own item in the page's lazy stack: a whole grid in one
// lazy item once pegged the CPU at 100% (the diary's lesson), so never nest rows in one item.

/// One row of a poster grid. Its ID carries the section's prefix ("highest.0"), so rows of
/// two sections never collide in one lazy stack.
struct StatsPosterGridRow<Item: Identifiable>: Identifiable {
    let id: String
    let index: Int
    let items: [Item]
}

enum StatsPosterGridLayout {
    static let columns = 4
    /// Every cell is this size whatever the artwork, so square album art and portrait posters
    /// line up along the bottom.
    static let slot: PosterSlot = .tagGrid
    static var cellSize: CGSize { slot.size }
    static let rowSpacing: CGFloat = 16

    /// `items` in rows of `columns`, in order; the last row may be short.
    static func rows<Item: Identifiable>(
        _ items: [Item],
        columns: Int = columns,
        idPrefix: String
    ) -> [StatsPosterGridRow<Item>] {
        guard columns > 0 else { return [] }
        return stride(from: 0, to: items.count, by: columns).enumerated().map { index, start in
            StatsPosterGridRow(
                id: "\(idPrefix).\(index)",
                index: index,
                items: Array(items[start ..< min(start + columns, items.count)])
            )
        }
    }
}

/// One grid row: fixed cells spread across the width, flush with both edges. A short last
/// row keeps the same columns, with empty cells on the right.
struct StatsPosterGridRowView<Item: Identifiable, Cell: View>: View {
    let row: StatsPosterGridRow<Item>
    var columns = StatsPosterGridLayout.columns
    @ViewBuilder let cell: (Item) -> Cell

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(0 ..< columns, id: \.self) { column in
                if column > 0 {
                    Spacer(minLength: 4)
                }
                if column < row.items.count {
                    cell(row.items[column])
                } else {
                    Color.clear
                        .frame(width: StatsPosterGridLayout.cellSize.width, height: 1)
                        .accessibilityHidden(true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A poster in a fixed cell with the artwork on the cell's bottom edge, and a caption under it.
struct StatsPosterCell<Caption: View>: View {
    let media: MediaSummary
    let accessibilityText: String
    let action: () -> Void
    @ViewBuilder let caption: () -> Caption

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                MediaArtwork(
                    url: media.displayPosterURL,
                    title: media.displayTitle,
                    slot: StatsPosterGridLayout.slot,
                    mediaType: media.ref.mediaType,
                    orientation: media.posterOrientation
                )
                .shadow(color: .black.opacity(0.28), radius: 8, y: 4)
                .frame(
                    width: StatsPosterGridLayout.cellSize.width,
                    height: StatsPosterGridLayout.cellSize.height,
                    alignment: .bottom
                )

                caption()
                    .frame(width: StatsPosterGridLayout.cellSize.width, alignment: .leading)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityAddTraits(.isButton)
    }
}

/// A one-line caption under a grid poster ("5 times", "Twice").
struct StatsPosterCaption: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .monospacedDigit()
            .foregroundStyle(.white.opacity(0.62))
            .lineLimit(1)
            .minimumScaleFactor(0.8)
    }
}

/// A title row in a card: an optional rank, a small poster in a fixed cell, the title with its
/// year and a detail line under it, and something on the right.
struct StatsMediaRow<Detail: View, Trailing: View>: View {
    let media: MediaSummary
    var rank: Int?
    var showsSeparator = false
    @ViewBuilder let detail: () -> Detail
    @ViewBuilder let trailing: () -> Trailing

    static var rankWidth: CGFloat { 30 }
    static var artworkCell: CGSize { PosterSlot.diaryEntry.size }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            if let rank {
                Text("\(rank)")
                    .font(.subheadline.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.45))
                    .frame(width: Self.rankWidth, alignment: .leading)
            }

            MediaArtwork(
                url: media.displayPosterURL,
                title: media.displayTitle,
                slot: .diaryEntry,
                mediaType: media.ref.mediaType,
                orientation: media.posterOrientation
            )
            .shadow(color: .black.opacity(0.2), radius: 6, y: 3)
            .frame(width: Self.artworkCell.width, height: Self.artworkCell.height)

            VStack(alignment: .leading, spacing: 5) {
                Text(StatsTitleFormat.titleWithYear(media))
                    .font(.system(size: 16, weight: .semibold))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                detail()
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            trailing()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                // Starts under the title, like an inset list separator.
                DiarySeparator()
                    .padding(.leading, separatorInset)
            }
        }
    }

    private var separatorInset: CGFloat {
        14 + (rank == nil ? 0 : Self.rankWidth + 12) + Self.artworkCell.width + 12
    }
}

/// Words and numbers shared by the ranking sections.
enum StatsTitleFormat {
    /// The release year from a media summary's `release_date` ("2024-03-01" → "2024").
    static func year(of media: MediaSummary) -> String? {
        guard let date = media.releaseDate, date.count >= 4 else { return nil }
        let year = String(date.prefix(4))
        return year.allSatisfy(\.isNumber) ? year : nil
    }

    /// The title (seasons read "Show S2", TV-004) with its year dimmed after it.
    static func titleWithYear(_ media: MediaSummary) -> AttributedString {
        var text = AttributedString(media.displayTitle)
        text.foregroundColor = .white
        if let year = year(of: media) {
            var yearText = AttributedString(" \(year)")
            yearText.foregroundColor = .white.opacity(0.45)
            yearText.font = .system(size: 16, weight: .regular)
            text += yearText
        }
        return text
    }

    /// "1st", "100th", "1,000th", "1,500th".
    static func ordinal(_ number: Int) -> String {
        let suffix: String
        switch number % 100 {
        case 11, 12, 13:
            suffix = "th"
        default:
            switch number % 10 {
            case 1: suffix = "st"
            case 2: suffix = "nd"
            case 3: suffix = "rd"
            default: suffix = "th"
            }
        }
        return number.formatted(.number.grouping(.automatic)) + suffix
    }

    /// A contract day ("2024-03-12") in the phone's short date style ("12 Mar 2024", or
    /// "Mar 12, 2024" in the US). It's a calendar day, so it's read and shown in UTC and never
    /// shifts with the phone's zone.
    static func day(_ text: String, locale: Locale = .current) -> String? {
        guard let date = StatsDateFormatters.dayParser.date(from: text) else { return nil }
        return StatsDateFormatters.utc(template: "dMMMy", locale: locale, gregorian: true).string(from: date)
    }

    /// A contract day without its year ("12 Mar", or "Mar 12" in the US), for a list that
    /// already names the year.
    static func dayWithoutYear(_ text: String, locale: Locale = .current) -> String? {
        guard let date = StatsDateFormatters.dayParser.date(from: text) else { return nil }
        return StatsDateFormatters.utc(template: "dMMM", locale: locale, gregorian: true).string(from: date)
    }
}

/// Date formatters for the Stats screens, made once and reused: charts and grids format many
/// dates on every redraw, and making a formatter costs far more than using one.
enum StatsDateFormatters {
    private static var cache: [String: DateFormatter] = [:]

    /// Reads a contract day ("2024-03-12") as midnight UTC.
    static let dayParser: DateFormatter = {
        let parser = DateFormatter()
        parser.calendar = Calendar(identifier: .gregorian)
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.timeZone = TimeZone(secondsFromGMT: 0)
        parser.dateFormat = "yyyy-MM-dd"
        return parser
    }()

    /// A UTC formatter for a localized template, one per template, locale and calendar.
    static func utc(template: String, locale: Locale, gregorian: Bool = false) -> DateFormatter {
        let key = "\(template)|\(locale.identifier)|\(gregorian)"
        if let formatter = cache[key] {
            return formatter
        }
        let formatter = DateFormatter()
        if gregorian {
            formatter.calendar = Calendar(identifier: .gregorian)
        }
        formatter.locale = locale
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.setLocalizedDateFormatFromTemplate(template)
        cache[key] = formatter
        return formatter
    }
}
