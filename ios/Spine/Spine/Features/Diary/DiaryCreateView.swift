import SwiftUI

@MainActor
@Observable
final class DiaryCreateViewModel {
    var mediaType = APIConstants.allMedia
    var mediaTypes = APIConstants.fallbackMediaTypes
    var query = ""
    var results: [MediaSummary] = []
    var selectedMedia: MediaSummary?
    var consumedAt = Date()
    var ratingText = ""
    var reviewTitle = ""
    var review = ""
    var tagsText = ""
    var visibility = "public"
    var containsSpoilers = false
    var liked = false
    var isRewatch = false
    var isSearching = false
    var isSaving = false
    var errorMessage: String?
    var unavailableMediaTypes: [String] = []
    /// A TV log's Rewatch default came from the server's composer (TV-404).
    private(set) var hasServerRewatchDefault = false
    /// The user changed the Rewatch toggle.
    private(set) var isRewatchEdited = false
    /// What happens after a TV log: the earlier-episodes question, then the season-finish composer (TV-107, TV-403).
    private(set) var tvFollowUp: TVWriteFollowUp?

    private let diaryRepository: DiaryRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository?
    private let onUnauthorized: () -> Void
    private let mutationId = UUID()
    private var searchID = 0
    private var selectionID = 0

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.diaryRepository = diaryRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
    }

    /// The latest date the picker allows: today for calendar-dated logs, TV included (TV-407).
    var latestConsumedDate: Date {
        DiaryLogDateLimit.latest(for: selectedMedia?.ref)
    }

    /// TV logs and the calendar-dated types pick a day, not a time (TV-407).
    var usesCalendarDate: Bool {
        selectedMedia?.ref.usesCalendarLogDate == true
    }

    /// The user turned Rewatch on or off: from now on the toggle is what saves.
    func setRewatch(_ value: Bool) {
        isRewatch = value
        isRewatchEdited = true
    }

    /// The Rewatch value a log saves with. Nil for a TV log whose server default (TV-404) hasn't loaded while the
    /// toggle is untouched: the request then leaves `is_rewatch` out and the server's default applies.
    var isRewatchForRequest: Bool? {
        guard selectedMedia?.ref.isTVFamily == true, !hasServerRewatchDefault, !isRewatchEdited else { return isRewatch }
        return nil
    }

    func loadMeta() async {
        do {
            let meta = try await mediaRepository.meta()
            mediaTypes = meta.enabledMediaTypes.map(SearchViewModel.enabledMediaTypes(from:))
                ?? SearchViewModel.lensMediaTypes(from: meta.mediaTypes)
        } catch {
            mediaTypes = SearchViewModel.lensMediaTypes(from: APIConstants.fallbackMediaTypes)
        }
    }

    func setEnabledMediaTypes(_ types: [String]) {
        mediaTypes = SearchViewModel.enabledMediaTypes(from: types)
        if mediaType != APIConstants.allMedia, !mediaTypes.contains(mediaType) {
            mediaType = APIConstants.allMedia
        }
    }

    func search() async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        searchID += 1
        let currentSearchID = searchID

        isSearching = true
        errorMessage = nil
        unavailableMediaTypes = []
        defer {
            if currentSearchID == searchID {
                isSearching = false
            }
        }

        do {
            let response: MediaSearchResponse
            if mediaType == APIConstants.allMedia {
                response = try await mediaRepository.searchAll(query: trimmed)
            } else {
                response = MediaSearchResponse(
                    results: try await mediaRepository.search(query: trimmed, mediaType: mediaType)
                )
            }
            guard currentSearchID == searchID else { return }
            results = response.results
            unavailableMediaTypes = response.unavailableMediaTypes
        } catch is CancellationError {
            return
        } catch {
            guard currentSearchID == searchID else { return }
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func create() async -> Bool {
        guard let selectedMedia else {
            errorMessage = "Choose media for the diary entry."
            return false
        }

        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            if selectedMedia.ref.usesCalendarLogDate, CalendarDateCodec.isFuture(consumedAt) {
                throw DiaryCreateError.futureDate
            }
            let isTV = selectedMedia.ref.isTVFamily
            let tags = tagsText
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            var request = DiaryEntryWriteRequest(
                ref: selectedMedia.ref,
                consumedAt: consumedAt,
                rating: Decimal(string: ratingText),
                review: review,
                reviewTitle: reviewTitle,
                liked: liked,
                isRewatch: isRewatchForRequest ?? isRewatch,
                autoMarkConsumed: true,
                containsSpoilers: containsSpoilers,
                visibility: selectedMedia.ref.isSingleWeight ? "public" : visibility,
                tags: tags,
                journeyId: selectedMedia.ref.mediaType == "book"
                    ? selectedMedia.userState?.book?.currentJourney?.id
                    : nil,
                // A TV log from this build applies at most once (TV-705).
                mutationId: selectedMedia.ref.mediaType == "book" || isTV ? mutationId : nil
            )
            if isTV {
                request.includesIsRewatch = isRewatchForRequest != nil
                let (_, envelope) = try await diaryRepository.createTV(request)
                if envelope.hasFollowUp {
                    let followUp = TVWriteFollowUp(
                        showRef: selectedMedia.ref,
                        trackingRepository: trackingRepository ?? AppRepositories.current().tracking,
                        mediaRepository: mediaRepository
                    )
                    followUp.handle(envelope)
                    tvFollowUp = followUp
                }
            } else {
                _ = try await diaryRepository.create(request)
            }
            MediaStateChange.post(ref: selectedMedia.ref)
            return true
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func select(_ media: MediaSummary) {
        selectedMedia = media
        ratingText = media.userState?.rating ?? ""
        liked = media.userState?.hasLiked ?? false
        selectionID += 1
        hasServerRewatchDefault = false
        isRewatchEdited = false
        if media.ref.isTVFamily {
            // Until the server's default loads, nothing is guessed (TV-404).
            isRewatch = false
        } else if media.ref.isSingleWeight {
            let state = media.userState
            isRewatch = state?.directConsumption == true
                || (state?.diaryCount ?? 0) > 0
                || (state?.directConsumption == nil
                    && state?.isTracked == true
                    && state?.status == "Completed")
        } else if media.ref.mediaType == "book" {
            isRewatch = media.userState?.book?.isRereading ?? false
        } else {
            isRewatch = (media.userState?.diaryCount ?? 0) > 0
        }
    }

    /// Loads a TV target's Rewatch default from the server's composer (`GET tracking/{source}/{type}/{id}/`,
    /// TV-404). A show that isn't tracked yet, or a failed load, leaves the default to the server when saving.
    func loadRewatchDefault() async {
        guard let media = selectedMedia, media.ref.isTVFamily, let trackingRepository else { return }
        let selection = selectionID
        do {
            let tracking = try await trackingRepository.detail(ref: media.ref)
            guard selection == selectionID, !isRewatchEdited,
                  let composer = tracking.tv?.composer,
                  let target = Self.composerTarget(for: media.ref, in: composer)
            else { return }
            isRewatch = target.isRewatch
            hasServerRewatchDefault = true
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    /// The composer target a log of `ref` saves against: the show, a season or an episode.
    static func composerTarget(for ref: MediaRef, in composer: TVComposer) -> TVComposerTarget? {
        composer.targets.first { target in
            switch ref.mediaType {
            case "tv":
                target.level == .show
            case "season":
                target.level == .season && target.seasonNumber == ref.seasonNumber
            case "episode":
                target.level == .episode && target.seasonNumber == ref.seasonNumber
                    && target.episodeNumber == ref.episodeNumber
            default:
                false
            }
        }
    }
}

private enum DiaryCreateError: LocalizedError {
    case futureDate

    var errorDescription: String? {
        "Consumption dates cannot be in the future."
    }
}

struct DiaryCreateView: View {
    private enum SearchContentPhase: Hashable {
        case idle
        case loading
        case loadingWithResults
        case results
        case selection
    }

    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: DiaryCreateViewModel
    let onCreated: () -> Void
    private let diaryRepository: DiaryRepository
    private let trackingRepository: TrackingRepository
    private let onUnauthorized: () -> Void

    init(
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository = AppRepositories.current().tracking,
        onUnauthorized: @escaping () -> Void = {},
        onCreated: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: DiaryCreateViewModel(
            diaryRepository: diaryRepository,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            onUnauthorized: onUnauthorized
        ))
        self.diaryRepository = diaryRepository
        self.trackingRepository = trackingRepository
        self.onUnauthorized = onUnauthorized
        self.onCreated = onCreated
    }

    var body: some View {
        NavigationStack {
            Form {
                mediaSearchSection
                entrySection
                Group {
                    if let error = viewModel.errorMessage {
                        Section {
                            Text(error)
                                .foregroundStyle(.red)
                        }
                    }
                }
                .spineContentTransition(value: viewModel.errorMessage != nil)
            }
            .navigationTitle("New Diary Entry")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            guard await viewModel.create() else { return }
                            if let followUp = viewModel.tvFollowUp {
                                finishWhenSettled(followUp)
                            } else {
                                onCreated()
                            }
                        }
                    } label: {
                        Group {
                            if viewModel.isSaving {
                                ProgressView()
                            } else {
                                Text("Save")
                            }
                        }
                        .frame(minWidth: 36)
                        .spineContentTransition(value: viewModel.isSaving)
                    }
                    .disabled(viewModel.selectedMedia == nil || viewModel.isSaving)
                }
            }
            .task {
                await viewModel.loadMeta()
            }
            .task(id: viewModel.selectedMedia?.ref) {
                await viewModel.loadRewatchDefault()
            }
            .background {
                // After a TV log: the earlier-episodes question, then the season-finish composer. The entry is
                // saved; this screen closes once nothing is left to show.
                if let followUp = viewModel.tvFollowUp {
                    Color.clear
                        .tvWriteFollowUp(followUp) { item in
                            MediaLogView(
                                detail: item.detail,
                                trackingRepository: trackingRepository,
                                diaryRepository: diaryRepository,
                                onUnauthorized: onUnauthorized,
                                onSaved: {},
                                tvComposer: item.finished.composer
                            )
                        }
                        .onChange(of: followUp.question == nil && followUp.composer == nil) { _, isIdle in
                            if isIdle { finishWhenSettled(followUp) }
                        }
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .profileDidUpdate)) { notification in
                guard let profile = notification.userInfo?["profile"] as? UserProfile else { return }
                let previousMediaType = viewModel.mediaType
                viewModel.setEnabledMediaTypes(profile.preferences.enabledMediaTypes)
                if viewModel.mediaType == previousMediaType {
                    Task { await viewModel.search() }
                }
            }
        }
    }

    /// Closes once the follow-up has nothing left to show: the question answered and any composer closed.
    private func finishWhenSettled(_ followUp: TVWriteFollowUp) {
        Task {
            await followUp.loading?.value
            if followUp.question == nil, followUp.composer == nil {
                onCreated()
            }
        }
    }

    private var mediaSearchSection: some View {
        Section("Media") {
            Picker("Type", selection: $viewModel.mediaType) {
                Text("All Media").tag(APIConstants.allMedia)
                ForEach(viewModel.mediaTypes, id: \.self) { type in
                    Text(MediaTypeTheme.theme(for: type).displayName).tag(type)
                }
            }
            .onChange(of: viewModel.mediaType) {
                Task { await viewModel.search() }
            }

            HStack {
                TextField("Search title", text: $viewModel.query)
                    .textInputAutocapitalization(.never)
                Button {
                    Task { await viewModel.search() }
                } label: {
                    Label("Search", systemImage: "magnifyingglass")
                }
                .labelStyle(.iconOnly)
            }

            Group {
                if viewModel.isSearching {
                    ProgressView()
                }

                if !viewModel.unavailableMediaTypes.isEmpty {
                    MediaSearchWarning(mediaTypes: viewModel.unavailableMediaTypes)
                }

                if let selected = viewModel.selectedMedia {
                    Label(selected.title, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }

                ForEach(viewModel.results.prefix(6)) { result in
                    Button {
                        viewModel.select(result)
                    } label: {
                        HStack {
                            MediaArtwork(
                                url: result.displayPosterURL,
                                title: result.title,
                                slot: .searchRow,
                                mediaType: result.ref.mediaType,
                                orientation: result.posterOrientation
                            )
                            VStack(alignment: .leading) {
                                Text(result.title)
                                if viewModel.mediaType == APIConstants.allMedia {
                                    MediaTypeChip(mediaType: result.ref.mediaType)
                                }
                                if let subtitle = result.subtitle ?? result.releaseDate {
                                    Text(subtitle)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            .spineContentTransition(value: searchContentPhase)
        }
    }

    private var searchContentPhase: SearchContentPhase {
        if viewModel.isSearching {
            return viewModel.results.isEmpty ? .loading : .loadingWithResults
        }
        if !viewModel.results.isEmpty { return .results }
        if viewModel.selectedMedia != nil { return .selection }
        return .idle
    }

    private var entrySection: some View {
        Section("Entry") {
            DatePicker(
                viewModel.selectedMedia?.ref.mediaType == "music" ? "Date listened" : "Consumed",
                selection: $viewModel.consumedAt,
                in: Date.distantPast...viewModel.latestConsumedDate,
                displayedComponents: viewModel.usesCalendarDate ? [.date] : [.date, .hourAndMinute]
            )
            TextField(
                viewModel.selectedMedia?.ref.usesFiveStarRatingScale == true ? "Rating 0.5-5" : "Rating 0-10",
                text: $viewModel.ratingText
            )
                .keyboardType(.decimalPad)
            TextField("Review title", text: $viewModel.reviewTitle)
            TextField("Review", text: $viewModel.review, axis: .vertical)
                .lineLimit(4...8)
            TextField("Tags, comma separated", text: $viewModel.tagsText)
            if viewModel.selectedMedia?.ref.isSingleWeight != true {
                Picker("Visibility", selection: $viewModel.visibility) {
                    ForEach(APIConstants.visibilityChoices, id: \.self) { value in
                        Text(value.capitalized).tag(value)
                    }
                }
            }
            Toggle("Liked", isOn: $viewModel.liked)
            Toggle("Contains spoilers", isOn: $viewModel.containsSpoilers)
            Toggle(viewModel.selectedMedia?.ref.repeatLabel ?? "Rewatch", isOn: Binding(
                get: { viewModel.isRewatch },
                set: { viewModel.setRewatch($0) }
            ))
        }
    }
}
