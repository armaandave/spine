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

/// The Trending tile's panel: the title, the average and friends' faces, centered; and under them the media page's own
/// Liquid Glass rail with Open in the middle. The title block and the info row are keyed by the title and fade up on a
/// swipe; the rail is not, so its glass never rebuilds while you browse and only its state slots change.
struct TrendingPanel: View {
    let title: TrendingTitle
    let detail: TrendingTileDetail
    let neighbors: [TrendingTitle]
    let store: TrendingRailStore?
    let actions: HomeTilesActions

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let entry = store?.entry(for: title.key)
        let pickerOpen = entry?.picker.isPresented == true
        ZStack(alignment: .top) {
            ZStack(alignment: .top) {
                TrendingTitleBlock(
                    detail: detail,
                    isHidden: pickerOpen,
                    onOpenFaces: { actions.onOpenTrending(title) }
                )
                .id(title.key)
                .transition(.asymmetric(insertion: .offset(y: 6).combined(with: .opacity), removal: .identity))
            }
            .animation(HomeDesign.Motion.soft(duration: 0.4, reduceMotion: reduceMotion), value: title.key)
            .padding(.top, HomeDesign.Metric.trendingTitleTop)
            .frame(maxHeight: .infinity, alignment: .top)

            if let store {
                TrendingRailHost(title: title, entry: entry, store: store, actions: actions)
                    .padding(.bottom, HomeDesign.Metric.trendingRailBottom)
                    .frame(maxHeight: .infinity, alignment: .bottom)
            }
        }
        .task(id: title.key) {
            store?.focus(title, neighbors: neighbors)
        }
        .onChange(of: title.key) { old, _ in
            store?.dismissPicker(for: old)
        }
        .onChange(of: store?.pendingMessages ?? [], initial: true) { _, messages in
            guard !messages.isEmpty, let store else { return }
            for message in store.takeMessages() { actions.onRailMessage(message) }
        }
    }
}

// MARK: - The title block

/// The centered title and, under it, the info row. Everything here is display except the faces, which open the title.
private struct TrendingTitleBlock: View {
    let detail: TrendingTileDetail
    let isHidden: Bool
    let onOpenFaces: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 0) {
            Text(detail.title)
                .font(HomeDesign.Text.trendingTitle)
                .tracking(-0.3)
                .foregroundStyle(.primary)
                .lineLimit(1)
                .truncationMode(.tail)
                .padding(.horizontal, 16)
                .frame(maxWidth: .infinity)
                .frame(height: HomeDesign.Metric.trendingTitleHeight)
                .dynamicTypeSize(...HomeDesign.Text.maxCategory)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(detail.accessibilityLabel)
                .accessibilityAddTraits(.isHeader)
                .allowsHitTesting(false)

            TrendingInfoRow(detail: detail, onOpenFaces: onOpenFaces)
                .padding(.top, HomeDesign.Metric.trendingInfoGap)
                .frame(height: HomeDesign.Metric.trendingInfoHeight)
                .opacity(isHidden ? 0 : 1)
                .animation(reduceMotion ? nil : .easeOut(duration: 0.25), value: isHidden)
                .allowsHitTesting(!isHidden)
                .accessibilityHidden(isHidden)
        }
    }
}

/// The average, then the friends' faces; neither has a word beside it except "avg". A title nobody has logged and
/// nobody has rated says "Popular this week", so the row is never empty.
private struct TrendingInfoRow: View {
    let detail: TrendingTileDetail
    let onOpenFaces: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if let average = detail.averageText {
                HStack(spacing: 4) {
                    Text("\u{2605}")
                        .foregroundStyle(HomeDesign.Palette.starGold)
                    Text(average)
                        .foregroundStyle(.white)
                    Text("avg")
                        .fontWeight(.semibold)
                        .foregroundStyle(.white.opacity(0.7))
                }
                .font(HomeDesign.Text.trendingInfo)
                .lineLimit(1)
                .accessibilityHidden(true)
            }
            if !detail.avatars.isEmpty {
                Button(action: onOpenFaces) {
                    faces
                        .padding(.vertical, 9)
                        .padding(.horizontal, 12)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.vertical, -9)
                .padding(.horizontal, -6)
                .accessibilityLabel(detail.facesLabel)
                .accessibilityHint("Opens the title")
                .accessibilityIdentifier("trending.faces")
            }
            if detail.isPopularFallback {
                Text("Popular this week")
                    .font(HomeDesign.Text.trendingInfo)
                    .fontWeight(.semibold)
                    .foregroundStyle(.white.opacity(0.7))
                    .lineLimit(1)
                    .accessibilityHidden(true)
            }
        }
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
    }

    private var faces: some View {
        HStack(spacing: -HomeDesign.Metric.trendingFaceOverlap) {
            ForEach(Array(detail.avatars.enumerated()), id: \.offset) { _, avatar in
                TileAvatar(avatar: avatar, size: HomeDesign.Metric.trendingFaceSize)
            }
            if detail.moreCount > 0 {
                TileMoreChip(count: detail.moreCount, size: HomeDesign.Metric.trendingFaceSize)
            }
        }
        .accessibilityElement(children: .ignore)
    }
}

// MARK: - Faces

/// A person's picture, decoded at its size, with a dark ring so overlapping faces read apart. Without a picture, their
/// initial on a pastel that stays the same for the same letter.
struct TileAvatar: View {
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
