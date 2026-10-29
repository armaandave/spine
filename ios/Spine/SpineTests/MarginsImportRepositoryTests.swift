import Foundation
import XCTest
@testable import Spine

/// What `APIImportRepository` puts on the wire for a Margins export, against the fake server in
/// `RefreshTestSupport.swift`.
@MainActor
final class MarginsImportRepositoryTests: RefreshTestCase {
    private static let path = "/api/v1/imports/margins"

    func testCSVExportIsPostedToTheMarginsEndpointWithItsMode() async throws {
        signIn(accessExpired: false)
        replyQueued(taskId: "margins-task-1")
        let csv = Data("Title,Author\nPiranesi,Susanna Clarke\n".utf8)

        let response = try await APIImportRepository(client: makeClient()).queueMarginsImport(
            fileData: csv,
            fileName: "margins-reading-2026-10-28.csv",
            mode: .overwrite,
            progressHandler: nil
        )

        XCTAssertEqual(response, ImportQueueResponse(taskId: "margins-task-1", status: "queued"))
        let requests = backend.requests(path: Self.path)
        XCTAssertEqual(requests.count, 1)
        let request = try XCTUnwrap(requests.first)
        XCTAssertEqual(request.method, "POST")
        XCTAssertEqual(request.bearer, "access-0")

        let body = request.body
        XCTAssertTrue(body.contains(text: "Content-Disposition: form-data; name=\"mode\"\r\n\r\noverwrite\r\n"))
        XCTAssertTrue(body.contains(text: "Content-Disposition: form-data; name=\"file\"; filename=\"margins-reading-2026-10-28.csv\""))
        XCTAssertTrue(body.contains(text: "Content-Type: text/csv\r\n\r\n"))
        XCTAssertNotNil(body.range(of: csv), "the CSV bytes go up untouched")
    }

    /// The 202 the endpoint answers with, in the same shape as the other imports.
    private func replyQueued(taskId: String) {
        let path = Self.path
        backend.setOverride { request in
            guard request.path == path else { return nil }
            return .json(#"{"task_id":"\#(taskId)","status":"queued"}"#, status: 202)
        }
    }
}

private extension Data {
    func contains(text: String) -> Bool {
        range(of: Data(text.utf8)) != nil
    }
}
