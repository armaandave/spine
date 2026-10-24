import XCTest
@testable import Spine

// Contract: docs/streaming-availability-contract.md

@MainActor
final class StreamingContractTests: XCTestCase {
    override func tearDown() {
        StreamingURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - Models

    func testProviderRegionAndCatalogResponsesDecode() throws {
        let provider = try decode(StreamingProvider.self, #"""
        { "provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/abc.png", "display_priority": 1 }
        """#)
        XCTAssertEqual(provider, StreamingProvider(
            providerId: 8,
            name: "Netflix",
            logoUrl: "https://image.tmdb.org/t/p/w154/abc.png",
            displayPriority: 1
        ))
        XCTAssertEqual(provider.id, 8)

        let regions = try decode(StreamingRegionsResponse.self, #"""
        { "results": [{ "code": "CA", "name": "Canada" }, { "code": "US", "name": "United States" }] }
        """#)
        XCTAssertEqual(regions.results.map(\.code), ["CA", "US"])
        XCTAssertEqual(regions.results.last?.name, "United States")

        let catalog = try decode(StreamingProvidersResponse.self, #"""
        { "region": "US", "results": [
            { "provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/abc.png", "display_priority": 1 },
            { "provider_id": 15, "name": "Hulu", "logo_url": null, "display_priority": 4 }
        ] }
        """#)
        XCTAssertEqual(catalog.region, "US")
        XCTAssertEqual(catalog.results.map(\.name), ["Netflix", "Hulu"])
        XCTAssertNil(catalog.results.last?.logoUrl)
    }

    func testPreferencesDecode() throws {
        let preferences = try decode(StreamingPreferences.self, Self.preferencesJSON)
        XCTAssertEqual(preferences.region, "US")
        XCTAssertEqual(preferences.regionName, "United States")
        XCTAssertTrue(preferences.regionIsSet)
        XCTAssertTrue(preferences.enabled)
        XCTAssertEqual(preferences.providerIds, [8, 337])
        XCTAssertEqual(preferences.providers.map(\.name), ["Netflix", "Disney Plus"])
        XCTAssertEqual(preferences.attribution, StreamingAttribution(name: "JustWatch", url: "https://www.justwatch.com"))
    }

    func testTurnedOffPreferencesDecodeWithoutRegion() throws {
        let preferences = try decode(StreamingPreferences.self, #"""
        { "region": null, "region_name": null, "region_is_set": false, "enabled": false,
          "provider_ids": [], "providers": [], "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" } }
        """#)
        XCTAssertNil(preferences.region)
        XCTAssertNil(preferences.regionName)
        XCTAssertFalse(preferences.enabled)
        XCTAssertFalse(preferences.regionIsSet)
        XCTAssertEqual(preferences.servicesSummary, "Off")
    }

    func testServicesSummary() throws {
        func summary(_ names: [String], enabled: Bool = true) -> String {
            StreamingPreferences(
                region: "US",
                regionName: "United States",
                regionIsSet: true,
                enabled: enabled,
                providerIds: Array(names.indices),
                providers: names.enumerated().map {
                    StreamingProvider(providerId: $0.offset, name: $0.element, logoUrl: nil, displayPriority: $0.offset)
                },
                attribution: nil
            ).servicesSummary
        }

        XCTAssertEqual(summary([]), "Not set")
        XCTAssertEqual(summary(["Netflix"]), "Netflix")
        XCTAssertEqual(summary(["Netflix", "Hulu"]), "Netflix, Hulu")
        XCTAssertEqual(summary(["Netflix", "Hulu", "Max", "Peacock"]), "Netflix, Hulu +2")
        XCTAssertEqual(summary(["Netflix"], enabled: false), "Off")
    }

    func testUpdateRequestEncodesBothFieldsInSnakeCase() throws {
        let data = try JSONEncoder.api.encode(StreamingPreferencesUpdateRequest(region: "US", providerIds: [8, 337]))
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(Set(json.keys), ["region", "provider_ids"])
        XCTAssertEqual(json["region"] as? String, "US")
        XCTAssertEqual(json["provider_ids"] as? [Int], [8, 337])

        let empty = try JSONEncoder.api.encode(StreamingPreferencesUpdateRequest(region: "GB", providerIds: []))
        let emptyJSON = try XCTUnwrap(JSONSerialization.jsonObject(with: empty) as? [String: Any])
        XCTAssertEqual(emptyJSON["provider_ids"] as? [Int], [])
    }

    func testWhereToWatchDecodesTheContractExample() throws {
        let whereToWatch = try decode(WhereToWatch.self, Self.whereToWatchJSON)
        XCTAssertEqual(whereToWatch.region, "US")
        XCTAssertEqual(whereToWatch.regionName, "United States")
        XCTAssertEqual(
            whereToWatch.linkURL?.absoluteString,
            "https://www.themoviedb.org/movie/693134-dune-part-two/watch?locale=US"
        )
        XCTAssertEqual(whereToWatch.providers.map(\.name), ["Tubi TV", "HBO Max"])
        let tubi = try XCTUnwrap(whereToWatch.providers.first)
        XCTAssertEqual(tubi.providerId, 73)
        XCTAssertEqual(tubi.displayPriority, 20)
        XCTAssertTrue(tubi.isMine)
        XCTAssertFalse(try XCTUnwrap(whereToWatch.providers.last).isMine)
        XCTAssertEqual(whereToWatch.attribution?.name, "JustWatch")
    }

    func testWhereToWatchWithNoServicesAndNoLink() throws {
        let whereToWatch = try decode(WhereToWatch.self, #"""
        { "region": "GB", "region_name": "United Kingdom", "link": null, "providers": [],
          "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" } }
        """#)
        XCTAssertNil(whereToWatch.link)
        XCTAssertNil(whereToWatch.linkURL)
        XCTAssertTrue(whereToWatch.providers.isEmpty)
    }

    func testStreamingPillsReadTheServiceName() throws {
        let whereToWatch = try decode(WhereToWatch.self, Self.whereToWatchJSON)
        XCTAssertEqual(whereToWatch.providers.map(\.accessibilityLabel), ["Tubi TV, one of your services", "HBO Max"])
    }

    func testCountryNamesSkipAServerNameThatIsOnlyTheCode() throws {
        let deviceName = Locale.current.localizedString(forRegionCode: "DE") ?? "DE"
        XCTAssertEqual(StreamingRegion.displayName(code: "DE", serverName: "Germany"), "Germany")
        XCTAssertEqual(StreamingRegion.displayName(code: "DE", serverName: "DE"), deviceName)
        XCTAssertEqual(StreamingRegion.displayName(code: "DE", serverName: ""), deviceName)
        XCTAssertEqual(StreamingRegion.displayName(code: "DE", serverName: nil), deviceName)

        XCTAssertEqual(
            StreamingFilterOptions(available: true, region: "DE", regionName: "DE").attributionText,
            "Availability in \(deviceName) · Powered by JustWatch"
        )
        XCTAssertEqual(
            StreamingFilterOptions(available: true, regionName: "Germany").attributionText,
            "Availability in Germany · Powered by JustWatch"
        )
    }

    func testProviderWithoutIsMineIsNotTheUsers() throws {
        let provider = try decode(WhereToWatchProvider.self, #"""
        { "provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/abc.png", "display_priority": 1 }
        """#)
        XCTAssertFalse(provider.isMine)
    }

    func testOnlyWebLinksOpen() throws {
        let whereToWatch = try decode(WhereToWatch.self, #"""
        { "region": "US", "region_name": "United States", "link": "javascript:alert(1)", "providers": [],
          "attribution": null }
        """#)
        XCTAssertNil(whereToWatch.linkURL)
        XCTAssertEqual(StreamingAttribution.justWatch.destination?.absoluteString, "https://www.justwatch.com")
    }

    func testOnlyTMDBMoviesShowsAndSeasonsHaveAvailability() {
        func ref(_ source: String, _ mediaType: String, season: Int? = nil, episode: Int? = nil) -> MediaRef {
            MediaRef(itemId: nil, source: source, mediaType: mediaType, mediaId: "1", seasonNumber: season, episodeNumber: episode)
        }
        XCTAssertTrue(WhereToWatch.isAvailable(for: ref("tmdb", "movie")))
        XCTAssertTrue(WhereToWatch.isAvailable(for: ref("tmdb", "tv")))
        XCTAssertTrue(WhereToWatch.isAvailable(for: ref("tmdb", "season", season: 1)))
        XCTAssertFalse(WhereToWatch.isAvailable(for: ref("tmdb", "episode", season: 1, episode: 2)))
        XCTAssertFalse(WhereToWatch.isAvailable(for: ref("mal", "anime")))
        XCTAssertFalse(WhereToWatch.isAvailable(for: ref("igdb", "game")))
    }

    // MARK: - Media detail

    func testMediaDetailDecodesWhereToWatch() throws {
        let detail = try decode(MediaDetail.self, Self.mediaDetailJSON(whereToWatch: Self.whereToWatchJSON))
        let whereToWatch = try XCTUnwrap(detail.whereToWatch)
        XCTAssertEqual(whereToWatch.region, "US")
        XCTAssertEqual(whereToWatch.providers.first?.name, "Tubi TV")
        XCTAssertNotNil(detail.providers, "The legacy providers field is unchanged")
    }

    func testMediaDetailWithoutWhereToWatch() throws {
        let null = try decode(MediaDetail.self, Self.mediaDetailJSON(whereToWatch: "null"))
        XCTAssertNil(null.whereToWatch)
        XCTAssertEqual(null.title, "Dune: Part Two")

        let absent = try decode(MediaDetail.self, Self.mediaDetailJSON(whereToWatch: nil))
        XCTAssertNil(absent.whereToWatch)
        XCTAssertEqual(absent.title, "Dune: Part Two")
    }

    func testUnreadableWhereToWatchHidesOnlyThatSection() throws {
        let detail = try decode(MediaDetail.self, Self.mediaDetailJSON(whereToWatch: #"{ "region": 5, "providers": "none" }"#))
        XCTAssertNil(detail.whereToWatch)
        XCTAssertEqual(detail.title, "Dune: Part Two")
        XCTAssertEqual(detail.ref.mediaId, "693134")
    }

    func testDetailCopiesKeepWhereToWatch() throws {
        let detail = try decode(MediaDetail.self, Self.mediaDetailJSON(whereToWatch: Self.whereToWatchJSON))
        let expected = try XCTUnwrap(detail.whereToWatch)
        let copies = [
            detail.replacingPoster(with: PosterSaveResponse(posterUrl: "https://example.com/p.jpg", customPosterUrl: nil, posterAccentColor: nil)),
            detail.replacingBackdrop(with: BackdropSaveResponse(backdropUrl: "https://example.com/b.jpg", customBackdropUrl: nil)),
            detail.replacingLogo(with: LogoSaveResponse(
                logoUrl: "https://example.com/l.png",
                customLogoUrl: nil,
                logoWidth: nil,
                logoHeight: nil,
                logoAspectRatio: nil
            )),
            detail.replacingHasLiked(true),
            detail.replacingIsTracked(true),
            detail.replacingExternalRatings(with: MediaExternalRatingsResponse(
                externalRatings: [],
                externalRatingsPreparation: .ready
            )),
        ]
        for copy in copies {
            XCTAssertEqual(copy.whereToWatch, expected)
        }
    }

    // MARK: - Region header

    func testRegionHeaderValueIsOnlyACountryCode() {
        XCTAssertEqual(APIClient.regionHeaderValue(for: Locale(identifier: "en_US")), "US")
        XCTAssertEqual(APIClient.regionHeaderValue(for: Locale(identifier: "fr_FR")), "FR")
        XCTAssertEqual(APIClient.regionHeaderValue(for: Locale(identifier: "zh_Hans_CN")), "CN")
        XCTAssertNil(APIClient.regionHeaderValue(for: Locale(identifier: "es_419")), "Latin America is not a country")
        XCTAssertNil(APIClient.regionHeaderValue(for: Locale(identifier: "en_001")), "The world is not a country")
    }

    func testRequestsCarryTheDeviceRegion() async throws {
        StreamingURLProtocol.responses["/api/v1/me/streaming/"] = Data(Self.preferencesJSON.utf8)
        _ = try await APIStreamingRepository(client: Self.client()).preferences()

        let request = try XCTUnwrap(StreamingURLProtocol.requests.last)
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Spine-Region"), APIClient.regionHeaderValue())
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Spine-Timezone"), TimeZone.current.identifier)
    }

    // MARK: - Repository

    func testRepositoryEndpoints() async throws {
        StreamingURLProtocol.responses = [
            "/api/v1/streaming/regions/": Data(#"{ "results": [{ "code": "GB", "name": "United Kingdom" }] }"#.utf8),
            "/api/v1/streaming/providers/": Data(#"""
            { "region": "GB", "results": [{ "provider_id": 38, "name": "BBC iPlayer", "logo_url": null, "display_priority": 2 }] }
            """#.utf8),
            "/api/v1/me/streaming/": Data(Self.preferencesJSON.utf8),
        ]
        let repository = APIStreamingRepository(client: Self.client())

        let regions = try await repository.regions()
        XCTAssertEqual(regions, [StreamingRegion(code: "GB", name: "United Kingdom")])
        XCTAssertEqual(StreamingURLProtocol.requests.last?.url?.absoluteString, "https://example.com/api/v1/streaming/regions/")

        let catalog = try await repository.providers(region: "GB")
        XCTAssertEqual(catalog.map(\.providerId), [38])
        XCTAssertEqual(
            StreamingURLProtocol.requests.last?.url?.absoluteString,
            "https://example.com/api/v1/streaming/providers/?region=GB"
        )

        _ = try await repository.providers(region: nil)
        XCTAssertEqual(StreamingURLProtocol.requests.last?.url?.absoluteString, "https://example.com/api/v1/streaming/providers/")

        let preferences = try await repository.preferences()
        XCTAssertEqual(preferences.providerIds, [8, 337])
        XCTAssertEqual(StreamingURLProtocol.requests.last?.httpMethod, "GET")
        XCTAssertEqual(StreamingURLProtocol.requests.last?.url?.absoluteString, "https://example.com/api/v1/me/streaming/")

        let saved = try await repository.savePreferences(StreamingPreferencesUpdateRequest(region: "US", providerIds: [8, 337]))
        XCTAssertEqual(saved.region, "US")
        let put = try XCTUnwrap(StreamingURLProtocol.requests.last)
        XCTAssertEqual(put.httpMethod, "PUT")
        XCTAssertEqual(put.url?.absoluteString, "https://example.com/api/v1/me/streaming/")
        let body = try XCTUnwrap(StreamingURLProtocol.bodies.last)
        XCTAssertEqual(body["region"] as? String, "US")
        XCTAssertEqual(body["provider_ids"] as? [Int], [8, 337])
    }

    // MARK: - Helpers

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private static func client() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StreamingURLProtocol.self]
        return APIClient(baseURL: URL(string: "https://example.com")!, session: URLSession(configuration: configuration))
    }

    static let preferencesJSON = #"""
    {
      "region": "US",
      "region_name": "United States",
      "region_is_set": true,
      "enabled": true,
      "provider_ids": [8, 337],
      "providers": [
        { "provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/abc.png", "display_priority": 1 },
        { "provider_id": 337, "name": "Disney Plus", "logo_url": "https://image.tmdb.org/t/p/w154/def.png", "display_priority": 3 }
      ],
      "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" }
    }
    """#

    static let whereToWatchJSON = #"""
    {
      "region": "US",
      "region_name": "United States",
      "link": "https://www.themoviedb.org/movie/693134-dune-part-two/watch?locale=US",
      "providers": [
        { "provider_id": 73, "name": "Tubi TV", "logo_url": "https://image.tmdb.org/t/p/w154/tubi.png", "display_priority": 20, "is_mine": true },
        { "provider_id": 1899, "name": "HBO Max", "logo_url": "https://image.tmdb.org/t/p/w154/max.png", "display_priority": 151, "is_mine": false }
      ],
      "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" }
    }
    """#

    static func mediaDetailJSON(whereToWatch: String?) -> String {
        let field = whereToWatch.map { #", "where_to_watch": \#($0)"# } ?? ""
        return #"""
        { "ref": { "source": "tmdb", "media_type": "movie", "media_id": "693134" },
          "title": "Dune: Part Two",
          "external_links": {},
          "providers": { "US": { "flatrate": [] } }\#(field) }
        """#
    }
}

@MainActor
final class StreamingServicesViewModelTests: XCTestCase {
    func testLoadPreselectsTheEffectiveCountryAndLoadsItsCatalog() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", isSet: false, ids: [8]))
        let viewModel = makeViewModel(repository)

        await viewModel.load()

        XCTAssertEqual(viewModel.selectedRegion, "US")
        XCTAssertEqual(viewModel.selectedRegionName, "United States")
        XCTAssertEqual(viewModel.selectedProviderIds, [8])
        XCTAssertEqual(viewModel.catalog.map(\.providerId), [8, 15, 337])
        XCTAssertTrue(viewModel.isCatalogLoaded)
        XCTAssertEqual(viewModel.regions.map(\.code), ["GB", "US"])
        XCTAssertEqual(repository.providerRequests, ["US"])
        XCTAssertFalse(viewModel.hasChanges, "Keeping the device country is not a change")
        XCTAssertFalse(viewModel.canSave)
        XCTAssertNil(viewModel.loadErrorMessage)
        XCTAssertFalse(viewModel.isLoading)
    }

    func testTurnedOffAvailabilityStartsFromTheDeviceCountry() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: nil, enabled: false, ids: []))
        let viewModel = makeViewModel(repository, deviceRegion: "GB")

        await viewModel.load()

        XCTAssertEqual(viewModel.selectedRegion, "GB")
        XCTAssertEqual(repository.providerRequests, ["GB"])
        XCTAssertTrue(viewModel.hasChanges, "Saving turns availability back on")
        XCTAssertTrue(viewModel.canSave)
    }

    func testTurnedOffAvailabilityWithoutADeviceCountryUsesTheDefault() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: nil, enabled: false, ids: []))
        let viewModel = makeViewModel(repository, deviceRegion: nil)

        await viewModel.load()

        XCTAssertEqual(viewModel.selectedRegion, "US")
    }

    func testLoadFailureShowsAnErrorAndRetryRecovers() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8]))
        repository.preferencesError = APIError.httpStatus(500, nil)
        let viewModel = makeViewModel(repository)

        await viewModel.load()
        XCTAssertNil(viewModel.saved)
        XCTAssertNotNil(viewModel.loadErrorMessage)
        XCTAssertTrue(repository.providerRequests.isEmpty)

        repository.preferencesError = nil
        await viewModel.load()
        XCTAssertNotNil(viewModel.saved)
        XCTAssertNil(viewModel.loadErrorMessage)
        XCTAssertEqual(viewModel.selectedProviderIds, [8])
    }

    func testUnauthorizedLoadEndsTheSession() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: []))
        repository.preferencesError = APIError.unauthorized
        var unauthorizedCount = 0
        let viewModel = StreamingServicesViewModel(
            repository: repository,
            onUnauthorized: { unauthorizedCount += 1 },
            deviceRegion: "US"
        )

        await viewModel.load()

        XCTAssertEqual(unauthorizedCount, 1)
    }

    func testCatalogFailureShowsAnErrorAndRetryRecovers() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8]))
        repository.catalogError = APIError.httpStatus(503, nil)
        let viewModel = makeViewModel(repository)

        await viewModel.load()
        XCTAssertNotNil(viewModel.catalogErrorMessage)
        XCTAssertFalse(viewModel.isCatalogLoaded)
        XCTAssertEqual(viewModel.selectedProviders.map(\.name), ["Netflix"], "Saved services still show")

        repository.catalogError = nil
        await viewModel.loadIfNeeded()
        XCTAssertNil(viewModel.catalogErrorMessage)
        XCTAssertTrue(viewModel.isCatalogLoaded)
    }

    func testToggleAndChangeDetection() async throws {
        let viewModel = makeViewModel(StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8])))
        await viewModel.load()
        let catalog = viewModel.catalog
        let hulu = try XCTUnwrap(catalog.first { $0.providerId == 15 })
        let netflix = try XCTUnwrap(catalog.first { $0.providerId == 8 })

        viewModel.toggle(hulu)
        XCTAssertTrue(viewModel.isSelected(hulu))
        XCTAssertEqual(viewModel.selectedProviderIds, [8, 15])
        XCTAssertTrue(viewModel.hasChanges)

        viewModel.toggle(hulu)
        XCTAssertFalse(viewModel.isSelected(hulu))
        XCTAssertFalse(viewModel.hasChanges, "Undoing a change leaves nothing to save")

        viewModel.toggle(netflix)
        XCTAssertTrue(viewModel.selectedProviderIds.isEmpty)
        XCTAssertTrue(viewModel.hasChanges)
        XCTAssertTrue(viewModel.selectedProviders.isEmpty)
    }

    func testSelectedServicesFollowCatalogOrder() async throws {
        let viewModel = makeViewModel(StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [337, 8])))
        await viewModel.load()

        XCTAssertEqual(viewModel.selectedProviders.map(\.name), ["Netflix", "Disney Plus"])
    }

    func testChangingCountryKeepsSelectionsAndShowsServicesItDoesNotList() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8, 15]))
        let viewModel = makeViewModel(repository)
        await viewModel.load()

        await viewModel.selectRegion("GB")

        XCTAssertEqual(repository.providerRequests, ["US", "GB"])
        XCTAssertEqual(viewModel.selectedRegion, "GB")
        XCTAssertEqual(viewModel.selectedRegionName, "United Kingdom")
        XCTAssertEqual(viewModel.selectedProviderIds, [8, 15], "Changing country keeps selections")
        XCTAssertEqual(viewModel.catalog.map(\.name), ["Netflix", "BBC iPlayer"])
        XCTAssertEqual(viewModel.selectedProvidersMissingFromCatalog.map(\.name), ["Hulu"])
        XCTAssertEqual(viewModel.services(matching: "").map(\.name), ["Hulu", "Netflix", "BBC iPlayer"])
        XCTAssertEqual(viewModel.selectedProviders.map(\.name), ["Netflix", "Hulu"])
        XCTAssertTrue(viewModel.hasChanges)

        // Hulu can still be removed from the new country.
        viewModel.toggle(try XCTUnwrap(viewModel.selectedProvidersMissingFromCatalog.first))
        XCTAssertEqual(viewModel.selectedProviderIds, [8])
        XCTAssertTrue(viewModel.selectedProvidersMissingFromCatalog.isEmpty)

        await viewModel.selectRegion("US")
        viewModel.toggle(try XCTUnwrap(viewModel.catalog.first { $0.providerId == 15 }))
        XCTAssertFalse(viewModel.hasChanges, "Back to the saved country and services")
    }

    func testChangingCountryDropsUnsavedPicksTheNewCountryDoesNotList() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [337]))
        let viewModel = makeViewModel(repository)
        await viewModel.load()
        viewModel.toggle(try XCTUnwrap(viewModel.catalog.first { $0.providerId == 8 }))
        viewModel.toggle(try XCTUnwrap(viewModel.catalog.first { $0.providerId == 15 }))

        await viewModel.selectRegion("GB")

        // GB lists Netflix and Disney Plus is saved, so both stay. Saving an unsaved Hulu would fail: GB doesn't list it.
        XCTAssertEqual(viewModel.selectedProviderIds, [337, 8])
        XCTAssertEqual(viewModel.selectedProvidersMissingFromCatalog.map(\.name), ["Disney Plus"])

        let didSave = await viewModel.save()
        XCTAssertTrue(didSave)
        XCTAssertEqual(repository.savedRequests, [StreamingPreferencesUpdateRequest(region: "GB", providerIds: [337, 8])])
    }

    func testServiceSearchFiltersByName() async throws {
        let viewModel = makeViewModel(StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [])))
        await viewModel.load()

        XCTAssertEqual(viewModel.services(matching: "net").map(\.name), ["Netflix"])
        XCTAssertEqual(viewModel.services(matching: "DISNEY").map(\.name), ["Disney Plus"])
        XCTAssertEqual(viewModel.services(matching: "  ").count, 3)
        XCTAssertTrue(viewModel.services(matching: "zzz").isEmpty)
        XCTAssertEqual(viewModel.regions(matching: "king").map(\.code), ["GB"])
        XCTAssertEqual(viewModel.regions(matching: "us").map(\.code), ["US"])
    }

    func testSaveSendsCountryAndServicesThenAnnouncesTheChange() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8]))
        let viewModel = makeViewModel(repository)
        await viewModel.load()
        await viewModel.selectRegion("GB")
        viewModel.toggle(try XCTUnwrap(viewModel.catalog.first { $0.providerId == 38 }))

        let announced = expectation(forNotification: .streamingPreferencesDidChange, object: nil) { notification in
            let saved = notification.object as? StreamingPreferences
            return saved?.region == "GB" && saved?.providerIds == [8, 38]
        }
        let didSave = await viewModel.save()

        XCTAssertTrue(didSave)
        await fulfillment(of: [announced], timeout: 1)
        XCTAssertEqual(repository.savedRequests, [StreamingPreferencesUpdateRequest(region: "GB", providerIds: [8, 38])])
        XCTAssertEqual(viewModel.saved?.region, "GB")
        XCTAssertFalse(viewModel.hasChanges)
        XCTAssertFalse(viewModel.isSaving)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testSaveFailureKeepsTheDraftAndShowsWhy() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8]))
        repository.saveError = APIError.httpStatus(400, #"{"provider_ids": ["Choose services from this country's catalog."]}"#)
        let viewModel = makeViewModel(repository)
        await viewModel.load()
        viewModel.toggle(try XCTUnwrap(viewModel.catalog.first { $0.providerId == 15 }))

        let didSave = await viewModel.save()

        XCTAssertFalse(didSave)
        XCTAssertEqual(viewModel.errorMessage, "Choose services from this country's catalog.")
        XCTAssertEqual(viewModel.selectedProviderIds, [8, 15])
        XCTAssertEqual(viewModel.saved?.providerIds, [8])
        XCTAssertTrue(viewModel.hasChanges)
        XCTAssertFalse(viewModel.isSaving)

        repository.saveError = APIError.httpStatus(500, nil)
        _ = await viewModel.save()
        XCTAssertEqual(viewModel.errorMessage, APIError.httpStatus(500, nil).localizedDescription)
    }

    func testUnauthorizedSaveEndsTheSession() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: []))
        repository.saveError = APIError.unauthorized
        var unauthorizedCount = 0
        let viewModel = StreamingServicesViewModel(
            repository: repository,
            onUnauthorized: { unauthorizedCount += 1 },
            deviceRegion: "US"
        )
        await viewModel.load()
        viewModel.toggle(try XCTUnwrap(viewModel.catalog.first))

        let didSave = await viewModel.save()

        XCTAssertFalse(didSave)
        XCTAssertEqual(unauthorizedCount, 1)
    }

    func testSaveWithoutChangesSendsNothing() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8]))
        let viewModel = makeViewModel(repository)
        await viewModel.load()

        let didSave = await viewModel.save()

        XCTAssertFalse(didSave)
        XCTAssertTrue(repository.savedRequests.isEmpty)
    }

    func testSelectionsStopAtTheLimit() async throws {
        let ids = Array(1 ... StreamingServicesViewModel.maxServices)
        let viewModel = makeViewModel(StreamingRepositoryFake(preferences: try preferences(region: "US", ids: ids)))
        await viewModel.load()

        viewModel.toggle(StreamingProvider(providerId: 9999, name: "One Too Many", logoUrl: nil, displayPriority: nil))

        XCTAssertEqual(viewModel.selectedProviderIds.count, StreamingServicesViewModel.maxServices)
        XCTAssertEqual(viewModel.errorMessage, "You can choose up to 100 services.")
    }

    func testAResponseForAnAbandonedCountryIsDropped() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: [8]))
        let viewModel = makeViewModel(repository)
        await viewModel.load()
        repository.holdsCatalogs = true

        let toGB = Task { await viewModel.selectRegion("GB") }
        await waitUntil { repository.isHolding("GB") }
        let toUS = Task { await viewModel.selectRegion("US") }
        await waitUntil { repository.isHolding("US") }

        repository.release("US")
        await toUS.value
        repository.release("GB")
        await toGB.value

        XCTAssertEqual(viewModel.selectedRegion, "US")
        XCTAssertEqual(viewModel.catalog.map(\.providerId), [8, 15, 337])
        XCTAssertTrue(viewModel.isCatalogLoaded)
        XCTAssertFalse(viewModel.isLoadingCatalog)
    }

    func testCountryNameFallsBackToTheSavedName() async throws {
        let repository = StreamingRepositoryFake(preferences: try preferences(region: "US", ids: []))
        repository.regionsError = APIError.httpStatus(500, nil)
        let viewModel = makeViewModel(repository)
        await viewModel.load()

        XCTAssertNotNil(viewModel.regionsErrorMessage)
        XCTAssertEqual(viewModel.selectedRegionName, "United States")
        XCTAssertEqual(viewModel.regionName(for: "FR"), Locale.current.localizedString(forRegionCode: "FR") ?? "FR")
    }

    func testCountryNameSkipsASavedNameThatIsOnlyTheCode() async throws {
        let repository = StreamingRepositoryFake(preferences: StreamingPreferences(
            region: "DE",
            regionName: "DE",
            regionIsSet: true,
            enabled: true,
            providerIds: [],
            providers: [],
            attribution: nil
        ))
        repository.regionsError = APIError.httpStatus(500, nil)
        let viewModel = makeViewModel(repository)
        await viewModel.load()

        XCTAssertEqual(viewModel.selectedRegionName, Locale.current.localizedString(forRegionCode: "DE") ?? "DE")
    }

    // MARK: - Helpers

    private func makeViewModel(_ repository: StreamingRepositoryFake, deviceRegion: String? = "US") -> StreamingServicesViewModel {
        StreamingServicesViewModel(repository: repository, onUnauthorized: {}, deviceRegion: deviceRegion)
    }

    private func preferences(region: String?, isSet: Bool = true, enabled: Bool = true, ids: [Int]) throws -> StreamingPreferences {
        let names = [8: "Netflix", 15: "Hulu", 337: "Disney Plus", 38: "BBC iPlayer"]
        let providers = ids.map { id in
            #"{ "provider_id": \#(id), "name": "\#(names[id] ?? "Service \(id)")", "logo_url": null, "display_priority": \#(id) }"#
        }
        let regionValue = region.map { "\"\($0)\"" } ?? "null"
        let regionName = region == "US" ? "\"United States\"" : region == "GB" ? "\"United Kingdom\"" : "null"
        let json = #"""
        { "region": \#(regionValue), "region_name": \#(regionName), "region_is_set": \#(isSet && region != nil),
          "enabled": \#(enabled), "provider_ids": [\#(ids.map(String.init).joined(separator: ","))],
          "providers": [\#(providers.joined(separator: ","))],
          "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" } }
        """#
        return try JSONDecoder.api.decode(StreamingPreferences.self, from: Data(json.utf8))
    }

    private func waitUntil(timeout: TimeInterval = 2, _ condition: () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            guard Date() < deadline else { return XCTFail("Timed out") }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }
}

@MainActor
private final class StreamingRepositoryFake: StreamingRepository {
    var preferencesResponse: StreamingPreferences
    var preferencesError: Error?
    var regionsError: Error?
    var catalogError: Error?
    var saveError: Error?
    var holdsCatalogs = false
    private(set) var providerRequests: [String?] = []
    private(set) var savedRequests: [StreamingPreferencesUpdateRequest] = []
    private var heldCatalogs: [String: CheckedContinuation<[StreamingProvider], Error>] = [:]

    static let catalogs: [String: [StreamingProvider]] = [
        "US": [
            StreamingProvider(providerId: 8, name: "Netflix", logoUrl: nil, displayPriority: 1),
            StreamingProvider(providerId: 15, name: "Hulu", logoUrl: nil, displayPriority: 2),
            StreamingProvider(providerId: 337, name: "Disney Plus", logoUrl: nil, displayPriority: 3),
        ],
        "GB": [
            StreamingProvider(providerId: 8, name: "Netflix", logoUrl: nil, displayPriority: 1),
            StreamingProvider(providerId: 38, name: "BBC iPlayer", logoUrl: nil, displayPriority: 2),
        ],
    ]

    init(preferences: StreamingPreferences) {
        preferencesResponse = preferences
    }

    func regions() async throws -> [StreamingRegion] {
        if let regionsError { throw regionsError }
        return [StreamingRegion(code: "GB", name: "United Kingdom"), StreamingRegion(code: "US", name: "United States")]
    }

    func providers(region: String?) async throws -> [StreamingProvider] {
        providerRequests.append(region)
        let key = region ?? ""
        if holdsCatalogs {
            return try await withCheckedThrowingContinuation { continuation in
                heldCatalogs[key] = continuation
            }
        }
        if let catalogError { throw catalogError }
        return Self.catalogs[key] ?? []
    }

    func preferences() async throws -> StreamingPreferences {
        if let preferencesError { throw preferencesError }
        return preferencesResponse
    }

    func savePreferences(_ request: StreamingPreferencesUpdateRequest) async throws -> StreamingPreferences {
        savedRequests.append(request)
        if let saveError { throw saveError }
        let known = Self.catalogs.values.flatMap { $0 }
        let providers = request.providerIds.compactMap { id in known.first { $0.providerId == id } }
        preferencesResponse = StreamingPreferences(
            region: request.region,
            regionName: nil,
            regionIsSet: true,
            enabled: true,
            providerIds: request.providerIds,
            providers: providers,
            attribution: .justWatch
        )
        return preferencesResponse
    }

    func isHolding(_ region: String) -> Bool {
        heldCatalogs[region] != nil
    }

    func release(_ region: String) {
        heldCatalogs.removeValue(forKey: region)?.resume(returning: Self.catalogs[region] ?? [])
    }
}

private final class StreamingURLProtocol: URLProtocol {
    nonisolated(unsafe) static var requests: [URLRequest] = []
    nonisolated(unsafe) static var bodies: [[String: Any]] = []
    nonisolated(unsafe) static var responses: [String: Data] = [:]

    static func reset() {
        requests = []
        bodies = []
        responses = [:]
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
            body = data
        }
        if let body, let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
            Self.bodies.append(json)
        }
        let path = request.url?.path(percentEncoded: false) ?? ""
        let data = Self.responses[path] ?? Self.responses[path + "/"] ?? Data("{}".utf8)
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: 200,
            httpVersion: nil,
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
