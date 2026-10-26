import SwiftUI

/// Rated higher than most (STAT-217): a segmented Higher / Lower control over rows of poster,
/// title, your stars and "Everyone 3.4". With only one side, the control goes and the title
/// says which side it is. One view per row, for the page's lazy stack.
struct StatsRatedVsEveryoneSection: View {
    let ratedVsEveryone: StatsRatedVsEveryone
    let onOpenTitle: (MediaSummary) -> Void

    enum Side: String, CaseIterable, Hashable, Identifiable {
        case higher
        case lower

        var id: String { rawValue }

        var title: String {
            switch self {
            case .higher: "Higher"
            case .lower: "Lower"
            }
        }
    }

    enum Row: Hashable, Identifiable {
        case title(String)
        case picker
        case compared(StatsComparedTitle, side: Side, isFirst: Bool, isLast: Bool)
        case sources(String)

        var id: String {
            switch self {
            case .title: "vs.title"
            case .picker: "vs.picker"
            case let .compared(item, side, _, _): "vs.\(side.rawValue).\(item.id)"
            case .sources: "vs.sources"
            }
        }
    }

    @State private var chosenSide: Side = .higher

    var body: some View {
        let side = Self.effectiveSide(chosen: chosenSide, in: ratedVsEveryone)
        ForEach(Self.rows(for: ratedVsEveryone, side: side)) { row in
            switch row {
            case let .title(title):
                StatsSectionTitle(title)
            case .picker:
                Picker("Rated", selection: $chosenSide) {
                    ForEach(Side.allCases) { side in
                        Text(side.title).tag(side)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.bottom, 12)
                .sensoryFeedback(.selection, trigger: chosenSide)
            case let .compared(item, _, isFirst, isLast):
                Button {
                    onOpenTitle(item.media)
                } label: {
                    StatsMediaRow(media: item.media, showsSeparator: !isLast) {
                        if let stars = item.stars?.value {
                            StatsStars(stars: stars)
                        }
                    } trailing: {
                        if let everyone = Self.everyoneText(item) {
                            Text(everyone)
                                .font(.footnote.weight(.semibold))
                                .monospacedDigit()
                                .foregroundStyle(.white.opacity(0.6))
                                .multilineTextAlignment(.trailing)
                        }
                    }
                }
                .buttonStyle(DiaryRowButtonStyle())
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(Self.accessibilityLabel(for: item))
                .accessibilityAddTraits(.isButton)
                .statsCardSlice(isFirst: isFirst, isLast: isLast, gapAfter: hasSources ? 0 : StatsSectionLayout.sectionGap)
            case let .sources(text):
                Text(text)
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.5))
                    .padding(.horizontal, 4)
                    .padding(.top, 8)
                    .padding(.bottom, StatsSectionLayout.sectionGap)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private var hasSources: Bool {
        Self.sourcesNote(ratedVsEveryone.sources) != nil
    }

    // MARK: - Data shaping

    /// The chosen side, or the other one when the chosen side has no rows.
    static func effectiveSide(chosen: Side, in section: StatsRatedVsEveryone) -> Side {
        switch chosen {
        case .higher: section.higher.isEmpty && !section.lower.isEmpty ? .lower : .higher
        case .lower: section.lower.isEmpty && !section.higher.isEmpty ? .higher : .lower
        }
    }

    /// "Rated higher than most", or "Rated lower than most" when only that side has rows.
    static func title(for section: StatsRatedVsEveryone) -> String {
        section.higher.isEmpty && !section.lower.isEmpty ? "Rated lower than most" : "Rated higher than most"
    }

    static func rows(for section: StatsRatedVsEveryone, side: Side) -> [Row] {
        guard !section.isEmpty else { return [] }
        let items = side == .higher ? section.higher : section.lower
        var rows: [Row] = [.title(title(for: section))]
        if !section.higher.isEmpty, !section.lower.isEmpty {
            rows.append(.picker)
        }
        for (index, item) in items.enumerated() {
            rows.append(.compared(item, side: side, isFirst: index == 0, isLast: index == items.count - 1))
        }
        if let note = sourcesNote(section.sources) {
            rows.append(.sources(note))
        }
        return rows
    }

    /// "Everyone 3.4".
    static func everyoneText(_ item: StatsComparedTitle) -> String? {
        item.everyoneStars.map { "Everyone \($0.text)" }
    }

    /// "Everyone's ratings from Letterboxd and IMDb".
    static func sourcesNote(_ sources: [StatsCommunitySource]) -> String? {
        var labels: [String] = []
        for source in sources {
            let label = source.label.trimmingCharacters(in: .whitespacesAndNewlines)
            if !label.isEmpty, !labels.contains(label) {
                labels.append(label)
            }
        }
        guard let last = labels.last else { return nil }
        let list = labels.count == 1 ? last : labels.dropLast().joined(separator: ", ") + " and " + last
        return "Everyone's ratings from \(list)"
    }

    static func accessibilityLabel(for item: StatsComparedTitle) -> String {
        var parts = [item.media.displayTitle]
        if let stars = item.stars {
            parts.append("you rated \(stars.text)")
        }
        if let everyone = item.everyoneStars {
            parts.append("everyone \(everyone.text)")
        }
        return parts.joined(separator: ", ")
    }
}
