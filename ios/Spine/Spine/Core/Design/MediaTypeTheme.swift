import SwiftUI

struct MediaTypeTheme: Equatable {
    let slug: String
    let displayName: String
    let symbolName: String
    let gradientColors = [Color(red: 0.31, green: 0.33, blue: 0.37), Color(red: 0.09, green: 0.10, blue: 0.12)]

    var accentColor: Color {
        slug == "music" ? Self.musicColor : Color(red: 0.72, green: 0.74, blue: 0.78)
    }

    var artworkOrientation: PosterOrientation {
        slug == "music" ? .square : .portrait
    }

    var statsColor: Color {
        switch slug {
        case "movie": Color(red: 0.98, green: 0.49, blue: 0.24)
        case "tv": Color(red: 0.25, green: 0.70, blue: 0.96)
        case "anime": Color(red: 0.93, green: 0.39, blue: 0.67)
        case "manga": Color(red: 0.34, green: 0.82, blue: 0.58)
        case "game": Color(red: 0.50, green: 0.47, blue: 0.96)
        case "book": Color(red: 0.91, green: 0.69, blue: 0.25)
        case "comic": Color(red: 0.93, green: 0.34, blue: 0.35)
        case "music": Self.musicColor
        default: Color(red: 0.30, green: 0.77, blue: 0.95)
        }
    }

    private static let musicColor = Color(red: 0.20, green: 0.78, blue: 0.74)

    static func theme(for slug: String) -> MediaTypeTheme {
        let normalized = slug.lowercased()
        switch normalized {
        case APIConstants.allMedia:
            return MediaTypeTheme(
                slug: normalized,
                displayName: "All Media",
                symbolName: "globe"
            )
        case "movie":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Movies",
                symbolName: "film"
            )
        case "tv":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "TV",
                symbolName: "tv"
            )
        case "anime":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Anime",
                symbolName: "sparkles"
            )
        case "manga":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Manga",
                symbolName: "book.closed"
            )
        case "game":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Games",
                symbolName: "gamecontroller.fill"
            )
        case "book":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Books",
                symbolName: "text.book.closed.fill"
            )
        case "comic":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Comics",
                symbolName: "rectangle.3.group.bubble.left"
            )
        case "music":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Music",
                symbolName: "music.note.list"
            )
        case "boardgame":
            return MediaTypeTheme(
                slug: normalized,
                displayName: "Board Games",
                symbolName: "dice.fill"
            )
        default:
            return MediaTypeTheme(
                slug: normalized,
                displayName: normalized.split(separator: "_").map { $0.capitalized }.joined(separator: " "),
                symbolName: "square.grid.2x2"
            )
        }
    }
}

struct MediaTypeGlyph: View {
    let theme: MediaTypeTheme
    let size: CGFloat

    var body: some View {
        switch theme.slug {
        case "movie":
            ClapperboardShape()
                .fill(.white, style: FillStyle(eoFill: true))
                .frame(width: size * 1.36, height: size * 1.14)
        case "tv":
            AntennaTVGlyph(size: size)
        case "anime":
            SpikyHairGlyph(size: size)
        case "manga":
            MangaBookGlyph(size: size)
        case "comic":
            ComicPowGlyph(size: size)
        default:
            Image(systemName: theme.symbolName)
                .symbolRenderingMode(.hierarchical)
                .font(.system(size: theme.slug == "book" ? size * 1.08 : size, weight: .semibold))
                .foregroundStyle(.white, theme.accentColor)
        }
    }
}

struct MediaLensCircleStyle: ViewModifier {
    let isSelected: Bool
    var isPressed = false

    func body(content: Content) -> some View {
        content
            .foregroundStyle(.white)
            .background {
                Circle()
                    .fill(isSelected ? Color.white.opacity(0.155) : Color.black.opacity(0.36))
            }
            .overlay {
                Circle()
                    .stroke(.white.opacity(isSelected ? 0.0 : 0.065), lineWidth: 1)
            }
            .scaleEffect(isPressed ? 0.94 : 1)
    }
}

private struct ClapperboardShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()

        let body = CGRect(
            x: rect.minX + rect.width * 0.08,
            y: rect.minY + rect.height * 0.43,
            width: rect.width * 0.84,
            height: rect.height * 0.48
        )
        path.addRoundedRect(in: body, cornerSize: CGSize(width: rect.width * 0.045, height: rect.width * 0.045))

        path.addRect(CGRect(
            x: body.minX + body.width * 0.08,
            y: body.minY + body.height * 0.38,
            width: body.width * 0.84,
            height: body.height * 0.46
        ))

        let stripTop = body.minY + body.height * 0.08
        let stripBottom = body.minY + body.height * 0.33
        for index in 0 ..< 3 {
            let x = body.minX + body.width * (0.08 + CGFloat(index) * 0.31)
            let width = body.width * 0.20
            path.move(to: CGPoint(x: x, y: stripBottom))
            path.addLine(to: CGPoint(x: x + width * 0.54, y: stripTop))
            path.addLine(to: CGPoint(x: x + width, y: stripTop))
            path.addLine(to: CGPoint(x: x + width * 0.46, y: stripBottom))
            path.closeSubpath()
        }

        let slate = [
            CGPoint(x: rect.minX + rect.width * 0.08, y: rect.minY + rect.height * 0.39),
            CGPoint(x: rect.minX + rect.width * 0.72, y: rect.minY + rect.height * 0.06),
            CGPoint(x: rect.minX + rect.width * 0.78, y: rect.minY + rect.height * 0.06),
            CGPoint(x: rect.minX + rect.width * 0.88, y: rect.minY + rect.height * 0.22),
            CGPoint(x: rect.minX + rect.width * 0.20, y: rect.minY + rect.height * 0.56)
        ]
        path.move(to: slate[0])
        for point in slate.dropFirst() {
            path.addLine(to: point)
        }
        path.closeSubpath()

        for cut in [
            [
                CGPoint(x: rect.minX + rect.width * 0.22, y: rect.minY + rect.height * 0.33),
                CGPoint(x: rect.minX + rect.width * 0.36, y: rect.minY + rect.height * 0.26),
                CGPoint(x: rect.minX + rect.width * 0.32, y: rect.minY + rect.height * 0.43),
                CGPoint(x: rect.minX + rect.width * 0.18, y: rect.minY + rect.height * 0.50)
            ],
            [
                CGPoint(x: rect.minX + rect.width * 0.48, y: rect.minY + rect.height * 0.20),
                CGPoint(x: rect.minX + rect.width * 0.62, y: rect.minY + rect.height * 0.13),
                CGPoint(x: rect.minX + rect.width * 0.58, y: rect.minY + rect.height * 0.30),
                CGPoint(x: rect.minX + rect.width * 0.44, y: rect.minY + rect.height * 0.37)
            ],
            [
                CGPoint(x: rect.minX + rect.width * 0.69, y: rect.minY + rect.height * 0.09),
                CGPoint(x: rect.minX + rect.width * 0.76, y: rect.minY + rect.height * 0.06),
                CGPoint(x: rect.minX + rect.width * 0.72, y: rect.minY + rect.height * 0.23),
                CGPoint(x: rect.minX + rect.width * 0.64, y: rect.minY + rect.height * 0.27)
            ]
        ] {
            path.move(to: cut[0])
            for point in cut.dropFirst() {
                path.addLine(to: point)
            }
            path.closeSubpath()
        }

        return path
    }
}

/// Antenna TV set with "TV" knocked out of the screen.
private struct AntennaTVGlyph: View {
    let size: CGFloat

    var body: some View {
        Canvas { context, canvasSize in
            let lineWidth = max(1.3, size * 0.085)
            let body = CGRect(
                x: 0,
                y: canvasSize.height * 0.265,
                width: canvasSize.width,
                height: canvasSize.height * 0.735
            )
            let tipY = lineWidth / 2 + canvasSize.height * 0.01

            var antenna = Path()
            antenna.move(to: CGPoint(x: canvasSize.width * 0.255, y: tipY))
            antenna.addLine(to: CGPoint(x: body.midX, y: body.minY + lineWidth))
            antenna.addLine(to: CGPoint(x: canvasSize.width * 0.745, y: tipY))
            context.stroke(antenna, with: .color(.white), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
            context.fill(Path(roundedRect: body, cornerRadius: body.height * 0.28, style: .continuous), with: .color(.white))

            context.blendMode = .destinationOut
            context.draw(
                Text("TV")
                    .font(.system(size: size * 0.68, weight: .black, design: .rounded))
                    .foregroundStyle(.black),
                at: CGPoint(x: body.midX, y: body.midY + size * 0.01)
            )
        }
        .frame(width: size * 1.38, height: size * 1.24)
    }
}

/// Anime: a spiky shonen hair silhouette with the face cut out.
private struct SpikyHairGlyph: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            SpikyHairShape()
                .fill(.white)
            SpikyHairFaceShape()
                .fill(.black)
                .blendMode(.destinationOut)
            SpikyHairFaceShape()
                .stroke(.white, style: StrokeStyle(lineWidth: max(1.0, size * 0.06), lineJoin: .round))
        }
        .compositingGroup()
        .frame(width: size * 1.6, height: size * 1.3)
    }
}

/// Maps the hair artwork's design space (a 512pt square) into the frame.
private func spikyHairPoint(in rect: CGRect) -> (CGFloat, CGFloat) -> CGPoint {
    let artwork = CGRect(x: 22, y: 60, width: 468, height: 382)
    let scale = min(rect.width / artwork.width, rect.height / artwork.height)
    let originX = rect.midX - artwork.midX * scale
    let originY = rect.midY - artwork.midY * scale
    return { x, y in CGPoint(x: originX + x * scale, y: originY + y * scale) }
}

private struct SpikyHairShape: Shape {
    func path(in rect: CGRect) -> Path {
        let point = spikyHairPoint(in: rect)
        var path = Path()
        path.move(to: point(24, 165))
        path.addQuadCurve(to: point(176, 128), control: point(100, 118))
        path.addLine(to: point(194, 62))
        path.addQuadCurve(to: point(302, 198), control: point(300, 92))
        path.addQuadCurve(to: point(372, 236), control: point(352, 208))
        for (x, y) in [
            (490, 238), (396, 290), (448, 324), (372, 340), (414, 392), (350, 364), (262, 368),
            (168, 352), (96, 382), (142, 322), (36, 304), (128, 268), (64, 232),
        ] as [(CGFloat, CGFloat)] {
            path.addLine(to: point(x, y))
        }
        path.addQuadCurve(to: point(24, 165), control: point(110, 205))
        path.closeSubpath()
        return path
    }
}

/// Face with the fringe hanging into it.
private struct SpikyHairFaceShape: Shape {
    func path(in rect: CGRect) -> Path {
        let point = spikyHairPoint(in: rect)
        var path = Path()
        path.move(to: point(166, 318))
        for (x, y) in [
            (200, 348), (222, 286), (248, 334), (286, 274), (302, 334), (332, 296), (356, 344), (362, 322), (354, 362),
        ] as [(CGFloat, CGFloat)] {
            path.addLine(to: point(x, y))
        }
        path.addQuadCurve(to: point(262, 436), control: point(330, 420))
        path.addQuadCurve(to: point(178, 360), control: point(196, 420))
        path.closeSubpath()
        return path
    }
}

/// Manga: an open book whose pages carry a pair of anime eyes.
private struct MangaBookGlyph: View {
    let size: CGFloat

    var body: some View {
        let width = size * 1.42, height = size * 1.10
        let eyeSize = width * 0.34
        let eyeOffsetX = width * 0.235, eyeOffsetY = height * 0.06
        // The tiny lower glint turns to noise below ~15pt.
        let showsSmallGlint = size >= 15

        ZStack {
            OpenBookShape()
                .fill(.white)
            ZStack {
                MangaEyeHoles(mirrored: true)
                    .frame(width: eyeSize, height: eyeSize)
                    .offset(x: -eyeOffsetX, y: eyeOffsetY)
                MangaEyeHoles(mirrored: false)
                    .frame(width: eyeSize, height: eyeSize)
                    .offset(x: eyeOffsetX, y: eyeOffsetY)
            }
            .blendMode(.destinationOut)
            MangaEyeGlintShape(mirrored: true, showsSmallGlint: showsSmallGlint)
                .fill(.white)
                .frame(width: eyeSize, height: eyeSize)
                .offset(x: -eyeOffsetX, y: eyeOffsetY)
            MangaEyeGlintShape(mirrored: false, showsSmallGlint: showsSmallGlint)
                .fill(.white)
                .frame(width: eyeSize, height: eyeSize)
                .offset(x: eyeOffsetX, y: eyeOffsetY)
        }
        .compositingGroup()
        .frame(width: width, height: height)
    }
}

private struct OpenBookShape: Shape {
    func path(in rect: CGRect) -> Path {
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + x * rect.width, y: rect.minY + y * rect.height)
        }

        var path = Path()
        path.move(to: point(0.47, 0.14))
        path.addQuadCurve(to: point(0.00, 0.08), control: point(0.24, -0.02))
        path.addLine(to: point(0.00, 0.86))
        path.addQuadCurve(to: point(0.47, 0.96), control: point(0.26, 0.76))
        path.closeSubpath()

        path.move(to: point(0.53, 0.14))
        path.addQuadCurve(to: point(1.00, 0.08), control: point(0.76, -0.02))
        path.addLine(to: point(1.00, 0.86))
        path.addQuadCurve(to: point(0.53, 0.96), control: point(0.74, 0.76))
        path.closeSubpath()
        return path
    }
}

/// Lash and iris drawn as separate shapes: overlapping subpaths in one path can cancel out under nonzero fill.
private struct MangaEyeHoles: View {
    let mirrored: Bool

    var body: some View {
        ZStack {
            MangaEyeLashShape(mirrored: mirrored).fill(.white)
            MangaEyeIrisShape().fill(.white)
        }
    }
}

/// Thick lash band that flicks out at the outer corner.
private struct MangaEyeLashShape: Shape {
    let mirrored: Bool

    func path(in rect: CGRect) -> Path {
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + (mirrored ? 1 - x : x) * rect.width, y: rect.minY + y * rect.height)
        }

        var path = Path()
        path.move(to: point(0.00, 0.42))
        path.addQuadCurve(to: point(0.84, 0.16), control: point(0.38, 0.00))
        path.addLine(to: point(1.00, 0.04))
        path.addLine(to: point(0.95, 0.30))
        path.addQuadCurve(to: point(0.05, 0.53), control: point(0.44, 0.17))
        path.closeSubpath()
        return path
    }
}

/// Tall iris, centered horizontally so it needs no mirroring.
private struct MangaEyeIrisShape: Shape {
    func path(in rect: CGRect) -> Path {
        Path(ellipseIn: CGRect(
            x: rect.minX + 0.24 * rect.width,
            y: rect.minY + 0.20 * rect.height,
            width: 0.52 * rect.width,
            height: 0.80 * rect.height
        ))
    }
}

private struct MangaEyeGlintShape: Shape {
    let mirrored: Bool
    let showsSmallGlint: Bool

    func path(in rect: CGRect) -> Path {
        func oval(_ x0: CGFloat, _ y0: CGFloat, _ x1: CGFloat, _ y1: CGFloat) -> Path {
            Path(ellipseIn: CGRect(
                x: rect.minX + x0 * rect.width,
                y: rect.minY + y0 * rect.height,
                width: (x1 - x0) * rect.width,
                height: (y1 - y0) * rect.height
            ))
        }

        let glintX: CGFloat = mirrored ? 1 - 0.51 : 0.31
        var path = oval(glintX, 0.37, glintX + 0.20, 0.60)
        if showsSmallGlint {
            let smallX: CGFloat = mirrored ? 1 - 0.67 : 0.57
            path.addPath(oval(smallX, 0.74, smallX + 0.10, 0.85))
        }
        return path
    }
}

/// Comic: a filled action burst with "POW" knocked out.
private struct ComicPowGlyph: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            ComicBurstShape()
                .fill(.white)
            Text("POW")
                .font(.system(size: size * 0.56, weight: .black))
                .fontWidth(.compressed)
                .fixedSize()
                .rotationEffect(.degrees(-8))
                .blendMode(.destinationOut)
        }
        .compositingGroup()
        .frame(width: size * 1.48, height: size * 1.24)
    }
}

private struct ComicBurstShape: Shape {
    private let spikes = 13
    private let innerRadius: CGFloat = 0.80
    private let spikeLengths: [CGFloat] = [1.0, 0.9, 1.0, 0.94, 1.0, 0.88, 1.0, 0.92, 0.98, 0.9, 1.0, 0.94, 0.9]

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let pointCount = spikes * 2
        for index in 0 ..< pointCount {
            let angle = CGFloat(index) / CGFloat(pointCount) * 2 * .pi - .pi / 2
            let radius = index.isMultiple(of: 2) ? spikeLengths[index / 2 % spikeLengths.count] : innerRadius
            let point = CGPoint(
                x: center.x + cos(angle) * rect.width / 2 * radius,
                y: center.y + sin(angle) * rect.height / 2 * radius
            )
            if index == 0 {
                path.move(to: point)
            } else {
                path.addLine(to: point)
            }
        }
        path.closeSubpath()
        return path
    }
}
