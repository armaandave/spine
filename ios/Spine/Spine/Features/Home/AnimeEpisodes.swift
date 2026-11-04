import SwiftUI

/// The sheet of one anime title's rows (AN-701), run by the title's page model: a tick, an untick, Mark watched up to
/// here and what they ask next work exactly as they do on its page. Home's In progress card opens it with a model of its
/// own, and a long title's page opens it for the whole list on the page's model (AN-101).
@MainActor
@Observable
final class AnimeEpisodes: Identifiable {
    /// The Home card the sheet opened from. Nil from the title's own page.
    let cardID: String?
    /// The title shown. Home's sheet moves on to the next title (AN-501).
    private(set) var ref: MediaRef
    /// The title shown: its page model loads its rows and sends its writes.
    private(set) var page: MediaDetailViewModel
    /// Up next as the sheet opened on this title: its row shows before the rows have loaded.
    private(set) var upNext: TVEpisodePointer?
    /// The episode the list opens on. It stays put as ticks move up next on.
    private(set) var startEpisode: Int?
    /// The title of the alert a failed write shows: what the last tap asked for.
    private(set) var failureTitle = ContinueWatchingEpisodes.tickFailureTitle
    /// Each write's state and the title it's about, whose card may be on the rail (TV-701).
    @ObservationIgnored var onStateChange: (_ ref: MediaRef, _ anime: AnimeState) -> Void = { _, _ in }
    /// Asks for a page: the screen closes the sheet first, then opens it.
    @ObservationIgnored var onOpen: (MediaBrowsingSelection) -> Void = { _ in }

    @ObservationIgnored private let makePage: (MediaRef) -> MediaDetailViewModel
    /// A write on this title since the sheet opened on it: the list stays where it opened.
    @ObservationIgnored private var hasWritten = false

    /// Home's sheet, starting from what the card already shows: its row shows at once while the rest load.
    init(item: LibraryItem, makePage: @escaping (MediaRef) -> MediaDetailViewModel) {
        cardID = item.id
        ref = item.media.ref
        self.makePage = makePage
        upNext = item.tracking.anime?.upNext?.tvPointer
        startEpisode = item.tracking.anime?.upNext?.episodeNumber
        page = makePage(item.media.ref)
        page.seedAnime(Self.seed(item: item))
        attach(page)
    }

    /// A long title's page opens the whole list on its own model: a write made before the sheet opened shows in it, and
    /// one still on its way when the sheet closes lands on the page, whose screen shows what it asks next or its failure
    /// (TV-701, TV-705).
    init(page: MediaDetailViewModel) {
        cardID = nil
        ref = page.ref
        self.page = page
        makePage = { _ in page }
        upNext = page.viewer?.anime?.upNext?.tvPointer
        startEpisode = page.detail?.animeRows?.startEpisode
    }

    var id: String { cardID ?? ref.id }
    var title: String { page.detail?.displayTitle ?? "" }
    var offersOpenPage: Bool { cardID != nil }

    /// The title's rows once they have loaded.
    var rows: AnimeRows? {
        guard let rows = page.detail?.animeRows, !rows.episodes.isEmpty else { return nil }
        return rows
    }

    /// The list the sheet shows: up next's row alone while the rows load, then every row. When it becomes every row, the
    /// list asks for up next's place in the same update, so it never shifts (TV-701).
    var listID: String {
        ref.id + (page.detail?.animeRows == nil ? "-up-next" : "")
    }

    /// The row at the top of the list as it opens: up next's, or the row a page's list opened on.
    var startRowID: String {
        guard let rows else { return upNext.map { TVEpisodeRowModel.upNext($0).id } ?? "" }
        let index = rows.episodes.firstIndex { $0.number == startEpisode } ?? rows.startIndex
        return rows.rowModel(at: index).id
    }

    /// Where that row is in the list.
    var startIndex: Int {
        guard let rows else { return 0 }
        return rows.episodes.firstIndex { $0.number == startEpisode } ?? rows.startIndex
    }

    /// Log… in the ··· menu: once the rows have loaded with the composer, when the title's page would offer it (AN-400).
    var canLog: Bool {
        guard let anime = page.viewer?.anime, anime.composer != nil else { return false }
        return anime.availableActions.contains("log")
    }

    func loadRowsIfNeeded() async {
        guard page.detail?.animeRows == nil else { return }
        await page.loadAnimeRows()
        guard let rows = page.detail?.animeRows else { return }
        if !hasWritten { startEpisode = rows.startEpisode }
    }

    /// Whether Log… can open: once the title's writes have settled, and not over what they asked next (AN-203).
    func readyForLog() async -> Bool {
        await page.settledForScreen()
    }

    /// A row's tick: ticks the episode, or unticks a ticked one (TV-100, TV-102).
    func toggle(_ episode: TVEpisodeState) {
        failureTitle = page.tvIsWatched(episode)
            ? ContinueWatchingEpisodes.untickFailureTitle
            : ContinueWatchingEpisodes.tickFailureTitle
        page.toggleTVEpisode(episode)
    }

    /// The dialog a long press's Mark watched up to here asks was confirmed (TV-108).
    func confirm(_ confirmation: TVPageConfirmation) {
        failureTitle = ContinueWatchingEpisodes.tickFailureTitle
        page.confirmTV(confirmation)
    }

    /// The log screen from the title's last tick closed, saved or not (AN-501). Home's sheet moves on when the next title
    /// has an aired episode and rows, and otherwise stays on this one, every tick filled. Moving on adds nothing to the
    /// library.
    private func finishComposerClosed() {
        guard cardID != nil, !page.tvFollowUp.isActive,
              let next = page.viewer?.anime?.nextTitle, next.hasAired, !next.single
        else { return }
        ref = next.ref
        upNext = nil
        startEpisode = nil
        hasWritten = false
        failureTitle = ContinueWatchingEpisodes.tickFailureTitle
        page = makePage(next.ref)
        page.seedAnime(Self.seed(next: next))
        attach(page)
    }

    /// Open Anime in the ··· menu.
    func openPage() {
        onOpen(MediaBrowsingSelection(ref: ref, within: [ref]))
    }

    private func attach(_ model: MediaDetailViewModel) {
        model.onAnimeChange = { [weak self] anime in
            guard let self else { return }
            hasWritten = true
            onStateChange(ref, anime)
        }
        model.tvFollowUp.onComposerClosed = { [weak self] in self?.finishComposerClosed() }
    }

    /// What the card knows of its title: enough to show up next's row and a log screen's header.
    private static func seed(item: LibraryItem) -> MediaDetail {
        let media = item.media
        return MediaDetail(
            ref: media.ref,
            title: media.title,
            preferredTitle: media.preferredTitle,
            imageUrl: media.imageUrl,
            posterUrl: media.posterUrl,
            posterOrientation: media.posterOrientation,
            posterAccentColor: media.posterAccentColor,
            userState: UserMediaState(
                isTracked: true,
                trackingId: item.tracking.trackingId,
                status: item.tracking.status,
                rating: item.tracking.rating,
                hasLiked: item.tracking.liked ?? false,
                anime: item.tracking.anime
            ),
            backdropUrl: media.backdropUrl,
            customPosterUrl: media.customPosterUrl,
            customBackdropUrl: media.customBackdropUrl
        )
    }

    private static func seed(next: AnimeNextTitle) -> MediaDetail {
        MediaDetail(ref: next.ref, title: next.title, preferredTitle: next.title, posterUrl: next.imageUrl, userState: .untracked)
    }
}

/// Where the rows of an anime list sit. Every row is the same height, so the list works out each row's place instead of
/// laying out a thousand rows or leaving a lazy stack to guess them: it builds only the rows around what's on screen,
/// between two spacers that stand in for the rest (AN-101).
nonisolated struct AnimeListMetrics: Equatable {
    /// The height of a row at the accessibility text sizes, whose rows stack their still and text.
    static let accessibilityRowHeight: CGFloat = 460
    /// Rows are built in chunks, so scrolling changes what's built only every few rows.
    static let chunk = 8

    let rowHeight: CGFloat
    let spacing: CGFloat
    let count: Int

    init(isAccessibilitySize: Bool = false, count: Int) {
        rowHeight = isAccessibilitySize ? Self.accessibilityRowHeight : TVEpisodeRows.rowHeight
        spacing = TVEpisodeRows.spacing
        self.count = count
    }

    /// From one row's top to the next's.
    var stride: CGFloat { rowHeight + spacing }

    /// How far down the list a row's top is.
    func offset(ofRow index: Int) -> CGFloat {
        CGFloat(index) * stride
    }

    /// The rows to build when the list's top `y` points down it shows `height` points of it: everything on screen and a
    /// chunk or more either side. It moves a whole chunk at a time, so scrolling changes what's built only every few rows,
    /// and it doesn't stop at the list's end, so a row a tick adds is there at once. `built` keeps it to the list.
    func window(y: CGFloat, height: CGFloat) -> Range<Int> {
        guard stride > 0 else { return 0..<0 }
        let firstChunk = Int(max(0, y) / stride) / Self.chunk
        let chunksOnScreen = Int((max(0, height) / stride).rounded(.up)) / Self.chunk + 1
        return max(0, firstChunk - 1) * Self.chunk..<(firstChunk + chunksOnScreen + 1) * Self.chunk
    }

    /// The rows of a window this list has.
    func built(_ window: Range<Int>) -> Range<Int> {
        window.clamped(to: 0..<count)
    }

    /// The window as the list opens on a row: a row at its top, whatever its height turns out to be.
    func window(openingOnRow index: Int) -> Range<Int> {
        window(y: offset(ofRow: index), height: 900)
    }

    /// The spacer above the window's rows.
    func heightAbove(_ window: Range<Int>) -> CGFloat {
        CGFloat(window.lowerBound) * stride
    }

    /// The spacer below the window's rows.
    func heightBelow(_ window: Range<Int>) -> CGFloat {
        CGFloat(max(0, count - window.upperBound)) * stride
    }

    /// The room the list leaves under its last row, so a row near the end can reach the top: the list's height and two
    /// rows more, less the rows from it down. The two rows cover the bars over the list's top and bottom.
    func roomBelowRows(viewportHeight: CGFloat, openingOnRow index: Int) -> CGFloat {
        max(0, viewportHeight - CGFloat(count - index - 2) * stride)
    }
}

/// The sheet's list of one title, opening with up next's row at the top, every time: on the first open and every reopen,
/// at the half and the full height, before and after the rows load, and however long the title is (AN-101, AN-701).
private struct AnimeEpisodeList: View {
    let episodes: AnimeEpisodes
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// Where the list is scrolled: on up next's row, until the user scrolls it. It names a row, not an offset, so the list
    /// doesn't depend on how an OS version counts the bars over it.
    @State private var position: ScrollPosition
    /// The rows built around what's on screen.
    @State private var window: Range<Int>
    /// The list on screen (`AnimeEpisodes.listID`): it catches up with the model together with the scroll position.
    @State private var shownListID: String

    init(episodes: AnimeEpisodes, isAccessibilitySize: Bool) {
        self.episodes = episodes
        let metrics = AnimeListMetrics(
            isAccessibilitySize: isAccessibilitySize,
            count: episodes.rows?.episodes.count ?? 0
        )
        _position = State(initialValue: ScrollPosition(id: episodes.startRowID, anchor: .top))
        _window = State(initialValue: metrics.window(openingOnRow: episodes.startIndex))
        _shownListID = State(initialValue: episodes.listID)
    }

    private var metrics: AnimeListMetrics {
        AnimeListMetrics(
            isAccessibilitySize: dynamicTypeSize.isAccessibilitySize,
            count: episodes.rows?.episodes.count ?? 0
        )
    }

    var body: some View {
        let page = episodes.page
        let viewer = page.viewer
        let metrics = metrics
        let rows = shownListID == episodes.listID ? episodes.rows : nil
        let start = episodes.startIndex
        ScrollView {
            VStack(spacing: 0) {
                if let rows {
                    let built = metrics.built(window)
                    Color.clear
                        .frame(height: metrics.heightAbove(built))
                        .accessibilityHidden(true)
                    VStack(spacing: metrics.spacing) {
                        rowList(built.map { rows.rowModel(at: $0) }, viewer: viewer, page: page, height: metrics.rowHeight)
                    }
                    .scrollTargetLayout()
                    Color.clear
                        .frame(height: metrics.heightBelow(built))
                        .accessibilityHidden(true)
                } else {
                    VStack(spacing: metrics.spacing) {
                        if let upNext = episodes.upNext {
                            rowList([TVEpisodeRowModel.upNext(upNext)], viewer: viewer, page: page, height: metrics.rowHeight)
                        }
                    }
                    .scrollTargetLayout()
                    if page.detail?.animeRows == nil {
                        loadingState(page)
                            .padding(.top, metrics.spacing)
                    }
                }
                // Room under the last row, so up next's row reaches the top even near the end.
                Color.clear
                    .containerRelativeFrame(.vertical) { height, _ in
                        rows == nil ? 0 : metrics.roomBelowRows(viewportHeight: height, openingOnRow: start)
                    }
                    .accessibilityHidden(true)
            }
        }
        .contentMargins(.horizontal, 16, for: .scrollContent)
        .contentMargins(.top, 4, for: .scrollContent)
        .contentMargins(.bottom, 24, for: .scrollContent)
        .scrollPosition($position, anchor: .top)
        .onScrollGeometryChange(for: Range<Int>.self) { geometry in
            metrics.window(
                y: geometry.contentOffset.y + geometry.contentInsets.top,
                height: geometry.containerSize.height
            )
        } action: { _, built in
            if built != window { window = built }
        }
        .onChange(of: episodes.listID) { _, listID in
            // Every row and up next's place, in one update.
            shownListID = listID
            window = metrics.window(openingOnRow: episodes.startIndex)
            position.scrollTo(id: episodes.startRowID, anchor: .top)
        }
    }

    private func rowList(
        _ models: [TVEpisodeRowModel],
        viewer: UserMediaState?,
        page: MediaDetailViewModel,
        height: CGFloat
    ) -> some View {
        TVEpisodeRows(
            rows: models,
            rowHeight: height,
            state: { page.tvEpisodeState($0.tv) },
            isWatched: { viewer?.tvIsWatched($0) ?? page.tvIsWatched($0) },
            onTick: { episodes.toggle($0) },
            onMarkUpTo: { page.askMarkTVUpTo($0) },
            onSelect: nil
        )
    }

    @ViewBuilder
    private func loadingState(_ page: MediaDetailViewModel) -> some View {
        if let error = page.errorMessage {
            VStack(spacing: 8) {
                Text("Could not load the episodes")
                    .font(.footnote.weight(.semibold))
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button("Try Again") {
                    Task { await episodes.loadRowsIfNeeded() }
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

/// The episode sheet (AN-701): half height, titled with the title, with its rows, ✕ to close, and a ··· menu with Log…
/// and Open Anime. It behaves as TV's sheet does (TV-701), and a row's body does nothing, because episodes have no pages
/// (AN-101).
struct AnimeEpisodesSheet: View {
    let episodes: AnimeEpisodes
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var isLogPresented = false
    @State private var failures = 0

    var body: some View {
        let page = episodes.page
        NavigationStack {
            AnimeEpisodeList(episodes: episodes, isAccessibilitySize: dynamicTypeSize.isAccessibilitySize)
                // The next title is a new list: its rows don't animate from the last title's ticks.
                .id(episodes.ref.id)
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
                            Button("Log…", systemImage: "square.and.pencil") {
                                openLog()
                            }
                            .disabled(!episodes.canLog)
                            if episodes.offersOpenPage {
                                Button("Open Anime", systemImage: "play.rectangle") {
                                    episodes.openPage()
                                }
                            }
                        } label: {
                            Label("More", systemImage: "ellipsis")
                        }
                        .accessibilityIdentifier("anime.episodes.more")
                    }
                }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
        .task(id: episodes.ref.id) {
            await episodes.loadRowsIfNeeded()
        }
        .alert(episodes.failureTitle, isPresented: Binding(
            get: { page.quickActionErrorMessage != nil },
            set: { if !$0 { page.quickActionErrorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(page.quickActionErrorMessage ?? "")
        }
        .alert(page.tvNotice ?? "", isPresented: Binding(
            get: { page.tvNotice != nil },
            set: { if !$0 { page.tvNotice = nil } }
        )) {
            Button("OK", role: .cancel) { page.tvNotice = nil }
        }
        .confirmationDialog(
            page.tvConfirmation?.prompt ?? "",
            isPresented: Binding(
                get: { page.tvConfirmation != nil },
                set: { if !$0 { page.tvConfirmation = nil } }
            ),
            titleVisibility: .visible,
            presenting: page.tvConfirmation
        ) { confirmation in
            Button(confirmation.confirmTitle, role: confirmation.isDestructive ? .destructive : nil) {
                episodes.confirm(confirmation)
            }
            Button("Cancel", role: .cancel) { page.tvConfirmation = nil }
        }
        // A failed write restores its row (TV-705), with an error haptic and the alert above.
        .onChange(of: page.quickActionErrorMessage) { _, message in
            if message != nil { failures += 1 }
        }
        .sensoryFeedback(.error, trigger: failures)
        // The earlier-episodes question, then the log screen, over the sheet (TV-107, AN-203).
        .tvWriteFollowUp(page.tvFollowUp) { item in
            AnimeFinishComposer(
                item: item,
                page: page,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(isPresented: $isLogPresented, onDismiss: { page.releaseDeferredTVFollowUp() }) {
            if let detail = page.detail {
                MediaLogView(
                    detail: detail,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    stateChangeOrigin: page.changeOrigin,
                    onResult: { page.applyLogSave($0) },
                    onUnauthorized: onUnauthorized,
                    onSaved: {}
                )
            }
        }
    }

    /// Log… opens the page's log screen once the title's writes have settled, as on its page (AN-400). A last tick still
    /// on its way opens its own log screen instead (AN-203).
    private func openLog() {
        Task {
            guard await episodes.readyForLog() else { return }
            isLogPresented = true
        }
    }
}

/// The log screen after a tick in the sheet finished its title (AN-203): dated that day, with Not now to close it.
struct AnimeFinishComposer: View {
    let item: TVFollowUpComposer
    let page: MediaDetailViewModel
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void

    var body: some View {
        MediaLogView(
            detail: item.detail,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            stateChangeOrigin: page.changeOrigin,
            onResult: { page.applyLogSave($0, followsUp: false) },
            onUnauthorized: onUnauthorized,
            onSaved: {},
            tvComposer: item.finished.composer,
            closeTitle: "Not now"
        )
    }
}
