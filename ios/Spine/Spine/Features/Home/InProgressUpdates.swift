import SwiftUI

/// Update progress from Home's In progress cards (TV-701). Every card's title and its "Update progress ›" line are
/// one button that opens the title's own progress UI over Home, as the title's page has it:
///
/// - a show: its episode sheet, on up next's season (`ContinueWatchingTicks`);
/// - a book: the page's progress sheet, with Finished Reading and the journey's other choices in ⋯;
/// - a game: the page's progress sheet, with Finished Playing, and Edit Dates and the rest in ⋯;
/// - a manga or a comic: the page's log composer, on Progress.
///
/// A book's or a game's sheet opens at once from the card, as a show's does, while the page loads behind it; its
/// writes wait for that load. A manga's or a comic's composer needs the loaded page, so it opens after it.
///
/// Each runs on the page's own model (`MediaDetailViewModel`) posting as Home, so the card stays where it is, Home
/// reloads only its activity, and the rail re-sorts on the next visit. Finishing a book or a game opens
/// the page's completion log. A card with nothing to update, or a type whose page has no progress UI (anime, movies,
/// music), opens the title's page.
@MainActor
@Observable
final class InProgressUpdates {
    /// Where a card's "Update progress ›" goes.
    enum Route: Equatable {
        /// A show's episode sheet.
        case episodes
        /// The title's progress UI from its page, once the page has loaded.
        case progress
        /// The title's page.
        case page
    }

    static func route(for item: LibraryItem) -> Route {
        let mediaType = item.media.ref.mediaType
        if mediaType == "tv" {
            // A show a tick just caught up or finished has no up next to open until the rail reloads.
            return item.tracking.tv?.upNext == nil ? .page : .episodes
        }
        if ["book", "game"].contains(mediaType) || MediaLogViewModel.supportsProgress(mediaType: mediaType) {
            return .progress
        }
        return .page
    }

    /// The title whose progress UI is on screen, or was last: kept until another card opens, so a write still on its
    /// way can say it failed.
    private(set) var update: InProgressUpdate?
    /// The card whose page is loading before its composer opens (a manga or a comic).
    private(set) var loadingItemID: String?
    /// A write changed a card since the rail last loaded: the screen re-sorts the rail on its next visit.
    private(set) var needsReload = false
    /// Counts the progress UIs opened, for the screen's selection haptic.
    private(set) var openings = 0
    /// Shows' episode sheets. Its `changeOrigin` is the one every progress UI on the screen posts with.
    let continueWatching: ContinueWatchingTicks
    /// Puts a card's new status and progress in place in the screen's rail, for what VoiceOver reads on it.
    @ObservationIgnored var onUpdate: (_ itemId: String, _ state: UserMediaState) -> Void = { _, _ in }

    /// Counts the card taps and the screen's other opens: a page that loads after a later one opens nothing.
    @ObservationIgnored private var request = 0
    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private let trackingRepository: TrackingRepository
    @ObservationIgnored private let diaryRepository: DiaryRepository
    @ObservationIgnored private let onUnauthorized: () -> Void

    init(
        continueWatching: ContinueWatchingTicks,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.continueWatching = continueWatching
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
    }

    /// A card's title and "Update progress ›". Returns the page to open instead, if any. It replaces a card still
    /// waiting for its page.
    func updateProgress(for item: LibraryItem) async -> MediaRef? {
        cancelPendingOpen()
        switch Self.route(for: item) {
        case .episodes:
            continueWatching.openEpisodes(for: item)
            return nil
        case .page:
            return item.media.ref
        case .progress:
            if ["book", "game"].contains(item.media.ref.mediaType) {
                return openSheet(for: item)
            }
            return await openComposer(for: item)
        }
    }

    /// The screen opened something else, or went away: a card still waiting for its page opens nothing, neither its
    /// progress UI nor its page.
    func cancelPendingOpen() {
        request += 1
        loadingItemID = nil
    }

    /// The rail loaded again, in a fresh order.
    func railDidLoad() {
        needsReload = false
    }

    /// A book's or a game's sheet, at once from the card, with its page loading behind it. Returns the page to open
    /// instead when the card has nothing to update.
    private func openSheet(for item: LibraryItem) -> MediaRef? {
        let update = makeUpdate(for: item)
        guard update.openSheet() else { return item.media.ref }
        self.update = update
        openings += 1
        return nil
    }

    /// Loads a manga's or a comic's page, then opens its composer on Progress. Returns the page to open instead when
    /// there's nothing to update there or the page didn't load: the page says why. Nothing opens when another tap, or
    /// something else on the screen, came first.
    private func openComposer(for item: LibraryItem) async -> MediaRef? {
        let update = makeUpdate(for: item)
        let ticket = request
        loadingItemID = item.id
        await update.page.load()
        guard ticket == request else { return nil }
        loadingItemID = nil
        guard update.openComposer() else { return item.media.ref }
        self.update = update
        openings += 1
        return nil
    }

    private func makeUpdate(for item: LibraryItem) -> InProgressUpdate {
        let page = MediaDetailViewModel(
            ref: item.media.ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized,
            changeOrigin: continueWatching.changeOrigin
        )
        return InProgressUpdate(item: item, page: page) { [weak self] state in
            guard let self else { return }
            needsReload = true
            if let state { onUpdate(item.id, state) }
        }
    }

    /// The rail with one card's status and progress replaced where it is, from its page after a write.
    static func replacingProgress(_ state: UserMediaState, id: String, in items: [LibraryItem]) -> [LibraryItem] {
        items.map { item in
            guard item.id == id else { return item }
            let tracking = item.tracking
            return LibraryItem(media: item.media, tracking: TrackingState(
                trackingId: tracking.trackingId,
                status: state.status,
                rating: tracking.rating,
                progress: state.progress,
                latestProgressChange: tracking.latestProgressChange,
                repeats: tracking.repeats,
                startDate: tracking.startDate,
                endDate: tracking.endDate,
                notes: tracking.notes,
                updatedAt: tracking.updatedAt,
                liked: tracking.liked,
                directConsumption: tracking.directConsumption,
                ratingSourceDiaryEntryId: tracking.ratingSourceDiaryEntryId,
                likeSourceDiaryEntryId: tracking.likeSourceDiaryEntryId,
                likeIsIndependent: tracking.likeIsIndependent,
                diaryCount: tracking.diaryCount,
                book: state.book,
                game: state.game,
                tv: tracking.tv
            ))
        }
    }
}

/// One title's progress UI over the screen, and what it opens next, as the title's page runs them
/// (`MediaDetailPageView`'s progress sheets and completion log).
@MainActor
@Observable
final class InProgressUpdate {
    /// The log composer over the screen: Progress for a manga or a comic, or the completion log.
    struct Composer: Identifiable {
        let id = UUID()
        let mode: MediaLogMode
        /// The book journey or game playthrough a completion log finishes.
        let completionJourneyId: Int?
    }

    private enum Next {
        case dates(GamePlaythroughState)
        case composer(Composer)
    }

    let item: LibraryItem
    /// The title's page model: its writes, one request each through its queue, show at once and roll back on failure.
    let page: MediaDetailViewModel
    /// The book's progress sheet, opened from the card.
    var bookPanel: ProgressSheetTitle?
    /// The game's progress sheet, for its current playthrough.
    var gameProgress: GamePlaythroughState?
    /// The playthrough editor the game sheet's Edit Dates opens.
    var gameDates: GamePlaythroughState?
    var composer: Composer?
    /// A choice closed a sheet; what follows waits for it to go (`sheetDidClose`).
    private(set) var isClosing = false
    @ObservationIgnored private var next: Next?
    /// The page to open once the sheet has gone: the page loaded with nothing left to update.
    @ObservationIgnored private var destination: MediaRef?
    /// The page's load behind a sheet opened from the card.
    @ObservationIgnored private var loading: Task<Void, Never>?
    /// A write landed: the page's confirmed state, for the card.
    @ObservationIgnored private let onChange: (UserMediaState?) -> Void

    init(item: LibraryItem, page: MediaDetailViewModel, onChange: @escaping (UserMediaState?) -> Void) {
        self.item = item
        self.page = page
        self.onChange = onChange
    }

    /// Something of the title's covers the screen.
    var isPresenting: Bool {
        bookPanel != nil || gameProgress != nil || gameDates != nil || composer != nil
    }

    /// A write that failed with nothing covering the screen: the page's "Tracking Update Failed" alert, over it.
    var showsFailure: Bool {
        page.quickActionErrorMessage != nil && !isPresenting && !isClosing
    }

    /// The book's sheet's title: the page's once loaded, which knows the page count; until then the card's.
    var bookTitle: ProgressSheetTitle {
        page.detail.map(ProgressSheetTitle.init(detail:)) ?? ProgressSheetTitle(item: item)
    }

    /// Where the book stands, from the page once loaded; until then the card's.
    var bookProgress: ProgressState? {
        page.viewer.map(\.progress) ?? item.tracking.progress
    }

    var bookJourneyStatus: String? {
        page.viewer.map { $0.book?.currentJourney?.status } ?? item.tracking.book?.currentJourney?.status
    }

    /// Opens a book's or a game's sheet from its card, as its page would, and loads the page behind it. False when the
    /// card has nothing to update: a book that isn't being read, or a game without a playthrough.
    func openSheet() -> Bool {
        let tracking = item.tracking
        switch item.media.ref.mediaType {
        case "book":
            // The page's Update Progress shows while the book is being read or paused.
            guard ["In progress", "Paused"].contains(tracking.status ?? "") else { return false }
            bookPanel = ProgressSheetTitle(item: item)
        case "game":
            guard let game = tracking.game, game.canUpdateProgress, let playthrough = game.currentPlaythrough else {
                return false
            }
            gameProgress = playthrough
        default:
            return false
        }
        loading = Task {
            await page.load()
            pageDidLoad()
        }
        return true
    }

    /// Opens a manga's or a comic's composer on Progress from its loaded page. False when the page has nothing to
    /// update.
    func openComposer() -> Bool {
        guard let detail = page.detail, page.viewer != nil,
              MediaLogViewModel.supportsProgress(mediaType: detail.ref.mediaType) else { return false }
        composer = Composer(mode: .progress, completionJourneyId: nil)
        return true
    }

    /// The page behind a sheet has loaded. When it no longer offers the update (the title changed elsewhere since the
    /// rail loaded), the sheet closes and the page opens, saying where the title stands. A page that didn't load keeps
    /// the sheet: a save tries again.
    private func pageDidLoad() {
        guard let viewer = page.viewer, !isClosing, bookPanel != nil || gameProgress != nil else { return }
        let offersUpdate = item.media.ref.mediaType == "book"
            ? ["In progress", "Paused"].contains(viewer.status ?? "")
            : viewer.game?.canUpdateProgress == true
        guard !offersUpdate else { return }
        destination = item.media.ref
        isClosing = true
        bookPanel = nil
        gameProgress = nil
    }

    /// The page, for a write: once its load behind the sheet has finished, and tried again if it failed. Nil when it
    /// fails again; the page's error says why.
    private func loadedPage() async -> MediaDetail? {
        await loading?.value
        if page.detail == nil {
            await page.load()
        }
        return page.detail
    }

    /// Runs a write on the loaded page: at once when it has loaded, so it shows at once; otherwise once it loads. A
    /// page that can't load says so in the alert over the screen.
    private func whenLoaded(_ write: @escaping (MediaDetail) -> Void) {
        if let detail = page.detail {
            write(detail)
            return
        }
        Task {
            guard let detail = await loadedPage() else {
                page.quickActionErrorMessage = page.errorMessage
                return
            }
            write(detail)
        }
    }

    // MARK: Book: the page's progress sheet

    func saveBookProgress(_ request: ProgressUpdateSaveRequest) async -> Bool {
        guard let detail = await loadedPage() else {
            page.progressErrorMessage = page.errorMessage
            return false
        }
        return await tracked(page.saveProgress(request, for: detail)).value
    }

    /// Pause, Resume, Did Not Finish and Restart from the sheet's ⋯. The sheet closes on the tap.
    func performBookAction(_ action: String) {
        isClosing = true
        whenLoaded { [self] detail in
            _ = tracked(page.performBookAction(action, for: detail))
        }
    }

    func deleteBookJourney() {
        isClosing = true
        whenLoaded { [self] detail in
            guard let journey = page.viewer?.book?.currentJourney else { return }
            _ = tracked(page.deleteBookJourney(journey, for: detail))
        }
    }

    // MARK: Game: the page's progress sheet and playthrough editor

    func saveGame(_ playthrough: GamePlaythroughState, _ request: GamePlaythroughWriteRequest) async -> Bool {
        guard let detail = await loadedPage() else {
            page.formErrorMessage = page.errorMessage
            return false
        }
        return await tracked(page.updateGamePlaythrough(playthrough, request: request, for: detail)).value
    }

    /// Pause, Resume, Drop and Restart. The sheet closes itself on the tap.
    func performGameAction(_ action: String) {
        isClosing = true
        whenLoaded { [self] detail in
            _ = tracked(page.performGameAction(action, for: detail))
        }
    }

    func deletePlaythrough(_ playthrough: GamePlaythroughState) {
        isClosing = true
        whenLoaded { [self] detail in
            _ = tracked(page.deleteGamePlaythrough(playthrough, for: detail))
        }
    }

    /// Edit Dates: the playthrough editor, once the progress sheet has gone.
    func editDates(_ playthrough: GamePlaythroughState) {
        next = .dates(playthrough)
        gameProgress = nil
    }

    /// A sheet has gone: what a choice in it opens next, if anything. Returns the title's page when the page loaded
    /// with nothing left to update.
    @discardableResult
    func sheetDidClose() -> MediaRef? {
        isClosing = false
        defer { destination = nil }
        switch next {
        case let .dates(playthrough):
            next = nil
            page.formErrorMessage = nil
            gameDates = playthrough
        case let .composer(composer):
            next = nil
            self.composer = composer
        case nil:
            break
        }
        return destination
    }

    // MARK: Finishing: the page's completion log

    /// Finished, or progress saved at the end: the completion log, which captures the journey or playthrough, so it
    /// opens once the page has loaded and its writes have settled, from confirmed state. A sheet still open closes
    /// first, and the log follows its dismissal. Nothing opens when a write it waited for failed.
    func openCompletion() {
        if bookPanel != nil || gameProgress != nil || gameDates != nil {
            isClosing = true
            bookPanel = nil
            gameProgress = nil
            gameDates = nil
        }
        Task {
            guard await loadedPage() != nil else {
                page.quickActionErrorMessage = page.errorMessage
                return
            }
            guard await page.settled(), gameProgress == nil, gameDates == nil, composer == nil,
                  page.quickActionErrorMessage == nil else { return }
            let confirmed = page.detail?.userState
            let journeyId: Int?
            if page.detail?.ref.mediaType == "game" {
                let game = confirmed?.game
                journeyId = game?.hasLivePlaythrough == true ? game?.currentPlaythrough?.id : nil
            } else {
                journeyId = confirmed?.book?.currentJourney?.id
            }
            let composer = Composer(mode: .finished, completionJourneyId: journeyId)
            if isClosing { next = .composer(composer) } else { self.composer = composer }
        }
    }

    /// A saved log, from the completion log or a manga's or comic's Progress, folded into the page.
    func logSaved(_ result: MediaLogSaveResult) {
        page.applyLogSave(result)
        changed()
    }

    /// Once a write has landed, the card takes the page's confirmed state, and its rail re-sorts on the next visit.
    @discardableResult
    private func tracked(_ write: Task<Bool, Never>) -> Task<Bool, Never> {
        Task {
            let succeeded = await write.value
            if succeeded { changed() }
            return succeeded
        }
    }

    private func changed() {
        onChange(page.detail?.userState)
    }
}

/// What VoiceOver reads for a card's title and "Update progress ›", with where the title stands that the card no
/// longer shows: "Breaking Bad, update progress, S2 E2 next, 8 of 62", "Project Hail Mary, update progress, 40 of
/// 476 pages".
enum InProgressCardPresentation {
    static let line = "Update progress"

    static func accessibilityLabel(for item: LibraryItem) -> String {
        let title = item.media.displayTitle
        if item.media.ref.mediaType == "tv", let tv = item.tracking.tv {
            return TVTrackingPresentation.continueWatchingAccessibilityLabel(show: title, tv: tv)
        }
        let progress: String?
        if let game = item.tracking.game {
            progress = game.currentPlaythrough?.progress.summary.replacingOccurrences(of: " · ", with: ", ")
        } else {
            progress = item.tracking.progress?.detailDisplayText(
                preferredMode: ProgressDisplayPreferences.mode(for: item.media.ref)
            )
        }
        return [title, line.lowercased(), progress.flatMap { $0.isEmpty ? nil : $0 }]
            .compactMap { $0 }
            .joined(separator: ", ")
    }

    /// How far along the title is, for the bar on its poster, from stored values only: a show's watched over aired
    /// regular episodes (TV-104), a book's pages or percent in its current journey, a game playthrough's percent, or
    /// chapters, issues or episodes over the total. Nil, so no bar, without a known total or with nothing done yet;
    /// never more than 1.
    static func progressFraction(for item: LibraryItem) -> Double? {
        let tracking = item.tracking
        switch item.media.ref.mediaType {
        case "tv":
            if let tv = tracking.tv {
                return fraction(tv.watchedCount.map(Double.init), of: tv.airedCount.map(Double.init))
            }
        case "book":
            if let journey = tracking.book?.currentJourney?.progress { return fraction(of: journey) }
        case "game":
            // A playthrough with hours only has no bar.
            if let game = tracking.game {
                return fraction(game.currentPlaythrough?.percentage.map(Double.init), of: 100)
            }
        default:
            break
        }
        guard let progress = tracking.progress else { return nil }
        return fraction(of: progress)
    }

    private static func fraction(of progress: ProgressState) -> Double? {
        fraction(progress.value.map { NSDecimalNumber(decimal: $0).doubleValue },
                 of: progress.max.map { NSDecimalNumber(decimal: $0).doubleValue })
    }

    private static func fraction(_ value: Double?, of total: Double?) -> Double? {
        guard let value, let total, value > 0, total > 0 else { return nil }
        return min(value / total, 1)
    }
}

/// A card in Home's In progress rail (TV-701): the poster opens the title's page; the title and "Update progress ›"
/// are one button, at least 44 points tall, that opens its progress UI over Home. Every media type reads the same.
/// A thin bar along the poster's bottom fills to how far along the title is, when that is known.
/// Profile's In progress rail shows posters only.
struct InProgressCard: View {
    /// Room under the poster for a two-line title and the line, so every card in the rail is the same height and no
    /// title is cut short.
    @ScaledMetric(relativeTo: .caption) private var labelHeight: CGFloat = 56

    let item: LibraryItem
    /// The title's page is loading before its progress UI opens.
    var isLoading = false
    let onOpen: () -> Void
    let onUpdateProgress: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onOpen) {
                MediaArtwork(
                    url: item.media.displayPosterURL,
                    title: item.media.title,
                    slot: .carousel,
                    mediaType: item.media.ref.mediaType,
                    orientation: item.media.posterOrientation
                )
                .overlay(alignment: .bottom) {
                    if let progressFraction {
                        InProgressBar(fraction: progressFraction)
                    }
                }
                // A save from the card's progress UI moves the bar where it is.
                .animation(.smooth(duration: 0.3), value: progressFraction)
                .shadow(color: .black.opacity(0.26), radius: 10, y: 5)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("View \(item.media.displayTitle)")

            Button(action: onUpdateProgress) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.media.displayTitle)
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                    // The chevron keeps to "progress" when the line wraps.
                    Text("\(InProgressCardPresentation.line)\u{00A0}\(Image(systemName: "chevron.forward"))")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.62))
                        .lineLimit(2)
                }
                .padding(.top, 8)
                .frame(maxWidth: .infinity, minHeight: max(44, labelHeight), alignment: .topLeading)
                .contentShape(Rectangle())
                .opacity(isLoading ? 0.45 : 1)
            }
            .buttonStyle(InProgressCardButtonStyle())
            .disabled(isLoading)
            .accessibilityLabel(InProgressCardPresentation.accessibilityLabel(for: item))
            .accessibilityIdentifier("in-progress.update")
        }
        .frame(width: PosterSlot.carousel.size.width, alignment: .leading)
    }

    private var progressFraction: Double? {
        InProgressCardPresentation.progressFraction(for: item)
    }
}

/// The hairline on a card's poster: a translucent dark track inset from the corners, filled with the app's tint to
/// how far along the title is. VoiceOver skips it; the card's label already reads the progress.
private struct InProgressBar: View {
    private static let inset = PosterSlot.carousel.cornerRadius
    private static let width = PosterSlot.carousel.size.width - 2 * inset
    private static let height: CGFloat = 3

    let fraction: Double

    var body: some View {
        ZStack(alignment: .leading) {
            Capsule().fill(.black.opacity(0.45))
            // At least a dot, so a first page or episode shows.
            Capsule().fill(.tint).frame(width: max(Self.height, Self.width * fraction))
        }
        .frame(width: Self.width, height: Self.height)
        .padding(.bottom, Self.inset * 0.75)
        .accessibilityHidden(true)
    }
}

/// A card's title and line, one button: it dims while pressed.
private struct InProgressCardButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.45 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

extension View {
    /// What a card's "Update progress ›" opens over the screen when the title isn't a show: the book's and the game's
    /// progress sheets, the log composer, and the alert for a write that failed once they had gone. `onOpenPage` opens
    /// the title's page when a sheet closes because the page loaded with nothing left to update.
    /// `screenOpenedOther` is true while the screen shows something else it opened (a page, a person, a sheet): that,
    /// or leaving the screen, cancels a card still waiting for its page.
    func inProgressUpdatePresentations(
        _ updates: InProgressUpdates,
        screenOpenedOther: Bool,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        onOpenPage: @escaping (MediaRef) -> Void
    ) -> some View {
        modifier(InProgressUpdatePresentations(
            updates: updates,
            screenOpenedOther: screenOpenedOther,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized,
            onOpenPage: onOpenPage
        ))
    }
}

private struct InProgressUpdatePresentations: ViewModifier {
    let updates: InProgressUpdates
    let screenOpenedOther: Bool
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void
    let onOpenPage: (MediaRef) -> Void

    func body(content: Content) -> some View {
        content
            .background {
                if let update = updates.update {
                    Color.clear
                        .modifier(InProgressUpdateSheets(
                            update: update,
                            trackingRepository: trackingRepository,
                            diaryRepository: diaryRepository,
                            onUnauthorized: onUnauthorized,
                            onOpenPage: onOpenPage
                        ))
                        .id(ObjectIdentifier(update))
                }
            }
            .sensoryFeedback(.selection, trigger: updates.openings)
            // A card's progress UI doesn't open over something else, or after the user has gone elsewhere.
            .onChange(of: screenOpenedOther) { _, opened in
                if opened { updates.cancelPendingOpen() }
            }
            .onDisappear { updates.cancelPendingOpen() }
    }
}

/// The book's and the game's sheets, the log composer and the failure alert for one title (as on its page).
private struct InProgressUpdateSheets: ViewModifier {
    @Bindable var update: InProgressUpdate
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let onUnauthorized: () -> Void
    let onOpenPage: (MediaRef) -> Void

    func body(content: Content) -> some View {
        let page = update.page
        content
            .sheet(item: $update.bookPanel, onDismiss: sheetDidClose) { _ in
                ProgressUpdateSheet(
                    title: update.bookTitle,
                    progress: update.bookProgress,
                    isSaving: page.isSavingProgress,
                    errorMessage: page.progressErrorMessage,
                    journeyStatus: update.bookJourneyStatus,
                    onSave: { await update.saveBookProgress($0) },
                    onFinish: { update.openCompletion() },
                    onAction: { update.performBookAction($0) },
                    onDelete: { update.deleteBookJourney() }
                )
            }
            .sheet(item: $update.gameProgress, onDismiss: sheetDidClose) { playthrough in
                GameProgressSheet(
                    title: update.item.media.displayTitle,
                    playthrough: playthrough,
                    onSave: { await update.saveGame(playthrough, $0) },
                    onAction: { update.performGameAction($0) },
                    onDelete: { update.deletePlaythrough(playthrough) },
                    onFinish: { update.openCompletion() },
                    onEditDates: { update.editDates(playthrough) },
                    errorMessage: { page.formErrorMessage }
                )
            }
            .sheet(item: $update.gameDates, onDismiss: sheetDidClose) { playthrough in
                GamePlaythroughEditor(
                    playthrough: playthrough,
                    onSave: { await update.saveGame(playthrough, $0) },
                    onAction: { update.performGameAction($0) },
                    onDelete: { update.deletePlaythrough(playthrough) },
                    onFinish: { update.openCompletion() },
                    errorMessage: { page.formErrorMessage }
                )
            }
            .fullScreenCover(item: $update.composer) { composer in
                if let detail = page.detail {
                    MediaLogView(
                        detail: detail,
                        trackingRepository: trackingRepository,
                        diaryRepository: diaryRepository,
                        completionJourneyId: composer.completionJourneyId,
                        stateChangeOrigin: page.changeOrigin,
                        onResult: { update.logSaved($0) },
                        onUnauthorized: onUnauthorized,
                        onSaved: {},
                        mode: composer.mode
                    )
                }
            }
            .alert("Tracking Update Failed", isPresented: Binding(
                get: { update.showsFailure },
                set: { if !$0 { page.quickActionErrorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(page.quickActionErrorMessage ?? "")
            }
            .sensoryFeedback(.error, trigger: page.quickActionErrorMessage) { _, message in message != nil }
            // A failed page or hour save says so in its sheet, with an error haptic, as a failed tick does.
            .sensoryFeedback(.error, trigger: page.progressErrorMessage) { _, message in message != nil }
            .sensoryFeedback(.error, trigger: page.formErrorMessage) { _, message in message != nil }
    }

    private func sheetDidClose() {
        if let ref = update.sheetDidClose() { onOpenPage(ref) }
    }
}
