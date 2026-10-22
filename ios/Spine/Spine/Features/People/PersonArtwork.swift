import SwiftUI

struct PersonArtwork: View {
    let urlString: String?
    let name: String
    var size: CGFloat

    var body: some View {
        SpineAsyncImage(url: URL(string: urlString ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Text(initials)
                    .font(.system(size: size * 0.27, weight: .bold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .frame(width: size, height: size)
        .background(.white.opacity(0.08), in: Circle())
        .clipShape(Circle())
        .overlay {
            Circle()
                .stroke(.white.opacity(0.1), lineWidth: 1)
        }
        .accessibilityHidden(true)
    }

    private var initials: String {
        Self.initials(for: name)
    }

    /// Up to two initials, or "?".
    static func initials(for name: String) -> String {
        let initials = name
            .split(whereSeparator: \.isWhitespace)
            .prefix(2)
            .compactMap(\.first)
        return initials.isEmpty ? "?" : String(initials).uppercased()
    }
}
