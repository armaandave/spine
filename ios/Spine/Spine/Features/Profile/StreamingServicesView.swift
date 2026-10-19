import SwiftUI

/// Streaming Services settings: the user's streaming country and the services they use (ST-100 to ST-103).
@MainActor
@Observable
final class StreamingServicesViewModel {
    static let maxServices = 100
    /// The server's default country (ST-100), for a user who turned availability off and has no device region.
    static let defaultRegion = "US"

    private(set) var saved: StreamingPreferences?
    private(set) var regions: [StreamingRegion] = []
    /// The selected country's catalog, popular first.
    private(set) var catalog: [StreamingProvider] = []
    private(set) var selectedRegion: String?
    private(set) var selectedProviderIds: [Int] = []
    private(set) var isLoading = false
    private(set) var isLoadingRegions = false
    private(set) var isLoadingCatalog = false
    private(set) var isSaving = false
    /// The preferences could not be loaded, so there is nothing to edit.
    private(set) var loadErrorMessage: String?
    private(set) var regionsErrorMessage: String?
    private(set) var catalogErrorMessage: String?
    /// A failed save, or a selection over the limit.
    var errorMessage: String?

    /// The country `catalog` belongs to.
    private var catalogRegion: String?
    /// Every service seen so far, so selections missing from the current catalog can still be shown.
    private var knownProviders: [Int: StreamingProvider] = [:]
    private var catalogGeneration = 0

    private let repository: StreamingRepository
    private let onUnauthorized: () -> Void
    private let deviceRegion: String?

    init(
        repository: StreamingRepository,
        onUnauthorized: @escaping () -> Void,
        deviceRegion: String? = APIClient.regionHeaderValue()
    ) {
        self.repository = repository
        self.onUnauthorized = onUnauthorized
        self.deviceRegion = deviceRegion
    }

    /// Loads what the screen still lacks. Leaving the screen (opening the country list) cancels its loads, so
    /// coming back finishes them.
    func loadIfNeeded() async {
        if saved == nil {
            guard !isLoading else { return }
            await load()
        } else if !isCatalogLoaded, !isLoadingCatalog {
            await loadCatalog()
        }
    }

    func load() async {
        isLoading = true
        loadErrorMessage = nil
        let preferences: StreamingPreferences
        do {
            preferences = try await repository.preferences()
        } catch {
            isLoading = false
            guard !(error is CancellationError) else { return }
            loadErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return
        }
        apply(preferences)
        isLoading = false

        async let regionsLoad: Void = loadRegions()
        async let catalogLoad: Void = loadCatalog()
        _ = await (regionsLoad, catalogLoad)
    }

    func loadRegionsIfNeeded() async {
        guard regions.isEmpty else { return }
        await loadRegions()
    }

    func loadRegions() async {
        guard !isLoadingRegions else { return }
        isLoadingRegions = true
        regionsErrorMessage = nil
        defer { isLoadingRegions = false }

        do {
            regions = try await repository.regions()
        } catch is CancellationError {
            return
        } catch {
            regionsErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    /// Loads the selected country's catalog. A response for a country the user has since left is dropped.
    func loadCatalog() async {
        guard let region = selectedRegion else { return }
        catalogGeneration += 1
        let generation = catalogGeneration
        isLoadingCatalog = true
        catalogErrorMessage = nil

        do {
            let providers = try await repository.providers(region: region)
            guard generation == catalogGeneration else { return }
            catalog = providers
            catalogRegion = region
            remember(providers)
        } catch {
            guard generation == catalogGeneration else { return }
            if !(error is CancellationError) {
                catalogErrorMessage = error.localizedDescription
                handleUnauthorized(error)
            }
        }
        isLoadingCatalog = false
    }

    /// Changing country keeps the selected services (ST-101).
    func selectRegion(_ code: String) async {
        guard code != selectedRegion else { return }
        selectedRegion = code
        catalog = []
        catalogRegion = nil
        errorMessage = nil
        await loadCatalog()
    }

    func toggle(_ provider: StreamingProvider) {
        errorMessage = nil
        remember([provider])
        if let index = selectedProviderIds.firstIndex(of: provider.id) {
            selectedProviderIds.remove(at: index)
        } else if selectedProviderIds.count >= Self.maxServices {
            errorMessage = "You can choose up to \(Self.maxServices) services."
        } else {
            selectedProviderIds.append(provider.id)
        }
    }

    func isSelected(_ provider: StreamingProvider) -> Bool {
        selectedProviderIds.contains(provider.id)
    }

    /// Whether `catalog` is the selected country's.
    var isCatalogLoaded: Bool {
        catalogRegion != nil && catalogRegion == selectedRegion
    }

    var hasChanges: Bool {
        guard let saved else { return false }
        let savedRegion = saved.enabled ? saved.region : nil
        return selectedRegion != savedRegion || Set(selectedProviderIds) != Set(saved.providerIds)
    }

    var canSave: Bool {
        hasChanges && selectedRegion != nil
    }

    /// Saves the country and services. On success the screens that show availability are told to reload.
    @discardableResult
    func save() async -> Bool {
        guard canSave, !isSaving, let region = selectedRegion else { return false }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }

        do {
            let updated = try await repository.savePreferences(
                StreamingPreferencesUpdateRequest(region: region, providerIds: selectedProviderIds)
            )
            apply(updated)
            NotificationCenter.default.post(name: .streamingPreferencesDidChange, object: updated)
            return true
        } catch is CancellationError {
            return false
        } catch {
            let fieldErrors = APIValidationMessages.fieldErrors(from: error)
            errorMessage = fieldErrors.isEmpty
                ? error.localizedDescription
                : fieldErrors.keys.sorted().compactMap { fieldErrors[$0] }.joined(separator: "\n")
            handleUnauthorized(error)
            return false
        }
    }

    /// The selected services: those in the country's catalog first, popular first, then any it doesn't list.
    var selectedProviders: [StreamingProvider] {
        let selected = Set(selectedProviderIds)
        let inCatalog = catalog.filter { selected.contains($0.id) }
        let inCatalogIDs = Set(inCatalog.map(\.id))
        return inCatalog + selectedProviderIds.filter { !inCatalogIDs.contains($0) }.map(provider(for:))
    }

    /// Selected services the country's catalog doesn't list. They stay selected so they can be removed (ST-101).
    /// Empty until the catalog for the selected country has loaded.
    var selectedProvidersMissingFromCatalog: [StreamingProvider] {
        guard isCatalogLoaded else { return [] }
        let catalogIDs = Set(catalog.map(\.id))
        return selectedProviderIds.filter { !catalogIDs.contains($0) }.map(provider(for:))
    }

    /// The "All services" list: selected services the country doesn't list first, then its catalog.
    func services(matching query: String) -> [StreamingProvider] {
        let all = selectedProvidersMissingFromCatalog + catalog
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return all }
        return all.filter { $0.name.localizedStandardContains(trimmed) }
    }

    func regions(matching query: String) -> [StreamingRegion] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return regions }
        return regions.filter {
            $0.name.localizedStandardContains(trimmed) || $0.code.caseInsensitiveCompare(trimmed) == .orderedSame
        }
    }

    var selectedRegionName: String {
        selectedRegion.map(regionName(for:)) ?? "Not set"
    }

    func regionName(for code: String) -> String {
        if let region = regions.first(where: { $0.code == code }) {
            return region.name
        }
        if let saved, saved.region == code, let name = saved.regionName, !name.isEmpty {
            return name
        }
        return Locale.current.localizedString(forRegionCode: code) ?? code
    }

    private func apply(_ preferences: StreamingPreferences) {
        saved = preferences
        remember(preferences.providers)
        selectedProviderIds = preferences.providerIds
        let savedRegion = preferences.enabled ? preferences.region : nil
        selectedRegion = savedRegion ?? deviceRegion ?? Self.defaultRegion
    }

    private func provider(for id: Int) -> StreamingProvider {
        knownProviders[id] ?? StreamingProvider(providerId: id, name: "Service \(id)", logoUrl: nil, displayPriority: nil)
    }

    private func remember(_ providers: [StreamingProvider]) {
        for provider in providers {
            knownProviders[provider.id] = provider
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

/// The Streaming Services row in Settings, summarizing the user's services ("Netflix, Hulu +2").
struct StreamingServicesSettingsLink: View {
    let repository: StreamingRepository
    let onUnauthorized: () -> Void
    @State private var summary: String?

    var body: some View {
        NavigationLink {
            StreamingServicesView(repository: repository, onUnauthorized: onUnauthorized)
        } label: {
            SettingsNavigationRow(
                title: "Streaming Services",
                detail: summary ?? "Your country and services",
                systemName: "play.tv.fill"
            )
        }
        .buttonStyle(.plain)
        .task {
            guard summary == nil else { return }
            // Only a hint on the row: if this fails, the row keeps its generic detail.
            if let preferences = try? await repository.preferences() {
                summary = preferences.servicesSummary
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .streamingPreferencesDidChange)) { notification in
            if let preferences = notification.object as? StreamingPreferences {
                summary = preferences.servicesSummary
            }
        }
    }
}

/// Streaming Services on its own, as a sheet from a detail page's Where to Watch.
struct StreamingServicesSheet: View {
    @Environment(\.dismiss) private var dismiss

    let repository: StreamingRepository
    let onUnauthorized: () -> Void

    var body: some View {
        NavigationStack {
            StreamingServicesView(repository: repository, onUnauthorized: onUnauthorized)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") {
                            dismiss()
                        }
                    }
                }
        }
        .preferredColorScheme(.dark)
        .tint(.white)
    }
}

struct StreamingServicesView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: StreamingServicesViewModel
    @State private var serviceQuery = ""

    init(repository: StreamingRepository, onUnauthorized: @escaping () -> Void) {
        _viewModel = State(initialValue: StreamingServicesViewModel(
            repository: repository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 24) {
                    content
                }
                .padding(.horizontal, 18)
                .padding(.top, 18)
                .padding(.bottom, 36)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .safeAreaInset(edge: .bottom) {
            if viewModel.saved != nil {
                saveBar
            }
        }
        .navigationTitle("Streaming Services")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadIfNeeded()
        }
    }

    @ViewBuilder
    private var content: some View {
        if let saved = viewModel.saved {
            Text("Pick the services you use to see what you can stream on them. Your services are private.")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.52))
                .fixedSize(horizontal: false, vertical: true)

            countrySection(saved)
            yourServicesSection
            allServicesSection

            JustWatchCredit()
                .frame(maxWidth: .infinity)
        } else if let message = viewModel.loadErrorMessage, !viewModel.isLoading {
            StreamingServicesStateCard(
                systemImage: "exclamationmark.triangle.fill",
                message: message,
                retryTitle: "Try Again"
            ) {
                Task { await viewModel.load() }
            }
        } else {
            ProgressView("Loading your services")
                .tint(.white)
                .foregroundStyle(.white.opacity(0.62))
                .frame(maxWidth: .infinity, minHeight: 240)
        }
    }

    private func countrySection(_ saved: StreamingPreferences) -> some View {
        SettingsGroup(title: "Country") {
            SettingsCard {
                NavigationLink {
                    StreamingCountryPickerView(viewModel: viewModel)
                } label: {
                    SettingsNavigationRow(
                        title: viewModel.selectedRegionName,
                        detail: "Where to Watch shows offers in this country",
                        systemName: "globe"
                    )
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Country, \(viewModel.selectedRegionName)")
            }

            if !saved.enabled {
                Text("Where to Watch is turned off for your account. Saving turns it on for this country.")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.46))
                    .padding(.horizontal, 3)
            }
        }
    }

    private var yourServicesSection: some View {
        SettingsGroup(title: "Your services") {
            SettingsCard {
                let selected = viewModel.selectedProviders
                if selected.isEmpty {
                    Text("Choose the services you use from the list below.")
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.52))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(16)
                } else {
                    LazyVGrid(
                        columns: [GridItem(.adaptive(minimum: 72), spacing: 10, alignment: .top)],
                        alignment: .leading,
                        spacing: 16
                    ) {
                        ForEach(selected) { provider in
                            selectedServiceTile(provider)
                        }
                    }
                    .padding(16)
                }
            }
        }
    }

    private func selectedServiceTile(_ provider: StreamingProvider) -> some View {
        Button {
            viewModel.toggle(provider)
        } label: {
            VStack(spacing: 7) {
                StreamingProviderLogo(url: provider.logoUrl, name: provider.name, size: 52)
                    .overlay(alignment: .topTrailing) {
                        Image(systemName: "xmark.circle.fill")
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, Color(white: 0.32))
                            .font(.system(size: 18))
                            .offset(x: 7, y: -7)
                    }

                Text(provider.name)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.78))
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Remove \(provider.name)")
    }

    private var allServicesSection: some View {
        SettingsGroup(title: "All services") {
            SettingsCard {
                serviceSearchField
                    .padding(12)

                if let message = viewModel.catalogErrorMessage, !viewModel.isCatalogLoaded {
                    StreamingServicesStateCard(
                        systemImage: "exclamationmark.triangle.fill",
                        message: message,
                        retryTitle: "Try Again"
                    ) {
                        Task { await viewModel.loadCatalog() }
                    }
                    .padding([.horizontal, .bottom], 12)
                } else if !viewModel.isCatalogLoaded {
                    ProgressView("Loading services")
                        .tint(.white)
                        .foregroundStyle(.white.opacity(0.62))
                        .frame(maxWidth: .infinity, minHeight: 120)
                } else {
                    serviceRows
                }
            }
        }
    }

    @ViewBuilder
    private var serviceRows: some View {
        let services = viewModel.services(matching: serviceQuery)
        if services.isEmpty {
            Text(serviceQuery.trimmingCharacters(in: .whitespaces).isEmpty
                ? "No services are listed for \(viewModel.selectedRegionName)."
                : "No services match “\(serviceQuery)”.")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.52))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding([.horizontal, .bottom], 16)
        } else {
            let missingIDs = Set(viewModel.selectedProvidersMissingFromCatalog.map(\.id))
            LazyVStack(spacing: 0) {
                ForEach(services) { provider in
                    serviceRow(provider, isMissing: missingIDs.contains(provider.id))
                    if provider.id != services.last?.id {
                        SettingsDivider()
                            .padding(.leading, 12)
                    }
                }
            }
            .padding(.bottom, 6)
        }
    }

    private func serviceRow(_ provider: StreamingProvider, isMissing: Bool) -> some View {
        let isSelected = viewModel.isSelected(provider)
        let missingNote = "Not listed in \(viewModel.selectedRegionName)"

        return Button {
            viewModel.toggle(provider)
        } label: {
            HStack(spacing: 12) {
                StreamingProviderLogo(url: provider.logoUrl, name: provider.name, size: 36)

                VStack(alignment: .leading, spacing: 2) {
                    Text(provider.name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                    if isMissing {
                        Text(missingNote)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.46))
                    }
                }

                Spacer(minLength: 8)

                if isSelected {
                    Image(systemName: "checkmark.circle.fill")
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.black, .white)
                        .font(.title3)
                } else {
                    Image(systemName: "circle")
                        .font(.title3)
                        .foregroundStyle(.white.opacity(0.28))
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isMissing ? "\(provider.name), \(missingNote)" : provider.name)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var serviceSearchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.white.opacity(0.42))
                .accessibilityHidden(true)
            TextField("Search services", text: $serviceQuery)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .foregroundStyle(.white)
            if !serviceQuery.isEmpty {
                Button {
                    serviceQuery = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.white.opacity(0.42))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(.black.opacity(0.22), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(.white.opacity(0.09), lineWidth: 1)
        }
    }

    private var saveBar: some View {
        VStack(spacing: 10) {
            if let message = viewModel.errorMessage {
                SettingsMessageCard(message: message, systemName: "exclamationmark.triangle.fill", tint: .red)
            }

            SettingsSaveButton(
                title: "Save Services",
                isSaving: viewModel.isSaving,
                isDisabled: !viewModel.canSave
            ) {
                if await viewModel.save() {
                    dismiss()
                }
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background {
            // Solid behind the button (it is translucent while disabled), fading out above it.
            SpinePalette.pageBackground
                .overlay(alignment: .top) {
                    LinearGradient(
                        colors: [SpinePalette.pageBackground.opacity(0), SpinePalette.pageBackground],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .frame(height: 24)
                    .offset(y: -24)
                }
                .ignoresSafeArea()
        }
    }
}

/// The country list: searchable, with a checkmark on the selected country.
private struct StreamingCountryPickerView: View {
    @Environment(\.dismiss) private var dismiss
    let viewModel: StreamingServicesViewModel
    @State private var query = ""

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollViewReader { proxy in
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 16) {
                        content
                    }
                    .padding(.horizontal, 18)
                    .padding(.top, 12)
                    .padding(.bottom, 36)
                }
                .scrollDismissesKeyboard(.interactively)
                .task(id: viewModel.regions.count) {
                    guard query.isEmpty, !viewModel.regions.isEmpty, let code = viewModel.selectedRegion else { return }
                    proxy.scrollTo(code, anchor: .center)
                }
            }
        }
        .navigationTitle("Country")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, prompt: "Search countries")
        .task {
            await viewModel.loadRegionsIfNeeded()
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.regions.isEmpty {
            if let message = viewModel.regionsErrorMessage, !viewModel.isLoadingRegions {
                StreamingServicesStateCard(
                    systemImage: "exclamationmark.triangle.fill",
                    message: message,
                    retryTitle: "Try Again"
                ) {
                    Task { await viewModel.loadRegions() }
                }
            } else {
                ProgressView("Loading countries")
                    .tint(.white)
                    .foregroundStyle(.white.opacity(0.62))
                    .frame(maxWidth: .infinity, minHeight: 240)
            }
        } else {
            let regions = viewModel.regions(matching: query)
            if regions.isEmpty {
                Text("No countries match “\(query)”.")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.52))
                    .frame(maxWidth: .infinity, minHeight: 120)
            } else {
                SettingsCard {
                    LazyVStack(spacing: 0) {
                        ForEach(regions) { region in
                            row(region)
                            if region.id != regions.last?.id {
                                Rectangle()
                                    .fill(.white.opacity(0.075))
                                    .frame(height: 1)
                                    .padding(.leading, 16)
                            }
                        }
                    }
                }
            }
        }
    }

    private func row(_ region: StreamingRegion) -> some View {
        let isSelected = region.code == viewModel.selectedRegion

        return Button {
            Task { await viewModel.selectRegion(region.code) }
            dismiss()
        } label: {
            HStack(spacing: 12) {
                Text(region.name)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.white)
                Spacer(minLength: 8)
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 13)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .id(region.code)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

/// An error with a retry button, inside the dark settings look.
private struct StreamingServicesStateCard: View {
    let systemImage: String
    let message: String
    let retryTitle: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: systemImage)
                .font(.system(size: 26, weight: .bold))
                .foregroundStyle(.white.opacity(0.78))
                .accessibilityHidden(true)
            Text(message)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(.white.opacity(0.62))
                .multilineTextAlignment(.center)
            Button(retryTitle, action: onRetry)
                .buttonStyle(.bordered)
                .tint(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
        .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}
