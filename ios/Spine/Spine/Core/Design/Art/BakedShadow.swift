import SwiftUI
import UIKit

/// A drop shadow drawn once into a bitmap, for posters that animate: a live `.shadow` re-renders its blur on
/// every frame of a moving poster, and a baked one is just an image that moves. The bitmap holds only the
/// shadow (the poster covers the shape that casts it), so a rotated poster shows no rectangle peeking out.
nonisolated enum BakedShadow {
    /// How far the bitmap reaches past the rectangle on every side, so the blur and the offset are never cut off.
    static func padding(blur: CGFloat) -> CGFloat {
        2 * blur
    }

    /// The bitmap's size in points: the rectangle plus `padding` on every side.
    static func imageSize(size: CGSize, blur: CGFloat) -> CGSize {
        CGSize(width: size.width + 2 * padding(blur: blur), height: size.height + 2 * padding(blur: blur))
    }

    /// `blur` and `offsetY` are the design's CSS values (the mock's poster shadow is `0 14px 28px black 0.5`);
    /// a positive `offsetY` moves the shadow down. Cached by its parameters.
    static func image(
        size: CGSize,
        cornerRadius: CGFloat,
        blur: CGFloat,
        opacity: Double,
        offsetY: CGFloat,
        scale: CGFloat = UIGraphicsImageRendererFormat.preferred().scale
    ) -> UIImage {
        let key = "\(size.width)x\(size.height)|\(cornerRadius)|\(blur)|\(opacity)|\(offsetY)|\(scale)" as NSString
        if let cached = cache.object(forKey: key) { return cached }

        let canvas = imageSize(size: size, blur: blur)
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        format.opaque = false
        let rendered = UIGraphicsImageRenderer(size: canvas, format: format).image { renderer in
            let context = renderer.cgContext
            let pad = padding(blur: blur)
            // The shape is drawn one canvas-width to the left, off the bitmap, and the shadow offset carries
            // the shadow back.
            let shift = canvas.width
            let shape = CGRect(x: pad - shift, y: pad, width: size.width, height: size.height)
            context.setShadow(
                offset: CGSize(width: shift, height: offsetY),
                blur: blur,
                color: UIColor.black.withAlphaComponent(opacity).cgColor
            )
            context.setFillColor(UIColor.black.cgColor)
            context.addPath(UIBezierPath(roundedRect: shape, cornerRadius: cornerRadius).cgPath)
            context.fillPath()
        }
        cache.setObject(rendered, forKey: key)
        return rendered
    }

    /// The slice of a glow for a capsule of any width and 36 pt tall (a template image: tint it with
    /// `foregroundStyle`). The capsule is 38 wide, so the bitmap is 38 + 4 * blur wide with a 2 pt column in the
    /// middle that stretches: stretch it with `resizable(capInsets:)` using `capsuleSliceInsets`. Cached.
    static func capsuleSlice(blur: CGFloat) -> UIImage {
        image(
            size: CGSize(width: capsuleSliceWidth, height: capsuleSliceHeight),
            cornerRadius: capsuleSliceHeight / 2,
            blur: blur,
            opacity: 1,
            offsetY: 0
        )
    }

    static let capsuleSliceWidth: CGFloat = 38
    static let capsuleSliceHeight: CGFloat = 36

    /// Leaves a 2 pt column to stretch, whatever the blur.
    static func capsuleSliceInsets(blur: CGFloat) -> EdgeInsets {
        let pad = padding(blur: blur)
        return EdgeInsets(
            top: pad + capsuleSliceHeight / 2 - 1,
            leading: pad + capsuleSliceWidth / 2 - 1,
            bottom: pad + capsuleSliceHeight / 2 - 1,
            trailing: pad + capsuleSliceWidth / 2 - 1
        )
    }

    nonisolated(unsafe) private static let cache = NSCache<NSString, UIImage>()

    // MARK: Capsules

    /// A capsule's shadow bitmap: a capsule `height` tall and `3 * height` wide, padded by `padding(blur:)`, at full
    /// opacity (the view that shows it tints it, and the tint carries the alpha). Its middle stretches, so the one image
    /// shadows a pill of any width: `capInsets` keep the two ends. Cached by its parameters.
    static func capsule(
        height: CGFloat,
        blur: CGFloat,
        offsetY: CGFloat,
        scale: CGFloat = UIGraphicsImageRendererFormat.preferred().scale
    ) -> (image: UIImage, capInsets: UIEdgeInsets) {
        let pad = padding(blur: blur)
        let insets = UIEdgeInsets(top: 0, left: pad + height / 2, bottom: 0, right: pad + height / 2)
        let key = "capsule|\(height)|\(blur)|\(offsetY)|\(scale)" as NSString
        if let cached = cache.object(forKey: key) { return (cached, insets) }

        let size = CGSize(width: 3 * height, height: height)
        let canvas = imageSize(size: size, blur: blur)
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        format.opaque = false
        let rendered = UIGraphicsImageRenderer(size: canvas, format: format).image { renderer in
            let context = renderer.cgContext
            // Drawn off the bitmap, one canvas-width to the left, with the shadow offset carrying the shadow back.
            let shift = canvas.width
            let shape = CGRect(x: pad - shift, y: pad, width: size.width, height: size.height)
            context.setShadow(
                offset: CGSize(width: shift, height: offsetY),
                blur: blur,
                color: UIColor.black.cgColor
            )
            context.setFillColor(UIColor.black.cgColor)
            context.addPath(UIBezierPath(roundedRect: shape, cornerRadius: height / 2).cgPath)
            context.fillPath()
        }
        cache.setObject(rendered, forKey: key)
        return (rendered, insets)
    }
}

/// The baked shadow of a poster of `size`, centered on it and reaching past it without taking any layout space.
/// Put it behind the poster: `.background { BakedShadowView(...) }`.
struct BakedShadowView: View {
    let size: CGSize
    var cornerRadius: CGFloat = HomeDesign.Metric.posterRadius
    var blur: CGFloat = 28
    var opacity: Double = 0.5
    var offsetY: CGFloat = 14
    /// A color to draw the shadow in: the bitmap is made at full opacity and shown as a template, so the color carries
    /// the alpha (`opacity` is ignored). Nil is a black shadow at `opacity`.
    var tint: Color?

    var body: some View {
        let canvas = BakedShadow.imageSize(size: size, blur: blur)
        Color.clear
            .frame(width: size.width, height: size.height)
            .overlay {
                if let tint {
                    Image(uiImage: BakedShadow.image(
                        size: size,
                        cornerRadius: cornerRadius,
                        blur: blur,
                        opacity: 1,
                        offsetY: offsetY
                    ))
                    .renderingMode(.template)
                    .resizable()
                    .frame(width: canvas.width, height: canvas.height)
                    .foregroundStyle(tint)
                } else {
                    Image(uiImage: BakedShadow.image(
                        size: size,
                        cornerRadius: cornerRadius,
                        blur: blur,
                        opacity: opacity,
                        offsetY: offsetY
                    ))
                    .resizable()
                    .frame(width: canvas.width, height: canvas.height)
                }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}
