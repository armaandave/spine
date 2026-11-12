import SwiftUI

/// What Search shows before you type, with every kind of media in the field: one big word per kind of list
/// ("Movies", "Books", "Across Media"), how many lists it has and three of their posters. A word opens that kind's
/// lists.
struct SearchListsLanding: View {
    let store: FeaturedListsStore
    let enabledMediaTypes: [String]
    let onSelect: (FeaturedListSelection) -> Void

    var body: some View {
        let layer = store.layer(mediaType: nil)
        Group {
            if let response = layer.response {
                let shelves = response.visibleShelves(enabledMediaTypes: enabledMediaTypes)
                if shelves.isEmpty {
                    FeaturedEmptyPrompt()
                } else {
                    kinds(shelves)
                }
            } else if let error = layer.errorMessage {
                FeaturedErrorState(message: error) {
                    Task { await store.load(mediaType: nil, force: true) }
                }
            } else {
                SearchListsSkeleton()
            }
        }
        .featuredLayerLifecycle(store: store, mediaType: nil)
    }

    private func kinds(_ shelves: [FeaturedShelf]) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                SearchListsHeading()
                ForEach(shelves) { shelf in
                    NavigationLink {
                        FeaturedShelfDetailView(
                            store: store,
                            mediaType: shelf.mediaType ?? shelf.key,
                            title: shelf.title,
                            onSelect: onSelect
                        )
                    } label: {
                        SearchListsKindRow(shelf: shelf)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 14)
            .padding(.bottom, 28)
        }
        .scrollDismissesKeyboard(.interactively)
        .refreshable {
            await store.load(mediaType: nil, force: true)
        }
    }
}

private struct SearchListsHeading: View {
    var body: some View {
        Text("LISTS")
            .font(.system(size: 13, weight: .bold))
            .tracking(0.8)
            .foregroundStyle(.white.opacity(0.55))
            .padding(.bottom, 4)
            .accessibilityAddTraits(.isHeader)
    }
}

/// "Movies ⁴⁶" in big type, three of its posters at the far end.
private struct SearchListsKindRow: View {
    let shelf: FeaturedShelf

    var body: some View {
        HStack(spacing: 12) {
            HStack(alignment: .top, spacing: 7) {
                Text(shelf.title)
                    .font(.system(size: 32, weight: .heavy))
                    .tracking(-1.1)
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text(shelf.count.formatted())
                    .font(.system(size: 13, weight: .bold))
                    .monospacedDigit()
                    .foregroundStyle(.white.opacity(0.45))
                    .padding(.top, 3)
            }
            Spacer(minLength: 8)
            SearchListsFan(posters: shelf.fanPosters, square: shelf.mediaType == "music")
        }
        .frame(height: 60)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.1))
                .frame(height: 0.5)
        }
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(shelf.title), \(shelf.count) \(shelf.count == 1 ? "list" : "lists")")
        .accessibilityHint("Opens its lists")
    }
}

/// Up to three posters leaning against each other, the first in front.
private struct SearchListsFan: View {
    let posters: [MediaSummary]
    let square: Bool

    /// Front to back: size, how far in from the right edge, how far down, how much it leans, how dim it is.
    private var slots: [(size: CGSize, right: CGFloat, top: CGFloat, degrees: Double, brightness: Double)] {
        square
            ? [(CGSize(width: 32, height: 32), 42, 7, -4, 1), (CGSize(width: 28, height: 28), 22, 9, 4, 0.7),
               (CGSize(width: 26, height: 26), 0, 11, 10, 0.55)]
            : [(CGSize(width: 30, height: 46), 44, 0, -4, 1), (CGSize(width: 27, height: 40), 22, 3, 4, 0.7),
               (CGSize(width: 25, height: 37), 0, 6, 10, 0.55)]
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            ForEach(Array(posters.prefix(slots.count).enumerated().reversed()), id: \.offset) { index, media in
                let slot = slots[index]
                SpineAsyncImage(url: URL(string: media.displayPosterURL ?? ""), thumbnailSize: slot.size) { phase in
                    if case let .success(image) = phase {
                        image
                            .resizable()
                            .scaledToFill()
                    } else {
                        Color.white.opacity(0.08)
                    }
                }
                .frame(width: slot.size.width, height: slot.size.height)
                .clipShape(RoundedRectangle(cornerRadius: index == 0 ? 5 : 4, style: .continuous))
                .brightness(slot.brightness - 1)
                .shadow(color: .black.opacity(index == 0 ? 0.5 : 0), radius: 5, y: 4)
                .rotationEffect(.degrees(slot.degrees))
                .padding(.trailing, slot.right)
                .padding(.top, slot.top)
            }
        }
        .frame(width: 74, height: 46, alignment: .topTrailing)
        .accessibilityHidden(true)
    }
}

private struct SearchListsSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SearchListsHeading()
            ForEach(0 ..< 8, id: \.self) { index in
                HStack {
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(.white.opacity(0.08))
                        .frame(width: [150, 70, 120, 120, 120, 120, 140, 110][index], height: 26)
                    Spacer()
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .fill(.white.opacity(0.06))
                        .frame(width: 30, height: 46)
                }
                .frame(height: 60)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .accessibilityLabel("Loading lists")
    }
}

extension FeaturedShelf {
    /// Up to three posters for a kind's row: the first poster of each of its first lists, then more from the first
    /// list when it has fewer lists, never the same title twice.
    var fanPosters: [MediaSummary] {
        var posters: [MediaSummary] = []
        func add(_ media: MediaSummary?) {
            guard let media, posters.count < 3, !posters.contains(where: { $0.ref == media.ref }) else { return }
            posters.append(media)
        }
        lists.forEach { add($0.previews.first?.media) }
        lists.first?.previews.forEach { add($0.media) }
        return posters
    }
}
