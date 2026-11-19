import SwiftUI

struct ListMediaPickerView: View {
    private enum ResultsPhase: Hashable {
        case error
        case loading
        case results
        case prompt
        case recent
        case noResults
    }

    @Environment(\.dismiss) private var dismiss

    private let viewModel: ListComposerViewModel

    @State private var searchViewModel: SearchViewModel
    @State private var selectedSearchType = APIConstants.allMedia
    @State private var searchText = ""
    @AppStorage("recentMedia") private var recentMediaData = "[]"

    init(
        viewModel: ListComposerViewModel,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.viewModel = viewModel
        _searchViewModel = State(initialValue: SearchViewModel(
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            VStack(spacing: 0) {
                header

                MediaSearchBar(
                    text: $searchText,
                    selectedMediaType: selectedMediaTypeBinding,
                    availableTypes: SearchViewModel.allSearchScopes(from: searchViewModel.mediaTypes),
                    onLensSelect: searchSelectedMediaType,
                    onSearch: searchSubmittedText,
                    onClear: searchViewModel.clear
                )

                if !searchViewModel.unavailableMediaTypes.isEmpty {
                    MediaSearchWarning(mediaTypes: searchViewModel.unavailableMediaTypes)
                        .padding(.horizontal, 18)
                        .padding(.vertical, 6)
                }

                ScrollView(showsIndicators: false) {
                    resultsContent
                        .frame(maxWidth: .infinity, minHeight: 420, alignment: .top)
                        .spineContentTransition(value: resultsPhase)
                        .padding(.horizontal, 16)
                        .padding(.top, 8)
                        .padding(.bottom, 28)
                }
                .scrollDismissesKeyboard(.interactively)
            }
        }
        .mediaLensAtmosphere(theme: MediaTypeTheme.theme(for: selectedSearchType))
        .task {
            await searchViewModel.loadMeta()
            validateSelectedMediaType()
        }
        .task(id: searchText) {
            await searchAfterDebounce()
        }
        .onReceive(NotificationCenter.default.publisher(for: .profileDidUpdate)) { notification in
            guard let profile = notification.userInfo?["profile"] as? UserProfile else { return }
            searchViewModel.mediaTypes = SearchViewModel.enabledMediaTypes(from: profile.preferences.enabledMediaTypes)
            validateSelectedMediaType()
            Task { await searchViewModel.search(searchText, mediaType: selectedSearchType) }
        }
        .preferredColorScheme(.dark)
    }

    private var header: some View {
        HStack {
            Text("Add Media")
                .font(.system(size: 22, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)

            Spacer()

            Button(action: dismiss.callAsFunction) {
                Text("Done · \(viewModel.draft.items.count)")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 13)
                    .frame(height: 36)
                    .background(.white, in: Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Done adding media, \(viewModel.draft.items.count) selected")
        }
        .padding(.horizontal, 18)
        .padding(.top, 8)
        .padding(.bottom, 10)
    }

    @ViewBuilder
    private var resultsContent: some View {
        if let error = searchViewModel.errorMessage {
            ContentUnavailableView("Search failed", systemImage: "exclamationmark.triangle", description: Text(error))
                .frame(minHeight: 420)
        } else if searchViewModel.isLoading && searchViewModel.results.isEmpty {
            ProgressView("Searching…")
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 420)
        } else if !searchViewModel.results.isEmpty {
            searchRows(searchViewModel.results)
        } else if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if recentMedia.isEmpty {
                ContentUnavailableView(
                    "Search Spine",
                    systemImage: "magnifyingglass",
                    description: Text("Enter a title to add media to this list.")
                )
                .frame(minHeight: 420)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Recently Opened")
                        .font(.system(size: 12, weight: .heavy, design: .rounded))
                        .foregroundStyle(.white.opacity(0.48))
                        .padding(.horizontal, 4)
                    searchRows(recentMedia)
                }
            }
        } else {
            ContentUnavailableView(
                selectedSearchType == APIConstants.allMedia ? "No media found" : "No results",
                systemImage: "magnifyingglass",
                description: Text("Try another title or media type.")
            )
            .frame(minHeight: 420)
        }
    }

    private var resultsPhase: ResultsPhase {
        if searchViewModel.errorMessage != nil { return .error }
        if searchViewModel.isLoading && searchViewModel.results.isEmpty { return .loading }
        if !searchViewModel.results.isEmpty { return .results }
        if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return recentMedia.isEmpty ? .prompt : .recent
        }
        return .noResults
    }

    private func searchRows(_ items: [MediaSummary]) -> some View {
        LazyVStack(spacing: 0) {
            ForEach(items) { item in
                Button {
                    viewModel.toggleSelection(item)
                    saveRecentMedia(item)
                } label: {
                    SearchResultRow(
                        result: item,
                        showsMediaType: selectedSearchType == APIConstants.allMedia,
                        accessory: .selection(isSelected: viewModel.contains(item))
                    )
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(viewModel.contains(item) ? "Remove" : "Add") \(item.title)")

                if item.id != items.last?.id {
                    Divider()
                        .overlay(.white.opacity(0.08))
                        .padding(.leading, 82)
                }
            }
        }
        .background(.black.opacity(0.2), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(.white.opacity(0.08))
        }
    }

    private var selectedMediaTypeBinding: Binding<String> {
        Binding(
            get: { selectedSearchType },
            set: { selectedSearchType = $0 }
        )
    }

    private var recentMedia: [MediaSummary] {
        let enabled = Set(searchViewModel.mediaTypes)
        return RecentMedia.decodeList(from: recentMediaData)
            .filter { enabled.contains($0.ref.mediaType) }
            .filter {
                selectedSearchType == APIConstants.allMedia || $0.ref.mediaType == selectedSearchType
            }
    }

    private func searchSelectedMediaType(_ mediaType: String) {
        Task {
            let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { return }
            await searchViewModel.search(trimmed, mediaType: mediaType)
        }
    }

    private func searchSubmittedText(_ text: String) {
        Task {
            await searchViewModel.search(text, mediaType: selectedSearchType)
        }
    }

    private func searchAfterDebounce() async {
        do {
            try await Task.sleep(for: .milliseconds(300))
            try Task.checkCancellation()
        } catch {
            return
        }

        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            searchViewModel.clear()
            return
        }
        await searchViewModel.search(trimmed, mediaType: selectedSearchType)
    }

    private func validateSelectedMediaType() {
        let available = searchViewModel.mediaTypes
        if selectedSearchType != APIConstants.allMedia, !available.contains(selectedSearchType) {
            selectedSearchType = APIConstants.allMedia
        }
    }

    private func saveRecentMedia(_ media: MediaSummary) {
        var items = RecentMedia.decodeList(from: recentMediaData).filter { $0.ref != media.ref }
        items.insert(media, at: 0)
        items = Array(items.prefix(8))
        if let data = try? JSONEncoder().encode(items),
           let value = String(data: data, encoding: .utf8) {
            recentMediaData = value
        }
    }
}
