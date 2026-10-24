import SwiftUI

/// A streaming service's logo, as TMDB serves it, in a small rounded square.
struct StreamingFilterServiceLogo: View {
    let urlString: String?
    var size: CGFloat = 28

    var body: some View {
        SpineAsyncImage(
            url: urlString.flatMap(URL.init(string:)),
            thumbnailSize: CGSize(width: size, height: size)
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "play.tv")
                    .font(.system(size: size * 0.45, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.secondary.opacity(0.18))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.24, style: .continuous))
        .accessibilityHidden(true)
    }
}

/// A quiet line above Service-filtered results while the server is still checking some titles (ST-301).
struct StreamingFilterPendingNote: View {
    let text: String

    var body: some View {
        Label {
            Text(text)
        } icon: {
            Image(systemName: "clock.arrow.circlepath")
        }
        .font(.footnote.weight(.semibold))
        .foregroundStyle(.white.opacity(0.62))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(.white.opacity(0.08), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
    }
}
