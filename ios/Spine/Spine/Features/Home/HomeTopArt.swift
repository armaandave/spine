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
    /// Called when `art` changes, so the glow behind the page knows whether a picture covers it at rest.
    @ObservationIgnored var onChange: (HomeTopArt?) -> Void = { _ in }

    /// How many titles one attempt may ask about: normally the first has a backdrop.
    static let maxTries = 3
    static let defaultsKey = "home.topArt.v1"

    private nonisolated struct Stored: Codable {
        let dayKey: Int
        let art: HomeTopArt
    }

    @ObservationIgnored private let mediaRepository: MediaRepository
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let calendar: Calendar
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var artDay: Int?
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
        // Today's pick from an earlier launch is there on the first frame.
        let today = HomeTopArtPick.dayKey(now(), calendar: calendar)
        if let data = defaults.data(forKey: Self.defaultsKey),
           let stored = try? JSONDecoder().decode(Stored.self, from: data),
           stored.dayKey == today {
            art = stored.art
            artDay = today
            ArtworkPrefetch.prefetch([HomeBackdropArt.request(url: stored.art.backdrop)].compactMap { $0 })
        }
    }

    var today: Int { HomeTopArtPick.dayKey(now(), calendar: calendar) }

    /// Offers the page's lists. `inProgress` is nil until the rail has loaded, so Trending cannot win a race with it.
    /// Nothing happens once today has a picture, while a pick is being made, or when nothing is eligible.
    func update(inProgress: [MediaSummary]?, trending: [MediaSummary]) {
        let key = today
        guard artDay != key, task == nil, let inProgress else { return }
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
    static let headerToTiles: CGFloat = 32

    /// Black over the picture: (opacity, location). A light shade keeps the status bar readable, a little darkens the
    /// middle, and the last stop is solid so the bottom meets the black page.
    static let shade: [(opacity: Double, location: CGFloat)] = [
        (0.35, 0),
        (0, 0.16),
        (0.22, 0.68),
        (0.62, 0.86),
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
