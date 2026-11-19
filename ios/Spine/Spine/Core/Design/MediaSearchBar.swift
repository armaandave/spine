import SwiftUI

/// The search field, with the media type picker under it when `showsMediaLens` is on.
struct MediaSearchBar: View {
    @FocusState private var isFocused: Bool
    @Binding var text: String
    @Binding var selectedMediaType: String
    let availableTypes: [String]
    var placeholderPrefix = "Search"
    var horizontalPadding: CGFloat = 16
    var showsMediaLens = true
    let onLensSelect: (String) -> Void
    let onSearch: (String) -> Void
    let onClear: () -> Void
    var onFocusChange: (Bool) -> Void = { _ in }
    var focusRequest: Int = 0

    var body: some View {
        VStack(spacing: 0) {
            searchControls
                .modifier(MediaSearchChrome(horizontalPadding: horizontalPadding))

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

    private var searchControls: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)

            TextField(searchPlaceholder, text: $text)
                .focused($isFocused)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit(performSearch)

            Button(action: clearOrDismiss) {
                Image(systemName: "xmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(text.isEmpty ? "Dismiss search" : "Clear search")
        }
    }

    private var searchPlaceholder: String {
        "\(placeholderPrefix) \(MediaTypeTheme.theme(for: selectedMediaType).displayName.lowercased())"
    }

    private func performSearch() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        onSearch(trimmed)
    }

    private func clearOrDismiss() {
        if text.isEmpty {
            isFocused = false
        } else {
            text = ""
            onClear()
        }
    }
}

private struct MediaSearchChrome: ViewModifier {
    let horizontalPadding: CGFloat

    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 14)
            .padding(.vertical, 3)
            .frame(minHeight: 44)
            .background(.ultraThinMaterial, in: Capsule())
            .overlay {
                Capsule()
                    .stroke(.white.opacity(0.10), lineWidth: 1)
            }
            .padding(.horizontal, horizontalPadding)
            .padding(.top, 2)
            .padding(.bottom, 5)
    }
}
