import SwiftUI

extension Notification.Name {
    static let diaryEntriesDidChange = Notification.Name("diaryEntriesDidChange")
}

/// How late a log can be dated. Calendar-dated logs, TV logs at every level included, stop at today (TV-407);
/// other types keep a time and have no cap.
enum DiaryLogDateLimit {
    static func latest(for ref: MediaRef?, now: Date = Date()) -> Date {
        ref?.usesCalendarLogDate == true ? now : .distantFuture
    }

    static func range(for ref: MediaRef?, now: Date = Date()) -> ClosedRange<Date> {
        Date.distantPast...latest(for: ref, now: now)
    }
}

@MainActor
@Observable
final class DiaryLogDetailViewModel {
    var entry: DiaryEntry?
    var mediaDetail: MediaDetail?
    var isLoading = true
    var isDeleting = false
    var deleteErrorMessage: String?
    var errorMessage: String?
    var isReviewRevealed = false
    /// Counts the times the show changed after the delete confirmation: the page reloaded and asks again with the
    /// new count (TV-406, TV-705).
    private(set) var deleteReconfirmations = 0
    /// One mutation ID per confirmed delete, kept for its retries, so a TV log deletion applies at most once (TV-705).
    private var deleteMutationId: UUID?

    private let entryId: Int
    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.entryId = entryId
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loadedEntry = try await diaryRepository.detail(id: entryId)
            await loadMediaDetail(for: loadedEntry.media.ref)
            entry = loadedEntry
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save(_ request: DiaryEntryUpdateRequest) async -> Bool {
        guard let entry else { return false }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            self.entry = try await diaryRepository.update(id: entry.id, request: request)
            if let ref = self.entry?.media.ref {
                await loadMediaDetail(for: ref)
                MediaStateChange.post(ref: ref)
            }
            NotificationCenter.default.post(name: .diaryEntriesDidChange, object: nil)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    /// The delete confirmation's title. A TV log that unmarks episodes says how many (TV-406).
    var deletePrompt: String {
        entry?.tvLog.flatMap { TVTrackingPresentation.deleteLogPrompt(unmarksCount: $0.unmarksCount) }
            ?? "Delete this diary entry?"
    }

    /// Starts a new confirmed delete: its retries share one mutation ID.
    func confirmDelete() async -> Bool {
        deleteMutationId = nil
        return await delete()
    }

    func delete() async -> Bool {
        guard !isDeleting, let entry else { return false }
        isDeleting = true
        errorMessage = nil
        deleteErrorMessage = nil
        defer { isDeleting = false }

        do {
            let ref = entry.media.ref
            if let tvLog = entry.tvLog {
                // The version the dialog showed: if the show changed since, nothing is deleted (TV-705).
                let mutationId = deleteMutationId ?? UUID()
                deleteMutationId = mutationId
                try await diaryRepository.delete(id: entry.id, expectedVersion: tvLog.stateVersion, mutationId: mutationId)
            } else {
                try await diaryRepository.delete(id: entry.id)
            }
            deleteMutationId = nil
            NotificationCenter.default.post(name: .diaryEntriesDidChange, object: nil)
            MediaStateChange.post(ref: ref)
            return true
        } catch let error as APIError where error.apiErrorCode == "tv_state_changed" {
            // The show changed since the dialog: refresh the log's counts and ask again.
            deleteMutationId = nil
            do {
                self.entry = try await diaryRepository.detail(id: entry.id)
                deleteReconfirmations += 1
            } catch {
                deleteErrorMessage = error.localizedDescription
                if case APIError.unauthorized = error {
                    onUnauthorized()
                }
            }
            return false
        } catch {
            errorMessage = error.localizedDescription
            deleteErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    private func loadMediaDetail(for ref: MediaRef) async {
        do {
            mediaDetail = try await mediaRepository.detail(ref: ref)
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct DiaryLogDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryLogDetailViewModel
    @State private var presentedRef: MediaRef?
    @State private var isEditing = false
    @State private var isDeleteConfirmationPresented = false
    @State private var edgeDragOffset: CGFloat = 0
    @State private var topSafeAreaInset: CGFloat = 0

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        entryId: Int,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .diary,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: DiaryLogDetailViewModel(
            entryId: entryId,
            diaryRepository: diaryRepository,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                Group {
                    if viewModel.isLoading, viewModel.entry == nil {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 520)
                    } else if let entry = viewModel.entry {
                        content(entry)
                    } else if let error = viewModel.errorMessage {
                        ContentUnavailableView("Could not load log", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding()
                    }
                }
                .spineContentTransition(value: contentPhase)
                .padding(.bottom, 38)
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)

            GlassEffectContainer(spacing: 8) {
                HStack(spacing: 8) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 17, weight: .bold))
                            .frame(width: 38, height: 38)
                    }
                    .buttonStyle(.plain)
                    .contentShape(.circle)
                    .glassEffect(.regular.interactive(), in: .circle)
                    .accessibilityLabel("Back")

                    Spacer()

                    if canManageEntry {
                        Button {
                            isEditing = true
                        } label: {
                            Text("Edit")
                                .font(.system(size: 14, weight: .heavy))
                                .padding(.horizontal, 13)
                                .frame(height: 38)
                        }
                        .buttonStyle(.plain)
                        .contentShape(.capsule)
                        .glassEffect(.regular.interactive(), in: .capsule)
                        .disabled(viewModel.isDeleting)

                        Button(role: .destructive) {
                            isDeleteConfirmationPresented = true
                        } label: {
                            Image(systemName: "trash")
                                .font(.system(size: 16, weight: .bold))
                                .frame(width: 38, height: 38)
                        }
                        .buttonStyle(.plain)
                        .contentShape(.circle)
                        .glassEffect(.regular.interactive(), in: .circle)
                        .accessibilityLabel("Delete")
                        .disabled(viewModel.isDeleting)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
        }
        .onGeometryChange(for: CGFloat.self) { $0.safeAreaInsets.top } action: { topSafeAreaInset = $0 }
        .navigationBarBackButtonHidden()
        .toolbar(.hidden, for: .tabBar)
        .dismissExplorationOnReturnHome()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .task {
            if viewModel.entry == nil {
                await viewModel.load()
            }
        }
        .fullScreenCover(item: $presentedRef) { ref in
            mediaDestination(ref)
        }
        .sheet(isPresented: $isEditing) {
            if let entry = viewModel.entry {
                DiaryLogEditSheet(
                    entry: entry,
                    diaryRepository: diaryRepository,
                    backdropURL: viewModel.mediaDetail?.displayBackdropURL,
                    errorMessage: { viewModel.errorMessage }
                ) { request in
                    await viewModel.save(request)
                }
            }
        }
        .alert("Could not delete log", isPresented: Binding(
            get: { viewModel.deleteErrorMessage != nil },
            set: { if !$0 { viewModel.deleteErrorMessage = nil } }
        )) {
            Button("Try Again") { Task { if await viewModel.delete() { dismiss() } } }
            Button("Cancel", role: .cancel) { viewModel.deleteErrorMessage = nil }
        } message: { Text(viewModel.deleteErrorMessage ?? "Could not delete the log.") }
        .onChange(of: viewModel.deleteReconfirmations) {
            isDeleteConfirmationPresented = true
        }
        .confirmationDialog(viewModel.deletePrompt, isPresented: $isDeleteConfirmationPresented, titleVisibility: .visible) {
            Button(viewModel.entry?.tvLog == nil ? "Delete Entry" : "Delete Log", role: .destructive) {
                Task {
                    if await viewModel.confirmDelete() {
                        dismiss()
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(viewModel.entry?.media.ref.mediaType == "game"
                 ? "A direct or older completion also deletes its playthrough and all progress. Deleting the current tracked completion reopens it with latest progress. This cannot be undone."
                 : "This cannot be undone.")
        }
    }

    private var canManageEntry: Bool {
        guard let entry = viewModel.entry, let currentUserId else { return false }
        return entry.user.id == currentUserId
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.entry != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    private func content(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            hero(entry)
            logSection(entry)
        }
    }

    private func hero(_ entry: DiaryEntry) -> some View {
        HStack(alignment: .bottom, spacing: 14) {
            MediaArtwork(
                url: viewModel.mediaDetail?.displayPosterURL ?? entry.media.displayPosterURL,
                title: entry.media.title,
                slot: .hero,
                mediaType: entry.media.ref.mediaType,
                orientation: viewModel.mediaDetail?.posterOrientation ?? entry.media.posterOrientation
            )
            .shadow(color: .black.opacity(0.48), radius: 22, y: 12)

            VStack(alignment: .leading, spacing: 10) {
                Text(mediaTypeLabel(entry.media.ref.mediaType))
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.56))
                    .textCase(.uppercase)

                if let logged = DiaryLogFormat.dateLabel(entry.consumedAt) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        if entry.isTrueReread ?? entry.isRewatch {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(.white.opacity(0.74))
                                .accessibilityLabel(entry.media.ref.repeatLabel)
                        }

                        Text("Logged \(logged)")
                            .font(.system(size: 13, weight: .heavy))
                            .foregroundStyle(.white.opacity(0.72))
                    }

                    if let age = DiaryLogFormat.ageLabel(entry.consumedAt) {
                        Text(age)
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.58))
                    }
                }

                if entry.media.ref.mediaType == "game" {
                    let progress = GameProgressValues(totalMinutes: entry.totalMinutes, percentage: entry.percentage).summary
                    if !progress.isEmpty { Text("At completion: \(progress)").font(.subheadline) }
                }
                let parts = titleParts(TVTrackingPresentation.diaryTitle(for: entry.media))
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    if let detail = viewModel.mediaDetail {
                        MediaTitleDisplay(
                            detail: detail,
                            title: parts.title,
                            showsLogo: .constant(true),
                            font: .system(size: 31, weight: .black),
                            lineLimit: 4,
                            minimumScaleFactor: 0.72,
                            maxLogoHeight: 48,
                            alignment: .leading,
                            onTap: { presentedRef = entry.media.ref },
                            onLongPress: nil
                        )
                    } else {
                        Text(parts.title)
                            .font(.system(size: 31, weight: .black))
                            .foregroundStyle(.white)
                            .lineLimit(4)
                            .minimumScaleFactor(0.72)
                    }

                }

                if let metadata = heroMetadata(entry) {
                    Text(metadata)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.66))
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .onTapGesture { presentedRef = entry.media.ref }
        .accessibilityLabel("View \(entry.media.displayTitle)")
        .accessibilityAddTraits(.isButton)
        .padding(.horizontal, 16)
        // The top bar ends 50 pt below the safe area; the artwork starts 18 pt under it, whatever its shape.
        .padding(.top, topSafeAreaInset + 68)
        .padding(.bottom, 22)
        .background {
            DiaryLogHeroArtwork(entry: entry, detail: viewModel.mediaDetail)
                .offset(y: BackdropLayout.topOffset)
        }
    }

    private func logSection(_ entry: DiaryEntry) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            if hasRatingOrLike(entry) {
                ratingLikeLine(entry)
            }

            if let title = clean(entry.reviewTitle) {
                Text(title)
                    .font(.system(size: 21, weight: .heavy))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
            }

            reviewBody(entry)

            if !entry.tags.isEmpty {
                DiaryLogTagsView(
                    tags: entry.tags,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .padding(.horizontal, 16)
    }

    private func hasRatingOrLike(_ entry: DiaryEntry) -> Bool {
        clean(entry.rating) != nil || entry.liked
    }

    private func ratingLikeLine(_ entry: DiaryEntry) -> some View {
        HStack(spacing: 10) {
            if let rating = clean(entry.rating) {
                DiaryStarRating(
                    rating: rating,
                    fontSize: 17
                )
            }

            if entry.liked {
                Image(systemName: "heart.fill")
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(.pink)
                    .accessibilityLabel("Liked")
            }
        }
    }

    @ViewBuilder
    private func reviewBody(_ entry: DiaryEntry) -> some View {
        if let review = clean(entry.review) {
            if entry.containsSpoilers && !viewModel.isReviewRevealed {
                Button {
                    viewModel.isReviewRevealed = true
                } label: {
                    Label("Reveal spoiler review", systemImage: "eye.slash")
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 18)
                        .background(.white.opacity(0.09), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                .buttonStyle(.plain)
            } else {
                Text(review)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(.white.opacity(0.78))
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            Text("No review text.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    private func mediaDestination(_ ref: MediaRef) -> some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }

    private func heroMetadata(_ entry: DiaryEntry) -> String? {
        var parts = [titleParts(entry.media.displayTitle).year, DiaryLogFormat.year(viewModel.mediaDetail?.releaseDate), detailString(viewModel.mediaDetail, "runtime")]
            .compactMap { clean($0) }
        var seen = Set<String>()
        parts = parts.filter { seen.insert($0.lowercased()).inserted }
        return parts.isEmpty ? nil : parts.joined(separator: " - ")
    }

    private func detailString(_ detail: MediaDetail?, _ key: String) -> String? {
        guard let value = detail?.details?[key] else { return nil }
        switch value {
        case let .string(string):
            return clean(string)
        case let .number(number):
            return number.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(number))" : "\(number)"
        case let .bool(bool):
            return bool ? "Yes" : "No"
        default:
            return nil
        }
    }

    private func mediaTypeLabel(_ mediaType: String) -> String {
        mediaType == "tv" ? "TV" : mediaType.capitalized
    }

    private func titleParts(_ title: String) -> (title: String, year: String?) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasSuffix(")") else { return (trimmed, nil) }
        let suffix = String(trimmed.suffix(6))
        guard suffix.first == "(", suffix.last == ")" else { return (trimmed, nil) }
        let year = String(suffix.dropFirst().dropLast())
        guard year.count == 4, year.allSatisfy(\.isNumber) else { return (trimmed, nil) }
        return (String(trimmed.dropLast(7)), year)
    }

    private func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct DiaryLogDetailNavigationCover: View {
    let entryId: Int
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        NavigationStack {
            DiaryLogDetailView(
                entryId: entryId,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
    }
}

private struct DiaryLogHeroArtwork: View {
    let entry: DiaryEntry
    let detail: MediaDetail?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color(red: 0.07, green: 0.07, blue: 0.065)

                SpineAsyncImage(url: artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .blur(radius: usesBackdrop ? 16 : 28)
                            .scaleEffect(usesBackdrop ? 1.08 : 1.24)
                            .opacity(0.62)
                    default:
                        LinearGradient(
                            colors: MediaTypeTheme.theme(for: entry.media.ref.mediaType).gradientColors,
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                        .opacity(0.28)
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.12), location: 0),
                        .init(color: .black.opacity(0.3), location: 0.42),
                        .init(color: Color(red: 0.07, green: 0.07, blue: 0.065).opacity(0.78), location: 0.78),
                        .init(color: Color(red: 0.07, green: 0.07, blue: 0.065), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
        .clipped()
    }

    private var artworkURL: URL? {
        URL(string: backdropURL ?? detail?.displayPosterURL ?? entry.media.displayPosterURL ?? "")
    }

    private var backdropURL: String? {
        guard ["movie", "tv"].contains(entry.media.ref.mediaType) else { return nil }
        return detail?.displayBackdropURL
    }

    private var usesBackdrop: Bool {
        backdropURL != nil
    }
}

@MainActor
@Observable
final class DiaryLogEditViewModel {
    let entry: DiaryEntry
    var consumedAt: Date
    var gameProgress: GameProgressDraft
    let initialGameProgress: GameProgressDraft
    var ratingSteps: Int
    var reviewTitle: String
    var review: String
    let tagDraft: LogTagDraft
    var liked: Bool
    var isRewatch: Bool
    var containsSpoilers: Bool
    var visibility: String
    var isSaving = false
    var errorMessage: String?

    private let diaryRepository: DiaryRepository

    init(entry: DiaryEntry, diaryRepository: DiaryRepository) {
        self.entry = entry
        self.diaryRepository = diaryRepository
        consumedAt = CalendarDateCodec.date(from: entry.consumedAt) ?? Date()
        gameProgress = GameProgressDraft(totalMinutes: entry.totalMinutes, percentage: entry.percentage)
        initialGameProgress = GameProgressDraft(totalMinutes: entry.totalMinutes, percentage: entry.percentage)
        ratingSteps = entry.rating?.ratingHalfSteps ?? 0
        reviewTitle = entry.reviewTitle ?? ""
        review = entry.review ?? ""
        tagDraft = LogTagDraft(tags: entry.tags)
        liked = entry.liked
        isRewatch = entry.isTrueReread ?? entry.isRewatch
        containsSpoilers = entry.containsSpoilers
        visibility = entry.visibility
    }

    var repeatLabel: String {
        entry.media.ref.repeatLabel
    }

    func ratingLabel() -> String {
        guard ratingSteps > 0 else { return "No rating" }
        let stars = Double(ratingSteps) / 2
        return stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))/5" : "\(stars)/5"
    }

    func setRating(locationX: CGFloat, width: CGFloat) {
        guard width > 0 else { return }
        let clamped = min(max(locationX, 0), width)
        ratingSteps = max(1, min(10, Int(ceil((clamped / width) * 10))))
    }

    /// Loads the user's own tags once, for suggestions while they type.
    func loadTags() async {
        try? await tagDraft.load(from: diaryRepository)
    }

    func request() throws -> DiaryEntryUpdateRequest {
        let progress = try gameProgress.request(comparedTo: initialGameProgress)
        return DiaryEntryUpdateRequest(
            consumedAt: consumedAt,
            rating: ratingSteps > 0 ? Decimal(ratingSteps) / 2 : nil,
            review: review,
            reviewTitle: reviewTitle,
            tags: tagDraft.tags,
            liked: liked,
            isRewatch: isRewatch,
            containsSpoilers: containsSpoilers,
            visibility: entry.media.ref.isSingleWeight ? nil : visibility,
            calendarDateOnly: entry.media.ref.usesCalendarLogDate,
            includesRating: true,
            totalMinutes: progress.totalMinutes, percentage: progress.percentage,
            includesMinutes: entry.media.ref.mediaType == "game" && progress.includesMinutes,
            includesPercentage: entry.media.ref.mediaType == "game" && progress.includesPercentage
        )
    }
}

private struct DiaryLogEditSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryLogEditViewModel
    @FocusState private var focusedField: EditField?
    let backdropURL: String?
    let onSave: (DiaryEntryUpdateRequest) async -> Bool
    let errorMessage: () -> String?

    private enum EditField: Hashable {
        case reviewTitle
        case review
        case tag
        case gameHours
        case gameMinutes
        case gamePercentage
    }

    init(entry: DiaryEntry, diaryRepository: DiaryRepository, backdropURL: String? = nil, errorMessage: @escaping () -> String? = { nil }, onSave: @escaping (DiaryEntryUpdateRequest) async -> Bool) {
        _viewModel = State(initialValue: DiaryLogEditViewModel(entry: entry, diaryRepository: diaryRepository))
        self.backdropURL = backdropURL
        self.onSave = onSave
        self.errorMessage = errorMessage
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                Form {
                    // A section header, not a row: rows clip to the card's rounded corners,
                    // which cut into the poster's left side.
                    Section {
                    } header: {
                        LogHeader(
                            title: TVTrackingPresentation.diaryTitle(for: viewModel.entry.media),
                            posterURL: viewModel.entry.media.displayPosterURL,
                            mediaType: viewModel.entry.media.ref.mediaType,
                            orientation: viewModel.entry.media.posterOrientation
                        )
                        .textCase(nil)
                        .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 8, trailing: 0))
                    }

                    Section {
                        LogRatingCard(
                            ratingSteps: $viewModel.ratingSteps,
                            liked: $viewModel.liked,
                            isRepeat: $viewModel.isRewatch,
                            repeatLabel: viewModel.repeatLabel,
                            ratingLabel: viewModel.ratingLabel(),
                            ratingIdentifier: "diary-log.rating"
                        ) { locationX, width in
                            viewModel.setRating(locationX: locationX, width: width)
                        }
                    }
                    .logRowBackground()

                    Section {
                        DatePicker(
                            viewModel.entry.media.ref.consumedDateLabel,
                            selection: $viewModel.consumedAt,
                            in: DiaryLogDateLimit.range(for: viewModel.entry.media.ref),
                            displayedComponents: .date
                        )

                        if isGame {
                            LogGameProgressRows(
                                draft: $viewModel.gameProgress,
                                focus: $focusedField,
                                hoursFocus: .gameHours,
                                minutesFocus: .gameMinutes,
                                percentageFocus: .gamePercentage
                            )
                        }
                    } footer: {
                        if isGame {
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

                    LogTagsSection(draft: viewModel.tagDraft, focus: $focusedField, focusValue: .tag, scrollProxy: proxy)

                    Section {
                        if !viewModel.entry.media.ref.usesCalendarConsumptionDate {
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
                }
                .listSectionSpacing(.compact)
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.interactively)
                .disabled(viewModel.isSaving)
            }
            .background { LogBackdrop(url: URL(string: backdropURL ?? viewModel.entry.media.displayPosterURL ?? "")) }
            .navigationTitle("Edit Log")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    } label: {
                        Label("Close", systemImage: "xmark")
                    }
                    .disabled(viewModel.isSaving)
                }

                ToolbarItem(placement: .confirmationAction) {
                    if viewModel.isSaving {
                        ProgressView()
                    } else {
                        Button(role: .confirm) {
                            save()
                        } label: {
                            Label("Save Changes", systemImage: "checkmark")
                        }
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
                }
            }
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(viewModel.isSaving)
        .task {
            await viewModel.loadTags()
        }
    }

    private var isGame: Bool {
        viewModel.entry.media.ref.mediaType == "game"
    }

    private func save() {
        guard !viewModel.isSaving else { return }
        focusedField = nil
        viewModel.tagDraft.commitQuery()
        // Set before the task starts, so a quick second tap can't send the update twice.
        viewModel.isSaving = true
        viewModel.errorMessage = nil
        Task {
            defer { viewModel.isSaving = false }
            do {
                if await onSave(try viewModel.request()) {
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                    dismiss()
                } else {
                    viewModel.errorMessage = errorMessage() ?? "Could not save. Try again."
                    UINotificationFeedbackGenerator().notificationOccurred(.error)
                }
            } catch {
                viewModel.errorMessage = error.localizedDescription
                UINotificationFeedbackGenerator().notificationOccurred(.error)
            }
        }
    }
}

private struct DiaryLogTagsView: View {
    let tags: [String]
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    @State private var isExpanded = false
    @State private var fullHeight: CGFloat = 0

    private let rowHeight: CGFloat = 30
    private let spacing: CGFloat = 8

    private var collapsedHeight: CGFloat {
        rowHeight * 3 + spacing * 2
    }

    private var canExpand: Bool {
        fullHeight > collapsedHeight + 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            tagLinks
                .frame(maxHeight: isExpanded ? nil : collapsedHeight, alignment: .top)
                .clipped()
                .background {
                    tagChips
                        .fixedSize(horizontal: false, vertical: true)
                        .background {
                            GeometryReader { proxy in
                                Color.clear.preference(key: DiaryLogTagsFullHeightKey.self, value: proxy.size.height)
                            }
                        }
                        .hidden()
                }

            if canExpand {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        isExpanded.toggle()
                    }
                } label: {
                    Label(isExpanded ? "READ LESS" : "READ MORE", systemImage: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.62))
                }
                .buttonStyle(.plain)
            }
        }
        .onPreferenceChange(DiaryLogTagsFullHeightKey.self) { fullHeight = $0 }
    }

    private var tagLinks: some View {
        FlowLayout(spacing: spacing) {
            ForEach(tags, id: \.self) { tag in
                NavigationLink {
                    TaggedDiaryView(
                        tag: tag,
                        diaryRepository: diaryRepository,
                        mediaRepository: mediaRepository,
                        trackingRepository: trackingRepository,
                        currentUserId: currentUserId,
                        selectedTab: selectedTab,
                        onSelectTab: onSelectTab,
                        onUnauthorized: onUnauthorized
                    )
                } label: {
                    DiaryLogChip(text: tag, systemImage: "tag")
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var tagChips: some View {
        FlowLayout(spacing: spacing) {
            ForEach(tags, id: \.self) { tag in
                DiaryLogChip(text: tag, systemImage: "tag")
            }
        }
    }
}

private struct DiaryLogTagsFullHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

/// A log's tag; list pages show their tags with it too.
struct DiaryLogChip: View {
    let text: String
    let systemImage: String?

    var body: some View {
        Label {
            Text(text)
                .lineLimit(1)
        } icon: {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 10, weight: .black))
            }
        }
        .font(.system(size: 12, weight: .heavy))
        .foregroundStyle(.white.opacity(0.82))
        .padding(.horizontal, 10)
        .frame(height: 30)
        .background(.white.opacity(0.11), in: Capsule())
    }
}

enum DiaryLogFormat {
    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let fallbackISOFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let dateOnlyFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale.current
        formatter.dateFormat = "MMM d, yyyy"
        return formatter
    }()

    static func dateLabel(_ rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return displayDateFormatter.string(from: date)
    }

    static func ageLabel(_ rawValue: String?, now: Date = Date(), calendar: Calendar = .current) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        let start = calendar.startOfDay(for: date)
        let end = calendar.startOfDay(for: now)
        guard start < end else { return nil }

        let components = calendar.dateComponents([.year, .month, .day], from: start, to: end)
        let parts = [
            componentLabel(components.year ?? 0, "year"),
            componentLabel(components.month ?? 0, "month"),
            componentLabel(components.day ?? 0, "day"),
        ].compactMap { $0 }

        return "\(joinedWithAnd(parts)) ago"
    }

    static func year(_ rawValue: String?) -> String? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 4 else { return nil }
        return String(trimmed.prefix(4))
    }

    private static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let dateOnly = String(trimmed.prefix(10))
        return dateOnlyFormatter.date(from: dateOnly)
            ?? isoFormatter.date(from: trimmed)
            ?? fallbackISOFormatter.date(from: trimmed)
    }

    private static func componentLabel(_ value: Int, _ unit: String) -> String? {
        guard value > 0 else { return nil }
        return "\(value) \(unit)\(value == 1 ? "" : "s")"
    }

    private static func joinedWithAnd(_ parts: [String]) -> String {
        guard parts.count > 2 else { return parts.joined(separator: " and ") }
        return "\(parts.dropLast().joined(separator: ", ")) and \(parts[parts.count - 1])"
    }
}
