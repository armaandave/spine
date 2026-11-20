import SwiftUI

/// The seam: a thin glowing T. Its crossbar runs just under the header, fading out towards both screen edges, and
/// curves down into its line, which runs down the middle of the closed row, between In progress and Trending. Idle it breathes. While a tile is open the line glides
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
            // The T's crossbar: just under the header, from screen edge to screen edge, curving down into the line's
            // top. It fades while a tile is open, as the line glides to the edge.
            SeamBar(width: contentWidth + 2 * margin)
                .opacity(breath)
                .animation(breathAnimation, value: breathing)
                .offset(x: -margin - pad, y: TileGeometry.seamBarTop - pad)
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

/// The crossbar of the T: as wide as the screen, brightest in the middle and fading out towards both edges, curving
/// down into the line's top on both sides of the middle. Its glow is the line's, so where the two meet nothing
/// changes but the direction. Drawn once into a texture, like the line.
private struct SeamBar: View {
    let width: CGFloat

    private static let gradient: [Gradient.Stop] = {
        let half = TileGeometry.seamBarStops
        let left = half.map { Gradient.Stop(color: .white.opacity($0.opacity), location: $0.location) }
        let right = half.dropLast().reversed().map {
            Gradient.Stop(color: .white.opacity($0.opacity), location: 1 - $0.location)
        }
        return left + right
    }()

    var body: some View {
        let radius = TileGeometry.seamCornerRadius
        SeamBarShape(radius: radius)
            .stroke(
                LinearGradient(stops: Self.gradient, startPoint: .leading, endPoint: .trailing),
                lineWidth: TileGeometry.seamWidth
            )
            .frame(width: width, height: radius + TileGeometry.seamWidth)
            .shadow(color: .white.opacity(0.5), radius: 3)
            .shadow(color: .white.opacity(0.28), radius: 8)
            .padding(SeamTexture.padding)
            .drawingGroup()
    }
}

/// From each screen edge along the top, then a quarter turn down into the middle, where the line starts.
private struct SeamBarShape: Shape {
    let radius: CGFloat

    func path(in rect: CGRect) -> Path {
        let y = rect.minY + TileGeometry.seamWidth / 2
        let mid = rect.midX
        let end = CGPoint(x: mid, y: y + radius)
        // A quarter circle as a cubic: the control points sit this share of the radius along each tangent.
        let k = radius * 0.5523
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: y))
        path.addLine(to: CGPoint(x: mid - radius, y: y))
        path.addCurve(to: end, control1: CGPoint(x: mid - radius + k, y: y), control2: CGPoint(x: mid, y: end.y - k))
        path.move(to: CGPoint(x: rect.maxX, y: y))
        path.addLine(to: CGPoint(x: mid + radius, y: y))
        path.addCurve(to: end, control1: CGPoint(x: mid + radius - k, y: y), control2: CGPoint(x: mid, y: end.y - k))
        return path
    }
}

/// The line: white, starting at the crossbar's own brightness where the two meet, brightening over its first stretch,
/// and fading out at the bottom, with two white glows round it. Drawn once, with room round it for the glow, into a
/// texture.
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
                    .init(color: .white.opacity(TileGeometry.seamBarStops.last?.opacity ?? 0.95), location: 0),
                    .init(color: .white.opacity(0.95), location: 0.12),
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
