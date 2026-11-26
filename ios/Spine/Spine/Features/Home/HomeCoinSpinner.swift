import SwiftUI
import UIKit

/// The coin's moves, apart from drawing them: how far it has turned for a pull, how fast it spins while Home reloads,
/// where it lands, and how its glow and the light across its face follow the turn.
nonisolated enum HomeCoinChoreography {
    /// Turns a second while Home reloads.
    static let turnsPerSecond: CGFloat = 3
    /// Radians a second while it spins.
    static var spinVelocity: CGFloat { turnsPerSecond * 2 * .pi }
    /// How long it carries its spin into the landing before the spring turns it face up, in seconds of spin.
    static let carry: CGFloat = 0.1

    /// The turn for a pull: none at rest, and one whole turn, face up again, by the time letting go reloads. Eased, so
    /// it starts and finishes slowly.
    static func angle(pull: CGFloat) -> CGFloat {
        let progress = min(1, max(0, pull / HomeRefreshModel.threshold))
        let eased = progress < 0.5 ? 4 * pow(progress, 3) : 1 - pow(-2 * progress + 2, 3) / 2
        return eased * 2 * .pi
    }

    /// Its size over the pull: half size at first, full most of the way down.
    static func scale(pull: CGFloat) -> CGFloat {
        let progress = min(1, max(0, pull / (HomeRefreshModel.threshold * 0.85)))
        return 0.5 + 0.5 * (1 - pow(1 - progress, 3))
    }

    /// Where it lands: the next angle face up once it has carried its spin a moment longer.
    static func landingAngle(from angle: CGFloat) -> CGFloat {
        let turn = 2 * CGFloat.pi
        return ((angle + spinVelocity * carry) / turn).rounded(.up) * turn
    }

    /// The warm glow at an angle: a flash each time a face turns to the screen (the mark is the same both ways round).
    static func glow(angle: CGFloat) -> CGFloat {
        0.18 + 0.5 * pow(abs(cos(angle)), 8)
    }

    /// The light across the face at an angle, in widths of the face from its centre, and how bright: brightest as the
    /// coin turns edge on.
    static func glint(angle: CGFloat) -> (offset: CGFloat, opacity: CGFloat) {
        (-sin(angle) * 0.9, 0.8 * abs(sin(angle)))
    }
}

/// The logo above Home while it's pulled and while it reloads: a coin (`HomeCoinSpinnerView`). It sits in the room
/// Home opens for it, `HomeRefreshModel.gap` tall.
struct HomeCoinSpinner: UIViewRepresentable {
    let pull: CGFloat
    let phase: HomeRefreshModel.Phase

    func makeUIView(context: Context) -> HomeCoinSpinnerView {
        HomeCoinSpinnerView()
    }

    func updateUIView(_ view: HomeCoinSpinnerView, context: Context) {
        view.show(pull: pull, phase: phase, reduceMotion: context.environment.accessibilityReduceMotion)
    }
}

/// The coin in 3D: a face, a back, and an edge of thin slices between them, turned about its upright axis with
/// perspective. Core Animation runs the spin, so it stays smooth while Home reloads on the main thread.
final class HomeCoinSpinnerView: UIView {
    private let stage = CALayer()
    private let glow = CALayer()
    private let coin = CATransformLayer()
    private let front = CALayer()
    private let glint = CAGradientLayer()
    private var phase: HomeRefreshModel.Phase = .pulling
    private var pull: CGFloat = 0
    private var reduceMotion = false
    /// After landing, the coin stays as it landed while Home closes the room, until the next pull starts: the page
    /// has to come back to rest first (`sawRest`), so its bounce back from the last pull doesn't count as a new one.
    private var holdsLanding = false
    private var sawRest = false
    /// When the spin started and from what angle, to know where it is when it lands.
    private var spinStart: CFTimeInterval = 0
    private var spinFrom: CGFloat = 0
    /// Whether this spin is the Reduce Motion glow, so its landing matches it.
    private var spinsStill = false

    private static let thickness: CGFloat = 2.6
    private static let slices = 6
    private static let edge = MeterTint.RGB(red: 0x8F / 255, green: 0x86 / 255, blue: 0x7B / 255)

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        accessibilityElementsHidden = true
        build()
        layer.addSublayer(stage)
        stage.opacity = 0
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func build() {
        let shape = UIImage(named: "HomeLogoShape")
        let size = shape?.size ?? CGSize(width: 28, height: 32)
        let scale = shape?.scale ?? 3
        let bounds = CGRect(origin: .zero, size: size)
        let center = CGPoint(x: bounds.midX, y: bounds.midY)
        let faceImage = LaunchLogo.drawn("HomeLogoShape", LaunchLogo.milk, over: "HomeLogoRim")?.cgImage
        let edgeImage = LaunchLogo.drawn("HomeLogoShape", Self.edge)?.cgImage

        stage.bounds = bounds
        var perspective = CATransform3DIdentity
        perspective.m34 = -1 / 240
        stage.sublayerTransform = perspective

        let glowImage = LaunchLogo.drawn("HomeLogoGlow", LaunchLogo.milkGlow)
        glow.contents = glowImage?.cgImage
        glow.contentsScale = glowImage?.scale ?? scale
        glow.bounds = CGRect(origin: .zero, size: glowImage?.size ?? size)
        glow.position = center
        stage.addSublayer(glow)

        coin.bounds = bounds
        coin.position = center
        stage.addSublayer(coin)

        // The edge: slices of the shape between the back and the face, darker, so it shows as the coin turns edge on.
        for slice in 1...Self.slices {
            let layer = CALayer()
            layer.contents = edgeImage
            layer.contentsScale = scale
            layer.frame = bounds
            layer.zPosition = -Self.thickness / 2 + Self.thickness * CGFloat(slice) / CGFloat(Self.slices + 1)
            coin.addSublayer(layer)
        }
        let back = CALayer()
        back.contents = faceImage
        back.contentsScale = scale
        back.frame = bounds
        back.transform = CATransform3DMakeRotation(.pi, 0, 1, 0)
        back.zPosition = -Self.thickness / 2
        back.isDoubleSided = false
        coin.addSublayer(back)

        front.contents = faceImage
        front.contentsScale = scale
        front.frame = bounds
        front.zPosition = Self.thickness / 2
        front.isDoubleSided = false
        let mask = CALayer()
        mask.contents = shape?.cgImage
        mask.contentsScale = scale
        mask.frame = bounds
        front.mask = mask
        glint.colors = [UIColor(white: 1, alpha: 0).cgColor, UIColor(white: 1, alpha: 0.9).cgColor, UIColor(white: 1, alpha: 0).cgColor]
        glint.locations = [0.35, 0.5, 0.65]
        glint.startPoint = CGPoint(x: 0, y: 0.5)
        glint.endPoint = CGPoint(x: 1, y: 0.5)
        glint.bounds = CGRect(x: 0, y: 0, width: size.width * 2, height: size.height)
        glint.position = center
        glint.opacity = 0
        front.addSublayer(glint)
        coin.addSublayer(front)
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        place()
    }

    /// Where the coin sits: in the middle of the room the pull has opened so far, then of the whole room.
    private func place() {
        let room = phase == .pulling && !holdsLanding ? min(pull, HomeRefreshModel.gap) : HomeRefreshModel.gap
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stage.position = CGPoint(x: bounds.midX, y: room / 2)
        CATransaction.commit()
    }

    func show(pull: CGFloat, phase: HomeRefreshModel.Phase, reduceMotion: Bool) {
        self.pull = pull
        self.reduceMotion = reduceMotion
        if phase != self.phase {
            let old = self.phase
            self.phase = phase
            switch phase {
            case .spinning: spin()
            case .landing: land()
            case .pulling: if old != .pulling { leave() }
            }
        }
        guard phase == .pulling else { return place() }
        if holdsLanding {
            if pull <= 0.5 { sawRest = true }
            guard sawRest, pull > 0.5 else { return }
            holdsLanding = false
            stage.removeAllAnimations()
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        turn(to: reduceMotion ? 0 : HomeCoinChoreography.angle(pull: pull))
        stage.opacity = Float(HomeRefreshModel.spinnerOpacity(pull: pull, isRefreshing: false))
        stage.setValue(HomeCoinChoreography.scale(pull: pull), forKeyPath: "transform.scale")
        CATransaction.commit()
        place()
    }

    private func turn(to angle: CGFloat) {
        coin.setValue(angle, forKeyPath: "transform.rotation.y")
        glow.opacity = Float(HomeCoinChoreography.glow(angle: angle))
        let light = HomeCoinChoreography.glint(angle: angle)
        glint.position.x = front.bounds.midX + light.offset * front.bounds.width
        glint.opacity = Float(light.opacity)
    }

    /// Letting go reloads: it spins on its edge, three turns a second, its glow flashing each time a face comes round.
    private func spin() {
        let from = (coin.value(forKeyPath: "transform.rotation.y") as? CGFloat) ?? 2 * .pi
        spinFrom = from
        spinStart = CACurrentMediaTime()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stage.opacity = 1
        stage.setValue(1, forKeyPath: "transform.scale")
        CATransaction.commit()
        place()
        spinsStill = reduceMotion
        if spinsStill {
            let pulse = CABasicAnimation(keyPath: "opacity")
            pulse.fromValue = 0.25
            pulse.toValue = 0.75
            pulse.duration = 0.6
            pulse.autoreverses = true
            pulse.repeatCount = .infinity
            glow.add(pulse, forKey: "spin")
            return
        }
        let period = CFTimeInterval(1 / HomeCoinChoreography.turnsPerSecond)
        let rotation = CABasicAnimation(keyPath: "transform.rotation.y")
        rotation.fromValue = from
        rotation.toValue = from + 2 * .pi
        rotation.duration = period
        rotation.repeatCount = .infinity
        coin.add(rotation, forKey: "spin")
        glow.add(oneTurn("opacity", from: from, period: period) { Float(HomeCoinChoreography.glow(angle: $0)) }, forKey: "spin")
        let width = front.bounds.width, middle = front.bounds.midX
        glint.add(oneTurn("position.x", from: from, period: period) { middle + HomeCoinChoreography.glint(angle: $0).offset * width }, forKey: "spin")
        glint.add(oneTurn("opacity", from: from, period: period) { Float(HomeCoinChoreography.glint(angle: $0).opacity) }, forKey: "spinLight")
    }

    /// Something that follows the coin round once a turn, over and over.
    private func oneTurn(_ keyPath: String, from: CGFloat, period: CFTimeInterval, _ value: (CGFloat) -> Any) -> CAKeyframeAnimation {
        let steps = 36
        let animation = CAKeyframeAnimation(keyPath: keyPath)
        animation.values = (0...steps).map { value(from + 2 * .pi * CGFloat($0) / CGFloat(steps)) }
        animation.keyTimes = (0...steps).map { NSNumber(value: Double($0) / Double(steps)) }
        animation.duration = period
        animation.repeatCount = .infinity
        return animation
    }

    /// The reload is done: it slows into a spring that lands it face up, then pops once and glows.
    private func land() {
        for layer in [coin, glow, glint] as [CALayer] { layer.removeAllAnimations() }
        if spinsStill {
            glow.opacity = Float(HomeCoinChoreography.glow(angle: 0))
            return
        }
        let angle = spinFrom + HomeCoinChoreography.spinVelocity * CGFloat(CACurrentMediaTime() - spinStart)
        let target = HomeCoinChoreography.landingAngle(from: angle)
        let spring = CASpringAnimation(keyPath: "transform.rotation.y")
        spring.fromValue = angle
        spring.toValue = target
        spring.mass = 1
        spring.stiffness = 420
        spring.damping = 26
        spring.initialVelocity = HomeCoinChoreography.spinVelocity / max(0.01, target - angle)
        spring.duration = spring.settlingDuration
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        coin.setValue(target, forKeyPath: "transform.rotation.y")
        glint.opacity = 0
        glow.opacity = Float(HomeCoinChoreography.glow(angle: target))
        CATransaction.commit()
        coin.add(spring, forKey: "land")

        let flash = CAKeyframeAnimation(keyPath: "opacity")
        flash.values = [0.3, 0.95, glow.opacity]
        flash.keyTimes = [0, 0.45, 1]
        flash.duration = 0.45
        glow.add(flash, forKey: "land")

        let pop = CAKeyframeAnimation(keyPath: "transform.scale")
        pop.values = [1, 1.14, 1]
        pop.keyTimes = [0, 0.45, 1]
        pop.duration = 0.24
        pop.beginTime = stage.convertTime(CACurrentMediaTime(), from: nil) + 0.08
        pop.fillMode = .backwards
        stage.add(pop, forKey: "land")
    }

    /// Home closes the room: the landed coin fades and shrinks away, and stays put until the next pull.
    private func leave() {
        holdsLanding = true
        sawRest = false
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = stage.presentation()?.opacity ?? stage.opacity
        fade.toValue = 0
        let shrink = CABasicAnimation(keyPath: "transform.scale")
        shrink.fromValue = 1
        shrink.toValue = 0.7
        let group = CAAnimationGroup()
        group.animations = [fade, shrink]
        group.duration = 0.3
        group.timingFunction = CAMediaTimingFunction(name: .easeIn)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        stage.opacity = 0
        stage.setValue(0.7, forKeyPath: "transform.scale")
        CATransaction.commit()
        stage.add(group, forKey: "leave")
    }
}
