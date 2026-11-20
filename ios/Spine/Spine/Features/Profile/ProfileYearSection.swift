import Nuke
import SwiftUI

/// The profile's This Year section, under the menu: this year's numbers per turned-on type, the Stats page's own
/// (STAT-312), on a glass plate over a wall of the year's posters. The wall is as wide as the menu. Its top tucks
/// under the Stats row, fading in from under the menu's glass, and it runs on below the plate, fading off the bottom
/// of the screen. Under 12 posters there's no wall: the numbers sit on glass like the menu's. Each type with something
/// this year opens the Stats page on that type and year.
struct ProfileYearSection<Destination: View>: View {
    let year: ProfileYear
    @ViewBuilder let destination: (String) -> Destination

    /// The section's size; the design's until it's measured.
    @State private var width: CGFloat = 398
    @State private var height: CGFloat = 600

    var body: some View {
        let rows = ProfileYearPresentation.rows(for: year)
        Group {
            if year.posters.count >= ProfileYearWall.minimumPosters {
                wall(rows)
            } else {
                numbers(rows)
                    .padding(EdgeInsets(top: 16, leading: 16, bottom: 6, trailing: 16))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .modifier(ProfileMenuGroupSurface(shape: RoundedRectangle(cornerRadius: ProfileMenuRow.groupCornerRadius, style: .continuous)))
                    .padding(.top, 20)
            }
        }
        .frame(maxWidth: .infinity)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }

    // MARK: The wall

    private func wall(_ rows: [ProfileYearRow]) -> some View {
        numbers(rows)
            .padding(EdgeInsets(top: 16, leading: 16, bottom: 6, trailing: 16))
            .frame(maxWidth: .infinity, alignment: .leading)
            // The same glass as the menu above: plain regular glass, no dark tint.
            .modifier(ProfileMenuGroupSurface(shape: RoundedRectangle(cornerRadius: 15, style: .continuous)))
            .padding(.horizontal, 8)
            // Posters show above the plate and run on below it.
            .padding(.top, ProfileYearWall.tuck + ProfileYearWall.above)
            .padding(.bottom, ProfileYearWall.below)
            .frame(maxWidth: .infinity)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { height = $0 }
            .background(alignment: .top) {
                // Drawn past the section's bottom, under the tab bar, so the fade reaches the screen's edge.
                ProfileYearWallImage(posters: year.posters, width: width, height: height + ProfileYearWall.bleed)
                    .accessibilityHidden(true)
            }
            .environment(\.colorScheme, .dark)
            // The top tucks under the Stats row; the menu draws over it.
            .padding(.top, -ProfileYearWall.tuck)
            // It runs on into the page's bottom space (ProfileView's 100 pt), so the wall ends at the page's end.
            .padding(.bottom, -ProfileYearWall.pageBottom)
    }

    // MARK: The numbers

    private func numbers(_ rows: [ProfileYearRow]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("This Year".uppercased())
                .font(.system(size: 13, weight: .black))
                .tracking(0.8)
                .foregroundStyle(.white.opacity(0.7))
                .padding(.bottom, 4)

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(year.titles.formatted())
                    .font(RatingsGlowChart.numberFont)
                    .tracking(-2)
                    .foregroundStyle(.white)
                Text(year.titles == 1 ? "title" : "titles")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(.white.opacity(0.64))
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(ProfileYearPresentation.totalAccessibilityLabel(year))

            VStack(spacing: 0) {
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                    let isLast = index == rows.count - 1
                    if row.isEmpty {
                        rowLabel(row, isLast: isLast)
                            .accessibilityElement(children: .ignore)
                            .accessibilityLabel(row.accessibilityLabel)
                    } else {
                        NavigationLink {
                            destination(row.mediaType)
                        } label: {
                            rowLabel(row, isLast: isLast)
                        }
                        .buttonStyle(ProfileMenuRowButtonStyle())
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(row.accessibilityLabel)
                        .accessibilityHint("Opens \(year.year) stats")
                        .accessibilityAddTraits(.isButton)
                    }
                }
            }
            .padding(.top, 10)
        }
    }

    private func rowLabel(_ row: ProfileYearRow, isLast: Bool) -> some View {
        HStack(spacing: 10) {
            Text(row.count)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white.opacity(row.isEmpty ? 0.58 : 0.9))
                .monospacedDigit()
                .lineLimit(1)
            Spacer(minLength: 10)
            if let measure = row.measure {
                Text(measure)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(.white.opacity(0.64))
                    .monospacedDigit()
                    .lineLimit(1)
            }
        }
        .frame(height: 34)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if !isLast {
                Rectangle()
                    .fill(.white.opacity(0.12))
                    .frame(height: 0.5)
            }
        }
    }
}

/// The wall's grid, worked out from the posters and the space, never from fixed steps. Every cell is exactly a
/// poster's 2:3 shape, so nothing is cropped, and the rows run from the top down past the wall's faded bottom. The
/// grid takes the most columns, up to 6, whose rows the posters fill; under 12 posters there's no wall.
enum ProfileYearWall {
    static let minimumPosters = 12
    static let maximumAcross = 6
    /// A poster is 2 wide by 3 tall.
    static let posterHeightPerWidth: CGFloat = 1.5
    static let gap: CGFloat = 0
    /// How far the top runs up under the Stats row.
    static let tuck: CGFloat = 48
    /// Full strength this far below the menu's bottom edge.
    static let fadeBelow: CGFloat = 56
    /// The wall between the menu and the plate.
    static let above: CGFloat = 120
    /// ProfileView's space under its last section, which the wall runs on into.
    static let pageBottom: CGFloat = 100
    /// The wall under the plate, inside the section: the plate sits centered on the wall, with as much of it below
    /// (down to the tab bar, when the page is scrolled to its end) as above (up to the Stats row).
    static var below: CGFloat { above + pageBottom }
    /// Drawn past the page's end, under the tab bar, down to the screen's bottom edge.
    static let bleed: CGFloat = 140
    /// The fade out starts this far above the plate's bottom edge.
    static let fadeOutLead: CGFloat = 60

    struct Grid: Hashable {
        let across: Int
        let down: Int

        var count: Int { across * down }
    }

    static func grid(posters: Int, width: CGFloat, height: CGFloat) -> Grid? {
        guard posters >= minimumPosters, width > 0, height > 0 else { return nil }
        for across in stride(from: maximumAcross, through: 1, by: -1) {
            let cell = cellSize(across: across, width: width)
            // Enough rows to reach the wall's bottom edge; the last one may run past it, into the fade.
            let down = Int(((height + gap) / (cell.height + gap)).rounded(.up))
            if across * down <= posters {
                return Grid(across: across, down: down)
            }
        }
        return nil
    }

    static func cellSize(across: Int, width: CGFloat) -> CGSize {
        let cellWidth = (width - CGFloat(across - 1) * gap) / CGFloat(across)
        return CGSize(width: cellWidth, height: cellWidth * posterHeightPerWidth)
    }

    /// The wall's opacity down its drawn height, as (location 0...1, alpha): nothing at the top, under the menu; a
    /// third at the menu's bottom edge; full strength down to just above the plate's bottom; then a long fade to
    /// nothing at the screen's bottom edge.
    static func fadeStops(height: CGFloat) -> [(location: CGFloat, alpha: CGFloat)] {
        let full = tuck + fadeBelow
        let fadeOut = max(full, height - bleed - below - fadeOutLead)
        return [
            (0, 0),
            (tuck / height, 0.35),
            (full / height, 1),
            (fadeOut / height, 1),
            ((fadeOut + (height - fadeOut) * 0.45) / height, 0.4),
            (1, 0),
        ]
    }
}

/// The wall as one picture: the posters drawn once, dimmed, with the top corners rounded and the fades baked in, so
/// scrolling moves a single image instead of dozens of filtered, masked layers. It fades in once drawn, and a wall
/// already drawn this session shows at once.
private struct ProfileYearWallImage: View {
    let posters: [MediaSummary]
    let width: CGFloat
    let height: CGFloat

    @Environment(\.displayScale) private var displayScale
    @State private var image: UIImage?

    var body: some View {
        let key = ProfileYearWallRenderer.Key(posters: posters, width: width, height: height, displayScale: displayScale)
        ZStack(alignment: .top) {
            if let shown = image ?? key.flatMap(ProfileYearWallRenderer.cached) {
                Image(uiImage: shown)
                    .resizable()
                    .frame(width: width, height: height)
                    .transition(.opacity)
            }
        }
        .frame(width: width, height: height, alignment: .top)
        .task(id: key) {
            guard let key else {
                image = nil
                return
            }
            if let cached = ProfileYearWallRenderer.cached(key) {
                image = cached
                return
            }
            guard let drawn = await ProfileYearWallRenderer.render(key) else { return }
            withAnimation(.easeOut(duration: 0.3)) { image = drawn }
        }
    }
}

/// Draws the wall into one image, off the main thread, and keeps the last few for the session.
enum ProfileYearWallRenderer {
    struct Key: Hashable {
        let urls: [URL?]
        let grid: ProfileYearWall.Grid
        let width: CGFloat
        let height: CGFloat
        let scale: CGFloat

        var size: CGSize { CGSize(width: width, height: height) }

        /// Nil when there's no wall to draw: too few posters, or no size yet.
        init?(posters: [MediaSummary], width: CGFloat, height: CGFloat, displayScale: CGFloat) {
            guard let grid = ProfileYearWall.grid(posters: posters.count, width: width, height: height) else { return nil }
            self.grid = grid
            urls = posters.prefix(grid.count).map { URL(string: $0.displayPosterURL ?? "") }
            self.width = width
            self.height = height
            // The screen's own sharpness: anything less is stretched up and looks soft.
            scale = displayScale
        }
    }

    private static var drawn: [Key: UIImage] = [:]
    private static var order: [Key] = []
    private static let keep = 2

    static func cached(_ key: Key) -> UIImage? {
        drawn[key]
    }

    static func render(_ key: Key) async -> UIImage? {
        let cell = ProfileYearWall.cellSize(across: key.grid.across, width: key.width)
        let requests = key.urls.map { SpineImagePipeline.request(url: $0, thumbnailSize: cell) }
        // Every poster at its cell's size, all at once, through the app's image caches.
        let posters = await withTaskGroup(of: (Int, UIImage?).self) { group in
            for (index, request) in requests.enumerated() {
                group.addTask {
                    guard let request else { return (index, nil) }
                    return (index, try? await ImagePipeline.shared.image(for: request))
                }
            }
            var loaded = [UIImage?](repeating: nil, count: requests.count)
            for await (index, image) in group {
                loaded[index] = image
            }
            return loaded
        }
        guard !Task.isCancelled else { return nil }
        let plan = WallPlan(
            size: key.size,
            scale: key.scale,
            cells: (0..<key.grid.count).map { index in
                CGRect(
                    x: CGFloat(index % key.grid.across) * (cell.width + ProfileYearWall.gap),
                    y: CGFloat(index / key.grid.across) * (cell.height + ProfileYearWall.gap),
                    width: cell.width,
                    height: cell.height
                )
            },
            cornerRadius: ProfileMenuRow.groupCornerRadius,
            fade: ProfileYearWall.fadeStops(height: key.size.height)
        )
        let image = await Task.detached(priority: .userInitiated) { plan.draw(posters) }.value
        guard !Task.isCancelled else { return nil }
        drawn[key] = image
        order.removeAll { $0 == key }
        order.append(key)
        if order.count > keep {
            drawn[order.removeFirst()] = nil
        }
        return image
    }
}

/// Everything the drawing needs, worked out on the main actor, so the drawing itself runs anywhere.
private nonisolated struct WallPlan: Sendable {
    let size: CGSize
    let scale: CGFloat
    let cells: [CGRect]
    let cornerRadius: CGFloat
    let fade: [(location: CGFloat, alpha: CGFloat)]

    /// Each poster filling its poster-shaped cell over the tile color, 10% less saturated, darkened 22%, at 62%. Then
    /// the top corners rounded and the fades applied as the picture's own transparency.
    func draw(_ posters: [UIImage?]) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        format.opaque = false
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            let cg = context.cgContext
            let bounds = CGRect(origin: .zero, size: size)
            UIBezierPath(
                roundedRect: bounds,
                byRoundingCorners: [.topLeft, .topRight],
                cornerRadii: CGSize(width: cornerRadius, height: cornerRadius)
            ).addClip()
            UIColor(red: 0.102, green: 0.102, blue: 0.098, alpha: 1).setFill()
            cg.fill(bounds)
            for (cell, poster) in zip(cells, posters) {
                guard let poster, poster.size.width > 0, poster.size.height > 0 else { continue }
                cg.saveGState()
                cg.clip(to: cell)
                cg.setAlpha(0.62)
                cg.beginTransparencyLayer(auxiliaryInfo: nil)
                let fill = max(cell.width / poster.size.width, cell.height / poster.size.height)
                let drawn = CGSize(width: poster.size.width * fill, height: poster.size.height * fill)
                poster.draw(in: CGRect(
                    x: cell.midX - drawn.width / 2,
                    y: cell.midY - drawn.height / 2,
                    width: drawn.width,
                    height: drawn.height
                ))
                cg.setBlendMode(.saturation)
                UIColor(white: 0.5, alpha: 0.1).setFill()
                cg.fill(cell)
                cg.setBlendMode(.normal)
                UIColor(white: 0, alpha: 0.22).setFill()
                cg.fill(cell)
                cg.endTransparencyLayer()
                cg.restoreGState()
            }
            // The fades: the picture keeps only as much of itself as the fade allows at each height.
            let colors = fade.map { UIColor(white: 0, alpha: $0.alpha).cgColor } as CFArray
            let locations = fade.map(\.location)
            if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: locations) {
                cg.setBlendMode(.destinationIn)
                cg.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: 0, y: size.height), options: [])
            }
        }
    }
}

/// One type's line: "92 films" then "171 hours", or "No games yet".
struct ProfileYearRow: Identifiable, Equatable {
    let mediaType: String
    let count: String
    /// The type's time in its own unit, as the Stats page writes it; nil when there's none to show.
    let measure: String?
    let isEmpty: Bool
    let accessibilityLabel: String

    var id: String { mediaType }
}

enum ProfileYearPresentation {
    /// One row per turned-on type, in the server's order. The count names the type ("92 films", "5 anime"), so the
    /// type's name never sits beside its own noun.
    static func rows(for year: ProfileYear) -> [ProfileYearRow] {
        let time = Dictionary(year.time.rows.map { ($0.mediaType, $0) }, uniquingKeysWith: { first, _ in first })
        return year.mediaTypes.map { type in
            let name = MediaTypeTheme.theme(for: type.mediaType).displayName
            guard type.titles > 0 else {
                let noun = ListCountWords.noun(count: 0, mediaTypes: [type.mediaType], isPeople: false)
                return ProfileYearRow(
                    mediaType: type.mediaType,
                    count: "No \(noun) yet",
                    measure: nil,
                    isEmpty: true,
                    accessibilityLabel: "\(name), nothing this year"
                )
            }
            let count = ListCountWords.text(count: type.titles, mediaTypes: [type.mediaType], isPeople: false)
            let measure = time[type.mediaType].flatMap { $0.value > 0 ? StatsTimeSection.value(for: $0) : nil }
            return ProfileYearRow(
                mediaType: type.mediaType,
                count: count,
                measure: measure,
                isEmpty: false,
                accessibilityLabel: [name, count, measure].compactMap { $0 }.joined(separator: ", ") + " this year"
            )
        }
    }

    static func totalAccessibilityLabel(_ year: ProfileYear) -> String {
        "\(year.titles.formatted()) \(year.titles == 1 ? "title" : "titles") this year"
    }
}
