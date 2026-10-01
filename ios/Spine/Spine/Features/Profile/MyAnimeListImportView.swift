import SwiftUI
import UniformTypeIdentifiers

struct MyAnimeListImportView: View {
    @State private var mode: ImportMode = .new
    @State private var isFileImporterPresented = false
    @State private var isOverwriteConfirmationPresented = false
    @State private var isUploadScreenPresented = false

    let coordinator: MyAnimeListImportCoordinator

    /// MyAnimeList exports are `.xml.gz` downloads (gzip) or, once unzipped, plain `.xml`. The extension lookups
    /// cover files whose type the system only knows by their name.
    static let allowedContentTypes: [UTType] = {
        var types: [UTType] = [.xml, .gzip]
        for fileExtension in ["xml", "gz", "gzip"] {
            if let type = UTType(filenameExtension: fileExtension), !types.contains(type) {
                types.append(type)
            }
        }
        return types
    }()

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
            source: "MyAnimeList",
            headline: "Bring your anime and manga.",
            instructions: """
            Sign in on myanimelist.net and open Export from your profile menu. Choose Anime List or Manga List, \
            tap Export, and download the .xml.gz file, then upload it here. An unzipped .xml file works too.

            Ratings, progress, statuses, and finish dates are imported. Completed titles with a finish date \
            become diary entries.
            """,
            linkTitle: "Open MyAnimeList Export",
            linkURL: URL(string: "https://myanimelist.net/panel.php?go=export")!,
            mode: $mode,
            modeDetail: modeDetail,
            isBusy: isBusy,
            chooseFile: chooseFile
        )
        .navigationTitle("MyAnimeList Import")
        .navigationBarTitleDisplayMode(.inline)
        .preferredColorScheme(.dark)
        .confirmationDialog(
            "This replaces your existing tracking and diary entries for the anime and manga in this file before importing. This can't be undone.",
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
            allowedContentTypes: Self.allowedContentTypes,
            allowsMultipleSelection: false,
            onCompletion: handleFileImporterResult
        )
        .fullScreenCover(isPresented: $isUploadScreenPresented) {
            MyAnimeListImportUploadView(
                coordinator: coordinator,
                onDone: { isUploadScreenPresented = false }
            )
        }
    }

    private var modeDetail: String {
        switch mode {
        case .new:
            "Import anime, manga, and dated diary entries that aren't already in Spine."
        case .overwrite:
            "Replace your existing tracking and diary entries for the titles in this file with what's in the export."
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
