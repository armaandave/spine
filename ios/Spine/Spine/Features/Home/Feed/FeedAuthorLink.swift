import SwiftUI

/// A feed card's author line: a 20 pt avatar and the username, in the card's ink. Tapping it opens the person's
/// profile. It is its own accessibility element, so the card's tap and VoiceOver action leave it alone.
struct FeedAuthorLink: View {
    /// Line one of a card is this tall.
    static let height: CGFloat = 22

    let username: String
    let avatarURL: String?
    let ink: ArtInk

    var body: some View {
        NavigationLink {
            UserProfileDestination(username: username)
        } label: {
            HStack(spacing: 6) {
                FeedAvatar(username: username, avatarURL: avatarURL, ink: ink)
                Text(username)
                    .font(HomeDesign.Text.username)
                    .lineLimit(1)
                    .feedTextLift(ink)
                    .foregroundStyle(ink.text)
            }
            .frame(height: Self.height)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(username)
        .accessibilityHint("Opens profile")
    }
}

/// A person's picture at 20 pt, decoded at that size. Without one, their initial on a pastel disc.
struct FeedAvatar: View {
    static let side: CGFloat = 20

    let username: String
    let avatarURL: String?
    let ink: ArtInk

    /// The ring outside the 20 pt disc (22 pt in all, the author line's height) and the baked shadow under it.
    private var ringColor: Color {
        ink == .dark ? Color.white.opacity(0.35) : Color.white.opacity(0.7)
    }

    private var shadowColor: Color {
        ink == .dark ? Color.black.opacity(0.45) : Color(red: 40 / 255, green: 30 / 255, blue: 0).opacity(0.30)
    }

    var body: some View {
        SpineAsyncImage(
            url: FeedCardArt.url(from: avatarURL),
            thumbnailSize: FeedCardArt.avatarSize
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                initialDisc
            }
        }
        .frame(width: Self.side, height: Self.side)
        .clipShape(Circle())
        .background {
            Circle().inset(by: -0.5).stroke(ringColor, lineWidth: 1)
        }
        .background {
            BakedShadowView(
                size: CGSize(width: Self.side, height: Self.side),
                cornerRadius: Self.side / 2,
                blur: 3,
                offsetY: 1,
                tint: shadowColor
            )
        }
        .accessibilityHidden(true)
    }

    private var initialDisc: some View {
        ZStack {
            Self.discColor(for: username)
            Text(Self.initial(of: username))
                .font(.system(size: 10, weight: .heavy))
                .foregroundStyle(HomeDesign.Palette.avatarInk)
        }
    }

    /// The first letter, uppercased; "?" for an empty name.
    nonisolated static func initial(of username: String) -> String {
        guard let first = username.first(where: { $0.isLetter || $0.isNumber }) else { return "?" }
        return String(first).uppercased()
    }

    /// A pastel that stays the same for a name: its hue comes from the name's characters.
    nonisolated static func hue(for username: String) -> Double {
        let sum = username.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) % 360 }
        return Double(sum) / 360
    }

    static func discColor(for username: String) -> Color {
        Color(hue: hue(for: username), saturation: 0.36, brightness: 0.97)
    }
}
