import SwiftUI

// The open In progress tile's panel: one persistent panel that follows the carousel's scroll and morphs from title
// to title (board R4InProgress, variant A "Edge"). Three views keep the work apart:
//
//   InProgressMorphPanel   the host. Reads the model, builds one spec per title and measures the width. It never
//                          reads the scroll position, so a scroll frame does not run it.
//   MorphPanelBody         reads the scroll position and the focus; picks the two titles it is between.
//   PanelRenderer          an Animatable view that draws everything from one vector.

// MARK: - Host

struct InProgressMorphPanel: View {
    let model: HomeTilesModel
    let state: HomeTilesState
    let actions: HomeTilesActions
    let quickID: String
    let updateID: String

    /// The panel's width: the section's content width less the panel's inset on both sides (326 on a 390 pt phone),
    /// until it has been measured.
    @State private var width = Double(TileGeometry.referenceContentWidth - 2 * TileGeometry.panelInset)

    var body: some View {
        let specs = model.inProgress.prefix(TileGeometry.maxPosters).map { item in
            PanelTitleSpec.make(item: item, detail: model.detail(for: item), width: width)
        }
        Color.clear
            .frame(maxWidth: .infinity)
            .frame(height: InProgressPanelMetrics.height)
            .onGeometryChange(for: Double.self) { Double($0.size.width) } action: { measured in
                if measured > 0, abs(measured - width) > 0.01 { width = measured }
            }
            .overlay(alignment: .topLeading) {
                MorphPanelBody(
                    specs: specs,
                    signature: PanelTitleSpec.signature(specs),
                    width: width,
                    state: state,
                    busy: model.busyItemIDs,
                    actions: actions,
                    quickID: quickID,
                    updateID: updateID
                )
            }
    }
}

// MARK: - Body

/// Reads the carousel's position and the focus, and nothing from the model.
struct MorphPanelBody: View {
    let specs: [PanelTitleSpec]
    let signature: Int
    let width: Double
    let state: HomeTilesState
    let busy: Set<String>
    let actions: HomeTilesActions
    let quickID: String
    let updateID: String

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The poster the panel last read as, which the hysteresis keeps while a finger rests near the midpoint.
    @State private var near = 0
    /// Item ids with a write in flight from this panel. Per item, not per view: the panel outlives a swipe.
    @State private var running: Set<String> = []
    @State private var updateTicks = 0
    @State private var successTicks = 0
    @State private var failureTicks = 0
    @State private var sweepToken = 0
    @State private var sweepSpan = SweepSpan(from: 0, to: 6)
    @State private var previous = SpecMemory()

    var body: some View {
        if specs.isEmpty {
            EmptyView()
        } else {
            let raw = PanelPosition.resolve(
                reported: state.scrollPosition, focusIndex: state.focusIndex, count: specs.count
            )
            let nearest = PanelPosition.nearest(raw, previous: near, count: specs.count)
            // Reduce Motion replaces the scrub with the nearest title: no splitting ticks, no sliding bar.
            let position = reduceMotion ? Double(nearest) : raw
            let split = PanelPosition.split(position, count: specs.count)
            let spec = specs[nearest]
            let vector = MorphVector(lo: specs[split.lo].values, hi: specs[split.hi].values, t: split.t)
            let settled = PanelPosition.isSettled(position, near: nearest)
            PanelRenderer(
                vector: vector,
                width: width,
                spec: spec,
                isBusy: busy.contains(spec.id) || running.contains(spec.id),
                isSettled: settled,
                sweepToken: sweepToken,
                sweepSpan: sweepSpan,
                quickID: quickID,
                updateID: updateID,
                onQuick: { quickTapped(spec) },
                onUpdate: { updateTapped(spec) }
            )
            .animation(PanelMotion.settle(reduceMotion: reduceMotion), value: signature)
            .onChange(of: nearest, initial: true) { _, now in near = now }
            .onChange(of: signature, initial: true) { _, _ in specsChanged(near: spec.id) }
            .sensoryFeedback(.impact, trigger: updateTicks)
            .sensoryFeedback(.success, trigger: successTicks)
            .sensoryFeedback(.error, trigger: failureTicks)
        }
    }

    // MARK: Actions

    private func updateTapped(_ spec: PanelTitleSpec) {
        updateTicks += 1
        actions.onUpdate(spec.item)
    }

    private func quickTapped(_ spec: PanelTitleSpec) {
        guard let plan = spec.plan, plan.writes else {
            updateTapped(spec)
            return
        }
        guard !busy.contains(spec.id), !running.contains(spec.id) else { return }
        running.insert(spec.id)
        let item = spec.item
        let id = spec.id
        let run = actions.onQuickAction
        Task {
            let saved = await run(item)
            if saved { successTicks += 1 } else { failureTicks += 1 }
            running.remove(id)
        }
    }

    /// The lit end of the title in view moved forward because the write this panel started was answered: sweep it.
    /// A reload or a swipe never does.
    private func specsChanged(near id: String) {
        defer { previous.specs = specs }
        guard !reduceMotion, busy.contains(id) || running.contains(id),
              let old = previous.specs.first(where: { $0.id == id }),
              let new = specs.first(where: { $0.id == id }),
              let span = SweepSpan.make(old: old, new: new) else { return }
        sweepSpan = span
        sweepToken += 1
    }
}

/// The specs as they were at the last change, kept without observing them.
private final class SpecMemory {
    var specs: [PanelTitleSpec] = []
}

// MARK: - Renderer

/// Draws the whole panel from one vector: the blend of the title it is on and the next one. Between two titles the
/// vector follows the finger; after a tap it is animated, so the bar slides, the digits roll and `+N` widens
/// together on one curve.
struct PanelRenderer: View, Animatable {
    var vector: MorphVector
    let width: Double
    /// The title the panel reads as: its words, its buttons.
    let spec: PanelTitleSpec
    let isBusy: Bool
    let isSettled: Bool
    let sweepToken: Int
    let sweepSpan: SweepSpan
    let quickID: String
    let updateID: String
    let onQuick: () -> Void
    let onUpdate: () -> Void

    var animatableData: MorphVector {
        get { vector }
        set { vector = newValue }
    }

    var body: some View {
        let _ = PerfCounters.bump(.panelBody)
        let layout = MorphLayout.resolve(vector)
        ZStack(alignment: .topLeading) {
            PanelTitleRow(layout: layout, spec: spec)
                .frame(width: width, height: InProgressPanelMetrics.titleRowHeight, alignment: .topLeading)
            PanelMeterCanvas(layout: layout, width: width)
                .offset(
                    x: -InProgressPanelMetrics.canvasMarginX,
                    y: InProgressPanelMetrics.meterTop - InProgressPanelMetrics.canvasMarginTop
                )
            PanelTapSweep(token: sweepToken, span: sweepSpan, layout: layout)
            PanelGlassButtons(
                layout: layout,
                width: width,
                spec: spec,
                isBusy: isBusy,
                isSettled: isSettled,
                quickID: quickID,
                updateID: updateID,
                onQuick: onQuick,
                onUpdate: onUpdate
            )
            .offset(y: InProgressPanelMetrics.buttonsTop)
        }
        .frame(width: width, height: InProgressPanelMetrics.height, alignment: .topLeading)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .accessibilityElement(children: .contain)
    }
}

// MARK: - Title row

/// The title on the left and where it stands on the right: prefix word, rolling number, joiner, rolling total,
/// suffix word, with no spacing between them. Words swap in slots whose width is measured and animated; the numbers
/// roll with the position.
struct PanelTitleRow: View {
    let layout: MorphLayout
    let spec: PanelTitleSpec

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let place = spec.detail.place
        let digit = PanelTextMetrics.digitWidth
        let comma = PanelTextMetrics.commaWidth
        let a = OdometerSlots.make(
            x: reduceMotion ? layout.a.rounded() : layout.a, shown: layout.aShown, digitWidth: digit, commaWidth: comma
        )
        let b = OdometerSlots.make(
            x: reduceMotion ? layout.b.rounded() : layout.b, shown: layout.bShown, digitWidth: digit, commaWidth: comma
        )
        let whereWidth = PanelTextMetrics.width(place.prefix, font: InProgressPanelMetrics.whereUIFont)
            + a.width
            + PanelTextMetrics.width(place.joiner, font: InProgressPanelMetrics.whereUIFont)
            + b.width
            + PanelTextMetrics.width(place.suffix, font: InProgressPanelMetrics.whereUIFont)
        HStack(spacing: 0) {
            title
                .padding(.trailing, whereWidth > 0.5 ? InProgressPanelMetrics.titleGap : 0)
            HStack(spacing: 0) {
                WordSlot(text: place.prefix)
                OdometerView(slots: a, height: InProgressPanelMetrics.titleRowHeight)
                WordSlot(text: place.joiner)
                OdometerView(slots: b, height: InProgressPanelMetrics.titleRowHeight)
                WordSlot(text: place.suffix)
            }
            .fixedSize()
            .offset(y: InProgressPanelMetrics.whereOffsetY)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spec.detail.accessibilityLabel)
        .accessibilityAddTraits(.isHeader)
    }

    private var title: some View {
        ZStack(alignment: .leading) {
            Text(spec.detail.title)
                .id(spec.id)
                .transition(PanelMotion.wordSwap(reduceMotion: reduceMotion))
        }
        .font(InProgressPanelMetrics.titleFont)
        .tracking(-0.3)
        .foregroundStyle(.white)
        .lineLimit(1)
        .truncationMode(.tail)
        .frame(maxWidth: .infinity, minHeight: InProgressPanelMetrics.titleRowHeight, alignment: .leading)
        .animation(PanelMotion.slot(reduceMotion: reduceMotion), value: spec.id)
    }
}

// MARK: - Tap sweep

/// A bright sweep along the part of the meter a tap just lit, 0.85 s: it starts a little behind, grows to full width
/// and fades. Always mounted at opacity 0 so the keyframe animator sees the trigger change; it draws nothing at rest.
struct PanelTapSweep: View {
    let token: Int
    let span: SweepSpan
    let layout: MorphLayout

    private struct Frame {
        var opacity = 0.0
        var shift = -0.45
        var scale = 0.6
    }

    var body: some View {
        let curve = UnitCurve.bezier(startControlPoint: UnitPoint(x: 0.25, y: 1), endControlPoint: UnitPoint(x: 0.35, y: 1))
        let width = span.width
        let accent = Color(.sRGB, red: layout.accentR, green: layout.accentG, blue: layout.accentB, opacity: 0.25)
        ZStack {
            RoundedRectangle(cornerRadius: 8, style: .continuous).fill(accent).padding(-4)
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(LinearGradient(
                    colors: [.white.opacity(0), .white.opacity(0.95)], startPoint: .leading, endPoint: .trailing
                ))
        }
        .frame(width: width, height: InProgressPanelMetrics.meterHeight + 6)
        .keyframeAnimator(initialValue: Frame(), trigger: token) { content, frame in
            content
                .scaleEffect(x: frame.scale, y: 1, anchor: .leading)
                .offset(x: frame.shift * width)
                .opacity(frame.opacity)
        } keyframes: { _ in
            KeyframeTrack(\.opacity) {
                LinearKeyframe(1, duration: 0.187, timingCurve: curve)
                LinearKeyframe(0, duration: PanelMotion.sweepDuration - 0.187, timingCurve: curve)
            }
            KeyframeTrack(\.shift) {
                LinearKeyframe(0, duration: PanelMotion.sweepDuration, timingCurve: curve)
            }
            KeyframeTrack(\.scale) {
                LinearKeyframe(1, duration: PanelMotion.sweepDuration, timingCurve: curve)
            }
        }
        .offset(x: span.from, y: InProgressPanelMetrics.meterTop - 3)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
