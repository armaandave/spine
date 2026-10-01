import Foundation
import XCTest
@testable import Spine

/// What `APIImportRepository` puts on the wire for a MyAnimeList export, against the fake server in
/// `RefreshTestSupport.swift`.
@MainActor
final class MyAnimeListImportRepositoryTests: RefreshTestCase {
    private static let path = "/api/v1/imports/mal_export"

    func testGzippedExportIsPostedToTheMyAnimeListEndpointAsIs() async throws {
        signIn(accessExpired: false)
        replyQueued(taskId: "mal-task-1")
        let gzipBytes = Data([0x1F, 0x8B, 0x08, 0x00, 0x01, 0x02])

        let response = try await APIImportRepository(client: makeClient()).queueMyAnimeListImport(
            fileData: gzipBytes,
            fileName: "animelist_1727800000_-_12345.xml.gz",
            mode: .overwrite,
            progressHandler: nil
        )

        XCTAssertEqual(response, ImportQueueResponse(taskId: "mal-task-1", status: "queued"))
        let requests = backend.requests(path: Self.path)
        XCTAssertEqual(requests.count, 1)
        let request = try XCTUnwrap(requests.first)
        XCTAssertEqual(request.method, "POST")
        XCTAssertEqual(request.bearer, "access-0")

        let body = request.body
        XCTAssertTrue(body.contains(text: "Content-Disposition: form-data; name=\"mode\"\r\n\r\noverwrite\r\n"))
        XCTAssertTrue(body.contains(text: "Content-Disposition: form-data; name=\"file\"; filename=\"animelist_1727800000_-_12345.xml.gz\""))
        XCTAssertTrue(body.contains(text: "Content-Type: application/gzip\r\n\r\n"))
        XCTAssertNotNil(body.range(of: gzipBytes), "the gzip bytes go up untouched")
    }

    func testUnzippedExportIsPostedAsXML() async throws {
        signIn(accessExpired: false)
        replyQueued(taskId: "mal-task-2")
        let xml = Data("<?xml version=\"1.0\"?><myanimelist></myanimelist>".utf8)

        let response = try await APIImportRepository(client: makeClient()).queueMyAnimeListImport(
            fileData: xml,
            fileName: "mangalist.xml",
            mode: .new,
            progressHandler: nil
        )

        XCTAssertEqual(response.taskId, "mal-task-2")
        let body = try XCTUnwrap(backend.requests(path: Self.path).first).body
        XCTAssertTrue(body.contains(text: "Content-Disposition: form-data; name=\"mode\"\r\n\r\nnew\r\n"))
        XCTAssertTrue(body.contains(text: "filename=\"mangalist.xml\""))
        XCTAssertTrue(body.contains(text: "Content-Type: application/xml\r\n\r\n"))
        XCTAssertNotNil(body.range(of: xml))
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
