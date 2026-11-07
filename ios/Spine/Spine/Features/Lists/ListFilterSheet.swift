import SwiftUI

/// The toolbar filter button of a page of lists, opening `ListFilterSheet`.
struct ListFilterButton: View {
    @Binding var state: ListBrowseState
    /// Your own lists can be narrowed to public or private ones.
    var showsVisibility = false
    let profileRepository: ProfileRepository
    var onApply: () -> Void

    @State private var isPresented = false
    @State private var mediaTypes = APIConstants.fallbackMediaTypes

    var body: some View {
        let count = state.activeCount(includesVisibility: showsVisibility)
        Button {
            isPresented = true
        } label: {
            Image(systemName: count > 0
                ? "line.3.horizontal.decrease.circle.fill"
                : "line.3.horizontal.decrease.circle")
        }
        .accessibilityLabel(count > 0 ? "Filters, \(count) active" : "Filters")
        .sheet(isPresented: $isPresented) {
            ListFilterSheet(state: $state, showsVisibility: showsVisibility, mediaTypes: mediaTypes, onApply: onApply)
        }
        .task {
            // Only the types you have on, as in every type picker.
            guard let profile = try? await profileRepository.me() else { return }
            mediaTypes = APIConstants.pickerMediaTypes(enabled: profile.preferences.enabledMediaTypes)
            let pruned = state.pruned(toMediaTypes: mediaTypes)
            if pruned != state {
                state = pruned
                onApply()
            }
        }
    }
}

struct ListFilterSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var state: ListBrowseState
    let showsVisibility: Bool
    let mediaTypes: [String]
    let onApply: () -> Void

    @State private var draft: ListBrowseState

    init(state: Binding<ListBrowseState>, showsVisibility: Bool, mediaTypes: [String], onApply: @escaping () -> Void) {
        _state = state
        self.showsVisibility = showsVisibility
        self.mediaTypes = mediaTypes
        self.onApply = onApply
        _draft = State(initialValue: state.wrappedValue)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Sort") {
                    Picker("Sort by", selection: sortBinding) {
                        ForEach(ListSort.allCases) { sort in
                            Text(sort.label).tag(sort)
                        }
                    }

                    Picker("Order", selection: directionBinding) {
                        ForEach(directions, id: \.self) { direction in
                            Text(draft.sort.label(for: direction)).tag(direction)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                Section {
                    Picker("List type", selection: $draft.listType) {
                        Text("Any").tag(CustomListType?.none)
                        Text("Media").tag(Optional(CustomListType.media))
                        Text("People").tag(Optional(CustomListType.people))
                    }

                    if draft.listType != .people {
                        Picker("Media type", selection: $draft.mediaType) {
                            Text("Any").tag(String?.none)
                            ForEach(mediaTypes, id: \.self) { type in
                                Text(MediaTypeTheme.theme(for: type).displayName).tag(Optional(type))
                            }
                        }
                    }

                    if showsVisibility {
                        Picker("Visibility", selection: $draft.visibility) {
                            Text("Any").tag(ListVisibilityFilter?.none)
                            ForEach(ListVisibilityFilter.allCases) { visibility in
                                Text(visibility.label).tag(Optional(visibility))
                            }
                        }
                    }
                } header: {
                    Text("Filter")
                } footer: {
                    if draft.effectiveMediaType != nil {
                        Text("Shows lists that hold only this type.")
                    }
                }
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
                        state = draft
                        onApply()
                        dismiss()
                    }
                    .fontWeight(.semibold)
                }
                ToolbarItem(placement: .bottomBar) {
                    Button("Reset", role: .destructive) {
                        draft = .default
                    }
                    .disabled(draft == .default)
                }
            }
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.large])
    }

    /// The sort's own order first: newest, A to Z, most.
    private var directions: [MediaFilterDirection] {
        draft.sort.defaultDirection == .asc ? [.asc, .desc] : [.desc, .asc]
    }

    /// A new sort starts on its own order: newest, A to Z, most.
    private var sortBinding: Binding<ListSort> {
        Binding(
            get: { draft.sort },
            set: { sort in
                draft.sort = sort
                draft.direction = nil
            }
        )
    }

    private var directionBinding: Binding<MediaFilterDirection> {
        Binding(
            get: { draft.resolvedDirection },
            set: { direction in
                draft.direction = direction == draft.sort.defaultDirection ? nil : direction
            }
        )
    }
}
