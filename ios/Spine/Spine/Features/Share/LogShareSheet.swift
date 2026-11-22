import LinkPresentation
import SwiftUI
import UIKit

/// The sheet a log's share button opens: the picture that will be shared, then Share Image, which hands it to the iOS
/// share sheet (Instagram Stories, Messages, Save Image and the rest).
struct LogShareSheet: View {
    let content: LogShareContent

    @Environment(\.dismiss) private var dismiss
    @State private var story: UIImage?

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Group {
                    if let story {
                        Image(uiImage: story)
                            .resizable()
                            .scaledToFit()
                            .clipShape(.rect(cornerRadius: 18, style: .continuous))
                            .shadow(color: .black.opacity(0.4), radius: 20, y: 10)
                            .accessibilityLabel("The picture of \(content.username)'s log of \(content.title)")
                    } else {
                        ProgressView()
                            .tint(.white)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)

                Button(action: share) {
                    Label("Share Image", systemImage: "square.and.arrow.up")
                        .font(.headline)
                        .foregroundStyle(story == nil ? .white.opacity(0.42) : .black)
                        .frame(maxWidth: .infinity, minHeight: 52)
                }
                .tint(story == nil ? .white.opacity(0.08) : .white)
                .buttonStyle(.glassProminent)
                .disabled(story == nil)
            }
            .padding(.horizontal, 24)
            .padding(.top, 8)
            .padding(.bottom, 12)
            .navigationTitle("Share")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            story = await LogSharePicture.story(for: content)
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
