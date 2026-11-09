import Foundation
import Observation
import SwiftUI

/// The title whose backdrop is the picture at the top of Home today, with what the museum-style label needs.
nonisolated struct HomeTopArt: Equatable, Codable {
    let ref: MediaRef
    let title: String
    /// Four digits, or nil when the title's page has no date.
    let year: String?
    let backdrop: URL

    /// "Severance · 2022", or just the title when its year is not known.
    var label: String {
        year.map { "\(title) · \($0)" } ?? title
    }
}

/// Which of a list's titles may be the picture, and which one a given day starts at. Pure, so the choice is testable.
nonisolated enum HomeTopArtPick {
    /// The kinds with a backdrop. Books have none.
    static let eligibleTypes: Set<String> = ["movie", "tv", "anime", "game"]

    /// The eligible titles of `media`, in the list's order, each once.
    static func candidates(_ media: [MediaSummary]) -> [MediaSummary] {
        var seen = Set<String>()
        return media.filter { eligibleTypes.contains($0.ref.mediaType) && seen.insert($0.ref.id).inserted }
    }

    /// A number that is the same all day and one higher tomorrow.
    static func dayKey(_ date: Date, calendar: Calendar) -> Int {
        calendar.ordinality(of: .day, in: .era, for: date) ?? 0
    }

    /// `list` starting at the day's place in it and wrapping around: the first title is today's pick, the rest are
    /// what to try if it has no backdrop.
    static func rotated(_ list: [MediaSummary], dayKey: Int) -> [MediaSummary] {
        guard !list.isEmpty else { return [] }
        let start = ((dayKey % list.count) + list.count) % list.count
        return Array(list[start...] + list[..<start])
    }
}

/// Today's picture for the top of Home: one of the In progress titles that has a backdrop (else a Trending one), the
/// same all day. The list rows carry no backdrop, so it asks the chosen title's page once, remembers the answer for
/// the day, and starts loading the image as soon as it knows.
@MainActor
@Observable
final class HomeTopArtModel {
    private(set) var art: HomeTopArt?
    /// Whether you may choose the site-wide picture (the project owner): only then does Home take a long press.
    private(set) var canChangeSitePicture = false
    /// Whether the server has a site-wide picture set; while it does, it is the picture for everyone.
    private(set) var hasSitePick = false
    /// Called when `art` changes, so the glow behind the page knows whether a picture covers it at rest.
    @ObservationIgnored var onChange: (HomeTopArt?) -> Void = { _ in }

    /// How many titles one attempt may ask about: normally the first has a backdrop.
    static let maxTries = 3
    static let defaultsKey = "home.topArt.v1"
    /// The server's last answer about the site-wide picture, kept apart from the daily pick.
    static let siteDefaultsKey = "home.sitePicture.v1"

    private nonisolated struct Stored: Codable {
        let dayKey: Int
        let art: HomeTopArt
    }

    private nonisolated struct SiteStored: Codable {
        let art: HomeTopArt?
        let canChange: Bool
    }

    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let calendar: Calendar
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var artDay: Int?
    @ObservationIgnored private var siteArt: HomeTopArt?
    /// The lists last offered, to start the daily pick again when the site-wide picture is cleared.
    @ObservationIgnored private var lastInProgress: [MediaSummary]?
    @ObservationIgnored private var lastTrending: [MediaSummary] = []
    @ObservationIgnored private var task: Task<Void, Never>?
    /// Titles whose page answered with no backdrop today: not asked about again.
    @ObservationIgnored private var withoutBackdrop = Set<String>()
    @ObservationIgnored private var withoutBackdropDay = -1

    init(
        mediaRepository: MediaRepository,
        defaults: UserDefaults = .standard,
        calendar: Calendar = .current,
        now: @escaping () -> Date = Date.init
    ) {
        self.mediaRepository = mediaRepository
        self.defaults = defaults
        self.calendar = calendar
        self.now = now
        // The site-wide picture from the last answer is there on the first frame, with no wait for the network.
        if let data = defaults.data(forKey: Self.siteDefaultsKey),
           let site = try? JSONDecoder().decode(SiteStored.self, from: data) {
            canChangeSitePicture = site.canChange
            if let siteArt = site.art {
                self.siteArt = siteArt
                hasSitePick = true
                art = siteArt
                ArtworkPrefetch.prefetch([HomeBackdropArt.request(url: siteArt.backdrop)].compactMap { $0 })
                return
            }
        }
        restoreDaily()
    }

    /// Today's pick from an earlier launch is there on the first frame.
    private func restoreDaily() {
        let today = HomeTopArtPick.dayKey(now(), calendar: calendar)
        if let data = defaults.data(forKey: Self.defaultsKey),
           let stored = try? JSONDecoder().decode(Stored.self, from: data),
           stored.dayKey == today {
            art = stored.art
            artDay = today
            ArtworkPrefetch.prefetch([HomeBackdropArt.request(url: stored.art.backdrop)].compactMap { $0 })
        }
    }

    /// Asks the server for the site-wide picture. A failed read changes nothing.
    func loadSitePicture(using repository: MediaRepository) async {
        guard let response = try? await repository.homePicture() else { return }
        applySitePicture(response)
    }

    /// The server's answer: its picture wins over the daily pick for as long as it is set; with none, the daily pick runs.
    func applySitePicture(_ response: HomePictureResponse) {
        canChangeSitePicture = response.canChange
        if let picked = response.picture?.topArt {
            siteArt = picked
            hasSitePick = true
            if art != picked {
                art = picked
                if let request = HomeBackdropArt.request(url: picked.backdrop) { ArtworkPrefetch.prefetch([request]) }
                onChange(picked)
            }
            saveSite()
            return
        }
        let hadPick = hasSitePick
        siteArt = nil
        hasSitePick = false
        saveSite()
        guard hadPick else { return }
        art = nil
        artDay = nil
        restoreDaily()
        onChange(art)
        update(inProgress: lastInProgress, trending: lastTrending)
    }

    private func saveSite() {
        if let data = try? JSONEncoder().encode(SiteStored(art: siteArt, canChange: canChangeSitePicture)) {
            defaults.set(data, forKey: Self.siteDefaultsKey)
        }
    }

    var today: Int { HomeTopArtPick.dayKey(now(), calendar: calendar) }

    /// Offers the page's lists. `inProgress` is nil until the rail has loaded, so Trending cannot win a race with it.
    /// Nothing happens once today has a picture, while a pick is being made, or when nothing is eligible.
    func update(inProgress: [MediaSummary]?, trending: [MediaSummary]) {
        lastInProgress = inProgress
        lastTrending = trending
        let key = today
        guard !hasSitePick, artDay != key, task == nil, let inProgress else { return }
        if withoutBackdropDay != key {
            withoutBackdrop = []
            withoutBackdropDay = key
        }
        let skip = withoutBackdrop
        var list = HomeTopArtPick.candidates(inProgress).filter { !skip.contains($0.ref.id) }
        if list.isEmpty { list = HomeTopArtPick.candidates(trending).filter { !skip.contains($0.ref.id) } }
        guard !list.isEmpty else { return }
        task = Task { [weak self] in await self?.resolve(list, key: key) }
    }

    private func resolve(_ list: [MediaSummary], key: Int) async {
        defer { task = nil }
        for media in HomeTopArtPick.rotated(list, dayKey: key).prefix(Self.maxTries) {
            guard let detail = try? await mediaRepository.detail(ref: media.ref) else { continue }
            guard let backdrop = detail.displayBackdropURL.flatMap(URL.init(string:)) else {
                withoutBackdrop.insert(media.ref.id)
                continue
            }
            guard !hasSitePick else { return }
            let picked = HomeTopArt(
                ref: media.ref,
                title: detail.displayTitle,
                year: TrendingTileModel.year(of: detail) ?? TrendingTileModel.year(from: media.releaseDate),
                backdrop: backdrop
            )
            art = picked
            artDay = key
            if let data = try? JSONEncoder().encode(Stored(dayKey: key, art: picked)) {
                defaults.set(data, forKey: Self.defaultsKey)
            }
            if let request = HomeBackdropArt.request(url: backdrop) { ArtworkPrefetch.prefetch([request]) }
            onChange(picked)
            return
        }
    }
}

/// The numbers of the top of Home on a 390 pt wide phone, scaled with the width (0.9 to 1.2 times).
enum HomeTopArtSpec {
    static let referenceWidth: CGFloat = 390
    /// How tall the picture is, from the top of the screen.
    static let referenceHeight: CGFloat = 470
    /// Where the header row starts, from the top of the screen.
    static let referenceHeaderTop: CGFloat = 232
    /// The header row: the date over "Home" on the left; the label over the profile photo on the right.
    static let headerRowHeight: CGFloat = 54
    /// From the header row to the tiles.
    static let headerToTiles: CGFloat = 16

    /// Black over the picture: (opacity, location). A light shade keeps the status bar readable; from the picture's
    /// lower part the colors (already dimmed in the image, `HomeBackdropArt.dim`) fade out gradually, eased, through
    /// the header and the tiles, so they meet the black page with no flat block of color and no edge.
    static let shade: [(opacity: Double, location: CGFloat)] = [
        (0.35, 0),
        (0, 0.16),
        (0.1, 0.4),
        (0.35, 0.52),
        (0.62, 0.64),
        (0.82, 0.76),
        (0.94, 0.86),
        (1, 0.95),
        (1, 1),
    ]

    static func scale(width: CGFloat) -> CGFloat {
        min(1.2, max(0.9, width / referenceWidth))
    }

    static func height(width: CGFloat) -> CGFloat {
        (referenceHeight * scale(width: width)).rounded()
    }

    static func headerRowTop(width: CGFloat) -> CGFloat {
        (referenceHeaderTop * scale(width: width)).rounded()
    }

    /// How tall the owner's long-press region is in the scrolling content: from its top to the header row.
    static func longPressHeight(width: CGFloat, topInset: CGFloat) -> CGFloat {
        max(0, headerRowTop(width: width) - topInset)
    }

    /// Where the closed tiles start, from the top of the screen.
    static func tilesTop(width: CGFloat) -> CGFloat {
        headerRowTop(width: width) + headerRowHeight + headerToTiles
    }
}

/// The picture: a background of the scrolling content like the glow, so it scrolls away with the page and stays at
/// the top while the page is pulled (`GlowPlacement`). One baked image under two plain gradients. It gives way to
/// the glow while a tile is open: the image and the shade fade out on their own, with the glow fully there under
/// them, so there is no dip and nothing is flattened.
struct HomeTopArtView: View {
    let art: HomeTopArtModel
    let glow: HomeGlowModel

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let open = glow.focused != nil
        let fade = HomeDesign.Motion.glowCrossfade(reduceMotion: reduceMotion)
        let backdrop = art.art?.backdrop
        Color.clear
            .frame(height: HomeTopArtSpec.height(width: glow.screenWidth))
            // The page's black under the picture, so the glow waiting below never shows at rest, even before the
            // picture has loaded or if it never does. It fades out with the picture when a tile opens.
            .overlay {
                if backdrop != nil {
                    HomeDesign.Palette.pageBackground
                        .opacity(open ? 0 : 1)
                        .animation(fade, value: open)
                }
            }
            .overlay {
                if let backdrop {
                    SpineAsyncImage(
                        url: backdrop,
                        thumbnailSize: HomeBackdropArt.thumbnailSize,
                        processors: HomeBackdropArt.processors
                    ) { phase in
                        if case let .success(image) = phase {
                            image
                                .resizable()
                                .scaledToFill()
                        } else {
                            Color.clear
                        }
                    }
                    .opacity(open ? 0 : 1)
                    .animation(fade, value: open)
                }
            }
            .overlay {
                if backdrop != nil {
                    LinearGradient(
                        stops: HomeTopArtSpec.shade.map {
                            Gradient.Stop(color: .black.opacity($0.opacity), location: $0.location)
                        },
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .opacity(open ? 0 : 1)
                    .animation(fade, value: open)
                }
            }
            .clipped()
            .modifier(GlowPlacement(topInset: glow.contentTopInset ?? GlowSpec.estimatedTopInset, topOffset: 0))
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// Where the one who may change Home's picture holds it to choose another: a clear region over the top of the page,
/// above the header row. It is there only while no tile is open, since an open tile rises into it and its own buttons
/// (the close button among them) must get their taps.
struct HomePictureHoldRegion: View {
    let art: HomeTopArtModel
    let tiles: HomeTilesState
    let glow: HomeGlowModel
    let onHold: () -> Void

    var body: some View {
        if art.canChangeSitePicture, tiles.open == nil {
            Color.clear
                .frame(height: HomeTopArtSpec.longPressHeight(
                    width: glow.screenWidth, topInset: glow.contentTopInset ?? 0
                ))
                .contentShape(Rectangle())
                .onLongPressGesture(minimumDuration: 0.5) {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    onHold()
                }
        }
    }
}
