import SwiftUI
import XCTest
@testable import Spine

/// The profile's signature beside the @handle: its ratings, read from the server's (STAT-313), and its line.
@MainActor
final class ProfileRatingsSignatureTests: XCTestCase {
    func testStepsComeFromTheStatsBucketsWithMissingOnesEmpty() throws {
        let ratings = try JSONDecoder.api.decode(ProfileRatings.self, from: Data(Self.payload.utf8)).ratings

        XCTAssertEqual(ratings.rated, 4)
        XCTAssertEqual(ratings.averageStars?.averageText, "3.6")
        // The server sent two of the ten buckets: the rest are empty steps, half a star to five.
        XCTAssertEqual(ProfileSignatureGeometry.counts(ratings), [0, 0, 0, 0, 0, 1, 1, 2, 0, 0])
        XCTAssertTrue(ProfileSignatureGeometry.shows(ratings))
        XCTAssertFalse(ProfileSignatureGeometry.shows(StatsRatings(averageStars: nil, rated: 0, distribution: [])))
    }

    func testTheBusiestStepReachesTheTopAndAnEmptyOneSitsOnTheFloor() {
        let ys = ProfileSignatureGeometry.ys(counts: [0, 0, 0, 0, 0, 1, 1, 2, 0, 0])

        XCTAssertEqual(ys[7], ProfileSignatureGeometry.floorY - ProfileSignatureGeometry.amplitude)
        XCTAssertEqual(ys[6], ProfileSignatureGeometry.floorY - ProfileSignatureGeometry.amplitude / 2)
        XCTAssertEqual(ys[0], ProfileSignatureGeometry.floorY)
        XCTAssertEqual(ys[9], ProfileSignatureGeometry.floorY)
        // The line spans the whole box, half a star at its left edge, five stars at its right.
        let bounds = ProfileSignatureGeometry.path(counts: [1, 0, 0, 0, 0, 0, 0, 0, 0, 1]).boundingRect
        XCTAssertEqual(bounds.minX, 0, accuracy: 0.001)
        XCTAssertEqual(bounds.maxX, ProfileSignatureGeometry.size.width, accuracy: 0.001)
    }

    /// theo's four ratings, as the server sends them: only the buckets it has.
    private static let payload = """
    {
      "range": {"start_date": null, "end_date": null, "timezone": "America/New_York", "is_all_time": true},
      "audience": "public",
      "ratings": {
        "average_stars": "3.63",
        "rated": 4,
        "distribution": [
          {"stars": "3.0", "count": 1, "rating_min": "5.5", "rating_max": "6.4", "by_type": {"movie": 1}},
          {"stars": "3.5", "count": 1, "rating_min": "6.5", "rating_max": "7.4", "by_type": {"movie": 1}},
          {"stars": "4.0", "count": 2, "rating_min": "7.5", "rating_max": "8.4", "by_type": {"movie": 2}}
        ]
      }
    }
    """
}
