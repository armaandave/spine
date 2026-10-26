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
                .diaryCard(roundsTop: isFirst, roundsBottom: isLast)
                .padding(.bottom, isLast ? 18 : 0)
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
    }
}

extension View {
    /// For a scroll view that pins month headers: tells each header where it pins, so it can
    /// blur the rows behind it once it does.
    func diaryPinnedHeaders() -> some View {
        modifier(DiaryPinnedHeaders())
    }

    /// The diary's glassy card, the same even translucent fill as the log sheet's cards.
    /// A row in the middle of a card rounds neither end.
    func diaryCard(roundsTop: Bool = true, roundsBottom: Bool = true) -> some View {
        let top: CGFloat = roundsTop ? 22 : 0
        let bottom: CGFloat = roundsBottom ? 22 : 0
        return background(LogComposerStyle.rowBackground)
            .clipShape(UnevenRoundedRectangle(
                cornerRadii: RectangleCornerRadii(
                    topLeading: top,
                    bottomLeading: bottom,
                    bottomTrailing: bottom,
                    topTrailing: top
                ),
                style: .continuous
            ))
    }
}

/// A row in a diary card: a soft highlight while pressed, like a list row.
struct DiaryRowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(Color.white.opacity(configuration.isPressed ? 0.08 : 0))
    }
}

private struct DiarySeparator: View {
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

/// A month's title in a Liquid Glass capsule. It stays pinned while that month scrolls
/// under it; with an action, a tap opens the month index.
struct DiaryMonthHeader: View {
    let title: String
    var isLoading = false
    var action: (() -> Void)? = nil

    @Environment(\.diaryPinnedHeaderTop) private var pinnedTop

    /// How far the pinned blur reaches above the header.
    private static let bandOverhang: CGFloat = 16

    var body: some View {
        Group {
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
        .background {
            // While pinned, rows scrolling under the header blur away across the full width
            // instead of running into the capsule. It fades in over the last stretch before
            // the header pins, read straight from its position so no state flips mid-layout.
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
        }
        // Dark glass and text on any page, whatever the phone's appearance.
        .environment(\.colorScheme, .dark)
    }

    /// The capsule on a full-width strip. The whole strip takes taps, so a tap beside the
    /// capsule of a pinned header doesn't open the row blurred behind it.
    private var strip: some View {
        // A darker tint keeps the title readable over rows scrolling under it.
        let glass = Glass.regular.tint(.black.opacity(0.3))
        return label
            .glassEffect(action == nil ? glass : glass.interactive(), in: .capsule)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 8)
            .contentShape(Rectangle())
    }

    private var label: some View {
        HStack(spacing: 6) {
            Text(title)
                // One line at every text size, so the capsule never wraps.
                .lineLimit(1)
                .minimumScaleFactor(0.6)

            if action != nil {
                if isLoading {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(.white.opacity(0.6))
                } else {
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white.opacity(0.5))
                        .accessibilityHidden(true)
                }
            }
        }
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(.white)
        .padding(.horizontal, 14)
        .frame(minHeight: 34)
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
        .shadow(color: .black.opacity(0.2), radius: 6, y: 3)
    }

    private var summary: DiaryEntrySummary { DiaryEntrySummary(entry: entry) }

    private var isRepeat: Bool { entry.isTrueReread ?? entry.isRewatch }

    /// "★★★½ ♥", then marks for a rewatch and a review.
    private var summaryLine: some View {
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
            [summary.isEmpty ? nil : summary.accessibilityLabel, isRepeat ? entry.media.ref.repeatLabel : nil]
                .compactMap { $0 }
                .joined(separator: ", ")
        )
    }

    private var titleText: AttributedString {
        let parts = titleParts
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
        let title = TVTrackingPresentation.diaryTitle(for: media).trimmingCharacters(in: .whitespacesAndNewlines)
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

/// What a diary row shows under its title: the star count, a heart, and whether there's a review.
struct DiaryEntrySummary: Equatable {
    let stars: String?
    let liked: Bool
    let hasReview: Bool

    init(entry: DiaryEntry) {
        stars = entry.rating?.starCount(mediaType: entry.media.ref.mediaType)
        liked = entry.liked
        hasReview = [entry.reviewTitle, entry.review].contains {
            $0?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        }
    }

    /// A filled star per whole star, then a half star for a .5: "3.5" is ★★★½.
    var starSymbols: [String] {
        guard let value = stars.flatMap(Double.init) else { return [] }
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
    var mediaType: String?

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

    private var value: Double {
        guard let raw = Double(rating) else { return 0 }
        return max(0, min(5, ["movie", "music", "book", "game"].contains(mediaType) ? raw : raw / 2))
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

private struct DiaryDateBadge: View {
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
