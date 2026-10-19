import XCTest
@testable import Spine

@MainActor
final class StreamingFilterTests: XCTestCase {
    // MARK: - Filter state

    func testAnyServiceSendsNothingAndIsNotAnActiveFilter() {
        let filter = MediaFilterState()

        XCTAssertEqual(filter.service, .any)
        XCTAssertEqual(filter.activeCount, 0)
        XCTAssertFalse(filter.queryItems().contains { $0.name == "service" })
    }

    func testMyServicesSendsMineAndCountsAsOneFilter() {
        var filter = MediaFilterState()
        filter.service = .mine

        XCTAssertEqual(serviceValues(filter.queryItems()), ["mine"])
        XCTAssertEqual(filter.activeCount, 1)
        XCTAssertTrue(filter.isActive)
    }

    func testSpecificServicesRepeatTheServiceParameterAndCountOnce() {
        var filter = MediaFilterState()
        filter.service = .providers([8, 337])

        XCTAssertEqual(serviceValues(filter.queryItems(page: "2", mediaType: "movie")), ["8", "337"])
        XCTAssertEqual(filter.activeCount, 1)
    }

    func testEmptyProviderSetIsInactive() {
        var filter = MediaFilterState()
        filter.service = .providers([])

        XCTAssertEqual(filter.activeCount, 0)
        XCTAssertTrue(serviceValues(filter.queryItems()).isEmpty)
    }

    func testServiceFilterCombinesWithOtherFilters() {
        var filter = MediaFilterState()
        filter.sort = .title
        filter.status = "Planning"
        filter.genres = ["Drama", "Comedy"]
        filter.year = 2024
        filter.service = .providers([8])

        let query = filter.queryItems(page: "3", mediaType: "tv")

        XCTAssertEqual(query.first { $0.name == "media_type" }?.value, "tv")
        XCTAssertEqual(query.first { $0.name == "status" }?.value, "Planning")
        XCTAssertEqual(query.filter { $0.name == "genre" }.map(\.value), ["Drama", "Comedy"])
        XCTAssertEqual(query.first { $0.name == "year" }?.value, "2024")
        XCTAssertEqual(query.first { $0.name == "page" }?.value, "3")
        XCTAssertEqual(serviceValues(query), ["8"])
        XCTAssertEqual(filter.activeCount, 5)
    }

    func testPickingServicesLeavesAnyOrMineAndUnpickingTheLastReturnsToAny() {
        XCTAssertEqual(StreamingFilterService.any.togglingProvider(8), .providers([8]))
        XCTAssertEqual(StreamingFilterService.mine.togglingProvider(8), .providers([8]))
        XCTAssertEqual(StreamingFilterService.providers([8]).togglingProvider(337), .providers([8, 337]))
        XCTAssertEqual(StreamingFilterService.providers([8, 337]).togglingProvider(8), .providers([337]))
        XCTAssertEqual(StreamingFilterService.providers([337]).togglingProvider(337), .any)
        XCTAssertTrue(StreamingFilterService.providers([8, 337]).includes(providerId: 337))
        XCTAssertFalse(StreamingFilterService.mine.includes(providerId: 8))
    }

    func testNarrowingToAMediaTypeWithoutAvailabilityClearsTheServiceFilter() {
        var filter = MediaFilterState()
        filter.service = .mine

        filter.selectMediaType("tv")
        XCTAssertEqual(filter.service, .mine)

        filter.selectMediaType(nil)
        XCTAssertEqual(filter.service, .mine)

        filter.selectMediaType("book")
        XCTAssertEqual(filter.mediaType, "book")
        XCTAssertEqual(filter.service, .any)
    }

    // MARK: - Filter options

    func testFilterOptionsDecodeTheStreamingObject() throws {
        let options = try decode(MediaFilterOptionsResponse.self, """
        {
          "sorts": [{ "value": "title", "label": "Title" }],
          "genres": [{ "value": "Drama", "label": "Drama" }],
          "languages": [],
          "years": [2024],
          "streaming": {
            "available": true,
            "region": "US",
            "region_name": "United States",
            "services": [
              { "value": "8", "label": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/netflix.png" },
              { "value": "1899", "label": "HBO Max", "logo_url": null }
            ]
          }
        }
        """)

        XCTAssertEqual(options.genres.map(\.value), ["Drama"])
        XCTAssertEqual(options.streaming, StreamingFilterOptions(
            available: true,
            region: "US",
            regionName: "United States",
            services: [
                StreamingFilterServiceOption(
                    providerId: 8,
                    label: "Netflix",
                    logoURL: "https://image.tmdb.org/t/p/w154/netflix.png"
                ),
                StreamingFilterServiceOption(providerId: 1899, label: "HBO Max"),
            ]
        ))
        XCTAssertEqual(options.streaming?.attributionText, "Availability in United States · Powered by JustWatch")
    }

    func testFilterOptionsWithoutStreamingStillDecode() throws {
        let options = try decode(MediaFilterOptionsResponse.self, """
        { "sorts": [{ "value": "title", "label": "Title" }], "genres": [], "languages": [], "years": [] }
        """)

        XCTAssertEqual(options.sorts.map(\.value), ["title"])
        XCTAssertNil(options.streaming)
        XCTAssertNil(MediaFilterOptionsResponse.empty.streaming)
    }

    func testUnavailableStreamingWithNoServicesDecodes() throws {
        let options = try decode(MediaFilterOptionsResponse.self, """
        { "sorts": [], "streaming": { "available": false, "region": "GB", "region_name": "United Kingdom", "services": [] } }
        """)

        XCTAssertEqual(options.streaming?.available, false)
        XCTAssertEqual(options.streaming?.services, [])
        XCTAssertEqual(options.streaming?.region, "GB")
    }

    func testUnreadableStreamingBlockKeepsTheOtherFilters() throws {
        let options = try decode(MediaFilterOptionsResponse.self, """
        {
          "sorts": [{ "value": "title", "label": "Title" }],
          "genres": [{ "value": "Drama", "label": "Drama" }],
          "streaming": { "available": true, "services": [{ "value": "netflix", "label": "Netflix" }] }
        }
        """)

        XCTAssertEqual(options.genres.map(\.value), ["Drama"])
        XCTAssertNil(options.streaming)
    }

    func testServiceValuesMayArriveAsNumbers() throws {
        let streaming = try decode(StreamingFilterOptions.self, """
        { "available": true, "services": [{ "value": 337, "label": "Disney Plus" }] }
        """)

        XCTAssertEqual(streaming.services.map(\.providerId), [337])
        XCTAssertNil(streaming.region)
        XCTAssertEqual(streaming.attributionText, "Powered by JustWatch")
    }

    // MARK: - Collection pages

    func testTrackingPageDecodesTheStreamingBlock() throws {
        let page = try decode(PagedResponse<Spine.LibraryItem>.self, """
        {
          "count": 1,
          "next": null,
          "previous": null,
          "results": [\(Self.libraryItemJSON(mediaId: "693134", title: "Dune: Part Two"))],
          "streaming": { "region": "US", "pending_count": 12 }
        }
        """)

        XCTAssertEqual(page.results.map(\.media.title), ["Dune: Part Two"])
        XCTAssertEqual(page.streaming, StreamingFilterPageInfo(region: "US", pendingCount: 12))
    }

    func testListItemsPageWithoutStreamingDecodes() throws {
        let page = try decode(PagedResponse<MediaSummary>.self, """
        {
          "count": 1,
          "next": null,
          "previous": null,
          "results": [\(Self.mediaSummaryJSON(mediaId: "550", title: "Fight Club"))]
        }
        """)

        XCTAssertEqual(page.results.map(\.title), ["Fight Club"])
        XCTAssertNil(page.streaming)
    }

    func testListItemsPageDecodesTheStreamingBlock() throws {
        let page = try decode(PagedResponse<MediaSummary>.self, """
        { "count": 0, "next": null, "previous": null, "results": [], "streaming": { "region": "CA", "pending_count": 0 } }
        """)

        XCTAssertEqual(page.streaming?.region, "CA")
        XCTAssertEqual(page.streaming?.pendingCount, 0)
    }

    func testDiaryPageDecodesWithAndWithoutTheStreamingBlock() throws {
        let withStreaming = try decode(PagedResponse<DiaryEntry>.self, """
        {
          "count": 1,
          "next": null,
          "previous": null,
          "results": [\(Self.diaryEntryJSON(id: 4, title: "Severance"))],
          "streaming": { "region": "US", "pending_count": 1 }
        }
        """)
        let withoutStreaming = try decode(PagedResponse<DiaryEntry>.self, """
        { "count": 1, "next": null, "previous": null, "results": [\(Self.diaryEntryJSON(id: 4, title: "Severance"))] }
        """)

        XCTAssertEqual(withStreaming.results.map(\.id), [4])
        XCTAssertEqual(withStreaming.streaming?.pendingCount, 1)
        XCTAssertNil(withoutStreaming.streaming)
    }

    // MARK: - Sheet visibility

    func testServiceSectionShowsOnlyForMovieAndTVLibraryShelves() {
        let options = Self.options(available: true)

        XCTAssertTrue(StreamingFilterRules.showsServiceSection(scope: .tracking(mediaType: "movie"), options: options, mediaType: nil))
        XCTAssertTrue(StreamingFilterRules.showsServiceSection(scope: .tracking(mediaType: "tv"), options: options, mediaType: nil))
        for mediaType in ["anime", "manga", "game", "book", "comic", "music"] {
            XCTAssertFalse(
                StreamingFilterRules.showsServiceSection(scope: .tracking(mediaType: mediaType), options: options, mediaType: nil),
                mediaType
            )
        }
    }

    func testServiceSectionShowsForListsAndDiaryUnlessNarrowedToAnotherType() {
        let options = Self.options(available: true)

        for scope in [MediaFilterScope.list(id: 3), .diary] {
            XCTAssertTrue(StreamingFilterRules.showsServiceSection(scope: scope, options: options, mediaType: nil))
            XCTAssertTrue(StreamingFilterRules.showsServiceSection(scope: scope, options: options, mediaType: "movie"))
            XCTAssertTrue(StreamingFilterRules.showsServiceSection(scope: scope, options: options, mediaType: "tv"))
            XCTAssertFalse(StreamingFilterRules.showsServiceSection(scope: scope, options: options, mediaType: "book"))
        }
    }

    func testServiceSectionHiddenForPeopleCompaniesAndUnavailableScopes() {
        let available = Self.options(available: true)
        let unavailable = Self.options(available: false)
        let missing = MediaFilterOptionsResponse(sorts: [], genres: [], languages: [], years: [])

        XCTAssertFalse(StreamingFilterRules.showsServiceSection(
            scope: .person(ref: PersonRef(source: "tmdb", id: "1")),
            options: available,
            mediaType: nil
        ))
        XCTAssertFalse(StreamingFilterRules.showsServiceSection(
            scope: .company(ref: CompanyRef(source: "igdb", companyId: "77")),
            options: available,
            mediaType: nil
        ))
        XCTAssertFalse(StreamingFilterRules.showsServiceSection(scope: .tracking(mediaType: "movie"), options: unavailable, mediaType: nil))
        XCTAssertFalse(StreamingFilterRules.showsServiceSection(scope: .list(id: 3), options: missing, mediaType: nil))
        XCTAssertFalse(StreamingFilterRules.showsServiceSection(scope: .diary, options: .empty, mediaType: nil))
    }

    // MARK: - Copy

    func testPendingNoteAppearsOnlyWhileAServiceFilterIsOn() {
        XCTAssertEqual(
            StreamingFilterCopy.pendingNote(pendingCount: 12, service: .mine),
            "Still checking availability for 12 titles — pull to refresh"
        )
        XCTAssertEqual(
            StreamingFilterCopy.pendingNote(pendingCount: 1, service: .providers([8])),
            "Still checking availability for 1 title — pull to refresh"
        )
        XCTAssertNil(StreamingFilterCopy.pendingNote(pendingCount: 12, service: .any))
        XCTAssertNil(StreamingFilterCopy.pendingNote(pendingCount: 0, service: .mine))
    }

    func testEmptyTitleNamesTheChosenServices() {
        let streaming = StreamingFilterOptions(available: true, services: [
            StreamingFilterServiceOption(providerId: 8, label: "Netflix"),
            StreamingFilterServiceOption(providerId: 1899, label: "HBO Max"),
            StreamingFilterServiceOption(providerId: 337, label: "Disney Plus"),
        ])

        XCTAssertEqual(StreamingFilterCopy.emptyTitle(service: .mine, options: streaming), "Nothing streaming on your services")
        XCTAssertEqual(StreamingFilterCopy.emptyTitle(service: .providers([8]), options: streaming), "Nothing streaming on Netflix")
        XCTAssertEqual(
            StreamingFilterCopy.emptyTitle(service: .providers([8, 1899]), options: streaming),
            "Nothing streaming on Netflix or HBO Max"
        )
        XCTAssertEqual(
            StreamingFilterCopy.emptyTitle(service: .providers([8, 1899, 337]), options: streaming),
            "Nothing streaming on the services you chose"
        )
        XCTAssertEqual(
            StreamingFilterCopy.emptyTitle(service: .providers([9]), options: streaming),
            "Nothing streaming on the services you chose"
        )
    }

    // MARK: - Discover

    func testDiscoverOnMyServicesNeedsNoGenreOrYear() {
        let request = MediaDiscoverRequest(mediaType: "movie", source: "tmdb", filter: nil, service: .mine)
        let names = request.queryItems.map(\.name)

        XCTAssertEqual(request.queryItems.first { $0.name == "media_type" }?.value, "movie")
        XCTAssertEqual(request.queryItems.first { $0.name == "sort" }?.value, "vote_count")
        XCTAssertEqual(serviceValues(request.queryItems), ["mine"])
        XCTAssertFalse(names.contains("genre"))
        XCTAssertFalse(names.contains("year"))
        XCTAssertFalse(names.contains("platform"))
        XCTAssertEqual(request.title, "On My Services · Movies")
        XCTAssertTrue(request.supportsServiceFilter)
    }

    func testDiscoverServiceCombinesWithThePillFilter() {
        var request = MediaDiscoverRequest(mediaType: "tv", source: "tmdb", filter: .genre("Drama"))
        let plainID = request.id
        request.service = .mine

        XCTAssertEqual(request.queryItems.first { $0.name == "genre" }?.value, "Drama")
        XCTAssertEqual(serviceValues(request.queryItems), ["mine"])
        XCTAssertEqual(request.title, "Drama · TV")
        XCTAssertEqual(plainID, "tv:tmdb:genre:Drama")
        XCTAssertNotEqual(request.id, plainID)
    }

    func testOnlyMovieAndTVDiscoverSupportsTheServiceFilter() {
        XCTAssertTrue(MediaDiscoverRequest(mediaType: "movie", source: nil, filter: .year("2024")).supportsServiceFilter)
        XCTAssertFalse(MediaDiscoverRequest(mediaType: "book", source: "openlibrary", filter: .year("1965")).supportsServiceFilter)
        XCTAssertFalse(MediaDiscoverRequest(mediaType: "anime", source: "mal", filter: .genre("Action")).supportsServiceFilter)
        XCTAssertFalse(MediaDiscoverRequest(mediaType: "movie", source: "manual", filter: .genre("Drama")).supportsServiceFilter)
    }

    func testChooseServicesErrorIsRecognizedInBothErrorShapes() {
        let raw = APIError.httpStatus(400, #"{"service": ["Choose your streaming services first."]}"#)
        let envelope = APIError.httpStatus(400, #"""
        {"error": {"code": "validation_error", "message": "One or more fields are invalid.", "fields": {"service": ["Choose your streaming services first."]}, "request_id": null}}
        """#)
        let otherField = APIError.httpStatus(400, #"{"non_field_errors": ["At least one of genre, year, or platform is required."]}"#)
        let serverError = APIError.httpStatus(500, #"{"service": ["Choose your streaming services first."]}"#)

        XCTAssertTrue(StreamingFilterRules.isChooseServicesError(raw))
        XCTAssertTrue(StreamingFilterRules.isChooseServicesError(envelope))
        XCTAssertFalse(StreamingFilterRules.isChooseServicesError(otherField))
        XCTAssertFalse(StreamingFilterRules.isChooseServicesError(serverError))
        XCTAssertFalse(StreamingFilterRules.isChooseServicesError(APIError.unauthorized))
    }

    func testDiscoverAsksForServicesWhenTheServerNeedsThem() async throws {
        let repository = StreamingFilterDiscoverRepository()
        let fightClub = try Self.mediaSummary(mediaId: "550", title: "Fight Club")
        repository.discoverHandler = { request in
            guard request.service.isActive else {
                return PagedResponse(count: 1, next: nil, previous: nil, results: [fightClub])
            }
            throw APIError.httpStatus(400, #"{"service": ["Choose your streaming services first."]}"#)
        }
        let viewModel = MediaDiscoverViewModel(
            request: MediaDiscoverRequest(mediaType: "movie", source: "tmdb", filter: .genre("Drama")),
            mediaRepository: repository,
            onUnauthorized: {}
        )

        await viewModel.load()
        XCTAssertEqual(viewModel.results.map(\.title), ["Fight Club"])
        XCTAssertFalse(viewModel.needsStreamingServices)

        await viewModel.setOnMyServices(true)
        XCTAssertTrue(viewModel.needsStreamingServices)
        XCTAssertTrue(viewModel.results.isEmpty)
        XCTAssertEqual(repository.requests.last.map { serviceValues($0.queryItems) }, ["mine"])

        await viewModel.setOnMyServices(false)
        XCTAssertFalse(viewModel.needsStreamingServices)
        XCTAssertEqual(viewModel.results.map(\.title), ["Fight Club"])
        XCTAssertEqual(repository.requests.last?.service, .any)
    }

    func testDiscoverDropsThePillFilterOnlyWhileOnMyServices() async throws {
        let repository = StreamingFilterDiscoverRepository()
        let severance = try Self.mediaSummary(mediaId: "95396", mediaType: "tv", title: "Severance")
        repository.discoverHandler = { _ in
            PagedResponse(count: 1, next: nil, previous: nil, results: [severance])
        }
        let viewModel = MediaDiscoverViewModel(
            request: MediaDiscoverRequest(mediaType: "tv", source: "tmdb", filter: .year("2022")),
            mediaRepository: repository,
            onUnauthorized: {}
        )
        await viewModel.load()

        await viewModel.setIncludesPillFilter(false)
        XCTAssertTrue(viewModel.includesPillFilter)
        XCTAssertFalse(viewModel.canTogglePillFilter)
        XCTAssertEqual(repository.requests.count, 1)

        await viewModel.setOnMyServices(true)
        XCTAssertTrue(viewModel.canTogglePillFilter)
        await viewModel.setIncludesPillFilter(false)

        let servicesOnly = try XCTUnwrap(repository.requests.last)
        XCTAssertNil(servicesOnly.filter)
        XCTAssertNil(servicesOnly.queryItems.first { $0.name == "year" })
        XCTAssertEqual(serviceValues(servicesOnly.queryItems), ["mine"])
        XCTAssertEqual(viewModel.request.title, "On My Services · TV")
        XCTAssertEqual(viewModel.results.map(\.title), ["Severance"])

        await viewModel.setOnMyServices(false)
        XCTAssertEqual(repository.requests.last?.filter, .year("2022"))
        XCTAssertEqual(repository.requests.last?.service, .any)
    }

    // MARK: - Library

    func testLibraryKeepsTheServiceFilterForTVAndClearsItForOtherTypes() async {
        let tracking = StreamingFilterTrackingRepository()
        let viewModel = LibraryViewModel(
            mediaRepository: StreamingFilterDiscoverRepository(),
            trackingRepository: tracking,
            filterOptionsRepository: StreamingFilterOptionsRepository(options: Self.options(available: true)),
            onUnauthorized: {}
        )
        viewModel.filter.service = .mine

        await viewModel.selectMediaType("tv")
        XCTAssertEqual(viewModel.filter.service, .mine)
        XCTAssertEqual(tracking.requests.last?.mediaType, "tv")
        XCTAssertEqual(tracking.requests.last.map { serviceValues($0.filter.queryItems()) }, ["mine"])

        await viewModel.selectMediaType("book")
        XCTAssertEqual(viewModel.filter.service, .any)
        XCTAssertEqual(tracking.requests.last?.mediaType, "book")
        XCTAssertEqual(tracking.requests.last.map { serviceValues($0.filter.queryItems()) }, [])
    }

    func testLibraryShowsThePendingNoteWhileFilteringByService() async throws {
        let tracking = StreamingFilterTrackingRepository()
        tracking.response = PagedResponse(
            count: 1,
            next: nil,
            previous: nil,
            results: [try Self.libraryItem(mediaId: "693134", title: "Dune: Part Two")],
            streaming: StreamingFilterPageInfo(region: "US", pendingCount: 12)
        )
        let viewModel = LibraryViewModel(
            mediaRepository: StreamingFilterDiscoverRepository(),
            trackingRepository: tracking,
            filterOptionsRepository: StreamingFilterOptionsRepository(options: Self.options(available: true)),
            onUnauthorized: {}
        )
        viewModel.filter.service = .mine

        await viewModel.reload()

        XCTAssertEqual(viewModel.streamingPendingCount, 12)
        XCTAssertEqual(viewModel.streamingPendingNote, "Still checking availability for 12 titles — pull to refresh")

        viewModel.filter.service = .any
        XCTAssertNil(viewModel.streamingPendingNote)
    }

    func testLibraryMyServicesToggleNeedsServicesUnlessAFilterIsOn() async {
        let viewModel = LibraryViewModel(
            mediaRepository: StreamingFilterDiscoverRepository(),
            trackingRepository: StreamingFilterTrackingRepository(),
            filterOptionsRepository: StreamingFilterOptionsRepository(options: Self.options(available: true)),
            onUnauthorized: {}
        )
        XCTAssertFalse(viewModel.showsMyServicesToggle)

        await viewModel.loadFilterOptions()
        XCTAssertTrue(viewModel.showsMyServicesToggle)

        await viewModel.toggleMyServices()
        XCTAssertEqual(viewModel.filter.service, .mine)
        await viewModel.toggleMyServices()
        XCTAssertEqual(viewModel.filter.service, .any)

        viewModel.filterOptions = Self.options(available: true, services: [])
        XCTAssertFalse(viewModel.showsMyServicesToggle)
        viewModel.filter.service = .providers([8])
        XCTAssertTrue(viewModel.showsMyServicesToggle)
    }

    // MARK: - Repository wiring

    func testTrackingRepositorySendsRepeatedServicesAndReadsPendingCount() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StreamingFilterCaptureURLProtocol.self]
        let client = APIClient(
            baseURL: URL(string: "https://example.com")!,
            tokenProvider: KeychainTokenStore.shared,
            session: URLSession(configuration: config)
        )
        client.tokenProvider.accessToken = "access"
        defer {
            StreamingFilterCaptureURLProtocol.handler = nil
            client.tokenProvider.clear()
        }
        let itemJSON = Self.libraryItemJSON(mediaId: "693134", title: "Dune: Part Two")
        StreamingFilterCaptureURLProtocol.handler = { request in
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            let query = components.queryItems ?? []
            XCTAssertEqual(components.path, "/api/v1/tracking/")
            XCTAssertEqual(query.filter { $0.name == "service" }.map(\.value), ["8", "337"])
            XCTAssertEqual(query.first { $0.name == "media_type" }?.value, "movie")
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                Data("""
                {
                  "count": 1,
                  "next": null,
                  "previous": null,
                  "results": [\(itemJSON)],
                  "streaming": { "region": "US", "pending_count": 3 }
                }
                """.utf8)
            )
        }
        var filter = MediaFilterState()
        filter.service = .providers([8, 337])

        let page = try await APITrackingRepository(client: client).list(mediaType: "movie", page: nil, filter: filter)

        XCTAssertEqual(page.results.map(\.media.title), ["Dune: Part Two"])
        XCTAssertEqual(page.streaming?.pendingCount, 3)
    }

    // MARK: - Helpers

    private func serviceValues(_ items: [URLQueryItem]) -> [String?] {
        items.filter { $0.name == "service" }.map(\.value)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    /// Netflix is the user's one service unless `services` says otherwise.
    private static func options(
        available: Bool,
        services: [StreamingFilterServiceOption]? = nil
    ) -> MediaFilterOptionsResponse {
        MediaFilterOptionsResponse(
            sorts: [FilterChoice(value: "title", label: "Title")],
            genres: [],
            languages: [],
            years: [],
            streaming: StreamingFilterOptions(
                available: available,
                region: "US",
                regionName: "United States",
                services: services ?? [StreamingFilterServiceOption(providerId: 8, label: "Netflix")]
            )
        )
    }

    private static func mediaSummaryJSON(mediaId: String, mediaType: String = "movie", title: String) -> String {
        """
        {
          "ref": { "item_id": null, "source": "tmdb", "media_type": "\(mediaType)", "media_id": "\(mediaId)", "season_number": null, "episode_number": null },
          "title": "\(title)",
          "image_url": "https://example.com/\(mediaId).jpg",
          "poster_url": "https://example.com/\(mediaId).jpg",
          "poster_orientation": "portrait"
        }
        """
    }

    private static func libraryItemJSON(mediaId: String, title: String) -> String {
        """
        { "media": \(mediaSummaryJSON(mediaId: mediaId, title: title)), "tracking": { "tracking_id": 1, "status": "Planning" } }
        """
    }

    private static func diaryEntryJSON(id: Int, title: String) -> String {
        """
        {
          "id": \(id),
          "user": { "id": 1, "username": "mobile", "display_name": "Mobile", "avatar_url": null },
          "media": {
            "ref": { "item_id": \(100 + id), "source": "tmdb", "media_type": "tv", "media_id": "95396", "season_number": null, "episode_number": null },
            "title": "\(title)",
            "image_url": null
          },
          "consumed_at": "2026-06-20T12:00:00Z",
          "rating": null,
          "review_title": "",
          "review": "",
          "contains_spoilers": false,
          "liked": false,
          "is_rewatch": false,
          "tags": [],
          "visibility": "public",
          "like_count": 0,
          "viewer_has_liked": false,
          "created_at": "2026-06-20T12:00:00Z",
          "updated_at": "2026-06-20T12:00:00Z"
        }
        """
    }

    private static func mediaSummary(mediaId: String, mediaType: String = "movie", title: String) throws -> MediaSummary {
        try JSONDecoder.api.decode(
            MediaSummary.self,
            from: Data(mediaSummaryJSON(mediaId: mediaId, mediaType: mediaType, title: title).utf8)
        )
    }

    private static func libraryItem(mediaId: String, title: String) throws -> Spine.LibraryItem {
        try JSONDecoder.api.decode(Spine.LibraryItem.self, from: Data(libraryItemJSON(mediaId: mediaId, title: title).utf8))
    }
}

/// A media repository that only answers Discover, from a scripted handler.
@MainActor
private final class StreamingFilterDiscoverRepository: MediaRepository {
    var discoverHandler: (MediaDiscoverRequest) throws -> PagedResponse<MediaSummary> = { _ in
        PagedResponse(count: 0, next: nil, previous: nil, results: [])
    }
    private(set) var requests: [MediaDiscoverRequest] = []

    func discover(_ request: MediaDiscoverRequest) async throws -> PagedResponse<MediaSummary> {
        requests.append(request)
        return try discoverHandler(request)
    }

    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func logos(ref: MediaRef) async throws -> [LogoOption] { fatalError("Not used") }
    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse { fatalError("Not used") }
}

@MainActor
private final class StreamingFilterTrackingRepository: TrackingRepository {
    struct Request {
        let mediaType: String
        let page: String?
        let filter: MediaFilterState
    }

    var response = PagedResponse<Spine.LibraryItem>(count: 0, next: nil, previous: nil, results: [])
    private(set) var requests: [Request] = []

    func list(mediaType: String, page: String?, filter: MediaFilterState) async throws -> PagedResponse<Spine.LibraryItem> {
        requests.append(Request(mediaType: mediaType, page: page, filter: filter))
        return response
    }

    func list(mediaType: String, page: String?, status: String?, query: String?) async throws -> PagedResponse<Spine.LibraryItem> {
        fatalError("Not used")
    }

    func detail(ref: MediaRef) async throws -> TrackingState { fatalError("Not used") }
    func update(ref: MediaRef, request: TrackingWriteRequest) async throws -> TrackingState { fatalError("Not used") }
    func consume(ref: MediaRef, consumedAt: Date?) async throws -> TrackingState { fatalError("Not used") }
    func watchSeason(source: String, mediaId: String, seasonNumber: Int) async throws -> TrackingState { fatalError("Not used") }

    func updateBookProgress(
        source: String,
        mediaId: String,
        progressType: String,
        value: Decimal,
        notes: String
    ) async throws -> TrackingState {
        fatalError("Not used")
    }

    func completeBook(source: String, mediaId: String, completedAt: Date?) async throws -> TrackingState {
        fatalError("Not used")
    }
}

private struct StreamingFilterOptionsRepository: FilterOptionsRepository {
    let options: MediaFilterOptionsResponse

    func options(scope: MediaFilterScope, filter: MediaFilterState) async throws -> MediaFilterOptionsResponse {
        options
    }
}

private final class StreamingFilterCaptureURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: APIError.invalidResponse)
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
