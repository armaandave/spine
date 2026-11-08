import SwiftUI

/// The outline of the open tile's language on top of a button's glass: a 0.5 pt ring and a 1 pt crescent along the
/// top. One Canvas of solid colors, no path boolean (the shapes change size on every frame of a swipe).
struct PanelOutline: View {
    /// How much of the top crescent shows, 0 to 1: the ring alone is the whole outline of an icon circle.
    var crescent: Double = 1

    var body: some View {
        let crescent = crescent
        Canvas { context, size in
            Self.draw(in: context, size: size, crescent: crescent)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    nonisolated private static func draw(in context: GraphicsContext, size: CGSize, crescent strength: Double) {
        let rect = CGRect(origin: .zero, size: size)
        guard rect.width > 2, rect.height > 2 else { return }
        let ring = rect.insetBy(dx: TileStyle.strokeWidth / 2, dy: TileStyle.strokeWidth / 2)
        context.stroke(
            Path(roundedRect: ring, cornerRadius: ring.height / 2, style: .continuous),
            with: .color(TileStyle.stroke),
            lineWidth: TileStyle.strokeWidth
        )
        guard strength > 0.004 else { return }
        // The top highlight is the capsule minus the same capsule moved down by 1 pt: an even-odd fill of both,
        // clipped to the first, is exactly its top crescent.
        var clipped = context
        clipped.opacity = strength
        clipped.clip(to: Path(roundedRect: rect, cornerRadius: rect.height / 2, style: .continuous))
        var crescent = Path(roundedRect: rect, cornerRadius: rect.height / 2, style: .continuous)
        crescent.addPath(Path(
            roundedRect: rect.offsetBy(dx: 0, dy: TileStyle.highlightWidth),
            cornerRadius: rect.height / 2,
            style: .continuous
        ))
        clipped.fill(crescent, with: .color(TileStyle.highlight), style: FillStyle(eoFill: true))
    }
}

/// `+N` and `Update`: two 36 pt Liquid Glass capsules. `+N` ends on the meter's lit end; `Update` takes the rest of
/// the row, one gap to its right, and turns into the pencil as it narrows. All of their numbers come from the blended
/// layout, so they move with the finger.
///
/// Both are `Button`s with the glass on the button itself and a content shape 4 pt taller on each side (a glass on
/// the label swallowed taps). The container's spacing is 6, under the 10 pt gap they rest at: when `+N` collapses
/// or `Update` slides over it, the system bridges the two glasses.
struct PanelGlassButtons: View {
    let layout: MorphLayout
    let width: Double
    let spec: PanelTitleSpec
    /// A quick write is in flight for this title: `+N` waits.
    let isBusy: Bool
    /// False between two titles: the buttons are morphing and a tap could land on the wrong title.
    let isSettled: Bool
    let quickID: String
    let updateID: String
    let onQuick: () -> Void
    let onUpdate: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GlassEffectContainer(spacing: InProgressPanelMetrics.glassMergeSpacing) {
            ZStack(alignment: .topLeading) {
                quickButton
                updateButton
                    .zIndex(1)
            }
        }
        .frame(width: width, height: InProgressPanelMetrics.buttonHeight, alignment: .topLeading)
    }

    // MARK: +N

    private var quickButton: some View {
        let quickWidth = max(layout.quickWidth, 1)
        let live = spec.hasQuick && layout.quickOn > 0.5 && isSettled
        return Button(action: onQuick) {
            // The button is exactly this big: its label, tint and outline are overlays, so a word wider than the
            // button (it shrinks to a circle while it morphs) can never make the outline or tint wider than the glass.
            Color.clear
                .frame(width: quickWidth, height: InProgressPanelMetrics.buttonHeight)
                .overlay { Capsule().fill(PanelStyle.primaryTint) }
                .overlay {
                    ZStack {
                        Text(spec.quickLabel)
                            .id(spec.quickLabel)
                            .transition(PanelMotion.wordSwap(reduceMotion: reduceMotion))
                    }
                    .animation(PanelMotion.slot(reduceMotion: reduceMotion), value: spec.quickLabel)
                    .font(InProgressPanelMetrics.labelFont)
                    .tracking(InProgressPanelMetrics.labelTracking)
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .fixedSize()
                    .opacity(isBusy ? 0.5 : 1)
                    .frame(width: quickWidth, height: InProgressPanelMetrics.buttonHeight)
                }
                .overlay { PanelOutline() }
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .capsule)
        .contentShape(Rectangle().inset(by: -InProgressPanelMetrics.hitSlop))
        .background(alignment: .topLeading) { quickGlow(width: quickWidth) }
        .opacity(layout.quickOn)
        .allowsHitTesting(live)
        .accessibilityHidden(!spec.hasQuick)
        .accessibilityLabel(spec.plan?.label ?? "")
        .accessibilityIdentifier(quickID)
    }

    /// The accent's soft glow behind `+N`: a baked, stretchable capsule shadow, tinted.
    private func quickGlow(width: Double) -> some View {
        let blur: CGFloat = 16
        let pad = BakedShadow.padding(blur: blur)
        return Image(uiImage: BakedShadow.capsuleSlice(blur: blur))
            .renderingMode(.template)
            .resizable(capInsets: BakedShadow.capsuleSliceInsets(blur: blur))
            .foregroundStyle(Color(.sRGB, red: layout.accentR, green: layout.accentG, blue: layout.accentB,
                                   opacity: PanelStyle.quickGlow))
            .frame(width: width + 2 * pad, height: BakedShadow.capsuleSliceHeight + 2 * pad)
            .offset(x: -pad, y: -pad)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    // MARK: Update

    private var updateButton: some View {
        let textOpacity = PanelButtonGeometry.textOpacity(updateWidth: layout.updateWidth)
        let iconOpacity = PanelButtonGeometry.iconOpacity(updateWidth: layout.updateWidth)
        let title = spec.detail.secondaryTitle
        let updateWidth = max(layout.updateWidth, 1)
        return Button(action: onUpdate) {
            // One shape, the size of the glass: when `Update` shrinks to its icon, "Update" is still laid out at its
            // own width, and inside a plain ZStack that width stretched the outline and the tint past the 36 pt
            // circle, drawing a second, larger ring around it.
            Color.clear
                .frame(width: updateWidth, height: InProgressPanelMetrics.buttonHeight)
                .overlay { Capsule().fill(PanelStyle.primaryTint).opacity(layout.updatePrimary) }
                .overlay {
                    ZStack {
                        Text(title)
                            .id(title)
                            .transition(PanelMotion.wordSwap(reduceMotion: reduceMotion))
                    }
                    .animation(PanelMotion.slot(reduceMotion: reduceMotion), value: title)
                    .font(InProgressPanelMetrics.labelFont)
                    .tracking(InProgressPanelMetrics.labelTracking)
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .fixedSize()
                    .opacity(textOpacity)
                    .scaleEffect(PanelButtonGeometry.textScale(updateWidth: layout.updateWidth))
                    .frame(width: updateWidth, height: InProgressPanelMetrics.buttonHeight)
                }
                .overlay {
                    Image(systemName: title == "Open" ? "arrow.up.right" : "pencil")
                        .font(InProgressPanelMetrics.iconFont)
                        .foregroundStyle(.white)
                        .opacity(iconOpacity)
                        .scaleEffect(PanelButtonGeometry.iconScale(updateWidth: layout.updateWidth))
                        .frame(width: updateWidth, height: InProgressPanelMetrics.buttonHeight)
                }
                .overlay { PanelOutline(crescent: 1 - iconOpacity) }
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .capsule)
        .contentShape(Rectangle().inset(by: -InProgressPanelMetrics.hitSlop))
        .offset(x: layout.updateX)
        .allowsHitTesting(isSettled)
        .accessibilityLabel(title)
        .accessibilityHint("Opens \(spec.detail.title)")
        .accessibilityIdentifier(updateID)
    }
}
