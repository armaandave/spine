import Foundation

/// `GET /tracking/up-next/`: dated TV and anime episodes that haven't aired yet, soonest first
/// (docs/up-next-contract.md, UN-001 and UN-006).
nonisolated struct UpNextResponse: Decodable, Hashable {
    let results: [UpNextItem]
}

/// One title on Up next and the episode of it that airs next.
nonisolated struct UpNextItem: Decodable, Hashable, Identifiable {
    let media: MediaSummary
    let airing: UpNextAiring

    var id: String { media.id }
}

nonisolated struct UpNextAiring: Decodable, Hashable {
    /// "episode", "season_premiere" or "series_premiere". Anything else reads as an episode.
    let kind: String
    /// Nil for anime, which has no seasons.
    let seasonNumber: Int?
    /// Nil when the provider names a season with no episode list yet.
    let episodeNumber: Int?
    /// "YYYY-MM-DD", a day on the user's calendar.
    let airDate: String

    var isSeasonPremiere: Bool { kind == "season_premiere" }
    var isSeriesPremiere: Bool { kind == "series_premiere" }
}
