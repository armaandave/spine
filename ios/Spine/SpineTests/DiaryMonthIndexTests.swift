import XCTest
@testable import Spine

final class DiaryMonthIndexTests: XCTestCase {
    func testGroupsMonthsByYearNewestFirst() {
        let groups = DiaryYearGroup.groups(from: [
            month("2026-10", "October 2026"),
            month("2026-09", "September 2026"),
            month("2025-12", "December 2025"),
            month("2025-11", "November 2025"),
        ])

        XCTAssertEqual(groups.map(\.year), ["2026", "2025"])
        XCTAssertEqual(groups.map { $0.sections.map(\.id) }, [["2026-10", "2026-09"], ["2025-12", "2025-11"]])
    }

    func testUndatedLogsGetAGroupWithoutAYear() {
        let groups = DiaryYearGroup.groups(from: [
            month("2026-10", "October 2026"),
            month("undated", "Undated"),
        ])

        XCTAssertEqual(groups.map(\.year), ["2026", nil])
        XCTAssertEqual(groups.last?.sections.map(\.id), ["undated"])
    }

    func testYearHeadingFollowsThePhonesCalendar() {
        XCTAssertEqual(DiaryDateFormatter.year(fromID: "2026-10", calendar: Calendar(identifier: .gregorian)), "2026")
        // The month titles say "October 2569 BE" on a Buddhist calendar, so the year heading must too.
        let buddhist = DiaryDateFormatter.year(fromID: "2026-10", calendar: Calendar(identifier: .buddhist))
        XCTAssertEqual(buddhist?.hasPrefix("2569"), true, buddhist ?? "nil")
        XCTAssertNil(DiaryDateFormatter.year(fromID: "undated"))
    }

    func testMonthNameComesFromTheMonthID() {
        XCTAssertEqual(DiaryDateFormatter.monthName(fromID: "2026-10"), "October")
        XCTAssertEqual(DiaryDateFormatter.monthName(fromID: "2025-02"), "February")
        XCTAssertNil(DiaryDateFormatter.monthName(fromID: "undated"))
    }

    private func month(_ id: String, _ title: String) -> DiaryMonthSection {
        DiaryMonthSection(id: id, title: title, entries: [])
    }
}
