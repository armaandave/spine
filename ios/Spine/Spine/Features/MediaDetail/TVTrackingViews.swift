import SwiftUI

// The show, season and episode pages' TV pieces: season cards, episode ticks, the Up next row and the + menu.
// Every value comes from the server's TV state (plan section 2.3); nothing here works out tracking rules.

// MARK: Confirmed writes (TV-705)

/// Confirmed TV writes the page's loaded episode and season lists don't show yet: their ticks, unticks and marks,
/// and the season and episode states their responses named. The page drops it when a refresh replaces the detail,
/// so a confirmed tick never flickers back to unwatched while the refresh loads.
struct TVConfirmedWrites: Equatable {
    var watches = TVPendingWatches()
    var episodes: [TVEpisodeCoordinate: TVEpisodeState] = [:]
    var seasons: [Int: TVSeasonState] = [:]

    /// `projected` is what the write's projection showed while it was pending.
    mutating func record(projected: TVPendingWatches, envelope: TVWriteEnvelope) {
        for season in projected.markedSeasons {
            watches.episodes = watches.episodes.filter { $0.key.seasonNumber != season }
            watches.markedSeasons.insert(season)
        }
        if projected.markedShow {
            watches.episodes = watches.episodes.filter { $0.key.seasonNumber == 0 }
            watches.markedShow = true
        }
        watches.episodes.merge(projected.episodes) { _, new in new }
        if let episode = envelope.episode {
            episodes[episode.coordinate] = episode
            watches.episodes[episode.coordinate] = episode.watched
        }
        if let season = envelope.season {
            seasons[season.seasonNumber] = season
        }
        recordSeasonEpisodes(envelope.seasonEpisodes)
    }

    /// The server's answer for every episode of a season page: it replaces what the page recorded for them.
    private mutating func recordSeasonEpisodes(_ states: [TVEpisodeState]?) {
        for episode in states ?? [] {
            episodes[episode.coordinate] = episode
            watches.episodes[episode.coordinate] = episode.watched
        }
    }

    /// After an eye's undo (TV-302): nothing this page recorded under that eye is known to be watched any more, and
    /// the season states it recorded there are stale. The response's own season, and on a season page every
    /// episode's state (`season_episodes`: the ticks and logs the undo kept), replace them at once; elsewhere the
    /// refresh does.
    mutating func recordUnmark(seasonNumber: Int?, envelope: TVWriteEnvelope) {
        if let seasonNumber {
            watches.markedSeasons.remove(seasonNumber)
            watches.episodes = watches.episodes.filter { $0.key.seasonNumber != seasonNumber }
            episodes = episodes.filter { $0.key.seasonNumber != seasonNumber }
            seasons[seasonNumber] = nil
            if seasonNumber != 0 { watches.markedShow = false }
        } else {
            watches = TVPendingWatches()
            episodes = [:]
            seasons = [:]
        }
        if let season = envelope.season { seasons[season.seasonNumber] = season }
        recordSeasonEpisodes(envelope.seasonEpisodes)
    }
}

// MARK: Season cards (TV-004, TV-103, TV-200)

/// One card on a show page's season rail: a listed season, Specials, or a season the provider no longer lists.
struct TVSeasonCardModel: Identifiable, Hashable {
    let seasonNumber: Int
    let title: String
    let imageUrl: String?
    let episodeCount: Int?
    let tv: TVSeasonState?
    let listed: Bool

    var id: Int { seasonNumber }

    /// Listed regular seasons in the provider's order, then Specials, then the tracked seasons the provider no
    /// longer lists, by number (TV-004, TV-103).
    static func cards(seasons: [SeasonSummary], unlisted: [TVUnlistedSeason]?) -> [TVSeasonCardModel] {
        let listed = seasons.map {
            TVSeasonCardModel(
                seasonNumber: $0.seasonNumber,
                title: $0.seasonNumber == 0 ? TVTrackingPresentation.seasonTitle(0) : $0.title,
                imageUrl: $0.imageUrl,
                episodeCount: $0.episodeCount,
                tv: $0.tv,
                listed: true
            )
        }
        let listedNumbers = Set(listed.map(\.seasonNumber))
        let gone = (unlisted ?? [])
            .filter { !listedNumbers.contains($0.seasonNumber) }
            .sorted { $0.seasonNumber < $1.seasonNumber }
            .map {
                TVSeasonCardModel(
                    seasonNumber: $0.seasonNumber,
                    title: $0.title,
                    imageUrl: nil,
                    episodeCount: nil,
                    tv: $0.tv,
                    listed: false
                )
            }
        return listed.filter { $0.seasonNumber != 0 } + listed.filter { $0.seasonNumber == 0 } + gone
    }

    /// The card's line: "Watched", "Airing", "6 of 10" or "9 of 10 · E4 not watched" (TV-200), counting a pending
    /// eye mark. A season with no stored episode list shows no count (TV-103); one the user hasn't started shows
    /// its episode count.
    func label(pending: TVPendingWatches = TVPendingWatches()) -> String? {
        guard let tv else { return episodeCountText }
        let pendingMark = pending.markedSeasons.contains(seasonNumber)
            || (pending.markedShow && !tv.isSpecials)
        if pendingMark, listed, ![.unaired, .unknown].contains(tv.state) {
            return tv.nextAirDate == nil ? "Watched" : "Airing"
        }
        if let label = TVTrackingPresentation.seasonLabel(for: tv) { return label }
        switch tv.state {
        case .unknown:
            return nil
        case .notStarted, .unaired:
            return listed ? episodeCountText : nil
        case .watched, .airing, .inProgress:
            return nil
        }
    }

    private var episodeCountText: String? {
        guard let episodeCount else { return nil }
        return episodeCount == 1 ? "1 episode" : "\(episodeCount) episodes"
    }
}

struct TVSeasonCard: View {
    let card: TVSeasonCardModel
    let label: String?
    let width: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            MediaArtwork(url: card.imageUrl, title: card.title, slot: .seasonCard, mediaType: "season")
            Text(card.title)
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(.white)
                .lineLimit(2)
            if let label {
                Text(label)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.55))
                    .lineLimit(2)
            }
        }
        .frame(width: width, alignment: .topLeading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([card.title, label].compactMap { $0 }.joined(separator: ", "))
    }
}

// MARK: Episode rows (TV-100, TV-103, TV-108)

/// One row of a season page's episode list: a listed episode, or a tracked one the provider no longer lists.
struct TVEpisodeRowModel: Identifiable {
    let episodeNumber: Int
    let title: String
    /// Nil for an episode the provider no longer lists.
    let summary: EpisodeSummary?
    let tv: TVEpisodeState?

    var listed: Bool { summary != nil }
    var id: String { listed ? "listed-\(episodeNumber)" : "unlisted-\(episodeNumber)" }

    /// Listed episodes in the provider's order, then the unlisted ones by number, last (TV-103).
    static func rows(episodes: [EpisodeSummary], unlisted: [TVUnlistedEpisode]?) -> [TVEpisodeRowModel] {
        let listed = episodes.map {
            TVEpisodeRowModel(episodeNumber: $0.episodeNumber, title: $0.title, summary: $0, tv: $0.tv)
        }
        let gone = (unlisted ?? [])
            .sorted { $0.episodeNumber < $1.episodeNumber }
            .map { TVEpisodeRowModel(episodeNumber: $0.episodeNumber, title: $0.title, summary: nil, tv: $0.tv) }
        return listed + gone
    }

    /// The show page's Up next as a row of its season's episode list (TV-105): the same still, number, title, air
    /// date and tick. Up next has aired and isn't watched, so the row always has a tick.
    static func upNext(_ pointer: TVEpisodePointer) -> TVEpisodeRowModel {
        let name = pointer.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let title = name.isEmpty ? "Episode \(pointer.episodeNumber)" : name
        let tv = TVEpisodeState(
            seasonNumber: pointer.seasonNumber,
            episodeNumber: pointer.episodeNumber,
            aired: true,
            airDate: pointer.airDate,
            isUpNext: true
        )
        let summary = EpisodeSummary(
            episodeNumber: pointer.episodeNumber,
            title: title,
            overview: nil,
            airDate: pointer.airDate,
            runtime: nil,
            imageUrl: pointer.imageUrl,
            rating: nil,
            tv: tv
        )
        return TVEpisodeRowModel(episodeNumber: pointer.episodeNumber, title: title, summary: summary, tv: tv)
    }
}

/// What the trailing side of an episode row shows (TV-100): a tick, or for an unaired episode its air date, or
/// "TBA" when it has none.
enum TVEpisodeTrailing: Equatable {
    case tick(watched: Bool)
    case airDate(String)
    case none

    static func resolve(_ tv: TVEpisodeState?, isWatched: Bool, calendar: Calendar = .autoupdatingCurrent) -> TVEpisodeTrailing {
        guard let tv else { return .none }
        if isWatched { return .tick(watched: true) }
        if !tv.aired {
            guard let date = TVTrackingPresentation.calendarDate(tv.airDate, calendar: calendar) else {
                return .airDate("TBA")
            }
            return .airDate(TVTrackingPresentation.shortDate(date, calendar: calendar))
        }
        return tv.listed ? .tick(watched: false) : .none
    }

    /// When an unaired episode airs, as its row reads to VoiceOver: "Airs Mar 12", or "Air date to be announced"
    /// for "TBA". Nil when the row has a tick or nothing.
    var accessibilityAirDate: String? {
        guard case let .airDate(text) = self else { return nil }
        return text == "TBA" ? "Air date to be announced" : "Airs \(text)"
    }
}

/// The episode tick: an SF Symbol button with a haptic (TV-100, TV-102).
struct TVEpisodeTickButton: View {
    let isWatched: Bool
    let episodeTitle: String
    let action: () -> Void
    @State private var taps = 0

    var body: some View {
        Button {
            taps += 1
            action()
        } label: {
            Image(systemName: isWatched ? "checkmark.circle.fill" : "circle")
                .font(.system(size: 24, weight: .regular))
                .foregroundStyle(isWatched ? Color.green : Color.white.opacity(0.5))
                .contentTransition(.symbolEffect(.replace))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.impact(weight: .medium), trigger: taps)
        .accessibilityLabel(isWatched ? "Watched, \(episodeTitle)" : "Mark \(episodeTitle) watched")
        .accessibilityHint(isWatched ? "Unticks this episode" : "Ticks this episode watched today")
        .accessibilityAddTraits(isWatched ? .isSelected : [])
        .accessibilityIdentifier("tv.episode-tick")
    }
}

// MARK: Confirmations (TV-003, TV-108, TV-300, TV-302, TV-705)

/// A native confirmation the page asks before a TV write.
struct TVPageConfirmation: Identifiable, Equatable {
    enum Kind: Equatable {
        /// The empty show eye (`mark_confirm`).
        case mark(seasonNumber: Int?)
        /// A filled eye that unmarks everything watched under it (`unmark_confirm`, TV-302 case 3).
        case unmark(seasonNumber: Int?)
        /// Mark watched up to here (TV-108).
        case markUpTo(TVEpisodeCoordinate)
        /// Remove from Library (TV-003).
        case remove
    }

    let id = UUID()
    let kind: Kind
    let prompt: String
    /// The state version the dialog's count came from. Unmark and Remove send it, so a show that changed since
    /// changes nothing and the page asks again with the new count (TV-705).
    let expectedVersion: Int?

    var confirmTitle: String {
        switch kind {
        case .mark, .markUpTo: "Mark Watched"
        case .unmark: "Mark Unwatched"
        case .remove: "Remove"
        }
    }

    var isDestructive: Bool {
        switch kind {
        case .unmark, .remove: true
        case .mark, .markUpTo: false
        }
    }

    static func == (lhs: TVPageConfirmation, rhs: TVPageConfirmation) -> Bool { lhs.id == rhs.id }
}

/// `error.tv` of a 409 `tv_state_changed`: the show's new state version and the new count to ask about.
struct TVStateChange: Equatable {
    let stateVersion: Int
    let count: Int
}

extension APIError {
    var tvStateChange: TVStateChange? {
        guard apiErrorCode == "tv_state_changed",
              case let .httpStatus(_, body?) = self,
              let data = body.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let error = json["error"] as? [String: Any],
              let tv = error["tv"] as? [String: Any],
              let version = (tv["state_version"] as? NSNumber)?.intValue,
              let count = (tv["count"] as? NSNumber)?.intValue
        else { return nil }
        return TVStateChange(stateVersion: version, count: count)
    }
}

// MARK: + menu (TV-005, TV-007, TV-003)

/// The show's or a season's + menu, as a native list: base options always listed, the current status shown as
/// selected, and unavailable options with the server's reason. Drop and Remove ask first.
struct TVActionSheet: View {
    let title: String
    let items: [TVMenuItem]
    /// "Remove this show? Your 13 watched episodes will be unmarked."
    let removePrompt: String
    let onCommand: (TVMenuItem.Command) -> Void
    /// What the sheet showed when a choice was made, kept while it slides away so rows don't change under the
    /// finger and a second tap is ignored.
    @State private var committed: [TVMenuItem]?
    @State private var confirmsDrop = false
    @State private var confirmsRemove = false

    var body: some View {
        let shown = committed ?? items
        NavigationStack {
            List {
                Section {
                    ForEach(shown.filter { $0.command != .confirmRemove }) { item in
                        row(item)
                    }
                }
                let removal = shown.filter { $0.command == .confirmRemove }
                if !removal.isEmpty {
                    Section {
                        ForEach(removal) { item in
                            row(item, role: .destructive)
                        }
                    }
                }
            }
            .fitsSheetToContent()
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .confirmationDialog(TVTrackingPresentation.dropPrompt, isPresented: $confirmsDrop, titleVisibility: .visible) {
                Button("Drop", role: .destructive) { commit(.action(.drop)) }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog(removePrompt, isPresented: $confirmsRemove, titleVisibility: .visible) {
                Button("Remove", role: .destructive) { commit(.confirmRemove) }
                Button("Cancel", role: .cancel) {}
            }
        }
        .preferredColorScheme(.dark)
    }

    private func row(_ item: TVMenuItem, role: ButtonRole? = nil) -> some View {
        Button(role: role) {
            tap(item)
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                Label(item.isSelected ? "\(item.title) ✓" : item.title, systemImage: item.systemImage)
                if let reason = item.reason {
                    Text(reason)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .disabled(!item.isAvailable)
        .accessibilityIdentifier("tv.menu.\(item.key)")
    }

    private func tap(_ item: TVMenuItem) {
        guard item.isAvailable, committed == nil else { return }
        switch item.command {
        case .confirmDrop:
            confirmsDrop = true
        case .confirmRemove:
            confirmsRemove = true
        default:
            commit(item.command)
        }
    }

    private func commit(_ command: TVMenuItem.Command) {
        guard committed == nil else { return }
        committed = items
        UISelectionFeedbackGenerator().selectionChanged()
        onCommand(command)
    }
}
