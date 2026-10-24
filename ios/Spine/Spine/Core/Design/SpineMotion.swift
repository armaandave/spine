import Nuke
import NukeUI
import SwiftUI

enum SpineMotion {
    static let standardDuration = 0.18
    static let reducedDuration = 0.10

    static func durations(reduceMotion: Bool) -> Double {
        reduceMotion ? reducedDuration : standardDuration
    }

    static func animation(reduceMotion: Bool) -> Animation {
        .easeOut(duration: durations(reduceMotion: reduceMotion))
    }
}

struct SpineAppearanceState: Equatable {
    private(set) var isRevealed = false

    var opacity: Double {
        isRevealed ? 1 : 0
    }

    mutating func reveal() {
        isRevealed = true
    }
}

enum SpineContentPhase: Hashable {
    case loading
    case content
    case error
    case empty

    static func resolve(isLoading: Bool, hasContent: Bool, hasError: Bool) -> SpineContentPhase {
        if hasContent {
            return .content
        }
        if isLoading {
            return .loading
        }
        if hasError {
            return .error
        }
        return .empty
    }
}

private struct SpineContentTransitionModifier<Value: Hashable>: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let value: Value

    func body(content: Content) -> some View {
        content
            .id(value)
            .transition(.opacity)
            .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: value)
            .spineSoftAppear()
    }
}

private struct SpineAncestorSoftAppearanceKey: EnvironmentKey {
    static let defaultValue = false
}

private extension EnvironmentValues {
    var spineAncestorSoftAppearanceActive: Bool {
        get { self[SpineAncestorSoftAppearanceKey.self] }
        set { self[SpineAncestorSoftAppearanceKey.self] = newValue }
    }
}

private struct SpineSoftAppearanceModifier: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.spineAncestorSoftAppearanceActive) private var ancestorAppearanceActive
    @State private var appearance = SpineAppearanceState()

    func body(content: Content) -> some View {
        content
            .opacity(ancestorAppearanceActive ? 1 : appearance.opacity)
            .environment(
                \.spineAncestorSoftAppearanceActive,
                ancestorAppearanceActive || !appearance.isRevealed
            )
            .onAppear {
                guard !appearance.isRevealed else { return }
                if ancestorAppearanceActive {
                    appearance.reveal()
                } else {
                    withAnimation(SpineMotion.animation(reduceMotion: reduceMotion)) {
                        appearance.reveal()
                    }
                }
            }
    }
}

extension View {
    func spineContentTransition<Value: Hashable>(value: Value) -> some View {
        modifier(SpineContentTransitionModifier(value: value))
    }

    func spineSoftAppear() -> some View {
        modifier(SpineSoftAppearanceModifier())
    }
}

/// Loads through `SpineImagePipeline` (memory + disk cache) but keeps the `AsyncImagePhase` content API.
/// A scrolled-back cell whose image is in memory renders it on the first frame, with no placeholder or fade.
/// Pass `thumbnailSize` when the display size is fixed so the image is decoded at that size, and
/// `processors` for effects baked into the cached image (a blur) instead of applied on every frame.
struct SpineAsyncImage<Content: View>: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var lastSuccessfulImage: Image?

    let url: URL?
    let thumbnailSize: CGSize?
    let processors: [any ImageProcessing]
    private let content: (AsyncImagePhase) -> Content

    init(
        url: URL?,
        thumbnailSize: CGSize? = nil,
        processors: [any ImageProcessing] = [],
        @ViewBuilder content: @escaping (AsyncImagePhase) -> Content
    ) {
        self.url = url
        self.thumbnailSize = thumbnailSize
        self.processors = processors
        self.content = content
    }

    var body: some View {
        LazyImage(
            request: SpineImagePipeline.request(url: url, thumbnailSize: thumbnailSize, processors: processors),
            transaction: Transaction(animation: SpineMotion.animation(reduceMotion: reduceMotion))
        ) { state in
            resolvedContent(for: SpineImagePhase.phase(url: url, image: state.image, error: state.error))
        }
        // A new URL gets a fresh view, so the old and new images crossfade.
        .id(url)
        .transition(.opacity)
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: url)
        .onChange(of: url) { _, newURL in
            if newURL == nil {
                lastSuccessfulImage = nil
            }
        }
    }

    @ViewBuilder
    private func resolvedContent(for phase: AsyncImagePhase) -> some View {
        switch phase {
        case let .success(image):
            content(.success(image))
                .transition(.opacity)
                .onAppear {
                    lastSuccessfulImage = image
                }
        case .empty, .failure:
            if url != nil, let lastSuccessfulImage {
                content(.success(lastSuccessfulImage))
                    .transition(.opacity)
            } else {
                content(phase)
                    .transition(.opacity)
            }
        @unknown default:
            content(phase)
                .transition(.opacity)
        }
    }
}

enum SpineImagePhase {
    /// Matches `AsyncImage`: no URL is `.empty` (Nuke reports it as an error), and only a real load error
    /// is `.failure`. Nuke never surfaces a cancelled load (a cell scrolled away mid-download) and restarts
    /// it when the cell reappears.
    static func phase(url: URL?, image: Image?, error: (any Error)?) -> AsyncImagePhase {
        if let image {
            return .success(image)
        }
        if url != nil, let error {
            return .failure(error)
        }
        return .empty
    }
}
