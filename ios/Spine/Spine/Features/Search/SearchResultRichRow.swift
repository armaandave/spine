import SwiftUI

/// A search result, or a title opened lately: the art, the title with what you typed set apart, what it is, and where
/// you and your friends stand on it.
struct SearchResultRichRow: View {
    let result: MediaSummary
    var query = ""
    /// The kind leads the line under the title when the search covers every kind.
    var showsKind = true

    /// The art column's width, so the list can start its separators where the text starts.
    static var artWidth: CGFloat { PosterSlot.libraryRow.size.width }

    private static let inProgressBlue = Color(red: 0.61, green: 0.86, blue: 1)
    /// Friends shown as faces; more than this adds "+N".
    private static let faces = 3

    var body: some View {
        HStack(spacing: 14) {
            MediaArtwork(
                url: result.displayPosterURL,
                title: result.title,
                slot: .libraryRow,
                mediaType: result.ref.mediaType,
                orientation: result.posterOrientation
            )
            .shadow(color: .black.opacity(0.4), radius: 7, y: 5)
            .frame(width: Self.artWidth)

            VStack(alignment: .leading, spacing: 3) {
                title
                    .font(.system(size: 17))
                    .lineLimit(2)

                if let meta {
                    meta
                        .font(.system(size: 13.5, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.6))
                        .lineLimit(1)
                }

                if standing != nil || friends != nil {
                    HStack(spacing: 8) {
                        standingView
                        friendsView
                    }
                    .padding(.top, 5)
                }
            }

            Spacer(minLength: 8)

            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
    }

    private var title: Text {
        let text = result.displayTitle
        guard let found = SearchRowText.match(in: text, query: query) else {
            return Text(text).fontWeight(.semibold).foregroundStyle(.white)
        }
        let quiet = Color.white.opacity(0.86)
        let before = Text(found.before).fontWeight(.semibold).foregroundStyle(quiet)
        let match = Text(found.match).fontWeight(.heavy).foregroundStyle(.white)
        let after = Text(found.after).fontWeight(.semibold).foregroundStyle(quiet)
        return Text("\(before)\(match)\(after)")
    }

    private var metaLine: String? {
        SearchRowText.meta(
            mediaType: result.ref.mediaType,
            subtitle: result.subtitle,
            releaseDate: result.releaseDate,
            showsKind: showsKind
        )
    }

    /// Explicit albums lead the line with Apple Music's "E", as Apple Music's own lists do.
    private var meta: Text? {
        guard MusicContentRating.isExplicit(result.contentRating) else { return metaLine.map { Text($0) } }
        return metaLine.map { Text("\(Text.explicitBadge) \($0)") } ?? Text.explicitBadge
    }

    private var standing: SearchRowText.Standing? {
        SearchRowText.standing(for: result.userState, mediaType: result.ref.mediaType)
    }

    private var friends: MediaFriendsSummary? {
        guard let friends = result.friends, friends.count > 0 else { return nil }
        return friends
    }

    @ViewBuilder
    private var standingView: some View {
        switch standing {
        case let .rated(stars, liked):
            LogRatingMarks(summary: DiaryEntrySummary(stars: stars, liked: liked, hasReview: false), isRepeat: false, repeatLabel: "")
                .standingPill()
        case let .status(word):
            Text(word)
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(["Watching", "Reading", "Playing", "Listening"].contains(word) ? Self.inProgressBlue : .white)
                .standingPill()
        case .liked:
            Image(systemName: "heart.fill")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(.pink)
                .standingPill()
        case nil:
            EmptyView()
        }
    }

    /// The faces of friends who've done something with it; their names are left to VoiceOver.
    @ViewBuilder
    private var friendsView: some View {
        if let friends {
            let shown = friends.people.prefix(Self.faces)
            HStack(spacing: 5) {
                HStack(spacing: -5) {
                    ForEach(shown) { person in
                        UserAvatar(urlString: person.avatarUrl, size: 20)
                            .overlay(Circle().stroke(.black, lineWidth: 1.5))
                    }
                }
                if friends.count > shown.count {
                    Text("+\(friends.count - shown.count)")
                        .font(.system(size: 12, weight: .semibold))
                        .monospacedDigit()
                        .foregroundStyle(.white.opacity(0.5))
                }
            }
        }
    }

    private var accessibilityText: String {
        var parts = [result.displayTitle]
        if let metaLine { parts.append(metaLine) }
        switch standing {
        case let .rated(stars, liked):
            parts.append("You rated it \(stars) stars" + (liked ? ", liked" : ""))
        case let .status(word):
            parts.append(word)
        case .liked:
            parts.append("Liked")
        case nil:
            break
        }
        if let friendsLine = friends.flatMap(SearchRowText.friendsLine) { parts.append("Friends: \(friendsLine)") }
        return parts.joined(separator: ", ")
    }
}

private extension View {
    /// The small capsule your standing sits in.
    func standingPill() -> some View {
        padding(.horizontal, 8)
            .frame(height: 22)
            .background(.white.opacity(0.08), in: Capsule())
    }
}
