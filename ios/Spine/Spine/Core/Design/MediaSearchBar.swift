import SwiftUI

/// The search field, with the media type picker under it when `showsMediaLens` is on. The field is a Liquid Glass
/// capsule with only the magnifier and the words in it; a clear button shows once something is typed, and while
/// searching a round glass ✕ slides out of the field to leave the search.
struct MediaSearchBar: View {
    @FocusState private var isFocused: Bool
    @Namespace private var glass
    @Binding var text: String
    @Binding var selectedMediaType: String
    let availableTypes: [String]
    var placeholderPrefix = "Search"
    var horizontalPadding: CGFloat = 16
    var showsMediaLens = true
    let onLensSelect: (String) -> Void
    let onSearch: (String) -> Void
    let onClear: () -> Void
    /// The page is still searching (showing Recently opened or results), so the ✕ stays out with the keyboard down
    /// and nothing typed.
    var isActive = false
    /// The ✕ beside the field: the keyboard goes away, what was typed is cleared, and the page may leave searching.
    var onDismiss: () -> Void = {}
    var onFocusChange: (Bool) -> Void = { _ in }
    var focusRequest: Int = 0

    var body: some View {
        VStack(spacing: 0) {
            searchRow
                .padding(.horizontal, horizontalPadding)
                .padding(.top, 2)
                .padding(.bottom, 5)

            if showsMediaLens {
                MediaTypeBar(
                    selectedType: $selectedMediaType,
                    availableTypes: availableTypes,
                    onSelect: onLensSelect
                )
                .padding(.horizontal, horizontalPadding)
                .padding(.top, 5)
                .padding(.bottom, 8)
            }
        }
        .onChange(of: isFocused) { _, focused in
            onFocusChange(focused)
        }
        .onChange(of: focusRequest) {
            Task { @MainActor in
                await Task.yield()
                isFocused = true
            }
        }
    }

    /// Typing, something typed, or the page still showing a search.
    private var isSearching: Bool {
        isFocused || isActive || !text.isEmpty
    }

    private var searchRow: some View {
        GlassEffectContainer(spacing: 8) {
            HStack(spacing: 8) {
                field

                if isSearching {
                    Button(action: dismiss) {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .semibold))
                            .frame(width: 44, height: 44)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .glassEffect(.regular.interactive(), in: .circle)
                    .glassEffectID("close", in: glass)
                    .accessibilityLabel("Close search")
                }
            }
        }
        .animation(.smooth(duration: 0.35), value: isSearching)
        // The pages it sits on are dark; light glass would wash out the white words.
        .environment(\.colorScheme, .dark)
    }

    private var field: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)

            TextField(searchPlaceholder, text: $text)
                .focused($isFocused)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit(performSearch)

            if !text.isEmpty {
                Button(action: clear) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 17))
                        .foregroundStyle(.secondary)
                        .frame(width: 28, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.leading, 14)
        .padding(.trailing, text.isEmpty ? 14 : 8)
        .frame(minHeight: 44)
        .glassEffect(.regular, in: .capsule)
        .glassEffectID("field", in: glass)
    }

    private var searchPlaceholder: String {
        "\(placeholderPrefix) \(MediaTypeTheme.theme(for: selectedMediaType).displayName.lowercased())"
    }

    private func performSearch() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        onSearch(trimmed)
    }

    private func clear() {
        text = ""
        onClear()
    }

    private func dismiss() {
        isFocused = false
        if !text.isEmpty {
            clear()
        }
        onDismiss()
    }
}
