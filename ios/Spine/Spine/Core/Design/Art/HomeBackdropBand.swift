import CoreGraphics
import PictureStrip

/// The strip above Home's picture, grown from the picture's own top so it reads as more of the picture (see the
/// PictureStrip package: patches of the picture's top joined along their least different paths, nothing mirrored,
/// blurred or stretched). The package is compiled optimized in every build, so Spine Dev grows it as fast as the App
/// Store build does.
nonisolated enum HomeBackdropBand {
    /// The strip's height as a share of the picture's width: taller than any iPhone's status bar for its width (59 of
    /// 430 pt on a 14 Pro Max, 62 of 402 on a 17 Pro), so the view only ever shows its lower part.
    static let share: CGFloat = 0.17

    /// The picture with the strip grown above it (`share` of its width taller). `sharpRows`: how far down the picture is
    /// still the sharp backdrop; the patches come only from there.
    static func grow(_ picture: CGImage, sharpRows: Int) -> CGImage? {
        let band = max(1, Int((CGFloat(picture.width) * share).rounded()))
        return PictureStrip.grow(picture, band: band, sharpRows: sharpRows)
    }
}
