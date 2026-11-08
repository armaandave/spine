import XCTest
@testable import Spine

/// The site-wide Home picture: the JSON the server sends, and how it wins over (and gives back to) the daily pick.
@MainActor
final class HomeTopArtSiteTests: XCTestCase {
    private var defaults: UserDefaults!
    private var media: RailMediaRepository!
    private var calendar: Calendar!
    private var date = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "HomeTopArtSiteTests.\(UUID().uuidString)")
        media = RailMediaRepository()
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        self.calendar = calendar
    }

    // MARK: Fixtures

    private func ref(_ type: String, _ id: String) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: type, mediaId: id, seasonNumber: nil, episodeNumber: nil)
    }

    private func summary(_ type: String, _ id: String) -> MediaSummary {
        MediaSummary(ref: ref(type, id), title: "Title \(id)")
    }

    private func makeModel() -> HomeTopArtModel {
        HomeTopArtModel(mediaRepository: media, defaults: defaults, calendar: calendar, now: { self.date })
    }

    private func settle() async {
        try? await Task.sleep(for: .milliseconds(150))
    }

    private func site(
        id: String = "603", year: Int? = 1999, canChange: Bool = true
    ) -> HomePictureResponse {
        HomePictureResponse(
            picture: HomePicturePayload(
                ref: ref("movie", id), title: "The Matrix", year: year,
                backdropUrl: "https://example.com/site-\(id).jpg", setBy: "armaan", updatedAt: nil
            ),
            canChange: canChange
        )
    }

    private func serveDaily(_ id: String) {
        media.details[ref("movie", id).id] = MediaDetail(
            ref: ref("movie", id), title: "Detail \(id)", backdropUrl: "https://example.com/daily-\(id).jpg"
        )
    }

    // MARK: Decoding

    func testTheEnvelopeWithNoPictureDecodes() throws {
        let json = Data(#"{"picture": null, "can_change": false}"#.utf8)
        let response = try JSONDecoder.api.decode(HomePictureResponse.self, from: json)
        XCTAssertNil(response.picture)
        XCTAssertFalse(response.canChange)
    }

    func testThePictureDecodesFromTheServersSnakeCase() throws {
        let json = Data("""
        {"picture": {"ref": {"source": "tmdb", "media_type": "movie", "media_id": "438631"},
         "title": "Dune", "year": 2021, "backdrop_url": "https://example.com/b.jpg",
         "set_by": "armaan", "updated_at": "2026-11-08T12:00:00Z"}, "can_change": true}
        """.utf8)
        let response = try JSONDecoder.api.decode(HomePictureResponse.self, from: json)
        XCTAssertTrue(response.canChange)
        XCTAssertEqual(response.picture?.ref.mediaId, "438631")
        XCTAssertEqual(response.picture?.year, 2021)
        XCTAssertEqual(response.picture?.setBy, "armaan")
    }

    func testAPictureWithNoYearDecodes() throws {
        let json = Data("""
        {"picture": {"ref": {"source": "tmdb", "media_type": "game", "media_id": "1"},
         "title": "Hades", "year": null, "backdrop_url": "https://example.com/b.jpg",
         "set_by": null, "updated_at": "2026-11-08T12:00:00Z"}, "can_change": false}
        """.utf8)
        let response = try JSONDecoder.api.decode(HomePictureResponse.self, from: json)
        XCTAssertNil(response.picture?.year)
        XCTAssertEqual(response.picture?.topArt?.label, "Hades")
    }

    func testTheLabelIsTheTitleAndYearAndABadURLGivesNoPicture() {
        XCTAssertEqual(site().picture?.topArt?.label, "The Matrix · 1999")
        var bad = site().picture
        bad?.backdropUrl = ""
        XCTAssertNil(bad?.topArt)
    }

    // MARK: Winning over the daily pick

    func testTheSitePictureReplacesTheShownPictureAndTheDailyPickNeverTakesItBack() async {
        serveDaily("1")
        let model = makeModel()
        model.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertEqual(model.art?.backdrop.absoluteString, "https://example.com/daily-1.jpg")

        model.applySitePicture(site())
        XCTAssertTrue(model.hasSitePick)
        XCTAssertEqual(model.art?.backdrop.absoluteString, "https://example.com/site-603.jpg")

        serveDaily("2")
        model.update(inProgress: [summary("movie", "2")], trending: [])
        await settle()
        XCTAssertEqual(model.art?.backdrop.absoluteString, "https://example.com/site-603.jpg")
    }

    func testTheSitePictureTellsTheGlowAboutIt() {
        let model = makeModel()
        var told: [HomeTopArt?] = []
        model.onChange = { told.append($0) }
        model.applySitePicture(site())
        XCTAssertEqual(told.count, 1)
        model.applySitePicture(site())
        XCTAssertEqual(told.count, 1, "the same picture again changes nothing")
    }

    func testTheSitePictureIsThereOnTheFirstFrameOfTheNextLaunch() {
        makeModel().applySitePicture(site())
        let fresh = makeModel()
        XCTAssertTrue(fresh.hasSitePick)
        XCTAssertEqual(fresh.art?.title, "The Matrix")
        XCTAssertTrue(fresh.canChangeSitePicture)
    }

    func testAFreshLaunchWithTheSitePictureStillIgnoresTheDailyPick() async {
        makeModel().applySitePicture(site())
        serveDaily("1")
        let fresh = makeModel()
        fresh.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertEqual(fresh.art?.backdrop.absoluteString, "https://example.com/site-603.jpg")
    }

    // MARK: Falling back

    func testClearingTheSitePictureLetsTheDailyPickRunAgain() async {
        serveDaily("1")
        let model = makeModel()
        model.applySitePicture(site())
        model.update(inProgress: [summary("movie", "1")], trending: [])
        model.applySitePicture(HomePictureResponse(picture: nil, canChange: true))
        XCTAssertFalse(model.hasSitePick)
        await settle()
        XCTAssertEqual(model.art?.backdrop.absoluteString, "https://example.com/daily-1.jpg")
    }

    func testClearingWhenThereWasNoSitePictureLeavesTheDailyArtAlone() async {
        serveDaily("1")
        let model = makeModel()
        model.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        let shown = model.art
        model.applySitePicture(HomePictureResponse(picture: nil, canChange: false))
        XCTAssertEqual(model.art, shown)
        XCTAssertFalse(model.hasSitePick)
    }

    func testAClearedSitePictureStaysClearedOnTheNextLaunch() {
        let model = makeModel()
        model.applySitePicture(site())
        model.applySitePicture(HomePictureResponse(picture: nil, canChange: true))
        let fresh = makeModel()
        XCTAssertFalse(fresh.hasSitePick)
        XCTAssertNil(fresh.art)
    }

    func testAFailedReadKeepsWhatWasShown() async {
        let model = makeModel()
        model.applySitePicture(site())
        media.homePictureAnswer = nil
        await model.loadSitePicture(using: media)
        XCTAssertTrue(model.hasSitePick)
        XCTAssertEqual(model.art?.title, "The Matrix")
    }

    func testAFailedReadWithNoSitePictureLeavesTheDailyRotation() async {
        serveDaily("1")
        let model = makeModel()
        media.homePictureAnswer = nil
        await model.loadSitePicture(using: media)
        model.update(inProgress: [summary("movie", "1")], trending: [])
        await settle()
        XCTAssertFalse(model.hasSitePick)
        XCTAssertEqual(model.art?.backdrop.absoluteString, "https://example.com/daily-1.jpg")
    }

    func testTheReadAppliesTheAnswer() async {
        let model = makeModel()
        media.homePictureAnswer = site()
        await model.loadSitePicture(using: media)
        XCTAssertEqual(model.art?.title, "The Matrix")
    }

    // MARK: Who may change it

    func testOnlyAPermittedUserGetsTheLongPress() {
        let model = makeModel()
        XCTAssertFalse(model.canChangeSitePicture)
        model.applySitePicture(site(canChange: false))
        XCTAssertFalse(model.canChangeSitePicture)
        model.applySitePicture(site(canChange: true))
        XCTAssertTrue(model.canChangeSitePicture)
    }

    func testThePermissionSurvivesALaunchAndGatesNothingElse() {
        makeModel().applySitePicture(HomePictureResponse(picture: nil, canChange: true))
        let fresh = makeModel()
        XCTAssertTrue(fresh.canChangeSitePicture)
        XCTAssertFalse(fresh.hasSitePick)
        XCTAssertNil(fresh.art)
    }

    func testTheLongPressRegionStopsAboveTheHeaderRow() {
        XCTAssertEqual(HomeTopArtSpec.longPressHeight(width: 390, topInset: 59), 232 - 59)
        XCTAssertEqual(HomeTopArtSpec.longPressHeight(width: 390, topInset: 1000), 0)
    }
}
