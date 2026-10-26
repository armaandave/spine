import SwiftUI

struct PosterViewerItem: Identifiable, Equatable {
    let id: String
    let url: URL
    let title: String
    let aspectRatio: CGFloat

    init?(detail: MediaDetail) {
        guard let rawURL = detail.displayPosterURL?.trimmingCharacters(in: .whitespacesAndNewlines),
              !rawURL.isEmpty,
              let url = URL(string: rawURL),
              let scheme = url.scheme?.lowercased(),
              ["http", "https"].contains(scheme) else {
            return nil
        }

        id = detail.ref.id
        self.url = url
        title = detail.displayTitle
        aspectRatio = Self.resolvedAspectRatio(for: detail)
    }

    private static func resolvedAspectRatio(for detail: MediaDetail) -> CGFloat {
        if let aspectRatio = detail.posterAspectRatio,
           aspectRatio.isFinite,
           aspectRatio > 0 {
            return CGFloat(aspectRatio)
        }

        if let width = detail.posterWidth,
           let height = detail.posterHeight,
           width > 0,
           height > 0 {
            return CGFloat(width) / CGFloat(height)
        }

        switch detail.posterOrientation {
        case .landscape:
            return 16 / 9
        case .square:
            return 1
        case .portrait, .unknown, nil:
            return 2 / 3
        }
    }
}

enum PosterViewerLayout {
    static let minimumScale: CGFloat = 1
    static let maximumScale: CGFloat = 5
    static let dismissDistance: CGFloat = 120
    static let predictedDismissDistance: CGFloat = 190

    static func clampedScale(_ scale: CGFloat) -> CGFloat {
        min(max(scale, minimumScale), maximumScale)
    }

    static func fittedSize(aspectRatio: CGFloat, in viewport: CGSize) -> CGSize {
        guard viewport.width > 0, viewport.height > 0 else { return .zero }

        let ratio = aspectRatio.isFinite && aspectRatio > 0 ? aspectRatio : 2 / 3
        if ratio > viewport.width / viewport.height {
            return CGSize(width: viewport.width, height: viewport.width / ratio)
        }
        return CGSize(width: viewport.height * ratio, height: viewport.height)
    }

    static func clampedOffset(
        _ offset: CGSize,
        scale: CGFloat,
        contentSize: CGSize,
        viewport: CGSize
    ) -> CGSize {
        let scale = clampedScale(scale)
        guard scale > minimumScale else { return .zero }

        let maximumX = max(0, (contentSize.width * scale - viewport.width) / 2)
        let maximumY = max(0, (contentSize.height * scale - viewport.height) / 2)
        return CGSize(
            width: min(max(offset.width, -maximumX), maximumX),
            height: min(max(offset.height, -maximumY), maximumY)
        )
    }

    static func shouldDismiss(
        translation: CGSize,
        predictedEndTranslation: CGSize,
        scale: CGFloat
    ) -> Bool {
        guard scale <= minimumScale + 0.01,
              translation.height > 0,
              translation.height > abs(translation.width) else {
            return false
        }
        return translation.height >= dismissDistance
            || predictedEndTranslation.height >= predictedDismissDistance
    }

    static func dismissalProgress(translationY: CGFloat, viewportHeight: CGFloat) -> CGFloat {
        guard viewportHeight > 0 else { return 0 }
        return min(max(translationY, 0) / (viewportHeight * 0.55), 1)
    }
}

struct PosterViewer: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let item: PosterViewerItem
    let namespace: Namespace.ID
    let onDismiss: () -> Void

    @State private var settledScale: CGFloat = PosterViewerLayout.minimumScale
    @State private var magnification: CGFloat = 1
    @State private var settledOffset = CGSize.zero
    @State private var dragTranslation = CGSize.zero

    var body: some View {
        GeometryReader { proxy in
            let viewport = proxy.size
            let contentSize = PosterViewerLayout.fittedSize(
                aspectRatio: item.aspectRatio,
                in: viewport
            )
            let scale = currentScale
            let progress = scale <= PosterViewerLayout.minimumScale + 0.01
                ? PosterViewerLayout.dismissalProgress(
                    translationY: dragTranslation.height,
                    viewportHeight: viewport.height
                )
                : 0
            let offset = displayedOffset(
                scale: scale,
                contentSize: contentSize,
                viewport: viewport
            )

            ZStack {
                Color.black
                    .opacity(1 - progress * 0.72)
                    .ignoresSafeArea()
                    .transition(.opacity)

                posterArtwork(size: contentSize)
                    .matchedGeometryEffect(id: item.id, in: namespace, isSource: false)
                    .scaleEffect(scale * (1 - progress * 0.06))
                    .offset(offset)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("\(item.title) poster")
                    .accessibilityValue("\(Int((scale * 100).rounded())) percent")
                    .accessibilityAddTraits(.isImage)
                    .accessibilityIdentifier("poster-viewer.image")
                    .accessibilityZoomAction { action in
                        accessibilityZoom(action.direction, contentSize: contentSize, viewport: viewport)
                    }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
            .overlay(alignment: .topTrailing) {
                closeButton
                    .padding(.top, max(proxy.safeAreaInsets.top, 12) + 4)
                    .padding(.trailing, 16)
                    .opacity(1 - min(progress * 1.8, 1))
            }
            .highPriorityGesture(dragGesture(contentSize: contentSize, viewport: viewport))
            .simultaneousGesture(magnifyGesture(contentSize: contentSize, viewport: viewport))
            .onTapGesture(count: 2) {
                toggleZoom(contentSize: contentSize, viewport: viewport)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
    }

    private var currentScale: CGFloat {
        PosterViewerLayout.clampedScale(settledScale * magnification)
    }

    private var closeButton: some View {
        Button(action: dismissFromButton) {
            Image(systemName: "xmark")
                .font(.system(size: 15, weight: .bold))
                .frame(width: 42, height: 42)
        }
        .buttonStyle(.plain)
        .contentShape(.circle)
        .glassEffect(.regular.interactive(), in: .circle)
        .accessibilityLabel("Close poster")
        .accessibilityIdentifier("poster-viewer.close")
    }

    @ViewBuilder
    private func posterArtwork(size: CGSize) -> some View {
        SpineAsyncImage(url: item.url) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFit()
            case .empty:
                ProgressView()
                    .tint(.white)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.black)
            default:
                ContentUnavailableView("Poster unavailable", systemImage: "photo")
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.black)
            }
        }
        .frame(width: size.width, height: size.height)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .shadow(color: .black.opacity(0.32), radius: 24, y: 10)
    }

    private func displayedOffset(scale: CGFloat, contentSize: CGSize, viewport: CGSize) -> CGSize {
        if scale <= PosterViewerLayout.minimumScale + 0.01 {
            return CGSize(width: 0, height: max(0, dragTranslation.height))
        }
        return PosterViewerLayout.clampedOffset(
            settledOffset + dragTranslation,
            scale: scale,
            contentSize: contentSize,
            viewport: viewport
        )
    }

    private func dragGesture(contentSize: CGSize, viewport: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 8, coordinateSpace: .global)
            .onChanged { value in
                if currentScale > PosterViewerLayout.minimumScale + 0.01 {
                    dragTranslation = value.translation
                } else if value.translation.height > 0,
                          value.translation.height > abs(value.translation.width) {
                    dragTranslation = CGSize(width: 0, height: value.translation.height)
                } else {
                    dragTranslation = .zero
                }
            }
            .onEnded { value in
                if currentScale > PosterViewerLayout.minimumScale + 0.01 {
                    let nextOffset = PosterViewerLayout.clampedOffset(
                        settledOffset + value.translation,
                        scale: currentScale,
                        contentSize: contentSize,
                        viewport: viewport
                    )
                    withAnimation(interactionAnimation) {
                        settledOffset = nextOffset
                        dragTranslation = .zero
                    }
                } else if PosterViewerLayout.shouldDismiss(
                    translation: value.translation,
                    predictedEndTranslation: value.predictedEndTranslation,
                    scale: currentScale
                ) {
                    onDismiss()
                } else {
                    withAnimation(interactionAnimation) {
                        dragTranslation = .zero
                    }
                }
            }
    }

    private func magnifyGesture(contentSize: CGSize, viewport: CGSize) -> some Gesture {
        MagnifyGesture()
            .onChanged { value in
                magnification = value.magnification
            }
            .onEnded { value in
                let nextScale = PosterViewerLayout.clampedScale(settledScale * value.magnification)
                let nextOffset = PosterViewerLayout.clampedOffset(
                    settledOffset,
                    scale: nextScale,
                    contentSize: contentSize,
                    viewport: viewport
                )
                withAnimation(interactionAnimation) {
                    settledScale = nextScale
                    magnification = 1
                    settledOffset = nextOffset
                    if nextScale <= PosterViewerLayout.minimumScale + 0.01 {
                        settledOffset = .zero
                    }
                    dragTranslation = .zero
                }
            }
    }

    private func toggleZoom(contentSize: CGSize, viewport: CGSize) {
        let nextScale: CGFloat = currentScale > PosterViewerLayout.minimumScale + 0.01 ? 1 : 2.5
        setZoom(nextScale, contentSize: contentSize, viewport: viewport)
    }

    private func accessibilityZoom(
        _ direction: AccessibilityZoomGestureAction.Direction,
        contentSize: CGSize,
        viewport: CGSize
    ) {
        let nextScale: CGFloat
        switch direction {
        case .zoomIn:
            nextScale = currentScale + 1
        case .zoomOut:
            nextScale = currentScale - 1
        }
        setZoom(nextScale, contentSize: contentSize, viewport: viewport)
    }

    private func setZoom(_ scale: CGFloat, contentSize: CGSize, viewport: CGSize) {
        let nextScale = PosterViewerLayout.clampedScale(scale)
        let nextOffset = PosterViewerLayout.clampedOffset(
            settledOffset,
            scale: nextScale,
            contentSize: contentSize,
            viewport: viewport
        )
        withAnimation(interactionAnimation) {
            settledScale = nextScale
            magnification = 1
            settledOffset = nextScale <= PosterViewerLayout.minimumScale + 0.01 ? .zero : nextOffset
            dragTranslation = .zero
        }
    }

    private func dismissFromButton() {
        guard currentScale > PosterViewerLayout.minimumScale + 0.01 || settledOffset != .zero else {
            onDismiss()
            return
        }

        withAnimation(interactionAnimation) {
            settledScale = PosterViewerLayout.minimumScale
            magnification = 1
            settledOffset = .zero
            dragTranslation = .zero
        }

        guard !reduceMotion else {
            onDismiss()
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.14, execute: onDismiss)
    }

    private var interactionAnimation: Animation? {
        reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.86)
    }
}

private func + (lhs: CGSize, rhs: CGSize) -> CGSize {
    CGSize(width: lhs.width + rhs.width, height: lhs.height + rhs.height)
}
