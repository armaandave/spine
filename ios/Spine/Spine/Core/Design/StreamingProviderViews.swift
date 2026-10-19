import SwiftUI

/// A streaming service's logo in a rounded square, like an app icon. Falls back to the name's first letter.
struct StreamingProviderLogo: View {
    let url: String?
    let name: String
    var size: CGFloat = 36

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.23, style: .continuous)

        SpineAsyncImage(
            url: url.flatMap(URL.init(string:)),
            thumbnailSize: CGSize(width: size, height: size)
        ) { phase in
            if case let .success(image) = phase {
                image.resizable().scaledToFill()
            } else {
                Text(name.prefix(1).uppercased())
                    .font(.system(size: size * 0.44, weight: .bold))
                    .foregroundStyle(.white.opacity(0.72))
                    .frame(width: size, height: size)
                    .background(.white.opacity(0.1))
            }
        }
        .frame(width: size, height: size)
        .clipShape(shape)
        .overlay { shape.strokeBorder(.white.opacity(0.12), lineWidth: 0.5) }
        .accessibilityHidden(true)
    }
}

/// "Powered by JustWatch", shown wherever streaming availability appears (ST-003).
struct JustWatchCredit: View {
    var body: some View {
        if let url = StreamingAttribution.justWatch.destination {
            Link(destination: url) {
                HStack(spacing: 4) {
                    Text("Powered by JustWatch")
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 9, weight: .bold))
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.white.opacity(0.46))
                .frame(minHeight: 28)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Powered by JustWatch")
            .accessibilityHint("Opens JustWatch in your browser")
        }
    }
}
