import SwiftUI
import XCTest
@testable import Spine

/// Which tile is open, which title it is on, and what the model says each tile shows.
@MainActor
final class HomeTilesStateTests: XCTestCase {
    // MARK: Fixtures

    private func ref(_ mediaType: String, _ id: String) -> MediaRef {
        MediaRef(itemId: nil, source: "tmdb", mediaType: mediaType, mediaId: "tiles-\(id)",
                 seasonNumber: nil, episodeNumber: nil)
    }

    private func media(_ id: String, type: String = "movie") -> MediaSummary {
        MediaSummary(
            ref: ref(type, id), title: "Title \(id)", posterUrl: "https://example.com/\(id).jpg",
            posterOrientation: .portrait
        )
    }

    private func item(_ id: String, type: String = "movie") -> Spine.LibraryItem {
        Spine.LibraryItem(
            media: media(id, type: type),
            tracking: TrackingState(
                trackingId: 1, status: "In progress", rating: nil, progress: nil, repeats: nil, startDate: nil,
                endDate: nil, notes: nil, updatedAt: nil
            )
        )
    }

    private func title(_ id: String, people: [UserSummary] = []) -> TrendingTitle {
        let media = media(id)
        return TrendingTitle(
            key: FeedEntries.titleKey(for: media.ref), media: media, people: people, includesYou: false,
            averageRating: nil, lastDay: nil
        )
    }

    private func id(_ name: String, type: String = "movie") -> String {
        media(name, type: type).id
    }

    /// A state with the In progress tile showing `names`.
    private func state(_ names: [String]) -> HomeTilesState {
        let state = HomeTilesState()
        state.sync(names.map { id($0) }, for: .inProgress)
        return state
    }

    // MARK: Opening and closing

    func testStartsClosed() {
        let state = HomeTilesState()
        XCTAssertNil(state.open)
        XCTAssertNil(state.mounted)
        XCTAssertNil(state.focusID)
        XCTAssertNil(state.focusIndex)
    }

    func testOpeningFocusesTheFirstTitleAndMountsEverything() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        XCTAssertEqual(state.open, .inProgress)
        XCTAssertTrue(state.isOpen(.inProgress))
        XCTAssertFalse(state.isOpen(.trending))
        XCTAssertEqual(state.mounted, .inProgress)
        XCTAssertEqual(state.focusID, id("a"))
        XCTAssertEqual(state.focusIndex, 0)
    }

    func testMountingComesBeforeOpening() {
        let state = state(["a", "b", "c", "d"])
        state.mount(.inProgress)
        XCTAssertEqual(state.mounted, .inProgress)
        XCTAssertNil(state.open, "the posters mount first and fly a frame later")
        state.openTile(.inProgress)
        XCTAssertEqual(state.open, .inProgress)
    }

    func testClosingKeepsThePostersMountedUntilTheFlightHomeEnds() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        state.focus(id("c"))

        state.close()
        XCTAssertNil(state.open)
        XCTAssertEqual(state.mounted, .inProgress, "they are still flying into the fan")
        XCTAssertEqual(state.focusID, id("c"), "the panel is still on screen as it leaves")

        state.finishClosing()
        XCTAssertNil(state.mounted)
        XCTAssertNil(state.focusID)
    }

    func testAnOpenDuringTheFlightHomeIsNotUndoneByItsEnd() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        state.close()
        state.openTile(.inProgress)
        state.finishClosing()
        XCTAssertEqual(state.open, .inProgress)
        XCTAssertEqual(state.mounted, .inProgress)
        XCTAssertEqual(state.focusID, id("a"))
    }

    func testReopeningStartsOnTheFirstTitleAgain() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        state.focus(id("b"))
        state.close()
        state.finishClosing()
        state.openTile(.inProgress)
        XCTAssertEqual(state.focusID, id("a"))
    }

    func testATileWithNoTitlesDoesNotOpen() {
        let state = HomeTilesState()
        state.openTile(.inProgress)
        XCTAssertNil(state.open)
        state.sync([], for: .trending)
        state.openTile(.trending)
        XCTAssertNil(state.open)
        XCTAssertNil(state.mounted)
    }

    func testOnlyOneTileOpensAtATime() {
        let state = state(["a", "b"])
        state.sync([id("x"), id("y")], for: .trending)
        state.openTile(.inProgress)
        state.openTile(.trending)
        XCTAssertEqual(state.open, .inProgress)
        state.mount(.trending)
        XCTAssertEqual(state.mounted, .inProgress)
    }

    func testARequestToOpenAFanSizedTileOpensAtOnce() {
        let state = state(["a", "b", "c"])
        state.requestOpen(.inProgress, posterCount: 3, animation: .default)
        XCTAssertEqual(state.open, .inProgress)
        XCTAssertEqual(state.focusID, id("a"))
    }

    func testARequestToOpenALongerTileMountsFirstAndOpensAFrameLater() async throws {
        let state = state(["a", "b", "c", "d", "e"])
        state.requestOpen(.inProgress, posterCount: 5, animation: .default)
        XCTAssertNil(state.open)
        XCTAssertEqual(state.mounted, .inProgress, "every poster is in the fan before any flies")
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(state.open, .inProgress)
    }

    func testAFlightHomeThatEndsInTheMountLeadDoesNotLoseTheOpen() async throws {
        let state = state(["a", "b", "c", "d", "e"])
        state.requestOpen(.inProgress, posterCount: 5, animation: .default)
        XCTAssertEqual(state.mounted, .inProgress)
        // The section's task for an earlier close ends the flight home just now.
        state.finishClosing()
        XCTAssertNil(state.mounted)
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(state.open, .inProgress, "the tapped tile still opens")
        XCTAssertEqual(state.mounted, .inProgress)
        XCTAssertEqual(state.focusID, id("a"))
    }

    func testReduceMotionOpensAtOnceWhateverTheLength() {
        let state = state(["a", "b", "c", "d", "e"])
        state.requestOpen(.inProgress, posterCount: 5, animation: .default, defersMount: false)
        XCTAssertEqual(state.open, .inProgress)
    }

    func testARequestToOpenWhileOpenChangesNothing() {
        let state = state(["a", "b", "c"])
        state.sync([id("x"), id("y")], for: .trending)
        state.requestOpen(.inProgress, posterCount: 3, animation: .default)
        state.requestOpen(.trending, posterCount: 2, animation: .default)
        XCTAssertEqual(state.open, .inProgress)
    }

    func testARequestToCloseClosesAndKeepsThePostersMounted() {
        let state = state(["a", "b", "c"])
        state.requestOpen(.inProgress, posterCount: 3, animation: .default)
        state.requestClose(animation: .default)
        XCTAssertNil(state.open)
        XCTAssertEqual(state.mounted, .inProgress)
        state.requestClose(animation: .default)
        XCTAssertNil(state.open)
    }

    // MARK: Focus by id

    func testFocusMovesByItemID() {
        let state = state(["a", "b", "c", "d"])
        state.openTile(.inProgress)
        XCTAssertTrue(state.focus(id("c")))
        XCTAssertEqual(state.focusID, id("c"))
        XCTAssertEqual(state.focusIndex, 2)
        XCTAssertTrue(state.focus(id("a")))
        XCTAssertEqual(state.focusIndex, 0)
    }

    func testFocusIgnoresAnIDTheTileDoesNotHold() {
        let state = state(["a", "b"])
        state.openTile(.inProgress)
        XCTAssertFalse(state.focus("nobody"))
        XCTAssertEqual(state.focusID, id("a"))
    }

    func testFocusingTheFocusedTitleIsNotAChange() {
        let state = state(["a", "b"])
        state.openTile(.inProgress)
        XCTAssertFalse(state.focus(id("a")))
    }

    func testAScrollThatEndsAfterClosingDoesNotMoveTheFocus() {
        let state = state(["a", "b", "c"])
        state.close()
        XCTAssertFalse(state.focus(id("c")), "nothing is open")
        XCTAssertNil(state.focusID)
    }

    // MARK: Reloads under an open tile

    func testAReloadInAnotherOrderKeepsTheFocusOnItsTitle() {
        let state = state(["a", "b", "c", "d"])
        state.openTile(.inProgress)
        state.focus(id("c"))
        state.sync([id("d"), id("c"), id("b"), id("a")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("c"))
        XCTAssertEqual(state.focusIndex, 1, "the index follows the title")
        XCTAssertEqual(state.open, .inProgress)
    }

    func testAReloadThatKeepsEverythingChangesNothing() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        state.focus(id("b"))
        state.sync([id("a"), id("b"), id("c")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("b"))
    }

    func testTheFocusedTitleGoingMovesFocusToTheOneAfterIt() {
        let state = state(["a", "b", "c", "d"])
        state.openTile(.inProgress)
        state.focus(id("c"))
        state.sync([id("a"), id("b"), id("d")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("d"))
        XCTAssertEqual(state.focusIndex, 2)
    }

    func testTheLastTitleGoingMovesFocusToTheOneBeforeIt() {
        let state = state(["a", "b", "c", "d"])
        state.openTile(.inProgress)
        state.focus(id("d"))
        state.sync([id("a"), id("b"), id("c")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("c"))
    }

    func testTheFirstTitleGoingMovesFocusToTheNext() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        state.sync([id("b"), id("c")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("b"))
        XCTAssertEqual(state.focusIndex, 0)
    }

    func testNeighborsThatWentTooMoveFocusToTheNearestSurvivor() {
        let state = state(["a", "b", "c", "d", "e"])
        state.openTile(.inProgress)
        state.focus(id("c"))
        // c, d and b are all gone; e (two after) is nearer than a (two before) because the one after wins a tie.
        state.sync([id("a"), id("e")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("e"))
    }

    func testAnUnrelatedNewListLandsOnTheSamePlace() {
        let state = state(["a", "b", "c"])
        state.openTile(.inProgress)
        state.focus(id("c"))
        state.sync([id("x"), id("y")], for: .inProgress)
        XCTAssertEqual(state.focusID, id("y"), "the end of the new list, nearest where it was")
    }

    func testAnEmptiedListCollapsesTheTile() {
        let state = state(["a", "b"])
        state.openTile(.inProgress)
        state.sync([], for: .inProgress)
        XCTAssertNil(state.open)
        XCTAssertNil(state.focusID)
        XCTAssertNil(state.mounted)
        XCTAssertNil(state.focusIndex)
    }

    func testAReloadOfTheOtherTileLeavesTheOpenOneAlone() {
        let state = state(["a", "b", "c"])
        state.sync([id("x")], for: .trending)
        state.openTile(.inProgress)
        state.focus(id("b"))
        state.sync([], for: .trending)
        XCTAssertEqual(state.open, .inProgress)
        XCTAssertEqual(state.focusID, id("b"))
    }

    func testAReloadWhileClosedOnlyRemembersTheIDs() {
        let state = state(["a", "b"])
        state.sync([id("b"), id("a")], for: .inProgress)
        XCTAssertNil(state.focusID)
        state.openTile(.inProgress)
        XCTAssertEqual(state.focusID, id("b"), "the first of the new order")
    }

    func testNearestHelper() {
        let old = ["a", "b", "c", "d"]
        XCTAssertEqual(HomeTilesState.nearest(to: "b", in: old, surviving: ["a", "c", "d"]), "c")
        XCTAssertEqual(HomeTilesState.nearest(to: "d", in: old, surviving: ["a", "b", "c"]), "c")
        XCTAssertEqual(HomeTilesState.nearest(to: "a", in: old, surviving: ["c", "d"]), "c")
        XCTAssertNil(HomeTilesState.nearest(to: "a", in: old, surviving: []))
        XCTAssertEqual(HomeTilesState.nearest(to: "z", in: old, surviving: ["q", "r"]), "q", "never in the old list")
    }

    // MARK: Glow

    func testTheGlowFollowsTheFocusedPoster() {
        let model = HomeTilesModel()
        model.inProgress = [item("a"), item("b"), item("c")]
        let state = HomeTilesState()
        state.sync(model.ids(for: .inProgress), for: .inProgress)
        let glow = HomeGlowModel()

        state.syncGlow(glow, model: model)
        XCTAssertNil(glow.focused, "both closed: the glow shows its own picture")

        state.openTile(.inProgress)
        state.syncGlow(glow, model: model)
        XCTAssertEqual(glow.focused, media("a"))

        state.focus(id("c"))
        state.syncGlow(glow, model: model)
        XCTAssertEqual(glow.focused, media("c"))
        XCTAssertEqual(glow.active, media("c"))

        state.close()
        state.syncGlow(glow, model: model)
        XCTAssertNil(glow.focused, "closing gives the glow back at once")
    }

    func testTheGlowFollowsTheTrendingTile() {
        let model = HomeTilesModel()
        model.inProgress = [item("a")]
        model.trending = [title("x"), title("y")]
        let state = HomeTilesState()
        state.sync(model.ids(for: .inProgress), for: .inProgress)
        state.sync(model.ids(for: .trending), for: .trending)
        let glow = HomeGlowModel()
        glow.idle = media("a")

        state.openTile(.trending)
        state.syncGlow(glow, model: model)
        XCTAssertEqual(glow.active, media("x"))
        state.focus(model.trending[1].id)
        state.syncGlow(glow, model: model)
        XCTAssertEqual(glow.active, media("y"))
        state.close()
        state.syncGlow(glow, model: model)
        XCTAssertEqual(glow.active, media("a"))
    }

    // MARK: Source

    private static let tileFiles = [
        "HomeTilesSection.swift", "HomeTilesState.swift", "HomeTilesModel.swift", "TileGeometry.swift",
        "TileCarousel.swift", "TileDetailPanel.swift", "HomeTilesSkeleton.swift",
    ]

    private func tileSource(_ name: String) throws -> String {
        let tiles = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Spine/Features/Home/Tiles")
        return try String(contentsOf: tiles.appendingPathComponent(name), encoding: .utf8)
    }

    func testTheTilesApplyNothingLiveAndUseNoGeometryReader() throws {
        for name in Self.tileFiles {
            // The app's own image view is fine; the system's is not.
            let source = try tileSource(name).replacingOccurrences(of: "SpineAsyncImage", with: "")
            XCTAssertFalse(source.contains(".blur("), name)
            XCTAssertFalse(source.contains("Material"), name)
            XCTAssertFalse(source.contains("GeometryReader"), name)
            XCTAssertFalse(source.contains("ViewThatFits"), name)
            XCTAssertFalse(source.contains("AsyncImage"), name)
            XCTAssertFalse(source.contains("compositingGroup"), name)
            XCTAssertFalse(source.contains("drawingGroup"), name)
        }
    }

    func testNoScrollOffsetIsKeptInObservableState() throws {
        for name in ["HomeTilesState.swift", "HomeTilesModel.swift"] {
            let source = try tileSource(name)
            XCTAssertFalse(source.contains("scrollOffset"), name)
            XCTAssertFalse(source.contains("contentOffset"), name)
            XCTAssertFalse(source.contains("CGFloat"), "\(name) holds no measurements")
        }
    }

    func testTheSectionCountsItsBodyFirstAndNamesItsControls() throws {
        let section = try tileSource("HomeTilesSection.swift")
        let afterBody = try XCTUnwrap(section.range(of: "var body: some View {\n")).upperBound
        XCTAssertTrue(
            section[afterBody...].trimmingCharacters(in: .whitespaces).hasPrefix("let _ = PerfCounters.bump(.tilesBody)")
        )
        XCTAssertTrue(section.contains("\"home.tile.close\""))
        XCTAssertTrue(try tileSource("TileCarousel.swift").contains("\"home.tile.carousel\""))
        XCTAssertTrue(try tileSource("TileDetailPanel.swift").contains("\"in-progress.update\""))
        XCTAssertEqual(HomeTileID.inProgress.identifier, "home.tile.in-progress")
        XCTAssertEqual(HomeTileID.trending.identifier, "home.tile.trending")
        // The tile's close control says Collapse, so the progress sheet's Close stays the only Close.
        XCTAssertTrue(section.contains("\"Collapse \\(tile.title)\""))
        XCTAssertFalse(section.contains("accessibilityLabel(\"Close\")"))
    }

    /// `func tapTarget` through the end of its switch: the closed tile's buttons and the placeholders.
    private func tapTargetSource() throws -> String {
        let section = try tileSource("HomeTilesSection.swift")
        let start = try XCTUnwrap(section.range(of: "private func tapTarget(")).lowerBound
        let end = try XCTUnwrap(section.range(of: "private func openTitle(", range: start ..< section.endIndex)).lowerBound
        return String(section[start ..< end])
    }

    /// A tap on a closed tile did nothing when the Button's label was a clear shape fill with the content shape on
    /// the Button from outside: SwiftUI hit-tests the label. So every tile Button's label must carry its own content
    /// shape (`TileTapArea`), and nothing may put a clear fill back in its place.
    func testEveryTileButtonLabelCarriesItsOwnContentShape() throws {
        let tapTarget = try tapTargetSource()
        let buttons = tapTarget.components(separatedBy: "Button(action:").count - 1
        XCTAssertEqual(buttons, 2, "the open button and the offer button")
        XCTAssertEqual(tapTarget.components(separatedBy: "TileTapArea(shape: shape)").count - 1, buttons)
        XCTAssertFalse(tapTarget.contains("fill(Color.clear)"))
        XCTAssertFalse(tapTarget.contains(".contentShape("), "a content shape outside the Button never reaches its label")

        let section = try tileSource("HomeTilesSection.swift")
        let areaStart = try XCTUnwrap(section.range(of: "private struct TileTapArea")).lowerBound
        XCTAssertTrue(section[areaStart...].prefix(400).contains("Color.clear.contentShape(shape)"))
    }

    /// An explicit `accessibilityHidden(false)` above a part that hides itself un-hides it, so no tile-level modifier
    /// may pass one: each part hides itself (the carousel and the label until the tile opens, the closed tile's
    /// button while a tile is open, and the placeholders the same).
    func testNoTileLevelAccessibilityHiddenOverridesItsParts() throws {
        let section = try tileSource("HomeTilesSection.swift")
        XCTAssertFalse(section.contains("accessibilityHidden(!frame.hitTestable)"))
        XCTAssertFalse(section.contains("accessibilityHidden(false)"))
        XCTAssertEqual(try tileSource("TileCarousel.swift").components(separatedBy: "accessibilityHidden(!isOpen)").count - 1, 1)
        XCTAssertTrue(section.contains("accessibilityHidden(!isOpen)"), "the label hides itself until its tile opens")
        let tapTarget = try tapTargetSource()
        XCTAssertEqual(
            tapTarget.components(separatedBy: "accessibilityHidden(state.open != nil)").count - 1, 4,
            "the open button, the offer button and both placeholders hide while a tile is open"
        )
    }

    /// `.accessibilityAction(.escape)` on a plain view stretched the accessibility frame of the Button inside it to
    /// the tile's laid-out content (620 x 294 for a 179 pt tile), so an element tap on `home.tile.in-progress` landed
    /// on the Trending tile. On a `.contain` container the frames stay the tile's own.
    func testTheEscapeActionSitsOnAContainerSoTheTileButtonKeepsItsFrame() throws {
        let section = try tileSource("HomeTilesSection.swift")
        let escape = try XCTUnwrap(section.range(of: ".accessibilityAction(.escape)"))
        XCTAssertEqual(section.components(separatedBy: ".accessibilityAction(.escape)").count - 1, 1)
        let before = section[section.index(escape.lowerBound, offsetBy: -80, limitedBy: section.startIndex)! ..< escape.lowerBound]
        XCTAssertTrue(before.contains(".accessibilityElement(children: .contain)"))
    }

    // MARK: Model

    func testTilesAreCappedAtTenPosters() {
        let model = HomeTilesModel()
        model.inProgress = (0 ..< 12).map { item("p\($0)") }
        model.trending = (0 ..< 12).map { title("t\($0)") }
        XCTAssertEqual(model.inProgressPosters.count, 10)
        XCTAssertEqual(model.trendingPosters.count, 10)
        XCTAssertEqual(model.ids(for: .inProgress).first, id("p0"))
        XCTAssertEqual(model.posters(for: .trending).last?.media, media("t9"))
    }

    func testPostersLookUpByID() {
        let model = HomeTilesModel()
        model.inProgress = [item("a"), item("b")]
        model.trending = [title("x")]
        XCTAssertEqual(model.item(id: id("b"))?.media, media("b"))
        XCTAssertNil(model.item(id: "nobody"))
        XCTAssertEqual(model.media(for: .inProgress, id: id("a")), media("a"))
        XCTAssertNil(model.media(for: .inProgress, id: model.trending[0].id))
        XCTAssertEqual(model.trendingTitle(id: model.trending[0].id)?.media, media("x"))
    }

    func testInProgressFaces() {
        let model = HomeTilesModel()
        XCTAssertEqual(model.face(for: .inProgress), .offer("Find something"))
        model.isLoadingInProgress = true
        XCTAssertEqual(model.face(for: .inProgress), .loading)
        model.isLoadingInProgress = false
        model.inProgressError = "offline"
        XCTAssertEqual(model.face(for: .inProgress), .message("Couldn\u{2019}t load"))
        model.inProgress = [item("a")]
        XCTAssertEqual(model.face(for: .inProgress), .items, "titles win over an error and a reload")
        model.isLoadingInProgress = true
        XCTAssertEqual(model.face(for: .inProgress), .items)
    }

    func testTrendingFaces() {
        let model = HomeTilesModel()
        XCTAssertEqual(model.face(for: .trending), .message("Nothing yet"))
        model.followsNobody = true
        XCTAssertEqual(model.face(for: .trending), .offer("Find people"))
        model.isLoadingTrending = true
        XCTAssertEqual(model.face(for: .trending), .loading)
        model.trending = [title("x")]
        XCTAssertEqual(model.face(for: .trending), .items)
    }

    func testSubtitlesAndLabels() {
        let model = HomeTilesModel()
        model.inProgress = [item("a"), item("b"), item("c"), item("d")]
        model.trending = [title("x")]
        model.trendingSubtitle = "This week"
        XCTAssertEqual(model.subtitle(for: .inProgress), "4 titles")
        XCTAssertEqual(model.subtitle(for: .trending), "This week")
        XCTAssertEqual(model.openLabel(for: .inProgress), "Open In progress, 4 titles")
        XCTAssertEqual(model.openLabel(for: .trending), "Open Trending, 1 title, This week")
        XCTAssertEqual(HomeTileID.inProgress.identifier, "home.tile.in-progress")
        XCTAssertEqual(HomeTileID.trending.identifier, "home.tile.trending")
        XCTAssertEqual(HomeTileID.inProgress.index, 0)
        XCTAssertEqual(HomeTileID.trending.index, 1)

        model.inProgress = []
        XCTAssertEqual(model.subtitle(for: .inProgress), "Find something")
    }

    func testDetailsReadTodayAndTheBaselineByTitle() {
        let model = HomeTilesModel()
        let book = item("book", type: "book")
        model.inProgress = [book]
        let plain = model.detail(for: book)
        XCTAssertEqual(plain.title, "Title book")
        XCTAssertNil(plain.todayText)

        model.todayParts[HomeTodayStore.titleKey(for: book.media.ref)] = TodayPart(unit: .pages, amount: 30)
        model.baselines[book.id] = 0
        // A book with no progress stored has no position, so there is nothing to light.
        XCTAssertNil(model.detail(for: book).todayText)
    }

    func testTrendingDetailReadsTheTitleAndItsNeighborsAreNextThenPrevious() {
        let model = HomeTilesModel()
        model.trending = [title("w"), title("x"), title("y"), title("z")]
        XCTAssertEqual(model.trendingDetail(for: model.trending[1]).title, "Title x")
        XCTAssertEqual(model.trendingNeighbors(of: model.trending[1]).map(\.key), [model.trending[2].key, model.trending[0].key])
        XCTAssertEqual(model.trendingNeighbors(of: model.trending[0]).map(\.key), [model.trending[1].key])
        XCTAssertEqual(model.trendingNeighbors(of: model.trending[3]).map(\.key), [model.trending[2].key])
        XCTAssertTrue(model.trendingNeighbors(of: title("elsewhere")).isEmpty)
    }
}
