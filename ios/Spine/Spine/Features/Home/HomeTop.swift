import SwiftUI

/// The first item of Home's lazy stack, one plain view: the header, the two tiles, the Up next tile, the "Friends" row
/// and the states that stand in for the feed (loading, error, empty). The feed's own rows follow it as separate items.
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
    @StableInput var onOpenUpNext: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let phase = Phase.resolve(hasProfile: viewModel.profile != nil, error: viewModel.profileErrorMessage)
        VStack(alignment: .leading, spacing: 0) {
            // It sits above the tiles, on the picture's soft lower part, and squeezes up to the top of the page as a
            // tile opens, with the tile; its left stays where it is.
            HomeHeaderSqueeze(state: viewModel.tilesState, glow: viewModel.glowModel) {
                HomeTopHeader(viewModel: viewModel, onOpenProfile: onOpenProfile, onOpenTitle: actions.onOpenTitle)
            }

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
            case .content:
                // From the first frame: each part shows its own loading look until its data is in, so nothing is
                // swapped for a copy of itself when the profile arrives.
                Group {
                    HomeTilesSection(
                        model: viewModel.tiles,
                        state: viewModel.tilesState,
                        glow: viewModel.glowModel,
                        actions: actions
                    )
                    .padding(.top, HomeTopSpec.headerToTiles)
                    // Full width under the two tiles, once something is coming up.
                    HomeUpNextTile(
                        model: viewModel.upNext,
                        tiles: viewModel.tilesState,
                        glow: viewModel.glowModel,
                        onOpen: onOpenUpNext
                    )
                    friendsRow
                    HomeFeedStatus(viewModel: viewModel, onFindPeople: onFindPeople)
                }
                .transition(.opacity)
            }
        }
        .padding(.horizontal, HomeDesign.Metric.pageMargin)
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: phase)
    }

    /// "Friends" and, beside it, the faces of the people you follow, as tall as its capital letters.
    private var friendsRow: some View {
        HStack(spacing: HomeFriendsSpec.headingGap) {
            Text("Friends")
                .font(HomeTopSpec.friendsFont)
                .tracking(HomeTopSpec.friendsTracking)
                .foregroundStyle(.white)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .dynamicTypeSize(...HomeDesign.Text.maxCategory)
                .accessibilityAddTraits(.isHeader)
            HomeFriendsStrip(model: viewModel.friends)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, HomeTopSpec.tilesToFriends)
        // The first card sits as far under the heading as the next card sits under it. The first card, or the
        // skeleton or message that stands in for it, starts right under this.
        .padding(.bottom, HomeTopSpec.friendsToFeed)
    }

    /// What the page top shows: the profile's failure, or the page (loading or loaded).
    enum Phase: Equatable {
        case failed(String)
        case content

        static func resolve(hasProfile: Bool, error: String?) -> Phase {
            if let error, !hasProfile { return .failed(error) }
            return .content
        }
    }
}

/// The numbers of the page top that the design's tokens do not hold: they come from the Home mock.
private enum HomeTopSpec {
    static let avatar: CGFloat = 36
    /// Below the status bar while a tile is open. The mock's 64 less a 47 pt safe area.
    static let squeezedTop: CGFloat = 17
    /// How small the header gets while a tile is open.
    static let squeezedScale: CGFloat = 0.7
    static let headerToTiles: CGFloat = 22
    /// What the header adds under itself at rest, so the tiles start `HomeTopArtSpec.headerToTiles` below its row.
    static let restExtraGap: CGFloat = HomeTopArtSpec.headerToTiles - headerToTiles
    static let tilesToFriends: CGFloat = 30
    /// Under the Friends row, to the first card: the same room as under each card (the gap between cards).
    static let friendsToFeed: CGFloat = HomeDesign.Metric.cardGap
    /// Four 115 pt cards fill about the height three of the old 164 pt ones did.
    static let skeletonCards = 4
    static let dateFont = Font.system(size: 13, weight: .semibold)
    static let titleFont = Font.system(size: 30, weight: .heavy)
    static let titleTracking: CGFloat = -0.5
    /// The title's glow, in the title's color: a tight layer and a wide one.
    static let titleGlowTightOpacity: Double = 0.45
    static let titleGlowWideOpacity: Double = 0.3
    /// The date's glow, the same strength, its reach scaled to the smaller text.
    static let dateGlowTightRadius: CGFloat = 3
    static let dateGlowWideRadius: CGFloat = 8
    /// The museum-style label over the profile photo, naming the picture.
    static let labelFont = Font.system(size: 11, weight: .semibold)
    static let labelTracking: CGFloat = 0.4
    static let labelOpacity: Double = 0.7
    /// The label's longest line, as a share of the page's width.
    static let labelMaxWidthShare: CGFloat = 0.55
    static let labelToAvatar: CGFloat = 4
    static let friendsFont = Font.system(size: HomeFriendsSpec.headingSize, weight: .heavy)
    static let friendsTracking: CGFloat = -0.4
    /// The mock's placeholder for an avatar with no picture.
    static let avatarDisc = Color(red: 0xED / 255, green: 0xED / 255, blue: 0xED / 255)
}

// MARK: - Header

/// The day's date over "Home" on the left; on the right the profile photo with, right above it, a small label naming
/// the picture ("Title · Year"), which opens that title's page. It reads the profile, the picture and whether a tile is
/// open, and nothing else.
private struct HomeTopHeader: View {
    let viewModel: HomeViewModel
    let onOpenProfile: () -> Void
    let onOpenTitle: (MediaRef) -> Void

    /// The date line is worked out when the header appears and again when the day changes under it.
    @State private var today = Date()
    /// The title's and the date's colors from the picture; nil (as before, no glow) until the picture's colors are known.
    @State private var tints = HeaderTints()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        // The label goes while a tile is open: the picture it names has faded out.
        let labelHidden = viewModel.tilesState.open != nil
        HStack(alignment: .bottom, spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                Text(today.formatted(.dateTime.weekday(.wide).month(.abbreviated).day()))
                    .font(HomeTopSpec.dateFont)
                    .foregroundStyle(tints.date?.color ?? .white.opacity(0.84))
                    // The same glow as the title's, in the date's own color (the picture's second one).
                    .shadow(
                        color: (tints.date?.color ?? .white).opacity(HomeTopSpec.titleGlowTightOpacity),
                        radius: HomeTopSpec.dateGlowTightRadius
                    )
                    .shadow(
                        color: (tints.date?.color ?? .white).opacity(HomeTopSpec.titleGlowWideOpacity),
                        radius: HomeTopSpec.dateGlowWideRadius
                    )
                    .lineLimit(1)
                    .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: tints)
                Text("Home")
                    .font(HomeTopSpec.titleFont)
                    .tracking(HomeTopSpec.titleTracking)
                    .foregroundStyle(tints.title?.color ?? .white)
                    // A soft glow in the same color: a tight one and a wide one, faint. White while the title is white.
                    .shadow(color: (tints.title?.color ?? .white).opacity(HomeTopSpec.titleGlowTightOpacity), radius: 6)
                    .shadow(color: (tints.title?.color ?? .white).opacity(HomeTopSpec.titleGlowWideOpacity), radius: 16)
                    .lineLimit(1)
                    .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: tints)
            }
            .dynamicTypeSize(...HomeDesign.Text.maxCategory)

            Spacer(minLength: 0)

            VStack(alignment: .trailing, spacing: HomeTopSpec.labelToAvatar) {
                if let art = viewModel.topArt.art {
                    Button {
                        onOpenTitle(art.ref)
                    } label: {
                        Text(art.label)
                            .font(HomeTopSpec.labelFont)
                            .tracking(HomeTopSpec.labelTracking)
                            .foregroundStyle(.white.opacity(HomeTopSpec.labelOpacity))
                            .shadow(color: .black.opacity(0.55), radius: 3, y: 1)
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .multilineTextAlignment(.trailing)
                            .frame(maxWidth: viewModel.glowModel.screenWidth * HomeTopSpec.labelMaxWidthShare, alignment: .trailing)
                            .dynamicTypeSize(...HomeDesign.Text.maxCategory)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Opens \(art.title)")
                    .accessibilityAddTraits(.isButton)
                    .opacity(labelHidden ? 0 : 1)
                    .allowsHitTesting(!labelHidden)
                    .accessibilityHidden(labelHidden)
                }
                Button(action: onOpenProfile) {
                    HomeAvatar(profile: viewModel.profile, size: HomeTopSpec.avatar)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Profile")
            }
        }
        .frame(minHeight: HomeTopArtSpec.headerRowHeight, alignment: .bottom)
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.significantTimeChangeNotification)) { _ in
            today = Date()
        }
        // Restarts when the picture changes (and drops the old count). The colors are counted once per picture, off
        // the main thread; the title is white until the new color is known, then fades to it.
        .task(id: tintSource) {
            await updateTitleTint(for: tintSource)
        }
    }

    /// Where the header's colors come from: the poster a tile's carousel is on while one is open, counted the same way
    /// as the picture and checked against the glow behind the header; else the picture.
    private var tintSource: TintSource? {
        let glow = viewModel.glowModel
        if let poster = glow.focused?.displayPosterURL, let url = URL(string: poster) {
            return TintSource(colors: url, glow: glow.activeURL)
        }
        return (viewModel.topArt.art?.backdrop).map { TintSource(colors: $0, glow: nil) }
    }

    private func updateTitleTint(for source: TintSource?) async {
        guard let source else {
            tints = HeaderTints()
            return
        }
        let store = PosterPaletteStore.homePicture
        let glowStore = PosterPaletteStore.homeGlow
        // Already counted: the colors are set at once (they still fade in from white after the first frame).
        if let known = store.known(server: nil, url: source.colors),
           source.glow == nil || glowStore.known(server: nil, url: source.glow) != nil {
            tints = HeaderTints(swatches: known, glow: glowStore.known(server: nil, url: source.glow))
            return
        }
        // Not counted yet: the old picture's colors don't belong on this one, so the header goes back to white first.
        tints = HeaderTints()
        async let counted = store.swatches(server: nil, url: source.colors)
        async let glow = glowStore.swatches(server: nil, url: source.glow)
        let (swatches, glowSwatches) = await (counted, glow)
        guard !Task.isCancelled else { return }
        tints = swatches.map { HeaderTints(swatches: $0, glow: glowSwatches) } ?? HeaderTints()
    }
}

/// The image the header's colors are counted from, and the glow image behind the header they must stand out from
/// (nil at rest, where the picture is darkened under the header).
private struct TintSource: Hashable {
    let colors: URL
    let glow: URL?
}

/// The header's colors from the picture: the title takes its most common real color, the date the next one.
private struct HeaderTints: Equatable {
    var title: MeterTint.RGB?
    var date: MeterTint.RGB?

    init() {}

    init(swatches: [PosterSwatch], glow: [PosterSwatch]? = nil) {
        title = HomeTitleTint.color(from: swatches, against: glow)
        date = HomeTitleTint.dateColor(from: swatches, against: glow)
    }
}

/// Places the header and shrinks it while a tile is open. At rest its row starts `HomeTopArtSpec.referenceHeaderTop`
/// down the screen, above the tiles. Open, it goes to the top of the page (`HomeTopSpec.squeezedTop`) at about 0.7,
/// anchored at its top-leading corner so its left stays put, and its layout height shrinks with it so the open tile
/// rides up into the room. All of it runs under the tile's own curve, started by the same change that opens or closes
/// the tile, and nothing here runs while the page scrolls.
private struct HomeHeaderSqueeze<Content: View>: View {
    let state: HomeTilesState
    let glow: HomeGlowModel
    @ViewBuilder let content: () -> Content

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let squeezed = state.open != nil
        let scale = squeezed ? HomeTopSpec.squeezedScale : 1
        // The page's content starts below the status bar, so the row's place on screen less that inset.
        let restTop = HomeTopArtSpec.headerRowTop(width: glow.screenWidth)
            - (glow.contentTopInset ?? GlowSpec.estimatedTopInset)
        SqueezeLayout(scale: scale) {
            content().scaleEffect(scale, anchor: .topLeading)
        }
        .padding(.top, squeezed ? HomeTopSpec.squeezedTop : max(HomeTopSpec.squeezedTop, restTop))
        .padding(.bottom, squeezed ? 0 : HomeTopSpec.restExtraGap)
        .animation(TileMotion.frame(closing: !squeezed, reduceMotion: reduceMotion), value: squeezed)
    }
}

/// Lays its one child out `1 / scale` times as wide as it is offered, at its natural height, and is `scale` times that
/// tall. The child's own `scaleEffect` shrinks what is drawn to match, so the squeezed row still spans the full width:
/// the photo stays at the right edge instead of drifting in with the scale. `scale` is animatable, so both ease.
private struct SqueezeLayout: Layout {
    var scale: CGFloat

    var animatableData: CGFloat {
        get { scale }
        set { scale = newValue }
    }

    /// The width the child is laid out at for an offered `width`.
    private func childWidth(_ width: CGFloat?) -> CGFloat? {
        width.map { $0 / max(scale, 0.01) }
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        let size = child.sizeThatFits(ProposedViewSize(width: childWidth(proposal.width), height: nil))
        return CGSize(width: proposal.width ?? size.width * scale, height: size.height * scale)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(
            at: bounds.origin,
            anchor: .topLeading,
            proposal: ProposedViewSize(width: childWidth(bounds.width), height: nil)
        )
    }
}

/// The signed-in person's picture (44 pt unless told otherwise), decoded at that size; their initial on a light disc without one.
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
                .transition(.opacity)
            case .followNobody:
                HomeInlineState(
                    title: "Follow people you know",
                    systemImage: "person.2",
                    message: "What they log and watch will appear here.",
                    actionTitle: "Find People",
                    action: onFindPeople
                )
                .transition(.opacity)
            case .quiet:
                HomeInlineState(
                    title: "No activity yet",
                    systemImage: "clock.arrow.circlepath",
                    message: "Logs and progress from people you follow will appear here."
                )
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
