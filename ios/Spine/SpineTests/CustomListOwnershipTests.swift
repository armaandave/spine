import Foundation
@testable import Spine
import XCTest

// Edit List and Delete List show only to the list's owner.
@MainActor
final class CustomListOwnershipTests: XCTestCase {
    func testOnlyTheOwnerOwnsAList() throws {
        let list = try JSONDecoder.api.decode(CustomListDetail.self, from: Data("""
        {"id": 4, "name": "Top 500", "slug": "top-500", "description": "", "visibility": "public",
         "owner": {"id": 99, "username": "Spine", "display_name": "Spine", "avatar_url": null},
         "image_url": null, "items_count": 0, "updated_at": null, "like_count": 0, "items": []}
        """.utf8))

        XCTAssertTrue(list.isOwned(by: 99))
        XCTAssertFalse(list.isOwned(by: 1))
        XCTAssertFalse(list.isOwned(by: nil))
    }
}
