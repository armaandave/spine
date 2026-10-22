import Foundation
@testable import Spine
import XCTest

// The list page's Edit List and Delete List follow the server's permissions.
@MainActor
final class CustomListOwnershipTests: XCTestCase {
    func testOwnerCanEditAndDelete() throws {
        let list = try decodeList()

        XCTAssertTrue(list.canEdit(by: 99))
        XCTAssertTrue(list.canDelete(by: 99))
    }

    func testCollaboratorCanEditButNotDelete() throws {
        let list = try decodeList()

        XCTAssertEqual(list.collaborators.map(\.id), [7])
        XCTAssertTrue(list.canEdit(by: 7))
        XCTAssertFalse(list.canDelete(by: 7))
    }

    func testOtherUsersAndSignedOutCanDoNeither() throws {
        let list = try decodeList()

        XCTAssertFalse(list.canEdit(by: 1))
        XCTAssertFalse(list.canDelete(by: 1))
        XCTAssertFalse(list.canEdit(by: nil))
        XCTAssertFalse(list.canDelete(by: nil))
    }

    func testMissingOrMalformedCollaboratorsMeanNone() throws {
        for collaborators in ["", #", "collaborators": null"#, #", "collaborators": "oops""#] {
            let list = try decodeList(collaboratorsJSON: collaborators)
            XCTAssertEqual(list.collaborators, [])
            XCTAssertFalse(list.canEdit(by: 7))
        }
    }

    private func decodeList(
        collaboratorsJSON: String = #", "collaborators": [{"id": 7, "username": "friend", "display_name": "Friend", "avatar_url": null}]"#
    ) throws -> CustomListDetail {
        try JSONDecoder.api.decode(CustomListDetail.self, from: Data("""
        {"id": 4, "name": "Top 500", "slug": "top-500", "description": "", "visibility": "public",
         "owner": {"id": 99, "username": "Spine", "display_name": "Spine", "avatar_url": null},
         "image_url": null, "items_count": 0, "updated_at": null, "like_count": 0, "items": []\(collaboratorsJSON)}
        """.utf8))
    }
}
