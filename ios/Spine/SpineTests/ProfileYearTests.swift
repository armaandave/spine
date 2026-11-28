import XCTest
@testable import Spine

/// The profile's This Year section: the wall's grid rule and the rows built from the server's numbers (STAT-312).
final class ProfileYearTests: XCTestCase {
    func testWallTakesTheMostColumnsItsPostersFillAtAPostersShape() {
        // A menu-width wall on a 430 pt phone with four types: 398 wide by 769 tall.
        let steps = [11, 12, 23, 24, 34, 35, 47, 48, 60].map { posters in
            ProfileYearWall.grid(posters: posters, width: 398, height: 769).map { [$0.across, $0.down] }
        }
        XCTAssertEqual(steps, [nil, [3, 4], [3, 4], [4, 6], [4, 6], [5, 7], [5, 7], [6, 8], [6, 8]])
        for posters in 12...60 {
            guard let grid = ProfileYearWall.grid(posters: posters, width: 398, height: 769) else {
                XCTFail("No wall for \(posters) posters")
                continue
            }
            let cell = ProfileYearWall.cellSize(across: grid.across, width: 398)
            // Never more posters than there are, every cell exactly 2:3, and the rows reach the wall's bottom.
            XCTAssertLessThanOrEqual(grid.count, posters)
            XCTAssertEqual(cell.height / cell.width, 1.5, accuracy: 0.0001)
            XCTAssertGreaterThanOrEqual(CGFloat(grid.down) * (cell.height + ProfileYearWall.gap) - ProfileYearWall.gap, 769)
        }
    }

    func testRowsFollowTheTurnedOnTypesAndShowTimeOnlyWhenThereIsSome() throws {
        let year = try JSONDecoder.api.decode(ProfileYear.self, from: Data(Self.payload.utf8))

        XCTAssertEqual(year.year, 2026)
        XCTAssertEqual(year.titles, 30)
        XCTAssertEqual(year.posters.map(\.title), ["Sinners"])

        let rows = ProfileYearPresentation.rows(for: year.thisYear)
        XCTAssertEqual(rows.map(\.mediaType), ["movie", "book", "music", "game"])
        XCTAssertEqual(rows.map(\.isEmpty), [false, false, false, true])
        // Movies and books have time; the album's runtime is unknown (0) and Games has nothing this year.
        XCTAssertEqual(rows.map { $0.measure != nil }, [true, true, false, false])
        XCTAssertEqual(rows[0].measure, StatsTimeSection.value(for: year.time.rows[0]))
        // A server without the All Time switch's numbers.
        XCTAssertNil(year.allTime)
        XCTAssertNil(year.ratings)
    }

    func testAllTimeHasItsOwnNumbersAndRatingsBesideTheYears() throws {
        let year = try JSONDecoder.api.decode(ProfileYear.self, from: Data(Self.withAllTime.utf8))

        XCTAssertEqual(year.ratings?.rated, 2)
        let allTime = try XCTUnwrap(year.allTime)
        XCTAssertEqual(allTime.titles, 140)
        XCTAssertEqual(allTime.ratings?.rated, 118)
        let rows = ProfileYearPresentation.rows(for: allTime, allTime: true)
        XCTAssertEqual(rows.map(\.mediaType), ["movie", "game"])
        // Games has nothing this year, but something of all time.
        XCTAssertEqual(rows.map(\.isEmpty), [false, false])
        XCTAssertEqual(rows[1].count, ListCountWords.text(count: 4, mediaTypes: ["game"], isPeople: false))
        // Dealing the wall again keeps both ranges' numbers.
        XCTAssertEqual(year.withPosters([]).allTime, allTime)
        XCTAssertEqual(year.withPosters([]).ratings, year.ratings)
    }

    func testTheWallStaysTheSameForTheSessionUntilANewDeal() throws {
        let session = ProfileYearWallSession()
        let first = try (1...12).map { try Self.poster("first-\($0)") }
        let later = try (1...12).map { try Self.poster("later-\($0)") }

        let shown = session.posters(for: "maya/2026", from: first, dealNew: false)
        XCTAssertEqual(Set(shown.map(\.id)), Set(first.map(\.id)))
        // Coming back to the profile, with a new pick from the server: the same wall, in the same order.
        XCTAssertEqual(session.posters(for: "maya/2026", from: later, dealNew: false).map(\.id), shown.map(\.id))
        // Another profile has its own wall.
        XCTAssertEqual(Set(session.posters(for: "theo/2026", from: later, dealNew: false).map(\.id)), Set(later.map(\.id)))
        // Pull to refresh deals from the new pick.
        XCTAssertEqual(Set(session.posters(for: "maya/2026", from: later, dealNew: true).map(\.id)), Set(later.map(\.id)))
        // A year that gained a poster deals again, so the wall follows it.
        let grown = try later + [Self.poster("later-13")]
        XCTAssertEqual(Set(session.posters(for: "maya/2026", from: grown, dealNew: false).map(\.id)), Set(grown.map(\.id)))
    }

    private static func poster(_ mediaID: String) throws -> MediaSummary {
        let json = """
        {"ref": {"item_id": 1, "source": "tmdb", "media_type": "movie", "media_id": "\(mediaID)", "season_number": null, "episode_number": null},
         "title": "\(mediaID)", "poster_url": "https://image.tmdb.org/t/p/w500/\(mediaID).jpg"}
        """
        return try JSONDecoder.api.decode(MediaSummary.self, from: Data(json.utf8))
    }

    private static let withAllTime = """
    {
      "year": 2026,
      "range": {"start_date": "2026-01-01", "end_date": "2026-12-31", "timezone": "America/New_York", "is_all_time": false},
      "audience": "owner",
      "titles": 3,
      "media_types": [
        {"media_type": "movie", "titles": 3, "logs": 3},
        {"media_type": "game", "titles": 0, "logs": 0}
      ],
      "time": {"rows": [], "hours_total": null},
      "ratings": {
        "average_stars": "4.00",
        "rated": 2,
        "distribution": [
          {"stars": "4.0", "count": 2, "rating_min": "7.5", "rating_max": "8.4", "by_type": {"movie": 2}}
        ]
      },
      "posters": [],
      "all_time": {
        "range": {"start_date": null, "end_date": null, "timezone": "America/New_York", "is_all_time": true},
        "titles": 140,
        "media_types": [
          {"media_type": "movie", "titles": 136, "logs": 150},
          {"media_type": "game", "titles": 4, "logs": 4}
        ],
        "time": {"rows": [], "hours_total": null},
        "ratings": {
          "average_stars": "3.50",
          "rated": 118,
          "distribution": [
            {"stars": "3.5", "count": 118, "rating_min": "6.5", "rating_max": "7.4", "by_type": {"movie": 118}}
          ]
        }
      }
    }
    """

    private static let payload = """
    {
      "year": 2026,
      "range": {"start_date": "2026-01-01", "end_date": "2026-12-31", "timezone": "America/New_York", "is_all_time": false},
      "audience": "owner",
      "titles": 30,
      "media_types": [
        {"media_type": "movie", "titles": 20, "logs": 24},
        {"media_type": "book", "titles": 8, "logs": 3},
        {"media_type": "music", "titles": 2, "logs": 2},
        {"media_type": "game", "titles": 0, "logs": 0}
      ],
      "time": {
        "rows": [
          {"media_type": "movie", "unit": "minutes", "value": 2400, "known": 24, "total": 24, "approximate": false},
          {"media_type": "book", "unit": "pages", "value": 2210, "known": 8, "total": 8, "approximate": false},
          {"media_type": "music", "unit": "minutes", "value": 0, "known": 0, "total": 2, "approximate": false}
        ],
        "hours_total": {"minutes": 2400, "media_types": ["movie", "game", "music"], "leaves_out": ["book"], "approximate": false}
      },
      "posters": [
        {
          "ref": {"item_id": 812, "source": "tmdb", "media_type": "movie", "media_id": "1233413", "season_number": null, "episode_number": null},
          "title": "Sinners",
          "subtitle": null,
          "overview": null,
          "image_url": "https://image.tmdb.org/t/p/w500/sinners.jpg",
          "poster_url": "https://image.tmdb.org/t/p/w500/sinners.jpg",
          "backdrop_url": null,
          "poster_accent_color": null,
          "release_date": null,
          "default_source": "tmdb",
          "custom_poster_url": null,
          "custom_backdrop_url": null,
          "user_state": null
        }
      ]
    }
    """
}
