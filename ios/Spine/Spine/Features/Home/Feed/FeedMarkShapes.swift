import SwiftUI

/// The two marks of a feed log card, drawn as "duotone" marks: a bright rim around a see-through tinted body. Both are
/// designed in a 24 x 24 box and scaled to the size they are drawn at.
nonisolated enum FeedMark: Equatable, Hashable, Sendable {
    case heart
    case rewatch

    /// The rim's width in the 24 box: the heart 1.9, the rewatch 1.5.
    var strokeWidth: Double {
        switch self {
        case .heart: 1.9
        case .rewatch: 1.5
        }
    }

    var cacheName: String {
        switch self {
        case .heart: "heart"
        case .rewatch: "rewatch"
        }
    }

    /// The outline in `rect` (square; scaled by its width over 24).
    func path(in rect: CGRect) -> Path {
        switch self {
        case .heart: HeartShape().path(in: rect)
        case .rewatch: RewatchShape().path(in: rect)
        }
    }
}

/// The board's heart, in the 24 box. Its bounds are (3, 4.4, 18 x 16).
nonisolated struct HeartShape: Shape {
    func path(in rect: CGRect) -> Path {
        let k = rect.width / 24
        func at(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: rect.minX + CGFloat(x) * k, y: rect.minY + CGFloat(y) * k)
        }
        var path = Path()
        path.move(to: at(12, 20.4))
        path.addCurve(to: at(10.75, 19.94), control1: at(11.55, 20.4), control2: at(11.1, 20.24))
        path.addCurve(to: at(3, 9.5), control1: at(6.2, 16.1), control2: at(3, 13.2))
        path.addCurve(to: at(7.9, 4.4), control1: at(3, 6.5), control2: at(5.2, 4.4))
        path.addCurve(to: at(12, 6.65), control1: at(9.55, 4.4), control2: at(11.1, 5.25))
        path.addCurve(to: at(16.1, 4.4), control1: at(12.9, 5.25), control2: at(14.45, 4.4))
        path.addCurve(to: at(21, 9.5), control1: at(18.8, 4.4), control2: at(21, 6.5))
        path.addCurve(to: at(13.25, 19.94), control1: at(21, 13.2), control2: at(17.8, 16.1))
        path.addCurve(to: at(12, 20.4), control1: at(12.9, 20.24), control2: at(12.45, 20.4))
        path.closeSubpath()
        return path
    }
}

/// The board's rewatch mark: one stroke that thickens from a hairline tail to a heavy head, ending in a solid
/// arrowhead. The numbers are the design.
nonisolated struct RewatchShape: Shape {
    func path(in rect: CGRect) -> Path {
        let k = Double(rect.width) / 24
        let cx = 12.0, cy = 12.0, r = 8.2
        let a0 = -72.0, a1 = 212.0, steps = 30
        func rad(_ degrees: Double) -> Double { degrees * .pi / 180 }
        func pt(_ angle: Double, _ radius: Double) -> CGPoint {
            CGPoint(
                x: rect.minX + CGFloat((cx + radius * cos(rad(angle))) * k),
                y: rect.minY + CGFloat((cy + radius * sin(rad(angle))) * k)
            )
        }
        // The stroke's width along the arc.
        func width(_ t: Double) -> Double { 0.7 + 2.9 * pow(t, 1.1) }
        var outer: [CGPoint] = []
        var inner: [CGPoint] = []
        for i in 0 ... steps {
            let t = Double(i) / Double(steps)
            let angle = a0 + (a1 - a0) * t
            outer.append(pt(angle, r + width(t) / 2))
            inner.append(pt(angle, r - width(t) / 2))
        }
        var path = Path()
        path.addLines(outer)
        path.addLines(inner.reversed())
        path.closeSubpath()

        // The arrowhead at the end of the arc.
        let end = (x: cx + r * cos(rad(a1)), y: cy + r * sin(rad(a1)))
        let tangent = (x: -sin(rad(a1)), y: cos(rad(a1)))
        let normal = (x: cos(rad(a1)), y: sin(rad(a1)))
        func at(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: rect.minX + CGFloat(x * k), y: rect.minY + CGFloat(y * k))
        }
        let tip = at(end.x + tangent.x * 5.2, end.y + tangent.y * 5.2)
        let b1 = at(end.x + normal.x * 4 - tangent.x * 0.8, end.y + normal.y * 4 - tangent.y * 0.8)
        let b2 = at(end.x - normal.x * 4 - tangent.x * 0.8, end.y - normal.y * 4 - tangent.y * 0.8)
        path.move(to: b1)
        path.addLine(to: tip)
        path.addLine(to: b2)
        path.closeSubpath()
        return path
    }
}

/// The colors of a mark on each ink. `glow` tints the body and the halo, `deep` is the light ink's contact shadow.
nonisolated struct MarkPalette: Equatable {
    let hi: ArtHex.RGB
    let mid: ArtHex.RGB
    let lo: ArtHex.RGB
    let glow: ArtHex.RGB
    let deep: ArtHex.RGB
    /// The body's opacity at its centre and at its rim.
    let bodyCenterAlpha: Double
    let bodyRimAlpha: Double

    private static func rgb(_ hex: UInt32) -> ArtHex.RGB {
        ArtHex.RGB(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    private static func rgb(_ red: Double, _ green: Double, _ blue: Double) -> ArtHex.RGB {
        ArtHex.RGB(red: red / 255, green: green / 255, blue: blue / 255)
    }

    static func palette(for mark: FeedMark, ink: ArtInk) -> MarkPalette {
        switch (mark, ink) {
        case (.heart, .dark):
            MarkPalette(
                hi: rgb(0xFFA3B8), mid: rgb(0xFF4F7F), lo: rgb(0xE01E56),
                glow: rgb(255, 76, 120), deep: rgb(110, 0, 40), bodyCenterAlpha: 0.52, bodyRimAlpha: 0.16
            )
        case (.heart, .light):
            MarkPalette(
                hi: rgb(0xFF5F95), mid: rgb(0xE0145E), lo: rgb(0xA3093F),
                glow: rgb(200, 20, 80), deep: rgb(90, 0, 30), bodyCenterAlpha: 0.62, bodyRimAlpha: 0.30
            )
        case (.rewatch, .dark):
            MarkPalette(
                hi: rgb(0xB8FBD0), mid: rgb(0x34D07A), lo: rgb(0x0E9E4B),
                glow: rgb(52, 209, 122), deep: rgb(0, 70, 30), bodyCenterAlpha: 0.52, bodyRimAlpha: 0.16
            )
        case (.rewatch, .light):
            MarkPalette(
                hi: rgb(0x2DBE72), mid: rgb(0x0C8F47), lo: rgb(0x055E2D),
                glow: rgb(10, 130, 60), deep: rgb(0, 50, 20), bodyCenterAlpha: 0.62, bodyRimAlpha: 0.30
            )
        }
    }

    static func color(_ rgb: ArtHex.RGB, opacity: Double = 1) -> Color {
        Color(red: rgb.red, green: rgb.green, blue: rgb.blue).opacity(opacity)
    }
}

/// The board's SVG gradients are set against the shape's bounding box. These are the same points as fractions of the
/// 24 box, which is what a SwiftUI gradient on a shape drawn in a square frame uses.
nonisolated struct MarkGradientUnits: Equatable {
    /// The body's radial gradient: its center, and its end radius as a fraction of the drawn size.
    let center: UnitPoint
    let radiusFraction: Double
    /// The rim's linear gradient.
    let lineStart: UnitPoint
    let lineEnd: UnitPoint

    static func units(for mark: FeedMark) -> MarkGradientUnits {
        let box = mark.path(in: CGRect(x: 0, y: 0, width: 24, height: 24)).boundingRect
        func x(_ fraction: Double) -> Double { (Double(box.minX) + fraction * Double(box.width)) / 24 }
        func y(_ fraction: Double) -> Double { (Double(box.minY) + fraction * Double(box.height)) / 24 }
        return MarkGradientUnits(
            center: UnitPoint(x: x(0.5), y: y(0.38)),
            radiusFraction: 0.62 * Double(box.width) / 24,
            lineStart: UnitPoint(x: x(0.2), y: Double(box.minY) / 24),
            lineEnd: UnitPoint(x: x(0.7), y: Double(box.maxY) / 24)
        )
    }
}

/// A mark drawn live, `size` points square: a radial tinted body under a gradient rim. No shadow; the glow is baked
/// into the images `FeedMarkImages` makes, and this is what a card draws when an image is not there.
struct DuotoneMark: View {
    let mark: FeedMark
    let ink: ArtInk
    let size: CGFloat

    var body: some View {
        let palette = MarkPalette.palette(for: mark, ink: ink)
        let units = MarkGradientUnits.units(for: mark)
        let shape = MarkShape(mark: mark)
        ZStack {
            shape.fill(RadialGradient(
                colors: [
                    MarkPalette.color(palette.glow, opacity: palette.bodyCenterAlpha),
                    MarkPalette.color(palette.glow, opacity: palette.bodyRimAlpha),
                ],
                center: units.center,
                startRadius: 0,
                endRadius: CGFloat(units.radiusFraction) * size
            ))
            shape.stroke(
                LinearGradient(
                    stops: [
                        .init(color: MarkPalette.color(palette.hi), location: 0),
                        .init(color: MarkPalette.color(palette.mid), location: 0.55),
                        .init(color: MarkPalette.color(palette.lo), location: 1),
                    ],
                    startPoint: units.lineStart,
                    endPoint: units.lineEnd
                ),
                style: StrokeStyle(lineWidth: CGFloat(mark.strokeWidth) * size / 24, lineCap: .round, lineJoin: .round)
            )
        }
        .frame(width: size, height: size)
    }
}

/// `FeedMark` as a `Shape`.
nonisolated struct MarkShape: Shape {
    let mark: FeedMark

    func path(in rect: CGRect) -> Path {
        mark.path(in: rect)
    }
}
