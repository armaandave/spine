import Foundation

/// One title's data for the panel: what it says, what its buttons do, and its layout at rest. All of it is a pure
/// function of the item's detail and the panel's width, built once per model change and never per frame.
struct PanelTitleSpec: Identifiable {
    let id: String
    let item: LibraryItem
    let detail: TileDetailModel
    /// `detail.primary` when it writes; nil when the title has only its `Update` button.
    let plan: QuickActionPlan?
    /// Which of `plan.labelTiers` fits the `+N` button of this title at rest.
    let tier: Int
    let layout: MorphLayout
    /// `layout.values`, kept so a frame's vector is two array copies.
    let values: [Double]
    /// How the lit end moves on a tap.
    let shape: MeterShape
    /// The number of units done (the meter's `to`), 0 without a meter.
    let doneUnits: Int
    /// A hash of the inputs, not of the layout: a change of width alone must not animate.
    let inputKey: Int

    var hasQuick: Bool { plan != nil }

    var quickLabel: String {
        guard let plan else { return "" }
        let tiers = plan.labelTiers
        return tiers[min(tier, tiers.count - 1)]
    }

    /// The meter's kind and tick width, for the tap sweep.
    struct MeterShape: Equatable {
        var isBar: Bool
        var tickWidth: Double
    }

    static func make(item: LibraryItem, detail: TileDetailModel, width: Double) -> PanelTitleSpec {
        let plan = detail.primary.flatMap { $0.writes ? $0 : nil }
        let layout = MorphLayout.make(
            meter: detail.meter,
            place: detail.place,
            accentHex: detail.accentHex,
            hasQuick: plan != nil,
            width: width
        )
        var tier = 0
        if let plan {
            tier = PanelLabelTier.pick(tiers: plan.labelTiers, quickWidth: layout.quickWidth) { text in
                PanelTextMetrics.width(
                    text, font: InProgressPanelMetrics.labelUIFont, tracking: InProgressPanelMetrics.labelTracking
                )
            }
        }
        var shape = MeterShape(isBar: false, tickWidth: 0)
        if let model = TileGeometry.panelMeter(detail.meter) {
            switch model.kind {
            case .bar:
                shape.isBar = true
            case .ticks, .window:
                let n = Double(max(model.tickCount, 1))
                shape.tickWidth = (width - InProgressPanelMetrics.tickGap * (n - 1)) / n
            }
        }
        var hasher = Hasher()
        hasher.combine(item.id)
        hasher.combine(detail.title)
        hasher.combine(detail.place)
        hasher.combine(detail.meter)
        hasher.combine(detail.accentHex)
        hasher.combine(detail.primary)
        hasher.combine(detail.secondaryTitle)
        return PanelTitleSpec(
            id: item.id,
            item: item,
            detail: detail,
            plan: plan,
            tier: tier,
            layout: layout,
            values: layout.values,
            shape: shape,
            doneUnits: detail.meter?.to ?? 0,
            inputKey: hasher.finalize()
        )
    }

    /// One number for the whole list, so the panel animates exactly when some title's data changed.
    static func signature(_ specs: [PanelTitleSpec]) -> Int {
        var hasher = Hasher()
        for spec in specs { hasher.combine(spec.inputKey) }
        return hasher.finalize()
    }
}

/// Where the bright sweep after a tap runs, in points from the meter's left.
struct SweepSpan: Equatable {
    var from: Double
    var to: Double

    var width: Double { max(6, to - from) }

    /// The lit end moved forward on the same title: a bar sweeps what was added; ticks and a window sweep the ticks
    /// that were added, ending on the lit end (a window's lit end stays on the same tick, so the old end says
    /// nothing). Nil when it did not move forward.
    static func make(old: PanelTitleSpec, new: PanelTitleSpec) -> SweepSpan? {
        guard old.id == new.id, new.layout.litEnd > old.layout.litEnd + 0.5 || new.doneUnits > old.doneUnits,
              new.layout.litEnd > 0 else { return nil }
        if new.shape.isBar {
            guard new.layout.litEnd > old.layout.litEnd else { return nil }
            return SweepSpan(from: old.layout.litEnd, to: new.layout.litEnd)
        }
        let added = Double(max(new.doneUnits - old.doneUnits, 1))
        let gap = InProgressPanelMetrics.tickGap
        let to = new.layout.litEnd
        return SweepSpan(from: max(0, to - added * (new.shape.tickWidth + gap) + gap), to: to)
    }
}
