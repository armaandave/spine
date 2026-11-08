import SwiftUI

/// The slots of a changing number: five places and a comma after the thousands. A pure function of the number being
/// shown `x` (fractional only while a swipe or an animation is between two values) and how much of it exists
/// (`shown`, 0 to 1), so a digit grows in as the number passes 10, 100, 1,000 and the neighbors slide to make room.
nonisolated struct OdometerSlots: Equatable {
    struct Slot: Equatable {
        /// 0 is the ones place.
        var place: Int
        var width: Double
        /// How much of the slot exists, 0 to 1: also its opacity.
        var opacity: Double
        /// Which glyph is showing: 7.5 is halfway from a 7 to an 8 (see `OdometerSlots.fade(roll:)`).
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

extension OdometerSlots {
    /// A slot's glyph at `roll` (7.5 is halfway from a 7 to an 8) as an in-place cross-fade: the digit it is on, the
    /// next one, and how much of the next is showing. Nothing travels: both sit in the middle of the slot. The
    /// fade is eased and takes the middle of each step, so a digit rests solid before and after it changes.
    nonisolated struct Fade: Equatable {
        var base: Int
        var next: Int
        /// 0 is all `base`, 1 is all `next`.
        var amount: Double
    }

    /// The share of a step that the digits rest solid at each end.
    nonisolated static let fadeRest = 0.15
    /// The blur a glyph has at the middle of its fade, in points.
    nonisolated static let fadeBlur = 3.0

    nonisolated static func fade(roll: Double) -> Fade {
        let whole = roll.rounded(.down)
        let step = roll - whole
        let t = min(max((step - fadeRest) / (1 - 2 * fadeRest), 0), 1)
        let digit = ((Int(whole) % 10) + 10) % 10
        return Fade(base: digit, next: (digit + 1) % 10, amount: t * t * (3 - 2 * t))
    }
}

/// Draws a changing number in one Canvas: each visible slot shows its digit and the next one cross-faded in place,
/// each a little blurred while it comes or goes. No glyph moves up or down, so a number only dissolves into the
/// next. The canvas reaches `pad` past the number on every side so a blur is not cut off; the padding is taken back
/// so layout sees the number's own size.
struct OdometerView: View {
    let slots: OdometerSlots
    let height: Double

    static let rowHeight = InProgressPanelMetrics.titleRowHeight
    private static let pad = 6.0

    var body: some View {
        let slots = slots
        let pad = Self.pad
        Canvas { context, size in
            Self.draw(slots, in: context, size: size, pad: pad)
        } symbols: {
            ForEach(0 ..< 10, id: \.self) { digit in
                glyph(String(digit)).tag(digit)
            }
            glyph(",").tag(10)
        }
        .frame(width: slots.width + 2 * pad, height: height + 2 * pad)
        .padding(-pad)
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }

    private func glyph(_ text: String) -> some View {
        Text(text)
            .font(InProgressPanelMetrics.whereFont)
            .foregroundStyle(TileStyle.panelSecondary)
            .fixedSize()
    }

    nonisolated private static func draw(_ slots: OdometerSlots, in context: GraphicsContext, size: CGSize, pad: Double) {
        let row = Double(size.height) - 2 * pad
        var x = pad
        for slot in slots.slots {
            if slot.place == 2, slots.commaWidth > 0.01 {
                drawComma(width: slots.commaWidth, at: x, in: context, row: row, pad: pad)
                x += slots.commaWidth
            }
            guard slot.width > 0.01, slot.opacity > 0.004 else { continue }
            let fade = OdometerSlots.fade(roll: slot.roll)
            let layers = [
                (digit: fade.base, share: 1 - fade.amount, blur: OdometerSlots.fadeBlur * fade.amount),
                (digit: fade.next, share: fade.amount, blur: OdometerSlots.fadeBlur * (1 - fade.amount)),
            ]
            for layer in layers {
                let alpha = slot.opacity * layer.share
                guard alpha > 0.004, let glyph = context.resolveSymbol(id: layer.digit) else { continue }
                var drawing = context
                drawing.opacity = alpha
                if layer.blur > 0.25 { drawing.addFilter(.blur(radius: layer.blur)) }
                drawing.draw(glyph, at: CGPoint(x: x + slot.width / 2, y: pad + row / 2), anchor: .center)
            }
            x += slot.width
        }
    }

    nonisolated private static func drawComma(
        width: Double, at x: Double, in context: GraphicsContext, row: Double, pad: Double
    ) {
        guard let glyph = context.resolveSymbol(id: 10) else { return }
        var layer = context
        layer.clip(to: Path(CGRect(x: x, y: pad, width: width, height: row)))
        layer.draw(glyph, at: CGPoint(x: x + width / 2, y: pad + row / 2), anchor: .center)
    }
}
