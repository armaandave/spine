import SwiftUI

/// The numbers behind the ratings chart: where each of the ten half-star steps sits, the smooth curve through them
/// (monotone cubic, so it never overshoots between steps and runs flat along the floor where nobody rated), and where
/// the photos on it stack. Pure, so each rule is tested on its own.
enum RatingsChartGeometry {
    static let stepCount = 10
    static let height: CGFloat = 170
    /// Room left of the first step and right of the last.
    static let inset: CGFloat = 14
    static let floorY: CGFloat = 140
    /// How far above the floor the busiest step reaches.
    static let amplitude: CGFloat = 92
    static let hairlineTop: CGFloat = 4
    static let labelY: CGFloat = 158
    /// How far up each further photo at one step sits.
    static let stackStep: CGFloat = 14
    static let photoSize: CGFloat = 24

    /// The distance between two neighbouring steps.
    static func stepWidth(chartWidth: CGFloat) -> CGFloat {
        (chartWidth - 2 * inset) / CGFloat(stepCount - 1)
    }

    /// The x of step `index` (0 is a half star, 9 is five stars).
    static func x(step index: Int, chartWidth: CGFloat) -> CGFloat {
        inset + CGFloat(index) * stepWidth(chartWidth: chartWidth)
    }

    /// The step nearest `x`, 0...9.
    static func step(atX x: CGFloat, chartWidth: CGFloat) -> Int {
        let raw = ((x - inset) / stepWidth(chartWidth: chartWidth)).rounded()
        return min(max(Int(raw), 0), stepCount - 1)
    }

    /// Where the average sits: stars 0.5...5 map onto steps 0...9.
    static func averageX(stars: Double, chartWidth: CGFloat) -> CGFloat {
        let position = min(max(stars * 2 - 1, 0), Double(stepCount - 1))
        return inset + CGFloat(position) * stepWidth(chartWidth: chartWidth)
    }

    /// How many ratings each of the ten steps holds. Ratings between half stars count at the nearest step.
    static func counts(from buckets: [RatingDistributionBucket]) -> [Int] {
        var counts = [Int](repeating: 0, count: stepCount)
        for bucket in buckets {
            counts[min(max(bucket.rating.ratingHalfSteps, 1), stepCount) - 1] += bucket.count
        }
        return counts
    }

    /// The height of each step's point: the floor, less the count's share of the busiest step.
    static func ys(counts: [Int]) -> [CGFloat] {
        let most = CGFloat(max(counts.max() ?? 1, 1))
        return counts.map { floorY - CGFloat($0) / most * amplitude }
    }

    /// Fritsch-Carlson slopes (per step) for the points' heights: zero at a turn or a flat stretch, and held back so
    /// the curve can't overshoot.
    static func slopes(_ ys: [CGFloat]) -> [CGFloat] {
        let n = ys.count
        guard n > 1 else { return [CGFloat](repeating: 0, count: n) }
        let d = (0..<(n - 1)).map { ys[$0 + 1] - ys[$0] }
        var m = [CGFloat](repeating: 0, count: n)
        m[0] = d[0]
        m[n - 1] = d[n - 2]
        if n > 2 {
            for i in 1..<(n - 1) {
                m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2
            }
        }
        for i in 0..<(n - 1) {
            if d[i] == 0 {
                m[i] = 0
                m[i + 1] = 0
                continue
            }
            let a = m[i] / d[i]
            let b = m[i + 1] / d[i]
            let s = a * a + b * b
            if s > 9 {
                let t = 3 / s.squareRoot()
                m[i] = t * a * d[i]
                m[i + 1] = t * b * d[i]
            }
        }
        return m
    }

    /// The curve's height at `x`.
    static func curveY(at x: CGFloat, ys: [CGFloat], slopes m: [CGFloat], chartWidth: CGFloat) -> CGFloat {
        let h = stepWidth(chartWidth: chartWidth)
        let u = min(max((x - inset) / h, 0), CGFloat(stepCount - 1) - 0.000_001)
        let i = Int(u.rounded(.down))
        let t = u - CGFloat(i)
        let t2 = t * t
        let t3 = t2 * t
        return (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * m[i + 1]
    }

    static func curvePath(ys: [CGFloat], slopes m: [CGFloat], chartWidth: CGFloat) -> Path {
        let h = stepWidth(chartWidth: chartWidth)
        var path = Path()
        path.move(to: CGPoint(x: x(step: 0, chartWidth: chartWidth), y: ys[0]))
        for i in 0..<(stepCount - 1) {
            path.addCurve(
                to: CGPoint(x: x(step: i + 1, chartWidth: chartWidth), y: ys[i + 1]),
                control1: CGPoint(x: x(step: i, chartWidth: chartWidth) + h / 3, y: ys[i] + m[i] / 3),
                control2: CGPoint(x: x(step: i + 1, chartWidth: chartWidth) - h / 3, y: ys[i + 1] - m[i + 1] / 3)
            )
        }
        return path
    }

    static func areaPath(ys: [CGFloat], slopes m: [CGFloat], chartWidth: CGFloat) -> Path {
        var path = curvePath(ys: ys, slopes: m, chartWidth: chartWidth)
        path.addLine(to: CGPoint(x: x(step: stepCount - 1, chartWidth: chartWidth), y: floorY))
        path.addLine(to: CGPoint(x: x(step: 0, chartWidth: chartWidth), y: floorY))
        path.closeSubpath()
        return path
    }

    /// A step's stars as words: "4.5", "4".
    static func label(step: Int) -> String {
        let stars = Double(step + 1) / 2
        return stars.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(stars))" : String(format: "%.1f", stars)
    }

    /// For each photo, in order, how many photos sit below it at the same step (0 is on the curve).
    static func stackSlots(steps: [Int]) -> [Int] {
        var used: [Int: Int] = [:]
        return steps.map { step in
            let slot = used[step, default: 0]
            used[step] = slot + 1
            return slot
        }
    }
}

/// Someone drawn on the curve: their photo at the step of their rating.
struct RatingChartPerson: Identifiable, Equatable {
    let id: String
    let avatarURL: String?
    /// 1...10.
    let halfSteps: Int
    let isYou: Bool

    var step: Int { halfSteps - 1 }

    /// You first (when you have stars), then every friend who rated, in the friends' order.
    static func people(
        viewerRating: String?,
        viewerAvatarURL: String?,
        friends: [MediaFriend]
    ) -> [RatingChartPerson] {
        var people: [RatingChartPerson] = []
        if let viewerRating, viewerRating.ratingHalfSteps > 0 {
            people.append(
                RatingChartPerson(id: "you", avatarURL: viewerAvatarURL, halfSteps: viewerRating.ratingHalfSteps, isYou: true)
            )
        }
        for friend in friends {
            guard let rating = friend.rating, rating.ratingHalfSteps > 0 else { continue }
            people.append(
                RatingChartPerson(
                    id: "friend-\(friend.user.id)",
                    avatarURL: friend.user.avatarUrl,
                    halfSteps: rating.ratingHalfSteps,
                    isYou: false
                )
            )
        }
        return people
    }
}

/// The title's color by Home's own rule (`HomeTitleTint`), counted from the poster the page shows.
enum MediaPageTint {
    static func known(_ posterURL: String?) -> Color? {
        guard let url = posterURL.flatMap(URL.init(string:)),
              let swatches = PosterPaletteStore.homePicture.known(server: nil, url: url) else { return nil }
        return HomeTitleTint.color(from: swatches)?.color
    }

    static func load(_ posterURL: String?) async -> Color? {
        guard let url = posterURL.flatMap(URL.init(string:)),
              let swatches = await PosterPaletteStore.homePicture.swatches(server: nil, url: url) else { return nil }
        return HomeTitleTint.color(from: swatches)?.color
    }
}

/// The header (the average, big, in the title's color) and the glowing curve under it, with the average as a hairline
/// and the people who rated as photos on the curve. Dragging across the chart reads any step.
struct RatingsGlowChart: View {
    typealias G = RatingsChartGeometry

    let counts: [Int]
    /// The average in stars, 0.5...5.
    let average: Double
    let totalCount: Int
    /// The title's color; white when the poster gave none.
    let tint: Color?
    let people: [RatingChartPerson]
    /// "See all 96 reviews", or nil when there are none to open.
    let reviewsTitle: String?
    let onSeeReviews: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @GestureState private var scrubX: CGFloat?
    @State private var appeared = false
    @State private var accessibilityStep: Int?

    private static let headerHeight: CGFloat = 64
    private static let headerGap: CGFloat = 4
    private static let pageColor = Color(red: 0x12 / 255, green: 0x12 / 255, blue: 0x10 / 255)
    private static let returnSpring = Animation.spring(response: 0.6, dampingFraction: 0.55)

    private var color: Color { tint ?? .white }

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let scrubIndex = scrubX.map { G.step(atX: $0, chartWidth: width) }
            VStack(alignment: .leading, spacing: Self.headerGap) {
                header(scrubIndex: scrubIndex)
                chart(width: width, scrubIndex: scrubIndex)
            }
            .sensoryFeedback(.selection, trigger: scrubIndex) { _, new in new != nil }
        }
        .frame(height: Self.headerHeight + Self.headerGap + G.height)
        .onAppear {
            // Reduce Motion: it is simply there.
            appeared = true
        }
    }

    // MARK: Header

    private func header(scrubIndex: Int?) -> some View {
        let scrubbing = scrubIndex != nil
        let number = scrubIndex.map { G.label(step: $0) } ?? String(format: "%.1f", average)
        let count = scrubIndex.map { counts[$0] } ?? totalCount
        return HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 2) {
                Text(scrubbing ? "AT THIS STEP" : "AVERAGE")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.62))
                    .frame(height: 14, alignment: .leading)
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(number)
                        .font(.system(size: 44, weight: .light))
                        .tracking(-1.2)
                        .monospacedDigit()
                        .foregroundStyle(color)
                        // Home's title glow.
                        .shadow(color: color.opacity(0.45), radius: 6)
                        .shadow(color: color.opacity(0.3), radius: 16)
                    Text("\(count.formatted()) \(count == 1 ? "rating" : "ratings")")
                        .font(.system(size: 13, weight: .semibold))
                        .monospacedDigit()
                        .foregroundStyle(.white.opacity(0.62))
                }
                .lineLimit(1)
            }
            .accessibilityHidden(true)
            Spacer(minLength: 8)
            if let reviewsTitle {
                Button(action: onSeeReviews) {
                    HStack(spacing: 5) {
                        Text(reviewsTitle)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .heavy))
                    }
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.82))
                }
                .buttonStyle(.plain)
                .padding(.bottom, 6)
                .accessibilityHint("Opens every written review")
            }
        }
        .frame(height: Self.headerHeight, alignment: .bottom)
    }

    // MARK: Chart

    private func chart(width: CGFloat, scrubIndex: Int?) -> some View {
        let ys = G.ys(counts: counts)
        let slopes = G.slopes(ys)
        let averageX = G.averageX(stars: average, chartWidth: width)
        let hairX = scrubX.map {
            min(max($0, G.inset), G.x(step: G.stepCount - 1, chartWidth: width))
        } ?? averageX
        let hairY = G.curveY(at: hairX, ys: ys, slopes: slopes, chartWidth: width)
        let animate = !reduceMotion
        return ZStack(alignment: .topLeading) {
            RatingsCurveLayer(ys: ys, slopes: slopes, width: width, tint: color, appeared: appeared, animate: animate)
            ForEach(1...5, id: \.self) { star in
                Text("\(star)")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.white.opacity(0.58))
                    .position(x: G.x(step: star * 2 - 1, chartWidth: width), y: G.labelY)
            }
            hairline(x: hairX, crossing: hairY, animate: animate, scrubbing: scrubX != nil)
            photos(ys: ys, width: width, scrubIndex: scrubIndex, animate: animate)
        }
        .frame(width: width, height: G.height, alignment: .topLeading)
        .contentShape(Rectangle())
        // A short press, then drag: a quick vertical swipe still scrolls the page.
        .gesture(
            LongPressGesture(minimumDuration: 0.12)
                .sequenced(before: DragGesture(minimumDistance: 0))
                .updating($scrubX) { value, state, _ in
                    if case let .second(true, drag?) = value {
                        state = drag.location.x
                    }
                }
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Ratings, average \(String(format: "%.1f", average)) out of 5, \(totalCount.formatted()) \(totalCount == 1 ? "rating" : "ratings")"
        )
        .accessibilityValue(accessibilityStep.map(stepDescription) ?? "")
        .accessibilityHint("Swipe up or down to hear each rating")
        .accessibilityAdjustableAction { direction in
            let current = accessibilityStep ?? G.step(atX: averageX, chartWidth: width)
            switch direction {
            case .increment: accessibilityStep = min(current + 1, G.stepCount - 1)
            case .decrement: accessibilityStep = max(current - 1, 0)
            @unknown default: break
            }
        }
    }

    private func stepDescription(_ step: Int) -> String {
        let count = counts[step]
        return "\(G.label(step: step)) stars, \(count.formatted()) \(count == 1 ? "rating" : "ratings")"
    }

    /// The average: a 1 pt line from the top to the floor, brightest where it crosses the curve and clear at both ends.
    private func hairline(x: CGFloat, crossing: CGFloat, animate: Bool, scrubbing: Bool) -> some View {
        let length = G.floorY - G.hairlineTop
        let location = min(max((crossing - G.hairlineTop) / length, 0.1), 0.9)
        return Rectangle()
            .fill(
                LinearGradient(
                    stops: [
                        .init(color: color.opacity(0), location: 0),
                        .init(color: color.opacity(0.95), location: location),
                        .init(color: color.opacity(0), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .frame(width: 1, height: length)
            .shadow(color: color.opacity(0.6), radius: 3)
            .shadow(color: color.opacity(0.35), radius: 10)
            .opacity(appeared ? 1 : 0)
            .animation(animate ? .easeInOut(duration: 0.7).delay(1.1) : nil, value: appeared)
            .position(x: x, y: G.hairlineTop + length / 2)
            // Follows the finger with no animation; springs back to the average when let go.
            .animation(animate && !scrubbing ? Self.returnSpring : nil, value: x)
            .allowsHitTesting(false)
    }

    private func photos(ys: [CGFloat], width: CGFloat, scrubIndex: Int?, animate: Bool) -> some View {
        let slots = G.stackSlots(steps: people.map(\.step))
        let order = people.indices.sorted { (people[$0].step, $0) < (people[$1].step, $1) }
        return ZStack(alignment: .topLeading) {
            ForEach(Array(people.enumerated()), id: \.element.id) { index, person in
                let lifted = person.step == scrubIndex
                let rank = order.firstIndex(of: index) ?? 0
                photo(person)
                    .scaleEffect(lifted ? 1.15 : 1)
                    .offset(y: lifted ? -6 : 0)
                    .animation(animate ? .spring(response: 0.35, dampingFraction: 0.7) : nil, value: lifted)
                    .scaleEffect(appeared ? 1 : 0.55)
                    .offset(y: appeared ? 0 : -28)
                    .opacity(appeared ? 1 : 0)
                    .animation(
                        animate ? .spring(response: 0.6, dampingFraction: 0.5).delay(0.85 + Double(rank) * 0.11) : nil,
                        value: appeared
                    )
                    .position(
                        x: G.x(step: person.step, chartWidth: width),
                        y: ys[person.step] - CGFloat(slots[index]) * G.stackStep
                    )
                    .zIndex(Double(20 - slots[index]))
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private func photo(_ person: RatingChartPerson) -> some View {
        UserAvatar(urlString: person.avatarURL, size: G.photoSize)
            .overlay {
                // A ring outside the photo: white for you, the page's color for friends so overlaps read.
                Circle()
                    .inset(by: person.isYou ? -1 : -0.75)
                    .stroke(person.isYou ? Color.white : Self.pageColor, lineWidth: person.isYou ? 2 : 1.5)
            }
            .shadow(color: person.isYou ? .white.opacity(0.35) : .clear, radius: 10)
    }
}

/// The area, the glowing line and its glow, drawn once and drawn in: it only changes with its inputs, so dragging
/// across the chart never redraws it.
private struct RatingsCurveLayer: View {
    typealias G = RatingsChartGeometry

    let ys: [CGFloat]
    let slopes: [CGFloat]
    let width: CGFloat
    let tint: Color
    let appeared: Bool
    let animate: Bool

    var body: some View {
        let curve = G.curvePath(ys: ys, slopes: slopes, chartWidth: width)
        let progress: CGFloat = appeared ? 1 : 0
        let draw = animate ? Animation.timingCurve(0.3, 0.6, 0.2, 1, duration: 1.5).delay(0.15) : nil
        ZStack {
            G.areaPath(ys: ys, slopes: slopes, chartWidth: width)
                .fill(
                    // From the curve's top to the floor, not the whole chart's height.
                    LinearGradient(
                        colors: [tint.opacity(0.18), tint.opacity(0)],
                        startPoint: UnitPoint(x: 0.5, y: (ys.min() ?? G.floorY) / G.height),
                        endPoint: UnitPoint(x: 0.5, y: G.floorY / G.height)
                    )
                )
                .opacity(appeared ? 1 : 0)
                .animation(animate ? .easeInOut(duration: 1.4).delay(0.6) : nil, value: appeared)
            curve.trim(from: 0, to: progress)
                .stroke(tint, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                .opacity(0.45)
                .blur(radius: 8)
                .animation(draw, value: appeared)
            curve.trim(from: 0, to: progress)
                .stroke(tint, style: StrokeStyle(lineWidth: 1.75, lineCap: .round, lineJoin: .round))
                .blur(radius: 3)
                .opacity(0.45)
                .animation(draw, value: appeared)
            curve.trim(from: 0, to: progress)
                .stroke(tint, style: StrokeStyle(lineWidth: 1.75, lineCap: .round, lineJoin: .round))
                .animation(draw, value: appeared)
        }
        .frame(width: width, height: G.height, alignment: .topLeading)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
