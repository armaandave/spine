import Foundation
import XCTest
@testable import Spine

@MainActor
final class MediaRatingPickerStateTests: XCTestCase {
    func testDraftDismissalAndLocalConfirmation() {
        var state = MediaRatingPickerState()

        state.open()
        XCTAssertTrue(state.isPresented)
        XCTAssertTrue(state.hasLocallyWatched)
        XCTAssertFalse(state.showsConfirm)

        state.draftHalfSteps = 7
        XCTAssertTrue(state.showsConfirm)
        XCTAssertTrue(state.isPresented)
        XCTAssertEqual(state.confirmedHalfSteps, 0)
        state.draftHalfSteps = 0
        XCTAssertFalse(state.showsConfirm)

        state.draftHalfSteps = 7
        state.dismiss()
        XCTAssertFalse(state.isPresented)
        XCTAssertEqual(state.draftHalfSteps, 0)

        state.open()
        state.draftHalfSteps = 7
        state.confirm()
        XCTAssertEqual(state.confirmedHalfSteps, 7)

        state.open()
        XCTAssertEqual(state.draftHalfSteps, 7)
        state.draftHalfSteps = 0
        XCTAssertTrue(state.showsConfirm)
        state.dismiss()
        XCTAssertEqual(state.draftHalfSteps, 7)
    }

    func testRatingOnlyDraftDoesNotImplyConsumptionOrChangeConfirmedOpinion() {
        var state = MediaRatingPickerState()
        state.syncConfirmed(7)
        state.open(markLocallyWatched: false)
        state.draftHalfSteps = 9
        XCTAssertFalse(state.hasLocallyWatched)
        state.dismiss()
        XCTAssertFalse(state.hasLocallyWatched)
        XCTAssertEqual(state.confirmedHalfSteps, 7)
        XCTAssertEqual(state.draftHalfSteps, 7)
    }

    func testSuccessfulUnwatchClearsOptimisticEyeAndRatingForNextToggle() {
        var state = MediaRatingPickerState()

        state.open()
        state.draftHalfSteps = 9
        state.confirm()
        XCTAssertTrue(state.hasLocallyWatched)
        XCTAssertEqual(state.confirmedHalfSteps, 9)

        state.resetAfterUnwatch()
        XCTAssertFalse(state.hasLocallyWatched)
        XCTAssertFalse(state.isPresented)
        XCTAssertEqual(state.confirmedHalfSteps, 0)

        state.open()
        XCTAssertTrue(state.hasLocallyWatched)
        XCTAssertTrue(state.isPresented)
    }

    func testSingleWeightRequestsEncodeDateOnlyWireRatingAndExplicitClears() throws {
        let ref = MediaRef(
            itemId: 1,
            source: "tmdb",
            mediaType: "movie",
            mediaId: "550",
            seasonNumber: nil,
            episodeNumber: nil
        )
        let consumedAt = Date(timeIntervalSince1970: 1_768_435_200)
        let create = DiaryEntryWriteRequest(
            ref: ref,
            consumedAt: consumedAt,
            rating: Decimal(string: "4.5"),
            review: "",
            reviewTitle: "",
            liked: true,
            isRewatch: false,
            autoMarkConsumed: true,
            containsSpoilers: false,
            visibility: "public",
            tags: []
        )
        let createJSON = try jsonObject(create)

        XCTAssertEqual(createJSON["consumed_at"] as? String, CalendarDateCodec.string(from: consumedAt))
        XCTAssertEqual(createJSON["rating"] as? Double, 4.5)

        let trackingClear = try jsonObject(TrackingWriteRequest(rating: nil, includesRating: true))
        XCTAssertTrue(trackingClear["rating"] is NSNull)

        let diaryClear = try jsonObject(DiaryEntryUpdateRequest(
            consumedAt: consumedAt,
            rating: nil,
            review: nil,
            reviewTitle: nil,
            tags: nil,
            liked: nil,
            isRewatch: nil,
            containsSpoilers: nil,
            visibility: nil,
            calendarDateOnly: true,
            includesRating: true
        ))
        XCTAssertTrue(diaryClear["rating"] is NSNull)
        XCTAssertEqual(diaryClear["consumed_at"] as? String, CalendarDateCodec.string(from: consumedAt))
    }

    func testSingleWeightCalendarDateFutureValidationAndLanguage() {
        let tomorrow = Calendar.autoupdatingCurrent.date(byAdding: .day, value: 1, to: Date())!
        XCTAssertTrue(CalendarDateCodec.isFuture(tomorrow))
        XCTAssertEqual(mediaRef("movie").consumedDateLabel, "Date watched")
        XCTAssertEqual(mediaRef("music").consumedDateLabel, "Date listened")
        XCTAssertEqual(mediaRef("music").repeatLabel, "Relisten")
    }

    private func jsonObject<T: Encodable>(_ value: T) throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: JSONEncoder.api.encode(value)) as! [String: Any]
    }

    private func mediaRef(_ mediaType: String) -> MediaRef {
        MediaRef(
            itemId: nil,
            source: "manual",
            mediaType: mediaType,
            mediaId: mediaType,
            seasonNumber: nil,
            episodeNumber: nil
        )
    }
}
