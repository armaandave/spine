import SwiftUI

/// Soft light in the profile picture's main color, falling from Home's profile picture to the right and off the
/// screen like a desk lamp's: one narrow cone with soft edges, brightest by the picture and fading as it goes, faint
/// enough to see through. Give it the picture's frame; the light draws outside it. Nothing shows until the picture's
/// colors are counted, or when it has none (no picture, or only black and white).
struct AvatarBeam: View {
    let avatarURL: String?
    let diameter: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var color: Color?

    var body: some View {
        Color.clear
            .frame(width: diameter, height: diameter)
            .overlay {
                if let color {
                    Canvas { context, size in
                        AvatarLight.draw(in: &context, size: size, color: color, diameter: diameter)
                    }
                    .frame(width: AvatarLight.length * 2, height: AvatarLight.length * 2)
                    .blendMode(.plusLighter)
                    .transition(.opacity)
                }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
            .task(id: avatarURL) { await countColor() }
    }

    /// The picture's main color, counted as a poster's is for the In progress bar.
    private func countColor() async {
        guard let url = avatarURL.flatMap({ URL(string: $0) }) else {
            color = nil
            return
        }
        let swatches = await PosterPaletteExtractor.load(url: url)
        guard !Task.isCancelled else { return }
        let main = swatches.flatMap { MeterTint.make(from: $0) }?.gradientColors.first
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.6)) { color = main }
    }
}

/// The light, drawn from the canvas's center (the picture's center) toward the right.
nonisolated enum AvatarLight {
    /// How far the light reaches from the picture's center.
    static let length: CGFloat = 140

    /// One cone of the light: how tall it starts at the picture (in diameters), how fast it widens (degrees each
    /// side), how strong it starts and how soft its edges are (in diameters).
    struct Layer {
        let startHeight: CGFloat
        let spreadDegrees: Double
        let opacity: Double
        let softness: CGFloat
    }

    /// A wide faint glow, then a narrower, stronger core inside it: together they read as one soft beam.
    static let layers: [Layer] = [
        Layer(startHeight: 1.0, spreadDegrees: 13, opacity: 0.15, softness: 0.3),
        Layer(startHeight: 0.7, spreadDegrees: 8, opacity: 0.24, softness: 0.16),
    ]

    /// The beam's two edges, lit a little brighter than its inside, as light catches the edge of a lamp's cone.
    static let edge = Layer(startHeight: 0.92, spreadDegrees: 12, opacity: 0.42, softness: 0.03)
    static let edgeWidth: CGFloat = 1.2

    static func draw(in context: inout GraphicsContext, size: CGSize, color: Color, diameter: CGFloat) {
        let origin = CGPoint(x: size.width / 2, y: size.height / 2)
        drawCones(in: &context, origin: origin, color: color, diameter: diameter)
        drawEdges(in: &context, origin: origin, color: color, diameter: diameter)
    }

    private static func drawEdges(in context: inout GraphicsContext, origin: CGPoint, color: Color, diameter: CGFloat) {
        context.drawLayer { layerContext in
            layerContext.addFilter(.blur(radius: max(0.8, diameter * edge.softness)))
            let halfStart = diameter * edge.startHeight / 2
            // The edges fade out a little before the light does.
            let reach = length * 0.8
            let halfEnd = halfStart + reach * CGFloat(tan(edge.spreadDegrees * .pi / 180))
            for side: CGFloat in [-1, 1] {
                var line = Path()
                let start = CGPoint(x: origin.x, y: origin.y + side * halfStart)
                let end = CGPoint(x: origin.x + reach, y: origin.y + side * halfEnd)
                line.move(to: start)
                line.addLine(to: end)
                layerContext.stroke(
                    line,
                    with: .linearGradient(
                        Gradient(stops: [
                            .init(color: color.opacity(edge.opacity), location: 0),
                            .init(color: color.opacity(edge.opacity * 0.5), location: 0.4),
                            .init(color: color.opacity(0), location: 1),
                        ]),
                        startPoint: start,
                        endPoint: end
                    ),
                    lineWidth: edgeWidth
                )
            }
        }
    }

    private static func drawCones(in context: inout GraphicsContext, origin: CGPoint, color: Color, diameter: CGFloat) {
        for layer in layers {
            context.drawLayer { layerContext in
                layerContext.addFilter(.blur(radius: diameter * layer.softness))
                let halfStart = diameter * layer.startHeight / 2
                let halfEnd = halfStart + length * CGFloat(tan(layer.spreadDegrees * .pi / 180))
                var cone = Path()
                cone.move(to: CGPoint(x: origin.x, y: origin.y - halfStart))
                cone.addLine(to: CGPoint(x: origin.x + length, y: origin.y - halfEnd))
                cone.addLine(to: CGPoint(x: origin.x + length, y: origin.y + halfEnd))
                cone.addLine(to: CGPoint(x: origin.x, y: origin.y + halfStart))
                cone.closeSubpath()
                // Brightest by the picture, fading smoothly with distance, like light in the air.
                layerContext.fill(
                    cone,
                    with: .radialGradient(
                        Gradient(stops: [
                            .init(color: color.opacity(layer.opacity), location: 0),
                            .init(color: color.opacity(layer.opacity * 0.55), location: 0.35),
                            .init(color: color.opacity(0), location: 1),
                        ]),
                        center: origin,
                        startRadius: diameter * 0.4,
                        endRadius: length
                    )
                )
            }
        }
    }
}
