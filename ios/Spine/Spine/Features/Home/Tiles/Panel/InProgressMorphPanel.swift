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
    /// Posters' colors counted on this device, by item id; the server's, or ones counted earlier, need no waiting.
    /// A title with none draws the plain meter, and its colors fade in when they arrive.
    @State private var counted: [String: [PosterSwatch]] = [:]
    @Environment(\.colorSchemeContrast) private var contrast

    private static func posterURL(_ item: LibraryItem) -> URL? {
        item.media.displayPosterURL.flatMap { URL(string: $0) }
    }

    /// The server's palette if it gives colors, else the count made here, whether by this view or by an earlier panel.
    private func tint(for item: LibraryItem) -> MeterTint? {
        let swatches = counted[item.id] ?? PosterPaletteStore.shared.known(server: nil, url: Self.posterURL(item))
        return MeterTint.choose(
            server: item.media.posterPalette, counted: swatches, highContrast: contrast == .increased
        )
    }

    var body: some View {
        let items = Array(model.inProgress.prefix(TileGeometry.maxPosters))
        let specs = items.map { item in
            PanelTitleSpec.make(item: item, detail: model.detail(for: item), width: width, tint: tint(for: item))
        }
        Color.clear
            .frame(maxWidth: .infinity)
            .frame(height: InProgressPanelMetrics.height)
            .onGeometryChange(for: Double.self) { Double($0.size.width) } action: { measured in
                if measured > 0, abs(measured - width) > 0.01 { width = measured }
            }
            .task(id: items.map { "\($0.id)|\($0.media.displayPosterURL ?? "")" }) {
                await countPosters(of: items)
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

extension InProgressMorphPanel {
    /// Counts the colors of every poster that has none yet, all at once and off the main thread, so one slow download
    /// no longer holds the others back and the title in view has its colors within a moment of the tile opening. A
    /// count that fails (the image was not there yet) is tried once more after a pause.
    private func countPosters(of items: [LibraryItem]) async {
        // Posters an earlier panel counted need no work, but this view has not read them yet.
        var wanted: [(id: String, url: URL)] = []
        for item in items {
            guard let url = Self.posterURL(item), MeterTint.choose(server: item.media.posterPalette, counted: nil) == nil
            else { continue }
            if let known = PosterPaletteStore.shared.known(server: nil, url: url) {
                counted[item.id] = known
            } else {
                wanted.append((item.id, url))
            }
        }
        await withTaskGroup(of: (String, [PosterSwatch]?).self) { group in
            for (id, url) in wanted {
                group.addTask {
                    var found = await PosterPaletteStore.shared.swatches(server: nil, url: url)
                    if found == nil {
                        try? await Task.sleep(for: .seconds(2))
                        if !Task.isCancelled { found = await PosterPaletteStore.shared.swatches(server: nil, url: url) }
                    }
                    return (id, found)
                }
            }
            for await (id, found) in group {
                if let found { counted[id] = found }
            }
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
            // Close to a poster, or not moving at all: a carousel that came to rest a little off its poster (a
            // sheet's keyboard laid the page out again) still takes the tap, for the title it reads as.
            let settled = PanelPosition.isSettled(position, near: nearest) || !state.isCarouselMoving
            PanelRenderer(
                vector: vector,
                width: width,
                spec: spec,
                lo: specs[split.lo],
                hi: specs[split.hi],
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
    /// The two titles a swipe is between (the same one at rest): the title row dissolves from `lo` to `hi` as the
    /// vector's `t` goes from 0 to 1.
    let lo: PanelTitleSpec
    let hi: PanelTitleSpec
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
            PanelTitleRow(spec: spec, lo: lo, hi: hi, t: vector.t)
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

/// The title on the left and where it stands on the right. Neither slides: the title changes in place letter by
/// letter (`TitleDissolve`), and the right-hand block ("S2 \u{00B7} 7 of 10 episodes") dissolves whole from the old
/// place to the new one (`PlaceChange`). Both follow a swipe between two titles and play on their own when a title's
/// data changes. The block's width animates and the title takes what is left of the row, so the row never jumps.
struct PanelTitleRow: View {
    /// The title the panel reads as: what VoiceOver says.
    let spec: PanelTitleSpec
    /// The two titles a swipe is between, and how far (0 at rest, where `lo` is the title).
    let lo: PanelTitleSpec
    let hi: PanelTitleSpec
    let t: Double

    var body: some View {
        HStack(spacing: 0) {
            TitleDissolve(from: lo.detail.title, to: hi.detail.title, t: t, style: .inProgress)
            PlaceChange(id: lo.id, lo: lo.detail.place, hi: hi.detail.place, t: t)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spec.detail.accessibilityLabel)
        .accessibilityAddTraits(.isHeader)
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
