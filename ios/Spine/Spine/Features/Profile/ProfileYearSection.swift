import SwiftUI

/// The profile's This Year section, under the menu: this year's numbers per turned-on type, the Stats page's own
/// (STAT-312), on a glass plate over a wall of the year's posters. The wall is as wide as the menu and its top tucks
/// under the Stats row, fading in from under the menu's glass. Under 9 posters there's no wall: the numbers sit on
/// glass like the menu's. Each type with something this year opens the Stats page on that type and year.
struct ProfileYearSection<Destination: View>: View {
    let year: ProfileYear
    @ViewBuilder let destination: (String) -> Destination

    /// The section's width; the design's until it's measured.
    @State private var width: CGFloat = 398

    var body: some View {
        let rows = ProfileYearPresentation.rows(for: year)
        Group {
            if let grid = ProfileYearWall.grid(posters: year.posters.count, width: width) {
                wall(grid, rows: rows)
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

    private func wall(_ grid: ProfileYearWall.Grid, rows: [ProfileYearRow]) -> some View {
        let shape = RoundedRectangle(cornerRadius: ProfileMenuRow.groupCornerRadius, style: .continuous)
        return ZStack(alignment: .bottom) {
            posterGrid(grid)
                .background(ProfileYearWall.tileColor)
                .overlay { shape.strokeBorder(.white.opacity(0.1), lineWidth: 0.5) }
                .clipShape(shape)
                .mask { ProfileYearWall.fade }
                .accessibilityHidden(true)

            numbers(rows)
                .padding(EdgeInsets(top: 16, leading: 16, bottom: 6, trailing: 16))
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassEffect(.regular.tint(.black.opacity(0.32)), in: RoundedRectangle(cornerRadius: 15, style: .continuous))
                .padding(8)
        }
        .frame(height: ProfileYearWall.height)
        .environment(\.colorScheme, .dark)
        // The top tucks under the Stats row; the menu draws over it.
        .padding(.top, -ProfileYearWall.tuck)
    }

    private func posterGrid(_ grid: ProfileYearWall.Grid) -> some View {
        let posters = Array(year.posters.prefix(grid.count))
        let cell = ProfileYearWall.cellSize(grid, width: width)
        return VStack(spacing: ProfileYearWall.gap) {
            ForEach(0..<grid.down, id: \.self) { row in
                HStack(spacing: ProfileYearWall.gap) {
                    ForEach(0..<grid.across, id: \.self) { column in
                        tile(posters[row * grid.across + column], size: cell)
                    }
                }
            }
        }
        .frame(height: ProfileYearWall.height)
    }

    private func tile(_ media: MediaSummary, size: CGSize) -> some View {
        ProfileYearWall.tileColor
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .overlay {
                SpineAsyncImage(url: URL(string: media.displayPosterURL ?? ""), thumbnailSize: size) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                            .saturation(0.9)
                            .overlay(Color.black.opacity(0.22))
                            .opacity(0.62)
                    }
                }
            }
            .clipped()
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

/// The wall's grid, worked out from the posters and the space, never from fixed steps: the most posters that fill
/// the wall with no gaps, at most 6 across, each cell within 20% of a poster's 2:3 shape. Under 9 posters there's
/// no wall. In the 398-wide wall that gives 9 (3 x 3), 16, 20, 25, 30 and 36 (6 x 6); a count in between drops to
/// the step below it.
enum ProfileYearWall {
    static let minimumPosters = 9
    static let maximumAcross = 6
    static let posterShape: CGFloat = 2.0 / 3.0
    static let shapeSlack: CGFloat = 0.2
    static let gap: CGFloat = 2
    /// What shows under the menu.
    static let visibleHeight: CGFloat = 520
    /// How far the top runs up under the Stats row.
    static let tuck: CGFloat = 48
    /// Full strength this far below the menu's bottom edge.
    static let fadeBelow: CGFloat = 56
    static var height: CGFloat { visibleHeight + tuck }
    static let tileColor = Color(red: 0.102, green: 0.102, blue: 0.098)

    struct Grid: Equatable {
        let across: Int
        let down: Int

        var count: Int { across * down }
    }

    static func grid(posters: Int, width: CGFloat, height: CGFloat = height) -> Grid? {
        guard posters >= minimumPosters, width > 0, height > 0 else { return nil }
        var best: Grid?
        var bestOff = CGFloat.infinity
        for across in 1...maximumAcross {
            var down = 1
            while across * down <= posters {
                let cell = cellSize(Grid(across: across, down: down), width: width, height: height)
                let shape = cell.width / cell.height
                if shape >= posterShape * (1 - shapeSlack), shape <= posterShape * (1 + shapeSlack) {
                    let off = abs(shape - posterShape)
                    let count = across * down
                    if count > best?.count ?? 0 || (count == best?.count && off < bestOff) {
                        best = Grid(across: across, down: down)
                        bestOff = off
                    }
                }
                down += 1
            }
        }
        return best
    }

    static func cellSize(_ grid: Grid, width: CGFloat, height: CGFloat = height) -> CGSize {
        CGSize(
            width: (width - CGFloat(grid.across - 1) * gap) / CGFloat(grid.across),
            height: (height - CGFloat(grid.down - 1) * gap) / CGFloat(grid.down)
        )
    }

    /// Nothing at the top, under the menu; a third at the menu's bottom edge; full strength below.
    static var fade: some View {
        LinearGradient(
            stops: [
                .init(color: .clear, location: 0),
                .init(color: .black.opacity(0.35), location: tuck / height),
                .init(color: .black, location: (tuck + fadeBelow) / height),
                .init(color: .black, location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
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
