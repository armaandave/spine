import SwiftUI

/// The numbers behind the ratings chart: where each of the ten half-star steps sits, the smooth curve through them
/// (monotone cubic, so it never overshoots between steps and runs flat along the floor where nobody rated), and where
/// the photos on it stack. Pure, so each rule is tested on its own.
enum RatingsChartGeometry {
    static let stepCount = 10
    /// Just the curve, the photos and room for the glow under the floor: no axis.
    static let height: CGFloat = 152
    /// Room left of the first step and right of the last: enough that a photo at half a star or five stars (24 pt,
    /// plus your 2 pt outline) sits inside the box with a little air, instead of touching its edge.
    static let inset: CGFloat = 24
    static let floorY: CGFloat = 140
    /// How far above the floor the busiest step reaches.
    static let amplitude: CGFloat = 92
    static let hairlineTop: CGFloat = 4
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

    /// Where the average sits: stars 0.5...5 map onto steps 0...9.
    static func averageX(stars: Double, chartWidth: CGFloat) -> CGFloat {
        let position = min(max(stars * 2 - 1, 0), Double(stepCount - 1))
        return inset + CGFloat(position) * stepWidth(chartWidth: chartWidth)
    }

    /// How many ratings each of the ten steps holds. Ratings between half stars count at the nearest step.
    static func counts(from buckets: [RatingDistributionBucket]) -> [Int] {
        var counts = [Int](repeating: 0, count: stepCount)
        for bucket in buckets {
            // No stars is no step: nobody is drawn there either.
            let steps = bucket.rating.ratingHalfSteps
            guard steps > 0 else { continue }
            counts[min(steps, stepCount) - 1] += bucket.count
        }
        return counts
    }

    /// The average in stars: the payload's own when it has one, else counted from the steps (0 when empty).
    static func average(payload: Double?, counts: [Int]) -> Double {
        if let payload { return payload }
        let total = counts.reduce(0, +)
        guard total > 0 else { return 0 }
        let weighted = counts.enumerated().reduce(0.0) { $0 + Double($1.element) * Double($1.offset + 1) / 2 }
        return weighted / Double(total)
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

    /// The lowest a photo's center may sit: its top edge stays inside the chart, under the header.
    static let photoTopLimit: CGFloat = photoSize / 2

    /// The height of each photo, in order: the first at a step sits on the curve, the rest above it, 14 pt apart, or
    /// closer together when the step is busy so the top one never rises into the header.
    static func photoYs(steps: [Int], ys: [CGFloat]) -> [CGFloat] {
        let slots = stackSlots(steps: steps)
        var perStep: [Int: Int] = [:]
        for step in steps { perStep[step, default: 0] += 1 }
        return steps.indices.map { index in
            let base = ys[steps[index]]
            let others = (perStep[steps[index]] ?? 1) - 1
            let room = max(base - photoTopLimit, 0)
            let gap = others > 0 ? min(stackStep, room / CGFloat(others)) : stackStep
            return base - CGFloat(slots[index]) * gap
        }
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

    /// The stars to plot as yours: an episode's page charts the episode, so it reads your episode stars; every other
    /// page reads the title's own.
    static func viewerRating(mediaType: String, titleRating: String?, episodeRating: String?) -> String? {
        mediaType == "episode" ? episodeRating : titleRating
    }

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
/// and the people who rated as photos on the curve.
struct RatingsGlowChart: View {
    typealias G = RatingsChartGeometry

    let counts: [Int]
    /// The average in stars, 0.5...5.
    let average: Double
    let totalCount: Int
    /// The title's color; white when the poster gave none.
    let tint: Color?
    let people: [RatingChartPerson]
    /// "96 reviews", or nil when there are none to open.
    let reviewsTitle: String?
    let onSeeReviews: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false

    private static let headerHeight: CGFloat = 48
    /// Air above the header, so the box's top edge sits 2 pt above the number's line.
    private static let topInset: CGFloat = 8
    /// The Inner light box: from 6 pt down to the floor, 1 pt in from each side.
    private static let boxTop: CGFloat = 6
    private static let boxRadius: CGFloat = 20
    /// Where the header's content sits from the box's left edge, and the reviews link from the right.
    private static let sideInset: CGFloat = 14

    /// Inter Tight Black: the bundled variable font (`Resources/Fonts`, registered under `UIAppFonts`) with its
    /// weight axis set to 900 through the font descriptor. A fixed 44 pt, like the board's fixed 48 pt header,
    /// so it does not scale with Dynamic Type.
    static let numberFont: Font = {
        assert(UIFont(name: "InterTight-Regular", size: 44) != nil, "Inter Tight is not bundled or registered")
        let weightAxis = 0x7767_6874 // 'wght'
        let descriptor = UIFontDescriptor(fontAttributes: [
            .name: "InterTight-Regular",
            UIFontDescriptor.AttributeName(rawValue: kCTFontVariationAttribute as String): [weightAxis: 900],
        ])
        return Font(UIFont(descriptor: descriptor, size: 44))
    }()

    private var color: Color { tint ?? .white }

    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            ZStack(alignment: .topLeading) {
                innerLightBox
                    .frame(width: width - 2, height: G.floorY + Self.topInset + Self.headerHeight - Self.boxTop)
                    .offset(x: 1, y: Self.boxTop)
                VStack(alignment: .leading, spacing: 0) {
                    header
                    chart(width: width)
                }
                .padding(.top, Self.topInset)
            }
        }
        .frame(height: Self.topInset + Self.headerHeight + G.height)
        .onAppear {
            // Reduce Motion: it is simply there.
            if reduceMotion { appeared = true }
        }
        // The page's scroll view builds this at load; draw in when it is actually on screen.
        .onScrollVisibilityChange(threshold: 0.3) { visible in
            if visible { appeared = true }
        }
    }

    // MARK: Box

    /// No stroke and no fill: only a soft white glow just inside the edges, so the box is felt rather than drawn.
    private var innerLightBox: some View {
        // CSS `inset 0 0 18px rgba(255,255,255,.06)`: the area outside the box, blurred, clipped to the box.
        GeometryReader { geometry in
            let box = CGRect(origin: .zero, size: geometry.size)
            Path { path in
                path.addRect(box.insetBy(dx: -40, dy: -40))
                path.addRoundedRect(in: box, cornerSize: CGSize(width: Self.boxRadius, height: Self.boxRadius), style: .circular)
            }
            .fill(Color.white.opacity(0.06), style: FillStyle(eoFill: true))
            .blur(radius: 9)
            .clipShape(RoundedRectangle(cornerRadius: Self.boxRadius, style: .circular))
        }
        .drawingGroup()
        .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(String(format: "%.1f", average))
                .font(Self.numberFont)
                .tracking(-2)
                // The board's 48 pt line box, so the baseline lands where it does there.
                .frame(height: Self.headerHeight)
                .foregroundStyle(color)
                // Softer than Home's title glow.
                .shadow(color: color.opacity(0.24), radius: 6)
                .shadow(color: color.opacity(0.14), radius: 14)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(totalCount.formatted())
                Image(systemName: "eye")
            }
            .font(.system(size: 13, weight: .semibold))
            .monospacedDigit()
            .foregroundStyle(.white.opacity(0.62))
            .accessibilityLabel("\(totalCount.formatted()) \(totalCount == 1 ? "rating" : "ratings")")
        }
        .lineLimit(1)
        .accessibilityHidden(true)
        .padding(.horizontal, Self.sideInset)
        .frame(maxWidth: .infinity, minHeight: Self.headerHeight, maxHeight: Self.headerHeight, alignment: .bottomLeading)
        .overlay(alignment: .topTrailing) {
            if let reviewsTitle {
                Button(action: onSeeReviews) {
                    HStack(spacing: 5) {
                        Text(reviewsTitle)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .heavy))
                    }
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.82))
                    // 10 pt of tappable air around the text; the outer padding below takes it back out.
                    .padding(10)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .frame(height: 38)
                // The text sits 12 pt below the header's top, 20 pt in from the right: inside the box's corner.
                .padding(.top, 2)
                .padding(.trailing, 10)
                .accessibilityHint("Opens every written review")
            }
        }
    }

    // MARK: Chart

    private func chart(width: CGFloat) -> some View {
        let ys = G.ys(counts: counts)
        let slopes = G.slopes(ys)
        let averageX = G.averageX(stars: average, chartWidth: width)
        let hairY = G.curveY(at: averageX, ys: ys, slopes: slopes, chartWidth: width)
        let animate = !reduceMotion
        return ZStack(alignment: .topLeading) {
            RatingsCurveLayer(ys: ys, slopes: slopes, width: width, tint: color, appeared: appeared, animate: animate)
                .equatable()
            hairline(x: averageX, crossing: hairY, animate: animate)
            photos(ys: ys, width: width, animate: animate)
        }
        .frame(width: width, height: G.height, alignment: .topLeading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Ratings, average \(String(format: "%.1f", average)) out of 5, \(totalCount.formatted()) \(totalCount == 1 ? "rating" : "ratings")"
        )
    }

    /// The average: a 1 pt line from the top to the floor, brightest where it crosses the curve and clear at both ends.
    private func hairline(x: CGFloat, crossing: CGFloat, animate: Bool) -> some View {
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
            .animation(animate ? .easeInOut(duration: 0.6).delay(1.4) : nil, value: appeared)
            .position(x: x, y: G.hairlineTop + length / 2)
            .allowsHitTesting(false)
    }

    private func photos(ys: [CGFloat], width: CGFloat, animate: Bool) -> some View {
        let photoYs = G.photoYs(steps: people.map(\.step), ys: ys)
        let slots = G.stackSlots(steps: people.map(\.step))
        var ranks = [Int](repeating: 0, count: people.count)
        for (rank, index) in people.indices.sorted(by: { (people[$0].step, $0) < (people[$1].step, $1) }).enumerated() {
            ranks[index] = rank
        }
        return ZStack(alignment: .topLeading) {
            ForEach(Array(people.enumerated()), id: \.element.id) { index, person in
                let rank = ranks[index]
                photo(person)
                    .scaleEffect(appeared ? 1 : 0.55)
                    .offset(y: appeared ? 0 : -28)
                    .opacity(appeared ? 1 : 0)
                    .animation(
                        animate ? .spring(response: 0.55, dampingFraction: 0.62).delay(0.85 + Double(rank) * 0.11) : nil,
                        value: appeared
                    )
                    .position(
                        x: G.x(step: person.step, chartWidth: width),
                        y: photoYs[index]
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
                // Friends' photos have no ring; yours has a white outline.
                if person.isYou {
                    Circle().inset(by: -1).stroke(Color.white, lineWidth: 2)
                }
            }
            .modifier(YourGlow(isYou: person.isYou))
    }
}

/// The area, the glowing line and its glow, drawn once and drawn in: it only changes with its inputs.
private struct RatingsCurveLayer: View, Equatable {
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
        // Rasterized once; the padding keeps the glow from being cut at the chart's edges.
        .padding(24)
        .drawingGroup()
        .padding(-24)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Only your own photo gets the faint white glow; friends' photos add no shadow layer.
private struct YourGlow: ViewModifier {
    let isYou: Bool

    func body(content: Content) -> some View {
        if isYou {
            content.shadow(color: .white.opacity(0.35), radius: 10)
        } else {
            content
        }
    }
}
