import Foundation

/// Whether, and how strongly, a list's art lights the glass plate on the list's page. Only art with a real color does:
/// a white, grey or black cover would turn the plate into a grey slab with a white glare, so it leaves the glass clear.
/// Light art is dimmed in step, so the plate's white text keeps reading on it.
enum ListPlateLight {
    /// A swatch this colorful (CIELAB chroma) or more counts as a real color.
    static let minimumChroma = 20.0
    /// And it has to cover this much of the art, so a speck of color doesn't light a white cover.
    static let minimumShare = 0.08
    /// Above this mean lightness (CIELAB L*, 0 black to 100 white) the light is dimmed.
    static let dimmingLightness = 55.0
    static let faintestOpacity = 0.5

    /// The light's opacity for the art's swatches, or nil when the art has no real color (or none were counted).
    static func opacity(for swatches: [PosterSwatch]?) -> Double? {
        guard let swatches, !swatches.isEmpty else { return nil }
        let tones = swatches.compactMap { swatch in
            MeterTint.RGB(hex: swatch.hex).map { (tone: HomeTitleTint.lab(of: $0), share: swatch.share) }
        }
        let hasColor = tones.contains { swatch in
            swatch.share >= minimumShare
                && swatch.tone.lightness >= HomeTitleTint.blackLightness
                && HomeTitleTint.chroma(swatch.tone) >= minimumChroma
        }
        guard hasColor else { return nil }
        let total = tones.reduce(0) { $0 + $1.share }
        guard total > 0 else { return nil }
        let lightness = tones.reduce(0) { $0 + $1.tone.lightness * $1.share } / total
        guard lightness > dimmingLightness else { return 1 }
        return max(faintestOpacity, 1 - (lightness - dimmingLightness) / 60)
    }
}
