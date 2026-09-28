import Foundation

protocol AuthRepository {
    var hasStoredTokens: Bool { get }
    func login(usernameOrEmail: String, password: String) async throws -> AuthUser
    func register(username: String, email: String, password: String) async throws -> AuthUser
    func refresh() async throws
    func logout() async
}

protocol MediaRepository {
    func meta() async throws -> MetaResponse
    func search(query: String, mediaType: String) async throws -> [MediaSummary]
    func searchAll(query: String) async throws -> MediaSearchResponse
    func discover(_ request: MediaDiscoverRequest) async throws -> PagedResponse<MediaSummary>
    func detail(ref: MediaRef) async throws -> MediaDetail
    func enrichedMusicDetail(ref: MediaRef) async throws -> MediaDetail
    func series(ref: SeriesRef) async throws -> SeriesDetail
    func externalRatings(ref: MediaRef) async throws -> MediaExternalRatingsResponse
    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse
    func reviews(ref: MediaRef) async throws -> [MediaReview]
    func reviewPage(ref: MediaRef, page: String?) async throws -> PagedResponse<MediaReview>
    func anilistReviews(ref: MediaRef, page: Int) async throws -> AniListReviewPage
    func posters(ref: MediaRef) async throws -> [PosterOption]
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse
    func backdrops(ref: MediaRef) async throws -> [PosterOption]
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse
    func logos(ref: MediaRef) async throws -> [LogoOption]
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse
}

protocol MusicRepository {
    func recordingDetail(album: MediaRef, recordingMbid: String) async throws -> MusicRecordingDetail
}

protocol PeopleRepository {
    func search(query: String) async throws -> PersonSearchResponse
    func detail(ref: PersonRef) async throws -> PersonDetail
    func detail(ref: PersonRef, filter: MediaFilterState) async throws -> PersonDetail
    func detail(
        ref: PersonRef,
        filter: MediaFilterState,
        creditsPage: Int?
    ) async throws -> PersonDetail
}

protocol CompanyRepository {
    func detail(ref: CompanyRef) async throws -> CompanyDetail
    func filterOptions(ref: CompanyRef) async throws -> MediaFilterOptionsResponse
    func catalog(
        ref: CompanyRef,
        role: CompanyCatalogRole,
        page: String?,
        filter: MediaFilterState
    ) async throws -> CompanyCatalogPage
}

extension CompanyRepository {
    func catalog(ref: CompanyRef, role: CompanyCatalogRole, page: String?) async throws -> CompanyCatalogPage {
        try await catalog(ref: ref, role: role, page: page, filter: MediaFilterState())
    }
}

extension PeopleRepository {
    func detail(ref: PersonRef, filter: MediaFilterState) async throws -> PersonDetail {
        try await detail(ref: ref)
    }

    func detail(
        ref: PersonRef,
        filter: MediaFilterState,
        creditsPage: Int?
    ) async throws -> PersonDetail {
        try await detail(ref: ref, filter: filter)
    }
}

extension MediaRepository {
    func searchAll(query: String) async throws -> MediaSearchResponse {
        MediaSearchResponse(results: try await search(query: query, mediaType: APIConstants.allMedia))
    }

    func discover(_ request: MediaDiscoverRequest) async throws -> PagedResponse<MediaSummary> {
        fatalError("Not implemented")
    }

    func enrichedMusicDetail(ref: MediaRef) async throws -> MediaDetail {
        try await detail(ref: ref)
    }

    func externalRatings(ref: MediaRef) async throws -> MediaExternalRatingsResponse {
        let detail = try await detail(ref: ref)
        return MediaExternalRatingsResponse(
            externalRatings: detail.externalRatings ?? [],
            externalRatingsPreparation: detail.externalRatingsPreparation ?? .ready
        )
    }

    func series(ref _: SeriesRef) async throws -> SeriesDetail {
        fatalError("Not implemented")
    }

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        fatalError("Not implemented")
    }

    func reviewPage(ref: MediaRef, page _: String?) async throws -> PagedResponse<MediaReview> {
        let reviews = try await reviews(ref: ref)
        return PagedResponse(count: reviews.count, next: nil, previous: nil, results: reviews)
    }

    func anilistReviews(ref _: MediaRef, page: Int) async throws -> AniListReviewPage {
        AniListReviewPage(currentPage: page, nextPage: nil, results: [])
    }
}

protocol TrackingRepository {
    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<LibraryItem>
    func list(mediaType: String, page: String?, filter: MediaFilterState) async throws -> PagedResponse<LibraryItem>
    func detail(ref: MediaRef) async throws -> TrackingState
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState
    func delete(ref: MediaRef) async throws
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState
    func watchEpisode(
        source: String,
        mediaId: String,
        seasonNumber: Int,
        episodeNumber: Int,
        watchedAt: Date?
    ) async throws -> TrackingState
    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState
    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState
    func performBookAction(source: String, mediaId: String, action: String, request: BookActionRequest) async throws -> TrackingState
    func undoBookRead(source: String, mediaId: String) async throws
    func updateBookJourney(source: String, mediaId: String, journeyId: Int, request: BookJourneyWriteRequest) async throws -> TrackingState
    func deleteBookJourney(source: String, mediaId: String, journeyId: Int) async throws -> TrackingState
    func completeBook(source: String, mediaId: String, request: BookCompletionWriteRequest) async throws -> BookCompletionResponse
    func performGameAction(ref: MediaRef, action: String, request: BookActionRequest) async throws
    func updateGamePlaythrough(ref: MediaRef, playthroughId: Int, request: GamePlaythroughWriteRequest) async throws -> TrackingState
    func deleteGamePlaythrough(ref: MediaRef, playthroughId: Int) async throws
    func completeGame(ref: MediaRef, request: GameCompletionWriteRequest) async throws -> BookCompletionResponse
}

extension TrackingRepository {
    func performGameAction(ref: MediaRef, action: String, request: BookActionRequest) async throws { fatalError("Not implemented") }
    func updateGamePlaythrough(ref: MediaRef, playthroughId: Int, request: GamePlaythroughWriteRequest) async throws -> TrackingState { fatalError("Not implemented") }
    func deleteGamePlaythrough(ref: MediaRef, playthroughId: Int) async throws { fatalError("Not implemented") }
    func completeGame(ref: MediaRef, request: GameCompletionWriteRequest) async throws -> BookCompletionResponse { fatalError("Not implemented") }
    func delete(ref _: MediaRef) async throws {
        fatalError("Not implemented")
    }

    func list(mediaType: String, page: String?, filter: MediaFilterState) async throws -> PagedResponse<LibraryItem> {
        try await list(
            mediaType: mediaType,
            page: page,
            status: filter.status,
            query: filter.q.isEmpty ? nil : filter.q
        )
    }

    func list(mediaType: String, page: String?, status: String?) async throws -> PagedResponse<LibraryItem> {
        try await list(mediaType: mediaType, page: page, status: status, query: nil)
    }

    func list(mediaType: String, page: String?) async throws -> PagedResponse<LibraryItem> {
        try await list(mediaType: mediaType, page: page, status: nil, query: nil)
    }

    func watchEpisode(
        source _: String,
        mediaId _: String,
        seasonNumber _: Int,
        episodeNumber _: Int,
        watchedAt _: Date?
    ) async throws -> TrackingState {
        fatalError("Not implemented")
    }

    func performBookAction(source _: String, mediaId _: String, action _: String, request _: BookActionRequest) async throws -> TrackingState {
        fatalError("Not implemented")
    }

    func undoBookRead(source _: String, mediaId _: String) async throws {
        fatalError("Not implemented")
    }

    func updateBookJourney(source _: String, mediaId _: String, journeyId _: Int, request _: BookJourneyWriteRequest) async throws -> TrackingState {
        fatalError("Not implemented")
    }

    func deleteBookJourney(source _: String, mediaId _: String, journeyId _: Int) async throws -> TrackingState {
        fatalError("Not implemented")
    }

    func completeBook(source _: String, mediaId _: String, request _: BookCompletionWriteRequest) async throws -> BookCompletionResponse {
        fatalError("Not implemented")
    }
}

protocol DiaryRepository {
    func list(filter: MediaFilterState) async throws -> [DiaryEntry]
    func list(filter: DiaryFilter) async throws -> [DiaryEntry]
    func list(tag: String?) async throws -> [DiaryEntry]
    func page(filter: MediaFilterState, page: String?) async throws -> PagedResponse<DiaryEntry>
    func recent(limit: Int) async throws -> [DiaryEntry]
    func detail(id: Int) async throws -> DiaryEntry
    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry
    func update(id: Int, request: DiaryEntryUpdateRequest) async throws -> DiaryEntry
    func delete(id: Int) async throws
    func setLike(entryId: Int, liked: Bool) async throws -> LikeState
    func tags(query: String, mine: Bool) async throws -> [DiaryTagSuggestion]
    func tags(query: String) async throws -> [DiaryTagSuggestion]
    func allTags(mine: Bool) async throws -> [DiaryTagSuggestion]
}

protocol ActivityRepository {
    func userActivity(username: String, limit: Int) async throws -> [ActivityItem]
    func userActivityPage(username: String, pageSize: Int, cursorLink: String?) async throws -> ActivityCursorResponse
}

extension ActivityRepository {
    func userActivityPage(username: String, pageSize: Int, cursorLink _: String?) async throws -> ActivityCursorResponse {
        ActivityCursorResponse(
            nextCursor: nil,
            previousCursor: nil,
            results: try await userActivity(username: username, limit: pageSize)
        )
    }
}

extension DiaryRepository {
    func list(filter: MediaFilterState) async throws -> [DiaryEntry] {
        var page: String?
        var entries: [DiaryEntry] = []

        repeat {
            let response = try await self.page(filter: filter, page: page)
            entries += response.results
            page = APIPageCursor.nextPage(from: response.next)
        } while page != nil

        return entries
    }

    func page(filter: MediaFilterState, page: String?) async throws -> PagedResponse<DiaryEntry> {
        let results = try await list(filter: DiaryFilter(
            tag: filter.tag,
            itemId: filter.itemId,
            hasReview: filter.hasReview,
            liked: filter.liked
        ))
        return PagedResponse(count: results.count, next: nil, previous: nil, results: results)
    }

    func list(filter: DiaryFilter) async throws -> [DiaryEntry] {
        try await list(tag: filter.tag)
    }

    func list() async throws -> [DiaryEntry] {
        try await list(tag: nil)
    }

    func recent(limit: Int) async throws -> [DiaryEntry] {
        guard limit > 0 else { return [] }
        return Array(try await list().prefix(limit))
    }

    func update(id: Int, request: DiaryEntryUpdateRequest) async throws -> DiaryEntry {
        fatalError("Not implemented")
    }

    func delete(id: Int) async throws {
        fatalError("Not implemented")
    }

    func tags(query: String, mine: Bool) async throws -> [DiaryTagSuggestion] {
        try await tags(query: query)
    }

    func allTags(mine: Bool) async throws -> [DiaryTagSuggestion] {
        try await tags(query: "", mine: mine)
    }
}

struct DiaryFilter: Equatable {
    var tag: String? = nil
    var itemId: Int? = nil
    var hasReview = false
    var liked = false
}

protocol ProfileRepository {
    func me() async throws -> UserProfile
    func profile(username: String) async throws -> UserProfile
    func statsSummary(username: String?, period: StatsPeriod) async throws -> StatsSummary
    func likedMedia() async throws -> [MediaSummary]
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String?
    func deleteAvatar() async throws -> String?
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences
    func changePassword(_ request: PasswordChangeRequest) async throws
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?]
    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?]
}

extension ProfileRepository {
    func profile(username: String) async throws -> UserProfile {
        fatalError("Not implemented")
    }

    func likedMedia() async throws -> [MediaSummary] {
        fatalError("Not implemented")
    }

    func statsSummary(username: String?, period: StatsPeriod) async throws -> StatsSummary {
        fatalError("Not implemented")
    }
}

protocol ListRepository {
    func list(membershipFor ref: MediaRef?) async throws -> [CustomListSummary]
    func list() async throws -> [CustomListSummary]
    func peopleLists(membershipFor ref: PersonRef) async throws -> [CustomListSummary]
    func featured() async throws -> [CustomListSummary]
    func detail(id: Int) async throws -> CustomListDetail
    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary
    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail
    func delete(id: Int) async throws
    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary
    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary>
    func removeItem(listId: Int, itemId: Int) async throws
    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail
    func people(listId: Int, page: String?) async throws -> PagedResponse<PersonListEntry>
    func addPerson(listId: Int, ref: PersonRef) async throws -> PersonListEntry
    func removePerson(listId: Int, entryId: Int) async throws
    func reorderPeople(listId: Int, entryIds: [Int]) async throws -> CustomListDetail
}

extension ListRepository {
    func list() async throws -> [CustomListSummary] {
        try await list(membershipFor: nil)
    }

    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary> {
        let detail = try await detail(id: listId)
        return PagedResponse(count: detail.items.count, next: nil, previous: nil, results: detail.items)
    }

    func featured() async throws -> [CustomListSummary] {
        []
    }
}

protocol FilterOptionsRepository {
    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse
}

protocol ImportRepository {
    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse
    func queueStoryGraphImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse
    func queueGoodreadsImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)?
    ) async throws -> ImportQueueResponse
    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus
}

struct AppRepositories {
    let auth: AuthRepository
    let media: MediaRepository
    let music: MusicRepository
    let people: PeopleRepository
    let companies: CompanyRepository
    let tracking: TrackingRepository
    let diary: DiaryRepository
    let activity: ActivityRepository
    let profile: ProfileRepository
    let lists: ListRepository
    let filterOptions: FilterOptionsRepository
    let imports: ImportRepository

    init(
        auth: AuthRepository,
        media: MediaRepository,
        music: MusicRepository,
        people: PeopleRepository = APIPeopleRepository(client: AppEnvironment.apiClient),
        companies: CompanyRepository = APICompanyRepository(client: AppEnvironment.apiClient),
        tracking: TrackingRepository,
        diary: DiaryRepository,
        activity: ActivityRepository,
        profile: ProfileRepository,
        lists: ListRepository,
        filterOptions: FilterOptionsRepository = APIFilterOptionsRepository(client: AppEnvironment.apiClient),
        imports: ImportRepository
    ) {
        self.auth = auth
        self.media = media
        self.music = music
        self.people = people
        self.companies = companies
        self.tracking = tracking
        self.diary = diary
        self.activity = activity
        self.profile = profile
        self.lists = lists
        self.filterOptions = filterOptions
        self.imports = imports
    }

    static func current() -> AppRepositories {
        live()
    }

    static func live(client: APIClient = AppEnvironment.apiClient) -> AppRepositories {
        AppRepositories(
            auth: APIAuthRepository(service: AuthService(client: client), tokenStore: client.tokenProvider),
            media: APIMediaRepository(client: client),
            music: APIMusicRepository(client: client),
            people: APIPeopleRepository(client: client),
            companies: APICompanyRepository(client: client),
            tracking: APITrackingRepository(client: client),
            diary: APIDiaryRepository(client: client),
            activity: APIActivityRepository(client: client),
            profile: APIProfileRepository(client: client),
            lists: APIListRepository(client: client),
            filterOptions: APIFilterOptionsRepository(client: client),
            imports: APIImportRepository(client: client)
        )
    }
}

struct APIAuthRepository: AuthRepository {
    let service: AuthService
    let tokenStore: KeychainTokenStore

    var hasStoredTokens: Bool {
        tokenStore.accessToken != nil || tokenStore.refreshToken != nil
    }

    func login(usernameOrEmail: String, password: String) async throws -> AuthUser {
        try await service.login(usernameOrEmail: usernameOrEmail, password: password)
    }

    func register(username: String, email: String, password: String) async throws -> AuthUser {
        try await service.register(username: username, email: email, password: password)
    }

    func refresh() async throws {
        try await service.refresh()
    }

    func logout() async {
        await service.logout()
    }
}

struct APIMediaRepository: MediaRepository {
    let client: APIClient

    func meta() async throws -> MetaResponse {
        try await client.get("/meta/", authenticated: true)
    }

    func search(query: String, mediaType: String) async throws -> [MediaSummary] {
        let response: PagedResponse<MediaSummary> = try await client.get(
            "/media/search/",
            query: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "media_type", value: mediaType),
            ],
            authenticated: true
        )
        return response.results
    }

    func searchAll(query: String) async throws -> MediaSearchResponse {
        try await client.get(
            "/media/search/",
            query: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "scope", value: APIConstants.allMedia),
            ],
            authenticated: true,
            requestTimeout: 12
        )
    }

    func discover(_ request: MediaDiscoverRequest) async throws -> PagedResponse<MediaSummary> {
        try await client.get(
            "/media/discover/",
            query: request.queryItems,
            authenticated: true
        )
    }

    func detail(ref: MediaRef) async throws -> MediaDetail {
        var query: [URLQueryItem] = []
        if ref.mediaType != "season", let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        let path: String
        if ref.mediaType == "music" && ref.source == "musicbrainz" {
            path = "/media/musicbrainz/music/\(ref.mediaId)/basic/"
        } else if ref.mediaType == "season", let seasonNumber = ref.seasonNumber {
            path = "/media/\(ref.source)/tv/\(ref.mediaId)/seasons/\(seasonNumber)/"
        } else {
            path = "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/"
        }
        return try await client.get(
            path,
            query: query,
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func enrichedMusicDetail(ref: MediaRef) async throws -> MediaDetail {
        try await client.get(
            "/media/musicbrainz/music/\(ref.mediaId)/enrichment/",
            authenticated: client.tokenProvider.accessToken != nil,
            requestTimeout: 30
        )
    }

    func series(ref: SeriesRef) async throws -> SeriesDetail {
        try await client.get(
            "/series/\(ref.source)/\(ref.id)/",
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func externalRatings(ref: MediaRef) async throws -> MediaExternalRatingsResponse {
        var query: [URLQueryItem] = []
        if let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        return try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/external-ratings/",
            query: query,
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func setLiked(ref: MediaRef, liked: Bool) async throws -> MediaLikeResponse {
        let request = HallOfFameItemWriteRequest(ref: ref)
        if liked {
            return try await client.post("/me/liked-media/", body: request, authenticated: true)
        }
        return try await client.delete("/me/liked-media/", body: request, authenticated: true)
    }

    func reviews(ref: MediaRef) async throws -> [MediaReview] {
        try await reviewPage(ref: ref, page: nil).results
    }

    func reviewPage(ref: MediaRef, page: String?) async throws -> PagedResponse<MediaReview> {
        var query = [
            URLQueryItem(name: "sort", value: "popular"),
        ]
        if let page {
            query.append(URLQueryItem(name: "page", value: page))
        }
        if let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        do {
            let mediaType = ref.mediaType == "season" ? "tv" : ref.mediaType
            return try await client.get(
                "/media/\(ref.source)/\(mediaType)/\(ref.mediaId)/reviews/",
                query: query,
                authenticated: client.tokenProvider.accessToken != nil
            )
        } catch APIError.httpStatus(404, _), APIError.httpStatus(501, _) {
            return PagedResponse(count: 0, next: nil, previous: nil, results: [])
        }
    }

    func anilistReviews(ref: MediaRef, page: Int) async throws -> AniListReviewPage {
        try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/anilist-reviews/",
            query: [URLQueryItem(name: "page", value: String(page))],
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func posters(ref: MediaRef) async throws -> [PosterOption] {
        var query: [URLQueryItem] = []
        if ref.mediaType == "season", let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        let response: PosterOptionsResponse = try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/posters/",
            query: query,
            authenticated: true
        )
        return response.posters
    }

    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse {
        try await client.put(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/poster/",
            body: PosterSaveRequest(
                posterUrl: posterURL,
                seasonNumber: ref.mediaType == "season" ? ref.seasonNumber : nil
            ),
            authenticated: true
        )
    }

    func backdrops(ref: MediaRef) async throws -> [PosterOption] {
        var query: [URLQueryItem] = []
        if ["season", "episode"].contains(ref.mediaType), let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if ref.mediaType == "episode", let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        let response: BackdropOptionsResponse = try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/backdrops/",
            query: query,
            authenticated: true
        )
        return response.backdrops
    }

    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse {
        try await client.put(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/backdrop/",
            body: BackdropSaveRequest(
                backdropUrl: backdropURL,
                seasonNumber: ["season", "episode"].contains(ref.mediaType) ? ref.seasonNumber : nil,
                episodeNumber: ref.mediaType == "episode" ? ref.episodeNumber : nil
            ),
            authenticated: true
        )
    }

    func logos(ref: MediaRef) async throws -> [LogoOption] {
        let response: LogoOptionsResponse = try await client.get(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/logos/",
            authenticated: true
        )
        return response.logos
    }

    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse {
        try await client.put(
            "/media/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/logo/",
            body: LogoSaveRequest(logoUrl: logoURL),
            authenticated: true
        )
    }
}

struct APIMusicRepository: MusicRepository {
    let client: APIClient

    func recordingDetail(album: MediaRef, recordingMbid: String) async throws -> MusicRecordingDetail {
        try await client.get(
            "/media/\(album.source)/\(album.mediaType)/\(album.mediaId)/recordings/\(recordingMbid)/",
            authenticated: client.tokenProvider.accessToken != nil
        )
    }
}

struct APIPeopleRepository: PeopleRepository {
    let client: APIClient

    func search(query: String) async throws -> PersonSearchResponse {
        try await client.get(
            "/people/search/",
            query: [URLQueryItem(name: "q", value: query)],
            authenticated: true
        )
    }

    func detail(ref: PersonRef) async throws -> PersonDetail {
        try await detail(ref: ref, filter: MediaFilterState())
    }

    func detail(ref: PersonRef, filter: MediaFilterState) async throws -> PersonDetail {
        try await detail(ref: ref, filter: filter, creditsPage: nil)
    }

    func detail(
        ref: PersonRef,
        filter: MediaFilterState,
        creditsPage: Int?
    ) async throws -> PersonDetail {
        var query = filter.queryItems()
        if let creditsPage {
            query.append(URLQueryItem(name: "credits_page", value: String(creditsPage)))
        }
        return try await client.get(
            "/people/\(ref.source)/\(ref.id)/",
            query: query,
            authenticated: client.tokenProvider.accessToken != nil
        )
    }
}

struct APICompanyRepository: CompanyRepository {
    let client: APIClient

    func detail(ref: CompanyRef) async throws -> CompanyDetail {
        try await client.get(
            "/companies/\(ref.source)/\(ref.companyId)/",
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func filterOptions(ref: CompanyRef) async throws -> MediaFilterOptionsResponse {
        let endpoint = ref.isAnimeStudio ? "anime-options" : "game-options"
        return try await client.get(
            "/companies/\(ref.source)/\(ref.companyId)/\(endpoint)/",
            authenticated: client.tokenProvider.accessToken != nil
        )
    }

    func catalog(
        ref: CompanyRef,
        role: CompanyCatalogRole,
        page: String?,
        filter: MediaFilterState
    ) async throws -> CompanyCatalogPage {
        let isAnimeCatalog = ref.isAnimeStudio && role == .studio
        var query = isAnimeCatalog ? [] : [URLQueryItem(name: "role", value: role.rawValue)]
        query += filter.queryItems(page: page)
        return try await client.get(
            "/companies/\(ref.source)/\(ref.companyId)/\(isAnimeCatalog ? "anime" : "games")/",
            query: query,
            authenticated: client.tokenProvider.accessToken != nil
        )
    }
}

struct APITrackingRepository: TrackingRepository {
    let client: APIClient

    func list(mediaType: String, page: String?, status: String? = nil, query searchQuery: String? = nil) async throws -> PagedResponse<LibraryItem> {
        var filter = MediaFilterState(q: searchQuery ?? "")
        filter.status = status
        return try await list(mediaType: mediaType, page: page, filter: filter)
    }

    func list(mediaType: String, page: String?, filter: MediaFilterState) async throws -> PagedResponse<LibraryItem> {
        return try await client.get(
            "/tracking/",
            query: filter.queryItems(page: page, mediaType: mediaType),
            authenticated: true
        )
    }

    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState {
        try await client.patch(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
            body: request,
            authenticated: true
        )
    }

    func delete(ref: MediaRef) async throws {
        let _: EmptyResponse = try await client.delete(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
            authenticated: true
        )
    }

    func detail(ref: MediaRef) async throws -> TrackingState {
        var query: [URLQueryItem] = []
        if let seasonNumber = ref.seasonNumber {
            query.append(URLQueryItem(name: "season_number", value: String(seasonNumber)))
        }
        if let episodeNumber = ref.episodeNumber {
            query.append(URLQueryItem(name: "episode_number", value: String(episodeNumber)))
        }
        return try await client.get(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/",
            query: query,
            authenticated: true
        )
    }

    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(ref.source)/\(ref.mediaType)/\(ref.mediaId)/actions/consume/",
            body: TrackingConsumeRequest(consumedAt: consumedAt),
            authenticated: true
        )
    }

    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/tv/\(mediaId)/seasons/\(seasonNumber)/watch/",
            body: EmptyResponse(),
            authenticated: true
        )
    }

    func watchEpisode(
        source: String,
        mediaId: String,
        seasonNumber: Int,
        episodeNumber: Int,
        watchedAt: Date?
    ) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/tv/\(mediaId)/seasons/\(seasonNumber)/episodes/\(episodeNumber)/watch/",
            body: EpisodeWatchRequest(watchedAt: watchedAt),
            authenticated: true
        )
    }

    func performGameAction(ref: MediaRef, action: String, request: BookActionRequest) async throws {
        let _: EmptyResponse = try await client.post("/tracking/\(ref.source)/game/\(ref.mediaId)/actions/\(action)/", body: request, authenticated: true)
    }

    func updateGamePlaythrough(ref: MediaRef, playthroughId: Int, request: GamePlaythroughWriteRequest) async throws -> TrackingState {
        try await client.patch("/tracking/\(ref.source)/game/\(ref.mediaId)/playthroughs/\(playthroughId)/", body: request, authenticated: true)
    }

    func deleteGamePlaythrough(ref: MediaRef, playthroughId: Int) async throws {
        do {
            let _: EmptyResponse = try await client.delete("/tracking/\(ref.source)/game/\(ref.mediaId)/playthroughs/\(playthroughId)/", authenticated: true)
        } catch APIError.httpStatus(404, _) {
            // A retry after a lost response can find the playthrough already deleted.
        }
    }

    func completeGame(ref: MediaRef, request: GameCompletionWriteRequest) async throws -> BookCompletionResponse {
        try await client.post("/tracking/\(ref.source)/game/\(ref.mediaId)/complete/", body: request, authenticated: true)
    }

    func updateBookProgress(source: String, mediaId: String, progressType: String, value: Decimal, notes: String) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/book/\(mediaId)/progress/",
            body: BookProgressRequest(progressType: progressType, value: value, notes: notes),
            authenticated: true
        )
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/book/\(mediaId)/complete/",
            body: BookCompleteRequest(completedAt: completedAt),
            authenticated: true
        )
    }

    func performBookAction(source: String, mediaId: String, action: String, request: BookActionRequest) async throws -> TrackingState {
        try await client.post(
            "/tracking/\(source)/book/\(mediaId)/actions/\(action)/",
            body: request,
            authenticated: true
        )
    }

    func undoBookRead(source: String, mediaId: String) async throws {
        let _: EmptyResponse = try await client.post(
            "/tracking/\(source)/book/\(mediaId)/actions/undo_read/",
            body: BookActionRequest(),
            authenticated: true
        )
    }

    func updateBookJourney(source: String, mediaId: String, journeyId: Int, request: BookJourneyWriteRequest) async throws -> TrackingState {
        try await client.patch(
            "/tracking/\(source)/book/\(mediaId)/journeys/\(journeyId)/",
            body: request,
            authenticated: true
        )
    }

    func deleteBookJourney(source: String, mediaId: String, journeyId: Int) async throws -> TrackingState {
        try await client.delete(
            "/tracking/\(source)/book/\(mediaId)/journeys/\(journeyId)/",
            authenticated: true
        )
    }

    func completeBook(source: String, mediaId: String, request: BookCompletionWriteRequest) async throws -> BookCompletionResponse {
        try await client.post(
            "/tracking/\(source)/book/\(mediaId)/complete/",
            body: request,
            authenticated: true
        )
    }
}

struct APIDiaryRepository: DiaryRepository {
    let client: APIClient

    func list(tag: String? = nil) async throws -> [DiaryEntry] {
        try await list(filter: DiaryFilter(tag: tag))
    }

    func list(filter: DiaryFilter) async throws -> [DiaryEntry] {
        var mediaFilter = MediaFilterState()
        mediaFilter.tag = filter.tag
        mediaFilter.itemId = filter.itemId
        mediaFilter.hasReview = filter.hasReview
        mediaFilter.liked = filter.liked
        return try await list(filter: mediaFilter)
    }

    func page(filter: MediaFilterState, page: String?) async throws -> PagedResponse<DiaryEntry> {
        var query = filter.queryItems(page: page)
        query.append(URLQueryItem(name: "page_size", value: "100"))
        return try await client.get("/diary/", query: query, authenticated: true)
    }

    func recent(limit: Int) async throws -> [DiaryEntry] {
        guard limit > 0 else { return [] }
        let response: PagedResponse<DiaryEntry> = try await client.get("/diary/", authenticated: true)
        return Array(response.results.prefix(limit))
    }

    func detail(id: Int) async throws -> DiaryEntry {
        try await client.get("/diary/\(id)/", authenticated: true)
    }

    func create(_ request: DiaryEntryWriteRequest) async throws -> DiaryEntry {
        try await client.post("/diary/", body: request, authenticated: true)
    }

    func update(id: Int, request: DiaryEntryUpdateRequest) async throws -> DiaryEntry {
        try await client.patch("/diary/\(id)/", body: request, authenticated: true)
    }

    func delete(id: Int) async throws {
        do {
            let _: EmptyResponse = try await client.delete("/diary/\(id)/", authenticated: true)
        } catch APIError.httpStatus(404, _) {
            // A retry after a lost response has already achieved the requested state.
        }
    }

    func setLike(entryId: Int, liked: Bool) async throws -> LikeState {
        if liked {
            return try await client.post(
                "/diary/\(entryId)/like/",
                body: EmptyResponse(),
                authenticated: true
            )
        }
        return try await client.delete("/diary/\(entryId)/like/", authenticated: true)
    }

    func tags(query: String) async throws -> [DiaryTagSuggestion] {
        try await tags(query: query, mine: false)
    }

    func tags(query: String, mine: Bool) async throws -> [DiaryTagSuggestion] {
        var queryItems = query.isEmpty ? [] : [URLQueryItem(name: "q", value: query)]
        if mine {
            queryItems.append(URLQueryItem(name: "mine", value: "true"))
        }
        let response: DiaryTagSuggestionsResponse = try await client.get(
            "/diary/tags/",
            query: queryItems,
            authenticated: true
        )
        return response.results
    }

    func allTags(mine: Bool) async throws -> [DiaryTagSuggestion] {
        var queryItems = [URLQueryItem(name: "all", value: "true")]
        if mine {
            queryItems.append(URLQueryItem(name: "mine", value: "true"))
        }
        let response: DiaryTagSuggestionsResponse = try await client.get(
            "/diary/tags/",
            query: queryItems,
            authenticated: true
        )
        return response.results
    }
}

enum APIPageCursor {
    static func nextPage(from next: String?) -> String? {
        guard let next, let url = URL(string: next) else { return nil }
        return URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first { $0.name == "page" }?
            .value
    }
}

struct APIActivityRepository: ActivityRepository {
    let client: APIClient

    func userActivity(username: String, limit: Int) async throws -> [ActivityItem] {
        let response = try await userActivityPage(username: username, pageSize: limit, cursorLink: nil)
        return Array(response.results.prefix(limit))
    }

    func userActivityPage(username: String, pageSize: Int, cursorLink: String?) async throws -> ActivityCursorResponse {
        var query = [URLQueryItem(name: "page_size", value: String(pageSize))]
        if let cursor = Self.cursorValue(from: cursorLink) {
            query.append(URLQueryItem(name: "cursor", value: cursor))
        }

        let response: ActivityCursorResponse = try await client.get(
            "/users/\(username)/activity/",
            query: query,
            authenticated: true
        )
        return response
    }

    private static func cursorValue(from cursorLink: String?) -> String? {
        guard let cursorLink, !cursorLink.isEmpty else { return nil }
        guard let components = URLComponents(string: cursorLink) else { return cursorLink }
        return components.queryItems?.first(where: { $0.name == "cursor" })?.value ?? cursorLink
    }
}

struct APIProfileRepository: ProfileRepository {
    let client: APIClient

    func me() async throws -> UserProfile {
        try await client.get("/me/", authenticated: true)
    }

    func profile(username: String) async throws -> UserProfile {
        let escapedUsername = username.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? username
        return try await client.get("/users/\(escapedUsername)/", authenticated: true)
    }

    func statsSummary(username: String?, period: StatsPeriod) async throws -> StatsSummary {
        let path: String
        if let username {
            let escapedUsername = username.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? username
            path = "/users/\(escapedUsername)/stats/summary/"
        } else {
            path = "/stats/me/summary/"
        }
        return try await client.get(path, query: period.query, authenticated: true)
    }

    func likedMedia() async throws -> [MediaSummary] {
        var page: String?
        var media: [MediaSummary] = []

        repeat {
            let query = page.map { [URLQueryItem(name: "page", value: $0)] } ?? []
            let response: PagedResponse<MediaSummary> = try await client.get(
                "/me/liked-media/",
                query: query,
                authenticated: true
            )
            media += response.results
            page = APIPageCursor.nextPage(from: response.next)
        } while page != nil

        return media
    }

    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile {
        try await client.patch("/me/", body: request, authenticated: true)
    }

    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? {
        let response: AvatarUploadResponse = try await client.uploadMultipart(
            "/me/avatar/",
            formFields: [:],
            fileFieldName: "avatar",
            fileName: fileName,
            fileData: imageData,
            mimeType: mimeType,
            authenticated: true
        )
        return response.avatarUrl
    }

    func deleteAvatar() async throws -> String? {
        let response: AvatarUploadResponse = try await client.delete("/me/avatar/", authenticated: true)
        return response.avatarUrl
    }

    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse {
        try await client.put(
            "/me/profile-backdrop/",
            body: ProfileBackdropSaveRequest(ref: ref, backdropUrl: backdropURL),
            authenticated: true
        )
    }

    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse {
        try await client.delete("/me/profile-backdrop/", authenticated: true)
    }

    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences {
        try await client.patch("/me/preferences/", body: request, authenticated: true)
    }

    func changePassword(_ request: PasswordChangeRequest) async throws {
        let _: EmptyResponse = try await client.post("/me/password/", body: request, authenticated: true)
    }

    func setHallOfFameItem(mediaType: String, ref: MediaRef) async throws -> [String: MediaSummary?] {
        let response: HallOfFameItemsResponse = try await client.put(
            "/me/hof/\(mediaType)/",
            body: HallOfFameItemWriteRequest(ref: ref),
            authenticated: true
        )
        return response.items
    }

    func clearHallOfFameItem(mediaType: String) async throws -> [String: MediaSummary?] {
        let response: HallOfFameItemsResponse = try await client.delete(
            "/me/hof/\(mediaType)/",
            authenticated: true
        )
        return response.items
    }
}

struct APIListRepository: ListRepository {
    let client: APIClient

    func list() async throws -> [CustomListSummary] {
        let response: PagedResponse<CustomListSummary> = try await client.get(
            "/lists/",
            query: [URLQueryItem(name: "list_type", value: "all")],
            authenticated: true
        )
        return response.results
    }

    func list(membershipFor ref: MediaRef? = nil) async throws -> [CustomListSummary] {
        var query: [URLQueryItem] = []
        if let ref {
            query.append(URLQueryItem(name: "ref[source]", value: ref.source))
            query.append(URLQueryItem(name: "ref[media_type]", value: ref.mediaType))
            query.append(URLQueryItem(name: "ref[media_id]", value: ref.mediaId))
            if let seasonNumber = ref.seasonNumber {
                query.append(URLQueryItem(name: "ref[season_number]", value: String(seasonNumber)))
            }
            if let episodeNumber = ref.episodeNumber {
                query.append(URLQueryItem(name: "ref[episode_number]", value: String(episodeNumber)))
            }
        }
        let response: PagedResponse<CustomListSummary> = try await client.get(
            "/lists/",
            query: query,
            authenticated: true
        )
        return response.results
    }

    func peopleLists(membershipFor ref: PersonRef) async throws -> [CustomListSummary] {
        let response: PagedResponse<CustomListSummary> = try await client.get(
            "/lists/",
            query: [
                URLQueryItem(name: "list_type", value: CustomListType.people.rawValue),
                URLQueryItem(name: "person_ref[source]", value: ref.source),
                URLQueryItem(name: "person_ref[id]", value: ref.id),
            ],
            authenticated: true
        )
        return response.results
    }

    func detail(id: Int) async throws -> CustomListDetail {
        try await client.get(
            "/lists/\(id)/",
            query: [URLQueryItem(name: "include_items", value: "false")],
            authenticated: true
        )
    }

    func featured() async throws -> [CustomListSummary] {
        let response: PagedResponse<CustomListSummary> = try await client.get(
            "/lists/featured/",
            authenticated: true
        )
        return response.results
    }

    func create(_ request: CustomListWriteRequest) async throws -> CustomListSummary {
        try await client.post("/lists/", body: request, authenticated: true)
    }

    func update(id: Int, _ request: CustomListWriteRequest) async throws -> CustomListDetail {
        try await client.patch("/lists/\(id)/", body: request, authenticated: true)
    }

    func delete(id: Int) async throws {
        let _: EmptyResponse = try await client.delete("/lists/\(id)/", authenticated: true)
    }

    func addItem(listId: Int, ref: MediaRef) async throws -> MediaSummary {
        let response: ListItemWriteResponse = try await client.post(
            "/lists/\(listId)/items/",
            body: ListItemWriteRequest(ref: ref),
            authenticated: true
        )
        return response.item
    }

    func items(listId: Int, page: String?, filter: MediaFilterState) async throws -> PagedResponse<MediaSummary> {
        try await client.get(
            "/lists/\(listId)/items/",
            query: filter.queryItems(page: page),
            authenticated: true
        )
    }

    func removeItem(listId: Int, itemId: Int) async throws {
        let _: EmptyResponse = try await client.delete("/lists/\(listId)/items/\(itemId)/", authenticated: true)
    }

    func reorderItems(listId: Int, itemIds: [Int]) async throws -> CustomListDetail {
        try await client.patch(
            "/lists/\(listId)/items/reorder/",
            body: ListItemsReorderRequest(itemIds: itemIds),
            authenticated: true
        )
    }

    func people(listId: Int, page: String?) async throws -> PagedResponse<PersonListEntry> {
        var query: [URLQueryItem] = []
        if let page {
            query.append(URLQueryItem(name: "page", value: page))
        }
        return try await client.get(
            "/lists/\(listId)/people/",
            query: query,
            authenticated: true
        )
    }

    func addPerson(listId: Int, ref: PersonRef) async throws -> PersonListEntry {
        let response: ListPersonWriteResponse = try await client.post(
            "/lists/\(listId)/people/",
            body: ListPersonWriteRequest(ref: ref),
            authenticated: true
        )
        return response.person
    }

    func removePerson(listId: Int, entryId: Int) async throws {
        let _: EmptyResponse = try await client.delete(
            "/lists/\(listId)/people/\(entryId)/",
            authenticated: true
        )
    }

    func reorderPeople(listId: Int, entryIds: [Int]) async throws -> CustomListDetail {
        try await client.patch(
            "/lists/\(listId)/people/reorder/",
            body: ListPeopleReorderRequest(entryIds: entryIds),
            authenticated: true
        )
    }
}

struct APIFilterOptionsRepository: FilterOptionsRepository {
    let client: APIClient

    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse {
        guard var query = scope.optionsQueryItems else {
            return .empty
        }
        query += filter.queryItems()
        return try await client.get("/filter-options/", query: query, authenticated: true)
    }
}

struct APIImportRepository: ImportRepository {
    let client: APIClient

    func queueLetterboxdImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> ImportQueueResponse {
        try await client.uploadMultipart(
            "/imports/letterboxd/",
            formFields: ["mode": mode.rawValue],
            fileFieldName: "file",
            fileName: fileName,
            fileData: fileData,
            mimeType: "application/zip",
            authenticated: true,
            progressHandler: progressHandler
        )
    }

    func queueStoryGraphImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> ImportQueueResponse {
        try await client.uploadMultipart(
            "/imports/storygraph/",
            formFields: ["mode": mode.rawValue],
            fileFieldName: "file",
            fileName: fileName,
            fileData: fileData,
            mimeType: "text/csv",
            authenticated: true,
            progressHandler: progressHandler
        )
    }

    func queueGoodreadsImport(
        fileData: Data,
        fileName: String,
        mode: ImportMode,
        progressHandler: (@MainActor @Sendable (Double) -> Void)? = nil
    ) async throws -> ImportQueueResponse {
        try await client.uploadMultipart(
            "/imports/goodreads/",
            formFields: ["mode": mode.rawValue],
            fileFieldName: "file",
            fileName: fileName,
            fileData: fileData,
            mimeType: "text/csv",
            authenticated: true,
            progressHandler: progressHandler
        )
    }

    func importTaskStatus(taskId: String) async throws -> ImportTaskStatus {
        try await client.get("/imports/tasks/\(taskId)/", authenticated: true)
    }
}
