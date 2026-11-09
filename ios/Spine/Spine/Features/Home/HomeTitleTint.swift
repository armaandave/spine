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

    /// While a tile is open the header sits on the glow, the blurred art of the same poster, so a color is brightened
    /// until it stands out from the glow by this contrast ratio (WCAG's 3:1 for large text).
    static let backgroundContrast = 3.0
    /// A color is never mixed more than this far toward white, so it stays its own color on the brightest glow.
    static let maximumWhiteMix = 0.5
    /// The black gradient over the glow where the open header sits (`GlowSpec.gradientStops` at about 0.2 to 0.27 of
    /// its height), and the glow's own opacity over the black page: together, how much the glow is darkened there.
    static let glowDimming = GlowSpec.imageOpacity * (1 - GlowSpec.headerShade)

    /// The title's color for a picture's swatches, or nil when every one of them is black or near-black. With
    /// `background` (the glow's swatches), the same color, brightened until it reads on the glow.
    static func color(from swatches: [PosterSwatch], against background: [PosterSwatch]? = nil) -> MeterTint.RGB? {
        standingOut(colors(from: swatches).first, against: background)
    }

    /// The date's color: the most common of the picture's other real colors that is clearly a different color from the
    /// title's (`dateHueGap` round the wheel, `dateMinimumChroma` of color), compared as they are shown, after being made
    /// readable. With none, a lighter shade of the title's color. Nil only when the title has no color (all dark). With
    /// `background`, the same color, brightened until it reads on the glow.
    static func dateColor(from swatches: [PosterSwatch], against background: [PosterSwatch]? = nil) -> MeterTint.RGB? {
        standingOut(pickedDateColor(from: swatches), against: background)
    }

    private static func pickedDateColor(from swatches: [PosterSwatch]) -> MeterTint.RGB? {
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

    /// `color` as it is when it already stands out from the glow; else brightened first (full brightness keeps all its
    /// color), then mixed toward white a step at a time, until it does or is halfway to white (`maximumWhiteMix`).
    /// Never dropped: a poster's color stays its color, only lighter.
    static func standingOut(_ color: MeterTint.RGB?, against background: [PosterSwatch]?) -> MeterTint.RGB? {
        guard var result = color, let glow = glowLuminance(of: background) else { return color }
        if contrast(result, glow) >= backgroundContrast { return result }
        let hsb = result.hsb
        for step in 1...4 where contrast(result, glow) < backgroundContrast {
            let brightness = hsb.brightness + (1 - hsb.brightness) * Double(step) / 4
            result = MeterTint.RGB(hue: hsb.hue, saturation: hsb.saturation, brightness: brightness)
        }
        let bright = result
        let steps = Int((maximumWhiteMix * 10).rounded())
        for step in 1...steps where contrast(result, glow) < backgroundContrast {
            result = bright.mixed(toward: .white, by: Double(step) * 0.1)
        }
        return result
    }

    /// How light the glow is behind the header: the glow's own baked image (`PosterPaletteStore.homeGlow`), blurred
    /// far past its shapes, so its colors averaged by share (in linear light, as the eye adds light), then darkened by
    /// the shade over it there (`glowDimming`).
    static func glowLuminance(of swatches: [PosterSwatch]?) -> Double? {
        let weighted = (swatches ?? []).compactMap { swatch in
            MeterTint.RGB(hex: swatch.hex).map {
                (TrendingOpenTint.luminance($0.mixed(toward: MeterTint.RGB(red: 0, green: 0, blue: 0), by: 1 - glowDimming)), swatch.share)
            }
        }
        let total = weighted.reduce(0) { $0 + $1.1 }
        guard total > 0 else { return nil }
        return weighted.reduce(0) { $0 + $1.0 * $1.1 } / total
    }

    /// The WCAG contrast ratio of a color against a luminance, 1 to 21.
    static func contrast(_ color: MeterTint.RGB, _ luminance: Double) -> Double {
        let own = TrendingOpenTint.luminance(color)
        return (max(own, luminance) + 0.05) / (min(own, luminance) + 0.05)
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

    /// Home's glow as it is drawn: the same baked, darkened image the glow shows (one cache entry with it), so the
    /// header is checked against what is really behind it, not the raw poster.
    nonisolated static let homeGlow = PosterPaletteStore { url in
        guard let request = BlurredArt.request(url: url, style: .glow) else { return nil }
        return await PosterPaletteExtractor.load(request: request, limit: HomeTitleTint.candidateCount)
    }
}
