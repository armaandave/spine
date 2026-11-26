import QuartzCore
import SwiftUI
import UIKit

/// The first thing a cold launch shows after the still launch screen: the logo, an empty outline on the plaster, fills
/// from the bottom while the app gets ready, and the plaster takes on the main color of Home's picture as it fills
/// while the logo fills in the picture's second color (`LaunchPalette`). Then the logo glides into Home's header and
/// lands beside "Home" as Home comes in under it (the motion lab's "Colour rises with the fill").
///
/// It starts as an exact copy of `LaunchScreen.storyboard`, so nothing jumps when the app takes over from it, and it
/// ends exactly on the header's own logo and word, which it then hands over to. All of the motion is Core Animation,
/// so it keeps its frame rate however busy the app is while Home loads.
///
/// Only Home gets the glide. Signed out, it fades into onboarding; with Reduce Motion on, it stays an outline and fades.
@MainActor
@Observable
final class LaunchHandoff {
    enum Phase: Equatable {
        /// The logo is on the plaster, waiting for Home.
        case covering
        /// The logo is flying into the header.
        case landing
        /// The overlay is fading out, with no flight.
        case fading
        /// The overlay is gone.
        case done
    }

    /// Where the app is in signing in, as far as the launch cares.
    enum Session: Equatable {
        case checking
        case signedIn
        case signedOut
    }

    private(set) var phase: Phase = .covering
    /// The header's logo and "Home" stay hidden while the overlay's copies of them are on their way.
    private(set) var hidesHeaderLogo: Bool
    /// The color the overlay's "Home" is drawn in, which the header's word keeps until the hand-over, so a picture
    /// whose colors arrive mid-glide doesn't change the word as it lands. Nil once the header shows its own color.
    private(set) var heldWordColor: Color?
    /// The same for the header logo's glow: the color the overlay's glow lands in.
    private(set) var heldGlowColor: MeterTint.RGB?
    let reduceMotion: Bool
    /// The colors of Home's picture as Home last saw it, or nil for pale rose and milk.
    let palette: LaunchPalette?

    /// The header's logo and word, in window coordinates, and the word's color: where the flight lands.
    @ObservationIgnored private(set) var logoFrame: CGRect?
    @ObservationIgnored private(set) var wordFrame: CGRect?
    @ObservationIgnored private(set) var wordColor: Color = .white

    @ObservationIgnored private var isSignedIn = false
    @ObservationIgnored private var homeIsReady = false
    @ObservationIgnored private var waitLimit: Task<Void, Never>?
    @ObservationIgnored private var finishAnyway: Task<Void, Never>?

    /// How long a signed-in launch waits for Home's profile before it lands anyway.
    static let longestWaitForHome: Duration = .milliseconds(1500)

    init(reduceMotion: Bool? = nil, palette: LaunchPalette? = LaunchPalette.saved()) {
        self.reduceMotion = reduceMotion ?? UIAccessibility.isReduceMotionEnabled
        self.palette = palette
        hidesHeaderLogo = !self.reduceMotion
    }

    func sessionChanged(_ session: Session) {
        guard phase == .covering else { return }
        switch session {
        case .checking:
            break
        case .signedOut:
            fadeOut()
        case .signedIn:
            guard !isSignedIn else { return }
            isSignedIn = true
            waitLimit = Task { [weak self] in
                try? await Task.sleep(for: Self.longestWaitForHome)
                guard !Task.isCancelled else { return }
                self?.waitLimitReached()
            }
            advance()
        }
    }

    /// Home can be shown: its profile is in (or failed), on the Home tab.
    func homeReadinessChanged(_ ready: Bool) {
        guard phase == .covering else { return }
        homeIsReady = ready
        advance()
    }

    func headerMoved(logo: CGRect) {
        guard phase == .covering else { return }
        logoFrame = logo
        advance()
    }

    func headerMoved(word: CGRect) {
        guard phase == .covering else { return }
        wordFrame = word
        advance()
    }

    func headerWordColorChanged(_ color: Color) {
        guard phase == .covering else { return }
        wordColor = color
    }

    /// The overlay finished: the header shows its own logo and word again.
    func finish() {
        guard phase != .done else { return }
        waitLimit?.cancel()
        finishAnyway?.cancel()
        hidesHeaderLogo = false
        heldWordColor = nil
        heldGlowColor = nil
        phase = .done
        LaunchLogo.forgetTints()
    }

    private func advance() {
        guard phase == .covering, isSignedIn, homeIsReady else { return }
        if reduceMotion {
            fadeOut()
        } else if logoFrame != nil, wordFrame != nil {
            land()
        }
    }

    private func waitLimitReached() {
        guard phase == .covering else { return }
        if !reduceMotion, logoFrame != nil, wordFrame != nil {
            land()
        } else {
            fadeOut()
        }
    }

    private func land() {
        waitLimit?.cancel()
        heldWordColor = wordColor
        heldGlowColor = LaunchLogo.headerGlow(palette)
        phase = .landing
        // At most the rest of the fill, then the glide.
        endAnyway(after: .seconds(LaunchChoreography.fillDuration + LaunchChoreography.duration + 1))
    }

    private func fadeOut() {
        waitLimit?.cancel()
        hidesHeaderLogo = false
        phase = .fading
        endAnyway(after: .seconds(1.5))
    }

    /// The overlay ends itself when its animation runs out. If something cut the animation short, this ends it a
    /// little later anyway, so the overlay never stays over Home (it holds the last frame, which is Home's own look).
    private func endAnyway(after delay: Duration) {
        finishAnyway = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled else { return }
            self?.finish()
        }
    }
}

// MARK: - The motion

/// The launch's motion as numbers (the motion lab's "Colour rises with the fill"). The launch screen shows the logo as
/// an empty outline on the plaster; while the app loads, it fills from the bottom like a rising level, a gentle wave on
/// top, its glow coming up with it, and the plaster takes on the picture's main color as the level rises. Once it's
/// full, the logo glides into Home's header and shrinks to the header's size, Home comes in under it, and "Home" slides
/// in beside the logo.
struct LaunchChoreography {
    /// The logo's center on the launch screen, and on the header's logo.
    var start: CGPoint
    var end: CGPoint
    /// The logo's size in the header, as a share of its size on the launch screen.
    var endScale: CGFloat

    /// From the empty outline to the full logo.
    static let fillDuration: CFTimeInterval = 1.4
    /// The wave on the rising level: how high it is, how long, and how long it takes to travel its own length.
    static let waveHeight: CGFloat = 2
    static let waveLength: CGFloat = 180
    static let wavePeriod: CFTimeInterval = 1.25
    /// The glow once the logo is full, the same as on the header's logo.
    static let fullGlow: CGFloat = 0.55
    /// The glide, from leaving the launch screen to "Home" settling beside the logo.
    static let duration: CFTimeInterval = 1.2
    /// How far "Home" slides in beside the logo.
    static let wordSlide: CGFloat = 11
    /// How far below the header the glide's curve dips, so the logo swings up into place.
    static let swing: CGFloat = 40
    /// When the logo is in place (the rest is "Home" settling beside it).
    static let landed: CGFloat = 0.8

    struct Frame: Equatable {
        var center: CGPoint
        var scale: CGFloat
        /// The empty outline around the full logo, which goes as the glide begins.
        var outline: CGFloat
        /// Pale rose launches: the plaster over Home, and the black between them, as two plain layers.
        var plaster: CGFloat
        var black: CGFloat
        /// Colored launches: the picture's color over Home, going as Home comes in.
        var color: CGFloat
        /// The logo's color moving from the launch's to the header's.
        var toHeader: CGFloat
        /// "Home" fading in and sliding the last few points into place.
        var word: CGFloat
    }

    /// How full the logo is `t` seconds after the launch screen: 0 empty, 1 full.
    static func fill(at t: CFTimeInterval) -> CGFloat {
        inOut(CGFloat(min(1, max(0, t / fillDuration))))
    }

    /// When the logo sets off, `ready` and `firstFrame` on the same clock: once Home is ready, and not before it's full.
    static func departure(ready: CFTimeInterval, firstFrame: CFTimeInterval) -> CFTimeInterval {
        max(ready, firstFrame + fillDuration)
    }

    func frame(at u: CGFloat) -> Frame {
        typealias C = LaunchChoreography
        let fly = C.inOut(C.seg(u, 0, C.landed))
        let q = 1 - fly
        let control = CGPoint(x: start.x * 0.9, y: end.y + C.swing)
        let center = CGPoint(
            x: q * q * start.x + 2 * q * fly * control.x + fly * fly * end.x,
            y: q * q * start.y + 2 * q * fly * control.y + fly * fly * end.y
        )
        // Pale rose: the plaster darkens to black, and Home fades in over it. As two layers over Home, black under
        // plaster: plaster at (1 - home)(1 - dark), and black at what makes up the rest of (1 - home) dark.
        let dark = C.inOut(C.seg(u, 0.1, 0.6))
        let home = C.out(C.seg(u, 0.35, 0.85))
        let plaster = (1 - home) * (1 - dark)
        let black = plaster >= 1 ? 0 : (1 - home) * dark / (1 - plaster)
        return Frame(
            center: center,
            scale: 1 + (endScale - 1) * fly,
            outline: 1 - C.out(C.seg(u, 0, 0.25)),
            plaster: plaster,
            black: black,
            // A picture's color goes straight into Home: they're the same colors.
            color: 1 - C.out(C.seg(u, 0.25, 0.85)),
            toHeader: C.inOut(C.seg(u, 0.2, C.landed)),
            word: C.out(C.seg(u, 0.7, 1))
        )
    }

    private static func seg(_ u: CGFloat, _ a: CGFloat, _ b: CGFloat) -> CGFloat { min(1, max(0, (u - a) / (b - a))) }
    private static func inOut(_ t: CGFloat) -> CGFloat { t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2 }
    private static func out(_ t: CGFloat) -> CGFloat { 1 - pow(1 - t, 3) }
}

/// The launch logo's images and size, from the brand kit (rendered by `spine-notes/brand/motion/launch-assets`). The
/// logo is a white shape and rim the app tints, so it can take any picture's colors.
enum LaunchLogo {
    /// The mark on the launch screen: 150 pt wide, and the mark is 710 x 803.
    static let markWidth: CGFloat = 150
    static let markHeight: CGFloat = 150 * 803 / 710
    /// The launch storyboard centers the empty outline, an image this big (the logo's shape is the same size).
    static let launchImageSize = CGSize(width: 158, height: 178)
    /// The brand's own colors, with no picture: milk, with a warm glow.
    static let milk = MeterTint.RGB(red: 0xEF / 255, green: 0xEA / 255, blue: 0xDF / 255)
    static let milkGlow = MeterTint.RGB(red: 1, green: 0xED / 255, blue: 0xD6 / 255)

    /// The glow around a logo of `color`: a lighter shade of it (milk's is warm).
    static func glowColor(for color: MeterTint.RGB) -> MeterTint.RGB {
        color == milk ? milkGlow : color.mixed(toward: .white, by: 0.35)
    }

    /// In the header the logo is milk, its glow in the picture's main color (or milk's own warm glow).
    static func headerGlow(_ palette: LaunchPalette?) -> MeterTint.RGB {
        palette?.headerGlow ?? milkGlow
    }

    /// How strong the header logo's glow is: a picture's color needs more to read as that color than milk's soft glow.
    static func headerGlowOpacity(for glow: MeterTint.RGB) -> CGFloat {
        glow == milkGlow ? LaunchChoreography.fullGlow : 0.8
    }

    /// The images tinted so far this launch: SwiftUI makes the overlay again as the app opens, and the new one takes
    /// these instead of drawing them again. Emptied once the launch is over (`forgetTints`).
    private static var tints: [String: UIImage] = [:]

    /// The asset `mask` (a white image whose alpha is the shape) in `color`, with the asset `over` drawn as it is on
    /// top: the logo's shape in a color with its white rim, or its glow in a color. Kept for the rest of the launch.
    static func tinted(_ mask: String, _ color: MeterTint.RGB, over: String? = nil) -> UIImage? {
        let key = "\(mask) \(color.red) \(color.green) \(color.blue) \(over ?? "")"
        if let made = tints[key] { return made }
        let made = drawn(mask, color, over: over)
        tints[key] = made
        return made
    }

    /// The same image drawn fresh and not kept, for views made after the launch (Home's refresh coin).
    static func drawn(_ mask: String, _ color: MeterTint.RGB, over: String? = nil) -> UIImage? {
        guard let image = UIImage(named: mask) else { return nil }
        let top = over.flatMap { UIImage(named: $0) }
        let format = UIGraphicsImageRendererFormat()
        format.scale = image.scale
        format.opaque = false
        return UIGraphicsImageRenderer(size: image.size, format: format).image { context in
            let rect = CGRect(origin: .zero, size: image.size)
            UIColor(red: color.red, green: color.green, blue: color.blue, alpha: 1).setFill()
            context.fill(rect)
            image.draw(in: rect, blendMode: .destinationIn, alpha: 1)
            top?.draw(in: rect)
        }
    }

    static func forgetTints() {
        tints = [:]
    }
}

// MARK: - The overlay

/// The launch overlay inside SwiftUI: a UIKit view whose layers Core Animation moves on its own.
struct LaunchOverlay: UIViewRepresentable {
    let handoff: LaunchHandoff
    /// Passed in so a new phase reaches `updateUIView`.
    let phase: LaunchHandoff.Phase

    func makeUIView(context: Context) -> LaunchOverlayView {
        let view = LaunchOverlayView(fills: !handoff.reduceMotion, palette: handoff.palette)
        view.onFinish = { [handoff] in handoff.finish() }
        return view
    }

    func updateUIView(_ view: LaunchOverlayView, context: Context) {
        switch phase {
        case .covering, .done:
            break
        case .landing:
            guard let logo = handoff.logoFrame, let word = handoff.wordFrame else { return view.show(.fade) }
            let wordImage = HomeTitleWord.image(color: handoff.wordColor, scale: context.environment.displayScale)
            view.show(.land(logo: logo, word: word, wordImage: wordImage))
        case .fading:
            view.show(.fade)
        }
    }
}

/// The overlay's layers: a copy of the launch screen whose empty logo fills, the plaster taking on the picture's color,
/// then glides into the header or fades.
///
/// SwiftUI makes this view again when the app's tab bar first appears under it, and may move it. So everything runs on
/// one clock for the whole launch (`Clock`), and a new view picks up exactly where the old one was.
final class LaunchOverlayView: UIView {
    enum Motion {
        /// Frames in window coordinates.
        case land(logo: CGRect, word: CGRect, wordImage: UIImage?)
        case fade
    }

    var onFinish: (() -> Void)?

    /// When each part began, once per launch.
    private enum Clock {
        static var firstFrame: CFTimeInterval?
        static var glide: CFTimeInterval?
        static var fade: CFTimeInterval?
    }

    // The fill and the glide never animate the same property: what fills is nested inside what glides.
    private let plaster = CALayer()
    private let black = CALayer()
    /// The picture's main color and the plaster's light from the top over it (each filling in), which go together
    /// into Home in the glide.
    private let backdrop = CALayer()
    private let color = CALayer()
    private let light = CAGradientLayer()
    private let word = CALayer()
    /// The logo: its outline, glow and body, moved and scaled together.
    private let logo = CALayer()
    /// The empty outline, and its stroke in the logo's color coming in as it fills; they go as the glide begins.
    private let outlines = CALayer()
    private let outline = CALayer()
    private let outlineColor = CALayer()
    /// The glow, coming up as it fills, in the logo's color and in the header's, which it turns to on the way in.
    private let glows = CALayer()
    private let glow = CALayer()
    private let headerGlow = CALayer()
    /// The body, revealed by the rising level, in the logo's color and in the header's.
    private let body = CALayer()
    private let launchBody = CALayer()
    private let headerBody = CALayer()
    /// The rising level: the body shows only below it.
    private let level = CAShapeLayer()
    private let fills: Bool
    private let palette: LaunchPalette?
    /// What SwiftUI asked for, started once this view is on screen at its size.
    private var motion: Motion?
    private var hasStartedMoving = false
    /// Every animation running here. Moving a view can strip its layers' animations; they are put back as they were.
    private var timeline: [(layer: CALayer, key: String, animation: CAAnimation)] = []

    init(fills: Bool, palette: LaunchPalette?) {
        self.fills = fills
        self.palette = palette
        super.init(frame: .zero)
        isAccessibilityElement = false
        accessibilityElementsHidden = true

        let logoColor = palette?.logo ?? LaunchLogo.milk
        let headerGlowColor = LaunchLogo.headerGlow(palette)
        let shape = UIImage(named: "LaunchLogoShape")
        let glowMask = UIImage(named: "LaunchLogoGlow")

        plaster.backgroundColor = UIColor(named: "LaunchBackground")?.cgColor
        plaster.contents = UIImage(named: "LaunchPlaster")?.cgImage
        black.backgroundColor = UIColor.black.cgColor
        black.opacity = 0
        if let main = palette?.background {
            color.backgroundColor = UIColor(red: main.red, green: main.green, blue: main.blue, alpha: 1).cgColor
        }
        color.opacity = 0
        // Faded as one in the glide without drawing them offscreen: the light is faint enough not to tell.
        backdrop.allowsGroupOpacity = false
        // The plaster's own light: a little lighter at the top, a little darker at the foot.
        light.colors = [
            UIColor(red: 1, green: 250 / 255, blue: 244 / 255, alpha: 0.10).cgColor,
            UIColor(red: 1, green: 250 / 255, blue: 244 / 255, alpha: 0).cgColor,
            UIColor(white: 0, alpha: 0.14).cgColor,
        ]
        light.locations = [0, 0.45, 1]
        light.opacity = 0

        logo.bounds = CGRect(origin: .zero, size: shape?.size ?? .zero)
        for container in [outlines, glows, body] {
            container.frame = logo.bounds
        }
        outline.frame = logo.bounds
        outline.contents = UIImage(named: "LaunchOutline")?.cgImage
        outlineColor.frame = logo.bounds
        outlineColor.contents = LaunchLogo.tinted("LaunchOutlineStroke", logoColor)?.cgImage
        outlineColor.opacity = 0
        body.frame = logo.bounds
        for (layer, tint) in [(launchBody, logoColor), (headerBody, LaunchLogo.milk)] {
            layer.frame = body.bounds
            layer.contents = LaunchLogo.tinted("LaunchLogoShape", tint, over: "LaunchLogoRim")?.cgImage
            body.addSublayer(layer)
        }
        headerBody.opacity = 0
        for (layer, tint) in [(glow, LaunchLogo.glowColor(for: logoColor)), (headerGlow, headerGlowColor)] {
            layer.bounds = CGRect(origin: .zero, size: glowMask?.size ?? .zero)
            layer.position = CGPoint(x: logo.bounds.midX, y: logo.bounds.midY)
            layer.contents = LaunchLogo.tinted("LaunchLogoGlow", tint)?.cgImage
            glows.addSublayer(layer)
        }
        glow.opacity = Float(LaunchChoreography.fullGlow)
        headerGlow.opacity = 0
        // Empty at first: no glow.
        glows.opacity = 0
        Self.shapeLevel(level, over: body.bounds)
        level.position = Self.levelPosition(fill: 0, in: body.bounds)
        body.mask = level
        // Smooth all the way down to the header's size.
        for layer in [outline, outlineColor, launchBody, headerBody, glow, headerGlow] {
            layer.minificationFilter = .trilinear
        }
        outlines.addSublayer(outline)
        outlines.addSublayer(outlineColor)
        for sublayer in [outlines, glows, body] {
            logo.addSublayer(sublayer)
        }
        word.opacity = 0
        backdrop.addSublayer(color)
        backdrop.addSublayer(light)

        // Black under the plaster: see `LaunchChoreography.Frame`.
        for sublayer in [black, plaster, backdrop, word, logo] {
            layer.addSublayer(sublayer)
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard !hasStartedMoving, bounds.width > 0 else { return }
        let scale = max(1, traitCollection.displayScale)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for layer in [plaster, black, backdrop, color, light] {
            layer.frame = bounds
        }
        for layer in [plaster, outline, outlineColor, launchBody, headerBody, glow, headerGlow, word, level] {
            layer.contentsScale = scale
        }
        logo.position = launchCenter
        CATransaction.commit()
        startMotionIfReady()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard window != nil else { return }
        if fills, !hasStartedMoving, timeline.isEmpty {
            startFilling()
        }
        let now = CACurrentMediaTime()
        for item in timeline where item.layer.animation(forKey: item.key) == nil && now < Self.end(of: item.animation) {
            item.layer.add(item.animation, forKey: item.key)
        }
        // Laid out again now that the screen's scale is known (a layout before this one may have used 1x).
        setNeedsLayout()
        layoutIfNeeded()
        startMotionIfReady()
    }

    /// Glides the logo into the header, or fades: SwiftUI asks once Home is ready, or there is no Home to glide into.
    func show(_ motion: Motion) {
        guard self.motion == nil else { return }
        self.motion = motion
        startMotionIfReady()
    }

    /// The glide's or the fade's clock ran out (not cut short by a move): the header takes over.
    fileprivate func animationEnded(finished: Bool) {
        guard finished else { return }
        timeline.removeAll()
        onFinish?()
    }

    /// The logo's center on the launch screen: centered the way the storyboard centers the launch image, whose origin
    /// Auto Layout puts on a whole pixel.
    private var launchCenter: CGPoint {
        let scale = max(1, traitCollection.displayScale)
        let size = LaunchLogo.launchImageSize
        let origin = CGPoint(
            x: ((bounds.width - size.width) / 2 * scale).rounded() / scale,
            y: ((bounds.height - size.height) / 2 * scale).rounded() / scale
        )
        return CGPoint(x: origin.x + size.width / 2, y: origin.y + size.height / 2)
    }

    private func startMotionIfReady() {
        guard let motion, !hasStartedMoving, window != nil, bounds.width > 0 else { return }
        hasStartedMoving = true
        switch motion {
        case let .land(logo, word, wordImage):
            land(logoFrame: logo, wordFrame: word, wordImage: wordImage)
        case .fade:
            fadeOut()
        }
    }

    /// The level rises from below the mark to above it, its wave drifting sideways; the glow comes up with it, and with a
    /// picture's colors, the plaster takes the picture's main color and the outline the logo's.
    private func startFilling() {
        let begin = Clock.firstFrame ?? CACurrentMediaTime()
        Clock.firstFrame = begin
        let steps = Int((LaunchChoreography.fillDuration * 120).rounded())
        let fill = (0...steps).map {
            LaunchChoreography.fill(at: LaunchChoreography.fillDuration * Double($0) / Double(steps))
        }
        // Each starts at the launch screen's look and ends full; after the fill each holds its full value.
        func filling(_ layer: CALayer, _ keyPath: String, _ animation: CAAnimation, to value: Any?) {
            layer.setValue(value, forKeyPath: keyPath)
            animation.beginTime = begin
            animation.fillMode = .backwards
            play(animation, on: layer, key: "fill.\(keyPath)")
        }
        func filling(_ layer: CALayer, _ keyPath: String, to full: CGFloat) {
            let values = fill.map { $0 * full }
            filling(layer, keyPath, Self.keyframes(keyPath, values, duration: LaunchChoreography.fillDuration), to: full)
        }
        let area = body.bounds
        let rise = CAKeyframeAnimation(keyPath: "position")
        rise.values = fill.map { NSValue(cgPoint: Self.levelPosition(fill: $0, in: area)) }
        rise.keyTimes = Self.evenKeyTimes(fill.count)
        rise.duration = LaunchChoreography.fillDuration
        rise.calculationMode = .linear
        filling(level, "position", rise, to: NSValue(cgPoint: Self.levelPosition(fill: 1, in: area)))

        let drift = CABasicAnimation(keyPath: "transform.translation.x")
        drift.fromValue = 0
        drift.toValue = -LaunchChoreography.waveLength
        drift.duration = LaunchChoreography.wavePeriod
        drift.repeatCount = .infinity
        drift.beginTime = begin
        play(drift, on: level, key: "fill.drift")

        filling(glows, "opacity", to: 1)
        if palette != nil {
            for layer in [color, light, outlineColor] {
                filling(layer, "opacity", to: 1)
            }
        }
    }

    private func play(_ animation: CAAnimation, on layer: CALayer, key: String) {
        timeline.append((layer, key, animation))
        layer.add(animation, forKey: key)
    }

    /// Glides the logo into the header's logo and slides "Home" in beside it, once the logo is full.
    private func land(logoFrame: CGRect, wordFrame: CGRect, wordImage: UIImage?) {
        let target = convert(logoFrame, from: nil)
        let wordRect = convert(wordFrame, from: nil)
        let landed = LaunchLogo.tinted("HomeLogoShape", LaunchLogo.milk, over: "HomeLogoRim")
        let headerGlowStrength = LaunchLogo.headerGlowOpacity(for: LaunchLogo.headerGlow(palette))
        // SwiftUI draws the header's logo and word on whole pixels, so the glide ends with the same pictures on the
        // same pixels (this view fills the window, so its points are the window's).
        let scale = max(1, traitCollection.displayScale)
        func snapped(_ center: CGPoint, _ size: CGSize) -> CGPoint {
            CGPoint(
                x: ((center.x - size.width / 2) * scale).rounded() / scale + size.width / 2,
                y: ((center.y - size.height / 2) * scale).rounded() / scale + size.height / 2
            )
        }
        let end = snapped(CGPoint(x: target.midX, y: target.midY), landed?.size ?? .zero)
        let wordCenter = snapped(CGPoint(x: wordRect.midX, y: wordRect.midY), wordImage?.size ?? .zero)
        let now = CACurrentMediaTime()
        let begin = Clock.glide ?? Clock.firstFrame.map { LaunchChoreography.departure(ready: now, firstFrame: $0) } ?? now
        Clock.glide = begin
        let plan = LaunchChoreography(
            start: launchCenter,
            end: end,
            endScale: target.height / LaunchLogo.markHeight
        )
        let steps = Int((LaunchChoreography.duration * 120).rounded())
        let frames = (0...steps).map { plan.frame(at: CGFloat($0) / CGFloat(steps)) }
        let duration = LaunchChoreography.duration
        let glow = LaunchChoreography.fullGlow

        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if let wordImage {
            word.contents = wordImage.cgImage
            word.contentsScale = wordImage.scale
            word.bounds = CGRect(origin: .zero, size: wordImage.size)
            word.position = wordCenter
        }
        // Each layer is left where its animation ends, so nothing moves when the finished animations are removed;
        // until the glide begins, each holds its first value.
        func run(_ layer: CALayer, _ keyPath: String, _ animation: CAAnimation, endingAt value: Any?) {
            layer.setValue(value, forKeyPath: keyPath)
            animation.beginTime = begin
            animation.fillMode = .backwards
            play(animation, on: layer, key: "glide.\(keyPath)")
        }
        func run(_ layer: CALayer, _ keyPath: String, _ values: [CGFloat]) {
            run(layer, keyPath, Self.keyframes(keyPath, values, duration: duration), endingAt: values.last)
        }
        let path = CAKeyframeAnimation(keyPath: "position")
        path.values = frames.map { NSValue(cgPoint: $0.center) }
        path.keyTimes = Self.evenKeyTimes(frames.count)
        path.duration = duration
        path.calculationMode = .linear
        // The glide's clock: when it runs out, the header takes over.
        path.delegate = AnimationEnd(self)
        run(logo, "position", path, endingAt: NSValue(cgPoint: end))
        run(logo, "transform.scale", frames.map(\.scale))
        run(outlines, "opacity", frames.map(\.outline))
        if palette != nil {
            // The picture's color is fully over the plaster by now (the logo leaves only once it's full), so the
            // plaster goes as the glide begins, and the color goes into Home.
            run(plaster, "opacity", frames.indices.map { $0 == 0 ? 1 : 0 })
            run(backdrop, "opacity", frames.map(\.color))
        } else {
            run(plaster, "opacity", frames.map(\.plaster))
            run(black, "opacity", frames.map(\.black))
        }
        // On the way in, the logo turns back to milk and its glow to the picture's main color (no change without one).
        run(self.glow, "opacity", frames.map { glow * (1 - $0.toHeader) })
        run(headerGlow, "opacity", frames.map { headerGlowStrength * $0.toHeader })
        run(headerBody, "opacity", frames.map(\.toHeader))
        run(word, "opacity", frames.map(\.word))
        run(word, "position.x", frames.map { wordCenter.x - LaunchChoreography.wordSlide * (1 - $0.word) })
        // Once the logo is in place, its body becomes the header's own image at its exact size: shrunk this far, the
        // big image is a touch softer, and the hand-over at the end must change nothing. The level is long past the top
        // by then and reaches well beyond the body, so the bigger bounds stay inside it.
        if let landed, let landedImage = landed.cgImage {
            let landedBounds = CGRect(
                origin: .zero,
                size: CGSize(width: landed.size.width / plan.endScale, height: landed.size.height / plan.endScale)
            )
            func swap(_ layer: CALayer, _ keyPath: String, to value: Any, from old: Any?) {
                let animation = CAKeyframeAnimation(keyPath: keyPath)
                animation.values = [old as Any, value]
                animation.keyTimes = [0, NSNumber(value: Double(LaunchChoreography.landed)), 1]
                animation.calculationMode = .discrete
                animation.duration = duration
                run(layer, keyPath, animation, endingAt: value)
            }
            swap(headerBody, "contents", to: landedImage, from: headerBody.contents)
            swap(headerBody, "bounds", to: NSValue(cgRect: landedBounds), from: NSValue(cgRect: headerBody.bounds))
            swap(launchBody, "opacity", to: NSNumber(value: 0), from: NSNumber(value: 1))
        }
        CATransaction.commit()
    }

    /// Fades the whole overlay out: no glide (signed out, Reduce Motion, or no header to land on).
    private func fadeOut() {
        let begin = Clock.fade ?? CACurrentMediaTime()
        Clock.fade = begin
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1
        fade.toValue = 0
        fade.duration = 0.35
        fade.timingFunction = CAMediaTimingFunction(name: .easeOut)
        fade.beginTime = begin
        fade.delegate = AnimationEnd(self)
        layer.opacity = 0
        play(fade, on: layer, key: "fade")
        CATransaction.commit()
    }

    /// The level's shape, in the body's points: a wave along its top edge, solid below. It reaches a body's width past
    /// either side and a wave's length further right, so drifting by one wave length never shows its ends.
    private static func shapeLevel(_ level: CAShapeLayer, over body: CGRect) {
        let wave = LaunchChoreography.waveHeight
        let length = LaunchChoreography.waveLength
        let width = body.width * 3 + length
        let height = body.height * 4
        let path = CGMutablePath()
        path.move(to: CGPoint(x: 0, y: height))
        var x: CGFloat = 0
        while x <= width {
            path.addLine(to: CGPoint(x: x, y: wave * (1 - sin(2 * .pi * x / length))))
            x += 2
        }
        path.addLine(to: CGPoint(x: width, y: height))
        path.closeSubpath()
        level.anchorPoint = .zero
        level.bounds = CGRect(x: 0, y: 0, width: width, height: height)
        level.path = path
        level.fillColor = UIColor.black.cgColor
    }

    /// Where the level sits at `fill` (0 empty, 1 full): its wave's crest just under the mark when empty, its troughs
    /// just over it when full.
    private static func levelPosition(fill: CGFloat, in body: CGRect) -> CGPoint {
        let markTop = (body.height - LaunchLogo.markHeight) / 2
        let markBottom = markTop + LaunchLogo.markHeight
        let empty = markBottom + 0.5
        let full = markTop - 2 * LaunchChoreography.waveHeight - 0.5
        return CGPoint(x: -body.width, y: empty + (full - empty) * fill)
    }

    /// When an animation on the launch's clock is over.
    private static func end(of animation: CAAnimation) -> CFTimeInterval {
        if animation.repeatCount == .infinity { return .infinity }
        return animation.beginTime + (animation.repeatDuration > 0 ? animation.repeatDuration : animation.duration)
    }

    /// Evenly spaced keyframes with straight lines between them, sampled finely enough to be the curve.
    private static func keyframes(_ keyPath: String, _ values: [CGFloat], duration: CFTimeInterval) -> CAKeyframeAnimation {
        let animation = CAKeyframeAnimation(keyPath: keyPath)
        animation.values = values.map { NSNumber(value: Double($0)) }
        animation.keyTimes = evenKeyTimes(values.count)
        animation.duration = duration
        animation.calculationMode = .linear
        return animation
    }

    private static func evenKeyTimes(_ count: Int) -> [NSNumber] {
        (0..<count).map { NSNumber(value: Double($0) / Double(max(1, count - 1))) }
    }
}

/// Tells the overlay when its glide or fade ends, without the animation keeping the overlay alive: an animation holds
/// its delegate, and the overlay holds its animations.
private final class AnimationEnd: NSObject, CAAnimationDelegate {
    private weak var view: LaunchOverlayView?

    init(_ view: LaunchOverlayView) {
        self.view = view
    }

    func animationDidStop(_ animation: CAAnimation, finished: Bool) {
        view?.animationEnded(finished: finished)
    }
}
