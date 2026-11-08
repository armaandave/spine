import SwiftUI
import XCTest
@testable import Spine

/// What the Trending panel's rail says for a title, as a table over type and state. The regression these pin: a title
/// that is in your library never offers "Plan"; `.plan` is only for a title that is not.
@MainActor
final class TrendingRailConfigTests: XCTestCase {
    private func ref(_ type: String, source: String = "tmdb") -> MediaRef {
        MediaRef(itemId: nil, source: source, mediaType: type, mediaId: "1", seasonNumber: nil, episodeNumber: nil)
    }

    private func media(_ type: String, source: String = "tmdb") -> MediaSummary {
        MediaSummary(ref: ref(type, source: source), title: "Fixture", posterUrl: "https://p/1.jpg", posterOrientation: .portrait)
    }

    private func make(
        _ type: String,
        _ viewer: UserMediaState,
        source: String = "tmdb",
        ring: TrendingRailConfig.Ring? = nil
    ) -> TrendingRailConfig {
        TrendingRailConfig.make(title: "Fixture", ref: ref(type, source: source), viewer: viewer, ring: ring)
    }

    private let types: [(type: String, source: String)] = [
        ("movie", "tmdb"), ("tv", "tmdb"), ("anime", "mal"), ("book", "hardcover"), ("manga", "mal"), ("game", "igdb"),
        ("music", "apple_music"),
    ]

    // MARK: The regression

    func testATitleNotInYourLibraryOffersPlanOnEveryType() {
        for (type, source) in types {
            let config = make(type, .untracked, source: source)
            XCTAssertEqual(config.lead, .plan, type)
            XCTAssertEqual(config.leadLabel, "Plan Fixture", type)
            XCTAssertEqual(config.railLead, .symbol(name: "bookmark", size: 20), type)
        }
    }

    func testATitleInYourLibraryNeverOffersPlanWhateverWayItIsThere() {
        let ways: [(String, (String) -> UserMediaState)] = [
            ("tracked", { _ in UserMediaState(isTracked: true) }),
            ("status", { _ in UserMediaState(isTracked: false, status: "Dropped") }),
            ("completed", { _ in UserMediaState(isTracked: true, status: "Completed") }),
            ("paused", { _ in UserMediaState(isTracked: true, status: "Paused") }),
            ("show in library", { _ in UserMediaState(isTracked: false, tv: TVShowState(inLibrary: true)) }),
            ("anime in library", { _ in UserMediaState(isTracked: false, anime: AnimeState(inLibrary: true)) }),
        ]
        for (type, source) in types {
            for (name, build) in ways {
                let lead = make(type, build(type), source: source).lead
                XCTAssertNotEqual(lead, .plan, "\(type) \(name)")
            }
        }
    }

    func testPlanningIsPlannedAndTakesTheTitleBackOut() {
        for (type, source) in types {
            let config = make(type, UserMediaState(isTracked: true, status: "Planning"), source: source)
            XCTAssertEqual(config.lead, .planned, type)
            XCTAssertEqual(config.leadLabel, "Planned. Remove from Planning", type)
            XCTAssertEqual(config.railLead, .symbol(name: "bookmark.fill", size: 20), type)
        }
        let show = UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "Planning"))
        XCTAssertEqual(make("tv", show).lead, .planned, "A show's own state decides")
        XCTAssertEqual(make("movie", UserMediaState(isTracked: true, status: "planning")).lead, .planned, "Any case")
    }

    // MARK: In progress and the ring

    func testInProgressWithAStepShowsTheRingAndTheStepsWords() {
        let ring = TrendingRailConfig.Ring(fraction: 0.4, label: "Watched E4")
        let config = make("tv", UserMediaState(isTracked: true, status: "In progress"), ring: ring)
        XCTAssertEqual(config.lead, .ring(fraction: 0.4))
        XCTAssertEqual(config.leadLabel, "Watched E4")
        XCTAssertEqual(config.railLead, .ring(fraction: 0.4, tint: .white))
    }

    func testTheRingFractionIsClamped() {
        let ring = TrendingRailConfig.Ring(fraction: 1.7, label: "Update")
        XCTAssertEqual(make("book", UserMediaState(isTracked: true, status: "In progress"), source: "hardcover", ring: ring).lead,
                       .ring(fraction: 1))
    }

    func testEverythingElseInTheLibraryIsThePagesPlus() {
        let inProgressWithNoStep = make("movie", UserMediaState(isTracked: true, status: "In progress"))
        XCTAssertEqual(inProgressWithNoStep.lead, .track, "A movie has no step to take")
        XCTAssertEqual(make("movie", UserMediaState(isTracked: true, status: "Completed")).lead, .track)
        XCTAssertEqual(make("tv", UserMediaState(isTracked: true, status: "Paused")).lead, .track)
        XCTAssertEqual(make("movie", UserMediaState(isTracked: true)).railLead, .plus, "The page's + at 22 pt")
        let ring = TrendingRailConfig.Ring(fraction: 0.2, label: "x")
        XCTAssertEqual(make("tv", UserMediaState(isTracked: true, status: "Completed"), ring: ring).lead, .track,
                       "Only an In progress title gets the ring")
    }

    func testTheLeadsLabelIsThePagesOwnTrackLabel() {
        let inLibrary = UserMediaState(isTracked: true, status: "Completed")
        XCTAssertEqual(make("movie", inLibrary).leadLabel, "Log")
        XCTAssertEqual(make("book", inLibrary, source: "hardcover").leadLabel, "Track")
        XCTAssertEqual(make("game", inLibrary, source: "igdb").leadLabel, "Track")
        XCTAssertEqual(make("tv", UserMediaState(isTracked: true, tv: TVShowState(inLibrary: true, status: "Completed"))).leadLabel,
                       "Track show")
        XCTAssertEqual(make("anime", UserMediaState(isTracked: true, anime: AnimeState(inLibrary: true, status: "Completed")), source: "mal").leadLabel,
                       "Track anime")
    }

    func testTheRingNeedsAPlanAndItsFractionIsHowFarTheShowIs() {
        let pointer = TVEpisodePointer(seasonNumber: 1, episodeNumber: 4)
        let tv = TVShowState(
            inLibrary: true, status: "In progress", started: true, watchedCount: 3, airedCount: 10, upNext: pointer
        )
        let viewer = UserMediaState(isTracked: true, status: "In progress", tv: tv)
        let ring = TrendingRailRing.make(media: media("tv"), viewer: viewer)
        XCTAssertEqual(ring?.label, "Watched E4")
        XCTAssertEqual(try XCTUnwrap(ring?.fraction), 0.3, accuracy: 0.0001)
        XCTAssertNil(TrendingRailRing.make(media: media("movie"), viewer: UserMediaState(isTracked: true, status: "In progress")),
                     "A movie has no step")
    }

    func testAShowOneEpisodeFromTheEndHasAnUpdateRingNotATickRing() {
        let tv = TVShowState(
            inLibrary: true, status: "In progress", started: true, watchedCount: 9, airedCount: 10,
            upNext: TVEpisodePointer(seasonNumber: 1, episodeNumber: 10)
        )
        let ring = TrendingRailRing.make(
            media: media("tv"), viewer: UserMediaState(isTracked: true, status: "In progress", tv: tv)
        )
        XCTAssertEqual(ring?.label, "Update Fixture", "The last tick finishes the show, which is the sheet's to do")
    }

    // MARK: The eye, the star, the heart

    func testTheEyesWordsFollowTheTypeAndState() {
        XCTAssertEqual(make("movie", .untracked).eyeLabel, "Mark as watched")
        XCTAssertEqual(make("movie", UserMediaState(isTracked: true, status: "Completed")).eyeLabel, "Watched")
        XCTAssertTrue(make("movie", UserMediaState(isTracked: true, status: "Completed")).isEyeSelected)
        XCTAssertEqual(make("music", .untracked, source: "apple_music").eyeLabel, "Mark as listened")
        XCTAssertEqual(make("music", UserMediaState(isTracked: true, status: "Completed"), source: "apple_music").eyeLabel, "Listened")
        XCTAssertEqual(make("book", .untracked, source: "hardcover").eyeLabel, "Finished Reading")
        XCTAssertEqual(make("manga", .untracked, source: "mal").eyeLabel, "Finished Reading")
        XCTAssertEqual(make("game", .untracked, source: "igdb").eyeLabel, "Finished Playing")
        XCTAssertFalse(make("movie", UserMediaState(isTracked: true, status: "Planning")).isEyeSelected)
    }

    func testAShowsAndAnimesEyeFollowsTheServersEye() {
        XCTAssertEqual(make("tv", .untracked).eyeLabel, "Mark show watched")
        XCTAssertEqual(make("tv", .untracked).eyeHint, "Marks every aired episode watched, without a date")
        let filled = TVShowState(inLibrary: true, eye: TVEyeState(filled: true, tap: .unmarkConfirm))
        let show = make("tv", UserMediaState(isTracked: true, tv: filled))
        XCTAssertEqual(show.eyeLabel, "Show watched")
        XCTAssertEqual(show.eyeHint, "Undoes marking these episodes watched")
        XCTAssertTrue(show.isEyeSelected)

        let anime = make("anime", UserMediaState(isTracked: true, anime: AnimeState(inLibrary: true, eye: TVEyeState(filled: true, tap: .unmarkMarks))), source: "mal")
        XCTAssertEqual(anime.eyeLabel, "Anime watched")
        XCTAssertTrue(anime.isEyeSelected)
        XCTAssertEqual(make("anime", .untracked, source: "mal").eyeLabel, "Mark anime watched")
    }

    func testTheStarIsAlwaysThereAndFilledByARating() {
        for (type, source) in types {
            XCTAssertTrue(make(type, .untracked, source: source).showsRating, type)
            XCTAssertFalse(make(type, .untracked, source: source).isRatingFilled, type)
        }
        XCTAssertTrue(make("movie", UserMediaState(isTracked: true, rating: "4.5")).isRatingFilled)
        XCTAssertFalse(make("movie", UserMediaState(isTracked: true, rating: "0")).isRatingFilled)
    }

    func testTheHeartFollowsTheViewer() {
        XCTAssertFalse(make("movie", .untracked).isLiked)
        XCTAssertTrue(make("movie", UserMediaState(isTracked: false, hasLiked: true)).isLiked)
    }

    // MARK: Parity with the page's rail

    /// `standardActionRail` and `tvActionRail` on the media page: the rating picker opens after the base action for
    /// single-weight types, a book with no live journey and a game with no live playthrough; never for a show or anime.
    func testTheRatingPickerOffersAfterTheBaseActionExactlyWhereThePageDoes() {
        XCTAssertTrue(make("movie", .untracked).offersRatingAfterBaseAction)
        XCTAssertTrue(make("music", .untracked, source: "apple_music").offersRatingAfterBaseAction)
        XCTAssertTrue(make("book", .untracked, source: "hardcover").offersRatingAfterBaseAction)
        XCTAssertTrue(make("manga", .untracked, source: "mal").offersRatingAfterBaseAction)
        XCTAssertTrue(make("game", .untracked, source: "igdb").offersRatingAfterBaseAction)
        XCTAssertFalse(make("tv", .untracked).offersRatingAfterBaseAction)
        XCTAssertFalse(make("anime", .untracked, source: "mal").offersRatingAfterBaseAction)
        XCTAssertFalse(make("anime", UserMediaState(isTracked: true, anime: AnimeState(inLibrary: true)), source: "mal").offersRatingAfterBaseAction)
    }

    func testTheRatingUnavailableReasonOnlyReachesShowsAndAnime() {
        let withReason = TrendingRailConfig.make(
            title: "Fixture", ref: ref("tv"), viewer: .untracked, ratingUnavailableReason: "Nothing has aired yet."
        )
        XCTAssertEqual(withReason.ratingUnavailableReason, "Nothing has aired yet.")
        let movie = TrendingRailConfig.make(
            title: "Fixture", ref: ref("movie"), viewer: .untracked, ratingUnavailableReason: "Nothing has aired yet."
        )
        XCTAssertNil(movie.ratingUnavailableReason)
    }
}
