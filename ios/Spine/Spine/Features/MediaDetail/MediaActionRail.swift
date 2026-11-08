import SwiftUI
import UIKit

/// What the rail's first slot shows. The media page's is the "+" (`nil`); Home's Trending panel follows where the title
/// stands in your library.
enum RailLead: Equatable {
    case symbol(name: String, size: CGFloat)
    case ring(fraction: Double, tint: Color)

    /// The page's "+".
    static let plus = RailLead.symbol(name: "plus", size: 22)
}

/// One more button between the eye and the stars. Home's Trending panel puts Open here, as an icon alone.
struct RailCenterButton {
    let systemName: String
    let label: String
    let identifier: String
    let action: () -> Void
}

/// What a tap on Home's rail leaves for the title's page to finish with its own screens (a log composer, a sheet): the
/// page runs it once, after its first load has settled.
enum RailHandoff: Equatable {
    case track
    case eye
    case like
    case rate(halfSteps: Int)

    /// Runs the page's function for this handoff.
    func run(
        track: () -> Void,
        eye: () -> Void,
        like: () -> Void,
        rate: (Int) -> Void
    ) {
        switch self {
        case .track: track()
        case .eye: eye()
        case .like: like()
        case let .rate(halfSteps): rate(halfSteps)
        }
    }
}

/// The media page's Liquid Glass action rail, and Home's Trending panel's: a "+" (or Home's state slot), the eye, the
/// stars and the heart in one glass pill, with the rating picker growing out of it. Every addition has a default that
/// gives the page exactly the rail it had.
struct ActionRail: View {
    static let buttonSize: CGFloat = 48
    /// The glass pill's width for `buttonCount` buttons; the open rating picker is at least 183 wide.
    static func surfaceWidth(buttonCount: Int, pickerPresented: Bool) -> CGFloat {
        let railWidth = CGFloat(buttonCount) * buttonSize + 10
        return pickerPresented ? max(railWidth, 183) : railWidth
    }

    /// 29 is a capsule at the rail's 58 pt height; the open picker's corners are 26.
    static func cornerRadius(pickerPresented: Bool) -> CGFloat { pickerPresented ? 26 : 29 }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPressing = false
    @State private var pressHaptics = UIImpactFeedbackGenerator(style: .light)
    @State private var actionHaptics = UISelectionFeedbackGenerator()

    @Binding var ratingPicker: MediaRatingPickerState
    let isLiked: Bool
    var showsEye = true
    var showsRating = false
    var offersRatingAfterBaseAction = false
    var trackLabel: String?
    var eyeLabel: String?
    /// What the eye does, for VoiceOver. Without one, an eye that opens the rating picker says so.
    var eyeHint: String?
    var isEyeSelected = false
    var isEyeLoading = false
    var isLikeLoading = false
    /// Overrides the star's fill, for stars that aren't a current rating (an episode's newest rated log, TV-502).
    var isRatingFilled: Bool?
    /// When set, the star runs this instead of opening the rating picker.
    var onRatingTap: (() -> Void)?
    /// The first slot's content. Nil is the page's "+".
    var lead: RailLead?
    var leadTint: Color?
    /// Home's slots carry identifiers; the page's don't.
    var leadIdentifier: String?
    /// A button between the eye and the stars.
    var center: RailCenterButton?
    /// The viewer's state has not arrived: the four state slots pulse and take no taps. The center button stays live.
    var isLoadingState = false
    /// The state could not load: the same dim, held still.
    var stateFailed = false
    /// Nil is the media page's "media-detail"; Home passes "trending.rail".
    var identifierPrefix: String?
    let onTrack: () -> Void
    let onLike: () -> Void
    var onEye: () -> Void = {}
    var onRating: (Int) -> Void = { _ in }

    var body: some View {
        VStack(spacing: -10) {
            if showsEye, ratingPicker.isPresented {
                ratingComposer
                    .transition(.opacity)
            }

            rail
        }
        .frame(width: surfaceWidth)
        .glassEffect(.regular.interactive(), in: glassShape)
        .onAppear {
            pressHaptics.prepare()
            actionHaptics.prepare()
        }
    }

    private var prefix: String { identifierPrefix ?? "media-detail" }
    private var isDimmed: Bool { isLoadingState || stateFailed }

    private var buttonCount: Int {
        2 + (showsEye ? 1 : 0) + (showsRating ? 1 : 0) + (center != nil ? 1 : 0)
    }

    private var surfaceWidth: CGFloat {
        Self.surfaceWidth(buttonCount: buttonCount, pickerPresented: ratingPicker.isPresented)
    }

    private var glassShape: RoundedRectangle {
        RoundedRectangle(cornerRadius: Self.cornerRadius(pickerPresented: ratingPicker.isPresented), style: .continuous)
    }

    private var rail: some View {
        HStack(spacing: 0) {
            railButton(
                content: lead ?? .plus,
                tint: leadTint,
                // The + opens a new log for these types, whether or not they're tracked.
                label: trackLabel ?? "Log",
                identifier: leadIdentifier,
                dimmed: isDimmed,
                action: handleTrack
            )
            if showsEye {
                railButton(
                    content: .symbol(
                        name: isEyeSelected || ratingPicker.hasLocallyWatched ? "eye.slash.fill" : "eye",
                        size: 16
                    ),
                    label: eyeLabel ?? "Mark as watched",
                    identifier: identifierPrefix.map { "\($0).eye" },
                    isLoading: isEyeLoading && !ratingPicker.isPresented,
                    dimmed: isDimmed,
                    action: handleEye
                )
                .accessibilityValue(offersRatingAfterBaseAction ? (ratingPicker.isPresented ? "Expanded" : "Collapsed") : "")
                .accessibilityHint(eyeAccessibilityHint)
            }
            if let center {
                railButton(
                    content: .symbol(name: center.systemName, size: 16),
                    tint: .white,
                    label: center.label,
                    identifier: center.identifier,
                    action: { handleCenter(center) }
                )
            }
            if showsRating {
                let isFilled = isRatingFilled ?? (ratingPicker.confirmedHalfSteps > 0)
                railButton(
                    content: .symbol(name: isFilled ? "star.fill" : "star", size: 16),
                    label: isFilled ? "Edit rating" : "Rate",
                    identifier: identifierPrefix.map { "\($0).rate" },
                    dimmed: isDimmed,
                    action: handleRating
                )
            }
            railButton(
                content: .symbol(name: isLiked ? "heart.fill" : "heart", size: 16),
                label: isLiked ? "Unlike" : "Like",
                identifier: identifierPrefix.map { "\($0).like" },
                isLoading: isLikeLoading,
                dimmed: isDimmed,
                action: handleLike
            )
        }
        .padding(5)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !isPressing else { return }
                    isPressing = true
                    pressHaptics.impactOccurred()
                    pressHaptics.prepare()
                }
                .onEnded { _ in isPressing = false }
        )
        .accessibilityIdentifier(identifierPrefix ?? "media-detail.actions")
    }

    private var ratingComposer: some View {
        ZStack {
            StarRatingPill(halfSteps: $ratingPicker.draftHalfSteps)

            if ratingPicker.showsConfirm {
                Button(action: confirmRating) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white.opacity(0.9))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isEyeLoading || isLikeLoading)
                .offset(x: 86)
                .transition(.offset(x: 23.75).combined(with: .opacity))
                .accessibilityLabel("Confirm rating")
                .accessibilityIdentifier("\(prefix).rating-confirm")
            }
        }
        .frame(width: 280, height: 60)
        .offset(x: ratingPicker.showsConfirm ? -9.75 : 0)
        .animation(
            reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84),
            value: ratingPicker.showsConfirm
        )
        .accessibilityIdentifier("\(prefix).rating-picker")
    }

    private func handleTrack() {
        dismissRatingPickerIfNeeded()
        onTrack()
    }

    private func handleCenter(_ center: RailCenterButton) {
        dismissRatingPickerIfNeeded()
        center.action()
    }

    private func handleEye() {
        guard offersRatingAfterBaseAction else {
            dismissRatingPickerIfNeeded()
            onEye()
            return
        }
        if isEyeSelected {
            dismissRatingPickerIfNeeded()
            onEye()
        } else if ratingPicker.isPresented {
            animate { ratingPicker.dismiss() }
        } else {
            let shouldTrack = !isEyeSelected && !ratingPicker.hasLocallyWatched
            animate { ratingPicker.open() }
            if shouldTrack {
                onEye()
            }
        }
    }

    private func handleLike() {
        if !isLiked, offersRatingAfterBaseAction, !ratingPicker.isPresented {
            animate { ratingPicker.open() }
        } else {
            dismissRatingPickerIfNeeded()
        }
        onLike()
    }

    private var eyeAccessibilityHint: String {
        if let eyeHint { return eyeHint }
        guard offersRatingAfterBaseAction, !isEyeSelected else { return "" }
        return ratingPicker.isPresented ? "Closes the rating picker" : "Opens the rating picker"
    }

    private func handleRating() {
        if let onRatingTap {
            dismissRatingPickerIfNeeded()
            onRatingTap()
            return
        }
        if ratingPicker.isPresented {
            animate { ratingPicker.dismiss() }
        } else {
            animate { ratingPicker.open(markLocallyWatched: false) }
        }
    }

    private func confirmRating() {
        let halfSteps = ratingPicker.draftHalfSteps
        actionHaptics.selectionChanged()
        actionHaptics.prepare()
        animate { ratingPicker.confirm() }
        onRating(halfSteps)
    }

    private func dismissRatingPickerIfNeeded() {
        guard ratingPicker.isPresented else { return }
        animate { ratingPicker.dismiss() }
    }

    private func animate(_ changes: () -> Void) {
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84)) {
            changes()
        }
    }

    private func railButton(
        content: RailLead,
        tint: Color? = nil,
        label: String,
        identifier: String? = nil,
        isLoading: Bool = false,
        dimmed: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button {
            actionHaptics.selectionChanged()
            actionHaptics.prepare()
            action()
        } label: {
            ZStack {
                Group {
                    if isLoading {
                        ProgressView()
                            .tint(.white)
                    } else {
                        slotIcon(content, tint: tint)
                            .modifier(RailSlotPulse(isPulsing: isLoadingState && dimmed, isStatic: stateFailed && dimmed))
                    }
                }
                .spineContentTransition(value: isLoading)
            }
            .frame(width: Self.buttonSize, height: Self.buttonSize)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading || dimmed)
        .accessibilityLabel(label)
        .modifier(OptionalIdentifier(identifier: identifier))
    }

    @ViewBuilder
    private func slotIcon(_ content: RailLead, tint: Color?) -> some View {
        switch content {
        case let .symbol(name, size):
            Image(systemName: name)
                .font(.system(size: size, weight: .semibold))
                .foregroundStyle(tint ?? railIconColor(systemName: name))
        case let .ring(fraction, ringTint):
            RailProgressRing(fraction: fraction, tint: ringTint)
        }
    }

    private func railIconColor(systemName: String) -> Color {
        if systemName == "heart.fill" { return .pink }
        if systemName == "star.fill" { return .yellow }
        return .white.opacity(0.84)
    }
}

/// Adds an accessibility identifier only when there is one, so the page's buttons stay as they were.
private struct OptionalIdentifier: ViewModifier {
    let identifier: String?

    @ViewBuilder
    func body(content: Content) -> some View {
        if let identifier {
            content.accessibilityIdentifier(identifier)
        } else {
            content
        }
    }
}

/// The icons of a slot whose state has not loaded pulse between 0.3 and 0.62 (static 0.45 when it failed, or with
/// Reduce Motion). The glass itself never pulses.
private struct RailSlotPulse: ViewModifier {
    let isPulsing: Bool
    let isStatic: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var high = false

    func body(content: Content) -> some View {
        if isPulsing || isStatic {
            content
                .opacity(isStatic || reduceMotion ? 0.45 : (high ? 0.62 : 0.3))
                .onAppear {
                    guard isPulsing, !reduceMotion else { return }
                    withAnimation(.easeInOut(duration: 0.55).repeatForever(autoreverses: true)) { high = true }
                }
        } else {
            content
        }
    }
}

/// A 24 pt ring for the lead slot of a title that is partway: a faint track and an arc as far as the title has got.
struct RailProgressRing: View {
    let fraction: Double
    let tint: Color

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.28), lineWidth: 3)
                .frame(width: 17, height: 17)
            Circle()
                .trim(from: 0, to: min(max(fraction, 0), 1))
                .stroke(tint, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .frame(width: 17, height: 17)
                .rotationEffect(.degrees(-90))
                .animation(reduceMotion ? nil : .timingCurve(0.25, 1, 0.35, 1, duration: 0.6), value: fraction)
        }
        .frame(width: 24, height: 24)
    }
}

struct StarRatingPill: View {
    @Binding var halfSteps: Int
    @State private var haptics = UISelectionFeedbackGenerator()

    private let duneGold = Color(red: 0.94, green: 0.64, blue: 0.24)

    var body: some View {
        GeometryReader { proxy in
            HStack(spacing: 1.25) {
                ForEach(1...5, id: \.self) { value in
                    Image(systemName: starSymbol(for: value))
                        .font(.system(size: 17.5, weight: .medium))
                        .foregroundStyle(halfSteps >= value * 2 - 1 ? duneGold : .white.opacity(0.28))
                        .frame(width: 22.5, height: 30)
                }
            }
            .accessibilityHidden(true)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Capsule())
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { gesture in
                        let clamped = min(max(gesture.location.x, 0), proxy.size.width)
                        let nextValue = min(
                            max(Int(ceil((clamped / proxy.size.width) * 10)), 0),
                            10
                        )
                        setRating(nextValue)
                    }
            )
        }
        .frame(width: 127.5, height: 30)
        .padding(.horizontal, 18)
        .padding(.vertical, 15)
        .onAppear { haptics.prepare() }
        .accessibilityRepresentation {
            Slider(value: Binding(get: { Double(halfSteps) }, set: { setRating(Int($0)) }), in: 0...10, step: 1) {
                Text("Your rating")
            }
            .accessibilityValue(Text(verbatim: halfSteps == 0 ? "Not rated" : "\(Double(halfSteps) / 2) out of 5"))
            .accessibilityIdentifier("media-detail.star-rating")
        }
    }

    private func starSymbol(for value: Int) -> String {
        if halfSteps >= value * 2 { return "star.fill" }
        if halfSteps == value * 2 - 1 { return "star.leadinghalf.filled" }
        return "star"
    }

    private func setRating(_ newValue: Int) {
        guard newValue != halfSteps else { return }
        halfSteps = newValue
        haptics.selectionChanged()
        haptics.prepare()
    }
}
