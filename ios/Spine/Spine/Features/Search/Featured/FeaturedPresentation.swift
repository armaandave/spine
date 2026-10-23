import SwiftUI

/// A featured list the user opened, and the shelf it was opened from: the same list can sit on several shelves,
/// so the zoom transition needs both to find the tile it grows out of.
struct FeaturedListSelection: Identifiable, Hashable {
    let card: FeaturedListCard
    let sourceKey: String

    var id: String { "\(sourceKey)/\(card.id)" }
}

extension EnvironmentValues {
    /// Set by Search so featured tiles can be the source of the list cover's zoom transition.
    @Entry var featuredZoomNamespace: Namespace.ID?
}

/// The one status line a featured tile shows, highest priority first.
enum FeaturedBadge: Equatable {
    case live(label: String)
    case period(String)
    case kind(FeaturedKind)

    static func resolve(for card: FeaturedListCard, now: Date) -> FeaturedBadge {
        if let live = card.live, live.isLive(at: now) {
            return .live(label: live.label)
        }
        if let period = card.periodLabel {
            return .period(period)
        }
        return .kind(card.kind)
    }
}

/// A featured list row's note as the list detail shows it ("Winner · Best Picture", "▲2 · $12.4M"), or nil when
/// the server sent none.
enum FeaturedEntryNote {
    static func text(_ note: String?) -> String? {
        guard let note = note?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty else { return nil }
        return note
    }
}

extension FeaturedListCard {
    func isLive(at now: Date) -> Bool {
        live?.isLive(at: now) == true
    }

    /// The single media type a list is about, or nil when it mixes types.
    var singleMediaType: String? {
        mediaTypes.count == 1 ? mediaTypes.first : nil
    }

    var usesSquareArtwork: Bool {
        singleMediaType == "music"
    }

    /// "10 films", "121 people"
    var countText: String {
        let noun = isPeopleList
            ? (itemsCount == 1 ? "person" : "people")
            : FeaturedCountNoun.noun(for: mediaTypes, count: itemsCount)
        return "\(itemsCount.formatted()) \(noun)"
    }

    /// The people a tile stacks in place of posters: a people list's, when it has no media previews.
    var stackPeople: [PersonListEntry] {
        previews.isEmpty ? previewPeople : []
    }

    /// What VoiceOver reads for a tile: the name, the status badge, the count and the source.
    func accessibilitySummary(now: Date) -> String {
        [name, FeaturedBadge.resolve(for: self, now: now).accessibilityText, countText, source?.name]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: ", ")
    }
}

extension FeaturedBadge {
    /// The badge as VoiceOver reads it ("Live, 7 of 23 announced", "2026", "Award").
    var accessibilityText: String? {
        switch self {
        case let .live(label): label.isEmpty ? "Live" : "Live, \(label)"
        case let .period(label): label
        case let .kind(kind): kind == .unknown ? nil : kind.rawValue.capitalized
        }
    }
}

enum FeaturedCountNoun {
    static func noun(for mediaTypes: [String], count: Int) -> String {
        let one = count == 1
        guard mediaTypes.count == 1, let mediaType = mediaTypes.first else {
            return one ? "title" : "titles"
        }
        switch mediaType {
        case "movie": return one ? "film" : "films"
        case "tv": return one ? "show" : "shows"
        case "anime": return "anime"
        case "manga": return "manga"
        case "game": return one ? "game" : "games"
        case "book": return one ? "book" : "books"
        case "comic": return one ? "comic" : "comics"
        case "music": return one ? "album" : "albums"
        default: return one ? "title" : "titles"
        }
    }
}

extension FeaturedShelvesResponse {
    /// The landing's shelves without media types the user turned off, and without "Now", which the landing doesn't
    /// show. "Across Media" keeps a list unless every one of its media types is off.
    func visibleShelves(enabledMediaTypes: [String]) -> [FeaturedShelf] {
        let enabled = Set(enabledMediaTypes)
        return shelves.compactMap { shelf in
            if shelf.isNow {
                return nil
            }
            if let mediaType = shelf.mediaType, !shelf.isMixed {
                return enabled.contains(mediaType) ? shelf : nil
            }
            let lists = shelf.lists.filter { card in
                card.mediaTypes.isEmpty || card.mediaTypes.contains { enabled.contains($0) }
            }
            guard !lists.isEmpty else { return nil }
            return lists.count == shelf.lists.count ? shelf : FeaturedShelf(
                key: shelf.key,
                title: shelf.title,
                mediaType: shelf.mediaType,
                count: shelf.count - (shelf.lists.count - lists.count),
                liveCount: shelf.liveCount,
                kindCounts: shelf.kindCounts,
                lists: lists,
                seeAll: shelf.seeAll
            )
        }
    }

    func hasLiveList(now: Date) -> Bool {
        allCards.contains { $0.isLive(at: now) }
    }

    /// The earliest start of an upcoming window that hasn't begun.
    func nextLiveStart(after now: Date) -> Date? {
        allCards.compactMap { $0.live?.upcomingStart(after: now) }.min()
    }

    /// The same shelves as `other`, ignoring when the server generated them.
    func hasSameContent(as other: FeaturedShelvesResponse) -> Bool {
        mediaType == other.mediaType && isLegacy == other.isLegacy && shelves == other.shelves
    }

    /// "42 lists · 3 live · updated today", for a media type's grouped layer.
    func summaryLine(now: Date, calendar: Calendar = .current) -> String {
        let cards = Dictionary(allCards.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first }).values
        let total = shelves.reduce(0) { $0 + $1.count }
        var parts = ["\(total.formatted()) \(total == 1 ? "list" : "lists")"]
        let live = cards.filter { $0.isLive(at: now) }.count
        if live > 0 {
            parts.append("\(live) live")
        }
        if let latest = cards.compactMap(\.contentChangedAt).max() {
            parts.append(Self.updatedPhrase(latest, now: now, calendar: calendar))
        }
        return parts.joined(separator: " · ")
    }

    private static func updatedPhrase(_ date: Date, now: Date, calendar: Calendar) -> String {
        if calendar.isDate(date, inSameDayAs: now) {
            return "updated today"
        }
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: now)).day ?? 0
        return days <= 1 ? "updated yesterday" : "updated \(days) days ago"
    }
}
