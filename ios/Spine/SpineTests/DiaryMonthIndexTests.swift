import XCTest
@testable import Spine

final class DiaryMonthIndexTests: XCTestCase {
    private let gregorian = Calendar(identifier: .gregorian)

    func testGroupsMonthsByYearNewestFirst() {
        let groups = DiaryYearGroup.groups(from: [
            month("2026-10", "October 2026"),
            month("2026-09", "September 2026"),
            month("2025-12", "December 2025"),
            month("2025-11", "November 2025"),
        ], calendar: gregorian)

        XCTAssertEqual(groups.map(\.year), ["2026", "2025"])
        XCTAssertEqual(groups.map { $0.sections.map(\.id) }, [["2026-10", "2026-09"], ["2025-12", "2025-11"]])
    }

    func testUndatedLogsGetAGroupWithoutAYear() {
        let groups = DiaryYearGroup.groups(from: [
            month("2026-10", "October 2026"),
            month("undated", "Undated"),
        ], calendar: gregorian)

        XCTAssertEqual(groups.map(\.year), ["2026", nil])
        XCTAssertEqual(groups.last?.sections.map(\.id), ["undated"])
    }

    func testYearHeadingFollowsThePhonesCalendar() {
        XCTAssertEqual(DiaryDateFormatter.year(from: "2026-10-01", calendar: gregorian), "2026")
        // The month titles say "October 2569 BE" on a Buddhist calendar, so the year heading must too.
        let buddhist = DiaryDateFormatter.year(from: "2026-10-01", calendar: Calendar(identifier: .buddhist))
        XCTAssertEqual(buddhist?.contains("2569"), true, buddhist ?? "nil")
        XCTAssertNil(DiaryDateFormatter.year(from: nil))
    }

    func testGroupsByTheCalendarsOwnYear() {
        // The Persian year starts in March: October 2026 is in 1405, February 2026 still in 1404.
        let groups = DiaryYearGroup.groups(from: [
            month("2026-10", "October 2026"),
            month("2026-02", "February 2026"),
        ], calendar: Calendar(identifier: .persian))

        XCTAssertEqual(groups.map { $0.sections.map(\.id) }, [["2026-10"], ["2026-02"]])
        XCTAssertEqual(groups.first?.year?.contains("1405"), true, groups.first?.year ?? "nil")
        XCTAssertEqual(groups.last?.year?.contains("1404"), true, groups.last?.year ?? "nil")
    }

    func testMonthNameComesFromTheTitleDate() {
        XCTAssertEqual(DiaryDateFormatter.monthName(from: "2026-10-19T20:00:00Z"), "October")
        XCTAssertEqual(month("2025-02", "February 2025").titleDate, "2025-02-01")
        XCTAssertEqual(DiaryDateFormatter.monthName(from: "2025-02-01"), "February")
        XCTAssertNil(month("undated", "Undated").titleDate)
    }

    private func month(_ id: String, _ title: String) -> DiaryMonthSection {
        DiaryMonthSection(id: id, title: title, entries: [])
    }
}
