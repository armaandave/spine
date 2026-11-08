import SwiftUI

/// The slots of a rolling number: five places and a comma after the thousands. A pure function of the number being
/// shown `x` (fractional only while a swipe or an animation is between two values) and how much of it exists
/// (`shown`, 0 to 1), so a digit grows in as the number passes 10, 100, 1,000 and the neighbors slide to make room.
nonisolated struct OdometerSlots: Equatable {
    struct Slot: Equatable {
        /// 0 is the ones place.
        var place: Int
        var width: Double
        /// How much of the slot exists, 0 to 1: also its opacity.
        var opacity: Double
        /// Which glyph is centered: 7.5 is halfway from a 7 to an 8.
        var roll: Double
    }

    static let places = 5
    /// The most a number shows (five places).
    static let maxValue = 99_999.0

    /// Places 4 down to 0, left to right.
    var slots: [Slot]
    /// The comma between the thousands and the hundreds: 0 to its full width.
    var commaWidth: Double
    var width: Double

    static func make(x rawX: Double, shown: Double, digitWidth: Double, commaWidth: Double) -> OdometerSlots {
        let x = min(max(rawX, 0), maxValue)
        let shown = min(max(shown, 0), 1)
        var slots: [Slot] = []
        var comma = 0.0
        for place in stride(from: places - 1, through: 0, by: -1) {
            let power = pow(10, Double(place))
            let f = place == 0 ? shown : min(shown, min(max(x - (power - 1), 0), 1))
            let roll: Double
            if place == 0 {
                roll = x.truncatingRemainder(dividingBy: 10)
            } else {
                let whole = (x / power).rounded(.down).truncatingRemainder(dividingBy: 10)
                let below = (x / (power / 10)).truncatingRemainder(dividingBy: 10)
                roll = whole + min(max(below - 9, 0), 1)
            }
            slots.append(Slot(place: place, width: digitWidth * f, opacity: f, roll: roll))
            if place == 3 { comma = commaWidth * min(shown, min(max(x - 999, 0), 1)) }
        }
        let width = slots.reduce(comma) { $0 + $1.width }
        return OdometerSlots(slots: slots, commaWidth: comma, width: width)
    }
}

/// Draws a rolling number in one Canvas: each visible slot is clipped to its box and shows the glyph for its roll
/// and the next one rising behind it, each with an alpha that falls off toward the box's edge (a per-glyph alpha
/// where the board has a vertical mask: masks are offscreen passes).
struct OdometerView: View {
    let slots: OdometerSlots
    let height: Double

    static let rowHeight = InProgressPanelMetrics.titleRowHeight

    var body: some View {
        let slots = slots
        Canvas { context, size in
            Self.draw(slots, in: context, size: size)
        } symbols: {
            ForEach(0 ..< 10, id: \.self) { digit in
                glyph(String(digit)).tag(digit)
            }
            glyph(",").tag(10)
        }
        .frame(width: slots.width, height: height)
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }

    private func glyph(_ text: String) -> some View {
        Text(text)
            .font(InProgressPanelMetrics.whereFont)
            .foregroundStyle(TileStyle.panelSecondary)
            .fixedSize()
    }

    /// How opaque a glyph centered `distance` from the middle of the box is: solid in the middle third, a third
    /// left at the edge.
    nonisolated static func edge(distance: Double) -> Double {
        min(max(1 - (distance - 5) / 10, 0), 1)
    }

    nonisolated private static func draw(_ slots: OdometerSlots, in context: GraphicsContext, size: CGSize) {
        let row = Double(size.height)
        var x = 0.0
        for slot in slots.slots {
            if slot.place == 2, slots.commaWidth > 0.01 {
                drawComma(width: slots.commaWidth, at: x, in: context, row: row)
                x += slots.commaWidth
            }
            guard slot.width > 0.01, slot.opacity > 0.004 else { continue }
            var clipped = context
            clipped.clip(to: Path(CGRect(x: x, y: 0, width: slot.width, height: row)))
            let base = slot.roll.rounded(.down)
            for k in [base, base + 1] {
                let y = row / 2 + row * (k - slot.roll)
                let alpha = slot.opacity * edge(distance: abs(y - row / 2))
                guard alpha > 0.004 else { continue }
                let digit = ((Int(k) % 10) + 10) % 10
                guard let glyph = clipped.resolveSymbol(id: digit) else { continue }
                var layer = clipped
                layer.opacity = alpha
                layer.draw(glyph, at: CGPoint(x: x + slot.width / 2, y: y), anchor: .center)
            }
            x += slot.width
        }
    }

    nonisolated private static func drawComma(width: Double, at x: Double, in context: GraphicsContext, row: Double) {
        guard let glyph = context.resolveSymbol(id: 10) else { return }
        var layer = context
        layer.clip(to: Path(CGRect(x: x, y: 0, width: width, height: row)))
        layer.draw(glyph, at: CGPoint(x: x + width / 2, y: row / 2), anchor: .center)
    }
}
