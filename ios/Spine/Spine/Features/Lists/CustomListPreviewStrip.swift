import SwiftUI

struct CustomListPreviewStrip: View {
    let list: CustomListSummary

    var body: some View {
        if list.listType == .people {
            peopleStrip
        } else {
            mediaStrip
        }
    }

    @ViewBuilder
    private var mediaStrip: some View {
        let media = list.previewItems ?? []
        if media.isEmpty {
            emptyState("No items yet")
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 8) {
                    // By index: a featured list can hold the same title more than once.
                    ForEach(Array(media.enumerated()), id: \.offset) { _, item in
                        MediaArtwork(
                            url: item.displayPosterURL,
                            title: item.title,
                            slot: .listPreview,
                            mediaType: item.ref.mediaType,
                            orientation: item.posterOrientation
                        )
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var peopleStrip: some View {
        if list.previewPeople.isEmpty {
            emptyState("No people yet")
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 8) {
                    ForEach(list.previewPeople, id: \.entryId) { person in
                        PersonArtwork(
                            urlString: person.profileUrl,
                            name: person.name,
                            size: PosterSlot.listPreview.size.width
                        )
                        .accessibilityLabel(person.name)
                    }
                }
                .frame(minHeight: PosterSlot.listPreview.size.height)
            }
        }
    }

    private func emptyState(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(.white.opacity(0.38))
            .frame(
                maxWidth: .infinity,
                minHeight: PosterSlot.listPreview.size.height,
                alignment: .leading
            )
            .padding(.horizontal, 10)
            .background(.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
