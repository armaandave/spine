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
/// - A Yes that fails asks again with the reason, and Try Again resends it with the same mutation ID, so it applies
///   at most once (SW-700, TV-705). Not Now then goes on as No.
/// - Whatever the answer, the composer opens for the season the first response named. A Yes can also name the
///   write's own season (`offer_season_number`); any other season only the Yes finished gets no composer.
/// - The composer opens in the same turn as the response that finished the season: its defaults come with the
///   response, and its header from what the presenting screen already shows (`context`), so nothing is loaded
///   first. The server offers each season's composer once.
@MainActor
@Observable
final class TVWriteFollowUp {
    let showRef: MediaRef
    /// The earlier-episodes question waiting for an answer.
    private(set) var question: TVEarlierUnwatched?
    /// The season-finish composer to present.
    private(set) var composer: TVFollowUpComposer?
    private(set) var isSending = false
    /// Why the last Yes failed; the question is asked again with it.
    private(set) var errorMessage: String?
    /// Answers in flight.
    private(set) var pendingWork = 0
    /// The show, or the season or episode page, as the presenting screen has it now. The composer's header takes
    /// the show's title and artwork from it.
    @ObservationIgnored var context: () -> MediaDetail? = { nil }
    /// Receives the Yes response, so the presenting screen can fold it in and refresh.
    @ObservationIgnored var onResponse: ((TVActionResponse) -> Void)?
    /// The last question shown, so the alert keeps its title while it animates away.
    @ObservationIgnored private(set) var lastQuestionText = ""
    /// The answer in flight, if any.
    @ObservationIgnored private(set) var loading: Task<Void, Never>?

    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private var pendingFinished: TVFinished?
    @ObservationIgnored private var generation = 0
    /// The mutation ID of a Yes that failed, kept while its question is asked again.
    @ObservationIgnored private var retryMutationId: UUID?

    /// `showRef` may be the show or one of its seasons or episodes; actions go to the show.
    init(showRef: MediaRef, trackingRepository: TrackingRepository) {
        self.showRef = showRef.parentTVRef ?? showRef
        self.trackingRepository = trackingRepository
    }

    /// Whether it has something to show or is still working towards it.
    var isActive: Bool { question != nil || composer != nil || pendingWork > 0 }

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
            errorMessage = nil
            retryMutationId = nil
            question = earlier
            pendingFinished = envelope.finished
            return
        }
        guard let finished = envelope.finished else { return }
        generation += 1
        present(finished)
    }

    /// Answers the question and waits for everything it starts.
    func answer(yes: Bool) async {
        guard let taken = takeQuestion() else { return }
        pendingWork += 1
        await resolve(taken, yes: yes)
        pendingWork -= 1
    }

    /// Answers the question from a button: takes it at once, so a second tap or the alert's dismissal can't answer
    /// it again.
    func respond(yes: Bool) {
        guard let taken = takeQuestion() else { return }
        run { [self] in await resolve(taken, yes: yes) }
    }

    /// The composer closed, saved or not ("Not now" saves nothing, SW-301).
    func composerDismissed() {
        composer = nil
    }

    private func run(_ work: @escaping () async -> Void) {
        pendingWork += 1
        loading = Task { [self] in
            await work()
            pendingWork -= 1
        }
    }

    private struct TakenQuestion {
        let question: TVEarlierUnwatched
        let firstFinished: TVFinished?
        let generation: Int
        let mutationId: UUID
    }

    private func takeQuestion() -> TakenQuestion? {
        guard let question else { return nil }
        self.question = nil
        let firstFinished = pendingFinished
        pendingFinished = nil
        generation += 1
        let mutationId = retryMutationId ?? UUID()
        retryMutationId = nil
        return TakenQuestion(question: question, firstFinished: firstFinished, generation: generation,
                             mutationId: mutationId)
    }

    private func resolve(_ taken: TakenQuestion, yes: Bool) async {
        var offer = taken.firstFinished
        if yes {
            isSending = true
            let request = TVActionRequest(
                mutationId: taken.mutationId,
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
                isSending = false
                errorMessage = nil
                onResponse?(response)
                if offer == nil,
                   let offered = taken.question.offerSeasonNumber,
                   let finished = response.finished,
                   finished.seasonNumber == offered {
                    offer = finished
                }
            } catch is CancellationError {
                isSending = false
                return
            } catch {
                isSending = false
                // A lost response may still have landed: every screen showing the show refreshes.
                if case APIError.network = error { MediaStateChange.post(ref: showRef) }
                // Ask again with the reason. Try Again resends the same mutation ID, so a Yes that did land applies
                // once; Not Now goes on as No.
                guard taken.generation == generation, question == nil else { return }
                errorMessage = (error as? APIError)?.apiErrorMessage ?? error.localizedDescription
                retryMutationId = taken.mutationId
                lastQuestionText = taken.question.question
                pendingFinished = taken.firstFinished
                question = taken.question
                return
            }
        }
        errorMessage = nil
        guard let offer, taken.generation == generation else { return }
        present(offer)
    }

    private func present(_ finished: TVFinished) {
        guard question == nil else { return }
        composer = TVFollowUpComposer(
            finished: finished,
            detail: Self.composerDetail(for: finished, showRef: showRef, context: context())
        )
    }

    /// The detail the composer opens on, for the season (or show) `finished` names. A season page finishing its
    /// own season gives its own detail. Otherwise the show's title and artwork go under the season's ref, with the
    /// season's poster when the screen has it: a show's or a season's detail carries the show's title, an
    /// episode's names its show in `series_title`. The write's defaults are in `finished.composer`.
    static func composerDetail(for finished: TVFinished, showRef: MediaRef, context: MediaDetail?) -> MediaDetail {
        let ref = finished.seasonNumber.map { seasonNumber in
            MediaRef(itemId: nil, source: showRef.source, mediaType: "season", mediaId: showRef.mediaId,
                     seasonNumber: seasonNumber, episodeNumber: nil)
        } ?? showRef
        if let context, context.ref.id == ref.id { return context }
        let fallbackTitle = finished.composer.initialTarget?.title
            ?? finished.seasonNumber.map(TVTrackingPresentation.seasonTitle) ?? "Log"
        guard let context else { return MediaDetail(ref: ref, title: fallbackTitle) }
        if context.ref.isEpisode {
            // An episode's artwork is its still, not a poster.
            let show = context.details?["series_title"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines)
            return MediaDetail(
                ref: ref,
                title: show.flatMap { $0.isEmpty ? nil : $0 } ?? fallbackTitle,
                backdropUrl: context.displayBackdropURL
            )
        }
        let season = finished.seasonNumber.flatMap { number in context.seasons?.first { $0.seasonNumber == number } }
        return MediaDetail(
            ref: ref,
            title: context.title,
            posterUrl: season?.imageUrl ?? context.displayPosterURL,
            posterOrientation: context.posterOrientation,
            posterAccentColor: context.posterAccentColor,
            backdropUrl: context.displayBackdropURL
        )
    }

    /// A context from a show's summary, as Continue Watching and the diary's picker have it: its title and artwork.
    static func context(show: MediaSummary) -> MediaDetail {
        MediaDetail(
            ref: show.ref,
            title: show.title,
            imageUrl: show.imageUrl,
            posterUrl: show.posterUrl,
            posterOrientation: show.posterOrientation,
            posterAccentColor: show.posterAccentColor,
            backdropUrl: show.backdropUrl,
            customPosterUrl: show.customPosterUrl,
            customBackdropUrl: show.customBackdropUrl
        )
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
                Button(followUp.errorMessage == nil ? "Mark Watched" : "Try Again") { followUp.respond(yes: true) }
                Button("Not Now", role: .cancel) { followUp.respond(yes: false) }
            } message: {
                // A Yes that failed is asked again, with the reason (SW-700).
                if let message = followUp.errorMessage {
                    Text("These episodes weren't marked. \(message)")
                }
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
