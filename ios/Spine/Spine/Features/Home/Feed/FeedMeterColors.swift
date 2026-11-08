import SwiftUI

/// The colors a feed card's bar (the solid bar and the tick meter) takes from its poster, chosen the way the In
/// progress panel chooses them: the server's palette when the row's media carries one, else the colors counted on this
/// device, else nothing, and the bar keeps its plain white look. Pure, so a card's body only looks the answer up.
nonisolated enum FeedMeterColors {
    /// The poster's tint for a card with `ink`, or nil for the plain bar.
    ///
    /// Only dark-ink cards (white text on darkened art) take a tint: `MeterTint` lifts its colors to read on a dark
    /// tile, and on the rare light-ink card (dark text on very bright art) those bright colors would not read, so that
    /// card keeps its current dark palette.
    static func tint(
        server: [PosterSwatch]?,
        counted: [PosterSwatch]?,
        highContrast: Bool,
        ink: ArtInk
    ) -> MeterTint? {
        guard ink == .dark else { return nil }
        return MeterTint.choose(server: server, counted: counted, highContrast: highContrast)
    }

    /// The bar's palette. With a tint it is In progress's mapping: earlier days run along the poster's ramp at 80 %
    /// (`MeterPalette.tintOpacity`), today's part is the poster's brightest color, and the rest is the card's own
    /// track (white at 16 % on dark art, the same as the panel's). Without one it is the card's plain palette, today
    /// in the poster's accent.
    static func palette(ink: ArtInk, accent: Color, tint: MeterTint?) -> MeterPalette {
        guard let tint else { return MeterPalette(before: ink.before, rest: ink.rest, accent: accent) }
        var palette = MeterPalette(before: ink.before, rest: ink.rest, accent: tint.today.color, tint: tint)
        // The series line under a window meter is one color, so it takes the middle of the ramp.
        palette.before = tint.color(at: 0.5).opacity(palette.tintOpacity)
        return palette
    }

    /// The poster whose colors a card would count: the card's own poster when it shows a meter and the server sent no
    /// usable palette. Nil when nothing needs counting, so a card without a bar never starts a count.
    @MainActor
    static func countURL(for model: FeedCardModel) -> URL? {
        countURL(hasMeter: model.meter != nil, art: model.art)
    }

    @MainActor
    static func countURL(hasMeter: Bool, art: FeedCardArtSpec) -> URL? {
        guard hasMeter, MeterTint.choose(server: art.posterPalette, counted: nil) == nil else { return nil }
        return FeedCardArt.url(from: art.posterURL)
    }
}
