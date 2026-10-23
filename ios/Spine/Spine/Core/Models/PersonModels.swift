import Foundation

nonisolated struct PersonRef: Codable, Hashable, Identifiable {
    let source: String
    let id: String

    var sourceDisplayName: String {
        switch source.lowercased() {
        case "tmdb": "TMDB"
        case "hardcover": "Hardcover"
        case "openlibrary": "Open Library"
        case "musicbrainz": "MusicBrainz"
        case "mal": "MyAnimeList"
        case "mangaupdates": "MangaUpdates"
        case "anilist": "AniList"
        default: source
        }
    }
}

struct PersonSearchResult: Decodable, Hashable, Identifiable {
    let ref: PersonRef
    let name: String
    let profileUrl: String?
    let knownForDepartment: String?

    var id: String {
        "\(ref.source):\(ref.id)"
    }
}

struct PersonSearchResponse: Decodable, Equatable {
    let count: Int
    let next: String?
    let previous: String?
    let results: [PersonSearchResult]
    let unavailableSources: [String]

    private enum CodingKeys: String, CodingKey {
        case count
        case next
        case previous
        case results
        case unavailableSources
    }

    init(
        count: Int,
        next: String? = nil,
        previous: String? = nil,
        results: [PersonSearchResult],
        unavailableSources: [String] = []
    ) {
        self.count = count
        self.next = next
        self.previous = previous
        self.results = results
        self.unavailableSources = unavailableSources
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        count = try container.decode(Int.self, forKey: .count)
        next = try container.decodeIfPresent(String.self, forKey: .next)
        previous = try container.decodeIfPresent(String.self, forKey: .previous)
        results = try container.decode([PersonSearchResult].self, forKey: .results)
        unavailableSources = try container.decodeIfPresent([String].self, forKey: .unavailableSources) ?? []
    }
}

struct PersonDetail: Decodable, Identifiable, Hashable {
    let id: String
    let source: String
    let name: String
    let biography: String?
    let profileUrl: String?
    let knownForDepartment: String?
    let birthDate: String?
    let deathDate: String?
    let placeOfBirth: String?
    let popularity: Double?
    let filterOptions: MediaFilterOptionsResponse?
    let ratingPreparation: PersonRatingPreparation?
    let creditsPage: Int?
    let creditsNextPage: Int?
    let creditsComplete: Bool?
    let series: [MediaSeriesSummary]?
    let credits: PersonCredits
    let completion: CompletionProgress?
    let mediaTypeCompletions: [String: CompletionProgress]?
    let roleCompletions: [String: [String: CompletionProgress]]?

    init(
        id: String,
        source: String,
        name: String,
        biography: String? = nil,
        profileUrl: String? = nil,
        knownForDepartment: String? = nil,
        birthDate: String? = nil,
        deathDate: String? = nil,
        placeOfBirth: String? = nil,
        popularity: Double? = nil,
        filterOptions: MediaFilterOptionsResponse? = nil,
        ratingPreparation: PersonRatingPreparation? = nil,
        creditsPage: Int? = nil,
        creditsNextPage: Int? = nil,
        creditsComplete: Bool? = nil,
        series: [MediaSeriesSummary]? = nil,
        credits: PersonCredits,
        completion: CompletionProgress? = nil,
        mediaTypeCompletions: [String: CompletionProgress]? = nil,
        roleCompletions: [String: [String: CompletionProgress]]? = nil
    ) {
        self.id = id
        self.source = source
        self.name = name
        self.biography = biography
        self.profileUrl = profileUrl
        self.knownForDepartment = knownForDepartment
        self.birthDate = birthDate
        self.deathDate = deathDate
        self.placeOfBirth = placeOfBirth
        self.popularity = popularity
        self.filterOptions = filterOptions
        self.ratingPreparation = ratingPreparation
        self.creditsPage = creditsPage
        self.creditsNextPage = creditsNextPage
        self.creditsComplete = creditsComplete
        self.series = series
        self.credits = credits
        self.completion = completion
        self.mediaTypeCompletions = mediaTypeCompletions
        self.roleCompletions = roleCompletions
    }

    var ref: PersonRef {
        PersonRef(source: source, id: id)
    }

    var filmography: [MediaSummary] {
        credits.cast
    }

    var bookSeries: [BookSeriesSummary] {
        series(for: "book")
    }

    func series(for mediaType: String) -> [MediaSeriesSummary] {
        (series ?? []).filter { $0.mediaType == mediaType }
    }
}

enum PersonRatingPreparationState: String, Decodable, Hashable {
    case ready
    case pending
    case degraded
}

struct PersonRatingPreparation: Decodable, Hashable {
    let ratingSource: String
    let state: PersonRatingPreparationState
    let total: Int
    let ready: Int
    let unavailable: Int
    let failed: Int

    var processed: Int { ready + unavailable }
}

struct PersonCredits: Decodable, Hashable {
    let cast: [MediaSummary]

    init(cast: [MediaSummary] = []) {
        self.cast = cast
    }
}
