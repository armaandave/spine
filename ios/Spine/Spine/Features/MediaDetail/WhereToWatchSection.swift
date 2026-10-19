import SwiftUI

/// Where to Watch on movie, TV, and season detail (ST-200 to ST-202).
struct WhereToWatchSection: View {
    let whereToWatch: WhereToWatch
    let onChooseServices: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            if let headline = whereToWatch.myServicesHeadline {
                Label {
                    Text(headline)
                } icon: {
                    Image(systemName: "checkmark.circle.fill")
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.black, .green)
                }
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white)
            }

            let groups = whereToWatch.visibleGroups
            if groups.isEmpty {
                Text("Not available to stream in \(whereToWatch.regionDisplayName).")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.62))
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(groups) { group in
                        groupRow(group)
                    }
                }
            }

            if !whereToWatch.hasServices {
                Button(action: onChooseServices) {
                    Label("Choose your streaming services", systemImage: "plus.circle.fill")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white.opacity(0.82))
                        .padding(.horizontal, 12)
                        .frame(minHeight: 32)
                        .background(.white.opacity(0.08), in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Pick the services you use to see what you can stream on them")
            }

            JustWatchCredit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            SectionLabel(title: "Where to Watch")
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            Text(whereToWatch.regionDisplayName)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.46))
                .lineLimit(1)
                .accessibilityLabel("In \(whereToWatch.regionDisplayName)")
        }
    }

    private func groupRow(_ group: WhereToWatchGroup) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(group.label)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(.white.opacity(0.52))
                .accessibilityAddTraits(.isHeader)

            ScrollView(.horizontal, showsIndicators: false) {
                GlassEffectContainer(spacing: 8) {
                    HStack(spacing: 8) {
                        ForEach(group.orderedProviders) { provider in
                            WhereToWatchProviderTile(provider: provider, group: group, link: whereToWatch.linkURL)
                        }
                    }
                }
            }
            .scrollClipDisabled()
            .scrollBounceBehavior(.basedOnSize, axes: .horizontal)
        }
    }
}

/// One provider in a Where to Watch group: logo and name, marked when it is one of the user's services.
/// Opens the title's TMDB watch page; without one the tile is plain, with no link affordance.
private struct WhereToWatchProviderTile: View {
    let provider: WhereToWatchProvider
    let group: WhereToWatchGroup
    let link: URL?

    var body: some View {
        if let link {
            Link(destination: link) {
                tile(isLinked: true)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(provider.accessibilityLabel(in: group))
            .accessibilityHint("Opens where to watch in your browser")
        } else {
            tile(isLinked: false)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(provider.accessibilityLabel(in: group))
        }
    }

    private func tile(isLinked: Bool) -> some View {
        HStack(spacing: 8) {
            StreamingProviderLogo(url: provider.logoUrl, name: provider.name, size: 26)
                .overlay(alignment: .bottomTrailing) {
                    if provider.isMine {
                        Image(systemName: "checkmark.circle.fill")
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.black, .green)
                            .font(.system(size: 12, weight: .bold))
                            .background(Circle().fill(.black).padding(1))
                            .offset(x: 4, y: 4)
                    }
                }

            Text(provider.name)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(1)

            if isLinked {
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white.opacity(0.42))
            }
        }
        .padding(.leading, 7)
        .padding(.trailing, 12)
        .frame(height: 40)
        .background(.white.opacity(provider.isMine ? 0.1 : 0.06), in: Capsule())
        .glassEffect(
            isLinked ? .regular.tint(.white.opacity(0.04)).interactive() : .regular.tint(.white.opacity(0.04)),
            in: Capsule()
        )
        .overlay {
            Capsule().stroke(.white.opacity(provider.isMine ? 0.24 : 0.12), lineWidth: 0.75)
        }
        .contentShape(Capsule())
    }
}
