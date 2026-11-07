import XCTest
@testable import Spine

final class PerfCountersTests: XCTestCase {
    func testBumpIsNoOpWhileInactive() {
        let store = PerfCounterStore()
        XCTAssertFalse(store.isActive)
        store.bump(.homeBody)
        store.bump(.feedCardBody)
        XCTAssertTrue(store.snapshotChanged().isEmpty)
    }

    func testCountsWhileActive() {
        let store = PerfCounterStore()
        store.setActive(true)
        store.bump(.feedCardBody)
        store.bump(.feedCardBody)
        store.bump(.tilesBody)
        let snapshot = store.snapshotChanged()
        XCTAssertEqual(snapshot.map(\.counter), [.feedCardBody, .tilesBody])
        XCTAssertEqual(snapshot.map(\.value), [2, 1])
    }

    func testBumpsStopCountingWhenDeactivated() {
        let store = PerfCounterStore()
        store.setActive(true)
        store.bump(.glowBody)
        store.setActive(false)
        store.bump(.glowBody)
        XCTAssertEqual(store.snapshotChanged().map(\.value), [1])
    }

    func testSnapshotReportsOnlyChangedCountersAndCumulativeValues() {
        let store = PerfCounterStore()
        store.setActive(true)
        store.bump(.homeBody)
        store.bump(.feedCardAppear)
        XCTAssertEqual(store.snapshotChanged().count, 2)
        XCTAssertTrue(store.snapshotChanged().isEmpty)

        store.bump(.feedCardAppear)
        let snapshot = store.snapshotChanged()
        XCTAssertEqual(snapshot.map(\.counter), [.feedCardAppear])
        XCTAssertEqual(snapshot.map(\.value), [2])
    }

    func testSharedFacadeDoesNothingUnlessProbeIsActive() {
        XCTAssertFalse(PerfCounterStore.shared.isActive)
        PerfCounters.bump(.homeBody)
        XCTAssertTrue(PerfCounterStore.shared.snapshotChanged().isEmpty)
    }

    func testRawNamesAreStable() {
        XCTAssertEqual(
            PerfCounter.allCases.map(\.rawValue),
            ["homeBody", "feedCardBody", "feedCardAppear", "tilesBody", "glowBody"]
        )
    }

    func testCounterLineFormat() {
        let line = PerfCounterStore.line(pid: 42, epoch: 1_700_000_000.1234, counter: .feedCardBody, value: 17)
        XCTAssertEqual(line, "C 42 1700000000.123 feedCardBody 17\n")
    }
}
