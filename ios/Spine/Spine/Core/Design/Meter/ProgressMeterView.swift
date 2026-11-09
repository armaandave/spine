import SwiftUI

/// The three colors a meter uses. It knows nothing about artwork tones: callers build it.
nonisolated struct MeterPalette: Equatable, Sendable {
    /// Done on an earlier day.
    var before: Color
    /// The empty track and the ticks still to come.
    var rest: Color
    /// Today's part.
    var accent: Color
    /// The title's poster colors, for the In progress panel's meter. Nil draws the plain colors above.
    var tint: MeterTint? = nil
    /// How strong the earlier part is drawn with a tint (Increase Contrast draws it fuller).
    var tintOpacity: Double = 0.8

    /// An earlier tick, `index` of `count`: its stretch of the poster's ramp, or the plain color.
    func beforeColor(index: Int, count: Int) -> Color {
        guard let tint else { return before }
        return tint.color(at: count > 1 ? Double(index) / Double(count - 1) : 0).opacity(tintOpacity)
    }

    /// Today's part: the poster's brightest color, or the accent.
    var todayColor: Color { tint?.today.color ?? accent }
}

/// Sizes of the compact (feed card) drawing.
nonisolated enum ProgressMeterMetrics {
    static let tickHeight: CGFloat = 8
    static let tickRadius: CGFloat = 2
    static let tickGap: CGFloat = 3
    /// Gap once there are more than 20 ticks.
    static let denseTickGap: CGFloat = 2
    static let denseTickCount = 20
    static let barHeight: CGFloat = 8
    static let barRadius: CGFloat = 4
    /// Space between the ticks and the series line.
    static let lineOffset: CGFloat = 5
    static let lineHeight: CGFloat = 2
    static let markerWidth: CGFloat = 3
    static let markerHeight: CGFloat = 6
    /// A window's left edge fades in over this share of its width.
    static let fadeFraction: CGFloat = 0.24
    /// The marker reaches this far above and below the line.
    static let markerOverhang: CGFloat = (markerHeight - lineHeight) / 2

    /// Height of the compact drawing: 8 for ticks and bars; a window adds the series line and its marker.
    static func height(for kind: ProgressMeterModel.Kind) -> CGFloat {
        switch kind {
        case .ticks, .bar: tickHeight
        case .window: tickHeight + lineOffset + lineHeight + markerOverhang
        }
    }
}

/// The compact meter on a feed card: ticks, a 20-tick window with a series line, or a bar,
/// all drawn in one Canvas (a handful of filled paths, no per-tick views).
struct ProgressMeterView: View, Equatable {
    let model: ProgressMeterModel
    let palette: MeterPalette

    var body: some View {
        let model = model
        let palette = palette
        Canvas { context, size in
            ProgressMeterView.draw(model, palette: palette, in: context, size: size)
        }
        .frame(height: ProgressMeterMetrics.height(for: model.kind))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(model.accessibilityLabel)
        .accessibilityAddTraits(.isImage)
    }

    /// The bar for the tile panel (10 tall, radius 4) and the sheets (14 tall, radius 7),
    /// whose lit and earlier parts animate when the numbers change.
    static func bar(
        model: ProgressMeterModel,
        palette: MeterPalette,
        height: CGFloat,
        radius: CGFloat
    ) -> ProgressMeterBarView {
        ProgressMeterBarView(model: model, palette: palette, height: height, radius: radius)
    }

    // MARK: Drawing

    nonisolated private static func draw(
        _ model: ProgressMeterModel,
        palette: MeterPalette,
        in context: GraphicsContext,
        size: CGSize
    ) {
        guard size.width > 0 else { return }
        switch model.kind {
        case .ticks:
            drawTicks(model, palette: palette, in: context, size: size, fades: false)
        case .window:
            drawTicks(model, palette: palette, in: context, size: size, fades: true)
            drawSeriesLine(model, palette: palette, in: context, size: size)
        case .bar:
            drawBar(model, palette: palette, in: context, size: size)
        }
    }

    nonisolated private static func drawTicks(
        _ model: ProgressMeterModel,
        palette: MeterPalette,
        in context: GraphicsContext,
        size: CGSize,
        fades: Bool
    ) {
        let count = model.tickCount
        guard count > 0 else { return }
        let gap = count > ProgressMeterMetrics.denseTickCount
            ? ProgressMeterMetrics.denseTickGap : ProgressMeterMetrics.tickGap
        let width = (size.width - gap * CGFloat(count - 1)) / CGFloat(count)
        guard width > 0 else { return }
        let radius = min(ProgressMeterMetrics.tickRadius, width / 2)

        var before = Path()
        var today = Path()
        var rest = Path()
        for index in 0..<count {
            let tick = Path(
                roundedRect: CGRect(
                    x: CGFloat(index) * (width + gap),
                    y: 0,
                    width: width,
                    height: ProgressMeterMetrics.tickHeight
                ),
                cornerRadius: radius
            )
            switch model.tickState(at: index) {
            case .before:
                if palette.tint != nil {
                    // A poster's colors run along the ticks, so each earlier tick has its own stretch of the ramp.
                    context.fill(
                        tick,
                        with: shading(palette.beforeColor(index: index, count: count), size: size, fades: fades)
                    )
                } else {
                    before.addPath(tick)
                }
            case .today: today.addPath(tick)
            case .rest: rest.addPath(tick)
            }
        }
        context.fill(rest, with: shading(palette.rest, size: size, fades: fades))
        context.fill(before, with: shading(palette.before, size: size, fades: fades))
        context.fill(today, with: shading(palette.todayColor, size: size, fades: fades))
    }

    /// A flat color, or the color fading in from clear over the left of the drawing
    /// (one gradient shading instead of masking the whole row offscreen).
    nonisolated private static func shading(_ color: Color, size: CGSize, fades: Bool) -> GraphicsContext.Shading {
        guard fades else { return .color(color) }
        return .linearGradient(
            Gradient(stops: [
                .init(color: color.opacity(0), location: 0),
                .init(color: color, location: ProgressMeterMetrics.fadeFraction),
                .init(color: color, location: 1),
            ]),
            startPoint: .zero,
            endPoint: CGPoint(x: size.width, y: 0)
        )
    }

    nonisolated private static func drawSeriesLine(
        _ model: ProgressMeterModel,
        palette: MeterPalette,
        in context: GraphicsContext,
        size: CGSize
    ) {
        let lineY = ProgressMeterMetrics.tickHeight + ProgressMeterMetrics.lineOffset
        let lineHeight = ProgressMeterMetrics.lineHeight
        let at = size.width * model.endFraction
        let track = CGRect(x: 0, y: lineY, width: size.width, height: lineHeight)
        context.fill(Path(roundedRect: track, cornerRadius: lineHeight / 2), with: .color(palette.rest))
        if at > 0 {
            let done = CGRect(x: 0, y: lineY, width: at, height: lineHeight)
            context.fill(Path(roundedRect: done, cornerRadius: lineHeight / 2), with: .color(palette.before))
        }
        let markerWidth = ProgressMeterMetrics.markerWidth
        let markerX = min(max(at - markerWidth / 2, 0), size.width - markerWidth)
        let marker = CGRect(
            x: markerX,
            y: lineY - ProgressMeterMetrics.markerOverhang,
            width: markerWidth,
            height: ProgressMeterMetrics.markerHeight
        )
        context.fill(Path(roundedRect: marker, cornerRadius: 1), with: .color(palette.todayColor))
    }

    nonisolated private static func drawBar(
        _ model: ProgressMeterModel,
        palette: MeterPalette,
        in context: GraphicsContext,
        size: CGSize
    ) {
        var clipped = context
        let track = CGRect(x: 0, y: 0, width: size.width, height: ProgressMeterMetrics.barHeight)
        clipped.clip(to: Path(roundedRect: track, cornerRadius: ProgressMeterMetrics.barRadius))
        clipped.fill(Path(track), with: .color(palette.rest))
        let beforeWidth = size.width * model.beforeFraction
        let todayWidth = size.width * model.todayFraction
        if beforeWidth > 0 {
            clipped.fill(
                Path(CGRect(x: 0, y: 0, width: beforeWidth, height: track.height)),
                with: beforeShading(palette, width: size.width)
            )
        }
        if todayWidth > 0 {
            clipped.fill(
                Path(CGRect(x: beforeWidth, y: 0, width: todayWidth, height: track.height)),
                with: .color(palette.todayColor)
            )
        }
    }

    /// The earlier part of a bar: the palette's color, or a poster's ramp laid along the whole bar so a color sits
    /// where the In progress panel's bar has it.
    nonisolated private static func beforeShading(_ palette: MeterPalette, width: CGFloat) -> GraphicsContext.Shading {
        guard let tint = palette.tint else { return .color(palette.before) }
        let colors = tint.gradientColors.map { $0.opacity(palette.tintOpacity) }
        return .linearGradient(
            Gradient(colors: colors.count > 1 ? colors : colors + colors),
            startPoint: .zero,
            endPoint: CGPoint(x: width, y: 0)
        )
    }
}

/// The animated bar: the earlier part and today's part are plain shapes sized by fractions of
/// the width in a small custom layout (no geometry reader), so a change slides the segments.
struct ProgressMeterBarView: View, Equatable {
    let model: ProgressMeterModel
    let palette: MeterPalette
    let height: CGFloat
    let radius: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static func == (lhs: ProgressMeterBarView, rhs: ProgressMeterBarView) -> Bool {
        lhs.model == rhs.model && lhs.palette == rhs.palette
            && lhs.height == rhs.height && lhs.radius == rhs.radius
    }

    var body: some View {
        MeterSegmentsLayout(before: model.beforeFraction, today: model.todayFraction) {
            if let tint = palette.tint {
                Rectangle()
                    .fill(LinearGradient(
                        colors: tint.gradientColors.count > 1 ? tint.gradientColors : tint.gradientColors + tint.gradientColors,
                        startPoint: .leading,
                        endPoint: .trailing
                    ))
                    .opacity(palette.tintOpacity)
            } else {
                Rectangle().fill(palette.before)
            }
            Rectangle().fill(palette.todayColor)
        }
        .frame(height: height)
        .background(palette.rest)
        .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
        .animation(
            reduceMotion ? nil : .easeInOut(duration: 0.3),
            value: [model.beforeFraction, model.todayFraction]
        )
        .animation(reduceMotion ? nil : .easeOut(duration: 0.45), value: palette)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(model.accessibilityLabel)
        .accessibilityAddTraits(.isImage)
    }
}

/// Places two children side by side from the leading edge: the first as wide as `before`
/// (a share of the width), the second as wide as `today`. Both shares animate.
struct MeterSegmentsLayout: Layout {
    var before: Double
    var today: Double

    var animatableData: AnimatablePair<Double, Double> {
        get { AnimatablePair(before, today) }
        set {
            before = newValue.first
            today = newValue.second
        }
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        CGSize(width: proposal.width ?? 0, height: proposal.height ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard subviews.count == 2 else { return }
        let beforeShare = min(max(before, 0), 1)
        let todayShare = min(max(today, 0), 1 - beforeShare)
        let beforeWidth = bounds.width * beforeShare
        let todayWidth = bounds.width * todayShare
        subviews[0].place(
            at: CGPoint(x: bounds.minX, y: bounds.minY),
            anchor: .topLeading,
            proposal: ProposedViewSize(width: beforeWidth, height: bounds.height)
        )
        subviews[1].place(
            at: CGPoint(x: bounds.minX + beforeWidth, y: bounds.minY),
            anchor: .topLeading,
            proposal: ProposedViewSize(width: todayWidth, height: bounds.height)
        )
    }
}
