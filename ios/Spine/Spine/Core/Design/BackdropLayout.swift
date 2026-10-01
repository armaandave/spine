import CoreGraphics

enum BackdropLayout {
    static let topOffset: CGFloat = -6

    static func safeAreaCompensation(for topInset: CGFloat) -> CGFloat {
        topInset + topOffset
    }
}
