import Nuke
import SwiftUI
import UIKit

/// One post in the activity feed: a poster on a cinematic gradient of its own colors. A log gets the full card, and a
/// day's progress or a start gets one slim row, so reviews stand out from everyday updates.
struct ActivityPostCard: View {
    private static let cornerRadius: CGFloat = 18

    let activity: ActivityItem
    let onOpen: () -> Void
    let onToggleLike: () -> Void
    let onComment: () -> Void

    @State private var isSpoilerRevealed = false

    var body: some View {
        Group {
            if case .log = activity.post {
                logCard
            } else {
                progressRow
            }
        }
        .background {
            PosterGradientBackground(posterURL: URL(string: activity.media?.displayPosterURL ?? ""))
        }
        .clipShape(RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)
                .strokeBorder(.white.opacity(0.08), lineWidth: 0.5)
                .allowsHitTesting(false)
        }
        .contentShape(RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous))
        .onTapGesture(perform: onOpen)
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Open", onOpen)
        .accessibilityHint(ActivityFeedPresentation.destinationHint(for: activity))
    }

    private var logCard: some View {
        HStack(alignment: .top, spacing: 12) {
            artwork(slot: .activityPost)

            VStack(alignment: .leading, spacing: 4) {
                authorLine(avatarSize: 22)

                Text(activity.media?.displayTitle ?? "")
                    .font(.system(size: 18, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)

                ratingLine
                review

                Spacer(minLength: 0)

                actions
                    .padding(.bottom, -9)
                    .padding(.leading, -1)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(11)
    }

    private var progressRow: some View {
        HStack(spacing: 10) {
            artwork(slot: .listPreview)

            VStack(alignment: .leading, spacing: 3) {
                authorLine(avatarSize: 20)

                Text(ActivityFeedPresentation.progressSentence(for: activity))
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            PostActions(
                engagement: activity.engagement,
                iconSize: 19,
                spacing: 0,
                onToggleLike: onToggleLike,
                onComment: onComment
            )
        }
        .padding(.vertical, 8)
        .padding(.leading, 8)
        .padding(.trailing, 2)
    }

    private func artwork(slot: PosterSlot) -> some View {
        MediaArtwork(
            url: activity.media?.displayPosterURL,
            title: activity.media?.title ?? "",
            slot: slot,
            mediaType: activity.media?.ref.mediaType,
            orientation: activity.media?.posterOrientation
        )
        .shadow(color: .black.opacity(0.42), radius: 10, y: 5)
    }

    private func authorLine(avatarSize: CGFloat) -> some View {
        HStack(spacing: 6) {
            PostAuthorLink(user: activity.actor, avatarSize: avatarSize, font: .system(size: 13, weight: .bold))

            if let date = ActivityTimestampFormatter.text(for: activity) {
                Spacer(minLength: 4)
                Text(date)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.76))
                    .lineLimit(1)
                    .accessibilityLabel(ActivityTimestampFormatter.accessibilityText(for: activity) ?? date)
            }
        }
    }

    @ViewBuilder
    private var ratingLine: some View {
        let rating = ActivityFeedPresentation.rating(for: activity)
        if rating != nil || activity.object.liked == true || activity.object.isRewatch == true {
            HStack(spacing: 6) {
                if let rating {
                    DiaryStarRating(rating: rating, fontSize: 12)
                }
                if activity.object.liked == true {
                    Image(systemName: "heart.fill")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.pink)
                        .accessibilityLabel("Hearted")
                }
                if activity.object.isRewatch == true {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.8))
                        .accessibilityLabel(activity.media?.ref.repeatLabel ?? "Rewatch")
                }
            }
        }
    }

    @ViewBuilder
    private var review: some View {
        if let text = ActivityFeedPresentation.reviewText(for: activity) {
            if activity.containsSpoilers == true, !isSpoilerRevealed {
                Button {
                    isSpoilerRevealed = true
                } label: {
                    Label("Reveal spoiler review", systemImage: "eye.slash")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.86))
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            } else {
                Text(text)
                    .font(.system(size: 14))
                    .foregroundStyle(.white.opacity(0.86))
                    .lineSpacing(1)
                    .lineLimit(2)
                    .padding(.top, 1)
            }
        }
    }

    private var actions: some View {
        PostActions(
            engagement: activity.engagement,
            onToggleLike: onToggleLike,
            onComment: onComment
        )
    }
}

/// The two colors a card's gradient runs through, taken from its poster.
nonisolated struct PosterGradientColors: Equatable, Sendable {
    let lead: AlbumBackdropColor.HSB
    let trail: AlbumBackdropColor.HSB

    static func colors(in image: CGImage) -> PosterGradientColors? {
        guard let main = AlbumBackdropColor.mainColors(in: image) else { return nil }
        return PosterGradientColors(lead: glow(main.first), trail: deep(main.second))
    }

    /// Where the gradient starts, behind the poster: the album page's tint, brought up to catch the eye.
    static func glow(_ color: AlbumBackdropColor.HSB) -> AlbumBackdropColor.HSB {
        let tint = AlbumBackdropColor.tint(for: color)
        return .init(hue: tint.hue, saturation: min(tint.saturation * 1.35, 0.85), brightness: tint.brightness * 2.3)
    }

    /// Where it settles, under the text: dark enough for white text on any poster.
    static func deep(_ color: AlbumBackdropColor.HSB) -> AlbumBackdropColor.HSB {
        let tint = AlbumBackdropColor.tint(for: color)
        return .init(hue: tint.hue, saturation: min(tint.saturation * 1.2, 0.8), brightness: tint.brightness)
    }

    @concurrent
    static func load(url: URL) async -> PosterGradientColors? {
        var request = ImageRequest(url: url)
        request.thumbnail = ImageRequest.ThumbnailOptions(maxPixelSize: 96)
        guard let image = try? await ImagePipeline.shared.image(for: request), let cgImage = image.cgImage else {
            return nil
        }
        return colors(in: cgImage)
    }
}

/// Keeps worked-out colors for this launch, so a card scrolled back into view doesn't fade in again.
private enum PosterGradientCache {
    static var colors: [URL: PosterGradientColors] = [:]
}

struct PosterGradientBackground: View {
    let posterURL: URL?

    @State private var colors: PosterGradientColors?

    init(posterURL: URL?) {
        self.posterURL = posterURL
        _colors = State(initialValue: posterURL.flatMap { PosterGradientCache.colors[$0] })
    }

    var body: some View {
        ZStack {
            Rectangle()
                .fill(.white.opacity(0.06))

            if let colors {
                let lead = Color(hue: colors.lead.hue, saturation: colors.lead.saturation, brightness: colors.lead.brightness)
                let trail = Color(hue: colors.trail.hue, saturation: colors.trail.saturation, brightness: colors.trail.brightness)
                // Fades toward black in the second color, so the card ends like a film's closing shot, not on gray.
                let end = Color(hue: colors.trail.hue, saturation: colors.trail.saturation, brightness: colors.trail.brightness * 0.32)

                LinearGradient(
                    stops: [
                        .init(color: lead, location: 0.12),
                        .init(color: trail, location: 0.58),
                        .init(color: end, location: 1),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.3), value: colors)
        .task(id: posterURL) {
            guard colors == nil, let posterURL else { return }
            guard let loaded = await PosterGradientColors.load(url: posterURL) else { return }
            PosterGradientCache.colors[posterURL] = loaded
            colors = loaded
        }
        .accessibilityHidden(true)
    }
}
