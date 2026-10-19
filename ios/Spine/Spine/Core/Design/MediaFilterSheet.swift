import SwiftUI

struct MediaFilterSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var filter: MediaFilterState
    let scope: MediaFilterScope
    let options: MediaFilterOptionsResponse
    let mediaTypes: [String]
    let showsTagFilter: Bool
    let onApply: () -> Void
    private let streamingRepository: StreamingRepository

    @State private var draft: MediaFilterState
    @State private var isGenreExpanded = false
    @State private var isLanguageExpanded = false

    init(
        filter: Binding<MediaFilterState>,
        scope: MediaFilterScope,
        options: MediaFilterOptionsResponse,
        mediaTypes: [String],
        showsTagFilter: Bool = true,
        streamingRepository: StreamingRepository = AppRepositories.current().streaming,
        onApply: @escaping () -> Void
    ) {
        _filter = filter
        self.scope = scope
        self.options = options
        self.mediaTypes = mediaTypes
        self.showsTagFilter = showsTagFilter
        self.onApply = onApply
        self.streamingRepository = streamingRepository
        _draft = State(initialValue: filter.wrappedValue)
    }

    var body: some View {
        NavigationStack {
            Form {
                sortSection
                contextSection
                serviceSection
                yearSection
                releaseSection
                choiceSection("Genre", choices: options.genres, include: \.genres, exclude: \.excludedGenres)
                if showsPlatformFilter {
                    choiceSection(
                        "Platform",
                        choices: options.platforms,
                        include: \.platforms,
                        exclude: \.excludedPlatforms
                    )
                }
                if showsLanguageFilter {
                    choiceSection("Language", choices: options.languages, include: \.languages, exclude: \.excludedLanguages)
                }
                ratingSection
                diarySection
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Apply") {
                        filter = draft
                        onApply()
                        dismiss()
                    }
                    .fontWeight(.semibold)
                }
                ToolbarItem(placement: .bottomBar) {
                    Button("Reset", role: .destructive) {
                        draft = resetFilter
                        if case .company = scope {
                            filter = draft
                            onApply()
                            dismiss()
                        }
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.large])
    }

    private var sortSection: some View {
        Section("Sort") {
            Picker("Direction", selection: directionBinding) {
                ForEach(MediaFilterDirection.allCases) { direction in
                    Text(direction.label).tag(direction)
                }
            }
            .pickerStyle(.segmented)
            .disabled(draft.sort == nil)

            Picker("Sort by", selection: sortBinding) {
                Text("Default").tag(String?.none)
                ForEach(sortChoices) { choice in
                    Text(choice.label).tag(Optional(choice.value))
                }
            }
        }
    }

    @ViewBuilder
    private var contextSection: some View {
        if showsMediaType || showsStatus {
            Section("Context") {
                if showsMediaType {
                    Picker("Media Type", selection: mediaTypeBinding) {
                        Text("All").tag(String?.none)
                        ForEach(mediaTypes, id: \.self) { type in
                            Text(MediaTypeTheme.theme(for: type).displayName).tag(Optional(type))
                        }
                    }
                }

                if showsStatus {
                    Picker("Status", selection: statusBinding) {
                        Text("All").tag(String?.none)
                        ForEach(APIConstants.statusChoices, id: \.self) { status in
                            Text(status).tag(Optional(status))
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var serviceSection: some View {
        if let streaming = options.streaming,
           StreamingFilterRules.showsServiceSection(scope: scope, options: options, mediaType: draft.mediaType) {
            Section {
                serviceRow(isSelected: draft.service == .any) {
                    Text("Any")
                } action: {
                    draft.service = .any
                }

                serviceRow(isSelected: draft.service == .mine) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("My services")
                        if streaming.services.isEmpty {
                            Text("Choose your streaming services first.")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                } action: {
                    draft.service = .mine
                }
                .disabled(streaming.services.isEmpty)

                ForEach(streaming.services) { service in
                    serviceRow(isSelected: draft.service.includes(providerId: service.providerId)) {
                        HStack(spacing: 12) {
                            StreamingFilterServiceLogo(urlString: service.logoURL)
                            Text(service.label)
                        }
                    } action: {
                        draft.service = draft.service.togglingProvider(service.providerId)
                    }
                }

                NavigationLink {
                    StreamingServicesView(repository: streamingRepository, onUnauthorized: {})
                } label: {
                    Text(streaming.services.isEmpty ? "Choose your streaming services" : "Edit your services")
                }
            } header: {
                Text("Service")
            } footer: {
                Text(attribution(for: streaming))
            }
        }
    }

    private func attribution(for streaming: StreamingFilterOptions) -> AttributedString {
        var text = AttributedString(streaming.attributionText)
        if let range = text.range(of: "JustWatch", options: .backwards) {
            text[range].link = URL(string: "https://www.justwatch.com")
        }
        return text
    }

    private func serviceRow<RowLabel: View>(
        isSelected: Bool,
        @ViewBuilder label: () -> RowLabel,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack {
                label()
                Spacer()
                if isSelected {
                    Image(systemName: "checkmark")
                        .font(.body.weight(.semibold))
                }
            }
        }
        .foregroundStyle(.primary)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var yearSection: some View {
        Section("Year") {
            Picker("Exact Year", selection: yearBinding) {
                Text("Any").tag(Int?.none)
                ForEach(options.years, id: \.self) { year in
                    Text(String(year)).tag(Optional(year))
                }
            }
            .disabled(options.years.isEmpty)
        }
    }

    private var releaseSection: some View {
        Section("Release") {
            Picker("Status", selection: releaseStatusBinding) {
                Text("Any").tag(String?.none)
                Text("Released").tag(Optional("released"))
                Text("Unreleased").tag(Optional("unreleased"))
            }

            if showsLengthFilter {
                Picker("Length", selection: lengthBinding) {
                    Text("Any").tag(String?.none)
                    Text("Feature Length").tag(Optional("feature"))
                    Text("Short Film").tag(Optional("short"))
                }
            }
        }
    }

    private func choiceSection(
        _ title: String,
        choices: [FilterChoice],
        include includeKeyPath: WritableKeyPath<MediaFilterState, [String]>,
        exclude excludeKeyPath: WritableKeyPath<MediaFilterState, [String]>
    ) -> some View {
        Section(title) {
            DisclosureGroup(isExpanded: expandedBinding(for: title)) {
                if choices.isEmpty {
                    Text("No options")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(choices) { choice in
                        Button {
                            draft.cycleFacet(choice.value, include: includeKeyPath, exclude: excludeKeyPath)
                        } label: {
                            HStack {
                                Text(choice.label)
                                Spacer()
                                if draft[keyPath: includeKeyPath].contains(choice.value) {
                                    Image(systemName: "checkmark")
                                        .font(.body.weight(.semibold))
                                } else if draft[keyPath: excludeKeyPath].contains(choice.value) {
                                    Image(systemName: "nosign")
                                        .font(.body.weight(.semibold))
                                }
                            }
                        }
                        .foregroundStyle(.primary)
                    }
                }
            } label: {
                Text(compactTitle(
                    title,
                    includeCount: draft[keyPath: includeKeyPath].count,
                    excludeCount: draft[keyPath: excludeKeyPath].count
                ))
            }
        }
    }

    private var ratingSection: some View {
        Section(ratingSectionTitle) {
            TextField(ratingMinimumLabel, text: decimalText(\.ratingMin))
                .keyboardType(.decimalPad)
            TextField(ratingMaximumLabel, text: decimalText(\.ratingMax))
                .keyboardType(.decimalPad)
        }
    }

    @ViewBuilder
    private var diarySection: some View {
        if showsDiaryFilters {
            Section("Diary") {
                if showsTagFilter {
                    TextField("Tag", text: tagBinding)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Toggle("Has Review", isOn: $draft.hasReview)
                Toggle("Liked", isOn: $draft.liked)
                OptionalDatePicker("Watched From", date: $draft.watchedFrom)
                OptionalDatePicker("Watched To", date: $draft.watchedTo)
            }
        }
    }

    private var sortChoices: [FilterChoice] {
        Self.availableSortChoices(options: options, scope: scope)
    }

    static func availableSortChoices(
        options: MediaFilterOptionsResponse,
        scope: MediaFilterScope
    ) -> [FilterChoice] {
        let choices = options.sorts.isEmpty ? fallbackSortChoices(for: scope) : options.sorts
        return choices.filter { choice in
            switch scope {
            case .tracking:
                return choice.value != MediaFilterSort.popularity.rawValue
                    && choice.value != MediaFilterSort.consumedAt.rawValue
                    && choice.value != MediaFilterSort.dateAdded.rawValue
            case .diary:
                return choice.value != MediaFilterSort.popularity.rawValue
                    && choice.value != MediaFilterSort.dateAdded.rawValue
            case .list:
                return choice.value != MediaFilterSort.popularity.rawValue
                    && choice.value != MediaFilterSort.consumedAt.rawValue
            case .person:
                return true
            case .company:
                return [
                    MediaFilterSort.popularity.rawValue,
                    MediaFilterSort.releaseDate.rawValue,
                    MediaFilterSort.averageRating.rawValue,
                    MediaFilterSort.title.rawValue,
                ].contains(choice.value)
            }
        }
    }

    private static func fallbackSortChoices(for scope: MediaFilterScope) -> [FilterChoice] {
        let sorts: [MediaFilterSort] = switch scope {
        case .tracking:
            [.title, .releaseDate, .yourRating, .averageRating]
        case .diary:
            [.title, .releaseDate, .yourRating, .averageRating, .consumedAt]
        case .list:
            [.title, .releaseDate, .yourRating, .averageRating, .dateAdded]
        case .person:
            [.title, .releaseDate, .averageRating]
        case .company:
            [.popularity, .releaseDate, .averageRating, .title]
        }
        return sorts.map { FilterChoice(value: $0.rawValue, label: $0.label) }
    }

    private var resetFilter: MediaFilterState {
        switch scope {
        case .tracking:
            return MediaFilterState()
        case .diary:
            return MediaFilterState()
        case .list:
            return MediaFilterState()
        case .person:
            return MediaFilterState()
        case .company:
            return MediaFilterState()
        }
    }

    private var showsMediaType: Bool {
        switch scope {
        case .tracking:
            false
        case .diary, .list, .person:
            true
        case .company:
            false
        }
    }

    private var showsStatus: Bool {
        switch scope {
        case .tracking, .list:
            true
        case .diary, .person, .company:
            false
        }
    }

    private var showsDiaryFilters: Bool {
        if case .diary = scope {
            return true
        }
        return false
    }

    private var showsPlatformFilter: Bool {
        Self.showsPlatformFilter(for: scope)
    }

    private var showsLanguageFilter: Bool {
        if case .company = scope {
            return false
        }
        return true
    }

    private var showsLengthFilter: Bool {
        if case .company = scope {
            return false
        }
        return true
    }

    private var ratingMinimumLabel: String {
        Self.ratingLabels(for: scope).minimum
    }

    private var ratingMaximumLabel: String {
        Self.ratingLabels(for: scope).maximum
    }

    static func showsPlatformFilter(for scope: MediaFilterScope) -> Bool {
        guard case let .company(ref) = scope else { return false }
        return !ref.isAnimeStudio
    }

    static func ratingLabels(for scope: MediaFilterScope) -> (minimum: String, maximum: String) {
        switch scope {
        case .person:
            return ("Minimum Average Rating", "Maximum Average Rating")
        case let .company(ref):
            if ref.isAnimeStudio {
                return ("Minimum MAL rating (0–10)", "Maximum MAL rating (0–10)")
            }
            return ("Minimum IGDB rating (0–100)", "Maximum IGDB rating (0–100)")
        case .tracking, .diary, .list:
            return ("Minimum", "Maximum")
        }
    }

    private var ratingSectionTitle: String {
        switch scope {
        case .tracking, .diary, .list:
            "Your Rating"
        case .person, .company:
            "Rating"
        }
    }

    private var sortBinding: Binding<String?> {
        Binding(
            get: { draft.sort?.rawValue },
            set: { draft.selectSort(rawValue: $0) }
        )
    }

    private var directionBinding: Binding<MediaFilterDirection> {
        Binding(
            get: { draft.direction ?? .desc },
            set: { draft.direction = $0 }
        )
    }

    private var mediaTypeBinding: Binding<String?> {
        Binding(get: { draft.mediaType }, set: { draft.selectMediaType($0) })
    }

    private var statusBinding: Binding<String?> {
        Binding(get: { draft.status }, set: { draft.status = $0 })
    }

    private var yearBinding: Binding<Int?> {
        Binding(get: { draft.year }, set: { draft.year = $0 })
    }

    private var releaseStatusBinding: Binding<String?> {
        Binding(get: { draft.releaseStatus }, set: { draft.releaseStatus = $0 })
    }

    private var lengthBinding: Binding<String?> {
        Binding(get: { draft.length }, set: { draft.length = $0 })
    }

    private var tagBinding: Binding<String> {
        Binding(
            get: { draft.tag ?? "" },
            set: { draft.tag = $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
        )
    }

    private func decimalText(_ keyPath: WritableKeyPath<MediaFilterState, Decimal?>) -> Binding<String> {
        Binding(
            get: {
                draft[keyPath: keyPath].map { NSDecimalNumber(decimal: $0).stringValue } ?? ""
            },
            set: { value in
                let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
                draft[keyPath: keyPath] = trimmed.isEmpty ? nil : Decimal(string: trimmed)
            }
        )
    }

    private func expandedBinding(for title: String) -> Binding<Bool> {
        title == "Genre" ? $isGenreExpanded : $isLanguageExpanded
    }

    private func compactTitle(_ title: String, includeCount: Int, excludeCount: Int) -> String {
        let count = includeCount + excludeCount
        guard count > 0 else { return title }
        return excludeCount > 0 ? "\(title) (-\(excludeCount))" : "\(title) (\(count))"
    }
}

private struct OptionalDatePicker: View {
    let title: String
    @Binding var date: Date?

    init(_ title: String, date: Binding<Date?>) {
        self.title = title
        _date = date
    }

    var body: some View {
        Toggle(isOn: isEnabled) {
            Text(title)
        }
        if date != nil {
            DatePicker(title, selection: selectedDate, displayedComponents: .date)
                .labelsHidden()
        }
    }

    private var isEnabled: Binding<Bool> {
        Binding(
            get: { date != nil },
            set: { enabled in
                date = enabled ? Date() : nil
            }
        )
    }

    private var selectedDate: Binding<Date> {
        Binding(
            get: { date ?? Date() },
            set: { date = $0 }
        )
    }
}
