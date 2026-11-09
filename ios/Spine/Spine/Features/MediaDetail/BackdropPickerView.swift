import SwiftUI

@MainActor
@Observable
final class BackdropPickerViewModel {
    var backdrops: [PosterOption] = []
    var selectedLanguage = "all"
    var selectedBackdropURL: String?
    var isLoading = true
    var isSaving = false
    var errorMessage: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private let currentBackdropURL: String?
    private let saveAction: (MediaRef, String) async throws -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        initialBackdropURL: String? = nil,
        currentBackdropURL: String? = nil,
        onUnauthorized: @escaping () -> Void,
        saveAction: @escaping (MediaRef, String) async throws -> Void
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.currentBackdropURL = currentBackdropURL
        self.onUnauthorized = onUnauthorized
        self.saveAction = saveAction
        if let initialBackdropURL {
            backdrops = [
                PosterOption(
                    url: initialBackdropURL,
                    thumbnailUrl: initialBackdropURL,
                    width: 0,
                    height: 0,
                    aspectRatio: 16.0 / 9.0,
                    voteAverage: 0,
                    voteCount: 0,
                    language: nil,
                    isOriginal: true,
                    isSelected: true
                ),
            ]
            selectedBackdropURL = initialBackdropURL
            selectedLanguage = "none"
            isLoading = false
        }
    }

    var languageOptions: [PosterLanguageOption] {
        var options = [PosterLanguageOption(id: "all", title: "All Languages")]
        let languages = Set(backdrops.compactMap(\.language))
        if languages.contains("en") {
            options.append(PosterLanguageOption(id: "en", title: "English"))
        }
        options += languages
            .filter { $0 != "en" }
            .sorted()
            .map { PosterLanguageOption(id: $0, title: PosterPickerViewModel.languageName(for: $0)) }
        if backdrops.contains(where: { $0.language == nil }) {
            options.append(PosterLanguageOption(id: "none", title: "No Language"))
        }
        return options
    }

    var filteredBackdrops: [PosterOption] {
        switch selectedLanguage {
        case "all":
            return backdrops
        case "none":
            return backdrops.filter { $0.language == nil }
        default:
            return backdrops.filter { $0.language == selectedLanguage }
        }
    }

    var canSave: Bool {
        selectedBackdropURL != nil && !isSaving
    }

    func isCurrent(_ backdrop: PosterOption) -> Bool {
        currentBackdropURL.map { $0 == backdrop.url } ?? backdrop.isSelected
    }

    func load() async {
        guard backdrops.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            backdrops = try await mediaRepository.backdrops(ref: ref).uniquedByURL().pinningCurrentFirst()
            let currentInOptions = currentBackdropURL.flatMap { current in
                backdrops.contains { $0.url == current } ? current : nil
            }
            selectedBackdropURL = currentInOptions ?? backdrops.first(where: \.isSelected)?.url ?? backdrops.first?.url
            selectedLanguage = backdrops.contains(where: { $0.language == nil }) ? "none" : "all"
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save() async {
        guard let selectedBackdropURL, !isSaving else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            try await saveAction(ref, selectedBackdropURL)
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private extension Array where Element == PosterOption {
    func uniquedByURL() -> [PosterOption] {
        var seen = Set<String>()
        return filter { seen.insert($0.url).inserted }
    }

    func pinningCurrentFirst() -> [PosterOption] {
        guard let selectedIndex = firstIndex(where: \.isSelected), selectedIndex != startIndex else { return self }
        var options = self
        let selected = options.remove(at: selectedIndex)
        options.insert(selected, at: startIndex)
        return options
    }
}

struct BackdropPickerView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: BackdropPickerViewModel

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        initialBackdropURL: String? = nil,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (BackdropSaveResponse) -> Void
    ) {
        _viewModel = State(initialValue: BackdropPickerViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            initialBackdropURL: initialBackdropURL,
            onUnauthorized: onUnauthorized,
            saveAction: { ref, backdropURL in
                let response = try await mediaRepository.saveBackdrop(ref: ref, backdropURL: backdropURL)
                onSaved(response)
            }
        ))
    }

    init(
        ref: MediaRef,
        currentBackdropURL: String?,
        mediaRepository: MediaRepository,
        profileRepository: ProfileRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (ProfileBackdropSaveResponse) -> Void
    ) {
        _viewModel = State(initialValue: BackdropPickerViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            currentBackdropURL: currentBackdropURL,
            onUnauthorized: onUnauthorized,
            saveAction: { ref, backdropURL in
                let response = try await profileRepository.saveProfileBackdrop(ref: ref, backdropURL: backdropURL)
                onSaved(response)
            }
        ))
    }

    /// For a pick that is saved by something other than a title or a profile (Home's site-wide picture).
    init(
        ref: MediaRef,
        currentBackdropURL: String?,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        saveAction: @escaping (MediaRef, String) async throws -> Void,
        onSaved: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: BackdropPickerViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            currentBackdropURL: currentBackdropURL,
            onUnauthorized: onUnauthorized,
            saveAction: { ref, backdropURL in
                try await saveAction(ref, backdropURL)
                onSaved()
            }
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                Group {
                    if viewModel.isLoading {
                        ProgressView()
                            .tint(.white)
                    } else if let error = viewModel.errorMessage, viewModel.backdrops.isEmpty {
                        ContentUnavailableView("Could not load backdrops", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding()
                    } else if viewModel.backdrops.isEmpty {
                        ContentUnavailableView("No backdrops found", systemImage: "photo.on.rectangle")
                            .foregroundStyle(.white)
                            .padding()
                    } else {
                        backdropGrid
                            .allowsHitTesting(!viewModel.isSaving)
                    }
                }
                .spineContentTransition(value: contentPhase)
            }
            .navigationTitle("Customize Backdrop")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(viewModel.isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            await viewModel.save()
                            if viewModel.errorMessage == nil {
                                dismiss()
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
                        .spineContentTransition(value: viewModel.isSaving)
                    }
                    .disabled(!viewModel.canSave)
                }
            }
            .task {
                await viewModel.load()
            }
        }
        .interactiveDismissDisabled(viewModel.isSaving)
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !viewModel.backdrops.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var backdropGrid: some View {
        VStack(spacing: 14) {
            Picker("Language", selection: $viewModel.selectedLanguage) {
                ForEach(viewModel.languageOptions) { option in
                    Text(option.title).tag(option.id)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red.opacity(0.9))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
            }

            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 12)], spacing: 12) {
                    ForEach(viewModel.filteredBackdrops) { backdrop in
                        BackdropOptionCell(
                            backdrop: backdrop,
                            isSelected: viewModel.selectedBackdropURL == backdrop.url,
                            isCurrent: viewModel.isCurrent(backdrop)
                        ) {
                            viewModel.selectedBackdropURL = backdrop.url
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
        }
        .padding(.top, 12)
    }
}

private struct BackdropOptionCell: View {
    let backdrop: PosterOption
    let isSelected: Bool
    let isCurrent: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: .topLeading) {
                backdropImage

                if isCurrent {
                    Text("Current")
                        .font(.caption2.weight(.heavy))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                        .background(.green, in: Capsule())
                        .padding(6)
                }

                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title2)
                        .foregroundStyle(.white)
                        .shadow(radius: 4)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                        .padding(7)
                }
            }
        }
        .buttonStyle(.plain)
        .contentShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityLabel(backdrop.isSelected ? "Current backdrop" : "Backdrop option")
    }

    private var backdropImage: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Color.gray.opacity(0.18))
            .aspectRatio(16.0 / 9.0, contentMode: .fit)
            .frame(maxWidth: .infinity)
            .overlay {
                SpineAsyncImage(url: URL(string: backdrop.thumbnailUrl ?? backdrop.url)) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(isSelected ? .white : .clear, lineWidth: 3)
            }
    }
}
