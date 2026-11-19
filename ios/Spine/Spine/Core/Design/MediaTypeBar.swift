import SwiftUI

/// The media type picker on Search, Add Media, Library, Diary and Stats: one thin bar with an icon
/// per type, drawn like iOS's segmented control. A lighter lens sits under the chosen type and
/// slides to the next one you pick. White on the page's dark only: no color, no names.
struct MediaTypeBar: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @Binding var selectedType: String
    let availableTypes: [String]
    /// Tapping the chosen type again clears the choice ("" is all media), as on Diary and Stats.
    var allowsEmptySelection = false
    /// Off where the page already ticks for its own switches.
    var playsHaptic = true
    var onSelect: (String) -> Void = { _ in }

    /// The type the lens sits under; it follows `selectedType`.
    @State private var shown: String
    /// The lens's place. It keeps its last one while nothing is chosen, so it fades out where it was.
    @State private var lensIndex: Int

    init(
        selectedType: Binding<String>,
        availableTypes: [String],
        allowsEmptySelection: Bool = false,
        playsHaptic: Bool = true,
        onSelect: @escaping (String) -> Void = { _ in }
    ) {
        _selectedType = selectedType
        self.availableTypes = availableTypes
        self.allowsEmptySelection = allowsEmptySelection
        self.playsHaptic = playsHaptic
        self.onSelect = onSelect
        _shown = State(initialValue: selectedType.wrappedValue)
        _lensIndex = State(initialValue: availableTypes.firstIndex(of: selectedType.wrappedValue) ?? 0)
    }

    var body: some View {
        Capsule()
            .fill(MediaTypeBarStyle.track)
            .frame(height: MediaTypeBarStyle.height)
            .overlay {
                GeometryReader { proxy in
                    let segment = proxy.size.width / CGFloat(max(availableTypes.count, 1))
                    lens
                        .padding(MediaTypeBarStyle.lensInset)
                        .frame(width: segment)
                        // The slide: `lensIndex` changes inside `withAnimation`.
                        .offset(x: CGFloat(lensIndex) * segment)
                }
            }
            // An overlay, so the 44 pt tall buttons reach past the 32 pt bar without making it taller.
            .overlay {
                HStack(spacing: 0) {
                    ForEach(availableTypes, id: \.self) { type in
                        segment(type)
                    }
                }
            }
        // Glyphs are drawn white: the bar is dark whatever the phone's appearance.
        .environment(\.colorScheme, .dark)
        .sensoryFeedback(.selection, trigger: shown) { _, _ in playsHaptic }
        .onChange(of: selectedType) { _, type in
            guard type != shown else { return }
            move(to: type)
        }
        .onChange(of: availableTypes) { _, types in
            guard let index = types.firstIndex(of: shown) else { return }
            lensIndex = index
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Media type")
    }

    private var isLensShown: Bool {
        availableTypes.contains(shown)
    }

    private var lens: some View {
        Capsule()
            .fill(.white.opacity(0.16))
            .overlay {
                Capsule()
                    .strokeBorder(
                        LinearGradient(colors: [.white.opacity(0.3), .clear], startPoint: .top, endPoint: .center),
                        lineWidth: 0.5
                    )
            }
            .shadow(color: .black.opacity(0.25), radius: 1, y: 1)
            .opacity(isLensShown ? 1 : 0)
            .scaleEffect(isLensShown ? 1 : 0.85)
            .animation(.easeInOut(duration: 0.2), value: isLensShown)
            .allowsHitTesting(false)
    }

    private func segment(_ type: String) -> some View {
        let isShown = type == shown
        let theme = MediaTypeTheme.theme(for: type)

        return Button {
            select(type)
        } label: {
            MediaTypeGlyph(theme: theme, size: MediaTypeBarStyle.glyphSize)
                .opacity(isShown ? 1 : 0.55)
                .animation(.easeInOut(duration: 0.2), value: isShown)
                .frame(maxWidth: .infinity)
                .frame(height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(MediaTypeBarPressStyle(reduceMotion: reduceMotion))
        .accessibilityLabel(theme.displayName)
        .accessibilityAddTraits(isShown ? .isSelected : [])
        .accessibilityHint(isShown && allowsEmptySelection ? "Double-tap to show all media" : "")
    }

    private func select(_ type: String) {
        let next = allowsEmptySelection && type == shown ? "" : type
        move(to: next)
        selectedType = next
        onSelect(next)
    }

    /// Slides the lens to `type`, or fades it out where it is when `type` isn't one of the types.
    /// A lens that was hidden appears on `type` instead of sliding in from its old place.
    private func move(to type: String) {
        if let index = availableTypes.firstIndex(of: type) {
            if isLensShown, !reduceMotion {
                withAnimation(MediaTypeBarStyle.slide) { lensIndex = index }
            } else {
                lensIndex = index
            }
        }
        shown = type
    }
}

enum MediaTypeBarStyle {
    static let height: CGFloat = 32
    static let lensInset: CGFloat = 2
    static let glyphSize: CGFloat = 14
    /// The lens's trip between types: a short spring with no bounce.
    static let slide = Animation.smooth(duration: 0.3)
    /// iOS's segmented control track on dark.
    static let track = Color(red: 118 / 255, green: 118 / 255, blue: 128 / 255).opacity(0.24)
}

/// A slight squeeze under the finger.
private struct MediaTypeBarPressStyle: ButtonStyle {
    let reduceMotion: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.94 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
