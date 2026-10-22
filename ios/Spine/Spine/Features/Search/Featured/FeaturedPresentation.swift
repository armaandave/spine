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
    case updated(Date)
    case period(String)
    case kind(FeaturedKind)

    static let recentWindow: TimeInterval = 7 * 24 * 60 * 60

    static func resolve(for card: FeaturedListCard, now: Date) -> FeaturedBadge {
        if let live = card.live, live.isLive(at: now) {
            return .live(label: live.label)
        }
        if card.wasUpdatedRecently(now: now), let changed = card.contentChangedAt {
            return .updated(changed)
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

/// A chart entry's movement, read from its note ("▲2", "▼1", "NEW").
enum FeaturedMovement: Equatable {
    case up(Int)
    case down(Int)
    case new

    init?(note: String?) {
        guard let note = note?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty else { return nil }
        if note.uppercased().contains("NEW") {
            self = .new
            return
        }
        for (arrow, make) in [("▲", FeaturedMovement.up), ("▼", FeaturedMovement.down)] {
            guard let range = note.range(of: arrow) else { continue }
            let digits = note[range.upperBound...].prefix { $0.isNumber }
            guard let amount = Int(digits), amount > 0 else { return nil }
            self = make(amount)
            return
        }
        return nil
    }

    var text: String {
        switch self {
        case let .up(amount): "▲\(amount)"
        case let .down(amount): "▼\(amount)"
        case .new: "NEW"
        }
    }

    var color: Color {
        switch self {
        case .up: Color(red: 0.34, green: 0.82, blue: 0.48)
        case .down: Color(red: 0.95, green: 0.38, blue: 0.36)
        case .new: Color(red: 0.98, green: 0.78, blue: 0.30)
        }
    }
}

enum FeaturedRelativeTime {
    /// "now", "12m", "2h", "3d", "5w"
    static func short(from date: Date, now: Date) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        switch seconds {
        case ..<60: return "now"
        case ..<3600: return "\(Int(seconds / 60))m"
        case ..<86400: return "\(Int(seconds / 3600))h"
        case ..<(7 * 86400): return "\(Int(seconds / 86400))d"
        default: return "\(Int(seconds / (7 * 86400)))w"
        }
    }

    /// "Updated 2h ago", "Updated just now"
    static func updatedLabel(from date: Date, now: Date) -> String {
        let short = short(from: date, now: now)
        return short == "now" ? "Updated just now" : "Updated \(short) ago"
    }
}

extension FeaturedListCard {
    func isLive(at now: Date) -> Bool {
        live?.isLive(at: now) == true
    }

    func wasUpdatedRecently(now: Date) -> Bool {
        guard let contentChangedAt else { return false }
        return now.timeIntervalSince(contentChangedAt) < FeaturedBadge.recentWindow
    }

    /// The single media type a list is about, or nil when it mixes types.
    var singleMediaType: String? {
        mediaTypes.count == 1 ? mediaTypes.first : nil
    }

    /// The color of status text: the media type's stats color, or the default for mixed lists.
    var accentColor: Color {
        MediaTypeTheme.theme(for: singleMediaType ?? APIConstants.allMedia).statsColor
    }

    var usesSquareArtwork: Bool {
        singleMediaType == "music"
    }

    /// "10 films · Weekly"
    var countLabel: String {
        let count = "\(itemsCount.formatted()) \(FeaturedCountNoun.noun(for: mediaTypes, count: itemsCount))"
        return [count, cadence.label].compactMap { $0 }.joined(separator: " · ")
    }

    /// The people a tile stacks in place of posters: a people list's, when it has no media previews.
    var stackPeople: [PersonListEntry] {
        previews.isEmpty ? previewPeople : []
    }

    /// The rank to show on a preview, or on a person in place of previews, when the list is ranked.
    func rank(at index: Int) -> Int? {
        guard isRanked else { return nil }
        if previews.indices.contains(index) {
            return previews[index].media.position ?? index + 1
        }
        guard stackPeople.indices.contains(index) else { return nil }
        return stackPeople[index].position ?? index + 1
    }

    /// "CHART · Netflix · Updated 2h ago · Refreshes weekly", for the list detail.
    func metadataLine(now: Date) -> String {
        var parts: [String] = []
        if kind != .unknown {
            parts.append(kind.badgeLabel)
        }
        if let name = source?.name, !name.isEmpty {
            parts.append(name)
        }
        if let changed = contentChangedAt {
            parts.append(FeaturedRelativeTime.updatedLabel(from: changed, now: now))
        } else if let period = periodLabel {
            parts.append(period)
        }
        if let refresh = cadence.refreshLabel {
            parts.append(refresh)
        }
        return parts.joined(separator: " · ")
    }

    var accessibilitySummary: String {
        [name, countLabel, source?.name].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ", ")
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
    /// The landing's shelves without media types the user turned off. "Now" and "Across Media" keep a list unless
    /// every one of its media types is off.
    func visibleShelves(enabledMediaTypes: [String]) -> [FeaturedShelf] {
        let enabled = Set(enabledMediaTypes)
        return shelves.compactMap { shelf in
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
