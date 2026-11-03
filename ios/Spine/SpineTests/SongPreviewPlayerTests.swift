import AVFoundation
import XCTest
@testable import Spine

/// The preview player's states, from local files: nothing here reaches the network, and the audio is silence.
@MainActor
final class SongPreviewPlayerTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { [directory] in
            try? FileManager.default.removeItem(at: directory!)
        }
    }

    func testTappingAgainStopsAndAnotherPreviewReplacesTheFirst() {
        let player = SongPreviewPlayer()
        let first = directory.appendingPathComponent("first.m4a")
        let second = directory.appendingPathComponent("second.m4a")

        player.toggle(first)
        XCTAssertEqual(player.currentURL, first)
        XCTAssertTrue(player.isLoading)

        player.toggle(second)
        XCTAssertEqual(player.currentURL, second, "One at a time")

        player.toggle(second)
        XCTAssertNil(player.currentURL)
        XCTAssertFalse(player.isLoading)
    }

    func testClosingThePageStopsAPreviewThatIsStillLoading() {
        let player = SongPreviewPlayer()
        player.toggle(directory.appendingPathComponent("loading.m4a"))

        player.close()

        XCTAssertNil(player.currentURL)
        XCTAssertFalse(player.isLoading)
    }

    func testAPreviewThatWontPlayLosesItsButtonUntilThePageOpensAgain() async throws {
        let player = SongPreviewPlayer()
        let missing = directory.appendingPathComponent("missing.m4a")

        player.toggle(missing)
        try await waitUntil { player.failedURL == missing }

        XCTAssertNil(player.currentURL)
        player.close()
        XCTAssertNil(player.failedURL)
    }

    func testAPreviewPlaysToItsEndAndGoesBackToPlay() async throws {
        let player = SongPreviewPlayer()
        // Long enough that a busy Mac can't skip past the playing state between two looks.
        let silence = try writeSilence(named: "silence.caf", seconds: 3)

        player.toggle(silence)
        try await waitUntil { player.currentURL == silence && !player.isLoading }
        try await waitUntil { player.currentURL == nil }

        XCTAssertNil(player.failedURL, "Reaching the end isn't a failure")
    }

    private func waitUntil(timeout: Duration = .seconds(20), _ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + timeout
        while !condition() {
            guard ContinuousClock.now < deadline else {
                return XCTFail("Timed out")
            }
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    private func writeSilence(named name: String, seconds: Double) throws -> URL {
        let url = directory.appendingPathComponent(name)
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1))
        let frames = AVAudioFrameCount(format.sampleRate * seconds)
        let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames))
        buffer.frameLength = frames
        try XCTUnwrap(buffer.floatChannelData)[0].update(repeating: 0, count: Int(frames))
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buffer)
        file.close()
        return url
    }
}
