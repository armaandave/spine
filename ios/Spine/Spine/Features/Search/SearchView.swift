import SwiftUI

struct RecentSearch: Codable, Equatable, Hashable {
    let text: String
    let mediaType: String

    func matches(_ other: RecentSearch) -> Bool {
        text.caseInsensitiveCompare(other.text) == .orderedSame && mediaType == other.mediaType
    }

    static func decodeList(from string: String, fallbackMediaType: String) -> [RecentSearch] {
        guard let data = string.data(using: .utf8) else { return [] }
        if let searches = try? JSONDecoder().decode([RecentSearch].self, from: data) {
            return searches
        }
        if let legacy = try? JSONDecoder().decode([String].self, from: data) {
            return legacy.map { RecentSearch(text: $0, mediaType: fallbackMediaType) }
        }
        return []
    }
}

@MainActor
@Observable
final class SearchViewModel {
    var query = ""
    var mediaTypes = APIConstants.fallbackMediaTypes
    var results: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?
    var unavailableMediaTypes: [String] = []
    var resultRevision = 0

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private var searchID = 0

    init(mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    static func lensMediaTypes(from mediaTypes: [String]) -> [String] {
        let filtered = enabledMediaTypes(from: mediaTypes)
        return filtered.isEmpty ? APIConstants.fallbackMediaTypes : filtered
    }

    static func enabledMediaTypes(from mediaTypes: [String]) -> [String] {
        mediaTypes.filter { !["episode", "season"].contains($0) }
    }

    static func allSearchScopes(from mediaTypes: [String]) -> [String] {
        [APIConstants.allMedia] + enabledMediaTypes(from: mediaTypes)
    }

    func loadMeta() async {
        do {
            let meta = try await mediaRepository.meta()
            mediaTypes = meta.enabledMediaTypes.map(Self.enabledMediaTypes(from:))
                ?? Self.lensMediaTypes(from: meta.mediaTypes)
        } catch {
            mediaTypes = Self.lensMediaTypes(from: APIConstants.fallbackMediaTypes)
        }
    }

    func clear() {
        searchID += 1
        query = ""
        results = []
        errorMessage = nil
        unavailableMediaTypes = []
        isLoading = false
    }

    func search(_ text: String? = nil, mediaType: String) async {
        let trimmed = (text ?? query).trimmingCharacters(in: .whitespacesAndNewlines)
        searchID += 1
        let currentSearchID = searchID
        query = trimmed

        guard !trimmed.isEmpty else {
            results = []
            unavailableMediaTypes = []
            return
        }

        isLoading = true
        errorMessage = nil
        unavailableMediaTypes = []
        defer {
            if currentSearchID == searchID {
                isLoading = false
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
            resultRevision += 1
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
}

struct SearchView: View {
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let mediaLensStore: MediaLensStore
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let focusRequest: Int
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    @MainActor
    init(
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository? = nil,
        peopleRepository: PeopleRepository? = nil,
        mediaLensStore: MediaLensStore? = nil,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .search,
        focusRequest: Int = 0,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository ?? AppRepositories.current().lists
        self.peopleRepository = peopleRepository ?? AppRepositories.current().people
        self.mediaLensStore = mediaLensStore ?? MediaLensStore()
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.focusRequest = focusRequest
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        SearchViewContainer(
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            peopleRepository: peopleRepository,
            mediaLensStore: mediaLensStore,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            focusRequest: focusRequest,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

/// Owns detail presentation so neither search typing nor results updates touch `MediaDetailView`.
private struct SearchViewContainer: View {
    @Environment(\.appNavigationState) private var appNavigationState
    @State private var selectedRef: MediaRef?
    @State private var selectedFeaturedList: FeaturedListSelection?
    @State private var focusesSearchOnDismiss = false
    @Namespace private var featuredZoom
    @State private var returnFocusRequest = 0

    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let listRepository: ListRepository
    let peopleRepository: PeopleRepository
    let mediaLensStore: MediaLensStore
    let currentUserId: Int?
    let selectedTab: AppTab
    let focusRequest: Int
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        SearchViewContent(
            mediaRepository: mediaRepository,
            listRepository: listRepository,
            mediaLensStore: mediaLensStore,
            focusRequest: focusRequest + returnFocusRequest,
            onUnauthorized: onUnauthorized,
            onFeaturedList: { selectedFeaturedList = $0 },
            onSelect: { selectedRef = $0.ref }
        )
        .environment(\.featuredZoomNamespace, featuredZoom)
        .onChange(of: appNavigationState?.returnToSearchRequest) {
            focusesSearchOnDismiss = true
        }
        .fullScreenCover(item: $selectedRef, onDismiss: {
            selectedRef = nil
            focusSearchIfReturning()
        }) { ref in
            MediaDetailCover(
                ref: ref,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $selectedFeaturedList, onDismiss: {
            selectedFeaturedList = nil
            focusSearchIfReturning()
        }) { selection in
            let repositories = AppRepositories.current()
            ProfileListDetailView(
                listId: selection.card.id,
                featured: selection.card,
                profileRepository: repositories.profile,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                activityRepository: repositories.activity,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
            .navigationTransition(.zoom(sourceID: selection.id, in: featuredZoom))
        }
    }

    /// Focus only after the cover finishes dismissing, so the search field is on screen.
    private func focusSearchIfReturning() {
        guard focusesSearchOnDismiss else { return }
        focusesSearchOnDismiss = false
        returnFocusRequest += 1
    }
}

/// Thin wrapper so `MediaDetailView` is only constructed when the cover is actually presented.
private struct MediaDetailCover: View {
    let ref: MediaRef
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let diaryRepository: DiaryRepository
    let listRepository: ListRepository
    let peopleRepository: PeopleRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void

    var body: some View {
        MediaDetailView(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            peopleRepository: peopleRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized
        )
    }
}

struct ProfileBackdropSearchView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selectedMedia: MediaSummary?
    @State private var mediaLensStore: MediaLensStore

    private let mediaRepository: MediaRepository
    private let profileRepository: ProfileRepository
    private let currentBackdropURL: String?
    private let onUnauthorized: () -> Void
    private let onSaved: (ProfileBackdropSaveResponse) -> Void

    private let supportedTypes = ["movie", "tv", "game"]

    init(
        mediaRepository: MediaRepository,
        profileRepository: ProfileRepository,
        currentBackdropURL: String?,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (ProfileBackdropSaveResponse) -> Void
    ) {
        self.mediaRepository = mediaRepository
        self.profileRepository = profileRepository
        self.currentBackdropURL = currentBackdropURL
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
        _mediaLensStore = State(initialValue: MediaLensStore(
            defaults: UserDefaults(suiteName: "ProfileBackdropSearch") ?? .standard
        ))
    }

    var body: some View {
        SearchViewContent(
            mediaRepository: mediaRepository,
            mediaLensStore: mediaLensStore,
            supportedMediaTypes: supportedTypes,
            title: "Profile Backdrop",
            onCancel: { dismiss() },
            onUnauthorized: onUnauthorized,
            onSelect: { selectedMedia = $0 }
        )
        .fullScreenCover(item: $selectedMedia, onDismiss: { selectedMedia = nil }) { media in
            BackdropPickerView(
                ref: media.ref,
                currentBackdropURL: currentBackdropURL,
                mediaRepository: mediaRepository,
                profileRepository: profileRepository,
                onUnauthorized: onUnauthorized
            ) { response in
                onSaved(response)
                dismiss()
            }
        }
    }
}

private struct SearchViewContent: View {
    @State private var viewModel: SearchViewModel
    @State private var isMediaLensExpanded = false
    @State private var isSearchActive = false
    @State private var draftText = ""
    @State private var recentMedia: [MediaSummary] = []
    @State private var featuredStore: FeaturedListsStore?
    @State private var selectedSearchType: String
    @AppStorage("recentMedia") private var recentMediaData = "[]"

    let mediaLensStore: MediaLensStore
    let focusRequest: Int
    let supportedMediaTypes: [String]?
    let title: String
    let onCancel: (() -> Void)?
    let onFeaturedList: (FeaturedListSelection) -> Void
    let onSelect: (MediaSummary) -> Void

    init(
        mediaRepository: MediaRepository,
        listRepository: ListRepository? = nil,
        mediaLensStore: MediaLensStore,
        focusRequest: Int = 0,
        supportedMediaTypes: [String]? = nil,
        title: String = "Search",
        onCancel: (() -> Void)? = nil,
        onUnauthorized: @escaping () -> Void,
        onFeaturedList: @escaping (FeaturedListSelection) -> Void = { _ in },
        onSelect: @escaping (MediaSummary) -> Void
    ) {
        _viewModel = State(initialValue: SearchViewModel(mediaRepository: mediaRepository, onUnauthorized: onUnauthorized))
        _selectedSearchType = State(initialValue: supportedMediaTypes == nil ? APIConstants.allMedia : mediaLensStore.selectedMediaType)
        _featuredStore = State(initialValue: listRepository.map {
            FeaturedListsStore(repository: $0, onUnauthorized: onUnauthorized)
        })
        self.mediaLensStore = mediaLensStore
        self.focusRequest = focusRequest
        self.supportedMediaTypes = supportedMediaTypes
        self.title = title
        self.onCancel = onCancel
        self.onFeaturedList = onFeaturedList
        self.onSelect = onSelect
    }

    var body: some View {
        ZStack {
            NavigationStack {
                VStack(spacing: 0) {
                    MediaSearchBar(
                        text: $draftText,
                        selectedMediaType: selectedMediaTypeBinding,
                        isLensExpanded: $isMediaLensExpanded,
                        availableTypes: availableSearchTypes,
                        onLensTap: {
                            isMediaLensExpanded = true
                        },
                        onLensSelect: { selectedType in
                            Task {
                                await searchCurrentQuery(mediaType: selectedType)
                            }
                        },
                        onSearch: { text in
                            search(text)
                        },
                        onClear: {
                            viewModel.clear()
                        },
                        onFocusChange: { focused in
                            if focused {
                                isSearchActive = true
                            }
                        },
                        focusRequest: focusRequest
                    )

                    SearchResultsSection(
                        query: viewModel.query,
                        selectedMediaType: selectedSearchType,
                        results: viewModel.results,
                        resultRevision: viewModel.resultRevision,
                        isLoading: viewModel.isLoading,
                        errorMessage: viewModel.errorMessage,
                        unavailableMediaTypes: viewModel.unavailableMediaTypes,
                        showsMediaTypes: selectedSearchType == APIConstants.allMedia,
                        isSearchActive: isSearchActive,
                        recentMedia: recentMedia,
                        featuredStore: featuredStore,
                        enabledMediaTypes: viewModel.mediaTypes,
                        onFeaturedList: onFeaturedList,
                        onRecentMedia: { media in
                            saveRecentMedia(media)
                            onSelect(media)
                        },
                        onSelect: { media in
                            saveRecentMedia(media)
                            onSelect(media)
                        }
                    )
                    .blur(radius: isMediaLensExpanded ? 8 : 0)
                    .allowsHitTesting(!isMediaLensExpanded)
                    .overlay {
                        if isMediaLensExpanded {
                            Color.black.opacity(0.001)
                                .onTapGesture {
                                    isMediaLensExpanded = false
                                }
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.black)
                .mediaLensAtmosphere(theme: currentTheme)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(.hidden, for: .navigationBar)
                .toolbarColorScheme(.dark, for: .navigationBar)
                .toolbar {
                    if let onCancel {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Cancel", action: onCancel)
                        }
                    }
                }
                .task {
                    if let supportedMediaTypes {
                        viewModel.mediaTypes = supportedMediaTypes
                    } else {
                        await viewModel.loadMeta()
                    }
                    validateSelectedMediaType()
                    loadRecentMedia()
                }
                .onChange(of: recentMediaData) { _, _ in loadRecentMedia() }
                .onReceive(NotificationCenter.default.publisher(for: .profileDidUpdate)) { notification in
                    guard allowsAllMediaSearch,
                          let profile = notification.userInfo?["profile"] as? UserProfile else { return }
                    viewModel.mediaTypes = SearchViewModel.enabledMediaTypes(from: profile.preferences.enabledMediaTypes)
                    validateSelectedMediaType()
                    loadRecentMedia()
                    Task { await searchCurrentQuery(mediaType: selectedSearchType) }
                }
                .onDisappear {
                    isSearchActive = false
                }
                .task(id: draftText) {
                    try? await Task.sleep(for: .milliseconds(300))
                    guard !Task.isCancelled else { return }
                    await viewModel.search(draftText, mediaType: selectedSearchType)
                }
            }
            .background(Color.black)
        }
    }

    private var currentTheme: MediaTypeTheme {
        mediaLensStore.theme(for: selectedSearchType)
    }

    private var allowsAllMediaSearch: Bool {
        supportedMediaTypes == nil
    }

    private var availableSearchTypes: [String] {
        let concrete = viewModel.mediaTypes
        return allowsAllMediaSearch ? SearchViewModel.allSearchScopes(from: concrete) : concrete
    }

    private var selectedMediaTypeBinding: Binding<String> {
        Binding(
            get: { selectedSearchType },
            set: {
                selectedSearchType = $0
                if $0 != APIConstants.allMedia {
                    mediaLensStore.setMediaType($0)
                }
                loadRecentMedia()
            }
        )
    }

    private func search(_ text: String, mediaType: String? = nil) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let type = mediaType ?? selectedSearchType
        Task { await viewModel.search(trimmed, mediaType: type) }
    }

    private func searchCurrentQuery(mediaType: String) async {
        let trimmed = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        await viewModel.search(trimmed, mediaType: mediaType)
    }

    private func validateSelectedMediaType() {
        if allowsAllMediaSearch {
            if selectedSearchType != APIConstants.allMedia,
               !viewModel.mediaTypes.contains(selectedSearchType) {
                selectedSearchType = APIConstants.allMedia
            }
        } else {
            selectedSearchType = mediaLensStore.validateSelection(in: viewModel.mediaTypes)
        }
    }

    private func loadRecentMedia() {
        let enabled = supportedMediaTypes ?? viewModel.mediaTypes
        recentMedia = RecentMedia.decodeList(from: recentMediaData, supportedMediaTypes: enabled)
        if selectedSearchType != APIConstants.allMedia {
            recentMedia = recentMedia.filter { $0.ref.mediaType == selectedSearchType }
        }
    }

    private func saveRecentMedia(_ media: MediaSummary) {
        var mediaItems = RecentMedia.decodeList(from: recentMediaData).filter { $0.ref != media.ref }
        mediaItems.insert(media, at: 0)
        mediaItems = Array(mediaItems.prefix(8))
        if let data = try? JSONEncoder().encode(mediaItems),
           let string = String(data: data, encoding: .utf8) {
            recentMediaData = string
            loadRecentMedia()
        }
    }
}

enum RecentMedia {
    static func decodeList(from string: String, supportedMediaTypes: [String]? = nil) -> [MediaSummary] {
        let media = (try? JSONDecoder().decode([MediaSummary].self, from: Data(string.utf8))) ?? []
        guard let supportedMediaTypes else { return media }
        return media.filter { supportedMediaTypes.contains($0.ref.mediaType) }
    }
}

private struct SearchResultsSection: View {
    private enum ContentPhase: Hashable {
        case error
        case results(Int)
        case loading
        case prompt
        case noResults
        case recent
        case featured(String)
    }

    let query: String
    let selectedMediaType: String
    let results: [MediaSummary]
    let resultRevision: Int
    let isLoading: Bool
    let errorMessage: String?
    let unavailableMediaTypes: [String]
    let showsMediaTypes: Bool
    let isSearchActive: Bool
    let recentMedia: [MediaSummary]
    let featuredStore: FeaturedListsStore?
    let enabledMediaTypes: [String]
    let onFeaturedList: (FeaturedListSelection) -> Void
    let onRecentMedia: (MediaSummary) -> Void
    let onSelect: (MediaSummary) -> Void

    var body: some View {
        VStack(spacing: 0) {
            if !unavailableMediaTypes.isEmpty {
                MediaSearchWarning(mediaTypes: unavailableMediaTypes)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
            }

            ZStack {
                Color.black

                content
                    .spineContentTransition(value: contentPhase)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if let error = errorMessage {
            ContentUnavailableView("Search failed", systemImage: "exclamationmark.triangle", description: Text(error))
        } else if !results.isEmpty {
            SearchResultsList(results: results, showsMediaTypes: showsMediaTypes, onSelect: onSelect)
        } else {
            SearchEmptyState(
                query: query,
                selectedMediaType: selectedMediaType,
                isLoading: isLoading,
                showsMediaTypes: showsMediaTypes,
                isSearchActive: isSearchActive,
                recentMedia: recentMedia,
                featuredStore: featuredStore,
                enabledMediaTypes: enabledMediaTypes,
                onFeaturedList: onFeaturedList,
                onRecentMedia: onRecentMedia
            )
        }
    }

    private var contentPhase: ContentPhase {
        if errorMessage != nil { return .error }
        if !results.isEmpty { return .results(resultRevision) }
        if isLoading { return .loading }
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return .noResults }
        if featuredStore != nil, !isSearchActive { return .featured(selectedMediaType) }
        return recentMedia.isEmpty ? .prompt : .recent
    }
}

private struct SearchEmptyState: View {
    let query: String
    let selectedMediaType: String
    let isLoading: Bool
    let showsMediaTypes: Bool
    let isSearchActive: Bool
    let recentMedia: [MediaSummary]
    let featuredStore: FeaturedListsStore?
    let enabledMediaTypes: [String]
    let onFeaturedList: (FeaturedListSelection) -> Void
    let onRecentMedia: (MediaSummary) -> Void

    var body: some View {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isLoading {
            SearchNoResultsState(query: query, selectedMediaType: selectedMediaType)
        } else if let featuredStore, !isSearchActive {
            FeaturedLanding(
                store: featuredStore,
                selectedMediaType: selectedMediaType,
                enabledMediaTypes: enabledMediaTypes,
                onSelect: onFeaturedList
            )
        } else if recentMedia.isEmpty {
            ContentUnavailableView("Search Spine", systemImage: "magnifyingglass", description: Text("Enter a title to find media."))
        } else {
            List {
                Section("Recently Opened") {
                    ForEach(recentMedia) { media in
                        Button {
                            onRecentMedia(media)
                        } label: {
                            SearchResultRow(
                                result: media,
                                usesDiarySize: true,
                                showsMediaType: showsMediaTypes
                            )
                        }
                        .buttonStyle(.plain)
                        .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 12))
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .scrollDismissesKeyboard(.interactively)
        }
    }
}

private struct FeaturedListsDiscovery: View {
    let lists: [CustomListSummary]
    let isLoading: Bool
    let errorMessage: String?
    let onSelect: (Int) -> Void

    var body: some View {
        if isLoading, lists.isEmpty {
            ProgressView("Loading featured lists…")
                .tint(.white)
        } else if let errorMessage, lists.isEmpty {
            ContentUnavailableView(
                "Could not load featured lists",
                systemImage: "exclamationmark.triangle",
                description: Text(errorMessage)
            )
        } else if lists.isEmpty {
            ContentUnavailableView(
                "Search Spine",
                systemImage: "magnifyingglass",
                description: Text("Enter a title to find media.")
            )
        } else {
            List {
                Section("Featured Lists") {
                    ForEach(lists) { list in
                        ProfileListRowContainer {
                            Button {
                                onSelect(list.id)
                            } label: {
                                ProfileListRow(list: list)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(list.name), \(list.itemsCount.formatted()) items")
                            .accessibilityHint("Opens list")
                        }
                        .listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 6, trailing: 0))
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
        }
    }
}

private struct SearchNoResultsState: View {
    let query: String
    let selectedMediaType: String

    var body: some View {
        ContentUnavailableView(
            selectedMediaType == APIConstants.allMedia
                ? "No media found"
                : "No \(MediaTypeTheme.theme(for: selectedMediaType).displayName.lowercased()) found",
            systemImage: "magnifyingglass",
            description: Text("No matches for \"\(query.trimmingCharacters(in: .whitespacesAndNewlines))\". Try another title or media type.")
        )
    }
}

private struct SearchResultsList: View {
    let results: [MediaSummary]
    let showsMediaTypes: Bool
    let onSelect: (MediaSummary) -> Void

    var body: some View {
        List {
            ForEach(results) { result in
                Button {
                    onSelect(result)
                } label: {
                    SearchResultRow(result: result, showsMediaType: showsMediaTypes)
                }
                .buttonStyle(.plain)
                .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 12))
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color.black)
        .scrollDismissesKeyboard(.interactively)
    }
}

enum SearchResultAccessory {
    case chevron
    case selection(isSelected: Bool)
}

struct SearchResultRow: View {
    let result: MediaSummary
    var usesDiarySize = false
    var showsMediaType = false
    var accessory: SearchResultAccessory = .chevron

    var body: some View {
        HStack(spacing: 14) {
            MediaArtwork(
                url: result.displayPosterURL,
                title: result.title,
                slot: usesDiarySize ? .diaryRow : .searchRow,
                mediaType: result.ref.mediaType,
                orientation: result.posterOrientation
            )
            .scaleEffect(usesDiarySize ? 0.75 : 1)
            .frame(width: usesDiarySize ? 42 : nil, height: usesDiarySize ? 63 : nil)

            VStack(alignment: .leading, spacing: 4) {
                Text(result.title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                if showsMediaType {
                    MediaTypeChip(mediaType: result.ref.mediaType)
                }

                if let subtitle = result.searchResultSubtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                if let overview = result.overview?.trimmingCharacters(in: .whitespacesAndNewlines),
                   !overview.isEmpty {
                    Text(overview)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .padding(.top, 2)
                }
            }

            Spacer(minLength: 8)

            accessoryView
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var accessoryView: some View {
        switch accessory {
        case .chevron:
            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.tertiary)
        case let .selection(isSelected):
            Image(systemName: isSelected ? "checkmark.circle.fill" : "plus.circle")
                .font(.system(size: 24, weight: .semibold))
                .foregroundStyle(isSelected ? .green : .white.opacity(0.72))
                .contentTransition(.symbolEffect(.replace))
                .accessibilityHidden(true)
        }
    }

}

struct MediaTypeChip: View {
    let mediaType: String

    var body: some View {
        Text(MediaTypeTheme.theme(for: mediaType).displayName)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(.white.opacity(0.08), in: Capsule())
    }
}

struct MediaSearchWarning: View {
    let mediaTypes: [String]

    var body: some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.caption)
            .foregroundStyle(.yellow)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var message: String {
        Self.message(for: mediaTypes)
    }

    static func message(for mediaTypes: [String]) -> String {
        let names = mediaTypes.map { MediaTypeTheme.theme(for: $0).displayName }
        let unavailable: String
        if names.count == 1 {
            unavailable = names[0]
        } else {
            unavailable = "\(names.dropLast().joined(separator: ", ")) and \(names.last ?? "")"
        }
        return "\(unavailable) couldn’t be searched. Other results are shown."
    }
}

extension MediaSummary {
    var searchResultSubtitle: String? {
        if ref.mediaType == "music" {
            let text = subtitle?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return text.isEmpty ? nil : text
        }

        let text = [subtitle, formattedReleaseDate]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        return text.isEmpty ? nil : text
    }

    private var formattedReleaseDate: String? {
        guard let releaseDate else { return nil }
        return SearchDateFormatter.string(from: releaseDate) ?? releaseDate
    }
}

private enum SearchDateFormatter {
    private static let input: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let output: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "MMMM d, yyyy"
        return formatter
    }()

    static func string(from raw: String) -> String? {
        let trimmed = String(raw.trimmingCharacters(in: .whitespacesAndNewlines).prefix(10))
        guard let date = input.date(from: trimmed) else { return nil }
        return output.string(from: date)
    }
}
