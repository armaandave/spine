import XCTest
@testable import Spine

/// What build 61 decodes stays decodable after the preparatory ship
/// (`src/api/tests/test_activity_wire_compat.py` pins the same shapes on the server).
final class ActivityWireCompatTests: XCTestCase {
    private func decode<Value: Decodable>(_ type: Value.Type, _ json: String) throws -> Value {
        try JSONDecoder.api.decode(type, from: Data(json.utf8))
    }

    private let progressRow = """
    {
      "id": -9, "type": "progress_updated", "created_at": "2026-10-25T16:00:00Z", "consumed_at": "2026-10-25",
      "actor": {"id": 3, "username": "reader", "display_name": "Reader", "avatar_url": null},
      "media": {"ref": {"source": "manual", "media_type": "book", "media_id": "1"}, "title": "Dune"},
      "person": null,
      "object": {"type": "progress_day", "id": 9, "summary": "read 40 pages", "label": "40 pages"},
      "viewer": {"can_view": true, "has_liked": false},
      "like_count": 0, "comment_count": 0
    }
    """

    func testAProgressRowDecodesAsBuild61ReadsIt() throws {
        let row = try decode(ActivityItem.self, progressRow)
        XCTAssertEqual(row.id, -9)
        XCTAssertEqual(row.type, "progress_updated")
    }

    /// A rollback's code can hand a status row over as the latest change.
    func testAStatusRowDecodesAndSaysNothing() throws {
        let status = """
        {"kind": "status", "value": 0, "max": null, "unit": "status", "move": "paused", "journey_id": 1}
        """
        let change = try decode(
            ProgressChangeState.self,
            """
            {"id": 12, "previous": \(status), "current": \(status), "created_at": "2026-10-25T16:00:00Z"}
            """
        )
        XCTAssertEqual(change.current.kind, "status")
        XCTAssertEqual(change.current.unit, "status")
        XCTAssertNil(change.compactDisplayParts(preferredMode: nil))
    }
}
