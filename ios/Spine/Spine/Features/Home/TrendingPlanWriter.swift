import Foundation
import Observation

/// The Trending tile's "Plan to watch": puts a title in Planning exactly as the title's own page does. A show goes
/// through the TV action, an anime through the anime action (TV's route with no season), and every other type through
/// a plain status update.
///
/// The title shows as planned at once (`plannedIDs`, which the tile reads) and goes back, with `errorMessage`, if the
/// write fails. It posts `MediaStateChange` so the rest of the app reloads. It records no activity: the server records
/// the add itself.
@MainActor
@Observable
final class TrendingPlanWriter {
    /// `TrendingTitle.id`s put in Planning from the tile, in this session.
    private(set) var plannedIDs: Set<String> = []
    /// Why the last write failed. The tile shows it, and clears it with `dismissError()`.
    private(set) var errorMessage: String?

    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let changeOrigin: UUID?
    @ObservationIgnored private let onUnauthorized: () -> Void

    init(
        trackingRepository: TrackingRepository,
        changeOrigin: UUID? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.trackingRepository = trackingRepository
        self.changeOrigin = changeOrigin
        self.onUnauthorized = onUnauthorized
    }

    func isPlanned(_ title: TrendingTitle) -> Bool {
        plannedIDs.contains(title.id)
    }

    func dismissError() {
        errorMessage = nil
    }

    /// Marks the title planned now and writes it. The task's value is whether the server took it; a title already
    /// planned (or being planned) sends nothing and answers true.
    @discardableResult
    func plan(_ title: TrendingTitle) -> Task<Bool, Never> {
        guard plannedIDs.insert(title.id).inserted else { return Task { true } }
        errorMessage = nil
        return Task { [self] in
            do {
                try await write(title.media.ref)
                MediaStateChange.post(ref: title.media.ref, origin: changeOrigin)
                return true
            } catch {
                plannedIDs.remove(title.id)
                if case APIError.unauthorized = error { onUnauthorized() }
                errorMessage = error.localizedDescription
                return false
            }
        }
    }

    private func write(_ ref: MediaRef) async throws {
        switch ref.mediaType {
        case "tv":
            _ = try await trackingRepository.performTVAction(
                source: ref.source, mediaId: ref.mediaId, action: .planning, request: TVActionRequest(mutationId: UUID())
            )
        case "anime":
            _ = try await trackingRepository.performAnimeAction(
                source: ref.source, mediaId: ref.mediaId, action: .planning, request: TVActionRequest(mutationId: UUID())
            )
        default:
            _ = try await trackingRepository.update(ref: ref, request: TrackingWriteRequest(status: "Planning"))
        }
    }
}
