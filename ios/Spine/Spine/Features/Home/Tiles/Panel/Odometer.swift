import SwiftUI

/// The right-hand block of the title row ("S2 · 7 of 10 episodes") changing from one place to another: the pure part.
///
/// The whole block dissolves in place from `from` to `to`. Nothing counts through the values in between (a jump from 7
/// to 10 never shows 8 or 9): each of the five segments (prefix, number, joiner, total, suffix) either is the same on
/// both sides, and then it stays solid and only slides to its new place, or it differs, and then the old one fades out
/// while the new one fades in. The block is right-aligned, so each segment's place is its distance from the right
/// edge, which blends with the progress and keeps the row from jumping as its width changes.
nonisolated struct PlaceDissolveLayout: Equatable {
    struct Segment: Equatable {
        var from: String
        var to: String
        var fromWidth: Double
        var toWidth: Double
        /// From the block's right edge to the segment's left edge, in the old block and in the new one.
        var fromEdge: Double
        var toEdge: Double
        /// A number (digits and commas) and not a word: only for the tests and the drawing.
        var isNumber: Bool

        var changes: Bool { from != to }

        func edge(at p: Double) -> Double { fromEdge + (toEdge - fromEdge) * p }
    }

    /// Prefix, number, joiner, total, suffix.
    var segments: [Segment]
    var fromWidth: Double
    var toWidth: Double

    /// The most a number shows (five places).
    static let maxValue = 99_999

    /// The block's width at progress `p` (0 is all `from`, 1 is all `to`).
    func width(at p: Double) -> Double { fromWidth + (toWidth - fromWidth) * p }

    static func make(
        from: TilePlace,
        to: TilePlace,
        digitWidth: Double = PanelTextMetrics.digitWidth,
        commaWidth: Double = PanelTextMetrics.commaWidth,
        wordWidth: (String) -> Double = { PanelTextMetrics.width($0, font: InProgressPanelMetrics.whereUIFont) }
    ) -> PlaceDissolveLayout {
        func parts(_ place: TilePlace) -> [(text: String, width: Double, isNumber: Bool)] {
            func number(_ value: Int?) -> (text: String, width: Double, isNumber: Bool) {
                guard let value else { return ("", 0, true) }
                let text = InProgressTilePresenter.grouped(min(max(value, 0), maxValue))
                let width = text.reduce(0.0) { $0 + ($1 == "," ? commaWidth : digitWidth) }
                return (text, width, true)
            }
            func word(_ text: String) -> (text: String, width: Double, isNumber: Bool) {
                (text, text.isEmpty ? 0 : wordWidth(text), false)
            }
            return [word(place.prefix), number(place.value), word(place.joiner), number(place.total), word(place.suffix)]
        }
        let old = parts(from)
        let new = parts(to)
        let oldTotal = old.reduce(0) { $0 + $1.width }
        let newTotal = new.reduce(0) { $0 + $1.width }
        var segments: [Segment] = []
        var oldOffset = 0.0
        var newOffset = 0.0
        for index in old.indices {
            segments.append(Segment(
                from: old[index].text,
                to: new[index].text,
                fromWidth: old[index].width,
                toWidth: new[index].width,
                fromEdge: oldTotal - oldOffset,
                toEdge: newTotal - newOffset,
                isNumber: old[index].isNumber
            ))
            oldOffset += old[index].width
            newOffset += new[index].width
        }
        return PlaceDissolveLayout(segments: segments, fromWidth: oldTotal, toWidth: newTotal)
    }

    // MARK: Progress

    /// The dissolve's progress for a swipe that is `t` of the way from one title to the next. The ends rest a little,
    /// so a title sitting under a finger that barely moved stays crisp, and the middle is eased.
    static func scrub(_ t: Double) -> Double {
        let x = min(max((t - 0.08) / 0.84, 0), 1)
        return x * x * (3 - 2 * x)
    }

    /// How much of the old and of the new block show at progress `p`, and how blurred and how big each is. The old
    /// one breathes out (a touch bigger and softer), the new one breathes in (from 0.96 to 1).
    struct Look: Equatable {
        var oldAlpha: Double
        var newAlpha: Double
        var oldBlur: Double
        var newBlur: Double
        var oldScale: Double
        var newScale: Double
    }

    /// The most a block is blurred, in points, at the middle of the change.
    static let maxBlur = 2.6

    static func look(progress p: Double, reduceMotion: Bool) -> Look {
        let p = min(max(p, 0), 1)
        if reduceMotion {
            return Look(oldAlpha: 1 - p, newAlpha: p, oldBlur: 0, newBlur: 0, oldScale: 1, newScale: 1)
        }
        return Look(
            oldAlpha: 1 - p,
            newAlpha: p,
            oldBlur: maxBlur * p,
            newBlur: maxBlur * (1 - p),
            oldScale: 1 + 0.04 * p,
            newScale: 0.96 + 0.04 * p
        )
    }
}

/// The block drawn in one Canvas at progress `progress`. It is `Animatable` on the progress alone, so a change that is
/// animated from outside (`withAnimation`) plays here frame by frame and a swipe drives it by hand. The view's width
/// is the block's width at that progress, plus the gap to the title when there is any block at all; the canvas reaches
/// `pad` past it on every side so a blur is not cut off, and the padding is taken back so layout sees the real size.
struct PlaceDissolve: View, Animatable {
    var from: TilePlace
    var to: TilePlace
    var progress: Double

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }

    private static let pad = 6.0

    var body: some View {
        let p = min(max(progress, 0), 1)
        let layout = PlaceDissolveLayout.make(from: from, to: to)
        let blockWidth = layout.width(at: p)
        let gap = InProgressPanelMetrics.titleGap * min(max(blockWidth / 10, 0), 1)
        let look = PlaceDissolveLayout.look(progress: p, reduceMotion: reduceMotion)
        let pad = Self.pad
        Canvas { context, size in
            Self.draw(layout, progress: p, look: look, in: context, size: size, pad: pad)
        }
        .frame(width: blockWidth + gap + 2 * pad, height: InProgressPanelMetrics.titleRowHeight + 2 * pad)
        .padding(-pad)
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }

    nonisolated private static func text(_ string: String) -> Text {
        Text(string)
            .font(InProgressPanelMetrics.whereFont)
            .foregroundStyle(TileStyle.panelSecondary)
    }

    nonisolated private static func draw(
        _ layout: PlaceDissolveLayout,
        progress p: Double,
        look: PlaceDissolveLayout.Look,
        in context: GraphicsContext,
        size: CGSize,
        pad: Double
    ) {
        let right = Double(size.width) - pad
        let midY = pad + InProgressPanelMetrics.titleRowHeight / 2 + InProgressPanelMetrics.whereOffsetY
        for segment in layout.segments {
            let left = right - segment.edge(at: p)
            if !segment.changes {
                guard !segment.to.isEmpty else { continue }
                context.draw(context.resolve(text(segment.to)), at: CGPoint(x: left, y: midY), anchor: .leading)
                continue
            }
            let sides = [
                (string: segment.from, width: segment.fromWidth, alpha: look.oldAlpha, blur: look.oldBlur, scale: look.oldScale),
                (string: segment.to, width: segment.toWidth, alpha: look.newAlpha, blur: look.newBlur, scale: look.newScale),
            ]
            for side in sides {
                guard !side.string.isEmpty, side.alpha > 0.004 else { continue }
                var drawing = context
                drawing.opacity = side.alpha
                if side.blur > 0.3 { drawing.addFilter(.blur(radius: side.blur)) }
                // Scale about the segment's own middle, so it breathes in place.
                drawing.translateBy(x: left + side.width / 2, y: midY)
                if side.scale != 1 { drawing.scaleBy(x: side.scale, y: side.scale) }
                drawing.draw(drawing.resolve(text(side.string)), at: CGPoint(x: -side.width / 2, y: 0), anchor: .leading)
            }
        }
    }
}

/// The two places a block can show and which of them does, for a change that plays on its own. A change writes the new
/// place into the slot that is not showing and flips the flag; the flag is the progress of the dissolve from slot `a`
/// (flag false, progress 0) to slot `b` (flag true, progress 1), so flipping it back is the same dissolve the other way
/// round. A view body only ever sees the new place, and this keeps the old one for as long as the dissolve needs it.
nonisolated struct PlaceSlots: Equatable {
    /// The title the slots were made for.
    var id: String
    var a: TilePlace
    var b: TilePlace
    var flag: Bool

    /// A swipe is between two titles when it is more than this far from the first.
    static let swipeEpsilon = 0.0005

    init(id: String, place: TilePlace) {
        self.id = id
        a = place
        b = place
        flag = false
    }

    /// What to draw: the swipe's pair while a finger is between two titles, else the slots. A title other than the one
    /// the slots were made for (a swipe has just ended on it) is drawn as it is, with no dissolve.
    func shown(id: String, lo: TilePlace, hi: TilePlace, t: Double) -> (from: TilePlace, to: TilePlace, progress: Double) {
        if t > Self.swipeEpsilon { return (lo, hi, PlaceDissolveLayout.scrub(t)) }
        if self.id != id { return (lo, lo, 1) }
        return (a, b, flag ? 1 : 0)
    }

    /// The slots after title `id` came to show `place`, and whether to animate the step. A different title (a swipe
    /// has already dissolved to it), or a change under a finger, resets them to the place with no second dissolve.
    func after(id: String, place: TilePlace, swiping: Bool) -> (slots: PlaceSlots, animated: Bool) {
        if id != self.id || swiping { return (PlaceSlots(id: id, place: place), false) }
        var next = self
        if flag { next.a = place } else { next.b = place }
        next.flag.toggle()
        return (next, true)
    }
}

/// The right-hand block of the title row: it follows the swipe between two titles, and changes on its own (a tap on
/// `+N`, a reload) as a 0.56 s ease-in-out dissolve from the old place to the new one (`PlaceSlots` keeps the old).
struct PlaceChange: View {
    let id: String
    let lo: TilePlace
    let hi: TilePlace
    /// How far a swipe is from the title `lo` to the title `hi` (0 at rest).
    let t: Double

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private struct Key: Equatable {
        var id: String
        var place: TilePlace
    }

    @State private var slots: PlaceSlots

    init(id: String, lo: TilePlace, hi: TilePlace, t: Double) {
        self.id = id
        self.lo = lo
        self.hi = hi
        self.t = t
        _slots = State(initialValue: PlaceSlots(id: id, place: lo))
    }

    var body: some View {
        let shown = slots.shown(id: id, lo: lo, hi: hi, t: t)
        PlaceDissolve(from: shown.from, to: shown.to, progress: shown.progress)
            .onChange(of: Key(id: id, place: lo)) { _, new in
                let step = slots.after(id: new.id, place: new.place, swiping: t > PlaceSlots.swipeEpsilon)
                if step.animated {
                    withAnimation(PanelMotion.numberChange(reduceMotion: reduceMotion)) { slots = step.slots }
                } else {
                    var transaction = Transaction()
                    transaction.disablesAnimations = true
                    withTransaction(transaction) { slots = step.slots }
                }
            }
    }
}
