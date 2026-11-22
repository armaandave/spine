import SwiftUI

/// How far Library has scrolled. Only the plate and the art at the top read it, so scrolling redraws those and not
/// the grid.
@MainActor
@Observable
final class LibraryScroll {
    var offset: CGFloat = 0

    /// 0 with the plate full, 1 once it has condensed to its slim bar.
    var collapse: CGFloat {
        min(max(offset / LibraryPlateLayout.collapseDistance, 0), 1)
    }
}

enum LibraryPlateLayout {
    static let fullHeight: CGFloat = 130
    static let slimHeight: CGFloat = 52
    /// The page's first row starts this far below the top of the safe area: under the plate, after a gap.
    static let contentTop: CGFloat = fullHeight + 16
    /// The scroll it takes the plate to condense.
    static let collapseDistance: CGFloat = 110
    static let titleHeight: CGFloat = 38
    /// How far the art reaches below the top of the safe area.
    static let artHeight: CGFloat = 300
    /// Past this the art is off the screen and the plate is slim, so the offset stops changing.
    static let scrollCap: CGFloat = artHeight + 100

    static func mix(_ from: CGFloat, _ to: CGFloat, _ amount: CGFloat) -> CGFloat {
        from + (to - from) * amount
    }
}

/// Library's header: a Liquid Glass plate with "Library", the status as the title, the filter, and the type bar.
/// As the page scrolls it condenses to a slim bar that keeps only the title and the filter. The glass takes a hint of
/// the first poster's color.
struct LibraryPlate<Title: View, Trailing: View, Bar: View>: View {
    let scroll: LibraryScroll
    let accentHex: String?
    private let title: Title
    private let trailing: Trailing
    private let bar: Bar

    init(
        scroll: LibraryScroll,
        accentHex: String?,
        @ViewBuilder title: () -> Title,
        @ViewBuilder trailing: () -> Trailing,
        @ViewBuilder bar: () -> Bar
    ) {
        self.scroll = scroll
        self.accentHex = accentHex
        self.title = title()
        self.trailing = trailing()
        self.bar = bar()
    }

    var body: some View {
        let progress = scroll.collapse
        let eased = progress * progress * (3 - 2 * progress)
        // What folds away goes in the first third of the scroll.
        let fold = max(0, 1 - progress * 3)
        let shape = RoundedRectangle(cornerRadius: LibraryPlateLayout.mix(28, 26, eased), style: .continuous)

        ZStack(alignment: .topLeading) {
            Text("Library")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.62))
                .frame(height: 16)
                .padding(.leading, 18)
                .offset(y: 15 - 6 * progress)
                .opacity(fold)
                .accessibilityAddTraits(.isHeader)

            // Full: under "Library". Slim: smaller, centered in the bar.
            title
                .scaleEffect(LibraryPlateLayout.mix(1, 0.62, eased), anchor: .topLeading)
                .padding(.leading, 18)
                .offset(y: LibraryPlateLayout.mix(34, 14, eased))

            // Centered on "Library" and the title, then on the slim bar.
            trailing
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding(.trailing, 12)
                .offset(y: LibraryPlateLayout.mix(26, 9, eased))

            bar
                .padding(.horizontal, 12)
                .offset(y: 84 - 10 * progress)
                .opacity(fold)
                .allowsHitTesting(progress < 0.3)
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(
            height: LibraryPlateLayout.mix(LibraryPlateLayout.fullHeight, LibraryPlateLayout.slimHeight, eased),
            alignment: .top
        )
        .clipShape(shape)
        .glassEffect(glass, in: shape)
        // The page is always dark; light glass would wash out the white text.
        .environment(\.colorScheme, .dark)
    }

    private var glass: Glass {
        guard let rgb = ArtHex.rgb(accentHex) else { return .regular }
        return .regular.tint(Color(red: rgb.red, green: rgb.green, blue: rgb.blue).opacity(0.1))
    }
}

/// The first poster of the status shown, blurred, from under the status bar down past the plate, fading into the
/// black page. It moves up with the page, stretches when the page is pulled down, and cross-fades when the status
/// changes.
struct LibraryBlurredTop: View {
    let url: URL?
    let scroll: LibraryScroll

    var body: some View {
        let offset = scroll.offset
        Color.clear
            .frame(height: LibraryPlateLayout.artHeight + max(-offset, 0))
            .frame(maxWidth: .infinity)
            .background(alignment: .bottom) {
                ZStack {
                    if let url {
                        art(url)
                            .id(url)
                            .transition(.opacity)
                    }
                }
                .ignoresSafeArea(edges: .top)
            }
            .offset(y: -max(offset, 0))
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    /// The Lists page's blurred top: a dark wash, solid down to 55% of its height, then fading out.
    private func art(_ url: URL) -> some View {
        BlurredArtBackground(url: url, accentHex: nil, style: .listTop, baseOpacity: 0)
            .overlay {
                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.4), location: 0),
                        .init(color: .black.opacity(0.18), location: 0.45),
                        .init(color: .black.opacity(0.32), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .mask {
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0),
                        .init(color: .white, location: 0.55),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
    }
}

/// One status in the status menu: its name, and under it how many titles it holds once the server has counted them.
struct LibraryStatusOption: View {
    let title: String
    let detail: String?

    var body: some View {
        Text(title)
        if let detail {
            Text(detail)
        }
    }
}
