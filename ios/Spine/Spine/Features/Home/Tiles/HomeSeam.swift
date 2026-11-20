import SwiftUI

/// The seam: one thin glowing line down the middle of the closed row, between In progress and Trending. Idle it
/// breathes. While a tile is open it glides to the edge that tile's posters came from, runs a little shorter and
/// softer, and glides back to the middle when the tile closes.
///
/// The line and its glow are drawn once into a texture (`SeamTexture`), and everything that moves is a transform or an
/// opacity of that texture, so nothing is drawn again as it breathes or glides. The breath is one repeating animation
/// of an opacity: no body runs for it. It lives in a view of its own, so the section's body never reads any of it.
struct HomeSeam: View {
    let state: HomeTilesState
    let contentWidth: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false

    var body: some View {
        let open = state.open
        let isOpen = open != nil
        let scaleY = isOpen ? TileGeometry.seamOpenHeight / TileGeometry.seamRestHeight : 1
        let top = isOpen ? TileGeometry.seamOpenTop : TileGeometry.seamRestTop
        let pad = SeamTexture.padding
        SeamTexture()
            // Reduce Motion holds it still.
            .opacity(reduceMotion || breathing ? 1 : SeamTexture.lowOpacity)
            .animation(
                reduceMotion ? nil : .easeInOut(duration: SeamTexture.breathDuration).repeatForever(autoreverses: true),
                value: breathing
            )
            .scaleEffect(y: scaleY, anchor: .top)
            .offset(
                x: TileGeometry.seamLeft(open: open?.index, contentWidth: contentWidth) - pad,
                y: top - pad * scaleY
            )
            .opacity(isOpen ? TileGeometry.seamOpenOpacity : 1)
            .animation(TileMotion.frame(closing: !isOpen, reduceMotion: reduceMotion), value: open)
            .onAppear {
                // Restarted on every appearance: a repeating animation does not survive the view leaving the screen.
                var transaction = Transaction()
                transaction.disablesAnimations = true
                withTransaction(transaction) { breathing = false }
                DispatchQueue.main.async { breathing = true }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// The line: white, fading out at both ends, with two white glows round it. Drawn once, with room round it for the
/// glow, into a texture.
private struct SeamTexture: View {
    /// What the texture adds on every side of the line, for the glow.
    static let padding: CGFloat = 14
    /// The breath goes between this and 1, over 3 s each way (6 s round).
    static let lowOpacity: Double = 0.72
    static let breathDuration: Double = 3

    var body: some View {
        Rectangle()
            .fill(LinearGradient(
                stops: [
                    .init(color: .white.opacity(0), location: 0),
                    .init(color: .white.opacity(0.95), location: 0.16),
                    .init(color: .white.opacity(0.95), location: 0.84),
                    .init(color: .white.opacity(0), location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            ))
            .frame(width: TileGeometry.seamWidth, height: TileGeometry.seamRestHeight)
            .shadow(color: .white.opacity(0.5), radius: 3)
            .shadow(color: .white.opacity(0.28), radius: 8)
            .padding(Self.padding)
            .drawingGroup()
    }
}
