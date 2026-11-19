import QuartzCore
import SwiftUI
import UIKit

/// The first thing a cold launch shows after the still launch screen: the logo's glow beats softly on the plaster while
/// the app gets ready, then the logo glides into Home's header and lands beside "Home", as the plaster darkens and Home
/// fades in under it (after the motion lab's "Breathe + lift, beside Home", made calmer).
///
/// It starts as an exact copy of `LaunchScreen.storyboard`, so nothing jumps when the app takes over from it, and it
/// ends exactly on the header's own logo and word, which it then hands over to. All of the motion is Core Animation,
/// so it keeps its frame rate however busy the app is while Home loads.
///
/// Only Home gets the flight. Signed out, it fades into onboarding; with Reduce Motion on, it holds still and fades.
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
    let reduceMotion: Bool

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

    init(reduceMotion: Bool? = nil) {
        self.reduceMotion = reduceMotion ?? UIAccessibility.isReduceMotionEnabled
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
        phase = .done
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
        phase = .landing
        // At most a beat to settle, then the glide.
        endAnyway(after: .seconds(LaunchChoreography.beatPeriod + LaunchChoreography.duration + 1))
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

/// The launch's motion as numbers. While the app loads, the logo's glow beats softly: it swells, settles and rests.
/// Then, from rest, the logo glides into Home's header and shrinks to the header's size, the plaster darkens and Home
/// fades in under it, and "Home" slides in beside the logo.
struct LaunchChoreography {
    /// The logo's center on the launch screen, and on the header's logo.
    var start: CGPoint
    var end: CGPoint
    /// The logo's size in the header, as a share of its size on the launch screen.
    var endScale: CGFloat

    /// The glide, from leaving the launch screen to "Home" settling beside the logo.
    static let duration: CFTimeInterval = 1.2
    /// One beat: the glow swells, settles back, then rests until the next one.
    static let swell: CFTimeInterval = 0.5
    static let settle: CFTimeInterval = 0.9
    static let beatPeriod: CFTimeInterval = 1.9
    /// The glow at rest (the launch screen, the glide, and the header's logo), and at the top of a beat.
    static let restGlow: CGFloat = 0.55
    static let peakGlow: CGFloat = 0.8
    /// How far the glow spreads at the top of a beat.
    static let peakGlowSpread: CGFloat = 1.04
    /// How far "Home" slides in beside the logo.
    static let wordSlide: CGFloat = 11
    /// How far below the header the glide's curve dips, so the logo swings up into place.
    static let swing: CGFloat = 40
    /// When the logo is in place (the rest is "Home" settling beside it).
    static let landed: CGFloat = 0.8

    struct Frame: Equatable {
        var center: CGPoint
        var scale: CGFloat
        /// The plaster over Home, and the black between them, as two plain layers (no group opacity).
        var plaster: CGFloat
        var black: CGFloat
        /// "Home" fading in and sliding the last few points into place.
        var word: CGFloat
    }

    /// The beat `t` seconds after the first one began: 0 at rest, 1 at the top of the swell.
    static func beat(at t: CFTimeInterval) -> CGFloat {
        let t = intoBeat(t)
        if t < swell { return CGFloat(0.5 - 0.5 * cos(.pi * t / swell)) }
        if t < swell + settle { return CGFloat(0.5 + 0.5 * cos(.pi * (t - swell) / settle)) }
        return 0
    }

    static func glow(_ beat: CGFloat) -> CGFloat { restGlow + (peakGlow - restGlow) * beat }
    static func glowSpread(_ beat: CGFloat) -> CGFloat { 1 + (peakGlowSpread - 1) * beat }

    /// When the logo sets off, `ready` and `firstBeat` on the same clock: after at least one whole beat, and only while
    /// the glow is at rest, so it leaves looking exactly like the launch screen.
    static func departure(ready: CFTimeInterval, firstBeat: CFTimeInterval) -> CFTimeInterval {
        let earliest = max(ready, firstBeat + swell + settle)
        let into = intoBeat(earliest - firstBeat)
        return into < swell + settle ? earliest + (swell + settle - into) : earliest
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
        // The plaster darkens to black, and Home fades in over it.
        let dark = C.inOut(C.seg(u, 0.1, 0.6))
        let home = C.out(C.seg(u, 0.35, 0.85))
        // As two layers over Home, black under plaster: plaster at (1 - home)(1 - dark), and black at what makes up
        // the rest of (1 - home) dark.
        let plaster = (1 - home) * (1 - dark)
        let black = plaster >= 1 ? 0 : (1 - home) * dark / (1 - plaster)
        return Frame(
            center: center,
            scale: 1 + (endScale - 1) * fly,
            plaster: plaster,
            black: black,
            word: C.out(C.seg(u, 0.7, 1))
        )
    }

    private static func intoBeat(_ t: CFTimeInterval) -> CFTimeInterval {
        t - beatPeriod * (t / beatPeriod).rounded(.down)
    }

    private static func seg(_ u: CGFloat, _ a: CGFloat, _ b: CGFloat) -> CGFloat { min(1, max(0, (u - a) / (b - a))) }
    private static func inOut(_ t: CGFloat) -> CGFloat { t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2 }
    private static func out(_ t: CGFloat) -> CGFloat { 1 - pow(1 - t, 3) }
}

/// The launch logo's images and size, from the brand kit (rendered by `spine-notes/brand/motion/launch-assets`).
enum LaunchLogo {
    /// The mark's height on the launch screen: 150 pt wide, and the mark is 710 x 803.
    static let markHeight: CGFloat = 150 * 803 / 710
    /// The launch storyboard centers the logo with its glow, an image this big.
    static let launchImageSize = CGSize(width: 330, height: 350)
}

// MARK: - The overlay

/// The launch overlay inside SwiftUI: a UIKit view whose layers Core Animation moves on its own.
struct LaunchOverlay: UIViewRepresentable {
    let handoff: LaunchHandoff
    /// Passed in so a new phase reaches `updateUIView`.
    let phase: LaunchHandoff.Phase

    func makeUIView(context: Context) -> LaunchOverlayView {
        let view = LaunchOverlayView(beats: !handoff.reduceMotion)
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

/// The overlay's layers: a copy of the launch screen whose glow beats, then glides into the header or fades.
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
        static var firstBeat: CFTimeInterval?
        static var glide: CFTimeInterval?
        static var fade: CFTimeInterval?
    }

    private let plaster = CALayer()
    private let black = CALayer()
    private let word = CALayer()
    /// The logo: its glow and body, moved and scaled together.
    private let logo = CALayer()
    private let glow = CALayer()
    private let body = CALayer()
    private let beats: Bool
    /// What SwiftUI asked for, started once this view is on screen at its size.
    private var motion: Motion?
    private var hasStartedMoving = false
    /// Every animation running here. Moving a view can strip its layers' animations; they are put back as they were.
    private var timeline: [(layer: CALayer, key: String, animation: CAAnimation)] = []

    init(beats: Bool) {
        self.beats = beats
        super.init(frame: .zero)
        isAccessibilityElement = false
        accessibilityElementsHidden = true

        let bodyImage = UIImage(named: "LaunchLogoBody")
        let glowImage = UIImage(named: "LaunchLogoHalo")

        plaster.backgroundColor = UIColor(named: "LaunchBackground")?.cgColor
        plaster.contents = UIImage(named: "LaunchPlaster")?.cgImage
        black.backgroundColor = UIColor.black.cgColor
        black.opacity = 0

        logo.bounds = CGRect(origin: .zero, size: bodyImage?.size ?? .zero)
        body.frame = logo.bounds
        body.contents = bodyImage?.cgImage
        glow.bounds = CGRect(origin: .zero, size: glowImage?.size ?? .zero)
        glow.position = CGPoint(x: logo.bounds.midX, y: logo.bounds.midY)
        glow.contents = glowImage?.cgImage
        glow.opacity = Float(LaunchChoreography.restGlow)
        // Smooth all the way down to the header's size.
        for layer in [body, glow] {
            layer.minificationFilter = .trilinear
        }
        logo.addSublayer(glow)
        logo.addSublayer(body)
        word.opacity = 0

        // Black under the plaster: see `LaunchChoreography.Frame`.
        for sublayer in [black, plaster, word, logo] {
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
        plaster.frame = bounds
        black.frame = bounds
        for layer in [plaster, body, glow, word] {
            layer.contentsScale = scale
        }
        logo.position = launchCenter
        CATransaction.commit()
        startMotionIfReady()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard window != nil else { return }
        if beats, !hasStartedMoving, timeline.isEmpty {
            startBeating()
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

    /// The glow's beat, looped, from rest (the launch screen) at the first frame.
    private func startBeating() {
        let begin = Clock.firstBeat ?? CACurrentMediaTime()
        Clock.firstBeat = begin
        let steps = 114
        let beat = (0...steps).map {
            LaunchChoreography.beat(at: LaunchChoreography.beatPeriod * Double($0) / Double(steps))
        }
        func loop(_ keyPath: String, _ value: (CGFloat) -> CGFloat) {
            let animation = Self.keyframes(keyPath, beat.map { value($0) }, duration: LaunchChoreography.beatPeriod)
            animation.repeatCount = .infinity
            animation.beginTime = begin
            play(animation, on: glow, key: "beat.\(keyPath)")
        }
        loop("opacity") { LaunchChoreography.glow($0) }
        loop("transform.scale") { LaunchChoreography.glowSpread($0) }
    }

    /// Lets the beat run until `time` (a moment at rest), then stop there.
    private func stopBeating(at time: CFTimeInterval) {
        timeline = timeline.map { item in
            guard item.key.hasPrefix("beat."), let copy = item.animation.copy() as? CAAnimation else { return item }
            copy.repeatCount = 0
            copy.repeatDuration = max(0.001, time - copy.beginTime)
            item.layer.add(copy, forKey: item.key)
            return (item.layer, item.key, copy)
        }
    }

    private func play(_ animation: CAAnimation, on layer: CALayer, key: String) {
        timeline.append((layer, key, animation))
        layer.add(animation, forKey: key)
    }

    /// Glides the logo into the header's logo and slides "Home" in beside it, once the glow is at rest.
    private func land(logoFrame: CGRect, wordFrame: CGRect, wordImage: UIImage?) {
        let target = convert(logoFrame, from: nil)
        let wordRect = convert(wordFrame, from: nil)
        let landed = UIImage(named: "HomeLogo")
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
        let begin = Clock.glide ?? Clock.firstBeat.map { LaunchChoreography.departure(ready: now, firstBeat: $0) } ?? now
        Clock.glide = begin
        let plan = LaunchChoreography(
            start: launchCenter,
            end: end,
            endScale: target.height / LaunchLogo.markHeight
        )
        let steps = Int((LaunchChoreography.duration * 120).rounded())
        let frames = (0...steps).map { plan.frame(at: CGFloat($0) / CGFloat(steps)) }
        let duration = LaunchChoreography.duration

        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stopBeating(at: begin)
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
        run(plaster, "opacity", frames.map(\.plaster))
        run(black, "opacity", frames.map(\.black))
        run(word, "opacity", frames.map(\.word))
        run(word, "position.x", frames.map { wordCenter.x - LaunchChoreography.wordSlide * (1 - $0.word) })
        // Once the logo is in place, its body becomes the header's own image at its exact size: shrunk this far, the
        // big image is a touch softer, and the hand-over at the end must change nothing.
        if let landed, let landedImage = landed.cgImage {
            let landedBounds = CGRect(
                origin: .zero,
                size: CGSize(width: landed.size.width / plan.endScale, height: landed.size.height / plan.endScale)
            )
            func swap(_ keyPath: String, to value: Any, from old: Any?) {
                let animation = CAKeyframeAnimation(keyPath: keyPath)
                animation.values = [old as Any, value]
                animation.keyTimes = [0, NSNumber(value: Double(LaunchChoreography.landed)), 1]
                animation.calculationMode = .discrete
                animation.duration = duration
                run(body, keyPath, animation, endingAt: value)
            }
            swap("contents", to: landedImage, from: body.contents)
            swap("bounds", to: NSValue(cgRect: landedBounds), from: NSValue(cgRect: body.bounds))
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
