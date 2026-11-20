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

                SearchRowMadeByLine(result: result, showsKind: showsKind)

                if standing != nil || friends != nil {
                    HStack(spacing: 8) {
                        standingView
                        friendsView
                    }
                    .padding(.top, 5)
                }
            }
            // The words take the row's width before the spacer does, so the line under the title is measured against
            // the whole column, not an even split with the spacer.
            .layoutPriority(1)

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
        if let line = SearchRowText.spokenLine(
            mediaType: result.ref.mediaType,
            makers: result.madeBy,
            year: SearchRowText.year(of: result),
            showsKind: showsKind
        ) {
            parts.append(line)
        }
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

/// The line under a result's title: the kind's icon when the search covers every kind, then who made it and the year
/// ("Denis Villeneuve · 2021"). The year is never cut: a name too long for the line ends in "…". With no maker known
/// it says the year, with no year the maker, and with neither the kind ("Game").
private struct SearchRowMadeByLine: View {
    let result: MediaSummary
    let showsKind: Bool

    var body: some View {
        let credits = SearchRowText.credits(result.madeBy)
        let year = SearchRowText.year(of: result)
        HStack(spacing: 5) {
            if showsKind {
                // The media picker's own icon, in a slot as wide as the widest, so every row's words start together.
                MediaTypeGlyph(theme: .theme(for: result.ref.mediaType), size: 10)
                    .saturation(0)
                    .opacity(0.6)
                    .frame(width: 16)
            }
            if credits.count > 1 {
                ViewThatFits(in: .horizontal) {
                    credit(credits[0], year: year)
                    credit(credits[1], year: year)
                }
            } else if let only = credits.first {
                credit(only, year: year)
            } else if let plain = year ?? TrendingTileModel.kind(of: result.ref.mediaType) {
                Text(plain)
                    .lineLimit(1)
            }
        }
        .font(.system(size: 13.5, weight: .semibold))
        .foregroundStyle(.white.opacity(0.6))
    }

    private func credit(_ credit: SearchRowText.Credit, year: String?) -> some View {
        HStack(spacing: 0) {
            Text(credit.name)
                .lineLimit(1)
                .truncationMode(.tail)
            if credit.more > 0 {
                Text(" +\(credit.more)")
                    .monospacedDigit()
                    .fixedSize()
            }
            if let year {
                Text(" · \(year)")
                    .fixedSize()
            }
        }
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
