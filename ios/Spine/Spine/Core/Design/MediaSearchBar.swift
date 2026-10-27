import SwiftUI

struct MediaSearchBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var isFocused: Bool
    @Binding var text: String
    @Binding var selectedMediaType: String
    @Binding var isLensExpanded: Bool
    let availableTypes: [String]
    var placeholderPrefix = "Search"
    var horizontalPadding: CGFloat = 16
    var showsMediaLens = true
    let onLensTap: () -> Void
    let onLensSelect: (String) -> Void
    let onSearch: (String) -> Void
    let onClear: () -> Void
    var onFocusChange: (Bool) -> Void = { _ in }
    var focusRequest: Int = 0

    var body: some View {
        ZStack {
            if isLensExpanded, showsMediaLens {
                MediaSearchLensPicker(
                    selectedType: $selectedMediaType,
                    availableTypes: availableTypes,
                    horizontalPadding: horizontalPadding,
                    onSelect: selectLens
                )
                .transition(.opacity)
            } else {
                searchControls
                    .modifier(MediaSearchChrome(isLensExpanded: false, horizontalPadding: horizontalPadding))
                    .transition(.opacity)
            }
        }
        .animation(reduceMotion ? nil : .spring(response: 0.36, dampingFraction: 0.84), value: isLensExpanded)
        .onChange(of: isLensExpanded) {
            if isLensExpanded {
                isFocused = false
            }
        }
        .onChange(of: isFocused) { _, focused in
            onFocusChange(focused)
        }
        .onChange(of: focusRequest) {
            isLensExpanded = false
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

            if showsMediaLens {
                MediaLensChip(selectedType: selectedMediaType, size: 24, symbolSize: 12, onTap: onLensTap)
                    .padding(.horizontal, 2)
            }

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

    private func selectLens(_ type: String) {
        selectedMediaType = type
        onLensSelect(type)
        isLensExpanded = false
    }
}

struct MediaSearchLensPicker: View {
    @Binding var selectedType: String
    let availableTypes: [String]
    var horizontalPadding: CGFloat = 16
    var fitsAllTypes = false
    var allowsEmptySelection = false
    var isCompact = false
    let onSelect: (String) -> Void

    var body: some View {
        MediaSearchLensRail(
            selectedType: $selectedType,
            availableTypes: availableTypes,
            fitsAllTypes: fitsAllTypes,
            isCompact: isCompact,
            allowsEmptySelection: allowsEmptySelection,
            onSelect: onSelect
        )
        .modifier(MediaSearchChrome(isLensExpanded: true, horizontalPadding: horizontalPadding, isCompact: isCompact))
    }
}

private struct MediaSearchChrome: ViewModifier {
    let isLensExpanded: Bool
    let horizontalPadding: CGFloat
    var isCompact = false

    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 14)
            .padding(.vertical, isCompact ? 5 : (isLensExpanded ? 12 : 3))
            .frame(minHeight: isCompact ? 52 : (isLensExpanded ? 92 : 44))
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

/// How the fitted lens row lays out its slots.
enum MediaSearchLensLayout {
    /// The spacing and slot width that fit every type in one row, or nil when they need to
    /// scroll. Slots are 44 pt or wider with the usual gap; when that's too wide, compact slots
    /// close the gap and touch, so each slot is its whole tap area, down to 38 pt. Eight types
    /// fit on a 402 pt phone (about 43 pt each) instead of scrolling one off screen.
    static func fittedSlots(available: CGFloat, count: Int, isCompact: Bool) -> (spacing: CGFloat, width: CGFloat)? {
        let count = max(count, 1)
        let spacing: CGFloat = isCompact ? 3 : 6
        let spaced = (available - spacing * CGFloat(count - 1)) / CGFloat(count)
        if spaced >= 44 {
            return (spacing, spaced)
        }
        let touching = available / CGFloat(count)
        if isCompact, touching >= 38 {
            return (0, touching)
        }
        return nil
    }
}

private struct MediaSearchLensRail: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Binding var selectedType: String
    let availableTypes: [String]
    let fitsAllTypes: Bool
    let isCompact: Bool
    let allowsEmptySelection: Bool
    let onSelect: (String) -> Void

    var body: some View {
        if fitsAllTypes {
            GeometryReader { proxy in
                let fit = MediaSearchLensLayout.fittedSlots(
                    available: proxy.size.width,
                    count: availableTypes.count,
                    isCompact: isCompact
                )
                let spacing = fit?.spacing ?? 0
                let width = fit?.width ?? 0
                let diameter = min(isCompact ? 34 : 38, width)

                if fit != nil {
                    HStack(spacing: spacing) {
                        ForEach(availableTypes, id: \.self) { type in
                            MediaSearchLensOrb(
                                type: type,
                                isSelected: selectedType == type,
                                diameter: diameter,
                                itemWidth: width,
                                minimumItemWidth: minimumFittedWidth,
                                showsSelectionIndicator: !isCompact,
                                clearOnReselection: allowsEmptySelection,
                                onTap: {
                                    select(type)
                                }
                            )
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: isCompact ? 44 : 68)
                } else {
                    scrollingRail
                }
            }
            .frame(height: isCompact ? 44 : 68)
            .accessibilityLabel("Media type picker")
        } else {
            scrollingRail
        }
    }

    /// The narrowest slot the fitted row allows before it scrolls instead.
    private var minimumFittedWidth: CGFloat { isCompact ? 38 : 44 }

    private var scrollingRail: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 12) {
                    ForEach(availableTypes, id: \.self) { type in
                        MediaSearchLensOrb(
                            type: type,
                            isSelected: selectedType == type,
                            diameter: isCompact ? 34 : 48,
                            itemWidth: isCompact ? 44 : 52,
                            showsSelectionIndicator: !isCompact,
                            clearOnReselection: allowsEmptySelection,
                            onTap: {
                                select(type)
                            }
                        )
                        .id(type)
                    }
                }
                .scrollTargetLayout()
                .padding(.horizontal, 2)
                .padding(.vertical, isCompact ? 0 : 10)
            }
            .frame(height: isCompact ? 44 : 68)
            .scrollTargetBehavior(.viewAligned)
            .onAppear {
                proxy.scrollTo(selectedType, anchor: .center)
            }
            .onChange(of: selectedType) {
                // The selected type always scrolls into view; Reduce Motion only drops the spring.
                guard !reduceMotion else {
                    proxy.scrollTo(selectedType, anchor: .center)
                    return
                }
                withAnimation(.spring(response: 0.30, dampingFraction: 0.82)) {
                    proxy.scrollTo(selectedType, anchor: .center)
                }
            }
        }
        .accessibilityLabel("Media type picker")
    }

    private func select(_ type: String) {
        if allowsEmptySelection, selectedType == type {
            selectedType = ""
            onSelect("")
        } else {
            selectedType = type
            onSelect(type)
        }
    }
}

private struct MediaSearchLensOrb: View {
    let type: String
    let isSelected: Bool
    var diameter: CGFloat = 48
    var itemWidth: CGFloat = 52
    var minimumItemWidth: CGFloat = 44
    var showsSelectionIndicator = true
    var clearOnReselection = false
    let onTap: () -> Void

    private var theme: MediaTypeTheme {
        MediaTypeTheme.theme(for: type)
    }

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: 6) {
                Circle()
                    .fill(.clear)
                    .modifier(MediaLensCircleStyle(isSelected: isSelected))
                    .overlay {
                        MediaTypeGlyph(theme: theme, size: diameter * (isSelected ? 0.44 : 0.38))
                    }
                    .frame(width: diameter, height: diameter)

                if showsSelectionIndicator {
                    Capsule()
                        .fill(.white.opacity(isSelected ? 0.58 : 0))
                        .frame(width: 12, height: 2)
                }
            }
            .frame(width: max(minimumItemWidth, itemWidth), height: max(44, showsSelectionIndicator ? 58 : diameter))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(theme.displayName)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
        .accessibilityHint(isSelected && clearOnReselection ? "Double-tap to show all media" : "")
    }
}
