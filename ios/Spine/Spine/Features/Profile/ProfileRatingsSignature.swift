import SwiftUI

/// The numbers behind the profile's signature: the person's ratings curve, drawn small beside the @handle. The Stats
/// page's ten half-star steps run left to right, half a star to five; the busiest step reaches the top, a step nobody
/// rated sits on the floor, and the line between them is the title pages' monotone cubic, so it never overshoots.
enum ProfileSignatureGeometry {
    static let size = CGSize(width: 40, height: 14)
    /// Room between the @handle and the line.
    static let gap: CGFloat = 6
    /// The floor sits 2 above the box's bottom, and the busiest step 2 below its top.
    static let floorY: CGFloat = 12
    static let amplitude: CGFloat = 10
    /// How far the 44 pt tap area reaches above and below the 14 pt line.
    static let tapInset: CGFloat = (44 - size.height) / 2

    /// Whether a profile shows one: only once the person has rated something.
    static func shows(_ ratings: StatsRatings) -> Bool {
        ratings.rated > 0
    }

    /// Each step's count, half a star to five, from the Stats page's ten buckets.
    static func counts(_ ratings: StatsRatings) -> [Int] {
        StatsRatingsChartData.buckets(ratings.distribution).map(\.count)
    }

    /// The height of each step's point: the floor, less the count's share of the busiest step.
    static func ys(counts: [Int]) -> [CGFloat] {
        let most = CGFloat(max(counts.max() ?? 1, 1))
        return counts.map { floorY - CGFloat($0) / most * amplitude }
    }

    static func path(counts: [Int]) -> Path {
        let ys = ys(counts: counts)
        var path = Path()
        guard let first = ys.first, ys.count > 1 else { return path }
        let slopes = RatingsChartGeometry.slopes(ys)
        let step = size.width / CGFloat(ys.count - 1)
        path.move(to: CGPoint(x: 0, y: first))
        for i in 0..<(ys.count - 1) {
            path.addCurve(
                to: CGPoint(x: CGFloat(i + 1) * step, y: ys[i + 1]),
                control1: CGPoint(x: CGFloat(i) * step + step / 3, y: ys[i] + slopes[i] / 3),
                control2: CGPoint(x: CGFloat(i + 1) * step - step / 3, y: ys[i + 1] - slopes[i + 1] / 3)
            )
        }
        return path
    }
}

/// The profile's signature: the person's ratings curve right after the @handle, where a badge would go. It is the title
/// pages' line with Home's glow, scaled to its size, in the color of the picture behind the profile's header (Home's
/// title rule), and white without one. It writes itself in once that color is known. The caller makes it a link to the
/// Stats page.
struct ProfileRatingsSignature: View {
    typealias G = ProfileSignatureGeometry

    let ratings: StatsRatings
    /// The picture behind the profile's header, whose color the line takes; nil draws it white.
    let pictureURL: String?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var tint: Color?
    @State private var drawn = false

    var body: some View {
        SignatureLine(counts: G.counts(ratings), tint: tint ?? .white, drawn: drawn, animate: !reduceMotion)
            .equatable()
            .opacity(tint == nil ? 0 : 1)
            .frame(width: G.size.width, height: G.size.height)
            // A 44 pt tap area; the link around it takes the room back out (`tapInset`).
            .padding(.vertical, G.tapInset)
            .contentShape(Rectangle())
            .task(id: pictureURL) { await reveal() }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityLabel)
            .accessibilityHint("Opens Stats")
    }

    private var accessibilityLabel: String {
        let count = "\(ratings.rated.formatted()) \(ratings.rated == 1 ? "rating" : "ratings")"
        guard let average = ratings.averageStars else { return "Ratings, \(count)" }
        return "Ratings, average \(average.averageText) out of 5 from \(count)"
    }

    /// Finds the picture's color, then writes the line in (`SignatureLine` animates its own drawing).
    private func reveal() async {
        var color = Color.white
        if let pictureURL {
            if let known = MediaPageTint.known(pictureURL) {
                color = known
            } else if let loaded = await MediaPageTint.load(pictureURL) {
                color = loaded
            }
        }
        guard !Task.isCancelled else { return }
        tint = color
        drawn = true
    }
}

/// The line and its glow, rasterized once; it only changes with its inputs.
private struct SignatureLine: View, Equatable {
    typealias G = ProfileSignatureGeometry

    let counts: [Int]
    let tint: Color
    let drawn: Bool
    let animate: Bool

    var body: some View {
        let curve = G.path(counts: counts)
        let progress: CGFloat = drawn ? 1 : 0
        // Written left to right, like the title pages' line, a little quicker for its size.
        let write = animate ? Animation.timingCurve(0.3, 0.6, 0.2, 1, duration: 1.1).delay(0.1) : nil
        ZStack {
            curve.trim(from: 0, to: progress)
                .stroke(tint, style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round))
                .blur(radius: 3)
                .opacity(0.45)
                .animation(write, value: drawn)
            curve.trim(from: 0, to: progress)
                .stroke(tint, style: StrokeStyle(lineWidth: 1.75, lineCap: .round, lineJoin: .round))
                .blur(radius: 2)
                .opacity(0.45)
                .animation(write, value: drawn)
            curve.trim(from: 0, to: progress)
                .stroke(tint, style: StrokeStyle(lineWidth: 1.75, lineCap: .round, lineJoin: .round))
                .animation(write, value: drawn)
        }
        .frame(width: G.size.width, height: G.size.height, alignment: .topLeading)
        // The padding keeps the glow from being cut at the box's edges.
        .padding(8)
        .drawingGroup()
        .padding(-8)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
