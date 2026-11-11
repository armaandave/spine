import Nuke
import SwiftUI

extension HomeDesign.Metric {
    /// The Trending panel, in tile space (the tile is 410 tall open): the title's line box starts here, 28 tall.
    static let trendingTitleTop: CGFloat = 256
    static let trendingTitleHeight: CGFloat = 28
    static let trendingInfoGap: CGFloat = 6
    static let trendingInfoHeight: CGFloat = 30
    /// The rail's bottom edge is this far above the tile's.
    static let trendingRailBottom: CGFloat = 16
    static let trendingFaceSize: CGFloat = 26
    static let trendingFaceOverlap: CGFloat = 6
    static let trendingMaxFaces = TrendingTileDetail.maxFaces
}

extension HomeDesign.Text {
    static let trendingTitle = Font.system(size: 20, weight: .heavy)
    static let trendingInfo = Font.system(size: 13.5, weight: .bold)
}

/// The Trending tile's panel: the title and friends' faces, centered; and under them the media page's own Liquid Glass
/// rail with Open in the middle. The title changes in place letter by letter along the swipe (`SwipeTitle`); the faces
/// row and the rail are not keyed, so the faces cross-fade from one set to the next and never blank, and the rail's
/// glass never rebuilds while you browse and only its state slots change.
struct TrendingPanel: View {
    let title: TrendingTitle
    let detail: TrendingTileDetail
    let neighbors: [TrendingTitle]
    let store: TrendingRailStore?
    let actions: HomeTilesActions
    /// The carousel's state and every title's name in its order: the title block changes in place along the scroll.
    let state: HomeTilesState
    let titles: [String]

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorSchemeContrast) private var contrast
    /// Posters' colors counted on this device, by title key; the server's, or ones counted earlier, need no waiting.
    @State private var counted: [String: [PosterSwatch]] = [:]
    /// Titles whose poster couldn't be counted (none, or unreadable): their Open icon shows white instead of waiting.
    @State private var uncountable: Set<String> = []

    var body: some View {
        let entry = store?.entry(for: title.key)
        let pickerOpen = entry?.picker.isPresented == true
        ZStack(alignment: .top) {
            // Behind everything, so the rail and the faces keep their own taps: a tap (or a drag) anywhere else on the
            // tile closes the open stars without changing the rating. The band over the posters is left out so the
            // carousel still swipes and opens; swiping it closes the stars through the title changing.
            if pickerOpen {
                TrendingOutsideTapLayer(onOutsideTap: collapsePicker)
            }

            TrendingTitleBlock(
                state: state, titles: titles, detail: detail, media: title.media, entry: entry, isHidden: pickerOpen
            )
                .padding(.top, HomeDesign.Metric.trendingTitleTop)
                .frame(maxHeight: .infinity, alignment: .top)

            if let store {
                TrendingRailHost(
                    title: title,
                    entry: entry,
                    store: store,
                    actions: actions,
                    openTint: openTint,
                    isOpenTintPending: posterSwatches == nil && !uncountable.contains(title.key)
                )
                    .padding(.bottom, HomeDesign.Metric.trendingRailBottom)
                    .frame(maxHeight: .infinity, alignment: .bottom)
            }
        }
        .task(id: title.key) {
            store?.focus(title, neighbors: neighbors)
        }
        .task(id: avatarURLs) {
            // The faces of the titles on either side are decoded before you swipe to them.
            ArtworkPrefetch.prefetch(avatarURLs.compactMap {
                SpineImagePipeline.request(url: URL(string: $0), thumbnailSize: TileAvatar.thumbnailSize)
            })
        }
        .task(id: title.key) {
            await countPoster()
        }
        .onChange(of: title.key) { old, _ in
            store?.dismissPicker(for: old)
        }
        .onDisappear {
            // A tile that closes with the stars open opens next time without them.
            store?.dismissPicker(for: title.key)
        }
        .onChange(of: store?.pendingMessages ?? [], initial: true) { _, messages in
            guard !messages.isEmpty, let store else { return }
            for message in store.takeMessages() { actions.onRailMessage(message) }
        }
    }

    private func collapsePicker() {
        guard let store, store.entry(for: title.key)?.picker.isPresented == true else { return }
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84)) {
            store.dismissPicker(for: title.key)
        }
    }

    // MARK: Faces

    /// The faces' pictures for this title and the two beside it.
    private var avatarURLs: [String] {
        ([title] + neighbors).flatMap { $0.friends.prefix(TrendingTileDetail.maxFaces).compactMap(\.avatarUrl) }
    }

    // MARK: The Open icon's color

    private var posterURL: URL? {
        title.media.displayPosterURL.flatMap { URL(string: $0) }
    }

    /// The poster's colors, if known yet: the server's, or counted here or earlier.
    private var posterSwatches: [PosterSwatch]? {
        counted[title.key] ?? PosterPaletteStore.shared.known(server: title.media.posterPalette, url: posterURL)
    }

    /// The poster's color for the Open icon: its first color that reads on the dark glass, else its second, else white.
    private var openTint: Color? {
        TrendingOpenTint.color(from: posterSwatches, highContrast: contrast == .increased)
    }

    /// Counts the poster's colors when the server sent none and none were counted before.
    private func countPoster() async {
        let key = title.key
        let server = title.media.posterPalette
        let url = posterURL
        guard PosterPaletteStore.shared.known(server: server, url: url) == nil else { return }
        if let found = await PosterPaletteStore.shared.swatches(server: server, url: url) {
            counted[key] = found
        } else {
            uncountable.insert(key)
        }
    }
}

// MARK: - Outside taps

/// Two clear bands over the tile, above and below the posters, that close the open rating stars when tapped or dragged
/// on. The posters' own band is left alone.
private struct TrendingOutsideTapLayer: View {
    let onOutsideTap: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            band
                .frame(height: TileGeometry.top)
            Color.clear
                .frame(height: TileGeometry.posterSize.height)
                .allowsHitTesting(false)
            band
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .accessibilityHidden(true)
    }

    private var band: some View {
        Color.clear
            .contentShape(Rectangle())
            .onTapGesture(perform: onOutsideTap)
            .simultaneousGesture(DragGesture(minimumDistance: 10).onChanged { _ in onOutsideTap() })
    }
}

// MARK: - The title block

/// The centered title and, under it, the friends' faces (or, with none, the title's kind and year). The title is drawn
/// by the shared `SwipeTitle`, which follows the scroll and changes in place; the row under it is one view that stays,
/// so it is never empty between two titles.
private struct TrendingTitleBlock: View {
    let state: HomeTilesState
    let titles: [String]
    let detail: TrendingTileDetail
    let media: MediaSummary
    let entry: TrendingRailStore.Entry?
    let isHidden: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 0) {
            SwipeTitle(titles: titles, state: state, style: .trending)
                .frame(height: HomeDesign.Metric.trendingTitleHeight)
                .dynamicTypeSize(...HomeDesign.Text.maxCategory)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(detail.accessibilityLabel)
                .accessibilityAddTraits(.isHeader)
                .allowsHitTesting(false)

            TrendingFacesRow(detail: detail, media: media, entry: entry)
                .padding(.top, HomeDesign.Metric.trendingInfoGap)
                .frame(height: HomeDesign.Metric.trendingInfoHeight)
                .opacity(isHidden ? 0 : 1)
                .animation(reduceMotion ? nil : .easeOut(duration: 0.25), value: isHidden)
                .allowsHitTesting(!isHidden)
                .accessibilityHidden(isHidden)
        }
    }
}

/// The friends' faces, each opening that person's profile. When the title changes the old faces stay until the new ones
/// fade in over them (no gap). A title nobody you follow has logged shows its kind and year instead ("Movie · 2025"),
/// once its page has loaded; it reads the rail's entry here, so only this row redraws when the page arrives.
private struct TrendingFacesRow: View {
    let detail: TrendingTileDetail
    let media: MediaSummary
    let entry: TrendingRailStore.Entry?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Quieter than the title above it.
    private static let infoInk = Color.white.opacity(0.6)

    var body: some View {
        let info = detail.avatars.isEmpty
            ? TrendingTileModel.info(
                for: media,
                detail: entry?.viewModel.detail,
                loading: entry.map { !$0.isReady && $0.phase != .failed } ?? true
            )
            : nil
        ZStack {
            if !detail.avatars.isEmpty {
                faces
                    .id(detail.facesKey)
                    .transition(.opacity)
            } else if let info {
                Text(info)
                    .font(HomeDesign.Text.quiet)
                    .foregroundStyle(Self.infoInk)
                    .lineLimit(1)
                    .id(info)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.3), value: detail.facesKey)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.3), value: info)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
    }

    private var faces: some View {
        HStack(spacing: -HomeDesign.Metric.trendingFaceOverlap) {
            ForEach(Array(detail.avatars.enumerated()), id: \.offset) { index, avatar in
                NavigationLink {
                    UserProfileDestination(username: avatar.username)
                } label: {
                    TileAvatar(avatar: avatar, size: HomeDesign.Metric.trendingFaceSize)
                        .padding(.vertical, 9)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.vertical, -9)
                .accessibilityLabel(avatar.name)
                .accessibilityHint("Opens profile")
                .accessibilityIdentifier("trending.face.\(index)")
            }
            if detail.moreCount > 0 {
                TileMoreChip(count: detail.moreCount, size: HomeDesign.Metric.trendingFaceSize)
            }
        }
        .accessibilityElement(children: .contain)
    }
}

// MARK: - Faces

/// A person's picture, decoded at its size, with a dark ring so overlapping faces read apart. Without a picture, their
/// initial on a pastel that stays the same for the same letter.
struct TileAvatar: View {
    /// The faces' decode size, which the panel's prefetch uses too so a prefetched face is the one cached.
    static let thumbnailSize = CGSize(width: HomeDesign.Metric.trendingFaceSize, height: HomeDesign.Metric.trendingFaceSize)

    let avatar: TrendingTileDetail.Avatar
    let size: CGFloat

    var body: some View {
        SpineAsyncImage(
            url: avatar.url.flatMap { URL(string: $0) },
            thumbnailSize: CGSize(width: size, height: size)
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    Color(hue: Self.hue(for: avatar.initial), saturation: 0.36, brightness: 0.97)
                    Text(avatar.initial)
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(HomeDesign.Palette.avatarInk)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .background {
            Circle()
                .fill(TrendingFaceStyle.ring)
                .padding(-2)
        }
        .accessibilityHidden(true)
    }

    nonisolated static func hue(for initial: String) -> Double {
        let value = initial.unicodeScalars.first.map { Int($0.value) } ?? 0
        return Double((value &* 37) % 360) / 360
    }
}

/// "+2": the friends past the faces shown.
private struct TileMoreChip: View {
    let count: Int
    let size: CGFloat

    var body: some View {
        Text("+\(count)")
            .font(.system(size: 10.5, weight: .heavy))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Circle().fill(TrendingFaceStyle.chipFill))
            .overlay(Circle().strokeBorder(Color.white.opacity(0.3), lineWidth: 0.5))
            .background {
                Circle()
                    .fill(TrendingFaceStyle.ring)
                    .padding(-2)
            }
            .accessibilityHidden(true)
    }
}

private enum TrendingFaceStyle {
    /// The 2 pt ring round each face, so a face over a face reads apart.
    static let ring = Color(white: 10.0 / 255).opacity(0.55)
    static let chipFill = Color(red: 70.0 / 255, green: 70.0 / 255, blue: 76.0 / 255).opacity(0.9)
}
