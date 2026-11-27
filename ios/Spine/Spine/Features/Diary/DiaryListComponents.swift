import SwiftUI

enum DiaryMonthDisplayMode {
    case expanded
    case collapsed
}

struct DiaryEntryList<Destination: View>: View {
    let monthSections: [DiaryMonthSection]
    let artworkOverride: DiaryEntryArtworkOverride?
    let onMonthHeaderTap: ((String) -> Void)?
    let loadingMonthID: String?
    @ViewBuilder let destination: (DiaryEntry) -> Destination

    init(
        entries: [DiaryEntry],
        artworkOverride: DiaryEntryArtworkOverride? = nil,
        onMonthHeaderTap: ((String) -> Void)? = nil,
        loadingMonthID: String? = nil,
        @ViewBuilder destination: @escaping (DiaryEntry) -> Destination
    ) {
        self.monthSections = DiaryMonthSection.sections(from: entries)
        self.artworkOverride = artworkOverride
        self.onMonthHeaderTap = onMonthHeaderTap
        self.loadingMonthID = loadingMonthID
        self.destination = destination
    }

    init(
        monthSections: [DiaryMonthSection],
        artworkOverride: DiaryEntryArtworkOverride? = nil,
        onMonthHeaderTap: ((String) -> Void)? = nil,
        loadingMonthID: String? = nil,
        @ViewBuilder destination: @escaping (DiaryEntry) -> Destination
    ) {
        self.monthSections = monthSections
        self.artworkOverride = artworkOverride
        self.onMonthHeaderTap = onMonthHeaderTap
        self.loadingMonthID = loadingMonthID
        self.destination = destination
    }

    var body: some View {
        ForEach(monthSections) { section in
            expandedSection(section)
            if section.id != monthSections.last?.id {
                DiaryMonthGap()
            }
        }
    }

    private func expandedSection(_ section: DiaryMonthSection) -> some View {
        Section {
            // Each row is its own item in the lazy stack, drawn as a slice of the month's card.
            // A whole month in one item gave the stack wildly uneven sizes to estimate, and it
            // could get stuck re-laying out the end of the list.
            ForEach(section.entries) { entry in
                let isFirst = entry.id == section.entries.first?.id
                let isLast = entry.id == section.entries.last?.id
                NavigationLink {
                    destination(entry)
                } label: {
                    DiaryEntryRow(entry: entry, artworkOverride: artworkOverride, showsSeparator: !isLast)
                }
                .buttonStyle(DiaryRowButtonStyle())
                .diaryCard(roundsTop: isFirst, roundsBottom: isLast, underTab: true)
            }
        } header: {
            monthHeader(for: section)
                .id(section.id)
        }
    }

    private func monthHeader(for section: DiaryMonthSection) -> some View {
        DiaryMonthHeader(
            title: section.title,
            isLoading: loadingMonthID == section.id,
            action: headerAction(for: section)
        )
    }

    private func headerAction(for section: DiaryMonthSection) -> (() -> Void)? {
        guard let onMonthHeaderTap else { return nil }
        return { onMonthHeaderTap(section.id) }
    }
}

/// Every month with logs, a card per year: tapping a month opens the diary there.
struct DiaryMonthIndex: View {
    let monthSections: [DiaryMonthSection]
    let onMonthHeaderTap: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(DiaryYearGroup.groups(from: monthSections)) { group in
                if let year = group.year {
                    Text(year)
                        .font(.title3.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.leading, 4)
                        .padding(.bottom, 8)
                        .accessibilityAddTraits(.isHeader)
                }

                VStack(spacing: 0) {
                    ForEach(group.sections) { section in
                        Button {
                            onMonthHeaderTap(section.id)
                        } label: {
                            DiaryMonthIndexRow(
                                section: section,
                                showsSeparator: section.id != group.sections.last?.id
                            )
                        }
                        .buttonStyle(DiaryRowButtonStyle())
                        .accessibilityLabel(section.title)
                        .accessibilityValue(section.entries.count == 1 ? "1 log" : "\(section.entries.count) logs")
                        .accessibilityHint("Shows diary entries for all months")
                        .id(section.id)
                    }
                }
                .diaryCard()
                .padding(.bottom, 22)
            }
        }
    }
}

/// The months of one year in the month index, newest first. Undated logs get a group with no year.
struct DiaryYearGroup: Identifiable {
    let id: String
    var sections: [DiaryMonthSection]

    /// "2026", or "2569 BE" on a Buddhist calendar: the year in each month's title.
    var year: String? { id.isEmpty ? nil : id }

    /// Groups by the year each month's title shows, in the phone's calendar, so a heading never
    /// disagrees with the titles under it, even where that calendar's year doesn't start in January.
    static func groups(
        from sections: [DiaryMonthSection],
        calendar: Calendar = .autoupdatingCurrent
    ) -> [DiaryYearGroup] {
        var groupIndexes: [String: Int] = [:]
        return sections.reduce(into: []) { groups, section in
            let id = DiaryDateFormatter.year(from: section.titleDate, calendar: calendar) ?? ""
            if let index = groupIndexes[id] {
                groups[index].sections.append(section)
            } else {
                groupIndexes[id] = groups.count
                groups.append(DiaryYearGroup(id: id, sections: [section]))
            }
        }
    }
}

private struct DiaryMonthIndexRow: View {
    let section: DiaryMonthSection
    let showsSeparator: Bool

    var body: some View {
        HStack(spacing: 10) {
            Text(DiaryDateFormatter.monthName(from: section.titleDate) ?? section.title)
                .font(.body)
                .foregroundStyle(.white)

            Spacer(minLength: 8)

            Text("\(section.entries.count)")
                .font(.body)
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.5))
        }
        .padding(.horizontal, 16)
        .frame(minHeight: 48)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                DiarySeparator()
                    .padding(.leading, 16)
            }
        }
    }
}

extension EnvironmentValues {
    /// Where a month header pins in its scroll view; nil where headers don't pin.
    @Entry var diaryPinnedHeaderTop: CGFloat? = nil
}

private struct DiaryPinnedHeaders: ViewModifier {
    @State private var top: CGFloat = 0

    func body(content: Content) -> some View {
        content
            // Pinned headers sit at the top content inset; it only changes with the safe area.
            .onScrollGeometryChange(for: CGFloat.self) { $0.contentInsets.top } action: { _, newTop in
                top = newTop
            }
            .environment(\.diaryPinnedHeaderTop, top)
            .modifier(DiaryCleanTop(top: top))
    }
}

/// Nothing scrolled draws above where headers pin: the page fades out over the last 18 pt before it, under the
/// status bar (or the navigation bar). One mask and no blur layers, so there is no seam between two of them.
private struct DiaryCleanTop: ViewModifier {
    let top: CGFloat

    static let fade: CGFloat = 18

    func body(content: Content) -> some View {
        content
            .scrollEdgeEffectHidden(true, for: .top)
            .mask {
                VStack(spacing: 0) {
                    LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                        .frame(height: Self.fade)
                    Rectangle()
                }
                .padding(.top, max(0, top - Self.fade))
                .ignoresSafeArea()
            }
    }
}

/// The clean top for a scroll view without pinned headers, such as the month index.
private struct DiaryCleanTopOnly: ViewModifier {
    @State private var top: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .onScrollGeometryChange(for: CGFloat.self) { $0.contentInsets.top } action: { _, newTop in
                top = newTop
            }
            .modifier(DiaryCleanTop(top: top))
    }
}

/// While a header is pinned, rows scrolling under it blur away across the full width instead
/// of running into it. It fades in over the last stretch before the header pins, read
/// straight from its position so no state flips mid-layout.
private struct DiaryPinnedHeaderBlur: ViewModifier {
    @Environment(\.diaryPinnedHeaderTop) private var pinnedTop

    /// How far the pinned blur reaches above the header.
    private static let bandOverhang: CGFloat = 16

    func body(content: Content) -> some View {
        content.background {
            Rectangle()
                .fill(.ultraThinMaterial)
                .mask {
                    LinearGradient(
                        // Soft at both ends: the top fades in across the overhang, under the
                        // status-bar blur's fade, so neither edge shows as a line.
                        stops: [
                            .init(color: .clear, location: 0),
                            .init(color: .black, location: 0.3),
                            .init(color: .black, location: 0.75),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                }
                // Out past the page margins to the screen edges, and up under the status-bar
                // blur's fade so the two blurs meet without a seam.
                .padding(.horizontal, -24)
                .padding(.top, -Self.bandOverhang)
                .visualEffect { [pinnedTop] content, proxy in
                    guard let pinnedTop else { return content.opacity(0) }
                    // The padding only lets the band draw outside the header; this is the header's own top.
                    let distance = proxy.frame(in: .scrollView).minY - pinnedTop
                    return content.opacity(min(1, max(0, 1 - distance / 24)))
                }
                .allowsHitTesting(false)
                .environment(\.colorScheme, .dark)
        }
    }
}

extension View {
    /// For a scroll view that pins month headers: tells each header where it pins, so a pinned month's card can
    /// keep its top edge under the tab, and keeps the page clear under the status bar.
    func diaryPinnedHeaders() -> some View {
        modifier(DiaryPinnedHeaders())
    }

    /// Keeps a diary page clear under the status bar, as `diaryPinnedHeaders()` does, for a scroll view with no
    /// pinned headers.
    func diaryCleanTop() -> some View {
        modifier(DiaryCleanTopOnly())
    }

    /// The blur behind a pinned header that isn't a month's tab (the tag page's Diary/Grid switch), in a scroll
    /// view using `diaryPinnedHeaders()`.
    func diaryPinnedHeaderBlur() -> some View {
        modifier(DiaryPinnedHeaderBlur())
    }

    /// The diary's glassy card, the same even translucent fill as the log sheet's cards.
    /// A row in the middle of a card rounds neither end. Under a month's tab (`underTab`), the card's top-leading
    /// corner is the tab's, and while the month is pinned its rows slide under the card's top edge.
    func diaryCard(roundsTop: Bool = true, roundsBottom: Bool = true, underTab: Bool = false) -> some View {
        modifier(DiaryCard(roundsTop: roundsTop, roundsBottom: roundsBottom, underTab: underTab))
    }
}

private struct DiaryCard: ViewModifier {
    let roundsTop: Bool
    let roundsBottom: Bool
    let underTab: Bool

    @Environment(\.diaryPinnedHeaderTop) private var pinnedTop

    static let radius: CGFloat = 22

    func body(content: Content) -> some View {
        let top: CGFloat = roundsTop ? Self.radius : 0
        let bottom: CGFloat = roundsBottom ? Self.radius : 0
        let shape = UnevenRoundedRectangle(
            cornerRadii: RectangleCornerRadii(
                topLeading: underTab ? 0 : top,
                bottomLeading: bottom,
                bottomTrailing: bottom,
                topTrailing: top
            ),
            style: .continuous
        )
        if underTab, pinnedTop != nil {
            // A pinned month's card keeps its top edge at its tab's base: the rows' content fades out over the
            // last 18 pt before it, and everything above it (the card's fill too) is cut away. The scroll view's
            // coordinate space starts below its top inset, where headers pin, so the edge is the tab's height.
            let edge = DiaryMonthHeader.height
            content
                .mask { DiaryPinnedEdgeFade(edge: edge) }
                .background(LogComposerStyle.rowBackground)
                .clipShape(shape)
                .mask { DiaryPinnedEdgeCut(edge: edge) }
        } else {
            content
                .background(LogComposerStyle.rowBackground)
                .clipShape(shape)
        }
    }
}

/// A row's mask while months pin: clear above the pinned card edge, opaque below it, rounding the trailing corner
/// the way the card's top corner rounds. Every row reads the same edge, so a corner that spans two rows lines up.
/// Moved straight from the row's position (no state), and overdrawn below so a raised mask still covers the row.
private struct DiaryPinnedEdgeCut: View {
    let edge: CGFloat

    var body: some View {
        UnevenRoundedRectangle(
            cornerRadii: RectangleCornerRadii(topLeading: 0, bottomLeading: 0, bottomTrailing: 0, topTrailing: DiaryCard.radius),
            style: .continuous
        )
        .padding(.bottom, -DiaryPinnedEdge.overdraw)
        .visualEffect { content, proxy in
            content.offset(y: DiaryPinnedEdge.cut(edge: edge, rowTop: proxy.frame(in: .scrollView).minY))
        }
    }
}

/// The rows' content fades out over the 18 pt before the pinned card edge; the card's fill stays crisp.
private struct DiaryPinnedEdgeFade: View {
    let edge: CGFloat

    var body: some View {
        VStack(spacing: 0) {
            LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                .frame(height: DiaryPinnedEdge.fade)
            Rectangle()
        }
        .padding(.bottom, -DiaryPinnedEdge.overdraw)
        .visualEffect { content, proxy in
            content.offset(y: DiaryPinnedEdge.cut(edge: edge, rowTop: proxy.frame(in: .scrollView).minY))
        }
    }
}

/// Read inside `visualEffect`, off the main actor.
nonisolated enum DiaryPinnedEdge {
    static let fade: CGFloat = 18
    /// How far below a row its masks reach, so lifting them over a row just under the edge leaves no gap.
    static let overdraw: CGFloat = 100

    /// How far into a row the pinned card edge is: positive once the row has started under it. Rows farther
    /// below stop at -60, which already lifts the corner and the fade clear of them.
    static func cut(edge: CGFloat, rowTop: CGFloat) -> CGFloat {
        max(edge - rowTop, -60)
    }
}

/// The space between two months. It sits outside both months, so a pinned tab leaves as its card's bottom edge
/// reaches it, never floating on its own.
struct DiaryMonthGap: View {
    static let height: CGFloat = 22

    var body: some View {
        Color.clear
            .frame(height: Self.height)
            .accessibilityHidden(true)
    }
}

/// A row in a diary card: a soft highlight while pressed, like a list row.
struct DiaryRowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(Color.white.opacity(configuration.isPressed ? 0.08 : 0))
    }
}

struct DiarySeparator: View {
    var body: some View {
        Rectangle()
            .fill(.white.opacity(0.09))
            .frame(height: 0.5)
    }
}

extension DiaryMonthSection {
    static func sections(from entries: [DiaryEntry]) -> [DiaryMonthSection] {
        var sectionIndexes: [String: Int] = [:]
        return entries.reduce(into: []) { sections, entry in
            let rawDate = entry.consumedAt ?? entry.createdAt
            let id = DiaryDateFormatter.monthID(from: rawDate) ?? "undated"
            let title = DiaryDateFormatter.monthHeader(from: rawDate) ?? "Undated"
            if let index = sectionIndexes[id] {
                sections[index].entries.append(entry)
            } else {
                sectionIndexes[id] = sections.count
                sections.append(DiaryMonthSection(id: id, title: title, entries: [entry]))
            }
        }
    }
}

struct DiaryEntryArtworkOverride {
    let url: String?
    let orientation: PosterOrientation?
}

struct DiaryMonthSection: Identifiable {
    let id: String
    let title: String
    var entries: [DiaryEntry]

    /// The date the title is formatted from: its newest log's, or the first of its month.
    var titleDate: String? {
        entries.first.flatMap { $0.consumedAt ?? $0.createdAt } ?? (id == "undated" ? nil : "\(id)-01")
    }
}

/// A month's title on a small tab that grows out of the top-leading corner of the month's card, in the card's
/// fill: the card's corner is the tab's corner, and an inside curve takes the tab into the card's top edge. It stays
/// pinned while that month scrolls under it, with the card's top edge under it (`diaryCard(underTab:)`); with an
/// action, a tap opens the month index.
struct DiaryMonthHeader: View {
    let title: String
    var isLoading = false
    var action: (() -> Void)? = nil

    /// The tab's height. A pinned month's card keeps its top edge this far below where headers pin.
    static let height: CGFloat = 26

    var body: some View {
        if let action {
            Button(action: action) {
                strip
            }
            .buttonStyle(.plain)
            .disabled(isLoading)
            .accessibilityLabel(title)
            .accessibilityValue(isLoading ? "Loading" : "Expanded")
            .accessibilityHint("Hides diary entries for all months")
        } else {
            strip
        }
    }

    /// The tab on a full-width strip. The whole strip takes taps, so a tap beside the tab of a
    /// pinned month never reaches a row under it.
    private var strip: some View {
        label
            .background(alignment: .leading) {
                DiaryMonthTabShape()
                    .fill(LogComposerStyle.rowBackground)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: Self.height)
            .contentShape(Rectangle())
    }

    private var label: some View {
        HStack(spacing: 5) {
            Text(title)
                // One line, so the tab never wraps.
                .lineLimit(1)
                .minimumScaleFactor(0.7)

            if action != nil {
                if isLoading {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(.white.opacity(0.6))
                } else {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.white.opacity(0.45))
                        .accessibilityHidden(true)
                }
            }
        }
        .font(.system(size: 13, weight: .semibold))
        .foregroundStyle(.white)
        .padding(.leading, 12)
        .padding(.trailing, 9)
        .frame(height: Self.height)
    }
}

/// The month's tab, drawn in the frame of its words: a 10 pt corner on the leading side (the card's corner), a
/// 9 pt corner on the trailing side, then an 8 pt inside curve past the frame into the card's top edge.
struct DiaryMonthTabShape: Shape {
    static let leadingRadius: CGFloat = 10
    static let trailingRadius: CGFloat = 9
    static let joinRadius: CGFloat = 8

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + Self.leadingRadius))
        path.addArc(
            tangent1End: CGPoint(x: rect.minX, y: rect.minY),
            tangent2End: CGPoint(x: rect.minX + Self.leadingRadius, y: rect.minY),
            radius: Self.leadingRadius
        )
        path.addLine(to: CGPoint(x: rect.maxX - Self.trailingRadius, y: rect.minY))
        path.addArc(
            tangent1End: CGPoint(x: rect.maxX, y: rect.minY),
            tangent2End: CGPoint(x: rect.maxX, y: rect.minY + Self.trailingRadius),
            radius: Self.trailingRadius
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - Self.joinRadius))
        path.addArc(
            tangent1End: CGPoint(x: rect.maxX, y: rect.maxY),
            tangent2End: CGPoint(x: rect.maxX + Self.joinRadius, y: rect.maxY),
            radius: Self.joinRadius
        )
        path.closeSubpath()
        return path
    }
}

struct DiaryEntryRow: View {
    let entry: DiaryEntry
    var artworkOverride: DiaryEntryArtworkOverride?
    var showsSeparator = false

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            DiaryDateBadge(rawValue: entry.consumedAt ?? entry.createdAt, mediaType: entry.media.ref.mediaType)

            compactArtwork

            VStack(alignment: .leading, spacing: 5) {
                Text(titleText)
                    .font(.system(size: 16, weight: .semibold))
                    .lineLimit(2)

                if !summary.isEmpty || isRepeat {
                    summaryLine
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsSeparator {
                // Starts under the artwork, like an inset list separator.
                DiarySeparator()
                    .padding(.leading, 56)
            }
        }
    }

    private var compactArtwork: some View {
        MediaArtwork(
            url: artworkOverride?.url ?? entry.media.displayPosterURL,
            title: entry.media.title,
            slot: .diaryEntry,
            mediaType: entry.media.ref.mediaType,
            orientation: artworkOverride?.orientation ?? entry.media.posterOrientation
        )
        // Laid out at its real size: a larger poster shrunk with scaleEffect overflowed the
        // row's layout, and the lazy list kept loading and unloading the last row forever.
        .frame(width: 42, height: 63)
        // A tap opens the log; holding the poster is about the title.
        .posterHoldMenu(entry.media.summary)
        .shadow(color: .black.opacity(0.2), radius: 6, y: 3)
    }

    private var summary: DiaryEntrySummary { DiaryEntrySummary(entry: entry) }

    private var isRepeat: Bool { entry.isTrueReread ?? entry.isRewatch }

    private var summaryLine: some View {
        LogRatingMarks(summary: summary, isRepeat: isRepeat, repeatLabel: entry.media.ref.repeatLabel)
    }

    private var titleText: AttributedString {
        Self.titleText(titleParts)
    }

    /// The title in white, its year greyed.
    static func titleText(_ parts: (title: String, year: String?)) -> AttributedString {
        var text = AttributedString(parts.title)
        text.foregroundColor = .white

        if let year = parts.year {
            var yearText = AttributedString(" \(year)")
            yearText.foregroundColor = .white.opacity(0.45)
            yearText.font = .system(size: 16, weight: .regular)
            text += yearText
        }

        return text
    }

    private var titleParts: (title: String, year: String?) {
        Self.titleParts(for: entry.media)
    }

    /// The title and its trailing "(2008)" year. TV logs read "Show · Season 1" or "Show · S1 E4 · Title" (TV-702).
    static func titleParts(for media: DiaryMedia) -> (title: String, year: String?) {
        splitYear(TVTrackingPresentation.diaryTitle(for: media))
    }

    /// The same for a title on the Activity list.
    static func titleParts(for media: MediaSummary) -> (title: String, year: String?) {
        splitYear(TVTrackingPresentation.diaryTitle(for: media))
    }

    private static func splitYear(_ rawTitle: String) -> (title: String, year: String?) {
        let title = rawTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard title.hasSuffix(")") else {
            return (title, nil)
        }

        let suffix = String(title.suffix(6))
        guard suffix.first == "(", suffix.last == ")" else {
            return (title, nil)
        }

        let year = String(suffix.dropFirst().dropLast())
        guard year.count == 4, year.allSatisfy(\.isNumber) else {
            return (title, nil)
        }

        return (String(title.dropLast(7)), year)
    }
}

/// "★★★½ ♥", then marks for a rewatch and a review: the line under the title on a diary row and on an activity post.
struct LogRatingMarks: View {
    let summary: DiaryEntrySummary
    let isRepeat: Bool
    let repeatLabel: String

    var body: some View {
        HStack(spacing: 6) {
            if !summary.starSymbols.isEmpty {
                HStack(spacing: 1) {
                    ForEach(Array(summary.starSymbols.enumerated()), id: \.offset) { _, symbol in
                        Image(systemName: symbol)
                    }
                }
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(LogComposerStyle.starGold)
            }
            if summary.liked {
                Image(systemName: "heart.fill")
                    .foregroundStyle(.pink)
            }
            if isRepeat {
                Image(systemName: "arrow.clockwise")
                    .foregroundStyle(.white.opacity(0.5))
            }
            if summary.hasReview {
                Image(systemName: "text.alignleft")
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .font(.system(size: 12, weight: .bold))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [summary.isEmpty ? nil : summary.accessibilityLabel, isRepeat ? repeatLabel : nil]
                .compactMap { $0 }
                .joined(separator: ", ")
        )
    }
}

/// What a diary row shows under its title: the star count, a heart, and whether there's a review.
struct DiaryEntrySummary: Equatable {
    let stars: String?
    let liked: Bool
    let hasReview: Bool

    init(stars: String?, liked: Bool, hasReview: Bool) {
        self.stars = stars
        self.liked = liked
        self.hasReview = hasReview
    }

    init(entry: DiaryEntry) {
        stars = entry.rating?.starCount
        liked = entry.liked
        hasReview = [entry.reviewTitle, entry.review].contains {
            $0?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        }
    }

    /// A filled star per whole star, then a half star for a .5: "3.5" is ★★★½. Ratings between half stars
    /// round to the nearest: "3.8" is ★★★★.
    var starSymbols: [String] {
        guard let stars, Double(stars) != nil else { return [] }
        let value = Double(stars.ratingHalfSteps) / 2
        let whole = Array(repeating: "star.fill", count: Int(value))
        return value - Double(Int(value)) >= 0.5 ? whole + ["star.leadinghalf.filled"] : whole
    }

    var isEmpty: Bool { starSymbols.isEmpty && !liked && !hasReview }

    var accessibilityLabel: String {
        [stars.map { "Rated \($0) out of 5 stars" }, liked ? "Liked" : nil, hasReview ? "Has review" : nil]
            .compactMap { $0 }
            .joined(separator: ", ")
    }
}

struct DiaryStarRating: View {
    let rating: String
    var fontSize: CGFloat = 9

    var body: some View {
        HStack(spacing: 1) {
            ForEach(0..<5, id: \.self) { index in
                Image(systemName: symbolName(for: index))
                    .font(.system(size: fontSize, weight: .bold))
                    .foregroundStyle(.yellow.opacity(0.92))
            }
        }
        .accessibilityLabel("Rating \(displayValue) out of 5 stars")
    }

    /// To the nearest half star: "3.75" draws as 4.
    private var value: Double {
        Double(rating.ratingHalfSteps) / 2
    }

    private var displayValue: String {
        value.formatted(.number.precision(.fractionLength(0...1)))
    }

    private func symbolName(for index: Int) -> String {
        let starValue = value - Double(index)
        if starValue >= 1 {
            return "star.fill"
        }
        if starValue >= 0.5 {
            return "star.leadinghalf.filled"
        }
        return "star"
    }
}

struct DiaryDateBadge: View {
    let rawValue: String?
    let mediaType: String

    var body: some View {
        VStack(spacing: 5) {
            Text(day)
                .font(.system(size: 20, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(.white.opacity(0.9))

            MediaTypeGlyph(theme: MediaTypeTheme.theme(for: mediaType), size: 11)
                .opacity(0.5)
        }
        .frame(width: 30)
    }

    private var day: String {
        DiaryDateFormatter.dayNumber(from: rawValue) ?? "--"
    }
}

struct DiaryStateCard: View {
    let title: String
    let systemImage: String
    let message: String

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: systemImage)
                .font(.system(size: 30))
                .foregroundStyle(.white.opacity(0.5))
                .padding(.bottom, 6)
            Text(title)
                .font(.headline)
                .foregroundStyle(.white)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.55))
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity, minHeight: 240)
        .padding(22)
        .diaryCard()
    }
}

enum DiaryDateFormatter {
    private static let isoFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let fallbackISOFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let dateOnlyFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static func monthHeader(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.month(.wide).year())
    }

    static func monthID(from rawValue: String?) -> String? {
        guard let rawValue else { return nil }
        let value = String(rawValue.prefix(7))
        return dateOnlyFormatter.date(from: "\(value)-01") == nil ? nil : value
    }

    /// "October", like `monthHeader` without the year.
    static func monthName(from rawValue: String?) -> String? {
        date(from: rawValue)?.formatted(.dateTime.month(.wide))
    }

    /// "2026", in the given calendar (the phone's by default), like `monthHeader`'s year.
    static func year(from rawValue: String?, calendar: Calendar = .autoupdatingCurrent) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        var style = Date.FormatStyle.dateTime.year()
        style.calendar = calendar
        return date.formatted(style)
    }

    static func exactDate(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.month(.abbreviated).day().year())
    }

    static func dayNumber(from rawValue: String?) -> String? {
        guard let date = date(from: rawValue) else { return nil }
        return date.formatted(.dateTime.day())
    }

    static func date(from rawValue: String?) -> Date? {
        guard let rawValue else { return nil }
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        return dateOnlyFormatter.date(from: String(trimmed.prefix(10)))
            ?? isoFormatter.date(from: trimmed)
            ?? fallbackISOFormatter.date(from: trimmed)
    }
}
