import CoreGraphics
import Nuke
import PictureStrip
import SwiftUI
import UIKit

/// A picture at the top of a page that starts under the status bar instead of behind it, with the strip behind the
/// status bar grown from the picture's own top (the PictureStrip package): the media page's and a list's backdrop, a
/// profile's backdrop and an episode's still. Home's picture does the same in its own bake (`HomeBackdropBand`).
nonisolated enum TopStrip {
    /// The strip's height as a share of the picture's width: taller than any iPhone's status bar plus the 6 pt these
    /// pictures reach above the screen (65 of 430 pt on a 14 Pro Max, 68 of 402 on a 17 Pro).
    static let share: CGFloat = 0.18
}

/// Crops the image around its center to the page's shape (`aspect`, width over height) and grows `TopStrip.share` of
/// its width above it. The output is one opaque bitmap: the strip, then the picture.
nonisolated struct TopStripProcessor: ImageProcessing, Hashable {
    /// Rounded, so the same page on the same phone always makes the same cache key.
    let aspect: CGFloat

    init(aspect: CGFloat) {
        self.aspect = (aspect * 1000).rounded() / 1000
    }

    var identifier: String {
        "com.armaan.spine.topstrip?aspect=\(aspect)&share=\(TopStrip.share)&quilt=1"
    }

    /// The part of a `width` x `height` image with `aspect`'s shape, centered.
    static func cropRect(width: CGFloat, height: CGFloat, aspect: CGFloat) -> CGRect {
        var cropWidth = width
        var cropHeight = width / aspect
        if cropHeight > height {
            cropHeight = height
            cropWidth = height * aspect
        }
        cropWidth = max(1, cropWidth.rounded())
        cropHeight = max(1, cropHeight.rounded())
        return CGRect(
            x: ((width - cropWidth) / 2).rounded(),
            y: ((height - cropHeight) / 2).rounded(),
            width: cropWidth,
            height: cropHeight
        )
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        guard let source = image.cgImage, aspect > 0 else { return nil }
        let crop = Self.cropRect(width: CGFloat(source.width), height: CGFloat(source.height), aspect: aspect)
        guard let picture = source.cropping(to: crop) else { return nil }
        let band = max(1, Int((CGFloat(picture.width) * TopStrip.share).rounded()))
        guard let grown = PictureStrip.grow(picture, band: band, sharpRows: picture.height) else { return nil }
        return PlatformImage(cgImage: grown, scale: image.scale, orientation: image.imageOrientation)
    }
}

/// A `TopStrip` picture in its frame: the picture from `pictureTop` to the frame's bottom, the grown strip above it.
/// Clear until it has loaded.
struct TopStripPictureView: View {
    let url: URL?
    /// Where the picture itself starts, from the frame's top: under the status bar.
    let pictureTop: CGFloat

    var body: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            let pictureHeight = proxy.size.height - pictureTop
            if width > 0, pictureHeight > 0 {
                let band = width * TopStrip.share
                SpineAsyncImage(
                    url: url,
                    thumbnailSize: CGSize(width: width, height: pictureHeight),
                    processors: [TopStripProcessor(aspect: width / pictureHeight)],
                    pipeline: SpineImagePipeline.strips
                ) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .frame(width: width, height: pictureHeight + band)
                            .offset(y: pictureTop - band)
                    } else {
                        Color.clear
                    }
                }
            }
        }
        .clipped()
    }
}
