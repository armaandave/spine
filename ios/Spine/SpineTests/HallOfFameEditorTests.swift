import XCTest
@testable import Spine

@MainActor
final class HallOfFameEditorTests: XCTestCase {
    func testListOrderPutsTheCardAbovePhotoFirstAndRoundTrips() {
        XCTAssertEqual(HallOfFameOrder.listOrder(["a", "b", "c"]), ["a", "b", "c"])
        XCTAssertEqual(HallOfFameOrder.listOrder(["a", "b", "c", "d"]), ["c", "a", "b", "d"])

        for count in 0...HallOfFameOrder.limit {
            let crown = (0..<count).map(String.init)
            XCTAssertEqual(HallOfFameOrder.crownOrder(HallOfFameOrder.listOrder(crown)), crown)
        }
    }

    func testDraggingARowToTheTopPutsItAboveThePhotoAndSavesOnceOnRelease() async {
        let picks = (1...4).map { media($0, "movie") }
        let repository = EditorRepository(catalog: picks)
        let model = editor(picks: picks, repository: repository)

        model.beginReorder()
        model.moveRow(picks[3], to: 2)
        model.moveRow(picks[3], to: 0)

        XCTAssertEqual(model.listRows.first?.id, picks[3].id)
        XCTAssertEqual(slotArrangement(model.visible).above?.id, picks[3].id)
        XCTAssertTrue(repository.savedRefs.isEmpty)

        model.endReorder()
        await model.waitForSaves()

        XCTAssertEqual(repository.savedRefs, [model.picks.map(\.ref)])
    }

    func testPickingUpARowAndPuttingItBackSavesNothing() async {
        let picks = (1...3).map { media($0, "movie") }
        let repository = EditorRepository(catalog: picks)
        let model = editor(picks: picks, repository: repository)

        model.beginReorder()
        model.moveRow(picks[0], to: 2)
        model.moveRow(picks[0], to: 0)
        model.endReorder()
        await model.waitForSaves()

        XCTAssertTrue(repository.savedRefs.isEmpty)
    }

    func testNewPickGoesOnTheRightEndAndTheListStopsAtEight() {
        let picks = (1...3).map { media($0, "movie") }
        let extra = (4...9).map { media($0, "book") }
        let model = editor(picks: picks, repository: EditorRepository(catalog: picks + extra))

        model.add(extra[0])

        XCTAssertEqual(slotArrangement(model.visible).below.last?.id, extra[0].id)

        extra.dropFirst().forEach(model.add)

        XCTAssertEqual(model.picks.count, HallOfFameOrder.limit)
        XCTAssertFalse(model.canAdd)
        XCTAssertFalse(model.picks.contains { $0.id == extra.last?.id })
    }

    func testTurnedOffPicksStayHiddenAndAreSavedWithTheRest() async {
        let movie = media(1, "movie")
        let game = media(2, "game")
        let book = media(3, "book")
        let newMovie = media(4, "movie")
        let repository = EditorRepository(catalog: [movie, game, book, newMovie])
        let model = editor(picks: [movie, game, book], enabledMediaTypes: ["movie", "book"], repository: repository)

        XCTAssertEqual(model.visible.map(\.id), [movie.id, book.id])
        XCTAssertEqual(model.hidden.map(\.id), [game.id])

        model.add(newMovie)
        await model.waitForSaves()

        XCTAssertEqual(repository.savedRefs, [[movie.ref, book.ref, newMovie.ref, game.ref]])
    }

    func testSavesRunOneAtATimeAndSkipOvertakenOnes() async {
        let picks = (1...3).map { media($0, "movie") }
        let repository = EditorRepository(catalog: picks)
        repository.isHolding = true
        let model = editor(picks: [picks[0]], repository: repository)

        model.add(picks[1])
        for _ in 0..<50 where repository.savedRefs.isEmpty {
            await Task.yield()
        }
        model.add(picks[2])
        model.remove(picks[0])
        repository.release()
        await model.waitForSaves()

        XCTAssertEqual(repository.savedRefs, [
            [picks[0].ref, picks[1].ref],
            [picks[1].ref, picks[2].ref],
        ])
        XCTAssertEqual(model.picks.map(\.id), [picks[1].id, picks[2].id])
    }

    func testFailedSavePutsBackTheLastSavedList() async {
        let picks = (1...2).map { media($0, "movie") }
        let repository = EditorRepository(catalog: picks)
        repository.saveError = URLError(.notConnectedToInternet)
        let model = editor(picks: picks, repository: repository)

        model.remove(picks[0])
        await model.waitForSaves()

        XCTAssertEqual(model.picks.map(\.id), picks.map(\.id))
        XCTAssertNotNil(model.errorMessage)
    }

    private func editor(
        picks: [MediaSummary],
        enabledMediaTypes: [String] = ["movie", "book"],
        repository: EditorRepository
    ) -> HallOfFameEditorModel {
        HallOfFameEditorModel(
            picks: picks,
            enabledMediaTypes: enabledMediaTypes,
            repository: repository,
            onSaved: { _ in },
            onUnauthorized: {}
        )
    }

    private func slotArrangement(_ picks: [MediaSummary]) -> HallOfFameCrownArrangement {
        HallOfFameCrownLayout.arrangement(for: ProfileFavorites.slots(from: picks))
    }

    private func media(_ index: Int, _ mediaType: String) -> MediaSummary {
        MediaSummary(
            ref: MediaRef(
                itemId: index,
                source: "test",
                mediaType: mediaType,
                mediaId: "\(index)",
                seasonNumber: nil,
                episodeNumber: nil
            ),
            title: "Favorite \(index)"
        )
    }
}

/// Answers each save with the picks it knows, and can hold saves until released.
private final class EditorRepository: ProfileRepository {
    let catalog: [MediaSummary]
    var saveError: Error?
    var isHolding = false
    private(set) var savedRefs: [[MediaRef]] = []
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(catalog: [MediaSummary]) {
        self.catalog = catalog
    }

    func release() {
        isHolding = false
        waiters.forEach { $0.resume() }
        waiters = []
    }

    func saveHallOfFame(refs: [MediaRef]) async throws -> [MediaSummary] {
        savedRefs.append(refs)
        if isHolding {
            await withCheckedContinuation { waiters.append($0) }
        }
        if let saveError {
            throw saveError
        }
        return refs.compactMap { ref in catalog.first { $0.ref == ref } }
    }

    func me() async throws -> UserProfile { fatalError("Not used") }
    func updateProfile(_ request: ProfileUpdateRequest) async throws -> UserProfile { fatalError("Not used") }
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async throws -> String? { fatalError("Not used") }
    func deleteAvatar() async throws -> String? { fatalError("Not used") }
    func saveProfileBackdrop(ref: MediaRef, backdropURL: String) async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func clearProfileBackdrop() async throws -> ProfileBackdropSaveResponse { fatalError("Not used") }
    func updatePreferences(_ request: PreferencesUpdateRequest) async throws -> UserPreferences { fatalError("Not used") }
    func changePassword(_ request: PasswordChangeRequest) async throws { fatalError("Not used") }
}
