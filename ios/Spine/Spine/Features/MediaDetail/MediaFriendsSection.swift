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

/// The marks on a friend's card, in the order the card draws them: the stars as glyphs (whole and half, none empty),
/// then a heart, the rewatch arrow and the spoiler eye.
struct FriendCardMarks: Equatable {
    var stars: [FeedMark] = []
    var liked = false
    var rewatch = false
    var spoiler = false

    var isEmpty: Bool { stars.isEmpty && !liked && !rewatch && !spoiler }
}

/// What a card says under the name: their review, else their log date, else where they stand.
enum FriendCardLine: Equatable {
    case review(title: String?, text: String)
    /// "Logged Oct 14".
    case plain(String)
    /// Progress with no status, as the dim half of a status line: "5 of 10 episodes".
    case detail(String)
    /// The status word, then how far they are: "Watching · 5 of 10 episodes".
    case status(word: String, progress: String?)
}

extension MediaFriend {
    var cardMarks: FriendCardMarks {
        FriendCardMarks(
            stars: (rating.map { PostPageStyle.starSymbols(rating: $0) } ?? []).compactMap(FeedMark.init(starSymbol:)),
            liked: liked,
            rewatch: isRewatch,
            spoiler: hidesReview
        )
    }

    func cardLine(for ref: MediaRef) -> FriendCardLine? {
        if showsReview {
            return .review(title: reviewTitle.flatMap { $0.isEmpty ? nil : $0 }, text: review)
        }
        if let logLine { return .plain(logLine) }
        if let standing = standingLabel(for: ref) { return .status(word: standing, progress: progressLabel) }
        return progressLabel.map(FriendCardLine.detail)
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
    /// The poster the page shows: a soft wash of the title's color sits behind the rail.
    var posterURL: String?
    let friends: [MediaFriend]
    let onSeeAll: () -> Void
    let onSelect: (MediaFriend) -> Void

    @State private var tint: Color?

    init(
        ref: MediaRef,
        posterURL: String? = nil,
        friends: [MediaFriend],
        onSeeAll: @escaping () -> Void,
        onSelect: @escaping (MediaFriend) -> Void
    ) {
        self.ref = ref
        self.posterURL = posterURL
        self.friends = friends
        self.onSeeAll = onSeeAll
        self.onSelect = onSelect
        _tint = State(initialValue: MediaPageTint.known(posterURL))
    }

    /// From three friends the rail needs a swipe to see them all, so the header opens the full list.
    static func linksToFullList(count: Int) -> Bool { count >= 3 }

    var body: some View {
        if !friends.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                header

                GlassEffectContainer(spacing: 4) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(alignment: .top, spacing: 12) {
                            ForEach(friends) { friend in
                                MediaFriendCard(friend: friend, ref: ref, isCompact: true) {
                                    onSelect(friend)
                                }
                            }
                        }
                        .scrollTargetLayout()
                    }
                    .scrollTargetBehavior(.viewAligned)
                    .scrollClipDisabled()
                }
                .background { FriendsRailWash(tint: tint) }
            }
            // The page is always dark; light-mode glass would wash out the white text.
            .environment(\.colorScheme, .dark)
            .task(id: posterURL) {
                tint = MediaPageTint.known(posterURL)
                if tint == nil {
                    let loaded = await MediaPageTint.load(posterURL)
                    withAnimation(.easeInOut(duration: 0.4)) { tint = loaded }
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
                GlassEffectContainer(spacing: 4) {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let stats = friends.ratingStats {
                        SpineRatingDistributionSection(
                            community: stats,
                            reviewCount: 0,
                            posterURL: detail.displayPosterURL,
                            people: RatingChartPerson.people(viewerRating: nil, viewerAvatarURL: nil, friends: friends)
                        ) {}
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
                }
                .padding(.horizontal, 14)
                .padding(.top, 14)
                .padding(.bottom, 40)
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .toolbar(.hidden, for: .navigationBar)
        .pushDestination(item: $presentedLog) { log in
            DiaryLogDetailView(
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
        .pushDestination(item: $presentedFriend, onDismiss: onProfileClosed) { friend in
            UserProfileDestination(username: friend.username)
        }
        .navigationPage(.page)
    }

    /// As the reviews page's header draws it.
    private var header: some View {
        HStack(spacing: 12) {
            PageHeaderBackButton { dismiss() }

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

/// A very soft wash of the title's color behind the rail, for the glass to bend: three ellipses, extended 48 pt above
/// and below. Nothing when the poster gave no color. Kept quiet on purpose (a louder one was far too strong).
private struct FriendsRailWash: View {
    let tint: Color?

    var body: some View {
        if let tint {
            GeometryReader { geometry in
                // The board's box: the rail's width plus the page's 14 pt gutters, 240 tall from 48 above.
                let width = geometry.size.width + 28
                let height: CGFloat = 240
                ZStack {
                    blob(tint, radii: CGSize(width: 150, height: 96), at: CGPoint(x: width * 0.14, y: height * 0.50), alpha: 0.114)
                    blob(tint, radii: CGSize(width: 170, height: 110), at: CGPoint(x: width * 0.52, y: height * 0.42), alpha: 0.066)
                    blob(tint, radii: CGSize(width: 150, height: 100), at: CGPoint(x: width * 0.92, y: height * 0.58), alpha: 0.090)
                }
                .frame(width: width, height: height)
                .offset(x: -14, y: -48)
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }

    private func blob(_ tint: Color, radii: CGSize, at center: CGPoint, alpha: Double) -> some View {
        EllipticalGradient(
            colors: [tint.opacity(alpha), tint.opacity(0)],
            center: .center,
            startRadiusFraction: 0,
            endRadiusFraction: 0.5
        )
        .frame(width: radii.width * 2, height: radii.height * 2)
        .position(center)
    }
}

/// One glass card per friend: photo and name, their marks, then what they wrote, when they logged, or how far they are.
/// In the rail it is 244 by 120 with a short review; on the full list it is the page's width with all of it.
private struct MediaFriendCard: View {
    let friend: MediaFriend
    let ref: MediaRef
    let isCompact: Bool
    let onSelect: () -> Void

    private static let shape = RoundedRectangle(cornerRadius: 24, style: .continuous)

    var body: some View {
        let line = friend.cardLine(for: ref)
        Button(action: onSelect) {
            VStack(alignment: .leading, spacing: 12) {
                topRow
                if let line {
                    lineView(line)
                }
            }
            .padding(16)
            // No second block: the row centers in the card.
            .frame(width: isCompact ? 244 : nil, height: isCompact ? 120 : nil, alignment: line == nil ? .leading : .topLeading)
            .frame(maxWidth: isCompact ? nil : .infinity, minHeight: isCompact ? nil : 120, alignment: line == nil ? .leading : .topLeading)
            .glassEffect(.regular, in: Self.shape)
            .overlay(alignment: .top) { specular }
            .contentShape(Self.shape)
        }
        .buttonStyle(.plain)
        .accessibilityHint(friend.diaryEntryId == nil ? "Opens their profile" : "Opens their log")
    }

    /// The top edge's catch of light: white in the middle, clear at both ends, 20 pt in from the corners.
    private var specular: some View {
        Rectangle()
            .fill(
                LinearGradient(
                    stops: [
                        .init(color: .white.opacity(0), location: 0),
                        .init(color: .white.opacity(0.75), location: 0.5),
                        .init(color: .white.opacity(0), location: 1),
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .frame(height: 1)
            .padding(.horizontal, 20)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }

    private var topRow: some View {
        let marks = friend.cardMarks
        return HStack(spacing: 12) {
            UserAvatar(urlString: friend.user.avatarUrl, size: 40)
            VStack(alignment: .leading, spacing: 4) {
                Text(friend.user.displayName)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                if !marks.isEmpty {
                    marksRow(marks)
                }
            }
        }
        .frame(height: 40, alignment: .leading)
    }

    /// The stars as glyphs, then a heart, the rewatch arrow and the spoiler eye: the app's own glowing marks.
    private func marksRow(_ marks: FriendCardMarks) -> some View {
        HStack(spacing: 8) {
            if !marks.stars.isEmpty {
                HStack(spacing: 1.5) {
                    ForEach(Array(marks.stars.enumerated()), id: \.offset) { _, mark in
                        FeedMarkView(mark: mark, ink: .dark, size: 12)
                    }
                }
            }
            if marks.liked { FeedMarkView(mark: .heart, ink: .dark, size: 12) }
            if marks.rewatch { FeedMarkView(mark: .rewatch, ink: .dark, size: 12) }
            if marks.spoiler { FeedMarkView(mark: .spoiler, ink: .dark, size: 14) }
        }
        .frame(height: 16, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [
                friend.rating?.starCount.map { "Rated \($0) out of 5 stars" },
                marks.liked ? "Liked" : nil,
                marks.rewatch ? ref.repeatLabel : nil,
                marks.spoiler ? "Review contains spoilers" : nil,
            ]
            .compactMap { $0 }
            .joined(separator: ", ")
        )
    }

    @ViewBuilder
    private func lineView(_ line: FriendCardLine) -> some View {
        switch line {
        case let .review(title, text):
            // In the rail, two lines at most, title included; the whole review is on the full list and their log.
            VStack(alignment: .leading, spacing: 0) {
                if let title {
                    Text(title)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white.opacity(0.94))
                        .lineLimit(isCompact ? 1 : nil)
                        .frame(height: isCompact ? 18 : nil, alignment: .topLeading)
                }
                Text(text)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.84))
                    .lineLimit(isCompact ? (title == nil ? 2 : 1) : nil)
            }
            .lineSpacing(2)
            .fixedSize(horizontal: false, vertical: !isCompact)
            .frame(height: isCompact ? 36 : nil, alignment: .topLeading)
        case let .plain(text):
            Text(text)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.white.opacity(0.58))
                .lineLimit(1)
                .frame(height: isCompact ? 36 : nil, alignment: .topLeading)
        case let .detail(text):
            Text(text)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.white.opacity(0.72))
                .lineLimit(1)
                .frame(height: isCompact ? 36 : nil, alignment: .topLeading)
        case let .status(word, progress):
            HStack(spacing: 0) {
                Text(word)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.90))
                if let progress {
                    Text(" · \(progress)")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.white.opacity(0.72))
                }
            }
            .lineLimit(1)
            .frame(height: isCompact ? 36 : nil, alignment: .topLeading)
        }
    }
}
