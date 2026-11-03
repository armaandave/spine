import SwiftUI

extension MediaFriend {
    /// Words to read on the card. A spoiler's words wait behind its log.
    var showsReview: Bool { !review.isEmpty && !containsSpoilers }
    var hidesReview: Bool { !review.isEmpty && containsSpoilers }

    /// Where they stand when there's no log to show, in the words the title's own page uses.
    func standingLabel(for ref: MediaRef) -> String? {
        guard diaryEntryId == nil, let status else { return nil }
        switch (status, ref.mediaType) {
        case ("In progress", "book"), ("In progress", "manga"), ("In progress", "comic"), ("In progress", "game"),
             ("In progress", "music"):
            return ref.trackingStatusLabel(status)
        case ("In progress", _):
            return "Watching"
        case ("Completed", "season"), ("Completed", "episode"):
            return "Watched"
        default:
            return ref.trackingStatusLabel(status)
        }
    }

    /// What a log says with no review to read: "Logged Oct 14".
    var logLine: String? {
        guard diaryEntryId != nil, !showsReview else { return nil }
        guard let date = ActivityTimestampFormatter.logDateText(from: consumedAt) else { return "Logged" }
        return "Logged \(["Today", "Yesterday"].contains(date) ? date.lowercased() : date)"
    }
}

extension [MediaFriend] {
    /// Their ratings in the shape the ratings chart reads, or nil when none of them rated.
    var ratingStats: CommunityStats? {
        let ratings = compactMap(\.rating)
        let values = ratings.compactMap(Double.init)
        guard !values.isEmpty else { return nil }
        return CommunityStats(
            averageRating: String(format: "%.2f", values.reduce(0, +) / Double(values.count)),
            ratingCount: values.count,
            diaryCount: 0,
            reviewCount: 0,
            likedCount: 0,
            ratingDistribution: Dictionary(grouping: ratings) { $0 }.map { rating, same in
                RatingDistributionBucket(rating: rating, count: same.count)
            }
        )
    }
}

struct MediaFriendsSection: View {
    let ref: MediaRef
    let friends: [MediaFriend]
    let onSeeAll: () -> Void
    let onSelect: (MediaFriend) -> Void

    /// From three friends the rail needs a swipe to see them all, so the header opens the full list.
    static func linksToFullList(count: Int) -> Bool { count >= 3 }

    var body: some View {
        if !friends.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                header

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(friends) { friend in
                            MediaFriendCard(friend: friend, ref: ref, isCompact: true) {
                                onSelect(friend)
                            }
                        }
                    }
                    // Every card takes the height of the tallest.
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private var header: some View {
        let count = friends.count
        let title = "Friends (\(count.formatted()))"
        if Self.linksToFullList(count: count) {
            Button(action: onSeeAll) {
                HStack(spacing: 6) {
                    SectionLabel(title: title)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .heavy))
                        .foregroundStyle(.white.opacity(0.42))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // A bigger tap target than the label, without moving the section.
            .padding(.vertical, -10)
            .accessibilityLabel("See all \(count.formatted()) friends")
        } else {
            SectionLabel(title: title)
        }
    }
}

/// Every friend on one page, under the chart of their ratings.
struct MediaFriendsListView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var presentedLog: PresentedDiaryEntry?
    @State private var presentedFriend: UserSummary?

    let detail: MediaDetail
    let friends: [MediaFriend]
    let diaryRepository: DiaryRepository
    let mediaRepository: MediaRepository
    let trackingRepository: TrackingRepository
    let currentUserId: Int?
    let selectedTab: AppTab
    let onSelectTab: (AppTab) -> Void
    let onUnauthorized: () -> Void
    let onProfileClosed: () -> Void

    var body: some View {
        ZStack {
            SpinePageBackground()
            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if let stats = friends.ratingStats {
                        SpineRatingDistributionSection(community: stats, reviewCount: 0) {}
                            .padding(.bottom, 8)
                    }
                    ForEach(friends) { friend in
                        MediaFriendCard(friend: friend, ref: detail.ref, isCompact: false) {
                            if let entryId = friend.diaryEntryId {
                                presentedLog = PresentedDiaryEntry(id: entryId)
                            } else {
                                presentedFriend = friend.user
                            }
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.top, 14)
                .padding(.bottom, 40)
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .fullScreenCover(item: $presentedLog) { log in
            DiaryLogDetailNavigationCover(
                entryId: log.id,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedFriend, onDismiss: onProfileClosed) { friend in
            NavigationStack {
                UserProfileDestination(username: friend.username)
            }
        }
    }

    /// As the reviews page's header draws it.
    private var header: some View {
        HStack(spacing: 12) {
            poster
                .frame(width: 40, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text("\(friends.count.formatted()) \(friends.count == 1 ? "friend" : "friends") on…")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
                Text(detail.displayTitle)
                    .font(.system(size: 19, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(.white.opacity(0.06), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close friends")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.08))
                .frame(height: 1)
        }
    }

    private var poster: some View {
        SpineAsyncImage(url: detail.displayPosterURL.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                Color.white.opacity(0.08)
            }
        }
        .accessibilityHidden(true)
    }
}

/// One layout for everyone: name, then their rating and status on the line below it, then what they wrote or how far
/// they got. In the rail it's a set width with a short review; on the full list it's the page's width with all of it.
private struct MediaFriendCard: View {
    let friend: MediaFriend
    let ref: MediaRef
    let isCompact: Bool
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            VStack(alignment: .leading, spacing: 9) {
                HStack(spacing: 8) {
                    UserAvatar(urlString: friend.user.avatarUrl, size: 34)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(friend.user.displayName)
                            .font(.system(size: 14, weight: .bold))
                            .foregroundStyle(.white)
                            .lineLimit(1)
                        // A set height, so the name stays put when this line is empty.
                        ratingLine
                            .frame(height: 16, alignment: .leading)
                    }
                }

                details
            }
            .padding(12)
            .frame(width: isCompact ? 236 : nil, alignment: .leading)
            .frame(maxWidth: isCompact ? nil : .infinity, alignment: .leading)
            .frame(maxHeight: .infinity, alignment: .topLeading)
            .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
            .contentShape(RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .accessibilityHint(friend.diaryEntryId == nil ? "Opens their profile" : "Opens their log")
    }

    /// "3.5★ ♥ ↻", as the page's own tracking line draws it, then their status when they have no log.
    private var ratingLine: some View {
        let stars = friend.rating?.starCount
        let standing = friend.standingLabel(for: ref)
        let hasMarks = stars != nil || friend.liked || friend.isRewatch || friend.hidesReview
        return HStack(spacing: 6) {
            if let stars {
                HStack(spacing: 2) {
                    Text(stars)
                    Image(systemName: "star.fill")
                }
                .foregroundStyle(.yellow)
            }
            if friend.liked {
                Image(systemName: "heart.fill")
                    .foregroundStyle(.pink)
            }
            if friend.isRewatch {
                Image(systemName: "arrow.clockwise")
                    .foregroundStyle(.white.opacity(0.62))
            }
            if friend.hidesReview {
                Image(systemName: "eye.slash.fill")
                    .foregroundStyle(.white.opacity(0.62))
            }
            if let standing {
                if hasMarks {
                    Text("·")
                        .foregroundStyle(.white.opacity(0.62))
                }
                Text(standing)
                    .foregroundStyle(.white.opacity(0.76))
            }
        }
        .font(.system(size: 13, weight: .bold))
        .lineLimit(1)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [
                stars.map { "Rated \($0) out of 5 stars" },
                friend.liked ? "Liked" : nil,
                friend.isRewatch ? ref.repeatLabel : nil,
                friend.hidesReview ? "Review contains spoilers" : nil,
                standing,
            ]
            .compactMap { $0 }
            .joined(separator: ", ")
        )
    }

    @ViewBuilder
    private var details: some View {
        if friend.showsReview {
            // In the rail, two lines at most, title included; the whole review is on the full list and their log.
            let title = friend.reviewTitle ?? ""
            VStack(alignment: .leading, spacing: 4) {
                if !title.isEmpty {
                    Text(title)
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(isCompact ? 1 : nil)
                }
                Text(friend.review)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.84))
                    .lineSpacing(2)
                    .lineLimit(isCompact ? (title.isEmpty ? 2 : 1) : nil)
                    .fixedSize(horizontal: false, vertical: !isCompact)
            }
        } else if let detail = friend.logLine ?? friend.progressLabel {
            Text(detail)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white.opacity(0.76))
                .lineLimit(1)
        }
    }
}
