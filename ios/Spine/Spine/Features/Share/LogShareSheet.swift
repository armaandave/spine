import LinkPresentation
import SwiftUI
import UIKit

/// The sheet a log's share button opens: the picture that will be shared, over the log page's own blurred poster, with
/// ✕ and Share in the bar. Share hands the picture to the iOS share sheet (Instagram Stories, Messages, Save Image).
struct LogShareSheet: View {
    let content: LogShareContent
    /// The log page's art and tint, so the sheet sits on the same blurred poster.
    let backdropURL: URL?
    let accentHex: String?

    @Environment(\.dismiss) private var dismiss
    @State private var story: UIImage?
    @State private var couldNotDraw = false

    var body: some View {
        NavigationStack {
            ZStack {
                PostPageBackground(url: backdropURL, accentHex: accentHex)

                if let story {
                    Image(uiImage: story)
                        .resizable()
                        .scaledToFit()
                        .clipShape(.rect(cornerRadius: 20, style: .continuous))
                        .overlay {
                            RoundedRectangle(cornerRadius: 20, style: .continuous)
                                .strokeBorder(.white.opacity(0.14), lineWidth: 0.5)
                        }
                        .shadow(color: .black.opacity(0.45), radius: 24, y: 12)
                        .padding(.horizontal, 32)
                        .padding(.top, 8)
                        .padding(.bottom, 24)
                        .accessibilityLabel("The picture of \(content.username)'s log of \(content.title)")
                } else if couldNotDraw {
                    ContentUnavailableView("Couldn't make the picture", systemImage: "photo")
                        .foregroundStyle(.white)
                } else {
                    ProgressView()
                        .tint(.white)
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(action: share) {
                        Image(systemName: "square.and.arrow.up")
                    }
                    .disabled(story == nil)
                    .accessibilityLabel("Share Image")
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            story = await LogSharePicture.story(for: content)
            couldNotDraw = story == nil
        }
    }

    private func share() {
        guard let story else { return }
        LogSharePicture.present([LogShareImageItem(image: story, title: "\(content.title) · \(content.username)’s log")])
    }
}

/// The picture for the iOS share sheet, with a title for the sheet's header in place of "PNG Image".
final class LogShareImageItem: NSObject, UIActivityItemSource {
    private let image: UIImage
    private let title: String

    init(image: UIImage, title: String) {
        self.image = image
        self.title = title
    }

    func activityViewControllerPlaceholderItem(_ activityViewController: UIActivityViewController) -> Any {
        image
    }

    func activityViewController(
        _ activityViewController: UIActivityViewController,
        itemForActivityType activityType: UIActivity.ActivityType?
    ) -> Any? {
        image
    }

    func activityViewControllerLinkMetadata(_ activityViewController: UIActivityViewController) -> LPLinkMetadata? {
        let metadata = LPLinkMetadata()
        metadata.title = title
        metadata.imageProvider = NSItemProvider(object: image)
        return metadata
    }
}
