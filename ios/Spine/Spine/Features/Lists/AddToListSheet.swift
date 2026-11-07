import Observation
import SwiftUI

enum AddToListTarget: Hashable {
    case media(MediaRef)
    case person(PersonRef)

    var listType: CustomListType {
        switch self {
        case .media:
            .media
        case .person:
            .people
        }
    }

    var emptyStateTitle: String {
        switch self {
        case .media:
            "No Lists"
        case .person:
            "No People Lists"
        }
    }

    var emptyStateDescription: String {
        switch self {
        case .media:
            "Create a list above to add this item."
        case .person:
            "Create a people list above to add this person."
        }
    }

    var noun: String {
        switch self {
        case .media:
            "item"
        case .person:
            "person"
        }
    }

    func contains(in list: CustomListSummary) -> Bool {
        switch self {
        case .media:
            list.hasItem == true
        case .person:
            list.hasPerson == true
        }
    }

    func countLabel(for list: CustomListSummary) -> String {
        switch self {
        case .media:
            "\(list.itemsCount.formatted()) \(list.itemsCount == 1 ? "item" : "items")"
        case .person:
            "\(list.entriesCount.formatted()) \(list.entriesCount == 1 ? "person" : "people")"
        }
    }
}

@MainActor
@Observable
final class AddToListViewModel {
    private(set) var lists: [CustomListSummary] = []
    private(set) var target: AddToListTarget
    private(set) var isLoading = false
    private(set) var loadingListID: Int?
    var loadErrorMessage: String?
    var actionErrorMessage: String?

    private let listRepository: ListRepository
    private let onUnauthorized: () -> Void

    init(
        target: AddToListTarget,
        listRepository: ListRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.target = target
        self.listRepository = listRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        loadErrorMessage = nil
        defer { isLoading = false }

        do {
            lists = try await fetchLists()
        } catch is CancellationError {
            return
        } catch {
            loadErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func toggle(_ list: CustomListSummary) async {
        guard loadingListID == nil else { return }
        loadingListID = list.id
        actionErrorMessage = nil
        defer { loadingListID = nil }

        do {
            switch target {
            case let .media(ref):
                if target.contains(in: list) {
                    guard let itemID = ref.itemId else {
                        actionErrorMessage = "This item could not be identified. Close this sheet, refresh, and try again."
                        return
                    }
                    try await listRepository.removeItem(listId: list.id, itemId: itemID)
                } else {
                    let item = try await listRepository.addItem(listId: list.id, ref: ref)
                    target = .media(item.ref)
                }
            case let .person(ref):
                if target.contains(in: list) {
                    guard let entryID = list.personEntryId else {
                        try await refreshLists()
                        actionErrorMessage = "The list status was refreshed. Try removing this person again."
                        return
                    }
                    try await listRepository.removePerson(listId: list.id, entryId: entryID)
                } else {
                    let person = try await listRepository.addPerson(listId: list.id, ref: ref)
                    target = .person(person.ref)
                }
            }
        } catch is CancellationError {
            return
        } catch {
            actionErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return
        }

        CustomListChange.post(listId: list.id, listType: target.listType)
        do {
            try await refreshLists()
        } catch is CancellationError {
            return
        } catch {
            actionErrorMessage = "The list was updated, but its current status could not be refreshed. \(error.localizedDescription)"
            handleUnauthorized(error)
        }
    }

    func addPersonToCreatedList(_ listID: Int) async {
        guard loadingListID == nil, case let .person(ref) = target else { return }
        loadingListID = listID
        actionErrorMessage = nil
        defer { loadingListID = nil }

        do {
            let person = try await listRepository.addPerson(listId: listID, ref: ref)
            target = .person(person.ref)
        } catch is CancellationError {
            return
        } catch {
            actionErrorMessage = "The list was created, but this person could not be added. \(error.localizedDescription)"
            handleUnauthorized(error)
            try? await refreshLists()
            return
        }

        CustomListChange.post(listId: listID, listType: .people)
        do {
            try await refreshLists()
        } catch is CancellationError {
            return
        } catch {
            actionErrorMessage = "The list was created and updated, but its current status could not be refreshed. \(error.localizedDescription)"
            handleUnauthorized(error)
        }
    }

    func isMember(of list: CustomListSummary) -> Bool {
        target.contains(in: list)
    }

    func countLabel(for list: CustomListSummary) -> String {
        target.countLabel(for: list)
    }

    func useCanonicalMediaReference(_ ref: MediaRef) {
        guard case .media = target else { return }
        target = .media(ref)
    }

    private func fetchLists() async throws -> [CustomListSummary] {
        switch target {
        case let .media(ref):
            try await listRepository.list(membershipFor: ref)
        case let .person(ref):
            try await listRepository.peopleLists(membershipFor: ref)
        }
    }

    private func refreshLists() async throws {
        lists = try await fetchLists()
        loadErrorMessage = nil
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

struct AddToListSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AddToListViewModel
    @State private var searchText = ""
    @State private var presentedComposer: ListComposerMode?
    @State private var pendingCreatedListID: Int?

    private let initialMedia: MediaSummary?
    private let listRepository: ListRepository
    private let mediaRepository: MediaRepository
    private let peopleRepository: PeopleRepository
    private let onUnauthorized: () -> Void

    init(
        target: AddToListTarget,
        initialMedia: MediaSummary? = nil,
        listRepository: ListRepository,
        mediaRepository: MediaRepository,
        peopleRepository: PeopleRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.initialMedia = initialMedia
        self.listRepository = listRepository
        self.mediaRepository = mediaRepository
        self.peopleRepository = peopleRepository
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: AddToListViewModel(
            target: target,
            listRepository: listRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            List {
                createSection

                listsSection
                    .spineContentTransition(value: contentPhase)
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .navigationTitle("Add to List")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(
                text: $searchText,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search lists"
            )
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .task {
                if viewModel.lists.isEmpty {
                    await viewModel.load()
                }
            }
            .alert(
                "Couldn’t Update List",
                isPresented: Binding(
                    get: { viewModel.actionErrorMessage != nil },
                    set: { if !$0 { viewModel.actionErrorMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) {
                    viewModel.actionErrorMessage = nil
                }
            } message: {
                Text(viewModel.actionErrorMessage ?? "")
            }
            .accessibilityIdentifier("add-to-list.sheet")
        }
        .fullScreenCover(item: $presentedComposer, onDismiss: finishCreatingList) { mode in
            ListComposerView(
                mode: mode,
                initialItems: initialMedia.map { [$0] } ?? [],
                listRepository: listRepository,
                mediaRepository: mediaRepository,
                peopleRepository: peopleRepository,
                onUnauthorized: onUnauthorized
            ) { listID, draft in
                if let initialMedia,
                   let savedItem = draft.items.first(where: { $0.ref.id == initialMedia.ref.id }) {
                    viewModel.useCanonicalMediaReference(savedItem.ref)
                }
                pendingCreatedListID = listID
            }
        }
    }

    @ViewBuilder
    private var listsSection: some View {
        Section {
            if viewModel.isLoading, viewModel.lists.isEmpty {
                HStack {
                    Spacer()
                    ProgressView()
                        .accessibilityLabel("Loading lists")
                    Spacer()
                }
            } else if let error = viewModel.loadErrorMessage, viewModel.lists.isEmpty {
                VStack(spacing: 12) {
                    ContentUnavailableView(
                        "Could Not Load Lists",
                        systemImage: "exclamationmark.triangle",
                        description: Text(error)
                    )
                    Button("Retry") {
                        Task { await viewModel.load() }
                    }
                    .buttonStyle(.bordered)
                }
                .frame(maxWidth: .infinity)
                .listRowBackground(Color.clear)
            } else if filteredLists.isEmpty {
                ContentUnavailableView(
                    viewModel.target.emptyStateTitle,
                    systemImage: "list.bullet.rectangle",
                    description: Text(emptyStateDescription)
                )
                .listRowBackground(Color.clear)
            } else {
                ForEach(filteredLists) { list in
                    listRow(list)
                }
            }
        }
    }

    private var createSection: some View {
        Section {
            Button {
                presentedComposer = .create(viewModel.target.listType)
            } label: {
                Label("Create new list...", systemImage: "plus")
            }
            .disabled(viewModel.loadingListID != nil)
            .accessibilityIdentifier("add-to-list.create")
        }
    }

    private func listRow(_ list: CustomListSummary) -> some View {
        let isMember = viewModel.isMember(of: list)
        let isUpdating = viewModel.loadingListID == list.id

        return HStack(alignment: .top, spacing: 8) {
            Button {
                Task {
                    await viewModel.toggle(list)
                }
            } label: {
                VStack(alignment: .leading, spacing: 9) {
                    HStack(spacing: 12) {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 7) {
                                Text(list.name)
                                    .font(.system(size: 16, weight: .semibold))
                                ListMediaTypeIcons(mediaTypes: list.mediaTypes)
                                    .opacity(0.6)
                            }
                            Text(viewModel.countLabel(for: list))
                                .font(.system(size: 12, weight: .medium))
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Group {
                            if isUpdating {
                                ProgressView()
                            } else if isMember {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 16, weight: .bold))
                                    .foregroundStyle(.green)
                            }
                        }
                        .frame(width: 20, height: 20)
                        .spineContentTransition(value: listIndicatorPhase(for: list))
                    }

                    CustomListPreviewStrip(list: list)
                        .accessibilityIdentifier("add-to-list.preview.\(list.id)")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(viewModel.loadingListID != nil)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(list.name)
            .accessibilityValue(
                "\(viewModel.countLabel(for: list)), \(isUpdating ? "updating" : (isMember ? "added" : "not added"))"
            )
            .accessibilityHint(
                isMember
                    ? "Removes the \(viewModel.target.noun) from this list"
                    : "Adds the \(viewModel.target.noun) to this list"
            )
            .accessibilityAddTraits(isMember ? .isSelected : [])
            .accessibilityIdentifier("add-to-list.list.\(list.id)")
        }
    }

    private var emptyStateDescription: String {
        if !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Try another list name."
        }
        return viewModel.target.emptyStateDescription
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !filteredLists.isEmpty,
            hasError: viewModel.loadErrorMessage != nil
        )
    }

    private func listIndicatorPhase(for list: CustomListSummary) -> String {
        if viewModel.loadingListID == list.id { return "loading" }
        return viewModel.isMember(of: list) ? "selected" : "empty"
    }

    private var filteredLists: [CustomListSummary] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return viewModel.lists }
        return viewModel.lists.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    private func finishCreatingList() {
        guard let listID = pendingCreatedListID else { return }
        pendingCreatedListID = nil
        Task {
            if viewModel.target.listType == .people {
                await viewModel.addPersonToCreatedList(listID)
            } else {
                await viewModel.load()
            }
        }
    }
}
