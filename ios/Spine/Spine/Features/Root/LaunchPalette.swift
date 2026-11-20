import Foundation

/// The colors the launch takes from Home's picture: the background takes its main color, the logo its second (moved
/// until it stands out on that background), and once it lands in the header the logo is milk again with a glow in the
/// picture's main color, the title's. Home saves them whenever it works out its picture's colors, so the next launch
/// has them from its first frame. Nil when there is no picture, or it is all shadow: the launch stays pale rose and
/// milk.
nonisolated struct LaunchPalette: Equatable, Sendable {
    var background: MeterTint.RGB
    var logo: MeterTint.RGB
    /// The header logo's glow: the picture's main color as the title shows it.
    var headerGlow: MeterTint.RGB

    /// The logo stands out from the background by at least this contrast ratio (WCAG's 3:1 for large shapes).
    static let logoContrast = 3.0

    init(background: MeterTint.RGB, logo: MeterTint.RGB, headerGlow: MeterTint.RGB) {
        self.background = background
        self.logo = logo
        self.headerGlow = headerGlow
    }

    /// From the picture's swatches: its most common real color, and the next one that is clearly a different color,
    /// picked the way the date picks its color (`HomeTitleTint`), but as they are, not lightened for the header.
    init?(swatches: [PosterSwatch]) {
        let colors = HomeTitleTint.realColors(from: swatches)
        guard let main = colors.first, let header = HomeTitleTint.color(from: swatches) else { return nil }
        let mainTone = HomeTitleTint.lab(of: main)
        let second = colors.dropFirst().first { candidate in
            let tone = HomeTitleTint.lab(of: candidate)
            guard HomeTitleTint.chroma(tone) >= HomeTitleTint.dateMinimumChroma else { return false }
            return HomeTitleTint.chroma(mainTone) < HomeTitleTint.dateMinimumChroma
                || HomeTitleTint.hueGap(mainTone, tone) >= HomeTitleTint.dateHueGap
        } ?? main.mixed(toward: .white, by: HomeTitleTint.dateFallbackMix)
        self.init(background: main, logo: Self.standingOut(second, on: main), headerGlow: header)
    }

    /// `color` as it is when it stands out on `background`; else moved toward white on a dark background (toward black
    /// on a light one) a step at a time until it does.
    static func standingOut(_ color: MeterTint.RGB, on background: MeterTint.RGB) -> MeterTint.RGB {
        let ground = TrendingOpenTint.luminance(background)
        if HomeTitleTint.contrast(color, ground) >= logoContrast { return color }
        let toward = ground < 0.3 ? MeterTint.RGB.white : MeterTint.RGB(red: 0, green: 0, blue: 0)
        for step in 1...20 {
            let moved = color.mixed(toward: toward, by: Double(step) / 20)
            if HomeTitleTint.contrast(moved, ground) >= logoContrast { return moved }
        }
        return toward
    }

    // MARK: Kept for the next launch

    private static let key = "launch.palette.v2"

    /// The palette Home last worked out, read once at launch.
    static func saved(in defaults: UserDefaults = .standard) -> LaunchPalette? {
        guard let hexes = defaults.stringArray(forKey: key), hexes.count == 3,
              let background = MeterTint.RGB(hex: hexes[0]),
              let logo = MeterTint.RGB(hex: hexes[1]),
              let headerGlow = MeterTint.RGB(hex: hexes[2]) else { return nil }
        return LaunchPalette(background: background, logo: logo, headerGlow: headerGlow)
    }

    /// Keeps `palette` for the next launch; nil forgets it, so the next launch is pale rose.
    static func remember(_ palette: LaunchPalette?, in defaults: UserDefaults = .standard) {
        guard let palette else { return defaults.removeObject(forKey: key) }
        defaults.set([palette.background, palette.logo, palette.headerGlow].map(hex), forKey: key)
    }

    private static func hex(_ color: MeterTint.RGB) -> String {
        func byte(_ value: Double) -> Int { Int((min(1, max(0, value)) * 255).rounded()) }
        return String(format: "#%02X%02X%02X", byte(color.red), byte(color.green), byte(color.blue))
    }
}
