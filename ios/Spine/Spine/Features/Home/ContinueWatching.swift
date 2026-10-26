import SwiftUI

/// Continue Watching on Home and Profile (TV-701): what each show's card does from its touch-and-hold menu.
///
/// - Mark Watched ticks up next with a mutation ID (TV-100, TV-705). The card shows the tick at once; the server's
///   answer then replaces the card's state where it is, so the show keeps its place in the row until the rail next
///   loads. A failure restores the card. Either way the screen plays a haptic.
/// - The tick's response goes to the show's `TVWriteFollowUp`, which asks the earlier-episodes question and opens the
///   season-finish composer in the same turn (TV-107, TV-403). Ticks on several shows each keep their follow-up; they
///   show one at a time, oldest first.
/// - Right after a tick, the menu also offers to untick that episode, naming the viewing the tick recorded (TV-102),
///   until the card leaves the screen or stops showing what the tick left.
/// - Log opens the episode page's composer (TV-404); a question or season composer it causes follows once it closes.
@MainActor
@Observable
final class ContinueWatchingTicks {
    /// The tick that can be undone from a card, and what the card showed right after it.
    struct Undo: Hashable {
        let show: MediaRef
        let episode: TVEpisodeCoordinate
        let viewingId: Int
        /// One per tick: a retried untick applies at most once (TV-705).
        let mutationId: UUID
        let upNextAfter: TVEpisodeCoordinate?
    }

    /// Cards with a tick on its way, and the episode it marks: the card shows it watched until the server answers.
    private(set) var pendingTicks: [String: TVEpisodeCoordinate] = [:]
    /// Cards with an untick on its way.
    private(set) var pendingUnticks: Set<String> = []
    private(set) var undos: [String: Undo] = [:]
    var errorTitle = "Could not mark watched"
    var errorMessage: String?
    /// Counts confirmed and failed ticks and unticks, for the screen's haptics.
    private(set) var successCount = 0
    private(set) var failureCount = 0
    /// A write changed a card since the rail last loaded: the screen re-sorts the rail on its next visit.
    private(set) var needsReload = false
    /// The episode log to open (Log S2 E5…).
    var logTarget: ContinueWatchingLogTarget?
    /// One follow-up per show, in the order their writes answered.
    private(set) var followUps: [TVWriteFollowUp] = []
    /// The follow-up for the screen to present: the oldest one with something to show, else the latest, so a
    /// composer that is closing keeps its host.
    var followUp: TVWriteFollowUp? { followUps.first(where: \.isActive) ?? followUps.last }
    /// Recognizes this screen's own state-change posts.
    let changeOrigin = UUID()
    /// Puts a card's new state in place in the screen's rail.
    @ObservationIgnored var onUpdate: (_ itemId: String, _ tracking: TrackingState) -> Void = { _, _ in }

    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let onUnauthorized: () -> Void
    /// A saved log's envelope, held until its composer has closed.
    @ObservationIgnored private var savedLog: (item: LibraryItem, envelope: TVWriteEnvelope)?

    init(trackingRepository: TrackingRepository, onUnauthorized: @escaping () -> Void) {
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
    }

    /// A show's state when the item is a Continue Watching card; nil for books, games and other media.
    static func state(of item: LibraryItem) -> TVShowState? {
        item.media.ref.mediaType == "tv" ? item.tracking.tv : nil
    }

    func isPending(_ item: LibraryItem) -> Bool {
        pendingTicks[item.id] != nil || pendingUnticks.contains(item.id)
    }

    /// The card's touch-and-hold menu; empty while a write on it is on its way.
    func menu(for item: LibraryItem) -> [ContinueWatchingMenuItem] {
        guard let tv = Self.state(of: item), !isPending(item) else { return [] }
        return TVTrackingPresentation.continueWatchingMenu(for: tv, undo: undos[item.id]?.episode)
    }

    /// Runs a menu item. `open` shows a page (Go to Season 2).
    func perform(_ command: ContinueWatchingMenuItem.Command, on item: LibraryItem, open: (MediaRef) -> Void) {
        switch command {
        case .markWatched:
            Task { _ = await tick(item) }
        case .markUnwatched:
            Task { _ = await untick(item) }
        case let .log(episode):
            cardsLeftScreen()
            logTarget = ContinueWatchingLogTarget(item: item, episode: Self.ref(show: item.media.ref, episode: episode))
        case let .openSeason(seasonNumber):
            open(Self.ref(show: item.media.ref, seasonNumber: seasonNumber))
        }
    }

    /// Ticks the show's up-next episode. Returns the server's answer, or nil when there was nothing to tick or the
    /// tick failed (the card then shows its last confirmed state).
    func tick(_ item: LibraryItem) async -> TVActionResponse? {
        let ref = item.media.ref
        guard ref.mediaType == "tv", let upNext = item.tracking.tv?.upNext, !isPending(item) else { return nil }
        pendingTicks[item.id] = upNext.coordinate
        errorMessage = nil
        defer { pendingTicks[item.id] = nil }

        do {
            let response = try await trackingRepository.performTVAction(
                source: ref.source,
                mediaId: ref.mediaId,
                action: .tick,
                request: TVActionRequest(
                    seasonNumber: upNext.seasonNumber,
                    episodeNumber: upNext.episodeNumber,
                    mutationId: UUID()
                )
            )
            if let tracking = response.tracking { onUpdate(item.id, tracking) }
            undos[item.id] = Self.undo(after: response, ticked: upNext.coordinate, show: ref)
            needsReload = true
            successCount += 1
            followUp(for: item).handle(response.envelope)
            MediaStateChange.post(ref: ref, origin: changeOrigin)
            return response
        } catch is CancellationError {
            return nil
        } catch {
            fail("Could not mark watched", error)
            return nil
        }
    }

    /// Unticks the episode the card's last tick marked, by the viewing that tick recorded (TV-102).
    func untick(_ item: LibraryItem) async -> TVActionResponse? {
        let ref = item.media.ref
        guard let undo = undos[item.id], !isPending(item) else { return nil }
        pendingUnticks.insert(item.id)
        errorMessage = nil
        defer { pendingUnticks.remove(item.id) }

        do {
            let response = try await trackingRepository.performTVAction(
                source: ref.source,
                mediaId: ref.mediaId,
                action: .untick,
                request: TVActionRequest(
                    seasonNumber: undo.episode.seasonNumber,
                    episodeNumber: undo.episode.episodeNumber,
                    viewingId: undo.viewingId,
                    mutationId: undo.mutationId
                )
            )
            undos[item.id] = nil
            // The viewing the tick recorded is gone, so nothing was unticked (TV-102): the episode changed elsewhere.
            guard response.envelope.changed else {
                let code = TVTrackingPresentation.episodeCode(season: undo.episode.seasonNumber,
                                                              episode: undo.episode.episodeNumber)
                fail("Could not mark unwatched", message: "\(code) changed after you marked it. Change it from the show's page.")
                return nil
            }
            if let tracking = response.tracking { onUpdate(item.id, tracking) }
            needsReload = true
            successCount += 1
            MediaStateChange.post(ref: ref, origin: changeOrigin)
            return response
        } catch is CancellationError {
            return nil
        } catch {
            fail("Could not mark unwatched", error)
            return nil
        }
    }

    /// The cards left the screen: another tab, a page or the Log cover opened over them. The undo goes with them, since
    /// the episode can change where the card can't show it (TV-701). The tick's own earlier-episodes question and Log
    /// Season, which open over the cards, keep it; an answer still on its way doesn't, as the cards are on screen then.
    func cardsLeftScreen() {
        guard !followUps.contains(where: { $0.question != nil || $0.composer != nil }) else { return }
        undos = [:]
    }

    /// The rail loaded again (a pull to refresh), in a fresh order. A card keeps its undo only while it still shows
    /// what its tick left.
    func railDidLoad(_ items: [LibraryItem]) {
        needsReload = false
        undos = undos.filter { id, undo in
            guard let item = items.first(where: { $0.id == id }), let tv = Self.state(of: item) else { return false }
            return tv.upNext?.coordinate == undo.upNextAfter
        }
    }

    /// A saved log's earlier-episodes question or season composer waits until the log's composer has closed.
    func logSaved(_ result: MediaLogSaveResult, for item: LibraryItem) {
        guard let envelope = result.tvEnvelope, envelope.hasFollowUp else { return }
        savedLog = (item, envelope)
    }

    /// The log's composer closed: what its save asks next can show now (TV-107, TV-403).
    func logClosed() {
        guard let saved = savedLog else { return }
        savedLog = nil
        followUp(for: saved.item).handle(saved.envelope)
    }

    /// A season-finish composer saved its log, which now covers the ticked episode: unticking it would only be
    /// refused (TV-102), so the menu stops offering it.
    func followUpLogSaved(show: MediaRef) {
        undos = undos.filter { $0.value.show != show }
    }

    /// A season page's ref under the show.
    static func ref(show: MediaRef, seasonNumber: Int) -> MediaRef {
        MediaRef(itemId: nil, source: show.source, mediaType: "season", mediaId: show.mediaId,
                 seasonNumber: seasonNumber, episodeNumber: nil)
    }

    /// An episode page's ref under the show.
    static func ref(show: MediaRef, episode: TVEpisodeCoordinate) -> MediaRef {
        MediaRef(itemId: nil, source: show.source, mediaType: "episode", mediaId: show.mediaId,
                 seasonNumber: episode.seasonNumber, episodeNumber: episode.episodeNumber)
    }

    /// The rail with one card's state replaced where it is.
    static func replacingTracking(_ tracking: TrackingState, id: String, in items: [LibraryItem]) -> [LibraryItem] {
        items.map { $0.id == id ? LibraryItem(media: $0.media, tracking: tracking) : $0 }
    }

    /// The undo a tick offers: only a tick that recorded a viewing, named in the response's episode state.
    private static func undo(after response: TVActionResponse, ticked: TVEpisodeCoordinate, show: MediaRef) -> Undo? {
        guard response.envelope.changed, let episode = response.envelope.episode, episode.coordinate == ticked,
              let viewingId = episode.untickViewingId
        else { return nil }
        return Undo(
            show: show,
            episode: ticked,
            viewingId: viewingId,
            mutationId: UUID(),
            upNextAfter: (response.tracking?.tv ?? response.tv)?.upNext?.coordinate
        )
    }

    private func fail(_ title: String, _ error: Error) {
        fail(title, message: (error as? APIError)?.apiErrorMessage ?? error.localizedDescription)
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }

    private func fail(_ title: String, message: String) {
        errorTitle = title
        errorMessage = message
        failureCount += 1
    }

    /// The show's follow-up. A show without one gets a new one, after the follow-ups still showing or loading
    /// something, so a write on another show never drops a question or composer (TV-403); idle ones go.
    private func followUp(for item: LibraryItem) -> TVWriteFollowUp {
        let ref = item.media.ref
        let showRef = ref.parentTVRef ?? ref
        if let existing = followUps.first(where: { $0.showRef == showRef }) {
            return existing
        }
        followUps.removeAll { !$0.isActive }
        let next = TVWriteFollowUp(showRef: ref, trackingRepository: trackingRepository)
        let context = TVWriteFollowUp.context(show: item.media)
        next.context = { context }
        // Yes to the earlier-episodes question changes the show's counts: the card takes them where it is.
        next.onResponse = { [weak self] response in
            guard let self, let tracking = response.tracking else { return }
            onUpdate(item.id, tracking)
            needsReload = true
        }
        followUps.append(next)
        return next
    }
}

/// The episode a card's Log S2 E5… opens.
struct ContinueWatchingLogTarget: Identifiable {
    let item: LibraryItem
    let episode: MediaRef

    var id: String { episode.id }
}

/// The line under a show in Continue Watching (TV-701): "S2 E5 next · 4 of 10", in SF's condensed width when that is
/// what fits, or "S2 E5 next" where the count doesn't fit at all. While a tick is on its way it reads "✓ S2 E5".
struct ContinueWatchingLine: View {
    let tv: TVShowState
    var pendingTick: TVEpisodeCoordinate?

    var body: some View {
        Group {
            if let pendingTick {
                Text("\(Image(systemName: "checkmark")) \(TVTrackingPresentation.episodeCode(season: pendingTick.seasonNumber, episode: pendingTick.episodeNumber))")
                    .foregroundStyle(.green)
            } else if let line = TVTrackingPresentation.continueWatchingLine(for: tv) {
                ViewThatFits(in: .horizontal) {
                    Text(line)
                    Text(line).fontWidth(.condensed)
                    Text(TVTrackingPresentation.continueWatchingLine(for: tv, includesProgress: false) ?? line)
                        .minimumScaleFactor(0.7)
                }
                .foregroundStyle(tv.upNext?.isNew == true ? Color.white : Color.white.opacity(0.54))
            }
        }
        .font(.caption2.weight(.bold))
        .lineLimit(1)
    }
}

/// What touch and hold lifts: up next's still with "S2 E5 · Title" under it, else the show's artwork (TV-701).
struct ContinueWatchingPreview: View {
    private static let width: CGFloat = 300

    let media: MediaSummary
    let tv: TVShowState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SpineAsyncImage(url: URL(string: imageURL ?? "")) { phase in
                if case let .success(image) = phase {
                    image
                        .resizable()
                        .scaledToFill()
                } else {
                    Color.white.opacity(0.08)
                }
            }
            .frame(width: Self.width, height: Self.width * 9 / 16)
            .clipped()

            Text(caption)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white)
                .lineLimit(2)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(width: Self.width)
        .background(SpinePalette.pageBackground)
    }

    private var imageURL: String? {
        tv.upNext?.imageUrl ?? media.displayBackdropURL ?? media.displayPosterURL
    }

    private var caption: String {
        tv.upNext.map(TVTrackingPresentation.upNextTitle) ?? media.displayTitle
    }
}

extension View {
    /// A Continue Watching card's touch-and-hold menu with its episode preview, and the same items as VoiceOver
    /// actions (TV-701). Cards of other media (`tv` nil) are left as they are.
    func continueWatchingMenu(
        media: MediaSummary,
        tv: TVShowState?,
        items: [ContinueWatchingMenuItem],
        perform: @escaping (ContinueWatchingMenuItem.Command) -> Void
    ) -> some View {
        modifier(ContinueWatchingMenuModifier(media: media, tv: tv, items: items, perform: perform))
    }

    /// What Continue Watching opens over its screen: the earlier-episodes question and season-finish composer after a
    /// tick or log (TV-107, TV-403), the episode log, a failed write's message, and the haptics. The screen leaving
    /// (another tab, a pushed or covering page) ends the cards' undo.
    func continueWatchingPresentations(
        _ ticks: ContinueWatchingTicks,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void
    ) -> some View {
        modifier(ContinueWatchingPresentations(
            ticks: ticks,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }
}

private struct ContinueWatchingMenuModifier: ViewModifier {
    let media: MediaSummary
    let tv: TVShowState?
    let items: [ContinueWatchingMenuItem]
    let perform: (ContinueWatchingMenuItem.Command) -> Void

    func body(content: Content) -> some View {
        if let tv {
            content
                .contextMenu {
                    ForEach(items) { item in
                        if case .markUnwatched = item.command { Divider() }
                        Button(item.title, systemImage: item.systemImage) { perform(item.command) }
                    }
                } preview: {
                    ContinueWatchingPreview(media: media, tv: tv)
                }
                .accessibilityActions {
                    ForEach(items) { item in
                        Button(item.accessibilityName) { perform(item.command) }
                    }
                }
        } else {
            content
        }
    }
}

private struct ContinueWatchingPresentations: ViewModifier {
    @Bindable var ticks: ContinueWatchingTicks
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    func body(content: Content) -> some View {
        content
            .background {
                if let followUp = ticks.followUp {
                    Color.clear
                        .tvWriteFollowUp(followUp) { item in
                            MediaLogView(
                                detail: item.detail,
                                trackingRepository: trackingRepository,
                                diaryRepository: diaryRepository,
                                onUnauthorized: onUnauthorized,
                                onSaved: { ticks.followUpLogSaved(show: followUp.showRef) },
                                tvComposer: item.finished.composer
                            )
                        }
                }
            }
            .fullScreenCover(item: $ticks.logTarget, onDismiss: { ticks.logClosed() }) { target in
                ContinueWatchingLogCover(
                    target: target,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    onUnauthorized: onUnauthorized,
                    onResult: { ticks.logSaved($0, for: target.item) }
                )
            }
            .alert(ticks.errorTitle, isPresented: Binding(
                get: { ticks.errorMessage != nil },
                set: { if !$0 { ticks.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(ticks.errorMessage ?? "")
            }
            .sensoryFeedback(.success, trigger: ticks.successCount)
            .sensoryFeedback(.error, trigger: ticks.failureCount)
            .onDisappear { ticks.cardsLeftScreen() }
    }
}

/// Log S2 E5… from Continue Watching: loads the episode as its page does, then opens the page's composer (TV-404).
private struct ContinueWatchingLogCover: View {
    let target: ContinueWatchingLogTarget
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void
    let onResult: (MediaLogSaveResult) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var detail: MediaDetail?
    @State private var errorMessage: String?

    var body: some View {
        if let detail {
            MediaLogView(
                detail: detail,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onResult: onResult,
                onUnauthorized: onUnauthorized,
                onSaved: {}
            )
        } else {
            NavigationStack {
                Group {
                    if let errorMessage {
                        ContentUnavailableView {
                            Label("Could Not Load Episode", systemImage: "exclamationmark.triangle")
                        } description: {
                            Text(errorMessage)
                        } actions: {
                            Button("Try Again") { Task { await load() } }
                        }
                    } else {
                        ProgressView()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(role: .close) {
                            dismiss()
                        } label: {
                            Label("Close", systemImage: "xmark")
                        }
                    }
                }
            }
            .preferredColorScheme(.dark)
            .task { await load() }
        }
    }

    private func load() async {
        errorMessage = nil
        do {
            detail = try await mediaRepository.detail(ref: target.episode)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}
