import Foundation

struct MediaRef: Codable, Hashable, Identifiable {
    let itemId: Int?
    let source: String
    let mediaType: String
    let mediaId: String
    let seasonNumber: Int?
    let episodeNumber: Int?

    var id: String {
        [
            source,
            mediaType,
            mediaId,
            seasonNumber.map(String.init) ?? "_",
            episodeNumber.map(String.init) ?? "_",
        ].joined(separator: ":")
    }

    func displayTitle(_ title: String) -> String {
        guard mediaType == "season", let seasonNumber else { return title }
        return "\(title) S\(seasonNumber)"
    }

    var isEpisode: Bool {
        mediaType == "episode"
    }

    var isSingleWeight: Bool {
        mediaType == "movie" || mediaType == "music"
    }

    var usesFiveStarRatingScale: Bool {
        isSingleWeight || ["book", "game"].contains(mediaType)
    }

    var usesCalendarConsumptionDate: Bool {
        isSingleWeight || ["book", "game"].contains(mediaType)
    }

    var episodeCode: String? {
        guard isEpisode, let seasonNumber, let episodeNumber else { return nil }
        return String(format: "S%02dE%02d", seasonNumber, episodeNumber)
    }

    var parentTVRef: MediaRef? {
        guard mediaType == "season" || isEpisode else { return nil }
        return MediaRef(
            itemId: nil,
            source: source,
            mediaType: "tv",
            mediaId: mediaId,
            seasonNumber: nil,
            episodeNumber: nil
        )
    }

    var parentSeasonRef: MediaRef? {
        guard isEpisode, let seasonNumber else { return nil }
        return MediaRef(
            itemId: nil,
            source: source,
            mediaType: "season",
            mediaId: mediaId,
            seasonNumber: seasonNumber,
            episodeNumber: nil
        )
    }

    var repeatLabel: String {
        switch mediaType {
        case "book", "manga", "comic":
            "Reread"
        case "game", "boardgame":
            "Replay"
        case "music":
            "Relisten"
        default:
            "Rewatch"
        }
    }

    var consumedDateLabel: String {
        switch mediaType {
        case "music": "Date listened"
        case "movie": "Date watched"
        case "game": "Completion date"
        default: "Date"
        }
    }

    func trackingStatusLabel(_ status: String) -> String {
        if mediaType == "game" { return status.lowercased() == "in progress" ? "Playing" : status }
        if mediaType == "book" {
            switch status {
            case "Planning": return "To Read"
            case "In progress": return "Currently Reading"
            case "Dropped": return "Did Not Finish"
            case "Completed": return "Read"
            default: return status
            }
        }
        guard mediaType == "music" else { return status }
        switch status {
        case "In progress":
            return "Listening"
        case "Completed":
            return "Listened"
        case "Dropped":
            return "Stopped"
        default:
            return status
        }
    }

    func episodeRef(episodeNumber: Int, itemId: Int? = nil) -> MediaRef? {
        guard mediaType == "season", let seasonNumber else { return nil }
        return MediaRef(
            itemId: itemId,
            source: source,
            mediaType: "episode",
            mediaId: mediaId,
            seasonNumber: seasonNumber,
            episodeNumber: episodeNumber
        )
    }
}

struct MediaBrowsingContext: Hashable {
    let refs: [MediaRef]
    let selectedID: MediaRef.ID

    init(refs: [MediaRef], selected: MediaRef) {
        var seen = Set<MediaRef.ID>()
        var uniqueRefs = refs.filter { seen.insert($0.id).inserted }
        if !seen.contains(selected.id) {
            uniqueRefs.append(selected)
        }
        self.refs = uniqueRefs
        selectedID = selected.id
    }
}

struct MediaBrowsingSelection: Hashable, Identifiable {
    let ref: MediaRef
    let context: MediaBrowsingContext

    var id: MediaRef.ID { ref.id }

    init(ref: MediaRef, within refs: [MediaRef]) {
        self.ref = ref
        context = MediaBrowsingContext(refs: refs, selected: ref)
    }
}

struct MediaSummary: Codable, Identifiable, Hashable {
    let ref: MediaRef
    let title: String
    let preferredTitle: String?
    let relation: String?
    let subtitle: String?
    let overview: String?
    let imageUrl: String?
    let posterUrl: String?
    let customPosterUrl: String?
    let backdropUrl: String?
    let customBackdropUrl: String?
    let posterOrientation: PosterOrientation?
    let posterAspectRatio: Double?
    let posterWidth: Int?
    let posterHeight: Int?
    let posterAccentColor: String?
    let logoUrl: String?
    let logoWidth: Int?
    let logoHeight: Int?
    let logoAspectRatio: Double?
    let releaseDate: String?
    let genres: [String]
    let languages: [String]
    let roles: [String]
    let creditRoles: [String]
    let defaultSource: String?
    let position: Int?
    var userState: UserMediaState?

    var id: String { ref.id }

    var displayPosterURL: String? {
        customPosterUrl ?? posterUrl ?? imageUrl
    }

    var displayBackdropURL: String? {
        customBackdropUrl ?? backdropUrl
    }

    var displayTitle: String {
        if let preferredTitle, !preferredTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return preferredTitle
        }
        return ref.displayTitle(title)
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case title
        case preferredTitle = "displayTitle"
        case relation
        case subtitle
        case overview
        case imageUrl
        case posterUrl
        case customPosterUrl
        case backdropUrl
        case customBackdropUrl
        case posterOrientation
        case posterAspectRatio
        case posterWidth
        case posterHeight
        case posterAccentColor
        case logoUrl
        case logoWidth
        case logoHeight
        case logoAspectRatio
        case releaseDate
        case genres
        case languages
        case roles
        case creditRoles
        case defaultSource
        case position
        case userState
    }

    init(
        ref: MediaRef,
        title: String,
        preferredTitle: String? = nil,
        relation: String? = nil,
        subtitle: String? = nil,
        overview: String? = nil,
        imageUrl: String? = nil,
        posterUrl: String? = nil,
        customPosterUrl: String? = nil,
        backdropUrl: String? = nil,
        customBackdropUrl: String? = nil,
        posterOrientation: PosterOrientation? = nil,
        posterAspectRatio: Double? = nil,
        posterWidth: Int? = nil,
        posterHeight: Int? = nil,
        posterAccentColor: String? = nil,
        logoUrl: String? = nil,
        logoWidth: Int? = nil,
        logoHeight: Int? = nil,
        logoAspectRatio: Double? = nil,
        releaseDate: String? = nil,
        genres: [String] = [],
        languages: [String] = [],
        roles: [String] = [],
        creditRoles: [String] = [],
        defaultSource: String? = nil,
        position: Int? = nil,
        userState: UserMediaState? = nil
    ) {
        self.ref = ref
        self.title = title
        self.preferredTitle = preferredTitle
        self.relation = relation
        self.subtitle = subtitle
        self.overview = overview
        self.imageUrl = imageUrl
        self.posterUrl = posterUrl ?? imageUrl
        self.customPosterUrl = customPosterUrl
        self.backdropUrl = backdropUrl
        self.customBackdropUrl = customBackdropUrl
        self.posterOrientation = posterOrientation
        self.posterAspectRatio = posterAspectRatio
        self.posterWidth = posterWidth
        self.posterHeight = posterHeight
        self.posterAccentColor = posterAccentColor
        self.logoUrl = logoUrl
        self.logoWidth = logoWidth
        self.logoHeight = logoHeight
        self.logoAspectRatio = logoAspectRatio
        self.releaseDate = releaseDate
        self.genres = genres
        self.languages = languages
        self.roles = roles
        self.creditRoles = creditRoles
        self.defaultSource = defaultSource
        self.position = position
        self.userState = userState
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        self.init(
            ref: try container.decode(MediaRef.self, forKey: .ref),
            title: try container.decode(String.self, forKey: .title),
            preferredTitle: try container.decodeIfPresent(String.self, forKey: .preferredTitle),
            relation: try container.decodeIfPresent(String.self, forKey: .relation),
            subtitle: try container.decodeIfPresent(String.self, forKey: .subtitle),
            overview: try container.decodeIfPresent(String.self, forKey: .overview),
            imageUrl: imageUrl,
            posterUrl: try container.decodeIfPresent(String.self, forKey: .posterUrl) ?? imageUrl,
            customPosterUrl: try container.decodeIfPresent(String.self, forKey: .customPosterUrl),
            backdropUrl: try container.decodeIfPresent(String.self, forKey: .backdropUrl),
            customBackdropUrl: try container.decodeIfPresent(String.self, forKey: .customBackdropUrl),
            posterOrientation: try container.decodeIfPresent(PosterOrientation.self, forKey: .posterOrientation),
            posterAspectRatio: try container.decodeIfPresent(Double.self, forKey: .posterAspectRatio),
            posterWidth: try container.decodeIfPresent(Int.self, forKey: .posterWidth),
            posterHeight: try container.decodeIfPresent(Int.self, forKey: .posterHeight),
            posterAccentColor: try container.decodeIfPresent(String.self, forKey: .posterAccentColor),
            logoUrl: try container.decodeIfPresent(String.self, forKey: .logoUrl),
            logoWidth: try container.decodeIfPresent(Int.self, forKey: .logoWidth),
            logoHeight: try container.decodeIfPresent(Int.self, forKey: .logoHeight),
            logoAspectRatio: try container.decodeIfPresent(Double.self, forKey: .logoAspectRatio),
            releaseDate: try container.decodeIfPresent(String.self, forKey: .releaseDate),
            genres: try container.decodeIfPresent([String].self, forKey: .genres) ?? [],
            languages: try container.decodeIfPresent([String].self, forKey: .languages) ?? [],
            roles: try container.decodeIfPresent([String].self, forKey: .roles) ?? [],
            creditRoles: try container.decodeIfPresent([String].self, forKey: .creditRoles) ?? [],
            defaultSource: try container.decodeIfPresent(String.self, forKey: .defaultSource),
            position: try container.decodeIfPresent(Int.self, forKey: .position),
            userState: try container.decodeIfPresent(UserMediaState.self, forKey: .userState)
        )
    }
}

struct MediaDiscoverRequest: Hashable, Identifiable {
    enum Filter: Hashable {
        case genre(String)
        case year(String)
        case platform(String)

        var value: String {
            switch self {
            case let .genre(value), let .year(value), let .platform(value):
                value
            }
        }

        var queryItem: URLQueryItem {
            switch self {
            case let .genre(value):
                URLQueryItem(name: "genre", value: value)
            case let .year(value):
                URLQueryItem(name: "year", value: value)
            case let .platform(value):
                URLQueryItem(name: "platform", value: value)
            }
        }
    }

    let mediaType: String
    let source: String?
    let filter: Filter
    var page: String?
    var pageSize: Int?

    var id: String {
        [mediaType, source ?? "_", filter.queryItem.name, filter.value].joined(separator: ":")
    }

    var queryItems: [URLQueryItem] {
        var items = [
            URLQueryItem(name: "media_type", value: mediaType),
            URLQueryItem(name: "sort", value: "vote_count"),
            filter.queryItem,
        ]
        if let source {
            items.append(URLQueryItem(name: "source", value: source))
        }
        if let page {
            items.append(URLQueryItem(name: "page", value: page))
        }
        if let pageSize {
            items.append(URLQueryItem(name: "page_size", value: String(pageSize)))
        }
        return items
    }

    var title: String {
        "\(filter.value) · \(MediaTypeTheme.theme(for: mediaType).displayName)"
    }

    static func detailPillRequest(ref: MediaRef, filter: Filter) -> MediaDiscoverRequest? {
        let mediaType = ref.mediaType == "season" ? "tv" : ref.mediaType
        switch (mediaType, filter) {
        case ("movie", .genre), ("movie", .year),
             ("tv", .genre), ("tv", .year),
             ("anime", .genre),
             ("manga", .genre),
             ("book", .genre), ("book", .year),
             ("game", .genre), ("game", .year), ("game", .platform),
             ("music", .genre):
            return MediaDiscoverRequest(
                mediaType: mediaType,
                source: mediaType == "manga" ? "mal" : ref.source,
                filter: filter,
                page: nil,
                pageSize: nil
            )
        default:
            return nil
        }
    }
}

struct MusicDetail: Decodable {
    let releaseGroupMbid: String
    let primaryType: String?
    let secondaryTypes: [String]
    let disambiguation: String?
    let annotation: String?
    let firstReleaseDate: String?
    let releaseCount: Int?
    let artistCredit: [MusicArtistCredit]
    let coverArt: MusicCoverArt
    let representativeRelease: MusicRepresentativeRelease?

    enum CodingKeys: String, CodingKey {
        case releaseGroupMbid
        case primaryType
        case secondaryTypes
        case disambiguation
        case annotation
        case firstReleaseDate
        case releaseCount
        case artistCredit
        case coverArt
        case representativeRelease
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        releaseGroupMbid = try container.decode(String.self, forKey: .releaseGroupMbid)
        primaryType = try container.decodeIfPresent(String.self, forKey: .primaryType)
        secondaryTypes = try container.decodeIfPresent([String].self, forKey: .secondaryTypes) ?? []
        disambiguation = try container.decodeIfPresent(String.self, forKey: .disambiguation)
        annotation = try container.decodeIfPresent(String.self, forKey: .annotation)
        firstReleaseDate = try container.decodeIfPresent(String.self, forKey: .firstReleaseDate)
        releaseCount = try container.decodeIfPresent(Int.self, forKey: .releaseCount)
        artistCredit = try container.decodeIfPresent([MusicArtistCredit].self, forKey: .artistCredit) ?? []
        coverArt = try container.decode(MusicCoverArt.self, forKey: .coverArt)
        representativeRelease = try container.decodeIfPresent(
            MusicRepresentativeRelease.self,
            forKey: .representativeRelease
        )
    }
}

struct MusicCoverArt: Decodable {
    let source: String
    let releaseGroupMbid: String
    let fallbackUsed: Bool
}

struct MusicRepresentativeRelease: Decodable {
    let releaseMbid: String
    let title: String
    let status: String?
    let date: String?
    let country: String?
    let barcode: String?
    let selectionBasis: String
    let labels: [MusicLabel]
    let format: String?
    let isDeluxeOrRemastered: Bool
    let streamingLinks: [MusicStreamingLink]
    let discCount: Int
    let trackCount: Int
    let media: [MusicMedium]

    enum CodingKeys: String, CodingKey {
        case releaseMbid
        case title
        case status
        case date
        case country
        case barcode
        case selectionBasis
        case labels
        case format
        case isDeluxeOrRemastered
        case streamingLinks
        case discCount
        case trackCount
        case media
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        releaseMbid = try container.decode(String.self, forKey: .releaseMbid)
        title = try container.decode(String.self, forKey: .title)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        date = try container.decodeIfPresent(String.self, forKey: .date)
        country = try container.decodeIfPresent(String.self, forKey: .country)
        barcode = try container.decodeIfPresent(String.self, forKey: .barcode)
        selectionBasis = try container.decode(String.self, forKey: .selectionBasis)
        labels = try container.decodeIfPresent([MusicLabel].self, forKey: .labels) ?? []
        format = try container.decodeIfPresent(String.self, forKey: .format)
        isDeluxeOrRemastered = try container.decode(Bool.self, forKey: .isDeluxeOrRemastered)
        streamingLinks = try container.decodeIfPresent(
            [MusicStreamingLink].self,
            forKey: .streamingLinks
        ) ?? []
        discCount = try container.decode(Int.self, forKey: .discCount)
        trackCount = try container.decode(Int.self, forKey: .trackCount)
        media = try container.decodeIfPresent([MusicMedium].self, forKey: .media) ?? []
    }
}

struct MusicLabel: Decodable {
    let labelMbid: String?
    let name: String?
    let catalogNumber: String?
}

struct MusicStreamingLink: Decodable {
    let service: String
    let url: String
}

struct MusicMedium: Decodable {
    let mediumMbid: String?
    let position: Int
    let title: String?
    let format: String?
    let trackCount: Int
    let tracks: [MusicTrack]

    enum CodingKeys: String, CodingKey {
        case mediumMbid
        case position
        case title
        case format
        case trackCount
        case tracks
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        mediumMbid = try container.decodeIfPresent(String.self, forKey: .mediumMbid)
        position = try container.decode(Int.self, forKey: .position)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        format = try container.decodeIfPresent(String.self, forKey: .format)
        trackCount = try container.decode(Int.self, forKey: .trackCount)
        tracks = try container.decodeIfPresent([MusicTrack].self, forKey: .tracks) ?? []
    }
}

struct MusicTrack: Decodable, Identifiable {
    let trackMbid: String
    let discNumber: Int
    let position: Int
    let number: String
    let title: String
    let lengthMs: Int?
    let artistCredit: [MusicArtistCredit]
    let recording: MusicRecording

    var id: String { trackMbid }

    enum CodingKeys: String, CodingKey {
        case trackMbid
        case discNumber
        case position
        case number
        case title
        case lengthMs
        case artistCredit
        case recording
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        trackMbid = try container.decode(String.self, forKey: .trackMbid)
        discNumber = try container.decode(Int.self, forKey: .discNumber)
        position = try container.decode(Int.self, forKey: .position)
        number = try container.decode(String.self, forKey: .number)
        title = try container.decode(String.self, forKey: .title)
        lengthMs = try container.decodeIfPresent(Int.self, forKey: .lengthMs)
        artistCredit = try container.decodeIfPresent([MusicArtistCredit].self, forKey: .artistCredit) ?? []
        recording = try container.decode(MusicRecording.self, forKey: .recording)
    }
}

struct MusicSongSelection: Identifiable {
    let album: MediaRef
    let recordingMbid: String
    let artworkURL: String?

    var id: String { "\(album.id):\(recordingMbid)" }

    init(album: MediaRef, track: MusicTrack, artworkURL: String?) {
        self.album = album
        recordingMbid = track.recording.recordingMbid
        self.artworkURL = artworkURL
    }
}

struct MusicRecording: Decodable {
    let recordingMbid: String
    let title: String
    let lengthMs: Int?
    let disambiguation: String?
    let firstReleaseDate: String?
    let isVideo: Bool
    let isrcs: [String]

    enum CodingKeys: String, CodingKey {
        case recordingMbid
        case title
        case lengthMs
        case disambiguation
        case firstReleaseDate
        case isVideo
        case isrcs
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        recordingMbid = try container.decode(String.self, forKey: .recordingMbid)
        title = try container.decode(String.self, forKey: .title)
        lengthMs = try container.decodeIfPresent(Int.self, forKey: .lengthMs)
        disambiguation = try container.decodeIfPresent(String.self, forKey: .disambiguation)
        firstReleaseDate = try container.decodeIfPresent(String.self, forKey: .firstReleaseDate)
        isVideo = try container.decode(Bool.self, forKey: .isVideo)
        isrcs = try container.decodeIfPresent([String].self, forKey: .isrcs) ?? []
    }
}

struct MusicArtistCredit: Decodable {
    let artistMbid: String?
    let name: String
    let joinPhrase: String

    var personRef: PersonRef? {
        guard let artistMbid = artistMbid?.trimmingCharacters(in: .whitespacesAndNewlines),
              !artistMbid.isEmpty
        else { return nil }
        return PersonRef(source: "musicbrainz", id: artistMbid)
    }
}

struct MusicRecordingCredit: Decodable {
    let artistMbid: String?
    let name: String
    let roles: [String]

    var personRef: PersonRef? {
        guard let artistMbid = artistMbid?.trimmingCharacters(in: .whitespacesAndNewlines),
              !artistMbid.isEmpty
        else { return nil }
        return PersonRef(source: "musicbrainz", id: artistMbid)
    }

    enum CodingKeys: String, CodingKey {
        case artistMbid
        case name
        case roles
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        artistMbid = try container.decodeIfPresent(String.self, forKey: .artistMbid)
        name = try container.decode(String.self, forKey: .name)
        roles = try container.decodeIfPresent([String].self, forKey: .roles) ?? []
    }
}

struct MusicRecordingAppearance: Decodable {
    let releaseMbid: String
    let title: String
    let status: String?
    let date: String?
    let country: String?
    let barcode: String?
    let releaseGroupMbid: String?
}

struct MusicWorkRelationship: Decodable {
    let workMbid: String
    let title: String
    let relationshipType: String?
    let iswcs: [String]
    let language: String?
    let credits: [MusicRecordingCredit]

    enum CodingKeys: String, CodingKey {
        case workMbid
        case title
        case relationshipType
        case iswcs
        case language
        case credits
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        workMbid = try container.decode(String.self, forKey: .workMbid)
        title = try container.decode(String.self, forKey: .title)
        relationshipType = try container.decodeIfPresent(String.self, forKey: .relationshipType)
        iswcs = try container.decodeIfPresent([String].self, forKey: .iswcs) ?? []
        language = try container.decodeIfPresent(String.self, forKey: .language)
        credits = try container.decodeIfPresent([MusicRecordingCredit].self, forKey: .credits) ?? []
    }
}

struct MusicAlternativeRecording: Decodable {
    let recordingMbid: String
    let relationshipType: String?
    let direction: String?
    let title: String
    let artistCredit: [MusicArtistCredit]
    let lengthMs: Int?
    let disambiguation: String?

    enum CodingKeys: String, CodingKey {
        case recordingMbid
        case relationshipType
        case direction
        case title
        case artistCredit
        case lengthMs
        case disambiguation
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        recordingMbid = try container.decode(String.self, forKey: .recordingMbid)
        relationshipType = try container.decodeIfPresent(String.self, forKey: .relationshipType)
        direction = try container.decodeIfPresent(String.self, forKey: .direction)
        title = try container.decode(String.self, forKey: .title)
        artistCredit = try container.decodeIfPresent([MusicArtistCredit].self, forKey: .artistCredit) ?? []
        lengthMs = try container.decodeIfPresent(Int.self, forKey: .lengthMs)
        disambiguation = try container.decodeIfPresent(String.self, forKey: .disambiguation)
    }
}

struct MusicRecordingRating: Decodable {
    let value: Double?
    let votesCount: Int?
    let maxValue: Int?
}

struct MusicRecordingContext: Decodable {
    let releaseMbid: String
    let mediumMbid: String?
    let track: MusicTrack
}

struct MusicRecordingDetail: Decodable {
    let recordingMbid: String
    let title: String
    let artistCredit: [MusicArtistCredit]
    let lengthMs: Int?
    let isrcs: [String]
    let disambiguation: String?
    let firstReleaseDate: String?
    let isVideo: Bool
    let genres: [String]
    let rating: MusicRecordingRating?
    let annotation: String?
    let works: [MusicWorkRelationship]
    let alternativeRecordings: [MusicAlternativeRecording]
    let sourceUrl: String?
    let externalLinks: [String: String]
    let parentAlbum: MediaSummary
    let albums: [MediaSummary]
    let releases: [MusicRecordingAppearance]
    let contextRelease: MusicRecordingContext
    let imageUrl: String?
    let capabilities: [String: Bool]

    enum CodingKeys: String, CodingKey {
        case recordingMbid
        case title
        case artistCredit
        case lengthMs
        case isrcs
        case disambiguation
        case firstReleaseDate
        case isVideo
        case genres
        case rating
        case annotation
        case works
        case alternativeRecordings
        case sourceUrl
        case externalLinks
        case parentAlbum
        case albums
        case releases
        case contextRelease
        case imageUrl
        case capabilities
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        recordingMbid = try container.decode(String.self, forKey: .recordingMbid)
        title = try container.decode(String.self, forKey: .title)
        artistCredit = try container.decodeIfPresent([MusicArtistCredit].self, forKey: .artistCredit) ?? []
        lengthMs = try container.decodeIfPresent(Int.self, forKey: .lengthMs)
        isrcs = try container.decodeIfPresent([String].self, forKey: .isrcs) ?? []
        disambiguation = try container.decodeIfPresent(String.self, forKey: .disambiguation)
        firstReleaseDate = try container.decodeIfPresent(String.self, forKey: .firstReleaseDate)
        isVideo = try container.decode(Bool.self, forKey: .isVideo)
        genres = try container.decodeIfPresent([String].self, forKey: .genres) ?? []
        rating = try container.decodeIfPresent(MusicRecordingRating.self, forKey: .rating)
        annotation = try container.decodeIfPresent(String.self, forKey: .annotation)
        works = try container.decodeIfPresent([MusicWorkRelationship].self, forKey: .works) ?? []
        alternativeRecordings = try container.decodeIfPresent(
            [MusicAlternativeRecording].self,
            forKey: .alternativeRecordings
        ) ?? []
        sourceUrl = try container.decodeIfPresent(String.self, forKey: .sourceUrl)
        externalLinks = try container.decodeIfPresent([String: String].self, forKey: .externalLinks) ?? [:]
        parentAlbum = try container.decode(MediaSummary.self, forKey: .parentAlbum)
        albums = try container.decodeIfPresent([MediaSummary].self, forKey: .albums) ?? []
        releases = try container.decodeIfPresent([MusicRecordingAppearance].self, forKey: .releases) ?? []
        contextRelease = try container.decode(MusicRecordingContext.self, forKey: .contextRelease)
        imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        capabilities = try container.decodeIfPresent([String: Bool].self, forKey: .capabilities) ?? [:]
    }
}

struct MediaDetail: Decodable, Identifiable {
    let ref: MediaRef
    let title: String
    let preferredTitle: String?
    let subtitle: String?
    let overview: String?
    let synopsis: String?
    let imageUrl: String?
    let posterUrl: String?
    let posterOrientation: PosterOrientation?
    let posterAspectRatio: Double?
    let posterWidth: Int?
    let posterHeight: Int?
    let posterAccentColor: String?
    let logoUrl: String?
    let logoWidth: Int?
    let logoHeight: Int?
    let logoAspectRatio: Double?
    let releaseDate: String?
    let defaultSource: String?
    let userState: UserMediaState?
    let backdropUrl: String?
    let details: [String: JSONValue]?
    let music: MusicDetail?
    let related: [String: JSONValue]?
    let externalLinks: [String: String]
    let providers: JSONValue?
    let community: CommunityStats?
    let externalRatings: [ExternalRating]?
    let externalRatingsPreparation: MediaExternalRatingsPreparation?
    let reviews: [MediaReview]?
    let cast: [CreditPerson]?
    let characters: [CreditPerson]?
    let crew: [CreditPerson]?
    let relatedSections: [RelatedMediaSection]?
    let episodes: [EpisodeSummary]?
    let seasons: [SeasonSummary]?
    let completion: CompletionProgress?
    let customPosterUrl: String?
    let customBackdropUrl: String?
    let customLogoUrl: String?

    var id: String { ref.id }

    var displayPosterURL: String? {
        customPosterUrl ?? posterUrl ?? imageUrl
    }

    var displayBackdropURL: String? {
        customBackdropUrl ?? backdropUrl
    }

    var displayLogoURL: String? {
        customLogoUrl ?? logoUrl
    }

    var displayTitle: String {
        if let preferredTitle, !preferredTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return preferredTitle
        }
        return ref.displayTitle(title)
    }

    var episodeStillURL: String? {
        guard ref.isEpisode else { return nil }
        return displayBackdropURL ?? imageUrl ?? posterUrl
    }

    enum CodingKeys: String, CodingKey {
        case ref
        case title
        case preferredTitle = "displayTitle"
        case subtitle
        case overview
        case synopsis
        case imageUrl
        case posterUrl
        case posterOrientation
        case posterAspectRatio
        case posterWidth
        case posterHeight
        case posterAccentColor
        case logoUrl
        case logoWidth
        case logoHeight
        case logoAspectRatio
        case releaseDate
        case defaultSource
        case userState
        case backdropUrl
        case details
        case music
        case related
        case externalLinks
        case providers
        case community
        case externalRatings
        case externalRatingsPreparation
        case reviews
        case cast
        case characters
        case crew
        case relatedSections
        case episodes
        case seasons
        case completion
        case customPosterUrl
        case customBackdropUrl
        case customLogoUrl
    }

    init(
        ref: MediaRef,
        title: String,
        preferredTitle: String? = nil,
        subtitle: String? = nil,
        overview: String? = nil,
        synopsis: String? = nil,
        imageUrl: String? = nil,
        posterUrl: String? = nil,
        posterOrientation: PosterOrientation? = nil,
        posterAspectRatio: Double? = nil,
        posterWidth: Int? = nil,
        posterHeight: Int? = nil,
        posterAccentColor: String? = nil,
        logoUrl: String? = nil,
        logoWidth: Int? = nil,
        logoHeight: Int? = nil,
        logoAspectRatio: Double? = nil,
        releaseDate: String? = nil,
        defaultSource: String? = nil,
        userState: UserMediaState? = nil,
        backdropUrl: String? = nil,
        details: [String: JSONValue]? = nil,
        music: MusicDetail? = nil,
        related: [String: JSONValue]? = nil,
        externalLinks: [String: String] = [:],
        providers: JSONValue? = nil,
        community: CommunityStats? = nil,
        externalRatings: [ExternalRating]? = nil,
        externalRatingsPreparation: MediaExternalRatingsPreparation? = nil,
        reviews: [MediaReview]? = nil,
        cast: [CreditPerson]? = nil,
        characters: [CreditPerson]? = nil,
        crew: [CreditPerson]? = nil,
        relatedSections: [RelatedMediaSection]? = nil,
        episodes: [EpisodeSummary]? = nil,
        seasons: [SeasonSummary]? = nil,
        completion: CompletionProgress? = nil,
        customPosterUrl: String? = nil,
        customBackdropUrl: String? = nil,
        customLogoUrl: String? = nil
    ) {
        self.ref = ref
        self.title = title
        self.preferredTitle = preferredTitle
        self.subtitle = subtitle
        self.overview = overview
        self.synopsis = synopsis
        self.imageUrl = imageUrl
        self.posterUrl = posterUrl ?? imageUrl
        self.posterOrientation = posterOrientation
        self.posterAspectRatio = posterAspectRatio
        self.posterWidth = posterWidth
        self.posterHeight = posterHeight
        self.posterAccentColor = posterAccentColor
        self.logoUrl = logoUrl
        self.logoWidth = logoWidth
        self.logoHeight = logoHeight
        self.logoAspectRatio = logoAspectRatio
        self.releaseDate = releaseDate
        self.defaultSource = defaultSource
        self.userState = userState
        self.backdropUrl = backdropUrl
        self.details = details
        self.music = music
        self.related = related
        self.externalLinks = externalLinks
        self.providers = providers
        self.community = community
        self.externalRatings = externalRatings
        self.externalRatingsPreparation = externalRatingsPreparation
        self.reviews = reviews
        self.cast = cast
        self.characters = characters
        self.crew = crew
        self.relatedSections = relatedSections
        self.episodes = episodes
        self.seasons = seasons
        self.completion = completion
        self.customPosterUrl = customPosterUrl
        self.customBackdropUrl = customBackdropUrl
        self.customLogoUrl = customLogoUrl
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        self.init(
            ref: try container.decode(MediaRef.self, forKey: .ref),
            title: try container.decode(String.self, forKey: .title),
            preferredTitle: try container.decodeIfPresent(String.self, forKey: .preferredTitle),
            subtitle: try container.decodeIfPresent(String.self, forKey: .subtitle),
            overview: try container.decodeIfPresent(String.self, forKey: .overview),
            synopsis: try container.decodeIfPresent(String.self, forKey: .synopsis),
            imageUrl: imageUrl,
            posterUrl: try container.decodeIfPresent(String.self, forKey: .posterUrl) ?? imageUrl,
            posterOrientation: try container.decodeIfPresent(PosterOrientation.self, forKey: .posterOrientation),
            posterAspectRatio: try container.decodeIfPresent(Double.self, forKey: .posterAspectRatio),
            posterWidth: try container.decodeIfPresent(Int.self, forKey: .posterWidth),
            posterHeight: try container.decodeIfPresent(Int.self, forKey: .posterHeight),
            posterAccentColor: try container.decodeIfPresent(String.self, forKey: .posterAccentColor),
            logoUrl: try container.decodeIfPresent(String.self, forKey: .logoUrl),
            logoWidth: try container.decodeIfPresent(Int.self, forKey: .logoWidth),
            logoHeight: try container.decodeIfPresent(Int.self, forKey: .logoHeight),
            logoAspectRatio: try container.decodeIfPresent(Double.self, forKey: .logoAspectRatio),
            releaseDate: try container.decodeIfPresent(String.self, forKey: .releaseDate),
            defaultSource: try container.decodeIfPresent(String.self, forKey: .defaultSource),
            userState: try container.decodeIfPresent(UserMediaState.self, forKey: .userState),
            backdropUrl: try container.decodeIfPresent(String.self, forKey: .backdropUrl),
            details: try container.decodeIfPresent([String: JSONValue].self, forKey: .details),
            music: try container.decodeIfPresent(MusicDetail.self, forKey: .music),
            related: try container.decodeIfPresent([String: JSONValue].self, forKey: .related),
            externalLinks: try container.decodeIfPresent([String: String].self, forKey: .externalLinks) ?? [:],
            providers: try container.decodeIfPresent(JSONValue.self, forKey: .providers),
            community: try container.decodeIfPresent(CommunityStats.self, forKey: .community),
            externalRatings: try container.decodeIfPresent([ExternalRating].self, forKey: .externalRatings),
            externalRatingsPreparation: try container.decodeIfPresent(
                MediaExternalRatingsPreparation.self,
                forKey: .externalRatingsPreparation
            ),
            reviews: try container.decodeIfPresent([MediaReview].self, forKey: .reviews),
            cast: try container.decodeIfPresent([CreditPerson].self, forKey: .cast),
            characters: try container.decodeIfPresent([CreditPerson].self, forKey: .characters),
            crew: try container.decodeIfPresent([CreditPerson].self, forKey: .crew),
            relatedSections: try container.decodeIfPresent([RelatedMediaSection].self, forKey: .relatedSections),
            episodes: try container.decodeIfPresent([EpisodeSummary].self, forKey: .episodes),
            seasons: try container.decodeIfPresent([SeasonSummary].self, forKey: .seasons),
            completion: try container.decodeIfPresent(CompletionProgress.self, forKey: .completion),
            customPosterUrl: try container.decodeIfPresent(String.self, forKey: .customPosterUrl),
            customBackdropUrl: try container.decodeIfPresent(String.self, forKey: .customBackdropUrl),
            customLogoUrl: try container.decodeIfPresent(String.self, forKey: .customLogoUrl)
        )
    }

    func replacingPoster(with response: PosterSaveResponse) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: response.customPosterUrl ?? response.posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: response.posterAccentColor ?? posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState,
            backdropUrl: backdropUrl,
            details: details,
            music: music,
            related: related,
            externalLinks: externalLinks,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            externalRatingsPreparation: externalRatingsPreparation,
            reviews: reviews,
            cast: cast,
            characters: characters,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            completion: completion,
            customPosterUrl: response.customPosterUrl ?? response.posterUrl,
            customBackdropUrl: customBackdropUrl,
            customLogoUrl: customLogoUrl
        )
    }

    func replacingBackdrop(with response: BackdropSaveResponse) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState,
            backdropUrl: backdropUrl,
            details: details,
            music: music,
            related: related,
            externalLinks: externalLinks,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            externalRatingsPreparation: externalRatingsPreparation,
            reviews: reviews,
            cast: cast,
            characters: characters,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            completion: completion,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: response.customBackdropUrl ?? response.backdropUrl,
            customLogoUrl: customLogoUrl
        )
    }

    func replacingLogo(with response: LogoSaveResponse) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: response.logoUrl,
            logoWidth: response.logoWidth,
            logoHeight: response.logoHeight,
            logoAspectRatio: response.logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState,
            backdropUrl: backdropUrl,
            details: details,
            music: music,
            related: related,
            externalLinks: externalLinks,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            externalRatingsPreparation: externalRatingsPreparation,
            reviews: reviews,
            cast: cast,
            characters: characters,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            completion: completion,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: customBackdropUrl,
            customLogoUrl: response.customLogoUrl
        )
    }

    func replacingHasLiked(_ liked: Bool) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: (userState ?? UserMediaState(isTracked: false)).replacingHasLiked(liked),
            backdropUrl: backdropUrl,
            details: details,
            music: music,
            related: related,
            externalLinks: externalLinks,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            externalRatingsPreparation: externalRatingsPreparation,
            reviews: reviews,
            cast: cast,
            characters: characters,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            completion: completion,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: customBackdropUrl,
            customLogoUrl: customLogoUrl
        )
    }

    func replacingIsTracked(_ isTracked: Bool) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: (userState ?? UserMediaState(isTracked: false)).replacingIsTracked(isTracked),
            backdropUrl: backdropUrl,
            details: details,
            music: music,
            related: related,
            externalLinks: externalLinks,
            providers: providers,
            community: community,
            externalRatings: externalRatings,
            externalRatingsPreparation: externalRatingsPreparation,
            reviews: reviews,
            cast: cast,
            characters: characters,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            completion: completion,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: customBackdropUrl,
            customLogoUrl: customLogoUrl
        )
    }

    func replacingExternalRatings(with response: MediaExternalRatingsResponse) -> MediaDetail {
        MediaDetail(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview,
            synopsis: synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            logoUrl: logoUrl,
            logoWidth: logoWidth,
            logoHeight: logoHeight,
            logoAspectRatio: logoAspectRatio,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState,
            backdropUrl: backdropUrl,
            details: details,
            music: music,
            related: related,
            externalLinks: externalLinks,
            providers: providers,
            community: community,
            externalRatings: response.externalRatings + (externalRatings ?? []).filter { existing in
                !response.externalRatings.contains { incoming in
                    incoming.source.caseInsensitiveCompare(existing.source) == .orderedSame
                }
            },
            externalRatingsPreparation: response.externalRatingsPreparation,
            reviews: reviews,
            cast: cast,
            characters: characters,
            crew: crew,
            relatedSections: relatedSections,
            episodes: episodes,
            seasons: seasons,
            completion: completion,
            customPosterUrl: customPosterUrl,
            customBackdropUrl: customBackdropUrl,
            customLogoUrl: customLogoUrl
        )
    }

    var displaySynopsis: String? {
        let placeholder = "No synopsis available."
        let candidates = [
            overview,
            synopsis,
            details?["synopsis"]?.stringValue,
            details?["overview"]?.stringValue,
            details?["description"]?.stringValue,
        ]
        for candidate in candidates {
            guard let text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !text.isEmpty,
                  text != placeholder else { continue }
            return text
        }
        return nil
    }
}

enum PosterOrientation: String, Codable, Hashable {
    case portrait
    case landscape
    case square
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let value = try container.decode(String.self)
        self = PosterOrientation(rawValue: value) ?? .unknown
    }
}

struct PosterOptionsResponse: Codable, Equatable {
    let posters: [PosterOption]
}

struct BackdropOptionsResponse: Codable, Equatable {
    let backdrops: [PosterOption]
}

struct PosterOption: Codable, Identifiable, Equatable {
    let url: String
    let thumbnailUrl: String?
    let width: Int
    let height: Int
    let aspectRatio: Double?
    let voteAverage: Double
    let voteCount: Int
    let language: String?
    var providerName: String? = nil
    var providerUrl: String? = nil
    let isOriginal: Bool
    let isSelected: Bool

    var id: String { url }
}

struct PosterSaveRequest: Codable, Equatable {
    let posterUrl: String
    let seasonNumber: Int?
}

struct PosterSaveResponse: Codable, Equatable {
    let posterUrl: String
    let customPosterUrl: String?
    let posterAccentColor: String?
}

struct BackdropSaveRequest: Codable, Equatable {
    let backdropUrl: String
    let seasonNumber: Int?
    let episodeNumber: Int?
}

struct BackdropSaveResponse: Codable, Equatable {
    let backdropUrl: String
    let customBackdropUrl: String?
}

struct LogoOptionsResponse: Codable, Equatable {
    let logos: [LogoOption]
}

struct LogoOption: Codable, Identifiable, Equatable {
    let url: String
    let thumbnailUrl: String?
    let width: Int
    let height: Int
    let aspectRatio: Double?
    let voteAverage: Double
    let voteCount: Int
    let language: String?
    let style: String?
    let isOriginal: Bool
    let isSelected: Bool

    var id: String { url }
}

struct LogoSaveRequest: Codable, Equatable {
    let logoUrl: String
}

struct LogoSaveResponse: Codable, Equatable {
    let logoUrl: String
    let customLogoUrl: String?
    let logoWidth: Int?
    let logoHeight: Int?
    let logoAspectRatio: Double?
}

struct HallOfFameItemWriteRequest: Codable, Equatable {
    let ref: MediaRef
}

struct HallOfFameItemsResponse: Codable, Equatable {
    let items: [String: MediaSummary?]
}

struct UserMediaState: Codable, Hashable {
    let isTracked: Bool
    let trackingId: Int?
    let status: String?
    let rating: String?
    let progress: ProgressState?
    let diaryEntryId: Int?
    let diaryCount: Int?
    let diaryRating: String?
    let diaryConsumedAt: String?
    let inLists: [Int]
    let hasLiked: Bool
    let directConsumption: Bool?
    let ratingSourceDiaryEntryId: Int?
    let likeSourceDiaryEntryId: Int?
    let likeIsIndependent: Bool?
    let book: BookTrackingState?
    let game: GameTrackingState?

    enum CodingKeys: String, CodingKey {
        case isTracked
        case trackingId
        case status
        case rating
        case progress
        case diaryEntryId
        case diaryCount
        case diaryRating
        case diaryConsumedAt
        case inLists
        case hasLiked
        case directConsumption
        case ratingSourceDiaryEntryId
        case likeSourceDiaryEntryId
        case likeIsIndependent
        case book
        case game
    }

    init(
        isTracked: Bool,
        trackingId: Int? = nil,
        status: String? = nil,
        rating: String? = nil,
        progress: ProgressState? = nil,
        diaryEntryId: Int? = nil,
        diaryCount: Int? = nil,
        diaryRating: String? = nil,
        diaryConsumedAt: String? = nil,
        inLists: [Int] = [],
        hasLiked: Bool = false,
        directConsumption: Bool? = nil,
        ratingSourceDiaryEntryId: Int? = nil,
        likeSourceDiaryEntryId: Int? = nil,
        likeIsIndependent: Bool? = nil,
        book: BookTrackingState? = nil,
        game: GameTrackingState? = nil
    ) {
        self.isTracked = isTracked
        self.trackingId = trackingId
        self.status = status
        self.rating = rating
        self.progress = progress
        self.diaryEntryId = diaryEntryId
        self.diaryCount = diaryCount
        self.diaryRating = diaryRating
        self.diaryConsumedAt = diaryConsumedAt
        self.inLists = inLists
        self.hasLiked = hasLiked
        self.directConsumption = directConsumption
        self.ratingSourceDiaryEntryId = ratingSourceDiaryEntryId
        self.likeSourceDiaryEntryId = likeSourceDiaryEntryId
        self.likeIsIndependent = likeIsIndependent
        self.book = book
        self.game = game
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        isTracked = try container.decode(Bool.self, forKey: .isTracked)
        trackingId = try container.decodeIfPresent(Int.self, forKey: .trackingId)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        rating = try container.decodeIfPresent(String.self, forKey: .rating)
        progress = try container.decodeIfPresent(ProgressState.self, forKey: .progress)
        diaryEntryId = try container.decodeIfPresent(Int.self, forKey: .diaryEntryId)
        diaryCount = try container.decodeIfPresent(Int.self, forKey: .diaryCount)
        diaryRating = try container.decodeIfPresent(String.self, forKey: .diaryRating)
        diaryConsumedAt = try container.decodeIfPresent(String.self, forKey: .diaryConsumedAt)
        inLists = try container.decodeIfPresent([Int].self, forKey: .inLists) ?? []
        hasLiked = try container.decodeIfPresent(Bool.self, forKey: .hasLiked) ?? false
        directConsumption = try container.decodeIfPresent(Bool.self, forKey: .directConsumption)
        ratingSourceDiaryEntryId = try container.decodeIfPresent(Int.self, forKey: .ratingSourceDiaryEntryId)
        likeSourceDiaryEntryId = try container.decodeIfPresent(Int.self, forKey: .likeSourceDiaryEntryId)
        likeIsIndependent = try container.decodeIfPresent(Bool.self, forKey: .likeIsIndependent)
        book = try container.decodeIfPresent(BookTrackingState.self, forKey: .book)
        game = try container.decodeIfPresent(GameTrackingState.self, forKey: .game)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(isTracked, forKey: .isTracked)
        try container.encodeIfPresent(trackingId, forKey: .trackingId)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(rating, forKey: .rating)
        try container.encodeIfPresent(progress, forKey: .progress)
        try container.encodeIfPresent(diaryEntryId, forKey: .diaryEntryId)
        try container.encodeIfPresent(diaryCount, forKey: .diaryCount)
        try container.encodeIfPresent(diaryRating, forKey: .diaryRating)
        try container.encodeIfPresent(diaryConsumedAt, forKey: .diaryConsumedAt)
        try container.encode(inLists, forKey: .inLists)
        try container.encode(hasLiked, forKey: .hasLiked)
        try container.encodeIfPresent(directConsumption, forKey: .directConsumption)
        try container.encodeIfPresent(ratingSourceDiaryEntryId, forKey: .ratingSourceDiaryEntryId)
        try container.encodeIfPresent(likeSourceDiaryEntryId, forKey: .likeSourceDiaryEntryId)
        try container.encodeIfPresent(likeIsIndependent, forKey: .likeIsIndependent)
        try container.encodeIfPresent(book, forKey: .book)
        try container.encodeIfPresent(game, forKey: .game)
    }

    func replacingHasLiked(_ liked: Bool) -> UserMediaState {
        UserMediaState(
            isTracked: isTracked,
            trackingId: trackingId,
            status: status,
            rating: rating,
            progress: progress,
            diaryEntryId: diaryEntryId,
            diaryCount: diaryCount,
            diaryRating: diaryRating,
            diaryConsumedAt: diaryConsumedAt,
            inLists: inLists,
            hasLiked: liked,
            directConsumption: directConsumption,
            ratingSourceDiaryEntryId: ratingSourceDiaryEntryId,
            likeSourceDiaryEntryId: likeSourceDiaryEntryId,
            likeIsIndependent: likeIsIndependent,
            book: book,
            game: game
        )
    }

    func replacingIsTracked(_ isTracked: Bool) -> UserMediaState {
        UserMediaState(
            isTracked: isTracked,
            trackingId: trackingId,
            status: status,
            rating: rating,
            progress: progress,
            diaryEntryId: diaryEntryId,
            diaryCount: diaryCount,
            diaryRating: diaryRating,
            diaryConsumedAt: diaryConsumedAt,
            inLists: inLists,
            hasLiked: hasLiked,
            directConsumption: directConsumption,
            ratingSourceDiaryEntryId: ratingSourceDiaryEntryId,
            likeSourceDiaryEntryId: likeSourceDiaryEntryId,
            likeIsIndependent: likeIsIndependent,
            book: book,
            game: game
        )
    }
}

struct MediaLikeResponse: Codable, Equatable {
    let liked: Bool
    let media: MediaSummary?
}

struct CommunityStats: Codable {
    let averageRating: String?
    let ratingCount: Int
    let diaryCount: Int
    let reviewCount: Int
    let likedCount: Int
    let ratingDistribution: [RatingDistributionBucket]

    enum CodingKeys: String, CodingKey {
        case averageRating
        case ratingCount
        case diaryCount
        case reviewCount
        case likedCount
        case ratingDistribution
    }

    init(
        averageRating: String?,
        ratingCount: Int,
        diaryCount: Int,
        reviewCount: Int,
        likedCount: Int,
        ratingDistribution: [RatingDistributionBucket] = []
    ) {
        self.averageRating = averageRating
        self.ratingCount = ratingCount
        self.diaryCount = diaryCount
        self.reviewCount = reviewCount
        self.likedCount = likedCount
        self.ratingDistribution = ratingDistribution
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        averageRating = try container.decodeIfPresent(String.self, forKey: .averageRating)
        ratingCount = try container.decode(Int.self, forKey: .ratingCount)
        diaryCount = try container.decode(Int.self, forKey: .diaryCount)
        reviewCount = try container.decode(Int.self, forKey: .reviewCount)
        likedCount = try container.decode(Int.self, forKey: .likedCount)
        ratingDistribution = try container.decodeIfPresent([RatingDistributionBucket].self, forKey: .ratingDistribution) ?? []
    }
}

struct RatingDistributionBucket: Codable, Hashable {
    let rating: String
    let count: Int
}

struct ExternalRating: Codable, Identifiable, Hashable {
    let source: String
    let value: String
    let voteCount: Int?
    let maxValue: String?
    let url: String?

    var id: String { source }

    var destinationURL: URL? {
        guard
            let url,
            let destination = URL(string: url),
            let scheme = destination.scheme?.lowercased(),
            ["http", "https"].contains(scheme),
            destination.host != nil
        else {
            return nil
        }
        return destination
    }
}

enum MediaExternalRatingsPreparationState: String, Codable, Hashable {
    case ready
    case pending
    case degraded
}

struct MediaExternalRatingsPreparation: Codable, Hashable {
    let state: MediaExternalRatingsPreparationState
    let retryAfterSeconds: Int

    static let ready = MediaExternalRatingsPreparation(
        state: .ready,
        retryAfterSeconds: 2
    )
}

struct MediaExternalRatingsResponse: Codable, Hashable {
    let externalRatings: [ExternalRating]
    let externalRatingsPreparation: MediaExternalRatingsPreparation
}

struct MediaReview: Codable, Identifiable, Hashable {
    let id: Int
    let user: UserSummary
    let rating: String?
    let reviewTitle: String?
    let review: String
    let containsSpoilers: Bool
    var likeCount: Int
    var viewerHasLiked: Bool
    let consumedAt: String?
    let createdAt: String?
}

struct AniListReviewPage: Codable, Equatable {
    let currentPage: Int
    let nextPage: Int?
    let results: [AniListReview]
}

struct AniListReview: Codable, Identifiable, Hashable {
    let id: String
    let user: AniListReviewUser
    let score: Int?
    let summary: String?
    let body: String
    let communityRating: Int?
    let communityRatingCount: Int?
    let url: String?
    let createdAt: String?
}

struct AniListReviewUser: Codable, Hashable {
    let id: String
    let name: String
    let avatarUrl: String?
    let profileUrl: String?
}

struct LikeState: Codable, Equatable {
    let liked: Bool
    let likeCount: Int
}

struct CreditPerson: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let personSource: String?
    let role: String?
    let character: String?
    let imageUrl: String?

    init(
        id: String,
        name: String,
        personSource: String? = nil,
        role: String?,
        character: String?,
        imageUrl: String?
    ) {
        self.id = id
        self.name = name
        self.personSource = personSource
        self.role = role
        self.character = character
        self.imageUrl = imageUrl
    }

    var personRef: PersonRef? {
        guard let personSource, !personSource.isEmpty, !id.isEmpty else { return nil }
        return PersonRef(source: personSource, id: id)
    }
}

struct RelatedMediaSection: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let items: [MediaSummary]
    let completion: CompletionProgress?

    init(
        id: String,
        title: String,
        items: [MediaSummary],
        completion: CompletionProgress? = nil
    ) {
        self.id = id
        self.title = title
        self.items = items
        self.completion = completion
    }
}

struct EpisodeSummary: Codable, Identifiable, Hashable {
    let episodeNumber: Int
    let title: String
    let overview: String?
    let airDate: String?
    let runtime: String?
    let imageUrl: String?
    let rating: String?

    var id: Int { episodeNumber }
}

struct SeasonSummary: Codable, Identifiable, Hashable {
    let seasonNumber: Int
    let title: String
    let episodeCount: Int?
    let imageUrl: String?
    let releaseDate: String?
    let completion: CompletionProgress?

    var id: Int { seasonNumber }
}

enum JSONValue: Codable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value.")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value):
            try container.encode(value)
        case let .number(value):
            try container.encode(value)
        case let .bool(value):
            try container.encode(value)
        case let .object(value):
            try container.encode(value)
        case let .array(value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}

extension JSONValue {
    var stringValue: String? {
        if case let .string(value) = self {
            return value
        }
        return nil
    }
}
