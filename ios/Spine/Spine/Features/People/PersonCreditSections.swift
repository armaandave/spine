import CoreGraphics
import Foundation

extension MediaSummary {
    /// An acting credit where they play themselves (talk shows, award shows, documentaries): the credit's character
    /// starts with Self, Himself, Herself or Themselves. The person page lists these under "Appearances", not "Actor".
    /// A cast credit's character comes first in `roles` (the server adds cast before crew).
    var isSelfAppearance: Bool {
        guard creditRoles.contains(FilmographyCreditGroup.actorRole),
              let character = roles.first?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !character.isEmpty
        else { return false }
        return ["self", "himself", "herself", "themselves", "themself"].contains { word in
            guard character.hasPrefix(word) else { return false }
            // "Self", "Self - Guest", "Self (voice)"; not "Selfridge".
            return character.dropFirst(word.count).first.map { !$0.isLetter } ?? true
        }
    }
}

/// One section of a person's credits: a role (with every credit in it) or their book series.
struct PersonCreditSection: Identifiable {
    enum Content {
        case media([MediaSummary])
        case series([MediaSeriesSummary])
    }

    static let seriesID = "media-series"

    let id: String
    let title: String
    let content: Content

    /// Every credit (or series) the section holds: the page shows them all.
    var count: Int {
        switch content {
        case let .media(media): media.count
        case let .series(series): series.count
        }
    }

    /// The credits the viewer has finished. Nil for series, which have no finished count.
    var done: Int? {
        guard case let .media(media) = content else { return nil }
        return media.lazy.filter { $0.userState?.status == "Completed" }.count
    }

    var media: [MediaSummary] {
        guard case let .media(media) = content else { return [] }
        return media
    }

    var series: [MediaSeriesSummary] {
        guard case let .series(series) = content else { return [] }
        return series
    }

    var isSeries: Bool {
        if case .series = content { return true }
        return false
    }

    /// Music covers are square; everything else is a 2:3 poster.
    var isSquare: Bool {
        let media = media
        return !media.isEmpty && media.allSatisfy { $0.ref.mediaType == FilmographyType.music.rawValue }
    }
}

/// Builds a person's credit sections for one kind: a single media type, or every type together (the globe).
enum PersonCreditSections {
    /// The globe: every type at once.
    static let allKind = APIConstants.allMedia

    /// The kinds the page offers: the globe first when the person has two or more types, then each type.
    static func kinds(in filmography: [MediaSummary]) -> [String] {
        let types = FilmographyType.available(in: filmography).map(\.rawValue)
        return types.count > 1 ? [allKind] + types : types
    }

    /// The kind a page opens on: the globe for two or more types, else the one type.
    static func defaultKind(in filmography: [MediaSummary]) -> String {
        kinds(in: filmography).first ?? FilmographyType.movie.rawValue
    }

    /// Every role is its own section, each holding all of its credits. With the globe a role merges across types: its
    /// credits alternate one of each type, each type in its own order (most popular first). Roles keep the app's order
    /// (the person's main role first, then by count, then by name; music in release order), "Appearances" last, and
    /// series after the roles.
    static func make(
        filmography: [MediaSummary],
        kind: String,
        knownForDepartment: String?,
        series: (String) -> [MediaSeriesSummary] = { _ in [] }
    ) -> [PersonCreditSection] {
        let types: [FilmographyType]
        if kind == allKind {
            types = FilmographyType.available(in: filmography)
        } else {
            types = FilmographyType(rawValue: kind).map { [$0] } ?? []
        }

        var listsByRole: [String: [[MediaSummary]]] = [:]
        for type in types {
            let media = filmography.filter { $0.ref.mediaType == type.rawValue }
            for group in FilmographyCreditGroup.groups(from: media, knownForDepartment: knownForDepartment) {
                listsByRole[group.role, default: []].append(group.media)
            }
        }

        let merged = listsByRole.map { role, lists in
            FilmographyCreditGroup(role: role, media: interleave(lists))
        }
        let isMusic = types == [.music]
        let primary = FilmographyCreditGroup.primaryRoles(for: knownForDepartment)
        var sections = merged
            .sorted { FilmographyCreditGroup.precedes($0, $1, primaryRoles: primary, isMusic: isMusic) }
            .map { PersonCreditSection(id: $0.role, title: $0.role, content: .media($0.media)) }

        let allSeries = types.flatMap { series($0.rawValue) }
        if !allSeries.isEmpty {
            sections.append(PersonCreditSection(id: PersonCreditSection.seriesID, title: "Series", content: .series(allSeries)))
        }
        return sections
    }

    /// One from each list in turn, each list in its own order, until every list is used up.
    static func interleave<Element>(_ lists: [[Element]]) -> [Element] {
        let longest = lists.map(\.count).max() ?? 0
        var result: [Element] = []
        result.reserveCapacity(lists.reduce(0) { $0 + $1.count })
        for index in 0..<longest {
            for list in lists where index < list.count {
                result.append(list[index])
            }
        }
        return result
    }
}

/// Where each credit section sits, worked out from its count, so the plate's title can follow the section in view and
/// its menu can jump to one before the grid below has been drawn. The page draws the sections with these same sizes.
enum PersonCreditsLayout {
    static let headerHeight: CGFloat = 28
    static let headerGap: CGFloat = 12
    static let sectionGap: CGFloat = 40
    static let columns = 4
    static let columnGap: CGFloat = 8
    static let rowGap: CGFloat = 10
    static let seriesColumns = 3
    static let seriesRowGap: CGFloat = 18
    /// A series card: its fanned covers, the name (two lines) and the count.
    static let seriesFanHeight: CGFloat = 150
    static let seriesCardHeight: CGFloat = seriesFanHeight + 8 + 30 + 14

    static func columnWidth(for width: CGFloat, columns: Int, gap: CGFloat) -> CGFloat {
        max(0, (width - gap * CGFloat(columns - 1)) / CGFloat(columns))
    }

    static func posterSize(width: CGFloat, isSquare: Bool) -> CGSize {
        let column = columnWidth(for: width, columns: columns, gap: columnGap)
        return CGSize(width: column, height: isSquare ? column : column * 1.5)
    }

    /// The grid (or series cards) under a section's header.
    static func bodyHeight(of section: PersonCreditSection, width: CGFloat) -> CGFloat {
        if section.isSeries {
            let rows = rowCount(section.count, columns: seriesColumns)
            return rows == 0 ? 0 : CGFloat(rows) * seriesCardHeight + CGFloat(rows - 1) * seriesRowGap
        }
        let rows = rowCount(section.count, columns: columns)
        let poster = posterSize(width: width, isSquare: section.isSquare)
        return rows == 0 ? 0 : CGFloat(rows) * poster.height + CGFloat(rows - 1) * rowGap
    }

    static func height(of section: PersonCreditSection, width: CGFloat) -> CGFloat {
        headerHeight + headerGap + bodyHeight(of: section, width: width)
    }

    /// Each section's top, measured from the top of the first one.
    static func tops(of sections: [PersonCreditSection], width: CGFloat) -> [CGFloat] {
        var tops: [CGFloat] = []
        var y: CGFloat = 0
        for section in sections {
            tops.append(y)
            y += height(of: section, width: width) + sectionGap
        }
        return tops
    }

    /// The section a reader is in: the last one whose top has reached `line` (both measured from the first section's
    /// top). The first section until then.
    static func currentIndex(tops: [CGFloat], line: CGFloat) -> Int {
        tops.lastIndex { $0 <= line } ?? 0
    }

    private static func rowCount(_ count: Int, columns: Int) -> Int {
        (count + columns - 1) / columns
    }
}
