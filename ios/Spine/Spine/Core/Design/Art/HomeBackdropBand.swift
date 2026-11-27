import CoreGraphics
import Nuke
import PictureStrip
import UIKit

/// The strip above Home's picture, grown from the picture's own top so it reads as more of the picture (see the
/// PictureStrip package: patches of the picture's top joined along their least different paths, nothing mirrored,
/// blurred or stretched). The package is compiled optimized in every build, so Spine Dev grows it as fast as the App
/// Store build does.
nonisolated enum HomeBackdropBand {
    /// The strip's height as a share of the picture's width: taller than any iPhone's status bar for its width (59 of
    /// 430 pt on a 14 Pro Max, 62 of 402 on a 17 Pro), so the view only ever shows its lower part.
    static let share: CGFloat = 0.17

    /// The picture with the strip grown above it (`share` of its width taller), or nil when its top is too busy.
    /// `sharpRows`: how far down the picture is still the sharp backdrop; the patches come only from there.
    static func grow(_ picture: CGImage, sharpRows: Int) -> CGImage? {
        let band = max(1, Int((CGFloat(picture.width) * share).rounded()))
        return PictureStrip.grow(picture, band: band, sharpRows: sharpRows)
    }

    /// Home's picture with its strip: the same bake as `HomeBackdropArt.request`, with the strip above it. Goes through
    /// `SpineImagePipeline.strips`, so it is made once and kept on disk.
    static func request(url: URL?) -> ImageRequest? {
        SpineImagePipeline.request(
            url: url,
            thumbnailSize: HomeBackdropArt.thumbnailSize,
            processors: [HomeBackdropStripProcessor()]
        )
    }
}

/// `HomeBackdropMeltProcessor`'s bitmap with `HomeBackdropBand`'s strip above it; a busy top gives
/// `TopStrip.noStrip`, and Home shows its picture the way it always has.
nonisolated struct HomeBackdropStripProcessor: ImageProcessing, Hashable {
    var identifier: String {
        HomeBackdropMeltProcessor().identifier + "&band=\(HomeBackdropBand.share)&quilt=\(PictureStrip.version)"
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        guard let source = image.cgImage,
              let melted = HomeBackdropMeltProcessor().process(image)?.cgImage else { return nil }
        // The picture's sharp part: the 3:2 strip above its lower `fade`.
        let crop = HomeBackdropMeltProcessor.cropRect(width: CGFloat(source.width), height: CGFloat(source.height))
        let sharpRows = Int((crop.height * (1 - HomeBackdropArt.fade)).rounded(.down))
        guard let grown = HomeBackdropBand.grow(melted, sharpRows: sharpRows) else {
            return TopStrip.noStrip(scale: image.scale)
        }
        return PlatformImage(cgImage: grown, scale: image.scale, orientation: image.imageOrientation)
    }
}
