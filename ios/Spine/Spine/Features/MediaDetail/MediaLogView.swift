import SwiftUI

enum MediaLogMode: String, CaseIterable, Identifiable {
    case finished
    case progress

    var id: String { rawValue }
}

@MainActor
@Observable
final class MediaLogViewModel {
    let detail: MediaDetail
    var mode: MediaLogMode = .finished
    var selectedSeasonNumber: Int?
    var consumedAt = Date()
    var ratingSteps = 0
    var reviewTitle = ""
    var review = ""
    var tags: [String] = []
    var tagQuery = ""
    var tagSuggestions: [DiaryTagSuggestion] = []
    var containsSpoilers = false
    var visibility = "public"
    var liked = false
    var isRepeat = false
    var gameProgress = GameProgressDraft()
    var gameStartDate = Date()
    var gameHasStartDate = false
    var gamePlaythrough: GamePlaythroughState?
    var progressText = ""
    var progressType = "pages"
    var isLoadingTags = false
    var isSaving = false
    var errorMessage: String?
    let completionJourneyId: Int?
    private let completionMutationId = UUID()

    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void
    private let onSaved: () -> Void

    init(
        detail: MediaDetail,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        tracking: TrackingState? = nil,
        completionJourneyId: Int? = nil,
        preselectedLiked: Bool? = nil,
        preselectedRatingSteps: Int? = nil,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping () -> Void
    ) {
        self.detail = detail
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.completionJourneyId = completionJourneyId
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
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

    var supportsProgress: Bool {
        ["manga", "comic", "boardgame"].contains(detail.ref.mediaType)
    }

    var supportsSeasonLogging: Bool {
        detail.ref.mediaType == "tv" && !(detail.seasons ?? []).isEmpty
    }

    var selectedRef: MediaRef {
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
        guard let selectedSeasonNumber else { return detail.title }
        return "\(detail.title) Season \(selectedSeasonNumber)"
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

    func loadTags() async {
        let query = tagQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else {
            tagSuggestions = []
            isLoadingTags = false
            return
        }

        isLoadingTags = true
        defer { isLoadingTags = false }
        do {
            try await Task.sleep(for: .milliseconds(250))
            try Task.checkCancellation()
            guard query == tagQuery.trimmingCharacters(in: .whitespacesAndNewlines) else { return }
            tagSuggestions = try await diaryRepository.tags(query: query)
        } catch is CancellationError {
            return
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func addTypedTag() {
        addTag(tagQuery)
        tagQuery = ""
        tagSuggestions = []
    }

    func addTag(_ rawTag: String) {
        let tag = rawTag.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !tag.isEmpty, !tags.contains(where: { $0.caseInsensitiveCompare(tag) == .orderedSame }) else { return }
        tags.append(tag)
        tagSuggestions = []
    }

    func removeTag(_ tag: String) {
        tags.removeAll { $0 == tag }
    }

    func save() async -> Bool {
        mode == .progress ? await saveProgressOnly() : await saveFinishedLog()
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
            } else if ref.mediaType == "season", let seasonNumber = ref.seasonNumber {
                _ = try await trackingRepository.watchSeason(source: ref.source, mediaId: ref.mediaId, seasonNumber: seasonNumber)
            } else if ref.mediaType == "book" {
                _ = try await trackingRepository.completeBook(source: ref.source, mediaId: ref.mediaId, completedAt: consumedAt)
            } else {
                _ = try await trackingRepository.consume(
                    ref: ref,
                    consumedAt: ref.isSingleWeight ? nil : consumedAt
                )
            }
        }
    }

    private func saveFinishedLog() async -> Bool {
        await performSave {
            guard !selectedRef.usesCalendarConsumptionDate || !CalendarDateCodec.isFuture(consumedAt) else {
                throw MediaLogError.futureDate
            }
            if selectedRef.mediaType == "game" {
                let progress = try gameProgress.values()
                _ = try await trackingRepository.completeGame(ref: selectedRef, request: GameCompletionWriteRequest(
                    playthroughId: gamePlaythrough?.id,
                    completionDate: CalendarDateCodec.string(from: consumedAt),
                    startDate: gamePlaythrough == nil && gameHasStartDate ? CalendarDateCodec.string(from: gameStartDate) : nil,
                    totalMinutes: progress.totalMinutes, percentage: progress.percentage,
                    rating: Self.ratingDecimal(for: ratingSteps, mediaType: "game"),
                    review: review, reviewTitle: reviewTitle, liked: liked, isRewatch: isRepeat,
                    containsSpoilers: containsSpoilers, tags: tags, mutationId: completionMutationId))
                MediaStateChange.post(ref: selectedRef)
                return
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
                        tags: tags,
                        mutationId: completionMutationId
                    )
                )
                MediaStateChange.post(ref: selectedRef)
                _ = response
                return
            }
            _ = try await diaryRepository.create(DiaryEntryWriteRequest(
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
                tags: tags
            ))
        }
    }

    private func saveProgressOnly() async -> Bool {
        await performSave {
            guard let value = Decimal(string: progressText.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                throw MediaLogError.invalidProgress
            }
            if detail.ref.mediaType == "book" {
                _ = try await trackingRepository.updateBookProgress(
                    source: detail.ref.source,
                    mediaId: detail.ref.mediaId,
                    progressType: progressType,
                    value: value,
                    notes: review
                )
            } else {
                _ = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(
                        status: "In progress",
                        progress: NSDecimalNumber(decimal: value).intValue,
                        notes: review.isEmpty ? nil : review
                    )
                )
            }
        }
    }

    private func performSave(_ operation: () async throws -> Void) async -> Bool {
        guard !isSaving else { return false }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await operation()
            MediaStateChange.post(ref: selectedRef)
            onSaved()
            return true
        } catch {
            errorMessage = error.localizedDescription
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

    var errorDescription: String? {
        switch self {
        case .invalidProgress:
            "Enter a valid progress value."
        case .futureDate:
            "Consumption dates cannot be in the future."
        }
    }
}

struct MediaLogView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaLogViewModel
    @FocusState private var focusedField: LogField?

    private enum LogField: Hashable {
        case review
        case tag
        case progress
    }

    private static let tagEditorAnchor = "mediaLogTagEditor"

    init(
        detail: MediaDetail,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        tracking: TrackingState? = nil,
        completionJourneyId: Int? = nil,
        preselectedLiked: Bool? = nil,
        preselectedRatingSteps: Int? = nil,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: MediaLogViewModel(
            detail: detail,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            tracking: tracking,
            completionJourneyId: completionJourneyId,
            preselectedLiked: preselectedLiked,
            preselectedRatingSteps: preselectedRatingSteps,
            onUnauthorized: onUnauthorized,
            onSaved: onSaved
        ))
    }

    var body: some View {
        NavigationStack {
            composerContent
                .toolbar(.hidden, for: .navigationBar)
        }
    }

    private var composerContent: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()
            backdrop

            ScrollViewReader { proxy in
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 20) {
                        header
                        modePicker
                        if viewModel.mode == .progress {
                            progressFields
                        } else {
                            finishedFields
                        }
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 20)
                    .disabled(viewModel.isSaving)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: focusedField) { _, field in
                    guard field == .tag else { return }
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(Self.tagEditorAnchor, anchor: .bottom)
                    }
                }
                .onChange(of: viewModel.tagSuggestions) { _, suggestions in
                    guard focusedField == .tag, !suggestions.isEmpty else { return }
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(Self.tagEditorAnchor, anchor: .bottom)
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(viewModel.isSaving)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if focusedField == nil {
                actionsFooter
            }
        }
        .overlay(alignment: .topLeading) {
            closeButton
                .padding(.leading, 18)
                .padding(.top, 12)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .bottom, spacing: 14) {
                if viewModel.detail.ref.mediaType != "episode" {
                    MediaArtwork(
                        url: viewModel.detail.displayPosterURL,
                        title: viewModel.detail.title,
                        slot: .logSheet,
                        mediaType: viewModel.detail.ref.mediaType,
                        orientation: viewModel.detail.posterOrientation
                    )
                    .shadow(color: .black.opacity(0.42), radius: 16, y: 8)
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text(viewModel.mode == .progress ? "Update Progress" : viewModel.primaryActionTitle)
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.56))
                        .textCase(.uppercase)

                    Text(viewModel.selectedTitle)
                        .font(.system(size: 28, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(3)
                        .minimumScaleFactor(0.78)

                    if let subtitle = viewModel.detail.subtitle ?? viewModel.detail.releaseDate {
                        Text(subtitle)
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white.opacity(0.58))
                            .lineLimit(1)
                    }
                }
            }
        }
        .padding(.top, 92)
        .padding(.bottom, 6)
    }

    private var backdrop: some View {
        ZStack(alignment: .top) {
            HeroArtwork(detail: viewModel.detail)
                .frame(height: 286)

            LinearGradient(
                colors: [.black.opacity(0.14), SpinePalette.pageBackground.opacity(0.64), SpinePalette.pageBackground],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 306)
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .ignoresSafeArea(edges: .top)
        .accessibilityHidden(true)
    }

    private var closeButton: some View {
        Button {
            dismiss()
        } label: {
            Image(systemName: "xmark")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 40, height: 40)
                .background(.black.opacity(0.34), in: Circle())
                .overlay {
                    Circle().stroke(.white.opacity(0.08))
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close log")
        .disabled(viewModel.isSaving)
    }

    @ViewBuilder
    private var modePicker: some View {
        VStack(spacing: 14) {
            if viewModel.supportsProgress {
                Picker("Log mode", selection: $viewModel.mode) {
                    Text("Finished").tag(MediaLogMode.finished)
                    Text("Progress").tag(MediaLogMode.progress)
                }
                .pickerStyle(.segmented)
                .tint(.white.opacity(0.9))
            }

            if viewModel.supportsSeasonLogging {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 8) {
                        seasonChip(
                            title: "Whole Show",
                            seasonNumber: nil
                        )
                        ForEach(viewModel.detail.seasons ?? []) { season in
                            seasonChip(
                                title: "S\(season.seasonNumber)",
                                seasonNumber: season.seasonNumber
                            )
                        }
                    }
                }
            }
        }
    }

    private func seasonChip(
        title: String,
        seasonNumber: Int?
    ) -> some View {
        Button {
            viewModel.selectedSeasonNumber = seasonNumber
        } label: {
            Text(title)
                .font(.system(size: 13, weight: .heavy))
                .foregroundStyle(viewModel.selectedSeasonNumber == seasonNumber ? .black : .white.opacity(0.82))
                .padding(.horizontal, 13)
                .frame(height: 34)
                .background(
                    viewModel.selectedSeasonNumber == seasonNumber ? .white.opacity(0.92) : .black.opacity(0.24),
                    in: Capsule()
                )
                .overlay {
                    Capsule().stroke(.white.opacity(viewModel.selectedSeasonNumber == seasonNumber ? 0 : 0.1))
                }
        }
        .buttonStyle(.plain)
        .frame(minHeight: 44)
    }

    private var finishedFields: some View {
        VStack(alignment: .leading, spacing: 14) {
            composerSurface {
                dateRow
                if viewModel.detail.ref.mediaType == "game" {
                    if viewModel.gamePlaythrough == nil {
                        Toggle("Known start date", isOn: $viewModel.gameHasStartDate)
                        if viewModel.gameHasStartDate {
                            DatePicker("Started", selection: $viewModel.gameStartDate, in: ...viewModel.consumedAt, displayedComponents: .date)
                        }
                    }
                    GameProgressFields(draft: $viewModel.gameProgress)
                    Divider().overlay(.white.opacity(0.1))
                }
                Divider().overlay(.white.opacity(0.1))
                ratingPicker
                Divider().overlay(.white.opacity(0.1))
                VStack(alignment: .leading, spacing: 8) {
                    sectionLabel("Review title")
                    TextField("Review title", text: $viewModel.reviewTitle)
                        .textFieldStyle(.plain)
                        .font(.system(size: 17, weight: .regular, design: .rounded))
                        .foregroundStyle(.white)
                        .tint(.white)
                    Divider().overlay(.white.opacity(0.1))
                    sectionLabel("Review")
                    TextField("Review", text: $viewModel.review, axis: .vertical)
                        .textFieldStyle(.plain)
                        .focused($focusedField, equals: .review)
                        .lineLimit(5...8)
                        .font(.system(size: 17, weight: .regular, design: .rounded))
                        .foregroundStyle(.white)
                        .tint(.white)
                }
            }

            tagEditor
                .id(Self.tagEditorAnchor)
            options
        }
    }

    private var progressFields: some View {
        composerSurface {
            VStack(alignment: .leading, spacing: 14) {
                if viewModel.detail.ref.mediaType == "book" {
                    Picker("Progress Type", selection: $viewModel.progressType) {
                        Text("Pages").tag("pages")
                        Text("Percent").tag("percentage")
                    }
                    .pickerStyle(.segmented)
                    .tint(.white.opacity(0.9))
                }

                sectionLabel("Progress")
                HStack {
                    TextField(viewModel.progressPlaceholder, text: $viewModel.progressText)
                        .keyboardType(.decimalPad)
                        .focused($focusedField, equals: .progress)
                        .font(.system(size: 22, weight: .bold, design: .rounded))
                    Text(viewModel.progressUnit)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white.opacity(0.48))
                }

                Divider().background(.white.opacity(0.12))

                TextField("Notes", text: $viewModel.review, axis: .vertical)
                    .lineLimit(3...7)
                    .font(.system(size: 17, weight: .regular, design: .rounded))
                    .foregroundStyle(.white)
                    .tint(.white)
            }
        }
    }

    private var dateRow: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 3) {
                sectionLabel(viewModel.selectedRef.consumedDateLabel)
            }

            Spacer(minLength: 12)

            DatePicker(
                "Date",
                selection: $viewModel.consumedAt,
                in: Date.distantPast...(viewModel.selectedRef.usesCalendarConsumptionDate ? Date() : Date.distantFuture),
                displayedComponents: [.date]
            )
                .labelsHidden()
                .datePickerStyle(.compact)
                .colorScheme(.dark)
                .tint(.white)
        }
    }

    private var ratingPicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionLabel("Your rating")
            HStack(alignment: .center, spacing: 10) {
                GeometryReader { proxy in
                    HStack(spacing: 5) {
                        ForEach(1...5, id: \.self) { star in
                            Image(systemName: starSystemName(star))
                                .font(.system(size: 34, weight: .bold))
                                .foregroundStyle(viewModel.ratingSteps >= star * 2 - 1 ? .yellow : .white.opacity(0.26))
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .accessibilityHidden(true)
                    .contentShape(Rectangle())
                    .simultaneousGesture(
                        DragGesture(minimumDistance: 0)
                            .onChanged { value in
                                viewModel.setRating(locationX: value.location.x, width: proxy.size.width)
                            }
                    )
                }
                .frame(maxWidth: 218)
                .frame(height: 44)
                .accessibilityRepresentation {
                    Slider(value: Binding(get: { Double(viewModel.ratingSteps) }, set: { viewModel.ratingSteps = Int($0) }), in: 0...10, step: 1) {
                        Text("Rating")
                    }
                    .accessibilityValue(viewModel.ratingLabel())
                    .accessibilityIdentifier("media-log.rating")
                }

                Spacer(minLength: 0)

                compactIconButton(
                    systemName: viewModel.liked ? "heart.fill" : "heart",
                    title: "Like",
                    isSelected: viewModel.liked
                ) {
                    viewModel.liked.toggle()
                }

                compactIconButton(
                    systemName: "arrow.clockwise.circle",
                    title: viewModel.repeatLabel,
                    isSelected: viewModel.isRepeat
                ) {
                    viewModel.isRepeat.toggle()
                }
            }

            Text(viewModel.ratingLabel())
                .font(.system(size: 14, weight: .bold, design: .rounded))
                .foregroundStyle(viewModel.ratingSteps == 0 ? .white.opacity(0.58) : .yellow.opacity(0.9))
        }
    }

    private func starSystemName(_ star: Int) -> String {
        if viewModel.ratingSteps >= star * 2 {
            return "star.fill"
        }
        if viewModel.ratingSteps == star * 2 - 1 {
            return "star.leadinghalf.filled"
        }
        return "star"
    }

    private var tagEditor: some View {
        composerSurface {
            VStack(alignment: .leading, spacing: 12) {
                sectionLabel("Tags")
                if !viewModel.tags.isEmpty {
                    FlowLayout(spacing: 8) {
                        ForEach(viewModel.tags, id: \.self) { tag in
                            Button {
                                viewModel.removeTag(tag)
                            } label: {
                                Label(tag, systemImage: "xmark")
                                    .font(.system(size: 12, weight: .bold))
                                    .labelStyle(.titleAndIcon)
                                    .foregroundStyle(.white)
                                    .padding(.horizontal, 10)
                                    .frame(height: 30)
                                    .background(.white.opacity(0.13), in: Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                HStack(spacing: 12) {
                    TextField("Add tags", text: $viewModel.tagQuery)
                        .textInputAutocapitalization(.never)
                        .focused($focusedField, equals: .tag)
                        .onSubmit { viewModel.addTypedTag() }
                        .task(id: viewModel.tagQuery) {
                            await viewModel.loadTags()
                        }
                    Button {
                        viewModel.addTypedTag()
                    } label: {
                        Image(systemName: "plus")
                            .font(.system(size: 15, weight: .bold))
                            .foregroundStyle(.black)
                            .frame(width: 30, height: 30)
                            .background(.white, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.tagQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityLabel("Add tag")
                }
                .font(.system(size: 16, weight: .medium, design: .rounded))
                .foregroundStyle(.white)
                .padding(.horizontal, 12)
                .frame(height: 46)
                .background(.black.opacity(0.2), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(.white.opacity(0.1))
                }
            }

            if !viewModel.tagSuggestions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(viewModel.tagSuggestions, id: \.self) { suggestion in
                            Button {
                                viewModel.addTag(suggestion.name)
                                viewModel.tagQuery = ""
                            } label: {
                                Text(suggestion.name)
                                    .font(.system(size: 12, weight: .bold))
                                    .foregroundStyle(.white.opacity(0.84))
                                    .padding(.horizontal, 10)
                                    .frame(height: 30)
                                    .background(.white.opacity(0.09), in: Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    private var options: some View {
        composerSurface {
            if !viewModel.selectedRef.usesCalendarConsumptionDate {
                Picker("Visibility", selection: $viewModel.visibility) {
                    ForEach(APIConstants.visibilityChoices, id: \.self) { value in
                        Text(value.capitalized).tag(value)
                    }
                }
                Divider().overlay(.white.opacity(0.1))
            }
            Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .tint(.red)
        }
    }

    private func compactIconButton(systemName: String, title: String, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(selectedIconColor(systemName: systemName, isSelected: isSelected))
                .frame(width: 44, height: 44)
                .background(isSelected ? .white.opacity(0.94) : .black.opacity(0.24), in: Circle())
                .overlay {
                    Circle().stroke(.white.opacity(isSelected ? 0 : 0.1))
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private func selectedIconColor(systemName: String, isSelected: Bool) -> Color {
        guard isSelected else { return .white }
        return systemName == "heart.fill" ? .pink : .black
    }

    @ViewBuilder
    private var errorText: some View {
        if let error = viewModel.errorMessage {
            Text(error)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.red.opacity(0.92))
        }
    }

    private var actionsFooter: some View {
        VStack(spacing: 10) {
            errorText
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("media-log.save-error")

            Button {
                Task {
                    if await viewModel.save() {
                        dismiss()
                    }
                }
            } label: {
                saveLabel(viewModel.mode == .progress ? "Save Progress" : viewModel.primaryActionTitle)
            }
            .buttonStyle(.plain)
            .disabled(viewModel.isSaving)

            if viewModel.detail.ref.mediaType == "music", viewModel.mode == .finished {
                Button {
                    Task {
                        if await viewModel.markOnly() {
                            dismiss()
                        }
                    }
                } label: {
                    Text("Mark Listened")
                        .font(.system(size: 15, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.9))
                        .frame(maxWidth: .infinity)
                        .frame(height: 46)
                        .background(.white.opacity(0.08), in: Capsule())
                        .overlay { Capsule().stroke(.white.opacity(0.14)) }
                }
                .buttonStyle(.plain)
                .disabled(viewModel.isSaving)
                .accessibilityHint("Completes tracking without creating a diary log")
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background(SpinePalette.pageBackground)
        .overlay(alignment: .top) {
            Divider().overlay(.white.opacity(0.1))
        }
    }

    private func saveLabel(_ title: String) -> some View {
        HStack {
            Spacer()
            Group {
                if viewModel.isSaving {
                    ProgressView()
                        .tint(.black)
                } else {
                    Text(title)
                        .font(.system(size: 16, weight: .heavy))
                }
            }
            .spineContentTransition(value: viewModel.isSaving)
            Spacer()
        }
        .foregroundStyle(.black)
        .frame(height: 54)
        .background(.white.opacity(0.94), in: Capsule())
    }

    private func composerSurface<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            content()
        }
        .foregroundStyle(.white)
        .tint(.white)
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SpinePalette.pageBackground, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .stroke(.white.opacity(0.09))
        }
    }

    private func sectionLabel(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 12, weight: .heavy, design: .rounded))
            .foregroundStyle(.white.opacity(0.56))
            .textCase(.uppercase)
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
