import Foundation
import SwiftUI

/// One cell of the meter's pool of 30. Ticks, a window's ticks and a bar are all states of the same pool, so a
/// swipe can split a bar into ticks and merge them back. Colors are straight (not premultiplied) RGBA.
nonisolated struct MorphCell: Equatable {
    static let valueCount = 9

    var x = 0.0
    var w = 0.0
    var opacity = 0.0
    var r = 1.0
    var g = 1.0
    var b = 1.0
    var a = 0.16
    var radiusLeading = 3.0
    var radiusTrailing = 3.0

    var values: [Double] { [x, w, opacity, r, g, b, a, radiusLeading, radiusTrailing] }

    init() {}

    init(_ v: ArraySlice<Double>) {
        let i = v.startIndex
        x = v[i]
        w = v[i + 1]
        opacity = v[i + 2]
        r = v[i + 3]
        g = v[i + 4]
        b = v[i + 5]
        a = v[i + 6]
        radiusLeading = v[i + 7]
        radiusTrailing = v[i + 8]
    }
}

/// Everything the panel draws for one title at rest, for a given width: a pure function of the title and `W`. Two
/// layouts and a blend factor are the panel while a finger is between two titles.
nonisolated struct MorphLayout: Equatable {
    static let cellCount = InProgressPanelMetrics.cellCount
    /// The earlier days' colors are kept at the meter's start, middle and end. A poster's ramp has at most three
    /// colors (`MeterTint.maximumColors`), evenly spaced, so these three points are the ramp exactly.
    static let rampStopCount = 3
    static let headCount = 18 + rampStopCount * 4
    static let valueCount = headCount + cellCount * MorphCell.valueCount

    /// 1 when the meter is a bar, else 0.
    var bar = 0.0
    /// Where the lit span starts and ends, in points from the meter's left. `litEnd` is what `+N` lines up with.
    var litStart = 0.0
    var litEnd = 0.0
    /// 0.6 when something is lit today, else 0.
    var glow = 0.0
    /// The series line (a window of ticks only).
    var lineOpacity = 0.0
    var lineAt = 0.0
    /// The two rolling numbers and whether each exists (0 or 1).
    var a = 0.0
    var aShown = 0.0
    var b = 0.0
    var bShown = 0.0
    var quickWidth = 0.0
    var quickOn = 0.0
    var updateX = 0.0
    var updateWidth = 0.0
    var updatePrimary = 0.0
    var accentR = 0.97
    var accentG = 0.97
    var accentB = 0.97
    /// The bar's earlier part, as RGBA at each of `rampStopCount` points along the whole meter: the poster's ramp, or
    /// plain white. It blends straight with the scroll. Taking it from the cells instead let a cell that is today's on
    /// the next title color the earlier part mid-swipe, so today's edge flickered and ran ahead of itself.
    var beforeRamp: [Double] = MorphLayout.plainRamp
    var cells: [MorphCell] = Array(repeating: MorphCell(), count: MorphLayout.cellCount)
    /// The poster's colors this layout was made with. Only at rest: it is not part of the flat form, the colors
    /// themselves are (the cells' and the accent), so they blend with the scroll like everything else.
    var tint: MeterTint?

    // MARK: Flat form

    /// The 300 numbers, head first then 30 cells of 9.
    var values: [Double] {
        var out = [bar, litStart, litEnd, glow, lineOpacity, lineAt, a, aShown, b, bShown,
                   quickWidth, quickOn, updateX, updateWidth, updatePrimary, accentR, accentG, accentB]
        out.reserveCapacity(Self.valueCount)
        out.append(contentsOf: beforeRamp)
        for cell in cells { out.append(contentsOf: cell.values) }
        return out
    }

    init() {}

    init(values v: [Double]) {
        guard v.count == Self.valueCount else { return }
        bar = v[0]
        litStart = v[1]
        litEnd = v[2]
        glow = v[3]
        lineOpacity = v[4]
        lineAt = v[5]
        a = v[6]
        aShown = v[7]
        b = v[8]
        bShown = v[9]
        quickWidth = v[10]
        quickOn = v[11]
        updateX = v[12]
        updateWidth = v[13]
        updatePrimary = v[14]
        accentR = v[15]
        accentG = v[16]
        accentB = v[17]
        beforeRamp = Array(v[18 ..< Self.headCount])
        cells = (0 ..< Self.cellCount).map { j in
            let start = Self.headCount + j * MorphCell.valueCount
            return MorphCell(v[start ..< start + MorphCell.valueCount])
        }
    }

    // MARK: Make

    static let before = (r: 1.0, g: 1.0, b: 1.0, a: 0.55)
    static let rest = (r: 1.0, g: 1.0, b: 1.0, a: 0.16)
    static let plainRamp: [Double] = (0 ..< rampStopCount).flatMap { _ in [before.r, before.g, before.b, before.a] }

    /// The layout of one title. `meter` is the title's meter as the model has it (a long series' window is placed
    /// here, as on the tile panel).
    static func make(
        meter: ProgressMeterModel?,
        place: TilePlace,
        accentHex: String?,
        hasQuick: Bool,
        width W: Double,
        tint: MeterTint? = nil
    ) -> MorphLayout {
        var layout = MorphLayout()
        if let tint {
            // The poster's colors: today's part is its brightest, the days before run along its ramp.
            (layout.accentR, layout.accentG, layout.accentB) = (tint.today.red, tint.today.green, tint.today.blue)
            layout.tint = tint
        } else {
            let hsb = ArtAccent.pastelHSB(fromHex: accentHex)
            (layout.accentR, layout.accentG, layout.accentB) = rgb(hue: hsb.hue, saturation: hsb.saturation, brightness: hsb.brightness)
        }
        layout.beforeRamp = (0 ..< rampStopCount).flatMap { i in
            let c = layout.beforeColor(at: Double(i) / Double(rampStopCount - 1))
            return [c.0, c.1, c.2, c.3]
        }

        for j in 0 ..< cellCount { layout.cells[j] = hidden(j, width: W) }

        if let model = TileGeometry.panelMeter(meter) {
            switch model.kind {
            case .ticks, .window:
                layout.fillTicks(model, width: W)
            case .bar:
                layout.fillBar(model, width: W)
            }
            layout.glow = layout.litEnd > layout.litStart ? PanelStyle.litGlow : 0
            if case .window = model.kind {
                layout.lineOpacity = 1
                layout.lineAt = min(1, model.endFraction) * W
            }
        }

        layout.a = Double(place.value ?? 0)
        layout.aShown = place.value == nil ? 0 : 1
        layout.b = Double(place.total ?? 0)
        layout.bShown = place.total == nil ? 0 : 1

        let buttons = PanelButtonGeometry.make(litEnd: layout.litEnd, hasQuick: hasQuick, width: W)
        layout.quickWidth = buttons.quickWidth
        layout.quickOn = hasQuick ? 1 : 0
        layout.updateX = buttons.updateX
        layout.updateWidth = buttons.updateWidth
        layout.updatePrimary = hasQuick ? 0 : 1
        return layout
    }

    /// A cell that is not drawn. Hidden cells sit spread along the bar, so a bar "splits" into ticks in place.
    private static func hidden(_ j: Int, width W: Double) -> MorphCell {
        var cell = MorphCell()
        cell.x = Double(j) / Double(cellCount) * W
        cell.w = 0
        cell.opacity = 0
        (cell.r, cell.g, cell.b, cell.a) = rest
        cell.radiusLeading = InProgressPanelMetrics.meterRadius
        cell.radiusTrailing = InProgressPanelMetrics.meterRadius
        return cell
    }

    /// An earlier day's color `fraction` of the way along the meter: white at 55% without a poster palette, else the
    /// palette's ramp at 80%.
    private func beforeColor(at fraction: Double) -> (Double, Double, Double, Double) {
        guard let tint else { return Self.before }
        let c = tint.components(at: fraction)
        return (c.red, c.green, c.blue, 0.8)
    }

    private mutating func fillTicks(_ model: ProgressMeterModel, width W: Double) {
        let n = min(model.tickCount, Self.cellCount)
        guard n > 0 else { return }
        let gap = InProgressPanelMetrics.tickGap
        let sw = (W - gap * Double(n - 1)) / Double(n)
        var beforeCount = 0
        var litCount = 0
        var isWindow = false
        if case .window = model.kind { isWindow = true }
        for j in 0 ..< n {
            var cell = MorphCell()
            cell.x = Double(j) * (sw + gap)
            cell.w = sw
            cell.opacity = isWindow ? TileGeometry.windowFade(index: j, count: n) : 1
            cell.radiusLeading = InProgressPanelMetrics.meterRadius
            cell.radiusTrailing = InProgressPanelMetrics.meterRadius
            switch model.tickState(at: j) {
            case .before:
                (cell.r, cell.g, cell.b, cell.a) = beforeColor(at: Double(j) / Double(max(n - 1, 1)))
                beforeCount += 1
                litCount += 1
            case .today:
                (cell.r, cell.g, cell.b, cell.a) = (accentR, accentG, accentB, 1)
                litCount += 1
            case .rest:
                (cell.r, cell.g, cell.b, cell.a) = Self.rest
            }
            cells[j] = cell
        }
        func px(_ count: Int) -> Double { count == 0 ? 0 : Double(count) * (sw + gap) - gap }
        litStart = px(beforeCount)
        litEnd = px(litCount)
    }

    private mutating func fillBar(_ model: ProgressMeterModel, width W: Double) {
        bar = 1
        let count = Double(Self.cellCount)
        for j in 0 ..< Self.cellCount {
            var cell = MorphCell()
            cell.x = Double(j) * W / count
            cell.w = j == Self.cellCount - 1 ? W / count : W / count + 0.4
            cell.opacity = 1
            let c = (Double(j) + 0.5) / count
            if c < model.beforeFraction {
                (cell.r, cell.g, cell.b, cell.a) = beforeColor(at: c)
            } else if c < model.endFraction {
                (cell.r, cell.g, cell.b, cell.a) = (accentR, accentG, accentB, 1)
            } else {
                (cell.r, cell.g, cell.b, cell.a) = Self.rest
            }
            let radius = InProgressPanelMetrics.barRadius
            cell.radiusLeading = j == 0 ? radius : 0
            cell.radiusTrailing = j == Self.cellCount - 1 ? radius : 0
            cells[j] = cell
        }
        litStart = model.beforeFraction * W
        litEnd = model.endFraction * W
    }

    // MARK: Blend

    private static func smoothstep(_ x: Double) -> Double { x * x * (3 - 2 * x) }

    /// Cell `j`'s own progress at blend factor `t`: cell 0 starts at once and is done at two thirds, cell 29 starts
    /// a third of the way in and finishes with `t`. The board's `mixFull` over a pool of 30.
    static func cellProgress(cell j: Int, t: Double) -> Double {
        smoothstep(min(max(t * 1.5 - Double(j) / Double(cellCount - 1) * 0.5, 0), 1))
    }

    /// `lo` at 0 and `hi` at 1 exactly. A number that is not shown on one side (`aShown == 0`) does not roll up
    /// from 0: it takes the other side's value, so it only fades in.
    static func blend(_ lo: MorphLayout, _ hi: MorphLayout, _ t: Double) -> MorphLayout {
        if t <= 0 { return lo }
        if t >= 1 { return hi }
        return MorphLayout(values: blendValues(lo.values[...], hi.values[...], t))
    }

    static func blendValues(_ lo: ArraySlice<Double>, _ hi: ArraySlice<Double>, _ t: Double) -> [Double] {
        var l = Array(lo)
        var h = Array(hi)
        if l[7] == 0 { l[6] = h[6] }
        if h[7] == 0 { h[6] = l[6] }
        if l[9] == 0 { l[8] = h[8] }
        if h[9] == 0 { h[8] = l[8] }
        var out = [Double](repeating: 0, count: valueCount)
        for i in 0 ..< headCount { out[i] = l[i] + (h[i] - l[i]) * t }
        for j in 0 ..< cellCount {
            let tj = cellProgress(cell: j, t: t)
            let start = headCount + j * MorphCell.valueCount
            for k in 0 ..< MorphCell.valueCount {
                out[start + k] = l[start + k] + (h[start + k] - l[start + k]) * tj
            }
        }
        return out
    }

    /// The layout the vector says: the blend of its two halves.
    static func resolve(_ vector: MorphVector) -> MorphLayout {
        guard vector.values.count == MorphVector.count else { return MorphLayout() }
        let t = vector.values[2 * valueCount]
        if t <= 0 { return MorphLayout(values: Array(vector.values[0 ..< valueCount])) }
        if t >= 1 { return MorphLayout(values: Array(vector.values[valueCount ..< 2 * valueCount])) }
        return MorphLayout(values: blendValues(
            vector.values[0 ..< valueCount], vector.values[valueCount ..< 2 * valueCount], t
        ))
    }

    // MARK: Color

    /// HSB (all 0 to 1) to RGB, so the accent needs no SwiftUI color to be tested.
    static func rgb(hue: Double, saturation: Double, brightness: Double) -> (Double, Double, Double) {
        let h = (hue - hue.rounded(.down)) * 6
        let i = Int(h.rounded(.down)) % 6
        let f = h - h.rounded(.down)
        let p = brightness * (1 - saturation)
        let q = brightness * (1 - saturation * f)
        let t = brightness * (1 - saturation * (1 - f))
        switch i {
        case 0: return (brightness, t, p)
        case 1: return (q, brightness, p)
        case 2: return (p, brightness, t)
        case 3: return (p, q, brightness)
        case 4: return (t, p, brightness)
        default: return (brightness, p, q)
        }
    }
}

/// The animatable form of the panel: a title's layout, the next title's layout and how far between them, in one
/// array so one animation curve interpolates all of it and `+N`'s edge stays on the lit end through the motion.
/// `zero` is an empty array that the operators treat as all zeros (the usual trap with array-backed vectors).
nonisolated struct MorphVector: VectorArithmetic, Equatable {
    static let count = 2 * MorphLayout.valueCount + 1

    var values: [Double]

    init(values: [Double] = []) {
        self.values = values
    }

    /// `[lo (300)] [hi (300)] [t]`.
    init(lo: [Double], hi: [Double], t: Double) {
        values = lo + hi + [t]
    }

    var lo: [Double] { values.isEmpty ? [] : Array(values[0 ..< MorphLayout.valueCount]) }
    var hi: [Double] { values.isEmpty ? [] : Array(values[MorphLayout.valueCount ..< 2 * MorphLayout.valueCount]) }
    var t: Double { values.last ?? 0 }

    static var zero: MorphVector { MorphVector(values: []) }

    static func += (lhs: inout MorphVector, rhs: MorphVector) {
        if rhs.values.isEmpty { return }
        if lhs.values.isEmpty { lhs.values = rhs.values; return }
        for i in lhs.values.indices where i < rhs.values.count { lhs.values[i] += rhs.values[i] }
    }

    static func -= (lhs: inout MorphVector, rhs: MorphVector) {
        if rhs.values.isEmpty { return }
        if lhs.values.isEmpty { lhs.values = rhs.values.map { -$0 }; return }
        for i in lhs.values.indices where i < rhs.values.count { lhs.values[i] -= rhs.values[i] }
    }

    static func + (lhs: MorphVector, rhs: MorphVector) -> MorphVector {
        var copy = lhs
        copy += rhs
        return copy
    }

    static func - (lhs: MorphVector, rhs: MorphVector) -> MorphVector {
        var copy = lhs
        copy -= rhs
        return copy
    }

    mutating func scale(by rhs: Double) {
        for i in values.indices { values[i] *= rhs }
    }

    var magnitudeSquared: Double {
        values.reduce(0) { $0 + $1 * $1 }
    }
}
