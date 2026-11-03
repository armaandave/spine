import XCTest
@testable import Spine

@MainActor
final class LetterboxdExportTests: XCTestCase {
    func testRecognizesTheExportFile() {
        XCTAssertTrue(LetterboxdExport.isExport(mimeType: "application/zip", contentDisposition: nil))
        XCTAssertTrue(LetterboxdExport.isExport(mimeType: "APPLICATION/X-ZIP-COMPRESSED", contentDisposition: nil))
        XCTAssertTrue(
            LetterboxdExport.isExport(
                mimeType: "application/octet-stream",
                contentDisposition: "attachment; filename=\"letterboxd-someone-2026-11-02.zip\""
            )
        )
    }

    func testPagesAreNotTheExportFile() {
        XCTAssertFalse(LetterboxdExport.isExport(mimeType: "text/html", contentDisposition: nil))
        XCTAssertFalse(LetterboxdExport.isExport(mimeType: nil, contentDisposition: nil))
        XCTAssertFalse(LetterboxdExport.isExport(mimeType: "text/html", contentDisposition: "inline; filename=\"page.zip\""))
        XCTAssertFalse(LetterboxdExport.isExport(mimeType: "application/pdf", contentDisposition: "attachment; filename=\"a.pdf\""))
    }

    func testWaitsUntilSignedInThenAsksForTheExportOnce() {
        XCTAssertEqual(LetterboxdExport.nextStep(isSignedIn: false, hasRequestedExport: false), .wait)
        XCTAssertEqual(LetterboxdExport.nextStep(isSignedIn: true, hasRequestedExport: false), .requestExport)
        XCTAssertEqual(LetterboxdExport.nextStep(isSignedIn: true, hasRequestedExport: true), .fail)
    }

    func testStaysOnLetterboxd() {
        XCTAssertTrue(LetterboxdExport.isLetterboxd(LetterboxdExport.pageURL))
        XCTAssertTrue(LetterboxdExport.isLetterboxd(URL(string: "https://www.letterboxd.com/sign-in/")))
        XCTAssertFalse(LetterboxdExport.isLetterboxd(URL(string: "http://letterboxd.com/")))
        XCTAssertFalse(LetterboxdExport.isLetterboxd(URL(string: "https://letterboxd.com.example.com/")))
        XCTAssertFalse(LetterboxdExport.isLetterboxd(URL(string: "https://notletterboxd.com/")))
        XCTAssertFalse(LetterboxdExport.isLetterboxd(nil))
    }
}
