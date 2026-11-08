import SwiftUI

/// The first item of Home's lazy stack, one plain view: the header, the two tiles, the "Friends" row and the states
/// that stand in for the feed (loading, error, empty). The feed's own rows follow it as separate items.
///
/// Each part reads what it needs inside its own body (`HomeTopHeader` the profile, `HomeTilesSection` the tiles,
/// `HomeFeedStatus` the feed's state), so a change in one redraws that part and not the others. Nothing here reads the
/// scroll offset.
struct HomeTop: View {
    let viewModel: HomeViewModel
    // Closures the page rebuilds every time it runs: they say nothing new (`StableInput`).
    @StableInput var actions: HomeTilesActions
    @StableInput var onOpenProfile: () -> Void
    @StableInput var onFindPeople: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let phase = Phase.resolve(
            hasProfile: viewModel.profile != nil,
            isLoading: viewModel.isLoading,
            error: viewModel.profileErrorMessage
        )
        VStack(alignment: .leading, spacing: 0) {
            HomeTopHeader(viewModel: viewModel, onOpenProfile: onOpenProfile)

            switch phase {
            case let .failed(message):
                HomeInlineState(
                    title: "Could not load home",
                    systemImage: "exclamationmark.triangle",
                    message: message,
                    actionTitle: "Retry"
                ) {
                    Task { await viewModel.reload() }
                }
                .frame(minHeight: 360, alignment: .top)
                .padding(.top, HomeTopSpec.headerToTiles)
                .transition(.opacity)
            case .loading:
                Group {
                    HomeTilesSkeletonHost()
                    friendsRow
                    VStack(spacing: HomeDesign.Metric.cardGap) {
                        ForEach(0 ..< HomeTopSpec.skeletonCards, id: \.self) { _ in
                            FeedCardSkeleton()
                        }
                    }
                    .padding(.top, HomeTopSpec.skeletonTop)
                }
                .transition(.opacity)
            case .content:
                Group {
                    HomeTilesSection(
                        model: viewModel.tiles,
                        state: viewModel.tilesState,
                        glow: viewModel.glowModel,
                        actions: actions
                    )
                    .padding(.top, HomeTopSpec.headerToTiles)
                    friendsRow
                    HomeFeedStatus(viewModel: viewModel, onFindPeople: onFindPeople)
                }
                .transition(.opacity)
            }
        }
        .padding(.horizontal, HomeDesign.Metric.pageMargin)
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: phase)
    }

    private var friendsRow: some View {
        Text("Friends")
            .font(HomeTopSpec.friendsFont)
            .tracking(HomeTopSpec.friendsTracking)
            .foregroundStyle(.white)
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, HomeTopSpec.tilesToFriends)
            .dynamicTypeSize(...HomeDesign.Text.maxCategory)
            .accessibilityAddTraits(.isHeader)
    }

    /// What the page top shows: the profile's failure, the first load, or the page.
    enum Phase: Equatable {
        case failed(String)
        case loading
        case content

        static func resolve(hasProfile: Bool, isLoading: Bool, error: String?) -> Phase {
            if let error, !hasProfile { return .failed(error) }
            if isLoading, !hasProfile { return .loading }
            return .content
        }
    }
}

/// The numbers of the page top that the design's tokens do not hold: they come from the Home mock.
private enum HomeTopSpec {
    static let avatar: CGFloat = 44
    static let headerGap: CGFloat = 12
    /// Below the status bar. The mock's 64 less a 47 pt safe area.
    static let headerTop: CGFloat = 17
    static let headerToTiles: CGFloat = 22
    static let tilesToFriends: CGFloat = 30
    static let skeletonTop: CGFloat = 14
    static let skeletonCards = 3
    static let dateFont = Font.system(size: 13, weight: .semibold)
    static let titleFont = Font.system(size: 32, weight: .heavy)
    static let titleTracking: CGFloat = -0.6
    static let friendsFont = Font.system(size: 22, weight: .heavy)
    static let friendsTracking: CGFloat = -0.4
    /// The mock's placeholder for an avatar with no picture.
    static let avatarDisc = Color(red: 0xED / 255, green: 0xED / 255, blue: 0xED / 255)
}

// MARK: - Header

/// The profile button, the day's date and "Home". It reads the profile and nothing else.
private struct HomeTopHeader: View {
    let viewModel: HomeViewModel
    let onOpenProfile: () -> Void

    /// The date line is worked out when the header appears and again when the day changes under it.
    @State private var today = Date()

    var body: some View {
        HStack(spacing: HomeTopSpec.headerGap) {
            Button(action: onOpenProfile) {
                HomeAvatar(profile: viewModel.profile, size: HomeTopSpec.avatar)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Profile")

            VStack(alignment: .leading, spacing: 0) {
                Text(today.formatted(.dateTime.weekday(.wide).month(.abbreviated).day()))
                    .font(HomeTopSpec.dateFont)
                    .foregroundStyle(.white.opacity(0.84))
                    .lineLimit(1)
                Text("Home")
                    .font(HomeTopSpec.titleFont)
                    .tracking(HomeTopSpec.titleTracking)
                    .foregroundStyle(.white)
                    .lineLimit(1)
            }
            .dynamicTypeSize(...HomeDesign.Text.maxCategory)

            Spacer(minLength: 0)
        }
        .padding(.top, HomeTopSpec.headerTop)
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.significantTimeChangeNotification)) { _ in
            today = Date()
        }
    }
}

/// The signed-in person's picture at 44 pt, decoded at that size; their initial on a light disc without one.
struct HomeAvatar: View {
    let profile: UserProfile?
    var size: CGFloat = 44

    var body: some View {
        SpineAsyncImage(
            url: URL(string: profile?.avatarUrl ?? ""),
            thumbnailSize: CGSize(width: size, height: size)
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    HomeTopSpec.avatarDisc
                    Text(FeedAvatar.initial(of: profile?.username ?? ""))
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(HomeDesign.Palette.avatarInk)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .shadow(color: .black.opacity(0.3), radius: 9, y: 6)
        .accessibilityHidden(true)
    }
}

// MARK: - Feed status

/// What stands in for the feed's rows when there are none: its skeleton, its error, or why it is empty. It reads the
/// feed's state, so the page top above it is not redrawn when the feed changes.
struct HomeFeedStatus: View {
    let viewModel: HomeViewModel
    let onFindPeople: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let feed = viewModel.feed
        let isEmpty = feed.items.isEmpty
        let state = Face.resolve(
            isLoading: feed.isLoadingInitial,
            isEmpty: isEmpty,
            error: feed.errorMessage,
            following: viewModel.profile?.counts.following ?? 0
        )
        Group {
            switch state {
            case .none:
                EmptyView()
            case .loading:
                VStack(spacing: HomeDesign.Metric.cardGap) {
                    ForEach(0 ..< HomeTopSpec.skeletonCards, id: \.self) { _ in
                        FeedCardSkeleton()
                    }
                }
                .padding(.top, HomeTopSpec.skeletonTop)
                .transition(.opacity)
            case let .failed(message):
                HomeInlineState(
                    title: "Could not load activity",
                    systemImage: "exclamationmark.triangle",
                    message: message,
                    actionTitle: "Retry"
                ) {
                    Task { await viewModel.loadActivity() }
                }
                .padding(.top, 8)
                .transition(.opacity)
            case .followNobody:
                HomeInlineState(
                    title: "Follow people you know",
                    systemImage: "person.2",
                    message: "What they log and watch will appear here.",
                    actionTitle: "Find People",
                    action: onFindPeople
                )
                .padding(.top, 8)
                .transition(.opacity)
            case .quiet:
                HomeInlineState(
                    title: "No activity yet",
                    systemImage: "clock.arrow.circlepath",
                    message: "Logs and progress from people you follow will appear here."
                )
                .padding(.top, 8)
                .transition(.opacity)
            }
        }
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: state)
    }

    enum Face: Equatable {
        /// There are rows, which draw themselves.
        case none
        case loading
        case failed(String)
        case followNobody
        case quiet

        static func resolve(isLoading: Bool, isEmpty: Bool, error: String?, following: Int) -> Face {
            guard isEmpty else { return .none }
            if isLoading { return .loading }
            if let error { return .failed(error) }
            return following == 0 ? .followNobody : .quiet
        }
    }
}

// MARK: - Pieces

/// The two tiles as placeholders for the first moments of Home. It measures its own width once.
private struct HomeTilesSkeletonHost: View {
    @State private var contentWidth = TileGeometry.referenceContentWidth

    var body: some View {
        HomeTilesSkeleton(contentWidth: contentWidth)
            .padding(.top, HomeTopSpec.headerToTiles)
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.width
            } action: { width in
                if width > 0 { contentWidth = width }
            }
    }
}

struct HomeInlineState: View {
    let title: String
    let systemImage: String
    let message: String
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white.opacity(0.56))
                .frame(width: 30)

            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(.white)

                Text(message)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.52))

                if let actionTitle, let action {
                    Button(actionTitle, action: action)
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.white.opacity(0.88))
                        .padding(.top, 2)
                }
            }

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
    }
}
