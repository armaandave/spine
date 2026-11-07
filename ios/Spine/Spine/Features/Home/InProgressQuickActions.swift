import Foundation
import Observation

/// Performs the In progress tile's main button for the titles that count in pages, percent, chapters or hours. A show's
/// or anime's tick is `ContinueWatchingTicks.quickTick`, not this.
///
/// Every write sends an absolute value worked out from the card's current state (212 pages + 20 = 232), never a
/// delta and never a value at or above a known end: reaching the end is the progress sheet's to do, so a plan that
/// would get there writes nothing and answers `.openSheet`. One write runs at a time. A failure leaves the card as it
/// was; a success replaces the card's tracking with the server's answer and posts `MediaStateChange` tagged with
/// `changeOrigin`, so Home skips its own echo.
@MainActor
@Observable
final class InProgressQuickActions {
    enum Result: Equatable {
        /// Written (or nothing to do).
        case done
        /// Nothing was written, or the write reached the end: the caller opens the title's progress UI
        /// (`InProgressUpdates.updateProgress(for:)`).
        case openSheet
        /// The write failed; the card is unchanged.
        case failed(String)
    }

    /// The card whose write is on its way, for the tile's button state.
    private(set) var busyItemID: String?

    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let changeOrigin: UUID
    @ObservationIgnored private let onUpdate: (_ itemID: String, _ tracking: TrackingState) -> Void
    @ObservationIgnored private let onUnauthorized: () -> Void
    @ObservationIgnored private let today: () -> Date

    init(
        trackingRepository: TrackingRepository,
        changeOrigin: UUID,
        onUpdate: @escaping (_ itemID: String, _ tracking: TrackingState) -> Void,
        onUnauthorized: @escaping () -> Void,
        today: @escaping () -> Date = { Date() }
    ) {
        self.trackingRepository = trackingRepository
        self.changeOrigin = changeOrigin
        self.onUpdate = onUpdate
        self.onUnauthorized = onUnauthorized
        self.today = today
    }

    func run(_ plan: QuickActionPlan, on item: LibraryItem) async -> Result {
        guard busyItemID == nil else { return .done }
        guard let write = write(for: plan, item: item) else { return .openSheet }
        busyItemID = item.id
        defer { busyItemID = nil }
        let ref = item.media.ref
        do {
            let response = try await write()
            let tracking = patched(response, plan: plan, position: InProgressTilePresenter.position(for: item))
            onUpdate(item.id, tracking)
            MediaStateChange.post(ref: ref, origin: changeOrigin)
            // The server says this save reached a manga's or comic's total: its completion log follows.
            if ref.isSeries, tracking.reading?.opensLog == true { return .openSheet }
            return .done
        } catch {
            if case APIError.unauthorized = error { onUnauthorized() }
            return .failed(error.localizedDescription)
        }
    }

    // MARK: The writes

    /// The call this plan makes for this card, or nil when it writes nothing: the plan opens the sheet, the card
    /// no longer matches the plan, or the new value would reach or pass a known end.
    private func write(for plan: QuickActionPlan, item: LibraryItem) -> (() async throws -> TrackingState)? {
        let ref = item.media.ref
        guard let position = InProgressTilePresenter.position(for: item) else { return nil }
        switch plan {
        case .tickUpNext, .openSheet:
            return nil
        case let .addPages(count):
            guard ref.mediaType == "book", position.unit == .pages,
                  let value = Self.value(position.current + Double(count), below: position.total) else { return nil }
            let decimal = Decimal(Int(value.rounded()))
            return { [trackingRepository] in
                try await trackingRepository.updateBookProgress(
                    source: ref.source, mediaId: ref.mediaId, progressType: ProgressUpdateMode.pages.apiValue,
                    value: decimal, notes: ""
                )
            }
        case let .addPercent(count):
            guard position.unit == .percent,
                  let value = Self.value(position.current + Double(count), below: position.total ?? 100) else { return nil }
            let percent = Self.percent(value)
            if ref.mediaType == "game" {
                guard let playthrough = item.tracking.game?.currentPlaythrough else { return nil }
                let request = GamePlaythroughWriteRequest(
                    percentage: percent, includesPercentage: true, progressedOn: CalendarDateCodec.string(from: today())
                )
                return { [trackingRepository] in
                    try await trackingRepository.updateGamePlaythrough(
                        ref: ref, playthroughId: playthrough.id, request: request
                    )
                }
            }
            guard ref.mediaType == "book" else { return nil }
            return { [trackingRepository] in
                try await trackingRepository.updateBookProgress(
                    source: ref.source, mediaId: ref.mediaId, progressType: ProgressUpdateMode.percentage.apiValue,
                    value: percent, notes: ""
                )
            }
        case let .addHours(count):
            guard ref.mediaType == "game", position.unit == .minutes,
                  let playthrough = item.tracking.game?.currentPlaythrough else { return nil }
            let request = GamePlaythroughWriteRequest(
                totalMinutes: (playthrough.totalMinutes ?? 0) + count * 60, includesMinutes: true,
                progressedOn: CalendarDateCodec.string(from: today())
            )
            return { [trackingRepository] in
                try await trackingRepository.updateGamePlaythrough(
                    ref: ref, playthroughId: playthrough.id, request: request
                )
            }
        case let .addChapters(count, _):
            guard ref.isSeries, position.unit == .chapters || position.unit == .issues,
                  let value = Self.value(position.current + Double(count), below: position.total) else { return nil }
            let number = Int(value.rounded())
            return { [trackingRepository] in
                try await trackingRepository.updateSeriesProgress(ref: ref, value: number, mutationId: UUID())
            }
        }
    }

    /// `value`, unless it reaches or passes a known `total`.
    private static func value(_ value: Double, below total: Double?) -> Double? {
        if let total, value >= total { return nil }
        return value
    }

    /// A percent to the two decimals the server keeps, without the noise a Double adds.
    private static func percent(_ value: Double) -> Decimal {
        Decimal(string: String(format: "%.2f", value)) ?? Decimal(Int(value.rounded()))
    }

    // MARK: The card afterwards

    /// The server's answer, with a book's progress in the unit it was written in, as the title's own page does.
    private func patched(_ response: TrackingState, plan: QuickActionPlan, position: TilePosition?) -> TrackingState {
        guard let position else { return response }
        switch plan {
        case let .addPages(count):
            let value = Decimal(Int((position.current + Double(count)).rounded()))
            let max = position.pagesTotal.map { Decimal(Int($0.rounded())) } ?? response.progress?.max
            return response.replacingProgress(ProgressState(kind: "pages", value: value, max: max, unit: "page"))
        case let .addPercent(count) where response.book != nil:
            let value = Self.percent(position.current + Double(count))
            return response.replacingProgress(ProgressState(kind: "percentage", value: value, max: 100, unit: "percent"))
        default:
            return response
        }
    }
}
