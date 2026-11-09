import Foundation

/// The color of Home's big title, taken from the picture above it: the most common real color in the picture. The date
/// above it takes the most common color that is clearly a different color (`dateColor`), else a lighter shade of the
/// title's.
///
/// The picture is mostly shadow, so its biggest swatch is often black or close to it. Those are skipped and the next
/// most common color is used instead. A color that is dark but still a color (a deep red, a navy) is kept and, since
/// the title sits on the darkened picture, lifted until it reads. When the picture has nothing but black the answer is
/// nil and the title stays white.
nonisolated enum HomeTitleTint {
    /// Below this lightness (CIELAB L*, 0 black to 100 white) a swatch is black, whatever its hue: the picture's shadow.
    static let blackLightness = 12.0
    /// Below this, a swatch with almost no color is a near-black grey, also skipped. A deep red or navy has plenty of
    /// color, so it stays.
    static let darkGreyLightness = 22.0
    static let greyChroma = 10.0
    /// A kept color is lifted until it is at least this light, so it reads on the dark picture.
    static let readableLightness = 50.0
    /// How many colors the picture is counted into: more than a poster's five, since its top colors are often one
    /// family (two blues, two browns) and the date needs one that isn't.
    static let candidateCount = 8
    /// The date's color sits at least this far round the color wheel from the title's (CIELAB hue, degrees): blue
    /// against orange, not light blue against dark blue.
    static let dateHueGap = 40.0
    /// And has at least this much color once made readable, so the picture's greys never win.
    static let dateMinimumChroma = 15.0
    /// With no color different enough, the date takes the title's color mixed this far toward white.
    static let dateFallbackMix = 0.45

    /// The title's color for a picture's swatches, or nil when every one of them is black or near-black.
    static func color(from swatches: [PosterSwatch]) -> MeterTint.RGB? {
        colors(from: swatches).first
    }

    /// The date's color: the most common of the picture's other real colors that is clearly a different color from the
    /// title's (`dateHueGap` round the wheel, `dateMinimumChroma` of color), compared as they are shown, after being made
    /// readable. With none, a lighter shade of the title's color. Nil only when the title has no color (all dark).
    static func dateColor(from swatches: [PosterSwatch]) -> MeterTint.RGB? {
        let found = colors(from: swatches)
        guard let title = found.first else { return nil }
        let titleTone = lab(of: title)
        for candidate in found.dropFirst() {
            let tone = lab(of: candidate)
            guard chroma(tone) >= dateMinimumChroma else { continue }
            // A title with almost no color has no hue to stay away from: any real color is different from it.
            if chroma(titleTone) < dateMinimumChroma || hueGap(titleTone, tone) >= dateHueGap {
                return candidate
            }
        }
        return title.mixed(toward: .white, by: dateFallbackMix)
    }

    static func chroma(_ tone: (lightness: Double, a: Double, b: Double)) -> Double {
        (tone.a * tone.a + tone.b * tone.b).squareRoot()
    }

    /// How far apart two colors' hues are round the CIELAB color wheel, 0 to 180 degrees.
    static func hueGap(
        _ first: (lightness: Double, a: Double, b: Double),
        _ second: (lightness: Double, a: Double, b: Double)
    ) -> Double {
        let difference = abs(atan2(first.b, first.a) - atan2(second.b, second.a)) * 180 / .pi
        return min(difference, 360 - difference)
    }

    /// The picture's real colors, most common first, each made readable: black, near-black greys and specks left out.
    static func colors(from swatches: [PosterSwatch]) -> [MeterTint.RGB] {
        swatches.sorted(by: { $0.share > $1.share }).compactMap { swatch in
            guard swatch.share >= MeterTint.minimumShare, let rgb = MeterTint.RGB(hex: swatch.hex) else { return nil }
            let tone = lab(of: rgb)
            if tone.lightness < blackLightness { return nil }
            if tone.lightness < darkGreyLightness, chroma(tone) < greyChroma { return nil }
            return readable(rgb)
        }
    }

    /// `rgb` as it is when light enough, else mixed toward white a step at a time until it reads. Mixing with white
    /// keeps the hue; it only takes color out. A dark color is first brought up to the meter's brightness floor, as
    /// the meter does (`MeterTint.minimumBrightness`).
    static func readable(_ rgb: MeterTint.RGB) -> MeterTint.RGB {
        if lab(of: rgb).lightness >= readableLightness { return rgb }
        let hsb = rgb.hsb
        var result = MeterTint.RGB(
            hue: hsb.hue,
            saturation: hsb.saturation,
            brightness: max(hsb.brightness, MeterTint.minimumBrightness)
        )
        var step = 0
        while lab(of: result).lightness < readableLightness, step < 6 {
            result = result.mixed(toward: .white, by: 0.1)
            step += 1
        }
        return result
    }

    static func lab(of rgb: MeterTint.RGB) -> (lightness: Double, a: Double, b: Double) {
        let values = PosterPaletteExtractor.lab(red: rgb.red * 255, green: rgb.green * 255, blue: rgb.blue * 255)
        return (values.0, values.1, values.2)
    }
}

extension PosterPaletteStore {
    /// Home's picture, counted into `HomeTitleTint.candidateCount` colors and kept apart from the posters' five.
    nonisolated static let homePicture = PosterPaletteStore {
        await PosterPaletteExtractor.load(url: $0, limit: HomeTitleTint.candidateCount)
    }
}
