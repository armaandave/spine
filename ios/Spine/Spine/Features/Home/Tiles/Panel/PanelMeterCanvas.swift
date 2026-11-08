import SwiftUI

/// The panel's meter in one Canvas: a glow under the lit span, the pool of 30 cells, an exact bar for when the meter
/// is a bar, and the series line of a window. The Canvas reaches past the meter on every side so a glow is not
/// clipped; its drawing is in the meter's own coordinates.
struct PanelMeterCanvas: View {
    let layout: MorphLayout
    let width: Double

    static func canvasSize(width: Double) -> CGSize {
        CGSize(
            width: width + 2 * InProgressPanelMetrics.canvasMarginX,
            height: InProgressPanelMetrics.canvasMarginTop + InProgressPanelMetrics.meterHeight
                + InProgressPanelMetrics.canvasMarginBottom
        )
    }

    var body: some View {
        let layout = layout
        let width = width
        let size = Self.canvasSize(width: width)
        Canvas { context, _ in
            var shifted = context
            shifted.translateBy(x: InProgressPanelMetrics.canvasMarginX, y: InProgressPanelMetrics.canvasMarginTop)
            Self.draw(layout, width: width, in: &shifted)
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    // MARK: Drawing

    nonisolated private static func accent(_ layout: MorphLayout, _ alpha: Double = 1) -> Color {
        Color(.sRGB, red: layout.accentR, green: layout.accentG, blue: layout.accentB, opacity: alpha)
    }

    nonisolated static func draw(_ layout: MorphLayout, width: Double, in context: inout GraphicsContext) {
        let height = InProgressPanelMetrics.meterHeight
        drawGlow(layout, height: height, in: context)
        drawBar(layout, width: width, height: height, in: context)
        drawCells(layout, height: height, in: context)
        drawLine(layout, width: width, in: context)
    }

    /// A soft accent glow under the lit span only: four stacked, inflated rounded rects standing in for a Gaussian
    /// (a live blur is not allowed in the tiles).
    nonisolated private static func drawGlow(_ layout: MorphLayout, height: Double, in context: GraphicsContext) {
        guard layout.glow > 0.01, layout.litEnd - layout.litStart > 0.5 else { return }
        let radius = InProgressPanelMetrics.meterRadius
            + (InProgressPanelMetrics.barRadius - InProgressPanelMetrics.meterRadius) * layout.bar
        let strength = layout.glow / PanelStyle.litGlow
        let lit = CGRect(x: layout.litStart, y: 0, width: layout.litEnd - layout.litStart, height: height)
        for layer in PanelStyle.litGlowLayers {
            let rect = lit.insetBy(dx: -layer.inflate, dy: -layer.inflate)
            context.fill(
                Path(roundedRect: rect, cornerRadius: radius + layer.inflate, style: .continuous),
                with: .color(accent(layout, layer.alpha * strength))
            )
        }
    }

    /// The bar and the pool cross-fade: the bar at `bar`, the pool (drawn over it) at `1 - bar`. Drawn "over", the
    /// two alphas combine to `1 - (1 - a * bar) * (1 - a * (1 - bar))`, never above `a`, so a half-transparent
    /// part (the track, the days before) never brightens mid-swipe. Fading them on separate, overlapping
    /// ramps (both full around the middle) stacked the two layers' white into a bright flash.
    nonisolated static func barOpacity(_ layout: MorphLayout) -> Double { min(max(layout.bar, 0), 1) }
    nonisolated static func poolOpacity(_ layout: MorphLayout) -> Double { 1 - barOpacity(layout) }

    nonisolated private static func drawCells(_ layout: MorphLayout, height: Double, in context: GraphicsContext) {
        let fade = poolOpacity(layout)
        guard fade > 0.004 else { return }
        var pool = context
        pool.opacity = fade
        for cell in layout.cells {
            guard cell.w >= 0.05, cell.opacity >= 0.004 else { continue }
            let limit = min(cell.w / 2, InProgressPanelMetrics.barRadius)
            let shape = UnevenRoundedRectangle(
                topLeadingRadius: min(cell.radiusLeading, limit),
                bottomLeadingRadius: min(cell.radiusLeading, limit),
                bottomTrailingRadius: min(cell.radiusTrailing, limit),
                topTrailingRadius: min(cell.radiusTrailing, limit),
                style: .continuous
            )
            pool.fill(
                shape.path(in: CGRect(x: cell.x, y: 0, width: cell.w, height: height)),
                with: .color(Color(.sRGB, red: cell.r, green: cell.g, blue: cell.b, opacity: cell.a * cell.opacity))
            )
        }
    }

    /// The bar at its exact pixels, so the lit end is where `+N` ends. Clipped to the 10 pt track with a Core
    /// Graphics clip, not an offscreen pass.
    nonisolated private static func drawBar(
        _ layout: MorphLayout, width: Double, height: Double, in context: GraphicsContext
    ) {
        let fade = barOpacity(layout)
        guard fade > 0.004 else { return }
        var bar = context
        bar.opacity = fade
        let track = CGRect(x: 0, y: 0, width: width, height: height)
        bar.clip(to: Path(roundedRect: track, cornerRadius: InProgressPanelMetrics.barRadius, style: .continuous))
        bar.fill(Path(track), with: .color(TileStyle.meterRest))
        if layout.litStart > 0.01 {
            bar.fill(
                Path(CGRect(x: 0, y: 0, width: layout.litStart, height: height)),
                with: beforeShading(layout, width: width)
            )
        }
        if layout.litEnd - layout.litStart > 0.01 {
            bar.fill(
                Path(CGRect(x: layout.litStart, y: 0, width: layout.litEnd - layout.litStart, height: height)),
                with: .color(accent(layout))
            )
        }
    }

    /// The earlier part of the bar: flat white, or, with a poster palette, a gradient through the colors of the cells
    /// that lie in it (the cells carry the blended colors, so it follows a swipe like the ticks do).
    nonisolated private static func beforeShading(_ layout: MorphLayout, width: Double) -> GraphicsContext.Shading {
        let stops: [Gradient.Stop] = layout.cells.compactMap { cell in
            let center = cell.x + cell.w / 2
            guard cell.w >= 0.05, center < layout.litStart, width > 0 else { return nil }
            return Gradient.Stop(
                color: Color(.sRGB, red: cell.r, green: cell.g, blue: cell.b, opacity: cell.a),
                location: min(max(center / width, 0), 1)
            )
        }
        let plain = Color(.sRGB, red: 1, green: 1, blue: 1, opacity: 0.55)
        guard stops.count > 1, stops.contains(where: { $0.color != plain }) else { return .color(TileStyle.meterBefore) }
        var ordered = [Gradient.Stop]()
        for stop in stops {
            ordered.append(Gradient.Stop(color: stop.color, location: max(stop.location, ordered.last?.location ?? 0)))
        }
        return .linearGradient(Gradient(stops: ordered), startPoint: .zero, endPoint: CGPoint(x: width, y: 0))
    }

    /// A window's line for the whole series: the track, how much is done, and a marker on the lit end.
    nonisolated private static func drawLine(_ layout: MorphLayout, width: Double, in context: GraphicsContext) {
        guard layout.lineOpacity > 0.004 else { return }
        var line = context
        line.opacity = layout.lineOpacity
        let top = InProgressPanelMetrics.lineTop
        let thickness = InProgressPanelMetrics.lineHeight
        line.fill(
            Path(roundedRect: CGRect(x: 0, y: top, width: width, height: thickness), cornerRadius: thickness / 2),
            with: .color(TileStyle.meterRest)
        )
        if layout.lineAt > 0 {
            line.fill(
                Path(
                    roundedRect: CGRect(x: 0, y: top, width: layout.lineAt, height: thickness),
                    cornerRadius: thickness / 2
                ),
                with: .color(TileStyle.meterBefore)
            )
        }
        let markerWidth = InProgressPanelMetrics.markerWidth
        let markerHeight = InProgressPanelMetrics.markerHeight
        let x = min(max(layout.lineAt - markerWidth / 2, 0), width - markerWidth)
        let marker = CGRect(
            x: x, y: top - (markerHeight - thickness) / 2, width: markerWidth, height: markerHeight
        )
        for glow in PanelStyle.markerGlowLayers {
            line.fill(
                Path(roundedRect: marker.insetBy(dx: -glow.inflate, dy: -glow.inflate), cornerRadius: 1 + glow.inflate, style: .continuous),
                with: .color(accent(layout, glow.alpha))
            )
        }
        line.fill(Path(roundedRect: marker, cornerRadius: 1), with: .color(accent(layout)))
    }
}
