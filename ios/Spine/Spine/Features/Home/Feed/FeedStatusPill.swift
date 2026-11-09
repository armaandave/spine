import SwiftUI

/// How a pill's surface is drawn on each ink: a fill, a hairline rim, a top highlight and one baked drop shadow. All
/// flat fills, strokes and a bitmap; nothing blurs live.
nonisolated struct FeedPillStyle {
    /// The fill from the top to the bottom; one color twice for a flat fill.
    let fillTop: Color
    let fillBottom: Color
    let rim: Color
    /// The white highlight along the top edge, at its brightest.
    let highlight: Double
    let shadowBlur: CGFloat
    let shadowOffsetY: CGFloat
    let shadow: Color
    /// The rim's and the top highlight's widths.
    var rimWidth: CGFloat = 0.5
    var highlightWidth: CGFloat = 0.75

    private static let warmShadow = Color(red: 40 / 255, green: 30 / 255, blue: 0)

    /// The counts pill: a soft gloss.
    static func counts(ink: ArtInk) -> FeedPillStyle {
        switch ink {
        case .dark:
            FeedPillStyle(
                fillTop: .white.opacity(0.11), fillBottom: .white.opacity(0.05), rim: .white.opacity(0.32),
                highlight: HomeDesign.FeedDepth.pillHighlightDark, shadowBlur: 3, shadowOffsetY: 1,
                shadow: .black.opacity(0.22)
            )
        case .light:
            FeedPillStyle(
                fillTop: .white.opacity(0.34), fillBottom: .white.opacity(0.24),
                rim: HomeDesign.Palette.lightInk.opacity(0.30),
                highlight: HomeDesign.FeedDepth.pillHighlightLight, shadowBlur: 3, shadowOffsetY: 1,
                shadow: warmShadow.opacity(0.12)
            )
        }
    }

    /// The status pill's border: one pixel on a 3x screen, and a highlight two thirds as wide as the counts pill's.
    static let statusRimWidth: CGFloat = 1.0 / 3
    static let statusHighlightWidth: CGFloat = 0.5

    /// The status pill: a hairline capsule, its highlight at its weakest.
    static func status(ink: ArtInk) -> FeedPillStyle {
        switch ink {
        case .dark:
            FeedPillStyle(
                fillTop: .white.opacity(0.07), fillBottom: .white.opacity(0.07), rim: .white.opacity(0.42),
                highlight: 0.20, shadowBlur: 5, shadowOffsetY: 2, shadow: .black.opacity(0.35),
                rimWidth: statusRimWidth, highlightWidth: statusHighlightWidth
            )
        case .light:
            FeedPillStyle(
                fillTop: .white.opacity(0.30), fillBottom: .white.opacity(0.30),
                rim: HomeDesign.Palette.lightInk.opacity(0.38),
                highlight: 0.60, shadowBlur: 5, shadowOffsetY: 2, shadow: warmShadow.opacity(0.22),
                rimWidth: statusRimWidth, highlightWidth: statusHighlightWidth
            )
        }
    }
}

/// A pill's surface behind and over its content: a baked, stretchy capsule shadow, the fill, the rim and a hairline
/// highlight that fades out by the middle. The content sets the pill's size.
struct FeedPillSurface: ViewModifier {
    let style: FeedPillStyle
    let height: CGFloat

    func body(content: Content) -> some View {
        let baked = BakedShadow.capsule(height: height, blur: style.shadowBlur, offsetY: style.shadowOffsetY)
        let pad = BakedShadow.padding(blur: style.shadowBlur)
        content
            .background {
                Image(uiImage: baked.image)
                    .renderingMode(.template)
                    .resizable(
                        capInsets: EdgeInsets(
                            top: baked.capInsets.top, leading: baked.capInsets.left,
                            bottom: baked.capInsets.bottom, trailing: baked.capInsets.right
                        ),
                        resizingMode: .stretch
                    )
                    .foregroundStyle(style.shadow)
                    .padding(-pad)
                    .allowsHitTesting(false)
            }
            .background {
                Capsule().fill(LinearGradient(
                    colors: [style.fillTop, style.fillBottom], startPoint: .top, endPoint: .bottom
                ))
            }
            .overlay {
                Capsule().strokeBorder(style.rim, lineWidth: style.rimWidth)
            }
            .overlay {
                Capsule()
                    .strokeBorder(
                        LinearGradient(
                            stops: [
                                .init(color: .white.opacity(style.highlight), location: 0),
                                .init(color: .white.opacity(0), location: 0.5),
                            ],
                            startPoint: .top,
                            endPoint: .bottom
                        ),
                        lineWidth: style.highlightWidth
                    )
                    .padding(style.rimWidth)
            }
    }
}

extension View {
    func feedPillSurface(_ style: FeedPillStyle, height: CGFloat) -> some View {
        modifier(FeedPillSurface(style: style, height: height))
    }
}

/// "Paused", "Picked back up", "Stopped": a hairline capsule with a glowing dot. At rest it is plain: no tilt, no pop,
/// no scale.
struct FeedStatusPill: View {
    let word: String
    let ink: ArtInk

    /// The words the presenter emits; anything else it capitalizes gets a neutral dot.
    enum Kind: Equatable {
        case paused
        case pickedBackUp
        case stopped
        case other

        init(word: String) {
            switch word {
            case "Paused": self = .paused
            case "Picked back up": self = .pickedBackUp
            case "Stopped": self = .stopped
            default: self = .other
            }
        }

        /// The glows at half the strength they first had.
        static let glowStrength = 0.5

        /// The dot, and the color of the glow behind it with its strength; nil for no glow.
        func dot(ink: ArtInk) -> (color: Color, glow: (color: Color, alpha: Double)?) {
            let dark = ink == .dark
            switch self {
            case .paused:
                return (
                    dark ? Color(hex: 0xFFC24D) : Color(hex: 0xD98A00),
                    (Color(red: 255 / 255, green: 176 / 255, blue: 40 / 255), (dark ? 0.85 : 0.55) * Self.glowStrength)
                )
            case .pickedBackUp:
                let green = dark ? Color(hex: 0x34D07A) : Color(hex: 0x0C8F47)
                return (green, (green, (dark ? 0.85 : 0.55) * Self.glowStrength))
            case .stopped:
                return (
                    dark ? Color(hex: 0xFF6B6B) : Color(hex: 0xC0392B),
                    (Color(red: 255 / 255, green: 107 / 255, blue: 107 / 255), (dark ? 0.7 : 0.45) * Self.glowStrength)
                )
            case .other:
                return (dark ? Color.white.opacity(0.7) : HomeDesign.Palette.lightInk.opacity(0.6), nil)
            }
        }
    }

    var body: some View {
        let kind = Kind(word: word)
        let dot = kind.dot(ink: ink)
        HStack(spacing: HomeDesign.FeedDepth.statusGap) {
            Circle()
                .fill(dot.color)
                .frame(width: HomeDesign.FeedDepth.statusDot, height: HomeDesign.FeedDepth.statusDot)
                .background { glow(dot.glow) }
                .padding(.horizontal, HomeDesign.FeedDepth.statusDotMargin)
            Text(word)
                .font(HomeDesign.FeedDepth.statusFont)
                .lineLimit(1)
                .fixedSize()
                .foregroundStyle(ink == .dark ? Color.white.opacity(0.94) : HomeDesign.Palette.lightInk)
        }
        .padding(.leading, HomeDesign.FeedDepth.statusLeading)
        .padding(.trailing, HomeDesign.FeedDepth.statusTrailing)
        .frame(height: HomeDesign.FeedDepth.statusPillHeight)
        .feedPillSurface(.status(ink: ink), height: HomeDesign.FeedDepth.statusPillHeight)
    }

    /// How far the glow reaches from the dot's center: 3.75 pt past its 2.5 pt edge (it was 5).
    static let glowRadius: CGFloat = 6.25

    /// A radial disc, at full strength to the dot's edge and fading out to `glowRadius`, in a frame that takes no room.
    @ViewBuilder
    private func glow(_ glow: (color: Color, alpha: Double)?) -> some View {
        if let glow {
            RadialGradient(
                gradient: Gradient(stops: [
                    .init(color: glow.color.opacity(glow.alpha), location: 0),
                    .init(color: glow.color.opacity(glow.alpha), location: HomeDesign.FeedDepth.statusDot / 2 / Self.glowRadius),
                    .init(color: glow.color.opacity(0), location: 1),
                ]),
                center: .center,
                startRadius: 0,
                endRadius: Self.glowRadius
            )
            .frame(width: Self.glowRadius * 2, height: Self.glowRadius * 2)
            .allowsHitTesting(false)
        }
    }
}

private extension Color {
    /// A color from a 0xRRGGBB number.
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}
