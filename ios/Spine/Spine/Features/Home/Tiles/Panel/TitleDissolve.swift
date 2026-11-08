import SwiftUI
import UIKit

// A panel title that changes in place as you swipe between titles, shared by the open In progress tile (left-aligned)
// and the open Trending tile (centered). The title never slides: each letter of the old title blurs and fades out and
// the matching place of the new title fades and sharpens in, letter by letter in the swipe's direction, with a soft
// glint at the front of the sweep. It follows the carousel's scroll position, so it is settled the moment the swipe is.
// All of it is drawn in one Canvas (the letters are resolved and drawn one by one), so there is no offscreen pass of
// its own. Three parts keep the work apart:
//
//   TitleMetrics      where each letter sits in a title, and how a title that is too long is cut short
//   TitleSweep        which letters have changed at a point of the swipe (the plan of the draw)
//   TitleDissolve     the Canvas, and SwipeTitle, the Trending panel's way to feed it from the scroll position

// MARK: - Letters

/// One letter (or, in a script that joins its letters, one word) of a title and where it starts.
nonisolated struct TitleGlyph: Equatable {
    var text: String
    /// From the start of the line.
    var x: Double
    var width: Double
}

/// A title as it is drawn: the letters that fit, with "…" at the end when it was cut short.
nonisolated struct TitleLine: Equatable {
    var glyphs: [TitleGlyph]
    var width: Double

    var text: String { glyphs.map(\.text).joined() }
}

/// Every letter of one title, uncut.
nonisolated struct TitleFullLayout: Equatable {
    var glyphs: [TitleGlyph]
    /// Where the line stands after letter `k`: the width of the first `k + 1` letters.
    var ends: [Double]
    var total: Double
}

nonisolated enum TitleMetrics {
    /// The letters of `text`: one per character, except that a run of letters in a script that joins them (Arabic,
    /// Hebrew, the Indic scripts, Thai) stays together as one word, because a letter drawn alone is the wrong shape.
    static func units(of text: String) -> [String] {
        var result: [String] = []
        var previousJoined = false
        for character in text {
            let joined = character.unicodeScalars.contains { isJoined($0) }
            if joined, previousJoined, !result.isEmpty {
                result[result.count - 1].append(character)
            } else {
                result.append(String(character))
            }
            previousJoined = joined
        }
        return result
    }

    private static func isJoined(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.value {
        case 0x0590 ... 0x0DFF, 0x0E00 ... 0x0EFF, 0xFB1D ... 0xFDFF, 0xFE70 ... 0xFEFF: true
        default: false
        }
    }

    /// Where each letter of `text` starts, given `measure` (the width of a string in the title's font and tracking).
    /// A letter starts where the line up to and including it ends, less its own width: that keeps the pair kerning
    /// between it and the letter before it.
    static func layout(_ text: String, measure: (String) -> Double) -> TitleFullLayout {
        var glyphs: [TitleGlyph] = []
        var ends: [Double] = []
        var prefix = ""
        for unit in units(of: text) {
            prefix += unit
            let end = measure(prefix)
            let width = measure(unit)
            glyphs.append(TitleGlyph(text: unit, x: end - width, width: width))
            ends.append(end)
        }
        return TitleFullLayout(glyphs: glyphs, ends: ends, total: ends.last ?? 0)
    }

    /// The line that fits `available`: all of it, else as many letters as leave room for "…", without a space just
    /// before the dots.
    static func line(_ full: TitleFullLayout, available: Double, ellipsis: TitleGlyph) -> TitleLine {
        if full.total <= available + 0.01 { return TitleLine(glyphs: full.glyphs, width: full.total) }
        let room = available - ellipsis.width
        guard room > 0 else { return TitleLine(glyphs: [], width: 0) }
        var count = 0
        while count < full.glyphs.count, full.ends[count] <= room { count += 1 }
        while count > 0, full.glyphs[count - 1].text.allSatisfy(\.isWhitespace) { count -= 1 }
        let start = count > 0 ? full.ends[count - 1] : 0
        var glyphs = Array(full.glyphs[0 ..< count])
        glyphs.append(TitleGlyph(text: ellipsis.text, x: start, width: ellipsis.width))
        return TitleLine(glyphs: glyphs, width: start + ellipsis.width)
    }

    // MARK: Real fonts

    nonisolated(unsafe) private static let cache = NSCache<NSString, Box>()

    nonisolated private final class Box {
        let layout: TitleFullLayout
        init(_ layout: TitleFullLayout) { self.layout = layout }
    }

    private static func width(_ string: String, font: UIFont, tracking: Double) -> Double {
        (string as NSString).size(withAttributes: [.font: font, .kern: tracking]).width
    }

    /// `text` in `font`, measured once.
    static func layout(_ text: String, font: UIFont, tracking: Double) -> TitleFullLayout {
        let key = "\(font.fontName)|\(font.pointSize)|\(tracking)|\(text)" as NSString
        if let box = cache.object(forKey: key) { return box.layout }
        let made = layout(text) { width($0, font: font, tracking: tracking) }
        cache.setObject(Box(made), forKey: key)
        return made
    }

    static func line(_ text: String, font: UIFont, tracking: Double, available: Double) -> TitleLine {
        let ellipsis = TitleGlyph(text: "\u{2026}", x: 0, width: width("\u{2026}", font: font, tracking: tracking))
        return line(layout(text, font: font, tracking: tracking), available: max(available, 0), ellipsis: ellipsis)
    }
}

// MARK: - The sweep

/// What the sweep draws at one point of a swipe.
nonisolated struct TitleOp: Equatable {
    /// A run of letters that have not changed (or have finished changing), drawn as one piece of text with its own
    /// natural spacing, or one letter that is changing.
    var isRun: Bool
    var text: String
    /// The left edge, in the canvas.
    var x: Double
    var width: Double
    var alpha: Double
    var blur: Double
    var scale: Double
    /// How strong the glint on the letter is, 0 to 1.
    var glint: Double
}

nonisolated enum TitleSweep {
    /// How much of the swipe each letter's own change takes.
    static let window = 0.42
    /// The first and last stretch of a swipe hold the title still.
    static let rest = 0.03
    static let maxBlur = 2.8
    /// Blur comes in these steps: fewer distinct filters, a smoother look.
    static let blurStep = 0.7
    static let minBlur = 0.5
    static let glintStrength = 0.34

    /// The swipe's progress (0 is the first title, 1 the second) with the resting ends cut off.
    static func shape(_ t: Double) -> Double {
        min(max((t - rest) / (1 - 2 * rest), 0), 1)
    }

    /// 0 for the first letter to change, 1 for the last: the rightmost letter changes first, because the content is
    /// pulled to the left by a swipe to the next title, and the same function run backwards sweeps the other way for
    /// a swipe back.
    static func order(center: Double, minX: Double, maxX: Double) -> Double {
        min(max((maxX - center) / max(maxX - minX, 1), 0), 1)
    }

    /// How far a letter at `order` has got when the swipe is at `s`: 0 not started, 1 done.
    static func progress(order: Double, s: Double) -> Double {
        let start = order * (1 - window)
        return min(max((s - start) / window, 0), 1)
    }

    static func ease(_ g: Double) -> Double { g * g * (3 - 2 * g) }

    static func quantized(blur: Double) -> Double {
        guard blur >= minBlur else { return 0 }
        return max((blur / blurStep).rounded(), 1) * blurStep
    }

    private static func key(_ glyph: TitleGlyph, _ offset: Double) -> String {
        "\(glyph.text)@\(Int(((offset + glyph.x) * 2).rounded()))"
    }

    /// What to draw for the swipe at `s` from the old line (at `oldOffset`) to the new line (at `newOffset`). A letter
    /// that is the same character in the same place in both stays put, so a shared start ("The ") does not flicker.
    static func ops(
        old: TitleLine, oldOffset: Double, new: TitleLine, newOffset: Double, s: Double
    ) -> [TitleOp] {
        func whole(_ line: TitleLine, _ offset: Double) -> [TitleOp] {
            guard let first = line.glyphs.first else { return [] }
            return [TitleOp(isRun: true, text: line.text, x: offset + first.x, width: line.width, alpha: 1, blur: 0, scale: 1, glint: 0)]
        }
        if s <= 0 || old.text == new.text { return whole(old, oldOffset) }
        if s >= 1 { return whole(new, newOffset) }

        let lines = [(old, oldOffset), (new, newOffset)].filter { !$0.0.glyphs.isEmpty }
        let minX = lines.map { $0.1 }.min() ?? 0
        let maxX = lines.map { $0.1 + $0.0.width }.max() ?? 0
        func glyphProgress(_ glyph: TitleGlyph, _ offset: Double) -> Double {
            let center = offset + glyph.x + glyph.width / 2
            return progress(order: order(center: center, minX: minX, maxX: maxX), s: s)
        }
        let oldKeys = Set(old.glyphs.map { key($0, oldOffset) })
        let newKeys = Set(new.glyphs.map { key($0, newOffset) })

        var ops: [TitleOp] = []
        // Each side collects the letters that sit still into one run and flushes it at the first one that does not.
        var run: (text: String, x: Double, width: Double)?
        func flush() {
            if let run {
                ops.append(TitleOp(isRun: true, text: run.text, x: run.x, width: run.width, alpha: 1, blur: 0, scale: 1, glint: 0))
            }
            run = nil
        }
        func extend(_ glyph: TitleGlyph, _ offset: Double) {
            if var current = run {
                current.text += glyph.text
                current.width = offset + glyph.x + glyph.width - current.x
                run = current
            } else {
                run = (glyph.text, offset + glyph.x, glyph.width)
            }
        }

        for glyph in old.glyphs {
            let g = glyphProgress(glyph, oldOffset)
            let e = ease(g)
            if newKeys.contains(key(glyph, oldOffset)) || g <= 0 {
                extend(glyph, oldOffset)
            } else if e < 0.996 {
                flush()
                ops.append(TitleOp(
                    isRun: false, text: glyph.text, x: oldOffset + glyph.x, width: glyph.width,
                    alpha: 1 - e, blur: quantized(blur: maxBlur * e), scale: 1 + 0.05 * e, glint: 0
                ))
            } else {
                flush()
            }
        }
        flush()

        for glyph in new.glyphs {
            let g = glyphProgress(glyph, newOffset)
            let e = ease(g)
            if oldKeys.contains(key(glyph, newOffset)) {
                flush()
            } else if g >= 1 {
                extend(glyph, newOffset)
            } else if e > 0.004 {
                flush()
                ops.append(TitleOp(
                    isRun: false, text: glyph.text, x: newOffset + glyph.x, width: glyph.width,
                    alpha: e, blur: quantized(blur: maxBlur * (1 - e)), scale: 0.93 + 0.07 * e,
                    glint: glintStrength * sin(Double.pi * g)
                ))
            } else {
                flush()
            }
        }
        flush()
        return ops
    }
}

// MARK: - Style

/// The look and the metrics of one panel's title: its font twice (one to draw, one to measure, the same size and
/// weight), its color and where it sits in its row.
nonisolated struct TitleDissolveStyle {
    var font: Font
    var uiFont: UIFont
    var tracking: Double
    var color: Color
    var centered: Bool
    /// Room kept clear on each side of the line.
    var inset: Double
    var height: Double

    /// The In progress panel's title: white, left-aligned, on the row's 24 pt.
    static let inProgress = TitleDissolveStyle(
        font: InProgressPanelMetrics.titleFont,
        uiFont: .systemFont(ofSize: 19, weight: .heavy),
        tracking: -0.3,
        color: .white,
        centered: false,
        inset: 0,
        height: InProgressPanelMetrics.titleRowHeight
    )

    /// The Trending panel's title: centered with 16 pt to spare, on its 28 pt line (`HomeDesign.Text.trendingTitle` and
    /// `HomeDesign.Metric.trendingTitleHeight`, which a test checks; they cannot be named from here).
    static let trending = TitleDissolveStyle(
        font: .system(size: 20, weight: .heavy),
        uiFont: .systemFont(ofSize: 20, weight: .heavy),
        tracking: -0.3,
        color: .primary,
        centered: true,
        inset: 16,
        height: 28
    )
}

// MARK: - The canvas

/// A title changing in place from `from` to `to` as a swipe goes `t` of the way (0 is `from`, up to 1 for `to`).
/// Reduce Motion: the title is the nearest one and changes with a short fade.
struct TitleDissolve: View {
    let from: String
    let to: String
    let t: Double
    let style: TitleDissolveStyle

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// How far the canvas reaches past its row on every side, so a blur and a glint are not cut off.
    private static let pad = 8.0

    var body: some View {
        if reduceMotion {
            ZStack(alignment: style.centered ? .center : .leading) {
                Text(from)
                    .id(from)
                    .transition(.opacity)
            }
            .font(style.font)
            .tracking(style.tracking)
            .foregroundStyle(style.color)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, style.inset)
            .frame(maxWidth: .infinity, minHeight: style.height, alignment: style.centered ? .center : .leading)
            .animation(HomeDesign.Motion.reduced, value: from)
            .accessibilityHidden(true)
            .allowsHitTesting(false)
        } else {
            let s = TitleSweep.shape(t)
            let from = from
            let to = to
            let style = style
            let pad = Self.pad
            Canvas { context, size in
                Self.draw(from: from, to: to, s: s, style: style, in: context, size: size, pad: pad)
            }
            .frame(maxWidth: .infinity)
            .frame(height: style.height + 2 * pad)
            .padding(-pad)
            .accessibilityHidden(true)
            .allowsHitTesting(false)
        }
    }

    nonisolated private static func text(_ string: String, style: TitleDissolveStyle, tracked: Bool) -> Text {
        let text = Text(string).font(style.font).foregroundStyle(style.color)
        return tracked ? text.tracking(style.tracking) : text
    }

    nonisolated private static func draw(
        from: String, to: String, s: Double, style: TitleDissolveStyle, in context: GraphicsContext, size: CGSize, pad: Double
    ) {
        let available = max(Double(size.width) - 2 * pad - 2 * style.inset, 0)
        let old = TitleMetrics.line(from, font: style.uiFont, tracking: style.tracking, available: available)
        // A title that is not changing never needs its partner measured.
        let new = s > 0 && from != to
            ? TitleMetrics.line(to, font: style.uiFont, tracking: style.tracking, available: available)
            : old
        func offset(_ line: TitleLine) -> Double {
            pad + style.inset + (style.centered ? (available - line.width) / 2 : 0)
        }
        let ops = TitleSweep.ops(old: old, oldOffset: offset(old), new: new, newOffset: offset(new), s: s)
        let midY = pad + style.height / 2
        for op in ops {
            if op.isRun {
                context.draw(
                    context.resolve(text(op.text, style: style, tracked: true)),
                    at: CGPoint(x: op.x, y: midY),
                    anchor: .leading
                )
                continue
            }
            let resolved = context.resolve(text(op.text, style: style, tracked: false))
            var drawing = context
            drawing.opacity = op.alpha
            if op.blur > 0 { drawing.addFilter(.blur(radius: op.blur)) }
            // Scale about the letter's own middle, so it breathes in place; the letter itself starts at its `x`.
            drawing.translateBy(x: op.x + op.width / 2, y: midY)
            if op.scale != 1 { drawing.scaleBy(x: op.scale, y: op.scale) }
            drawing.draw(resolved, at: CGPoint(x: -op.width / 2, y: 0), anchor: .leading)
            if op.glint > 0.02 {
                var glint = context
                glint.opacity = op.glint
                glint.blendMode = .plusLighter
                glint.translateBy(x: op.x + op.width / 2, y: midY)
                if op.scale != 1 { glint.scaleBy(x: op.scale, y: op.scale) }
                glint.draw(resolved, at: CGPoint(x: -op.width / 2, y: 0), anchor: .leading)
            }
        }
    }
}

// MARK: - Trending

/// The Trending panel's title: reads the carousel's scroll position (only this view does, so a scroll frame redraws
/// this and nothing above it) and hands the two titles it is between to the shared canvas.
struct SwipeTitle: View {
    /// Every title of the carousel's list, in its order.
    let titles: [String]
    let state: HomeTilesState
    let style: TitleDissolveStyle

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The title the position last read as, which the hysteresis keeps while a finger rests near the midpoint.
    @State private var near = 0

    var body: some View {
        if titles.isEmpty {
            Color.clear.frame(height: style.height)
        } else {
            let raw = PanelPosition.resolve(
                reported: state.scrollPosition, focusIndex: state.focusIndex, count: titles.count
            )
            let nearest = PanelPosition.nearest(raw, previous: near, count: titles.count)
            // Reduce Motion replaces the sweep with the nearest title.
            let position = reduceMotion ? Double(nearest) : raw
            let split = PanelPosition.split(position, count: titles.count)
            TitleDissolve(from: titles[split.lo], to: titles[split.hi], t: split.t, style: style)
                .onChange(of: nearest, initial: true) { _, now in near = now }
        }
    }
}
