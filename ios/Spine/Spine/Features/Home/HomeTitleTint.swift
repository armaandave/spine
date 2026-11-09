import Foundation

/// The color of Home's big title, taken from the picture above it: the most common real color in the picture.
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

    /// The title's color for a picture's swatches, or nil when every one of them is black or near-black.
    static func color(from swatches: [PosterSwatch]) -> MeterTint.RGB? {
        for swatch in swatches.sorted(by: { $0.share > $1.share }) where swatch.share >= MeterTint.minimumShare {
            guard let rgb = MeterTint.RGB(hex: swatch.hex) else { continue }
            let tone = lab(of: rgb)
            let chroma = (tone.a * tone.a + tone.b * tone.b).squareRoot()
            if tone.lightness < blackLightness { continue }
            if tone.lightness < darkGreyLightness, chroma < greyChroma { continue }
            return readable(rgb)
        }
        return nil
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
