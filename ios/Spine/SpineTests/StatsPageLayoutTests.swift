import Foundation
import XCTest
@testable import Spine

/// Which sections the Stats page shows, and in what order (STAT-218, STAT-302).
@MainActor
final class StatsPageLayoutTests: XCTestCase {
    func testGlobalShowsEverySectionInTheContractOrder() throws {
        let page = try decode(Self.pageJSON(mediaType: nil))

        XCTAssertEqual(StatsPageSection.sections(for: page), [
            .header, .time, .top, .highestRated, .activity, .ratings, .ratedVsEveryone, .releaseYears,
            .genres, .languages, .listProgress, .mostLogged, .milestones,
        ])
    }

    func testATypeTabMovesActivityDownAddsCollectionsAndFoldsTimeIntoTheHeader() throws {
        let page = try decode(Self.pageJSON(mediaType: "movie"))

        XCTAssertEqual(StatsPageSection.sections(for: page), [
            .header, .top, .highestRated, .ratings, .ratedVsEveryone, .releaseYears, .genres,
            .languages, .activity, .listProgress, .collections, .mostLogged, .milestones,
        ])
        XCTAssertNotNil(page.time, "The tab's Time row still feeds the header's third number")
    }

    func testPeopleFollowGenresAndLanguagesAndEmptyGroupsHide() throws {
        let people = """
        {"groups": [
          {"role": "director", "label": "Directors", "rows": [], "rated_rows": [], "total": 0},
          {"role": "cast", "label": "Cast", "total": 1,
           "rows": [{"person": {"source": "tmdb", "id": "6193", "name": "Leonardo DiCaprio", "image_url": null},
                     "titles": 2, "rated": 1, "average_stars": "4.5"}],
           "rated_rows": []}
        ]}
        """
        let page = try decode(Self.pageJSON(mediaType: "movie", people: people))

        XCTAssertEqual(StatsPageSection.sections(for: page), [
            .header, .top, .highestRated, .ratings, .ratedVsEveryone, .releaseYears, .genres,
            .languages, .people, .activity, .listProgress, .collections, .mostLogged, .milestones,
        ])
        XCTAssertEqual(StatsPeopleData.visibleGroups(page.people).map(\.role), ["cast"], "The empty Directors group hides")

        let nobody = try decode(Self.pageJSON(mediaType: "movie", people: """
        {"groups": [{"role": "director", "label": "Directors", "rows": [], "rated_rows": [], "total": 0},
                    {"role": "cast", "label": "Cast", "rows": [], "rated_rows": [], "total": 0}]}
        """))
        XCTAssertFalse(StatsPageSection.sections(for: nobody).contains(.people))
        XCTAssertFalse(StatsPageSection.sections(for: try decode(Self.pageJSON(mediaType: "movie"))).contains(.people), "Older servers send no people")
    }

    func testAYearShowsCollectionsFromItsOwnProgress() throws {
        let progressed = """
        {"complete_count": 4, "almost_count": 2, "complete": [], "almost": [],
         "in_range": {"completed_count": 0, "completed": [], "progressed_count": 1,
                      "progressed": [{"id": "10", "source": "tmdb", "media_type": "movie", "name": "Dune", "item_count": 3,
                                      "poster_urls": [], "completion": {"completed_count": 2, "total_count": 3}, "seen_in_range": 1}]}}
        """
        let page = try decode(Self.pageJSON(mediaType: "movie", collections: progressed))
        XCTAssertTrue(StatsPageSection.sections(for: page).contains(.collections))
        XCTAssertEqual(page.collections.map(StatsCollectionsData.groups)?.map(\.group), [.progressedInRange])

        let quietYear = """
        {"complete_count": 4, "almost_count": 2, "complete": [], "almost": [],
         "in_range": {"completed_count": 0, "completed": [], "progressed_count": 0, "progressed": []}}
        """
        let quiet = try decode(Self.pageJSON(mediaType: "movie", collections: quietYear))
        XCTAssertFalse(StatsPageSection.sections(for: quiet).contains(.collections), "A year that moved nothing forward has no Collections")
    }

    func testTheMediaPickerFitsAllEightTypesOnAPhone() throws {
        // A 402 pt phone less the page's 16 pt edges and the capsule's 14 pt insets.
        let eight = try XCTUnwrap(MediaSearchLensLayout.fittedSlots(available: 402 - 32 - 28, count: 8, isCompact: true))
        XCTAssertEqual(eight.spacing, 0, "Too narrow for gaps, the slots touch")
        XCTAssertEqual(eight.width, 42.75, accuracy: 0.01)

        // Seven types keep the usual gap and 44 pt slots, as the Diary has them.
        let seven = try XCTUnwrap(MediaSearchLensLayout.fittedSlots(available: 342, count: 7, isCompact: true))
        XCTAssertEqual(seven.spacing, 3)
        XCTAssertGreaterThanOrEqual(seven.width, 44)

        XCTAssertNil(MediaSearchLensLayout.fittedSlots(available: 280, count: 8, isCompact: true), "Too narrow: it scrolls")
        XCTAssertNil(MediaSearchLensLayout.fittedSlots(available: 342, count: 8, isCompact: false))
    }

    func testCollectionsNeverShowOnGlobal() throws {
        let page = try decode(Self.pageJSON(mediaType: nil, collections: Self.collectionsJSON))

        XCTAssertFalse(StatsPageSection.sections(for: page).contains(.collections))
    }

    func testTheTopFallbackHidesHighestRated() throws {
        let page = try decode(Self.pageJSON(mediaType: "movie", topSource: "highest_rated"))
        let sections = StatsPageSection.sections(for: page)

        XCTAssertTrue(sections.contains(.top))
        XCTAssertFalse(sections.contains(.highestRated), "Top already shows the highest rated titles")
    }

    func testNullAndEmptySectionsAreHidden() throws {
        let page = try decode("""
        {
          "schema_version": 2,
          "range": {"start_date": "2025-01-01", "end_date": "2025-12-31", "timezone": "UTC", "is_all_time": false},
          "media_type": "manga",
          "audience": "owner",
          "years": {"first": 2020, "current": 2026},
          "media_types": [],
          "header": {"titles": 3, "logs": 4, "active_days": 2, "longest_daily_streak": 1, "details": []},
          "time": {"rows": [], "hours_total": null},
          "top": {"source": "highest_rated", "tag": "top2025", "tag_hint": null, "hint_private": false, "list": null, "items": []},
          "highest_rated": {"total": 0, "items": []},
          "activity": {"months": [], "weekdays": [0, 0, 0, 0, 0, 0, 0], "week_starts_on": "monday", "busiest_day": null, "busiest_month": null},
          "ratings": {"average_stars": null, "rated": 0, "distribution": []},
          "rated_vs_everyone": {"sources": [], "higher": [], "lower": []},
          "release_years": {"rows": [], "coverage": null, "new_vs_older": null},
          "genres": {"rows": [], "total": 0, "coverage": null, "music": []},
          "languages": null,
          "list_rings": [],
          "collections": null,
          "most_logged": {"label": "Most read", "total": 0, "items": []},
          "milestones": []
        }
        """)

        XCTAssertEqual(StatsPageSection.sections(for: page), [.header])
    }

    func testMusicGenresAloneKeepTheGenresSection() throws {
        let page = try decode(Self.pageJSON(
            mediaType: nil,
            genres: #"{"rows": [], "total": 0, "coverage": null, "music": [{"name": "shoegaze", "titles": 7}]}"#
        ))

        XCTAssertTrue(StatsPageSection.sections(for: page).contains(.genres))
    }

    func testALegacyPageShowsOnlyItsRebuiltSections() throws {
        let page = try decode("""
        {
          "schema_version": 1,
          "range": {"start_date": null, "end_date": null, "timezone": "UTC", "is_all_time": true},
          "overview": {"tracked_count": 40, "completed_count": 30, "diary_entry_count": 15, "unique_logged_count": 12,
                       "review_count": 5, "repeat_count": 3, "rated_count": 10, "average_rating": "3.5", "liked_count": 6,
                       "active_days": 9, "current_streak_days": 2, "longest_streak_days": 4},
          "media_types": [],
          "activity": {
            "days": [{"date": "2025-01-06", "count": 2}],
            "months": [{"month": "2025-01", "count": 2}],
            "active_days": 1, "current_streak_days": 0, "longest_streak_days": 1
          },
          "top_genres": [{"name": "Drama", "count": 8}],
          "metadata_coverage": {"total_items": 12, "release_year_items": 11, "genre_items": 10, "language_items": 9}
        }
        """)

        XCTAssertEqual(StatsPageSection.sections(for: page), [.header, .activity, .genres])
    }

    func testShowAllScreensNameTheirScope() {
        XCTAssertEqual(StatsPageSection.scopeTitle(mediaType: "movie", period: .year(2025)), "Movies · 2025")
        XCTAssertEqual(StatsPageSection.scopeTitle(mediaType: nil, period: .allTime), "All Media · All Time")
    }

    // MARK: - Helpers

    private func decode(_ json: String) throws -> StatsPage {
        try JSONDecoder.api.decode(StatsPage.self, from: Data(json.utf8))
    }

    private static func media(id: Int, title: String, type: String = "movie") -> String {
        """
        {"ref": {"item_id": \(id), "source": "tmdb", "media_type": "\(type)", "media_id": "\(id)", \
        "season_number": null, "episode_number": null}, \
        "title": "\(title)", "poster_url": "https://example.com/\(id).jpg"}
        """
    }

    private static let collectionsJSON = """
    {"complete_count": 1, "almost_count": 0,
     "complete": [{"id": "10", "source": "tmdb", "media_type": "movie", "name": "Dune", "item_count": 2,
                   "poster_urls": [], "completion": {"completed_count": 2, "total_count": 2}}],
     "almost": []}
    """

    private static func pageJSON(
        mediaType: String?,
        topSource: String = "list",
        collections: String? = nil,
        genres: String? = nil,
        people: String? = nil
    ) -> String {
        let list = topSource == "list" ? #"{"id": 81, "name": "My 2025", "total": 24}"# : "null"
        let collectionsBlock = collections ?? (mediaType == nil ? "null" : collectionsJSON)
        let genresBlock = genres ?? """
        {"rows": [{"key": "drama", "name": "Drama", "values": null, "titles": 4, "rated": 3, "average_stars": "3.5", "by_type": {"movie": 4}}],
         "total": 1, "coverage": null, "music": []}
        """
        return """
        {
          "schema_version": 2,
          "range": {"start_date": "2025-01-01", "end_date": "2025-12-31", "timezone": "UTC", "is_all_time": false},
          "media_type": \(mediaType.map { "\"\($0)\"" } ?? "null"),
          "audience": "owner",
          "years": {"first": 2014, "current": 2026},
          "media_types": [{"media_type": "movie", "titles": 4, "logs": 5}],
          "header": {"titles": 4, "logs": 5, "active_days": 3, "longest_daily_streak": 2, "details": []},
          "time": {"rows": [{"media_type": "movie", "unit": "minutes", "value": 480, "known": 4, "total": 4, "approximate": false}],
                   "hours_total": null},
          "top": {"source": "\(topSource)", "tag": "top2025", "tag_hint": null, "hint_private": false, "list": \(list),
                  "items": [{"rank": 1, "media": \(media(id: 1, title: "One")), "stars": "5.0"}]},
          "highest_rated": {"total": 1, "items": [{"media": \(media(id: 1, title: "One")), "stars": "5.0", "day": "2025-03-02"}]},
          "activity": {"months": [{"month": "2025-03", "count": 5, "by_type": {"movie": 5}}],
                       "weekdays": [1, 1, 1, 1, 1, 0, 0], "week_starts_on": "monday",
                       "busiest_day": {"date": "2025-03-02", "count": 2}, "busiest_month": {"month": "2025-03", "count": 5}},
          "ratings": {"average_stars": "4.0", "rated": 3,
                      "distribution": [{"stars": "4.0", "count": 3, "rating_min": "7.5", "rating_max": "8.4", "by_type": {"movie": 3}}]},
          "rated_vs_everyone": {"sources": [{"media_type": "movie", "source": "letterboxd", "label": "Letterboxd"}],
                                "higher": [{"media": \(media(id: 1, title: "One")), "stars": "5.0", "everyone_stars": "3.1",
                                            "difference": "1.9", "source": "letterboxd"}],
                                "lower": []},
          "release_years": {"rows": [{"year": 2024, "titles": 4, "logs": 5, "rated": 3, "average_stars": "4.0", "by_type": {"movie": 4}}],
                            "coverage": {"known": 4, "total": 4}, "new_vs_older": {"new": 4, "recent": 0, "older": 0}},
          "genres": \(genresBlock),
          "languages": {"rows": [{"name": "English", "titles": 4, "rated": 3, "average_stars": "4.0", "by_type": {"movie": 4}}],
                        "total": 1, "coverage": null, "media_types": ["movie", "tv"]},
          "list_rings": [{"id": 5, "name": "Top 500", "media_type": "movie", "kind": "community", "seen": 4, "total": 500}],
          "collections": \(collectionsBlock),
          "people": \(people ?? "null"),
          "most_logged": {"label": "Most watched", "total": 1, "items": [{"media": \(media(id: 1, title: "One")), "times": 2}]},
          "milestones": [{"number": 1, "day": "2025-03-02", "entry_id": 11, "media_type": "movie", "media": \(media(id: 1, title: "One"))}]
        }
        """
    }
}
