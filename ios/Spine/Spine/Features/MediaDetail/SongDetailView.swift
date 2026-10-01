import Foundation
import SwiftUI

@MainActor
@Observable
final class SongDetailViewModel {
    var detail: MusicRecordingDetail?
    var isLoading = true
    var errorMessage: String?

    private let selection: MusicSongSelection
    private let musicRepository: MusicRepository
    private let onUnauthorized: () -> Void

    init(
        selection: MusicSongSelection,
        musicRepository: MusicRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.selection = selection
        self.musicRepository = musicRepository
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            detail = try await musicRepository.recordingDetail(
                album: selection.album,
                recordingMbid: selection.recordingMbid
            )
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

enum MusicSongPresentation {
    static func albumContext(_ detail: MusicRecordingDetail) -> String {
        let trackNumber = detail.contextRelease.track.number
        let number = trackNumber.isEmpty
            ? String(detail.contextRelease.track.position)
            : trackNumber
        return "Track \(number) on \(detail.parentAlbum.title)"
    }

    static func credit(_ credit: MusicRecordingCredit) -> String? {
        let roles = credit.roles.compactMap { role -> String? in
            switch role.lowercased() {
            case "writer": "Songwriter"
            case "composer": "Composer"
            case "lyricist": "Lyricist"
            default: nil
            }
        }
        guard !roles.isEmpty else { return nil }
        return "\(credit.name) · \(roles.joined(separator: ", "))"
    }
}

struct SongDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var viewModel: SongDetailViewModel
    @State private var selectedAlbum: MediaRef?
    @State private var presentedPerson: PersonRef?
    @State private var edgeDragOffset: CGFloat = 0

    private let selection: MusicSongSelection
    private let musicRepository: MusicRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let companyRepository: CompanyRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void

    init(
        selection: MusicSongSelection,
        musicRepository: MusicRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository,
        peopleRepository: PeopleRepository,
        companyRepository: CompanyRepository,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.selection = selection
        self.musicRepository = musicRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.companyRepository = companyRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: SongDetailViewModel(
            selection: selection,
            musicRepository: musicRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            SpinePageBackground()
            content
                .spineContentTransition(value: contentPhase)
            backButton
                .padding(.horizontal, 16)
                .padding(.top, 8)
        }
        .toolbar(.hidden, for: .tabBar)
        .navigationBarBackButtonHidden()
        .dismissExplorationOnReturnHome()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 28)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .overlay(alignment: .bottomTrailing) {
            ExplorationHomeButton()
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
        }
        .fullScreenCover(item: $selectedAlbum) { ref in
            MediaDetailView(
                ref: ref,
                mediaRepository: mediaRepository,
                musicRepository: musicRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedPerson) { person in
            PersonDetailView(
                ref: person,
                peopleRepository: peopleRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .task {
            if viewModel.detail == nil {
                await viewModel.load()
            }
        }
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading, viewModel.detail == nil {
            ProgressView("Loading song…")
                .tint(.white)
                .foregroundStyle(.white.opacity(0.72))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityIdentifier("song-detail.loading")
        } else if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    hero(detail)

                    VStack(alignment: .leading, spacing: 28) {
                        works(detail)
                        albums(detail)
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 28)
                    .padding(.bottom, 40)
                }
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)
        } else {
            VStack(spacing: 18) {
                ContentUnavailableView(
                    "Could not load song",
                    systemImage: "exclamationmark.triangle",
                    description: Text(viewModel.errorMessage ?? "Unknown error")
                )
                .foregroundStyle(.white)
                Button("Try Again") {
                    Task { await viewModel.load() }
                }
                .buttonStyle(.borderedProminent)
                .tint(.white.opacity(0.16))
                .accessibilityIdentifier("song-detail.retry")
            }
            .padding()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityIdentifier("song-detail.error")
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var backButton: some View {
        Button { dismiss() } label: {
            Image(systemName: "chevron.left")
                .font(.system(size: 15, weight: .heavy))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(.black.opacity(0.48), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Back to album")
    }

    private func hero(_ detail: MusicRecordingDetail) -> some View {
        let artworkURL = selection.artworkURL ?? detail.imageUrl ?? detail.parentAlbum.displayPosterURL
        let artworkSize: CGFloat = 244

        return VStack(spacing: 0) {
            MediaArtwork(
                url: artworkURL,
                title: detail.parentAlbum.title,
                slot: .hero,
                mediaType: "music",
                orientation: .square
            )
            .scaleEffect(1.28)
            .frame(width: artworkSize, height: artworkSize)
            .accessibilityLabel("Album cover for \(detail.parentAlbum.title)")
            .shadow(color: .black.opacity(0.48), radius: 22, y: 12)
            .padding(.bottom, 18)

            VStack(alignment: .leading, spacing: 11) {
                Text(detail.title)
                    .font(.system(size: 33, weight: .heavy))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityAddTraits(.isHeader)

                songArtistByline(detail.artistCredit)

                HStack(spacing: 8) {
                    albumContext(detail)
                    if let duration = MusicAlbumPresentation.duration(detail.lengthMs) {
                        songPill(duration)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 14)
        .padding(.top, 108)
        .padding(.bottom, 24)
        .background {
            HeroArtwork(artworkURL: URL(string: artworkURL ?? ""))
                .offset(y: BackdropLayout.topOffset)
        }
    }

    private func albumContext(_ detail: MusicRecordingDetail) -> some View {
        Button { openAlbum(detail.parentAlbum.ref) } label: {
            HStack(spacing: 5) {
                Text(MusicSongPresentation.albumContext(detail))
                    .lineLimit(1)
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.bold))
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.82))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(.white.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\(MusicSongPresentation.albumContext(detail)). Open album"
        )
    }

    private func songPill(_ value: String) -> some View {
        Text(value)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.82))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(.white.opacity(0.12), in: Capsule())
    }

    @ViewBuilder
    private func works(_ detail: MusicRecordingDetail) -> some View {
        if !detail.works.isEmpty {
            SongSection(title: "Related Works") {
                VStack(spacing: 10) {
                    ForEach(detail.works, id: \.workMbid) { work in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(work.title)
                                .font(.headline)
                                .foregroundStyle(.white.opacity(0.92))
                            let metadata = workMetadata(work)
                            if !metadata.isEmpty {
                                Text(metadata.joined(separator: " · "))
                                    .font(.caption.weight(.medium))
                                    .foregroundStyle(.white.opacity(0.62))
                            }
                            ForEach(Array(work.credits.enumerated()), id: \.offset) { _, credit in
                                if let value = MusicSongPresentation.credit(credit) {
                                    songWorkCredit(value, credit: credit)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(14)
                        .songSurface()
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func albums(_ detail: MusicRecordingDetail) -> some View {
        if !detail.albums.isEmpty {
            SongSection(title: "Albums") {
                VStack(spacing: 0) {
                    ForEach(detail.albums) { album in
                        Button { openAlbum(album.ref) } label: {
                            HStack(spacing: 12) {
                                MediaArtwork(
                                    url: album.displayPosterURL,
                                    title: album.title,
                                    slot: .libraryRow,
                                    mediaType: "music",
                                    orientation: .square
                                )
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(album.title)
                                        .font(.subheadline.weight(.bold))
                                        .foregroundStyle(.white.opacity(0.92))
                                    if let subtitle = nonEmpty(album.subtitle) ?? nonEmpty(album.releaseDate) {
                                        Text(subtitle)
                                            .font(.caption.weight(.medium))
                                            .foregroundStyle(.white.opacity(0.62))
                                    }
                                }
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(.white.opacity(0.3))
                            }
                            .padding(12)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Open album \(album.title)")
                        if album.id != detail.albums.last?.id {
                            Divider().overlay(.white.opacity(0.05))
                        }
                    }
                }
                .songSurface()
            }
        }
    }

    private func openAlbum(_ ref: MediaRef) {
        if ref.id == selection.album.id {
            dismiss()
        } else {
            selectedAlbum = ref
        }
    }

    @ViewBuilder
    private func songArtistByline(_ credits: [MusicArtistCredit]) -> some View {
        if let presentation = MediaCreditPresentation.musicArtists(credits) {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(presentation.heroPeople, id: \.self) { person in
                    if let personRef = person.personRef {
                        Button { presentedPerson = personRef } label: {
                            songArtistText(person.name)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(person.name)")
                    } else {
                        songArtistText(person.name)
                    }
                }
                if presentation.heroMoreCount > 0 {
                    Text("+\(presentation.heroMoreCount) more")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.44))
                }
            }
        }
    }

    private func songArtistText(_ value: String) -> some View {
        Text(value)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.white.opacity(0.62))
    }

    @ViewBuilder
    private func songWorkCredit(_ value: String, credit: MusicRecordingCredit) -> some View {
        if let personRef = credit.personRef {
            Button { presentedPerson = personRef } label: {
                songWorkCreditText(value)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("View \(credit.name)")
        } else {
            songWorkCreditText(value)
        }
    }

    private func songWorkCreditText(_ value: String) -> some View {
        Text(value)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.white.opacity(0.8))
    }

    private func workMetadata(_ work: MusicWorkRelationship) -> [String] {
        [
            nonEmpty(work.relationshipType)?.capitalized,
            work.iswcs.isEmpty ? nil : work.iswcs.joined(separator: ", "),
            nonEmpty(work.language),
        ].compactMap { $0 }
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }
}

private struct SongSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title.uppercased())
                .font(.caption2.weight(.heavy))
                .foregroundStyle(.white.opacity(0.62))
            content
        }
    }
}

private extension View {
    func songSurface() -> some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        return background(Color.white.opacity(0.028), in: shape)
            .overlay { shape.stroke(.white.opacity(0.045), lineWidth: 1) }
    }
}
