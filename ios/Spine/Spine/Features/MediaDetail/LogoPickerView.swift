import SwiftUI

@MainActor
@Observable
final class LogoPickerViewModel {
    var logos: [LogoOption] = []
    var selectedLanguage = "en"
    var selectedLogoURL: String?
    var isLoading = true
    var isSaving = false
    var errorMessage: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private let onSaved: (LogoSaveResponse) -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (LogoSaveResponse) -> Void
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
    }

    var languageOptions: [PosterLanguageOption] {
        var options = [PosterLanguageOption(id: "all", title: "All Languages")]
        let languages = Set(logos.compactMap(\.language))
        if languages.contains("en") {
            options.append(PosterLanguageOption(id: "en", title: "English"))
        }
        options += languages
            .filter { $0 != "en" }
            .sorted()
            .map { PosterLanguageOption(id: $0, title: PosterPickerViewModel.languageName(for: $0)) }
        if logos.contains(where: { $0.language == nil }) {
            options.append(PosterLanguageOption(id: "none", title: "No Language"))
        }
        return options
    }

    var filteredLogos: [LogoOption] {
        let filtered: [LogoOption]
        switch selectedLanguage {
        case "all":
            filtered = logos
        case "none":
            filtered = logos.filter { $0.language == nil }
        default:
            filtered = logos.filter { $0.language == selectedLanguage }
        }

        guard let current = logos.first(where: \.isSelected) else { return filtered }
        return [current] + filtered.filter { $0.url != current.url }
    }

    var canSave: Bool {
        selectedLogoURL != nil && !isSaving
    }

    func load() async {
        guard logos.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            logos = try await mediaRepository.logos(ref: ref)
            selectedLogoURL = logos.first(where: \.isSelected)?.url ?? logos.first?.url
            selectedLanguage = logos.contains(where: { $0.language == "en" }) ? "en" : "all"
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func save() async {
        guard let selectedLogoURL, !isSaving else { return }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let response = try await mediaRepository.saveLogo(ref: ref, logoURL: selectedLogoURL)
            onSaved(response)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

struct LogoPickerView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: LogoPickerViewModel
    private let showsLanguageFilter: Bool

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (LogoSaveResponse) -> Void
    ) {
        showsLanguageFilter = ref.mediaType != "game"
        _viewModel = State(initialValue: LogoPickerViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized,
            onSaved: onSaved
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
                    } else if let error = viewModel.errorMessage, viewModel.logos.isEmpty {
                        ContentUnavailableView(
                            "Could not load logos",
                            systemImage: "exclamationmark.triangle",
                            description: Text(error)
                        )
                        .foregroundStyle(.white)
                        .padding()
                    } else if viewModel.logos.isEmpty {
                        ContentUnavailableView("No logos found", systemImage: "textformat")
                            .foregroundStyle(.white)
                            .padding()
                    } else {
                        logoList
                            .allowsHitTesting(!viewModel.isSaving)
                    }
                }
                .spineContentTransition(value: contentPhase)
            }
            .navigationTitle("Customize Logo")
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
            hasContent: !viewModel.logos.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var logoList: some View {
        VStack(spacing: 14) {
            if showsLanguageFilter {
                Picker("Language", selection: $viewModel.selectedLanguage) {
                    ForEach(viewModel.languageOptions) { option in
                        Text(option.title).tag(option.id)
                    }
                }
                .pickerStyle(.menu)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red.opacity(0.9))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
            }

            ScrollView {
                LazyVGrid(
                    columns: [
                        GridItem(.flexible(), spacing: 12),
                        GridItem(.flexible()),
                    ],
                    spacing: 12
                ) {
                    ForEach(viewModel.filteredLogos) { logo in
                        LogoOptionRow(
                            logo: logo,
                            isSelected: viewModel.selectedLogoURL == logo.url
                        ) {
                            viewModel.selectedLogoURL = logo.url
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

private struct LogoOptionRow: View {
    let logo: LogoOption
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                HStack(spacing: 0) {
                    Color.white.opacity(0.92)
                    Color.black.opacity(0.78)
                }

                LogoRemoteImage(thumbnailURL: logo.thumbnailUrl, fullURL: logo.url)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 18)

                if logo.isSelected {
                    Text("Current")
                        .font(.caption2.weight(.heavy))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                        .background(.green, in: Capsule())
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                        .padding(7)
                }

                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title2)
                        .foregroundStyle(.white)
                        .shadow(radius: 4)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                        .padding(9)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 88)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .stroke(isSelected ? .white : .white.opacity(0.12), lineWidth: isSelected ? 3 : 1)
            }
        }
        .buttonStyle(.plain)
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        var components = [logo.isSelected ? "Current logo" : "Logo option"]
        if let language = logo.language {
            components.append(PosterPickerViewModel.languageName(for: language))
        } else {
            components.append("No language")
        }
        if let style = logo.style, !style.isEmpty {
            components.append(style.capitalized)
        }
        return components.joined(separator: ", ")
    }
}

private struct LogoRemoteImage: View {
    let thumbnailURL: String?
    let fullURL: String

    var body: some View {
        if let thumbnailURL, thumbnailURL != fullURL {
            SpineAsyncImage(url: URL(string: thumbnailURL)) { phase in
                switch phase {
                case .success(let image):
                    logoImage(image)
                case .failure:
                    fullImage
                default:
                    progress
                }
            }
        } else {
            fullImage
        }
    }

    private var fullImage: some View {
        SpineAsyncImage(url: URL(string: fullURL)) { phase in
            switch phase {
            case .success(let image):
                logoImage(image)
            case .failure:
                Image(systemName: "photo")
                    .font(.title2)
                    .foregroundStyle(.gray)
            default:
                progress
            }
        }
    }

    private var progress: some View {
        ProgressView()
            .tint(.gray)
    }

    private func logoImage(_ image: Image) -> some View {
        image
            .resizable()
            .scaledToFit()
    }
}

#Preview("Logo Picker") {
    LogoPickerView(
        ref: MediaRef(
            itemId: nil,
            source: "tmdb",
            mediaType: "movie",
            mediaId: "550",
            seasonNumber: nil,
            episodeNumber: nil
        ),
        mediaRepository: PreviewLogoRepository(),
        onUnauthorized: {}
    ) { _ in }
}

private struct PreviewLogoRepository: MediaRepository {
    func meta() async throws -> MetaResponse { fatalError("Not used") }
    func search(query: String, mediaType: String) async throws -> [MediaSummary] { fatalError("Not used") }
    func detail(ref: MediaRef) async throws -> MediaDetail { fatalError("Not used") }
    func reviews(ref: MediaRef) async throws -> [MediaReview] { fatalError("Not used") }
    func posters(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func savePoster(ref: MediaRef, posterURL: String) async throws -> PosterSaveResponse { fatalError("Not used") }
    func backdrops(ref: MediaRef) async throws -> [PosterOption] { fatalError("Not used") }
    func saveBackdrop(ref: MediaRef, backdropURL: String) async throws -> BackdropSaveResponse { fatalError("Not used") }
    func homePicture() async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func setHomePicture(ref _: MediaRef, backdropURL _: String) async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }
    func clearHomePicture() async throws -> HomePictureResponse { HomePictureResponse(picture: nil, canChange: false) }

    func logos(ref: MediaRef) async throws -> [LogoOption] {
        [
            LogoOption(
                url: "https://image.tmdb.org/t/p/w500/wwemzKWzjKYJFfCeiB57q3r4Bcm.png",
                thumbnailUrl: "https://image.tmdb.org/t/p/w300/wwemzKWzjKYJFfCeiB57q3r4Bcm.png",
                width: 500,
                height: 190,
                aspectRatio: 2.63,
                voteAverage: 5.4,
                voteCount: 12,
                language: "en",
                style: nil,
                isOriginal: true,
                isSelected: true
            ),
            LogoOption(
                url: "https://image.tmdb.org/t/p/w500/8Vt6mWEReuy4Of61Lnj5Xj704m8.png",
                thumbnailUrl: "https://image.tmdb.org/t/p/w300/8Vt6mWEReuy4Of61Lnj5Xj704m8.png",
                width: 500,
                height: 134,
                aspectRatio: 3.73,
                voteAverage: 5.2,
                voteCount: 8,
                language: nil,
                style: "white",
                isOriginal: false,
                isSelected: false
            ),
        ]
    }

    func saveLogo(ref: MediaRef, logoURL: String) async throws -> LogoSaveResponse {
        LogoSaveResponse(
            logoUrl: logoURL,
            customLogoUrl: logoURL,
            logoWidth: 500,
            logoHeight: 190,
            logoAspectRatio: 2.63
        )
    }
}
