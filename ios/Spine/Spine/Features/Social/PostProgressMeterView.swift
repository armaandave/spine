import SwiftUI

/// The bar a feed card draws, on its post page: the same meter, tinted with the poster's colors the way the card tints
/// it (the server's palette, else the colors counted on this phone, else the plain bar). The page is always white text
/// on darkened art, so it takes the card's dark-ink look. A log's bar ends in its check, as on the card.
struct PostProgressMeterView: View {
    let meter: ProgressMeterModel
    let art: FeedCardArtSpec
    let check: Bool

    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var counted: [PosterSwatch]?

    private let ink = ArtInk.dark

    init(meter: ProgressMeterModel, art: FeedCardArtSpec, check: Bool = false) {
        self.meter = meter
        self.art = art
        self.check = check
        _counted = State(initialValue: Self.known(art: art))
    }

    var body: some View {
        let accent = ink.accent(posterAccentHex: art.accentHex)
        let tint = FeedMeterColors.tint(
            server: art.posterPalette, counted: counted, highContrast: contrast == .increased, ink: ink
        )
        HStack(alignment: .top, spacing: HomeDesign.Metric.checkGap) {
            ZStack(alignment: .topLeading) {
                ProgressMeterView(model: meter, palette: FeedMeterColors.palette(ink: ink, accent: accent, tint: nil))
                    .opacity(tint == nil ? 1 : 0)
                if let tint {
                    ProgressMeterView(model: meter, palette: FeedMeterColors.palette(ink: ink, accent: accent, tint: tint))
                        .transition(.opacity)
                }
            }
            if check {
                LogCheckMark(color: FeedMeterColors.palette(ink: ink, accent: accent, tint: tint).todayColor)
                    .offset(y: (ProgressMeterMetrics.tickHeight - HomeDesign.Metric.checkSize) / 2)
            }
        }
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: tint != nil)
        .task(id: FeedMeterColors.countURL(hasMeter: true, art: art), priority: .utility) { await countColors() }
    }

    private static func known(art: FeedCardArtSpec) -> [PosterSwatch]? {
        guard let url = FeedMeterColors.countURL(hasMeter: true, art: art) else { return nil }
        return PosterPaletteStore.shared.known(server: nil, url: url)
    }

    private func countColors() async {
        guard counted == nil, let url = FeedMeterColors.countURL(hasMeter: true, art: art) else { return }
        counted = await PosterPaletteStore.shared.swatches(server: nil, url: url)
    }
}
