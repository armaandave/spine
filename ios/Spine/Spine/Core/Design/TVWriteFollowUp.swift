import SwiftUI

/// The season-finish composer a follow-up opens: what the write finished, and the detail the composer shows.
struct TVFollowUpComposer: Identifiable {
    let id = UUID()
    let finished: TVFinished
    let detail: MediaDetail
}

/// What happens after a TV write, log or heart. It asks the earlier-episodes question first (TV-107), then opens
/// the season-finish composer once for the season the write finished (TV-403).
///
/// - Yes sends `mark_earlier` with a mutation ID and `offer_season_number`. No, or dismissing, sends nothing.
/// - Whatever the answer, the composer opens for the season the first response named. A Yes can also name the
///   write's own season (`offer_season_number`); any other season only the Yes finished gets no composer.
@MainActor
@Observable
final class TVWriteFollowUp {
    let showRef: MediaRef
    /// The earlier-episodes question waiting for an answer.
    private(set) var question: TVEarlierUnwatched?
    /// The season-finish composer to present.
    private(set) var composer: TVFollowUpComposer?
    private(set) var isSending = false
    var errorMessage: String?
    /// Receives the Yes response, so the presenting screen can fold it in and refresh.
    @ObservationIgnored var onResponse: ((TVActionResponse) -> Void)?
    /// The last question shown, so the alert keeps its title while it animates away.
    @ObservationIgnored private(set) var lastQuestionText = ""
    /// The detail load in flight, if any.
    @ObservationIgnored private(set) var loading: Task<Void, Never>?

    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private var pendingFinished: TVFinished?
    @ObservationIgnored private var generation = 0

    /// `showRef` may be the show or one of its seasons or episodes; actions go to the show.
    init(showRef: MediaRef, trackingRepository: TrackingRepository, mediaRepository: MediaRepository) {
        self.showRef = showRef.parentTVRef ?? showRef
        self.trackingRepository = trackingRepository
        self.mediaRepository = mediaRepository
    }

    /// Call after every TV write, log or heart with its envelope.
    func handle(_ envelope: TVWriteEnvelope) {
        if question != nil {
            // The question is still up: keep it, and offer a season this write finished once it is answered.
            if pendingFinished == nil { pendingFinished = envelope.finished }
            return
        }
        if let earlier = envelope.earlierUnwatched {
            generation += 1
            lastQuestionText = earlier.question
            question = earlier
            pendingFinished = envelope.finished
            return
        }
        guard let finished = envelope.finished else { return }
        generation += 1
        let current = generation
        loading = Task { await present(finished, generation: current) }
    }

    /// Answers the question and waits for everything it starts.
    func answer(yes: Bool) async {
        guard let taken = takeQuestion() else { return }
        await resolve(taken, yes: yes)
    }

    /// Answers the question from a button: takes it at once, so a second tap or the alert's dismissal can't answer
    /// it again.
    func respond(yes: Bool) {
        guard let taken = takeQuestion() else { return }
        loading = Task { await resolve(taken, yes: yes) }
    }

    /// The composer closed, saved or not ("Not now" saves nothing, SW-301).
    func composerDismissed() {
        composer = nil
    }

    private struct TakenQuestion {
        let question: TVEarlierUnwatched
        let firstFinished: TVFinished?
        let generation: Int
    }

    private func takeQuestion() -> TakenQuestion? {
        guard let question else { return nil }
        self.question = nil
        let firstFinished = pendingFinished
        pendingFinished = nil
        generation += 1
        return TakenQuestion(question: question, firstFinished: firstFinished, generation: generation)
    }

    private func resolve(_ taken: TakenQuestion, yes: Bool) async {
        var offer = taken.firstFinished
        if yes {
            isSending = true
            let request = TVActionRequest(
                mutationId: UUID(),
                from: taken.question.from,
                to: taken.question.to,
                offerSeasonNumber: taken.question.offerSeasonNumber
            )
            do {
                let response = try await trackingRepository.performTVAction(
                    source: showRef.source,
                    mediaId: showRef.mediaId,
                    action: .markEarlier,
                    request: request
                )
                onResponse?(response)
                if offer == nil,
                   let offered = taken.question.offerSeasonNumber,
                   let finished = response.finished,
                   finished.seasonNumber == offered {
                    offer = finished
                }
            } catch {
                errorMessage = (error as? APIError)?.apiErrorMessage ?? error.localizedDescription
            }
            isSending = false
        }
        guard let offer, taken.generation == generation else { return }
        await present(offer, generation: taken.generation)
    }

    private func present(_ finished: TVFinished, generation current: Int) async {
        let ref = finished.seasonNumber.map { seasonNumber in
            MediaRef(itemId: nil, source: showRef.source, mediaType: "season", mediaId: showRef.mediaId,
                     seasonNumber: seasonNumber, episodeNumber: nil)
        } ?? showRef
        do {
            let detail = try await mediaRepository.detail(ref: ref)
            guard current == generation, question == nil else { return }
            composer = TVFollowUpComposer(finished: finished, detail: detail)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = (error as? APIError)?.apiErrorMessage ?? error.localizedDescription
        }
    }
}

extension View {
    /// Presents a `TVWriteFollowUp`: the earlier-episodes question as a native alert, then the caller's composer
    /// (normally `MediaLogView(..., tvComposer: item.finished.composer)`) as a full-screen cover.
    func tvWriteFollowUp<Composer: View>(
        _ followUp: TVWriteFollowUp,
        @ViewBuilder composer: @escaping (TVFollowUpComposer) -> Composer
    ) -> some View {
        modifier(TVWriteFollowUpModifier(followUp: followUp, composer: composer))
    }
}

private struct TVWriteFollowUpModifier<Composer: View>: ViewModifier {
    let followUp: TVWriteFollowUp
    let composer: (TVFollowUpComposer) -> Composer

    func body(content: Content) -> some View {
        content
            .alert(
                followUp.question?.question ?? followUp.lastQuestionText,
                isPresented: Binding(
                    get: { followUp.question != nil },
                    set: { isPresented in
                        if !isPresented { followUp.respond(yes: false) }
                    }
                )
            ) {
                Button("Mark Watched") { followUp.respond(yes: true) }
                Button("Not Now", role: .cancel) { followUp.respond(yes: false) }
            }
            .fullScreenCover(
                item: Binding(
                    get: { followUp.composer },
                    set: { item in
                        if item == nil { followUp.composerDismissed() }
                    }
                )
            ) { item in
                composer(item)
            }
    }
}
