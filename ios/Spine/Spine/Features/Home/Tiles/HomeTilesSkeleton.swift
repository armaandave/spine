import SwiftUI

/// The fan of a tile that is still loading: three faint poster shapes where the posters will be. It is the closed
/// tile's fan, drawn flat, so nothing jumps when the real posters arrive.
struct TileSkeletonFace: View {
    let contentWidth: CGFloat

    var body: some View {
        let side = TileGeometry.tileSide(contentWidth: contentWidth)
        ZStack(alignment: .topLeading) {
            ForEach((0 ..< TileGeometry.fanMountedCount).reversed(), id: \.self) { index in
                let fan = HomeDesign.Metric.fan(index: index, tileSide: side)
                RoundedRectangle(cornerRadius: HomeDesign.Metric.posterRadius, style: .continuous)
                    .fill(Color.white.opacity(0.05 + 0.07 * fan.brightness))
                    .frame(width: fan.width, height: fan.height)
                    .rotationEffect(.degrees(fan.degrees))
                    .offset(x: fan.x, y: fan.y)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Both tiles as quiet placeholders, for the first moments of Home before any of their data exists. Same size and
/// corners as the tiles, so the page does not move when they arrive.
struct HomeTilesSkeleton: View {
    let contentWidth: CGFloat

    var body: some View {
        let side = TileGeometry.tileSide(contentWidth: contentWidth)
        HStack(spacing: HomeDesign.Metric.tileGap) {
            ForEach(HomeTileID.allCases, id: \.self) { tile in
                ZStack(alignment: .topLeading) {
                    TileSkeletonFace(contentWidth: contentWidth)
                    Text(tile.title)
                        .font(HomeDesign.Text.tileTitle)
                        .tracking(-0.3)
                        .foregroundStyle(.primary)
                        .offset(x: TileGeometry.labelLeading, y: TileGeometry.titleClosedTop)
                    Capsule()
                        .fill(Color.white.opacity(0.12))
                        .frame(width: 52, height: 10)
                        .offset(x: TileGeometry.labelLeading, y: TileGeometry.subtitleTop(isOpen: false) + 3)
                }
                .frame(width: contentWidth, height: HomeDesign.Metric.tileOpenHeight, alignment: .topLeading)
                .frame(width: side, height: TileGeometry.closedHeight, alignment: .topLeading)
                .background(TileStyle.veil)
                .clipShape(RoundedRectangle(cornerRadius: HomeDesign.Metric.tileCorner, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: HomeDesign.Metric.tileCorner, style: .continuous)
                        .strokeBorder(
                            TileStyle.stroke.opacity(TileStyle.outlineStrength), lineWidth: TileStyle.strokeWidth
                        )
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Loading \(tile.title)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: TileGeometry.closedHeight)
        .environment(\.colorScheme, .dark)
    }
}
