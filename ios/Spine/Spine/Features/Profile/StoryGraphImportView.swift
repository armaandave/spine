import SwiftUI
import UniformTypeIdentifiers

struct StoryGraphImportView: View {
    @State private var mode: ImportMode = .new
    @State private var isFileImporterPresented = false
    @State private var isOverwriteConfirmationPresented = false
    @State private var isUploadScreenPresented = false

    let coordinator: StoryGraphImportCoordinator

    private var isBusy: Bool {
        switch coordinator.phase {
        case .uploading, .processing:
            true
        case .idle, .succeeded, .failed:
            false
        }
    }

    var body: some View {
        SettingsImportLandingPage(
            source: "StoryGraph",
            headline: "Bring your reading story.",
            instructions: "Export your library from StoryGraph, then upload the CSV file here.",
            linkTitle: "Open StoryGraph",
            linkURL: URL(string: "https://app.thestorygraph.com/")!,
            mode: $mode,
            modeDetail: modeDetail,
            isBusy: isBusy,
            chooseFile: chooseFile
        )
        .navigationTitle("StoryGraph Import")
        .navigationBarTitleDisplayMode(.inline)
        .preferredColorScheme(.dark)
        .confirmationDialog(
            "This replaces existing book tracking and book diary entries before importing. This can't be undone.",
            isPresented: $isOverwriteConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Replace Existing", role: .destructive) {
                isOverwriteConfirmationPresented = false
                isFileImporterPresented = true
            }
            Button("Cancel", role: .cancel) {}
        }
        .fileImporter(
            isPresented: $isFileImporterPresented,
            allowedContentTypes: [.commaSeparatedText, UTType(filenameExtension: "csv")!],
            allowsMultipleSelection: false,
            onCompletion: handleFileImporterResult
        )
        .fullScreenCover(isPresented: $isUploadScreenPresented) {
            StoryGraphImportUploadView(
                coordinator: coordinator,
                onDone: { isUploadScreenPresented = false }
            )
        }
    }

    private var modeDetail: String {
        switch mode {
        case .new:
            "Import books and dated diary entries that aren't already in Spine."
        case .overwrite:
            "Delete your existing book tracking and book diary entries, then import everything from this file."
        }
    }

    private func chooseFile() {
        if mode == .overwrite {
            isOverwriteConfirmationPresented = true
        } else {
            isFileImporterPresented = true
        }
    }

    private func handleFileImporterResult(_ result: Result<[URL], Error>) {
        switch result {
        case let .success(urls):
            guard let url = urls.first else { return }
            isUploadScreenPresented = true
            coordinator.startImport(fileURL: url, mode: mode)
        case let .failure(error):
            isUploadScreenPresented = true
            coordinator.phase = .failed(message: error.localizedDescription)
        }
    }
}
