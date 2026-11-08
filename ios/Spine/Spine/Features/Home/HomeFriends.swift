import Foundation
import Observation
import SwiftUI

/// One person you follow, as the Friends row shows them.
struct HomeFriendFace: Identifiable, Equatable {
    let id: Int
    let username: String
    let avatarUrl: String?
}

/// The people you follow, in the order the Friends row shows them. Written by `HomeViewModel` and read only by the
/// row, so a change here redraws the row and never the page.
@MainActor
@Observable
final class HomeFriendsModel {
    private(set) var faces: [HomeFriendFace] = []
    /// Who the server listed, as it listed them: the order of the people with no activity in the feed.
    @ObservationIgnored private var following: [SocialUser] = []

    /// A fresh list from the server.
    func setFollowing(_ users: [SocialUser], feed: [ActivityItem]) {
        following = users
        reorder(feed: feed)
    }

    /// The feed changed: whoever posted most recently comes first.
    func reorder(feed: [ActivityItem]) {
        let ordered = Self.order(following, feed: feed)
        if ordered != faces { faces = ordered }
    }

    /// The people with a post in the loaded feed first, by their newest post (the feed is newest first), then the
    /// rest in the server's order.
    nonisolated static func order(_ following: [SocialUser], feed: [ActivityItem]) -> [HomeFriendFace] {
        var firstSeen: [Int: Int] = [:]
        for (index, item) in feed.enumerated() where firstSeen[item.actor.id] == nil {
            firstSeen[item.actor.id] = index
        }
        let active = following.filter { firstSeen[$0.id] != nil }.sorted { (firstSeen[$0.id] ?? 0) < (firstSeen[$1.id] ?? 0) }
        let quiet = following.filter { firstSeen[$0.id] == nil }
        return (active + quiet).map { HomeFriendFace(id: $0.id, username: $0.username, avatarUrl: $0.avatarUrl) }
    }
}

/// The Friends heading and the faces beside it. The faces are as tall as the heading's capital letters.
enum HomeFriendsSpec {
    static let headingSize: CGFloat = 22
    /// Between the heading and the first face.
    static let headingGap: CGFloat = 12
    /// Between faces.
    static let gap: CGFloat = 6
    /// A face is easy to hit without being any taller to look at.
    static let touchPadding: CGFloat = 10
    /// How far a soft edge reaches into the strip where more faces lie beyond it.
    static let fade: CGFloat = 18

    /// The height of the heading's capital letters (about 15.5 pt at 22 pt heavy), to the half point.
    static let faceSide: CGFloat = (UIFont.systemFont(ofSize: headingSize, weight: .heavy).capHeight * 2).rounded() / 2
}

/// The faces of the people you follow in a sideways strip. Nothing shows until there are some, so the heading does not
/// jump as they load. A soft edge shows on a side where more lie beyond it. Tapping a face opens their profile.
struct HomeFriendsStrip: View {
    let model: HomeFriendsModel

    /// Whether faces lie beyond the strip's left and right edges.
    private struct Overflow: Equatable {
        var leading = false
        var trailing = false
    }

    @State private var overflow = Overflow()

    var body: some View {
        let faces = model.faces
        Group {
            if !faces.isEmpty { strip(faces) }
        }
        .animation(.easeOut(duration: 0.25), value: faces.isEmpty)
    }

    private func strip(_ faces: [HomeFriendFace]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: HomeFriendsSpec.gap) {
                ForEach(faces) { face in
                    NavigationLink {
                        UserProfileDestination(username: face.username)
                    } label: {
                        HomeFriendAvatar(face: face)
                            .padding(.vertical, HomeFriendsSpec.touchPadding)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(face.username)
                    .accessibilityHint("Opens profile")
                }
            }
        }
        .onScrollGeometryChange(for: Overflow.self) { geometry in
            let maxOffset = geometry.contentSize.width - geometry.containerSize.width
            return Overflow(
                leading: geometry.contentOffset.x > 1,
                trailing: maxOffset > 1 && geometry.contentOffset.x < maxOffset - 1
            )
        } action: { _, new in
            if overflow != new { overflow = new }
        }
        .mask { fadeMask }
        // The padding that makes a face easy to hit is not part of the row's height.
        .padding(.vertical, -HomeFriendsSpec.touchPadding)
        .transition(.opacity)
    }

    private var fadeMask: some View {
        HStack(spacing: 0) {
            LinearGradient(colors: [.black.opacity(overflow.leading ? 0 : 1), .black], startPoint: .leading, endPoint: .trailing)
                .frame(width: HomeFriendsSpec.fade)
            Color.black
            LinearGradient(colors: [.black, .black.opacity(overflow.trailing ? 0 : 1)], startPoint: .leading, endPoint: .trailing)
                .frame(width: HomeFriendsSpec.fade)
        }
    }
}

/// A person's picture, decoded at its size, or their initial on a pastel disc (`FeedAvatar`).
private struct HomeFriendAvatar: View {
    let face: HomeFriendFace

    var body: some View {
        let side = HomeFriendsSpec.faceSide
        SpineAsyncImage(
            url: FeedCardArt.url(from: face.avatarUrl),
            thumbnailSize: CGSize(width: side, height: side)
        ) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    FeedAvatar.discColor(for: face.username)
                    Text(FeedAvatar.initial(of: face.username))
                        .font(.system(size: 8, weight: .heavy))
                        .foregroundStyle(HomeDesign.Palette.avatarInk)
                }
            }
        }
        .frame(width: side, height: side)
        .clipShape(Circle())
        .accessibilityHidden(true)
    }
}
