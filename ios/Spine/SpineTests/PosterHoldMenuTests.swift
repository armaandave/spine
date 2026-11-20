import XCTest
@testable import Spine

/// What holding a poster offers, over type and where you are with the title: the shelf line only for a title in your
/// library, Watched and Planning until it's in progress, + Log in Watched's place while it is, and Remove from Library
/// only where the title's page offers it.
@MainActor
final class PosterHoldMenuTests: XCTestCase {
    private func ref(_ type: String, source: String = "tmdb", season: Int? = nil) -> MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: type, mediaId: "1", seasonNumber: season, episodeNumber: nil)
    }

    private let types: [(type: String, source: String)] = [
        ("movie", "tmdb"), ("tv", "tmdb"), ("anime", "mal"), ("book", "hardcover"), ("manga", "mal"),
        ("comic", "comicvine"), ("game", "igdb"), ("music", "apple_music"),
    ]

    private func watchedWord(_ type: String) -> String {
        PosterHoldMenu.watchedWord(for: ref(type))
    }

    func testATitleNotInYourLibraryOffersWatchedAndPlanningWithNoShelf() {
        for (type, source) in types {
            for viewer in [nil, UserMediaState.untracked] {
                let menu = PosterHoldMenu(ref: ref(type, source: source), viewer: viewer)
                XCTAssertNil(menu.shelf, type)
                XCTAssertEqual(menu.quick, [.watched(word: watchedWord(type), isFilled: false), .planning(isFilled: false)], type)
                XCTAssertFalse(menu.offersRemoval, type)
            }
        }
    }

    func testTheEyesWordFollowsTheType() {
        XCTAssertNotEqual(watchedWord("music"), watchedWord("movie"))
        XCTAssertEqual(Set(["book", "manga", "comic"].map(watchedWord)).count, 1, "Every book type says the same")
        XCTAssertNotEqual(watchedWord("book"), watchedWord("movie"))
        XCTAssertNotEqual(watchedWord("game"), watchedWord("movie"))
        XCTAssertEqual(watchedWord("tv"), watchedWord("movie"))
        XCTAssertEqual(watchedWord("anime"), watchedWord("movie"))
    }

    func testPlanningShowsItsShelfAndAFilledBookmarkThatTakesItOut() {
        for (type, source) in types {
            let menu = PosterHoldMenu(ref: ref(type, source: source), viewer: UserMediaState(isTracked: true, status: "Planning"))
            XCTAssertEqual(menu.shelf, PosterHoldMenu.shelfName(status: "Planning", mediaType: type), type)
            XCTAssertNotNil(menu.shelf, type)
            XCTAssertEqual(menu.quick, [.watched(word: watchedWord(type), isFilled: false), .planning(isFilled: true)], type)
            XCTAssertFalse(menu.offersRemoval, "The bookmark takes a planned title out: \(type)")
        }
        let show = UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "Planning"))
        XCTAssertEqual(PosterHoldMenu(ref: ref("tv"), viewer: show).quick.last, .planning(isFilled: true), "A show's own state decides")
    }

    func testInProgressHasNoRowJustItsShelfOnEveryType() {
        for (type, source) in types {
            let menu = PosterHoldMenu(ref: ref(type, source: source), viewer: UserMediaState(isTracked: true, status: "In progress"))
            XCTAssertEqual(menu.quick, [], type)
            XCTAssertEqual(menu.shelf, PosterHoldMenu.shelfName(status: "In progress", mediaType: type), type)
        }
        let watching = UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "In progress"))
        XCTAssertEqual(PosterHoldMenu(ref: ref("tv"), viewer: watching).quick, [])
    }

    func testPausedAndDroppedKeepTheEye() {
        for status in ["Paused", "Dropped"] {
            let menu = PosterHoldMenu(ref: ref("book", source: "hardcover"), viewer: UserMediaState(isTracked: true, status: status))
            XCTAssertEqual(menu.quick, [.watched(word: watchedWord("book"), isFilled: false)], status)
            XCTAssertNotNil(menu.shelf, status)
        }
    }

    func testAFinishedTitleShowsWatchedFilledAndNoPlanning() {
        let menu = PosterHoldMenu(ref: ref("movie"), viewer: UserMediaState(isTracked: true, status: "Completed"))
        XCTAssertEqual(menu.quick, [.watched(word: watchedWord("movie"), isFilled: true)])
        XCTAssertEqual(menu.shelf, PosterHoldMenu.shelfName(status: "Completed", mediaType: "movie"))
    }

    func testRemoveFromLibraryOnlyWhereTheTitlesPageOffersIt() {
        let completed = UserMediaState(isTracked: true, status: "Completed")
        XCTAssertTrue(PosterHoldMenu(ref: ref("book", source: "hardcover"), viewer: completed).offersRemoval)
        XCTAssertTrue(PosterHoldMenu(ref: ref("game", source: "igdb"), viewer: completed).offersRemoval)
        XCTAssertFalse(PosterHoldMenu(ref: ref("movie"), viewer: completed).offersRemoval, "A movie leaves from its eye")
        XCTAssertFalse(PosterHoldMenu(ref: ref("music", source: "apple_music"), viewer: completed).offersRemoval)

        let listed = UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "Completed", availableActions: ["remove"]))
        let withheld = UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "Completed", availableActions: ["rewatch"]))
        let listsCopy = UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "Completed"))
        XCTAssertTrue(PosterHoldMenu(ref: ref("tv"), viewer: listed).offersRemoval, "When the server lists it")
        XCTAssertFalse(PosterHoldMenu(ref: ref("tv"), viewer: withheld).offersRemoval, "When the server leaves it out")
        XCTAssertTrue(PosterHoldMenu(ref: ref("tv"), viewer: listsCopy).offersRemoval,
                      "A list's copy sends no actions: offered, and the choice checks with the title first")
    }

    func testTheEyeStaysOpenToFlipOnlyWhenItsTapIsAWriteByItself() {
        XCTAssertTrue(PosterHoldMenu(ref: ref("movie"), viewer: nil).eyeStaysOpen)
        XCTAssertTrue(PosterHoldMenu(ref: ref("movie"), viewer: UserMediaState(isTracked: true, status: "Completed")).eyeStaysOpen)
        XCTAssertFalse(PosterHoldMenu(ref: ref("movie"), viewer: UserMediaState(isTracked: true, status: "Completed", diaryCount: 2)).eyeStaysOpen,
                       "Logs keep it watched: undoing is explained, and the menu closes for it")
        XCTAssertTrue(PosterHoldMenu(ref: ref("book", source: "hardcover"), viewer: nil).eyeStaysOpen)
        XCTAssertFalse(PosterHoldMenu(ref: ref("tv"), viewer: nil).eyeStaysOpen, "A show's eye marks every aired episode, asking first")
        XCTAssertFalse(PosterHoldMenu(ref: ref("anime", source: "mal"), viewer: nil).eyeStaysOpen)
        XCTAssertFalse(PosterHoldMenu(ref: ref("movie"), viewer: UserMediaState(isTracked: true, status: "In progress")).eyeStaysOpen,
                       "No eye while in progress")
    }

    func testASeasonLeavesPlanningToItsShow() {
        let season = ref("season", season: 2)
        XCTAssertEqual(PosterHoldMenu(ref: season, viewer: nil).quick, [.watched(word: watchedWord("tv"), isFilled: false)])
        let watching = UserMediaState(isTracked: true, status: "In progress", tv: TVShowState(inLibrary: true, status: "In progress"))
        XCTAssertEqual(PosterHoldMenu(ref: season, viewer: watching).quick, [])
        XCTAssertFalse(PosterHoldMenu(ref: season, viewer: watching).offersRemoval)
    }

    func testTheShelfIsLibrarysOwnName() {
        XCTAssertEqual(PosterHoldMenu.shelfName(status: "In progress", mediaType: "game"), LibraryShelf.currentlyReading.title(mediaType: "game"))
        XCTAssertEqual(PosterHoldMenu.shelfName(status: "Planning", mediaType: "book"), LibraryShelf.planning.title(mediaType: "book"))
        XCTAssertEqual(PosterHoldMenu.shelfName(status: "completed", mediaType: "movie"), LibraryShelf.read.title(mediaType: "tv"),
                       "A movie, with no status shelves of its own, uses the show words, in any case")
        XCTAssertNil(PosterHoldMenu.shelfName(status: nil, mediaType: "movie"))
    }
}
