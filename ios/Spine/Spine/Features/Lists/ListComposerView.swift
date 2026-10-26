import SwiftUI

struct ListComposerView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ListComposerViewModel
    @State private var presentedSheet: ListComposerSheet?
    @State private var showsDiscardConfirmation = false
    @FocusState private var focusedField: Field?

    private let mediaRepository: MediaRepository
    private let peopleRepository: PeopleRepository
    private let onUnauthorized: () -> Void
    private let onSaved: (Int, ListComposerDraft) -> Void

    private enum Field: Hashable {
        case name
        case description
        case tags
    }

    private static let tagsRowID = "list-composer-tags"

    init(
        mode: ListComposerMode,
        initialItems: [MediaSummary] = [],
        listRepository: ListRepository,
        mediaRepository: MediaRepository,
        peopleRepository: PeopleRepository,
        currentUserId: Int? = nil,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping (Int, ListComposerDraft) -> Void
    ) {
        _viewModel = State(initialValue: ListComposerViewModel(
            mode: mode,
            initialItems: initialItems,
            listRepository: listRepository,
            currentUserId: currentUserId,
            onUnauthorized: onUnauthorized
        ))
        self.mediaRepository = mediaRepository
        self.peopleRepository = peopleRepository
        self.onUnauthorized = onUnauthorized
        self.onSaved = onSaved
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            VStack(spacing: 0) {
                topBar

                ScrollViewReader { proxy in
                    List {
                        detailsSection
                            .listRowInsets(EdgeInsets(top: 16, leading: 16, bottom: 9, trailing: 16))
                            .listRowSeparator(.hidden)

                        settingsSection
                            .listRowInsets(EdgeInsets(top: 9, leading: 16, bottom: 9, trailing: 16))
                            .listRowSeparator(.hidden)

                        if viewModel.canEditTags {
                            tagsSection(proxy: proxy)
                                .id(Self.tagsRowID)
                                .listRowInsets(EdgeInsets(top: 9, leading: 16, bottom: 9, trailing: 16))
                                .listRowSeparator(.hidden)
                        }

                        itemsSection

                        errorSection
                            .listRowInsets(EdgeInsets(top: 9, leading: 16, bottom: 9, trailing: 16))
                            .listRowSeparator(.hidden)
                    }
                    .listStyle(.plain)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
                    .scrollContentBackground(.hidden)
                    .scrollDismissesKeyboard(.interactively)
                    .contentMargins(.bottom, 19, for: .scrollContent)
                    .environment(\.defaultMinListRowHeight, 1)
                    .environment(\.editMode, .constant(.active))
                    .disabled(viewModel.isSaving)
                }
            }
        }
        .task {
            await viewModel.loadTagLibrary()
        }
        .onChange(of: viewModel.suggestsTopTags) {
            viewModel.refreshTagSuggestions()
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if focusedField == nil {
                actionFooter
            }
        }
        .overlay(alignment: .bottom) {
            if let removedItem = viewModel.removedItem {
                undoBanner(removedItem)
                    .padding(.horizontal, 16)
                    .padding(.bottom, focusedField == nil ? 92 : 14)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            } else if let removedPerson = viewModel.removedPerson {
                undoBanner(removedPerson)
                    .padding(.horizontal, 16)
                    .padding(.bottom, focusedField == nil ? 92 : 14)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.18), value: removedEntryID)
        .fullScreenCover(item: $presentedSheet) { sheet in
            switch sheet {
            case .mediaPicker:
                ListMediaPickerView(
                    viewModel: viewModel,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                )
            case .peoplePicker:
                ListPeoplePickerView(
                    viewModel: viewModel,
                    peopleRepository: peopleRepository,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .interactiveDismissDisabled(viewModel.requiresDiscardConfirmation || viewModel.isSaving)
        .alert("Discard Changes?", isPresented: $showsDiscardConfirmation) {
            Button("Discard Changes", role: .destructive) {
                dismiss()
            }
            Button("Keep Editing", role: .cancel) {}
        } message: {
            Text(viewModel.discardConfirmationMessage)
        }
        .preferredColorScheme(.dark)
    }

    private var topBar: some View {
        HStack(spacing: 14) {
            Button(action: requestClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(.black.opacity(0.34), in: Circle())
                    .overlay { Circle().stroke(.white.opacity(0.08)) }
            }
            .buttonStyle(.plain)
            .disabled(viewModel.isSaving)
            .accessibilityLabel("Close list editor")

            Text(viewModel.mode.title)
                .font(.system(size: 22, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)

            Spacer(minLength: 8)
        }
        .padding(.horizontal, 18)
        .padding(.top, 8)
        .padding(.bottom, 8)
    }

    private var detailsSection: some View {
        composerSurface {
            VStack(alignment: .leading, spacing: 10) {
                TextField("Name", text: $viewModel.draft.name)
                    .textFieldStyle(.plain)
                    .focused($focusedField, equals: .name)
                    .font(.system(size: 16, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .description }

                Divider().overlay(.white.opacity(0.1))

                TextField("Description", text: $viewModel.draft.description, axis: .vertical)
                    .textFieldStyle(.plain)
                    .focused($focusedField, equals: .description)
                    .lineLimit(3...6)
                    .font(.system(size: 16, weight: .regular, design: .rounded))
                    .foregroundStyle(.white)
            }
        }
    }

    private var settingsSection: some View {
        composerSurface {
            HStack(spacing: 12) {
                Text("Visibility")
                    .font(.system(size: 15, weight: .semibold, design: .rounded))

                Spacer(minLength: 8)

                Picker("Visibility", selection: $viewModel.draft.visibility) {
                    Text("Public").tag("public")
                    Text("Private").tag("private")
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .tint(.white)
            }

            Divider().overlay(.white.opacity(0.1))

            Toggle("Ranked", isOn: $viewModel.draft.isRanked)
                .font(.system(size: 15, weight: .semibold, design: .rounded))
                .tint(.white)
        }
    }

    /// Tags, with the log sheet's chips: tap a chip to remove it; return or a comma adds what's typed.
    /// Suggestions show while typing, inside the card, so their taps work in the always-editing list.
    private func tagsSection(proxy: ScrollViewProxy) -> some View {
        let tagDraft = viewModel.tagDraft
        return composerSurface {
            Text("Tags")
                .font(.system(size: 15, weight: .semibold, design: .rounded))

            if !tagDraft.tags.isEmpty {
                FlowLayout(spacing: 8) {
                    ForEach(tagDraft.tags, id: \.self) { tag in
                        Button {
                            tagDraft.remove(tag)
                        } label: {
                            HStack(spacing: 5) {
                                Text(tag)
                                Image(systemName: "xmark")
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(.secondary)
                            }
                            .font(.subheadline)
                            .foregroundStyle(.primary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Color(.tertiarySystemFill), in: Capsule())
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Remove tag \(tag)")
                    }
                }
            }

            TextField("Add tags", text: Bindable(tagDraft).query, axis: .vertical)
                .lineLimit(1...3)
                .textFieldStyle(.plain)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($focusedField, equals: .tags)
                .font(.system(size: 16, weight: .regular, design: .rounded))
                .foregroundStyle(.white)
                .onChange(of: tagDraft.query) { _, text in
                    if LogTagDraft.containsSeparator(text) {
                        tagDraft.updateQuery(text)
                    }
                }
                .onSubmit {
                    tagDraft.commitQuery()
                    focusedField = .tags
                }

            if focusedField == .tags, !tagDraft.suggestions.isEmpty {
                VStack(spacing: 0) {
                    ForEach(tagDraft.suggestions, id: \.name) { suggestion in
                        Divider().overlay(.white.opacity(0.1))
                        Button {
                            tagDraft.pick(suggestion.name)
                            focusedField = .tags
                        } label: {
                            HStack(spacing: 10) {
                                Image(systemName: "number")
                                    .foregroundStyle(.secondary)
                                Text(suggestion.name)
                                    .foregroundStyle(.primary)
                                Spacer(minLength: 8)
                                if suggestion.usageCount > 0 {
                                    Text("\(suggestion.usageCount)")
                                        .monospacedDigit()
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .font(.system(size: 15, weight: .regular, design: .rounded))
                            .frame(minHeight: 44)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Add tag \(suggestion.name)")
                    }
                }
            }

            if viewModel.suggestsTopTags {
                Text("Tag it \(viewModel.topTags[0]) or topalltime to show it as your Top in Stats.")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .sensoryFeedback(trigger: tagDraft.tags.count) { oldCount, newCount in
            newCount > oldCount ? SensoryFeedback.impact(weight: .light) : .selection
        }
        .onChange(of: focusedField) { oldField, newField in
            if oldField == .tags, newField != .tags {
                // Done, a swipe or another field: keep what was typed as a tag.
                tagDraft.commitQuery()
            }
            guard newField == .tags else { return }
            // The suggestions and the keyboard arrive after focus does.
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(350))
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo(Self.tagsRowID, anchor: .bottom)
                }
            }
        }
    }

    @ViewBuilder
    private var itemsSection: some View {
        if viewModel.draft.listType == .people {
            peopleHeader
                .listRowInsets(EdgeInsets(top: 9, leading: 18, bottom: 6, trailing: 18))
                .listRowSeparator(.hidden)

            if viewModel.draft.people.isEmpty {
                emptyPeopleState
                    .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 9, trailing: 16))
                    .listRowSeparator(.hidden)
            } else {
                ForEach(Array(viewModel.draft.people.enumerated()), id: \.element.entryId) { index, person in
                    ListComposerPersonRow(
                        person: person,
                        rank: viewModel.draft.isRanked ? index + 1 : nil,
                        onRemove: { viewModel.removePerson(entryId: person.entryId) }
                    )
                    .frame(height: 79)
                    .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
                    .listRowSeparator(.hidden)
                }
                .onMove { source, destination in
                    guard let sourceIndex = source.first else { return }
                    viewModel.movePerson(from: sourceIndex, to: destination)
                }
            }
        } else {
            mediaItemsHeader
                .listRowInsets(EdgeInsets(top: 9, leading: 18, bottom: 6, trailing: 18))
                .listRowSeparator(.hidden)

            if viewModel.draft.items.isEmpty {
                emptyItemsState
                    .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 9, trailing: 16))
                    .listRowSeparator(.hidden)
            } else {
                ForEach(Array(viewModel.draft.items.enumerated()), id: \.element.id) { index, item in
                    ListComposerItemRow(
                        item: item,
                        rank: viewModel.draft.isRanked ? index + 1 : nil,
                        onRemove: { viewModel.removeItem(id: item.id) }
                    )
                    .frame(height: 79)
                    .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
                    .listRowSeparator(.hidden)
                }
                .onMove { source, destination in
                    guard let sourceIndex = source.first else { return }
                    viewModel.moveItem(from: sourceIndex, to: destination)
                }
            }
        }
    }

    private var mediaItemsHeader: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Your Items")
                    .font(.system(size: 16, weight: .heavy, design: .rounded))
                    .foregroundStyle(.white)
                Text("\(viewModel.draft.items.count) selected")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.46))
            }

            Spacer()

            Button(action: presentMediaPicker) {
                Label("Add Media", systemImage: "plus")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 12)
                    .frame(height: 36)
                    .background(.white, in: Capsule())
            }
            .buttonStyle(.plain)
        }
    }

    private var peopleHeader: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Your People")
                    .font(.system(size: 16, weight: .heavy, design: .rounded))
                    .foregroundStyle(.white)
                Text("\(viewModel.draft.people.count) selected")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.46))
            }

            Spacer()

            Button(action: presentPeoplePicker) {
                Label("Add People", systemImage: "plus")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 12)
                    .frame(height: 36)
                    .background(.white, in: Capsule())
            }
            .buttonStyle(.plain)
        }
    }

    private var emptyPeopleState: some View {
        VStack(spacing: 10) {
            Image(systemName: "person.crop.circle.badge.plus")
                .font(.system(size: 30, weight: .semibold))
                .foregroundStyle(.white.opacity(0.54))
            Text("No people added yet")
                .font(.system(size: 17, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)
            Text("Search for people or add them from any person page.")
                .font(.system(size: 13, weight: .medium, design: .rounded))
                .foregroundStyle(.white.opacity(0.5))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
        .padding(.vertical, 30)
        .background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(.white.opacity(0.07))
        }
    }

    private var emptyItemsState: some View {
        VStack(spacing: 10) {
            Image(systemName: "rectangle.stack.badge.plus")
                .font(.system(size: 30, weight: .semibold))
                .foregroundStyle(.white.opacity(0.54))
            Text("No media added yet")
                .font(.system(size: 17, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
        .padding(.vertical, 30)
        .background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(.white.opacity(0.07))
        }
    }

    @ViewBuilder
    private var errorSection: some View {
        if let error = viewModel.errorMessage {
            VStack(alignment: .leading, spacing: 8) {
                Label("Could not finish saving", systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 14, weight: .heavy, design: .rounded))
                Text(error)
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                if !viewModel.failedItemTitles.isEmpty {
                    Text(viewModel.failedItemTitles.joined(separator: ", "))
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.64))
                }
            }
            .foregroundStyle(.red.opacity(0.94))
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.red.opacity(0.12), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
    }

    private var actionFooter: some View {
        VStack(spacing: 7) {
            Button {
                Task { await save() }
            } label: {
                HStack(spacing: 10) {
                    if viewModel.isSaving {
                        ProgressView().tint(.black)
                    }
                    Text(viewModel.isSaving ? viewModel.phaseLabel : viewModel.primaryActionTitle)
                        .font(.system(size: 16, weight: .heavy, design: .rounded))
                }
                .spineContentTransition(value: viewModel.isSaving)
                .foregroundStyle(.black)
                .frame(maxWidth: .infinity)
                .frame(height: 52)
                .background(viewModel.canSave ? .white : .white.opacity(0.28), in: Capsule())
            }
            .buttonStyle(.plain)
            .disabled(!viewModel.canSave)

            if viewModel.draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text("Add a list name to continue")
            } else if viewModel.draft.listType == .media, viewModel.draft.items.isEmpty {
                Text("Add at least one media item to continue")
            }
        }
        .font(.system(size: 11, weight: .semibold, design: .rounded))
        .foregroundStyle(.white.opacity(0.48))
        .padding(.horizontal, 18)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(.ultraThinMaterial)
    }

    private func undoBanner(_ removedItem: RemovedListComposerItem) -> some View {
        HStack(spacing: 12) {
            Text("Removed \(removedItem.item.title)")
                .font(.system(size: 13, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .lineLimit(1)
            Spacer(minLength: 8)
            Button("Undo") {
                viewModel.undoRemoval()
            }
            .font(.system(size: 13, weight: .heavy, design: .rounded))
            .foregroundStyle(.white)
        }
        .padding(.horizontal, 15)
        .frame(height: 48)
        .background(.black.opacity(0.94), in: Capsule())
        .overlay { Capsule().stroke(.white.opacity(0.14)) }
        .task(id: removedItem.id) {
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            viewModel.clearUndo()
        }
    }

    private func undoBanner(_ removedPerson: RemovedListComposerPerson) -> some View {
        HStack(spacing: 12) {
            Text("Removed \(removedPerson.person.name)")
                .font(.system(size: 13, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .lineLimit(1)
            Spacer(minLength: 8)
            Button("Undo") {
                viewModel.undoPersonRemoval()
            }
            .font(.system(size: 13, weight: .heavy, design: .rounded))
            .foregroundStyle(.white)
        }
        .padding(.horizontal, 15)
        .frame(height: 48)
        .background(.black.opacity(0.94), in: Capsule())
        .overlay { Capsule().stroke(.white.opacity(0.14)) }
        .task(id: removedPerson.id) {
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            viewModel.clearUndo()
        }
    }

    private var removedEntryID: UUID? {
        viewModel.removedItem?.id ?? viewModel.removedPerson?.id
    }

    private func composerSurface<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 14, content: content)
            .padding(15)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.black.opacity(0.24), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(.white.opacity(0.09))
            }
    }

    private func requestClose() {
        guard !viewModel.isSaving else { return }
        if viewModel.requiresDiscardConfirmation {
            showsDiscardConfirmation = true
        } else {
            dismiss()
        }
    }

    private func save() async {
        if let listID = await viewModel.save() {
            onSaved(listID, viewModel.draft)
            dismiss()
        }
    }

    private func presentMediaPicker() {
        focusedField = nil
        presentedSheet = .mediaPicker
    }

    private func presentPeoplePicker() {
        focusedField = nil
        presentedSheet = .peoplePicker
    }
}

private enum ListComposerSheet: Identifiable {
    case mediaPicker
    case peoplePicker

    var id: String {
        switch self {
        case .mediaPicker: "media-picker"
        case .peoplePicker: "people-picker"
        }
    }
}

private struct ListComposerPersonRow: View {
    let person: PersonListEntry
    let rank: Int?
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            if let rank {
                Text("#\(rank)")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.56))
                    .frame(width: 30)
            }

            PersonArtwork(urlString: person.profileUrl, name: person.name, size: 48)

            VStack(alignment: .leading, spacing: 4) {
                Text(person.name)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.94))
                    .lineLimit(2)
                if let department = person.knownForDepartment {
                    Text(department)
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundStyle(.white.opacity(0.46))
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 6)

            Button(role: .destructive, action: onRemove) {
                Image(systemName: "minus.circle.fill")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(.red.opacity(0.82))
                    .frame(width: 38, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(person.name)")
        }
        .padding(.horizontal, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.black.opacity(0.22), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(.white.opacity(0.075))
        }
    }
}

private struct ListComposerItemRow: View {
    let item: MediaSummary
    let rank: Int?
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            if let rank {
                Text("#\(rank)")
                    .font(.system(size: 13, weight: .heavy, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.56))
                    .frame(width: 30)
            }

            MediaArtwork(
                url: item.displayPosterURL,
                title: item.title,
                slot: .diaryRow,
                mediaType: item.ref.mediaType,
                orientation: item.posterOrientation
            )
            .scaleEffect(0.75)
            .frame(width: 42, height: 63)

            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.94))
                    .lineLimit(2)
                if let subtitle = item.subtitle ?? item.releaseDate {
                    Text(subtitle)
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundStyle(.white.opacity(0.46))
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 6)

            Button(role: .destructive, action: onRemove) {
                Image(systemName: "minus.circle.fill")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(.red.opacity(0.82))
                    .frame(width: 38, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(item.title)")
        }
        .padding(.horizontal, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.black.opacity(0.22), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(.white.opacity(0.075))
        }
    }
}
