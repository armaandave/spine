import SwiftUI

/// A list on the Lists page: a glass rail with the list's name, its type icon and count at the end of the name's line,
/// and its first posters. A dim blur of the list's first poster sits behind the glass, so each rail takes on its
/// list's own colors.
struct CustomListRail: View {
    let list: CustomListSummary

    private static let shape = RoundedRectangle(cornerRadius: 26, style: .continuous)

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            nameLine
            CustomListRailStrip(list: list)
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        // The strip scrolls under the rail's rounded ends.
        .clipShape(Self.shape)
        .glassEffect(glass, in: Self.shape)
        .background { art }
        .contentShape(Self.shape)
        // The page is always dark; light glass would wash out the white text.
        .environment(\.colorScheme, .dark)
    }

    /// The name, then the type icon and the count, quiet, at the end of its first line.
    private var nameLine: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(list.name)
                .font(.system(size: 17, weight: .semibold))
                .tracking(-0.2)
                .foregroundStyle(.white)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 6) {
                ListMediaTypeIcons(mediaTypes: list.mediaTypes, isPeople: list.listType == .people, size: 13)
                Text(list.railCount.formatted())
                    .font(.system(size: 15, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white)
            }
            .opacity(0.62)
            .fixedSize()
        }
        .padding(.horizontal, 18)
    }

    /// A hint of the first poster's color in the glass, when the server knows it.
    private var glass: Glass {
        guard let rgb = ArtHex.rgb(list.previewItems?.first?.posterAccentColor) else { return .regular }
        return .regular.tint(Color(red: rgb.red, green: rgb.green, blue: rgb.blue).opacity(0.08))
    }

    /// The first poster (or photo), blurred and dimmed behind the rail for the glass to bend.
    private var art: some View {
        BlurredArtBackground(
            url: list.railArtURL,
            accentHex: nil,
            style: .card,
            artOpacity: 0.55,
            baseOpacity: 0
        )
        .clipShape(RoundedRectangle(cornerRadius: 30, style: .continuous))
        .padding(.horizontal, 6)
        .padding(.top, 8)
        .padding(.bottom, -2)
    }
}

/// The rail's posters: a row that scrolls sideways (square art for albums, round photos for people).
private struct CustomListRailStrip: View {
    let list: CustomListSummary

    private static let spacing: CGFloat = 6
    private static let inset: CGFloat = 18

    var body: some View {
        if list.listType == .people {
            people
        } else {
            media
        }
    }

    @ViewBuilder
    private var media: some View {
        let items = list.previewItems ?? []
        if items.isEmpty {
            emptyState("No items yet")
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: Self.spacing) {
                    // By index: a featured list can hold the same title more than once.
                    ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                        MediaArtwork(
                            url: item.displayPosterURL,
                            title: item.title,
                            slot: .listRail,
                            mediaType: item.ref.mediaType,
                            orientation: item.posterOrientation
                        )
                    }
                }
                .padding(.horizontal, Self.inset)
            }
        }
    }

    @ViewBuilder
    private var people: some View {
        if list.previewPeople.isEmpty {
            emptyState("No people yet")
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: Self.spacing) {
                    ForEach(list.previewPeople, id: \.entryId) { person in
                        PersonArtwork(
                            urlString: person.profileUrl,
                            name: person.name,
                            size: PosterSlot.listRail.size.width
                        )
                        .accessibilityLabel(person.name)
                    }
                }
                .padding(.horizontal, Self.inset)
            }
        }
    }

    private func emptyState(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(.white.opacity(0.58))
            .padding(.horizontal, Self.inset)
    }
}

/// A rail scales down a touch while pressed.
struct CustomListRailButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        CustomListRailPress(configuration: configuration)
    }
}

private struct CustomListRailPress: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let configuration: ButtonStyleConfiguration

    var body: some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.985 : 1)
            .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: configuration.isPressed)
    }
}

extension CustomListSummary {
    /// What the rail counts: people on a people list, titles on a media list.
    var railCount: Int {
        listType == .people ? entriesCount : itemsCount
    }

    /// The art behind the rail: the first poster, or the first person's photo.
    var railArtURL: URL? {
        let url = listType == .people ? previewPeople.first?.profileUrl : previewItems?.first?.displayPosterURL
        return url.flatMap(URL.init(string:))
    }

    /// "Anime and manga to start with, 10 titles, Anime and Manga": the name, the count in words, and what a mixed
    /// list holds (a one-type list's count already says it).
    var railAccessibilityLabel: String {
        let count = ListCountWords.text(count: railCount, mediaTypes: mediaTypes, isPeople: listType == .people)
        let holds = mediaTypes.count > 1 ? mediaTypesLabel : nil
        return [name, count, holds].compactMap { $0 }.joined(separator: ", ")
    }
}
