import SwiftUI

enum MediaLogMode: String, CaseIterable, Identifiable {
    case finished
    case progress

    var id: String { rawValue }
}

/// What a saved log changed, so the presenting page can fold it in without a reload. `tracking` is nil when the
/// save returns no state the page can apply (tv, season and episode logs).
struct MediaLogSaveResult {
    let ref: MediaRef
    let tracking: TrackingState?
    let diaryEntry: DiaryEntry?
    /// A TV log's write envelope (`tv`, `finished`, `earlier_unwatched`), which the presenting page hands to its
    /// `TVWriteFollowUp` (TV-107, TV-403). Nil for other logs.
    var tvEnvelope: TVWriteEnvelope? = nil
}

@MainActor
@Observable
final class MediaLogViewModel {
    let detail: MediaDetail
    var mode: MediaLogMode = .finished
    /// The season a TV show log targets when the server sent no composer (nil for the whole show).
    var selectedSeasonNumber: Int?
    var reviewTitle = ""
    var review = ""
    let tagDraft = LogTagDraft()
    var containsSpoilers = false
    var visibility = "public"
    /// A TV log's target and defaults from the server (TV-404). The date, stars, heart and Rewatch below read and
    /// write it, so switching the target replaces only the values the user hasn't changed.
    private(set) var tvDraft: TVComposerDraft?
    private var storedConsumedAt = Date()
    private var storedRatingSteps = 0
    private var storedLiked = false
    private var storedIsRepeat = false
    var gameProgress = GameProgressDraft()
    var gameStartDate = Date()
    var gameHasStartDate = false
    var gamePlaythrough: GamePlaythroughState?
    var progressText = ""
    var progressType = "pages"
    var isSaving = false
    var errorMessage: String?
    let completionJourneyId: Int?
    /// The season-finish composer's server defaults (TV-403, TV-404); nil for every other log, which takes the
    /// page's own composer from `userState.tv`.
    let tvComposer: TVComposer?
    /// One per composer session: a retried save never logs twice (TV-705).
    private let completionMutationId = UUID()
    /// The TV page the composer was opened from, so a saved log's response carries that page's state and composer
    /// (TV-404, TV-705); nil when no page folds the response in.
    let tvPage: MediaRef?

    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let stateChangeOrigin: UUID?
    private let onUnauthorized: () -> Void
    private let onResult: ((MediaLogSaveResult) -> Void)?
    private let onSaved: () -> Void

    init(
        detail: MediaDetail,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        tracking: TrackingState? = nil,
        completionJourneyId: Int? = nil,
        preselectedLiked: Bool? = nil,
        preselectedRatingSteps: Int? = nil,
        stateChangeOrigin: UUID? = nil,
        onResult: ((MediaLogSaveResult) -> Void)? = nil,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping () -> Void,
        tvComposer: TVComposer? = nil,
        tvPage: MediaRef? = nil
    ) {
        self.detail = detail
        self.tvPage = tvPage
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.completionJourneyId = completionJourneyId
        self.tvComposer = tvComposer
        self.stateChangeOrigin = stateChangeOrigin
        self.onUnauthorized = onUnauthorized
        self.onResult = onResult
        self.onSaved = onSaved
        if detail.ref.isTVFamily {
            // The server works out every default when the composer opens (TV-404); the user's own picks win.
            tvDraft = (tvComposer ?? Self.pageComposer(of: detail)).flatMap { TVComposerDraft(composer: $0) }
            if tvDraft == nil {
                liked = detail.userState?.hasLiked ?? false
            }
            if let preselectedRatingSteps { ratingSteps = preselectedRatingSteps }
            if let preselectedLiked { liked = preselectedLiked }
            return
        }
        if detail.ref.isSingleWeight {
            let state = detail.userState
            isRepeat = state?.directConsumption == true
                || (state?.diaryCount ?? 0) > 0
                || (state?.directConsumption == nil
                    && state?.isTracked == true
                    && state?.status == "Completed")
        } else if detail.ref.mediaType == "game" {
            let game = tracking?.game ?? detail.userState?.game
            isRepeat = (game?.lifetimeCompletionCount ?? 0) > 0
            if game?.hasLivePlaythrough == true {
                gamePlaythrough = game?.currentPlaythrough
                gameProgress = GameProgressDraft(totalMinutes: gamePlaythrough?.totalMinutes, percentage: gamePlaythrough?.percentage)
            }
        } else if detail.ref.mediaType == "book" {
            isRepeat = tracking?.book?.isRereading ?? detail.userState?.book?.isRereading ?? false
        } else {
            isRepeat = (detail.userState?.diaryCount ?? 0) > 0
        }
        liked = preselectedLiked ?? tracking?.liked ?? detail.userState?.hasLiked ?? false
        if let preselectedRatingSteps {
            ratingSteps = preselectedRatingSteps
        } else if let rating = (tracking?.rating ?? detail.userState?.rating).flatMap({ Decimal(string: $0) }) {
            ratingSteps = detail.ref.usesFiveStarRatingScale
                ? NSDecimalNumber(decimal: rating * 2).intValue
                : NSDecimalNumber(decimal: rating).intValue
        }
    }

    // MARK: Draft values

    /// The log's calendar date (TV-407 for TV: capped at today, sent as a date).
    var consumedAt: Date {
        get { tvDraft?.dateValue() ?? storedConsumedAt }
        set {
            if tvDraft != nil { tvDraft?.setDate(newValue) } else { storedConsumedAt = newValue }
        }
    }

    /// Half steps 0-10; for TV these are the wire's whole values 1-10 (TV-504).
    var ratingSteps: Int {
        get { tvDraft?.ratingSteps ?? storedRatingSteps }
        set {
            if tvDraft != nil { tvDraft?.ratingSteps = newValue } else { storedRatingSteps = newValue }
        }
    }

    var liked: Bool {
        get { tvDraft?.liked ?? storedLiked }
        set {
            if tvDraft != nil { tvDraft?.liked = newValue } else { storedLiked = newValue }
        }
    }

    var isRepeat: Bool {
        get { tvDraft?.isRewatch ?? storedIsRepeat }
        set {
            if tvDraft != nil { tvDraft?.isRewatch = newValue } else { storedIsRepeat = newValue }
        }
    }

    // MARK: Targets

    var supportsProgress: Bool {
        ["manga", "comic", "boardgame"].contains(detail.ref.mediaType)
    }

    /// The server-less fallback for a show log: Whole Show or one of its seasons.
    var supportsSeasonLogging: Bool {
        tvDraft == nil && detail.ref.mediaType == "tv" && !(detail.seasons ?? []).isEmpty
    }

    /// The fallback picker's seasons: regular seasons, then Specials last (TV-004).
    var seasonPickerSeasons: [SeasonSummary] {
        let seasons = detail.seasons ?? []
        return seasons.filter { $0.seasonNumber != 0 } + seasons.filter { $0.seasonNumber == 0 }
    }

    /// The TV composer's targets: the seasons, Specials last, then Whole show (TV-404). Targets that can't be
    /// logged yet are left out unless selected.
    var tvTargets: [TVComposerTarget] {
        guard let tvDraft else { return [] }
        let targets = tvDraft.composer.targets.filter { $0.available || $0.id == tvDraft.target.id }
        let seasons = targets.filter { $0.level == .season && $0.seasonNumber != 0 }
        let specials = targets.filter { $0.level == .season && $0.seasonNumber == 0 }
        let episodes = targets.filter { $0.level == .episode }
        let others = targets.filter { ![.season, .episode, .show].contains($0.level) }
        let show = targets.filter { $0.level == .show }
        return episodes + seasons + specials + others + show
    }

    var supportsTVTargetPicker: Bool { tvTargets.count > 1 }

    /// Switching the target replaces only the defaults the user hasn't changed (TV-404).
    var selectedTVTargetId: String {
        get { tvDraft?.target.id ?? "" }
        set { tvDraft?.selectTarget(id: newValue) }
    }

    var selectedRef: MediaRef {
        if let tvDraft {
            let target = tvDraft.target.ref(show: detail.ref.parentTVRef ?? detail.ref)
            return Self.isSameTarget(target, detail.ref) ? detail.ref : target
        }
        guard let selectedSeasonNumber else { return detail.ref }
        return MediaRef(
            itemId: nil,
            source: detail.ref.source,
            mediaType: "season",
            mediaId: detail.ref.mediaId,
            seasonNumber: selectedSeasonNumber,
            episodeNumber: nil
        )
    }

    var selectedTitle: String {
        if let tvDraft, detail.ref.mediaType == "tv", tvDraft.target.level == .season {
            return "\(detail.title) · \(tvDraft.target.title)"
        }
        guard tvDraft == nil, let selectedSeasonNumber else { return detail.title }
        return "\(detail.title) \(TVTrackingPresentation.seasonTitle(selectedSeasonNumber))"
    }

    /// An episode page's composer is the episode's; show and season pages carry theirs on `tv`.
    private static func pageComposer(of detail: MediaDetail) -> TVComposer? {
        guard let tv = detail.userState?.tv else { return nil }
        return tv.episode?.composer ?? tv.composer
    }

    private static func isSameTarget(_ lhs: MediaRef, _ rhs: MediaRef) -> Bool {
        lhs.source == rhs.source && lhs.mediaType == rhs.mediaType && lhs.mediaId == rhs.mediaId
            && lhs.seasonNumber == rhs.seasonNumber && lhs.episodeNumber == rhs.episodeNumber
    }

    var repeatLabel: String {
        detail.ref.repeatLabel
    }

    var primaryActionTitle: String {
        switch selectedRef.mediaType {
        case "movie":
            "Log Movie"
        case "tv":
            "Log TV Show"
        case "season":
            "Log Season"
        case "episode":
            "Log Episode"
        case "anime":
            "Log Anime"
        case "manga":
            "Log Manga"
        case "comic":
            "Log Comic"
        case "game":
            "Log Completion"
        case "boardgame":
            "Log Board Game"
        case "book":
            "Log Book"
        case "music":
            isRepeat ? "Relisten" : "Log Album"
        default:
            "Log Entry"
        }
    }

    var progressUnit: String {
        switch detail.ref.mediaType {
        case "book":
            progressType == "percentage" ? "percent" : "pages"
        case "manga":
            "chapters"
        case "comic":
            "issues"
        case "game", "boardgame":
            "progress"
        default:
            "progress"
        }
    }

    var progressPlaceholder: String {
        if let maxProgress {
            return "0-\(maxProgress)"
        }
        return detail.ref.mediaType == "book" && progressType == "percentage" ? "0-100" : "Progress"
    }

    var maxProgress: Int? {
        switch detail.ref.mediaType {
        case "book":
            return detail.detailInt("number_of_pages") ?? detail.detailInt("pages") ?? detail.detailInt("total_pages")
        case "manga":
            return detail.detailInt("number_of_chapters") ?? detail.detailInt("chapters")
        case "comic":
            return detail.detailInt("issues_count") ?? detail.detailInt("issues")
        default:
            return nil
        }
    }

    static func ratingDecimal(for steps: Int, mediaType: String) -> Decimal? {
        guard steps > 0 else { return nil }
        return ["movie", "music", "book", "game"].contains(mediaType)
            ? Decimal(steps) / 2
            : Decimal(steps)
    }

    func ratingLabel(for steps: Int? = nil) -> String {
        let value = steps ?? ratingSteps
        guard value > 0 else { return "No rating" }
        let stars = Double(value) / 2
        return stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))/5" : "\(stars)/5"
    }

    func setRating(star: Int, half: Bool) {
        let next = star * 2 - (half ? 1 : 0)
        ratingSteps = ratingSteps == next ? 0 : next
    }

    func setRating(locationX: CGFloat, width: CGFloat) {
        guard width > 0 else { return }
        let clamped = min(max(locationX, 0), width)
        ratingSteps = min(10, Int(ceil((clamped / width) * 10)))
    }

    /// Loads the user's own tags once, for suggestions while they type.
    func loadTags() async {
        do {
            try await tagDraft.load(from: diaryRepository)
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save() async -> Bool {
        tagDraft.commitQuery()
        return mode == .progress ? await saveProgressOnly() : await saveFinishedLog()
    }

    func markOnly() async -> Bool {
        await performSave {
            let ref = selectedRef
            if
                ref.mediaType == "episode",
                let seasonNumber = ref.seasonNumber,
                let episodeNumber = ref.episodeNumber
            {
                _ = try await trackingRepository.watchEpisode(
                    source: ref.source,
                    mediaId: ref.mediaId,
                    seasonNumber: seasonNumber,
                    episodeNumber: episodeNumber,
                    watchedAt: consumedAt
                )
                return result(tracking: nil)
            } else if ref.mediaType == "season", let seasonNumber = ref.seasonNumber {
                _ = try await trackingRepository.watchSeason(source: ref.source, mediaId: ref.mediaId, seasonNumber: seasonNumber)
                return result(tracking: nil)
            } else if ref.mediaType == "book" {
                return result(tracking: try await trackingRepository.completeBook(
                    source: ref.source,
                    mediaId: ref.mediaId,
                    completedAt: consumedAt
                ))
            } else {
                return result(tracking: try await trackingRepository.consume(
                    ref: ref,
                    consumedAt: ref.isSingleWeight ? nil : consumedAt
                ))
            }
        }
    }

    private func saveFinishedLog() async -> Bool {
        await performSave {
            guard !selectedRef.usesCalendarLogDate || !CalendarDateCodec.isFuture(consumedAt) else {
                throw selectedRef.isTVFamily ? MediaLogError.futureLogDate : MediaLogError.futureDate
            }
            if selectedRef.isTVFamily {
                return try await saveTVLog()
            }
            if selectedRef.mediaType == "game" {
                let progress = try gameProgress.values()
                let response = try await trackingRepository.completeGame(ref: selectedRef, request: GameCompletionWriteRequest(
                    playthroughId: gamePlaythrough?.id,
                    completionDate: CalendarDateCodec.string(from: consumedAt),
                    startDate: gamePlaythrough == nil && gameHasStartDate ? CalendarDateCodec.string(from: gameStartDate) : nil,
                    totalMinutes: progress.totalMinutes, percentage: progress.percentage,
                    rating: Self.ratingDecimal(for: ratingSteps, mediaType: "game"),
                    review: review, reviewTitle: reviewTitle, liked: liked, isRewatch: isRepeat,
                    containsSpoilers: containsSpoilers, tags: tagDraft.tags, mutationId: completionMutationId))
                return result(tracking: response.tracking, diaryEntry: response.diaryEntry)
            } else if selectedRef.mediaType == "book" {
                let response = try await trackingRepository.completeBook(
                    source: selectedRef.source,
                    mediaId: selectedRef.mediaId,
                    request: BookCompletionWriteRequest(
                        journeyId: completionJourneyId,
                        completionDate: CalendarDateCodec.string(from: consumedAt),
                        rating: Self.ratingDecimal(for: ratingSteps, mediaType: selectedRef.mediaType),
                        review: review,
                        reviewTitle: reviewTitle,
                        liked: liked,
                        isRewatch: isRepeat,
                        containsSpoilers: containsSpoilers,
                        tags: tagDraft.tags,
                        mutationId: completionMutationId
                    )
                )
                return result(tracking: response.tracking, diaryEntry: response.diaryEntry)
            }
            let entry = try await diaryRepository.create(DiaryEntryWriteRequest(
                ref: selectedRef,
                consumedAt: consumedAt,
                rating: Self.ratingDecimal(for: ratingSteps, mediaType: selectedRef.mediaType),
                review: review,
                reviewTitle: reviewTitle,
                liked: liked,
                isRewatch: isRepeat,
                autoMarkConsumed: true,
                containsSpoilers: containsSpoilers,
                visibility: selectedRef.isSingleWeight ? "public" : visibility,
                tags: tagDraft.tags
            ))
            // A diary create returns no tracking state, so the page refreshes in full.
            return result(tracking: nil, diaryEntry: entry)
        }
    }

    /// A TV log: a calendar date, the target's stars on TV's 1-10 scale, Rewatch always sent, and one mutation ID
    /// per composer session (TV-404, TV-407, TV-504, TV-705). The response's envelope goes back to the caller.
    private func saveTVLog() async throws -> MediaLogSaveResult {
        if let target = tvDraft?.target, !target.available {
            throw MediaLogError.unavailable(target.reason ?? "Nothing has aired yet.")
        }
        let ref = selectedRef
        var request = DiaryEntryWriteRequest(
            ref: ref,
            consumedAt: consumedAt,
            rating: Self.ratingDecimal(for: ratingSteps, mediaType: ref.mediaType),
            review: review,
            reviewTitle: reviewTitle,
            liked: liked,
            isRewatch: isRepeat,
            autoMarkConsumed: true,
            containsSpoilers: containsSpoilers,
            visibility: visibility,
            tags: tagDraft.tags,
            mutationId: completionMutationId
        )
        if let tvPage, tvPage.mediaType == "season" || tvPage.isEpisode {
            request.pageSeasonNumber = tvPage.seasonNumber
            request.pageEpisodeNumber = tvPage.isEpisode ? tvPage.episodeNumber : nil
        }
        let (entry, envelope) = try await diaryRepository.createTV(request)
        return MediaLogSaveResult(ref: ref, tracking: nil, diaryEntry: entry, tvEnvelope: envelope)
    }

    private func saveProgressOnly() async -> Bool {
        await performSave {
            guard let value = Decimal(string: progressText.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                throw MediaLogError.invalidProgress
            }
            if detail.ref.mediaType == "book" {
                return result(tracking: try await trackingRepository.updateBookProgress(
                    source: detail.ref.source,
                    mediaId: detail.ref.mediaId,
                    progressType: progressType,
                    value: value,
                    notes: review
                ))
            }
            return result(tracking: try await trackingRepository.update(
                ref: detail.ref,
                request: TrackingWriteRequest(
                    status: "In progress",
                    progress: NSDecimalNumber(decimal: value).intValue,
                    notes: review.isEmpty ? nil : review
                )
            ))
        }
    }

    /// Tv, season and episode states can't be folded in by the page, so their results carry no tracking.
    private func result(tracking: TrackingState?, diaryEntry: DiaryEntry? = nil) -> MediaLogSaveResult {
        let ref = selectedRef
        let foldable = !["tv", "season", "episode"].contains(ref.mediaType)
        return MediaLogSaveResult(ref: ref, tracking: foldable ? tracking : nil, diaryEntry: diaryEntry)
    }

    private func performSave(_ operation: () async throws -> MediaLogSaveResult) async -> Bool {
        guard !isSaving else { return false }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let result = try await operation()
            MediaStateChange.post(ref: result.ref, origin: stateChangeOrigin)
            onResult?(result)
            onSaved()
            return true
        } catch {
            // TV errors carry user copy, including the retryable "TV details are unavailable right now".
            let tvMessage = selectedRef.isTVFamily ? (error as? APIError)?.apiErrorMessage : nil
            errorMessage = tvMessage ?? error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }
}

private enum MediaLogError: LocalizedError {
    case invalidProgress
    case futureDate
    case futureLogDate
    case unavailable(String)

    var errorDescription: String? {
        switch self {
        case .invalidProgress:
            "Enter a valid progress value."
        case .futureDate:
            "Consumption dates cannot be in the future."
        case .futureLogDate:
            "Log dates can't be in the future."
        case let .unavailable(reason):
            reason
        }
    }
}

struct MediaLogView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaLogViewModel
    @FocusState private var focusedField: LogField?

    private enum LogField: Hashable {
        case reviewTitle
        case review
        case tag
        case progress
        case gameHours
        case gameMinutes
        case gamePercentage
    }

    init(
        detail: MediaDetail,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        tracking: TrackingState? = nil,
        completionJourneyId: Int? = nil,
        preselectedLiked: Bool? = nil,
        preselectedRatingSteps: Int? = nil,
        stateChangeOrigin: UUID? = nil,
        onResult: ((MediaLogSaveResult) -> Void)? = nil,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping () -> Void,
        tvComposer: TVComposer? = nil,
        tvPage: MediaRef? = nil
    ) {
        _viewModel = State(initialValue: MediaLogViewModel(
            detail: detail,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            tracking: tracking,
            completionJourneyId: completionJourneyId,
            preselectedLiked: preselectedLiked,
            preselectedRatingSteps: preselectedRatingSteps,
            stateChangeOrigin: stateChangeOrigin,
            onResult: onResult,
            onUnauthorized: onUnauthorized,
            onSaved: onSaved,
            tvComposer: tvComposer,
            tvPage: tvPage
        ))
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                Form {
                    // A section header, not a row: rows clip to the card's rounded corners,
                    // which cut into the poster's left side.
                    Section {
                    } header: {
                        header
                            .textCase(nil)
                            .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 8, trailing: 0))
                    }

                    if viewModel.supportsProgress {
                        Section {
                            Picker("Log mode", selection: $viewModel.mode) {
                                Text("Finished").tag(MediaLogMode.finished)
                                Text("Progress").tag(MediaLogMode.progress)
                            }
                            .pickerStyle(.segmented)
                        }
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                    }

                    if viewModel.mode == .progress {
                        progressSection
                    } else {
                        finishedSections(scrollProxy: proxy)
                    }
                }
                .listSectionSpacing(.compact)
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.interactively)
                .disabled(viewModel.isSaving)
            }
            .background(alignment: .top) {
                backdrop
            }
            .navigationTitle(viewModel.mode == .progress ? "Update Progress" : viewModel.primaryActionTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    } label: {
                        Label("Close", systemImage: "xmark")
                    }
                    .disabled(viewModel.isSaving)
                    .accessibilityLabel("Close log")
                }

                ToolbarItem(placement: .confirmationAction) {
                    if viewModel.isSaving {
                        ProgressView()
                    } else {
                        Button(role: .confirm) {
                            save()
                        } label: {
                            Label("Save", systemImage: "checkmark")
                        }
                        .accessibilityLabel(viewModel.mode == .progress ? "Save Progress" : viewModel.primaryActionTitle)
                    }
                }

                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") {
                        focusedField = nil
                    }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                errorBanner
            }
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(viewModel.isSaving)
        .task {
            await viewModel.loadTags()
        }
    }

    private var header: some View {
        LogHeader(
            title: viewModel.selectedTitle,
            subtitle: viewModel.detail.subtitle
                ?? DiaryDateFormatter.exactDate(from: viewModel.detail.releaseDate)
                ?? viewModel.detail.releaseDate,
            posterURL: viewModel.detail.displayPosterURL,
            mediaType: viewModel.detail.ref.mediaType,
            orientation: viewModel.detail.posterOrientation,
            showsPoster: viewModel.detail.ref.mediaType != "episode"
        )
    }

    private var backdrop: some View {
        LogBackdrop(
            url: URL(string: viewModel.detail.displayBackdropURL ?? viewModel.detail.displayPosterURL ?? ""),
            accentColor: Color(hex: viewModel.detail.posterAccentColor)
        )
    }

    @ViewBuilder
    private func finishedSections(scrollProxy: ScrollViewProxy) -> some View {
        if viewModel.supportsTVTargetPicker {
            Section {
                Picker("Log", selection: $viewModel.selectedTVTargetId) {
                    ForEach(viewModel.tvTargets) { target in
                        Text(target.title).tag(target.id)
                    }
                }
                .accessibilityIdentifier("media-log.tv-target")
            }
            .logRowBackground()
        } else if viewModel.supportsSeasonLogging {
            Section {
                Picker("Season", selection: $viewModel.selectedSeasonNumber) {
                    ForEach(viewModel.seasonPickerSeasons) { season in
                        Text(season.seasonNumber == 0 ? "Specials" : season.title).tag(Int?.some(season.seasonNumber))
                    }
                    Text("Whole Show").tag(Int?.none)
                }
            }
            .logRowBackground()
        }

        Section {
            LogRatingCard(
                ratingSteps: $viewModel.ratingSteps,
                liked: $viewModel.liked,
                isRepeat: $viewModel.isRepeat,
                repeatLabel: viewModel.repeatLabel,
                ratingLabel: viewModel.ratingLabel(),
                ratingIdentifier: "media-log.rating"
            ) { locationX, width in
                viewModel.setRating(locationX: locationX, width: width)
            }
        }
        .logRowBackground()

        Section {
            DatePicker(
                viewModel.selectedRef.consumedDateLabel,
                selection: $viewModel.consumedAt,
                in: Date.distantPast...(viewModel.selectedRef.usesCalendarLogDate ? Date() : Date.distantFuture),
                displayedComponents: .date
            )

            if viewModel.detail.ref.mediaType == "game" {
                if viewModel.gamePlaythrough == nil {
                    Toggle("Known start date", isOn: $viewModel.gameHasStartDate)
                    if viewModel.gameHasStartDate {
                        DatePicker("Started", selection: $viewModel.gameStartDate, in: ...viewModel.consumedAt, displayedComponents: .date)
                    }
                }
                LogGameProgressRows(
                    draft: $viewModel.gameProgress,
                    focus: $focusedField,
                    hoursFocus: .gameHours,
                    minutesFocus: .gameMinutes,
                    percentageFocus: .gamePercentage
                )
            }
        } footer: {
            if viewModel.detail.ref.mediaType == "game" {
                Text(LogComposerStyle.gameProgressFootnote)
            }
        }
        .logRowBackground()

        Section {
            TextField("Review title", text: $viewModel.reviewTitle)
                .focused($focusedField, equals: .reviewTitle)
                .submitLabel(.next)
                .onSubmit { focusedField = .review }

            TextField("Add a review…", text: $viewModel.review, axis: .vertical)
                .lineLimit(4...12)
                .focused($focusedField, equals: .review)
        }
        .logRowBackground()

        LogTagsSection(draft: viewModel.tagDraft, focus: $focusedField, focusValue: .tag, scrollProxy: scrollProxy)

        Section {
            if !viewModel.selectedRef.usesCalendarConsumptionDate {
                Picker("Visibility", selection: $viewModel.visibility) {
                    ForEach(APIConstants.visibilityChoices, id: \.self) { value in
                        Text(value.capitalized).tag(value)
                    }
                }
            }
            Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
                .tint(.red)
        }
        .logRowBackground()

        if viewModel.detail.ref.mediaType == "music" {
            Section {
                Button("Mark as Listened") {
                    markOnly()
                }
                .frame(maxWidth: .infinity)
                .accessibilityHint("Completes tracking without creating a diary log")
            } footer: {
                Text("Counts the album as listened without adding a diary entry.")
            }
            .logRowBackground()
        }
    }

    private var progressSection: some View {
        Section {
            if viewModel.detail.ref.mediaType == "book" {
                Picker("Progress type", selection: $viewModel.progressType) {
                    Text("Pages").tag("pages")
                    Text("Percent").tag("percentage")
                }
                .pickerStyle(.segmented)
            }

            LabeledContent("Progress") {
                HStack(spacing: 6) {
                    TextField(viewModel.progressPlaceholder, text: $viewModel.progressText)
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                        .monospacedDigit()
                        .foregroundStyle(.primary)
                        .focused($focusedField, equals: .progress)
                    Text(viewModel.progressUnit)
                }
            }

            TextField("Notes", text: $viewModel.review, axis: .vertical)
                .lineLimit(3...8)
                .focused($focusedField, equals: .review)
        }
        .logRowBackground()
    }

    @ViewBuilder
    private var errorBanner: some View {
        if let error = viewModel.errorMessage {
            Text(error)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.red)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .glassEffect(.regular, in: .rect(cornerRadius: 18))
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
                .accessibilityIdentifier("media-log.save-error")
        }
    }

    private func save() {
        focusedField = nil
        Task {
            finishSave(await viewModel.save())
        }
    }

    private func markOnly() {
        focusedField = nil
        Task {
            finishSave(await viewModel.markOnly())
        }
    }

    private func finishSave(_ saved: Bool) {
        if saved {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            dismiss()
        } else if viewModel.errorMessage != nil {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }
}

private extension MediaDetail {
    func detailInt(_ key: String) -> Int? {
        details?[key]?.intValue
    }
}

private extension JSONValue {
    var intValue: Int? {
        switch self {
        case let .number(value):
            Int(value)
        case let .string(value):
            Int(value)
        default:
            nil
        }
    }
}
