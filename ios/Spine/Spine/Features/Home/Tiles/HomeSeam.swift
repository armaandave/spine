import SwiftUI

/// The seam: a thin glowing T. Its line runs down the middle of the closed row, between In progress and Trending, and
/// its crossbar runs across the row's top to both screen edges. Idle it breathes. While a tile is open the line glides
/// to the edge that tile's posters came from, runs longer and softer, and the crossbar fades; both come back when the
/// tile closes.
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
        let margin = HomeDesign.Metric.pageMargin
        // Reduce Motion holds it still.
        let breath = reduceMotion || breathing ? 1 : SeamTexture.lowOpacity
        let breathAnimation: Animation? = reduceMotion
            ? nil : .easeInOut(duration: SeamTexture.breathDuration).repeatForever(autoreverses: true)
        ZStack(alignment: .topLeading) {
            SeamTexture()
                .opacity(breath)
                .animation(breathAnimation, value: breathing)
                .scaleEffect(y: scaleY, anchor: .top)
                .offset(
                    x: TileGeometry.seamLeft(open: open?.index, contentWidth: contentWidth) - pad,
                    y: top - pad * scaleY
                )
                .opacity(isOpen ? TileGeometry.seamOpenOpacity : 1)
            // The T's crossbar: across the top of the closed row from screen edge to screen edge. It fades while a
            // tile is open, as the line glides to the edge.
            SeamBar(width: contentWidth + 2 * margin)
                .opacity(breath)
                .animation(breathAnimation, value: breathing)
                .offset(x: -margin - pad, y: TileGeometry.seamRestTop - pad)
                .opacity(isOpen ? 0 : 1)
        }
            // Takes no room: the crossbar is wider than the section, and the section measures its own width to lay
            // out the row, so a seam that sized it would widen the row forever (Home froze on the launch screen).
            .frame(width: 0, height: 0, alignment: .topLeading)
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

/// The crossbar of the T: the line's white and glow, one point tall and as wide as the screen. Drawn once into a
/// texture, like the line.
private struct SeamBar: View {
    let width: CGFloat

    var body: some View {
        Rectangle()
            .fill(.white.opacity(0.95))
            .frame(width: width, height: TileGeometry.seamWidth)
            .shadow(color: .white.opacity(0.5), radius: 3)
            .shadow(color: .white.opacity(0.28), radius: 8)
            .padding(SeamTexture.padding)
            .drawingGroup()
    }
}

/// The line: white, solid where it meets the crossbar at the top and fading out at the bottom, with two white glows
/// round it. Drawn once, with room round it for the glow, into a texture.
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
                    .init(color: .white.opacity(0.95), location: 0),
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
