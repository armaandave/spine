import SwiftUI

/// One post in the activity feed, the same size whatever it is: a log, a day's progress, or a start. The artwork opens
/// the row and the text beside it is as tall as the artwork, so a post with no review keeps its height and the like and
/// comment row lines up from post to post. A post's full review is on its page.
struct ActivityPostRow: View {
    private static let artwork = PosterSlot.activityPost
    private static let artworkSpacing: CGFloat = 12

    let activity: ActivityItem
    let onOpen: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: Self.artworkSpacing) {
            artwork
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                authorLine

                // One element to VoiceOver, which opens the post from it.
                VStack(alignment: .leading, spacing: 3) {
                    // A day of Planning adds reads as its count, with the names under it, not as its first title.
                    Text(activity.planning != nil ? PlanningWords.headline(for: activity) : activity.media?.displayTitle ?? "")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                    summaryLine
                    reviewLine
                    Spacer(minLength: 0)
                    // A day of Planning adds has no likes or comments to count.
                    if activity.planning == nil {
                        PostEngagementSummary(engagement: activity.engagement)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isButton)
                .accessibilityHint(ActivityFeedPresentation.destinationHint(for: activity))
                .accessibilityAction(.default, onOpen)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: Self.artwork.size.height)
        }
        .padding(.vertical, 14)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .overlay(alignment: .bottom) {
            // Starts under the text and runs past the page's margin to the screen's edge.
            DiarySeparator()
                .padding(.leading, Self.artwork.size.width + Self.artworkSpacing)
                .padding(.trailing, -14)
        }
    }

    /// The name gives way to the date, which is never cut off.
    private var authorLine: some View {
        HStack(spacing: 5) {
            PostAuthorLink(user: activity.actor, avatarSize: 20, spacing: 6, font: .system(size: 14, weight: .semibold))

            if let date = ActivityTimestampFormatter.text(for: activity) {
                Text(date)
                    .font(.system(size: 14))
                    .foregroundStyle(.white.opacity(0.5))
                    .fixedSize()
                    .accessibilityLabel(ActivityTimestampFormatter.accessibilityText(for: activity) ?? date)
            }
        }
    }

    /// Directly under the title: a log's stars and marks, or in words what the post is when it has none.
    @ViewBuilder
    private var summaryLine: some View {
        if activity.planning != nil {
            if let names = PlanningWords.names(for: activity) {
                detailText(names)
            }
        } else if let status = ActivityFeedPresentation.statusText(for: activity) {
            detailText(status)
        } else {
            LogRatingMarks(
                summary: ActivityFeedPresentation.ratingSummary(for: activity),
                isRepeat: ActivityFeedPresentation.isRewatch(activity),
                repeatLabel: activity.media?.ref.repeatLabel ?? "Rewatch"
            )
            .frame(height: 17)
        }
    }

    @ViewBuilder
    private var reviewLine: some View {
        if let review = ActivityFeedPresentation.reviewText(for: activity) {
            if activity.containsSpoilers == true {
                HStack(spacing: 5) {
                    Image(systemName: "eye.slash")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(.white.opacity(0.7))
                        .accessibilityHidden(true)
                    detailText("Contains spoilers")
                }
            } else {
                detailText(review)
            }
        }
    }

    private func detailText(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 14))
            .foregroundStyle(.white.opacity(0.7))
            .lineLimit(1)
    }

    @ViewBuilder
    private var artwork: some View {
        if let posters = PlanningWords.fanPosters(for: activity) {
            // A day of several Planning adds fans out its titles, as a series card does.
            PosterFan(
                urls: posters,
                title: PlanningWords.headline(for: activity),
                slot: Self.artwork,
                mediaType: activity.media?.ref.mediaType
            )
            .frame(width: Self.artwork.size.width, height: Self.artwork.size.height, alignment: .top)
        } else {
            singleArtwork
        }
    }

    private var singleArtwork: some View {
        MediaArtwork(
            url: activity.media?.displayPosterURL,
            title: activity.media?.title ?? "",
            slot: Self.artwork,
            mediaType: activity.media?.ref.mediaType,
            orientation: activity.media?.posterOrientation
        )
        // So a dark cover keeps its edge on the dark page.
        .overlay {
            RoundedRectangle(cornerRadius: Self.artwork.cornerRadius, style: .continuous)
                .strokeBorder(.white.opacity(0.1), lineWidth: 0.5)
        }
        // Square album art sits at the top of the same column.
        .frame(width: Self.artwork.size.width, height: Self.artwork.size.height, alignment: .top)
    }
}
