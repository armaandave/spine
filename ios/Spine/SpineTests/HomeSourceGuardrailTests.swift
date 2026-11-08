import XCTest

/// The performance rules of the Home redesign, as scans of the source: what the new folders may not use, and how
/// Home's page is put together. A scan is crude, but it fails the build the day someone adds a live blur to a row that
/// repeats, and the counters in the perf harness (not these tests) prove the rest at run time.
final class HomeSourceGuardrailTests: XCTestCase {
    private static let sources: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("Spine", isDirectory: true)

    /// The folders where rows repeat or move: no live blur, no material, no geometry readers, no AsyncImage and no
    /// ViewThatFits. A width comes from one `onGeometryChange` at a section; the only glass is the tile's Close button.
    private static let guardedFolders = [
        "Features/Home/Feed",
        "Features/Home/Tiles",
        "Features/Home/EpisodeTicks",
        "Core/Design/Art",
        "Core/Design/Meter",
    ]

    private static let banned: [(name: String, pattern: String)] = [
        ("a live blur", #"\.blur\("#),
        ("a material", #"Material\b"#),
        ("a GeometryReader", #"\bGeometryReader\b"#),
        ("AsyncImage", #"\bAsyncImage\b"#),
        ("ViewThatFits", #"\bViewThatFits\b"#),
    ]

    // MARK: Helpers

    private func swiftFiles(in folder: String) throws -> [URL] {
        let root = Self.sources.appendingPathComponent(folder, isDirectory: true)
        let enumerator = try XCTUnwrap(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        return enumerator.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
    }

    private func read(_ relativePath: String) throws -> String {
        try String(contentsOf: Self.sources.appendingPathComponent(relativePath), encoding: .utf8)
    }

    /// The code without its `//` comments, so a comment may say what a file avoids.
    private func code(_ source: String) -> String {
        source.split(separator: "\n", omittingEmptySubsequences: false)
            .map { line -> Substring in
                guard let range = line.range(of: "//") else { return line }
                return line[..<range.lowerBound]
            }
            .joined(separator: "\n")
    }

    /// `body`'s text in a source file: from the first `var body: some View {` to the marker that follows.
    private func homeViewBody() throws -> String {
        let source = try read("Features/Home/HomeView.swift")
        let start = try XCTUnwrap(source.range(of: "var body: some View {"))
        let end = try XCTUnwrap(source.range(of: "// MARK: Actions", range: start.upperBound ..< source.endIndex))
        return String(source[start.upperBound ..< end.lowerBound])
    }

    // MARK: The guarded folders

    func testTheGuardedFoldersExistAndHaveSwiftFiles() throws {
        for folder in Self.guardedFolders {
            XCTAssertFalse(try swiftFiles(in: folder).isEmpty, "\(folder) has no Swift files, so the scan proves nothing")
        }
    }

    func testNoRowThatRepeatsOrMovesUsesALiveBlurAMaterialAGeometryReaderAsyncImageOrViewThatFits() throws {
        for folder in Self.guardedFolders {
            for file in try swiftFiles(in: folder) {
                let text = code(try String(contentsOf: file, encoding: .utf8))
                for (name, pattern) in Self.banned {
                    XCTAssertNil(
                        text.range(of: pattern, options: .regularExpression),
                        "\(folder)/\(file.lastPathComponent) uses \(name)"
                    )
                }
            }
        }
    }

    func testTheNewHomeFilesUseNoLiveBlurMaterialOrGeometryReader() throws {
        for path in ["HomeTop.swift", "HomeFeedRows.swift", "HomeFeedModel.swift"] {
            let text = code(try read("Features/Home/\(path)"))
            for (name, pattern) in Self.banned {
                XCTAssertNil(text.range(of: pattern, options: .regularExpression), "\(path) uses \(name)")
            }
        }
    }

    func testFeedRowsUseNoLiveShadowExceptTheLightHaloAndTheBakedMarks() throws {
        // A live shadow is an offscreen pass per row. The text lift's light halo is the one in a row that repeats
        // (a few texts of the minority of cards on light art); the marks' glow is drawn once into a bitmap.
        var uses: [String] = []
        for file in try swiftFiles(in: "Features/Home/Feed") {
            let text = code(try String(contentsOf: file, encoding: .utf8))
            if text.contains(".shadow(") { uses.append(file.lastPathComponent) }
        }
        XCTAssertEqual(uses.sorted(), ["FeedMarkImages.swift", "FeedTextLift.swift"])
    }

    func testTheOnlyGlassInTheTilesIsTheCloseButtonAndTheOpenPanelsTwoButtons() throws {
        var uses: [String] = []
        for file in try swiftFiles(in: "Features/Home/Tiles") + swiftFiles(in: "Features/Home/Feed") {
            let text = code(try String(contentsOf: file, encoding: .utf8))
            if text.contains(".glassEffect") { uses.append(file.lastPathComponent) }
        }
        XCTAssertEqual(
            uses.sorted(), ["HomeTilesSection.swift", "PanelGlassButtons.swift"],
            "Glass belongs on the tile's Close button and the open In progress panel's +N and Update"
        )
    }

    func testThePanelsHostNeverReadsTheScrollPositionAndTheCarouselOnlyWritesIt() throws {
        // The host runs when the model changes; reading the scroll position there would run it on every frame.
        let host = try read("Features/Home/Tiles/Panel/InProgressMorphPanel.swift")
        let hostPart = try XCTUnwrap(host.components(separatedBy: "// MARK: - Body").first)
        XCTAssertGreaterThan(host.count, hostPart.count, "The host and the body are separated by a MARK")
        XCTAssertFalse(code(hostPart).contains("scrollPosition"), "InProgressMorphPanel (the host) reads the scroll position")
        // Nothing but the panel's body reads it.
        for file in try swiftFiles(in: "Features/Home") {
            let name = file.lastPathComponent
            guard !["InProgressMorphPanel.swift", "TileCarousel.swift", "HomeTilesState.swift"].contains(name) else {
                continue
            }
            XCTAssertFalse(code(try String(contentsOf: file, encoding: .utf8)).contains("state.scrollPosition"), name)
        }
        let carousel = code(try read("Features/Home/Tiles/TileCarousel.swift"))
        XCTAssertTrue(carousel.contains("state.scrollPosition = position"), "The carousel assigns the position")
        for use in carousel.components(separatedBy: "state.scrollPosition").dropFirst() {
            XCTAssertTrue(use.hasPrefix(" = position"), "The carousel only assigns the scroll position: \(use.prefix(20))")
        }
    }

    func testThePanelHasNoOffscreenPassesOfItsOwn() throws {
        for file in try swiftFiles(in: "Features/Home/Tiles/Panel") {
            let text = code(try String(contentsOf: file, encoding: .utf8))
            for token in [".mask(", ".compositingGroup", ".drawingGroup", "TimelineView", ".shadow(", ".subtracting("] {
                XCTAssertFalse(text.contains(token), "\(file.lastPathComponent) uses \(token)")
            }
        }
    }

    func testTheTrendingRailIsTheSharedActionRail() throws {
        // The rail's glass lives in the shared file, outside the guarded folders; the Tiles folder only calls it, from
        // the one host view, and the shared file has exactly one glass surface.
        var callers: [String] = []
        for file in try swiftFiles(in: "Features/Home/Tiles") {
            let text = code(try String(contentsOf: file, encoding: .utf8))
            if text.contains("ActionRail(") { callers.append(file.lastPathComponent) }
        }
        XCTAssertEqual(callers, ["TrendingRailHost.swift"])
        let rail = code(try read("Features/MediaDetail/MediaActionRail.swift"))
        XCTAssertEqual(rail.components(separatedBy: ".glassEffect(").count - 1, 1, "One glass surface for the whole rail")
    }

    func testTheMediaPageNoLongerDefinesTheRail() throws {
        let page = code(try read("Features/MediaDetail/MediaDetailView.swift"))
        XCTAssertFalse(page.contains("struct ActionRail"))
        XCTAssertFalse(page.contains("struct StarRatingPill"))
        XCTAssertTrue(page.contains("ActionRail("), "The page still draws it")
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: Self.sources.appendingPathComponent("Features/Home/TrendingPlanWriter.swift").path),
            "Planning goes through the title's page model now"
        )
    }

    func testTheTrendingPanelDrawsNoGlassBlurOrShadowOfItsOwn() throws {
        for path in ["Features/Home/Tiles/TrendingPanel.swift", "Features/Home/Tiles/TrendingRailHost.swift"] {
            let text = code(try read(path))
            XCTAssertFalse(text.contains(".glassEffect"), path)
            XCTAssertFalse(text.contains(".shadow("), path)
        }
    }

    func testTheTileSectionDrawsNoGradient() throws {
        // A gradient stroke or fill on a tile is rasterized by Core Graphics on the main thread every frame the tile's
        // size changes (the open and the close). The outline is solid colors.
        let text = code(try read("Features/Home/Tiles/HomeTilesSection.swift"))
        XCTAssertFalse(text.contains("Gradient"), "HomeTilesSection draws a gradient")
    }

    // MARK: The page

    func testHomeHasExactlyOneLazyStackAndNoSecondOneInItsParts() throws {
        let home = code(try read("Features/Home/HomeView.swift"))
        XCTAssertEqual(home.components(separatedBy: "LazyVStack").count - 1, 1, "Home has one LazyVStack")
        for path in ["HomeTop.swift", "HomeFeedRows.swift"] {
            XCTAssertFalse(code(try read("Features/Home/\(path)")).contains("LazyVStack"), "\(path) nests a lazy stack")
        }
        for folder in ["Features/Home/Feed", "Features/Home/Tiles"] {
            for file in try swiftFiles(in: folder) {
                let text = code(try String(contentsOf: file, encoding: .utf8))
                XCTAssertFalse(text.contains("LazyVStack"), "\(file.lastPathComponent) nests a lazy stack")
                XCTAssertFalse(text.contains("LazyHStack"), "\(file.lastPathComponent) nests a lazy stack")
            }
        }
    }

    func testRowsAreOneFlatListWithoutSectionsOrPinnedHeaders() throws {
        let rows = code(try read("Features/Home/HomeFeedRows.swift"))
        XCTAssertFalse(rows.contains("Section"), "The cards are siblings, not sections")
        XCTAssertFalse(rows.contains("pinnedViews"), "Nothing in the feed is pinned")
        XCTAssertTrue(rows.contains(".equatable()"), "A card skips drawing when its model is equal")
        XCTAssertTrue(rows.contains(".prefetchingFeedCards("), "Cards prefetch the art that follows them")
        XCTAssertTrue(rows.contains("frame(height: Self.cardRowHeight"), "Rows have fixed heights")
        let home = code(try read("Features/Home/HomeView.swift"))
        XCTAssertFalse(home.contains("pinnedViews"))
    }

    func testHomesBodyReadsNoTileTodayTrendingGlowOrFeedData() throws {
        let body = try homeViewBodyWithoutComments()
        for property in [
            "inProgressItems", "viewModel.tiles.", "viewModel.tilesState.", "todayParts", "todayStore", "baselines",
            "trending", "plannedIDs", "busyItemIDs", "entries", "feed.items", "activityItems", "viewModel.profile",
            "viewModel.isLoading", "errorMessage", "isLoadingInProgress", "writeFailure",
        ] {
            XCTAssertFalse(body.contains(property), "HomeView.body reads \(property)")
        }
        // The scroll offset is written from the scroll view's action and read nowhere on the page.
        let uses = body.components(separatedBy: "scrollOffset").dropFirst()
        XCTAssertEqual(uses.count, 1)
        XCTAssertTrue(uses.allSatisfy { $0.hasPrefix(" = offset") }, "HomeView.body only writes the scroll offset")
    }

    func testHomesBodyStartsWithItsCounter() throws {
        let body = try homeViewBody()
        let first = body.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty }
        XCTAssertEqual(first, "let _ = PerfCounters.bump(.homeBody)")
    }

    func testTheOtherCountersAreTheFirstStatementOfTheirBodies() throws {
        let expectations: [(path: String, marker: String, counter: String)] = [
            ("Features/Home/Feed/FeedCardView.swift", "var body: some View {", "feedCardBody"),
            ("Features/Home/Tiles/HomeTilesSection.swift", "var body: some View {", "tilesBody"),
            ("Features/Home/Tiles/HomeGlow.swift", "var body: some View {", "glowBody"),
            (
                "Features/Home/Tiles/Panel/InProgressMorphPanel.swift",
                "set { vector = newValue }\n    }\n\n    var body: some View {",
                "panelBody"
            ),
        ]
        for (path, marker, counter) in expectations {
            let source = try read(path)
            let start = try XCTUnwrap(source.range(of: marker), path)
            let first = source[start.upperBound...].split(separator: "\n")
                .map { $0.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty }
            XCTAssertEqual(first, "let _ = PerfCounters.bump(.\(counter))", path)
        }
    }

    func testTheGreyPageAndTheLogBackdropWashAreGoneFromHome() throws {
        for path in ["HomeView.swift", "HomeTop.swift", "HomeFeedRows.swift"] {
            let text = code(try read("Features/Home/\(path)"))
            XCTAssertFalse(text.contains("LogBackdrop"), "\(path) still has the backdrop wash")
            XCTAssertFalse(text.contains("SpinePalette.pageBackground"), "\(path) still has the grey page")
            XCTAssertFalse(text.contains("compositingGroup"), "\(path) flattens a group")
        }
        XCTAssertTrue(code(try read("Features/Home/HomeView.swift")).contains("HomeDesign.Palette.pageBackground"))
    }

    func testTheOldRailAndRowsAreGone() throws {
        let removed = [
            "InProgressCard(", "InProgressBar(", "InProgressCardButtonStyle", "ActivityPostRow", "ActivityFeedSkeleton",
            "HomeInProgressSkeleton", "HomeArtworkAtmosphere(", "HomeScrollingAtmosphere", "HomeBackdropMotion",
        ]
        let files = try swiftFiles(in: "Features")
        for file in files {
            let text = code(try String(contentsOf: file, encoding: .utf8))
            for token in removed {
                XCTAssertFalse(text.contains(token), "\(file.lastPathComponent) still has \(token)")
            }
        }
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: Self.sources.appendingPathComponent("Features/Home/ActivityPostRow.swift").path
        ))
    }

    // MARK: The identifiers the harness and the UI tests look for

    func testTheAccessibilityIdentifiersThePerfHarnessReadsExist() throws {
        XCTAssertTrue(try read("Features/Home/Tiles/TileDetailPanel.swift").contains("\"in-progress.update\""))
        XCTAssertTrue(try read("Features/Home/Tiles/TileDetailPanel.swift").contains("\"in-progress.quick\""))
        let tiles = try read("Features/Home/Tiles/HomeTilesSection.swift")
        XCTAssertTrue(tiles.contains("tile.identifier"), "Each tile carries its home.tile.* identifier")
        XCTAssertTrue(try read("Features/Home/Tiles/HomeTilesState.swift").contains("\"home.tile.in-progress\""))
        XCTAssertTrue(try read("Features/Home/Tiles/HomeTilesState.swift").contains("\"home.tile.trending\""))
        XCTAssertTrue(try read("Features/Social/ActivityPostViews.swift").contains("\"post.like\""))
        // A card's label names its title and its comment count.
        let card = try read("Features/Home/Feed/FeedCardView.swift")
        XCTAssertTrue(card.contains(".accessibilityLabel(Self.accessibilityText(for: model))"))
        XCTAssertTrue(card.contains(".accessibilityAddTraits(.isButton)"))
    }

    private func homeViewBodyWithoutComments() throws -> String {
        code(try homeViewBody())
    }
}
