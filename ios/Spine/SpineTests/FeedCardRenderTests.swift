import Nuke
import SwiftUI
import UIKit
import XCTest
@testable import Spine

/// Renders feed cards with `ImageRenderer` at the card's real width and checks them. No art loads in a synchronous
/// render, so this shows layout, truncation, ink and meters over each card's base color, not the baked art. The
/// PNGs land in the Mac's `~/Library/Caches/home-redesign-gallery/feed/`, since the scratch simulator is deleted when
/// a run ends.
@MainActor
final class FeedCardRenderTests: XCTestCase {
    private static let cardWidth: CGFloat = 358
    private static let cardHeight: CGFloat = 115

    private struct Fixture {
        let name: String
        let caption: String
        let model: FeedCardModel
    }

    // MARK: Fixtures

    private let sinnersReview = "saw it again in IMAX and the juke joint scene still levitates. the cut from the guitar to a "
        + "whole century of music in one room is the best thing a movie did this year."

    private func card(
        _ item: ActivityItem,
        standing: FeedStanding? = nil,
        planning: PlanningDay? = nil
    ) -> FeedCardModel {
        // Every kind of day the cards show, by item id; the long name (3) gets the longest day.
        let days = ["Today", "Yesterday", "Friday", "Thursday", "Oct 21", "Oct 21, 2025", "Tuesday"]
        let day = item.id == 3 ? "Oct 21, 2025" : days[abs(item.id) % days.count]
        return FeedCardPresenter.model(
            for: item, standing: standing, isNewestOfTitle: true, planning: planning, dayLabel: day
        )
    }

    /// Art the gallery with pictures makes: "name|accent" to a file. Unset, a card's art is a web address that
    /// never loads.
    private var artFiles: [String: URL] = [:]
    private var requestedArt: Set<String> = []

    private func artURL(_ name: String, _ accent: String) -> String {
        requestedArt.insert("\(name)|\(accent)")
        return artFiles["\(name)|\(accent)"]?.absoluteString ?? "https://p/\(name).jpg"
    }

    private func art(_ name: String, _ accent: String, key: String = "poster_url") -> String {
        "\"\(key)\": \"\(artURL(name, accent))\", \"poster_accent_color\": \"\(accent)\""
    }

    private func fixtures() throws -> [Fixture] {
        var list: [Fixture] = []
        func add(_ name: String, _ caption: String, _ model: FeedCardModel) {
            list.append(Fixture(name: name, caption: caption, model: model))
        }

        // Logs: only stars, heart and the rewatch glyph under the title.
        add("01-film-rewatch-long-review", "FILM · REWATCH, 5 STARS, LOVED, LONG REVIEW (DARK)", card(try FeedTestItems.log(
            id: 1, rating: "5", liked: true, rewatch: true, review: sinnersReview, title: "Sinners",
            mediaFields: art("sinners", "873324"), username: "jules"
        )))
        add("02-film-spoiler-chip", "FILM · SPOILER REVIEW, RATED, LOVED, REWATCH", card(try FeedTestItems.log(
            id: 2, rating: "3.5", liked: true, rewatch: true, review: "he was dead the whole time", spoilers: true,
            title: "Weapons", mediaFields: art("weapons", "1F5A6E"),
            username: "noor"
        )))
        add("03-long-name-long-title", "LONG NAME, LONG TITLE, RATED, LOVED, REVIEW", card(try FeedTestItems.log(
            id: 3, rating: "4.5", liked: true,
            review: "the hills chase alone is worth the ticket. sean penn is doing something i have never seen a human face do on purpose.",
            title: "One Battle After Another",
            mediaFields: art("obaa", "8A4D2B"),
            username: "maximiliano.rodriguez"
        )))
        add("04-film-heart-review-no-stars", "FILM · NO STARS, A HEART AND A REVIEW", card(try FeedTestItems.log(
            id: 4, liked: true, review: "nothing happens and its the best thing ive seen all year. komorebi. going to go look at a tree now.",
            title: "Perfect Days", mediaFields: art("pd", "3A4A8C"),
            username: "theo"
        )))
        add("05-album-square-review", "ALBUM · SQUARE ART, RELISTEN, RATED, LOVED, REVIEW", card(try FeedTestItems.log(
            id: 5, rating: "4.5", liked: true, rewatch: true,
            review: "kill bill at 1am with the windows down. still undefeated three years later.", mediaType: "music",
            title: "SOS", mediaFields: art("sos", "2F7BB0"),
            username: "lena"
        )))
        add("06-album-square-no-review", "ALBUM · SQUARE ART, RATED, LOVED, NO REVIEW", card(try FeedTestItems.log(
            id: 6, rating: "4", liked: true, mediaType: "music", title: "Hit Me Hard and Soft",
            mediaFields: art("hmhas", "2B6C9E"), username: "maya"
        )))
        add("07-tv-episode-still", "TV · ONE EPISODE, THE STILL ASPECT-FILLED INTO THE POSTER SLOT", card(try FeedTestItems.log(
            id: 7, liked: true, review: "chikhai bardo is the best hour of tv this year and i will not be taking questions.",
            mediaType: "episode", season: 2, episode: 7, title: "Chikhai Bardo",
            mediaFields: "\"show_title\": \"Severance\", " + art("still", "2A6F8C", key: "backdrop_url"),
            username: "maya"
        )))
        add("08-tv-season-log", "TV · SEASON LOG, RATED, LOVED, REVIEW", card(try FeedTestItems.log(
            id: 8, rating: "5", liked: true, review: "the finale earned every one of those three years.",
            mediaType: "season", season: 2, title: "Severance",
            mediaFields: "\"show_title\": \"Severance\", " + art("sev", "2A6F8C"),
            username: "theo"
        )))
        add("09-anime-light-ink", "ANIME · REWATCH, RATED, LOVED, REVIEW (LIGHT INK)", card(try FeedTestItems.log(
            id: 9, rating: "5", liked: true, rewatch: true,
            review: "second time through and the flower field still gets me every single time.",
            mediaType: "anime", source: "mal", title: "Frieren",
            mediaFields: art("frieren", "9CC7CF"), username: "maya"
        )))
        add("10-two-line-review-no-marks", "NO MARKS · NO HEADLINE ROW, A TWO-LINE REVIEW STARTS HIGHER", card(try FeedTestItems.log(
            id: 10, review: "a long review with no stars or heart at all, so the card has no headline row and the text climbs up the card",
            mediaType: "book", source: "hardcover", title: "Intermezzo",
            mediaFields: art("int", "6B5A2A"), username: "noor"
        )))
        add("11-rewatch-only", "REWATCH ALONE (A REREAD) · NO REVIEW", card(try FeedTestItems.log(
            id: 11, rewatch: true, mediaType: "book", source: "hardcover", title: "Tomorrow, and Tomorrow, and Tomorrow",
            mediaFields: art("ttt", "C9C5A3"), username: "noor"
        )))

        // Progress days with meters.
        add("12-window-paused-light", "TV · 24 EPISODES, THEN PAUSED · WINDOW AND SERIES LINE (LIGHT)", card(
            try FeedTestItems.day(
                id: 12, label: "24 episodes", moves: ["paused"], unit: "minutes", mediaType: "tv", source: "tmdb",
                title: "One Piece",
                mediaFields: art("op", "C9D6DE"), username: "theo"
            ),
            standing: FeedStanding(unit: .episodes, from: 1065, to: 1089, total: 1150, whereText: "E1,066 to E1,089")
        ))
        add("13-bar-pages-paused-light", "BOOK · 86 PAGES, THEN PAUSED · BAR (LIGHT)", card(
            try FeedTestItems.day(
                id: 13, label: "86 pages", moves: ["paused"], unit: "pages", title: "Intermezzo",
                mediaFields: art("int", "D8C27A"), username: "noor"
            ),
            standing: FeedStanding(unit: .pages, from: 126, to: 212, total: 448, whereText: "p. 212")
        ))
        add("14-bar-pages-tail", "BOOK · 86 PAGES AND THE REAL WHERE (NO PILL)", card(
            try FeedTestItems.day(
                id: 14, label: "86 pages", unit: "pages", title: "Intermezzo",
                mediaFields: art("int", "4A3A12"), username: "noor"
            ),
            standing: FeedStanding(unit: .pages, from: 126, to: 212, total: 448, whereText: "p. 212")
        ))
        add("15-bar-percent-time", "GAME · 2H 30M IN A DAY · PERCENT BAR", card(
            try FeedTestItems.day(
                id: 15, label: "2h 30m", unit: "minutes", mediaType: "game", source: "igdb",
                title: "Clair Obscur: Expedition 33",
                mediaFields: art("e33", "6B5A3A"), username: "lena"
            ),
            standing: FeedStanding(unit: .percent, from: 61, to: 64, total: nil, whereText: "now 64%")
        ))
        add("16-ticks-lit", "TV · 3 EPISODES TODAY · TICKS, TODAY'S LIT", card(
            try FeedTestItems.day(
                id: 16, label: "3 episodes", unit: "minutes", mediaType: "tv", source: "tmdb", title: "Pluribus",
                mediaFields: art("plu", "2B4A6A"), username: "maya"
            ),
            standing: FeedStanding(unit: .episodes, from: 4, to: 7, total: 10, whereText: "7 of 10 episodes")
        ))
        add("17-status-stopped-bar", "BOOK · STOPPED (A STATUS, NOTHING LIT)", card(
            try FeedTestItems.day(
                id: 17, moves: ["stopped"], unit: "pages", title: "James",
                mediaFields: art("james", "5A4710"), username: "sam"
            ),
            standing: FeedStanding(unit: .pages, from: 140, to: 140, total: 303, whereText: "at p. 140")
        ))
        add("18-status-picked-back-up-light", "TV · PICKED BACK UP (A STATUS, NOTHING LIT) · TICKS (LIGHT)", card(
            try FeedTestItems.day(
                id: 18, moves: ["picked_back_up"], unit: "minutes", mediaType: "tv", source: "tmdb", title: "Pluribus",
                mediaFields: art("plu", "D9C23A"), username: "maya"
            ),
            standing: FeedStanding(unit: .episodes, from: 5, to: 5, total: 9, whereText: "after E5")
        ))
        add("19-status-no-meter", "STATUS WITH NO STANDING · JUST THE WORDS", card(try FeedTestItems.day(
            id: 19, moves: ["paused"], unit: "pages", title: "James",
            mediaFields: art("james", "5A4710"), username: "sam"
        )))
        add("20-started", "BOOK · STARTED", card(try FeedTestItems.day(
            id: 20, label: "Started", unit: "pages", title: "Project Hail Mary",
            mediaFields: art("phm", "6A5A20"), username: "jules"
        )))
        add("21-amount-no-standing", "PROGRESS WITH NO STANDING · AMOUNT ONLY, NO METER", card(try FeedTestItems.day(
            id: 21, label: "4 chapters", unit: "chapters", mediaType: "manga", source: "mangadex", title: "Berserk",
            mediaFields: art("berserk", "3A3A3A"), username: "sam"
        )))

        // Planning.
        let planningNames = [
            ("Frankenstein", artURL("f", "5A3A2A")), ("Bugonia", artURL("b", "2A4A5A")),
            ("Marty Supreme", artURL("m", "6A2A4A")), ("Hamnet", artURL("h", "3A5A3A")),
            ("Weapons", artURL("w", "1F5A6E")), ("One Battle After Another", artURL("o", "8A4D2B")),
        ]
        let planningFace = try FeedTestItems.planningRow(
            id: -30, count: 6, mediaType: "movie",
            faceFields: art("f", "5A3A2A")
        )
        add("22-planning-one-face", "PLANNING · SIX FILMS, BEFORE THE TITLES LOAD", card(planningFace))
        add("23-planning-fan", "PLANNING · SIX FILMS, THREE POSTERS FANNED AND THE NAMES", card(
            planningFace, planning: try FeedTestItems.planningDay(titles: planningNames, count: 6)
        ))
        let albums = try FeedTestItems.planningRow(
            id: -31, count: 3, mediaType: "music",
            faceFields: art("a", "2F6F5A")
        )
        add("24-planning-albums-square", "PLANNING · THREE ALBUMS, SQUARE FAN", card(
            albums,
            planning: try FeedTestItems.planningDay(
                titles: [("SOS", artURL("a", "2F6F5A")), ("Charm", artURL("b2", "6A4A2A")), ("Blonde", artURL("c", "4A2A6A"))],
                count: 3
            )
        ))

        // Counts.
        add("25-zero-counts", "ZERO LIKES, ZERO COMMENTS · BOTH SHOW A 0", card(try FeedTestItems.item(
            id: 25, objectID: 250, objectFields: "\"rating\": \"3\", \"liked\": false, \"is_rewatch\": false",
            username: "ava", title: "Past Lives", mediaFields: art("pl", "7A3A3A"),
            likes: 0, comments: 0
        )))
        add("26-big-counts", "1,204 LIKES, 38 COMMENTS, A PICTURE FOR AN AVATAR", card(try FeedTestItems.item(
            id: 26, objectID: 260, objectFields: "\"rating\": \"5\", \"liked\": true, \"is_rewatch\": false",
            username: "ava", avatar: artURL("avatar", "7A5AAA"), title: "Past Lives", mediaFields: art("pl", "7A3A3A"),
            likes: 1204, comments: 38
        )))
        return list
    }

    // MARK: Rendering

    private func render(_ model: FeedCardModel, scale: CGFloat = 3) throws -> UIImage {
        let view = FeedCardView(model: model, onOpen: { _ in })
            .frame(width: Self.cardWidth, height: Self.cardHeight)
            .background(Color.black)
            .environment(\.colorScheme, .dark)
        let renderer = ImageRenderer(content: view)
        renderer.scale = scale
        return try XCTUnwrap(renderer.uiImage)
    }

    private func galleryFolder() -> URL? {
        guard let home = ProcessInfo.processInfo.environment["SIMULATOR_HOST_HOME"] else { return nil }
        return URL(fileURLWithPath: home).appendingPathComponent("Library/Caches/home-redesign-gallery/feed", isDirectory: true)
    }

    // MARK: Tests

    func testEveryFixtureRendersAtTheCardSize() throws {
        let fixtures = try fixtures()
        XCTAssertGreaterThanOrEqual(fixtures.count, 24)
        for fixture in fixtures {
            let image = try render(fixture.model)
            XCTAssertEqual(image.size.width, Self.cardWidth, "\(fixture.name)")
            XCTAssertEqual(image.size.height, Self.cardHeight, "\(fixture.name)")
            XCTAssertEqual(image.scale, 3, "\(fixture.name)")
            XCTAssertEqual(image.cgImage?.width, 1074, "\(fixture.name)")
            XCTAssertEqual(image.cgImage?.height, 345, "\(fixture.name)")
        }
    }

    func testTheFixturesCoverBothInksAndEveryMeterShape() throws {
        let fixtures = try fixtures().map(\.model)
        XCTAssertTrue(fixtures.contains { $0.art.guessInk == .light })
        XCTAssertTrue(fixtures.contains { $0.art.guessInk == .dark })
        XCTAssertTrue(fixtures.contains { $0.art.isSquare })
        var kinds = Set<String>()
        for model in fixtures {
            switch model.meter?.kind {
            case .ticks: kinds.insert("ticks")
            case .window: kinds.insert("window")
            case .bar: kinds.insert("bar")
            case nil: break
            }
        }
        XCTAssertEqual(kinds, ["ticks", "window", "bar"])
        XCTAssertTrue(fixtures.contains { $0.details == [.spoilers] })
        XCTAssertTrue(fixtures.contains { $0.details.contains { if case .review(_, 2) = $0 { true } else { false } } })
        XCTAssertTrue(fixtures.contains { $0.kind == .status })
        XCTAssertTrue(fixtures.contains { $0.kind == .start })
        XCTAssertTrue(fixtures.contains { $0.kind == .planning && $0.art.planningPosterURLs.isEmpty })
        XCTAssertTrue(fixtures.contains { $0.kind == .planning && !$0.art.planningPosterURLs.isEmpty })
    }

    func testNoFixtureTextReachesTheMeterOrTheCounts() throws {
        for fixture in try fixtures() {
            let model = fixture.model
            let bottom = FeedDetailPlanner.bottom(of: model.details, hasHeadline: model.headline != nil)
            let limit = FeedDetailPlanner.rowsLimit(hasMeter: model.meter != nil)
            XCTAssertLessThanOrEqual(bottom, limit, "\(fixture.name) text ends at \(bottom), past \(limit)")
            // The meter's top is where the view puts it; the last row may share the counts' band (it stops short of
            // them sideways) but never goes below the counts' bottom.
            if let meter = model.meter {
                XCTAssertLessThanOrEqual(bottom, FeedCardView.meterTop(for: meter.kind) - 2, "\(fixture.name)")
            }
            XCTAssertLessThanOrEqual(bottom, 115 - 12, "\(fixture.name)")
        }
    }

    func testTheMeterSitsLevelWithTheCounts() {
        // Ticks and bars end 15 above the bottom (92 to 100); a window starts higher so its series line ends 17 above.
        let ticks = ProgressMeterModel.Kind.ticks(count: 10)
        let bar = ProgressMeterModel.Kind.bar(total: 400)
        let window = ProgressMeterModel.Kind.window(first: 141, span: 20, seriesTotal: 1100)
        XCTAssertEqual(FeedCardView.meterTop(for: ticks), 92)
        XCTAssertEqual(FeedCardView.meterTop(for: bar), 92)
        XCTAssertEqual(FeedCardView.meterTop(for: ticks) + ProgressMeterMetrics.tickHeight, 115 - 15)
        XCTAssertEqual(
            115 - (FeedCardView.meterTop(for: window) + ProgressMeterMetrics.tickHeight + ProgressMeterMetrics.lineOffset
                + ProgressMeterMetrics.lineHeight),
            HomeDesign.Metric.meterLineBottom, accuracy: 0.001
        )
        // The tallest meter still clears the rows' limit with a meter.
        XCTAssertGreaterThanOrEqual(FeedCardView.meterTop(for: window) - 2, FeedDetailPlanner.rowsLimit(hasMeter: true))
    }

    func testTheCountsReserveFollowsTheirDigits() {
        XCTAssertEqual(FeedCardView.countsReserve(for: nil), 0)
        XCTAssertEqual(FeedCardView.countsReserve(for: PostEngagement(hasLiked: false, likeCount: 0, commentCount: 0)), 68)
        XCTAssertEqual(FeedCardView.countsReserve(for: PostEngagement(hasLiked: true, likeCount: 12, commentCount: 3)), 76)
    }

    func testTwoEqualModelsCompareEqual() throws {
        let first = try fixtures()
        let second = try fixtures()
        for (a, b) in zip(first, second) {
            XCTAssertEqual(a.model, b.model, a.name)
            XCTAssertEqual(
                FeedCardView(model: a.model, onOpen: { _ in }),
                FeedCardView(model: b.model, onOpen: { _ in }),
                a.name
            )
        }
        let other = try FeedTestItems.item(
            id: 26, objectID: 260, objectFields: "\"rating\": \"5\", \"liked\": true, \"is_rewatch\": false",
            username: "ava", avatar: artURL("avatar", "7A5AAA"), title: "Past Lives", mediaFields: art("pl", "7A3A3A"),
            likes: 1205, comments: 38
        )
        XCTAssertNotEqual(first[25].model, card(other), "A like-count change is a different card")
        XCTAssertNotEqual(
            FeedCardView(model: first[25].model, onOpen: { _ in }),
            FeedCardView(model: card(other), onOpen: { _ in })
        )
    }

    func testTheCombinedLabelHasTheTitleAndTheComments() throws {
        for fixture in try fixtures() {
            let label = FeedCardView.accessibilityText(for: fixture.model)
            XCTAssertTrue(label.contains(fixture.model.title), "\(fixture.name): \(label)")
            XCTAssertTrue(label.contains(fixture.model.username), "\(fixture.name): \(label)")
            if fixture.model.engagement != nil {
                XCTAssertTrue(label.contains("comment"), "\(fixture.name): \(label)")
            }
        }
        let label = FeedCardView.accessibilityText(for: try fixtures()[11].model)
        XCTAssertTrue(label.contains("Episode 1089 of 1150"), "The meter's place is in the label: \(label)")
    }

    func testAZeroCountShowsItsNumberNextToItsIcon() throws {
        // The mock draws "0" beside the heart and the bubble; the card never shows a bare icon.
        XCTAssertEqual(FeedCardView.countText(0), "0")
        XCTAssertEqual(FeedCardView.countText(38), "38")
        let zero = try XCTUnwrap(try fixtures().first { $0.name == "25-zero-counts" }?.model.engagement)
        XCTAssertEqual(zero.likeCount, 0)
        XCTAssertEqual(zero.commentCount, 0)
    }

    func testSkeletonsHaveTheirFixedSize() throws {
        let skeleton = ImageRenderer(content: FeedCardSkeleton().frame(width: Self.cardWidth))
        skeleton.scale = 3
        XCTAssertEqual(try XCTUnwrap(skeleton.uiImage).size.height, Self.cardHeight)
    }

    /// Text is a fixed size and every row has a fixed height; a card never grows or reflows with Dynamic Type, and the
    /// clamp (`HomeDesign.Text.maxCategory`) holds even if a font did scale.
    func testDynamicTypeNeverReflowsACard() throws {
        let all = try fixtures()
        let names = ["01-film-rewatch-long-review", "12-window-paused-light", "23-planning-fan"]
        let chosen = all.filter { names.contains($0.name) }
        XCTAssertEqual(chosen.count, names.count)
        for fixture in chosen {
            func data(_ size: DynamicTypeSize) throws -> Data {
                let view = FeedCardView(model: fixture.model, onOpen: { _ in })
                    .frame(width: Self.cardWidth, height: Self.cardHeight)
                    .background(Color.black)
                    .environment(\.colorScheme, .dark)
                    .environment(\.dynamicTypeSize, size)
                let renderer = ImageRenderer(content: view)
                renderer.scale = 2
                return try XCTUnwrap(renderer.uiImage?.pngData())
            }
            let atClamp = try data(HomeDesign.Text.maxCategory)
            XCTAssertEqual(try data(.accessibility5), atClamp, "\(fixture.name) reflows past the clamp")
            if let folder = galleryFolder() {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                try atClamp.write(to: folder.appendingPathComponent("dynamic-type-\(fixture.name).png"))
            }
        }
    }

    func testTheGalleryIsWritten() throws {
        let fixtures = try fixtures()
        guard let folder = galleryFolder() else {
            throw XCTSkip("Not running on a simulator: no host home to write the gallery to")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var images: [(Fixture, UIImage)] = []
        for fixture in fixtures {
            let image = try render(fixture.model)
            let data = try XCTUnwrap(image.pngData())
            try data.write(to: folder.appendingPathComponent("\(fixture.name).png"))
            images.append((fixture, image))
        }
        // Contact sheets: eight cards each, two across, with the caption above each.
        var sheets: [URL] = []
        for (index, start) in stride(from: 0, to: images.count, by: 8).enumerated() {
            let sheet = contactSheet(Array(images[start ..< min(start + 8, images.count)]))
            let url = folder.appendingPathComponent("sheet-\(index + 1).png")
            try XCTUnwrap(sheet.pngData()).write(to: url)
            sheets.append(url)
        }
        print("FEED GALLERY: \(folder.path)")
        for sheet in sheets { print("FEED GALLERY SHEET: \(sheet.path)") }
    }

    /// The same cards with pictures, for looking at: made-up posters are written to files and loaded through the
    /// app's own image pipeline, so the baked art, its tone and the measured ink are all in the picture. The cards
    /// are hosted in a window (`ImageRenderer` never loads images), four to a sheet.
    func testTheGalleryWithArtIsWritten() async throws {
        guard let folder = galleryFolder() else {
            throw XCTSkip("Not running on a simulator: no host home to write the gallery to")
        }
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first else {
            throw XCTSkip("No window scene to host the cards in")
        }
        _ = try fixtures()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("feed-gallery-art", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        for key in requestedArt {
            let parts = key.split(separator: "|").map(String.init)
            let file = directory.appendingPathComponent("\(parts[0])-\(parts[1]).png")
            try XCTUnwrap(Self.syntheticPoster(accentHex: parts[1])).write(to: file)
            artFiles[key] = file
        }
        let withArt = try fixtures()
        let artFolder = folder.appendingPathComponent("art", isDirectory: true)
        try FileManager.default.createDirectory(at: artFolder, withIntermediateDirectories: true)
        for (index, start) in stride(from: 0, to: withArt.count, by: 4).enumerated() {
            let chunk = withArt[start ..< min(start + 4, withArt.count)].map { ($0.caption, $0.model) }
            // What the views load must be exactly what the prefetcher asks for: these requests are the prefetcher's,
            // and the cards fill the same cache entries.
            let expected = chunk.map { entry in
                (caption: entry.0, requests: [
                    FeedCardArt.posterRequest(for: entry.1.art),
                    FeedCardArt.backgroundRequest(for: entry.1.art),
                    FeedCardArt.avatarRequest(urlString: entry.1.avatarURL),
                ].compactMap { $0 })
            }
            let image = try await hostedSheet(chunk, in: scene, waitingFor: expected.flatMap(\.requests))
            let file = artFolder.appendingPathComponent("sheet-\(index + 1).png")
            try XCTUnwrap(image.pngData()).write(to: file)
            print("FEED GALLERY ART SHEET: \(file.path)")

            for entry in expected {
                for request in entry.requests {
                    XCTAssertNotNil(
                        ImagePipeline.shared.cache[request],
                        "\(entry.caption): the card never loaded \(request.url?.lastPathComponent ?? "?")"
                    )
                }
            }
        }
    }

    private struct GallerySheet: View {
        let cards: [(caption: String, model: FeedCardModel)]

        var body: some View {
            // In a navigation stack, as on Home, so the author link is live and in its own color.
            NavigationStack {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(cards.indices, id: \.self) { index in
                        VStack(alignment: .leading, spacing: 0) {
                            Text(cards[index].caption)
                                .font(.system(size: 8.5, weight: .bold, design: .monospaced))
                                .foregroundStyle(Color.white.opacity(0.8))
                                .frame(height: 16, alignment: .leading)
                            FeedCardView(model: cards[index].model, onOpen: { _ in })
                                .frame(width: FeedCardRenderTests.cardWidth, height: FeedCardRenderTests.cardHeight)
                        }
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(Color.black)
                .toolbar(.hidden, for: .navigationBar)
                .ignoresSafeArea()
            }
            .ignoresSafeArea()
        }
    }

    private func hostedSheet(
        _ cards: [(String, FeedCardModel)],
        in scene: UIWindowScene,
        waitingFor requests: [ImageRequest]
    ) async throws -> UIImage {
        let host = UIHostingController(rootView: GallerySheet(cards: cards.map { (caption: $0.0, model: $0.1) }))
        host.view.backgroundColor = .black
        let window = UIWindow(windowScene: scene)
        window.windowLevel = .alert + 1
        window.overrideUserInterfaceStyle = .dark
        window.rootViewController = host
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        // Until every picture has loaded and baked (the first bake of a run warms up Core Image and can take
        // seconds), then a moment for the fade-in and the ink measurement to settle.
        let deadline = Date().addingTimeInterval(25)
        while Date() < deadline, requests.contains(where: { ImagePipeline.shared.cache[$0] == nil }) {
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        try await Task.sleep(nanoseconds: 1_000_000_000)
        let size = CGSize(
            width: Self.cardWidth + 32,
            height: 16 + CGFloat(cards.count) * (16 + Self.cardHeight + 12)
        )
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
    }

    /// A made-up poster: the accent shaded from light to dark, with a sun and some title bars, so a blur of it has
    /// something to show.
    private static func syntheticPoster(accentHex: String) -> Data? {
        guard let rgb = ArtHex.rgb(accentHex) else { return nil }
        let size = CGSize(width: 300, height: 450)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        func color(_ factor: Double, mixWhite: Double = 0) -> UIColor {
            UIColor(
                red: min(rgb.red * factor + mixWhite, 1),
                green: min(rgb.green * factor + mixWhite, 1),
                blue: min(rgb.blue * factor + mixWhite, 1),
                alpha: 1
            )
        }
        let image = UIGraphicsImageRenderer(size: size, format: format).image { context in
            let cg = context.cgContext
            let colors = [color(1, mixWhite: 0.30).cgColor, color(1).cgColor, color(0.35).cgColor] as CFArray
            if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 0.5, 1]) {
                cg.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: 0, y: size.height), options: [])
            }
            UIColor(white: 1, alpha: 0.85).setFill()
            cg.fillEllipse(in: CGRect(x: 170, y: 90, width: 90, height: 90))
            color(0.2).setFill()
            cg.fill(CGRect(x: 0, y: 300, width: size.width, height: 150))
            UIColor(white: 1, alpha: 0.9).setFill()
            cg.fill(CGRect(x: 28, y: 340, width: 190, height: 14))
            cg.fill(CGRect(x: 28, y: 366, width: 120, height: 8))
        }
        return image.pngData()
    }

    private func contactSheet(_ cards: [(Fixture, UIImage)]) -> UIImage {
        let margin: CGFloat = 16
        let captionHeight: CGFloat = 16
        let rowGap: CGFloat = 12
        let rows = (cards.count + 1) / 2
        let size = CGSize(
            width: margin + (Self.cardWidth + margin) * 2,
            height: margin + CGFloat(rows) * (captionHeight + Self.cardHeight + rowGap)
        )
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        let attributes: [NSAttributedString.Key: Any] = [
            .font: UIFont.monospacedSystemFont(ofSize: 8.5, weight: .bold),
            .foregroundColor: UIColor(white: 1, alpha: 0.8),
        ]
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor.black.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            for (offset, entry) in cards.enumerated() {
                let column = CGFloat(offset % 2)
                let row = CGFloat(offset / 2)
                let x = margin + column * (Self.cardWidth + margin)
                let y = margin + row * (captionHeight + Self.cardHeight + rowGap)
                (entry.0.caption as NSString).draw(
                    in: CGRect(x: x, y: y, width: Self.cardWidth, height: captionHeight), withAttributes: attributes
                )
                entry.1.draw(in: CGRect(x: x, y: y + captionHeight, width: Self.cardWidth, height: Self.cardHeight))
            }
        }
    }
}
