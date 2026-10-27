import SwiftUI

/// Continue Watching on Home and Profile (TV-701). A show's card opens the show from its poster, and from its title
/// and "Update progress ›" a sheet of up next's season (`ContinueWatchingEpisodes`). A write in the sheet changes the
/// card's show state where it is; the rail re-sorts the next time the screen loads it.
@MainActor
@Observable
final class ContinueWatchingTicks {
    /// The episode sheet on screen.
    var episodes: ContinueWatchingEpisodes?
    /// A sheet that closed while its season still had a write on its way or a question or composer to show. The
    /// screen shows what that write asks next (TV-107, TV-403), or its failure (TV-705).
    private(set) var closedEpisodes: ContinueWatchingEpisodes?
    /// A write changed a card since the rail last loaded: the screen re-sorts the rail on its next visit.
    private(set) var needsReload = false
    /// Recognizes this screen's own state-change posts, the sheet's included.
    let changeOrigin = UUID()
    /// Puts a card's new show state in place in the screen's rail.
    @ObservationIgnored var onUpdate: (_ itemId: String, _ tv: TVShowState) -> Void = { _, _ in }

    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let diaryRepository: DiaryRepository
    @ObservationIgnored private let onUnauthorized: () -> Void
    /// The sheet as it was presented, kept until its dismissal has finished.
    @ObservationIgnored private var presented: ContinueWatchingEpisodes?
    /// The page the sheet asked for. It opens once the sheet has gone.
    @ObservationIgnored private var destination: MediaBrowsingSelection?
    /// A page asked for while a write was on its way, waiting for that write.
    @ObservationIgnored private(set) var opening: Task<Void, Never>?

    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    /// A show's state when the item is a Continue Watching card; nil for books, games and other media.
    static func state(of item: LibraryItem) -> TVShowState? {
        item.media.ref.mediaType == "tv" ? item.tracking.tv : nil
    }

    /// Opens the card's episode sheet on up next's season. A show without up next has nothing to open there: its
    /// card opens the show.
    func openEpisodes(for item: LibraryItem) {
        guard let upNext = Self.state(of: item)?.upNext else { return }
        let sheet = ContinueWatchingEpisodes(item: item, upNext: upNext) { [
            mediaRepository, trackingRepository, diaryRepository, onUnauthorized, changeOrigin
        ] ref in
            // The season page's own model, posting as this screen, so the screen keeps the card where it is.
            MediaDetailViewModel(
                ref: ref,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized,
                changeOrigin: changeOrigin
            )
        }
        sheet.onShowChange = { [weak self] tv in
            guard let self else { return }
            needsReload = true
            onUpdate(item.id, tv)
        }
        sheet.onOpen = { [weak self, weak sheet] selection in
            guard let self, let sheet else { return }
            open(selection, from: sheet)
        }
        presented = sheet
        episodes = sheet
    }

    /// Closes the sheet for a page it asked for. A write still on its way may yet ask the earlier-episodes question
    /// or open Log Season over the sheet (TV-107, TV-403), which a page covering the screen would block: the page
    /// waits for that write, and doesn't open when the write asks something or fails, whose message shows in the
    /// sheet (TV-705).
    private func open(_ selection: MediaBrowsingSelection, from sheet: ContinueWatchingEpisodes) {
        let season = sheet.season
        guard season.hasPendingWrites || season.tvFollowUp.isActive else {
            destination = selection
            episodes = nil
            return
        }
        opening = Task { [weak self] in
            let succeeded = await season.settled()
            await season.tvFollowUp.loading?.value
            guard let self, succeeded, episodes === sheet, sheet.season === season,
                  !season.tvFollowUp.isActive else { return }
            destination = selection
            episodes = nil
        }
    }

    /// The sheet has gone. Returns the page it asked for, if any: the show, or one of the season's episodes.
    func sheetDidClose() -> MediaBrowsingSelection? {
        if let presented, presented.season.hasPendingWrites || presented.season.tvFollowUp.isActive {
            closedEpisodes = presented
        }
        presented = nil
        defer { destination = nil }
        return destination
    }

    /// The rail loaded again, in a fresh order.
    func railDidLoad() {
        needsReload = false
    }

    /// The rail with one card's show state replaced where it is.
    static func replacingTV(_ tv: TVShowState, id: String, in items: [LibraryItem]) -> [LibraryItem] {
        items.map { item in
            guard item.id == id else { return item }
            var tracking = item.tracking
            tracking.tv = tv
            return LibraryItem(media: item.media, tracking: tracking)
        }
    }
}

/// The sheet a Continue Watching card's title and line open (TV-701): one season of the show, listed with the season
/// page's episode rows and run by the season page's own model. A tick, an untick, Mark watched up to here and what
/// they ask next work exactly as they do on the season page.
@MainActor
@Observable
final class ContinueWatchingEpisodes: Identifiable {
    static let tickFailureTitle = "Could not mark watched"
    static let untickFailureTitle = "Could not mark unwatched"

    /// The card the sheet opened from.
    let item: LibraryItem
    /// Up next as the sheet opened on this season: its row shows before the season has loaded, and the list starts
    /// at it.
    private(set) var upNext: TVEpisodePointer
    /// The season shown: its page model loads its episodes and sends its writes.
    private(set) var season: MediaDetailViewModel
    /// The show after the sheet's latest write, else as the card had it.
    private(set) var show: TVShowState?
    /// The title of the alert a failed write shows: what the last tap asked for.
    private(set) var failureTitle = ContinueWatchingEpisodes.tickFailureTitle
    /// Each write's show state, for the card.
    @ObservationIgnored var onShowChange: (TVShowState) -> Void = { _ in }
    /// Asks for a page: the screen closes the sheet first, then opens it.
    @ObservationIgnored var onOpen: (MediaBrowsingSelection) -> Void = { _ in }

    @ObservationIgnored private let makeSeason: (MediaRef) -> MediaDetailViewModel

    init(item: LibraryItem, upNext: TVEpisodePointer, makeSeason: @escaping (MediaRef) -> MediaDetailViewModel) {
        self.item = item
        self.upNext = upNext
        self.makeSeason = makeSeason
        show = ContinueWatchingTicks.state(of: item)
        season = makeSeason(Self.seasonRef(show: item.media.ref, seasonNumber: upNext.seasonNumber))
        attach(season)
    }

    var id: String { item.id }
    var seasonNumber: Int { upNext.seasonNumber }
    var seasonRef: MediaRef { Self.seasonRef(show: item.media.ref, seasonNumber: seasonNumber) }

    /// "The Crown · Season 2", or the season's own name once its page names it (`season_title`, which the season
    /// page's facts read too).
    var title: String {
        TVTrackingPresentation.episodeSheetTitle(
            show: item.media.displayTitle,
            seasonNumber: seasonNumber,
            seasonName: season.detail?.details?["season_title"]?.stringValue
        )
    }

    /// The season's episodes once they have loaded; until then up next's row alone, from what the card has.
    var rows: [TVEpisodeRowModel] {
        let loaded = seasonRows
        return loaded.isEmpty ? [TVEpisodeRowModel.upNext(upNext)] : loaded
    }

    /// The list the sheet shows: up next's row alone while the season loads, then the whole season. When it becomes
    /// the whole season, the list shows the new rows together with a request for up next's row (TV-701). Left to
    /// itself, SwiftUI kept up next's row in place by its estimate of the rows that arrived above it, which missed
    /// their spacing and depended on the rows it had laid out, so the sheet opened rows away from up next.
    var listID: String {
        "S\(seasonNumber)" + (seasonRows.isEmpty ? "-up-next" : "")
    }

    /// The row at the top of the list as it opens: up next's.
    var startRowID: String {
        let rows = rows
        return rows.first { $0.episodeNumber == upNext.episodeNumber && $0.listed }?.id
            ?? rows.first { $0.episodeNumber == upNext.episodeNumber }?.id
            ?? TVEpisodeRowModel.upNext(upNext).id
    }

    /// Up next's row and the rows below it.
    var rowsFromStart: Int {
        Self.rowsFromStart(rows: rows, startRowID: startRowID)
    }

    static func rowsFromStart(rows: [TVEpisodeRowModel], startRowID: String) -> Int {
        rows.count - (rows.firstIndex { $0.id == startRowID } ?? 0)
    }

    /// The room the list leaves under its last row, so up next's row can reach the top: the list's height and two
    /// rows more, less the rows from up next down. The two rows cover the bars over the list's top and bottom, which
    /// the list's height leaves out. Accessibility sizes make rows taller, which only leaves more room than it needs.
    static func roomBelowRows(viewportHeight: CGFloat, rowsFromStart: Int) -> CGFloat {
        max(0, viewportHeight - CGFloat(rowsFromStart - 2) * (TVEpisodeRows.rowHeight + TVEpisodeRows.spacing))
    }

    private var seasonRows: [TVEpisodeRowModel] {
        season.detail.map {
            TVEpisodeRowModel.rows(episodes: $0.episodes ?? [], unlisted: $0.tvUnlistedEpisodes)
        } ?? []
    }

    /// Log Season… in the ··· menu: once the season has loaded, and when its page would offer it (TV-404).
    var canLogSeason: Bool {
        guard season.detail != nil else { return false }
        guard let state = season.viewer?.tv?.season else { return true }
        return state.availableActions.contains("log_season")
    }

    /// A row's tick: ticks the episode, or unticks a ticked one, as on the season page (TV-100, TV-102).
    func toggle(_ episode: TVEpisodeState) {
        failureTitle = season.tvIsWatched(episode) ? Self.untickFailureTitle : Self.tickFailureTitle
        season.toggleTVEpisode(episode)
    }

    /// The dialog a long press's Mark watched up to here asks was confirmed (TV-108).
    func confirm(_ confirmation: TVPageConfirmation) {
        failureTitle = Self.tickFailureTitle
        season.confirmTV(confirmation)
    }

    /// A composer over the sheet closed: Log Season after the season's last tick (saved or not), or Log Season… from
    /// the menu. When up next has moved on to a later season, the sheet shows that season. A show caught up or
    /// finished has no up next, so the sheet stays on the season it finished, every tick filled.
    func composerClosed() {
        guard !season.tvFollowUp.isActive, let next = show?.upNext, next.seasonNumber > upNext.seasonNumber else { return }
        upNext = next
        season = makeSeason(Self.seasonRef(show: item.media.ref, seasonNumber: next.seasonNumber))
        attach(season)
    }

    /// A row's body: the episode's page, which pages through the season's episodes as it does from the season page.
    func openEpisode(_ episode: EpisodeSummary) {
        let show = item.media.ref
        let numbers = season.detail?.episodes?.map(\.episodeNumber) ?? []
        let refs = numbers.map { Self.episodeRef(show: show, seasonNumber: seasonNumber, episodeNumber: $0) }
        let selected = Self.episodeRef(show: show, seasonNumber: seasonNumber, episodeNumber: episode.episodeNumber)
        onOpen(MediaBrowsingSelection(ref: selected, within: refs))
    }

    /// Open Show in the ··· menu.
    func openShow() {
        onOpen(MediaBrowsingSelection(ref: item.media.ref, within: [item.media.ref]))
    }

    /// The season's model reports each write to the card, and its Log Season header, before the season has loaded,
    /// comes from the card: the show's title and artwork (TV-403).
    private func attach(_ model: MediaDetailViewModel) {
        let card = TVWriteFollowUp.context(show: item.media)
        model.tvFollowUp.context = { [weak model] in model?.detail ?? card }
        model.onTVShowChange = { [weak self] tv in
            guard let self else { return }
            show = tv
            onShowChange(tv)
        }
    }

    static func seasonRef(show: MediaRef, seasonNumber: Int) -> MediaRef {
        MediaRef(itemId: nil, source: show.source, mediaType: "season", mediaId: show.mediaId,
                 seasonNumber: seasonNumber, episodeNumber: nil)
    }

    static func episodeRef(show: MediaRef, seasonNumber: Int, episodeNumber: Int) -> MediaRef {
        MediaRef(itemId: nil, source: show.source, mediaType: "episode", mediaId: show.mediaId,
                 seasonNumber: seasonNumber, episodeNumber: episodeNumber)
    }
}

extension View {
    /// What Continue Watching opens over its screen: the episode sheet, and the page the sheet asks for once it has
    /// closed (`onOpen`). A sheet closed while its write was on its way still shows that write's question and Log
    /// Season (TV-107, TV-403), or its failure (TV-705).
    func continueWatchingPresentations(
        _ ticks: ContinueWatchingTicks,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        onOpen: @escaping (MediaBrowsingSelection) -> Void
    ) -> some View {
        modifier(ContinueWatchingPresentations(
            ticks: ticks,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized,
            onOpen: onOpen
        ))
    }
}

private struct ContinueWatchingPresentations: ViewModifier {
    @Bindable var ticks: ContinueWatchingTicks
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void
    let onOpen: (MediaBrowsingSelection) -> Void

    func body(content: Content) -> some View {
        content
            .background {
                if ticks.episodes == nil, let closed = ticks.closedEpisodes {
                    let season = closed.season
                    Color.clear
                        .tvWriteFollowUp(season.tvFollowUp) { item in
                            ContinueWatchingFinishComposer(
                                item: item,
                                episodes: closed,
                                trackingRepository: trackingRepository,
                                diaryRepository: diaryRepository,
                                onUnauthorized: onUnauthorized
                            )
                        }
                        // Its write failed after the sheet closed: the sheet's message and error haptic, over the
                        // screen (TV-705).
                        .alert(closed.failureTitle, isPresented: Binding(
                            get: { season.quickActionErrorMessage != nil },
                            set: { if !$0 { season.quickActionErrorMessage = nil } }
                        )) {
                            Button("OK", role: .cancel) {}
                        } message: {
                            Text(season.quickActionErrorMessage ?? "")
                        }
                        .sensoryFeedback(.error, trigger: season.quickActionErrorMessage) { _, message in
                            message != nil
                        }
                }
            }
            .sheet(item: $ticks.episodes, onDismiss: {
                if let selection = ticks.sheetDidClose() { onOpen(selection) }
            }) { episodes in
                ContinueWatchingEpisodesSheet(
                    episodes: episodes,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    onUnauthorized: onUnauthorized
                )
            }
            .sensoryFeedback(.selection, trigger: ticks.episodes?.id) { _, opened in opened != nil }
    }
}

/// Continue Watching's episode sheet (TV-701): half height, with the season's episodes in the season page's rows,
/// ✕ to close, and a ··· menu with Log Season… and Open Show. It opens with up next's row at the top, which shows at
/// once from the card while the rest of the season loads.
private struct ContinueWatchingEpisodesSheet: View {
    let episodes: ContinueWatchingEpisodes
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var isLogPresented = false
    @State private var failures = 0

    var body: some View {
        let season = episodes.season
        NavigationStack {
            ContinueWatchingEpisodeList(episodes: episodes)
                // A new season is a new list: its rows don't animate from the last season's ticks.
                .id(episodes.seasonNumber)
                .navigationTitle(episodes.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(role: .close) {
                            dismiss()
                        } label: {
                            Label("Close", systemImage: "xmark")
                        }
                    }
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            Button("Log \(TVTrackingPresentation.seasonTitle(episodes.seasonNumber))…",
                                   systemImage: "square.and.pencil") {
                                openLog()
                            }
                            .disabled(!episodes.canLogSeason)
                            Button("Open Show", systemImage: "tv") {
                                episodes.openShow()
                            }
                        } label: {
                            Label("More", systemImage: "ellipsis")
                        }
                        .accessibilityIdentifier("continue-watching.episodes.more")
                    }
                }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
        .task(id: episodes.seasonNumber) {
            // Loads the rest of the season around up next's row. A sheet coming back from a cover over it keeps its
            // list and its place.
            let season = episodes.season
            guard season.detail == nil else { return }
            await season.load()
        }
        .alert(episodes.failureTitle, isPresented: Binding(
            get: { season.quickActionErrorMessage != nil },
            set: { if !$0 { season.quickActionErrorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(season.quickActionErrorMessage ?? "")
        }
        .alert(season.tvNotice ?? "", isPresented: Binding(
            get: { season.tvNotice != nil },
            set: { if !$0 { season.tvNotice = nil } }
        )) {
            Button("OK", role: .cancel) { season.tvNotice = nil }
        }
        .confirmationDialog(
            season.tvConfirmation?.prompt ?? "",
            isPresented: Binding(
                get: { season.tvConfirmation != nil },
                set: { if !$0 { season.tvConfirmation = nil } }
            ),
            titleVisibility: .visible,
            presenting: season.tvConfirmation
        ) { confirmation in
            Button(confirmation.confirmTitle, role: confirmation.isDestructive ? .destructive : nil) {
                episodes.confirm(confirmation)
            }
            Button("Cancel", role: .cancel) { season.tvConfirmation = nil }
        }
        // A failed write restores its row (TV-705), with an error haptic and the alert above.
        .onChange(of: season.quickActionErrorMessage) { _, message in
            if message != nil { failures += 1 }
        }
        .sensoryFeedback(.error, trigger: failures)
        // The earlier-episodes question, then Log Season, over the sheet (TV-107, TV-403).
        .tvWriteFollowUp(season.tvFollowUp) { item in
            ContinueWatchingFinishComposer(
                item: item,
                episodes: episodes,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized
            )
        }
        .onChange(of: season.tvFollowUp.composer?.id) { closed, open in
            if closed != nil, open == nil { episodes.composerClosed() }
        }
        .fullScreenCover(isPresented: $isLogPresented, onDismiss: logClosed) {
            if let detail = season.detail {
                MediaLogView(
                    detail: detail,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    stateChangeOrigin: season.changeOrigin,
                    onResult: { season.applyLogSave($0) },
                    onUnauthorized: onUnauthorized,
                    onSaved: {},
                    tvPage: episodes.seasonRef
                )
            }
        }
    }

    /// Log Season… opens the season page's composer once the season's writes have settled, as on its page (TV-404).
    private func openLog() {
        let season = episodes.season
        Task {
            guard await season.settled() else { return }
            isLogPresented = true
        }
    }

    /// The log's question or the next composer can follow now; a saved log may move the sheet on a season.
    private func logClosed() {
        episodes.season.releaseDeferredTVFollowUp()
        episodes.composerClosed()
    }
}

/// The episode sheet's list of one season (TV-701), opening with up next's row at the top. It starts as up next's row
/// alone, and when the rest of the season arrives it shows those rows in the same update that asks for up next's
/// row, so SwiftUI places that row itself instead of guessing how far the rows above it moved it.
private struct ContinueWatchingEpisodeList: View {
    let episodes: ContinueWatchingEpisodes
    /// Where the list is scrolled: on up next's row, until the user scrolls it.
    @State private var position: ScrollPosition
    /// The list on screen (`ContinueWatchingEpisodes.listID`): it catches up with the model together with the scroll
    /// position.
    @State private var shownListID: String

    init(episodes: ContinueWatchingEpisodes) {
        self.episodes = episodes
        _position = State(initialValue: ScrollPosition(id: episodes.startRowID, anchor: .top))
        _shownListID = State(initialValue: episodes.listID)
    }

    /// The season's rows once the list shows them; until then up next's row alone, as it opened.
    private var rows: [TVEpisodeRowModel] {
        shownListID == episodes.listID ? episodes.rows : [TVEpisodeRowModel.upNext(episodes.upNext)]
    }

    var body: some View {
        let season = episodes.season
        let rows = rows
        let rowsFromStart = ContinueWatchingEpisodes.rowsFromStart(rows: rows, startRowID: episodes.startRowID)
        ScrollView {
            VStack(spacing: TVEpisodeRows.spacing) {
                // Every row laid out, not a lazy stack: a lazy stack guesses the height of the rows above up next
                // that it hasn't built, and moved up next by rows when its guesses changed, as the sheet grew to its
                // full height. A season is at most a few dozen rows.
                VStack(spacing: TVEpisodeRows.spacing) {
                    TVEpisodeRows(
                        rows: rows,
                        state: { season.tvEpisodeState($0.tv) },
                        isWatched: { season.tvIsWatched($0) },
                        onTick: { episodes.toggle($0) },
                        onMarkUpTo: { season.askMarkTVUpTo($0) },
                        onSelect: { episodes.openEpisode($0) }
                    )
                }
                .scrollTargetLayout()

                if season.detail == nil {
                    loadingState(season)
                }

                // Room under the last row, so up next's row reaches the top even near the end of the season.
                Color.clear
                    .containerRelativeFrame(.vertical) { height, _ in
                        ContinueWatchingEpisodes.roomBelowRows(viewportHeight: height, rowsFromStart: rowsFromStart)
                    }
                    .accessibilityHidden(true)
            }
        }
        // Margins the scroll view knows about, so opening on a row further down doesn't also shift the rows
        // sideways by the padding.
        .contentMargins(.horizontal, 16, for: .scrollContent)
        .contentMargins(.top, 4, for: .scrollContent)
        .contentMargins(.bottom, 24, for: .scrollContent)
        .scrollPosition($position, anchor: .top)
        .onChange(of: episodes.listID) { _, listID in
            // The season's rows and up next's row, in one update.
            shownListID = listID
            position.scrollTo(id: episodes.startRowID, anchor: .top)
        }
    }

    @ViewBuilder
    private func loadingState(_ season: MediaDetailViewModel) -> some View {
        if let error = season.errorMessage {
            VStack(spacing: 8) {
                Text("Could not load the rest of the season")
                    .font(.footnote.weight(.semibold))
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button("Try Again") {
                    Task { await season.load() }
                }
                .font(.footnote.weight(.semibold))
            }
            .frame(maxWidth: .infinity)
        } else {
            ProgressView()
                .frame(maxWidth: .infinity)
        }
    }
}

/// Log Season after a write in the episode sheet finished its season (TV-403): the season page's finish composer.
private struct ContinueWatchingFinishComposer: View {
    let item: TVFollowUpComposer
    let episodes: ContinueWatchingEpisodes
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    var body: some View {
        let season = episodes.season
        MediaLogView(
            detail: item.detail,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            stateChangeOrigin: season.changeOrigin,
            onResult: { season.applyLogSave($0, followsUp: false) },
            onUnauthorized: onUnauthorized,
            onSaved: {},
            tvComposer: item.finished.composer,
            tvPage: episodes.seasonRef
        )
    }
}
